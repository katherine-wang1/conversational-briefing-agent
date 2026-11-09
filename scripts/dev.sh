#!/usr/bin/env bash
# One command to set up and run the prototype locally (see README "Quick start").
#
#   ./scripts/dev.sh           set up whatever is missing, then run the API server + worker
#   ./scripts/dev.sh --reset   also wipe the database first (you onboard again)
#
# Safe to rerun: every step skips work that is already done. Ctrl-C stops everything.
# Written for bash 3.2 (macOS's /bin/bash) as well as newer bash.

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

PORT=8000
URL="http://localhost:${PORT}"
PG_ADMIN="postgresql://postgres:postgres@localhost:5432/postgres"
PG_DB="briefing"
RESET=0

for arg in "$@"; do
  case "$arg" in
    --reset) RESET=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then B=$'\033[1m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; N=$'\033[0m'; else B=; R=; G=; Y=; N=; fi
step() { printf '%s==> %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '\n%sError:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

T0=$(date +%s)
mkdir -p var

# ---- 1. Python and Node ---------------------------------------------------------------------------
step "Checking Python and Node"
PY=""
for c in python3.13 python3.12 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "Python 3.12 or newer is needed. On a Mac: brew install python@3.12 (then rerun this script)."
ok "$("$PY" --version)"
have node || die "Node.js 20 or newer is needed. On a Mac: brew install node (then rerun this script)."
node -e 'process.exit(+process.versions.node.split(".")[0] < 20 ? 1 : 0)' \
  || die "Node.js 20 or newer is needed (you have $(node --version)). On a Mac: brew upgrade node."
ok "Node $(node --version)"

# ---- 2. Python packages -------------------------------------------------------------------------
step "Python packages"
STAMP=".venv/.dev-installed"
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
fi
if [ ! -f "$STAMP" ] || [ pyproject.toml -nt "$STAMP" ]; then
  echo "  installing (a few minutes the first time)…"
  .venv/bin/pip install -q --upgrade pip >/dev/null 2>&1 || true
  .venv/bin/pip install -q -e ".[dev,newsroom]" >var/pip-install.log 2>&1 \
    || { tail -20 var/pip-install.log >&2; die "pip install failed (full log: var/pip-install.log)."; }
  touch "$STAMP"
  ok "installed"
else
  ok "already installed"
fi
VPY="$ROOT/.venv/bin/python"
envpy() { "$VPY" scripts/dev_env.py "$@"; }

# ---- 3. Postgres --------------------------------------------------------------------------------
step "Database"
if envpy db-ready "$PG_ADMIN"; then
  ok "Postgres is running on localhost:5432"
elif have docker && docker info >/dev/null 2>&1; then
  echo "  starting Postgres in Docker…"
  docker compose up -d db >/dev/null
  for _ in $(seq 1 60); do envpy db-ready "$PG_ADMIN" && break; sleep 1; done
  envpy db-ready "$PG_ADMIN" || die "Postgres in Docker didn't come up. Check: docker compose logs db"
  ok "Postgres (Docker) is running"
elif have brew; then
  if ! brew list postgresql@16 >/dev/null 2>&1; then
    echo "  installing Postgres 16 with Homebrew…"
    brew install postgresql@16 >/dev/null
  fi
  PGBIN="$(brew --prefix postgresql@16)/bin"
  brew services start postgresql@16 >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do "$PGBIN/pg_isready" -q -h localhost -p 5432 && break; sleep 1; done
  "$PGBIN/pg_isready" -q -h localhost -p 5432 \
    || die "Postgres didn't start. Try: brew services restart postgresql@16 (another Postgres on port 5432?)"
  if ! "$PGBIN/psql" -h localhost -d postgres -tAc "select 1 from pg_roles where rolname = 'postgres'" | grep -q 1; then
    "$PGBIN/psql" -h localhost -d postgres -qc "create role postgres with superuser login password 'postgres'"
  fi
  envpy db-ready "$PG_ADMIN" \
    || die "Postgres is running but rejects postgres/postgres. Set the password: $PGBIN/psql -d postgres -c \"alter role postgres with login superuser password 'postgres'\""
  ok "Postgres (Homebrew) is running"
else
  die "No Postgres found. Install Docker Desktop (docker.com), or Homebrew (brew.sh) and rerun this script,
       or start any Postgres 16 on localhost:5432 with a superuser postgres / password postgres."
fi

if [ "$RESET" = 1 ]; then
  printf '  %sReset:%s delete every user, brief and digest in the local database? [y/N] ' "$Y" "$N"
  read -r answer || answer=""
  [ -t 0 ] || echo
  case "$answer" in
    y|Y|yes|YES) envpy drop-db "$PG_ADMIN" "$PG_DB"; rm -rf var/audio; ok "database wiped" ;;
    *) echo "  keeping the database" ;;
  esac
fi
envpy ensure-db "$PG_ADMIN" "$PG_DB"
MIGRATIONS_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/${PG_DB}" \
  "$VPY" -m app.db.migrate --dev-role-passwords | tail -1 | sed 's/^/  /'

# ---- 4. Configuration (.env) --------------------------------------------------------------------
step "Configuration"
for f in .env.txt env .env.local; do
  [ -f "$f" ] && [ ! -f .env ] && warn "found '$f' but the app reads a file named exactly '.env'"
done
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  envpy set DEV_AUTH_BYPASS 1
  envpy set LLM_SPEND_CAP_USD 2
  ok "created .env from .env.example"
fi
envpy lint || die "fix the lines above in .env, then rerun."

# An empty variable exported in the shell would hide the value in .env: don't pass those on.
for k in $(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env | tr -d '='); do
  if eval "[ \"\${$k+set}\" = set ] && [ -z \"\${$k}\" ]"; then
    warn "your shell exports an empty $k; ignoring it so the value in .env is used"
    unset "$k"
  fi
done

ask_key() { # ask_key VAR "label" required|optional
  local var="$1" label="$2" need="$3" cur val
  cur="$(envpy get "$var")"
  [ -n "$cur" ] && return 0
  if eval "[ -n \"\${$var:-}\" ]"; then  # exported in the shell: save it to .env for next time
    eval "envpy set \"$var\" \"\${$var}\""; ok "$label: saved from your shell to .env"; return 0
  fi
  if [ "$need" = optional ] && [ -f var/.dev-no-search-key ]; then return 0; fi
  if [ "$need" = required ]; then
    printf '  Paste your %s (input hidden), then Enter: ' "$label"
  else
    printf '  Paste your %s for web search (input hidden), or press Enter to skip: ' "$label"
  fi
  val=""
  if [ -t 0 ]; then read -r -s val || val=""; echo; else read -r val || val=""; echo; fi
  val="$(printf '%s' "$val" | tr -d '[:space:]')"
  if [ -n "$val" ]; then
    envpy set "$var" "$val"; ok "$label saved to .env"
  elif [ "$need" = required ]; then
    die "an OpenRouter API key is required (openrouter.ai → Keys). Rerun when you have one."
  else
    mkdir -p var && touch var/.dev-no-search-key
    warn "no search key: briefs use only the daily news digest (add SEARCH_API_KEY to .env later)"
  fi
}
ask_key OPENROUTER_API_KEY "OpenRouter API key" required
ask_key SEARCH_API_KEY "Tavily API key" optional
envpy check || die "fix .env (see above), then rerun."

# ---- 5. Web app -----------------------------------------------------------------------------------
step "Web app"
if [ ! -d web/node_modules ] || [ web/package-lock.json -nt web/node_modules/.package-lock.json ]; then
  echo "  installing (a minute or two the first time)…"
  (cd web && npm install --no-audit --no-fund --loglevel=error >/dev/null)
fi
BUILD_STAMP="web/dist/.dev-build"
if [ ! -f "$BUILD_STAMP" ] \
  || [ -n "$(find web/src web/public web/index.html web/vite.config.ts web/package-lock.json -newer "$BUILD_STAMP" 2>/dev/null | head -1)" ]; then
  echo "  building…"
  (cd web && VITE_DEV_BYPASS=true npm run build --silent >/dev/null 2>"$ROOT/var/web-build.err") \
    || { cat var/web-build.err >&2; die "the web app didn't build (output above)."; }
  touch "$BUILD_STAMP"
  ok "built"
else
  ok "up to date"
fi

# ---- 6. Run ---------------------------------------------------------------------------------------
step "Starting the server and the worker"
if "$VPY" -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1', $PORT)) != 0)"; then
  die "something is already running on port $PORT (an earlier ./scripts/dev.sh or uvicorn?). Stop it first."
fi
# Only this script's worker runs locally, so jobs still marked 'running' were cut off by the last Ctrl-C.
envpy requeue "postgresql://postgres:postgres@localhost:5432/${PG_DB}"
mkdir -p var/logs
: > var/logs/web.log
: > var/logs/worker.log
"$ROOT/.venv/bin/uvicorn" app.main:app --port "$PORT" >> var/logs/web.log 2>&1 &
WEB_PID=$!
"$VPY" -m app.jobs.worker >> var/logs/worker.log 2>&1 &
WORKER_PID=$!
TAIL_PID=""

cleanup() {
  trap - EXIT INT TERM
  echo
  step "Stopping"
  kill "$WEB_PID" "$WORKER_PID" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$WEB_PID" 2>/dev/null || kill -0 "$WORKER_PID" 2>/dev/null || break
    sleep 1
  done
  kill -9 "$WEB_PID" "$WORKER_PID" 2>/dev/null || true
  [ -n "$TAIL_PID" ] && kill "$TAIL_PID" 2>/dev/null || true
  echo "  stopped. Logs: var/logs/web.log, var/logs/worker.log"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

for _ in $(seq 1 60); do
  curl -fs "$URL/healthz" >/dev/null 2>&1 && break
  kill -0 "$WEB_PID" 2>/dev/null || { tail -20 var/logs/web.log >&2; die "the server stopped (log above)."; }
  sleep 1
done
curl -fs "$URL/healthz" >/dev/null 2>&1 || { tail -20 var/logs/web.log >&2; die "the server didn't answer on $URL/healthz."; }
for _ in $(seq 1 30); do grep -q "started (concurrency" var/logs/worker.log && break; sleep 1; done
if grep -q "agents=stub" var/logs/worker.log; then
  die "the worker started with placeholder agents (no model key). Check OPENROUTER_API_KEY in .env."
fi
grep -q "agents=real" var/logs/worker.log || { tail -20 var/logs/worker.log >&2; die "the worker didn't start (log above)."; }

cat <<EOF

${G}${B}Ready in $(( $(date +%s) - T0 )) s.${N}  Open ${B}${URL}${N}

  1. Click ${B}Dev sign-in (bypass)${N}.
  2. Say what you want to hear about, then submit.
  3. Your first brief takes about 3–5 minutes: the worker is gathering today's news and then
     writing your brief. The app shows which, and starts when it's ready.
  4. Play it. Tap the microphone (or "Type instead") any time to ask a question.

  Ctrl-C stops everything. Logs: var/logs/. Start over: ./scripts/dev.sh --reset

EOF
if [ "$(uname)" = Darwin ] && have open; then open "$URL" || true; fi

# Live log: server and worker lines, prefixed (outgoing-request noise filtered out).
tail -n +1 -F var/logs/web.log var/logs/worker.log 2>/dev/null | awk '
  /^==> .*web\.log <==$/    { p = "[server]"; next }
  /^==> .*worker\.log <==$/ { p = "[worker]"; next }
  /^$/ || /HTTP Request:/ || /GET \/api\/briefs\/today/ || /GET \/(assets|vad|media)\// { next }
  { print p " " $0; fflush() }' &
TAIL_PID=$!

while kill -0 "$WEB_PID" 2>/dev/null && kill -0 "$WORKER_PID" 2>/dev/null; do sleep 2; done
kill -0 "$WEB_PID" 2>/dev/null || die "the server stopped. See var/logs/web.log."
die "the worker stopped. See var/logs/worker.log."
