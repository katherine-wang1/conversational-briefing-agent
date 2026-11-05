# Build status — 2026-11-05 (orchestrator session)

Branch: `claude/mvp-proposal-design-td19a9`. All seven workstreams (W-A … W-G) are built, reviewed and merged, plus an integration pass. Collected data lives on `data/archive-raw`.

Related docs: `docs/preflight.md` (network/keys), `docs/spikes.md` (Models + Archive), `docs/archive-datasheet.md`, `docs/smoke/2026-11-05/` (real live outputs), `docs/deploy.md`, `docs/iphone-checklist.md`, `eval/archive/README.md`.

## 1. What works

**Tests:** `pytest` 533 passed, 3 skipped (live tests, opt-in), with `TEST_DATABASE_ADMIN_URL` pointing at a local Postgres; `ruff` clean. Web: typecheck, lint, build, 26 vitest tests and a 35-check Playwright interrupt-flow run against the mock server (reported by W-F; not re-run after merge).

| Workstream | Built | Evidence |
|---|---|---|
| W-E platform | Postgres schema + RLS (app_rw is not BYPASSRLS), FastAPI routes (TDD §11), job queue + scheduler + worker, tools (search adapters, replay FTS search, fetch, headline_source, hn_comments), voice session server (text mode + TTS chunking), speech interfaces, Dockerfile, `docs/deploy.md` | 98 isolation tests (users can't see each other's rows via SQL, API or WebSocket) |
| W-B model layer | OpenRouter + Tinker clients behind `ModelClient`, role router, call cache, cost ledger (JSONL + `llm_calls`), token budgets, hard spend cap, bake-off CLI, spikes | `docs/spikes.md` § Models |
| W-C newsroom | N2 normalise, N3 clustering + story identity across days, N4 features, N5 LLM scoring (with rules fallback), N6 digest, traces | live digest in `docs/smoke/2026-11-05/` |
| W-D chief of staff | C1–C8 (plan, bounded search loop, candidate pool, scoring, code selection, source packs with verbatim passages, writing with citations, claim check), memory rules (TDD §9.3), reflect, onboarding extraction, follow-up research, B1/B2/B5/A1/A3 flags | live brief + packs in `docs/smoke/2026-11-05/` |
| W-G evaluation | 18-profile simulated-user panel + coverage check, simulator, all code graders, LLM-judge framework, labelling store + Streamlit app, runner skeleton, stats, hindsight labels, 50-item injection set, T3 question generator | fake-client tests only; **no evaluation runs** |
| W-F web | React PWA: all screens, single-audio-element engine, interrupt triggers (tap / button / Media Session), VAD, browser + server STT, answer queue, resume, stage-2 headphones mode, mock server | `docs/iphone-checklist.md` |
| W-A archive | Parsers for Techmeme, HN (HTML, Algolia, comments), arXiv, HF papers, RSS/Atom, front pages, Wayback CDX; resumable, rate-limited, robots-respecting collectors; SQLite + FTS archive; gzip JSONL shards to `data/archive-raw`; rebuild, status, datasheet | `docs/spikes.md` § Archive, `docs/archive-datasheet.md` |

**Live end-to-end smoke (`docs/smoke/2026-11-05/`, real unedited outputs, `anthropic/claude-haiku-5.5` for every role):**
1. Live AI-beat digest: 119 observations → 106 stories → 30 entries; a second run matched 97/98 stories to the first (story identity works).
2. A test user created through the onboarding API → `cos_daily` → brief with 4 stories, 19 sentences, every story sentence cited to its source pack, claim check run, 4 source packs. The first attempt failed (reasoning tokens used the extraction output budget); fixed by turning reasoning off in `dev_default` and making structured-output failures degrade instead of failing the run.
3. Scripted text-mode voice session over the real WebSocket: interrupt → question → cited answer → resume → end → `memory_update` raised the asked-about interest (0.40 → 0.775).
4. The worker ran `newsroom_run`, `cos_daily`, `onboarding_seed`, `tts_render`, `memory_update`, `spend_guard`, `cleanup`.
5. Web app against the real backend (headless Chromium): sign-in, Today screen with the real brief, session creation and WebSocket hello work. **Partial:** fake TTS bytes aren't decodable audio, so in-browser playback/interrupt against the real backend wasn't exercised (it was exercised against the mock).

Fixes made at integration: real agent entry points wired into jobs; the Router plugged into the Postgres ledger; fetch-budget double charge removed; search loop now stops after two consecutive "search unavailable" errors (it used to burn the whole search budget); W-A's live adapters registered with `headline_source` (interim shim removed); additive migrations for new contract fields.

**Live model spend in this session:** OpenRouter usage $1.847 → $1.968 = **$0.121** (spikes $0.047, integration smoke $0.073), plus Tinker ≈ $0.003 (from the price table; Tinker's billing API hadn't shown the events yet). Well under the $10 cap.

## 2. Archive collection

**Blocked host:** `web.archive.org` is blocked by egress policy for every cloud session (confirmed in two sessions, even on Full network access). So everything Wayback-based runs on Kate's laptop.

**Cloud run (reachable sources), started 2026-11-05 08:55 UTC**, in the background from a checkout of `ws/archive`; pushes shards to `data/archive-raw` (`shards/direct/`) every 30 min. Sources: HN Algolia (+ comments), HN `/front?day=` (labels only), arXiv cs.CL/LG/AI/RO, HF daily papers (labels only for upvotes), current RSS feeds, Techmeme river, article bodies from origin (flagged `origin_live`), and Wayback availability probes. Expected 4–5 h. If the session ends first, resume (idempotent) with:

```bash
.venv/bin/python -m eval.archive.collect --start 2026-08-01 --end 2026-10-31 --sources reachable --cache-dir eval/data/cache --export-shards-every 30 --push-shards
```

**Feasibility findings (W-A smoke + probes):**
- **HN front page, Wayback coverage (OQ-14):** 89/92 days (97%) have a capture on the same UTC day; Techmeme 88/92 (96%). This is from `archive.org/wayback/available` probes only. Captures within 1 h of 06:00 UTC exist on only 52/92 days. The real check needs the laptop run's CDX listings and parsed snapshots.
- **Techmeme:** dated archive pages are behind a Cloudflare challenge (not circumvented). `/river` ignores `?date=` and only reaches back ~5 days, so the Techmeme history must come from Wayback.
- **RSS:** most feeds keep only 1–3 weeks of items. OpenAI, HF blog and DeepMind feeds reach back far enough to backfill directly.
- **Article bodies from origin:** 73/104 (70%) extracted; 15 bot checks, 12 HTTP 403. For 22/80 the page date didn't match the publish date, so origin bodies may be later revisions. The Wayback body path is the preferred one.
- **Wayback throttling:** archive.org returned 429 after ~300 requests, then sustained ~15–20 requests/min. The full Wayback run (~40–64k requests) could take **35–70 h**; `eval/archive/README.md` has a staged order (HN + Techmeme every 3 h first, ~3,300 requests).

## 3. Recommended replay window

Rule (TDD §15.6): ~40 days (10 dev + 30 test), start after every agent model's training cutoff, end ≥ 14 days before today (→ on or before 2026-10-22), ≥ 90% daily Techmeme and HN snapshot coverage.

- Published cutoffs: Claude Haiku/Sonnet 5.5 Jun 2026; GPT-6 Luna May 18, 2026. Gemini 3.5 Flash-Lite, DeepSeek V4.1 Flash, Qwen 3.8 Flash, Mistral Small 2603 and Inkling-Small publish none; their release dates allow cutoffs as late as Aug–Sep 2026.
- **Recommendation: 2026-09-12 → 2026-10-21** (dev 09-12 … 09-21, test 09-22 … 10-21). That's the latest window that satisfies the 14-day rule. It is at least 2.5 months after the published cutoffs, and same-day capture coverage across Aug–Oct is 96–97% (probe-level).
- Conditions: (a) confirm ≥ 90% coverage *near brief time* for these days from the laptop Wayback run; (b) keep DeepSeek V4.1 and Qwen 3.8 (released 2026-09-10 / 08-26) out of the bake-off shortlist for importance/scoring roles, or check them with a leakage probe, since their cutoffs could fall inside the window.
- If the laptop run shows thin brief-time coverage, the OQ-14 default applies: drop HN as a signal in both replay and live modes.

## 4. What Kate needs to do

1. **Run the Wayback collection on your laptop**, as soon as possible (it is the long pole). Steps are in `eval/archive/README.md`. Short version:
   ```bash
   git clone https://github.com/katherine-wang1/conversational-briefing-agent.git && cd conversational-briefing-agent && git checkout claude/mvp-proposal-design-td19a9
   ```
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
   ```
   ```bash
   caffeinate -i .venv/bin/python -m eval.archive.collect --start 2026-08-01 --end 2026-10-31 --sources wayback --export-shards-every 30 --push-shards --offline-patience-minutes 240 2>&1 | tee -a eval/data/wayback-run.log
   ```
   Re-run the same command to resume after sleep or network loss. Follow the README's staged order if it's throttled.
2. **Add a live search key:** `SEARCH_PROVIDER=tavily` and `SEARCH_API_KEY=<key>` (OQ-15b default: Tavily free tier). Without it the agent falls back to digest-only candidates.
3. **Create the Supabase and Railway projects**: steps and every env var in `docs/deploy.md`.
4. **Pick a TTS provider** (OQ-7) and add `TTS_API_KEY` (and `STT_API_KEY` if you choose server STT, OQ-8). Until then briefs have no audio in the real backend.
5. **Run `docs/iphone-checklist.md`** on your iPhone (Safari and home-screen app) once the app is deployed over HTTPS.
6. **Decide the open questions (TDD §26):** OQ-6 window (recommendation above), OQ-14 HN signal (keep for now, pending laptop data), OQ-4 labelling time, OQ-16 bake-off shortlist (exclude late-cutoff families from scoring roles), and the design defaults OQ-7 … OQ-23.
7. **Housekeeping:** delete the leftover remote branch `ws/w-g`. It's a duplicate of `ws/eval`, and the permission checks blocked me from deleting it.

## 5. Known gaps (next engineering work)

- Per-candidate interest ids aren't saved with the brief, so `ShownStory.interest_ids` is empty and the repeated-skip rule can't attribute skips. Persist candidate scores (the `candidates` table exists).
- The chief of staff doesn't apply memory decay before a run; memory and follow-up traces aren't saved.
- Follow-up research hasn't run live yet; the newsroom fetch path is covered only by unit tests.
- TDD §20 "importance-only quick brief after 3 minutes" isn't implemented (TODO in `app/api/routes.py`).
- Spend cap is per process, not global (the daily `spend_guard` job is the global guard).
- Clustering thresholds (`tau` 0.78) haven't been tuned on real fastembed output.
- Hindsight labels: `eval/archive/labels.py` should call `eval.tasks.hindsight.build_labels` with W-C's clustering; LLM topic labelling isn't implemented.
- W-F's `/api/history` and brief-before-onboarding (409/202) shapes differ slightly from what the web app assumes; check during the first real-backend run.
- Tests that use the newsroom/cos local fakes can switch to `app/llm/fake.py`.

## 6. Next steps against the TDD §25 plan

- **W1 (to Wed Nov 11):** finish the archive (cloud run today; laptop Wayback run starting now). Deploy to Supabase + Railway, then fix the gaps above. Wire hindsight labels to the newsroom clustering and compute labels for collected days. Datasheet v0 after the laptop run. Confirm the replay window.
- **W2 (Nov 12–18):** TTS provider + iPhone checklist. Kate's onboarding on the deployed app. **Self-study starts Sun Nov 15.** Labelling session 1 on historical news.
- **W3 (Nov 19–25):** replay backends in the runner, newsroom replay over the window, bake-off on the dev slice → pin `config/models.yaml`, judge validation, code freeze, test runs launch Tue Nov 24.
- **W4 (Nov 26–Dec 2):** grading, failure analysis, report.
