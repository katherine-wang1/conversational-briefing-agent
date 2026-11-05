# Conversational Briefing Agent

A voice-first briefing agent that gives each user a short spoken morning brief, takes live
interruptions and questions, and learns what they care about. CS329Z final project (Fall 2026).

- Product requirements: [`docs/01-PRD.md`](docs/01-PRD.md)
- Technical design: [`docs/02-technical-design.md`](docs/02-technical-design.md)
- Decisions and options: [`docs/02a-technical-design-options.md`](docs/02a-technical-design-options.md)
- How the work is split across parallel workstreams: [`docs/parallel-work.md`](docs/parallel-work.md)

## Quick start (dev)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/python -m pytest -q
```

```bash
docker compose up -d db
```

Shared contracts live in `app/contracts/` and `config/`. Read `docs/parallel-work.md` before changing them.
