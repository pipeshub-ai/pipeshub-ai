"""Model prices and output limits, as data (USD per 1M tokens).

OpenAI family: the Standard tier of https://developers.openai.com/api/docs/pricing, read
2026-10-03; maximum output tokens from each model's page under
https://developers.openai.com/api/docs/models/ (read for gpt-6-sol, gpt-5.6-terra,
gpt-5.4, gpt-4.1, gpt-4o-mini, o4-mini; the rest of each family assumed equal).
Azure OpenAI deployments are priced by their model name at the same list prices
(regional and Data Zone premiums are not modelled). Anthropic rows predate P6.5 and
keep the cache multipliers of ``pricing.py``. Update a row when a provider's page
changes; nothing else reads prices.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICES_READ_ON = "2026-10-03"
OPENAI_PRICES_SOURCE = "https://developers.openai.com/api/docs/pricing"


@dataclass(frozen=True, slots=True)
class Tier:
    input: float
    output: float
    # None: the provider publishes none; ``pricing.py`` applies its multipliers.
    cache_read: float | None = None
    cache_write: float | None = None


@dataclass(frozen=True, slots=True)
class ModelPrice:
    short: Tier
    # Prices for a call whose input exceeds ``long_context_above`` tokens.
    long: Tier | None = None
    long_context_above: int | None = None
    max_output_tokens: int | None = None


_GPT_LONG = 272_000

MODEL_PRICES: dict[str, ModelPrice] = {
    # Opus 5.5 and Sonnet 5.5 list cache reads at 5% of input, not the 10% ``pricing.py`` assumes.
    "claude-opus-5-5": ModelPrice(Tier(4.0, 20.0, 0.20)),
    "claude-sonnet-5-5": ModelPrice(Tier(2.0, 10.0, 0.10)),
    "claude-sonnet-5": ModelPrice(Tier(2.0, 10.0)),
    "claude-sonnet-4-6": ModelPrice(Tier(3.0, 15.0)),
    "claude-opus-4-8": ModelPrice(Tier(5.0, 25.0)),
    "claude-haiku-4-5": ModelPrice(Tier(1.0, 5.0)),
    "claude-haiku-4-5-20251001": ModelPrice(Tier(1.0, 5.0)),
    "gpt-6-astra": ModelPrice(Tier(10.00, 50.00, 1.00, 12.50), Tier(20.00, 75.00, 2.00, 25.00), _GPT_LONG, 128_000),
    "gpt-6.1-sol": ModelPrice(Tier(2.00, 10.00, 0.10, 2.50), Tier(4.00, 15.00, 0.20, 5.00), _GPT_LONG, 128_000),
    "gpt-6-sol": ModelPrice(Tier(2.00, 10.00, 0.20, 2.50), Tier(4.00, 15.00, 0.40, 5.00), _GPT_LONG, 128_000),
    "gpt-6-luna": ModelPrice(Tier(0.10, 0.50, 0.01, 0.125), Tier(0.20, 0.75, 0.02, 0.25), _GPT_LONG, 128_000),
    "gpt-5.6-sol": ModelPrice(Tier(4.00, 20.00, 0.40, 5.00), Tier(8.00, 30.00, 0.80, 10.00), _GPT_LONG, 128_000),
    "gpt-5.6-terra": ModelPrice(Tier(2.00, 12.00, 0.20, 2.50), Tier(4.00, 18.00, 0.40, 5.00), _GPT_LONG, 128_000),
    "gpt-5.6-luna": ModelPrice(Tier(0.20, 1.20, 0.02, 0.25), Tier(0.40, 1.80, 0.04, 0.50), _GPT_LONG, 128_000),
    "gpt-5.5": ModelPrice(Tier(5.00, 30.00, 0.50, 0.0), Tier(10.00, 45.00, 1.00, 0.0), _GPT_LONG, 128_000),
    "gpt-5.5-pro": ModelPrice(Tier(30.00, 180.00), max_output_tokens=128_000),
    "gpt-5.4": ModelPrice(Tier(2.50, 15.00, 0.25, 0.0), Tier(5.00, 22.50, 0.50, 0.0), _GPT_LONG, 128_000),
    "gpt-5.4-mini": ModelPrice(Tier(0.75, 4.50, 0.075, 0.0), max_output_tokens=128_000),
    "gpt-5.4-nano": ModelPrice(Tier(0.20, 1.25, 0.02, 0.0), max_output_tokens=128_000),
    "gpt-5.4-pro": ModelPrice(Tier(30.00, 180.00), max_output_tokens=128_000),
    "gpt-5.3-codex": ModelPrice(Tier(1.75, 14.00, 0.175, 0.0), max_output_tokens=128_000),
    "gpt-5.2": ModelPrice(Tier(1.75, 14.00, 0.175, 0.0), max_output_tokens=128_000),
    "gpt-5.2-pro": ModelPrice(Tier(21.00, 168.00), max_output_tokens=128_000),
    "gpt-5.1": ModelPrice(Tier(1.25, 10.00, 0.125, 0.0), max_output_tokens=128_000),
    "gpt-5": ModelPrice(Tier(1.25, 10.00, 0.125, 0.0), max_output_tokens=128_000),
    "gpt-5-mini": ModelPrice(Tier(0.25, 2.00, 0.025, 0.0), max_output_tokens=128_000),
    "gpt-5-nano": ModelPrice(Tier(0.05, 0.40, 0.005, 0.0), max_output_tokens=128_000),
    "gpt-5-pro": ModelPrice(Tier(15.00, 120.00), max_output_tokens=128_000),
    "gpt-4.1": ModelPrice(Tier(2.00, 8.00, 0.50, 0.0), max_output_tokens=32_768),
    "gpt-4.1-mini": ModelPrice(Tier(0.40, 1.60, 0.10, 0.0), max_output_tokens=32_768),
    "gpt-4.1-nano": ModelPrice(Tier(0.10, 0.40, 0.025, 0.0), max_output_tokens=32_768),
    "gpt-4o": ModelPrice(Tier(2.50, 10.00, 1.25, 0.0), max_output_tokens=16_384),
    "gpt-4o-mini": ModelPrice(Tier(0.15, 0.60, 0.075, 0.0), max_output_tokens=16_384),
    "o1": ModelPrice(Tier(15.00, 60.00, 7.50, 0.0), max_output_tokens=100_000),
    "o3": ModelPrice(Tier(2.00, 8.00, 0.50, 0.0), max_output_tokens=100_000),
    "o3-pro": ModelPrice(Tier(20.00, 80.00), max_output_tokens=100_000),
    "o3-mini": ModelPrice(Tier(1.10, 4.40, 0.55, 0.0), max_output_tokens=100_000),
    "o4-mini": ModelPrice(Tier(1.10, 4.40, 0.275, 0.0), max_output_tokens=100_000),
}

__all__ = ["MODEL_PRICES", "OPENAI_PRICES_SOURCE", "PRICES_READ_ON", "ModelPrice", "Tier"]
