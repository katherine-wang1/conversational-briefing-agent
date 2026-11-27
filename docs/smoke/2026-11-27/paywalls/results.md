# Paywalled and blocked stories: live check, 2026-11-27

Kate's question was whether we should drop NYT, Bloomberg, FT, WSJ and Reuters stories just because we
can't fetch their text. We no longer have to. This check covers both fixes: publisher snippets used as
summary-level sources, and one search per story for free coverage of the same event. Paywalls are never
bypassed. A paywalled URL is tried once with the normal fetcher and recorded as paywalled. There are no
mirrors, archives, logins or user-agent tricks.

Script: `scripts/smoke_paywalls.py`. Data: the collection `e2e-2026-11-27b` (6,557 headlines, collected
06:36 UTC; read only). Newsroom: the default config (`editor`, Sonnet 5.5, prompt `newsroom.editor_v4`).
Story notes: Haiku 5.5. Search: Tavily. All model calls go through OpenRouter, and no Anthropic key was set.

Files in this folder:

- `digest_<beat>.json`: the newsroom's digests, which now include `source_snippets`.
- `outcomes.json`: one row per story. It has the status before and after, how the story was rescued, the
  free-coverage search (query, accepted hits and rejected hits with reasons), the sources and the facts.
- `notes.json`: the StoryNote objects.
- `summary.json`: totals per beat.
- `run1_untuned/`: the first pass of story notes on the same digests, before the thresholds were tightened
  (see "Tuning").

## Result

The "before" column is what the previous code writes for the same digests: the same listed-URL fetches,
with no free coverage and no snippets. With no listed source fetched, the old status was `no_text`.

| Status (80 stories) | Before | After |
|---|---|---|
| ok (enough fetched articles) | 41 | 45 |
| partial (fewer articles; may add snippets) | 18 | 21 |
| summary (no article text, only the publishers' own headline and summary) | – | 11 |
| no_text (headline only, no facts) | **21** | **3** |

| Beat | Digest | no_text before | Free coverage | Snippet only | Still no_text | Searches |
|---|---|---|---|---|---|---|
| ai | 30 | 11 | 6 | 5 | 0 | 11 |
| energy | 25 | 10 | 1 | 6 | 3 | 10 |
| physical_ai | 25 | 0 | 0 | 0 | 0 | 0 |
| **all** | 80 | **21** | **7** | **11** | **3** | **21** |

This newsroom run produced a different digest from the e2e run's (`docs/e2e/2026-11-27/run_log.md`: 62
with text, 18 `no_text`), but the pattern is the same. Here 59 stories had text before. After the fixes,
66 have article text and 77 have citable text of some kind.

Three stories are still `no_text`. They are energy stories whose only source is a Google News item, and
those carry no summary (the feed's description is just the headline plus the outlet):

- "PJM proposes plan…": the search found no acceptable hit.
- "Massachusetts to require new data centers…": the accepted hit was a Google redirect, and fetching it
  returned HTTP 403.
- "Pacific Fusion granted radioactive materials license…": the accepted bizjournals hit returned HTTP 403.

physical_ai has no rescues because its digest is all Google News links plus robotics247. Its links resolve
and fetch, so no story there lost its text.

### Free coverage found (all 7 checked by hand: same event, no wrong match)

| Story (listed source that failed) | Free source used | Match |
|---|---|---|
| AI agents used in five cyber attacks in Switzerland (Reuters via Google News, 401) | ca.news.yahoo.com (Reuters copy) | title 100, 1/1 entities |
| Bank of England warns of financial risks from AI borrowing (Reuters 401, FT 403) | businesstimes.com.sg | title 100, 1/1 |
| IAEA working with Russia, Ukraine on Zaporizhzhia ceasefire (Reuters 401) | internazionale.it (Reuters wire page) | title 100, 2/2 |
| Dutch government to resist US MATCH Act on ASML (Bloomberg 403) | taipeitimes.com | title 98, 2/3 |
| JERA in talks with Anthropic and Meta on data centers (Nikkei login wall, Reuters 401) | tradingview.com news | title 84, 3/3 |
| Los Angeles blocks AI features as Google threatens schools (AP via Google News, 403) | ottumwacourier.com (AP copy) | title 100, 2/2 |
| Dutch groups ask court to halt Meta smart glasses sales (Reuters via Google News) | shattered.io | title 90, 1/1 |

Each free source was then used like any other source. For example, the ASML note now carries "Dutch
Minister Sjoerd Sjoerdsma told parliament the government opposes the extraterritorial scope of the
proposed MATCH Act", which was not in any headline.

### Snippet-only notes (11): what they look like

Every fact is attributed to the outlet. Each one quotes the publisher's own headline or summary, and code
checked each quote against that text. No facts from snippet sources were dropped. The 11 notes carry 33
facts in total, about 3 each.

> **Backlash over AI power costs threatens $67bn NextEra-Dominion deal** (status summary)
> - The Financial Times reports that the $67bn NextEra-Dominion deal faces fierce opposition.
> - The Financial Times reports that anger over soaring electricity bills is spilling into Wall Street dealmaking.

> **SAP preparing to cut at least 10,000 jobs** (status summary; two Bloomberg feed items)
> - Bloomberg reports that SAP is preparing a plan to eliminate at least 10,000 positions.
> - Bloomberg reports, citing people familiar with the matter, that the restructuring would affect around 9% of SAP's global workforce.

Other snippet-only notes: the NYT story on the bipartisan push to regulate AI in Congress, the WSJ story on
the giving pledge, Bloomberg's story on a Chinese hedge fund star's AI kernel, and the FT stories on the UK
grid in the cold snap, Texas data-centre permits, nuclear life extensions and China's solar and wind
makers.

## Tuning (first pass → final)

The first pass (`run1_untuned/`) used min title similarity 50, with the entity requirement capped at the
number of entities the story has. It rescued 2 stories by free coverage. It also **accepted** four hits
that were different stories, though none of them was used because their fetches failed:

- "Bowen's anti-nuclear power spin…" (title similarity 51) for the story on nuclear plants staying open
  longer.
- Two other Massachusetts data-centre stories (63 and 62) for the clean-energy rule.
- A blog on OpenAI's IPO (56) for the wealth-sharing story.

The first pass also showed three problems in the acceptance code:

- Tavily's news results are mostly `google.com/url?q=CAES…` redirects whose target is encoded.
- The newsroom sometimes lists the reporting outlet ("Bloomberg") as an entity.
- A Google redirect got the "primary source" flag on the story about Google.

The final rules:

- **Title:** rapidfuzz, the mean of token_set and token_sort ratio, at least 60. At 80 or more, one
  named story entity is enough. Below 80, at least 2 entities must be named (figures from the headline,
  such as "10,000", count as entities). A story with a single entity therefore needs a strong title match.
- **Entities:** the newsroom's entities, minus generic ones (countries, "AI") and outlet names.
- **Domain:** not paywalled (`newsroom_editor.paywalled_domains`, now with reuters.com, axios.com and
  nikkei.com), and not an aggregator, social site or the search provider's own pages. A URL the story
  already tried is skipped.
- **Window:** published from the story's first-seen time minus 36 h, until now. Undated hits are
  rejected.
- **Redirect links:** they are fetched like any link, and then judged by the URL they land on. Landing on
  a paywalled domain is rejected. Redirects never count as primary.
- **Fetched page:** it must name the story's entities, which catches error pages and off-topic pages.
- **Order:** primary-looking sources go first (the company's own site or newsroom, a .gov or regulator
  site, press-release wires, configured primary sources), then the best match. At most 2 fetches per
  story.

The final run was made before two small fixes, which were then committed with tests: the redirect
"primary" flag, and naming the outlet of a found source (for example yahoo.com instead of the full
host). The only effect on this run would be that the Los Angeles story tries the AP copy before a failed
Google redirect.

## Cost and time

| | Time | OpenRouter | Tavily searches |
|---|---|---|---|
| Newsroom, 3 beats (run 1) | 85 s | $0.314 | 0 |
| Story notes, first pass (run 1) | 106 s | $0.058 | 21 |
| Story notes, final (run 2, same digests) | 93 s | $0.060 | 21 |
| **Total for this check** | | **$0.431** | **42** |

The Google News resolver needed no search fallback in either run, because all of its lookups went
through batchexecute. Free coverage costs one search per story whose listed sources all fail, at most 25
per digest run. Here that was 21 searches a day for three beats. Snippet sources add no calls: the model
call they feed would have run anyway for a story that has text.

## Open decisions and follow-ups

1. **Google redirects in Tavily news results.** Across both runs, 120 of 211 hits (57%) were
   `google.com/url` links, and 22 of them were fetched. Most fetches returned HTTP 403 or 429, or a
   92-character interstitial. 4 reached a publisher's page with text. 3 of this run's 7 rescues came
   through a redirect: the Taipei Times (ASML), TradingView (JERA) and the Yahoo copy of the Reuters
   Swiss story. Options:
   - keep them, since they produced those rescues;
   - let the free-coverage search use a provider that returns direct URLs (Exa or Brave);
   - skip them (one line in `NOT_SOURCES`), which would also stop the repeated requests to Google.
2. **Syndicated wire copies** (Yahoo carrying Reuters, local papers carrying AP, internazionale.it's
   Reuters page) are legitimate free republications. Today the note names the host ("yahoo.com"). A
   follow-up could detect the byline and say "Reuters, via Yahoo News".
3. **Low-grade free sites.** shattered.io and tradingview's news feed were correct this time, but they
   are content farms or aggregators. A small quality allowlist or blocklist may be worth adding once more
   days are in.
4. **C6 re-searches.** The per-user pack fallback runs its own search, charged to the user's 12-search
   budget. When a shared note already found a free source, C6 could reuse it from the note store instead.
   Not done here.
5. **The brief's attribution rule.** It is added to the C7 payload only for stories that have
   `publisher_summary` passages. It is not in the system prompt file, because `cos.write_brief` is
   budget-tight: the existing write-reserve test fails with about 30 more prompt tokens. So
   `cos.write_brief` stays at v2. `newsroom.story_note` (v3) and `voice.turn` (v4) were bumped.
6. **The classic newsroom implementation** (`newsroom.impl: classic`) does not fill `source_snippets`. Only
   the editor does.

## A licensed full-text source (note for Kate; not integrated)

- **Event Registry / NewsAPI.ai** is already evaluated in `docs/sources/eventregistry.md`. Its plans page
  says article bodies are included at no extra token cost. Plans run from free (2,000 tokens, last 30
  days) to $90 for 5K tokens. Whether it carries full text for hard-paywall outlets (FT, Bloomberg, WSJ)
  and whether its terms allow re-use in a product is unverified. It is the cheapest thing to test next.
- **NYT Developer APIs** (Article Search, Top Stories) are free with a key. They return the abstract and,
  for Article Search, the lead paragraph: licensed summary-level text for NYT stories, not full text.
  Check the current terms before use.
- **The Guardian Open Platform** returns full article text with a free developer key (non-commercial
  terms).
- **Reuters, AP, Dow Jones (Factiva) and LexisNexis** license full text only under enterprise
  agreements. That is out of scope for a prototype.
