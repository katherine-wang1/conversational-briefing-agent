# Smoke: default newsroom → one user's brief (2026-11-27)

Checks the switch to the editor newsroom (`newsroom.impl: editor`, Sonnet 5.5 with the v4 prompt)
against the per-user run. Script: `scripts/smoke_editor_to_brief.py`, in process, without Postgres or
the worker (they only wrap these two calls). Input: the 2026-11-26 22:09 UTC live collection
(`eval/data/newsroom_compare/2026-11-26-late`). User: the synthetic test user in
`tests/unit/cos/fixtures.py` (interests: AI agents and memory, SMR licensing). Search: Tavily. The
Anthropic key was unset.

| Step | Result |
|---|---|
| Newsroom, 3 beats | 30 / 25 / 25 entries, no degradation, 26–33 s per beat |
| Per-user run (C1–C8) | 11 searches, 43 finds, pool of 80, 6 picked, 6 source packs; brief of 4 stories, 20 sentences; 73 s |
| Cost (OpenRouter) | $0.343 in all: `newsroom.editor` $0.325, the per-user roles $0.018 (Haiku) |

Files: `summary.json`, `digest_<beat>.json`, `newsroom_trace_<beat>.json`, `brief.json`,
`brief_script.txt`, `source_packs.json`, `cos_trace.json`, `llm_ledger.jsonl`.

## Problems found (all downstream of the newsroom; they predate the switch)

1. **Google News redirect URLs can't be fetched.** Many digest stories carry only
   `news.google.com/rss/articles/…` links. The fetcher doesn't follow them to the publisher, so the
   story gets no article text. Stories whose only URLs are Google News links: ai 9 of 30, energy 18 of
   25, physical_ai 19 of 25. In this run, one of the two "big news" picks (physical_ai) got no
   passages and was removed from the brief ("no grounded sentences").
2. **Paywalls:** the FT link returned HTTP 403.
3. **Budget:** C6 reached the 90k input-token budget; one story fell back to its lead passage.
4. **Brief text:** the claim check dropped a story's opening sentence (s007), so "The run lasted about
   30 hours…" starts the Fermat's Last Theorem story with no introduction. Two consecutive sentences
   repeat the same fact (the Commerce Department closing the loophole). Part 3 (audio prompt) covers
   the wording; the dropped intro is a C7/C8 interaction.

Item 1 matters most for story notes: without the publisher's URL there's no text to write a note from.
Fix options: resolve Google News links to the publisher URL at collection time, or find the
publisher's article from the title (Google News titles end in " - Publisher") through the `search` tool.
Follow-up: the live `fetch` tool now resolves these links lazily (`app/sources/google_news.py`); see
`../google_news_resolve/results.md` (166 of 166 links resolved; 37 of the 46 stories get article text).
