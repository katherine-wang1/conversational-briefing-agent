# Parallel workstreams

The build is split into workstreams that can run at the same time. They meet only at the **shared contracts**:
- `app/contracts/*.py`: data shapes, the tool interface, the model interface, the session protocol;
- `config/*.yaml`: taxonomy, beats, agent parameters, model roles, experiments;
- `tests/contract/`: checks that the contracts and configs agree.

## Rules for every workstream

1. **Start from branch `claude/mvp-proposal-design-td19a9`.** Work on your own branch, named in the table below. Push it. Don't open a pull request unless Kate asks.
2. **Only edit the directories you own.** If you need a change to `app/contracts/` or `config/`, keep it **additive** (new optional fields, new files) and list it in your final summary. Never rename or remove a contract field: other streams depend on it.
3. Keep `pytest` and `ruff` green, including `tests/contract/`.
4. **No secrets in the repo.** Code reads keys from the environment variables in `.env.example`.
5. **Build principle** (PRD §6.0): we write the agent loop, tools, memory, selection, and evaluation. Models are called only through `app/contracts/llm.ModelClient`. No hosted agent platforms or provider-side tools.
6. **Don't fake missing results.** If a network host or API key is unavailable, say so. Build against fixtures and leave a clear TODO; never invent data and present it as collected.

Full instructions for each workstream are in `docs/workstreams/W-*.md`. The orchestrator brief is `docs/workstreams/ORCHESTRATOR.md`.

## Workstreams

| # | Workstream | Branch | Owns (directories) | Depends on | Needs network / keys |
|---|---|---|---|---|---|
| W-A | **Archive collectors + source parsers** | `ws/archive` | `eval/archive/`, `app/sources/` | Contracts | **Yes**: web.archive.org, techmeme.com, hn.algolia.com, news.ycombinator.com, export.arxiv.org, huggingface.co, beat source domains |
| W-B | **Model layer + bake-off harness** | `ws/llm` | `app/llm/`, `eval/bakeoff/` | Contracts | `OPENROUTER_API_KEY`, `TINKER_API_KEY` for live tests (mocked otherwise) |
| W-C | **Newsroom pipeline (N2–N6)** | `ws/newsroom` | `app/agents/newsroom/`, newsroom prompts | Contracts; uses `ModelClient` via a fake in tests | None for unit tests |
| W-D | **Chief of staff + selection + memory rules** | `ws/cos` | `app/agents/cos/`, `app/agents/memory/`, `app/agents/followup/`, related prompts | Contracts | None for unit tests |
| W-E | **Platform: DB, RLS, API, jobs, voice session server** | `ws/platform` | `supabase/migrations/`, `app/db/`, `app/api/`, `app/jobs/`, `app/agents/voice/`, `app/speech/`, `app/tools/` | Contracts | Docker (local Postgres) |
| W-F | **PWA frontend + audio engine** | `ws/web` | `web/` | Session protocol contract | None (a mock WebSocket server for dev) |
| W-G | **Simulated users + graders + labelling app + test content** | `ws/eval` | `eval/sim/`, `eval/graders/`, `eval/tasks/`, `eval/labeling/`, `eval/runner/` | Contracts, taxonomy | None (Tinker key only for live simulator runs) |

## Merge order

Merge into `claude/mvp-proposal-design-td19a9` in this order, rebasing each branch on the previous merge:

1. W-E platform
2. W-B llm
3. W-A archive
4. W-C newsroom
5. W-D chief of staff
6. W-G eval
7. W-F web

Contract additions from different streams are reconciled at merge time.

## Notes

- The taxonomy uses `robotics-hardware` and `robotics-business` as subtopic ids for the Physical AI beat. The PRD's `hardware` and `business-and-deployments` were renamed for uniqueness.
- Feed URLs in `config/beats/*.yaml` are drafts (`verified: false`) until W-A confirms them.
