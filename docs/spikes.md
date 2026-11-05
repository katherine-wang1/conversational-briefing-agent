# Week-1 spikes

<!-- Each workstream owns one section. W-B owns "Models" (below); W-A owns "Archive". -->

## Models

*W-B, 2026-11-05. TDD §27 spikes 1 (OpenRouter), 2 (Tinker), 5 (model cutoffs).*

**Method.** `python -m eval.bakeoff.spike --live --cap 1.0` ran each model through the production `Router` (ledger, budget, repair). Per model it made:
- one tool-calling request (OpenAI function format, one `search` tool);
- one JSON-schema request (a 3-field `Verdict`; `strict: true`);
- two identical requests over a ~8–11K-token system prefix marked `cache_breakpoint=True`, 2 s apart;
- two streaming requests to measure time to first token (TTFT);
- one "what is your training cutoff?" question.

Raw usage blocks and outputs are in `eval/bakeoff/spike_2026-11-05/results.json`, and every call is in `ledger.jsonl` next to it. TTFT was measured from this cloud build container, not from Railway's region, and each number comes from only 2 samples. Treat the latencies as rough indications.

### OpenRouter results

Prices are USD per 1M tokens (input / output / cached read), from `GET /api/v1/models` on 2026-11-05.

| Model (snapshot) | Price in / out / cache-read | Tools | JSON schema | Cache fields observed | 1st → 2nd call cost on the long prefix | TTFT p50 | Reasoning by default |
|---|---|---|---|---|---|---|---|
| `anthropic/claude-haiku-5.5` (20261007) | 0.10 / 0.50 / 0.01 | ✅ | ✅ first try | `cache_write_tokens` 10,900 → `cached_tokens` 10,900 | $0.00137 → $0.00011 (−92%) | 679 ms | adaptive; 0 reasoning tokens on tool/JSON/cache probes, but 153 output tokens for a one-line cutoff answer |
| `openai/gpt-6-luna` (20260922) | 0.10 / 0.50 / 0.01 | ✅ | ✅ first try | write 7,794 → cached 7,794 | $0.00098 → $0.00008 (−92%) | 1,045 ms | yes (a few reasoning tokens) |
| `google/gemini-3.5-flash-lite` (20260721) | 0.30 / 2.50 / 0.03 | ✅ | ✅ first try | cached 9,950 on **both** calls (plus write 9,950 on the 1st) | $0.00113 → $0.00031 (−73%) | 543 ms | mandatory (min `minimal`) |
| `deepseek/deepseek-v4.1-flash` (20260910) | 0.045 / 0.50 / 0.018 | ✅ | ✅ first try (330 output tokens, 4.1 s) | **no cache hit** (0 / 0) | $0.00036 → $0.00082 (two different upstreams) | 1,174 ms (1 of 2 runs: no text in 60 tokens) | yes, `high`; ate the whole budget on the cutoff question |
| `qwen/qwen3.8-flash` (20260826) | 0.15 / 0.47 / 0.016 | ✅ (2.7 s) | ✅ first try | 0 → cached 9,984 | $0.00151 → $0.00017 (−89%) | 1,835 ms | yes; empty answer to the cutoff question |
| `mistralai/mistral-small-2603` | 0.15 / 0.60 / 0.015 | ✅ | ✅ first try | **no cache hit** (0 / 0) | $0.00146 → $0.00160 | **437 ms** | off |
| `anthropic/claude-sonnet-5.5` (20260928), mid-tier | 2.00 / 10.00 / 0.10 | ✅ | ✅ first try | write 10,900 → cached 10,900 | $0.0273 → $0.0012 (−96%) | 590 ms | adaptive; none observed |

**Findings (spike 1)**
- **Cost field.** `usage.cost` (USD) is present on every OpenRouter response, both non-streaming and in the final streamed chunk. It matched the account: the ledger summed $0.047433 for OpenRouter, and `GET /api/v1/key` `usage` moved by $0.047433. `usage.cost_details.upstream_inference_*` splits the cost into prompt and completion.
- **Caching fields.**
  - Responses report `usage.prompt_tokens_details.cached_tokens` and `cache_write_tokens` (OpenRouter's name for cache creation). Both are captured: `cached_tokens` → `Usage.tokens_cached`. The raw block is kept in the spike results; `cache_write_tokens` is not in the contract yet.
  - Anthropic models need the explicit `cache_control` breakpoint. The client sends it automatically for `Message.cache_breakpoint` on `anthropic/*` and `google/gemini*`.
  - Writes cost 1.25× input (`input_cache_write` in the catalog). Reads cost 0.1× for Haiku 5.5 and 0.05× for Sonnet 5.5.
  - OpenAI, Qwen and Gemini cache implicitly as well.
- **No caching** was seen for DeepSeek V4.1 Flash or Mistral Small 4.
  - DeepSeek's two calls were served by different upstream providers at different prices. Caching and stable cost would need provider pinning, which works through `ModelRole.params`, e.g. `{"provider": {"order": [...], "allow_fallbacks": false}}`.
- **Reasoning models need explicit control.**
  - DeepSeek V4.1 Flash, Qwen 3.8 Flash and GPT-6 Luna reason by default, which adds output tokens and latency. With a small `max_output_tokens` they can return empty text: DeepSeek and Qwen returned nothing for the 300-token cutoff question.
  - For any such role, set `params: {reasoning: {effort: none}}` (or `low`) in `config/models.yaml`. This was not live-tested here.
- **Tool calling and JSON-schema mode** worked first try on all 7 models. No repair call was needed.
  - All 7 list `structured_outputs` in their catalog entry, so all got `response_format: json_schema`.
  - The prompt-mode fallback and the repair path are covered by mocked tests only.

### Tinker results (spike 2)

- **Endpoint.** Tinker has an **OpenAI-compatible API (beta)** at `https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1`, with the same bearer `TINKER_API_KEY`. `TinkerClient` is plain httpx over this endpoint.
  - The official `tinker` SDK (0.37.0, PyPI) also works. Its default base URL is already `https://tinker.thinkingmachines.dev/services/tinker-prod`, so no `TINKER_BASE_URL` override is needed. `ServiceClient().get_server_capabilities()` listed 35 models.
  - `api.tinker.thinkingmachines.ai` is blocked here, but nothing needs it.
- **Model id.** Serverless Inkling-Small is `thinkingmachines/Inkling-Small:peft:262144:sampling-nvfp4`. The `inkling-small` and `inkling` aliases in `config/models.yaml` map to these ids inside `TinkerClient`.
- **Reasoning is on by default** (effort 0.9). The first probe spent all 40 output tokens in `reasoning_content` and returned no answer. Use `params: {reasoning_effort: none}` for simulators and judges that must answer briefly. The spike ran with that setting.
- **Inkling-Small, `reasoning_effort: none`:**

| Check | Result |
|---|---|
| Plain chat latency | 0.9–1.4 s (short prompts) |
| Streaming TTFT | ~930 ms, **equal to total latency**: the short answer arrived as one chunk, so incremental streaming wasn't observable |
| JSON (prompt mode, validated) | ✅ first try, 1.3 s |
| Tools | not used (no tool calling on this endpoint; `TinkerClient` rejects tools) |
| Prefix caching | `usage.prompt_tokens_details.cached_tokens`: 0 → **7,680 of 7,802** on the repeat call |
| Cost on the long prefix (price table) | $0.00235 → $0.00050 (**−79%**; list price says cached input −80%) |

- **Cost.** Tinker returns **no cost field**, so the ledger prices calls from the table in `app/llm/tinker.py`.
  - Serverless rates: Inkling-Small $0.30 input / $0.06 cached / $1.20 output per 1M; Inkling $1.00 / $0.17 / $4.05. Source: thinkingmachines.ai/tinker/pricing, "as of October 19, 2026".
  - 02a §1.2 quoted $0.30 / $1.20 for Inkling-Small: that's the serverless rate. Non-serverless sampling of `thinkingmachines/Inkling-Small` is $0.58 / $1.44.
- **Real cost not confirmed.** The billing API (`tinker.AdminClient().get_billing_usage`, hourly buckets) returned **no events** for the last 12 h when checked. Either serverless usage isn't reported there yet, or it lags. Total Tinker spend by price table: **$0.0030**.
- **Rate limits.** Neither the docs nor any response mention them. None were hit at this volume.

### Training cutoffs (spike 5)

OpenRouter's `/api/v1/models` has a `knowledge_cutoff` field, but it is **null for every model tested**. Published cutoffs below come from provider docs. Self-reported answers come from asking the model (`max_output_tokens` 300) and are **unreliable**: for example, both Claude models gave dates 1.5 years earlier than the published cutoff.

| Model | Published cutoff (source) | Snapshot / release | Self-reported (unreliable) |
|---|---|---|---|
| Claude Haiku 5.5 | **Jun 2026** reliable knowledge + training data (platform.claude.com models overview) | 2026-10-07 | "January 2025" |
| Claude Sonnet 5.5 | **Jun 2026** (same page) | 2026-09-28 | "early 2025" |
| GPT-6 Luna | **May 18, 2026** (developers.openai.com model page) | 2026-09-22 | "June 2024" |
| Gemini 3.5 Flash-Lite | not published (the model page shows only "Latest update: July 2026") | 2026-07-21 | "March 2026" |
| DeepSeek V4.1 Flash | not published (HF card) | 2026-09-10 | (empty: reasoning used the budget) |
| Qwen 3.8 Flash | not published (HF card) | 2026-08-26 | (empty: reasoning used the budget) |
| Mistral Small 4 (2603) | not published (HF card) | 2026-03-16 | "June 2024" |
| Tinker Inkling-Small | not published (Tinker docs) | n/a | "January 2026" |

**Replay-window implication.** For the Claude 5.5 and GPT-6 Luna families, start replay windows **on or after 2026-07-01**. For models with no published cutoff, only the release date bounds the cutoff, so their cutoff could be as late as **Aug–Sep 2026** (Qwen 3.8, DeepSeek V4.1). Either pick windows from **Sep/Oct 2026**, or keep those families out of roles where knowing the news ahead of time matters (newsroom importance, chief-of-staff scoring).

### Recommendation: `dev_default`

**`anthropic/claude-haiku-5.5`**, now set in `config/models.yaml` (`dev_default`):
- cheapest tier ($0.10 / $0.50);
- tool calling and strict JSON-schema mode first try;
- explicit prompt caching with reported cached tokens and cache reads at 10% (−92% on a cached prefix);
- provider-reported cost;
- TTFT ~0.7 s;
- no reasoning tokens on the tool, JSON and cache probes. Adaptive thinking does show on open questions, so set `reasoning: {effort: none}` in `params` if that matters for a role.

Runner-up is `openai/gpt-6-luna`: same price, but slower TTFT and reasoning on by default. For latency-critical `voice.turn`, `mistralai/mistral-small-2603` had the best TTFT (437 ms) but no caching. Let the bake-off decide.

Caveat for evaluation: Claude in the agent roles keeps judges and simulators on Tinker Inkling (a different family), as the plan intends.

### Spend

| | USD |
|---|---|
| OpenRouter: `GET /api/v1/key` `usage` before → after | $1.846992 → $1.894429: **delta $0.047437** (spike ledger $0.047433 + live smoke test $0.000004). `usage_daily` equals the delta, so no other session spent on this key meanwhile |
| Tinker (price table; billing API showed no events yet) | $0.0030 |
| **Spike total** | **≈ $0.050** |

Two extra live smoke tests (`LLM_LIVE_TESTS=1 pytest -m live`) spent less than $0.001.

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
