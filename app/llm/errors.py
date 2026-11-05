"""Errors raised by the model layer. `BudgetExceeded` lives in app.contracts.common."""

from __future__ import annotations

from app.contracts.llm import StructuredOutputInvalid


class LLMError(Exception):
    """Base class for model-layer errors."""


class ProviderError(LLMError):
    """A provider returned an error that retrying will not fix (4xx other than 429, bad payload)."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class RetryableProviderError(ProviderError):
    """429 / 5xx / transport failure. Retried with backoff; raised once retries are exhausted."""


class StructuredOutputError(LLMError, StructuredOutputInvalid):
    """The model failed to return valid structured output after one repair attempt (TDD §4.4)."""

    def __init__(self, message: str, *, raw: str = "", errors: str = ""):
        super().__init__(message)
        self.raw = raw
        self.errors = errors


class SpendCapExceeded(LLMError):
    """The ledger's hard spend cap (USD) would be exceeded. Used to fence spikes and bake-offs."""
