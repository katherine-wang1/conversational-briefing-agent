# W-B: Model layer + bake-off harness

**Branch:** `ws/llm` (from `claude/mvp-proposal-design-td19a9`)
**Owns:** `app/llm/`, `eval/bakeoff/`, `tests/unit/llm/`
**Contract changes:** additive only. List them in your summary.

## Read first
- `docs/parallel-work.md`
- `docs/02-technical-design.md` §4 (model layer), §19.4 (bake-off), §21 (observability), §27 spikes 1–2
- `docs/02a-technical-design-options.md` §1 (budget, model strategy)
- `app/contracts/llm.py`, `app/contracts/common.py`, `config/models.yaml`, `app/config/loader.py`

## Build principle
No agent frameworks and no provider-side tools. Implement the `ModelClient` protocol yourself over HTTP (httpx). An OpenAI-compatible SDK is acceptable for transport only.

## Build
1. **`OpenRouterClient`**
   - Chat and streaming via OpenAI-compatible Chat Completions at `https://openrouter.ai/api/v1`.
   - Tool calling in OpenAI function format, mapped to and from `ToolCall`.
   - Structured output: JSON-schema mode when the model supports it, otherwise the schema in the prompt. Validate with Pydantic, allow **one repair retry**, then raise `StructuredOutputError`.
   - Prompt-caching hints: `Message.cache_breakpoint` maps to the provider's cache control where supported.
   - Record usage and the provider-reported cost.
   - Retry with backoff on 429 and 5xx.
2. **`TinkerClient`**: the same interface over Tinker's serverless sampling. Check the current Tinker SDK and API docs. Chat-style prompting; no tools needed.
3. **`Router`**: picks the provider and model per logical role via `loader.models().for_role()`.
4. **Call cache** (`app/llm/cache.py`):
   - SQLite.
   - Key: sha256(provider, model, canonical messages / tools / schema / params, `cache_namespace`).
   - A hit returns `cached=True` at $0 cost.
5. **Ledger** (`app/llm/ledger.py`):
   - One record per call: role, provider, model, tokens in / out / cached, cost, latency, run_id, user_id, job_id.
   - JSONL sink now, behind an interface so W-E can add a Postgres `llm_calls` sink.
6. **Budgets:** enforce `TokenBudget` around each call. Estimate input tokens before the call and charge actual usage after.
7. **`FakeModelClient`** (`app/llm/fake.py`) for the other workstreams' tests: scripted text, tool calls, structured outputs, and streaming.
8. **Bake-off harness** (`eval/bakeoff/`):
   - CLI: `python -m eval.bakeoff --role cos.score --models a,b,c`.
   - Runs fixed prompts and fixtures per role.
   - Records schema validity, tool-call success, time to first token, cost, and raw outputs (for later judging).
9. **Spike report** in `docs/spikes.md` § "Models". Use real keys if set, with **≤ $5 total spend**:
   - Test 4–6 cheap models across families plus one mid-tier: tool calling, JSON-schema mode, prompt-caching fields in usage, the cost field, time to first token.
   - Test Tinker sampling: latency, real cost, caching discount.
   - Record each model's **published training cutoff** (it constrains the replay window).
   - Recommend one cheap `dev_default` model.
   - If keys are missing, say so; don't fake results.

## Done when
- Tests with mocked HTTP cover chat, streaming, tool calls, structured output + repair, cache, ledger, and budgets.
- The fake client is ready for other workstreams.
- `pytest` and `ruff` are green.
- The spike report is written.
