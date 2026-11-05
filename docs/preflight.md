# Preflight (2026-11-05, orchestrator session)

Checked from the cloud build container through its egress proxy. Honest client identification only; no bot-check circumvention.

## Network
| Host | Result | Notes |
|---|---|---|
| web.archive.org (incl. CDX API, `id_` snapshots) | **BLOCKED** | Egress policy: `Blocked by egress policy` over http; TLS tunnel dropped after 10 s over https. Same for wayback.archive.org, web-beta.archive.org, archive.ph. |
| archive.org | OK | `archive.org/wayback/available?url=…&timestamp=…` works (tells you the closest snapshot, but the snapshot itself is on web.archive.org, which is blocked). |
| www.techmeme.com | Partial | Front page 200. Dated archive pages `/YYMMDD/pHH` return a **Cloudflare challenge (403)** to this client. `/river?date=YYYYMMDD` returns 200. |
| hn.algolia.com | OK | |
| news.ycombinator.com | OK | `/front?day=YYYY-MM-DD` returns 200 (past front pages; points shown are current, not as-of). |
| export.arxiv.org | OK | |
| huggingface.co | OK | `/api/daily_papers?date=YYYY-MM-DD` returns 200. |
| openrouter.ai | OK | |
| Tinker | OK via `tinker.thinkingmachines.dev/services/tinker-prod` (healthz ok). `api.tinker.thinkingmachines.ai` is blocked (502 on CONNECT). |
| Beat domains (openai.com RSS, techcrunch, utilitydive, therobotreport, arstechnica, reddit RSS) | OK | Direct feeds only expose recent items. |
| Search APIs (api.tavily.com, api.exa.ai, api.search.brave.com) | reachable | No key set. |

## Keys (presence only)
- `OPENROUTER_API_KEY`: set. Account usage at session start: $1.85 of $200.
- `TINKER_API_KEY`: set.
- `SEARCH_PROVIDER` / `SEARCH_API_KEY`: **not set**.
- TTS / STT keys: not set.

## Tooling
- Python 3.13, venv + `pip install -e ".[dev]"`: OK; `pytest` 7 passed; `ruff` clean.
- Docker: daemon was not running; started `dockerd` manually, now works. Postgres 16 binaries are also installed at `/usr/lib/postgresql/16`.
- Node 22 available.

## Impact
- **W-A (archive): most affected.** Wayback-based collection (HN front page as-of snapshots, outlet RSS/front pages, article bodies as of publish time, Techmeme via Wayback) cannot run until `web.archive.org` is allowed. Directly collectable now: HN Algolia, HN `/front?day=`, arXiv, HF daily papers, Techmeme river (if it parses), current RSS.
- W-B: live spikes possible (OpenRouter, Tinker).
- W-E: live search returns "search unavailable" until a key is added.
