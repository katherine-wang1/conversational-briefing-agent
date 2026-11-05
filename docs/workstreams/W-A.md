# W-A: Archive collectors + source parsers

**Branch:** `ws/archive` (from `claude/mvp-proposal-design-td19a9`)
**Owns:** `eval/archive/`, `app/sources/`, `tests/fixtures/sources/`, `tests/unit/sources/`
**Contract changes:** additive only. List them in your summary.

## Read first
- `docs/parallel-work.md`
- `docs/02-technical-design.md` §6 (beats, sources), §15 (replay archive), §27 spike 4
- `app/contracts/sources.py` (`HeadlineObservation`, `Document`, `Comment`, `Engagement`)
- `config/beats/*.yaml` (draft source lists, `verified: false`)

## Network
This workstream needs outbound access to:
- web.archive.org, archive.org;
- www.techmeme.com;
- hn.algolia.com, news.ycombinator.com;
- export.arxiv.org;
- huggingface.co;
- the beat source domains.

Check each host with curl first. If any are blocked, report exactly which ones, build against saved fixtures, and **never fabricate collected data**.

## Build
1. **Parsers (`app/sources/`)**, shared by live adapters and archive collectors. Each returns `HeadlineObservation`s. Cover:
   - Techmeme front / archive pages;
   - HN front page HTML (rank, points, and comments *as shown*);
   - HN Algolia items;
   - arXiv API listings;
   - Hugging Face daily papers;
   - RSS / Atom (feedparser);
   - generic outlet front pages (title + link + position).

   Save real HTML/XML fixtures in `tests/fixtures/sources/` and unit-test every parser.
2. **Collectors (`eval/archive/`)** for **2026-08-01 to 2026-10-31**. The range is deliberately broad; the evaluation window is chosen later.
   - Techmeme dated archive pages (find the URL scheme and granularity).
   - HN front page via the **Wayback CDX API**: rank and points **as of each snapshot**, never final points. HN Algolia for story metadata and comments (with `created_at`).
   - arXiv listings by submission date: cs.CL, cs.LG, cs.AI (cs.RO optional).
   - Hugging Face daily papers by date (direct or Wayback).
   - Each RSS / front-page source in `config/beats/ai.yaml` via Wayback CDX snapshots. Then energy and physical_ai if time allows.
   - Article bodies: the Wayback copy nearest after the publish time (≤ publish + 2 days), extracted with trafilatura, with hidden text, scripts and zero-width characters stripped.
   - Collectors must be **resumable** (a disk cache of raw responses), **rate-limited** (about 1 request/second to Wayback, with backoff), and **idempotent**.
3. **Storage:** `eval/data/archive.sqlite` with these tables (TDD §15.3):
   - `observations`;
   - `documents` (with `available_at` = when it became observable);
   - `documents_fts` (FTS5 over title + text);
   - `comments`;
   - `meta`.

   Containers are ephemeral, so also export **gzip JSONL shards (≤ 50 MB each)**, committed to the separate branch **`data/archive-raw`**, plus `eval/archive/rebuild.py` to rebuild the SQLite file from the shards. The collector must support periodic shard export during long runs.
4. **Feasibility report:** `docs/spikes.md` § "Archive". For each source:
   - availability per day across Aug–Oct 2026;
   - snapshot granularity;
   - body-fetch success rate;
   - **HN front-page Wayback coverage per day** (needed for TDD OQ-14).

   Update `config/beats/*.yaml`: set `verified: true` for confirmed URLs and fix wrong ones.
5. **Datasheet:** `eval/archive/datasheet.py` writes per-source per-day counts, gaps, and success rates to `meta`, and to `docs/archive-datasheet.md`.
6. **Hand-off:** run a short smoke collection (1–2 days), then report the exact command for the full run. The orchestrator runs the full collection in the background.

**Not in scope:** hindsight labels. They wait for the newsroom clustering code (W-C).

## Done when
- Parsers are tested.
- Collectors are resumable and rate-limited.
- The smoke collection has succeeded.
- Shard export works.
- The feasibility report and datasheet are written.
- `pytest` and `ruff` are green.
