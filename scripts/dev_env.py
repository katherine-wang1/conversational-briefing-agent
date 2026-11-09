"""Helpers for scripts/dev.sh: read, write and check `.env` (run with the project's venv Python).

    python scripts/dev_env.py get KEY            # value in .env ('' if missing)
    python scripts/dev_env.py set KEY VALUE      # set (or append) KEY=VALUE in .env
    python scripts/dev_env.py lint               # malformed lines -> messages, exit 1
    python scripts/dev_env.py check              # what the server and worker will actually see
    python scripts/dev_env.py db-ready DSN       # exit 0 if Postgres answers at DSN
    python scripts/dev_env.py ensure-db DSN NAME # create database NAME if missing (DSN: a superuser)
    python scripts/dev_env.py drop-db DSN NAME   # drop database NAME (closes its connections)
    python scripts/dev_env.py requeue DSN        # jobs left 'running' by a stopped worker -> queued
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ENV = Path(".env")
LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _lines() -> list[str]:
    return ENV.read_text().splitlines() if ENV.is_file() else []


def _value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw.split(" #", 1)[0].strip()


def get(key: str) -> str:
    for ln in _lines():
        m = LINE.match(ln)
        if m and m.group(1) == key:
            return _value(m.group(2))
    return ""


def set_(key: str, value: str) -> None:
    out, done = [], False
    for ln in _lines():
        m = LINE.match(ln)
        if m and m.group(1) == key and not done:
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{key}={value}")
    ENV.write_text("\n".join(out) + "\n")
    ENV.chmod(0o600)


def lint() -> int:
    bad = []
    for n, ln in enumerate(_lines(), 1):
        s = ln.strip()
        if not s or s.startswith("#") or LINE.match(ln):
            continue
        name = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)", ln)
        what = name.group(1) if name else "?"  # never echo the value: it may be a key
        if re.match(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=", ln):
            bad.append(f"  line {n} ({what}): remove the spaces around '=' (write KEY=value)")
        else:
            bad.append(f"  line {n} ({what}...): not in KEY=value form")
    for n, ln in enumerate(_lines(), 1):
        m = LINE.match(ln)
        if m and m.group(1).endswith("_API_KEY") and re.search(r"\s", _value(m.group(2))):
            bad.append(f"  line {n}: {m.group(1)} contains a space; paste the key on its own")
    if bad:
        print(".env has lines the app can't read:\n" + "\n".join(bad))
        return 1
    return 0


def check() -> int:
    """Load .env exactly as the server and worker do, then report what they will see."""
    sys.path.insert(0, os.getcwd())
    from app.config.env_file import load_env_file

    in_shell = {k for k in ("OPENROUTER_API_KEY", "SEARCH_API_KEY") if os.environ.get(k)}
    load_env_file()
    from app.config.settings import Settings

    s = Settings()
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key or not s.openrouter_api_key:
        print("OPENROUTER_API_KEY is empty: the agents would run as placeholders. Add it to .env.")
        return 1
    src = "your shell (overrides .env)" if "OPENROUTER_API_KEY" in in_shell else ".env"
    print(f"  OpenRouter key  …{key[-4:]} (from {src})")
    try:
        import httpx

        r = httpx.get(
            "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=10
        )
        if r.status_code in (401, 403):
            print(f"OpenRouter rejected this key (HTTP {r.status_code}). Check OPENROUTER_API_KEY in .env.")
            return 1
    except Exception as e:  # noqa: BLE001 - offline is not fatal here
        print(f"  (couldn't reach openrouter.ai to check the key: {type(e).__name__})")
    search = s.search_api_key and s.search_provider
    print(f"  Web search      {s.search_provider + ' key …' + s.search_api_key[-4:] if search else 'off'}")
    print(f"  Speech          {s.tts_provider or 'off'}   Agents  {s.agent_ports}   Spend cap  "
          f"{os.environ.get('LLM_SPEND_CAP_USD') or 'none'} USD")
    if not s.dev_auth_bypass or s.app_env == "prod":
        print("DEV_AUTH_BYPASS=1 and APP_ENV=dev are needed for the local Dev sign-in.")
        return 1
    return 0


def _connect(dsn: str):  # noqa: ANN202
    import psycopg

    return psycopg.connect(dsn, connect_timeout=3, autocommit=True)


def main(argv: list[str]) -> int:
    cmd, args = argv[0], argv[1:]
    if cmd == "get":
        print(get(args[0]))
    elif cmd == "set":
        set_(args[0], args[1])
    elif cmd == "lint":
        return lint()
    elif cmd == "check":
        return check()
    elif cmd == "db-ready":
        try:
            with _connect(args[0]) as c:
                c.execute("select 1")
        except Exception:  # noqa: BLE001
            return 1
    elif cmd == "ensure-db":
        with _connect(args[0]) as c:
            if not c.execute("select 1 from pg_database where datname = %s", [args[1]]).fetchone():
                c.execute(f'create database "{args[1]}"')
                print(f"  created database {args[1]}")
    elif cmd == "requeue":
        with _connect(args[0]) as c:
            n = c.execute(
                "update jobs set status = 'queued', locked_by = null, locked_at = null, run_at = now()"
                " where status = 'running'"
            ).rowcount
            if n:
                print(f"  re-queued {n} job(s) interrupted when the worker last stopped")
    elif cmd == "drop-db":
        with _connect(args[0]) as c:
            c.execute(f'drop database if exists "{args[1]}" with (force)')
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
