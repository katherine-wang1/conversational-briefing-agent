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
- [ ] **Decision:** may the agent use background knowledge for stable context (what a company is,
  what a term means) while news claims stay cited? Leaning yes.

## 3. Prompting for audio only (parked)

**Problems** (see `docs/smoke/2026-11-05/brief_script.txt`): the headline is never read aloud, the
intro doesn't preview the stories, internal labels are spoken ("a story picked for you"), stories
change with no transition, and the rule of one citation per sentence with sentences under 25 words
makes it sound like a list of facts with no context.

- [ ] Rewrite `app/agents/prompts/cos.write_brief.md` for a listener with no screen:
  - Spoken rundown in the intro.
  - Signposts between stories.
  - Name sources aloud.
  - One sentence of context and one on why it matters to this listener.
  - End each story with a hook for questions.
- [ ] Write each story as a spoken paragraph and cite at paragraph level. Code splits sentences and
  maps citations; the claim check stays per sentence.
- [ ] Rewrite `app/agents/voice/prompts/voice.turn.md` the same way: always name the story, allow
  longer answers for "tell me more", never assume a screen.

## Order

1. Part 1 quick wins.
2. Prompt rewrite (part 3).
3. Shared news store with story notes and voice tools (rest of parts 1 and 2).
