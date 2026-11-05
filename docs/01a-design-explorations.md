# Design explorations: corpus, sources, voice, and mobile

**Status:** Draft v0.3, companion to PRD v0.6

> **Decision (PRD v0.6):** the MVP is **agent-native** (§6 below). There's no ingestion pipeline and no user-facing channels. A shared **newsroom** agent judges importance from headline signals, a per-user **chief-of-staff** agent personalises, and a fast **voice** agent talks. The three topic areas become the newsroom's **beats**. A frozen archive exists only for evaluation. Sections 1–2 are kept as the **alternatives we considered** (shared corpus, topic channels, news APIs); they remain useful if per-user cost ever pushes back toward shared ingestion. Sections 3–4 (voice, iPhone) still apply as written.
**Purpose:** Work through the biggest open design questions before the technical design: how news gets into the system, whether a shared corpus still makes sense once users can follow any topic, whether to use audio-to-audio models, and how users reach the agent from an iPhone. Each section ends with a recommendation and its **effect on the research goals**.

> Pricing and model details below come from web searches in November 2026. Sources often disagree, so treat the numbers as rough and check vendor pricing pages before committing.

---

## 1. The core question: a shared corpus when users want different things

### 1.1 The problem

v0.3 assumed one **shared corpus** built from a fixed source list of AI news, ingested once and ranked separately for each user. That works because every user draws from the same pool.

If users can ask for *any* topic ("energy storage", "Formula 1", "Brazilian politics", "AI chips"), a single fixed corpus can't cover them. The opposite extreme is that every user's daily job searches the web for their own interests. That covers anything, but cost grows with *users × topics*, the same story gets fetched and summarised many times, and nothing is reproducible.

### 1.2 The key idea: separate *finding stories* from *choosing stories for a person*

- **Finding stories** (what happened, which articles cover it, how big it is) doesn't depend on who's asking. It depends only on the **topic**.
- **Choosing stories** (what goes in *this* user's brief, and how it's packaged) is personal.

So the shared unit shouldn't be "the corpus". It should be a **topic channel**: one ingestion stream per topic, shared by every user who follows that topic. Cost then grows with the number of **distinct topics**, not the number of users. Interests overlap a lot (many people follow "AI" or "climate"), so distinct topics grow much more slowly than users.

### 1.3 Options compared

| | **A. One fixed shared corpus** (v0.3) | **B. Per-user retrieval** | **C. Topic channels** (recommended) |
|---|---|---|---|
| How it works | Fixed source list, ingested once, ranked per user | Each user's daily job searches for each of their interests | Each topic is a channel with its own queries and feeds. Channels are created when a user first names a topic and shared by everyone who follows it. |
| Any topic? | ❌ Only what the source list covers | ✅ | ✅ |
| Cost grows with | Fixed | Users × topics × queries | Distinct topics |
| Duplicate work | None | High: the same story is fetched and summarised per user | Low: once per channel, reused across users |
| Importance signal | Strong (cross-source coverage, HN, curated) | Weak (search-engine ranking only) | Medium–strong: within-channel coverage counts, outlet authority, community signals where available |
| Reproducible / replayable | ✅ | ❌ unless every result is snapshotted | ✅ if every channel's ingested items are snapshotted (we do this) |
| Exploration (G4) | Within the corpus | Has to fetch outside interests anyway | Pull from adjacent channels or a global "top stories" channel |
| Complexity | Low | Low to build, high to run well | Medium |

### 1.4 How topic channels would work

```
 user says "keep me posted on energy storage"         (onboarding or in conversation)
          │
          ▼
 TOPIC RESOLVER ── does an existing channel match? ("battery tech" ≈ "energy storage")
          │ yes → subscribe the user, with a weight        │ no → create a channel:
          │                                                │   name, description, search queries,
          │                                                │   optional curated feeds, source policy
          ▼                                                ▼
 CHANNEL INGESTION (shared, per channel, scheduled)
   news APIs / search APIs / feeds → dedupe → cluster into stories → importance within channel
          │
          ▼
 PER-USER JOB: candidate stories = union of the user's channels + exploration pool
   → relevance (weighted by subscription and memory) → slots + MMR → brief
```

- **Channel = topic definition, not a source list.** A channel holds a topic description plus how to fetch it: search queries, news-API filters, and optional curated feeds for high-value topics. AI channels get rich curated feeds (arXiv, lab blogs, HN). Long-tail channels may use search queries only.
- **Normalisation keeps the channel count down.** New topic phrases are matched to existing channels by embedding similarity and an LLM check before a new channel is created. Channels can be hierarchical (for example `energy` → `energy storage`).
- **Low-subscriber channels are still cheap.** A channel with one follower costs about what per-user retrieval would for that topic, but the next person who follows it gets it for free. Channels with no followers for N days are paused.
- **Importance across very different topics isn't directly comparable.** "Big news in Formula 1" and "big news in AI" are on different scales. Importance is computed *within* a channel and normalised. The brief then allocates slots across channels by the user's weights, rather than ranking everything on one global scale.
- **The global channel.** A "top stories" channel (major news across domains) supplies exploration candidates and catches events big enough that everyone should hear about them.

### 1.5 Rough cost sense

Assume a channel is refreshed 2–4 times a day with 2–3 search or news-API calls each, which is about 4–12 calls per channel per day.

- **Search and news APIs:** at about $5–8 per 1,000 searches ([Tavily](https://www.tavily.com/blog/7-best-exa-alternatives-for-ai-agents-in-2026), [Firecrawl](https://www.firecrawl.dev/blog/best-search-tools-for-agents), [Keirolabs](https://keirolabs.cloud/blogs/guide/best-ai-search-apis-for-agents-aug-2026)), that's roughly **$0.03–0.10 per channel per day**. 100 channels would be about $3–10 a day.
- **LLM clustering and summarising:** clustering and summarising each story happens once per channel, not per user. This is likely the larger cost.
- **Free sources first:** many channels can be served by free sources (§2), with paid search used only to fill gaps and for live deep-dive search.

With per-user retrieval (option B), the same numbers multiply by the number of users who share a topic.

### 1.6 Do we need a fixed, configurable source list?

**Not for the product. Yes for the research domain.**

- **Product:** sources are discovered per channel through news and search APIs, governed by a **source policy** rather than a fixed list:
  - a credibility tier per domain (wire services and major outlets, specialist and trade press, blogs, forums);
  - a blocklist for spam, content farms, and AI-generated content mills;
  - per-user source preferences in memory ("more from X, less from Y").
- **Research domain (AI):** keeps a **curated, versioned source registry**. The replay corpus and the hindsight labels (HN score, multi-source coverage "on our list") are only well-defined over a fixed list. Without it, results aren't reproducible and the importance metric changes meaning from run to run.

### 1.7 Recommendation

Use **topic channels (option C)**. Treat the AI research domain as a set of curated channels with a frozen source registry. Snapshot everything any channel ingests, with timestamps, so any past window can later be replayed.

### 1.8 Effect on research goals

| Research goal | Effect | Resolution |
|---|---|---|
| R1 Replay | Search APIs return *today's* results and can't be reliably time-travelled. Re-querying them later leaks the future. | Replay **only** from timestamped snapshots, never from live re-queries. Every live ingestion is written to the snapshot store from day one. |
| R4 Importance recall | The primary hindsight label (HN score) only means something for tech and AI. | The **core evaluation stays on the AI domain**, as in the proposal. Cross-source coverage, which works for any topic, can be an *extension* metric on a second domain if time allows. |
| R4 Topic coverage | Needs a fixed taxonomy. An open-ended set of channels isn't one. | Coverage is measured over the **fixed AI evaluation taxonomy**. Channels are a product concept, and the evaluation taxonomy is a research concept. |
| R3 Simulated users | Would need cross-domain corpora for cross-domain users. | The simulated panel stays within the AI domain for the core results. |

---

## 2. Agent-friendly ways to get news (beyond crawling)

There's now a solid set of options that avoid writing scrapers:

| Category | Examples | What you get | Good for | Caveats |
|---|---|---|---|---|
| **Event-clustered news APIs** | [NewsAPI.ai / Event Registry](https://newsapi.ai/blog/tag/news-api-pricing/), [NewsCatcher's NewsMCP](https://www.newscatcherapi.com/blog-posts/newsmcp-for-ai-agents) | Articles already grouped into **events or stories**, with entities, categories, and article counts; large outlet coverage; some historical archive | Channel ingestion for any topic; importance from coverage counts; saves building our own clustering | Event Registry is reported at about $90/month entry, with token-based historical queries ([Geekflare](https://geekflare.com/guides/global-news-api/), [G2](https://www.g2.com/products/newsapi-ai/pricing)). NewsMCP was announced in September 2026 as free, no key ([press release](https://www.daily-tribune.com/online_features/press_releases/newscatcher-launches-newsmcp-free-news-search-for-ai-agents/article_a94fbc34-55a1-5574-9fb8-6a83d4e116f4.html)); limits and reliability unverified. |
| **AI search APIs** | [Exa, Tavily, Perplexity Sonar](https://www.humai.blog/ai-search-apis-compared-tavily-vs-exa-vs-perplexity/) | Search results, often with cleaned page text, built for LLM use; date filters | Long-tail channels; **live deep-dive search on cache misses** | About $5–8 per 1,000 searches, with prices changing in 2026 ([Firecrawl](https://www.firecrawl.dev/blog/best-search-tools-for-agents)). Not reproducible over time. |
| **News MCP servers** | NewsMCP; [Apify News MCP](https://apify.com/nexgendata/news-mcp-server) (AP, BBC, NPR, HN, Google News) | Tools an agent can call directly | Fast prototyping; deep-dive tools | Many are small third-party projects. Treat as convenient, not dependable. |
| **Free, structured feeds** | Google News RSS topic search, GDELT, outlet RSS, HN Algolia API, arXiv API, Reddit API | Raw items, some with engagement signals | Cheap baseline coverage for many channels; the AI research corpus | Needs our own dedupe and clustering. Check terms of use (especially for Google News RSS). |
| **Content extraction** | Jina Reader, Firecrawl, and the content endpoints of the search APIs | Clean article text from a URL | Pre-fetching full text for deep dives | Paywalls; per-page cost |

**Recommendation:**
- **AI research channels:** use free structured feeds (arXiv, HN Algolia, lab blog RSS, curated outlets), the same as before.
- **Other channels:** one event-clustered news API as the backbone (NewsMCP to start, since it's free; Event Registry if NewsMCP is too limited), with Google News RSS or GDELT as a fallback.
- **Live search on cache misses and follow-up research:** one AI search API (Exa or Tavily).
- **Behind an interface:** each provider sits behind a `SourceProvider` interface, so providers can be swapped without touching the agent.

---

## 3. Audio-to-audio (speech-to-speech) models vs. a speech-to-text pipeline

### 3.1 Options

| | **Cascade**: speech-to-text → text agent → text-to-speech | **End-to-end speech-to-speech** model as the whole agent | **Hybrid**: speech-to-speech front end, text agent as the "brain" via tools |
|---|---|---|---|
| Examples | Any STT (for example Deepgram, Whisper) + any LLM + any TTS (for example ElevenLabs, Cartesia) | OpenAI Realtime API (gpt-realtime family), Google Gemini Live API, Amazon Nova Sonic ([Stream](https://getstream.io/blog/speech-apis/), [Cekura benchmark](https://benchmarks.cekura.ai/speech-to-speech)) | Same speech-to-speech models, with tools like `get_brief()`, `answer(question)`, `save_followup()` that call our text agent |
| Latency | Three hops; about 1–2 s with streaming and fast models | Lowest; natural turn-taking and interruption | Low for conversational turns; a tool call adds a pause (which can be covered with "let me check") |
| Naturalness | Good TTS is very good, but turn-taking is cruder | Best: prosody, interruptions, back-channelling | Close to the best |
| Grounding and citations (G6) | **Strong**: we control every generated sentence | **Weak**: the model speaks directly, so claims are hard to constrain or check before they're heard | **Medium**: grounded text comes from our agent, but the voice model may paraphrase it, and paraphrase can drift |
| Model choice | Any LLM, including the strongest text models | Locked to a few vendors' realtime models | Any LLM for the brain; vendor-locked voice layer |
| Cost | Pay per component; usually cheapest with good choices | OpenAI reported at about $32 / $64 per 1M audio tokens in / out (mini about $10 / $20); Gemini Live much cheaper ([Micdrop](https://micdrop.dev/blog/gpt-live-vs-gpt-realtime-vs-gemini-live), [Layer3](https://www.layer3labs.io/guides/best-llm-for-voice-agents)) | Speech-to-speech cost + text agent cost |
| Evaluation fit | **Exact**: the text we evaluate is what the user hears | **Poor**: simulations run in text, but users hear a different system | **Medium**: the text agent is evaluated directly, and the paraphrase gap must be measured separately |

### 3.2 Recommendation

- **MVP: cascade.** It's the only option where the evaluated system and the system people use are the same, and where grounding can be enforced before speech. That's what the research goals need. Build the voice layer behind an interface (`VoiceFrontEnd`), so it can be swapped later.
- **Stretch experiment: hybrid speech-to-speech front end.** Time-boxed, after the core evaluation is running. It's also a research question worth reporting: *how much does faithfulness drop when a speech-to-speech model voices grounded content?* This can be measured by running the citation checker on the speech-to-speech output transcripts and comparing against the text agent's output.
- **Avoid the end-to-end speech-to-speech agent** as the main path. It gives up the grounding and evaluation properties the project is built on.

### 3.3 Effect on research goals

> ⚠️ **Flag:** an end-to-end speech-to-speech agent would make the text-based evaluation (R4) measure a different system from the one users hear, and would weaken G6 grounding. Speech-to-speech is acceptable only as a front end with its paraphrase drift measured, or as a separate experiment.

---

## 4. iPhone access

| Option | Lift | Voice experience | Distribution | Notes |
|---|---|---|---|---|
| **Mobile web app in Safari** | Lowest: same codebase as the desktop web app | Push-to-talk works in a Safari tab. Mic capture stops when the screen locks. | A URL | Good enough to start. |
| **Home-screen web app (PWA)** | Low | Microphone access from the home screen has had long-standing WebKit bugs ([WebKit 185448](https://bugs.webkit.org/show_bug.cgi?id=185448)); current status needs testing on a real device | Manual "Add to Home Screen" | Safari can clear storage for unused web apps ([MagicBell](https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide)) |
| **React Native / Expo app** | Medium: new client, reuses the backend API | Native audio sessions, background audio, AirPods controls, CarPlay possible later | TestFlight (needs an Apple Developer account, $99/year) | Best commute experience; a meaningful extra build |
| **Native Swift app** | High | Best | TestFlight | Not worth it for the MVP |
| **Phone call** (telephony, for example Twilio) | Low–medium | Call a number and talk; works hands-free, with AirPods or in the car | A phone number; identify the user by caller ID + PIN | Surprisingly good fit for "on the go". Adds telephony audio quality and per-minute cost. |

**Recommendation:**
- **MVP:** a mobile-first web app in Safari, since the backend API has to exist anyway. Test the home-screen web app's microphone early on a real iPhone.
- **Later:** if commuting with the screen locked is painful in the self-study, add an **Expo app** (or the phone-call channel) as the second client. Both reuse the same API, so this doesn't affect the backend design.

**Effect on research goals:** none, as long as every client goes through the same agent API and logging.

---

## 5. Summary of recommendations

| Question | Recommendation |
|---|---|
| Shared corpus with diverse interests | **Superseded by §6: agent-native.** No corpus in the product; a shared newsroom digest plus per-user search. The topic-channel analysis (§1) is kept as the alternative. |
| Fixed source list? | Not for the product (use a source policy). **Yes for the AI research domain** (a curated, versioned registry). |
| Where news comes from | Free structured feeds for AI; an event-clustered news API for other channels; an AI search API for live search and follow-up research; all behind a provider interface. |
| Audio-to-audio | Cascade for the MVP; a hybrid speech-to-speech front end as a stretch experiment that measures faithfulness drift. |
| iPhone | Mobile web first; Expo app or phone call as the second client if needed. |
| Research | The core evaluation stays on a **frozen AI-domain research config**. Product extensions are evaluated separately or switched off in that config. |

---

## 6. Agent-native design (adopted in PRD v0.6)

### 6.1 Why not keep a corpus?

A capable agent with web search and fetch can find what's new on demand, and importance can be **borrowed from external judgements** instead of computed over our own corpus:
- **editorial front pages** (Techmeme, trade-press front pages, wire top stories);
- **crowd rankings** (Hacker News, Reddit, Hugging Face daily papers);
- **expert curation** (newsletters);
- **coverage** across independent outlets.

Agreement across these signals is a strong, cheap importance estimate. That removes the ingestion pipeline, clustering infrastructure, and channel management from the product.

### 6.2 Why three roles

| Role | Why it's separate |
|---|---|
| **Newsroom** (shared, no user data) | Importance must be judged **without** the user's memory in context, or "important" drifts toward "important in your areas" and the filter bubble returns. A separate run also makes the importance-only baseline trivial, separates retrieval misses from selection misses in failure analysis, and lets one cached digest serve every user. |
| **Chief of staff** (per user, slow) | Personal search, scoring, brief writing, and source packs need minutes of agentic work, which is fine before the brief and impossible during a voice turn. |
| **Voice agent** (per session, fast) | A spoken turn needs about 1 s. It works from pre-built source packs with a fast model and a narrow tool set. |

The separation is enforced by **context**, not by deployment. One orchestrating job starts each role as its own model run, with its own prompt, tools, and inputs. The newsroom run is simply never given user data or memory tools.

### 6.3 Live vs. replay

Agents call tools (`search`, `fetch`, `headline_source`) whose backends are swappable:
- **Live:** the web.
- **Replay:** a frozen, date-stamped archive of the evaluation window, built from sources with date archives (Techmeme daily pages, HN Algolia, arXiv listings, RSS and blog archives).

The agent code is identical in both modes, which keeps the evaluation honest. Every live run also saves a trace of its tool calls and results.

### 6.4 Rough cost (planning only; measure in M4)

Model names below are price reference points only. Models are chosen per role by a bake-off (see `02a-technical-design-options.md` §1.3).

| Role | Per run | Frequency | Rough cost |
|---|---|---|---|
| Newsroom | About 30–60 fetches and searches, about 200–400K input tokens | 2–4× a day, shared | About $1–6 a day on Opus 5.5 ($4 / $20 per M tokens), shared by all users |
| Chief of staff | About 10–30 searches and fetches, about 100–200K input tokens | 1× per user per day | About $0.20–0.50 on Sonnet 5.5 ($2 / $10); a few cents on Haiku 5.5 |
| Voice agent | About 10–30K cached context, short turns | Per session | About $0.01–0.05 per session on Haiku 5.5, plus speech-to-text and text-to-speech |
| Web search | About $10 per 1,000 searches (Claude web search) or about $5–8 per 1,000 (Exa / Tavily) | Per search | Included in the estimates above at about $0.10–0.30 a day per user |

Per-user cost lands at **roughly $0.05–1.00 a day** depending on model tiering. Evaluation runs (6 configs × 3 seeds × about 600 user-days) are the larger budget item and must be sized in the technical design. Prices change, so confirm against current pricing pages.

### 6.5 Effect on research goals

- **Replay** works through the replay backends. The archive is needed **only** for evaluation.
- **Hacker News circularity:** using HN as a live signal while also using HN score as a hindsight label is partly circular. The fix is brief-time HN values only, the coverage label as primary, and an ablation without HN.
- **Non-determinism:** agent runs vary, so use traces, at least 3 seeds, and report variance.
- **Search mismatch:** live web search differs from replay archive search. State this as a limitation.
