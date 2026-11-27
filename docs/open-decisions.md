# Open decisions and follow-ups

Kept current during the 2026-11-27 work (part 1 of `docs/architecture-revisit.md` and the steps after
it). "Decision" items need Kate. "Follow-up" items are known work. Closed items move to the bottom with
the date and outcome.

## Decisions for Kate

| # | Decision | Context | Recommendation |
|---|---|---|---|
| D1 | Compare newsroom lists (replaces rating all 195) | 12 blind side-by-side comparisons (4 days × 3 beats) of the three remaining setups' top 5: Haiku with the old prompt, Haiku v4, Sonnet v4. Pick best and worst list. Page: https://claude.ai/artifact/Dppc6cYRVSSbZFogXCXecw (answer key: `docs/rating/lists_key_2026-11-27.json`). The 195-story page stays open for spot checks (https://claude.ai/artifact/YWXefuNeTZwhEfPy8kkuz3) | 6 of 12 done (2026-11-27): Haiku with the old prompt best 5 of 6; Sonnet v4 worst 3 of 6. Kate's notes: lists lose for clustering (several stories about one company or saga), and gain for headline-like titles. Round 2 adds Sonnet with the old prompt and the blended v5 |
| D2 | Shared store size (25–30 stories per beat) and the per-user pool cap of 80 | A user following all 3 beats has 80 shared stories plus their own finds; the cap cuts digest stories by shared importance before personal scoring (55 of 80 reached scoring in the smoke run) | Kate, 2026-11-27: leave as is for now |
| D4 | Statements and commentary ("X says Y") | Kate, 2026-11-27: several pooled stories read as opinion, not something happening. v4 skips "opinion pieces without news" but lets a notable person's remarks through (Amodei calling claims "absurd", Aaronson on unreleased breakthroughs). Proposed general rule: a statement is news only when it announces or reveals something (a decision, a plan, new facts); commentary, reactions and predictions rank low or are skipped | In the blended v5 prompt (2026-11-27), together with one-story-per-saga and variety at the top |
| D3 | Test Exa for full text | Exa is supported by the `search` tool but has no key here | Decide after the story-notes numbers show how much text the fetcher gets |
| D5 | Story-note model | Haiku: $0.0008 per note, about $0.09 per run of 80 once Google News links resolve; Sonnet about 20× (estimated, not measured). Live notes read well on Haiku (`docs/smoke/2026-11-27/story_notes/`) | Keep Haiku; compare Sonnet on ~10 stories after Google News resolution |
| D6 | Story-note reuse (`story_notes.reuse_ok_hours: 6`) | Story ids persist across runs, so a recent ok note is kept instead of rewritten | Keep; set 0 to rewrite every run |
| D7 | Notes inside the newsroom job vs their own job | A separate job would get its own retries; today a notes failure is logged and never fails the newsroom job | Keep in the newsroom job for now |
| D8 | Mix of the per-user stored set (20) | It continues the brief's for-you ranking, which favours the user's own finds (no shared notes): in the smoke run 13 of 20 were the user's finds, 7 shared stories. Important shared stories with low relevance drop out (XPeng, importance 90, relevance 0.15, was in only because it was in the brief). In the e2e run, 13 of the 14 stories beyond the brief were the user's finds and only 1 was shared (`docs/e2e/2026-11-27/run_log.md`) | Rank by the higher of the for-you and big-news scores, or reserve about half for shared stories |
| D9 | Paywalls leave many shared stories without a note | In the end-to-end run, 15 of 30 ai notes had no text: links resolve, but to Bloomberg, FT, NYT and Reuters, which return 401/403. The architecture decision says prefer a free outlet or the primary source for the same story, without circumventing paywalls | Kate, 2026-11-27: do both: (1) ground on the publisher's own headline and summary from the feeds, attributed (\"The New York Times reports…\"); (2) one search for free coverage of the same event or the primary source. In progress |

## Follow-ups

| # | Follow-up | Where |
|---|---|---|
| F1 | Dedupe stories that appear in more than one beat (Nvidia/Huang in physical_ai) before a cross-beat per-user store | newsroom / per-user set |
| F2 | Collect real days going forward (free) so tuning and test days can be split; rebuilt days are thinner (40–60% of items, no front-page signals) | `python -m eval.newsroom_compare collect` |
| F4 | The first comparison day (Nov 26, 19:59) includes pre-date-fix outputs with months-old lab posts; they're in the 195-story pool, not in the list comparison | rating pool |
| F5 | Re-run the story-notes smoke after Google News resolution merges (15 of 24 notes had no text, 13 because of Google News links) | `scripts/smoke_story_notes.py` |
| F6 | reuters.com returned 401: consider adding it to the paywalled domains | `config/agent.yaml` |
| F7 | Story notes' quote check proves a phrase is in the source, not that the fact paraphrases it faithfully; consider a judge check in eval | eval |
| F8 | `memory_update` learns only from brief stories, so a `get_story` dive on a stored story is ignored | memory |
| F9 | Google News resolution uses Google's undocumented batchexecute endpoint; a format change makes it return `failed` (never a wrong URL). One cloud IP may hit Google's 429 page. Its JSON cache is on local disk (lost on Railway restarts) | `app/sources/google_news.py` |
| F10 | The title-search fallback for unresolved Google News links needs title hints passed from the observations | `GoogleNewsResolver.hint` |
| F11 | Stale items in the energy digest: three Google-News-only stories resolve to energynow.com articles from June–September (freshness filter misses them: Google News dates are re-index dates) | newsroom freshness |
| F12 | 9 of 46 Google-News-only stories still get no text (single outlet behind a paywall or bot block: energynow, Axios, Reuters 401, WSJ, Bloomberg, Nikkei) | story notes / C6 |
| F13 | Brief: a middle sentence can keep a dangling reference ("that loophole") after the claim check drops the sentence it refers to; only opening sentences are protected | `app/agents/cos/spoken.py` |
| F14 | Brief: story-end hooks ("I can walk you through…") aren't claim-checked and may offer detail the sources don't have | C7/C8 |
| F15 | Brief: some C6 source-pack citations don't state the fact (a title-derived fact); the claim check then drops the story | C6 |
| F16 | Brief: Haiku style slips in teasers (symbols like ≠, 9/4); the rundown runs 45–60 words with 4+ stories | `cos.write_brief.md` |
| F17 | Paragraph-level citation for the brief: not done (weaker claim check, voice loses per-sentence sources, fragile sentence splitting). Revisit if the claim check finds its own supporting passages | part 3 |
| F18 | Stored set: near-duplicate user finds ("Procedure Inference as Memory" twice) and low-value pages (a Yahoo Finance quote page) take slots; dedupe and filter per-user finds before ranking | `app/agents/cos/stored_set.py` |
| F19 | Voice narrates its tool use ("I need the detail on that one, so I'll pull up the stored note") | `voice.turn.md` |
| F20 | Voice states a stored story's headline as fact without a source (an Instagram reel in the e2e run); per-user finds need a source-quality filter (social posts, quote pages) | voice / stored set |
| F21 | Per-user token budget: give each step its own allowance instead of one shared cap (search used up what packs needed); turn on prompt caching for scoring (instructions and profile repeat in every batch; 0 cached tokens today); send scoring a shorter line per story. Keep the 80-story pool | `app/agents/cos/` |
| F3 | The replay archive on `data/archive-raw` has only Hacker News, arXiv and Hugging Face papers; the Wayback half (outlets, feeds) was never collected | `eval/archive/README.md` |

## Closed

| Date | Item | Outcome |
|---|---|---|
| 2026-11-27 | Newsroom default | Editor newsroom, Sonnet 5.5 (OpenRouter), prompt v4 (`docs/smoke/2026-11-27/editor_multiday/results.md`) |
| 2026-11-27 | Anthropic key use | Avoid; everything that can runs through OpenRouter. `claude_agent` runs are on hold |
| 2026-11-27 | Per-user token budget | Raised 90k → 150k input tokens: at 90k, search and scoring (~63k) plus the write reserve left source packs ~9k, so 3 of 6 picks were dropped from the brief (`docs/e2e/run1_budget90k/`). About $0.006 more per user per day on Haiku |
| 2026-11-27 | Newsroom default reopened | Kate's first 6 list comparisons preferred Haiku with the old prompt (5 of 6) over Sonnet v4 (worst 3 of 6). Default stays Sonnet v4 until round 2 (Sonnet with the old prompt, Sonnet v5) is rated |
| 2026-11-27 | Per-user token budget | 150k → 300k (a safety cap, not a model limit; costs ~$0.015 per user at most on Haiku) |
