# One image, two Railway services (TDD §22):
#   web:    uvicorn app.main:app --host 0.0.0.0 --port $PORT      (default CMD)
#   worker: python -m app.jobs.worker
#
# Stage 1 builds the PWA in web/ when it exists; stage 2 is the Python runtime.

FROM node:22-slim AS web
# Vite bakes these into the PWA at build time. Railway passes service variables with the same names as build args.
ARG VITE_SUPABASE_URL=""
ARG VITE_SUPABASE_ANON_KEY=""
ARG VITE_API_BASE=""
WORKDIR /src
COPY . .
RUN if [ -f web/package.json ]; then \
      cd web && (npm ci || npm install) && npm run build; \
    else \
      mkdir -p web/dist; \
    fi

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000 \
    FASTEMBED_CACHE_PATH=/srv/var/fastembed \
    LLM_LEDGER_PATH=/srv/var/runs/llm_ledger.jsonl \
    FASTEMBED_THREADS=2
WORKDIR /srv
COPY pyproject.toml README.md ./
COPY app ./app
COPY eval ./eval
# The newsroom extra (fastembed, rapidfuzz, numpy) is needed by the worker's newsroom_run.
RUN pip install -e ".[newsroom]"
# Bake the clustering embedding model (config/agent.yaml newsroom.embedder) into the image so a
# fresh worker doesn't download it on its first run.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
COPY config ./config
COPY supabase ./supabase
COPY --from=web /src/web/dist ./web/dist
RUN useradd --create-home appuser && mkdir -p /srv/var/audio && chown -R appuser /srv/var
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
