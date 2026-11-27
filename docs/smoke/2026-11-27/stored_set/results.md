# Stored set + voice `get_story`: live smoke (2026-11-27)

Command (in process; no Postgres, no worker):

    env -u CLAUDE_API_KEY -u ANTHROPIC_API_KEY .venv/bin/python scripts/smoke_stored_set.py \
        --digests docs/smoke/2026-11-27/editor_to_brief --out docs/smoke/2026-11-27/stored_set

- Inputs: the committed real digests `editor_to_brief/digest_{ai,energy,physical_ai}.json`
  (30 + 25 + 25 entries, editor newsroom, collection of 2026-11-26 22:10 UTC) and the synthetic
  fixture user (`tests/unit/cos/fixtures.py`: interests AI agents and memory, open models, SMR
  licensing). Followed beats: all three.
- Models: every role on `dev_default` (Haiku 5.5 via OpenRouter, reasoning off). Search: Tavily.
  Fetch: live.
- Voice: one in-process `VoiceSession` turn in text mode after the brief finished, with an
  **empty** `InMemoryNoteStore` (no real story notes exist yet; the notes writer is a separate work
  stream). So this shows the no-note fallback path, not a note being read.

Files: `stored_set.json` (the `UserStorySet`), `brief.json`, `brief_script.txt`,
`source_packs.json`, `voice_turn.json` (answer, citations, live log), `summary.json`,
`llm_ledger.jsonl`. `run1/` is the first run (before two fixes, see the end).

## Stored set (20 stories; 4 in the brief)

`run_cos_daily` took 77 s and returned the brief (4 stories after the claim check), 6 source packs
(the 6 C5 picks), and `trace.story_set`. Order: brief stories in listening order, then C5 picks the
claim check cut from the brief, then the next best by the C5 for-you ranking (MMR).

| # | story_ref | in brief | note | beat | rel | imp | why | headline |
|---|---|---|---|---|---|---|---|---|
| 1 | `ai-5166002f4d42` | yes | shared | ai | 0.40 | 93 | big news, i-open | Nvidia tells server partners to pause shipments to Malaysia, Singapore, Thailand and Indonesia… |
| 2 | `ai-651cc167b0ad` | yes | shared | ai | 0.30 | 88 | outside your usual, i-open | Sources say leading AI labs hold unreleased algorithmic breakthroughs that could break deploye… |
| 3 | `u:43c22bce363c` | yes | per_user | ai | 0.75 | 50 | for you, i-open | NVIDIA Releases Open Weights For Nemotron-Labs-Image-30B-A3B, An Autoregressive Image Generato… |
| 4 | `u:f0c64e2aa10c` | yes | per_user | ai | 0.80 | 35 | for you, i-agents | Leto: Utility-Centric Agent Memory |
| 5 | `physical_ai-2af40820bf64` |  | shared | physical_ai | 0.15 | 90 | big news, cut from the brief | XPeng unveils IRON humanoid and Turing VLA 3.0 physical AI architecture at AI Day, targeting m… |
| 6 | `u:f0feba66efd2` |  | per_user | – | 0.75 | 55 | for you, cut from the brief, i-smr | Travis Sachs on Instagram: "Dow and X-energy just received NRC approval to build four Xe-100 s… |
| 7 | `u:16d8e33a0630` |  | per_user | ai | 0.70 | 40 | for you, adjacent, i-agents | Lazy Agents: RLVB Trains Commit Timing (+17% Avg) / explainx.ai Blog |
| 8 | `u:dbf4ca7909ae` |  | per_user | ai | 0.70 | 40 | for you, personal, i-open | InclusionAI releases open-weight medical AI with a million-token context / Superpower Daily |
| 9 | `u:c4c8118aa18d` |  | per_user | ai | 0.70 | 35 | for you, adjacent, i-agents | EvolveMAS: A Unified Self-Evolution Framework for Decentralized Multi-Agent Systems |
| 10 | `ai-1109dc2af8f5` |  | shared | ai | 0.60 | 60 | for you, digest, i-agents | AI leaders push back on Anthropic vision; OpenAI and Anthropic researchers warn new models may… |
| 11 | `u:21c2dd316a1d` |  | per_user | ai | 0.75 | 35 | for you, personal, i-agents | AI Agent Memory Breaks the Day a Fact Changes |
| 12 | `ai-68db70806804` |  | shared | ai | 0.50 | 80 | for you, digest, i-open | US chip curbs and distillation crackdown threaten Chinese AI model development; Z.ai's GLM-5.5… |
| 13 | `u:6320c78f53dd` |  | per_user | – | 0.60 | 45 | for you, personal, i-smr | How 12 months made 20 years - Nuclear Engineering International |
| 14 | `u:bbaab1549e54` |  | per_user | ai | 0.55 | 45 | for you, personal, i-open | China and the United States Are Running Different AI Races in Africa / Carnegie Endowment for … |
| 15 | `u:32ca84e4b7b9` |  | per_user | ai | 0.65 | 30 | for you, adjacent, i-agents | Procedure Inference as Memory: Learning Recruiter Decision ... |
| 16 | `u:f81b8ae246e0` |  | per_user | energy | 0.60 | 40 | for you, personal, i-smr | Small Modular Reactors Finally Have Real Momentum in the U.S. / OilPrice.com |
| 17 | `ai-404c6bdd6fca` |  | shared | ai | 0.50 | 65 | for you, digest, i-open | ASML CEO and Dutch government push back on proposed US export curbs |
| 18 | `u:62a2d2e68969` |  | per_user | ai | 0.60 | 25 | for you, personal, i-agents | Context Engineering: The Three Layers AI Agents Need |
| 19 | `u:f26b25c3772d` |  | per_user | ai | 0.60 | 25 | for you, personal, i-agents | Most of the 2026 AI agent stack maps onto classic architecture styles - DEV Community |
| 20 | `ai-16b0900dd66d` |  | shared | ai | 0.40 | 68 | for you, digest, i-agents | Microsoft says China and North Korea state hackers use AI pentest tool ARTEX |

What this shows:
- 13 of 20 are the user's own finds (`per_user`, no shared note); 7 are digest stories (`shared`).
  The for-you score (`relevance × (0.5 + 0.5·importance/100)`) favours the user's high-relevance
  finds, so most of the stored set has no shared note. See "Open decisions" in docs/stored-set.md.
- No energy or physical_ai digest story made the set beyond the cut XPeng pick: the fixture user's
  energy interest (SMR licensing) is covered by finds, and physical_ai scores low relevance.
- Two C5 picks (ranks 5, 6) were cut from the brief by the claim check; they still have source
  packs in the CORPUS, so the voice agent can explain them from passages.

## Voice turn about a stored story the brief didn't cover

Target: rank 10, `ai-1109dc2af8f5` (digest story, `note_status=shared`, no source pack this run).
The script picks the first non-brief `shared` story without a pack, so the note store is asked and
nothing else in the context covers it.

> **Listener:** Before we go, what's the story with AI leaders push back on Anthropic vision; OpenAI
> and Anthropic researchers warn new models may be too powerful to release?
>
> **Agent:** That one isn't in the brief, so I'll pull up the detailed note on it first. The stored
> note on this one is thin. Based on its headline and summary, Axios reports that rival AI leaders
> are disputing Anthropic's vision, and that Dario Amodei has pushed back on reporting about free
> will. It also reports warnings from researchers at OpenAI and Anthropic that some new models may
> be unsafe to release. I don't have the details of those arguments, so I can look for more if
> you'd like, or I can bookmark the question for the next brief.

Live log (`voice_turn.json`):

    voice.turn round 1: anthropic/claude-haiku-5.5 · 2664 ms (first token 2279 ms) · 12587 in (0 cached) / 88 out · $0.0016 · 1 tool call(s)
    tool call get_story(story_ref='ai-1109dc2af8f5')
    get_story ai-1109dc2af8f5: no note stored; headline and summary only
    voice.turn round 2: anthropic/claude-haiku-5.5 · 1362 ms (first token 665 ms) · 13768 in (12411 cached) / 146 out · $0.0003
    turn: 4.0 s total; signal: dive on ai-1109dc2af8f5

The stored summary it had: "Axios reports rivals disputing Anthropic's vision, Amodei pushing back on
free-will reporting, and warnings about unsafe-to-release models."

Honest reading:
- The path works: the model recognised the story from the L1 list, called `get_story`, got the
  fallback (headline, summary, links, "no detailed note is stored"), stayed within the summary, and
  offered a search or a bookmark instead of inventing detail. It did not call `quick_search` on its
  own; it offered it.
- Weak spots, for the prompt rewrite: it narrated ("I'll pull up the detailed note", "The stored note
  on this one is thin"; there is no note at all), and it did not put the `[p:stored:…]` marker on
  its factual sentences, so no source link was shown for this answer. In run 1 (below) it did cite.
- The L1 list (20 lines) sits in the cached prefix: the turn's first call sent 12.6k input tokens,
  and round 2 read 12.4k of them from the cache.
- Links in the stored set are still `news.google.com` redirects; a separate work stream resolves
  them.

## Run 1 (`run1/`)

Before two fixes. The voice target was rank 4 there, XPeng (`physical_ai-2af40820bf64`), a C5 pick
cut by the claim check, so its pack was in the CORPUS. The agent answered from the stored summary
with `[p:stored:physical_ai-2af40820bf64]` citations (resolved to the story's first link). Fixes
made after it: (1) brief stories in the stored set now follow listening order (the writer had
reordered `script.stories`); (2) the no-note fallback now points to the CORPUS when the run built a
pack for the story, and the smoke script targets a story without a pack.

## Cost

| run | cos_daily | voice turn | total |
|---|---|---|---|
| run 1 | $0.0157 | $0.0018 | $0.0175 |
| run 2 (this page) | $0.0165 | $0.0019 | $0.0184 |
| **total** | | | **$0.036** |

By role (run 2): cos.plan 0.0004, cos.search_loop 0.0039, cos.score 0.0080, cos.extract_passages
0.0018, cos.write_brief 0.0015, cos.claim_check 0.0010, voice.turn 0.0019. Building the stored set
itself makes no model calls (`stored_set_extra_packs: 0`). Tavily search costs are not in the
ledger (C2 used its usual search budget; the voice turn made no search).
