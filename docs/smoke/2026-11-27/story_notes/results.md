# Shared story notes: live smoke run (2026-11-27)

Code: `app/agents/newsroom/notes.py` (`write_story_notes`), prompt `newsroom.story_note` v2, role on
`dev_default` (Haiku 5.5, reasoning off). Script: `scripts/smoke_story_notes.py`.

## Method

- Input: the committed real digests `docs/smoke/2026-11-27/editor_to_brief/digest_{ai,energy,physical_ai}.json`
  (editor newsroom, 2026-11-26 22:09 UTC collection). The first 8 entries of each beat (the top 8 by the
  editor's importance), 24 stories in all.
- Same code as the newsroom job: live `fetch` tool (httpx + trafilatura, in-memory document cache, the
  current backend, which does **not** resolve Google News links), model calls through the Router
  (OpenRouter), config `story_notes` from `config/agent.yaml` (3 sources per story, 6 fetch attempts,
  6000 chars per source, 6 stories at once, quote check on).
- Outputs here: `notes.json` (the 24 `StoryNote`s), `notes.md` (every note, readable), `outcomes.json`
  (per story: seconds, tokens, facts proposed and dropped, redirects skipped), `summary.json` (per beat),
  `llm_ledger.jsonl` (cost per call as reported by OpenRouter). `prompt_v1/` is the same run with prompt v1
  (kept for the comparison below).

## Numbers

| | ai | energy | physical_ai | all |
|---|---|---|---|---|
| ok | 1 | 1 | 1 | **3** |
| partial | 4 | 1 | 1 | **6** |
| no_text | 3 | 6 | 6 | **15** |
| facts | 46 | 20 | 15 | **81** |
| cost (USD) | 0.0041 | 0.0016 | 0.0015 | **0.0072** |
| wall time (s) | 7.1 | 6.3 | 7.9 | 21 |

- **Status:** 3 ok, 6 partial, 15 no_text. Every story got a note (no model failure in the final run).
- **Facts per note with text:** mean 9.0 (min 5, max 12), 81 facts in 9 notes. Entities 8.8 per note,
  open questions 2.4.
- **Citation validity:** 81 / 81 facts cite only sources fetched ok (enforced in code; checked again from
  `notes.json`). 83 facts proposed, **2 dropped** by the quote check (quote not found in the cited source).
  No fact cited two sources: the model attributes each fact to one source even when two say it.
- **Sources:** 66 fetches tried: 14 ok, 12 paywalled (ft.com 7, nytimes.com 2, wsj.com, theinformation.com,
  bloomberg.com: HTTP 401/403), 40 failed (39 Google News redirects with 0 chars of text, 1 reuters.com
  HTTP 401). A further 77 Google News links were **not fetched**: once one redirect in a story came back
  unresolved, its other redirects were skipped. Mean ok sources per note with text: 1.6 (of 3 wanted).
- **Google News is the main cause of missing text.** Of the 15 no_text stories, 10 have *only* Google
  News links in the digest, 3 have Google News links plus paywalled or 401 sources, and 2 (both energy,
  one ft.com link each) have no Google News link at all. All 6 partial notes also left Google News links
  unread. So 13 of 15 no_text notes, and all partial ones, could gain text once the fetch backend resolves
  Google News links (whether they do depends on which outlet each link resolves to; some will be paywalls).
  physical_ai and energy digests are mostly Google News links, which is why they fare worst.
- **Time per story:** median 6.3 s with text (max 7.9 s; the model call is ~4.7 s of it), 0.8 s for
  no_text (fetch failures only). Wall time per beat (8 stories, 6 at once): 6–8 s; whole run 21 s.
- **Cost:** 9 model calls, $0.0072 total, **$0.0008 per note with text** (mean 3,449 tokens in, 1,188 out).
  No call for no_text stories.

### Extrapolation to one run (80 notes: ai 30, energy 25, physical_ai 25)

| Scenario | Notes with text | Tokens in per note | Cost per run (Haiku) | Same with Sonnet 5.5* |
|---|---|---|---|---|
| Today's fetch backend (this run: 9/24) | ~30 | ~3.5k | **~$0.025** | ~$0.55 |
| Google News links resolved (assume ~85% with text, ~2.5 sources each) | ~68 | ~6.5k | **~$0.09** | ~$1.70 |

\* Sonnet estimated from the newsroom.editor ledger rows of the same day (about $2.1 per M input and
$9.4 per M output tokens, as OpenRouter reported; reasoning tokens included), i.e. ~$0.025 per note.
Wall time at concurrency 6: about 30–45 s per run (today) to ~90 s (all stories with text), roughly
`ceil(stories / 6) × 7 s`. Fetches: ~220 per run, under the 400 budget. With `reuse_ok_hours: 6` and
stable story ids, a refresh run 1–3 h later rewrites only new stories and earlier partial/no_text ones.

## Quality (read through `notes.md`)

- Facts are specific and sourced (names, numbers, dates, attributions such as "according to the FT
  report"). Context uses background knowledge as intended (what Nvidia, the Entity List or a Series A is).
- **Prompt v1 leaked headline claims** (see `prompt_v1/notes.md`): the Aaronson note's *why it matters*
  and *open questions* mentioned the Bank of England governor, who appears only in the editor's headline,
  not in the fetched blog post. v2 says the headline and summary only identify the story and that context,
  why it matters and open questions add no news beyond the facts. In v2 the Bailey claim is gone; one open
  question still refers to a headline claim ("whether Vance's comment ... is accurate") as unknown, which is
  harmless but shows the headline still steers the model.
- **Output length:** with `max_output_tokens: 1500`, 1 of 9 calls (12 facts with quotes) hit the limit and
  produced no note (an intermediate run, not kept). Raised to 2500; in the final run the longest reply was
  ~1,400 tokens.
- The same event can be a story in two beats (Huang's reinstatement is in ai and physical_ai): two notes,
  two calls.

## Caveats

- One day, 24 stories, the top 8 per beat only. The live fetch backend here does not resolve Google News
  links; that work is on another branch, and these numbers should be re-run after it merges.
- Quote check: the model copies quotes well (2 of 83 dropped); the check proves a phrase is in the source,
  not that the fact paraphrases it faithfully.
- Costs are OpenRouter's reported `cost_usd`; Sonnet figures are an estimate, not measured on this task.
- Total live spend for this work (including a 1-story trial, the v1 run and the truncated intermediate
  run): about $0.025.
