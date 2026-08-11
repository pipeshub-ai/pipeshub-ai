from __future__ import annotations

from pydantic import BaseModel

# Prices are USD per 1M tokens: (input, output). Single source of truth for
# cost math — both BudgetTracker (enforcement) and the CLI (display) read
# this table instead of keeping their own hardcoded copies in sync by hand.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (15.0, 75.0),
    "claude-haiku-4-5": (0.80, 4.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
}

_DEFAULT_PRICING = (3.0, 15.0)  # falls back to Sonnet-class pricing for unknown models

# Prompt-cache economics: reads cost a fraction of a fresh input token,
# writes cost a premium (or, for OpenAI, no premium at all — it caches
# automatically at no extra charge). These ratios are provider-specific,
# not model-specific, and are the same figures
# `app.llm.prompt_cache.capabilities` uses for cache-eligibility decisions
# — duplicated here rather than imported (this module is
# `agent_loop_lib`'s own hermetic budget-enforcement path, `app.llm.
# prompt_cache` is framework-neutral and used by indexing/query without
# any `agent_loop_lib` dependency; importing across that boundary in
# either direction would invert it for a two-float lookup table).
# (read_multiplier, write_multiplier)
CACHE_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "anthropic": (0.10, 1.25),
    "openai": (0.50, 1.0),
    "azure_openai": (0.50, 1.0),
    "google": (0.25, 1.0),
}
_DEFAULT_CACHE_MULTIPLIERS = CACHE_MULTIPLIERS["anthropic"]  # every model in MODEL_PRICING today is Claude

# Backward-compatible module-level aliases for the pre-Phase-7 Anthropic-only
# constants — nothing in this codebase reads these directly anymore
# (`ModelPricing` now carries its own resolved multipliers), kept only so a
# stale external reference degrades to the old default instead of an
# `AttributeError`.
CACHE_READ_MULTIPLIER, CACHE_WRITE_MULTIPLIER = _DEFAULT_CACHE_MULTIPLIERS


class ModelPricing(BaseModel):
    input_price_per_mtok: float
    output_price_per_mtok: float
    cache_read_multiplier: float = _DEFAULT_CACHE_MULTIPLIERS[0]
    cache_write_multiplier: float = _DEFAULT_CACHE_MULTIPLIERS[1]

    @property
    def cache_read_price_per_mtok(self) -> float:
        return self.input_price_per_mtok * self.cache_read_multiplier

    @property
    def cache_write_price_per_mtok(self) -> float:
        return self.input_price_per_mtok * self.cache_write_multiplier

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


def get_pricing(model: str | None, provider: str | None = None) -> ModelPricing:
    """Look up per-model pricing, falling back to Sonnet-class defaults for
    unknown/local/mock models so cost math never raises for an unrecognised
    name. `provider` selects the cache read/write multiplier pair
    (`CACHE_MULTIPLIERS`); omitted or unrecognised falls back to the
    Anthropic-shaped default, matching every caller from before this
    parameter existed."""
    input_price, output_price = MODEL_PRICING.get(model or "", _DEFAULT_PRICING)
    read_multiplier, write_multiplier = CACHE_MULTIPLIERS.get(
        (provider or "").lower(), _DEFAULT_CACHE_MULTIPLIERS
    )
    return ModelPricing(
        input_price_per_mtok=input_price,
        output_price_per_mtok=output_price,
        cache_read_multiplier=read_multiplier,
        cache_write_multiplier=write_multiplier,
    )


# Total context window per model, in tokens. Drives the Phase 1 context
# budget (window size minus a reserved-for-output slice) rather than a
# hardcoded flat number — the single source of truth other model metadata
# tables in this file already establish the pattern for.
MODEL_CONTEXT_WINDOW: dict[str, int] = {
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
}

_DEFAULT_CONTEXT_WINDOW = 128_000


def get_context_window(model: str | None) -> int:
    """Total token window for a model, falling back to a conservative default
    for unknown/local/mock models."""
    return MODEL_CONTEXT_WINDOW.get(model or "", _DEFAULT_CONTEXT_WINDOW)
