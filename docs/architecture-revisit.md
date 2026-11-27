# Architecture revisit: open tasks for thought

Started 2026-11-26. A review of three parts of the system: how we find news, how we store it for
live questions, and how we prompt for audio. Part 1 (finding news) is being worked on first. Parts 2
and 3 are parked here until then.

## 1. Finding news (in progress)

**Problems:** the pipeline is slow (newsroom about 2.5 min, brief about 2 min), uses too much memory
when deployed, and returns mediocre results.

**Findings from the code and the smoke traces:**
- Almost everything runs one step at a time: source fetches, observation inserts
  (`app/jobs/handlers.py`), scoring batches (`app/agents/newsroom/pipeline.py`), the 10–12 searches
  (`app/agents/cos/search_loop.py`), and fetch plus extract for each story (`app/agents/cos/pipeline.py`).
- `newsroom.score` produced about 51k output tokens over 10 calls, 3 of them repairs (smoke run
  2026-11-05). That roughly accounts for the newsroom's run time.
- Clustering merged only 8 of 98 stories (106 items). fastembed/ONNX plus three newsroom jobs running
  at once (worker concurrency 4) are the most likely cause of the deploy memory problems.
- Tavily `news`: about half the URLs are `google.com/url?...` redirects, and results are snippets
  only, so every story needs a separate fetch. Paywalled fetches fail (wsj.com 401, openai.com 403),
  and in one run both big-news stories were dropped because of it.

**Quick wins:**
- [ ] Run collection, searches and fetch+extract concurrently, each behind a semaphore.
- [ ] Cut the scoring output down to id, score, subtopics and a short reason.
- [ ] Remove fastembed: dedupe with shared URLs and rapidfuzz title similarity, and let the scoring call flag duplicates.
- [ ] Run one newsroom job at a time in the worker.

**Bigger change: gather news once for everyone.** A scheduled shared job every 1–3 h: collect,
dedupe, rank, fetch full text once for the top ~100 stories, and store them. Per-user briefs
retrieve from that store; live search only fills interests the store doesn't cover.

**Questions still open:**
- [ ] Buy ranking and clustering from a provider (NewsCatcher, Perigon, Event Registry), or keep our
  own feeds and Techmeme/HN ranking? See the provider notes below.
- [ ] If clustering goes, the importance "coverage" signal and the eval's hindsight labels
  (TDD §16, "covered by ≥ k independent domains") need another source.
- [ ] Paywalls: prefer a free outlet or the primary source from the same story; don't circumvent paywalls.

**Provider notes (2026-11-26, not yet tested):**
| Provider | Ranking / clustering | Notes |
|---|---|---|
| NewsCatcher v3 | Clustering on search and latest headlines; a breaking-news endpoint ranks clusters by publication spikes, publisher spread and high-ranking sources | Returns embeddings; full text |
| Perigon | Stories endpoint (clusters with aggregate stats, `sourceDiversity`, dedupe) | Paid; check which tier includes Stories |
| Event Registry (newsapi.ai) | Events = article clusters; sort by size (article count) or social shares | Free plan: 2,000 searches |
| Exa | No ranking; news category, full text in the same call | Already wired up (`SEARCH_PROVIDER=exa`) |
| Tavily | No ranking; `include_raw_content` would skip the fetch | Current provider |

### Decisions (2026-11-26)

- **Balance:** the evaluated research config must stay replayable and reproducible (course rubric:
  results 10/25, reproducibility 5/25; final report Dec 11, demo Dec 9). The live product should use
  the best off-the-shelf discovery. Both are tested side by side.
- **BUILD-1 amended** (PRD §6.0): provider search, including Anthropic's built-in web search, may sit
  behind our `search` tool as a live backend. A provider-run agent is a comparison arm only.
- **Keys stay optional**, with a fallback, so the README quick-start keeps working for graders.
- The evaluation hasn't started, so the newsroom can still change before the research config is frozen.
- **2026-11-27: the editor newsroom is the default** (`newsroom.impl: editor`), with Sonnet 5.5 through
  OpenRouter (reasoning effort medium) and the v4 prompt (`newsroom.editor_v4`: one event per story,
  general importance criteria). Evidence: `docs/smoke/2026-11-27/editor_multiday/results.md`. Haiku
  returned short lists in 5 of 24 runs and bundled more; Sonnet costs about $0.09 per beat. Unrated so
  far: the blind pool should still confirm it. `classic` stays selectable (`NEWSROOM_IMPL=classic` or the
  `newsroom_impl` experiment flag) for rollback and as a comparison arm.

### Comparison arms

| Arm | What it is | Replayable? |
|---|---|---|
| `classic` | The current newsroom, sped up (no change in what it decides) | Yes |
| `editor` | Wide headline collection (major outlets, Google News topic feeds, Techmeme, HN) → dedupe → one LLM editor call that groups and ranks | Yes |
| `editor+search` | `editor`, plus a bounded gap-fill search ("what important news isn't on this list?") through the `search` tool, Anthropic backend live | Core yes; gap-fill live only |
| `claude_agent` | Claude with web search + web fetch ranks the beat's news by itself (what a scheduled task does) | No |
| `eventregistry` | Event Registry's own events ranked by article count | Past dates via its API, if the free plan allows |

**Measured per arm and day:** top-10 overlap, stories found only by that arm, outlet mix
(including paywalled outlets), run time, peak memory, cost.

**Blind rating:** top stories from all arms are pooled, de-duplicated, shuffled and shown without
the arm. Kate rates each one must-know, nice-to-know or skip. This can be done daily, or in one
sitting at the end over the collected days. Arms that can't be replayed must be collected live on each
day, so the daily collection runs even if the rating happens later.

**Keys:** `ANTHROPIC_API_KEY` and `EVENT_REGISTRY_API_KEY` are environment variables, not
proxy-injected secrets: Anthropic's hosts don't take network secrets, and Event Registry takes the
key in the request body.

## 2. Storing news for live questions (parked)

**Problem:** the voice agent sees only the 4–6 brief stories' packs (passages cut to 700 chars,
`app/agents/voice/context.py`) plus 2 quick searches with a 3 s timeout. It can't answer "what else
happened?" or give background.

- [ ] Four levels of access:
  - L0: brief stories as full story notes and full text, in the prompt.
  - L1: a compact list of every story in today's digest, all beats (headline, 1–2 sentences, id), in the cached prompt.
  - L2: `get_story(id)` and `search_news(query)` tools over the last 7–30 days (Postgres full-text search, pgvector later).
  - L3: web search.
- [ ] Story notes, written once per story at collection time and shared by all users: facts with
  citations, people and companies involved, context, why it matters.
- [ ] Store more stories per user than the spoken brief covers (Kate, 2026-11-27). The brief reads
  4–6 stories aloud, but each user's run should keep a larger ranked set (their top N across beats,
  with story notes and source packs) so the live conversation can explain and discuss stories that
  weren't in the intro. Today only the brief's stories get packs (C6), and the voice agent sees only
  those.
- [ ] **Decision:** may the agent use background knowledge for stable context (what a company is,
  what a term means) while news claims stay cited? Leaning yes.

## 3. Prompting for audio only (done 2026-11-27, see `docs/smoke/2026-11-27/brief_audio/results.md`)

**Problems** (see `docs/smoke/2026-11-05/brief_script.txt`): the headline is never read aloud, the
intro doesn't preview the stories, internal labels are spoken ("a story picked for you"), stories
change with no transition, and the rule of one citation per sentence with sentences under 25 words
makes it sound like a list of facts with no context.

- [x] Rewrite `app/agents/prompts/cos.write_brief.md` for a listener with no screen (v2):
  - Spoken rundown in the intro: the writer gives a cited teaser per story; C8 checks the teasers and
    code builds the rundown from the stories that survive (`app/agents/cos/spoken.py`).
  - Signposts between stories, a spoken headline sentence first, sources named aloud, one sentence of
    context and one on why it matters (only for interests the story matches), a hook at the end.
  - No internal labels (slot names are no longer in the payload).
  - Code: every story keeps an introducing sentence (C8-dropped lead → its checked teaser, else the
    story goes); adjacent near-duplicate sentences are dropped (rapidfuzz); uncited lines with numbers
    are dropped.
- [ ] ~~Write each story as a spoken paragraph and cite at paragraph level.~~ **Not done, by decision.**
  The listing sound came from the prompt's rules, not the per-sentence contract; v2 gets the flow
  with sentence-level citations. Paragraph citations would give each sentence every passage of its
  paragraph (a weaker and costlier C8, and the voice agent loses which passage backs which sentence),
  would send framing lines (signposts, hooks) through the claim check, where they fail, and need a
  sentence splitter whose mistakes move audio segment boundaries ("U.S.", "34.3"). Revisit if C8 starts
  finding its own supporting passages.
- [x] Rewrite `app/agents/voice/prompts/voice.turn.md` the same way (v3): always name the story,
  four to eight sentences for "tell me more", never assume a screen, sources aloud.

## Order

1. Part 1 quick wins.
2. Prompt rewrite (part 3).
3. Shared news store with story notes and voice tools (rest of parts 1 and 2).
