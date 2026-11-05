# Integration smoke run — 2026-11-05 (REAL outputs)

Everything in this folder is unedited output from one live local run of the integrated system on branch
`ws/integration` (ORCHESTRATOR §5). Nothing was hand-edited. Failures are recorded below as they happened.

**Setup.** Postgres 16 (local container) with all migrations applied (`briefing_smoke` database). Real web
process (`uvicorn app.main:app`, `APP_ENV=dev`, `DEV_AUTH_BYPASS=1`) and real worker process
(`python -m app.jobs.worker`, `AGENT_PORTS=real`). Model: OpenRouter `anthropic/claude-haiku-5.5`
(`dev_default`) for every role. `LLM_SPEND_CAP_USD=1.00` per process (Router hard cap). TTS: fake.
No STT. No search provider key (live search unavailable). Headline sources: the **interim** adapters
in `app/sources/_interim.py` (RSS feeds, HN front page via the Firebase API, Techmeme RSS), because
W-A's parsers are not merged yet. `hf_papers`, `arxiv` and `frontpage` sources had no adapter and
were skipped (logged).

## Spend

| | USD |
|---|---|
| OpenRouter key usage at start (`GET /api/v1/key`, before any live call) | 1.894428602 |
| at end | 1.967427622 |
| **delta (everything in this run, incl. 3 debug calls)** | **0.0730** |
| Router ledger (JSONL) = `llm_calls` table, 46 calls | 0.0705 |

Ledger and `llm_calls` agree row for row (46 / 46). The ~$0.0025 difference is three raw provider
calls made while diagnosing failure 1 below, which bypassed the Router. See `ledger_summary.json` and `llm_ledger.jsonl`.

## Steps

| § 5 item | Result | Evidence |
|---|---|---|
| 1. Live AI-beat digest (newsroom) | **Worked**, two runs. Run 1 (08:22Z, reasoning on): 119 observations → 106 stories → 30 entries; the 3 score batches hit `max_output_tokens` (finish=length) and each needed a repair call, and the 4th batch was skipped on the token budget (20 stories were scored by rules only). Run 2 (08:39Z, reasoning off): 110 obs → 98 stories, **97 continued from run 1** (story identity across runs via the new `stories` history), 80 scored, 30 entries, no degradation, $0.015. | `digest_ai-20261105T0822Z-c22b5dbc.json`, `digest_ai-20261105T0839Z-bb9e1a2f.json`, `newsroom_trace_*.json` |
| 2. Brief and source packs for an onboarded test user | **Worked on retry.** `POST /api/onboarding` → `onboarding_seed` (real `memory.onboarding_extract`) → `cos_daily`. **Attempt 1 failed 3/3** (see failure 1). After the fix, the job was retried through `POST /api/admin/jobs/26/retry` and succeeded: 4 stories (1 big news, 3 for you), 19 sentences, every story sentence cites passages, claim check ran, 4 source packs, fake-TTS audio manifest with 19 segments, `status=ready`. | `brief.json`, `brief_script.txt`, `source_pack_sample.json`, `source_packs_all.json`, `cos_trace_*.json` |
| 2a. Behaviour without live search | Degrades without failing, but wastefully. C1 planned 10 queries; C2 ran all 10 against the search tool, and each returned `search_unavailable` (each still charged the search budget). The refine model then called `finish`. The result was 0 personal finds and a **digest-only** candidate pool. C6 fetches: 5 ok, 3 failed (openai.com 403 ×2, wsj.com 401) and fell back to other URLs or snippets. | `cos_trace_*.json` (step C2 / C6 events) |
| 3. Scripted text-mode voice session → memory update | **Worked.** Over the real `/ws/session/{id}`: play → interrupt at `s010` → question → grounded 3-sentence answer with citations (one real `voice.turn` call; it also recorded a `more_like` signal) → `resume_brief{s010}` → end. The `memory_update` job (real `memory.reflect` + rules) reinforced "open-weight model releases" from 0.40 to 0.775 weight (confidence 0.3 to 0.8) and stored the outcome and report on `sessions.memory_outcome`. | `session_transcript_ws.jsonl`, `sessions_db.json`, `memory_diff.json` |
| 4. Job queue runs `newsroom_run` and `cos_daily` via the worker | **Worked.** Both ran in the separate worker process (`jobs.json`), along with `onboarding_seed`, `tts_render`, `memory_update` ×2, `spend_guard` and `cleanup`. The scheduler's 06:00Z newsroom slots for the three beats were pre-empted with cancelled placeholder jobs, so that only the manually enqueued AI run spent money (rows 1–3 in `jobs.json`). | `jobs.json` |
| 5. Web app against the real backend (headless Chromium, fake TTS) | **Partial.** Dev sign-in, `/api/me`, `/api/briefs/today` (real brief rendered), `POST /api/sessions`, WebSocket hello → `READY`/`PLAYING_BRIEF`, and `/media/*.mp3` fetches all work. **Audio does not play.** Fake TTS writes `FAKE-MP3:` bytes, which the browser can't decode (`MediaError 4`), so the player skipped every segment to `open_floor`. The interrupt flow could not be exercised in the browser this way. | `web_vs_real_backend.json` |

## Failures (as they happened)

1. **cos_daily failed 3/3 on the first attempt** with `StructuredOutputError ... Extraction after repair:
   Invalid JSON: EOF` in C6 (`cos.extract_passages`). Cause: Haiku 5.5's adaptive reasoning used the whole
   1500-token output budget (`finish_reason=length`, empty `content`, opaque `reasoning_details`). That
   happened on the call and on the repair. A second problem made it fatal: the Router's `StructuredOutputError`
   was not the exception W-D's fallbacks catch (`StructuredOutputFailure`), so the whole run failed instead
   of using the lead passage. Fixes (commit d8d9385): `dev_default` gets `params: {reasoning: {effort: none}}`;
   new contract exception `StructuredOutputInvalid`, which the Router error subclasses, so W-D maps it to
   `StructuredOutputFailure` and the newsroom degrades to rules-only. Each failed attempt cost about $0.007.
2. Newsroom run 1 output truncation and token-budget skip (above). Same root cause (reasoning tokens).
   Run 2 with reasoning off had no degradation.
3. Web audio with fake TTS (step 5) — not a backend bug, but there is no browser-decodable fake audio.
