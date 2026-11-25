"""BU-05: cache reads and writes are priced per model, so the cost ledger's per session, user,
agent and org totals (Node, ``cost-ledger.test.ts``) add up what each call really cost: a model that
publishes cache prices is charged its own, one that does not the Anthropic multipliers."""

from __future__ import annotations

import pytest

from harness_core.kernel.budget.prices import MODEL_PRICES
from harness_core.kernel.budget.pricing import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    get_pricing,
)

CALL = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_tokens": 600_000, "cache_write_tokens": 200_000}


def _expected(input_price: float, output_price: float, read_price: float, write_price: float) -> float:
    uncached = CALL["input_tokens"] - CALL["cache_read_tokens"] - CALL["cache_write_tokens"]
    return (
        uncached * input_price
        + CALL["output_tokens"] * output_price
        + CALL["cache_read_tokens"] * read_price
        + CALL["cache_write_tokens"] * write_price
    ) / 1_000_000


def test_bu05_a_model_with_published_cache_prices_is_charged_its_own() -> None:
    tier = MODEL_PRICES["gpt-6-sol"].short
    assert tier.cache_read is not None and tier.cache_write is not None
    cost = get_pricing("gpt-6-sol", prompt_tokens=CALL["input_tokens"] // 10).cost_usd(**CALL)
    assert cost == pytest.approx(_expected(tier.input, tier.output, tier.cache_read, tier.cache_write))


def test_bu05_a_model_without_cache_prices_is_charged_the_multipliers() -> None:
    tier = MODEL_PRICES["claude-sonnet-4-6"].short
    assert tier.cache_read is None and tier.cache_write is None
    cost = get_pricing("claude-sonnet-4-6").cost_usd(**CALL)
    assert cost == pytest.approx(
        _expected(tier.input, tier.output, tier.input * CACHE_READ_MULTIPLIER, tier.input * CACHE_WRITE_MULTIPLIER)
    )


def test_bu05_the_same_tokens_cost_differently_per_model() -> None:
    costs = {model: get_pricing(model).cost_usd(**CALL) for model in ("gpt-6-sol", "gpt-6-luna", "claude-sonnet-4-6")}
    assert len(set(costs.values())) == 3
    assert costs["gpt-6-luna"] < costs["gpt-6-sol"]
