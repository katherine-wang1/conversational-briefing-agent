# Week-1 spikes (TDD §27)

## Archive

Spike 4 (W-A), run 2026-11-05 from the cloud build container with the honest User-Agent
`conversational-briefing-agent/0.1 (research project)`. Everything below is what we actually observed; where
something could not be measured, it says so. Raw numbers: `docs/archive-datasheet.md` (generated) and the
smoke shards on branch `data/archive-raw` (`shards/direct/`).

### What was run

* **Smoke collection** of every reachable source for **2026-10-30 .. 2026-10-31** (`--sources reachable`,
  bodies capped at 80 URLs), then a resume run (everything skipped, no refetch) and a shard export ->
  `rebuild` round trip (all 7 tables identical, row for row) and a push to `data/archive-raw` + rebuild
  from the pushed branch (identical counts).
* **Wayback availability probes** for the whole window 2026-08-01 .. 2026-10-31 via
  `archive.org/wayback/available` (the only Wayback endpoint reachable here): HN and Techmeme front pages
  at 06:00 and 18:00 UTC every day; every feed / outlet front page in `config/beats/*.yaml` at 12:00 UTC once
  a week (15 probe days).
* **Not run:** anything that needs `web.archive.org` (CDX queries, snapshot contents, Wayback article
  bodies). The host is blocked by the cloud egress policy. These collectors are built and tested against a
  clearly labelled synthetic CDX fixture and run unchanged on Kate's laptop (`eval/archive/README.md`).

### Per source

| source | how | availability Aug-Oct 2026 | granularity | notes |
|---|---|---|---|---|
| **Techmeme** front page | Wayback snapshots (laptop) | **not measured** (web.archive.org blocked). Availability API: a capture on the same UTC day on **88/92 days (96%)**; closest capture within 1 h of 06:00Z on 56/92 days, of 18:00Z on 65/92 | Wayback: as captured (closest-capture deltas suggest several per day); collector keeps <= 1 per hour | Techmeme's own dated archive `/YYMMDD/hHHMM` (5-minute granularity, back to 2005) returns a **Cloudflare challenge (403)** to us; recorded, not circumvented (`techmeme_archive`). |
| Techmeme river | direct `/river` | **last ~5 days only** (2026-10-31 .. 11-05 on the day we fetched); `?date=` is ignored | every headline with its post time (ET, minute) | Only 5 river items fall inside the window (2026-10-31). Useful for forward collection, not backfill. |
| **HN front page** (as-of) | Wayback snapshots (laptop) | **not measured** (blocked). Availability API: a capture on the same UTC day on **89/92 days (97%)**; within 1 h of 06:00Z on 52/92 days, of 18:00Z on 29/92; median distance to probe 69 min | collector keeps <= 1 per hour | Rank, points and comments as shown at capture time (no hindsight). |
| HN `/front?day=` | direct | expected every day (2/2 smoke days, 30 stories per page, 2 pages) | one list per day | Ranking and points are **as of the fetch (weeks later)**: stored as `hn_front_day` / `hindsight_listing`, observed at fetch time, rank and points only in `extra.hindsight`. Labels only. robots.txt `Crawl-delay: 30` honoured. |
| HN Algolia stories | direct | every day (1,021 stories on 10-30, 685 on 10-31, Saturday) | per story, `created_at` to the second | Observed at submission time; points / comment counts are as of fetch (hindsight, labels only). |
| HN comments | direct (Algolia items) | 12,883 comments for 118 of the 120 front-page stories (2 had none) | per comment, `created_at` | Comments written after the window are kept; replay filters by `created_at`. |
| arXiv cs.CL / cs.LG / cs.AI / cs.RO | direct API by `submittedDate` | every day (smoke: CL 83+64, LG 180+145, AI 183+124, RO 74+37 on 10-30 / 10-31) | per paper | Observed at the estimated **announcement** time (20:00 ET, 14:00 ET cutoff, weekends shifted; holidays not modelled). Abstracts stored as documents. robots.txt disallows crawlers on export.arxiv.org, but arXiv's API terms designate it for programmatic use at <= 1 request / 3 s, which we follow. |
| HF daily papers | direct API | weekdays (39 papers on Fri 10-30; Sat 10-31 empty) | one list per day | API gives only the listing date: observed at the end of that day (conservative). Upvotes are as of fetch: hindsight. Summaries stored as documents. |
| Lab / company feeds | direct (feed fetched now) + Wayback (laptop) | Direct feeds reach back to: OpenAI **2015** (1,359 items), HF blog **2020** (897), DeepMind 2025-11 (100), IEEE robotics 2026-08-18, Energy-Storage.news 2026-09-08, import_ai 2026-06; most others only 1-3 weeks (TechCrunch, Verge, MIT TR, Utility Dive, DCD, pv magazine, NVIDIA, Carbon Brief, Electrek, Robot Report). Wayback availability API, weekly probes, same-day capture: openai_news 6/14, techcrunch_ai 6/15, carbon_brief 6/15, electrek 6/15, canary_media 6/15, utility_dive / dcd / heatmap / hf_blog / pv_magazine / energy_storage_news ~5/15, deepmind 4/15, meta_ai_blog 3/15, verge / mit_tr / robot_report 2/15, import_ai / robotics247 1/15, **ars_ai, nvidia_blog, ieee_robotics, techcrunch_robotics, dc_frontier, latitude_media, eia_today, reddit_ml, reddit_robotics: 0/15** | feed entries carry publish times | Feeds with a long history (OpenAI, HF blog, DeepMind) can be backfilled directly with `feed_pubdate` observations. For short feeds the window depends on Wayback, and coverage of feed URLs is sparse: expect gaps of days to weeks. |
| Outlet front pages | Wayback (laptop) | anthropic_news 13/15 same-day, canary_media 6/15, heatmap 5/15, meta_ai_blog 3/15, robotics247 1/15, latitude_media 0/15 | collector keeps <= 1 per 6 h | Generic title+link+position parser. |

The availability API returns only the single closest capture to each probe, so "same-day capture" is a
**lower bound** for daily coverage and says nothing about how many captures a day has. Some probes came
back empty (e.g. 11 of 184 HN probes); some of those may be throttling artefacts, not missing captures.

### Article bodies

* **Wayback (nearest capture after publish, <= publish + 2 days): not measured** (web.archive.org blocked).
* **Origin fallback (`body_source=origin_live`)**, 104 smoke URLs from the HN front page, Techmeme river and
  feeds: **73 extracted (70%)**; 15 Cloudflare bot checks (openai.com, energy-storage.news,
  theinformation.com, ...; not circumvented), 12 HTTP 403, 3 empty extractions, 1 robots.txt disallow.
  Of the 80 stored origin bodies, the page's own date matches the publish / first-seen date (+-2 days) for
  57, does not for 22 (not investigated; HN front pages often link to older pages), unknown for 1. Origin bodies may be later revisions
  than what was visible at publish time; they are flagged and Wayback bodies replace them when available.

### HN front-page Wayback coverage (TDD OQ-14)

Measured only through the availability API: **89 of 92 days (97%) have at least one front-page capture
on the same UTC day** (August 30/31, September 30/30, October 29/31). That is above the 80% threshold in
OQ-14, so **keep HN as a brief-time signal for now**. This is not yet the final answer: whether each day has
a capture *near brief time* (06:00 UTC: within 1 h on only 52/92 days by this probe) and whether the
captures parse needs the CDX listing and the snapshots themselves, which the laptop run produces. Re-check
with `docs/archive-datasheet.md` after it.

### Wayback throttling

`archive.org/wayback/available` answered **HTTP 429** after ~300 requests at 1 request/s, then allowed
bursts of ~30 requests a minute between multi-minute 429 periods (~15-20 requests/minute sustained). The
fetcher now honours `Retry-After`, slows a host down on 429 and recovers slowly. If web.archive.org
throttles the laptop the same way, the full Wayback run (~40-64k requests) takes ~35-70 h instead of
~12-18 h; `eval/archive/README.md` gives a staged order (front pages first, then AI feeds, then bodies).

### Config changes (`config/beats/*.yaml`)

`verified: true` now means "fetched and parsed live on 2026-11-05". Fixed: `dc_frontier` (old `/rss` was
404; now the feed the home page advertises), `robotics247` (`/rss` serves the HTML home page, so it is a
`frontpage` source now). Left `verified: false`: `reddit_ml`, `reddit_robotics` (reddit's robots.txt
disallows generic crawlers), `eia_today` (eia.gov robots.txt disallows `/rss`); they can only come from
Wayback copies, and Wayback has 0/15 same-day captures for the reddit feeds.
