"""P6.5 PR-1..PR-4: prices as data, cached input per model, output allowances."""

from __future__ import annotations

import pytest

from harness_core.access.spend import (
    OutputAllowance,
    priced,
    reservation_for,
    usage_spend,
)
from harness_core.kernel.budget.prices import MODEL_PRICES
from harness_core.kernel.budget.pricing import get_pricing, max_output_tokens
from harness_core.kernel.core.responses import TokenUsage

MTOK = 1_000_000


@pytest.mark.parametrize(
    ("model", "input_usd", "cached_usd", "output_usd"),
    [
        # https://developers.openai.com/api/docs/pricing, Standard tier, read 2026-10-03.
        ("gpt-6-sol", 2.00, 0.20, 10.00),
        ("gpt-5.6-terra", 2.00, 0.20, 12.00),
        ("gpt-5.6-luna", 0.20, 0.02, 1.20),
        ("gpt-5.5", 5.00, 0.50, 30.00),
        ("gpt-5.4", 2.50, 0.25, 15.00),
        ("gpt-5.4-mini", 0.75, 0.075, 4.50),
        ("gpt-5.3-codex", 1.75, 0.175, 14.00),
        ("gpt-5", 1.25, 0.125, 10.00),
        ("gpt-5-mini", 0.25, 0.025, 2.00),
        ("gpt-4.1", 2.00, 0.50, 8.00),
        ("gpt-4.1-mini", 0.40, 0.10, 1.60),
        ("gpt-4o", 2.50, 1.25, 10.00),
        ("gpt-4o-mini", 0.15, 0.075, 0.60),
        ("o3", 2.00, 0.50, 8.00),
        ("o4-mini", 1.10, 0.275, 4.40),
    ],
)
def test_pr1_pr2_openai_rows(model: str, input_usd: float, cached_usd: float, output_usd: float) -> None:
    assert priced(model)
    pricing = get_pricing(model)
    assert (pricing.input_price_per_mtok, pricing.cache_read_price_per_mtok, pricing.output_price_per_mtok) == (
        input_usd,
        cached_usd,
        output_usd,
    )
    # 200,000 prompt tokens, half of them cached reads: below every long-context threshold.
    usage = TokenUsage(input_tokens=200_000, output_tokens=100_000, cache_read_tokens=100_000)
    assert usage_spend(usage, model, charges_usd=True).usd_micros == round((input_usd + cached_usd + output_usd) * MTOK / 10)


def test_pr1_a_long_prompt_is_priced_at_the_long_context_tier() -> None:
    short = usage_spend(TokenUsage(input_tokens=272_000, output_tokens=0), "gpt-5.4", charges_usd=True)
    long = usage_spend(TokenUsage(input_tokens=272_001, output_tokens=0), "gpt-5.4", charges_usd=True)
    assert short.usd_micros == 680_000
    assert long.usd_micros == 1_360_005


def test_pr2_anthropic_keeps_the_cache_multipliers() -> None:
    pricing = get_pricing("claude-sonnet-4-6")
    assert (pricing.cache_read_price_per_mtok, pricing.cache_write_price_per_mtok) == pytest.approx((0.30, 3.75))


@pytest.mark.parametrize(
    ("model", "allowance", "tokens"),
    [
        ("gpt-5.4", OutputAllowance(), 128_000),
        ("gpt-4o-mini", OutputAllowance(), 16_384),
        ("gpt-4.1", OutputAllowance(), 32_768),
        ("o4-mini", OutputAllowance(), 100_000),
        ("unknown-model", OutputAllowance(), 32_768),
        ("unknown-model", OutputAllowance(default_tokens=2_000), 2_000),
        ("gpt-5.4", OutputAllowance(cap_tokens=8_000), 8_000),
        ("gpt-4o-mini", OutputAllowance(cap_tokens=64_000), 16_384),
    ],
)
def test_pr3_output_allowance(model: str, allowance: OutputAllowance, tokens: int) -> None:
    assert allowance.tokens(model) == tokens


def test_pr3_a_reservation_is_input_plus_allowance_and_its_minimum_the_input() -> None:
    requested, minimum = reservation_for(1_000, "gpt-5.4", OutputAllowance(), charges_usd=True)
    # The money minimum is the input at the fresh price; the token minimum the input read from the cache
    # (gpt-5.4: 0.25 / 2.50 of a fresh token, ENGBUDGET).
    assert (minimum.usd_micros, minimum.tokens) == (2_500, 100)
    assert (requested.usd_micros, requested.tokens) == (2_500 + 1_920_000, 129_000)
    free, floor = reservation_for(1_000, "gpt-5.4", OutputAllowance(), charges_usd=False)
    assert (free.usd_micros, floor.usd_micros, free.tokens) == (0, 0, 129_000)


def test_pr4_an_unknown_model_gets_the_default_price() -> None:
    assert not priced("my-local-model")
    assert (get_pricing("my-local-model").input_price_per_mtok, max_output_tokens("my-local-model")) == (3.0, None)


def test_every_row_is_well_formed() -> None:
    for name, price in MODEL_PRICES.items():
        assert price.short.input > 0 and price.short.output > 0, name
        assert (price.long is None) == (price.long_context_above is None), name
