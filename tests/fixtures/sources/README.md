# Source parser fixtures

Real responses saved on 2026-11-05 with the honest User-Agent
`conversational-briefing-agent/0.1 (research project)`, unless marked SYNTHETIC.

| file | origin |
|---|---|
| `techmeme_front.html` | `https://www.techmeme.com/` (live front page) |
| `techmeme_river.html` | `https://www.techmeme.com/river` |
| `techmeme_archive_challenge_403.html` | `https://www.techmeme.com/261001/p10`: the Cloudflare block page (HTTP 403) we get for dated archive pages |
| `hn_front_live.html` | `https://news.ycombinator.com/` |
| `hn_front_day_2026-10-01.html` | `https://news.ycombinator.com/front?day=2026-10-01` (points are as of 2026-11-05, not as of the day) |
| `hn_algolia_search_by_date.json` | HN Algolia `search_by_date`, stories created 2026-10-01 00:00-01:00 UTC, 5 hits |
| `hn_algolia_item_49916351.json` | HN Algolia `items/49916351` (comment tree) |
| `arxiv_cs_cl.xml` | arXiv API, cat:cs.CL submitted 2026-10-01, first 3 results |
| `hf_daily_papers_2026-10-01.json` | `huggingface.co/api/daily_papers?date=2026-10-01`, **truncated to the first 5 of 50 entries** |
| `openai_news_rss.xml` | `https://openai.com/news/rss.xml`, **truncated to the first 10 of 1359 items** (channel header kept) |
| `verge_ai_atom.xml` | `https://www.theverge.com/rss/ai-artificial-intelligence/index.xml` (Atom) |
| `anthropic_news.html` | `https://www.anthropic.com/news` (generic front-page parser) |
| `article_hf_blog.html` | `https://huggingface.co/blog/allenai/statebench` (body extraction) |
| `wayback_available_hn.json` | `archive.org/wayback/available?url=news.ycombinator.com&timestamp=20261001060000` |
| `SYNTHETIC_wayback_cdx_hn_2026-10-01.json` | **SYNTHETIC test fixture.** Hand-written in the documented Wayback CDX `output=json` format (header row + rows). web.archive.org is blocked by this environment's egress policy, so no real CDX response could be saved. The timestamps and digests are invented; it is only used to exercise the parser and collector logic and is never written to the archive DB or shards. |
