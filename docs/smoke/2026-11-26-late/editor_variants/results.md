# Editor variants: 2026-11-26, 22:09 UTC collection

Why: on the 19:59 collection, the `claude_agent` arm's top 10 on ai read as more relevant than the
editor's (the AI stock selloff, Huang's reinstatement, S&P's rating watch on Nvidia). This run splits
the possible causes: the editor model (Haiku 5.5, reasoning off), the editor prompt, and recall (what
the feeds collect). Everything ran through OpenRouter; the Anthropic key was unset for every run.

Fresh collection, about 2 h after the 19:59 one (the 19:59 observations weren't in this container):
ai 4,620 observations, energy 1,407, physical_ai 795. Dead sources: reddit_ml and reddit_robotics,
as before. All arms ran on this one collection, one process per arm.

| Arm | Editor model (OpenRouter) | Prompt | Gap-fill search |
|---|---|---|---|
| `editor` | Haiku 5.5, reasoning off (`dev_default`) | `newsroom.editor` (v2) | none |
| `editor_v3` | Haiku 5.5, reasoning off | `newsroom.editor_v3` | none |
| `editor_sonnet` | Sonnet 5.5, reasoning effort medium | v2 | none |
| `editor_sonnet_v3` | Sonnet 5.5, reasoning effort medium | v3 | none |
| `editor_v3_search_tavily` | Haiku 5.5, reasoning off | v3 | 4 Tavily searches per beat through the `search` tool |

The v3 prompt changes two rules and one word:

- **Grouping:** one story is one real-world event. Reports built on the same scoop belong together.
  No round-up stories or "X; Y; Z" headlines, even on a quiet day.
- **Importance:** what an informed reader of the beat would be expected to know today. It names
  governance crises, sharp market moves in the beat's leading companies, credit-rating actions,
  actions by regulators or attorneys general, and major security incidents as high importance.
- **"deals" → "shopping deals"** in the skip list (v2's "skip … deals" could be read as business deals).

Variants are config only: `newsroom_editor.prompt` picks the prompt file and the arm binds the model.
The agent code is the same, so every variant replays on stored observations.

Files: `<beat>/<arm>.json` and `<beat>/collect_meta.json`; full tables in `summary.md`.

## Recall: the feeds had most of the agent's stories

Headlines in the ai collection for the stories `claude_agent` ranked high (19:59 run):

| Story | Collected? |
|---|---|
| AI stock selloff | yes: Reuters "AI stock rout deepens…", Guardian "Tech sell-off resumes…", FT, The Information |
| S&P rating watch on Nvidia | yes, 10 headlines (TechCrunch, The Information, Google News) |
| Amodei calls claims "absurd" | yes, 9 |
| 42 state AGs vs Character.AI | yes, 2 (TechCrunch, The Verge) |
| CBS: Nvidia chips to Chinese-owned US firms | yes, 1 (Google News chips feed) |
| Cisco Talos: OpenAI agents escape sandboxes | no |
| Sen. Kelly demands AI oversight | no |

So the gap was mostly judgment, not recall.

## Cost, time, memory

| Arm | ai | energy | physical_ai | Time per beat | Peak MB |
|---|---|---|---|---|---|
| `editor` | $0.0067 | $0.0061 | $0.0053 | 23–25 s | 75–99 |
| `editor_v3` | $0.0072 | $0.0076 | $0.0055 | 26–52 s | 76–99 |
| `editor_sonnet` | $0.1269 | $0.1115 | $0.0981 | 30–35 s | 75–99 |
| `editor_sonnet_v3` | $0.1253 | $0.1122 | $0.0924 | 27–35 s | 75–99 |
| `editor_v3_search_tavily` | $0.0078 | $0.0067 | $0.0058 | 26–40 s | 81–100 |

For reference, `claude_agent` on Sonnet 5.5 cost $1.64 for ai (19:59 collection), and on Opus 5.5 $3.24.
Tavily: 12 searches in all (Tavily credits, no LLM tokens).

## ai: where the stories the agent ranked high land

Rank in each arm's list of 30 (blank = not in the list). "Bundles" counts stories whose headline
joins items with ";", a rough count of events merged into one story.

| | editor | editor_v3 | editor_sonnet | editor_sonnet_v3 | v3 + Tavily |
|---|---|---|---|---|---|
| Nvidia halts Southeast Asia shipments | 1 | 1 | 1 | 1 | 1 |
| Huang reinstated | 9 (bundled) | **2** | 5 (bundled with S&P) | 3 | **2** |
| S&P rating watch on Nvidia | 6 (inside a bundle), 9, 16 | **3** | 3, 5 | 6 | **4** |
| AI stock selloff | 29 | 6 (as "…and AI stocks sell off") | — | 4 (as "AI stock rotation") | — |
| 42 AGs vs Character.AI | 13 | 15 | 20 | 16 | **10** |
| Amodei | — | 6 | 13 | — | — |
| CBS chip investigation | — | 11 | — | 14 | — |
| Bundles in top 10 / all 30 | 4 / 11 | 1 / 5 | 4 / 7 | 5 / 8 | 2 / 5 |

## Reading the numbers (unrated, one collection)

- **The prompt mattered more than the model on ai.** Haiku with the v3 prompt puts Huang at #2 and
  S&P at #3, folds the selloff into #6, and bundles least. Sonnet with the v2 prompt bundles as much
  as Haiku (4 in the top 10, e.g. "Huang reinstated; S&P weighs downgrade") and drops the selloff.
- **Sonnet helps grouping on physical_ai**: it splits the Waymo recall from the freeway ban and
  Rockwell's AGILOX deal from GM's robotics plans. With either prompt it bundles less than Haiku there
  (2 bundles in the top 10, against 4).
- **No arm made the selloff its own top story.** Every arm that kept it merged it into the Anthropic
  share-price story. If a sector-wide market move should stand alone, the prompt has to say so.
- **energy:** all variants agree on the leads (the NextEra–Dominion backlash, chip curbs stalling Asian
  data-center deals, Google–Constellation's 3.6 GW deal, Poland's nuclear plant). Sonnet adds the
  Senate permitting bill to the top 5. `editor_v3` has one off-beat item at #10 (Trump's tariffs).
- **Gap-fill search through Tavily barely changed the lists.** Generic "news today" queries mostly
  return stories the feeds already have; the Character.AI story rose from #15 to #10. It didn't find
  the two stories the feeds missed (Talos, Sen. Kelly).
- **Cost:** v3 costs the same as v2 (under $0.01 per beat). Sonnet costs about 15–18× Haiku: about
  $0.11 per beat, $0.33 a run for three beats. Tavily adds no LLM cost.

## Fixes in this run

- `app/tools/search.py`: provider dates without a timezone (Tavily) are now taken as UTC.
  Comparing them with feed dates crashed the editor's gap-fill step (`TypeError: can't compare
  offset-naive and offset-aware datetimes`). Test: `test_provider_dates_are_timezone_aware`.

## Spend

| | This run |
|---|---|
| OpenRouter | $0.73 (15 arm runs; Sonnet $0.67 of it) |
| Tavily | 12 searches |
| Anthropic | $0 |
