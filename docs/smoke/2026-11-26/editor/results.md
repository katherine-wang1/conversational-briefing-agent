# Editor newsroom smoke run: `ai` beat, live sources (2026-11-26 19:29 UTC)

Command: `.venv/bin/python scripts/smoke_editor_newsroom.py --beat ai --out docs/smoke/2026-11-26/editor`
(`newsroom.editor` -> dev_default `anthropic/claude-haiku-5.5`, reasoning off). Files: `digest_*.json`,
`newsroom_trace_*.json`, `summary.json` (per-source counts, step details), `llm_ledger.jsonl`.

## Numbers

| | |
|---|---|
| Sources | 36 configured; 34 returned items (reddit_ml 429, import_ai 403) |
| Collection (concurrent, 12 at a time) | 5.7 s |
| Newsroom run (all steps) | 30.0 s, of which the one model call 28.9 s |
| Observations in | 4,689 |
| Dropped: outside the run window (observed_at) | 591 (arXiv announcements older than 24 h) |
| Dropped: freshness rule, stale `published_at` | 3,298 (openai_news 1,430, hf_blog 915, arXiv 563, deepmind_blog 100, ...) |
| Dropped: undated over the per-source cap | 0 |
| Observations kept -> items (canonical URL) | 800 -> 788 (583 are Google News links) |
| Candidates after title dedupe | 764 (23 multi-item) |
| Shown to the editor (cap 400, strongest signal first) | 400, in 1 call |
| Editor output | 45 stories covering 177 candidates; 0 invalid stories, 2 repeated ids dropped |
| Digest entries | 30 (digest_size 30, none below the floor of 20) |
| Tokens / cost | 33,203 in / 7,715 out, **$0.0072** |

For comparison, the classic newsroom took about 2.5 min on 2026-11-05 (mostly about 51k output tokens
of `newsroom.score` over 10 calls), and clustering merged 8 of 98 stories.

## Top 15 headlines

| # | imp | rules | domains | Headline | Evidence |
|---|---|---|---|---|---|
| 1 | 88 | 54.2 | 5 | Anthropic files for, prices and closes its initial public offering, then trades as a public stock | primary source (anthropic.com); covered by NYT, Bloomberg, FT, Reuters, WSJ |
| 2 | 86 | 48.3 | 4 | Nvidia halts AI server shipments to Malaysia, Singapore, Thailand and Indonesia as US tightens export controls | covered by FT, Reuters, NYT, Forbes; techmeme #1 |
| 3 | 84 | 38.8 | 5 | US closes remote-access loophole and restricts Chinese access to Nvidia chips, hitting China's AI development | covered by WSJ, CNBC, Reuters, Businesskorea |
| 4 | 82 | 49.8 | 1 | Scott Aaronson says leading AI labs hold unreleased algorithmic breakthroughs that could threaten deployed cryptosystems | hn_front #17 1907pts; techmeme #2 |
| 5 | 80 | 41.1 | 1 | Anthropic releases Claude Fable 5.2, Mythos 5.2, Haiku 5.5, Opus 5.5 and Sonnet 5.5 model family | primary source (anthropic.com) |
| 6 | 70 | 37.1 | 3 | SAP weighs at least 10,000 job cuts to remake its software business for the AI era | covered by Bloomberg, FT, Reuters; techmeme #7 |
| 7 | 68 | 39.7 | 3 | Z.AI's GLM-5.5 flagship reportedly delayed as US curbs and distillation crackdown bite | techmeme #3; Reuters-sourced |
| 8 | 66 | 36.4 | 3 | Apple pulls MiniMax's Talkie chatbot from the App Store worldwide over sexualised content | Reuters; techmeme #14 |
| 9 | 64 | 35.0 | 4 | S&P considers downgrading Nvidia credit over governance and key-man risk | covered by TechCrunch, The Information, Moody's commentary |
| 10 | 63 | 40.1 | 1 | Meta launches Muse Spark 1.1, Muse Image and Muse Video models | primary source (meta.com) |
| 11 | 62 | 39.8 | 3 | OpenAI merges ChatGPT Chat and Work with a single usage meter; GPT-6.2 billing error corrected | TechCrunch; techmeme #4 |
| 12 | 60 | 20.8 | 1 | Bank of England's Bailey says unreleased AI models raise encryption concerns; BoE cuts bank capital requirements | FT coverage |
| 13 | 58 | 27.3 | 1 | Microsoft says China and North Korea state hackers use AI pentesting tool ARTEX | techmeme #8; The Record |
| 14 | 57 | 35.8 | 3 | Pentagon discussed Defense Production Act to pressure Nvidia's board to reinstate Jensen Huang | WaPo; techmeme #19; Reuters |
| 15 | 55 | 40.4 | 1 | Anthropic publishes a wave of policy, science, partnership and governance announcements | primary source (anthropic.com) |

## Observations

- The freshness rule does most of the filtering: 70% of observations were feed archives (openai_news,
  hf_blog, deepmind_blog) or arXiv papers older than the window. Without it, months-old posts would
  have filled the candidate list.
- Grouping happens in the editor call (177 candidates into 45 stories), not in the title dedupe (23
  merges). Top stories have 3-5 independent domains, versus mostly single-source stories in the
  classic run.
- Weak spots: #15 lumps several separate Anthropic front-page posts into one "wave" story; #12 is
  close to #4 (same theme, different speaker), which is arguably correct under the same-event rule.
  The model returned 45 stories when asked for at most 40 (harmless: the digest takes 30).
- The 400-candidate cap cut 364 weak candidates, mostly Google News topic-search results from small
  outlets.
