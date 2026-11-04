# PRD: Conversational Briefing Agent (MVP)

**Status:** Draft v0.2, ready for review
**Owner:** Kate Wang
**Source:** `proposal.pdf` ("Expanding the Aperture", CS329Z Fall 2026)
**Next docs:** `02-technical-design.md` (after this PRD is approved), then the build

**Changelog**
- **v0.2:** The product is now **multi-user from day one**. Added accounts, onboarding, per-user data isolation, per-user scheduled processes, shared versus per-user pipeline stages, admin tools, per-user cost limits, and a small real-user pilot. Simulated users now run as accounts on the same infrastructure.
- **v0.1:** Initial draft from the proposal.

---

## 1. Summary

A chief-of-staff style agent that gives each user a short spoken morning brief on AI news, asks what they want to dig into, holds a back-and-forth on those items with a cited source for every claim, and then updates **that user's** memory of what they care about and how they like information packaged.

Each brief balances three things:

1. **Importance:** stories that matter in the world.
2. **Relevance:** stories that matter to this user.
3. **Exploration:** stories that widen the user's view rather than reinforce a filter bubble.

To keep voice turns fast, the agent pre-fetches source material for each user's likely deep dives before their brief starts, and searches live only when the cache can't answer.

The MVP is built as a **multi-user service**. Each user has an account, isolated stored data (profile, memory, sessions, ratings), and their own scheduled daily processes. The expensive, user-independent work (ingesting, clustering, and scoring news) runs **once** and is shared. Everything personal runs **per user**.

The MVP has to do three jobs:

- **A usable daily product** for a small invite-only group of real users: Kate first, then a pilot of friends and classmates.
- **A reproducible research system** that can be replayed over a frozen news corpus, driven by simulated users, and compared against baselines.
- **A foundation that scales to more users** without re-architecting. Adding a user should be a matter of configuration, not new code.

Every product decision below is checked against all three jobs.

---

## 2. Problem

- AI news moves faster than one person can follow. The useful signal is spread across papers, lab blogs, forums, and business and policy press.
- Access to information isn't the bottleneck. **The time and attention to filter it** is.
- Typing and scrolling don't fit the moments people actually have, such as a commute. Voice does.
- Existing options fall short:
  - **Static daily summaries and ranked feeds** don't learn from conversation and don't let you ask follow-ups.
  - **Engagement-optimised recommenders** narrow what you see over time.
  - **Commercial briefings** (ChatGPT Pulse, Google Daily Listen, Huxe) publish no evaluation of importance coverage, filter-bubble effects, or faithfulness.

## 3. Target users

The problem applies to anyone who has to stay current in a fast-moving field with little time, so the product is designed for many users. The MVP stays invite-only.

| Persona | Need | MVP role |
|---|---|---|
| **Kate**: a Stanford student with limited time who wants to stay current on AI | A 3–5 minute brief on the commute, deep dives on demand, and an agent that remembers what she cares about without over-indexing on it | **First real user.** Primary self-study user; also the admin. |
| **Researcher** | Paper-heavy, technical, wants methods and numbers | Pilot users + simulated users |
| **Operator or PM** | Products, launches, pricing, competitors | Pilot users + simulated users |
| **Investor** | Funding, business and policy, market moves | Pilot users + simulated users |
| **Policy follower** | Regulation and safety | Pilot users + simulated users |
| **Admin** (Kate in the MVP) | Invite users, watch that daily jobs run, control cost, disable accounts | Internal |

**Pilot scope:** about 5–15 real users (friends and classmates) who opt in, plus Kate. Pilot users are recruited after Kate's own use is stable (see §10).

**Simulated users** are created as accounts of type `synthetic` on the same infrastructure. They go through the same pipeline as real users and only differ in who answers the brief.

---

## 4. Goals and non-goals

### 4.1 Product goals (MVP)

- **G1. Daily brief.** Deliver to each user a spoken or text brief of about 5–7 stories in roughly 2–4 minutes, each with a one- or two-sentence summary and a source.
- **G2. Conversational deep dive.** After the brief, the user can say "tell me more about #3", "what did people on HN think?", or "skip, next". The agent answers from cited sources.
- **G3. Memory from conversation.** The agent infers each user's interests and delivery preferences from what they say and do, lets stale interests fade, and keeps room for new ones.
- **G4. Exploration.** Every brief reserves explicit slots for topics outside the user's current profile, and labels them as such ("Something outside your usual: …").
- **G5. Low latency.** Follow-ups feel conversational: most are answered from a pre-fetched cache, and live search is a fallback.
- **G6. Groundedness.** Every factual claim in a brief or deep dive is tied to a cited source item.

### 4.2 Platform goals (MVP)

- **P1. Accounts.** Invite-based sign-up, login, and a short onboarding that seeds interests and delivery preferences and sets timezone and brief time.
- **P2. Per-user data isolation.** Every piece of personal data (profile, memory, sessions, transcripts, ratings, experiment assignments) is scoped to one user and can't be read across users.
- **P3. Per-user processes.** Each user's daily pre-compute (selection, brief script, pre-fetch) runs as its own scheduled job, timed to that user's brief time and timezone. One user's job failing or slowing down doesn't affect anyone else.
- **P4. Shared ingestion.** Ingestion, clustering, importance scoring, and topic labelling run once per cycle and feed every user.
- **P5. Per-user cost limits.** Every LLM and search call is attributed to a user (or to the shared pipeline), with daily budgets per user.
- **P6. Data rights.** Users can view, export, and delete their memory and history, and consent to research use separately.
- **P7. Admin tools.** Invite users, see each user's job status and cost, retry failed jobs, and disable accounts.

### 4.3 Research goals (MVP)

- **R1. Replay mode.** The whole loop runs deterministically over a frozen corpus (for example August–September 2026) on a simulated clock, with no leakage from future items.
- **R2. Ablations.** The full agent and the four baselines run from per-user config: importance-only, static profile, no exploration, and no pre-fetch.
- **R3. Simulated users.** 10–20 LLM-driven users with hidden, drifting interest and delivery profiles, one of them calibrated to Kate, provisioned as synthetic accounts.
- **R4. Evaluation harness.** Computes every metric in §9 from text transcripts and logs, per user and aggregated.
- **R5. Real-user instrumentation.** Daily ratings, plus blinded baseline days assigned per user. Kate's self-study is the core; pilot users add a small, noisier sample.

### 4.4 Non-goals (MVP)

- Domains other than AI news, though the source list and topic taxonomy should be swappable.
- **Public sign-up, billing, or payments.** The MVP is invite-only.
- **Teams, organisations, or sharing** briefs or memory between users.
- **Large scale.** The MVP targets up to about 50 accounts (real + synthetic). The architecture shouldn't block growing beyond that, but the MVP isn't load-tested for it.
- Custom speech models. Speech-to-text and text-to-speech use off-the-shelf components, and **all quantitative evaluation runs on text**.
- Native mobile apps or push notifications. Email or a link reminder is enough.
- Full-duplex voice with barge-in. Push-to-talk is enough.
- Training or fine-tuning models.
- Paywalled sources and full-text redistribution. The agent stores and quotes snippets and links out.
- Per-user custom source lists. All users share one source list in the MVP; per-user source *preferences* still affect ranking.

---

## 5. User experience

### 5.1 Account lifecycle

```
 invite (admin) ──► sign up / log in ──► onboarding (≈2 min) ──► daily loop ──► manage memory / data
                                            │                                    │
                                            ├─ pick seed topics                  ├─ view / edit memory
                                            ├─ delivery prefs (length, depth)    ├─ export data
                                            ├─ timezone + brief time             ├─ change schedule
                                            └─ research consent (opt-in)         └─ delete account
```

- **Onboarding** gives a cold-start profile so the first brief isn't purely importance-based. Seed interests are marked as low-confidence, so conversation overrides them quickly.
- A user who skips onboarding gets an importance-heavy brief, and the agent learns from conversation alone.

### 5.2 The daily loop (per user)

```
 SHARED (once per cycle)              PER USER (scheduled before each user's brief time)
 ┌──────────────────────┐             ┌──────────────────────────────────────────────┐
 │ ingest → cluster →    │  stories   │ 1. PRE-COMPUTE                               │
 │ importance + topics   │──────────► │    relevance + novelty vs. this user's memory │
 └──────────────────────┘             │    → select brief (incl. exploration slots)  │
                                      │    → write brief script → pre-fetch dives    │
                                      └──────────────┬───────────────────────────────┘
                                                     ▼  user opens a session
                                      ┌──────────────────────────────────────────────┐
                                      │ 2. BRIEF  → 3. DEEP DIVE (cache first, live   │
                                      │    search on miss)                            │
                                      └──────────────┬───────────────────────────────┘
                                                     ▼  session ends
                                      ┌──────────────────────────────────────────────┐
                                      │ 4. UPDATE MEMORY (this user only)             │
                                      └──────────────────────────────────────────────┘
```

- If a user opens a session **before** their scheduled pre-compute has run, or after it failed, the agent computes the brief on demand. This is slower, but it always works.
- Each user's brief is valid for their local day. Briefs a user didn't open are kept, so they can be used as negative or implicit signals.

### 5.3 Example session (illustrative)

> **Agent:** Morning. Six stories today, about three minutes.
> 1) A frontier lab released a new open-weights reasoning model, with benchmarks close to the top closed models. *[lab blog, HN]*
> 2) The EU published draft guidance on general-purpose AI model obligations. *[Reuters]*
> 3) A paper shows long-context retrieval degrades sharply past a certain length. You've been following agent memory, so this one's for you. *[arXiv]*
> …
> 6) **Outside your usual:** robotics foundation models raised two large rounds this week. *[TechCrunch]*
> What do you want to dig into?
>
> **User:** Three. And give me the actual numbers.
>
> **Agent:** *(answers from the cache with figures and citations)* …
>
> **User:** Skip the EU stuff in future unless it's big.
>
> **Agent:** Got it. I'll only bring up EU regulation when it's major news.

Memory written to **this user's** store:
- interest: `agent memory / long context` ↑ (explicit dive)
- delivery: `prefers quantitative detail` ↑
- interest: `EU regulation` → threshold raised to "major only"

### 5.4 Interaction requirements

| ID | Requirement |
|---|---|
| UX-1 | The brief opens with the story count and expected length, and stories are numbered so the user can refer to them by number. |
| UX-2 | Each story gets a one-line *why you're hearing this* tag where useful: "big news", "for you", or "outside your usual". |
| UX-3 | Supported intents: dive into an item (by number or name), "more like this", "skip" or "less of this", "go deeper" or "simpler", "who's reacting?", "what's the source?", "repeat", "end". |
| UX-4 | The user can ask "what do you know about me?" and edit or delete memory entries in conversation or in a settings page. |
| UX-5 | Citations are spoken lightly ("per the Anthropic blog") and shown in full in the text transcript. |
| UX-6 | If the agent can't support an answer from a source, it says so rather than guessing. |
| UX-7 | The session works in **text mode** (web chat, plus a CLI for development) and in **voice mode** (push-to-talk in the web app), with the same agent logic underneath. |
| UX-8 | Logged-in users can see past briefs and transcripts (their own only). |
| UX-9 | Optional daily reminder (email) with a link to that day's brief, at the user's brief time. |

---

## 6. Accounts, data, and processes

This section covers the multi-user infrastructure. Exact technology choices belong in the technical design.

### 6.1 Accounts and authentication

| ID | Requirement |
|---|---|
| ACC-1 | Accounts have: id, email, display name, timezone, brief time, status (`invited`, `active`, `disabled`, `deleted`), type (`real`, `synthetic`), role (`user`, `admin`), research consent flag, and creation date. |
| ACC-2 | **Invite-only sign-up:** an admin creates an invite, and the invitee signs up from a single-use link. |
| ACC-3 | **Authentication** with a passwordless email link or a single OAuth provider (Google). The MVP stores no passwords. |
| ACC-4 | Every API request and agent session is authenticated and bound to exactly one user id. Synthetic accounts authenticate with service credentials and can't log in from the web app. |
| ACC-5 | **Roles:** admins can see account metadata, job status, and cost for all users. Admins **can't** read another real user's transcripts or memory unless that user has consented to research use, and every such access is logged. |

### 6.2 Data model and isolation

Data falls into three tiers:

| Tier | Contents | Scope |
|---|---|---|
| **Shared corpus** | Items, stories, clusters, importance scores, topic labels, hindsight labels, the shared pre-fetch cache of source text | Global, read-only for user processes |
| **Per-user state** | Profile, onboarding answers, interest model, delivery prefs, memory history, experiment assignments, budgets | One user |
| **Per-user activity** | Briefs, selections with scores, transcripts, per-user pre-fetch manifests, ratings, timings, cost records | One user |

| ID | Requirement |
|---|---|
| DATA-1 | Every per-user record carries a `user_id`. Every read or write of per-user data goes through a data-access layer that requires a user context. There are no ad hoc unscoped queries in agent code. |
| DATA-2 | The agent's memory and retrieval tools are **constructed per session with a fixed user id**. The LLM can't choose or change whose memory it reads. |
| DATA-3 | Shared caches (source text, embeddings, search results for corpus items) are keyed by content, not by user, and contain no user data. This lets pre-fetch work be shared across users who get the same story. |
| DATA-4 | **Replay and simulation runs** live in a separate namespace (a `run_id`) from live production data, so experiments can't contaminate real users' memory, and runs can be wiped as a whole. |
| DATA-5 | **Retention:** raw voice audio isn't stored by default; transcripts are. Users can delete individual sessions or their whole account. Deleting an account removes all per-user records within 7 days. |
| DATA-6 | **Export:** a user can download their profile, memory, and transcripts as JSON. |
| DATA-7 | Data is encrypted in transit and at rest, using the hosting platform's defaults. Secrets (API keys) are never stored in the database or repo. |
| DATA-8 | Research and evaluation queries over real users include only users who have consented, and use pseudonymous ids. |

### 6.3 Processes and scheduling

| ID | Requirement |
|---|---|
| PROC-1 | **Shared ingestion job:** runs on a fixed schedule (for example every few hours in live mode), is idempotent, and records each run's status and coverage. |
| PROC-2 | **Per-user pre-compute job:** for each active user, runs once per local day, scheduled about 60 minutes before that user's brief time, after the latest ingestion has finished. It produces that day's selection, brief script, and pre-fetch manifest. |
| PROC-3 | **Job isolation:** per-user jobs are separate units of work on a queue with workers. One failure is retried (with backoff) and alerted without blocking other users. Each job is tagged with `user_id`, date, agent config version, and `run_id`. |
| PROC-4 | **On-demand fallback:** if a session starts without a ready brief, the same job runs synchronously for that user (§5.2). |
| PROC-5 | **Session process:** each live conversation is an isolated session holding only that user's context. Sessions for different users run concurrently. |
| PROC-6 | **Memory update job:** runs at session end (or after an idle timeout) for that user only. Updates are serialised per user, so two sessions can't corrupt one user's memory. |
| PROC-7 | **Concurrency:** the system supports at least 10 concurrent live sessions and pre-compute for 50 accounts within a 1-hour window. |
| PROC-8 | **Simulation runner:** in replay mode, the same per-user jobs are driven by a simulated clock across many synthetic accounts in parallel, under a `run_id`. |
| PROC-9 | **Observability:** every job and session logs structured events (user id, job, duration, tokens, cost, cache hits and misses, errors) that can be filtered by user and by run. |

### 6.4 Cost control

| ID | Requirement |
|---|---|
| COST-1 | Every LLM, search, and speech call records its cost against a `user_id` or the shared pipeline. |
| COST-2 | Per-user daily budget (configurable). When a user is close to it, the agent moves to cheaper models or degrades gracefully (shorter deep dives, no live search) rather than failing. |
| COST-3 | Global daily budget cap for the whole deployment and for each simulation run. |
| COST-4 | Shared work (ingestion, clustering, importance, source-text pre-fetch) is computed once and reused by every user. Only personalised work is per user. |

### 6.5 Admin

| ID | Requirement |
|---|---|
| ADM-1 | Admin page or CLI: create and revoke invites, list accounts with status, see last brief time, last job status, and cost to date. |
| ADM-2 | Retry a user's failed job, disable or re-enable an account, and change a user's experiment assignment. |
| ADM-3 | Create synthetic accounts in bulk from simulated-user profile files, and delete them by `run_id`. |

---

## 7. Agent functional requirements

All of these run **per user**, using that user's memory and config, except where marked *shared*.

### 7.1 Sources and ingestion (*shared*)

| ID | Requirement |
|---|---|
| ING-1 | A fixed, configurable source list covering four categories: **research** (arXiv cs.AI, cs.CL, cs.LG, cs.CV, plus paper aggregators), **products and labs** (official blogs), **community** (Hacker News, selected subreddits), and **business and policy** (tech and business press, government and regulator releases). |
| ING-2 | Each item stores: id, source, category, URL, title, published timestamp, author, text or snippet, and engagement signals where available. |
| ING-3 | Two modes: **live** (scheduled pulls feeding every user) and **replay** (a frozen snapshot loaded from disk). |
| ING-4 | **Story clustering:** items about the same event are grouped into one *story*, the unit that gets ranked. |
| ING-5 | **No time leakage:** in replay mode, every component sees only items published at or before the simulated "now". |

### 7.2 Ranking and selection

| ID | Requirement |
|---|---|
| RANK-1 | *Shared:* an **importance score** per story from signals available at brief time. It must *not* use hindsight labels. |
| RANK-2 | *Per user:* a **relevance score** per story against the user's interest model. |
| RANK-3 | *Per user:* a **novelty and exploration score**: distance from the user's profile and from stories recently shown to that user. |
| RANK-4 | *Per user:* **slot-based selection** of K stories (default 6): importance slots + relevance slots + at least one reserved exploration slot, with an MMR-style redundancy penalty. Slot counts come from the user's config. |
| RANK-5 | Exploration candidates must still clear a minimum importance or quality floor. |
| RANK-6 | Each selection decision is logged per user with its component scores. |

### 7.3 Brief generation (per user)

| ID | Requirement |
|---|---|
| BRF-1 | Generates a script using only text from the selected stories' source items. Every sentence maps to at least one citation. |
| BRF-2 | Length, depth, and technicality follow the user's delivery preferences, within limits (for example 3–8 stories, 1–4 minutes). |
| BRF-3 | Ends with an open prompt to dig in. |

### 7.4 Pre-fetch and deep dive

| ID | Requirement |
|---|---|
| PF-1 | *Per user:* predict which stories this user is likely to dive into, and build a pre-fetch manifest for them. |
| PF-2 | *Shared:* fetched source material (full text, related stories, community reaction) goes into the content-keyed shared cache, so it's reused across users (DATA-3). |
| PF-3 | Per-user pre-fetch budget (number of stories × depth). |
| PF-4 | Deep-dive answers come from the cache first. A **miss** triggers live search: corpus search in replay mode, and web or source search in live mode. Each hit and miss is logged per user. |
| PF-5 | Deep-dive answers follow the grounding rules of BRF-1, and hedge when support is missing (UX-6). |

### 7.5 Memory (per user)

| ID | Requirement |
|---|---|
| MEM-1 | **Interest model:** a set of topics, each with a weight, evidence (session and turn references), a last-reinforced time, and a source (onboarding, explicit, implicit). |
| MEM-2 | **Signals.** Explicit: "more of X", "less of Y". Implicit: dove in, number of follow-ups, skipped, cut short, brief not opened. Explicit signals weigh more. |
| MEM-3 | **Decay** with a configurable half-life. |
| MEM-4 | **Delivery preferences:** length, depth, technicality, quantitative versus narrative, preferred sources, each with a value and confidence. |
| MEM-5 | **Exploration feedback:** an exploration story that gets engagement can be promoted into the interest model. |
| MEM-6 | Memory is readable and editable by its owner (UX-4), persisted in the per-user store, and versioned, so each day's memory snapshot can be recovered for evaluation. |
| MEM-7 | A **static-profile** mode freezes a user's memory after initialisation, for the baseline. |

### 7.6 Voice

| ID | Requirement |
|---|---|
| VOX-1 | Off-the-shelf streaming speech-to-text and text-to-speech. |
| VOX-2 | Push-to-talk in the web app, working in a phone browser. |
| VOX-3 | Latency target: first audio within about 1 second of end of user speech on cache hits. |
| VOX-4 | The voice layer is a thin adapter over the text agent. |

### 7.7 Simulation and evaluation

| ID | Requirement |
|---|---|
| SIM-1 | **Simulated user:** an LLM agent with a hidden interest profile, hidden delivery preferences, and a drift schedule. It reacts to briefs by picking dives, asking follow-ups, skipping, and stating preferences, sometimes noisily or implicitly. |
| SIM-2 | 10–20 simulated users across the personas, including one calibrated to Kate, each provisioned as a **synthetic account** under a `run_id` (ADM-3). |
| SIM-3 | Simulated users also give per-story relevance ratings as a judge, kept separate from their in-conversation behaviour. |
| SIM-4 | **Runner:** for each (synthetic account, agent config), replays every day of the window through the normal per-user jobs (PROC-8). |
| SIM-5 | Runs are reproducible: fixed seeds, cached LLM calls, versioned configs. |
| SIM-6 | Simulated results are cross-checked against real-user results. |

### 7.8 Real-user instrumentation

| ID | Requirement |
|---|---|
| SS-1 | After each session, a 30-second rating: overall usefulness (1–5), per-story relevance, "learned something new" (yes/no), and length about right / too long / too short. |
| SS-2 | **Blinded baseline days, assigned per user:** on randomly scheduled days, a baseline config is served in place of the full agent. The schedule is drawn per user so baseline days differ across users. |
| SS-3 | **Experiment assignment** is stored per user and per day (config version + arm), so every brief can be traced to the config that produced it. |
| SS-4 | Real-user sessions are included in analysis only if the user has given research consent (DATA-8). |

---

## 8. MVP scope cut

| Area | In the MVP | Later or stretch |
|---|---|---|
| Accounts | Invite-only, passwordless or Google login, up to about 50 accounts | Public sign-up, billing, organisations |
| Processes | Shared ingestion + per-user scheduled jobs on a simple queue with workers | Autoscaling, multi-region |
| Sources | about 10–20 sources across 4 categories, chosen for historical availability and RSS or API access | Per-user custom sources, paywalled sources |
| Interface | Web app (text chat + push-to-talk voice, settings and memory page, history) usable in a phone browser; CLI for development and simulation | Native app, wake word, barge-in, podcast-style feed |
| Ranking | Weighted scoring + slots + MMR | Contextual bandit, learned weights, cross-user signals ("people like you") |
| Memory | Structured topic profile + delivery prefs + decay, per user | Embedding-based open-vocabulary interests, episodic memory |
| Pre-fetch | Top-N predicted stories per user, shared content cache | Learned dive predictor, adaptive budget |
| Evaluation | All metrics in §9, LLM judge + human spot-checks | Larger user study |

**Cross-user learning** (using other users' behaviour to rank stories for you) is deliberately left out of the MVP. It would muddy the per-user memory evaluation and raises privacy questions. The data model shouldn't rule it out later.

---

## 9. Evaluation and success metrics

Evaluation covers **selection** (did the agent pick the right stories, given what's important and what it knows about the user?) and **packaging** (did it deliver them the way this user wants?). All per-user metrics are reported per user and averaged across users.

### 9.1 Hindsight labels (replay corpus only)

- **Primary importance label:** the story has a Hacker News score above a calibrated cutoff, chosen so that about *N* stories per day are important.
- **Secondary importance label:** the story is covered by at least *k* distinct sources on our list within 14 days of first appearance.
- **Topic labels:** each story gets one or more topics from a fixed taxonomy of about 20–40 topics, assigned by an LLM and spot-checked.

### 9.2 Metrics

| Layer | Metric | Definition (precise version in the technical design) |
|---|---|---|
| Corpus, user-independent | **Importance recall@K** | Share of hindsight-important stories surfaced in a user's brief within *d* days of appearing |
| | **Citation faithfulness** | Share of atomic claims that the cited source supports |
| | **Latency** | p50 and p95 time to first token or audio for a follow-up turn, split by cache hit and miss, and under concurrent load |
| | **Pre-fetch hit rate** | Share of deep-dive turns answered entirely from the cache |
| | **Pre-fetch efficiency** | Share of pre-fetched stories used, cost per brief per user, and reuse of the shared cache across users |
| Simulated users | **Relevance** | Mean hidden-profile relevance of the stories shown |
| | **Interest recovery** | Similarity between the learned and hidden profiles over time |
| | **Topic coverage** | Number of distinct topics (or topic entropy) shown over a window |
| | **Packaging adherence** | Match between delivered length, depth, and technicality and the hidden prefs |
| Real users | Daily usefulness, per-story relevance, "learned something new", and length fit | Full agent vs. blinded baseline days, within each user |
| Platform | **Job reliability, cost per user** | Share of users whose brief was ready on time; mean and max daily cost per user |

### 9.3 Baselines

1. **Importance-only:** no personalisation.
2. **Static profile:** profile set on day 1 and never updated.
3. **No exploration:** exploration slots removed.
4. **No pre-fetch:** every follow-up uses live search.

### 9.4 MVP success criteria

**Agent.** The MVP works if the full agent:
- ✅ **beats the static profile on relevance** for simulated users and on real-user ratings;
- ✅ **stays close to importance-only on importance recall** (provisional target: at least 90% of the baseline);
- ✅ **keeps topic coverage above the no-exploration baseline**;
- ✅ **reaches at least 95% supported claims**;
- ✅ **answers most follow-ups from the pre-fetch cache** (provisional target: at least 70% hit rate) with p95 time to first audio under about 1.5 s on hits.

**Platform.**
- ✅ At least 95% of active users' briefs are ready before their brief time.
- ✅ No cross-user data access in an isolation test suite. Every per-user endpoint and tool is tested against another user's ids.
- ✅ Adding a user takes an invite plus onboarding, with no code or manual config changes.
- ✅ Average daily cost per user stays under budget (to be set in the technical design).

Targets are provisional and get refined once the baselines run.

---

## 10. Milestones (proposed)

Dates are placeholders until they're lined up with the CS329Z deliverable dates. The **self-study needs 4–5 weeks**, so a usable live agent is the critical path. Multi-user support is built into the foundations at M1–M2 rather than added later, because changing the data model afterwards is expensive.

| Milestone | Contents | Exit criterion |
|---|---|---|
| **M0: Docs** | This PRD, then the technical design | Both approved |
| **M1: Foundations + corpus** | Database with users and per-user tables, data-access layer, job queue and workers; source list; replay corpus; clustering; hindsight labels; topic taxonomy | Two test accounts with isolated stores; corpus loaded; label cutoff calibrated |
| **M2: Text agent v0** | Per-user ranking + brief + deep dive (live search, no pre-fetch) + basic memory, in the CLI; shared ingestion job; per-user pre-compute job; replay and live modes | One end-to-end replayed day for two synthetic accounts; one live day for Kate |
| **M3: Web app + Kate's self-study** | Login (invite-only), onboarding, web chat + push-to-talk voice, memory and settings page, ratings, per-user blinded baseline scheduler, admin CLI | Kate uses it daily with logging; isolation tests pass |
| **M4: Full agent + pilot** | Exploration slots, decay, delivery prefs, pre-fetch with shared cache, ablation flags, cost budgets; invite about 5–15 pilot users | All 5 configs run on replay; pilot users onboarded and receiving daily briefs |
| **M5: Simulation and evaluation** | Simulated users as synthetic accounts, runner, metrics, LLM judges, human audit | Full results table vs. baselines; pilot data summarised |
| **M6: Write-up** | Analysis, failure cases, report | Final report |

---

## 11. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Multi-user infrastructure adds scope** for a solo project | Slips the agent work and the self-study | Use managed services (hosted Postgres, a managed auth provider, a simple job queue); keep the admin side to a CLI; the pilot is optional if time runs short, but the infrastructure stays multi-user. |
| **Cross-user data leak** | Privacy breach, loss of trust | User-scoped data-access layer (DATA-1), memory tools bound to a user id (DATA-2), no user data in shared caches (DATA-3), isolation test suite (§9.4). |
| **Storing other people's personal data** (interests, transcripts) | Privacy and ethics obligations | Invite-only; clear consent screen; separate research consent; no raw audio stored; export and delete; pseudonymous analysis. Check whether the course or Stanford requires a review for studies with human participants before recruiting the pilot. |
| **Per-user LLM cost grows linearly** | Budget overrun | Shared corpus work and a shared content cache; per-user and global budgets; cheaper models for per-user steps; cap the pilot size. |
| **Scheduler or job failures** | Users miss briefs | Retries with backoff, an on-demand fallback (PROC-4), alerts to the admin. |
| **Historical data gaps** in the replay corpus | Weak corpus | Pick sources by historical availability (arXiv API, HN Algolia API, lab blog archives, GDELT); snapshot live data daily from day one. |
| **LLM knowledge leakage** about events in the window | Inflated quality or importance judgements | Prefer a window after the model's cutoff; ground ranking signals in corpus data; never use the LLM as the importance oracle in evaluation. |
| **Hindsight label noise** (HN skews toward developers) | Biased importance metric | Also use the multi-source coverage label; report both. |
| **Unreliable simulated users** | Misleading evaluation | Calibrate one simulated user to Kate; cross-check against real users, which is now more than one person; report limitations. |
| **Hallucinated or unsupported claims** | Safety and trust, now for users other than the builder | Retrieval-only generation, claim checking at generation time, refusal over guessing, a required faithfulness metric. |
| **Voice latency** under concurrent sessions | Poor UX | Pre-fetch, streaming text-to-speech, a fast model for turn handling, a load test at the PROC-7 level. |

---

## 12. Open questions (decisions for Kate)

1. **Replay window:** August–September 2026 as in the proposal? It must sit after the chosen LLMs' training cutoff.
2. **LLM provider and models:** OpenRouter (course credits) as the default gateway? Course credits may not be meant for other users' usage, so do we need a separate budget for the pilot?
3. **Hosting:** where should the service run? Options include a single small cloud VM with Postgres, or a managed platform (for example Fly.io, Render, or Supabase for the database and auth). This matters now because there will be real users outside Kate's laptop.
4. **Auth:** passwordless email link, or Google login (simplest for Stanford and Gmail users)?
5. **Pilot:** how many users, and who? Should pilot data go into the course report? If it does, check whether a human-subjects review is required.
6. **Source list:** any must-have or must-exclude sources?
7. **Brief size:** default of 6 stories, about 3 minutes, with at least 1 exploration slot. Is that the right default for every user, or should onboarding ask?
8. **Course deadlines:** milestone, final report, and poster dates, to put real dates on §10.
9. **Memory transparency:** should the brief ever say *why* the agent thinks you care about something?

---

## Appendix A: Glossary

- **Account:** a user record, either `real` or `synthetic` (a simulated user).
- **Item:** a single source document (a paper, post, article, or HN thread).
- **Story:** a cluster of items about the same event; the unit of ranking. Shared across users.
- **Brief:** the ordered set of K stories delivered to one user at the start of a session.
- **Exploration slot:** a brief position reserved for a story outside the user's current profile.
- **Shared pipeline:** ingestion, clustering, importance, and topic labelling, computed once for everyone.
- **Per-user job:** a scheduled unit of work (pre-compute, memory update) that reads and writes one user's data only.
- **Run (`run_id`):** a namespace that isolates a replay or simulation experiment from live data.
- **Hindsight label:** an importance label computed with future information. Used only for evaluation.
- **Replay mode:** running the agent over a frozen corpus on a simulated clock.

## Appendix B: Related work, as it informs requirements

| Work | Informs |
|---|---|
| Li et al. 2010 (contextual bandit), MIND | Relevance and exploration trade-off; later bandit upgrade |
| Carbonell & Goldstein 1998 (MMR), Ziegler et al. 2005 | RANK-4 redundancy and diversity |
| Nguyen et al. 2014, Kaminskas & Bridge 2017 | Coverage, novelty, and serendipity metrics |
| MemGPT, Mem0, LaMP | Per-user memory design; we evaluate *selection*, not just recall of facts |
| Park et al. 2023, Zhang et al. 2024, Wang et al. 2023, Zhu et al. 2024 | Simulated users, and their limitations |
| Gao et al. 2023, Liu et al. 2023 | Citation faithfulness as a required metric |
