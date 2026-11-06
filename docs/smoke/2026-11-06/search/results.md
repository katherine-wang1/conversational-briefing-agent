# Live search smoke (Tavily): 2026-11-06

**Result: BLOCKED. No valid Tavily key reaches this session, so steps 2 to 4 were not run.**

## How the key is provided
- Cloud environment "CS329Z" (personal) stores `SEARCH_API_KEY` and `SEARCH_PROVIDER` as **environment variables**.
  It has **no network secrets**, so the proxy injects nothing for api.tavily.com.
- These variables are **not set in this running session** (`SEARCH_API_KEY` empty, `SEARCH_PROVIDER` empty).
  Environment variables only take effect after the session or container restarts, and this one has not restarted since Kate added them.
- No `.env` in the repo (only `.env.example` with `SEARCH_API_KEY=` empty). `app/config/settings.py` reads the process env plus `.env`.

## Steps
| Step | Result | Detail |
|---|---|---|
| 1. Raw POST https://api.tavily.com/search | FAIL | env key absent; tried dummy `tvly-dummy` (proxy-secret path): HTTP 401 "missing or invalid API key" |
| 2. `search` tool, 3 queries | NOT RUN | the tool would return `search_unavailable` (provider is None) |
| 3. Live cos_daily brief | NOT RUN | C2 would degrade to digest-only, same as 2026-11-05 |
| 4. Bug diagnosis | partial (code read only) | see below |

Usage: 1 Tavily request (rejected with 401, so likely no credit used). $0 model spend.

## What Kate needs to do (either one)
1. **Restart / start a new session** on the CS329Z environment so `SEARCH_API_KEY` and `SEARCH_PROVIDER=tavily` appear in the process env, then re-run this smoke. Nothing else is needed in the cloud.
2. Or add the key as a **network secret** for host `api.tavily.com` (Authorization: Bearer) in the environment settings. Then the code still needs a non-empty placeholder `SEARCH_API_KEY` (e.g. `tvly-dummy`) plus `SEARCH_PROVIDER=tavily`.

For local dev or Railway, the key must also be set there (`SEARCH_PROVIDER=tavily`, `SEARCH_API_KEY=...` in a local `.env` that is gitignored, or in Railway service variables). The cloud environment variable does not carry over.

## Code observations (unverified live, check when the key works)
- `app/tools/search.py:83` sends `"topic": "general"`. Tavily documents `published_date` in results only for `topic: "news"`. With "general", most hits will likely have `published_at=None`. The `since` post-filter in `LiveSearch.run` (`search.py:~190`) then keeps every undated hit, so date filtering would rest entirely on Tavily's `start_date`/`end_date`. Possible minimal fix: use `"topic": "news"` (optionally with `"days"`) for news queries. Confirm by counting dated hits in step 2.
- The Bearer auth header format is correct: Tavily's 401 body names the API key, so it reads that header.
