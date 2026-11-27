# Open decisions and follow-ups

Kept current during the 2026-11-27 work (part 1 of `docs/architecture-revisit.md` and the steps after
it). "Decision" items need Kate. "Follow-up" items are known work. Closed items move to the bottom with
the date and outcome.

## Decisions for Kate

| # | Decision | Context | Recommendation |
|---|---|---|---|
| D1 | Rate the blind pool | 195 pooled top stories from every newsroom setup, 5 days, 3 beats, setups hidden. Rating page: https://claude.ai/artifact/YWXefuNeTZwhEfPy8kkuz3 (pool with setup labels: `docs/rating/pool_2026-11-27.json`) | Rate when convenient; Claude scores each setup from the saved ratings |
| D2 | Shared store size (25–30 stories per beat) and the per-user pool cap of 80 | A user following all 3 beats has 80 shared stories plus their own finds; the cap cuts digest stories by shared importance before personal scoring (55 of 80 reached scoring in the smoke run) | Kate, 2026-11-27: leave as is for now |
| D3 | Test Exa for full text | Exa is supported by the `search` tool but has no key here | Decide after the story-notes numbers show how much text the fetcher gets |

## Follow-ups

| # | Follow-up | Where |
|---|---|---|
| F1 | Dedupe stories that appear in more than one beat (Nvidia/Huang in physical_ai) before a cross-beat per-user store | newsroom / per-user set |
| F2 | Collect real days going forward (free) so tuning and test days can be split; rebuilt days are thinner (40–60% of items, no front-page signals) | `python -m eval.newsroom_compare collect` |
| F3 | The replay archive on `data/archive-raw` has only Hacker News, arXiv and Hugging Face papers; the Wayback half (outlets, feeds) was never collected | `eval/archive/README.md` |

## Closed

| Date | Item | Outcome |
|---|---|---|
| 2026-11-27 | Newsroom default | Editor newsroom, Sonnet 5.5 (OpenRouter), prompt v4 (`docs/smoke/2026-11-27/editor_multiday/results.md`) |
| 2026-11-27 | Anthropic key use | Avoid; everything that can runs through OpenRouter. `claude_agent` runs are on hold |
