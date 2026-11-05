# Week-1 spikes

<!-- Each workstream owns one section. W-B owns "Models" (below); W-A owns "Archive". -->

## Models

*W-B, 2026-11-05. TDD §27 spikes 1 (OpenRouter), 2 (Tinker), 5 (model cutoffs).*

**Method.** `python -m eval.bakeoff.spike --live --cap 1.0` ran each model through the production `Router` (ledger, budget, repair). Per model it made:
- one tool-calling request (OpenAI function format, one `search` tool);
- one JSON-schema request (a 3-field `Verdict`; `strict: true`);
- two identical requests over a ~8–11K-token system prefix marked `cache_breakpoint=True`, 2 s apart;
- two streaming requests to measure time to first token (TTFT);
- one "what is your training cutoff?" question.

Raw usage blocks and outputs are in `eval/bakeoff/spike_2026-11-05/results.json`, and every call is in `ledger.jsonl` next to it. TTFT was measured from this cloud build container, not from Railway's region, and each number comes from only 2 samples. Treat the latencies as rough indications.

### OpenRouter results

Prices are USD per 1M tokens (input / output / cached read), from `GET /api/v1/models` on 2026-11-05.

| Model (snapshot) | Price in / out / cache-read | Tools | JSON schema | Cache fields observed | 1st → 2nd call cost on the long prefix | TTFT p50 | Reasoning by default |
|---|---|---|---|---|---|---|---|
| `anthropic/claude-haiku-5.5` (20261007) | 0.10 / 0.50 / 0.01 | ✅ | ✅ first try | `cache_write_tokens` 10,900 → `cached_tokens` 10,900 | $0.00137 → $0.00011 (−92%) | 679 ms | adaptive; 0 reasoning tokens on tool/JSON/cache probes, but 153 output tokens for a one-line cutoff answer |
| `openai/gpt-6-luna` (20260922) | 0.10 / 0.50 / 0.01 | ✅ | ✅ first try | write 7,794 → cached 7,794 | $0.00098 → $0.00008 (−92%) | 1,045 ms | yes (a few reasoning tokens) |
| `google/gemini-3.5-flash-lite` (20260721) | 0.30 / 2.50 / 0.03 | ✅ | ✅ first try | cached 9,950 on **both** calls (plus write 9,950 on the 1st) | $0.00113 → $0.00031 (−73%) | 543 ms | mandatory (min `minimal`) |
| `deepseek/deepseek-v4.1-flash` (20260910) | 0.045 / 0.50 / 0.018 | ✅ | ✅ first try (330 output tokens, 4.1 s) | **no cache hit** (0 / 0) | $0.00036 → $0.00082 (two different upstreams) | 1,174 ms (1 of 2 runs: no text in 60 tokens) | yes, `high`; ate the whole budget on the cutoff question |
| `qwen/qwen3.8-flash` (20260826) | 0.15 / 0.47 / 0.016 | ✅ (2.7 s) | ✅ first try | 0 → cached 9,984 | $0.00151 → $0.00017 (−89%) | 1,835 ms | yes; empty answer to the cutoff question |
| `mistralai/mistral-small-2603` | 0.15 / 0.60 / 0.015 | ✅ | ✅ first try | **no cache hit** (0 / 0) | $0.00146 → $0.00160 | **437 ms** | off |
| `anthropic/claude-sonnet-5.5` (20260928), mid-tier | 2.00 / 10.00 / 0.10 | ✅ | ✅ first try | write 10,900 → cached 10,900 | $0.0273 → $0.0012 (−96%) | 590 ms | adaptive; none observed |

**Findings (spike 1)**
- **Cost field.** `usage.cost` (USD) is present on every OpenRouter response, both non-streaming and in the final streamed chunk. It matched the account: the ledger summed $0.047433 for OpenRouter, and `GET /api/v1/key` `usage` moved by $0.047433. `usage.cost_details.upstream_inference_*` splits the cost into prompt and completion.
- **Caching fields.**
  - Responses report `usage.prompt_tokens_details.cached_tokens` and `cache_write_tokens` (OpenRouter's name for cache creation). Both are captured: `cached_tokens` → `Usage.tokens_cached`. The raw block is kept in the spike results; `cache_write_tokens` is not in the contract yet.
  - Anthropic models need the explicit `cache_control` breakpoint. The client sends it automatically for `Message.cache_breakpoint` on `anthropic/*` and `google/gemini*`.
  - Writes cost 1.25× input (`input_cache_write` in the catalog). Reads cost 0.1× for Haiku 5.5 and 0.05× for Sonnet 5.5.
  - OpenAI, Qwen and Gemini cache implicitly as well.
- **No caching** was seen for DeepSeek V4.1 Flash or Mistral Small 4.
  - DeepSeek's two calls were served by different upstream providers at different prices. Caching and stable cost would need provider pinning, which works through `ModelRole.params`, e.g. `{"provider": {"order": [...], "allow_fallbacks": false}}`.
- **Reasoning models need explicit control.**
  - DeepSeek V4.1 Flash, Qwen 3.8 Flash and GPT-6 Luna reason by default, which adds output tokens and latency. With a small `max_output_tokens` they can return empty text: DeepSeek and Qwen returned nothing for the 300-token cutoff question.
  - For any such role, set `params: {reasoning: {effort: none}}` (or `low`) in `config/models.yaml`. This was not live-tested here.
- **Tool calling and JSON-schema mode** worked first try on all 7 models. No repair call was needed.
  - All 7 list `structured_outputs` in their catalog entry, so all got `response_format: json_schema`.
  - The prompt-mode fallback and the repair path are covered by mocked tests only.

### Tinker results (spike 2)

- **Endpoint.** Tinker has an **OpenAI-compatible API (beta)** at `https://tinker.thinkingmachines.dev/services/tinker-prod/oai/api/v1`, with the same bearer `TINKER_API_KEY`. `TinkerClient` is plain httpx over this endpoint.
  - The official `tinker` SDK (0.37.0, PyPI) also works. Its default base URL is already `https://tinker.thinkingmachines.dev/services/tinker-prod`, so no `TINKER_BASE_URL` override is needed. `ServiceClient().get_server_capabilities()` listed 35 models.
  - `api.tinker.thinkingmachines.ai` is blocked here, but nothing needs it.
- **Model id.** Serverless Inkling-Small is `thinkingmachines/Inkling-Small:peft:262144:sampling-nvfp4`. The `inkling-small` and `inkling` aliases in `config/models.yaml` map to these ids inside `TinkerClient`.
- **Reasoning is on by default** (effort 0.9). The first probe spent all 40 output tokens in `reasoning_content` and returned no answer. Use `params: {reasoning_effort: none}` for simulators and judges that must answer briefly. The spike ran with that setting.
- **Inkling-Small, `reasoning_effort: none`:**

| Check | Result |
|---|---|
| Plain chat latency | 0.9–1.4 s (short prompts) |
| Streaming TTFT | ~930 ms, **equal to total latency**: the short answer arrived as one chunk, so incremental streaming wasn't observable |
| JSON (prompt mode, validated) | ✅ first try, 1.3 s |
| Tools | not used (no tool calling on this endpoint; `TinkerClient` rejects tools) |
| Prefix caching | `usage.prompt_tokens_details.cached_tokens`: 0 → **7,680 of 7,802** on the repeat call |
| Cost on the long prefix (price table) | $0.00235 → $0.00050 (**−79%**; list price says cached input −80%) |

- **Cost.** Tinker returns **no cost field**, so the ledger prices calls from the table in `app/llm/tinker.py`.
  - Serverless rates: Inkling-Small $0.30 input / $0.06 cached / $1.20 output per 1M; Inkling $1.00 / $0.17 / $4.05. Source: thinkingmachines.ai/tinker/pricing, "as of October 19, 2026".
  - 02a §1.2 quoted $0.30 / $1.20 for Inkling-Small: that's the serverless rate. Non-serverless sampling of `thinkingmachines/Inkling-Small` is $0.58 / $1.44.
- **Real cost not confirmed.** The billing API (`tinker.AdminClient().get_billing_usage`, hourly buckets) returned **no events** for the last 12 h when checked. Either serverless usage isn't reported there yet, or it lags. Total Tinker spend by price table: **$0.0030**.
- **Rate limits.** Neither the docs nor any response mention them. None were hit at this volume.

### Training cutoffs (spike 5)

OpenRouter's `/api/v1/models` has a `knowledge_cutoff` field, but it is **null for every model tested**. Published cutoffs below come from provider docs. Self-reported answers come from asking the model (`max_output_tokens` 300) and are **unreliable**: for example, both Claude models gave dates 1.5 years earlier than the published cutoff.

| Model | Published cutoff (source) | Snapshot / release | Self-reported (unreliable) |
|---|---|---|---|
| Claude Haiku 5.5 | **Jun 2026** reliable knowledge + training data (platform.claude.com models overview) | 2026-10-07 | "January 2025" |
| Claude Sonnet 5.5 | **Jun 2026** (same page) | 2026-09-28 | "early 2025" |
| GPT-6 Luna | **May 18, 2026** (developers.openai.com model page) | 2026-09-22 | "June 2024" |
| Gemini 3.5 Flash-Lite | not published (the model page shows only "Latest update: July 2026") | 2026-07-21 | "March 2026" |
| DeepSeek V4.1 Flash | not published (HF card) | 2026-09-10 | (empty: reasoning used the budget) |
| Qwen 3.8 Flash | not published (HF card) | 2026-08-26 | (empty: reasoning used the budget) |
| Mistral Small 4 (2603) | not published (HF card) | 2026-03-16 | "June 2024" |
| Tinker Inkling-Small | not published (Tinker docs) | n/a | "January 2026" |

**Replay-window implication.** For the Claude 5.5 and GPT-6 Luna families, start replay windows **on or after 2026-07-01**. For models with no published cutoff, only the release date bounds the cutoff, so their cutoff could be as late as **Aug–Sep 2026** (Qwen 3.8, DeepSeek V4.1). Either pick windows from **Sep/Oct 2026**, or keep those families out of roles where knowing the news ahead of time matters (newsroom importance, chief-of-staff scoring).

### Recommendation: `dev_default`

**`anthropic/claude-haiku-5.5`**, now set in `config/models.yaml` (`dev_default`):
- cheapest tier ($0.10 / $0.50);
- tool calling and strict JSON-schema mode first try;
- explicit prompt caching with reported cached tokens and cache reads at 10% (−92% on a cached prefix);
- provider-reported cost;
- TTFT ~0.7 s;
- no reasoning tokens on the tool, JSON and cache probes. Adaptive thinking does show on open questions, so set `reasoning: {effort: none}` in `params` if that matters for a role.

Runner-up is `openai/gpt-6-luna`: same price, but slower TTFT and reasoning on by default. For latency-critical `voice.turn`, `mistralai/mistral-small-2603` had the best TTFT (437 ms) but no caching. Let the bake-off decide.

Caveat for evaluation: Claude in the agent roles keeps judges and simulators on Tinker Inkling (a different family), as the plan intends.

### Spend

| | USD |
|---|---|
| OpenRouter: `GET /api/v1/key` `usage` before → after | $1.846992 → $1.894429: **delta $0.047437** (spike ledger $0.047433 + live smoke test $0.000004). `usage_daily` equals the delta, so no other session spent on this key meanwhile |
| Tinker (price table; billing API showed no events yet) | $0.0030 |
| **Spike total** | **≈ $0.050** |

Two extra live smoke tests (`LLM_LIVE_TESTS=1 pytest -m live`) spent less than $0.001.
