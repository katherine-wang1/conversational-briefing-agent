"""Gzip JSONL shards of the archive tables, and pushing them to the `data/archive-raw` branch.

Layout: `<shards_dir>/<table>/<partition>[-NNN].jsonl.gz`, one partition per UTC day (the day an
observation is for, a document became available, a comment was written, ...). Rows are sorted by primary
key and gzip headers carry no timestamp, so an unchanged partition re-exports to identical bytes and git
only stores what changed. Each file stays under `max_bytes` (default 45 MB, below the 50 MB limit).
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from eval.archive.store import TABLE_KEYS, ArchiveStore

log = logging.getLogger("archive.shards")

DATA_BRANCH = "data/archive-raw"
MAX_BYTES = 45 * 1024 * 1024

# SQL expression giving a row's day partition.
PARTITION = {
    "observations": "coalesce(listing_day, substr(observed_at, 1, 10))",
    "documents": "substr(available_at, 1, 10)",
    "comments": "substr(created_at, 1, 10)",
    "hn_items": "substr(created_at, 1, 10)",
    "coverage": "substr(probe_ts, 1, 10)",
    "units": "coalesce(day, 'none')",
    "meta": "'all'",
}


def _write_part(path: Path, lines: list[bytes]) -> None:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        for ln in lines:
            gz.write(ln)
    data = buf.getvalue()
    if path.exists() and path.read_bytes() == data:
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def export_shards(store: ArchiveStore, shards_dir: str | Path, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    shards_dir = Path(shards_dir)
    summary: dict[str, Any] = {}
    for table in TABLE_KEYS:
        tdir = shards_dir / table
        tdir.mkdir(parents=True, exist_ok=True)
        written: set[str] = set()
        total = 0
        for part in store.partitions(table, PARTITION[table]):
            lines = [
                (json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n").encode()
                for r in store.rows_in_partition(table, PARTITION[table], part)
            ]
            total += len(lines)
            # split by an uncompressed budget estimate (text compresses ~5-10x, so this is conservative)
            chunks: list[list[bytes]] = [[]]
            size = 0
            for ln in lines:
                if size + len(ln) > max_bytes * 3 and chunks[-1]:
                    chunks.append([])
                    size = 0
                chunks[-1].append(ln)
                size += len(ln)
            for i, chunk in enumerate(chunks):
                name = f"{part}.jsonl.gz" if len(chunks) == 1 else f"{part}-{i:03d}.jsonl.gz"
                p = tdir / name
                _write_part(p, chunk)
                if p.stat().st_size > max_bytes:
                    log.warning("shard %s is %d bytes (> %d)", p, p.stat().st_size, max_bytes)
                written.add(name)
        for old in tdir.glob("*.jsonl.gz"):
            if old.name not in written:
                old.unlink()
        summary[table] = {"rows": total, "files": len(written)}
    (shards_dir / "manifest.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    return summary


def iter_shard_rows(shards_dir: str | Path, table: str):
    for p in sorted((Path(shards_dir) / table).glob("*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def shard_sets(shards_dir: str | Path) -> list[Path]:
    """Directories holding one export (a `manifest.json` next to table folders). `shards_dir` may be one
    export or a parent of several namespaces (e.g. a data/archive-raw checkout's `shards/`)."""
    root = Path(shards_dir)
    return sorted({m.parent for m in root.rglob("manifest.json")}) or [root]


def import_shards(store: ArchiveStore, shards_dir: str | Path, batch: int = 5000) -> dict[str, int]:
    counts: dict[str, int] = {t: 0 for t in TABLE_KEYS}
    for sdir in shard_sets(shards_dir):
        for table in TABLE_KEYS:
            buf: list[dict[str, Any]] = []
            for row in iter_shard_rows(sdir, table):
                buf.append(row)
                if len(buf) >= batch:
                    counts[table] += store.import_rows(table, _align(buf))
                    buf = []
            if buf:
                counts[table] += store.import_rows(table, _align(buf))
    return counts


def _align(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every row in a batch the same columns (older shards may lack newer additive columns)."""
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return [{c: r.get(c) for c in cols} for r in rows]


# ---------------------------------------------------------------- git (data branch)

DATA_README = """# Archive shards (`data/archive-raw`)

Data only: gzip JSONL shards of the replay archive built by `python -m eval.archive.collect` (code on
branch `ws/archive`, W-A). No code lives on this branch.

* `shards/<namespace>/<table>/<YYYY-MM-DD>[-NNN].jsonl.gz`: one partition per UTC day, rows sorted by
  primary key. Each collection host writes its own namespace (`direct`: sources reachable from the cloud
  container; `wayback`: web.archive.org sources collected on a laptop), so pushes never overwrite each other.
* `shards/<namespace>/manifest.json`: row and file counts per table at that namespace's last export.

Rebuild the SQLite archive (all namespaces merged, idempotent upserts):

```bash
python -m eval.archive.rebuild --shards-dir <checkout>/shards --db eval/data/archive.sqlite
```

Provenance matters: `observations.provenance` says whether rank / engagement are *as of* `observed_at`
(`wayback`, `live`, `river_post_time`, `feed_pubdate`) or were captured later (`hindsight_listing`,
`api_created_at`: then they sit in `extra_json.hindsight` and are for labels only). See
`docs/archive-datasheet.md` and `eval/archive/README.md` on `ws/archive`.
"""


def _git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    log.debug("git %s (cwd=%s)", " ".join(args), cwd)
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def _repo_root() -> Path:
    here = Path(__file__).resolve().parent
    out = _git(["rev-parse", "--show-toplevel"], here)
    return Path(out.stdout.strip())


def _has_ref(repo: Path, ref: str) -> bool:
    return _git(["rev-parse", "--verify", "--quiet", ref], repo, check=False).returncode == 0


def ensure_data_worktree(
    worktree: Path, remote: str = "origin", branch: str = DATA_BRANCH, repo: Path | None = None
) -> Path:
    """A separate git worktree checked out on `branch` (created as an orphan if it doesn't exist anywhere).
    The main working tree and its branch are never touched."""
    repo = repo or _repo_root()
    if (worktree / ".git").exists():
        return worktree
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "prune"], repo, check=False)
    _git(["fetch", remote, branch], repo, check=False)
    if _has_ref(repo, f"refs/heads/{branch}"):
        r = _git(["worktree", "add", str(worktree), branch], repo, check=False)
        if r.returncode != 0:
            # e.g. the branch is already checked out in another worktree: work detached; pushes go to
            # HEAD:refs/heads/<branch> and the tree is re-synced to the remote tip before every commit
            start = f"{remote}/{branch}" if _has_ref(repo, f"refs/remotes/{remote}/{branch}") else branch
            _git(["worktree", "add", "--detach", str(worktree), start], repo)
    elif _has_ref(repo, f"refs/remotes/{remote}/{branch}"):
        _git(["worktree", "add", "-b", branch, str(worktree), f"{remote}/{branch}"], repo)
    else:
        _git(["worktree", "add", "--detach", str(worktree)], repo)
        _git(["checkout", "--orphan", branch], worktree)
        _git(["rm", "-rf", "--quiet", "."], worktree, check=False)
        for p in worktree.iterdir():
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    return worktree


def _sync_to_remote(wt: Path, remote: str, branch: str) -> None:
    """Move the data worktree onto the remote tip (data-only branch; our namespace is regenerated anyway)."""
    _git(["fetch", remote, branch], wt, check=False)
    if _has_ref(wt, f"refs/remotes/{remote}/{branch}"):
        _git(["reset", "-q", "--hard", f"{remote}/{branch}"], wt)


def push_shards(
    shards_dir: str | Path,
    worktree: str | Path,
    *,
    namespace: str = "direct",
    remote: str = "origin",
    branch: str = DATA_BRANCH,
    message: str = "Update archive shards",
    push: bool = True,
    trailer: str = "",
    repo: Path | None = None,
    attempts: int = 4,
) -> str:
    """Mirror `shards_dir` into `shards/<namespace>/` of the data worktree, commit if anything changed, push.

    Before committing, the worktree is reset to the remote tip so other namespaces pushed from elsewhere
    (container vs. laptop) are kept; a rejected push (someone pushed in between) is retried the same way.
    """
    wt = ensure_data_worktree(Path(worktree), remote, branch, repo)
    src = Path(shards_dir)
    last_err = ""
    for attempt in range(attempts):
        if push:
            _sync_to_remote(wt, remote, branch)
        dst = wt / "shards" / namespace
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        (wt / "README.md").write_text(DATA_README)
        _git(["add", "-A", "."], wt)
        if _git(["diff", "--cached", "--quiet"], wt, check=False).returncode == 0:
            return "no changes"
        msg = message + (f"\n\n{trailer}" if trailer else "")
        c = _git(["commit", "-q", "-m", msg], wt, check=False)
        if c.returncode != 0:
            raise RuntimeError(f"git commit failed: {c.stderr.strip()}")
        if not push:
            break
        r = _git(["push", "-u", remote, f"HEAD:refs/heads/{branch}"], wt, check=False)
        if r.returncode == 0:
            break
        last_err = r.stderr.strip()
        log.warning("shard push rejected (attempt %d/%d): %s", attempt + 1, attempts, last_err)
        time.sleep(min(30, 2 ** (attempt + 1)))
    else:
        raise RuntimeError(f"git push failed: {last_err}")
    return _git(["rev-parse", "--short", "HEAD"], wt).stdout.strip()


COMMIT_TRAILER = os.environ.get(
    "ARCHIVE_COMMIT_TRAILER",
    "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
    "Claude-Session: https://claude.ai/code/session_017GdLG5rcCgpXYYL4hnuPX5",
)
