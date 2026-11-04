# PRD: Conversational Briefing Agent (MVP)

**Status:** Draft v0.5, ready for review
**Owner:** Kate Wang
**Source:** `proposal.pdf` ("Expanding the Aperture", CS329Z Fall 2026)
**Companion doc:** `01a-design-explorations.md` (corpus, sources, voice, mobile: options and the path to open topics)
**Next docs:** `02-technical-design.md` (after this PRD is approved), then the build

**Changelog**
- **v0.5:** The MVP uses a **fixed catalog of three curated topic channels** instead of open topics:
  - **AI** (the anchor and research domain);
  - **Energy, Grid & Data Centers**;
  - **Physical AI, Robotics & Simulation**.

  Each has a defined scope, subtopics, curated sources, and boundary rules (§7.1). Removed the topic resolver, on-the-fly channel creation, and the open-web source policy. The channel architecture stays, so more topics can be added later. Requests for topics outside the catalog are logged as demand signals. Research flags are simplified, and an optional second-domain evaluation is added.
- **v0.4:** Topic channels, follow-up bookmarks, memory that doesn't over-curate, iPhone access, specific headlines, research guardrails, future vision.
- **v0.3:** Target users described broadly; persona variety moved to the simulated-user panel.
- **v0.2:** Multi-user from day one.
- **v0.1:** Initial draft from the proposal.

---

## 1. Summary

A chief-of-staff style agent that gives each user a short spoken morning brief on **the topics they follow**, asks what they want to dig into, holds a back-and-forth with a cited source for every claim, and updates **that user's** memory of their interests and preferred format. When it can't answer a question on the spot, it **bookmarks it, researches it in the background, and follows up**.

Each brief balances three things:

1. **Importance:** stories that matter in the world.
2. **Relevance:** stories that matter to this user.
3. **Exploration:** stories that widen the user's view rather than reinforce a filter bubble.

To keep voice turns fast, the agent pre-fetches material for each user's likely deep dives before the brief.

The MVP covers a **fixed catalog of three curated topic channels**:

| Channel | One-line scope |
|---|---|
| 🧠 **AI** | Models, research, products, business, and policy of AI. **The research domain.** |
| ⚡ **Energy, Grid & Data Centers** | The clean-energy transition and the power and infrastructure buildout behind AI |
| 🤖 **Physical AI, Robotics & Simulation** | AI in the physical world: robots, autonomy, and the simulation and world models that train them |

Users pick channels at sign-up, weight them, and tell the agent what they care about within each. Memory learns the finer-grained interests. Each channel is gathered **once** and shared by everyone who follows it.

The MVP is a **multi-user service**. Each user has an account, isolated stored data, and their own scheduled daily processes.

The MVP has to do three jobs:

- **A usable daily product** for anyone on the go who wants to stay current in these fields: Kate first, then pilot users from a variety of roles.
- **A reproducible research system.** The **core research evaluation runs on the AI channel**, as in the proposal: a frozen corpus replayed with simulated users and baselines, under a frozen research configuration (§9).
- **A foundation for expansion.** Adding a topic channel is a configuration change, and the architecture leaves room for open topics later (`01a` §1).

**Rule for every extension:** product features may go beyond the research scope, but they must not weaken or silently change the research evaluation (§9).

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

**Anyone who wants to stay current in a fast-moving field and is on the go.** They have little time, the news is scattered, and the moments they do have (a commute, a walk, the gym) suit listening and talking better than reading and scrolling.

The MVP's three channels share a thread: **AI, the energy needed to run it, and AI moving into the physical world**. That covers a wide range of people: researchers, engineers, founders, investors, operators, policy people, and students. The product doesn't assume fixed user types. The agent learns each person's interests and format from conversation.

**Real users in the MVP:**
- **Kate:** the first user, the primary self-study participant, and the admin.
- **Pilot users:** about 5–15 people from a variety of roles, recruited after Kate's own use is stable (§11).

**Simulated users** are where variety is designed in on purpose (SIM-2). They run as `synthetic` accounts on the same infrastructure.

---

## 4. Goals and non-goals

### 4.1 Product goals (MVP)

- **G1. Daily brief.** A spoken or text brief of about 5–7 stories in roughly 2–4 minutes, drawn from the user's channels. Each story is **specific** (it names the company, person, product, paper, project, or bill) and carries a source.
- **G2. Conversational deep dive.** The user describes what they want in their own words. They don't need to remember list numbers.
- **G3. Memory from conversation.** The agent infers interests (channel weights and subtopics) and delivery preferences, **without over-reading a short session**. It lets stale interests fade and keeps room for new ones.
- **G4. Exploration.** Every brief reserves explicit, labelled slots for stories outside the user's current profile: less-followed subtopics, or channels the user doesn't follow.
- **G5. Low latency.** Most follow-ups are answered from a pre-fetched cache.
- **G6. Groundedness.** Every factual claim is tied to a cited source item.
- **G7. Follow-up bookmarks.** If the agent can't answer well live, it says so, saves the question, researches it in the background, and brings back a grounded answer later.
- **G8. Catalog topics.** Users choose and weight channels at sign-up and can change them by voice ("more on data centers", "drop energy"). Requests outside the catalog are handled politely and logged.

### 4.2 Platform goals (MVP)

- **P1. Accounts.** Invite-based sign-up, login, and a short onboarding: channels, interests within them, delivery preferences, timezone, and brief time.
- **P2. Per-user data isolation.**
- **P3. Per-user processes**, isolated from each other.
- **P4. Shared topic channels.** News gathering, clustering, and importance scoring run once per channel and are shared by all of its followers.
- **P5. Cost limits** per user and overall.
- **P6. Data rights:** view, export, delete, and separate research consent.
- **P7. Admin tools.**
- **P8. iPhone access**, including voice.
- **P9. Channels as config.** A new channel (sources, subtopics, schedule) can be added with a config file and no agent code changes.

### 4.3 Research goals (MVP), all held

- **R1. Replay mode.** Deterministic replay of a frozen **AI-channel** corpus (for example August–September 2026) on a simulated clock, with no future leakage.
- **R2. Ablations.** Full agent vs. importance-only, static profile, no exploration, and no pre-fetch.
- **R3. Simulated users.** 10–20 varied users with hidden, drifting profiles, one calibrated to Kate.
- **R4. Evaluation harness.** Every metric in §10.
- **R5. Real-user study.** Kate's 4–5 week self-study with daily ratings and blinded baseline days; pilot data as a secondary sample.
- **R6 (optional extension). Second-domain check.** Repeat the importance-recall and coverage metrics on the **Physical AI** channel, to test whether the results hold outside the core AI domain (§9, F1).

### 4.4 Non-goals (MVP)

- **Open topics** (following anything at all). The path there is designed (`01a` §1), but not built.
- **Public sign-up, billing, teams, or sharing.**
- Large scale. The MVP targets up to about 50 accounts.
- Custom speech models, or an **end-to-end speech-to-speech agent** as the main path (§9, F3).
- A native Swift app; full-duplex voice with barge-in.
- Training or fine-tuning models.
- Paywalled content and full-text redistribution.
- **Cross-user learning** (§9, F7).
- Everything in §4.5.

### 4.5 Future vision (bookmarked, NOT in the MVP)

- **More channels, then open topics.** Add catalog channels driven by logged topic requests. Then add a news-API provider and a topic resolver, so users can follow anything (`01a` §1).
- **A broader personal chief of staff** beyond news: email triage, tasks and calendar, meeting prep, in the same conversational, memory-driven style.

The MVP keeps these paths open by making channels config-driven, putting source providers and agent tools behind interfaces, and keeping the user model general.

---

## 5. User experience

### 5.1 Account lifecycle

```
 invite ──► sign up / log in ──► onboarding (≈2 min) ──► daily loop ──► manage memory / channels / data
                                    ├─ pick channels + rough weights (AI / Energy & DC / Physical AI)
                                    ├─ "what do you care about within these?" (free text or voice)
                                    ├─ delivery prefs (length, depth, technicality)
                                    ├─ timezone + brief time
                                    └─ research consent (opt-in)
```

Free-text interests are mapped to each channel's subtopics. They seed the interest model with low confidence, so conversation overrides them quickly.

### 5.2 The daily loop

```
 SHARED, PER CHANNEL (scheduled)               PER USER (scheduled before the user's brief time)
 ┌──────────────────────────────┐             ┌─────────────────────────────────────────────────┐
 │ AI │ Energy & DC │ Physical AI │  stories   │ 1. PRE-COMPUTE: candidates from followed channels│
 │ fetch → dedupe → cluster →    │──────────► │    + exploration pool → relevance, novelty →     │
 │ importance within channel     │            │    channel slots + MMR → brief → pre-fetch →     │
 └──────────────────────────────┘             │    attach answered follow-ups                    │
                                              └──────────────┬──────────────────────────────────┘
                                                             ▼  user opens a session
                                              ┌─────────────────────────────────────────────────┐
                                              │ 2. BRIEF (+ "you asked yesterday…")  3. DEEP DIVE │
                                              │    cache first → live search → bookmark           │
                                              └──────────────┬──────────────────────────────────┘
                                                             ▼  session ends
                                              ┌─────────────────────────────────────────────────┐
                                              │ 4. UPDATE MEMORY   5. RESEARCH BOOKMARKED Qs      │
                                              └─────────────────────────────────────────────────┘
```

If the brief isn't ready when the user opens a session, it's computed on demand.

### 5.3 Example session (illustrative; companies and products are fictional)

The user follows AI (high), Energy & Data Centers (medium), and doesn't follow Physical AI.

> **Agent:** Morning. Six stories, about three minutes. Also, you asked yesterday why Halden's chip launch slipped. I found the answer, and it's at the end.
> 1) **Northwind AI released Atlas-3**, an open-weights reasoning model. It scores within two points of the top closed models on the main math and coding benchmarks. *[Northwind blog; HN discussion]*
> 2) **A Stanford and CMU paper finds long-context retrieval accuracy falls by about a third past 200k tokens**, across five models. This one's because you've been following agent memory. *[arXiv]*
> 3) **The EU AI Office published draft guidance** on what general-purpose model providers must disclose about training data. *[Reuters]*
> 4) **Voltaic Storage signed a 2 GWh grid battery deal with Texas utility Lonestar Power**, the largest sodium-ion order so far. *[Utility Dive; Canary Media]*
> 5) **Cirrus Cloud is pairing a 1 GW Arizona data center with an on-site gas plant**, because the grid connection is five years out. *[Data Center Dynamics]*
> 6) **Outside your usual (Physical AI):** **Kinema released an open robot foundation model trained mostly in simulation**, and says it transfers to real warehouse arms with no fine-tuning. *[The Robot Report; arXiv]*
> What do you want to dig into?
>
> **User:** The long-context paper. Which models held up best?
>
> **Agent:** *(answers from the pre-fetched paper with figures and a citation)* …
>
> **User:** Does that hold for Atlas-3 too?
>
> **Agent:** The paper didn't test Atlas-3, and I couldn't find anyone who has yet. I'll look into it and tell you tomorrow.
>
> **User:** Great. I've got to go, but the battery one sounded interesting too.
>
> **Agent:** I'll save the battery story and bring you more tomorrow. Here's the Halden answer quickly: …

Memory written to **this user's** store:
- AI › `long-context / agent memory` ↑ (explicit dive + follow-up)
- Energy › `grid storage` ↑ (explicitly saved, even though there was no time to dive)
- the other stories they didn't dive into, including the Physical AI exploration story: **no negative signal**, because the session was cut short
- open question: "Does the long-context degradation hold for Atlas-3?" → background research

### 5.4 Interaction requirements

| ID | Requirement |
|---|---|
| UX-1 | The brief opens with the story count and expected length, plus any answered follow-ups. |
| UX-2 | **Specific headlines:** every story names the concrete actors and objects in its first sentence. No generic phrasing ("a frontier lab", "a utility") when the source names them. |
| UX-3 | **Natural references:** stories can be referred to by description, keyword, or position. The agent resolves the reference and asks briefly if it's ambiguous. |
| UX-4 | Open questions that aren't tied to a story are supported ("what's going on with Nvidia this week?"). |
| UX-5 | *Why you're hearing this* tags where useful: "big news", "for you", "outside your usual (channel)". |
| UX-6 | Supported intents include: dive, ask, "more like this", "less of this", "save for later", "more / less [channel or subtopic]", "follow / unfollow [channel]", "go deeper" / "simpler", "who's reacting?", "source?", "what do you know about me?", "what questions do I have open?", "I only have N minutes", and "end". |
| UX-7 | **Out-of-catalog requests** ("follow Formula 1"): the agent explains which topics it covers, logs the request (§7.1, CH-9), and answers one-off questions about the topic via live search where it can. |
| UX-8 | Citations are spoken lightly and shown in full in the transcript. |
| UX-9 | Unsupported claims are never made. The agent offers to bookmark the question instead. |
| UX-10 | Text and voice modes share the same agent logic. |
| UX-11 | Users can see and edit their past briefs, transcripts, open questions, channels, and memory. |
| UX-12 | Optional email reminder at brief time. |

---

## 6. Accounts, data, and processes

### 6.1 Accounts and authentication

| ID | Requirement |
|---|---|
| ACC-1 | Account fields: id, email, name, timezone, brief time, status, type (`real` / `synthetic`), role (`user` / `admin`), research consent, created date. |
| ACC-2 | Invite-only sign-up from a single-use link. |
| ACC-3 | Passwordless email link or Google login. |
| ACC-4 | Every request and session is bound to one user id. Synthetic accounts use service credentials only. |
| ACC-5 | Admins see metadata, job status, and cost. They can read real users' content only with consent, and access is logged. |

### 6.2 Data tiers and isolation

| Tier | Contents | Scope |
|---|---|---|
| **Shared** | Channel configs, source registries, ingested items, stories, importance scores, labels, the content cache, topic-request counts (aggregated) | Global, read-only to user processes |
| **Per-user state** | Profile, channel follows and weights, interest model, delivery prefs, open questions, experiment assignments, budgets | One user |
| **Per-user activity** | Briefs, selections, transcripts, pre-fetch manifests, follow-up answers, topic requests, ratings, timings, costs | One user |

| ID | Requirement |
|---|---|
| DATA-1 | Every per-user record carries a `user_id`, and all access goes through a user-scoped data-access layer. |
| DATA-2 | Agent tools are built per session with a fixed user id. |
| DATA-3 | Shared caches are content-keyed and contain no user data. Topic-request counts are aggregated, with no user identifiers. |
| DATA-4 | Replay and simulation runs live in `run_id` namespaces. |
| DATA-5 | No raw audio is stored by default. Deletion is supported; deleting an account purges per-user data within 7 days. |
| DATA-6 | Users can export their data as JSON. |
| DATA-7 | Encryption in transit and at rest. Secrets are never stored in the database or repo. |
| DATA-8 | Research analysis uses only consenting users, with pseudonymous ids. |

### 6.3 Processes and scheduling

| ID | Requirement |
|---|---|
| PROC-1 | **Channel ingestion jobs:** one per channel, on that channel's schedule. They're idempotent, and every item is written to the **timestamped snapshot store**. |
| PROC-2 | **Per-user pre-compute job:** once per local day, about 60 minutes before the user's brief time. |
| PROC-3 | Jobs are isolated, queued with retries, and tagged with `user_id` or `channel_id`, date, config version, and `run_id`. |
| PROC-4 | **On-demand fallback** if the brief isn't ready. |
| PROC-5 | Live sessions are isolated and run concurrently. |
| PROC-6 | Memory updates are serialised per user. |
| PROC-7 | **Follow-up research job** per bookmarked question, with a larger budget than a live turn. It must finish before the user's next brief. |
| PROC-8 | **Capacity:** at least 10 concurrent sessions; pre-compute for 50 accounts within 1 hour. |
| PROC-9 | The simulation runner drives the same jobs on a simulated clock. |
| PROC-10 | Structured logs, filterable by user, channel, and run. |

### 6.4 Cost control

| ID | Requirement |
|---|---|
| COST-1 | Every external call records its cost against a user, a channel, or the shared pipeline. |
| COST-2 | Per-user daily budget, with graceful degradation. |
| COST-3 | Global daily budget, plus a budget per simulation run. |
| COST-4 | Shared work is computed once per channel and reused by every follower. Because the catalog is fixed, channel ingestion cost is fixed and known. |

### 6.5 Admin

| ID | Requirement |
|---|---|
| ADM-1 | Invites, an account list, job status, and cost. |
| ADM-2 | Retry jobs, disable accounts, change experiment assignments. |
| ADM-3 | Bulk-create and delete synthetic accounts by `run_id`. |
| ADM-4 | **Channel health:** per channel, freshness, items per day per source, failing sources, follower count, and cost. |
| ADM-5 | **Topic-request report:** out-of-catalog requests, grouped and counted, to choose the next channel. |

---

## 7. Agent functional requirements

Requirements run **per user** unless marked *shared*.

### 7.1 The topic catalog (*shared*)

#### 7.1.1 Channel definitions

**🧠 AI: models, research & industry** (anchor; **research domain**)

| | |
|---|---|
| **In scope** | Frontier and open models and labs; research (reasoning, agents, memory, evaluation, interpretability, training methods); AI products and developer tools; AI business (funding, M&A, revenue, talent); AI policy, safety, and regulation; **AI chips and compute supply** (accelerators, chip export controls, cloud GPU capacity) |
| **Subtopics** | `frontier-models` · `open-models` · `research-methods` · `agents-and-tools` · `evals-and-safety` · `products-and-apps` · `business-and-funding` · `policy-and-regulation` · `chips-and-compute` |
| **Sources (draft registry)** | arXiv (cs.AI, cs.CL, cs.LG); lab and company blogs (OpenAI, Anthropic, Google DeepMind, Meta AI, Mistral, Microsoft, NVIDIA, Hugging Face, and others); Hacker News (Algolia API); selected subreddits; tech and business press (for example TechCrunch, The Verge, Reuters tech, Ars Technica, SemiAnalysis public posts); policy sources (EU AI Office, US NIST / OSTP, UK AISI) |
| **Importance signals** | HN points and comments, coverage across registry sources, arXiv attention (for example Hugging Face daily papers), source tier |

**⚡ Energy, Grid & Data Centers**

| | |
|---|---|
| **Framing** | *The clean-energy transition and the power and infrastructure buildout behind AI.* |
| **In scope** | Solar, wind, and storage deployment and economics; nuclear (fission, SMRs, fusion milestones); grid, transmission, and interconnection; power markets and prices; **data center buildout** (hyperscaler capex, siting, power purchase agreements, on-site generation, cooling, water); energy policy and regulation (for example FERC, DOE, tax credits, permitting); climate-tech funding |
| **Out of scope** | Oil and gas markets in general (unless tied to power supply or data centers); consumer EV reviews; general climate science |
| **Subtopics** | `solar-wind` · `storage-batteries` · `nuclear-fusion` · `grid-transmission` · `power-markets` · `data-center-buildout` · `energy-policy` · `climatetech-funding` |
| **Sources (draft registry)** | Trade and specialist press with RSS: Canary Media, Utility Dive, Latitude Media, Heatmap, Data Center Dynamics, Data Center Frontier, Electrek (energy), Carbon Brief, pv magazine, Energy-Storage.news; government and agency releases: EIA, DOE, FERC, IEA; hyperscaler sustainability and infrastructure blogs; HN filtered by keyword |
| **Importance signals** | Coverage across registry sources, source tier, deal size or capacity (GW, GWh, $) extracted from the text. There's no strong community-vote signal like HN here. |

**🤖 Physical AI, Robotics & Simulation**

| | |
|---|---|
| **Framing** | *AI that acts in the physical world, and the simulation and world models used to build it.* |
| **In scope** | Robot foundation models and vision-language-action models; humanoids and general-purpose robots; industrial, warehouse, and logistics automation; autonomous vehicles and robotaxis; drones and field robotics; **simulation, digital twins, and world models** (sim-to-real, synthetic data, physics simulators, generative world models); robotics hardware (actuators, sensors, hands); robotics funding and deployments |
| **Out of scope** | Consumer gadget reviews; pure manufacturing news without automation |
| **Subtopics** | `robot-foundation-models` · `humanoids` · `industrial-logistics` · `autonomous-vehicles` · `drones-field` · `simulation-world-models` · `hardware` · `business-and-deployments` |
| **Sources (draft registry)** | arXiv (cs.RO, plus cs.CV / cs.LG filtered for embodied AI); The Robot Report; IEEE Spectrum Robotics; Robotics 24/7; company and lab blogs (NVIDIA robotics / Isaac, Google DeepMind robotics, Physical Intelligence, Figure, Boston Dynamics, Agility, Waymo, and others); TechCrunch robotics; HN filtered by keyword; r/robotics |
| **Importance signals** | HN points (partial coverage), coverage across registry sources, arXiv attention, source tier |

#### 7.1.2 Boundaries between channels

The channels overlap on purpose. These rules keep the overlap manageable:

| Overlap | Rule |
|---|---|
| **AI chips vs. data centers** | Chip design, supply, and export controls → **AI** (`chips-and-compute`). Physical sites, power, cooling, and grid deals → **Energy** (`data-center-buildout`). Hyperscaler capex announcements usually go in both. |
| **AI research vs. robotics research** | Embodied, robotic, or physical-world work → **Physical AI**. General model methods → **AI**. VLA or world-model papers from major labs often go in both. |
| **World models** | Generative world models used for robots and simulation → **Physical AI**. Generative video models as products → **AI**. |

- **Stories can belong to several channels.** A story is one cluster with multiple channel tags. It's never duplicated, and it appears at most once in a brief.
- **For research purposes,** the AI evaluation corpus is every story tagged **AI** (§9).

#### 7.1.3 Catalog requirements

| ID | Requirement |
|---|---|
| CH-1 | A channel is a **versioned config**: name, framing, scope notes, subtopic list, source registry (provider + feed + tier), refresh schedule, and importance-signal settings. |
| CH-2 | **Channels as config:** adding a channel needs only a new config and its source registry (P9). |
| CH-3 | **Source providers behind one interface:** RSS, arXiv API, HN Algolia, Reddit, government feeds, and an AI search API (for live deep-dive search). A news-API provider can be added later without changing the agent. |
| CH-4 | **Curated registries for every channel.** Sources are known and tiered, so no open-web source policy is needed in the MVP. |
| CH-5 | Item fields: id, channel tags, subtopic tags, source, URL, title, published time, fetched time, text or snippet, engagement signals, provider. |
| CH-6 | **Story clustering** within and across channels. One event is one story, with multiple channel tags. |
| CH-7 | **Snapshot everything** with fetch times. Replay never re-queries live APIs. |
| CH-8 | **No time leakage in replay.** |
| CH-9 | **Topic-request logging:** out-of-catalog requests are stored per user (activity tier) and aggregated anonymously for ADM-5. |
| CH-10 | An optional **wire headlines** feed (major general-news outlets) may be added only as an exploration and "huge news" safety net, and is off in the research config. |

### 7.2 Ranking and selection

| ID | Requirement |
|---|---|
| RANK-1 | *Shared:* **importance within each channel**, from signals available at brief time. It never uses hindsight labels. |
| RANK-2 | Relevance against the user's channel weights and subtopic interests. |
| RANK-3 | Novelty and exploration scores against the user's profile and recently shown stories. |
| RANK-4 | **Slot-based selection:** brief slots are split across followed channels by weight (each followed channel gets at least one slot), then importance + relevance slots, then at least one **exploration slot**, with an MMR redundancy penalty. Importance isn't compared across channels on one scale. |
| RANK-5 | **Exploration pool:** stories from low-weight subtopics, or from **unfollowed catalog channels**, above a quality floor. |
| RANK-6 | All component scores are logged per decision. |
| RANK-7 | Saved-for-later stories carry over (capped). |

### 7.3 Brief generation

| ID | Requirement |
|---|---|
| BRF-1 | Uses only source text, and every sentence carries a citation. |
| BRF-2 | Specific headlines (UX-2). |
| BRF-3 | Follows the user's delivery preferences, within limits. |
| BRF-4 | Opens with answered follow-ups and ends with an open prompt. |

### 7.4 Pre-fetch and deep dive

| ID | Requirement |
|---|---|
| PF-1 | Predict likely dives and build a per-user pre-fetch manifest. |
| PF-2 | Fetched material goes into the shared, content-keyed cache. |
| PF-3 | Per-user pre-fetch budget. |
| PF-4 | Cache first, then live search (corpus search in replay mode), with hits and misses logged. |
| PF-5 | Grounded answers, with a hedge when support is missing. |
| PF-6 | Natural-language references and open questions (UX-3, UX-4). |

### 7.5 Follow-up bookmarks

| ID | Requirement |
|---|---|
| FUP-1 | A question is bookmarked when no supported answer is found within the latency budget, or when the user asks. The agent tells the user. |
| FUP-2 | An open question stores: user, question, context, time, status (`open`, `researching`, `answered`, `unanswerable`, `dismissed`), answer, and citations. |
| FUP-3 | The background research job writes a grounded answer or marks the question unanswerable. |
| FUP-4 | Answers are surfaced at the next session, in the email reminder, and on request, and can be explored in full. |
| FUP-5 | Open questions count as an interest signal and can create a temporary watch. |
| FUP-6 | Open questions can be listed and dismissed, and they expire. |

### 7.6 Memory

| ID | Requirement |
|---|---|
| MEM-1 | **Interest model:** channel weights and subtopic interests (open-ended labels inside a channel's subtopics), each with a weight, confidence, evidence, a last-reinforced time, and a source. |
| MEM-2 | **Positive signals:** explicit statements, dives, follow-ups, "save for later", and bookmarked questions. |
| MEM-3 | **Negative signals are explicit only, or strong and repeated** across several sessions where the user had time. Not diving in once is not disinterest. |
| MEM-4 | **Session-context awareness:** implicit signals are weighted by time available and how the session ended. |
| MEM-5 | **Exposure-normalised learning.** |
| MEM-6 | **Concentration guard:** no single subtopic or channel absorbs more than a set share of weight unless the user has said so. |
| MEM-7 | An occasional "save any of the others for later?" when the user ends early. |
| MEM-8 | Decay with a configurable half-life. |
| MEM-9 | Delivery preferences: length, depth, technicality, quantitative vs. narrative, preferred sources. |
| MEM-10 | Exploration feedback can promote a subtopic, or suggest following a channel ("You've dug into three robotics stories. Want me to add Physical AI?"). |
| MEM-11 | Memory can be viewed and edited by its owner, and is versioned daily. |
| MEM-12 | Static-profile mode for the baseline. |

### 7.7 Voice and clients

| ID | Requirement |
|---|---|
| VOX-1 | **Cascade** (streaming speech-to-text → text agent → streaming text-to-speech). |
| VOX-2 | Voice sits behind a `VoiceFrontEnd` interface, so speech-to-speech can be a separate experiment (§9, F3). |
| VOX-3 | Push-to-talk. |
| VOX-4 | First audio within about 1 s on cache hits. |
| CLI-1 | **iPhone:** a mobile-first web app that works in iPhone Safari, including microphone and audio. Home-screen microphone access is tested early. |
| CLI-2 | One backend API for every client. An Expo app or phone-call channel is a stretch goal. |
| CLI-3 | A CLI client for development and simulation. |

### 7.8 Simulation and evaluation

| ID | Requirement |
|---|---|
| SIM-1 | Simulated user: hidden interests, delivery prefs, drift schedule, behaviour settings. |
| SIM-2 | **A deliberately varied panel** of 10–20 users, one calibrated to Kate, as synthetic accounts. |
| SIM-3 | Separate per-story relevance ratings, used as a judge. |
| SIM-4 | The runner replays the window through the normal per-user jobs. |
| SIM-5 | Reproducible: seeds, cached calls, versioned configs. |
| SIM-6 | Results are cross-checked against real users. |
| SIM-7 | Simulated users ask some questions the corpus can't answer, to exercise follow-up bookmarks. |

**Simulated-panel dimensions (SIM-2):**

| Dimension | Range to cover | Example values |
|---|---|---|
| Role and lens | Several reasons for following AI | Researcher, builder, PM, investor, policy or safety, journalist, student, generalist |
| Interest breadth | Specialist through generalist | 1–2 subtopics vs. 6+ |
| Interest drift | Stable through fast-changing | Fixed; a new subtopic each week; a sudden shift mid-window |
| Delivery preferences | Every option in MEM-9 | Short vs. long; headline vs. deep; technical vs. plain; numbers vs. narrative |
| Engagement style | Amount of signal | Explicit vs. implicit; many vs. few follow-ups |
| Time available per session | Rushed through relaxed | Often 2 minutes vs. 10+ minutes (tests MEM-3–6) |
| Openness to exploration | Curious through dismissive | Often dives into exploration stories vs. always skips |
| Consistency | Clean through noisy | Acts on stated interests vs. sometimes contradicts |

For the **core results**, the panel's hidden interests are defined over the AI channel's subtopics (§9, F1). Users in the optional R6 check get Physical AI interests.

### 7.9 Real-user instrumentation

| ID | Requirement |
|---|---|
| SS-1 | Post-session rating: usefulness, per-story relevance, "learned something new", length fit, follow-up usefulness. |
| SS-2 | Blinded baseline days, drawn per user. |
| SS-3 | Experiment assignment stored per user and day. |
| SS-4 | Only consenting users are analysed. |
| SS-5 | Each rated story is tagged with its channel, so AI-channel results are reported as primary (§9, F5). |

---

## 8. MVP scope cut

| Area | In the MVP | Later or stretch |
|---|---|---|
| Accounts | Invite-only, up to about 50 accounts | Public sign-up, billing |
| Topics | **3 catalog channels** with curated registries; topic-request logging | More channels; open topics via news API + topic resolver |
| Sources | RSS, arXiv, HN, Reddit, government feeds; an AI search API for live search | Event-clustered news API |
| Clients | Mobile-first web app (iPhone Safari) + dev CLI | Expo app or phone channel |
| Voice | Cascade with push-to-talk | Speech-to-speech front-end experiment; barge-in |
| Ranking | Weighted scoring + channel slots + MMR | Contextual bandit, cross-user signals |
| Memory | Channel weights + subtopics, exposure-normalised, decay, concentration guard | Open-vocabulary embeddings, episodic memory |
| Follow-ups | Bookmark, background research, surface next session | Push notifications, ongoing watches |
| Evaluation | All metrics in §10 on the AI channel; optional Physical AI check | Energy channel evaluation |
| Beyond news | None | Personal chief of staff (§4.5) |

---

## 9. Research integrity guardrails

### 9.1 Rules

| ID | Rule |
|---|---|
| RG-1 | **Frozen research config.** All core results come from one versioned config: the **AI channel** and its source registry, the AI subtopic taxonomy, cascade voice (text evaluation), the baseline set, and model versions. Other channels and product features are off or evaluated separately. |
| RG-2 | **Replay only from snapshots** (CH-7). |
| RG-3 | **No hindsight leakage into ranking** (RANK-1). |
| RG-4 | **New features declare their evaluation impact**, and this table is updated in each revision. |
| RG-5 | **Shared code.** The research config and the product run the same code paths. |
| RG-6 | **Scope protection.** Product extensions are cut before research goals. |

### 9.2 Research goals → evaluation

| Goal | Evaluated by | Data |
|---|---|---|
| R1 Replay | Determinism and leakage tests | AI replay corpus |
| R2 Ablations | Every metric across the 5 configs | AI corpus × simulated panel |
| R3 Simulated users | Relevance, interest recovery, coverage, packaging adherence, over-narrowing | AI corpus × simulated panel |
| R4 Corpus metrics | Importance recall@K, citation faithfulness, latency, pre-fetch hit rate and efficiency, follow-up resolution | AI corpus |
| R5 Real users | Ratings, full agent vs. blinded baselines; AI-channel stories primary | Kate (primary), pilot (secondary) |
| R6 Second domain (optional) | Importance recall (coverage label + HN where present) and coverage | Physical AI replay corpus × small panel |

### 9.3 Flags: where product features pull against the research goals

| # | Feature | Tension | Resolution | Status |
|---|---|---|---|---|
| F1 | **Multiple channels** | The HN-based importance label is strongest for AI, weaker for Physical AI, and absent for Energy. | Core evaluation on AI (RG-1). Physical AI as the optional R6 check, using the coverage label plus HN. Energy isn't evaluated for importance. | ✅ Resolved by scoping |
| F2 | **Sourcing** | Live search isn't reproducible. | Every channel has a curated registry; replay only from snapshots. Live search is used only for deep-dive misses and follow-ups, and is replaced by corpus search in replay. | ✅ Resolved |
| F3 | **Audio-to-audio models** | An end-to-end speech-to-speech agent means the evaluation measures a different system, and grounding is weaker. | Cascade for the MVP. Speech-to-speech only as a separate experiment measuring faithfulness drift. | ⚠️ **Don't adopt speech-to-speech as the main path without revisiting** |
| F4 | **Follow-up bookmarks** | Changes what counts as a pre-fetch miss. | Bookmarked turns count as misses; follow-up resolution rate added; faithfulness checks on follow-up answers. | ✅ Resolved |
| F5 | **Kate and pilot users following Energy and Physical AI** | Fewer AI-channel stories get rated in the self-study. | Ratings tagged by channel (SS-5). Make sure the AI channel keeps enough slots in Kate's briefs, for example by keeping her AI weight high or guaranteeing a minimum number of AI slots during the study. | ⚠️ **Watch** |
| F6 | **Memory changes** | They improve alignment with the research. | Time-available dimension in the simulated panel; over-narrowing metric. | ✅ Strengthens |
| F7 | **Cross-user learning** | Breaks the per-user memory evaluation. | Out of the MVP. | ✅ Excluded |
| F8 | **Broader personal agent** | Scope only. | Out of the MVP. | ✅ Excluded |
| F9 | **Exploration from other channels** | In the research config, exploration from non-AI channels would change the AI coverage metric. | In the research config, the exploration pool is limited to the AI channel's lower-weight subtopics. In the product, exploration can use any catalog channel. Report this difference. | ✅ Resolved |
| F10 | **Overall scope** | Solo-project time risk. | RG-6; ◇ milestones; research work on the critical path. Three fixed channels cut most of the open-topic complexity. | ⚠️ **Ongoing risk, now reduced** |

---

## 10. Evaluation and success metrics

### 10.1 Hindsight labels (AI replay corpus; also Physical AI for R6)

- **Primary importance label:** HN score above a calibrated cutoff (AI channel).
- **Secondary importance label:** covered by at least *k* registry sources within 14 days (works for any channel).
- **Topic labels:** the channel's fixed subtopic taxonomy, assigned by an LLM and spot-checked.

### 10.2 Metrics

| Layer | Metric | Definition (precise version in the technical design) |
|---|---|---|
| Corpus | **Importance recall@K** | Share of hindsight-important stories surfaced within *d* days |
| | **Citation faithfulness** | Share of atomic claims supported, across briefs, dives, and follow-up answers |
| | **Latency** | p50 and p95 time to first audio or token, split by hit and miss, under concurrency |
| | **Pre-fetch hit rate** | Share of deep-dive turns answered from the cache; bookmarked turns count as misses |
| | **Pre-fetch efficiency** | Share of pre-fetched stories used, cost per brief, cache reuse |
| | **Follow-up resolution rate** | Share of bookmarked questions answered with support before the next brief |
| Simulated users | **Relevance** | Hidden-profile relevance of the stories shown |
| | **Interest recovery** | Similarity between learned and hidden profiles over time |
| | **Over-narrowing** | Learned profile spread vs. hidden profile spread |
| | **Topic coverage** | Distinct subtopics or entropy over a window |
| | **Packaging adherence** | Delivered format vs. hidden prefs |
| Real users | Usefulness, relevance, "learned something new", length fit, follow-up usefulness | Full agent vs. blinded baselines, AI-channel stories primary |
| Platform | Brief on-time rate, cost per user, cost per channel, channel freshness | |

### 10.3 Baselines

1. Importance-only.
2. Static profile.
3. No exploration.
4. No pre-fetch.

### 10.4 MVP success criteria

**Research (all required):**
- ✅ Beats the static profile on relevance (simulated and real users).
- ✅ At least about 90% of importance-only's importance recall.
- ✅ Topic coverage above the no-exploration baseline.
- ✅ At least 95% supported claims.
- ✅ At least about 70% pre-fetch hit rate; p95 time to first audio under about 1.5 s on hits.
- ✅ Over-narrowing no worse than the static profile for time-constrained users.

**Product and platform:**
- ✅ At least 95% of briefs ready on time.
- ✅ Isolation tests pass.
- ✅ A new user needs no code changes; a new channel needs config only.
- ✅ At least about 60% follow-up resolution rate (provisional).
- ✅ The full voice loop works on an iPhone.
- ✅ Each channel delivers enough fresh stories per day (target set per channel during M1).
- ✅ Cost per user within budget.

---

## 11. Milestones (proposed)

Dates are placeholders until they're lined up with the course deadlines. Milestones marked ◇ can slip without hurting the research goals (RG-6).

| Milestone | Contents | Exit criterion |
|---|---|---|
| **M0: Docs** | PRD, design explorations, technical design | Approved |
| **M1: Foundations + AI channel** | Database (users, channels, per-user tables), data-access layer, job queue, snapshot store, provider interface, **AI channel config + registry**, replay corpus, clustering, hindsight labels | Two isolated test accounts; AI corpus loaded; label cutoff calibrated |
| **M2: Text agent v0** | Ranking + brief + deep dive + basic memory + follow-up bookmarks (CLI); per-user and channel jobs; replay and live | One replayed day for two synthetic accounts; one live day for Kate |
| **M3: Web app + self-study** | Login, onboarding, mobile web chat + voice (on iPhone), memory, channels and open-questions pages, ratings, blinded baselines, admin CLI | Kate uses it daily on her iPhone; isolation tests pass |
| **M3◇: Energy + Physical AI channels** | Configs + registries for both; cross-channel clustering and boundaries; channel slots; topic-request logging | Both channels fresh daily; mixed briefs work |
| **M4: Full agent** | Exploration, exposure-normalised memory, delivery prefs, pre-fetch, ablation flags, budgets | All 5 configs run on replay |
| **M4◇: Pilot** | Invite pilot users | Pilot users get daily briefs |
| **M5: Simulation and evaluation** | Simulated panel, runner, metrics, judges, human audit | Full results table vs. baselines |
| **M5◇: Stretch** | R6 Physical AI check; speech-to-speech experiment; Expo app or phone channel | Optional |
| **M6: Write-up** | Analysis and report | Final report |

Snapshotting all three channels should start **as early as possible**, ideally in M1 even before their agent support is finished. That way the replay windows for R6 exist when they're needed.

---

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Overall scope for a solo project | Slips the research | RG-6; ◇ milestones; fixed catalog |
| Cross-user data leak | Privacy breach | DATA-1–3; isolation tests |
| Personal data of other people | Ethics | Consent, export and delete, no audio, pseudonymised analysis. Check whether a human-subjects review is needed before the pilot. |
| **Thin or stale sources for Energy or Physical AI** | Weak briefs on those channels | Channel health monitoring (ADM-4); a freshness target per channel; add sources to the registry as needed |
| **Weak importance signals for Energy** (no HN equivalent) | Lower-quality "big news" picks | Coverage across sources, source tier, deal-size extraction; Energy isn't part of the importance evaluation |
| **Overlapping channels cause duplicate or confusing stories** | Repetitive briefs | Cross-channel clustering, one story with multiple tags, MMR penalty, boundary rules (§7.1.2) |
| **Users want topics outside the catalog** | Unmet demand | Polite handling, live search for one-off questions, request logging to choose the next channel |
| Per-user cost | Budget | Shared channels and cache, budgets, cheap models for routine steps |
| Job failures | Missed briefs | Retries, on-demand fallback, alerts |
| iPhone web microphone limits | Poor commute experience | Test early; stretch Expo or phone channel |
| Historical data gaps | Weak replay | Choose sources by archive availability; snapshot from day one |
| LLM knowledge leakage | Inflated quality judgements | Window after model cutoff; ranking from corpus signals only |
| Hindsight label noise | Biased importance metric | Two labels; report both |
| Unreliable simulated users | Misleading evaluation | Calibration to Kate; real-user cross-check |
| Unsupported claims | Trust | Retrieval-only generation, claim checks, bookmark instead of guessing |
| Voice latency under load | Poor UX | Pre-fetch, streaming, a fast turn model, a load test |

---

## 13. Open questions (decisions for Kate)

1. **Channel definitions (§7.1):** are the scopes, subtopics, and boundaries right? In particular:
   - Should **autonomous vehicles** sit in Physical AI, or be left out?
   - Should **nuclear and fusion** sit in Energy?
   - Is **chips and compute** right in AI rather than Energy & Data Centers?
2. **Source registries:** any must-have sources for each channel (newsletters, podcasts with transcripts, specific blogs)?
3. **Replay window:** August–September 2026? It must sit after the models' training cutoff.
4. **LLM provider and models:** OpenRouter as the default? A separate budget for pilot users?
5. **Live search provider:** Exa or Tavily?
6. **Hosting:** a single VM with Postgres, or a managed platform (Fly.io, Render, Supabase)?
7. **Auth:** email link or Google?
8. **Pilot size**, and whether a human-subjects review is needed.
9. **iPhone:** is Safari web acceptable for the start of the self-study?
10. **Brief size default:** 6 stories, about 3 minutes, at least 1 exploration slot?
11. **Course deadlines**, to put dates on §11.
12. **Memory transparency:** should the agent say *why* it thinks you care about something?
13. **Self-study channel mix (F5):** a guaranteed minimum number of AI slots in your briefs during the study?

---

## Appendix A: Glossary

- **Account:** a `real` or `synthetic` user record.
- **Topic channel:** a curated, config-defined stream of news for one topic area, shared by all its followers. The MVP has three.
- **Subtopic:** a fixed category within a channel, used for interests, coverage, and evaluation.
- **Source registry:** the curated, versioned, tiered list of sources for a channel.
- **Source provider:** an adapter for one way of getting news (RSS, arXiv, HN, a search API).
- **Item / Story:** a single document / a cluster of items about one event, which can carry several channel tags.
- **Brief:** the ordered stories delivered to one user in a session.
- **Open question / follow-up bookmark:** a user question saved for background research.
- **Topic request:** a logged request for a topic outside the catalog.
- **Research config:** the frozen configuration all core results come from (RG-1).
- **Run (`run_id`):** a namespace isolating a replay or simulation.
- **Hindsight label:** an importance label computed with future information, used only for evaluation.

## Appendix B: Related work, as it informs requirements

| Work | Informs |
|---|---|
| Li et al. 2010, MIND | Relevance and exploration trade-off |
| Carbonell & Goldstein 1998 (MMR), Ziegler et al. 2005 | Redundancy and diversity |
| Nguyen et al. 2014, Kaminskas & Bridge 2017 | Coverage, novelty, over-narrowing metrics |
| MemGPT, Mem0, LaMP | Per-user memory; we evaluate *selection* |
| Park et al. 2023, Zhang et al. 2024, Wang et al. 2023, Zhu et al. 2024 | Simulated users and their limitations |
| Gao et al. 2023, Liu et al. 2023 | Citation faithfulness |
