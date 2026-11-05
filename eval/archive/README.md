# Replay archive collectors (`eval/archive`)

Builds `eval/data/archive.sqlite` (TDD §15) for **2026-08-01 .. 2026-10-31** and exports it as gzip JSONL
shards to the data-only branch `data/archive-raw`. No model / LLM calls are made anywhere in here.

| module | what |
|---|---|
| `collect.py` | the CLI (resumable, idempotent, ~1 request/s per host, periodic shard export + push) |
| `collectors.py` | one collector per source; list of source names and groups at the top |
| `http.py` | polite cached fetcher (honest UA, robots.txt, backoff, bot-check and host-down handling) |
| `store.py` | SQLite schema and idempotent upserts |
| `shards.py`, `rebuild.py` | shard export / push, and rebuilding the SQLite file from shards |
| `datasheet.py` | per-source per-day counts, gaps, success rates -> `meta` and `docs/archive-datasheet.md` |
| `status.py` | progress of a running or finished collection |

Parsers live in `app/sources/` and are shared with the live adapters (`app/sources/adapters.py`).

## Source groups

* `--sources reachable`: hosts reachable from the cloud container (HN `/front?day=`, HN Algolia stories +
  comments, arXiv, HF daily papers, Techmeme river, current RSS feeds, Wayback availability probes, origin
  article bodies). Run by the orchestrator in the cloud.
* `--sources wayback`: everything that needs **web.archive.org** (blocked for cloud sessions):
  `hn_front_wayback`, `techmeme_wayback`, `rss_wayback`, `frontpage_wayback`, `bodies_wayback`.
  Run on Kate's laptop (below).
* `techmeme_archive` (Techmeme's own `/YYMMDD/hHHMM` pages) answers our honest client with a Cloudflare
  challenge; the collector records that and stops. It is in neither group.

Each host pushes its shards into its own folder on `data/archive-raw` (`shards/direct/`, `shards/wayback/`),
so the two runs never overwrite each other. `rebuild` merges all folders.

## Laptop runbook: the Wayback subset (macOS)

Needs Python **3.12+** (`python3 --version`; macOS's built-in python is too old: `brew install python@3.12`)
and your normal GitHub credentials (the same ones you `git push` with).

```bash
# 1. clone the code branch
git clone https://github.com/katherine-wang1/conversational-briefing-agent.git
cd conversational-briefing-agent
git checkout ws/archive

# 2. virtualenv (plain install is enough)
python3.12 -m venv .venv
.venv/bin/pip install -e .

# 3. quick check that web.archive.org answers from your network (should print 200)
curl -s -o /dev/null -w '%{http_code}\n' 'https://web.archive.org/cdx/search/cdx?url=news.ycombinator.com/&from=20261001&to=20261001&limit=1'

# 4. run (caffeinate keeps the Mac awake; output also goes to a log file)
mkdir -p eval/data
caffeinate -i .venv/bin/python -m eval.archive.collect \
    --start 2026-08-01 --end 2026-10-31 --sources wayback \
    --export-shards-every 30 --push-shards --offline-patience-minutes 240 \
    2>&1 | tee -a eval/data/wayback-run.log
```

What it does: for every day, a CDX query per page/feed, then the captures it picked (HN and Techmeme
front pages: at most one per hour; feeds and outlet front pages: at most one per 6 hours), parsed with
rank and points **as of the capture**. Then, for every article URL those pages linked, the capture nearest
**after** its publish time (within 2 days), with the text extracted. Every 30 minutes the shards are
exported and pushed to `data/archive-raw` under `shards/wayback/` (a separate git worktree in
`eval/data/archive-raw-worktree`; your checkout of `ws/archive` is not touched).

To make the run shorter, split it: `--sources hn_front_wayback,techmeme_wayback,rss_wayback,frontpage_wayback`
first, then `--sources bodies_wayback`. `--beats ai` limits feeds to the AI beat.

### Resume

Just run the same command again (after a crash, Ctrl-C, reboot, or lost network). Raw responses are cached
in `eval/data/cache/` and finished work units are recorded in the database, so nothing is fetched twice.
Ctrl-C exports and pushes what is there before exiting.

* Laptop asleep or Wi-Fi dropped: with `--offline-patience-minutes 240` the run waits up to 4 hours for
  the network to come back instead of giving up on web.archive.org.
* If the run did give up on a host, its unfinished units are retried on the next run.
* Fresh clone or new machine: `git fetch origin data/archive-raw`, then add
  `--import-shards <checkout of data/archive-raw>/shards/wayback` to the command once; finished units are
  then skipped (the raw cache is not in the shards, so only unfinished work is refetched).

### Progress

```bash
.venv/bin/python -m eval.archive.status          # units done per collector and status, table sizes
tail -f eval/data/wayback-run.log                # live log
.venv/bin/python -m eval.archive.datasheet       # regenerate docs/archive-datasheet.md
```

### Expected size and duration (at ~1 request/s to web.archive.org)

All Wayback requests go to one host, so they run one at a time at about 1 request/s (Wayback often takes
1-3 s to serve a capture, so real throughput is lower).

| collector | requests (upper bound, 92 days, all 3 beats) |
|---|---|
| `hn_front_wayback` | 92 CDX + up to 24 captures/day = **~2,300** |
| `techmeme_wayback` | **~2,300** |
| `rss_wayback` (22 feeds) | 22 x 92 x (1 CDX + up to 4 captures) = **<= 10,100** (most feeds are captured far less often; expect ~4,000-6,000) |
| `frontpage_wayback` (6 pages) | 6 x 92 x 5 = **<= 2,760** |
| `bodies_wayback` | 2 per article URL (CDX + capture); ~150-250 article URLs/day -> **~28,000-46,000** |
| **total** | **~40,000-64,000 requests: roughly 12-18 hours at 1 req/s, likely 1-1.5 days wall clock with Wayback latency** |

Headline sources alone (without bodies) are ~11,000-17,000 requests: about 3-6 hours. Disk: the raw cache
is roughly 1-2 GB (gzip); shards are much smaller.

### Commit attribution

Shard commits carry the trailer in `ARCHIVE_COMMIT_TRAILER` (default: the Claude session trailer). Set
`ARCHIVE_COMMIT_TRAILER=""` to omit it.

## Cloud runbook: the reachable subset

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m eval.archive.collect --start 2026-08-01 --end 2026-10-31 --sources reachable \
    --export-shards-every 30 --push-shards 2>&1 | tee -a eval/data/reachable-run.log
```

## Rebuild the database from shards

```bash
git fetch origin data/archive-raw
git worktree add /tmp/archive-raw origin/data/archive-raw
.venv/bin/python -m eval.archive.rebuild --shards-dir /tmp/archive-raw/shards --db eval/data/archive.sqlite
```

## Provenance (read before using the data)

`observations.provenance` says whether `rank` / `engagement` are as of `observed_at`:

* `wayback`, `live`, `techmeme_archive`: yes (as shown on the page at that time);
* `river_post_time` (Techmeme river), `feed_pubdate` (feeds fetched now), `api_created_at` (HN Algolia),
  `api_announce_estimate` (arXiv): observed at the item's own time, no rank;
* `hindsight_listing` (HN `/front?day=`, HF daily papers): fetched weeks later. Rank, points and upvotes are
  in `extra_json.hindsight` and are **labels only**, never agent inputs. HN `/front?day=` rows are observed
  at fetch time so a replay never sees them.

`documents.body_source`: `wayback` (as of publish + <= 2 days), `api` (arXiv / HF abstracts), `origin_live`
(fetched now; may be a later revision; `date_check` compares the page's own date to the publish date).
