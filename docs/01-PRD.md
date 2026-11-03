# PRD: Conversational Briefing Agent (MVP)

**Status:** Draft v0.1, ready for review
**Owner:** Kate Wang
**Source:** `proposal.pdf` ("Expanding the Aperture", CS329Z Fall 2026)
**Next docs:** `02-technical-design.md` (after this PRD is approved), then the build

---

## 1. Summary

A chief-of-staff style agent that gives a short spoken morning brief on AI news, asks what the user wants to dig into, holds a back-and-forth on those items with a cited source for every claim, and then updates a memory of what the user cares about and how they like information packaged.

Each brief balances three things:

1. **Importance:** stories that matter in the world.
2. **Relevance:** stories that matter to this user.
3. **Exploration:** stories that widen the user's view rather than reinforce a filter bubble.

To keep voice turns fast, the agent pre-fetches source material for likely deep dives before the brief starts and searches live only when the cache can't answer.

The MVP has to do two jobs:

- **A usable daily product** that Kate uses for a 4–5 week self-study.
- **A reproducible research system** that can be replayed over a frozen news corpus, driven by simulated users, and compared against baselines.

Every product decision below is checked against both jobs.

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

| Persona | Need | MVP priority |
|---|---|---|
| **Kate (primary)**: a Stanford student with limited time who wants to stay current on AI | A 3–5 minute brief on the commute, deep dives on demand, and an agent that remembers what she cares about without over-indexing on it | **Primary.** Self-study user, and the model for one simulated user. |
| Researcher | Paper-heavy, technical, wants methods and numbers | Simulated users only |
| Operator or PM | Products, launches, pricing, competitors | Simulated users only |
| Investor | Funding, business and policy, market moves | Simulated users only |
| Policy follower | Regulation and safety | Simulated users only |

For the MVP, non-Kate personas exist only as **simulated users** with hidden, drifting interest profiles. Supporting multiple real users is out of scope.

---

## 4. Goals and non-goals

### 4.1 Product goals (MVP)

- **G1. Daily brief.** Deliver a spoken or text brief of about 5–7 stories in roughly 2–4 minutes, each with a one- or two-sentence summary and a source.
- **G2. Conversational deep dive.** After the brief, the user can say "tell me more about #3", "what did people on HN think?", or "skip, next". The agent answers from cited sources.
- **G3. Memory from conversation.** The agent infers interests and delivery preferences from what the user says and does, lets stale interests fade, and keeps room for new ones.
- **G4. Exploration.** Every brief reserves explicit slots for topics outside the user's current profile, and labels them as such ("Something outside your usual: …").
- **G5. Low latency.** Follow-ups feel conversational: most are answered from a pre-fetched cache, and live search is a fallback.
- **G6. Groundedness.** Every factual claim in a brief or deep dive is tied to a cited source item.

### 4.2 Research goals (MVP)

- **R1. Replay mode.** The whole loop runs deterministically over a frozen corpus (for example August–September 2026) on a simulated clock, with no leakage from future items.
- **R2. Ablations.** The full agent and the four baselines run from config flags: importance-only, static profile, no exploration, and no pre-fetch.
- **R3. Simulated users.** 10–20 LLM-driven users with hidden, drifting interest and delivery profiles, one of them calibrated to Kate.
- **R4. Evaluation harness.** Computes every metric in §8 from text transcripts and logs.
- **R5. Self-study instrumentation.** Daily ratings, plus blinded baseline days.

### 4.3 Non-goals (MVP)

- Domains other than AI news, though the design shouldn't rule them out.
- Multiple real users, accounts, or authentication beyond a single user.
- Custom speech models. Speech-to-text and text-to-speech use off-the-shelf components, and **all quantitative evaluation runs on text**.
- Native mobile apps or push notifications.
- Full-duplex voice with barge-in. Push-to-talk is enough.
- Training or fine-tuning models. The MVP uses prompting, retrieval, and heuristic or learned-lite scoring.
- Paywalled sources and full-text redistribution. The agent stores and quotes snippets and links out.

---

## 5. User experience

### 5.1 The daily loop

```
 ┌──────────────┐   overnight / before brief
 │ 1. PRE-COMPUTE│  ingest → cluster into stories → score (importance, relevance, novelty)
 │              │  → select brief (with exploration slots) → pre-fetch deep-dive material
 └──────┬───────┘
        ▼
 ┌──────────────┐   user starts session
 │ 2. BRIEF     │  short spoken brief, numbered stories, ends with "What do you want to dig into?"
 └──────┬───────┘
        ▼
 ┌──────────────┐   multi-turn
 │ 3. DEEP DIVE │  answers from the pre-fetch cache with citations; live search only on a miss
 └──────┬───────┘
        ▼
 ┌──────────────┐   end of session
 │ 4. UPDATE    │  explicit and implicit interest signals, delivery preferences,
 │    MEMORY    │  decay of quiet interests; the user can review or edit memory
 └──────────────┘
```

### 5.2 Example session (illustrative)

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

Memory written from that session:
- interest: `agent memory / long context` ↑ (explicit dive)
- delivery: `prefers quantitative detail` ↑
- interest: `EU regulation` → threshold raised to "major only"

### 5.3 Interaction requirements

| ID | Requirement |
|---|---|
| UX-1 | The brief opens with the story count and expected length, and stories are numbered so the user can refer to them by number. |
| UX-2 | Each story gets a one-line *why you're hearing this* tag where useful: "big news", "for you", or "outside your usual". This keeps ranking transparent and makes exploration explicit. |
| UX-3 | Supported intents: dive into an item (by number or name), "more like this", "skip" or "less of this", "go deeper" or "simpler", "who's reacting?", "what's the source?", "repeat", "end". |
| UX-4 | The user can ask "what do you know about me?" and edit or delete memory entries in conversation. |
| UX-5 | Citations are spoken lightly ("per the Anthropic blog") and shown in full in the text transcript. |
| UX-6 | If the agent can't support an answer from a source, it says so rather than guessing. |
| UX-7 | The session works in **text mode** (CLI or web chat) and in **voice mode** (push-to-talk), with the same agent logic underneath. |

---

## 6. Functional requirements

### 6.1 Sources and ingestion

| ID | Requirement |
|---|---|
| ING-1 | A fixed, configurable source list covering four categories: **research** (arXiv cs.AI, cs.CL, cs.LG, cs.CV, plus paper aggregators), **products and labs** (official blogs of major labs and companies), **community** (Hacker News, selected subreddits), and **business and policy** (tech and business press, government and regulator releases). |
| ING-2 | Each item stores: id, source, category, URL, title, published timestamp, author, text or snippet, and engagement signals where available (such as HN points and comment count). |
| ING-3 | Two modes: **live** (daily pull for the self-study) and **replay** (a frozen snapshot loaded from disk). |
| ING-4 | **Story clustering:** items about the same event are grouped into one *story* (for example a lab blog post, its HN thread, and press coverage). The story is the unit that gets ranked. |
| ING-5 | **No time leakage:** in replay mode, every component sees only items published at or before the simulated "now". |

### 6.2 Ranking and selection

| ID | Requirement |
|---|---|
| RANK-1 | **Importance score** per story, from signals available at brief time: source authority, early engagement, cross-source coverage so far, and an optional LLM judgement. It must *not* use the hindsight labels used for evaluation. |
| RANK-2 | **Relevance score** per story against the user's current interest profile. |
| RANK-3 | **Novelty and exploration score:** distance from the user's profile and from recently shown topics. |
| RANK-4 | **Slot-based selection:** a brief of size K (default 6) is filled as roughly *importance slots + relevance slots + at least one reserved exploration slot*, with an MMR-style redundancy penalty. Slot counts are configurable. |
| RANK-5 | Exploration candidates must still clear a minimum importance or quality floor. Exploration means "worth knowing, outside your bubble", not random. |
| RANK-6 | Each selection decision is logged with its component scores, for evaluation and for UX-2. |

### 6.3 Brief generation

| ID | Requirement |
|---|---|
| BRF-1 | Generates a script from the selected stories, using only text from their source items. Every sentence maps to at least one citation. |
| BRF-2 | Length, depth, and technicality follow the delivery preferences in memory, within limits (for example 3–8 stories, 1–4 minutes). |
| BRF-3 | Ends with an open prompt to dig in. |

### 6.4 Pre-fetch and deep dive

| ID | Requirement |
|---|---|
| PF-1 | Before the brief, the agent predicts which stories the user is likely to dive into (from relevance and history) and pre-fetches material for them: full text or extended snippets of all items in the story cluster, related prior stories, and community reaction. |
| PF-2 | Pre-fetch has a configurable budget (number of stories × depth), so cost can be traded against hit rate. |
| PF-3 | Deep-dive answers come from the cache first. A **cache miss** triggers live search: corpus search in replay mode, and web or source search in live mode. Every hit and miss is logged. |
| PF-4 | Deep-dive answers follow the same grounding rules as BRF-1, and refuse or hedge when support is missing (UX-6). |

### 6.5 Memory

| ID | Requirement |
|---|---|
| MEM-1 | **Interest model:** a set of topics, each with a weight, evidence (session and turn references), a last-reinforced time, and a source (explicit or implicit). |
| MEM-2 | **Signals.** Explicit: "more of X", "less of Y", or a stated interest. Implicit: dove in, number of follow-ups, skipped, cut short. Explicit signals weigh more. |
| MEM-3 | **Decay:** interest weights decay with a configurable half-life when not reinforced, so quiet interests fade. |
| MEM-4 | **Delivery preferences:** brief length, depth, technicality, quantitative versus narrative, and preferred sources, each with a value and confidence. |
| MEM-5 | **Exploration feedback:** when an exploration story gets engagement, it can be promoted into the interest model. |
| MEM-6 | Memory is readable and editable by the user (UX-4), and is persisted between sessions as inspectable files or a database. |
| MEM-7 | A **static-profile** mode freezes memory after initialisation, for the baseline. |

### 6.6 Voice

| ID | Requirement |
|---|---|
| VOX-1 | Off-the-shelf streaming speech-to-text and text-to-speech. |
| VOX-2 | Push-to-talk. The brief audio is streamed or pre-rendered. |
| VOX-3 | Latency target: first audio within about 1 second of end of user speech on cache hits (see §8). |
| VOX-4 | The voice layer is a thin adapter over the text agent. Turning voice off must not change agent behaviour. |

### 6.7 Simulation and evaluation infrastructure

| ID | Requirement |
|---|---|
| SIM-1 | **Simulated user:** an LLM agent with a *hidden* interest profile (topics and weights), hidden delivery preferences, and a drift schedule (interests appear and fade over the window). It reacts to briefs: picks dives, asks follow-ups, skips, and states preferences, sometimes noisily or implicitly. |
| SIM-2 | 10–20 simulated users across the personas in §3, including one calibrated to Kate from her stated interests and early self-study logs. |
| SIM-3 | Simulated users can also give per-story relevance ratings, used as a judge. These ratings are kept separate from their in-conversation behaviour. |
| SIM-4 | **Runner:** for each (user, agent config) pair, replays every day of the window and stores transcripts, selections, memory snapshots, and timings. |
| SIM-5 | Runs are reproducible: fixed seeds, cached LLM calls where possible, and versioned configs. |
| SIM-6 | Because LLM user simulators are known to be unreliable (Zhu et al. 2024), simulated results are cross-checked against the self-study. |

### 6.8 Self-study instrumentation

| ID | Requirement |
|---|---|
| SS-1 | After each session, a 30-second rating: overall usefulness (1–5), per-story relevance, "learned something new" (yes/no), and length about right / too long / too short. |
| SS-2 | **Blinded baseline days:** on randomly chosen days, a baseline config is served in place of the full agent, without telling the user. |
| SS-3 | All sessions are logged, the same as in simulation. |

---

## 7. MVP scope cut

| Area | In the MVP | Later or stretch |
|---|---|---|
| Sources | about 10–20 sources across 4 categories, chosen for **historical availability** (replay) and RSS or API access (live) | Broader coverage, paywalled sources |
| Interface | Text CLI for development and simulation; a **minimal web page with push-to-talk** for the self-study (works on a phone browser) | Native app, wake word, barge-in |
| Ranking | Weighted scoring + slots + MMR | Contextual bandit (Li et al. 2010), learned weights |
| Memory | Structured topic profile + delivery prefs + decay | Embedding-based open-vocabulary interests, episodic memory |
| Pre-fetch | Top-N predicted stories, fixed depth | Learned dive predictor, adaptive budget |
| Evaluation | All metrics in §8, LLM judge + human spot-checks | Inter-annotator studies, larger user panel |

---

## 8. Evaluation and success metrics

Evaluation covers **selection** (did the agent pick the right stories, given what's important and what it knows about the user?) and **packaging** (did it deliver them the way this user wants?).

### 8.1 Hindsight labels (replay corpus only)

- **Primary importance label:** the story has a Hacker News score above a calibrated cutoff. Calibration chooses a cutoff such that about *N* stories per day are important, with *N* to be set during the data phase.
- **Secondary importance label:** the story is covered by at least *k* distinct sources on our list within 14 days of first appearance.
- **Topic labels:** each story gets one or more topics from a fixed taxonomy of about 20–40 topics, assigned by an LLM and spot-checked. These are used for coverage and for the hidden profiles.

### 8.2 Metrics

| Layer | Metric | Definition (precise version in the technical design) |
|---|---|---|
| Corpus, user-independent | **Importance recall@K** | Share of hindsight-important stories surfaced in a brief within *d* days of appearing |
| | **Citation faithfulness** | Share of atomic claims in briefs and deep dives that the cited source supports (LLM judge + human audit sample) |
| | **Latency** | p50 and p95 time to first token or audio for a follow-up turn, split by cache hit and miss |
| | **Pre-fetch hit rate** | Share of deep-dive turns answered entirely from the cache |
| | **Pre-fetch efficiency** | Share of pre-fetched stories actually used, and tokens or cost per brief |
| Simulated users | **Relevance** | Mean hidden-profile relevance of the stories shown |
| | **Interest recovery** | Similarity between the learned and hidden profiles over time, including tracking of drift |
| | **Topic coverage** | Number of distinct topics (or topic entropy) shown over a window |
| | **Packaging adherence** | Match between delivered length, depth, and technicality and the hidden delivery prefs |
| Self-study | Daily usefulness, per-story relevance, "learned something new", and length fit | Full agent vs. blinded baseline days |

### 8.3 Baselines

1. **Importance-only:** no personalisation.
2. **Static profile:** profile set on day 1 and never updated.
3. **No exploration:** exploration slots removed.
4. **No pre-fetch:** every follow-up uses live search.

### 8.4 MVP success criteria

The MVP works if the full agent:

- ✅ **beats the static profile on relevance** for simulated users, and on self-study ratings;
- ✅ **stays close to importance-only on importance recall**: a provisional target of at least 90% of the baseline's recall;
- ✅ **keeps topic coverage above the no-exploration baseline**;
- ✅ **reaches at least 95% supported claims**;
- ✅ **answers most follow-ups from the pre-fetch cache** (provisional target: at least 70% hit rate) and meets the latency target on hits (provisional: p95 time to first audio under about 1.5 s).

These targets are provisional, as the proposal says, and get refined once the baselines run.

---

## 9. Milestones (proposed)

Dates are placeholders until they're lined up with the CS329Z deliverable dates. The **self-study needs 4–5 weeks**, so it is the critical path: a usable live agent has to exist early.

| Milestone | Contents | Exit criterion |
|---|---|---|
| **M0: Docs** | This PRD, then the technical design | Both approved |
| **M1: Corpus** | Source list; replay corpus built for the window; clustering; hindsight labels; topic taxonomy | Corpus loaded, label cutoff calibrated, spot-check passed |
| **M2: Text agent v0** | Ranking + brief + deep dive (with live search, no pre-fetch) + basic memory, in a text CLI; replay and live modes | One end-to-end replayed day; one live day |
| **M3: Self-study start** | Minimal voice web page; rating form; blinded baseline scheduler | Kate uses it daily, with logging on |
| **M4: Full agent** | Exploration slots, decay, delivery prefs, pre-fetch, ablation flags | All 5 configs run on replay |
| **M5: Simulation and evaluation** | Simulated users, runner, metrics, LLM judges, human audit | Full results table vs. baselines |
| **M6: Write-up** | Analysis, failure cases, report | Final report |

M3 depends only on M2, so the self-study can start before M4. Agent improvements roll in during the study, and each session is logged with the version it used.

---

## 10. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Historical data gaps:** RSS feeds don't go back in time, and press archives are patchy | Weak replay corpus | Pick sources by historical availability: the arXiv API, the HN Algolia API (with historical points), lab blog archives, and GDELT or press archives. Snapshot live mode daily from day one so later windows are covered. |
| **LLM knowledge leakage:** the model may already know events in the window | Inflated quality or importance judgements | Prefer a window after the model's knowledge cutoff where possible; ground all ranking signals in corpus data; never use the LLM as the importance oracle in evaluation. |
| **Hindsight label noise:** HN skews toward developers | Biased importance metric | Use the secondary multi-source coverage label too; report both. |
| **Unreliable simulated users** | Misleading evaluation | Calibrate one simulated user to Kate; cross-check against the self-study; add noise and implicit signals to the simulator; report limitations. |
| **Hallucinated or unsupported claims** | Safety and trust | Generation is restricted to retrieved text, claims are checked at generation time, refusal is preferred to guessing, and a faithfulness metric is required. |
| **Voice latency** | Poor UX | Pre-fetch, streaming text-to-speech, short first sentences, a fast model for turn handling. |
| **LLM cost:** 20 users × about 60 days × 5 configs | Budget overrun | Cache calls, use small models for the simulator and judge, use course credits (OpenRouter), and add a budget cap in config. |
| **Solo-project scope** | Slip | Strict MVP cut (§7); start the self-study early; treat stretch items as optional. |

---

## 11. Open questions (decisions for Kate)

1. **Replay window:** August–September 2026 as in the proposal? It must sit after the chosen LLMs' training cutoff to limit leakage.
2. **LLM provider and models:** OpenRouter (course credits) as the default gateway? Which model tiers for the agent, the simulator, and the judge?
3. **Self-study interface:** is a phone-browser web page with push-to-talk acceptable for commute use, or is an audio-file brief plus later chat good enough?
4. **Source list:** any must-have or must-exclude sources (for example specific newsletters, X/Twitter, which is hard to get legally)?
5. **Brief size:** default of 6 stories, about 3 minutes, with at least 1 exploration slot. Is that right for you?
6. **Course deadlines:** milestone, final report, and poster dates, so §9 can get real dates.
7. **Memory transparency:** should the brief ever say *why* the agent thinks you care about something ("you dug into X twice last week")?

---

## Appendix A: Glossary

- **Item:** a single source document (a paper, post, article, or HN thread).
- **Story:** a cluster of items about the same event; the unit of ranking.
- **Brief:** the ordered set of K stories delivered at the start of a session.
- **Exploration slot:** a brief position reserved for a story outside the user's current profile.
- **Hindsight label:** an importance label computed with future information. Used only for evaluation, never for ranking.
- **Replay mode:** running the agent over a frozen corpus on a simulated clock.

## Appendix B: Related work, as it informs requirements

| Work | Informs |
|---|---|
| Li et al. 2010 (contextual bandit), MIND | Relevance and exploration trade-off; later bandit upgrade |
| Carbonell & Goldstein 1998 (MMR), Ziegler et al. 2005 | RANK-4 redundancy and diversity |
| Nguyen et al. 2014, Kaminskas & Bridge 2017 | Coverage, novelty, and serendipity metrics |
| MemGPT, Mem0, LaMP | Memory design; we evaluate *selection*, not just recall of facts |
| Park et al. 2023, Zhang et al. 2024, Wang et al. 2023, Zhu et al. 2024 | Simulated users, and their limitations |
| Gao et al. 2023, Liu et al. 2023 | Citation faithfulness as a required metric |
