# Technical design options

**Status:** Draft v0.1, for decision before writing `02-technical-design.md`
**Inputs:** PRD v0.6 (`01-PRD.md`), design explorations (`01a-design-explorations.md`)
**Constraints:** **$200 OpenRouter credit**, **$100 Tinker credit**, a solo builder, one quarter, and access from an iPhone.

This doc lays out the key technical decisions, the options for each, and a recommendation. Items marked 🔬 need a quick spike in week 1 to confirm, because public information was thin or conflicting.

---

## 0. TL;DR

| # | Decision | Recommendation |
|---|---|---|
| 1 | **Model strategy under $300** | Cheap models do the bulk: **Claude Haiku 5.5** for the chief of staff and the voice agent (via OpenRouter); **Sonnet 5.5 only for the newsroom**, if a dev-slice test shows it's worth it. Simulated users and judges on **Tinker** (a different model family, paid from separate credit). |
| 2 | **Agent harness** | **Our own thin orchestrator in Python** using an OpenAI-compatible client pointed at OpenRouter, with **our own (client-side) tools**. Managed Agents and Claude's built-in web tools are out: they aren't covered by these credits and can't be replayed. |
| 3 | **Tools and replay** | Every tool is an interface with a live and a replay backend. Replay search uses **SQLite FTS5**; live search uses a free tier (Tavily or Exa); live fetch uses Jina Reader or trafilatura. |
| 4 | **Replay archive** | Build it from date-addressable sources: Techmeme archives, arXiv listings, HN Algolia, the **Wayback Machine** (for front pages and blogs as of a date). ⚠️ Historical **HN points at brief time** aren't directly available (§4). |
| 5 | **Mobile + voice** | A **PWA** (installable web app) over HTTPS, with push-to-talk. Speech-to-text and text-to-speech start with **free browser APIs**, then a server fallback if they're flaky on iOS. Expo app or a phone number as plan B. |
| 6 | **Backend, data, auth, hosting** | **FastAPI** + **Supabase** (Postgres + Auth + row-level security) + one small always-on host (Fly.io or Railway) for the API and the scheduled jobs. About $0–10 a month, paid out of pocket. |
| 7 | **Memory** | Structured Postgres records. The LLM *proposes* updates; **code applies** the weighting, decay, and guards. |
| 8 | **Evaluation infrastructure** | A custom runner with an **LLM-call cache**, JSON-lines traces, and a DuckDB or pandas analysis layer. Design the evaluation to **reuse computation across configs**. |
| 9 | **Tinker** | (a) Simulated users and judges on Inkling-Small. (b) Optional "optimization" stretch: fine-tune a small scorer or judge and compare it to the prompted model. |

The two biggest consequences:
- **Evaluation must be redesigned to fit the budget.** The PRD's evaluation at full scale with mid-tier models would cost well over $1,000. With Haiku-tier models, computation reuse, and Tinker for simulators and judges, it fits (§1.4).
- **Replay forces client-side tools.** Server-side web search can't be pointed at a frozen archive, so tools must be ours. That conveniently makes us provider-agnostic.

---

## 1. Model strategy and budget

### 1.1 What the credits cover

| Credit | Gives access to | Notes |
|---|---|---|
| **OpenRouter $200** | Hundreds of models through one OpenAI-compatible API, including Claude (Haiku 5.5, Sonnet 5.5, Opus 5.5), GPT, Gemini, DeepSeek, Qwen | Prices are passed through roughly at provider rates. **Prompt caching for Claude via OpenRouter is reported to work, but third-party sources conflict** 🔬. Claude's built-in web search and Managed Agents aren't available this way. |
| **Tinker $100** | Thinking Machines' API: fine-tuning (LoRA / RL) and sampling of open-weight models; **serverless inference (beta) for Inkling and Inkling-Small** | Listed prices (October 2026): Inkling-Small about **$0.30 / $1.20 per M** tokens (prefill / sample); Inkling about $1.87 / $4.68; **cached prefill 80% off** ([pricing](https://thinkingmachines.ai/tinker/pricing/)). Model lineup and API shape need checking 🔬. |
| Not covered | Web search APIs, speech-to-text and text-to-speech, hosting | Mostly free tiers; about $0–15 a month out of pocket (§5, §6) |
| Possible extra | AWS credits via the course (Lecture 7 mentioned $200 via Laude Institute, unconfirmed) | Would give Claude on Bedrock. Worth asking about. |

### 1.2 Reference prices (per million tokens, input / output)

| Model | Price | Good for |
|---|---|---|
| Claude Haiku 5.5 | $0.10 / $0.50 | Bulk agent work: chief of staff, voice, follow-ups |
| Claude Sonnet 5.5 | $2 / $10 | The newsroom (quality-critical, shared, runs rarely) |
| Claude Opus 5.5 | $4 / $20 | Probably not affordable except for a few spot checks |
| Tinker Inkling-Small | $0.30 / $1.20 (cached prefill 80% off) | Simulated users, judges, fine-tuning experiments |
| Other cheap models on OpenRouter (Gemini Flash, DeepSeek, Qwen, GPT-mini tiers) | Roughly $0.1–0.5 input | Alternative judge or simulator families |

### 1.3 Options

| Option | Newsroom | Chief of staff | Voice | Sim users + judges | Pros | Cons |
|---|---|---|---|---|---|---|
| **A. Claude-tiered + Tinker** (recommended) | Sonnet 5.5 (or Haiku if the dev test shows parity) | Haiku 5.5 | Haiku 5.5 | Inkling-Small on Tinker | Strong agent quality at low cost; judges from a **different family**, which reduces self-preference bias; uses both credits | Two providers to integrate |
| B. All cheap, open models | Qwen / DeepSeek | Same | Same | Same or Tinker | Cheapest | Weaker tool use and faithfulness are likely; more prompt tuning |
| C. Sonnet everywhere | Sonnet | Sonnet | Haiku | Haiku | Best quality | **Evaluation unaffordable** (about 20× the cost of option A) |

**Research-integrity note:** the research config and the product must use the **same models per role** (PRD RG-5). Whatever model the chief of staff uses in evaluation is what Kate uses daily.

### 1.4 Budget model (option A, with the leaner evaluation in §8)

Assumptions:
- **Per run:** a newsroom run is about 300K tokens in and 15K out. A chief-of-staff run is about 150K in and 8K out. A session is about 4 turns over a cached context of about 20K.
- **Evaluation size:** the test set is 12 simulated users × 30 days = 360 user-days. The dev set is 6 users × 10 days.
- **Seeds:** 3 seeds for the main comparisons and 2 for the rest.

| Item | Calculation | OpenRouter | Tinker |
|---|---|---|---|
| Development and debugging | Prompt iteration, smoke runs | **$30–40** | $5 |
| Newsroom, replay (40 days, one digest per day shared by all configs and seeds) | 40 × about $0.75 (Sonnet) | $30 (or $2 on Haiku) | |
| Chief of staff, replay (Full, B2, A1 need their own runs; B1, B3, B4 reuse scores) | about 3 × 360 × 3 × about $0.02 | **about $65** | |
| Naive baseline B5 | 360 × 3 × about $0.02 + search | about $25 | |
| Voice agent in simulated sessions (6 configs, 2–3 seeds, mostly cached) | about 5,500 sessions × about $0.005–0.01 | **about $30–50** | |
| Simulated users in those sessions | about 5,500 × about $0.01 | | **about $55** |
| Judges (faithfulness sample, relevance, packaging, QA, injection) | about 50–80M tokens | | **about $25** |
| Live product (Kate 35 days + pilot about 10 users × 20 days) | Newsroom 1×/day on Sonnet + Haiku per user | about $30 | |
| **Total** | | **about $210–265** ⚠️ | **about $85** ✅ |

The OpenRouter side is **over budget by up to about 30%**. Levers, in order:
1. **Run the newsroom on Haiku if the dev test shows similar importance recall.** Saves about $50 across replay and live.
2. **Use 2 seeds instead of 3 for B1, B3, and B4.** Saves about $15.
3. **Trim the panel to 10 users.** Saves about $20–30.
4. **Get prompt caching working** on the cached voice and chief-of-staff contexts (verify 🔬). Could save 30–50% of voice cost.
5. **Ask about the AWS course credits** for headroom.

**The cost model must be measured, not assumed:** M2 includes a cost probe (one replayed day, every role) before the evaluation is sized.

---

## 2. Agent harness and orchestration

| Option | What it is | Pros | Cons |
|---|---|---|---|
| **A. Thin custom orchestrator** (recommended) | Python; OpenAI-compatible SDK pointed at OpenRouter (and Tinker); our own tool loop (about 200 lines); Pydantic models for structured outputs | Full control over contexts (needed for newsroom isolation), tools, caching, traces, and replay; provider-agnostic; easy to explain in a report | We write the loop and retries |
| B. PydanticAI | Typed agent framework that supports OpenRouter | Less boilerplate, typed outputs, works with many providers | Another abstraction to learn; tracing and replay hooks still need custom work |
| C. LangGraph / OpenAI Agents SDK | Graph or agent frameworks | Many features | Heavy for this; harder to control context isolation and call caching |
| D. Claude Managed Agents / Claude built-in web tools | Anthropic-hosted agent loop, scheduling, memory, web search | Least code | **Not payable with these credits**; **server-side tools can't be replayed** against the archive, which breaks RG-5 |

**Recommendation: A** (optionally with Pydantic for schemas).

The orchestrator owns three things:
1. **Role contexts:** what each role's prompt, tools, and inputs contain. This is how newsroom isolation is enforced (NEWS-2).
2. **The tool registry with live and replay backends.**
3. **The call cache and traces** (§8).

Structured outputs vary by model through OpenRouter. Use JSON-schema mode where it's supported, and validate with Pydantic plus a retry otherwise.

---

## 3. Tools: live and replay backends

| Tool | Live options | Replay backend |
|---|---|---|
| `search(query, since, until)` | **Tavily** (free 1,000 credits a month) or **Exa** (free credits); Brave Search API; OpenRouter's web plugin (paid per result) | **SQLite FTS5** (BM25) over the archive, with a date filter. Optional embedding re-rank later. |
| `fetch(url)` | **Jina Reader** (free tier) or self-hosted **trafilatura** / readability | The archived copy as of the replay date |
| `headline_source(name, at)` | RSS or HTML parsers for each source (Techmeme, HN API, trade-press front pages, Hugging Face daily papers) | The source's dated archive or Wayback snapshot nearest *before* `at` |
| Memory, bookmark, and save tools | Postgres via the user-scoped data layer | The same, in a `run_id` namespace |

Live search volume (Kate plus pilot, about 20 searches per user-day) is about 5,000–6,000 a quarter, which fits in free tiers or costs about $30–50 if paid. Replay search is free.

**Prompt-injection hygiene (TOOL-4):** tool results are wrapped in clearly marked untrusted-content blocks. Hidden text is stripped during extraction. The voice agent's tool set is narrow.

---

## 4. Replay archive construction (the main data risk)

| Source | How to get it as of a past date | Quality |
|---|---|---|
| **Techmeme** | Dated archive pages on techmeme.com (check whether they're daily or hourly 🔬) | Strong editorial signal for AI and tech |
| **arXiv** | API / listing by submission date | Complete |
| **Hacker News** | Algolia API: stories with `created_at` | ⚠️ **Gives *current* points, not points at brief time.** Using final points would leak hindsight (RG-3). Options: (a) use HN **front-page presence and rank** from Wayback snapshots of news.ycombinator.com; (b) use only comment and post *timing*; (c) drop HN as a brief-time signal in replay and run ablation A2 in the opposite direction. **Recommend (a), with (c) as a fallback.** 🔬 |
| **Lab and company blogs, trade press front pages** | **Wayback Machine CDX API** (snapshots by timestamp) + RSS history | Good for major sites; gaps for small ones (document them in the datasheet) |
| **Newsletters** | Public web archives (many have them) | Optional |
| **Hindsight labels** | Coverage count (primary): independent archive sources mentioning a story within 14 days. HN final points (secondary). | Computed after collection; never shown to agents |

**Window choice:** pick a window **after the training cutoffs of the agent models** (Haiku and Sonnet 5.5; check the published cutoffs 🔬) and with good Wayback coverage. If August–September 2026 is before a model's cutoff, move the window later (for example October 2026), or note the leakage risk.

**Size estimate:** about 30–60K items for a 40-day window for the AI beat, which is a few GB at most. SQLite handles it easily.

---

## 5. Mobile access and voice

### 5.1 Getting onto the iPhone

| Option | Lift | Experience | Cost | Notes |
|---|---|---|---|---|
| **A. PWA (installable web app)** (recommended) | Lowest: one web codebase | Push-to-talk in Safari or from the home screen; full chat UI; no app store | Free | Needs HTTPS (required for the microphone). **Test microphone access in home-screen mode early** 🔬 (historical WebKit bugs). Audio must start from a tap (autoplay rules). Behaviour with the screen locked is limited. |
| B. Expo (React Native) app | Medium | Native audio and better background behaviour; AirPods controls | Free on your own phone via Expo Go or a development build; **$99/year** Apple developer account for TestFlight distribution to the pilot | Reuses the same backend API. Good plan B if the PWA hurts on the commute. |
| C. Phone number (Twilio voice) | Low–medium | Call to get your brief; hands-free, CarPlay | About $1–2 a month for the number + about $0.01 per minute + speech services | Very "on the go"; identify the user by caller ID + PIN |
| D. Native Swift | High | Best | $99/year | Not worth it for the MVP |

### 5.2 Voice pipeline (cascade)

| Piece | Options | Recommendation |
|---|---|---|
| **Speech-to-text** | (1) Browser **Web Speech API** (free; supported in Safari, quality varies); (2) record with MediaRecorder, then server speech-to-text: **Groq Whisper** (cheap, free tier), Deepgram (free starter credit), OpenAI transcription | Start with (1); add (2) as a fallback switch if accuracy or reliability is poor on iOS 🔬 |
| **Text-to-speech** | (1) Browser **speechSynthesis** (free, robotic); (2) hosted: OpenAI TTS, ElevenLabs (free tier), Cartesia (free tier) | Start with (1) for development; switch the self-study to (2) if the voice quality hurts the experience (about $0–10 total) |
| **Latency path** | Stream LLM tokens → split into sentences → text-to-speech per sentence → play while generating | Required to hit about 1 s to first audio |
| **Turn model** | Haiku 5.5 with a cached brief and source packs | Measure time to first token through OpenRouter (it adds a small hop) 🔬 |

**Frontend stack:** React (Vite) PWA, or Next.js. A single-page app served by the backend keeps deployment to one service. Next.js on Vercel's free tier plus a separate API is also fine.

---

## 6. Backend, data, auth, jobs, and hosting

| Concern | Options | Recommendation |
|---|---|---|
| **Language** | Python (agents, evaluation, scraping libraries) vs. TypeScript end-to-end | **Python backend** (FastAPI) + TypeScript frontend |
| **Database** | **Supabase Postgres** (free tier) · Neon (free) · SQLite on a VM | **Supabase**: Postgres + Auth + **row-level security**, which enforces per-user isolation (DATA-1) in the database itself, not just in our code |
| **Auth** | Supabase Auth (magic link + Google) · Clerk · Auth.js | **Supabase Auth.** Invite-only by pre-creating users or with an allowlist table. |
| **Jobs and scheduling** | Postgres-backed queue (`SELECT … FOR UPDATE SKIP LOCKED`, or a library such as procrastinate) + an in-process scheduler (APScheduler) · Celery + Redis · Supabase pg_cron | **Postgres queue + APScheduler in a worker process.** No Redis to run. |
| **Hosting** | **Fly.io** or **Railway** (about $5 a month, always on) · Render (free tier sleeps, which breaks schedules) · a small VM (Hetzner about €4 a month) | **Fly.io or Railway**: one app with two processes (API and worker). HTTPS included. |
| **Replay and evaluation storage** | SQLite files + Parquet locally · object storage | **Local or laptop** for evaluation runs (they don't need the cloud); Supabase only for the live product |
| **Secrets** | Platform secret store | API keys only in host secrets / `.env` (never committed) |

Monthly out-of-pocket: about **$5–10** for hosting, plus any paid speech services.

---

## 7. Memory representation

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A. Structured records + LLM-proposed updates** (recommended) | Tables: `interests` (label, beat or subtopic, weight, confidence, evidence, last reinforced), `delivery_prefs`, `signals`, `open_questions`, daily `snapshots`. After each session an LLM turns the transcript into structured **signals**; **code** applies opportunity weighting, decay, and the concentration guard. | Deterministic, testable, easy to ablate (A3, B2); easy to compare with hidden profiles in evaluation; easy for users to edit | Fixed schema |
| B. Free-text memory notes (MemGPT-style) | The agent writes and reads prose memories | Flexible | Hard to evaluate interest recovery; drifts; hard to guard against over-curation |
| C. Memory library (for example Mem0) | Off-the-shelf extraction and storage | Fast to start | Less control; harder to enforce PRD rules MEM-3–6 |

Interest labels are open text, mapped to beat subtopics with a cheap LLM call, or with local embeddings (sentence-transformers, which are free) if deduplication becomes an issue.

---

## 8. Evaluation infrastructure (and making it affordable)

| Component | Options | Recommendation |
|---|---|---|
| **Runner** | Custom Python runner · Inspect AI (UK AISI evaluation framework) | **Custom runner**: the simulation is multi-agent and stateful across days, which fits Inspect's single-task model awkwardly. Borrow Inspect's ideas (logs, scorers). |
| **LLM-call cache** | A disk cache keyed by a hash of (model, prompt, params, seed tag) | **Required:** makes re-runs free and analysis reproducible |
| **Traces** | JSON lines per run · Langfuse (free cloud or self-hosted) | **JSON lines** (simple and portable); add Langfuse only if debugging gets painful |
| **Analysis** | DuckDB + pandas + notebooks | Yes |
| **Judges** | Tinker Inkling-Small (different family) · cheap OpenRouter model from another family | **Tinker** for the bulk; validate against Kate's labels (PRD §10.4) |

**Making the evaluation affordable: reuse computation across configs**

| Config | What it can reuse |
|---|---|
| B1 Importance-only | The digest only. No chief-of-staff LLM scoring; just brief writing. |
| B3 No exploration | The **same scored candidates** as Full; only code selection and brief writing differ |
| B4 No pre-fetch | The **same briefs** as Full; only the deep-dive phase differs |
| Full, B2, A1 | Need their own chief-of-staff runs (different memory or context) |
| Digest | **One per replay day, shared** across configs and seeds (a fixed shared input; note this in the report) |

**Proposed resizing of PRD §10.1** (to update in the technical design):

| | PRD v0.6 | Proposed |
|---|---|---|
| Simulated panel × test days | 15 × 40 = 600 | **12 × 30 = 360** test + 6 × 10 dev |
| Seeds | 3 for everything | 3 for Full / B2 / B5; 2 for others |
| Claim audit | about 1,500 claims | about 1,000 judged + 150 human-checked |
| Deep-dive question set | about 300 | about 250 |

This is still a meaningful scale for the course, and it fits the budget.

---

## 9. What to do with Tinker

| Option | What | Value | Effort |
|---|---|---|---|
| **A. Simulated users + judges on Inkling-Small** (recommended) | Serverless sampling (beta) | Uses the separate credit; **different model family from the agent**, which reduces self-preference in judging and simulation | Low, if the API is straightforward 🔬 |
| B. **"Optimization" stretch:** distil a small scorer or judge | LoRA fine-tune Inkling-Small on Claude-labelled relevance scores (or on Kate's ratings), then compare cost and quality with the prompted Haiku scorer | Hits the course's *optimization* topic; a nice extra results table | Medium. Optional, M5◇. Would revise the PRD non-goal on fine-tuning to "optional". |
| C. RL on the chief of staff's query generation with a simulator reward | Tinker RL | Research-y, interesting | High. Not for this quarter. |

---

## 10. Week-1 spikes (to settle the 🔬 items)

Each is about 1–3 hours:

1. **OpenRouter + Claude:** check that prompt caching works (look at the cache fields in usage), check structured-output support, and measure time to first token for Haiku 5.5.
2. **Tinker:** check that serverless sampling of Inkling-Small works (API shape, rate limits, real cost per 1M tokens).
3. **iPhone:** a minimal HTTPS PWA with push-to-talk. Test Web Speech recognition and speechSynthesis in Safari and in home-screen mode.
4. **Archive feasibility:** pull one day of Techmeme, the HN front page from Wayback, arXiv, and 3 trade-press front pages for a candidate window. Confirm the granularity and the HN-points workaround.
5. **Model cutoffs:** confirm the training cutoffs of the chosen models and pick the replay window.
6. **Cost probe:** one replayed day through every role. Measure real tokens and cost, then re-run §1.4.

---

## 11. Decisions needed from Kate

1. **Model strategy:** option A (Claude-tiered via OpenRouter + Tinker for simulators and judges)?
2. **Newsroom model:** decide by a dev-slice test (Sonnet vs. Haiku on importance recall)?
3. **Evaluation resizing** (§8): OK to shrink to 12 users × 30 days with mixed seeds?
4. **Mobile:** PWA first, with Expo or a phone number only if the self-study shows pain?
5. **Hosting stack:** Supabase + Fly.io/Railway (about $5–10 a month out of pocket)?
6. **Tinker stretch** (option B): include the "optimization" experiment as optional M5◇?
7. **AWS credits:** worth asking course staff whether they're available?

### Sources
- [Thinking Machines: Tinker pricing](https://thinkingmachines.ai/tinker/pricing/) · [LLM Reference: Tinker models](https://www.llmreference.com/provider/tinker/models) · [Sacra: Thinking Machines](https://sacra.com/research/thinking-machines)
- OpenRouter caching and pricing (third-party, verify): [aicostlab: Claude via OpenRouter](https://aicostlab.pages.dev/model/openrouter-anthropic-claude-opus-4-5) · [Finout: Claude pricing 2026](https://www.finout.io/blog/claude-pricing-in-2026-for-individuals-organizations-and-developers?hs_amp=true)
- Search API pricing and PWA notes: see `01a-design-explorations.md` sources.
- Claude model prices: Anthropic's published model table, as of October 2026.
