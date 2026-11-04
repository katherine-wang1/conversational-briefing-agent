# PRD: Conversational Briefing Agent (MVP)

**Status:** Draft v0.4, ready for review
**Owner:** Kate Wang
**Source:** `proposal.pdf` ("Expanding the Aperture", CS329Z Fall 2026)
**Companion doc:** `01a-design-explorations.md` (corpus, sources, voice, mobile: options and recommendations)
**Next docs:** `02-technical-design.md` (after this PRD is approved), then the build

**Changelog**
- **v0.4:**
  - Users can follow **any news topic**, not just AI; the shared corpus becomes shared **topic channels** (§7.1).
  - Added **follow-up bookmarks** for questions the agent can't answer live (§7.5).
  - Memory no longer **over-curates** from short sessions (§7.6).
  - Added **iPhone access** (§7.7).
  - The example session uses specific names and natural-language references.
  - Added **research integrity guardrails**, with flags where product features pull against the research goals (§9).
  - Added a bookmarked **future vision** of a broader personal agent (§4.5).
- **v0.3:** Target users described broadly; persona variety moved to the simulated-user panel.
- **v0.2:** Multi-user from day one: accounts, per-user data and processes, cost limits, admin, pilot.
- **v0.1:** Initial draft from the proposal.

---

## 1. Summary

A chief-of-staff style agent that gives each user a short spoken morning brief on **the news they care about**, asks what they want to dig into, holds a back-and-forth with a cited source for every claim, and updates **that user's** memory of their interests and preferred format. When it can't answer a question on the spot, it **bookmarks it, researches it in the background, and follows up**.

Each brief balances three things:

1. **Importance:** stories that matter in the world.
2. **Relevance:** stories that matter to this user.
3. **Exploration:** stories that widen the user's view rather than reinforce a filter bubble.

To keep voice turns fast, the agent pre-fetches material for each user's likely deep dives before the brief.

The MVP is a **multi-user service**:
- Each user has an account, isolated stored data, and their own scheduled daily processes.
- News is gathered once per **topic channel** (for example "AI research", "energy storage") and shared by everyone who follows that topic, so cost grows with the number of distinct topics, not the number of users.

The MVP has to do three jobs:

- **A usable daily product** for anyone on the go who needs to stay current in a fast-moving field, on any topic. In the MVP that's a small invite-only group: Kate first, then pilot users from a variety of roles.
- **A reproducible research system.** The **core research evaluation stays on AI news**, as in the proposal. It replays a frozen corpus with simulated users and baselines, under a frozen research configuration (§9).
- **A foundation that scales** to more users and topics without re-architecting.

**Rule for every extension:** product features may go beyond the research scope, but they must not weaken or silently change the research evaluation. §9 lists every place where they could, and how each one is handled.

---

## 2. Problem

- The world moves faster than one person can follow, and the useful signal is spread across many sources.
- Access to information isn't the bottleneck. **The time and attention to filter it** is.
- Typing and scrolling don't fit the moments people actually have, such as a commute. Voice does.
- Existing options fall short:
  - **Static summaries and ranked feeds** don't learn from conversation and don't take follow-up questions.
  - **Engagement-optimised recommenders** narrow what you see over time.
  - **Commercial briefings** (ChatGPT Pulse, Google Daily Listen, Huxe) publish no evaluation of importance coverage, filter-bubble effects, or faithfulness.

## 3. Target users

**Anyone who wants to stay current in a fast-moving field and is on the go.** They have little time, the news is scattered, and the moments they do have (a commute, a walk, the gym) suit listening and talking better than reading and scrolling. Users can follow any area: AI, energy, biotech, markets, policy, sports, or several at once.

The product doesn't assume a fixed set of user types. The point of the agent is to learn each person's interests and preferred format from conversation.

**Real users in the MVP:**
- **Kate:** the first user, the primary self-study participant, and the admin.
- **Pilot users:** about 5–15 people from a variety of roles, recruited by Kate after her own use is stable (§12).

**Simulated users** are where variety is designed in on purpose (SIM-2). They run as `synthetic` accounts on the same infrastructure.

---

## 4. Goals and non-goals

### 4.1 Product goals (MVP)

- **G1. Daily brief.** A spoken or text brief of about 5–7 stories in roughly 2–4 minutes. Each story is **specific** (it names the company, person, product, paper, or bill) and carries a source.
- **G2. Conversational deep dive.** The user describes what they want in their own words ("the battery thing", "what's the catch with that model?"). They don't need to remember list numbers.
- **G3. Memory from conversation.** The agent infers interests and delivery preferences from what the user says and does, **without over-reading a short session**. It lets stale interests fade and keeps room for new ones.
- **G4. Exploration.** Every brief reserves explicit, labelled slots for stories outside the user's current profile.
- **G5. Low latency.** Most follow-ups are answered from a pre-fetched cache.
- **G6. Groundedness.** Every factual claim is tied to a cited source item.
- **G7. Follow-up bookmarks.** If the agent can't answer well live, it says so, saves the question, researches it in the background, and brings back a grounded answer in a later conversation.
- **G8. Any topic.** Users choose topics at sign-up and can add, drop, or reweight them by voice at any time ("start following energy storage", "less crypto").

### 4.2 Platform goals (MVP)

- **P1. Accounts.** Invite-based sign-up, login, and a short onboarding: topics, delivery preferences, timezone, and brief time.
- **P2. Per-user data isolation.** All personal data is scoped to one user and can't be read across users.
- **P3. Per-user processes.** Each user's daily pre-compute is its own scheduled job. One user's failure doesn't affect anyone else.
- **P4. Shared topic channels.** News gathering, clustering, and importance scoring run once per channel and are shared by all of the channel's followers.
- **P5. Cost limits** per user, per channel, and overall.
- **P6. Data rights.** Users can view, export, and delete their data, and consent to research use separately.
- **P7. Admin tools.**
- **P8. iPhone access.** The full experience, including voice, works from an iPhone.

### 4.3 Research goals (MVP), all held

- **R1. Replay mode.** Deterministic replay of a frozen **AI-news** corpus (for example August–September 2026) on a simulated clock, with no future leakage.
- **R2. Ablations.** Full agent vs. importance-only, static profile, no exploration, and no pre-fetch.
- **R3. Simulated users.** 10–20 varied users with hidden, drifting profiles, one calibrated to Kate.
- **R4. Evaluation harness.** Every metric in §10.
- **R5. Real-user study.** Kate's 4–5 week self-study with daily ratings and blinded baseline days; pilot data as a secondary sample.

§9 maps each research goal to its evaluation and to the product features that could affect it.

### 4.4 Non-goals (MVP)

- **Public sign-up, billing, teams, or sharing** between users.
- Large scale. The MVP targets up to about 50 accounts.
- Custom speech models, or an **end-to-end speech-to-speech agent** as the main path (see §9, flag F3).
- A native Swift app.
- Full-duplex voice with barge-in. Push-to-talk is enough.
- Training or fine-tuning models.
- Paywalled content and full-text redistribution.
- **Cross-user learning** ("people like you also liked…"). It would muddy the per-user memory evaluation (§9, flag F7).
- Everything in §4.5.

### 4.5 Future vision (bookmarked, NOT in the MVP)

The longer-term idea is a **broader personal chief of staff** that goes beyond news: triaging email, managing tasks and calendar, and preparing for meetings, all in the same conversational, memory-driven style. The MVP doesn't build any of this.

Two cheap choices keep that path open:
- Memory and the user model are general: interests, preferences, and open questions, not news-specific fields.
- Source providers and agent tools sit behind interfaces, so non-news tools could be added later.

---

## 5. User experience

### 5.1 Account lifecycle

```
 invite ──► sign up / log in ──► onboarding (≈2 min) ──► daily loop ──► manage memory / topics / data
                                    ├─ topics in own words ("AI chips, energy storage, F1")
                                    ├─ delivery prefs (length, depth, technicality)
                                    ├─ timezone + brief time
                                    └─ research consent (opt-in)
```

Onboarding topics are matched to existing topic channels, or new channels are created (§7.1). They seed the interest model with low confidence, so conversation overrides them quickly.

### 5.2 The daily loop

```
 SHARED, PER TOPIC CHANNEL (scheduled)        PER USER (scheduled before the user's brief time)
 ┌──────────────────────────────┐            ┌─────────────────────────────────────────────────┐
 │ fetch → dedupe → cluster →    │  stories  │ 1. PRE-COMPUTE: candidates from the user's       │
 │ importance within channel     │─────────► │    channels + exploration pool → relevance,      │
 └──────────────────────────────┘            │    novelty → slots + MMR → brief script →         │
                                             │    pre-fetch dives → attach answered follow-ups   │
                                             └──────────────┬──────────────────────────────────┘
                                                            ▼  user opens a session
                                             ┌─────────────────────────────────────────────────┐
                                             │ 2. BRIEF (+ "you asked yesterday…")  3. DEEP DIVE │
                                             │    cache first → live search → bookmark if still  │
                                             │    unanswered                                     │
                                             └──────────────┬──────────────────────────────────┘
                                                            ▼  session ends
                                             ┌─────────────────────────────────────────────────┐
                                             │ 4. UPDATE MEMORY (this user)  5. RESEARCH         │
                                             │    BOOKMARKED QUESTIONS (background job)          │
                                             └─────────────────────────────────────────────────┘
```

If the brief isn't ready when the user opens a session, it's computed on demand.

### 5.3 Example session (illustrative; companies and products are fictional)

> **Agent:** Morning. Six stories, about three minutes. Also, you asked yesterday why Halden's chip launch slipped. I found the answer, and it's at the end.
> 1) **Northwind AI released Atlas-3**, an open-weights reasoning model. It scores within two points of the top closed models on the main math and coding benchmarks. *[Northwind blog; HN discussion]*
> 2) **The EU AI Office published draft guidance** on what general-purpose model providers must disclose about training data. *[Reuters]*
> 3) **A Stanford and CMU paper finds long-context retrieval accuracy falls by about a third past 200k tokens**, across five models. This one's because you've been following agent memory. *[arXiv]*
> 4) **Voltaic Storage signed a 2 GWh grid battery deal with Texas utility Lonestar Power**, the largest sodium-ion order so far. *[Bloomberg; Canary Media]*
> 5) …
> 6) **Outside your usual:** **two robotics foundation-model startups, Kinema and Graspworks, raised a combined $600M this week.** *[TechCrunch]*
> What do you want to dig into?
>
> **User:** The long-context paper. Which models held up best?
>
> **Agent:** *(answers from the pre-fetched paper with figures and a citation)* …
>
> **User:** Does that hold for Atlas-3 too?
>
> **Agent:** The paper didn't test Atlas-3, and I couldn't find anyone who has yet. I'll look into it and tell you tomorrow.
> *(bookmarks the question)*
>
> **User:** Great. I've got to go, but the battery one sounded interesting too.
>
> **Agent:** I'll save the battery story and bring you more on it tomorrow. Here's the Halden answer quickly: …

Memory written to **this user's** store:
- interest: `long-context / agent memory` ↑ (explicit dive + follow-up)
- interest: `grid storage` ↑ (explicitly saved, even though there was no time to dive)
- the other stories they didn't dive into: **no negative signal**. The session was short and the user said they were out of time.
- open question: "Does the long-context degradation hold for Atlas-3?" → background research

### 5.4 Interaction requirements

| ID | Requirement |
|---|---|
| UX-1 | The brief opens with the story count and expected length, plus any answered follow-ups. |
| UX-2 | **Specific headlines:** every story names the concrete actors and objects (company, product, person, paper, bill, number) in its first sentence. Generic phrasing such as "a frontier lab" isn't allowed when the source names the actor. |
| UX-3 | **Natural references:** the user can refer to a story by description, keyword, or position ("the battery one", "that EU thing", "the last one"). The agent resolves the reference and asks briefly if it's ambiguous. |
| UX-4 | The user can also ask open questions that aren't tied to a story ("what's going on with Nvidia this week?"). The agent answers from the corpus or live search, or bookmarks the question. |
| UX-5 | Each story gets a *why you're hearing this* tag where useful: "big news", "for you", or "outside your usual". |
| UX-6 | Supported intents include: dive, ask a question, "more like this", "less of this", "save for later", "follow [topic]" / "stop following [topic]", "go deeper" / "simpler", "who's reacting?", "what's the source?", "what do you know about me?", "what questions do I have open?", "I only have N minutes", and "end". |
| UX-7 | Citations are spoken lightly and shown in full in the transcript. |
| UX-8 | If the agent can't support an answer from a source, it says so and offers to bookmark the question. It never guesses. |
| UX-9 | Text and voice modes share the same agent logic. |
| UX-10 | Users can see their past briefs, transcripts, open questions, topics, and memory, and edit them. |
| UX-11 | Optional email reminder at brief time, which also mentions answered follow-ups. |

---

## 6. Accounts, data, and processes

### 6.1 Accounts and authentication

| ID | Requirement |
|---|---|
| ACC-1 | Account fields: id, email, name, timezone, brief time, status, type (`real` / `synthetic`), role (`user` / `admin`), research consent, created date. |
| ACC-2 | Invite-only sign-up from a single-use link. |
| ACC-3 | Passwordless email link or Google login. No stored passwords. |
| ACC-4 | Every request and session is bound to exactly one user id. Synthetic accounts use service credentials only. |
| ACC-5 | Admins see metadata, job status, and cost for every account. They can read real users' content only with consent, and every such access is logged. |

### 6.2 Data tiers and isolation

| Tier | Contents | Scope |
|---|---|---|
| **Shared** | Topic channels (definitions and ingested items), stories, importance scores, labels, the content cache, the source policy, and the research source registry | Global, read-only to user processes |
| **Per-user state** | Profile, channel subscriptions and weights, interest model, delivery prefs, open questions, experiment assignments, budgets | One user |
| **Per-user activity** | Briefs, selections, transcripts, pre-fetch manifests, follow-up answers, ratings, timings, costs | One user |

| ID | Requirement |
|---|---|
| DATA-1 | Every per-user record carries a `user_id`, and all access goes through a user-scoped data-access layer. |
| DATA-2 | Agent tools (memory, follow-ups, history) are built per session with a fixed user id. The LLM can't choose whose data it reads. |
| DATA-3 | Shared caches are keyed by content and contain no user data. **Channel definitions contain no user identifiers.** A channel created from one user's phrasing must not reveal who created it or who follows it. |
| DATA-4 | Replay and simulation runs live in separate `run_id` namespaces. |
| DATA-5 | No raw audio is stored by default. Sessions and accounts can be deleted; deleting an account purges per-user data within 7 days. |
| DATA-6 | Users can export their data as JSON. |
| DATA-7 | Encryption in transit and at rest. Secrets are never stored in the database or repo. |
| DATA-8 | Research analysis uses only consenting users, with pseudonymous ids. |

### 6.3 Processes and scheduling

| ID | Requirement |
|---|---|
| PROC-1 | **Channel ingestion jobs:** one per active channel, on a schedule matched to how fast that topic moves. They're idempotent, and every ingested item is written to the **timestamped snapshot store**. |
| PROC-2 | **Per-user pre-compute job:** once per local day, about 60 minutes before the user's brief time, after their channels have refreshed. |
| PROC-3 | Jobs are isolated, run on a queue with retries, and are tagged with `user_id` or `channel_id`, date, config version, and `run_id`. |
| PROC-4 | **On-demand fallback** if the brief isn't ready. |
| PROC-5 | Each live session is isolated, and sessions run concurrently. |
| PROC-6 | Memory updates are serialised per user. |
| PROC-7 | **Follow-up research job:** per user, triggered when a question is bookmarked. It has a larger time and cost budget than a live turn, and it must finish before the user's next brief. |
| PROC-8 | **Capacity:** at least 10 concurrent sessions; pre-compute for 50 accounts within 1 hour; up to about 100 active channels. |
| PROC-9 | The simulation runner drives the same jobs on a simulated clock. |
| PROC-10 | Structured logs (user or channel, job, duration, tokens, cost, cache hits and misses, errors), filterable by user, channel, and run. |

### 6.4 Cost control

| ID | Requirement |
|---|---|
| COST-1 | Every external call records its cost against a user, a channel, or the shared pipeline. |
| COST-2 | Per-user daily budget, with graceful degradation as it runs out. |
| COST-3 | **Per-channel budget.** Channels with no followers for N days are paused. Users have a cap on the number of channels they create. |
| COST-4 | Global daily budget, plus a budget per simulation run. |
| COST-5 | Shared work is computed once per channel and reused by every follower. |

### 6.5 Admin

| ID | Requirement |
|---|---|
| ADM-1 | Invites, an account list, job status, and cost. |
| ADM-2 | Retry jobs, disable accounts, and change experiment assignments. |
| ADM-3 | Bulk-create and delete synthetic accounts by `run_id`. |
| ADM-4 | A list of channels with follower count, freshness, cost, and source mix. Admins can merge duplicate channels and pause channels. |

---

## 7. Agent functional requirements

Requirements run **per user** unless marked *shared*. The trade-offs behind §7.1 are in `01a-design-explorations.md` §1–2.

### 7.1 Topic channels, sources, and ingestion (*shared*)

| ID | Requirement |
|---|---|
| CH-1 | A **topic channel** has a name, a description, fetch settings (search or news-API queries and filters, optional curated feeds), a source policy, a refresh schedule, and a parent channel (channels can be hierarchical). |
| CH-2 | **Topic resolution:** when a user names a topic (at onboarding or by voice), it's matched to an existing channel (by embedding similarity plus an LLM check) or a new channel is created. Duplicate channels can be merged by an admin. |
| CH-3 | A **global "top stories" channel** feeds every user's exploration pool and catches major cross-domain events. |
| CH-4 | **Source providers behind one interface:** free structured feeds (RSS, arXiv, HN Algolia, GDELT), an event-clustered news API, and an AI search API. Providers can be swapped per channel without changing agent code. |
| CH-5 | **Source policy instead of a fixed list** for general channels: credibility tiers by domain, a blocklist for spam and content farms, and per-user source preferences from memory. |
| CH-6 | **Research source registry:** the AI research channels use a **curated, versioned** source list, so the replay corpus and hindsight labels are well-defined (§9). |
| CH-7 | Each item stores: id, channel(s), source, URL, title, published time, fetched time, text or snippet, engagement signals, and the provider it came from. |
| CH-8 | **Story clustering** within and across channels (one event followed by two channels is one story). |
| CH-9 | **Snapshot everything:** every ingested item is stored with its fetch time, so any past window can be replayed. Replay never re-queries live APIs. |
| CH-10 | **No time leakage in replay:** components see only items fetched and published at or before the simulated "now". |

### 7.2 Ranking and selection

| ID | Requirement |
|---|---|
| RANK-1 | *Shared:* an **importance score within each channel**, from signals available at brief time (coverage across outlets, outlet tier, community engagement where available). It never uses hindsight labels. |
| RANK-2 | Relevance per story against the user's interest model and channel weights. |
| RANK-3 | Novelty and exploration scores against the user's profile and recently shown stories. |
| RANK-4 | **Slot-based selection:** slots are allocated across the user's channels by weight, then importance + relevance slots, then at least one exploration slot, with an MMR redundancy penalty. Importance isn't compared across very different channels on one scale. |
| RANK-5 | Exploration candidates must clear a quality floor. |
| RANK-6 | All component scores are logged per decision. |
| RANK-7 | Stories the user explicitly saved for later and didn't get to can carry over to the next brief (capped). |

### 7.3 Brief generation

| ID | Requirement |
|---|---|
| BRF-1 | Uses only source text, and every sentence carries a citation. |
| BRF-2 | Specific headlines (UX-2). |
| BRF-3 | Follows the user's delivery preferences, within limits. |
| BRF-4 | Opens with answered follow-ups (briefly, with an offer to expand) and ends with an open prompt. |

### 7.4 Pre-fetch and deep dive

| ID | Requirement |
|---|---|
| PF-1 | Predict likely dives and build a per-user pre-fetch manifest. |
| PF-2 | Fetched material goes into the shared, content-keyed cache. |
| PF-3 | Per-user pre-fetch budget. |
| PF-4 | Answer from the cache first, then live search (corpus search in replay mode), and log hits and misses. |
| PF-5 | Grounded answers, with a hedge when support is missing. |
| PF-6 | Resolve natural-language references to stories (UX-3), and handle open questions (UX-4). |

### 7.5 Follow-up bookmarks

| ID | Requirement |
|---|---|
| FUP-1 | A question is bookmarked when (a) cache and live search can't give a supported answer within the latency budget, or (b) the user asks ("look into that and get back to me"). The agent tells the user it's been saved. |
| FUP-2 | An **open question** stores: user id, question text, context (story and turn), time asked, status (`open`, `researching`, `answered`, `unanswerable`, `dismissed`), answer, and citations. |
| FUP-3 | The **background research job** (PROC-7) does deeper search with more time and budget, and writes a grounded answer, or marks the question unanswerable with what it found. |
| FUP-4 | Answered follow-ups are surfaced at the start of the next session (BRF-4), included in the email reminder, and available on demand ("any updates on my questions?"). The user can then hold a full deep-dive conversation on the answer. |
| FUP-5 | Open questions are also an **interest signal** for memory, and can create a temporary watch: if the topic develops later, the agent mentions it. |
| FUP-6 | The user can list and dismiss open questions. Open questions expire after a configurable period. |

### 7.6 Memory

| ID | Requirement |
|---|---|
| MEM-1 | **Interest model:** topics and channel weights, each with a weight, confidence, evidence, a last-reinforced time, and a source (onboarding, explicit, implicit). |
| MEM-2 | **Positive signals:** explicit statements ("more of X", "follow X"), dives, follow-up questions, "save for later", and bookmarked questions. |
| MEM-3 | **Negative signals are explicit only, or strong and repeated:** "less of this", "skip", "stop following", or consistently skipping a topic across **several** sessions where the user had time. **Not diving into a story in one session is not evidence of disinterest.** |
| MEM-4 | **Session-context awareness:** the agent tracks time available ("I only have 5 minutes"), session length, and how the session ended (natural end vs. cut short). Implicit signals are weighted by opportunity: a skipped story in a short or cut-short session counts for almost nothing. |
| MEM-5 | **Exposure-normalised learning:** interest estimates are based on engagement *relative to how often the topic was shown and when the user had time*, not on raw counts. This stops one dive from dominating. |
| MEM-6 | **Concentration guard:** the interest model keeps a minimum spread. It may not let one topic absorb more than a set share of weight unless the user has said so explicitly. |
| MEM-7 | When the user ends a session early, the agent may ask one short question ("Want me to save any of the others for later?"). It doesn't ask every time. |
| MEM-8 | **Decay** with a configurable half-life. |
| MEM-9 | **Delivery preferences:** length, depth, technicality, quantitative vs. narrative, preferred sources. |
| MEM-10 | **Exploration feedback:** engagement with an exploration story can promote its topic into the interest model. |
| MEM-11 | Memory can be viewed and edited by its owner, and is versioned daily for evaluation. |
| MEM-12 | **Static-profile** mode for the baseline. |

### 7.7 Voice and clients

| ID | Requirement |
|---|---|
| VOX-1 | **MVP voice architecture: cascade** (streaming speech-to-text → text agent → streaming text-to-speech), so the evaluated text is exactly what the user hears. The rationale is in `01a` §3. |
| VOX-2 | The voice layer sits behind a `VoiceFrontEnd` interface, so a speech-to-speech front end can be tried later as a **separate experiment** (§9, flag F3). |
| VOX-3 | Push-to-talk. |
| VOX-4 | Latency target: first audio within about 1 s of end of speech on cache hits. |
| CLI-1 | **iPhone:** the MVP client is a **mobile-first web app that works in iPhone Safari**, including microphone and audio playback. Microphone access from a home-screen web app is tested early. |
| CLI-2 | Every client uses one backend API. A **native client (Expo / React Native) or a phone-call channel** is a stretch goal, added if the self-study shows the web client is painful on a commute (for example with the screen locked). See `01a` §4. |
| CLI-3 | A CLI client for development and simulation. |

### 7.8 Simulation and evaluation

| ID | Requirement |
|---|---|
| SIM-1 | **Simulated user:** an LLM agent with a hidden interest profile, hidden delivery preferences, a drift schedule, and behaviour settings. |
| SIM-2 | **A deliberately varied panel** of 10–20 users, one calibrated to Kate, provisioned as synthetic accounts. The panel varies along every dimension below. |
| SIM-3 | Simulated users also give separate per-story relevance ratings, used as a judge. |
| SIM-4 | The runner replays the window through the normal per-user jobs. |
| SIM-5 | Runs are reproducible: seeds, cached calls, versioned configs. |
| SIM-6 | Results are cross-checked against real users. |
| SIM-7 | Simulated users can ask questions the corpus can't answer, so follow-up bookmarks get exercised and measured. |

**Simulated-panel dimensions (SIM-2):**

| Dimension | Range to cover | Example values |
|---|---|---|
| Role and lens | Several reasons for following AI | Researcher, builder, PM, investor, policy or safety, journalist, student, generalist |
| Interest breadth | Specialist through generalist | 1–2 deep topics vs. 8+ shallow ones |
| Interest drift | Stable through fast-changing | Fixed; a new interest each week; a sudden shift mid-window |
| Delivery preferences | Every option in MEM-9 | Short vs. long; headline vs. deep; technical vs. plain; numbers vs. narrative |
| Engagement style | Amount of signal given | Explicit and talkative vs. terse and implicit; many vs. few follow-ups |
| **Time available per session** | Rushed through relaxed | Often only 2 minutes (dives into at most one story despite broad interest) vs. usually 10+ minutes. **Tests that memory doesn't over-curate (MEM-3–6).** |
| Openness to exploration | Curious through dismissive | Often dives into exploration stories vs. always skips them |
| Consistency | Clean through noisy | Acts on stated interests vs. sometimes contradicts them |

The panel stays within the AI domain for the core results (§9, flag F1).

### 7.9 Real-user instrumentation

| ID | Requirement |
|---|---|
| SS-1 | A 30-second post-session rating: usefulness (1–5), per-story relevance, "learned something new", length fit, and **follow-up answer usefulness**. |
| SS-2 | **Blinded baseline days**, drawn per user. |
| SS-3 | Experiment assignment stored per user and day. |
| SS-4 | Only consenting users are analysed. |
| SS-5 | Each rated story is tagged with its channel and domain, so AI-domain results can be separated from the rest (§9, flag F5). |

---

## 8. MVP scope cut

| Area | In the MVP | Later or stretch |
|---|---|---|
| Accounts | Invite-only, up to about 50 accounts | Public sign-up, billing |
| Topics | Any topic via channels; curated AI research channels | Per-user custom feeds, very niche or local news |
| Sources | Free feeds + one event-clustered news API + one AI search API | More providers, paywalled sources |
| Clients | Mobile-first web app (iPhone Safari) + dev CLI | Expo iPhone app or phone-call channel |
| Voice | Cascade with push-to-talk | Speech-to-speech front end experiment; barge-in |
| Ranking | Weighted scoring + channel slots + MMR | Contextual bandit, cross-user signals |
| Memory | Structured, exposure-normalised, with decay and a concentration guard | Open-vocabulary embeddings, episodic memory |
| Follow-ups | Bookmark, background research, surface next session | Push notifications, ongoing "watches" |
| Evaluation | All metrics in §10 on the AI research config | Extension metrics on a second domain |
| Beyond news | None | Personal chief of staff (§4.5) |

---

## 9. Research integrity guardrails

The research goals are the project's core deliverable. The product can grow beyond them, but these rules keep the research valid.

### 9.1 Rules

| ID | Rule |
|---|---|
| RG-1 | **Frozen research config.** All core results come from one versioned config: the AI research channels, the curated source registry (CH-6), the fixed AI topic taxonomy, cascade voice (text evaluation), the baseline set, and model versions. Product features outside this config are **switched off** or **evaluated separately**. |
| RG-2 | **Replay only from snapshots.** Never re-query live APIs for past dates (CH-9). |
| RG-3 | **No hindsight leakage into ranking** (RANK-1). |
| RG-4 | **New features declare their evaluation impact.** Each new feature states: off in the research config / on, with metric X added / on, with these metrics affected. This table is updated in each PRD and technical-design revision. |
| RG-5 | **Shared code.** The research config and the product run the *same* agent code paths. No research-only fork. |
| RG-6 | **Scope protection.** If time gets tight, product extensions are cut before any research goal. |

### 9.2 Research goals → evaluation

| Goal | Evaluated by | Data |
|---|---|---|
| R1 Replay | Determinism check: the same run twice gives identical selections; leakage tests | AI replay corpus |
| R2 Ablations | Every metric, compared across the 5 configs | AI replay corpus × simulated panel |
| R3 Simulated users | Relevance, interest recovery, coverage, packaging adherence, over-narrowing | AI replay corpus × simulated panel |
| R4 Corpus metrics | Importance recall@K, citation faithfulness, latency, pre-fetch hit rate and efficiency, follow-up resolution | AI replay corpus |
| R5 Real users | Ratings, full agent vs. blinded baseline days | Kate (primary), pilot (secondary) |

### 9.3 Flags: where product features pull against the research goals

| # | Feature | Tension with research | Resolution | Status |
|---|---|---|---|---|
| **F1** | **Any-topic news** (G8) | The HN-based importance label only works for tech and AI. The coverage metric needs a fixed taxonomy. Simulated users would need cross-domain corpora. | Core evaluation stays on the AI domain (RG-1). Cross-source coverage importance on a second domain is an optional extension. | ✅ Resolved by scoping |
| **F2** | **No fixed source list / search-API sourcing** (CH-5) | Search APIs aren't reproducible over time and can leak future content. "Covered by k sources on our list" needs a list. | A curated registry for the research channels (CH-6); replay only from snapshots (RG-2). | ✅ Resolved |
| **F3** | **Audio-to-audio models** | An end-to-end speech-to-speech agent means the text evaluation measures a different system from the one users hear, and weakens grounding (G6). | Cascade for the MVP and the research config. Speech-to-speech only as a separate front-end experiment that measures faithfulness drift. | ⚠️ **Don't adopt speech-to-speech as the main path without revisiting this** |
| **F4** | **Follow-up bookmarks** (G7) | Changes what counts as a pre-fetch miss, and creates answers that are outside the latency metric. | Bookmarked turns count as **misses** in the pre-fetch hit rate. Add a *follow-up resolution rate* and run faithfulness checks on follow-up answers. | ✅ Resolved |
| **F5** | **Pilot users and Kate following non-AI topics** | Real-user ratings would mix domains and drift from the AI research config, so pooled results are harder to interpret. | Tag ratings by domain (SS-5). Report AI-domain stories as the primary real-user result and everything else separately. Pilot results are secondary. | ⚠️ **Watch:** if Kate follows many non-AI topics, fewer AI stories get rated in the self-study |
| **F6** | **Memory changes** (MEM-3–6) | These change agent behaviour, but they *improve* alignment with the research (interest recovery). | Add the *time available* dimension to the simulated panel and an over-narrowing metric. | ✅ Strengthens the research |
| **F7** | **Cross-user learning** (future) | Mixes signals across users and breaks the per-user memory evaluation. | Out of the MVP (§4.4). | ✅ Excluded |
| **F8** | **Broader personal agent** (§4.5) | Scope only. | Out of the MVP. | ✅ Excluded |
| **F9** | **Exploration from other domains** | For a user in the AI research config, exploration slots could pull from non-AI channels, which changes the coverage metric. | In the research config, the exploration pool is limited to AI channels plus the AI-relevant parts of "top stories". | ✅ Resolved |
| **F10** | **Overall scope** (multi-user + any-topic + follow-ups + mobile) | Solo-project time risk to the self-study timeline and the evaluation. | RG-6 (cut product extensions first); milestones put research-critical work on the critical path (§11). | ⚠️ **Ongoing risk** |

---

## 10. Evaluation and success metrics

All per-user metrics are reported per user and averaged. Core numbers come from the research config (RG-1).

### 10.1 Hindsight labels (AI replay corpus)

- **Primary importance label:** HN score above a calibrated cutoff (about *N* important stories a day).
- **Secondary importance label:** covered by at least *k* registry sources within 14 days.
- **Topic labels:** from a fixed AI taxonomy of about 20–40 topics, assigned by an LLM and spot-checked.

### 10.2 Metrics

| Layer | Metric | Definition (precise version in the technical design) |
|---|---|---|
| Corpus | **Importance recall@K** | Share of hindsight-important stories surfaced within *d* days |
| | **Citation faithfulness** | Share of atomic claims the cited source supports, across briefs, dives, and follow-up answers |
| | **Latency** | p50 and p95 time to first audio or token, split by hit and miss, under concurrency |
| | **Pre-fetch hit rate** | Share of deep-dive turns answered fully from the cache. **Bookmarked turns count as misses.** |
| | **Pre-fetch efficiency** | Share of pre-fetched stories used, cost per brief, shared-cache reuse |
| | **Follow-up resolution rate** | Share of bookmarked questions answered with a supported answer before the next brief |
| Simulated users | **Relevance** | Hidden-profile relevance of the stories shown |
| | **Interest recovery** | Similarity between learned and hidden profiles over time |
| | **Over-narrowing** | Learned profile's spread compared with the hidden profile's spread, especially for time-constrained users |
| | **Topic coverage** | Distinct topics or entropy over a window |
| | **Packaging adherence** | Delivered format compared with the hidden prefs |
| Real users | Usefulness, relevance, "learned something new", length fit, follow-up usefulness | Full agent vs. blinded baselines, AI-domain stories as the primary result |
| Platform | Brief on-time rate, cost per user, cost per channel | |

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
- ✅ Over-narrowing no worse than the static profile for time-constrained simulated users.

**Product and platform:**
- ✅ At least 95% of briefs ready on time.
- ✅ Isolation test suite passes.
- ✅ A new user or new topic needs no code changes.
- ✅ At least about 60% follow-up resolution rate (provisional).
- ✅ The full voice loop works on an iPhone.
- ✅ Daily cost per user and per channel within budget.

---

## 11. Milestones (proposed)

Dates are placeholders until they're lined up with the course deadlines. Research-critical work is on the critical path. Product extensions marked ◇ can slip without hurting the research goals (RG-6).

| Milestone | Contents | Exit criterion |
|---|---|---|
| **M0: Docs** | PRD, design explorations, technical design | Approved |
| **M1: Foundations + AI corpus** | Database with users, channels, per-user tables; data-access layer; job queue; snapshot store; source provider interface; AI research channels + registry; replay corpus; clustering; hindsight labels; taxonomy | Two isolated test accounts; AI corpus loaded; label cutoff calibrated |
| **M2: Text agent v0** | Ranking + brief + deep dive + basic memory + follow-up bookmarks (CLI); per-user and channel jobs; replay and live | One replayed day for two synthetic accounts; one live day for Kate |
| **M3: Web app + self-study** | Login, onboarding, mobile web chat + voice (tested on iPhone), memory, topics and open-questions pages, ratings, blinded baselines, admin CLI | Kate uses it daily on her iPhone; isolation tests pass |
| **M4: Full agent** | Exploration, exposure-normalised memory, delivery prefs, pre-fetch, ablation flags, budgets | All 5 configs run on replay |
| **M4◇: Open topics + pilot** | Topic resolver, general channels via the news API, source policy; invite pilot users | Pilot users get briefs on their own topics |
| **M5: Simulation and evaluation** | Simulated panel, runner, metrics, judges, human audit | Full results table vs. baselines |
| **M5◇: Stretch** | Speech-to-speech front-end experiment; Expo app or phone channel | Optional |
| **M6: Write-up** | Analysis and report | Final report |

---

## 12. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Overall scope for a solo project | Slips the research | RG-6; ◇ milestones; research work on the critical path |
| Cross-user data leak | Privacy breach | DATA-1–3; isolation test suite |
| Personal data of other people | Ethics | Consent, export and delete, no audio, pseudonymised analysis. Check whether a human-subjects review is needed before the pilot. |
| **Too many channels or duplicates** from open topics | Cost and quality | Topic resolver, admin merge, per-user channel cap, pausing inactive channels |
| **Source quality and misinformation** in general topics | Trust | Source policy tiers, blocklist, a minimum number of sources for "big news" claims, grounding rules |
| **News API limits or shutdown** (for example a free service changing terms) | Lost coverage | Provider interface; fallback to free feeds; snapshots keep history |
| Per-user and per-channel cost | Budget | Shared channels and cache, budgets, cheap models for routine steps |
| Job failures | Missed briefs | Retries, on-demand fallback, alerts |
| iPhone web microphone limits (home-screen, locked screen) | Poor commute experience | Test early; stretch Expo or phone channel |
| Historical data gaps (AI corpus) | Weak replay | Choose sources by archive availability; snapshot from day one |
| LLM knowledge leakage | Inflated quality judgements | Window after model cutoff; ranking from corpus signals only |
| Hindsight label noise | Biased importance metric | Two labels; report both |
| Unreliable simulated users | Misleading evaluation | Calibration to Kate; real-user cross-check |
| Unsupported claims | Trust | Retrieval-only generation, claim checks, bookmark instead of guessing |
| Voice latency under load | Poor UX | Pre-fetch, streaming, a fast turn model, a load test |

---

## 13. Open questions (decisions for Kate)

1. **Replay window:** August–September 2026? It must sit after the models' training cutoff.
2. **LLM provider and models:** OpenRouter as the default? A separate budget for pilot users?
3. **News providers:** start with NewsCatcher's NewsMCP (free, new) and add Event Registry (paid) only if needed? Exa or Tavily for live search? See `01a` §2.
4. **Hosting:** a single VM with Postgres, or a managed platform (Fly.io, Render, Supabase)?
5. **Auth:** email link or Google?
6. **Pilot size**, and whether pilot data goes into the report (and so whether a human-subjects review is needed).
7. **iPhone:** is Safari web acceptable for the start of the self-study, with an Expo app only if it proves painful?
8. **Brief size default:** 6 stories, about 3 minutes, at least 1 exploration slot?
9. **Course deadlines**, to put dates on §11.
10. **Memory transparency:** should the agent say *why* it thinks you care about something?
11. **Your own topics:** will you follow non-AI topics during the self-study? That affects flag F5 (fewer AI stories rated).

---

## Appendix A: Glossary

- **Account:** a `real` or `synthetic` user record.
- **Topic channel:** a shared stream of news for one topic, defined by a description and fetch settings and followed by any number of users.
- **Source provider:** an adapter for one way of getting news (RSS, a news API, a search API).
- **Source policy:** credibility tiers and blocklists that govern general channels.
- **Research source registry:** the curated, versioned source list for the AI research channels.
- **Item / Story:** a single document / a cluster of items about one event.
- **Brief:** the ordered stories delivered to one user in a session.
- **Open question / follow-up bookmark:** a user question saved for background research.
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
