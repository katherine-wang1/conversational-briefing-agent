# Newsroom comparison, first live day: 2026-11-26 (AI beat)

Thanksgiving, so a light news day. Headlines were collected once at 19:34 UTC (4,677 observations from 35
of 36 sources; reddit_ml returned 404), and every arm ran on the same collection with
`python -m eval.newsroom_compare run --beat ai`. Model: Haiku 5.5 through OpenRouter (dev_default)
for classic and editor. **The `claude_agent`, `editor_search` and `eventregistry` arms didn't run:**
`ANTHROPIC_API_KEY` and `EVENT_REGISTRY_API_KEY` weren't set in this session. `editor_search`
therefore ran the same as `editor`, with 0 gap-fill hits.

## Speed, memory, cost

| Arm | Code | Time | Peak memory | Cost |
|---|---|---|---|---|
| classic (original 17 sources) | before today's changes | 145 s | 1,750 MB | $0.017 |
| classic | + concurrency, slim scoring output | 65 s | 1,732 MB | $0.014 |
| classic | + publication-date filter | **25 s** | **426 MB** | $0.013 |
| classic_all (36 sources) | all of the above | 33 s | 490 MB | $0.015 |
| editor (36 sources) | one editor call, no embeddings | 35–39 s | **104 MB** | $0.008 |

Peak memory is the arm's own process (`ru_maxrss`), measured by the harness.

Most of the classic gain comes from the date filter. Live feeds return their whole archive with
`observed_at` set to the fetch time: openai_news returned 1,430 items, hf_blog 915, arXiv about 560 stale
ones. Before the filter those items were embedded and ranked as today's news (two of the old top-3
stories were past model launches). `newsroom.freshness_filter: false` restores the old behaviour.

## Editor story count

With only "at most 40" in the prompt, Haiku returned 30, 15, 21 and 22 stories on four runs over the
same input. Asking for "between 30 and 40" (prompt v2) gave 30 on two runs out of two.

## Top 10 (unrated)

The files in this folder hold all 30 stories per arm, with outlets and evidence. Rating happens in
`python -m eval.newsroom_compare pool` and `rate` once more days and arms are in.

- **classic** (original sources) still ranks several Anthropic front-page posts high (model
  launches, a Claude Code feature, distillation defences). The `frontpage` source type is exempt from
  the date filter, and some of those posts may not be from today. Single-outlet stories dominate.
- **classic_all** and **editor** both lead with stories covered by several outlets: Nvidia halting
  server shipments to Southeast Asia (FT, NYT), the Anthropic IPO, and the Aaronson post. The editor
  also surfaces the US remote-access chip-curb change (WSJ), Huang's reinstatement (CBS, Reuters,
  TechCrunch), SAP's job cuts (Bloomberg, FT) and ASML / the Netherlands (Bloomberg).

## Known issues

- The `frontpage` exemption in the date filter is meant for aggregator front pages, but it also
  covers lab news index pages (anthropic_news, meta_ai_blog).
- In classic, Google News items count as `news.google.com` in the outlet list; the editor counts the
  real outlet.
- The editor cuts candidates at 400 (335 were cut today, mostly weak Google News topic-search
  items).

**Second run, all beats, with Event Registry and the date-filter fix:** `../compare_all_beats/results.md`.
