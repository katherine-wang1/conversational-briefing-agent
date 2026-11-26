# Event Registry (newsapi.ai): datasheet

Written 2026-11-26 for the newsroom comparison (docs/architecture-revisit.md, "buy ranking and
clustering from a provider?"), first from the official Python SDK (github.com/EventRegistry/event-registry-python,
cloned 2026-11-26), the newsapi.ai web app's own API calls, and the plans page. **Checked against the
live API on 2026-11-26** (free-plan key): everything formerly marked *(assumed)* is now marked
*(confirmed live)* or corrected. See "Live check, 2026-11-26" at the end.

Code: `app/sources/eventregistry.py` (headline source type `eventregistry`, and `eventregistry_ranked`).
Key: `EVENT_REGISTRY_API_KEY` (the official SDK reads `EVENTREGISTRY_API_KEY`; we don't). Without a key
every call raises `ToolError("unavailable")`.

## What it is

Event Registry crawls news sites, then clusters articles about the same real-world event into
**events** (across languages). An event has a title and summary (taken from one "info article" per
language), a date, concepts (Wikipedia-URI entities), categories, and an article count. Ranking by
**size** (article count) is their stand-in for importance; that is the ranking we compare against.

## Endpoints we use

| Call | Request | Notes |
|---|---|---|
| Events search | `POST https://eventregistry.org/api/v1/event/getEvents`, JSON body | The SDK sends the same parameters to `/api/v1/event` with `"action": "getEvents"`; the newsapi.ai web app uses the `/event/getEvents` path. Both carry `apiKey` in the body. |
| Articles of one event (optional) | `POST https://eventregistry.org/api/v1/event/getEvent` with `eventUri`, `resultType: "articles"` | Only when `params.top_articles > 0`; one call per event *(confirmed live: this path works, 1 token per call; the server logs it as an `articleSearch`)*. |
| Breaking events (not used) | `POST /api-c/v1/event/getBreakingEvents` | Returns `breakingScore`; an alternative "what's hot now" ranking worth a look later. |

### getEvents request fields (as we send them)

| Field | Value | Source |
|---|---|---|
| `apiKey` | key | SDK `jsonRequest` |
| `resultType` | `"events"` | SDK (`RequestEventsInfo`) |
| `keyword`, `keywordOper`, `keywordLoc` | list, `"or"`/`"and"`, `"body"`/`"title"`/`"body,title"` | SDK `QueryEvents` |
| `conceptUri`, `conceptOper` | Wikipedia URIs, `"or"` | SDK |
| `categoryUri`, `categoryOper` | e.g. `news/Business`, `"or"` | SDK |
| `ignoreKeyword` | list | SDK |
| `lang` | `"eng"` (ISO 639-3) | SDK |
| `dateStart`, `dateEnd` | `YYYY-MM-DD` (UTC dates; **day granularity only**) | SDK `encodeDate` |
| `minArticlesInEvent` | int | SDK |
| `eventsSortBy`, `eventsSortByAsc` | `"size"`, `false` (also `rel`, `date`, `socialScore`*) | SDK; *socialScore is no longer collected, the server ignores it |
| `eventsCount`, `eventsPage` | ≤ 50 per page, from 1 | SDK (raises above 50) |
| `includeEventInfoArticle` | `true` | SDK `EventInfoFlags` (default false) |
| `forceMaxDataTimeWindow` | `31` | SDK sets it when the archive must not be used; we set it for recent windows |

Defaults the server applies without flags (per `EventInfoFlags`): title, summary, articleCounts,
concepts, categories, location, date are included.

### getEvents response fields (what we read)

`{"events": {"results": [...], "totalResults": n, "page": 1, "count": c, "pages": p}}`; errors come back
as HTTP 200 with `{"error": "..."}` (we raise `unavailable`). Per event:

| Field | Shape | Used as |
|---|---|---|
| `uri` | `"eng-11966764"` | `external_id`, `extra.event_uri` |
| `eventDate` | `"2026-08-31"` | `published_at` fallback (midnight UTC), `extra.event_date` |
| `totalArticleCount` | int | `extra.article_count` (rank key) |
| `articleCounts` | `{"eng": 314, ...}` | `extra.article_counts` |
| `title`, `summary` | `{"eng": "..."}` | `title`, `snippet` |
| `concepts` | `[{uri, type, score, label: {eng}}]` | not mapped yet |
| `categories` | `[{uri, label, wgt}]` | `extra.categories` (top 3) |
| `location`, `sentiment`, `socialScore` | | not mapped |
| `infoArticle` *(confirmed live: one article object per language)* | `{"eng": {uri, lang, isDuplicate, date, time, dateTime, dateTimePub, dataType, sim, url, source: {uri, dataType, title}}}`; no `title` or `body` in it | `url` (top article), outlet (`source.uri`, a domain such as `bbc.com`), `published_at` |
| `wgt`, `relevance` | int | not mapped (`wgt` equalled `totalArticleCount` in the live sample) |

Event articles (`getEvent`, `resultType: articles`): `{"<eventUri>": {"articles": {"results": [...]}}}`;
each article has `uri, url, title, body, date, dateTime, dateTimePub, source{uri,title}, lang,
isDuplicate, ...`. Sort options: `cosSim` (closest to the event centroid, default), `date`,
`sourceImportance`, `sourceAlexaGlobalRank`, ... We ask for `articleBodyLen: 300` and keep the excerpt.

## Plans and limits (newsapi.ai/plans, read 2026-11-26)

| | Free | Paid (monthly) |
|---|---|---|
| Price / tokens | $0, **2,000 tokens one-time**, never expire | 5K $90, 10K $150, 20K $220, 50K $400, 100K $700, 200K $1,300, 500K $3,000 |
| Data | **last 30 days only** | historical since 2014 from the 5K plan |
| Search cost (recent, ≤ 30 days) | article search 1 token, **event search 5 tokens** | same |
| Search cost (archive) | n/a | articles 5 tokens per year searched; events 20 tokens per year |
| Article bodies | **full text included, no extra tokens** | same |
| Support | docs + sandbox only | |

So the free plan buys about **400 event searches** (e.g. 3 beats × 1 search/day ≈ 4 months; each
`top_articles` follow-up costs 1 token per call, *(confirmed live)*). The comparison arm uses 5 + 10 =
15 tokens per beat and run, so 45 a day for three beats: about 40 days on the free plan. Concurrency: at most 5
simultaneous requests per key (HTTP 429 above that, per the SDK). HTTP codes: 401 bad key or out of
tokens, 403 account/IP disabled or tokens used up, 204 no data, 429, 500, 503.

**Historical date ranges on the free plan:** only inside the last 30 days. A window older than that
needs a paid plan (archive), so the eval's Aug–Oct 2026 archive window can't be back-filled for free.
Even inside 30 days, results are **not as-of**: `totalArticleCount` includes articles published after
`at`, and late-arriving events appear. `observe_events` therefore marks a past `at` as
`provenance="hindsight_listing"` (rank and counts in `extra.hindsight`, observed_at = fetch time).

## Caveats for the comparison

- Dates are calendar days, so "the 24 h before `at`" is the two UTC days it touches (up to 48 h).
- Sorting by size over two days favours yesterday's big events over today's breaking ones; consider
  `getBreakingEvents` or a `date`-restricted second query if that shows up in the comparison.
- `importance` in `eventregistry_ranked` is relative (100 = biggest event in that result), not absolute.
- English only (`lang: eng`) by default; an event's count covers all languages (`articleCounts` splits it).
- Keyword queries on short generic words ("AI") match a lot of unrelated coverage; concept URIs are
  more precise. The concept URIs below were checked with
  `POST /api/v1/suggestConceptsFast {"prefix": "...", "lang": "eng", "apiKey": ...}` on 2026-11-26: all 18 resolve.
- Not an agent tool: there is no replay backend. Record results if a comparison run has to be repeated.

## Example `params` per beat (not added to config/beats/*.yaml)

To try one, add a headline source like
`{id: er_ai, type: eventregistry, tier: 2, weight: 0.5, params: {...}, verified: false}`.

```yaml
# ai
params:
  concepts:
    - http://en.wikipedia.org/wiki/Artificial_intelligence
    - http://en.wikipedia.org/wiki/Large_language_model
    - http://en.wikipedia.org/wiki/Generative_artificial_intelligence
    - http://en.wikipedia.org/wiki/OpenAI
    - http://en.wikipedia.org/wiki/Anthropic
    - http://en.wikipedia.org/wiki/Nvidia
  # alternative to concepts: keywords: ["artificial intelligence", "large language model"], keyword_loc: title
  lang: eng
  count: 30
  min_articles: 3

# energy
params:
  concepts:
    - http://en.wikipedia.org/wiki/Renewable_energy
    - http://en.wikipedia.org/wiki/Solar_power
    - http://en.wikipedia.org/wiki/Wind_power
    - http://en.wikipedia.org/wiki/Nuclear_power
    - http://en.wikipedia.org/wiki/Electrical_grid
    - http://en.wikipedia.org/wiki/Data_center
    - http://en.wikipedia.org/wiki/Battery_energy_storage_system
  # alternative: keywords: ["data center power", "transmission line", "battery storage"], keyword_loc: title
  ignore_keywords: ["oil price", "crude"]
  lang: eng
  count: 30
  min_articles: 3

# physical_ai
params:
  concepts:
    - http://en.wikipedia.org/wiki/Robotics
    - http://en.wikipedia.org/wiki/Humanoid_robot
    - http://en.wikipedia.org/wiki/Self-driving_car
    - http://en.wikipedia.org/wiki/Industrial_robot
    - http://en.wikipedia.org/wiki/Unmanned_aerial_vehicle
  # alternative: keywords: ["humanoid robot", "robotaxi", "warehouse automation"], keyword_loc: title
  lang: eng
  count: 30
  min_articles: 2
```

Within a field the list is OR'd (`conceptOper` / `keywordOper` = `or`); different fields (`concepts`,
`keywords`, `categories`) are AND'd together *(confirmed live: adding `categoryUri:
dmoz/Computers/Artificial_Intelligence` to the ai concepts cut 125 events to 21)*, so the examples use
concepts alone. `ignore_keywords` excludes events whose articles mention any of the words.
`top_articles: 5` (with `top_articles_events: 5`) adds the five closest articles for the five biggest
events, at extra token cost.

## Live check, 2026-11-26

One free-plan key, about 110 tokens used in total (suggest calls included).

- **Request fields:** the `getEvents` body built by `build_events_body` is accepted as is: `resultType`,
  `conceptUri`/`conceptOper`, `lang`, `dateStart`/`dateEnd`, `minArticlesInEvent`, `eventsSortBy: size`,
  `eventsCount`, `includeEventInfoArticle`, `forceMaxDataTimeWindow`. HTTP 200.
- **Response:** `{"events": {"results", "totalResults", "page", "count", "pages"}}` as documented. Event keys:
  `articleCounts, categories, concepts, eventDate, infoArticle, location, relevance, sentiment, summary,
  title, totalArticleCount, uri, wgt`. `title` and `summary` hold every language the event has
  (e.g. 10); `categories` are dmoz paths with `wgt`.
- **Cost headers:** each response carries `req-tokens` (5.000 for a recent event search, 1.000 for
  event articles), `req-action`, `req-archive: 0` and `req-chargable`. `eventregistry_ranked` now sums
  `req-tokens` into `usage.tokens`. `POST /api/v1/usage {"apiKey"}` returns
  `{"availableTokens", "usedTokens"}` (the free plan shows 2,000 available; usage counts up). The
  `suggestConceptsFast` and `suggestCategoriesFast` calls also seem to count, about 1 token each.
- **Outlets:** `source.uri` is a domain (`bbc.com`, `nytimes.com`), `source.title` a display name. The
  code now uses the domain as the outlet (as the other arms do), so the paywall check works.
- **Query quality (ai beat, 30 events, two calendar days):** sorting by size over six OR'd concepts
  surfaces big events where AI is a side concept: gold prices, Miss Universe, a Brad Pitt film review,
  "Air India / AI Express". Adding the AI category (AND) removes those, but also the two biggest real
  stories (SpaceX's orbital data centres, Nvidia halting Southeast Asia sales), and leaves physical_ai
  with 1 event. The comparison keeps the concept-only query; the blind rating will show the noise.
- **Category URIs** (suggestCategoriesFast): `dmoz/Computers/Artificial_Intelligence`,
  `dmoz/Computers/Robotics`, `dmoz/Business/Energy`, `dmoz/Business/Energy/Renewable`,
  `iptc/environment/natural_resource/renewable_energy`.
