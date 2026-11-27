# Next session: where things stand and what to tackle

Written 2026-11-27 at the end of a long working session, as the starting point for the next one. Branch:
`claude/sweet-curie-r8ufht` (pushed). No PR.

## Where the task lists live

| File | What it is | State |
|---|---|---|
| **This file** | The prioritized task list and the open questions for the next session | Current |
| `docs/open-decisions.md` | The running tracker: decisions for Kate (D#), follow-ups (F#), closed items with outcomes | Current; the detail behind this file |
| `docs/architecture-revisit.md` | The architecture review (finding news, storing news for live questions, audio prompting), with its decisions log | Current for its three parts |
| `docs/TODO.md` | The older Track 1 / Track 2 list from the midpoint demo work (TTS, README, deploy, eval) | Older; still the place for the demo, deploy and evaluation items. Not updated this session |
| `docs/stored-set.md` | Design note for the per-user stored set and the voice `get_story` tool | Reference |

## What exists now (built this session)

One daily cycle runs end to end in about 5 minutes and $0.40 for three beats and one user (`scripts/e2e_run.py`;
latest log `docs/e2e/2026-11-27/run_log.md`, earlier runs beside it):

1. **Collect** headlines from every source (free).
2. **Newsroom** (shared): the editor implementation is the default (`newsroom.impl: editor`), Sonnet 5.5 through
   OpenRouter with prompt v4. Prompts v2 (old), v3, v4, v5 (blend) exist; the choice is open (see Q1).
3. **Story notes** (shared, one per story, Haiku): facts quoted from fetched sources. Google News links are
   resolved to the publisher. Paywalled stories fall back to free coverage of the same event or to the
   publisher's own headline and summary, attributed. 7 of 80 had no text in the last run.
4. **Per-user run** (Haiku): plan, search, pool of 80, score, select 6, source packs, write, claim check,
   then a stored set of 20 stories for the conversation.
5. **Voice**: the brief, the packs, the stored-set list, and `get_story` for any stored story's note.

Comparison and rating tools: `python -m eval.newsroom_compare` (collect, `collect --at` to rebuild a past
day, run arms, summary). Blind list-comparison pages: round 1
https://claude.ai/artifact/Dppc6cYRVSSbZFogXCXecw, round 2 https://claude.ai/artifact/84Cv2BmqUi7sLeNCY7x7CE
(answer keys in `docs/rating/`).

Working rules carried over: avoid the Anthropic key (everything through OpenRouter; `claude_agent` runs on
hold); never circumvent paywalls; keys optional with fallbacks; the evaluated setup stays replayable; don't
create scheduled tasks.

## Kate's latest feedback (2026-11-27, on run 4)

- **Energy stories in the brief:** the test user's profile has interests in agent memory (0.6), open
  models (0.4), Acme (0.3) and **SMR licensing (0.3, "keep me posted")**, which sits in the energy
  beat. The e2e script also makes that user follow all three beats (the fixture default is ai only), and
  the brief has an "outside your usual" slot that deliberately draws from other areas. Together they
  brought in the X-Energy, TVA and Senate permitting stories.
- **URLs reaching the user:** not important. F27 drops to low priority.
- **The spoken brief:** too disjointed, more news anchor than podcast. Wants a narrative meant to be
  heard, with more context on each story: what it is, why it's happening, background. Context should
  scale with novelty, using the history of stories already given to the user (GTA VI got two sentences).
- **No segment labels:** don't announce "energy", "outside your usual beat" or similar. The listener
  doesn't need the system's categories.
- **Newsroom lists (round 2, ai only):** no clear winner. Best: Sonnet + old prompt 2, Sonnet v5 1,
  Haiku + old prompt 1. Worst: Haiku + old prompt 2, Sonnet v5 1, Sonnet + old prompt 1. Notes:
  - Every list was concentrated on Nvidia, chips and markets.
  - The same topics repeated across days ("the Anthropic IPO is now old news").
  - Prefers actual developments (model releases, deals) over someone saying something.
  - Prefers complete headlines ("raises $81.3B, then shares fall from first-day gains").
  - Would like more on what a model release actually changes.
- **Round 1 (6 of 12):** Haiku + old prompt best 5 of 6, Sonnet v4 worst 3 of 6. Notes: lists lose
  for clustering (several stories about one company or saga) and gain for headline-like titles.

## Task list, in suggested order

### 1. The spoken brief as a podcast (highest value, all per-user)
- [ ] Rewrite `app/agents/prompts/cos.write_brief.md` for a narrative, conversational, podcast-like brief.
      Drop the anchor-style signposts and never name segments or slots ("now to energy", "outside your
      usual beat"). Connect stories where they connect.
- [ ] More context per story: what happened, why it's happening, background. The writer only sees the
      C6 passages today; give it the shared story note (facts, context, why it matters) as well, so it
      has background without new fetches.
- [ ] Context scales with novelty. Use the user's history (`ShownStory`, story identity across runs via
      `update_of`) to mark each story as new to this listener, a follow-up, or a repeat. New topics get
      background; follow-ups get "since we last talked…"; repeats get skipped or kept to one line. Check
      what history the pipeline already passes (`history` argument of `run_cos_daily`).
- [ ] Re-balance the claim check for longer narrative sentences: context sentences cite the note or pack;
      background-knowledge context (allowed for stable facts, see Q4) must be marked so C8 doesn't drop it.
- [ ] Watch the output limit: the writer now has 10,000 output tokens (4,000 truncated a draft in run 3).
      Longer stories may need more, or one call per story.
- [ ] Rundown: shorter and spoken (it is a 60-word list today, F16).

### 2. Newsroom ranking: concentration and novelty across days
- [ ] Novelty across days: the newsroom ranks each day in isolation in the comparisons. Pass prior days'
      digests or stories (N6 story identity already exists) so continuing stories need a new development
      to rank high, and "old news" (the IPO three days later) drops.
- [ ] Concentration: cap stories about one company or saga near the top (v5 tries this; round 2 says it
      isn't enough) and favour breadth: releases, deals, research, policy.
- [ ] Scope: keep off-beat stories out (GTA VI in the ai digest, F26).
- [ ] Then pick the newsroom prompt (Q1) on real collected days, ideally consecutive ones with history,
      split into days to tune on and days to test on (F2).

### 3. Per-user stored set and voice
- [ ] Stored-set mix (D8): today it is mostly the user's own search finds; reserve a share for shared
      stories (they have notes) or rank by the higher of the personal and big-news scores.
- [ ] Filter low-quality personal finds: social posts, quote pages, duplicates (F18, F20). The voice agent
      stated an Instagram post as fact.
- [ ] Voice prompt: no narration of tool use (F19); same podcast tone as the brief.

### 4. Cost and tokens (low urgency; $0.02 per user per run today)
- [ ] Per-step token allowances instead of one shared cap; prompt caching for scoring; a shorter line per
      story in scoring (F21). Keep the 80-story pool (D2).

### 5. Evaluation and data
- [ ] Collect real days every day (free) so prompts can be tuned and tested on different days (F2).
- [ ] Re-run the round-2 comparison on energy and physical_ai if you want them rated (only ai was rated).
- [ ] The older evaluation track in `docs/TODO.md` (Track 2) is untouched by this session.

## Open questions and considerations

| # | Question | Notes |
|---|---|---|
| Q1 | Which newsroom prompt and model? | Open. Default is Sonnet + v4, which round 1 rated worst. Round 2 had no clear winner. The deciding issues (novelty across days, concentration) are prompt and history problems, so decide after task 2. Sonnet costs ~$0.09 per beat; Haiku was unreliable (short lists, once a single story) |
| Q2 | Should the "outside your usual" slot stay? | Kate doesn't want it labelled. Whether it should exist at all is open |
| Q3 | How much context per story, and how long a brief? | Kate wants more context. The delivery preference today is 180 s (`length_s`), which six stories with real context won't fit. Fewer stories, or a longer brief? |
| Q4 | Background knowledge in the brief | `docs/architecture-revisit.md` part 2 leans yes for stable context (what a company is) while news claims stay cited. The notes already allow it in `context`. The brief writer needs the same rule, and C8 must not drop it |
| Q5 | Statements vs news (D4) | In v5; keep in any newsroom prompt (Kate prefers developments over "X says Y") |
| Q6 | Licensed full text (D10) | Summary-only notes are thin. Cheapest to try: NYT Developer API (licensed abstract and lead paragraph), Event Registry article bodies. Coverage and terms unverified |
| Q7 | Story-note model (D5) | Haiku today (~$0.06 per run). Compare Sonnet on ~10 stories |
| Q8 | Do source links matter? | Kate, 2026-11-27: not much. Keep citations internally for grounding; don't invest in showing links |
| Q9 | Cross-beat duplicates (F1) | The same story (Huang) appears in two beats' digests and gets two notes |
| Q10 | Free-coverage search provider | 57% of Tavily hits are Google redirect links that mostly fail; Exa or Brave return direct links (F22) |

## Spend this session

Anthropic: $5.06 early on (Opus, then Sonnet `claude_agent` runs), none since Kate asked to avoid that key.
OpenRouter: roughly $10 across comparisons, sub-agents and five end-to-end runs. Tavily: a few hundred searches.
