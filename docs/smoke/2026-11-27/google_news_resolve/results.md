# Live check: Google News links → publisher URLs → article text (2026-11-27)

Follow-up to problem 1 in `../editor_to_brief/README.md`: many digest stories carry only
`news.google.com/rss/articles/CBMi…` links, which the fetch tool couldn't follow. This checks the new
resolver (`app/sources/google_news.py`) and the `fetch` tool's live backend, which now resolves such
links before fetching.

Script: `scripts/check_google_news_resolve.py --all --search-sample 10`, which also runs a story-level
pass (below). Title and outlet hints come from the 2026-11-26 22:09 UTC collection's
`observations.jsonl`. Per-link results: `results.json`; a rerun without the search sample gave the same
counts. No model calls; the Anthropic key was unset.

## Input

The committed digests `../editor_to_brief/digest_{ai,energy,physical_ai}.json`. Stories whose links are
all Google News links: ai 9, energy 18, physical_ai 19 = **46 stories, 166 links**.

- **Set A** (46): each story's first link, the one C6 (per-user source packs) and story notes fetch
  first. Resolved, then fetched through the `fetch` tool.
- **Set B** (120): the stories' other links. Resolved only (and used in the story-level pass).

## Resolution

| | Set A | Set B | All |
|---|---|---|---|
| links | 46 | 120 | 166 |
| offline (id decodes to the URL) | 0 | 0 | 0 |
| batchexecute (article page signature + RPC) | **46** | **120** | **166 (100%)** |
| search fallback needed | 0 | 0 | 0 |
| failed | 0 | 0 | 0 |
| wall time (2 page requests at a time, 10 ids per RPC) | 3.7 s | 8.2 s | 11.9 s |

Time per link: **0.07 s** amortised over the batch; **≈0.2 s** for one link on its own (0.6 s for the
first, with connection set-up). Each link costs one GET of the Google News article page (~590 KB
uncompressed, gzip on the wire) and a share of one POST. No link failed in any of three full runs
(3 × 166 page loads at concurrency 2; the third took 16.2 s, 0.10 s per link). Retries weren't logged, so a retried 429 can't be ruled out. (`news.google.com/articles/<id>` without `/rss/` did return Google's 429 "sorry"
page from this machine in a manual probe; the resolver uses `/rss/articles/<id>` and only falls back to
the other form.)

Offline decoding resolved nothing: all 4,457 distinct Google News ids in the five collections under
`eval/data/newsroom_compare/` are new-style (`AU_yqL…` tokens). The offline path is kept for older ids
and costs nothing.

### Search fallback, measured on its own

On 10 set-A links that batchexecute had resolved, the fallback alone (Tavily; query
`"<title> site:<outlet domain>"`, then the bare title; only hits on the outlet's domain; accept at title
similarity ≥ 0.85):

| found | agrees with batchexecute | wrong | not found | searches |
|---|---|---|---|---|
| 5 | 5 | 0 | 5 (best on-domain similarity 0.34–0.72) | 16 |

So: precise but low recall, useful only as a backstop when Google refuses the RPC. A first version
(site-query only) found 3 of 10; adding the bare-title query found 2 more.

## Fetching the publisher URLs (set A, through the `fetch` tool)

| outcome | links | notes |
|---|---|---|
| article text (≥ 400 chars) | **29** | |
| blocked (HTTP 401 / 403) | 11 | energynow.com 4 (Cloudflare), axios.com 2, reuters.com 2 (401), wsj.com, bloomberg.com, bizjournals.com |
| short or empty text | 4 | asia.nikkei.com (paywall stub, 379 chars), autonews.com (213), marketscreener.com (0), digg.com (85) |
| failed | 2 | aithority.com (read timeout; it fetched fine in the first run), suaralandak.co.id (HTTP 520) |

Paywalls and bot blocks are reported, not bypassed (no mirrors, no UA tricks).

### Story level (what C6 does: try the story's next link when one fails, up to 6)

| stories | text from the first link | text from a later link | no text |
|---|---|---|---|
| 46 | 29 | 8 | **9** |

Before this change all 46 got no article text. The 9 left: single-outlet stories behind a paywall or
bot block (Reuters / MiniMax, WSJ, Bloomberg, bizjournals, 4 × energynow.com, Reuters video).

## Examples

| beat | Google News title | publisher URL (resolved) | fetch |
|---|---|---|---|
| ai | Anthropic built a honeypot to see if AI agents will break out - Malwarebytes | `https://www.malwarebytes.com/blog/ai/2026/11/anthropic-built-a-honeypot-to-see-if-ai-agents-will-break-out` | text, 3,482 chars |
| ai | EXCLUSIVE: Data from Chinese AI firm MiniMax accessed by unidentified parties… - Reuters | `https://www.reuters.com/world/data-chinese-ai-firm-minimax-accessed-by-unidentified-parties-researcher-says-20…` | HTTP 401 |
| energy | Norway pauses data center connections as requests outgrow it - Rystad Energy | `https://www.rystadenergy.com/insights/norway-pauses-data-center-connections-as-r…` | text, 3,485 chars |
| energy | Why the Global Solar Buildout Is Starting to Slow Down - Bloomberg | `https://www.bloomberg.com/news/articles/2026-11-26/why-the-global-solar-buildout…` | HTTP 403 |
| energy | Japan's JERA aims to build Anthropic, Meta data centers near… - Nikkei Asia | `https://asia.nikkei.com/business/technology/artificial-intelligence/japan-s-jera…` | 379 chars (paywall stub); a later link (cryptobriefing.com) had text |
| physical_ai | Waymo halts freeway rides after California DMV orders ban… - Mashable | `https://mashable.com/tech/waymo-freeway-rides-safety-california-dmv-ban` | text, 3,338 chars |
| physical_ai | Volvo puts humanoid robots to work at Belgium plant… - Automotive News | `https://www.autonews.com/volvo/ane-volvo-humanoid-robots-ghent-2027-1126/` | 213 chars; a later link (mlq.ai) had text |
| physical_ai | Lute raises $20 million to give robots a sense of touch - Robotics & Automation News | `https://roboticsandautomationnews.com/2026/11/26/lute-raises-20-million-to-give-…` | text, 3,734 chars |

## Cost

No model calls. Tavily: 33 basic searches across this check and its probes (16 in the final run). That
is under $0.30 at Tavily's pay-as-you-go rate, or free-tier credits. Google requests are free.

## Caveats

- **Undocumented RPC.** batchexecute `Fbv4je` / `garturlreq` is Google News' internal endpoint (the
  approach of the open-source `googlenewsdecoder`). Google can change the page attributes or the RPC
  without notice. The resolver then returns `failed` (or a search match): it never returns a Google
  URL or a guess. `tests/unit/sources/test_google_news.py` pins the formats seen today.
- **Rate limits.** One run's ~170 page loads went through. A multi-user deployment from one cloud IP
  could hit Google's "sorry" page. Mitigations in place: concurrency 2, retries with backoff, a cache
  (and the fetched document is cached under both URLs). Lazy resolution keeps the volume to stories
  that need text.
- **Search fallback in the fetch path needs a title.** The fetch tool only gets a URL, so the
  fallback runs there only when a title hint has been registered (`GoogleNewsResolver.hint`). Today
  no caller registers one. Today that doesn't matter (batchexecute resolved everything), but it does
  if Google blocks the RPC.
- **Stale items in the energy digest.** Three of energy's Google-News-only stories resolve to articles
  from June, July and September 2026 (energynow.com re-posts). That's a freshness problem in the
  newsroom (`published_at` from the feed), not a resolution problem.
- Single run, one machine, one time of day. Fetch outcomes vary a little between runs (aithority.com
  timed out in one of two runs).
