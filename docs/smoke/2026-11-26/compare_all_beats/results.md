# Newsroom comparison, all three beats: 2026-11-26 (second run)

Second collection of the day, at 19:59 UTC (Thanksgiving, light news): ai 4,665 observations, energy
1,400, physical_ai 817. Dead sources: reddit_ml and reddit_robotics (HTTP 404). Every arm ran on that
one collection, one process per arm (`python -m eval.newsroom_compare run`). Model for classic and
editor: Haiku 5.5 through OpenRouter (dev_default). The first run (ai only, 19:34) is in
`../compare/results.md`.

**`claude_agent`: ai only. `editor_search`: not run.** A later session (key read from
`CLAUDE_API_KEY`, since cloud sessions don't pass `ANTHROPIC_API_KEY` through) ran `claude_agent` for
ai against this collection's time (19:59 UTC). Energy and physical_ai were held back on cost; see
"claude_agent (ai)" below. `editor_search` needs this day's observations, which weren't in that
container. The follow-up on why the agent's picks read better is in
`../../2026-11-26-late/editor_variants/results.md`.

- The Anthropic client is pinned to `https://api.anthropic.com`. Cloud agent containers set
  `ANTHROPIC_BASE_URL` to their own endpoint, and the SDK would otherwise read it. Override it with
  `ANTHROPIC_API_BASE_URL`.

Files:

- `<beat>/<arm>.json`: the arms after the date-filter fix (below).
- `<beat>/before/`: the same arms, run before the fix.
- `<beat>/dated/`: the same arms, with the site front pages re-parsed with dates.
- `<beat>/collect_meta.json`: per-source counts and timings.

This day's observations stay in the gitignored `eval/data/`. The scheduled daily run will commit
them gzipped.

## Spend

| | This run |
|---|---|
| OpenRouter (classic, classic_all, editor: 4 passes × 3 beats) | about $0.39 |
| Event Registry | 15 tokens per beat and run (45 for three beats). 107 of the free plan's 2,000 used so far, checks included |
| Anthropic (later session) | $5.06: one search smoke test $0.18 (Opus), `claude_agent` ai $3.24 (Opus) then $1.64 (Sonnet) |

Event Registry tokens, read from the `req-tokens` response header: 5 per recent `getEvents`, plus 1
per `getEvent` article call. That's 10 article calls per beat, to get outlets for the 10 biggest
events. At the cheapest paid tier (5K tokens for $90), one beat's run would cost about $0.27.

## claude_agent (ai)

Claude with Anthropic's web search and web fetch server tools ranks the beat by itself, live, in one
turn (`app/sources/claude_agent.py`). Run at 21:20 UTC with the window ending at 19:59, so it saw
about 80 minutes of hindsight. The server-side fallback beta (`server-side-fallback-2026-07-01`,
`fallbacks: "default"`) was accepted with both server tools; no request fell back.

| Model | Time | Peak MB | Input / output tokens | Searches / fetches | Cost | Stories |
|---|---|---|---|---|---|---|
| Sonnet 5.5 (default now; `<beat>/claude_agent.json`) | 144 s | 104 | 637,819 / 16,376 | 20 (limit hit) / 11 | **$1.64** | 27 |
| Opus 5.5 (`ai/opus/claude_agent.json`) | 169 s | 104 | 676,628 / 16,482 | 20 / 10 | $3.24 | 28 |

One Anthropic `search` call (search tool, Anthropic backend, Opus): 36,920 input / 1,334 output
tokens, 1 search, 14 s, $0.18. About 84% of the agent's cost is input tokens (search results read back
into the turn); no tokens were served from cache.

With the matcher fix (below), on ai the agent shares 1 top-10 story with each other arm (the Nvidia
halt). By hand it's about 3: the matcher misses the Reuters chip-curbs story, Huang's reinstatement
and the Amodei quote, which are worded differently. Its top 10 has 23 outlets and no paywalled ones;
it cites secondary coverage and round-up pages instead. Stories only it has in its top 10: the AI
stock selloff (#2), S&P's rating watch on Nvidia (#3), the AI-written math papers and Aaronson (#4),
Amodei (#5), the GLM-5.5 delay (#6), CBS on Nvidia's Chinese-owned partners (#7), ASML and the MATCH
Act (#8), Cisco Talos on OpenAI agents escaping sandboxes (#9), and Sen. Kelly on AI oversight (#10).
It cited 24 URLs that never appeared in a search or fetch result; those were dropped.

**Matcher fix** (`eval/newsroom_compare/pool.py`): the agent cites the same round-up page (e.g. an
"AI news today" page) for several stories, so matching on a shared URL chained 8 of its top 10 into
one group. URLs an arm cites for two or more of its stories no longer count, and a story is compared
with every headline in a group. The other arms' overlap numbers below are unchanged.

## Per beat (after the fix, same collection)

How to read the columns:

- **Time and Peak MB:** the arm's wall-clock time and the arm process's peak memory (`ru_maxrss`).
- **Outlets:** distinct domains across the arm's top 10. Event Registry lists 10 articles for each of
  its 10 biggest events, so its count isn't comparable with the others.
- **Only this arm:** top-10 stories that no other arm has in its top 10. Stories match on a shared
  URL or title similarity (rapidfuzz `token_set_ratio` ≥ 82), as in the blind pool.

### Beat: ai

| Arm | Time (s) | Peak MB | Cost $ | Stories | Outlets (top 10) | Paywalled outlets | Only this arm (top 10) |
|---|---|---|---|---|---|---|---|
| classic | 24.3 | 432.0 | 0.0124 | 30 | 8 | 2 (ft.com, washingtonpost.com) | 1 |
| classic_all | 36.2 | 485.1 | 0.0158 | 30 | 13 | 4 (bloomberg.com, ft.com, nytimes.com, wsj.com) | 2 |
| editor | 32.1 | 103.6 | 0.0078 | 30 | 9 | 4 (bloomberg.com, ft.com, nytimes.com, wsj.com) | 4 |
| eventregistry | 15.5 | 57.0 | 15 ER tokens | 30 | 91 | 2 (businessinsider.com, nytimes.com) | 10 |

Top-10 overlap (stories in both arms' top 10):

| | classic | classic_all | editor | eventregistry |
|---|---|---|---|---|
| classic | 10 | 8 | 6 | 0 |
| classic_all | 8 | 10 | 5 | 0 |
| editor | 6 | 5 | 10 | 0 |
| eventregistry | 0 | 0 | 0 | 10 |

Only one arm (top 10):

- **classic**: Pentagon officials discussed Defense Production Act to pressure Nvidia board on Jensen Hua (#10)
- **classic_all**: Anthropic shares split analysts after newly listed stock slips below IPO price (#4); US approves SpaceX plan to launch one million satellites for orbital AI data centers (#7)
- **editor**: Reuters exclusive: US chip curbs and distillation crackdown threaten Chinese AI model deve (#4); US closes remote-access loophole for Chinese firms using Nvidia chips and tightens smuggli (#5); Jensen Huang reinstated at Nvidia after board reportedly moved to oust him; Washington rea (#7); SAP considers cutting at least 10,000 jobs to transform software business for AI era (#10)
- **eventregistry**: US regulators approve SpaceX plan to send a million data satellites to space (#1); Elon Musk floats a truly wild figure for SpaceX's potential value: $128 quintillion (#2); Nvidia Halts Sales to Southeast Asia as U.S. Tightens China Restrictions (#3); Gold Price Today on November 25, 2026 (#4); Was Miss Universe 2026 Rigged? The Internet Thinks So (#5); Mark Cuban Says Someone Else Could Take Your Job if You Don't Do This (#6); Is Nvidia Stock a Buy After Dell's Record AI Backlog? (#7); Brad Pitt's 'Cliff Booth' is back and still relentlessly cool - Review (#8); Surrendering Human Control To AI? Anthropic CEO Dario Amodei Calls It 'Absurd' (#9); Rupee soars 65 paise to hit 8-month high of 92.20 against US dollar (#10)

### Beat: energy

| Arm | Time (s) | Peak MB | Cost $ | Stories | Outlets (top 10) | Paywalled outlets | Only this arm (top 10) |
|---|---|---|---|---|---|---|---|
| classic | 24.6 | 424.9 | 0.0114 | 25 | 2 | 0 () | 4 |
| classic_all | 34.6 | 467.3 | 0.0152 | 25 | 4 | 1 (ft.com) | 2 |
| editor | 32.8 | 87.0 | 0.0075 | 25 | 5 | 1 (ft.com) | 5 |
| eventregistry | 11.7 | 56.9 | 15 ER tokens | 30 | 87 | 4 (africa.businessinsider.com, bloomberg.com, businessinsider.com, nytimes.com) | 10 |

Top-10 overlap (stories in both arms' top 10):

| | classic | classic_all | editor | eventregistry |
|---|---|---|---|---|
| classic | 10 | 6 | 3 | 0 |
| classic_all | 6 | 10 | 5 | 0 |
| editor | 3 | 5 | 10 | 0 |
| eventregistry | 0 | 0 | 0 | 10 |

Only one arm (top 10):

- **classic**: Ohio approves gas plant for data centers despite voter opposition (#5); New York awards $230M in repurposed offshore wind grants to ports (#8); Michigan prepares rules to rein in utilities and data centers (#9); Vantage and Heron Power sign gigawatt-scale 800 VDC power deal for AI data centers (#10)
- **classic_all**: UK faces third day of strained electricity supplies during cold snap (#5); Texas resumes data center permitting before Democrats take over, FT reports (#10)
- **editor**: Backlash over AI power costs threatens $67bn NextEra-Dominion deal (#1); Britain's grid operator makes record number of calls for extra power as cold weather and S (#4); Nvidia reportedly halts server shipments to Malaysia, Singapore, Thailand and Indonesia af (#6); Microsoft and Chevron sign 20-year power deal for Texas data center (#7); Kansas City blocks high-rise data center as data center fights divide Republicans (#10)
- **eventregistry**: US regulators approve SpaceX plan to send a million data satellites to space (#1); Elon Musk, SpaceX pledge major stock donations to active Trump Accounts (#2); Nvidia Halts Sales to Southeast Asia as U.S. Tightens China Restrictions (#3); Kim Jong Un Makes Rare Show Of Nuclear Warheads After Snubbing Trump Talks (#4); Mark Cuban Says Someone Else Could Take Your Job if You Don't Do This (#5); Is Nvidia Stock a Buy After Dell's Record AI Backlog? (#6); S&P Global warns Nvidia's credit rating could be downgraded over governance risks (#7); Alice Huang-Linked Firms Listed $270 Million In AI Servers In China: Report (#8); Malaysia regulator seeks $146 million damages from Google over 'harmful' content (#9); Cabinet Approves Rs 1,491-Crore Push To Modernise Post Offices (#10)

### Beat: physical_ai

| Arm | Time (s) | Peak MB | Cost $ | Stories | Outlets (top 10) | Paywalled outlets | Only this arm (top 10) |
|---|---|---|---|---|---|---|---|
| classic | 15.6 | 358.2 | 0.0029 | 11 | 1 | 0 () | 9 |
| classic_all | 28.6 | 436.5 | 0.0178 | 25 | 1 | 0 () | 3 |
| editor | 23.7 | 81.7 | 0.0052 | 25 | 2 | 0 () | 2 |
| eventregistry | 12.0 | 57.1 | 15 ER tokens | 30 | 72 | 0 () | 10 |

Top-10 overlap (stories in both arms' top 10):

| | classic | classic_all | editor | eventregistry |
|---|---|---|---|---|
| classic | 10 | 0 | 1 | 0 |
| classic_all | 0 | 10 | 7 | 0 |
| editor | 1 | 7 | 10 | 0 |
| eventregistry | 0 | 0 | 0 | 10 |

Only one arm (top 10):

- **classic**: Rockwell Automation to acquire AGILOX (#2); Humyn Labs raises $47M Series A for physical AI research and deployment (#3); Boston Dynamics expands GlobalFoundries partnership with Spot and Atlas robots (#4); Dexory launches DexoryView Inspect warehouse safety and compliance module (#5); GM targets 20% increase in robotics and factory automation by 2028 (#6); FlytBase launches Octane platform for autonomous drones in oil and gas (#7); IntelliProve raises €2.7M for health sensing layer across cameras (#8); Miba deploys Flexxbotics platform for advanced manufacturing operations (#9); Beckhoff Automation controls power Four Growers' robotic greenhouse harvesting (#10)
- **classic_all**: Waymo recalls 4,488 robotaxi units over two highway-related issues (#2); XPeng unveils IRON humanoid and robotaxi at AI Day, targets 2027 sales (#5); Tesla reportedly plans stake in German robotics supplier EMAG (#10)
- **editor**: Tesla releases FSD v15 update removing a feature, and Europe expansion faces test (#6); Jensen Huang reinstated as Nvidia president, CEO and board member (#8)
- **eventregistry**: Iran denies attacks on Kuwait, calls accusations attempt to exacerbate instability - TopNe (#1); UK conscription warning as 100,000 MoD letters 'step one' over WW3 concerns (#2); Five Russian military jets hit in airfield strike, says Ukraine (#3); Two Italians killed after Iranian strike in Kuwait (#4); UK moving military aircraft to hangars at night over possible drone threats, media reports (#5); 25 Tankers Hit, Hormuz Traffic Down 40%: Why Isn't The World Facing An Oil Price Shock? (#6); Laser shoots down Russian drone in world first, says Ukraine (#7); Russia burning as Ukraine rocks country with 2 huge strikes - Putin humiliated (#8); Five Mexicans arrested in Nigeria over $227m meth lab (#9); Drones attack Russian chemical plant supplying materials for military explosives - video (#10)


## Reading the numbers (unrated)

- **Speed and memory:** every arm finishes in under 40 s. The editor uses 80–105 MB, against
  360–490 MB for classic (fastembed/ONNX). Event Registry takes 12–16 s at 57 MB.
- **Cost:** the editor is the cheapest LLM arm, at $0.005–0.008 per beat. classic_all costs about
  twice as much.
- **Overlap:** classic_all and editor share 5–7 of their top 10. On physical_ai, classic has only the
  8 original sources and shares 0–1 stories with the other arms. Event Registry shares nothing with
  any arm.
- **Event Registry quality:** sorting by event size with OR'd concepts brings in big general events.
  - ai: gold prices, Miss Universe and a Brad Pitt review. About 5 of its top 10 are on-beat.
  - energy: no energy story in the top 10 (SpaceX, Nvidia, Kim Jong Un's warheads).
  - physical_ai: war news, because the `Unmanned_aerial_vehicle` concept pulls in drone strikes.

  Some of its unique stories are real, such as SpaceX's orbital data centres and Nvidia halting
  Southeast Asia sales. Others are a matching gap: ER's "Nvidia Halts Sales to Southeast Asia as U.S.
  Tightens China Restrictions" and the editor's "Nvidia halts AI server shipments to Malaysia,
  Singapore…" are the same story, but the title-similarity rule doesn't pair them. The blind pool
  shows both versions to the rater, so the overlap numbers can be corrected after rating. Narrower
  queries would help; see "Live check, 2026-11-26" in `docs/sources/eventregistry.md`.
- **Paywalled outlets in the top 10:** on ai, editor and classic_all each reach 4 (WSJ, FT,
  Bloomberg, NYT), classic 2 and ER 2. On energy the LLM arms reach at most 1 (FT). Physical_ai has
  none.

## Date filter: aggregators only (the known issue)

`freshness.py` exempted every `frontpage` source from the date rule. As a result, lab index pages
(anthropic_news, meta_ai_blog) put old launches into the top 10 every day. Two changes:

1. **Exemption:** only aggregator front pages are exempt now: techmeme, hn_front, hf_papers and
   reddit_top (`EXEMPT_TYPES`). The editor's ranking signal ("listed on a front page") still counts
   every front page.
2. **Dates for site front pages:** these pages yielded no dates at all. With change 1 alone, their
   items only fall under the 20-per-source cap for undated items. The `frontpage` parser now reads
   dates in two places:
   - **Card date:** a `<time>` tag or date text in the story's own card. That's the link itself or
     an ancestor, stopping before one that holds another story's link. Every appearance of the link
     on the page counts.
   - **Page newest date:** the newest story date on the page. It's recorded only when at least half
     of the stories (and at least 3) are dated.

   The freshness rule now drops undated items from a page whose newest story is older than the
   window (`stale_page`). Dates after the fetch (webinar and event listings) are ignored. A date with
   no time of day counts as the end of that day.

   Results on today's pages (story URLs identical to the collection's):

   | Page | Dated stories | Newest | Result |
   |---|---|---|---|
   | anthropic_news | 24 of 29 | Nov 20 | all dropped |
   | meta_ai_blog | 5 of 6 | Jul 27 | all dropped |
   | latitude_media | 25 of 26 | Nov 25 | keeps its two Nov 25 stories |
   | robotics247 | 4 of 24 (webinars) | none recorded | drops the webinars only |
   | canary_media, heatmap | 0 | none | unchanged |

Tests:

- `tests/unit/newsroom/test_editor.py::test_freshness_exempts_aggregators_not_site_front_pages`
- `tests/unit/sources/test_parsers.py::test_front_page_card_dates_*`

The stored observations came from the old parser, so change 2 can't take effect on this collection
directly. `dated/` is the same collection with the six site front pages re-parsed from copies saved
about 10 minutes after collection; the story URLs are the same.

Top-10 stories that come only from a single site's front page, ai beat. In this data these are old
lab posts:

| Arm | before | after (change 1, same collection) | after + page dates |
|---|---|---|---|
| classic | 6: Anthropic S-1 (Oct 26), the Haiku/Opus 5.5 launch, Fable 5.2 (Nov 9), Meta Muse, explainers | 4 | **0** |
| classic_all | 3 | 3 | **0** |
| editor | 1 | 2 | **0** |

On energy, the stories still coming from a single site are today's news from canary_media and
latitude_media (the permitting bill, the Ohio gas plant), so keeping them is correct.

Front-page items dropped by the date rule (editor, step N2):

| Beat | before | after | after + page dates |
|---|---|---|---|
| ai | 0 | 9 (anthropic_news, over the cap) | 35 (anthropic_news 29, meta_ai_blog 6) |
| energy | 0 | 23 | 40 (canary_media 17 over the cap, latitude_media 23 stale) |
| physical_ai | 0 | 4 | 4 (robotics247 webinars) |

Time, memory and cost are the same in all three versions: classic 15–25 s and 360–430 MB, editor
21–34 s and 82–104 MB.

With page dates, classic's ai top 10 is:

1. Nvidia halts AI server shipments
2. The GLM-5.5 delay
3. OpenAI's GPT-6.2 billing error
4. The Aaronson post
5. Microsoft on state hackers
6. A Hugging Face benchmark post
7. Rubio and Huang
8. Apple pulls MiniMax
9. SAP job cuts
10. The Pentagon and the Defense Production Act

Full tables for this version: `summary_dated.md`.
