# Live search smoke (Tavily): 2026-11-06

**Result: PASS.** Live Tavily search works end to end: the raw API, the `search` tool, and C2 in a live `cos_daily` brief. The brief's 3 "for you" stories all came from search.

## How the key is provided
- Network secret for `api.tavily.com` on the CS329Z cloud environment; the HTTPS proxy injects the Authorization header.
- The code still needs placeholders to select the provider: `SEARCH_PROVIDER=tavily SEARCH_API_KEY=proxy-injected`. The real key is never in the session.
- The first attempt (earlier today) failed with 401 because the key was then only an env var that the running session hadn't picked up.
- **For local dev / Railway** the real key must also be set there (`SEARCH_PROVIDER=tavily`, `SEARCH_API_KEY=...` in a gitignored `.env` or Railway variables). The proxy secret only exists in cloud sessions.

## Steps
| Step | Result | Numbers |
|---|---|---|
| 1. Raw POST /search (placeholder key) | PASS | HTTP 200, 3 results |
| 2. `search` tool, 3 queries, since = now-2d, until = now | PASS (with `topic: "news"`) | see table below |
| 3. Live `cos_daily` brief (test user `5a0e…0001`, `briefing_smoke` DB, Haiku 5.5) | PASS | C2: 6/6 searches ok, n_hits 5/6/3/5/4/2, 25 finds, no `search_unavailable`. C3 pool 41 = 26 digest + 15 personal (search). Brief: 4 stories, 15 sentences; **3 of 4 stories from search** (`u:` refs: TokenPost, Trending Topics, Tech Insider), 1 from the digest. Each search story has a 5–6-passage source pack. |
| 4. Bugs | see below | |

### Step 2: `topic: "general"` (current code) vs `topic: "news"` (same queries)
| query | general: hits / dated | news: Tavily raw → after `since` filter / dated |
|---|---|---|
| AI agents | 8 / 0 | 8 → 6 / 6 |
| humanoid robotics | 8 / 0 | 8 / 8 |
| grid-scale battery storage | 8 / 0 | 8 → 6 / 6 |

- `general` results contain **no `published_date` field at all**, so the `since` filter never applies. Hits are evergreen pages: "Top 100 Humanoid Robotics Companies", YouTube, Instagram, Facebook, ScienceDirect.
- `news` hits are all from the last 2 days and on-story (Agility Robotics' Nasdaq debut, India's 92 GWh storage VGF scheme, Cloudflare/Airbnb on agents).
- Tavily honours `start_date` at **day** granularity; the 4 hits dated the day of `since` were trimmed by `LiveSearch`'s `since` filter. All hits had url/title/snippet.
- **Recommendation: use `topic: "news"`.**

Local, **uncommitted** diff (also used for the step 3 run):
```diff
--- a/app/tools/search.py
+++ b/app/tools/search.py
@@ -83 +83 @@ class TavilyProvider(_HttpProvider):
-            "topic": "general",
+            "topic": "news",  # "general" returns no published_date (smoke 2026-11-06)
```

## Issues found
1. **`general` topic gives undated, evergreen results** (`app/tools/search.py:83`). Fix: the diff above.
2. **Google redirect URLs.** About half of `news` hits have url `https://www.google.com/url?...&q=<opaque>`, so source = "google.com". One reached a source pack as its citation URL (run 1). Fetch follows the redirect, so passages still load, but citations and source names are poor. The `q=` value is opaque, so it can't be unwrapped. Suggested fix in `TavilyProvider.search` (`search.py:~92`): take `source` and the cited URL from the final URL after fetch, or rank or drop google.com/url hits.
3. **Dates parsed naive.** Tavily sends RFC 2822 (`Thu, 05 Nov 2026 23:00:00 GMT`). `_parse_dt` (`search.py:24-35`) uses `%Z`, which yields a naive datetime. It works (`_aware_lt` strips tz), but `email.utils.parsedate_to_datetime` would give aware UTC. Many times are `23:00:00`, which looks date-only.
4. **Not search, seen in step 3.**
   - Both big-news digest stories were dropped in C7 ("no grounded sentences"): their only fetches failed (wsj.com 401, openai.com 403) and the packs got 0 passages, so the brief has no big-news slot.
   - The 3 search stories overlap heavily (all about Kimi / Mistral / open weights), so for-you diversity is weak.
5. **Environment (fixed locally, not code).** Run 1 hit `column "body_source" of relation "documents" does not exist`: `briefing_smoke` lacked migration `20261105000400_sources_provenance`. Every fetch-cache write failed, no passages were extracted for digest stories, and the brief had 3 stories, all from search. I applied that additive migration to the local `briefing_smoke` DB and reran (run 2, the numbers above). Run 1's script and packs were saved before its rows were deleted.

## Runs
- Run 1: job 121, brief deleted after its output was saved. 12 searches, all HTTP 200; C2 included `cos.search_loop` refinement.
- Run 2: job 123, brief `br_01m6sm2rrxt3562z3egb06zkh4`, status `rendering` (TTS job not run). The C2 search budget was capped at 6 at runtime (in the driver script, not the config) to stay within 25 searches.
- Trace and ledgers are in the session scratchpad (not committed).

## Usage
- **Tavily: 25 searches** (1 raw + 6 tool + 12 run 1 + 6 run 2) plus 1 rejected (401) earlier.
- **Model: $0.0217** from the Router ledger (run 1 $0.0116, run 2 $0.0101; Haiku 5.5).
- The OpenRouter account-wide usage rose more ($0.17) over the same window. That counter covers the whole account, and other processes are spending on the same key.
