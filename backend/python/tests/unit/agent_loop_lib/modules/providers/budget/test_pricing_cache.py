"""``ModelPricing.cost_usd`` with prompt caching: ``input_tokens`` includes
cached tokens, so only the uncached remainder is priced as fresh input."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.modules.providers.budget.pricing import ModelPricing

PRICE = ModelPricing(input_price_per_mtok=4.0, output_price_per_mtok=20.0)


@pytest.mark.parametrize(("input_tokens", "read", "write", "expected"), [
    (1_000_000, 0, 0, 4.0),
    (1_000_000, 900_000, 0, 0.4 + 0.9 * 0.4),
    (1_000_000, 0, 1_000_000, 5.0),
    (1_000_000, 600_000, 300_000, 0.1 * 4 + 0.6 * 0.4 + 0.3 * 5),
    (100, 500, 0, 500 * 0.4 / 1_000_000),
])
def test_cached_tokens_are_priced_once(input_tokens: int, read: int, write: int, expected: float) -> None:
    assert PRICE.cost_usd(input_tokens, 0, read, write) == pytest.approx(expected)


def test_caching_never_costs_more_than_reading_the_prefix_fresh_twice() -> None:
    fresh = PRICE.cost_usd(200_000, 1_000)
    cached = PRICE.cost_usd(100_000, 500, 0, 95_000) + PRICE.cost_usd(100_000, 500, 95_000, 5_000)
    assert cached < fresh
