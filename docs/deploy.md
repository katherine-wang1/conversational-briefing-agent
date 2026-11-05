# Deploy: Supabase + Railway (and local dev)

This is the step-by-step setup for the live system (TDD §22). One Docker image runs as two Railway
services, **web** (API + WebSocket + PWA) and **worker** (jobs + scheduler). Supabase provides
Postgres (with RLS), Auth (magic link + Google), and Storage (brief audio).

Time needed: about an hour the first time.

---

## 0. Run it locally first (10 min)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
docker compose up -d db                                  # Postgres 16 on localhost:5432
MIGRATIONS_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/briefing \
  .venv/bin/python -m app.db.migrate --dev-role-passwords  # schema + RLS; app_rw/app_admin get dev passwords
cp .env.example .env                                     # then edit: DEV_AUTH_BYPASS=1, VOICE_MODEL=fake, TTS_PROVIDER=fake
.venv/bin/uvicorn app.main:app --reload                  # web: http://localhost:8000/healthz
.venv/bin/python -m app.jobs.worker                      # worker (second terminal)
```

- `DEV_AUTH_BYPASS=1` lets you call the API with `Authorization: Bearer dev` (a fixed, auto-invited
  local admin user) or `Bearer dev:<uuid>:<email>` (any invited email). It is ignored when `APP_ENV=prod`.
- `VOICE_MODEL=fake` uses a rule-based stand-in for the voice model (no LLM calls). Leave it as `auto`
  once the model layer is wired and `OPENROUTER_API_KEY` is set.
- `TTS_PROVIDER=fake` writes placeholder "audio" so the pipeline runs end to end without a TTS key.
- `AGENT_PORTS=auto` uses the real newsroom / chief-of-staff agents when their code is present **and**
  `OPENROUTER_API_KEY` is set, and a clearly marked placeholder (`arm="stub"`) otherwise.
- Model calls go through W-B's Router (`app.llm.get_client`). Every call is logged to the JSONL ledger
  (`LLM_LEDGER_PATH`, default `eval/runs/llm_ledger.jsonl`) and to the `llm_calls` table.
  `LLM_SPEND_CAP_USD` sets a hard per-process spend fence; it is unset (no fence) by default.
  `TRACE_DIR` (optional) writes newsroom / chief-of-staff run traces as JSON.
- FakeTTS audio can't be decoded by a browser, so the web app skips every segment. Use a real
  `TTS_PROVIDER` to hear the brief.
- Until W-A's parsers merge, live headlines come from the interim adapters in `app/sources/_interim.py`
  (RSS, HN front page, Techmeme feed). Sources of type `arxiv`, `hf_papers` and `frontpage` are skipped.
  A worked live run: `docs/smoke/2026-11-05/`.
- Tests: `.venv/bin/python -m pytest` (DB tests create and drop their own database on the local server;
  set `TEST_DATABASE_ADMIN_URL` to point them elsewhere).

---

## 1. Supabase project (20 min)

1. **Create a project** at supabase.com (free tier is fine). Pick a region close to Railway's
   (e.g. `us-west`). Save the database password. Note the **project ref** (the `abcd1234` in
   `https://abcd1234.supabase.co`).

2. **Apply the migrations** (`supabase/migrations/*.sql`). Either:
   - Supabase CLI (recommended):
     ```bash
     supabase login
     supabase link --project-ref <ref>
     supabase db push
     ```
   - or our runner, with the `postgres` connection string from *Project Settings → Database →
     Connection string → Session pooler*:
     ```bash
     MIGRATIONS_DATABASE_URL='postgresql://postgres.<ref>:<db-password>@aws-0-<region>.pooler.supabase.com:5432/postgres' \
       python -m app.db.migrate
     ```
   Use one method consistently (they track applied migrations separately).

3. **Give the app roles a login.** The migrations create `app_rw` (what the app uses; *not*
   BYPASSRLS, so row-level security always applies) and `app_admin` (read-only admin views) without
   passwords. In *SQL Editor* run, with two new strong passwords:
   ```sql
   alter role app_rw with login password '<APP_RW_PASSWORD>';
   alter role app_admin with login password '<APP_ADMIN_PASSWORD>';
   ```

4. **Connection strings for the app** — use the **session pooler** (port 5432; Supavisor accepts
   custom roles as `<role>.<ref>`). Don't use the transaction pooler (6543): psycopg prepares
   statements, which it doesn't support.
   ```
   DATABASE_URL=postgresql://app_rw.<ref>:<APP_RW_PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres
   DATABASE_ADMIN_URL=postgresql://app_admin.<ref>:<APP_ADMIN_PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```
   The web and worker processes refuse to start if `DATABASE_URL` connects as a superuser or a
   BYPASSRLS role (e.g. if you paste the `postgres` string by mistake).

5. **Auth providers** (*Authentication → Sign In / Providers*):
   - **Email**: enabled, magic link. Keep "Allow new users to sign up" on — the API gates access with
     its own invite list (a signed-in user who isn't invited gets `403 not_invited`).
   - **Google**: create an OAuth client in Google Cloud Console (type *Web application*), authorised
     redirect URI `https://<ref>.supabase.co/auth/v1/callback`; paste client id + secret into Supabase.
   - *Authentication → URL Configuration*: **Site URL** = your Railway web URL (step 2.4), and add it
     (plus `http://localhost:5173` for frontend dev) to **Redirect URLs**.

6. **JWT verification.** The API verifies Supabase access tokens with the project's JWKS at
   `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json` (asymmetric signing keys, the default for
   new projects; derived automatically from `SUPABASE_URL`). If your project still uses the legacy
   shared secret (HS256), also set `SUPABASE_JWT_SECRET` (*Project Settings → JWT Keys*).

7. **Storage bucket.** *Storage → New bucket* → name `brief-audio`, **private** (not public). The
   worker uploads per-sentence MP3s there with the service-role key; the API hands out signed URLs
   (6 h). The daily `cleanup` job deletes audio older than 7 days (TDD §10.4).

8. **Keys to copy** (*Project Settings → API*): the project URL (`SUPABASE_URL`) and the
   **service_role** key (`SUPABASE_SERVICE_ROLE_KEY`; server-side only, never in the frontend). The
   frontend needs the **anon/publishable** key (W-F's `VITE_SUPABASE_ANON_KEY`).

9. **Invite yourself as admin** (SQL Editor):
   ```sql
   insert into invites (email, role) values ('<your email, lowercase>', 'admin');
   ```
   Further invites: `POST /api/admin/invites {"email": "...", "role": "user"}` as an admin.

---

## 2. Railway (20 min)

1. **New project → Deploy from GitHub repo** → this repo, branch you deploy from (e.g. `main`).
   Railway detects the `Dockerfile` (multi-stage: builds `web/` with Node if present, then Python).

2. **Service `web`** (the one created by default):
   - Start command: leave empty (the image runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT`).
   - *Settings → Networking → Generate domain* (gives `https://<name>.up.railway.app`).
   - *Settings → Healthcheck path*: `/healthz`.

3. **Service `worker`**: *New → GitHub repo* (same repo and branch) → *Settings → Custom start
   command*: `python -m app.jobs.worker`. No domain. One replica is enough (any number is safe:
   jobs are claimed with `FOR UPDATE SKIP LOCKED` and the scheduler dedupes).

4. **Variables.** Put these in *Project → Shared variables* and reference them from both services
   (or paste into each):

   | Variable | Value | Notes |
   |---|---|---|
   | `APP_ENV` | `prod` | disables the dev auth bypass |
   | `APP_MODE` | `live` | |
   | `DATABASE_URL` | app_rw session-pooler URL (1.4) | |
   | `DATABASE_ADMIN_URL` | app_admin session-pooler URL (1.4) | web only; admin routes 503 without it |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` | JWKS URL is derived from it |
   | `SUPABASE_JWKS_URL` | optional override | |
   | `SUPABASE_JWT_SECRET` | only for legacy HS256 projects | |
   | `SUPABASE_SERVICE_ROLE_KEY` | service_role key | storage + deleting auth users on account deletion |
   | `PUBLIC_BASE_URL` | `https://<name>.up.railway.app` | web; used to build `ws_url` (`wss://…`) |
   | `AUDIO_STORAGE` | `supabase` | |
   | `AUDIO_BUCKET` | `brief-audio` | |
   | `OPENROUTER_API_KEY` | | model layer |
   | `TINKER_API_KEY` | | not needed in prod unless sim/judges run there |
   | `SEARCH_PROVIDER` | `tavily` \| `exa` \| `brave` | live `search` tool |
   | `SEARCH_API_KEY` | | without it, search returns "search unavailable" (never fake results) |
   | `TTS_PROVIDER` | `openai` | any OpenAI-compatible `/audio/speech` endpoint |
   | `TTS_API_KEY` | | without it briefs are served without audio |
   | `TTS_BASE_URL`, `TTS_MODEL`, `TTS_VOICE` | defaults: OpenAI, `gpt-4o-mini-tts`, `alloy` | |
   | `STT_PROVIDER`, `STT_API_KEY` | `groq` + key (optional) | server STT fallback; browser STT otherwise |
   | `WORKER_CONCURRENCY` | `4` | worker |
   | `CREDIT_OPENROUTER_USD`, `CREDIT_TINKER_USD` | `200`, `100` | spend-guard alerts at 40/60/80 % |
   | `AGENT_PORTS` | `auto` (or `real` to fail loudly if agents are missing) | worker |
   | `DEV_AUTH_BYPASS` | `0` | never `1` in prod (ignored anyway when `APP_ENV=prod`) |

5. **Deploy.** Push to the branch; both services rebuild. Check:
   - `https://<name>.up.railway.app/healthz` → `{"ok": true, "db": true, "env": "prod"}`
   - worker logs show `worker … started (concurrency 4, agents=real)` and, within a minute, the
     scheduler enqueueing `spend_guard` / `cleanup` (and `newsroom_run` at beat times).

6. **First login.** Open the web URL, sign in with the invited email, finish onboarding. The first
   brief is generated on demand (`GET /api/briefs/today` enqueues `cos_daily` at top priority; the
   app shows "Preparing your brief"). After that, `cos_daily` runs at `brief_time − 60 min` in your
   timezone.

---

## 3. Operating it

- **Admin API** (admin role): `GET /api/admin/users`, `/api/admin/jobs?status=failed`,
  `/api/admin/costs?days=30`, `POST /api/admin/jobs/{id}/retry`, `POST /api/admin/invites`.
  Admin routes connect as `app_admin`, which can't read user content; every action is audited in
  `admin_audit`.
- **Spend guard** runs hourly; alerts land in `admin_alerts` (shown in `/api/admin/costs`) and in the
  worker log. Above 60 % of a credit it pauses queued `eval_*` jobs.
- **New migrations**: add `supabase/migrations/<timestamp>_<name>.sql`, then `supabase db push`
  (prod) and `python -m app.db.migrate` (local). Keep them additive.
- **Account deletion** (`DELETE /api/me`): per-user rows are deleted immediately (cascade); a
  `delete_user` job then removes the user's audio, unlinks their ledger rows, and deletes the
  Supabase auth user (needs `SUPABASE_SERVICE_ROLE_KEY`).
- **Rollback**: Railway → service → Deployments → redeploy a previous build. Migrations are not
  rolled back automatically.
