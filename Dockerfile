# One image, two Railway services (TDD §22):
#   web:    uvicorn app.main:app --host 0.0.0.0 --port $PORT      (default CMD)
#   worker: python -m app.jobs.worker
#
# Stage 1 builds the PWA in web/ when it exists; stage 2 is the Python runtime.

FROM node:22-slim AS web
WORKDIR /src
COPY . .
RUN if [ -f web/package.json ]; then \
      cd web && (npm ci || npm install) && npm run build; \
    else \
      mkdir -p web/dist; \
    fi

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000
WORKDIR /srv
COPY pyproject.toml README.md ./
COPY app ./app
COPY eval ./eval
RUN pip install -e .
COPY config ./config
COPY supabase ./supabase
COPY --from=web /src/web/dist ./web/dist
RUN useradd --create-home appuser && mkdir -p /srv/var/audio && chown -R appuser /srv/var
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
