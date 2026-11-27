# Editor prompt v4 across four days (three rebuilt, one live)

Why: the v3 prompt's importance examples (selloffs, credit-rating actions, attorneys-general actions)
were written after seeing one day's misses, so testing on that day overstated v3. v4 keeps v3's
general grouping rule (one story = one real-world event, no round-ups) and replaces the examples with
general criteria: consequence, novelty and coverage, with no kind of news favoured by type. It also
says that individual readers' interests are applied later, not in the shared ranking. Prompt:
`app/agents/prompts/newsroom.editor_v4.md`. v3 stays as its own file so its recorded results still
match their prompt.

Everything ran through OpenRouter; the Anthropic key was unset.

| Arm | Model (OpenRouter) | Prompt |
|---|---|---|
| `editor` | Haiku 5.5, reasoning off (`dev_default`) | v2 (`newsroom.editor`) |
| `editor_v4` | Haiku 5.5, reasoning off | v4 |
| `editor_sonnet_v4` | Sonnet 5.5, reasoning effort medium | v4 |

## Days

| Day (window ends) | How | In-window dated items: ai / energy / physical_ai |
|---|---|---|
| Thu 2026-11-19 20:00 UTC | rebuilt (`collect --at`, backfill) | 271 / 301 / 120 |
| Mon 2026-11-23 20:00 UTC | rebuilt | 335 / 393 / 119 |
| Wed 2026-11-25 20:00 UTC | rebuilt | 378 / 338 / 180 |
| Thu 2026-11-26 22:09 UTC | live (`../../2026-11-26-late/`) | 663 / 560 / 299 |

**How a past day is rebuilt** (`eval/newsroom_compare/arms.py`, `backfill_source`):

- Google News search feeds swap `when:1d` for `after:<day-1> before:<day+1>`. Google News honours the
  range (checked: a Nov 24 query returned items from Nov 22–25 only).
- Other RSS feeds, arXiv and Hugging Face papers are read as they are now. Items published after the
  target time are dropped, and so are undated ones.
- Sources that only show the present are skipped: site front pages, Techmeme, the Hacker News front
  page, Reddit top.

**Biases:** a rebuilt day has about 40–60% of a live day's dated items, mostly from Google News, which
caps each query at 100 items. It has no front-page or Techmeme signals, so ranking leans on how widely
a story is covered. Google News returns results as indexed now, not as they stood on the day. These
inputs are fine for comparing prompts and models with each other, since every arm sees the same ones.
They aren't comparable with a live day's absolute numbers. Rebuilding gets thinner further back,
because RSS feeds keep only their latest items.

The replay archive on `data/archive-raw` (Aug–Oct 2026) was not used. Only its "direct" half was ever
pushed: Hacker News, arXiv and Hugging Face papers, with 5 Techmeme rows in total and none of the
outlets or Google News feeds the live editor reads. The Wayback half (outlet front pages and feeds)
was never collected.

## Results (12 runs per arm: 4 days × 3 beats)

| Arm | Short lists (< 20 stories) | Multi-event stories in top 10 (avg per run) | In all stories | Cost per beat |
|---|---|---|---|---|
| `editor` (Haiku, v2) | **4 of 12** (one energy run returned 1 story) | 2.0 | 65 | $0.005 |
| `editor_v4` (Haiku, v4) | 1 of 12 (16 stories) | **1.4** | 41 | $0.006 |
| `editor_sonnet_v4` (Sonnet, v4) | **0** | 1.5 | **35** | $0.094 |

"Multi-event stories" counts headlines that join items with ";", a rough count of separate events
merged into one story. Top-10 overlap between the arms is 6–8 stories on most beat-days. The
exception is energy on Nov 23, where v2 returned a single story.

## Reading (unrated)

- **v4 generalizes:** on every day the leads are the day's obvious stories for all three arms (the
  Anthropic IPO, Huang's exit and return, S&P's CreditWatch, Waymo's freeway ban and recall, the
  Senate permitting bill, the IEA outlook). With general criteria only, v4 still ranks market moves
  when they matter. On Nov 19 it has "Wall Street and European shares fall as Anthropic debut fails to
  revive AI trade" (#5). On Nov 23 Sonnet has "Huang's return sparks tech rally" (#5).
- **v4 bundles less on Haiku:** 30% fewer multi-event stories in the top 10, and 37% fewer over whole
  lists. Vague round-ups like v2's "US state and federal lawmakers push AI safety frameworks and
  regulation" give way to specific stories ("Newsom pitches California AI safety framework…").
  Haiku still writes some ("Robotaxi services expand testing: Zoox…, Uber and Nuro…"). Sonnet
  splits those into separate stories.
- **Haiku is unreliable on list length.** Five of 24 Haiku runs returned short lists. In one, Haiku
  stopped after a single story and 211 output tokens. In others it packed 130–145 candidates into
  14–16 stories. With v2 that cost a whole energy digest on Nov 23. Sonnet returned the full count in
  every run.
- **Sonnet costs about 16× more** than Haiku: $0.094 against $0.006 per beat, or about $0.28 a day
  for three beats. That's still about 1/17 of `claude_agent` on Sonnet ($1.64 for one beat).
- **Cross-beat duplicates:** Nvidia and Huang stories appear in physical_ai's top 5 (Nov 23) in every
  arm. That's the beat scope's call, but a per-user store across beats will need to dedupe stories
  that appear in several beats.

## Spend

| | This run |
|---|---|
| OpenRouter | $1.24 (`editor` $0.04, `editor_v4` $0.07, `editor_sonnet_v4` $1.13) |
| Anthropic | $0 |
