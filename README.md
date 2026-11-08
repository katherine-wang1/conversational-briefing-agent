# Conversational Briefing Agent

A voice-first news agent. Each morning it gives you a short spoken brief built around what you
care about. You can interrupt it at any point to ask a question; it answers out loud with
citations, then picks the brief back up where you left off. Over time it learns your interests
from what you ask about, skip and finish.

CS329Z final project (Fall 2026). Midpoint prototype.

- [How to run it locally](#run-it-locally) (about 15 minutes, plus about 5 minutes of waiting for the first brief)
- [What happens when you use it](#what-you-will-see)
- [Architecture tour](#architecture)
- [Repository map](#repository-map)

---

## Run it locally

You start four things: a Postgres database, the API server, a background worker that builds the
briefs, and the web app. Everything runs on your machine; the only external services are the
model provider and web search.

### Prerequisites

| What | Version | Notes |
|---|---|---|
| Python | 3.12 or newer | `python3 --version` |
| Node.js | 20 or newer (tested on 22) | `node --version` |
| Postgres 16 | **either** Docker (Docker Desktop or any Docker with `docker compose`) **or** a local Postgres, e.g. from Homebrew | step 2 shows both. On a Mac without Docker, the Homebrew route is quicker. |
| Chrome or Edge | any recent | for the microphone and Web Speech recognition. Safari works for playback and typed questions. |
| An **OpenRouter** API key | | runs all the model calls *and* text-to-speech. One full run (news digest + onboarding + brief + a few questions) costs about **$0.10**. |
| A **Tavily** API key | free tier is enough | live web search. Without it the brief still works but uses only the daily news digest, and questions can't search the web. |

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

It should end with `4 migration(s) applied`. It creates the tables, row-level
security and two app roles (`app_rw`, `app_admin`) with local-only passwords.

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` and set these four lines (everything else can stay as it is for a local run):

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

### 4. Start the API server and the worker

Use two terminals, both in the repository root. Both read `.env` from the current directory.

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

```bash
.venv/bin/python -m app.jobs.worker
```

Check the server: `curl localhost:8000/healthz` should print `{"ok":true,"db":true,"env":"dev"}`.

The worker should log `worker ... started (concurrency 4, agents=real)`. If it says `agents=stub`,
`OPENROUTER_API_KEY` isn't being read; check `.env`.

**Wait for the news digest before you onboard.** On a fresh database the worker immediately
builds today's shared news digest for three beats (AI, energy, physical AI) from live feeds. Wait
until the worker log shows three lines like:

```
job 1 newsroom_run done in 161130 ms
```

That takes about 3 minutes. If you onboard before it finishes, your first brief still works but has
only stories found by search.

### 5. Start the web app

In a third terminal:

```bash
cd web && BACKEND_URL=http://localhost:8000 npm run dev
```

Open **http://localhost:5173** in Chrome. The dev server forwards `/api`, `/ws` and `/media` to the
backend, so the app and the API share an origin.

### 6. Use it

1. **Sign in:** click **Dev sign-in (bypass)**. This signs you in as a fixed local user
   (`dev@localhost`); the email box doesn't matter.
2. **Onboard:** describe what you want to hear about in your own words, for example *"AI agents and
   agent evaluation, voice AI products, humanoid robots, grid-scale battery storage"*. Pick a length and
   depth, then submit.
3. **Wait for your brief:** the app shows "Preparing your brief" while the worker extracts your
   interests, plans and runs searches, picks and writes the stories, checks every claim against
   its sources, and renders the audio. **Expect about 2 minutes.**
4. **Play it.** Tap the microphone, or click **Type instead**, at any time to ask a question. The
   brief pauses, you get a spoken answer with source links, and the brief resumes from the start of
   the sentence you interrupted.
5. Look at **History** (briefs and session transcripts), **Memory** (what it thinks you care
   about), **Questions** (things it saved to look into) and **Settings**. Turn on **Settings →
   Show what's happening** for a live log of the voice session: what it heard, model calls with
   time and cost, searches, and memory signals.

To start over from scratch, see [Reset](#reset).

---

## What you will see

Measured on a fresh database (2026-11-08):

| Step | Time | Notes |
|---|---|---|
| News digest (3 beats, `newsroom_run`) | 2–3 min | runs once at startup, then on a schedule (`config/agent.yaml`) |
| Onboarding → interests stored (`onboarding_seed`) | ~2 s | |
| Personal brief (`cos_daily`) | ~2 min | 6 stories: 2 big news, 3 for you, 1 outside your usual interests |
| Audio (`tts_render`) | ~12 s | about 2.5 min of speech at 1.2× |
| Spoken answer to a question | ~3 s to first audio | |
| **Model + speech cost for the whole run** | **≈ $0.08** | every call is logged to `eval/runs/llm_ledger.jsonl` and the `llm_calls` table |

Example run outputs (real, unedited) are in `docs/smoke/` (briefs, source packs, traces and a session transcript).

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

## Tests

```bash
.venv/bin/python -m pytest -q
```

The database tests create and drop their own database on the Postgres from step 2
(`postgres:postgres@localhost:5432`). Set `TEST_DATABASE_ADMIN_URL` to use a different server. Tests that call real model
providers are skipped unless `LLM_LIVE_TESTS=1`.

```bash
cd web && npm run typecheck && npm run lint && npm test
```

The web app also runs without the backend, against a mock server: `cd web && npm run dev:mock`.

---

## Troubleshooting

- **`command not found: docker`.** Docker isn't installed. Use option B (Homebrew) in step 2.
- **`command not found: createdb` or `psql`.** Homebrew's `postgresql@16` isn't on your PATH.
  Run the `export PATH=...` line from step 2 in that terminal.
- **`role "postgres" already exists`.** That's fine; the role is already there. Carry on with the
  migrate command.
- **Port 5432 is already in use.** Another Postgres is running. Stop it, or use it instead: create a
  database named `briefing` and a superuser `postgres` with password `postgres` (or change the URLs in
  the migrate command and `.env`), then run the migrate command.
- **`agents=stub` in the worker log**, or briefs that say they're placeholders: the worker can't see
  `OPENROUTER_API_KEY`. Start it from the repository root, where `.env` is.
- **"fastembed is not installed"** in the worker log: you installed without the extra. Run
  `.venv/bin/pip install -e ".[dev,newsroom]"`.
- **No sound.** Check that `TTS_PROVIDER=openrouter_audio` is set. `fake` makes placeholder audio
  that browsers can't play. Click once in the page first, since browsers block autoplay until you interact.
- **The microphone doesn't hear you.** Use Chrome or Edge on `http://localhost:5173`. Localhost
  counts as a secure origin, so the mic is allowed. Or use **Type instead**.
- **The first brief has no "big news" stories.** You onboarded before the news digest finished. Wait
  for the three `newsroom_run done` lines, then reset and onboard again.
- **A `409` in the browser console** right after sign-in is expected: it is the app asking for
  today's brief before you've onboarded.

### Reset

To start over with an empty database (you'll onboard again), stop the API server and the worker,
then:

Docker:

```bash
docker compose down -v && docker compose up -d db
```

Homebrew (with the `export PATH=...` line from step 2 in this terminal):

```bash
dropdb briefing && createdb briefing
```

Then rerun the migrate command from step 2 and restart the API server and the worker. The newsroom runs
again on startup. To also clear rendered audio: `rm -rf var/audio`.

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
| `tests/` | Python tests |
| `docs/` | design docs, deploy guide, status, smoke-test results |

## Deploying

The hosted setup (Supabase for Postgres/Auth/Storage, Railway for the web and worker services) is
described in [`docs/deploy.md`](docs/deploy.md). It is not needed to run the prototype.
