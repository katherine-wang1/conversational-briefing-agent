# Working plan

The one file for open work: tasks, decisions and Kate's feedback. Consolidated on 2026-11-27 from
`docs/next-session.md`, `docs/open-decisions.md`, the checkboxes in `docs/architecture-revisit.md` and
`docs/TODO.md` (now `docs/archive/TODO-midpoint.md`). The 2026-11-27 versions of the first two are in `docs/archive/` (stubs at the old paths); this file is current.
`docs/architecture-revisit.md` stays as design rationale.

Branch: `claude/sweet-curie-r8ufht`. Owners: **Kate** (accounts, keys, decisions, listening, rating),
**Claude** (code, docs, runs), **both**. Status: `open`, `doing`, `blocked`, `done`, `dropped`.
IDs are per area (NR, SN, BR, MEM, VO, APP, COST, EV, DEMO); "was" gives the old ID so earlier references
still resolve (F# and D# from open-decisions, Q# and task numbers from next-session, T#.# from TODO, A1–A3
for the three parts of architecture-revisit).

## Deadlines and target

| Date | What |
|---|---|
| **Fri Dec 4** | **Our target: everything done** (system frozen, evaluation run, report drafted, demo ready) |
| Wed Dec 9 | Demo (course deadline) |
| Fri Dec 11 | Final report (course deadline). Rubric: results 10/25, reproducibility 5/25 |

**The constraint is evaluation data, not code.** Waiting for days to collect live gives only ~7 days by
Dec 4. So: collect every day from now (free), and **rebuild past days** with `collect --at` (thinner:
40–60% of items, no front-page signals), so tuning and testing don't wait (EV-1, EV-2). The Wayback
archive (35–70 h on the laptop) is likely out of reach for Dec 4 (EV-13, decision D17).

### Schedule to Dec 4 (proposal)

| Days | Critical path | In parallel |
|---|---|---|
| Fri Nov 27 – Sat Nov 28 | EV-1 daily collection starts; EV-2 rebuild ~10 past days; MEM-1 memory audit and test | Plan consolidation (this file) |
| Sat Nov 28 – Sun Nov 29 | NR-1..NR-3 newsroom novelty, concentration, scope; BR-1..BR-3 podcast brief using MEM history | Kate rates newsroom lists |
| Mon Nov 30 | NR-6 pick newsroom prompt and model (D1) on tune days, check on test days; **freeze the research config** (EV-6) | VO-1..VO-3 |
| Tue Dec 1 – Wed Dec 2 | EV-7..EV-10 evaluation runs on the frozen config, judges, Kate's labels | APP-1 voice-forward UI |
| Thu Dec 3 | EV-11 grading, failure analysis | DEMO-1 deploy; DEMO-2 demo dry run |
| Fri Dec 4 | DEMO-3 report draft; DEMO-4 tag and grader access | |

## Where things stand (2026-11-27)

One daily cycle runs end to end in about 5 minutes and $0.40 for three beats and one user
(`scripts/e2e_run.py`; latest log `docs/e2e/2026-11-27/run_log.md`, earlier runs beside it:
`docs/e2e/run1_budget90k/`, `docs/e2e/run2_before_paywalls/`, `docs/e2e/run3_write_truncated/`):

1. **Collect** headlines from every source (free).
2. **Newsroom** (shared): editor implementation (`newsroom.impl: editor`), Sonnet 5.5 through OpenRouter,
   prompt v4. Prompts v2 (old), v3, v4, v5 (blend) exist; the choice is open (D1).
3. **Story notes** (shared, one per story, Haiku): facts quoted from fetched sources. Google News links
   resolve to the publisher. Paywalled stories fall back to free coverage of the same event or the
   publisher's own headline and summary, attributed. 7 of 80 had no text in the last run.
4. **Per-user run** (Haiku): plan, search, pool of 80, score, select 6, source packs, write, claim check,
   then a stored set of 20 stories for the conversation.
5. **Voice**: the brief, the packs, the stored-set list, and `get_story` for any stored story's note.

Comparison and rating tools: `python -m eval.newsroom_compare` (collect, `collect --at` to rebuild a past
day, run arms, summary). Blind list-comparison pages: round 1
https://claude.ai/artifact/Dppc6cYRVSSbZFogXCXecw, round 2 https://claude.ai/artifact/84Cv2BmqUi7sLeNCY7x7CE
(answer keys in `docs/rating/`). 195-story rating page for spot checks:
https://claude.ai/artifact/YWXefuNeTZwhEfPy8kkuz3.

Working rules: no Anthropic key (everything through OpenRouter; `claude_agent` runs on hold); never
circumvent paywalls; keys optional with fallbacks; the evaluated setup stays replayable (same agent code,
only tool backends change); no scheduled tasks; tell Kate before spend over ~$2 and report each live run's cost.

Spend so far: Anthropic $5.06 early on (none since 2026-11-27); OpenRouter roughly $10 across comparisons,
sub-agents and five end-to-end runs; Tavily a few hundred searches.

---

## Tasks

`*` marks the Dec 4 critical path.

### Newsroom (shared ranking)

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| NR-1* | Novelty across days: pass prior days' digests or stories (N6 story identity exists) so a continuing story needs a new development to rank high, and old news drops | open | Claude | Kate: "the Anthropic IPO is now old news"; comparisons rank each day in isolation | task 2 |
| NR-2* | Concentration: cap stories about one company or saga near the top; favour breadth (releases, deals, research, policy) | open | Claude | Every round-2 list was concentrated on Nvidia, chips and markets; v5's cap isn't enough | task 2, D1 notes |
| NR-3* | Scope: keep off-beat stories out of a beat | open | Claude | GTA VI in the ai digest became the brief's second story (`docs/e2e/run3_write_truncated`, run 4) | F26 |
| NR-4* | Prompt rules to keep: developments over statements (D4), complete headline-like titles ("raises $81.3B, then shares fall from first-day gains"), what a model release actually changes | open | Claude | Kate's round-1 and round-2 notes | D4, feedback |
| NR-5 | Try a new prompt (v6) combining the parts Kate liked from the old prompt, v4 and v5, plus NR-1..NR-4 | open | Claude | Round 2 had no clear winner; each prompt won something | Kate 2026-11-27 |
| NR-6* | Pick the newsroom prompt and model on collected days, split into tune and test days | blocked on NR-1..5, EV-2 | both | Needed to freeze the research config | Q1, D1, F2 |
| NR-7 | Stale items: Google-News-only stories resolving to June–September articles (Google News dates are re-index dates) | open | Claude | Freshness filter misses them in energy | F11 |
| NR-8 | Cross-beat duplicates: one story in two beats' digests gets two notes | open | Claude | Huang/Nvidia in ai and physical_ai | F1, Q9 |
| NR-9 | Re-run the round-2 list comparison on energy and physical_ai | open (optional) | both | Only ai was rated | task 5 |

### Story notes and sources

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| SN-1 | Reuters returns 401: add to the paywalled domains | open | Claude | Saves a failed fetch per story | F6 |
| SN-2 | Wire copies attributed to the host ("yahoo.com"): detect bylines ("Reuters, via Yahoo News") | open | Claude | Sources named aloud should be right | F23 |
| SN-3 | C6 reuses the free source a shared note already found instead of searching again | open | Claude | Duplicate searches | F25 |
| SN-4 | Free-coverage thresholds (title 60/80, 2 entities, 36 h, 25 searches) set from one day; maybe a host quality list | open | Claude | Content farms may pass | F24 |
| SN-5 | Google News resolution: undocumented endpoint (returns `failed` on a format change, never a wrong URL), possible 429 from one cloud IP, JSON cache on local disk (lost on Railway restarts) | open (watch) | Claude | Fragile in deploy | F9 |
| SN-6 | Title-search fallback for unresolved Google News links needs title hints from the observations | open (low) | Claude | Partly covered by free-coverage search | F10 |
| SN-7 | Check UTC date parsing for search results | open | Claude | Rest of T1.17 (redirect links done: 7b82254) | T1.17 |

Decisions: D10 licensed full text, D5 note model, D16 free-coverage search provider.

### Per-user brief

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| BR-1* | Rewrite `app/agents/prompts/cos.write_brief.md` as a podcast-style narrative: no anchor signposts, never name segments or slots ("now to energy", "outside your usual beat"), connect stories where they connect | open | Claude | Kate: too disjointed, more news anchor than podcast | task 1 |
| BR-2* | More context per story (what happened, why, background): give the writer the shared story note as well as the C6 passages | open | Claude | The writer only sees passages; GTA VI got two sentences | task 1 |
| BR-3* | Context scales with what this listener has heard: mark each story new, follow-up ("since we last talked…") or repeat (skip or one line) from the user's history | blocked on MEM-2 | Claude | History today is headline-level only, see MEM | task 1 |
| BR-4* | Claim check for longer narrative sentences: context sentences cite the note or pack; allowed background knowledge (D11) is marked so C8 keeps it. Also: dangling references after a dropped sentence (F13), unchecked story-end hooks (F14), pack citations that don't state the fact (F15) | open | Claude | Longer sentences fail C8 more | task 1, F13–F15 |
| BR-5 | Output limit: writer has 10,000 tokens (4,000 truncated run 3); longer stories may need more or one call per story | open | Claude | `docs/e2e/run3_write_truncated` | task 1 |
| BR-6 | Rundown shorter and spoken (60+ words naming five stories today); Haiku style slips (≠, 9/4) | open | Claude | Run 4 reading | F16 |
| BR-7 | A story without a news opening (X-Energy from the user's own search began with background) | open | Claude | Run 4 reading, item 4 | run 4 |
| BR-8 | Quick fallback brief when the full run takes too long (TODO at `app/api/routes.py:257`, TDD §20) | open (low) | Claude | Demo safety | T1.9 |
| BR-9 | Run follow-up research live once (unit tests only so far) | open | Claude | Bookmarked asks (MEM) depend on it | T1.7 |

Decisions: D11 background knowledge, D12 "outside your usual" slot, D13 brief length and story count.

### Memory: stories given, interests, asks

How it works today (checked 2026-11-27, see the end of this file) and what to fix.

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| MEM-1* | Memory audit and test: a script that runs two or three simulated days for one user (brief → session with signals → `memory_update` → next brief) and prints the memory snapshot, story history, open questions and what changed each day; plus a short design note | open | Claude | Kate wants to see and test the memory design | Kate 2026-11-27 |
| MEM-2* | Story history the brief can use: record every story the user was given or discussed (brief stories, stored-set stories opened with `get_story`, voice answers), with what was said (the facts or sentences), not only the headline; link across days by story identity (`update_of`) | open | Claude | "New information" needs to know what they've heard; today only brief headlines, 14 days, no `update_of`, no subtopics | F8, task 1 |
| MEM-3 | Fill `ShownStory.interest_ids` from the scorer's `because` (and subtopics), so a skip or dive teaches the right interest | open | Claude | Always empty: `shown_from_script` never sets them | T1.6 |
| MEM-4 | Specific asks as first-class memory: "keep me posted on X", "less of Y", bookmarked questions; check each reaches the next brief (keep_posted interests, `open_questions`, `topic_requests`) | open | Claude | Asks are spread over three stores; only onboarding text reaches `topic_requests` | Kate 2026-11-27, T1.23 |
| MEM-5 | Apply interest decay before each run, not only after a session | open (low) | Claude | Today decay runs inside `memory_update` only | T1.8 |

### Stored set and voice

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| VO-1 | Stored-set mix: reserve about half for shared stories (they have notes), or rank by the higher of the for-you and big-news scores | open | Claude | 13 of 14 stories beyond the brief were the user's finds | D8, task 3 |
| VO-2 | Filter low-quality personal finds before ranking: social posts, quote pages, near-duplicates | open | Claude | Voice stated an Instagram reel as fact; "Procedure Inference as Memory" twice; a Yahoo Finance quote page | F18, F20 |
| VO-3 | Voice prompt: no narration of tool use, same podcast tone as the brief | open | Claude | "I'll pull up the stored note" | F19 |
| VO-4 | Ask before bookmarking: the voice offered to look something up and saved it in the same answer | open | Claude | Found in T1.3 | T1.23 |
| VO-5 | Check how often the meta-sentence filter fires (log line `dropped a meta sentence`) | open (low) | Claude | Haiku narrates its plan sometimes | T1.24 |
| VO-6 | `search_news(query)` over the last 7–30 days of stored stories (L2 in the design) | open (low) | Claude | "What happened with X last week?" | A2 |
| VO-7 | Citation links in voice answers (model leaves out markers, 0 sources shown) | dropped to low | — | Kate: links don't matter much (D15) | F27, Q8 |

### App and audio

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| APP-1* | Redesign the web app to be fully voice-forward: talking is the main interface (start, interrupt, ask, navigate by voice), screens are secondary | open | both (Kate: design calls) | Kate 2026-11-27; the demo should show a voice product | new |
| APP-2 | MP3 instead of WAV for brief audio (~8× smaller; ffmpeg in the image) | open | Claude | ~23 MB per 8-minute brief | T1.19 |
| APP-3 | Stream answer audio as it arrives (each sentence waits ~1.4 s today; target ≤ 1 s) | open | Claude | Voice latency | T1.20 |
| APP-4 | Brief build trace (C1–C8) in the live log next to the session events | open (low) | Claude | Good for the demo and debugging | T1.27 |
| APP-5 | Safari/iPhone listening check, home-screen app checklist (`docs/iphone-checklist.md`) | open | Kate | Not yet done on a phone | T1.1, T1.12 |
| APP-6 | Server speech-to-text if browser recognition isn't good enough (OQ-8, `STT_API_KEY`) | open (optional) | both | Matters more once the UI is voice-first | T1.13 |

### Cost and tokens ($0.02 per user per run today; low urgency)

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| COST-1 | Per-step token allowances instead of one shared cap; prompt caching for scoring (0 cached tokens today); a shorter line per story in scoring. Keep the 80-story pool (D2) | open (low) | Claude | Search used up what packs needed in run 1 | F21, task 4 |
| COST-2 | Spend cap is per process, not global | open (low) | Claude | Daily `spend_guard` job covers it for now | T2.14 |

### Evaluation

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| EV-1* | Collect every day from now: `python -m eval.newsroom_compare collect` (free; eval/data/ is gitignored, so keep copies) | open | Claude | Real days for tuning and testing | F2, task 5 |
| EV-2* | Rebuild past days with `collect --at <ISO time>` to get ~10+ days now; record what's thinner (40–60% of items, no front-page signals) | open | Claude | Can't wait for live days before Dec 4 | Kate 2026-11-27, F2 |
| EV-3* | Decide the evaluation scope that fits Dec 4 (D17): which arms, tasks, days; cut the rest explicitly | open | both | The Track-2 plan was sized for more time | T2.13 |
| EV-4 | Replay backends in the runner, then a newsroom replay over the chosen days | open | Claude | Reproducibility (5/25) | T2.6 |
| EV-5 | Hindsight labels: wire `eval.tasks.hindsight.build_labels` into the label step with the newsroom's grouping; the editor has no clustering, so the "covered by ≥ k domains" signal needs another source (A1 open question) | open | Claude | The old path `eval/archive/labels.py` doesn't exist | T2.5, A1 |
| EV-6* | Freeze the research config (newsroom, per-user, models) and tag it | blocked on NR-6 | both | Evaluation runs need a fixed system | T2.11 (was Nov 24) |
| EV-7* | Model bake-off on dev days, then pin `config/models.yaml` (OQ-16: keep late-cutoff models out of scoring roles, or a leakage probe) | open | both | | T2.9 |
| EV-8* | Labelling: decide labelling time (OQ-4), then a session on the collected days | open | Kate | Ground truth | T2.7 |
| EV-9* | Validate LLM judges against Kate's labels; add a faithfulness judge for notes (quote check proves the phrase is in the source, not that the fact paraphrases it) | open | both | | T2.10, F7 |
| EV-10* | Self-study: Kate uses it daily (also exercises memory across days) | open | Kate | Was to start Nov 15 | T2.8, T1.4 |
| EV-11* | Grading, failure analysis | open | both | | T2.12 |
| EV-12 | Move tests to the shared fake model client (`app/llm/fake.py`) | open (low) | Claude | | T2.15 |
| EV-13 | Wayback collection on the laptop (35–70 h), finish the cloud collection (HN comments, article bodies; resume command in `docs/status.md` §2), replay window (OQ-6), HN as a signal (OQ-14), datasheet v0 | likely dropped for Dec 4 (D17) | both | The archive on `data/archive-raw` has only HN, arXiv and HF papers | T2.1–2.4, F3 |

### Demo, report and deploy

| ID | Task | Status | Owner | Why | Was |
|---|---|---|---|---|---|
| DEMO-1 | Deploy to Supabase and Railway (`docs/deploy.md`; Supabase project created, migrations deploy from `main`) | open | both | Demo on the phone | T1.11 |
| DEMO-2 | Demo dry run on the voice-forward UI | open | both | | new |
| DEMO-3* | Final report draft (Claude drafts sections from the evidence; Kate edits) | open | both | Dec 11, target Dec 4 | T2.12 |
| DEMO-4* | Merge into `main`, tag the submitted commit, keep `data/archive-raw`, delete merged `ws/*` branches (incl. duplicate `ws/w-g`), give graders access | open | both | The midpoint merge was katherine-wang1/conversational-briefing-agent PR #5 | T1.16 |

---

## Decisions

### Open (need Kate)

| # | Decision | Context | Recommendation |
|---|---|---|---|
| D1 | Newsroom prompt and model (was Q1) | Default Sonnet + v4. Round 1 (6 of 12): Haiku + old prompt best 5 of 6, Sonnet v4 worst 3 of 6. Round 2 (ai only): no clear winner (best: Sonnet + old 2, Sonnet v5 1, Haiku + old 1; worst: Haiku + old 2, Sonnet v5 1, Sonnet + old 1). Sonnet ~$0.09 per beat; Haiku returned short lists in 5 of 24 runs, once a single story. Answer keys `docs/rating/lists_key_2026-11-27.json`, `docs/rating/lists_key_round2_2026-11-27.json` | Decide after NR-1..NR-5 on tune/test days (NR-6). Lean Sonnet for reliability |
| D5 | Story-note model (was Q7) | Haiku $0.0008 per note, ~$0.06–0.09 per run; Sonnet ~20× (estimated). Notes read well on Haiku (`docs/smoke/2026-11-27/story_notes/`) | Keep Haiku; compare Sonnet on ~10 stories if time allows |
| D8 | Stored-set mix (VO-1) | Smoke run: 13 of 20 the user's finds. E2E: 13 of 14 beyond the brief were the user's finds, 1 shared. Important shared stories with low relevance drop out (XPeng, importance 90, relevance 0.15) | Reserve about half for shared stories |
| D10 | Licensed full text (was Q6; absorbs D3 Exa and F12) | Snippets and free coverage cut stories without note text from 21 to 3 of 80 (`docs/smoke/2026-11-27/paywalls/results.md`); 7 of 80 in run 4; summary-level notes are thin. Options (unverified): NYT Developer API (free, licensed abstract and lead paragraph), Event Registry article bodies (free 2K tokens, $90 for 5K), Guardian Open Platform (non-commercial), Exa (no key here), enterprise wires | Try NYT API and Event Registry bodies before paying; probably after Dec 4 |
| D11 | Background knowledge in the brief (was Q4) | `docs/architecture-revisit.md` part 2 leans yes for stable context (what a company is, what a term means) while news claims stay cited. Notes already allow it in `context` | Yes, marked as background so C8 keeps it (BR-4) |
| D12 | Should the "outside your usual" slot stay? (was Q2) | Kate doesn't want it labelled; it brought in the energy stories along with the SMR interest | Keep the slot unlabelled, one story at most; revisit after BR-1 |
| D13 | Brief length and stories (was Q3) | `length_s` is 180 s; six stories with real context won't fit | Fewer stories (4) with more context at ~4 min; the rest go to the stored set for the conversation |
| D14 | Statements vs news (was Q5) | Closed into v5 (D4); keep in any newsroom prompt | Keep (NR-4) |
| D15 | Do source links matter? (was Q8) | Kate: not much | Keep citations internally for grounding; don't build link display (VO-7) |
| D16 | Free-coverage search provider (was Q10, F22) | 57% of Tavily news hits are google.com/url redirects that mostly fail (403/429); Exa or Brave return direct links | Stay on Tavily until Dec 4; note as a known limit |
| D17 | Evaluation scope for Dec 4 | 7 days; Wayback archive out of reach; rebuilt days are thinner | Evaluate on rebuilt plus live days of Nov–Dec only; drop the Aug–Oct Wayback replay; state it in the report |
| D18 | Voice-forward UI: what the voice controls and what stays on screen | New (APP-1) | Kate to sketch the must-haves; Claude proposes a layout |

### Closed

| Date | Item | Outcome |
|---|---|---|
| 2026-11-26 | Balance research vs product | Evaluated config stays replayable and reproducible; the live product may use the best off-the-shelf discovery; both tested side by side (`docs/architecture-revisit.md`) |
| 2026-11-26 | BUILD-1 amended (PRD §6.0) | Provider search, incl. Anthropic web search, may sit behind our `search` tool as a live backend; a provider-run agent is a comparison arm only |
| 2026-11-26 | Keys optional | With fallbacks, so the README quick-start works for graders |
| 2026-11-27 | Newsroom default | Editor newsroom, Sonnet 5.5 (OpenRouter), prompt v4 (`docs/smoke/2026-11-27/editor_multiday/results.md`); `classic` stays selectable. Reopened as D1 after round 1 |
| 2026-11-27 | Ranking provider (A1) | Keep our own feeds plus the editor; Event Registry tested as an arm (`docs/smoke/2026-11-26/compare_all_beats/`) |
| 2026-11-27 | Anthropic key use | Avoid; everything through OpenRouter. `claude_agent` runs on hold |
| 2026-11-27 | D2 store size and the 80-story pool cap | Leave as is |
| 2026-11-27 | D4 statements and commentary | A statement is news only when it announces or reveals something; in prompt v5 with one-story-per-saga and variety at the top |
| 2026-11-27 | D6 story-note reuse | Keep `story_notes.reuse_ok_hours: 6` |
| 2026-11-27 | D7 notes in the newsroom job | Keep there; a notes failure never fails the newsroom job |
| 2026-11-27 | D9 paywalled stories | Publisher headline and summary as attributed sources, plus one free-coverage search per blocked story: 21 → 3 of 80 without text (`docs/smoke/2026-11-27/paywalls/results.md`) |
| 2026-11-27 | Per-user token budget | 90k → 150k (`docs/e2e/run1_budget90k/`), then 300k as a safety cap (~$0.015 per user at most on Haiku) |
| 2026-11-27 | Brief writer output limit | 4,000 → 10,000 tokens (`docs/e2e/run3_write_truncated`) |
| 2026-11-27 | Paragraph-level citation (F17) | Not done: listing sound came from the prompt rules; v2 keeps sentence citations (`docs/smoke/2026-11-27/brief_audio/results.md`). Revisit if C8 finds its own passages |
| 2026-11-27 | Audio prompting (A3) | Brief prompt v2, claim check v2, voice turn v3 (`docs/smoke/2026-11-27/brief_audio/results.md`) |
| 2026-11-27 | Midpoint video and report | Done (Kate) |

### Done since the architecture review (evidence)

- Concurrent collection and scoring (7c729c6), concurrent C2 plans and C6 packs with paywall-aware fetching
  (86e1ce8), slim scoring output (094b296), at most one newsroom job at a time (63291ba).
- fastembed removal: moot on the default path (the editor never imports it; only `classic` does). T1.10
  (clustering threshold) is classic-only and dropped.
- Shared store: newsroom + story notes (8e93960, `docs/smoke/2026-11-27/story_notes/`), per-user stored set
  and `get_story` (81b41d2, `docs/stored-set.md`, `docs/smoke/2026-11-27/stored_set/`), Google News
  resolution (7b82254, `docs/smoke/2026-11-27/google_news_resolve/`). Story-notes rerun after resolution (F5)
  is in the paywall smoke.
- T1.18 (keep big-news stories when fetches fail): done through D9.
- Midpoint items (T1.1–1.5, 1.21, 1.22, 1.25, 1.26) with their evidence: `docs/archive/TODO-midpoint.md`.

---

## Kate's feedback log

**2026-11-27, on e2e run 4**
- Energy stories in the brief came from the test user's SMR interest (0.3, "keep me posted"), the e2e
  script following all three beats (fixture default is ai only), and the "outside your usual" slot.
- URLs reaching the user: not important (VO-7).
- The spoken brief is too disjointed, more news anchor than podcast. Wants a narrative meant to be heard,
  with more context per story (what it is, why it's happening, background), scaled by novelty using the
  history of stories already given (GTA VI got two sentences). (BR-1..BR-3)
- No segment labels ("energy", "outside your usual beat"). (BR-1)
- Newsroom lists, round 2 (ai only): no clear winner (D1). Every list concentrated on Nvidia, chips and
  markets; topics repeat across days; prefers developments (model releases, deals) over someone saying
  something; prefers complete headlines; wants more on what a model release changes. (NR-1..NR-4)
- Round 1 (6 of 12): Haiku + old prompt best 5 of 6, Sonnet v4 worst 3 of 6. Lists lose for clustering
  (several stories about one company or saga) and gain for headline-like titles.
- Consider a new prompt combining parts of several (NR-5).

**2026-11-27, on the consolidation**
- Aim to be done by Dec 4; find a faster way to get evaluation days, or collect older news (EV-2, D17).
- Add: redesign the UI to be fully voice-forward (APP-1).
- Test the memory structure and understand its design. Memory should track the stories given to the user
  (they define what is new to them), their interests, and their specific asks (MEM-1..MEM-4).

---

## Memory today (checked in the code, 2026-11-27)

| What | Where it lives | Gaps |
|---|---|---|
| **Interests** | `MemorySnapshot.interests` (`app/contracts/memory.py`): label, subtopic, entity, kind (`topic`, `entity`, `keep_posted`), weight, confidence, pin, shown/engaged counts, source, evidence. Delivery prefs (`length_s`, depth, technicality, style, sources) beside them | Updated only by `memory_update` after a session (rules in `app/agents/memory/rules.py`, LLM proposals in `reflect.py`); decay runs only then (MEM-5) |
| **Stories given** | No table of its own. `shown_history` (`app/jobs/agent_inputs.py`) rebuilds `ShownStory` rows from the last 14 days of brief scripts, with heard/engaged/saved from session signals | Brief stories only (not stored-set stories opened in voice, F8); headline only, not what was said; `interest_ids`, `subtopics`, `update_of` never filled (MEM-2, MEM-3) |
| **Signals** | `memory_signals` per session: dive, followup, save, bookmark, more/less like, stop covering, keep posted, skip, not reached, delivery feedback, time available | Feed interests; only tied to brief stories |
| **Specific asks** | `open_questions` (bookmarked questions, follow-up research, attached to a later brief); `keep_posted` interests; `topic_requests` (onboarding text only) | Spread over three stores; follow-up research never run live (BR-9, MEM-4) |
