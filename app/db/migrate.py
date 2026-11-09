"""Apply supabase/migrations/*.sql to a Postgres database (local dev / eval / tests).

In prod, Kate applies the same files with the Supabase CLI (`supabase db push`, docs/deploy.md).

    python -m app.db.migrate                       # uses MIGRATIONS_DATABASE_URL (an owner role)
    python -m app.db.migrate --dev-role-passwords  # local only: LOGIN + password 'app_rw' / 'app_admin'
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "supabase" / "migrations"

_TRACKING = """
create table if not exists schema_migrations (
  version text primary key,
  applied_at timestamptz not null default now()
);
alter table schema_migrations enable row level security;
"""


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def apply_migrations(dsn: str, *, dev_role_passwords: bool = False, verbose: bool = False) -> list[str]:
    applied: list[str] = []
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(_TRACKING)
        cur = await conn.execute("select version from schema_migrations")
        done = {r[0] for r in await cur.fetchall()}
        for path in migration_files():
            version = path.stem
            if version in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())  # type: ignore[arg-type]
                await conn.execute("insert into schema_migrations (version) values (%s)", [version])
            applied.append(version)
            if verbose:
                print(f"applied {version}")
        if dev_role_passwords:
            await conn.execute("alter role app_rw with login password 'app_rw'")
            await conn.execute("alter role app_admin with login password 'app_admin'")
    return applied


def main() -> None:
    from app.config.env_file import load_env_file

    load_env_file()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("MIGRATIONS_DATABASE_URL", ""))
    ap.add_argument("--dev-role-passwords", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("set MIGRATIONS_DATABASE_URL (an owner role such as postgres) or pass --dsn")
    applied = asyncio.run(
        apply_migrations(args.dsn, dev_role_passwords=args.dev_role_passwords, verbose=True)
    )
    print(f"{len(applied)} migration(s) applied")


if __name__ == "__main__":
    main()
