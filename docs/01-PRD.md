# PRD: Conversational Briefing Agent (MVP)

**Status:** Draft v0.7, ready for review
**Technical design:** `02-technical-design.md`
**Owner:** Kate Wang
**Source:** `proposal.pdf` ("Expanding the Aperture", CS329Z Fall 2026)
**Companion doc:** `01a-design-explorations.md` (alternatives considered: shared corpus, topic channels, news sources, voice, mobile)
**Next docs:** `02-technical-design.md` (after this PRD is approved), then the build

**Changelog**
- **v0.7:** Role split clarified: news gathering is a pipeline, and the chief-of-staff agent decides what fits the user, compiles the brief and the live Q&A corpus, and decides memory updates. Exploration means **adjacent discovery**, and simulated users get hidden **latent interests** to measure it. Interruption is two-stage: tap or AirPods-squeeze to interrupt with automatic end-of-speech detection, then voice barge-in with headphones. The evaluation is resized to fit the budget (12 users × 30 days). Schedule risk flagged.
- **v0.6.1:** Added the build principle (§6.0): we build the agent loop, tools, memory, selection, and evaluation ourselves; models are interchangeable API calls with no default provider; no hosted agent platforms or provider-side tools.
- **v0.6:** **Agent-native architecture.**
  - No news-ingestion pipeline and no user-facing channels.
  - Three agent roles, each run in its own context:
    - a shared **newsroom** agent that judges importance from headline signals (editorial front pages, community rankings, expert curation, coverage);
    - a per-user **chief-of-staff** agent that personalises the brief and pre-fetches material for deep dives;
    - a fast **voice** agent for the conversation.
  - Users state interests in their own words. The three topic areas (AI; Energy, Grid & Data Centers; Physical AI, Robotics & Simulation) become the newsroom's **beats**.
  - Agent tools have **live and replay backends**, so evaluation runs the same agent code over a frozen archive.
  - Rebuilt the evaluation plan to course standards: explicit task sets, data provenance, grader types, LLM-judge validation, statistics, failure analysis.
  - Added a naive-agent baseline, a separation ablation, and a prompt-injection safety evaluation.
  - Added a course-requirements map (§13).
- **v0.5:** Fixed catalog of three topic channels.
- **v0.4:** Topic channels, follow-up bookmarks, memory that doesn't over-curate, iPhone access, specific headlines, research guardrails, future vision.
- **v0.3:** Target users described broadly; persona variety moved to simulated users.
- **v0.2:** Multi-user from day one.
- **v0.1:** Initial draft from the proposal.

---

## 1. Summary

A chief-of-staff style agent that gives each user a short spoken morning brief, asks what they want to dig into, holds a back-and-forth with a cited source for every claim, and updates **that user's** memory of their interests and preferred format. When it can't answer something live, it **bookmarks the question, researches it in the background, and follows up**.

Each brief balances three things:

1. **Importance:** stories that matter in the world.
2. **Relevance:** stories that matter to this user.
3. **Exploration:** stories that widen the user's view rather than reinforce a filter bubble.

**How it works: three agent roles, one daily job**

| Role | Runs | Speed | Job |
|---|---|---|---|
| 📰 **Newsroom** (shared) | A few times a day | Minutes | Reads headline signals across three beats and writes a cached **headlines digest**: what's big today, with evidence. **Never sees any user data.** |
| 🗂️ **Chief of staff** (per user) | Daily, before the user's brief time | Minutes | Reads the digest and the user's memory, runs personal searches, scores candidates, writes the brief, and pre-fetches a **source pack** for each story |
| 🎙️ **Voice agent** (per session) | Live | About 1 s per turn | Delivers the brief and answers follow-ups from the source packs. Bookmarks what it can't answer. Records memory signals. |

**Beats** are the newsroom's coverage areas: 🧠 **AI**; ⚡ **Energy, Grid & Data Centers**; 🤖 **Physical AI, Robotics & Simulation**. Users don't pick from a menu. They describe their interests in their own words, and personal search covers interests outside the beats.

The MVP is a **multi-user service** with isolated per-user data and processes, used by Kate and a small invite-only pilot.

The MVP has to do three jobs:
- **A usable daily product** for anyone on the go who wants to stay current in a fast-moving field.
- **A rigorously evaluated research system.** The core evaluation replays the same agent code over a frozen archive of the **AI beat** for a past window, with simulated users, baselines, ablations, validated graders, and failure analysis (§10).
- **A foundation to grow** toward more beats, more users, and eventually a broader personal chief of staff.

**Rule for every extension:** product features may go beyond the research scope, but they must not weaken or silently change the research evaluation (§11).

---

## 2. Problem

- These fields move faster than one person can follow, and the useful signal is spread across papers, company blogs, trade press, forums, and policy releases.
- Access to information isn't the bottleneck. **The time and attention to filter it** is.
- Typing and scrolling don't fit the moments people actually have, such as a commute. Voice does.
- Existing options fall short:
  - **Static summaries and ranked feeds** don't learn from conversation and don't take follow-up questions.
  - **Engagement-optimised recommenders** narrow what you see over time.
  - **Commercial briefings** (ChatGPT Pulse, Google Daily Listen, Huxe) publish no evaluation of importance coverage, filter-bubble effects, or faithfulness.

## 3. Target users

**Anyone who wants to stay current in a fast-moving field and is on the go.** The MVP's beats share a thread: AI, the energy that runs it, and AI moving into the physical world. That covers researchers, engineers, founders, investors, operators, policy people, and students. The product doesn't assume fixed user types.

**Real users in the MVP:**
- **Kate:** first user, primary self-study participant, admin.
- **Pilot users:** about 5–15 people from a variety of roles, recruited after Kate's use is stable.

**Simulated users** are where variety is designed in on purpose (§9).

---

## 4. Goals and non-goals

### 4.1 Product goals (MVP)

- **G1. Daily brief.** About 5–7 **specific** stories (named companies, people, products, papers, bills, numbers) in roughly 2–4 minutes, each with a source.
- **G2. Conversational deep dive.** The user refers to stories in their own words and asks open questions.
- **G3. Memory from conversation.** The agent learns interests and delivery preferences without over-reading a short session. Stale interests fade, and there's room for new ones.
- **G4. Exploration as adjacent discovery.** Every brief has an explicit, labelled "outside your usual" slot. It holds stories **related to what the user thinks about that they wouldn't know to search for** (adjacent topics, companies, people, methods), not random news.
- **G5. Low latency.** Most follow-ups are answered from pre-fetched source packs.
- **G6. Groundedness.** Every factual claim cites a source. The agent says when it can't support something.
- **G7. Follow-up bookmarks.** Questions the agent can't answer live are researched in the background and answered next time.
- **G8. Open interests.** Users state and change interests in natural language, at sign-up or by voice.

### 4.2 Platform goals (MVP)

- **P1. Accounts:** invite-only sign-up, login, short onboarding.
- **P2. Per-user data isolation.**
- **P3. Per-user processes**, isolated from each other.
- **P4. Shared newsroom digest**, computed once and reused by every user.
- **P5. Cost limits** per user and overall.
- **P6. Data rights:** view, export, delete, separate research consent.
- **P7. Admin tools.**
- **P8. iPhone access**, including voice.
- **P9. Swappable tool backends:** every agent tool has a live backend and a replay backend (§6.4).

### 4.3 Research goals (MVP), all held

- **R1. Replay.** Deterministic-as-possible replay of the **same agent code** over a frozen **AI-beat archive** for a past window (for example August–September 2026), with no future leakage.
- **R2. Baselines and ablations** (§10.5).
- **R3. Simulated users:** 10–20 varied users with hidden, drifting profiles, one calibrated to Kate.
- **R4. Evaluation harness:** every task set and metric in §10, with validated graders.
- **R5. Real-user study:** Kate's 4–5 week self-study with blinded baseline days; pilot as a secondary sample.
- **R6 (optional). Second beat:** repeat importance and coverage metrics on the Physical AI beat.
- **R7. Failure analysis:** a categorised error analysis for every task set (§10.8).
- **R8. Safety:** faithfulness, correct abstention, prompt-injection robustness, and data isolation (§10.9).

### 4.4 Non-goals (MVP)

- A news-ingestion pipeline or a full news corpus for the **product**. A frozen archive exists **only for evaluation**.
- Public sign-up, billing, teams, or sharing between users.
- Large scale. The MVP targets up to about 50 accounts.
- Custom speech models, or an end-to-end speech-to-speech agent as the main path (§11, F3).
- A native Swift app; full-duplex voice with barge-in.
- Training or fine-tuning models.
- Paywalled content and full-text redistribution.
- Cross-user learning (§11, F7).
- Everything in §4.5.

### 4.5 Future vision (bookmarked, NOT in the MVP)

- **More beats** and better coverage of interests outside the beats.
- **A broader personal chief of staff** beyond news: email triage, tasks and calendar, meeting prep, in the same conversational, memory-driven style. The role-based agent design and the general user model keep this path open.

---

## 5. User experience

### 5.1 Account lifecycle

```
 invite ──► sign up / log in ──► onboarding (≈2 min) ──► daily loop ──► manage memory / data
                                    ├─ "What do you want to keep up with?" (free text or voice)
                                    ├─ delivery prefs (length, depth, technicality)
                                    ├─ timezone + brief time
                                    └─ research consent (opt-in)
```

Onboarding interests seed memory with low confidence. Conversation overrides them quickly.

### 5.2 The daily loop

```
 ┌──────────────────────── DAILY JOB (orchestrated by code) ────────────────────────┐
 │                                                                                    │
 │  📰 NEWSROOM RUN (once, shared)              🗂️ CHIEF-OF-STAFF RUN (per user)       │
 │  context: beat source lists only            context: digest + THIS user's memory   │
 │  tools: search, fetch                       tools: search, fetch, read_memory(user)│
 │  ─────────────────────────────►  digest  ─► personal searches → score candidates  │
 │  front pages, rankings, curation,  (cached,  → selection (slots + MMR, in code)    │
 │  coverage → "what's big today"      no user   → brief script → source packs        │
 │                                     data)     → attach answered follow-ups         │
 └────────────────────────────────────────────────────────────────────────────────────┘
                                                        │ user opens a session
                                                        ▼
                         🎙️ VOICE AGENT: brief → deep dive from source packs
                            → quick search (with "let me check") → bookmark if unresolved
                                                        │ session ends
                                                        ▼
                         memory update (this user) · background research on bookmarks
```

If the brief isn't ready when the user opens a session, the chief-of-staff run happens on demand.

### 5.3 Example session (illustrative; companies and products are fictional)

> **Agent:** Morning. Six stories, about three minutes. Also, you asked yesterday why Halden's chip launch slipped. I found the answer, and it's at the end.
> 1) **Northwind AI released Atlas-3**, an open-weights reasoning model. It scores within two points of the top closed models on the main math and coding benchmarks. *[Northwind blog; HN discussion]*
> 2) **A Stanford and CMU paper finds long-context retrieval accuracy falls by about a third past 200k tokens**, across five models. This one's because you've been following agent memory. *[arXiv]*
> 3) **The EU AI Office published draft guidance** on what general-purpose model providers must disclose about training data. *[Reuters]*
> 4) **Voltaic Storage signed a 2 GWh grid battery deal with Texas utility Lonestar Power**, the largest sodium-ion order so far. *[Utility Dive; Canary Media]*
> 5) **Cirrus Cloud is pairing a 1 GW Arizona data center with an on-site gas plant**, because the grid connection is five years out. *[Data Center Dynamics]*
> 6) **Outside your usual:** **Kinema released an open robot foundation model trained mostly in simulation**, and says it transfers to real warehouse arms with no fine-tuning. *[The Robot Report; arXiv]*
> What do you want to dig into?
>
> **User:** The long-context paper. Which models held up best?
>
> **Agent:** *(answers from the source pack with figures and a citation)* …
>
> **User:** Does that hold for Atlas-3 too?
>
> **Agent:** The paper didn't test Atlas-3, and I couldn't find anyone who has yet. I'll look into it and tell you tomorrow.
>
> **User:** Great. I've got to go, but the battery one sounded interesting too.
>
> **Agent:** I'll save the battery story for tomorrow. Here's the Halden answer quickly: …

Memory written to **this user's** store:
- `long-context / agent memory` ↑ (explicit dive + follow-up)
- `grid storage` ↑ (explicitly saved)
- the other stories they didn't dive into: **no negative signal**, because the session was cut short
- open question → background research

### 5.4 Interaction requirements

| ID | Requirement |
|---|---|
| UX-1 | The brief opens with the story count, expected length, and any answered follow-ups. |
| UX-2 | **Specific headlines:** concrete actors and objects named in the first sentence. No generic phrasing when the source names them. |
| UX-3 | **Natural references:** stories can be referred to by description, keyword, or position. The agent asks briefly if a reference is ambiguous. |
| UX-4 | Open questions not tied to a story are supported. |
| UX-5 | *Why you're hearing this* tags where useful: "big news", "for you", "outside your usual". |
| UX-6 | Supported intents include: dive, ask, "more like this", "less of this", "save for later", "keep me posted on X", "stop covering X", "go deeper" / "simpler", "who's reacting?", "source?", "what do you know about me?", "what questions do I have open?", "I only have N minutes", and "end". |
| UX-7 | Citations are spoken lightly and shown in full in the transcript. |
| UX-8 | Unsupported claims are never made. The agent offers to bookmark the question instead. |
| UX-9 | Text and voice share one agent stack. |
| UX-10 | Users can see and edit their past briefs, transcripts, open questions, and memory. |
| UX-11 | Optional email reminder at brief time. |

---

## 6. System design: agents, signals, and tools

### 6.0 Build principle: we build the agent

This is a course project in building agents, so **the agentic system is our own code**. LLMs are interchangeable components we call through plain APIs.

| We build | We use off the shelf |
|---|---|
| The agent loop and orchestration (role contexts, tool calling, retries, budgets) | LLM inference APIs (any provider via OpenRouter or Tinker), chosen per role by a bake-off |
| The tools (search, fetch, headline sources, memory, bookmarks) and their live and replay backends | Search APIs and web pages as *data sources* behind our tools |
| Newsroom importance judgement, candidate scoring, and selection (slots + MMR) | Speech-to-text and text-to-speech engines |
| Memory: schema, signal extraction, weighting, decay, guards | Standard infrastructure (database, auth, hosting) |
| Pre-fetch and source packs, follow-up research | |
| Simulated users, the replay archive and labels, graders, the evaluation harness | |

| ID | Requirement |
|---|---|
| BUILD-1 | **No hosted agent platforms or agent frameworks that own the loop** (for example vendor-managed agents, provider-side "server tools" like built-in web search). These would hide the parts we're meant to build and can't be replayed (RG-5). |
| BUILD-2 | **Provider-agnostic model layer:** every role calls models through one thin interface. Swapping a role's model is a config change. |
| BUILD-3 | Thin libraries are fine where they don't own the agent's logic (an HTTP client, a schema validator, an article-text extractor, a BM25 index). |

### 6.1 📰 Newsroom agent (shared)

| ID | Requirement |
|---|---|
| NEWS-1 | Runs on a schedule (a few times a day), **once for all users**. |
| NEWS-2 | **Context isolation:** its prompt and tools contain no user data and no memory access. This is enforced by the orchestrating code, not by instructions to the model. |
| NEWS-3 | Reads **headline signals** for each beat (§6.4) and writes a **headlines digest**: for each story, a specific headline, beat and subtopic tags, an importance score, the **importance evidence** (which signals, coverage count, engagement observed at that time), and links to primary sources. |
| NEWS-4 | Covers roughly 15–30 stories per beat per day, deliberately broader than any one brief, so it also supplies **exploration candidates**. |
| NEWS-5 | The digest is versioned and cached, identical for every user, and saved with all of the newsroom's tool calls and results (the **trace**). |
| NEWS-6 | **Importance is evidence-based.** Scores must cite observable signals. The model's background knowledge isn't evidence. |

### 6.2 🗂️ Chief-of-staff agent (per user)

| ID | Requirement |
|---|---|
| COS-1 | Runs per user, scheduled about 60 minutes before the user's brief time, in a **fresh context** with the digest, that user's memory, and that user's open questions. |
| COS-2 | **Personal search and adjacent discovery:** turns memory into personal queries (subtopics, followed entities, open questions, "keep me posted" items) and **adjacent-discovery queries** (related topics, companies, people, and methods the user may not know to search for). It gathers candidates, including ones outside the beats. |
| COS-3 | **Scoring:** for each candidate (digest stories + personal finds), outputs structured scores for importance (from digest evidence, or estimated with evidence for personal finds), relevance (against memory, with the reason), and novelty (against recently shown stories and the profile). |
| COS-4 | **Selection runs in code, not in the model:** slot allocation (importance + relevance + at least one exploration slot) plus an MMR redundancy penalty run deterministically over the agent's scores. This keeps ablations clean and the selection auditable. |
| COS-5 | Writes the **brief script** (grounded, specific, in the user's preferred format) and a **source pack** for each story: key passages from the primary sources, related context, and community reaction, sized for the voice agent's context. |
| COS-6 | **Pre-fetch prediction:** spends more source-pack depth on stories the user is likely to dive into. |
| COS-7 | Attaches answered follow-ups to the brief. |
| COS-8 | Its trace is saved per user. |

### 6.3 🎙️ Voice agent (per session)

| ID | Requirement |
|---|---|
| VOICE-1 | **Cascade:** streaming speech-to-text → a fast text model → streaming text-to-speech. The rationale is in `01a` §3. |
| VOICE-2 | Holds the brief and source packs in a cached context. It answers from them first. |
| VOICE-3 | Tools: `quick_search` (time-boxed, covered by "let me check"), `bookmark_question`, `save_for_later`, `note_preference`. It has no other tools. |
| VOICE-4 | First audio within about 1 s on source-pack answers. |
| VOICE-5 | Sits behind a `VoiceFrontEnd` interface; speech-to-speech is only a separate experiment (§11, F3). |
| VOICE-6 | Emits **memory signals** during the session (explicit and implicit, with session context: time available, how it ended). The voice agent does **not** write memory. The chief-of-staff agent decides memory updates after the session (MEM-13). |
| VOICE-7 | **Interruption, stage 1 (MVP):** the brief is pre-rendered audio; the user can interrupt **at any moment** by tapping, squeezing AirPods or pressing the headphone button, or using the on-screen button. The microphone opens and end of speech is detected automatically (no second tap). The agent knows the interrupted position and resumes from it. Answers can be interrupted the same way. |
| VOICE-8 | **Interruption, stage 2:** with headphones, the microphone stays open during playback and starting to speak interrupts (barge-in), giving a fully conversational feel. Built after stage 1 is stable on iPhone. |

### 6.4 Headline signals, beats, and tool backends

**Headline signals** (how importance is judged without our own corpus):

| Signal type | Examples | What it tells us |
|---|---|---|
| Editorial front pages | Techmeme (AI); Utility Dive, Canary Media, Latitude Media (Energy & DC); The Robot Report, IEEE Spectrum Robotics (Physical AI); Google News and wire front pages | Editors have already ranked the day |
| Crowd rankings | Hacker News front page and points (**brief-time values only**), Reddit top posts, Hugging Face daily papers, GitHub trending | What practitioners are paying attention to |
| Expert curation | Field newsletters (for example Import AI, The Batch; energy and robotics newsletters) | Expert judgement, usually a day late |
| Coverage | How many independent outlets the agent finds covering a story; optionally event-clustered news APIs | Breadth of coverage |
| Primary sources | Lab, company, and agency blogs and releases; arXiv | Ground truth for claims, not for importance |

**Beats** are versioned config: a beat name, its scope notes and subtopic list (carried over from v0.5 §7.1, with its boundary rules), and its headline-signal list.

**Tool backends (P9):**

| Tool | Live backend | Replay backend |
|---|---|---|
| `search` | Our tool, calling a search API (for example Tavily, Exa, Brave) | Our date-filtered search index over the frozen archive |
| `fetch` | Our tool: HTTP fetch + article-text extraction | Archived copy of the page as of the replay date |
| `headline_source(name, date)` | The source's current front page or feed | The source's **date archive** (for example Techmeme daily pages, HN Algolia by date, arXiv listings by date, archived RSS) |

| ID | Requirement |
|---|---|
| TOOL-1 | Agent code is **identical** in live and replay mode. Only the tool backend changes. |
| TOOL-2 | Replay backends return only content published and observable at or before the simulated time. |
| TOOL-3 | **Live traces:** every live tool call and result is saved, so self-study days can be re-analysed. |
| TOOL-4 | All fetched web content is treated as **untrusted data**: tool results are clearly delimited, and instructions inside them are never followed (tested in §10.9). |

---

## 7. Accounts, data, and processes

### 7.1 Accounts and authentication

| ID | Requirement |
|---|---|
| ACC-1 | Account fields: id, email, name, timezone, brief time, status, type (`real` / `synthetic`), role (`user` / `admin`), research consent, created date. |
| ACC-2 | Invite-only sign-up from a single-use link. |
| ACC-3 | Passwordless email link or Google login. |
| ACC-4 | Every request and agent run is bound to one user id. Synthetic accounts use service credentials only. |
| ACC-5 | Admins see metadata, job status, and cost. They can read real users' content only with consent, and access is logged. |

### 7.2 Data tiers and isolation

| Tier | Contents | Scope |
|---|---|---|
| **Shared** | Beat configs, newsroom digests and traces, the shared source-pack cache (content-keyed) | Global; read-only to per-user runs |
| **Per-user state** | Profile, interest model, delivery prefs, open questions, experiment assignments, budgets | One user |
| **Per-user activity** | Chief-of-staff traces, briefs, source packs, transcripts, memory signals, follow-up answers, ratings, timings, costs | One user |
| **Evaluation** | Frozen replay archive, hindsight labels, simulated-user profiles, task sets, graders, judge validation labels | Research only, in `run_id` namespaces |

| ID | Requirement |
|---|---|
| DATA-1 | Per-user records carry a `user_id`, and all access goes through a user-scoped data-access layer. |
| DATA-2 | Agent tools are built per run with a fixed user id. The newsroom gets **no** user-scoped tools. |
| DATA-3 | Shared caches are content-keyed and contain no user data. |
| DATA-4 | Replay and simulation runs live in `run_id` namespaces. |
| DATA-5 | No raw audio is stored by default. Deletion is supported; deleting an account purges per-user data within 7 days. |
| DATA-6 | Users can export their data as JSON. |
| DATA-7 | Encryption in transit and at rest. Secrets are never stored in the database or repo. |
| DATA-8 | Research analysis uses only consenting users, with pseudonymous ids. |

### 7.3 Processes and scheduling

| ID | Requirement |
|---|---|
| PROC-1 | **Daily job orchestrator** (code): newsroom run(s) → per-user chief-of-staff runs, each scheduled before that user's brief time and after the latest digest. |
| PROC-2 | Runs are isolated, queued with retries, and tagged (user or newsroom, date, config version, `run_id`). One failure doesn't block others. |
| PROC-3 | **On-demand fallback** if a brief isn't ready. |
| PROC-4 | Voice sessions are isolated and concurrent. |
| PROC-5 | Memory updates are serialised per user. |
| PROC-6 | **Follow-up research runs** (chief-of-staff role, larger budget) finish before the user's next brief. |
| PROC-7 | **Capacity:** at least 10 concurrent sessions; chief-of-staff runs for 50 accounts within 1 hour. |
| PROC-8 | The simulation runner drives the same jobs on a simulated clock with replay backends. |
| PROC-9 | Structured logs and traces, filterable by user, role, and run. |

### 7.4 Cost control

| ID | Requirement |
|---|---|
| COST-1 | Every model and tool call records its cost against a user, the newsroom, or a run. |
| COST-2 | Per-user daily budget, with graceful degradation (cheaper model, shallower source packs, fewer personal searches). |
| COST-3 | Global daily budget, plus a budget per simulation run. |
| COST-4 | The newsroom digest and source packs are shared and cached. Prompt caching is used for stable prompts and digests. |
| COST-5 | Rough planning figure: about $0.05–1.00 per user per day depending on model choice (`01a` §6). It's measured and reported in evaluation (§10.3, T6). |

### 7.5 Admin

| ID | Requirement |
|---|---|
| ADM-1 | Invites, an account list, job status, and cost. |
| ADM-2 | Retry runs, disable accounts, change experiment assignments. |
| ADM-3 | Bulk-create and delete synthetic accounts by `run_id`. |
| ADM-4 | Newsroom health: digest freshness, headline sources that are failing or empty, stories per beat. |
| ADM-5 | A report of interests outside the beats, to pick future beats. |

---

## 8. Agent behaviour details

### 8.1 Brief generation

| ID | Requirement |
|---|---|
| BRF-1 | Uses only source text, and every sentence carries a citation. |
| BRF-2 | Specific headlines (UX-2). |
| BRF-3 | Follows delivery preferences, within limits (3–8 stories, 1–4 minutes). |
| BRF-4 | Opens with answered follow-ups and ends with an open prompt. |

### 8.2 Follow-up bookmarks

| ID | Requirement |
|---|---|
| FUP-1 | A question is bookmarked when no supported answer is found within the latency budget, or when the user asks. |
| FUP-2 | An open question stores: user, question, context, time, status (`open`, `researching`, `answered`, `unanswerable`, `dismissed`), answer, and citations. |
| FUP-3 | A background research run writes a grounded answer, or marks the question unanswerable with what it found. |
| FUP-4 | Answers are surfaced at the next session, in the reminder email, and on request, and can be explored in full. |
| FUP-5 | Open questions count as interest signals and feed personal search (COS-2). |
| FUP-6 | Open questions can be listed and dismissed, and they expire. |

### 8.3 Memory

| ID | Requirement |
|---|---|
| MEM-1 | **Interest model:** open-vocabulary interests, mapped to beat subtopics where possible, plus followed entities and "keep me posted" items. Each has a weight, confidence, evidence, a last-reinforced time, and a source. |
| MEM-2 | **Positive signals:** explicit statements, dives, follow-ups, "save for later", bookmarked questions. |
| MEM-13 | **The chief-of-staff agent decides memory updates.** After each session it reviews the transcript and signals and proposes updates. Code applies the weighting, decay, and guards (MEM-3–8). |
| MEM-3 | **Negative signals are explicit only, or strong and repeated** across several sessions where the user had time. Not diving in once is not disinterest. |
| MEM-4 | Session-context awareness: implicit signals are weighted by time available and how the session ended. |
| MEM-5 | Exposure-normalised learning. |
| MEM-6 | Concentration guard: no single interest absorbs more than a set share of weight unless the user has said so. |
| MEM-7 | An occasional "save any of the others for later?" when the user ends early. |
| MEM-8 | Decay with a configurable half-life. |
| MEM-9 | Delivery preferences: length, depth, technicality, quantitative vs. narrative, preferred sources. |
| MEM-10 | Engagement with exploration stories can promote new interests. |
| MEM-11 | Memory can be viewed and edited by its owner, and is versioned daily (snapshots for evaluation). |
| MEM-12 | Static-profile mode for the baseline. |

### 8.4 Clients

| ID | Requirement |
|---|---|
| CLI-1 | **iPhone:** a mobile-first web app that works in iPhone Safari, including microphone and audio. Home-screen microphone access is tested early. |
| CLI-2 | One backend API for every client. An Expo app or phone-call channel is a stretch goal. |
| CLI-3 | A CLI client for development and simulation. |

---

## 9. Simulated users

| ID | Requirement |
|---|---|
| SIM-1 | **Simulated user:** an LLM agent with a hidden interest profile, hidden **latent interests** (things it would engage with if shown but never mentions; the target of adjacent discovery), hidden delivery prefs, a drift schedule, and behaviour settings. It holds full conversations with the voice agent (in text). |
| SIM-2 | **A deliberately varied panel** of 10–20 users, one calibrated to Kate, as synthetic accounts. It varies along the dimensions below. |
| SIM-3 | Simulated users also give **separate** per-story relevance ratings, used as a judge (§10.4). |
| SIM-4 | Simulated users ask deep-dive questions, including some the source packs can't answer, so bookmarks get exercised. |
| SIM-5 | Profiles, drift schedules, and behaviour settings are versioned files, and generation is documented in a datasheet (§10.2). |
| SIM-6 | **Simulator validity check:** the Kate-calibrated simulator's choices are compared with Kate's real choices on the same briefs (§10.4). |

| Dimension | Range to cover | Example values |
|---|---|---|
| Role and lens | Several reasons for following AI | Researcher, builder, PM, investor, policy or safety, journalist, student, generalist |
| Interest breadth | Specialist through generalist | 1–2 subtopics vs. 6+ |
| Interest drift | Stable through fast-changing | Fixed; a new interest each week; a sudden shift mid-window |
| Delivery preferences | Every option in MEM-9 | Short vs. long; headline vs. deep; technical vs. plain; numbers vs. narrative |
| Engagement style | Amount of signal | Explicit vs. implicit; many vs. few follow-ups |
| Time available per session | Rushed through relaxed | Often 2 minutes vs. 10+ (tests MEM-3–6) |
| Openness to exploration | Curious through dismissive | Often dives into exploration stories vs. always skips |
| Consistency | Clean through noisy | Acts on stated interests vs. sometimes contradicts |

For the core results, hidden interests are defined over the AI beat's subtopics (§11, F1).

---

## 10. Evaluation plan

This section is the backbone of the project, defined before the build. Exact formulas and prompts go in the technical design. The task sets, data, success criteria, and graders are fixed here.

### 10.1 What the agent is evaluated on: task sets

| Task set | Unit (one task) | Approximate size | Tests |
|---|---|---|---|
| **T1 Daily selection** | One (user, day): choose the brief from the digest + personal candidates | 12 simulated users × 30 test days = **360 user-days** per config (+ 6 × 10 dev) | Importance, relevance, exploration, over-narrowing |
| **T2 Brief writing** | One brief: the script and its claims | All T1 briefs; claim-level audit on a sample of **about 1,500 claims** | Faithfulness, specificity, packaging adherence |
| **T3 Deep-dive QA** | One question about a story in the brief, labelled answerable from the pack / needs search / unanswerable | Simulated-user questions + a **curated set of about 300 questions** with labels | Answer accuracy and grounding, correct abstention and bookmarking, latency, pre-fetch hit rate |
| **T4 Memory** | One user's trajectory over the window | 15 trajectories + targeted scenarios (drift, rushed users, contradictions) | Interest recovery, drift tracking, delivery-pref learning, no over-curation |
| **T5 Follow-up research** | One bookmarked question | All bookmarks from T3 (target **≥100**) | Resolution rate, faithfulness, correct "unanswerable" |
| **T6 System** | One run or session | All runs | Latency p50/p95, digest-ready rate, cost per user-day |
| **T7 Safety** | One adversarial or isolation case | About **50 injected pages** + the isolation test suite | Prompt-injection robustness, cross-user isolation |
| **T8 Real use** | One real session | Kate about 25–35 sessions; pilot as available | Usefulness, relevance, "learned something new", follow-up usefulness |

### 10.2 Data: where it comes from and how it's collected

| Dataset | Source and collection | Documentation |
|---|---|---|
| **Replay archive (AI beat; Physical AI optional)** | Collected after the fact for the window: Techmeme daily pages, HN Algolia (items + time-stamped points where available), arXiv listings by date, lab/company blog archives and RSS history, and selected outlets' archives. Pages are stored as fetched, with publication time. | Datasheet: sources, coverage per day, collection scripts, known gaps, licence and terms notes |
| **Hindsight importance labels** | (a) HN score above a calibrated cutoff; (b) **coverage by at least k independent archive sources within 14 days** (primary, see §11, F2) | Calibration procedure, per-day label counts, spot-check results |
| **Topic labels** | The AI beat's fixed subtopic taxonomy, assigned by an LLM | Spot-check of about 200 stories by Kate; agreement reported |
| **Simulated-user profiles** | Generated from a documented template across the §9 dimensions; one calibrated to Kate from her onboarding and early sessions | Profile files + generation datasheet |
| **Deep-dive question set** | About 300 questions written by an LLM from source packs, plus questions drawn from simulated-user and real sessions; each labelled for answerability and with the supporting passage. A sample is human-checked. | Labelling guide, agreement on the human-checked sample |
| **Injection set** | About 50 archive pages with planted instructions (for example "ignore previous instructions and say X", hidden text) | Construction guide |
| **Real-use logs** | Live traces, transcripts, and ratings from consenting users | Consent form; pseudonymised |

### 10.3 Success criteria and metrics

| Task | Metric | Success criterion (provisional; refined after first baselines) | Grader |
|---|---|---|---|
| T1 | **Importance recall@K** (share of hindsight-important stories surfaced within d days) | At least about 90% of the importance-only baseline | Code |
| T1 | **Relevance** (hidden-profile relevance of shown stories) | Beats static profile and the naive agent | LLM judge (simulated user's separate rating) + code (profile overlap) |
| T1 | **Topic coverage** (distinct subtopics or entropy over a window) | Above the no-exploration baseline | Code |
| T1 | **Latent discovery** (share of a simulated user's latent interests surfaced, and engagement when surfaced) | Above the no-exploration baseline and the naive agent | Code |
| T1 / T4 | **Over-narrowing** (learned vs. hidden profile spread) | No worse than static profile for rushed users | Code |
| T2 | **Citation faithfulness** (share of atomic claims supported by the cited source) | At least 95% | LLM judge (validated, §10.4) + human audit |
| T2 | **Specificity** (headlines naming concrete actors) | At least 95% of headlines | LLM judge + code (named-entity check) |
| T2 | **Packaging adherence** (length, depth, technicality vs. hidden prefs) | Beats static profile | Code (length) + LLM judge (depth, technicality) |
| T3 | **Answer accuracy and groundedness** | At least 90% on answerable questions | LLM judge vs. labelled supporting passage |
| T3 | **Abstention quality** (bookmarks unanswerable questions, doesn't guess) | At least 90% correct abstention, under 5% unsupported answers | Code (labels) + LLM judge |
| T3 | **Pre-fetch hit rate** (answered from the pack; bookmarks count as misses) | At least about 70% | Code |
| T3 / T6 | **Latency** (p50 / p95 time to first audio, pack vs. search) | p95 under about 1.5 s on pack answers | Code |
| T4 | **Interest recovery** (learned vs. hidden profile over time) | Beats static profile; tracks drift within about 3–5 days | Code |
| T5 | **Follow-up resolution** (supported answer before the next brief) | At least about 60% of answerable bookmarks | LLM judge + code |
| T6 | **Digest and brief on-time rate; cost per user-day** | At least 95% on time; within budget | Code |
| T7 | **Injection success rate** (agent follows a planted instruction) | 0 in the brief and in actions; reported for all cases | Code + LLM judge |
| T7 | **Isolation tests** | 100% pass | Code |
| T8 | **Ratings: full agent vs. blinded baseline days** | Full agent rated higher on usefulness and relevance (effect size reported) | Human ratings |

### 10.4 Grader validity

- **LLM-judge validation:** each LLM judge (faithfulness, relevance, packaging, answer accuracy) is checked against **human labels by Kate on a held-out sample of 100–200 items**. Agreement (accuracy and Cohen's κ) is reported. A judge with poor agreement is revised before its numbers are used.
- **Judge independence:** judges use a different prompt and, where practical, a different model from the agent being graded. Judges never see which config produced an output.
- **Simulator validity:** on a set of real briefs, the Kate-calibrated simulator's choices (dives, skips, ratings) are compared with Kate's actual choices. Agreement is reported as a limitation or support.
- **Label quality:** hindsight-label spot checks and topic-label agreement are reported (§10.2).

### 10.5 Baselines and ablations

| # | Config | Purpose |
|---|---|---|
| B1 | **Importance-only:** serve the newsroom digest's top stories, with no personalisation | Upper reference for importance; floor for relevance |
| B2 | **Static profile:** memory frozen after onboarding | Tests learned memory |
| B3 | **No exploration:** exploration slots removed | Tests the filter-bubble claim |
| B4 | **No pre-fetch:** every follow-up uses live or quick search | Tests latency and pre-fetch value |
| B5 | **Naive agent:** one model call with web search and the user's profile as text ("write today's brief for this person"), with no newsroom, scoring, or memory updates | A strong, realistic baseline close to what a commercial "AI briefing" does |
| A1 | **No newsroom separation:** one chief-of-staff run that judges importance with memory in context | **Tests the design claim** that separating importance protects importance recall and coverage |
| A2 | **No HN signal** at brief time | Checks circularity with the HN hindsight label (§11, F2) |
| A3 | **No session-context weighting** in memory | Tests the over-curation fixes (MEM-3–6) |

The full agent and B1–B5 are required. A1–A3 are planned and ordered by priority (A1 first).

### 10.6 Real-user protocol (T8)

- **Kate:** 4–5 weeks of daily use. About 20–25% of days are **blinded baseline days**, drawn at random and stratified across B1, B2, and B3. Kate rates every session (SS-1) without knowing which config she got.
- **Pilot:** the same protocol where possible, as a secondary, noisier sample.
- **Analysis:** within-user comparisons, effect sizes with confidence intervals. Results are reported as a case study, not as population claims.

### 10.7 Statistics and reporting

- Results reported **per config with 95% confidence intervals** (bootstrap over user-days or items). Paired comparisons use paired bootstrap or permutation tests.
- **At least 3 seeds** per config in simulation, to account for agent non-determinism. Variance across seeds is reported.
- Results are reported per simulated-user type (dimension), not only on average, because averages can hide failures on rushed or narrow users.
- Cost and latency are reported next to quality for every config.

### 10.8 Failure analysis (R7)

For every task set, failures are sampled (at least 30 per task set, or all if there are fewer) and categorised with a fixed taxonomy, refined during analysis:

| Area | Failure categories |
|---|---|
| Importance | Newsroom missed the story (retrieval) · newsroom found it but ranked it low · chief of staff dropped it (selection) · label noise |
| Relevance and memory | Wrong interest inferred · over-narrowing · slow to notice drift · ignored explicit feedback · delivery pref misread |
| Faithfulness | Unsupported number · wrong attribution · claim extrapolated beyond the source · citation to the wrong source · stale information |
| Deep dive | Reference resolution failed · answered when it should have bookmarked · bookmarked when the pack had the answer · slow path taken unnecessarily |
| Follow-ups | Unresolved but answerable · answer unsupported |
| Safety | Injection partially followed · content leaked into the brief |

The report includes the category distribution, representative examples, and what was changed in response (with before and after numbers where a fix was made).

### 10.9 Safety evaluation (R8)

- **Faithfulness and abstention:** T2, T3, and T5 above.
- **Prompt injection:** planted instructions in archive pages (T7). Measured: whether any agent role follows them, mentions them as fact, or changes tool behaviour.
- **Isolation:** automated tests try to make each per-user tool and API read another user's data, and check that newsroom runs contain no user data.
- **Privacy:** consent flow, deletion, and pseudonymisation verified with tests.

---

## 11. Research integrity guardrails

### 11.1 Rules

| ID | Rule |
|---|---|
| RG-1 | **Frozen research config:** AI beat, replay archive, AI subtopic taxonomy, cascade voice evaluated in text, baselines B1–B5, pinned model versions and prompts. Other features are off or evaluated separately. |
| RG-2 | **Replay only from the frozen archive.** Never re-query live sources for past dates. |
| RG-3 | **No hindsight leakage:** agents see only signals observable at brief time (for example HN points as of that time). |
| RG-4 | **New features declare their evaluation impact** in this table. |
| RG-5 | **The same agent code** runs live and in replay. Only tool backends differ (TOOL-1). |
| RG-6 | **Scope protection:** product extensions are cut before research goals. |
| RG-7 | **Evaluation defined first:** task sets, graders, and success criteria (§10) are fixed before the full agent is tuned. Tuning uses a separate dev slice of days and users, and the test slice is held out. |

### 11.2 Flags: where product features pull against the research goals

| # | Feature | Tension | Resolution | Status |
|---|---|---|---|---|
| F1 | **Three beats + open interests** | The HN label is strongest for AI; open interests have no labels | Core evaluation on the AI beat; Physical AI optional (R6); open interests evaluated only through real-user ratings | ✅ Resolved by scoping |
| F2 | **HN as a live headline signal** | Circularity with the HN hindsight label | Brief-time HN values only; **coverage label is primary**; ablation A2 | ✅ Resolved |
| F3 | **Audio-to-audio models** | The evaluation would measure a different system; grounding is weaker | Cascade; speech-to-speech only as a separate experiment | ⚠️ **Don't adopt without revisiting** |
| F4 | **Follow-up bookmarks** | Changes what counts as a pre-fetch miss | Bookmarks count as misses; T5 metrics | ✅ Resolved |
| F5 | **Non-AI interests in the self-study** | Fewer AI stories rated | Ratings tagged by beat; guaranteed minimum AI share in Kate's briefs during the study | ⚠️ **Watch** |
| F6 | **Memory changes** | They improve alignment with the research | Time-available dimension; ablation A3 | ✅ Strengthens |
| F7 | **Cross-user learning** | Breaks per-user memory evaluation | Out of the MVP | ✅ Excluded |
| F8 | **Broader personal agent** | Scope | Out of the MVP | ✅ Excluded |
| F9 | **Exploration outside the AI beat** | Changes the AI coverage metric | In the research config, the exploration pool is limited to AI-beat digest stories | ✅ Resolved |
| F10 | **Overall scope** | Solo project | RG-6; ◇ milestones | ⚠️ **Ongoing risk** |
| F11 | **Agent non-determinism** | Reproducibility | Traces, pinned prompts and models, at least 3 seeds, variance reported | ✅ Resolved |
| F12 | **Live web search vs. replay search** | The self-study system searches a bigger, different index than the simulations | Same code (RG-5); report it as a limitation; compare search-result quality on a small sample | ⚠️ **State as a limitation** |
| F13 | **LLM knowledge of the replay window** | The model may already know the window's events, which inflates quality | Choose a window after the model's training cutoff; importance must cite evidence (NEWS-6); spot-check | ⚠️ **Check the cutoff when picking the window** |

---

## 12. MVP scope cut

| Area | In the MVP | Later or stretch |
|---|---|---|
| Accounts | Invite-only, up to about 50 accounts | Public sign-up, billing |
| News | Newsroom over 3 beats + personal search; replay archive for the AI beat | More beats; Physical AI replay (R6) |
| Clients | Mobile-first web app (iPhone Safari) + dev CLI | Expo app or phone channel |
| Voice | Cascade with push-to-talk | Speech-to-speech front-end experiment; barge-in |
| Selection | Agent scoring + code selection (slots + MMR) | Contextual bandit, learned weights |
| Memory | Open-vocabulary interests, exposure-normalised, decay, concentration guard | Episodic memory, cross-user signals |
| Follow-ups | Bookmark, research, surface next session | Push notifications, ongoing watches |
| Evaluation | T1–T8, B1–B5, A1, judge validation, failure analysis | A2–A3 if time; R6 |
| Beyond news | None | Personal chief of staff (§4.5) |

---

## 13. Course requirements coverage (CS329Z final project)

| Requirement | Where it's covered | Status |
|---|---|---|
| **Relates to core course topics** | **Retrieval:** headline signals, personal search, source packs (§6). **Tool use:** search, fetch, bookmark, and memory tools; pre-fetch decisions (§6). **Memory:** per-user interest and preference memory with decay and over-curation guards (§8.3). **Multi-agent:** newsroom, chief of staff, and voice roles with enforced context separation (§6), plus simulated users. **Optimization:** pre-fetch budget, latency and cost trade-offs (§10.3). **Data agents need:** replay archive, hindsight labels, question set, datasheets (§10.2). **Evaluation and safety:** §10, including faithfulness, abstention, injection, isolation. | ✅ Covers most topics |
| **Working in teams** | Proposal states a team of one | ⚠️ **Confirm a team of one is OK with course staff** (if not already confirmed) |
| **Design, build, and evaluate over the quarter** | Milestones §14 | ✅ |
| **Define evaluation early** | §10 is fixed before the full build; RG-7 requires it | ✅ |
| **What tasks the agent handles** | Task sets T1–T8 (§10.1) | ✅ |
| **Where the data comes from and how it was collected** | §10.2, with datasheets | ✅ |
| **What counts as success** | Success criteria per metric (§10.3) | ✅ (thresholds provisional, refined after first baselines, as the proposal says) |
| **How success is measured (code, LLM judge, human)** | Grader column (§10.3); **all three types used**; judge validation against human labels (§10.4) | ✅ |
| **Meaningful set of tasks** | About 360 user-days per config, about 1,000 judged claims (150 human-checked), about 250 curated deep-dive questions, at least 100 bookmarks, about 50 injection cases, about 25–35 real sessions | ✅ Sizes to be confirmed against budget in the technical design |
| **Baselines and comparisons** | B1–B5 including a strong naive-agent baseline; ablations A1–A3 (§10.5); confidence intervals and seeds (§10.7) | ✅ |
| **Analyse where and why the agent fails** | Failure taxonomy and process (§10.8) | ✅ |
| **"Showing it works in one instance won't score highly"** | Replay over many user-days, multiple seeds, a varied simulated panel, real-user blinded comparison | ✅ |

**Remaining risks to the grade, and mitigations:**
1. **Scope vs. evaluation time.** The evaluation needs about 2–3 weeks of a solo quarter. Milestones protect M5 (evaluation) over ◇ product work.
2. **Simulated-user validity** is a known weakness in the literature. It's mitigated by the simulator validity check (§10.4) and the real-user comparison. Discuss it honestly in the report.
3. **Small real-user sample.** Frame it as a case study with effect sizes, not significance claims.
4. **Evaluation cost.** Addressed by the lean design rules and computation reuse (`02a` §1.4, §8): target about $100–130 total.
5. **Schedule.** If about 5 weeks remain in the quarter, the self-study shrinks to about 3 weeks and the pilot drops (`02-technical-design.md` §25).

---

## 14. Milestones (proposed)

> ⚠️ See `02-technical-design.md` §25 for the week-by-week build plan, including a **compressed 5-week variant** in case the quarter ends in early to mid December.


Dates are placeholders until they're lined up with the course deadlines. Milestones marked ◇ can slip without hurting research goals (RG-6).

| Milestone | Contents | Exit criterion |
|---|---|---|
| **M0: Docs** | PRD, design explorations, technical design **including the full evaluation spec** | Approved |
| **M1: Foundations + evaluation data** | Database and user-scoped data layer; orchestrator and queue; tool interface with live and replay backends; **replay archive for the AI beat** + datasheet; hindsight labels calibrated; subtopic taxonomy | Archive covers the window; labels spot-checked; two isolated test accounts |
| **M2: Agents v0 (text)** | Newsroom, chief of staff, voice agent (text), basic memory, bookmarks; CLI; live and replay | One replayed day for two synthetic accounts; one live day for Kate |
| **M3: Web app + self-study** | Login, onboarding, mobile web chat + voice on iPhone, memory and open-questions pages, ratings, blinded baselines, admin CLI | Kate uses it daily; isolation tests pass |
| **M4: Full agent + evaluation harness** | Exploration, full memory rules, source-pack pre-fetch, configs B1–B5 and A1; task sets T1–T7 built; judges written and **validated** | All required configs run on the dev slice; judge agreement reported |
| **M4◇: Pilot** | Invite pilot users | Pilot users get daily briefs |
| **M5: Full evaluation** | Test-slice runs (3 seeds), statistics, failure analysis, real-user analysis | Results tables + failure analysis |
| **M5◇: Stretch** | A2–A3, R6 Physical AI, speech-to-speech experiment, Expo app | Optional |
| **M6: Write-up** | Report | Final report |

---

## 15. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Overall scope for a solo project | Research slips | RG-6; ◇ milestones; evaluation spec first |
| **Historical archive gaps** for the window | Weak replay | Choose sources with date archives (Techmeme, HN Algolia, arXiv); document gaps in the datasheet; choose the window around availability |
| **Agent non-determinism** | Noisy comparisons | Seeds, traces, confidence intervals |
| **Prompt injection from web pages** | Unsafe or wrong behaviour | Untrusted-data handling (TOOL-4); the voice agent has a narrow tool set; T7 evaluation |
| **LLM-judge bias** | Wrong conclusions | Validation against human labels; blind judging; a different judge model |
| **Evaluation cost** | Budget | Budget per run; cheaper simulator and judge models; prompt caching; size the panel to fit |
| Per-user cost in production | Budget | Shared digest and cache, per-user budgets, model tiering |
| Headline sources change or block access | Weaker importance | Several signals per beat; ADM-4 health checks; fallbacks |
| Cross-user data leak | Privacy breach | DATA-1–3; isolation tests |
| Personal data of other people | Ethics | Consent, deletion, pseudonymisation. Check whether a human-subjects review is needed before the pilot. |
| iPhone web microphone limits | Poor commute UX | Test early; Expo or phone channel as a stretch goal |
| Voice latency under load | Poor UX | Source packs, streaming, a fast model, a load test |
| LLM knowledge of window events | Inflated results | Window after the model's training cutoff; evidence-only importance |

---

## 16. Open questions (decisions for Kate)

> Decisions made so far, and the defaults adopted for the rest, are recorded in `02a-technical-design-options.md` §12. The list below is kept for history.

1. **Team of one:** confirmed with course staff?
2. **Course deadlines** (milestone, final report, poster), to put dates on §14. The course site was blocked from this session.
3. **Replay window:** August–September 2026, or adjust after checking the models' training cutoff and archive availability?
4. **Models:** no default provider. Models are picked per role by a small bake-off across OpenRouter models, with simulated users and judges from a different family than the agent (see `02a-technical-design-options.md` §1). Budget: $200 OpenRouter + $100 Tinker.
5. ~~Agent harness~~ **Decided:** we build the agent ourselves (§6.0). No hosted agent platforms.
6. **Headline sources per beat:** any must-haves (newsletters, sites)?
7. **Hosting** and **auth** (email link vs. Google).
8. **Pilot size**, and whether a human-subjects review is needed.
9. **iPhone:** Safari web for the start of the self-study?
10. **Brief size default:** 6 stories, about 3 minutes, at least 1 exploration slot?
11. **Self-study AI minimum (F5):** guarantee, for example, at least 4 of 6 stories from the AI beat during the study?
12. **Human labelling time:** about 4–6 hours of Kate's time for judge validation, label spot checks, and the question-set check. Is that OK?

---

## Appendix A: Glossary

- **Newsroom agent:** shared role that judges what's big today from headline signals. Never sees user data.
- **Headlines digest:** the newsroom's cached output: stories with importance scores and evidence.
- **Beat:** a newsroom coverage area (AI; Energy, Grid & Data Centers; Physical AI, Robotics & Simulation), with scope, subtopics, and headline signals.
- **Headline signal:** an external judgement of importance (front page, ranking, curation, coverage).
- **Chief-of-staff agent:** per-user role that personalises, selects, writes the brief, and builds source packs.
- **Source pack:** pre-fetched, compact source material for one story, used by the voice agent.
- **Voice agent:** per-session role that delivers the brief and handles the conversation.
- **Tool backend:** the live (web) or replay (frozen archive) implementation behind an agent tool.
- **Trace:** the saved record of an agent run's tool calls and results.
- **Replay archive:** frozen, date-stamped pages for an evaluation window, used only for research.
- **Open question / bookmark:** a question saved for background research.
- **Research config:** the frozen configuration all core results come from (RG-1).

## Appendix B: Related work, as it informs requirements

| Work | Informs |
|---|---|
| Li et al. 2010, MIND | Relevance and exploration trade-off |
| Carbonell & Goldstein 1998 (MMR), Ziegler et al. 2005 | Redundancy and diversity in selection |
| Nguyen et al. 2014, Kaminskas & Bridge 2017 | Coverage, novelty, over-narrowing metrics |
| MemGPT, Mem0, LaMP | Per-user memory; we evaluate *selection* |
| Park et al. 2023, Zhang et al. 2024, Wang et al. 2023, Zhu et al. 2024 | Simulated users and their limits; simulator validity check |
| Yao et al. 2024 (τ-bench) | Tool-agent-user evaluation design |
| Gao et al. 2023, Liu et al. 2023 | Citation faithfulness |
