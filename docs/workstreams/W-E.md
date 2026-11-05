# W-E: Platform (database + privacy, API, jobs, tools, voice session server)

**Branch:** `ws/platform` (from `claude/mvp-proposal-design-td19a9`)
**Owns:** `supabase/migrations/`, `app/db/`, `app/api/`, `app/jobs/`, `app/agents/voice/`, `app/speech/`, `app/tools/`, `app/main.py`, `Dockerfile`, `docs/deploy.md`, `tests/isolation/`, `tests/unit/platform/`
**Contract changes:** additive only. List them in your summary.

## Read first
- `docs/parallel-work.md`
- `docs/02-technical-design.md` §2, §5, §10–12, §20–24
- `app/contracts/*` (especially `session.py` and `tools.py`), `config/agent.yaml`, `docker-compose.yml`, `.env.example`

## Build
1. **Migrations** (`supabase/migrations/*.sql`):
   - every table in TDD §10.2, with `run_id` columns;
   - roles `app_rw` (**not** BYPASSRLS) and `app_admin`;
   - **RLS policies** keyed on `current_setting('app.user_id')`, exactly as in §10.3.

   Apply them to local Postgres (docker-compose).
2. **DB layer** (`app/db/`): a psycopg3 async pool; a `user_scope(user_id | None)` context manager that sets `app.user_id` inside the transaction; one repository per table. No unscoped queries.
3. **Isolation test suite** (`tests/isolation/`), a **hard requirement**:
   - for every per-user table, user A can't read or write user B's rows;
   - a newsroom context (empty `app.user_id`) sees zero per-user rows and can't write them.
4. **API** (`app/api/`, FastAPI):
   - the routes in TDD §11;
   - Supabase JWT verification via JWKS, with a `DEV_AUTH_BYPASS` flag for local use;
   - the invite allowlist;
   - JSON export and account deletion.
5. **Jobs** (`app/jobs/`):
   - a Postgres queue (`FOR UPDATE SKIP LOCKED`) with dedupe keys, priorities, and retries with backoff;
   - a scheduler tick: per-user `cos_daily` at brief_time − 60 min in the user's timezone, and the newsroom at beat times;
   - worker entry point `python -m app.jobs.worker`;
   - handlers that call the agent entry points (W-C / W-D). Use stubs or fakes until those are merged;
   - the spend-guard job;
   - a Postgres sink for the W-B ledger interface (table `llm_calls`).
6. **Tool layer** (`app/tools/`):
   - **`search`**
     - Live: an adapter chosen by `SEARCH_PROVIDER` (`tavily` | `exa` | `brave`) with `SEARCH_API_KEY`. Implement all three adapters, tested with mocked HTTP. **The live key may not be set yet.** If so, live search returns a clear "search unavailable" tool error, never fake results.
     - Replay: SQLite FTS5 over `eval/data/archive.sqlite`, filtered to `available_at <= ctx.now`.
   - **`fetch`**
     - Live: httpx + trafilatura + the shared `documents` cache.
     - Replay: archive lookup.
   - **`headline_source`:** dispatches to the `app/sources` parsers (W-A); replay returns archive observations up to `at`.
   - **`hn_comments`.**
   - The untrusted-content wrapper (TDD §5.3).
   - **Contract tests:** live and replay backends return identical shapes. Live tests are skipped without network or keys. Build a tiny fixture archive for these tests.
7. **Voice session server** (`app/agents/voice/`):
   - WebSocket `/ws/session/{id}` implementing the state machine (§12.1) and `app/contracts/session.py`.
   - Interrupt at any point and record `{segment_id, offset_ms}`.
   - Context assembly per §12.2, with a cached prefix: system + brief + packs.
   - Streaming turns via `ModelClient.stream`, with the voice tools from §12.4:
     - `quick_search`, with a spoken filler and a 3 s timeout;
     - `bookmark_question`;
     - `save_for_later`;
     - `note_preference`;
     - `navigate`.
   - Sentence splitting → TTS adapter → `tts_chunk`s.
   - Resume from the interrupted sentence.
   - Emit memory signals; enqueue `memory_update` at session end.
   - **Text mode** (no TTS), used by simulated users.
8. **Speech adapters** (`app/speech/`):
   - `TTSProvider` and `STTProvider` interfaces.
   - One HTTP implementation each, with keys from env (`TTS_API_KEY`, `STT_API_KEY`).
   - A fake for tests.
   - The brief pre-render job, which writes an `AudioManifest` (storage abstracted: local disk in dev, Supabase Storage in prod).
9. **`Dockerfile`** (multi-stage: build `web/` if present, then Python) and **`docs/deploy.md`**: step-by-step Supabase and Railway setup for Kate, covering the two services (web + worker) from one image, every env var, auth providers, the storage bucket, and applying migrations.

Use fakes for `ModelClient` and the agent entry points, so you're never blocked on other workstreams.

## Done when
- Migrations apply.
- The **isolation suite passes**.
- The API and WebSocket run locally with fakes: a scripted text session (hello → interrupt → utterance → answer → resume → end) passes in a test.
- The job queue is tested.
- `pytest` and `ruff` are green.
