"""Token-budget enforcement around model calls (TDD §4.6).

Before a call: estimate input tokens and refuse (`BudgetExceeded`) if the run would go over.
After a call: charge the provider-reported usage. Post-call charging never raises: the tokens
were already spent, so they are recorded and the *next* call is refused instead.
"""

from __future__ import annotations

import json

from app.contracts.common import BudgetExceeded, TokenBudget
from app.contracts.llm import ChatRequest

CHARS_PER_TOKEN = 4  # conservative-ish heuristic for English + JSON; no tokenizer dependency


def estimate_input_tokens(req: ChatRequest) -> int:
    n = 0
    for m in req.messages:
        n += len(m.content) + 8 * CHARS_PER_TOKEN  # per-message framing overhead
        if m.tool_calls:
            n += sum(len(tc.name) + len(json.dumps(tc.arguments)) for tc in m.tool_calls)
    if req.tools:
        n += sum(len(t.name) + len(t.description) + len(json.dumps(t.parameters)) for t in req.tools)
    if req.response_schema is not None:
        n += len(json.dumps(req.response_schema.model_json_schema()))
    return max(1, n // CHARS_PER_TOKEN)


def precheck(budget: TokenBudget, est_in: int) -> None:
    if budget.used_input_tokens + est_in > budget.input_tokens:
        raise BudgetExceeded(
            f"input token budget {budget.input_tokens} would be exceeded "
            f"(used {budget.used_input_tokens}, next call ~{est_in})"
        )
    if budget.output_tokens is not None and budget.used_output_tokens >= budget.output_tokens:
        raise BudgetExceeded(f"output token budget {budget.output_tokens} exhausted")


def charge_actual(budget: TokenBudget, tokens_in: int, tokens_out: int) -> None:
    budget.used_input_tokens += tokens_in
    budget.used_output_tokens += tokens_out
