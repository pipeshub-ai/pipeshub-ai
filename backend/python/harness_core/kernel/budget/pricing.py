# pyright: basic
from __future__ import annotations

import logging

from pydantic import BaseModel

from harness_core.kernel.budget.prices import MODEL_PRICES, ModelPrice, Tier

logger = logging.getLogger(__name__)

# USD per 1M tokens: (input, output), for readers that want only the base prices.
# Single source of truth for cost math: the data table in ``prices.py``.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    name: (price.short.input, price.short.output) for name, price in MODEL_PRICES.items()
}

_DEFAULT_PRICING = (3.0, 15.0)  # falls back to Sonnet-class pricing for unknown models

# Prompt-cache economics as fractions of the input price, applied when a provider publishes
# no cache prices of its own: reads are cheaper, writes cost a premium (Anthropic's 5-minute
# writes, OpenAI's on GPT-5.6 and later; a provider that does not bill writes reports none).
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


class ModelPricing(BaseModel):
    input_price_per_mtok: float
    output_price_per_mtok: float
    cache_read_override_per_mtok: float | None = None
    cache_write_override_per_mtok: float | None = None

    @property
    def cache_read_price_per_mtok(self) -> float:
        if self.cache_read_override_per_mtok is not None:
            return self.cache_read_override_per_mtok
        return self.input_price_per_mtok * CACHE_READ_MULTIPLIER

    @property
    def cache_write_price_per_mtok(self) -> float:
        if self.cache_write_override_per_mtok is not None:
            return self.cache_write_override_per_mtok
        return self.input_price_per_mtok * CACHE_WRITE_MULTIPLIER

    def cost_usd(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """``input_tokens`` counts every prompt token, cached ones included (the
        convention every transport reports in); only the uncached remainder is
        priced as fresh input."""
        uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        return (
            uncached * self.input_price_per_mtok
            + output_tokens * self.output_price_per_mtok
            + cache_read_tokens * self.cache_read_price_per_mtok
            + cache_write_tokens * self.cache_write_price_per_mtok
        ) / 1_000_000


def _tier(price: ModelPrice, prompt_tokens: int) -> Tier:
    if price.long is not None and price.long_context_above is not None and prompt_tokens > price.long_context_above:
        return price.long
    return price.short


def get_pricing(model: str | None, *, prompt_tokens: int = 0) -> ModelPricing:
    """Look up per-model pricing, falling back to Sonnet-class defaults for
    unknown/local/mock models so cost math never raises for an unrecognised name.
    ``prompt_tokens`` (input and cached input) picks a long-context tier where one exists."""
    price = MODEL_PRICES.get(model or "")
    if price is None:
        input_price, output_price = _DEFAULT_PRICING
        return ModelPricing(input_price_per_mtok=input_price, output_price_per_mtok=output_price)
    tier = _tier(price, prompt_tokens)
    return ModelPricing(
        input_price_per_mtok=tier.input,
        output_price_per_mtok=tier.output,
        cache_read_override_per_mtok=tier.cache_read,
        cache_write_override_per_mtok=tier.cache_write,
    )


def max_output_tokens(model: str | None) -> int | None:
    """The model's published maximum output, or None when the table does not know it."""
    price = MODEL_PRICES.get(model or "")
    return price.max_output_tokens if price is not None else None


# Total context window per model, in tokens. Drives the Phase 1 context
# budget (window size minus a reserved-for-output slice) rather than a
# hardcoded flat number — the single source of truth other model metadata
# tables in this file already establish the pattern for.
MODEL_CONTEXT_WINDOW: dict[str, int] = {
    "claude-opus-5-5": 300_000,
    "claude-opus-5": 300_000,
    "claude-sonnet-5-5": 200_000,
    "claude-sonnet-5": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-8": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "gpt-5.3-codex": 128_000,
    "gpt-5.5-extra-high": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-5.4-mini": 128_000,
    "gpt-5.4": 128_000,
    "gpt-5.6-luna": 200_000,
    "gpt-5.6-terra": 200_000,
    # A working budget well below its 922k input limit: prompts past 272k are
    # billed at the long-context rate and answer slower. contextLength overrides it.
    "gpt-6.1-sol": 300_000,
    # Maximum input, not the 1.05M window: the API rejects a prompt above it.
    "gpt-6-sol": 922_000,
    "gpt-6-luna": 922_000,
    "gpt-6-astra": 922_000,
    "gpt-5.6-sol": 922_000,
    # OpenAI's alias for gpt-5.6-sol.
    "gpt-5.6": 922_000,
    "gpt-5.5": 922_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000

_FALLBACK_LOGGED: set[str] = set()


def get_context_window(model: str | None) -> int:
    """Total token window for a model, falling back to a conservative default
    for unknown/local/mock models."""
    window = MODEL_CONTEXT_WINDOW.get(model or "")
    if window is not None:
        return window
    if model and model not in _FALLBACK_LOGGED:
        _FALLBACK_LOGGED.add(model)
        logger.info(
            "No context window known for model %r; budgeting for %d tokens. "
            "Set contextLength on the model's configuration to use its real window.",
            model, _DEFAULT_CONTEXT_WINDOW,
        )
    return _DEFAULT_CONTEXT_WINDOW
