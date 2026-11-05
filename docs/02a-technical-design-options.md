# Technical design options

**Status:** Draft v0.1, for decision before writing `02-technical-design.md`
**Inputs:** PRD v0.6 (`01-PRD.md`), design explorations (`01a-design-explorations.md`)
**Constraints:** **$200 OpenRouter credit**, **$100 Tinker credit**, a solo builder, one quarter, and access from an iPhone.

This doc lays out the key technical decisions, the options for each, and a recommendation. Items marked 🔬 need a quick spike in week 1 to confirm, because public information was thin or conflicting.

---

## 0. TL;DR

| # | Decision | Recommendation |
|---|---|---|
| 1 | **Model strategy under $300** | **No default provider.** Pick the cheapest model per role that clears quality bars in a small bake-off on OpenRouter (§1.3). Simulated users and judges come from a **different family** than the agent (Tinker credit is a good fit). |
| 2 | **Agent harness** | **We build it:** our own orchestrator in Python, our own tools, memory, and selection (PRD §6.0). Models are called through a thin, provider-agnostic interface. No hosted agent platforms or provider-side tools. |
| 3 | **Tools and replay** | Every tool is an interface with a live and a replay backend. Replay search uses **SQLite FTS5**; live search uses a free tier (Tavily or Exa); live fetch uses Jina Reader or trafilatura. |
| 4 | **Replay archive** | Build it from date-addressable sources: Techmeme archives, arXiv listings, HN Algolia, the **Wayback Machine** (for front pages and blogs as of a date). ⚠️ Historical **HN points at brief time** aren't directly available (§4). |
| 5 | **Mobile + voice** | A **PWA** (installable web app) over HTTPS, with push-to-talk. Speech-to-text and text-to-speech start with **free browser APIs**, then a server fallback if they're flaky on iOS. Expo app or a phone number as plan B. |
| 6 | **Backend, data, auth, hosting** | **FastAPI** + **Supabase** (Postgres + Auth + row-level security) + one small always-on host (Fly.io or Railway) for the API and the scheduled jobs. About $0–10 a month, paid out of pocket. |
| 7 | **Memory** | Structured Postgres records. The LLM *proposes* updates; **code applies** the weighting, decay, and guards. |
| 8 | **Evaluation infrastructure** | A custom runner with an **LLM-call cache**, JSON-lines traces, and a DuckDB or pandas analysis layer. Design the evaluation to **reuse computation across configs**. |
| 9 | **Tinker** | (a) Simulated users and judges on Inkling-Small. (b) Optional "optimization" stretch: fine-tune a small scorer or judge and compare it to the prompted model. |

The two biggest consequences:
- **Design for a lean budget, not the ceiling.** Naive agent loops with mid-tier models would blow through the credits. Lean design rules (code does deterministic work, snippets before pages, small calls, caching, reuse) target **about $100–130 total**, with roughly half the credits as buffer (§1.4).
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
| A. Claude-tiered + Tinker (example of one outcome of D) | Sonnet 5.5 (or Haiku if the dev test shows parity) | Haiku 5.5 | Haiku 5.5 | Inkling-Small on Tinker | Strong agent quality at low cost; judges from a **different family**, which reduces self-preference bias; uses both credits | Two providers to integrate |
| B. All cheap, open models | Qwen / DeepSeek | Same | Same | Same or Tinker | Cheapest | Weaker tool use and faithfulness are likely; more prompt tuning |
| C. Sonnet everywhere | Sonnet | Sonnet | Haiku | Haiku | Best quality | **Evaluation unaffordable** (about 20× the cost of option A) |

| **D. Model-agnostic, chosen per role by a bake-off** (recommended, superseding A as the decision process) | Best of the candidates | Best of the candidates | Best of the candidates | A different family from the agent | Picks the cheapest model that clears quality bars per role; the orchestrator is provider-agnostic anyway | Needs a small dev-slice bake-off (about $5–10) |

**Claude isn't required.** Any model on OpenRouter that handles tool calling and structured output reliably can fill a role. Candidates per role for the bake-off:

| Role | What matters most | Candidates |
|---|---|---|
| Newsroom | Judgement, tool use over many pages, faithfulness | Sonnet 5.5, Haiku 5.5, a Gemini Flash/Pro tier, DeepSeek, Qwen |
| Chief of staff | Tool use, structured scores, faithful writing, cost (runs per user per day) | Haiku 5.5, Gemini Flash, DeepSeek, Qwen, GPT-mini tier |
| Voice agent | **Latency**, faithfulness, prompt caching | Haiku 5.5, Gemini Flash, GPT-mini tier |
| Simulated users and judges | A **different family** from the agent; cost | Tinker Inkling-Small, or whichever cheap family the agent doesn't use |

Bake-off metrics on about 5 dev days: tool-call success rate, schema validity, claim faithfulness (judged), importance recall (newsroom), time to first token (voice), and cost per run. Free models on OpenRouter are fine for development but not for evaluation: they're rate-limited and can change without notice.

**Research-integrity note:** the research config and the product must use the **same models per role** (PRD RG-5). Whatever model the chief of staff uses in evaluation is what Kate uses daily. Once the bake-off picks them, models are pinned.

**OpenRouter vs. Tinker costs:** they're **separate credit pools with separate price lists**.
- **OpenRouter** passes through each provider's own price (it charges its fee when you buy credits, not per token), so a model costs roughly what its provider charges.
- **Tinker** prices its own models: Inkling-Small at about $0.30 / $1.20 is actually **more expensive per token than Haiku 5.5** ($0.10 / $0.50).

So Tinker is useful because it's **extra money and a different model family**, not because it's cheaper.

### 1.4 Budget: where the tokens go, and a lean target

**The credits ($200 OpenRouter + $100 Tinker) are a ceiling, not a target.** The plan aims for **about $100–130 total**, leaving roughly half as a buffer.

#### Why naive agents get expensive

Cost is driven by **input tokens**, and agents pile them up in three ways:

1. **Reading pages.** An extracted article is about 2–5K tokens. An agent that reads 40 pages has read 100–200K tokens before it writes anything.
2. **The loop re-sends everything.** In a tool loop, every model call re-sends the whole conversation so far: the system prompt plus every earlier tool result. Ten steps whose context grows 10K → 100K cost about 550K input tokens in total, not 100K. This is the biggest hidden multiplier.
3. **Evaluation multiplies everything.** About 360 user-days × 6 configs × 2–3 seeds is about 5,000–6,500 simulated days. Each one has a chief-of-staff run and a conversation where two models talk, and each turn re-sends the history on both sides.

The first estimate (about $210–265 on OpenRouter) assumed **naive loops** like that, with mid-tier models. That's the ceiling case, not the plan.

#### Design rules that cut tokens (5–10×)

| # | Rule | Effect |
|---|---|---|
| L1 | **Code does the deterministic work; the model does the judgement.** Fetching front pages and feeds, extracting text, deduplicating headlines, and counting coverage are code. The model sees compact, structured inputs. | The newsroom sees about 50K tokens of headlines and metadata instead of 300K+ of pages |
| L2 | **Headlines and snippets first, full pages only for selected stories.** Full text is fetched only for the stories that make a brief (source packs). | Most candidates cost about 100 tokens, not 3K |
| L3 | **Small calls instead of long loops.** Map-reduce: one cheap call per page returns a short passage summary. The main call sees the summaries. Tool results are compacted before they're added to a loop. | No growing context |
| L4 | **Hard token budgets per run**, enforced by the orchestrator, with logging when they're hit | No runaway runs |
| L5 | **Prompt caching** for stable prefixes (system prompts, digest, brief + source packs in the voice context), where the chosen model supports it | Cached tokens cost about 10–25% of normal |
| L6 | **Share and reuse:** one digest for everyone; content-hashed source packs shared across users; evaluation configs reuse scored candidates (§8) | Pay once, use many times |
| L7 | **LLM-call cache:** never pay twice for an identical call (re-runs, analysis, debugging) | Re-runs are free |
| L8 | **Short simulated sessions** (brief + 1–3 turns on average) and **sample-based judging** | Evaluation scales gently |
| L9 | **Cheapest model per role that passes the bake-off** (§1.3) | 5–20× cheaper than mid-tier everywhere |

These rules are also good engineering: smaller, cleaner contexts tend to improve faithfulness. The cost and quality trade-off is reportable under the course's *optimization* topic.

#### Lean estimate

Assumes a cheap-tier model at about $0.10–0.30 per M input tokens for most roles, and a mid-tier model (about $2 per M) for the newsroom only if the bake-off says it's needed.

| Unit | Lean token budget | Cost per unit |
|---|---|---|
| Newsroom run | About 60K in / 8K out | About $0.01–0.02 (cheap tier) · about $0.20 (mid tier) |
| Chief-of-staff run | About 70K in / 8K out (snippets + digest + memory → scores; passage extraction for 6 stories; brief writing) | About $0.01–0.03 |
| Voice session (live or simulated) | About 4 turns over a cached 15K context | About $0.003–0.01 |
| Simulated user, per session | About 3K context per turn | About $0.003–0.01 (Tinker) |

| Item | OpenRouter | Tinker |
|---|---|---|
| Development, prompt iteration, bake-off | $25–35 | $5 |
| Newsroom: 40 replay days + about 35 live days | $2–15 | |
| Chief of staff: evaluation (about 2,700 runs, after reuse) + live (about 250 user-days) | $30–45 | |
| Voice sessions: evaluation (about 5,400) + live | $15–30 | |
| Simulated users | | $20–35 |
| Judges (sampled) | | $10–20 |
| **Total** | **about $70–125** | **about $35–60** |

**Guardrails:**
- **Cost probe in M2:** one replayed day through every role. Re-estimate from real numbers before sizing the evaluation.
- **Stop-loss:** pause evaluation runs if spend passes 60% of either credit before M5. Then cut seeds or the panel.
- **A dashboard** of spend by role, run, and config (PRD COST-1).

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

**Provider built-in web search vs. our own `search` tool**

| | Provider built-in search (model provider runs it server-side) | Our own tool over a search API |
|---|---|---|
| Per-search price | Roughly $10–35 per 1,000 searches depending on provider, **plus** the result text billed as input tokens (often large); OpenRouter's web plugin is roughly $4 per 1,000 results | $0 within free tiers (Tavily, Exa), otherwise about $5–8 per 1,000; we control how much result text reaches the model |
| Live volume (Kate + pilot, about 6,000 searches) | About $60–200 + tokens | About $0–50 |
| Evaluation (replay) | **Can't be used.** Results can't come from the frozen archive, so we'd still build our own replay search | Same tool, replay backend (free) |
| Code to write | Almost none for live, but **two different search paths** (live vs. replay) | About 150–250 lines: search wrapper, fetch + extraction, caching |
| Same behaviour live and in replay (RG-5) | ❌ The provider decides result format, count, page fetching, ranking | ✅ |
| Logging, caching, sharing results across users | Partial, inside the model response | Full |
| Model choice | Limited to providers that offer it | Any model |
| Course framing | Hides the retrieval step (a core course topic) | Retrieval is ours and evaluable |
| Result quality | Possibly better freshness and recall (big search indexes) | Depends on the API; swappable (Brave, Exa, Tavily, Google-results APIs) |

**Decision:** build our own `search` tool. The only real advantage of built-in search is result quality. If live results disappoint, swap the search API behind our tool rather than switching to built-in search.

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

**Window choice:** pick a window **after the training cutoffs of the agent models** (whichever models the bake-off picks; check their published cutoffs 🔬) and with good Wayback coverage. If August–September 2026 is before a model's cutoff, move the window later (for example October 2026), or note the leakage risk.

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
| **Turn model** | The fastest bake-off model that stays faithful, with a cached brief and source packs | Measure time to first token through OpenRouter (it adds a small hop) 🔬 |

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
| B. **"Optimization" stretch:** distil a small scorer or judge | LoRA fine-tune Inkling-Small on relevance scores labelled by a stronger model (or on Kate's ratings), then compare cost and quality with the prompted scorer | Hits the course's *optimization* topic; a nice extra results table | Medium. Optional, M5◇. Would revise the PRD non-goal on fine-tuning to "optional". |
| C. RL on the chief of staff's query generation with a simulator reward | Tinker RL | Research-y, interesting | High. Not for this quarter. |

---

## 10. Week-1 spikes (to settle the 🔬 items)

Each is about 1–3 hours:

1. **OpenRouter models:** for each bake-off candidate, check prompt-caching support (look at the cache fields in usage), tool-calling and structured-output support, and time to first token.
2. **Tinker:** check that serverless sampling of Inkling-Small works (API shape, rate limits, real cost per 1M tokens).
3. **iPhone:** a minimal HTTPS PWA with push-to-talk. Test Web Speech recognition and speechSynthesis in Safari and in home-screen mode.
4. **Archive feasibility:** pull one day of Techmeme, the HN front page from Wayback, arXiv, and 3 trade-press front pages for a candidate window. Confirm the granularity and the HN-points workaround.
5. **Model cutoffs:** confirm the training cutoffs of the chosen models and pick the replay window.
6. **Cost probe:** one replayed day through every role. Measure real tokens and cost, then re-run §1.4.

---

## 11. Decisions needed from Kate

1. **Model strategy:** option D: a per-role bake-off across OpenRouter models (no default provider), with simulated users and judges from a different family?
2. **Bake-off budget:** about $5–10 on about 5 dev days, before any evaluation runs?
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

---

## 12. Decision log

### 12.1 Decided

| # | Decision | Choice |
|---|---|---|
| D1 | Build principle | We build the agent loop, tools, memory, selection, and evaluation (PRD §6.0) |
| D2 | Search | **Our own `search` tool** over a search API (Tavily or Exa free tier to start), with live and replay backends |
| D3 | Models | No default provider; a per-role bake-off on OpenRouter; simulated users and judges from a different family (Tinker) |
| D4 | Client | **Web app (PWA)**, mobile-first, push-to-talk |
| D5 | Voice pipeline | §5.2 recommendations: browser speech-to-text and text-to-speech first, server fallback if flaky on iOS, sentence-streamed text-to-speech |
| D6 | Hosting and backend | Goal: **easy and cheap, good enough for daily use.** **Supabase free tier** (Postgres + Auth + row-level security) + **one Railway service** (about $5 a month on the Hobby plan; FastAPI runs the API, the worker, the scheduler, and serves the web app). One `git push` deploys. Cheaper but more hands-on: Fly.io (about $2–5 a month) or a small VM (Hetzner about €4 a month, with Docker + Caddy for HTTPS). |

### 12.2 Why not Vercel

Vercel is excellent for hosting a frontend, but our backend needs things serverless functions handle poorly:
- **agent runs that take minutes** (newsroom, chief of staff, follow-up research);
- **per-user scheduled jobs** through the day;
- an **always-on worker** and queue;
- a **Python** evaluation and agent codebase.

Vercel's function time limits and limited cron jobs on the free plan would force either a second backend host anyway, or a rewrite in TypeScript plus a third-party job service.

| Option | Services to run | Verdict |
|---|---|---|
| **Supabase + one Railway service** (FastAPI serves the API and the built web app) | 2 | **Chosen:** fewest moving parts, one deploy, one backend language |
| Vercel (frontend) + Railway (API and worker) + Supabase | 3 | Fine if we later want Vercel's preview deploys; the web app can move to Vercel with no backend changes |
| All on Vercel (TypeScript) + a job service (for example Inngest or Trigger.dev) | 2–3 | Loses the Python ecosystem; long agent runs are awkward |

Railway gives HTTPS (required for microphone access), environment secrets, and separate processes for web and worker in one project. Cost is about $5–10 a month. Fly.io is an equivalent alternative if Railway's pricing changes.

### 12.3 Defaults adopted (change any time)

| # | Question | Default |
|---|---|---|
| 1 | Replay window | Chosen in the week-1 spike: the latest window that's after the chosen models' training cutoffs and has good archive coverage (August–September 2026 if cutoffs allow, otherwise October 2026) |
| 2 | Evaluation size | 12 simulated users × 30 test days + 6 × 10 dev; 3 seeds for Full / B2 / B5, 2 for the rest |
| 3 | Bake-off budget | About $5–10 on about 5 dev days, before any evaluation run |
| 4 | Brief default | 6 stories, about 3 minutes, at least 1 exploration slot |
| 5 | Self-study AI share | At least 4 of 6 stories from the AI beat during the study |
| 6 | Auth | Supabase magic link + Google login; invite allowlist |
| 7 | Headline sources | The draft lists in PRD §6.4 and v0.5 §7.1; Kate adds favourites at any time (config) |
| 8 | Memory transparency | The agent explains a "for you" pick briefly when asked, and through UX-5 tags; it doesn't volunteer memory details unprompted |
| 9 | Tinker fine-tuning stretch | Included as optional (M5◇); the PRD fine-tuning non-goal becomes "optional stretch" |
| 10 | Pilot | 5–8 users; pilot data stays out of the report unless the human-subjects question is cleared |
| 11 | iPhone fallback | If the PWA microphone or audio is painful in the self-study, add an Expo app (plan B) |

### 12.4 Only Kate can answer

1. **Team of one:** confirmed with course staff?
2. **Course deadlines:** milestone, final report, poster.
3. **Human-subjects question** for the pilot: ask course staff whether a review is needed if pilot data appears in the report.
4. **Labelling time:** about 4–6 hours for judge validation and label checks. OK?
5. **AWS credits:** worth asking staff whether the $200 mentioned in lecture is available.
