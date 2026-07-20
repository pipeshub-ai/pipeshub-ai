from __future__ import annotations

from pydantic import BaseModel

# Prices are USD per 1M tokens: (input, output). Single source of truth for
# cost math — both BudgetTracker (enforcement) and the CLI (display) read
# this table instead of keeping their own hardcoded copies in sync by hand.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
    "claude-haiku-4-5": (0.80, 4.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}

_DEFAULT_PRICING = (3.0, 15.0)  # falls back to Sonnet-class pricing for unknown models

# Anthropic prompt-cache economics: reads cost a fraction of a fresh input
# token, writes cost a premium. Applied uniformly since cache mechanics are
# provider-specific but this ratio is stable across Anthropic's current models.
CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


class ModelPricing(BaseModel):
    input_price_per_mtok: float
    output_price_per_mtok: float

    @property
    def cache_read_price_per_mtok(self) -> float:
        return self.input_price_per_mtok * CACHE_READ_MULTIPLIER

    @property
    def cache_write_price_per_mtok(self) -> float:
        return self.input_price_per_mtok * CACHE_WRITE_MULTIPLIER

    def cost_usd(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        return (
            input_tokens * self.input_price_per_mtok
            + output_tokens * self.output_price_per_mtok
            + cache_read_tokens * self.cache_read_price_per_mtok
            + cache_write_tokens * self.cache_write_price_per_mtok
        ) / 1_000_000


def get_pricing(model: str | None) -> ModelPricing:
    """Look up per-model pricing, falling back to Sonnet-class defaults for
    unknown/local/mock models so cost math never raises for an unrecognised name."""
    input_price, output_price = MODEL_PRICING.get(model or "", _DEFAULT_PRICING)
    return ModelPricing(input_price_per_mtok=input_price, output_price_per_mtok=output_price)


# Total context window per model, in tokens. Drives the Phase 1 context
# budget (window size minus a reserved-for-output slice) rather than a
# hardcoded flat number — the single source of truth other model metadata
# tables in this file already establish the pattern for.
MODEL_CONTEXT_WINDOW: dict[str, int] = {
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-8": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "gpt-5.3-codex": 128_000,
    "gpt-5.5-extra-high": 128_000,
}

_DEFAULT_CONTEXT_WINDOW = 128_000


def get_context_window(model: str | None) -> int:
    """Total token window for a model, falling back to a conservative default
    for unknown/local/mock models."""
    return MODEL_CONTEXT_WINDOW.get(model or "", _DEFAULT_CONTEXT_WINDOW)
