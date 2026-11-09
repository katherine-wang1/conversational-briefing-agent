# Conversational Briefing Agent

A voice-first news agent. Each morning it gives you a short spoken brief built around what you
care about. You can interrupt it at any point to ask a question; it answers out loud with
citations, then picks the brief back up where you left off. Over time it learns your interests
from what you ask about, skip and finish.

CS329Z final project (Fall 2026). Midpoint prototype.

- [Quick start](#quick-start): one command, then about 5 minutes until your first brief
- [What to try](#what-to-try)
- [Architecture tour](#architecture)
- [Manual setup](#manual-setup) (the same steps by hand, or with the hot-reloading web dev server)
- [Troubleshooting](#troubleshooting)

---

## Quick start

### You need

| What | Notes |
|---|---|
| **macOS or Linux** with Python 3.12+ and Node.js 20+ | On a Mac: `brew install python@3.12 node` |
| **Postgres 16**, from any one of: Homebrew, Docker, or a server you already run | The script finds a running one, or starts it with Docker, or installs it with Homebrew. |
| An **OpenRouter API key** (openrouter.ai → Keys) | Runs every model call and the text-to-speech. A full first run costs about **$0.10**; the script sets a $2 cap. |
| *Optional:* a **Tavily API key** (tavily.com, free tier) | Live web search. Without it, briefs use only the daily news digest and questions can't search the web. |
| **Chrome or Edge** | For the microphone and speech recognition. Safari works for listening and typed questions. |

### Run it

From the repository root:

```bash
./scripts/dev.sh
```

The first run installs everything (a few minutes) and asks you to paste your OpenRouter key and,
optionally, your Tavily key. Both are saved to `.env`. Then it starts the server and the worker and
opens **http://localhost:8000**. Reruns start in a few seconds. **Ctrl-C** stops everything.

| Option | What it does |
|---|---|
| `./scripts/dev.sh --reset` | Wipes the local database first (after asking), so you can onboard again |

What it does, in order (each step is skipped when already done): checks Python and Node; creates
`.venv` and installs the Python packages; finds or starts Postgres and creates the `briefing`
database and schema; creates `.env` from `.env.example` with local settings and your keys, and
checks the OpenRouter key; builds the web app; starts the API server and the background worker,
with one combined live log (also saved to `var/logs/`).

### What to try

1. Click **Dev sign-in (bypass)**. This signs you in as a fixed local user; no account is needed.
2. **Onboard:** say what you want to hear about in your own words, for example *"AI agents and agent
   evaluation, voice AI products, humanoid robots, grid-scale battery storage"*. Pick a length and
   depth, then submit. You can do this right away.
3. **Wait for your first brief: about 3–5 minutes.** The app shows what's happening. First comes
   *Gathering today's news*: on a fresh database the worker builds today's shared digest for three
   beats (AI, energy, physical AI) from live feeds. Then *Writing your brief*: it extracts your
   interests, plans and runs searches, picks the stories, writes a script where every sentence
   cites a source passage, checks the claims, and renders the audio.
4. **Play it.** Tap the microphone, or click **Type instead**, at any point to ask a question. The
   brief pauses, you get a spoken answer with source links, and the brief resumes from the start of
   the sentence you interrupted.
5. Look at **History** (briefs and session transcripts), **Memory** (what it thinks you care
   about), **Questions** (things it saved to look into) and **Settings**. Turn on **Settings →
   Show what's happening** for a live log of the voice session: what it heard, model calls with
   time and cost, searches, and memory signals.

Measured on a fresh database (2026-11-09, onboarding straight after startup):

| Step | Time |
|---|---|
| `./scripts/dev.sh` until "Ready" (dependencies already installed; reruns) | 3–18 s |
| Today's news digest, 3 beats (`newsroom_run`, at startup) | 2–2.5 min |
| Your brief (`cos_daily`: plan, search, select, write, claim check) after the news | ~2 min |
| Audio (`tts_render`) | ~10 s |
| **Script start → brief ready to play** | **~4.5 min** |
| Spoken answer to a question | ~3 s to first audio |
| Model + speech cost for the whole run | ≈ $0.08 (logged to `eval/runs/llm_ledger.jsonl` and the `llm_calls` table) |

Example run outputs (real, unedited) are in `docs/smoke/` (briefs, source packs, traces and a
session transcript).

---

## Architecture

```
            ┌──────────── worker (python -m app.jobs.worker) ────────────┐
 live feeds │  Newsroom (shared, per beat)      Chief of staff (per user) │
 RSS, HN,   │  normalise → cluster → score  →   plan → search → score →   │
 arXiv,  ──►│  → daily digest                   select → source packs →   │──► brief + audio
 Techmeme   │                                   write → claim check       │
            │  Memory: onboarding → interests; session signals → updates  │
            └──────────────────────────┬──────────────────────────────────┘
                                       │ Postgres (jobs queue, RLS per user)
            ┌──────────────────────────┴──────────────────────────────────┐
 browser ◄──┤  API + voice session server (uvicorn app.main:app)          │
 (web/)  ──►│  REST: onboarding, today's brief, history, memory, ratings  │
  audio,    │  WebSocket: interrupt → question → cited spoken answer →    │
  mic, text │  resume; quick searches; bookmarks for follow-up research   │
            └─────────────────────────────────────────────────────────────┘
```

- **Newsroom** (`app/agents/newsroom/`). Shared across users, one run per beat. Pulls headlines
  from the source adapters (`app/sources/`), clusters them into stories with local embeddings
  (and an LLM tie-break for close pairs), tracks story identity across days, scores importance
  with an LLM, and writes a daily digest.
- **Chief of staff** (`app/agents/cos/`). Builds one user's brief in eight steps (C1–C8): plan
  searches from the user's interests, run a bounded web-search loop, pool candidates from the digest
  and the searches, score them, pick stories for each slot in code, build source packs of verbatim
  passages, write a spoken script where every sentence cites a passage, and run a claim check.
- **Voice session** (`app/agents/voice/`). A WebSocket state machine (playing → listening →
  answering → resuming). Answers come only from the brief's source packs, plus up to two quick searches,
  and are spoken sentence by sentence through text-to-speech (`app/speech/`).
- **Memory** (`app/agents/memory/`). Interests with weights. Onboarding text is extracted into
  interests; explicit feedback and implicit signals (asked about, skipped, finished) update them by
  the rules in `config/agent.yaml`.
- **Follow-up research** (`app/agents/followup/`). Questions the user saves for later are
  researched in the background with a bounded search-and-fetch loop, answered only from verified
  passages, and shown under **Questions**.
- **Model layer** (`app/llm/`). One router for all model calls (OpenRouter, Tinker), with
  per-role model choice (`config/models.yaml`), token budgets, a call cache, a cost ledger and a
  hard spending cap.
- **Platform** (`app/api/`, `app/jobs/`, `app/db/`). FastAPI, a Postgres job queue
  (`FOR UPDATE SKIP LOCKED`) with a scheduler, and row-level security so users can never read each
  other's rows.
- **Web app** (`web/`). React + TypeScript PWA built for the iPhone: one audio element, tap/button/
  headphone interrupt, voice-activity detection, browser or server speech-to-text.
- **Evaluation** (`eval/`). Historical news archive collectors, an 18-profile simulated-user panel,
  code graders and LLM judges, a labelling app, and the experiment runner.

Design documents: product requirements in [`docs/01-PRD.md`](docs/01-PRD.md), technical design in
[`docs/02-technical-design.md`](docs/02-technical-design.md), decisions in
[`docs/02a-technical-design-options.md`](docs/02a-technical-design-options.md), current status in
[`docs/status.md`](docs/status.md) and [`docs/TODO.md`](docs/TODO.md).

---

## Manual setup

The same steps as `scripts/dev.sh`, by hand. Use these if the script doesn't fit your machine, or
to run the web app with Vite's hot-reloading dev server on port 5173.

### 1. Install

From the repository root:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,newsroom]"
```

The `newsroom` extra installs the local embedding model the worker uses to cluster news stories.
On first use the worker downloads the model (about 65 MB).

```bash
cd web && npm install && cd ..
```

### 2. Start the database and create the schema

Start Postgres 16 with **one** of the two options below. Both give you a database named `briefing`
on `localhost:5432` with a superuser `postgres` (password `postgres`), which is what `.env.example` and
the tests expect.

**Option A: Docker**

```bash
docker compose up -d db
```

**Option B: Homebrew (macOS, no Docker)**

```bash
brew install postgresql@16
```

```bash
brew services start postgresql@16
```

```bash
export PATH="$(brew --prefix postgresql@16)/bin:$PATH"
```

```bash
createdb briefing
```

```bash
psql -d briefing -c "create role postgres with superuser login password 'postgres';"
```

Homebrew's Postgres makes your macOS user the superuser. The last command adds the `postgres` role the
app's settings use. The `export PATH` line only applies to the current terminal; you need it only for
`createdb` and `psql`. On Linux, install `postgresql-16` from your package manager and create the
same database and role.

**Then, either way, create the schema:**

```bash
MIGRATIONS_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/briefing .venv/bin/python -m app.db.migrate --dev-role-passwords
```

It should end with `4 migration(s) applied`. It creates the tables, row-level security and two app
roles (`app_rw`, `app_admin`) with local-only passwords.

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` and set these four lines (everything else can stay as it is for a local run). Write
`KEY=value` with no spaces around `=`.

```ini
OPENROUTER_API_KEY=<your OpenRouter key>
SEARCH_API_KEY=<your Tavily key>
DEV_AUTH_BYPASS=1
LLM_SPEND_CAP_USD=2
```

- `DEV_AUTH_BYPASS=1` lets you sign in locally without Supabase. It is ignored when `APP_ENV=prod`.
- `LLM_SPEND_CAP_USD=2` is a hard spending limit per process.
- The defaults already set `TTS_PROVIDER=openrouter_audio` (speech through your OpenRouter key),
  `SEARCH_PROVIDER=tavily`, `AUDIO_STORAGE=local` (audio files under `var/audio/`) and
  `AGENT_PORTS=auto` (the real agents).
- The server and the worker load `.env` from the directory you start them in. Variables exported in
  your shell take precedence over `.env`, including empty ones.

### 4. Start the API server and the worker

Use two terminals, both in the repository root.

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
.venv/bin/python -m app.jobs.worker
```

Check the server: `curl localhost:8000/healthz` should print `{"ok":true,"db":true,"env":"dev"}`.
The worker should log `worker ... started (concurrency 4, agents=real)`. If it says `agents=stub`,
see [Troubleshooting](#troubleshooting).

### 5. Start the web app

Either build it once and use the server's URL (what the script does):

```bash
cd web && VITE_DEV_BYPASS=true npm run build && cd ..
```

Then open **http://localhost:8000**. Or, in a third terminal, run the dev server with hot reload:

```bash
cd web && BACKEND_URL=http://localhost:8000 npm run dev
```

Then open **http://localhost:5173**. The dev server forwards `/api`, `/ws` and `/media` to the
backend, so the app and the API share an origin. Then follow [What to try](#what-to-try). You can
onboard right away: the first brief waits for today's news by itself.

---

## Tests

```bash
.venv/bin/python -m pytest -q
```

The database tests create and drop their own database on the local Postgres
(`postgres:postgres@localhost:5432`). Set `TEST_DATABASE_ADMIN_URL` to use a different server. Tests
that call real model providers are skipped unless `LLM_LIVE_TESTS=1`.

```bash
cd web && npm run typecheck && npm run lint && npm test
```

The web app also runs without the backend, against a mock server: `cd web && npm run dev:mock`.

---

## Troubleshooting

- **The worker logs `agents=stub` or a "NO MODEL KEY" banner, or briefs read headlines back without
  real writing.** The worker can't see `OPENROUTER_API_KEY`. Check that the file is named exactly `.env`
  in the repository root, that the line reads `OPENROUTER_API_KEY=sk-or-...` with no spaces around
  `=`, and that your shell doesn't export an empty one (`echo "[$OPENROUTER_API_KEY]"`; if it prints
  `[]`, run `unset OPENROUTER_API_KEY`). `./scripts/dev.sh` checks all of this before it starts.
  Today's placeholder brief stays until you start over: `./scripts/dev.sh --reset`, or see [Reset](#reset).
- **"OpenRouter rejected this key".** The key in `.env` is wrong or revoked. Paste a new one.
- **`command not found: docker`** (manual setup). Use option B (Homebrew) in step 2, or the script,
  which uses Homebrew when Docker isn't there.
- **`command not found: createdb` or `psql`.** Homebrew's `postgresql@16` isn't on your PATH.
  Run the `export PATH=...` line from step 2 in that terminal.
- **`role "postgres" already exists`.** That's fine; the role is already there. Carry on with the
  migrate command.
- **Port 5432 is already in use**, or the script says Postgres rejects `postgres/postgres`. Another
  Postgres is running. Stop it, or give it a superuser `postgres` with password `postgres`; the
  script prints the command.
- **"something is already running on port 8000".** An earlier run is still going. Stop it with
  Ctrl-C in its terminal, or find it with `lsof -i :8000`.
- **"fastembed is not installed"** in the worker log: you installed without the extra. Run
  `.venv/bin/pip install -e ".[dev,newsroom]"`.
- **The brief stays on "Gathering today's news" for more than about 8 minutes.** A news source is
  slow or failing. The brief goes ahead after 8 minutes with whatever news is ready. See
  `var/logs/worker.log`.
- **No sound.** Check that `TTS_PROVIDER=openrouter_audio` is set. `fake` makes placeholder audio
  that browsers can't play. Click once in the page first, since browsers block autoplay until you interact.
- **The microphone doesn't hear you.** Use Chrome or Edge on `localhost`, which counts as a secure
  origin, so the mic is allowed. Or use **Type instead**.
- **A `409` in the browser console** right after sign-in is expected: it is the app asking for
  today's brief before you've onboarded.

### Reset

To start over with an empty database (you'll onboard again):

```bash
./scripts/dev.sh --reset
```

By hand: stop the server and the worker, then either `docker compose down -v && docker compose up -d db`
(Docker) or `dropdb briefing && createdb briefing` (Homebrew, with the `export PATH=...` line from
step 2), then rerun the migrate command and restart. To also clear rendered audio: `rm -rf var/audio`.

---

## Repository map

| Path | What |
|---|---|
| `app/agents/` | newsroom, chief of staff (`cos`), voice, memory, follow-up research, prompts |
| `app/api/` | REST routes, auth (Supabase JWT, dev bypass, invites), admin |
| `app/jobs/` | job queue, scheduler, worker, job handlers |
| `app/llm/` | model router, providers, budgets, cache, ledger |
| `app/sources/`, `app/tools/` | headline source adapters; search / fetch / archive tools for the agents |
| `app/speech/` | text-to-speech and speech-to-text providers, sentence splitting, audio storage |
| `app/contracts/` | shared data models (pydantic) between components |
| `config/` | agent settings (`agent.yaml`), model choices per role (`models.yaml`), sources, experiments |
| `supabase/migrations/` | database schema and row-level security |
| `web/` | the web app (PWA) and its mock backend |
| `eval/` | archive collection, simulated users, graders, judges, labelling, runner |
| `scripts/` | `dev.sh` (one-command local setup and run) and its `.env` / database helper |
| `tests/` | Python tests |
| `docs/` | design docs, deploy guide, status, smoke-test results |

## Deploying

The hosted setup (Supabase for Postgres/Auth/Storage, Railway for the web and worker services) is
described in [`docs/deploy.md`](docs/deploy.md). It is not needed to run the prototype.
