# Open decisions and follow-ups

Kept current during the 2026-11-27 work (part 1 of `docs/architecture-revisit.md` and the steps after
it). "Decision" items need Kate. "Follow-up" items are known work. Closed items move to the bottom with
the date and outcome.

## Decisions for Kate

| # | Decision | Context | Recommendation |
|---|---|---|---|
| D1 | Compare newsroom lists (replaces rating all 195) | 12 blind side-by-side comparisons (4 days × 3 beats) of the three remaining setups' top 5: Haiku with the old prompt, Haiku v4, Sonnet v4. Pick best and worst list. Page: https://claude.ai/artifact/Dppc6cYRVSSbZFogXCXecw (answer key: `docs/rating/lists_key_2026-11-27.json`). The 195-story page stays open for spot checks (https://claude.ai/artifact/YWXefuNeTZwhEfPy8kkuz3) | About 10 minutes; Claude scores the setups from the saved choices |
| D2 | Shared store size (25–30 stories per beat) and the per-user pool cap of 80 | A user following all 3 beats has 80 shared stories plus their own finds; the cap cuts digest stories by shared importance before personal scoring (55 of 80 reached scoring in the smoke run) | Kate, 2026-11-27: leave as is for now |
| D4 | Statements and commentary ("X says Y") | Kate, 2026-11-27: several pooled stories read as opinion, not something happening. v4 skips "opinion pieces without news" but lets a notable person's remarks through (Amodei calling claims "absurd", Aaronson on unreleased breakthroughs). Proposed general rule: a statement is news only when it announces or reveals something (a decision, a plan, new facts); commentary, reactions and predictions rank low or are skipped | Add to the prompt as v5 and re-run the 4 days (~$0.40) |
| D3 | Test Exa for full text | Exa is supported by the `search` tool but has no key here | Decide after the story-notes numbers show how much text the fetcher gets |
| D5 | Story-note model | Haiku: $0.0008 per note, about $0.09 per run of 80 once Google News links resolve; Sonnet about 20× (estimated, not measured). Live notes read well on Haiku (`docs/smoke/2026-11-27/story_notes/`) | Keep Haiku; compare Sonnet on ~10 stories after Google News resolution |
| D6 | Story-note reuse (`story_notes.reuse_ok_hours: 6`) | Story ids persist across runs, so a recent ok note is kept instead of rewritten | Keep; set 0 to rewrite every run |
| D7 | Notes inside the newsroom job vs their own job | A separate job would get its own retries; today a notes failure is logged and never fails the newsroom job | Keep in the newsroom job for now |

## Follow-ups

| # | Follow-up | Where |
|---|---|---|
| F1 | Dedupe stories that appear in more than one beat (Nvidia/Huang in physical_ai) before a cross-beat per-user store | newsroom / per-user set |
| F2 | Collect real days going forward (free) so tuning and test days can be split; rebuilt days are thinner (40–60% of items, no front-page signals) | `python -m eval.newsroom_compare collect` |
| F4 | The first comparison day (Nov 26, 19:59) includes pre-date-fix outputs with months-old lab posts; they're in the 195-story pool, not in the list comparison | rating pool |
| F5 | Re-run the story-notes smoke after Google News resolution merges (15 of 24 notes had no text, 13 because of Google News links) | `scripts/smoke_story_notes.py` |
| F6 | reuters.com returned 401: consider adding it to the paywalled domains | `config/agent.yaml` |
| F7 | Story notes' quote check proves a phrase is in the source, not that the fact paraphrases it faithfully; consider a judge check in eval | eval |
| F3 | The replay archive on `data/archive-raw` has only Hacker News, arXiv and Hugging Face papers; the Wayback half (outlets, feeds) was never collected | `eval/archive/README.md` |

## Closed

| Date | Item | Outcome |
|---|---|---|
| 2026-11-27 | Newsroom default | Editor newsroom, Sonnet 5.5 (OpenRouter), prompt v4 (`docs/smoke/2026-11-27/editor_multiday/results.md`) |
| 2026-11-27 | Anthropic key use | Avoid; everything that can runs through OpenRouter. `claude_agent` runs are on hold |
