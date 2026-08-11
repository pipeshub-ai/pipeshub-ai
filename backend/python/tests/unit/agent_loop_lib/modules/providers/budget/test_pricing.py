"""`get_pricing`/`ModelPricing` per-provider cache multipliers (prompt-caching
Phase 7). Before this phase every model was priced with Anthropic's cache
read/write ratios regardless of which provider actually served the call —
harmless while `MODEL_PRICING` only listed Claude models, but silently wrong
cost math the moment a non-Anthropic model's cache tokens got run through it.
These tests pin: (1) the historical no-`provider`-argument call keeps its old
Anthropic-shaped numbers exactly, and (2) passing a provider selects that
provider's own multiplier pair.
"""

from __future__ import annotations

from app.agent_loop_lib.modules.providers.budget.pricing import (
    CACHE_MULTIPLIERS,
    get_pricing,
)


class TestBackwardCompatibleDefault:
    def test_no_provider_argument_keeps_anthropic_shaped_multipliers(self) -> None:
        pricing = get_pricing("claude-sonnet-5")
        assert pricing.cache_read_multiplier == 0.10
        assert pricing.cache_write_multiplier == 1.25

    def test_unrecognised_provider_falls_back_to_anthropic_shaped_default(self) -> None:
        pricing = get_pricing("claude-sonnet-5", provider="some-unknown-gateway")
        assert pricing.cache_read_multiplier == 0.10
        assert pricing.cache_write_multiplier == 1.25


class TestPerProviderMultipliers:
    def test_openai_read_and_write_multipliers(self) -> None:
        pricing = get_pricing("gpt-4o", provider="openai")
        assert pricing.cache_read_multiplier == 0.50
        assert pricing.cache_write_multiplier == 1.0

    def test_azure_openai_matches_openai(self) -> None:
        pricing = get_pricing("gpt-4o", provider="azure_openai")
        assert pricing.cache_read_multiplier == 0.50
        assert pricing.cache_write_multiplier == 1.0

    def test_google_read_and_write_multipliers(self) -> None:
        pricing = get_pricing("gemini-2.5-pro", provider="google")
        assert pricing.cache_read_multiplier == 0.25
        assert pricing.cache_write_multiplier == 1.0

    def test_provider_lookup_is_case_insensitive(self) -> None:
        pricing = get_pricing("gpt-4o", provider="OpenAI")
        assert pricing.cache_read_multiplier == 0.50


class TestCostMathUsesSelectedMultiplier:
    def test_cache_read_and_write_price_reflect_provider(self) -> None:
        anthropic_pricing = get_pricing("claude-sonnet-5", provider="anthropic")
        openai_pricing = get_pricing("claude-sonnet-5", provider="openai")

        # Same base input price (both look up the same model string) — only
        # the cache multiplier differs by provider.
        assert anthropic_pricing.input_price_per_mtok == openai_pricing.input_price_per_mtok
        assert anthropic_pricing.cache_read_price_per_mtok != openai_pricing.cache_read_price_per_mtok
        assert anthropic_pricing.cache_write_price_per_mtok != openai_pricing.cache_write_price_per_mtok

    def test_cost_usd_accounts_for_provider_specific_cache_pricing(self) -> None:
        openai_pricing = get_pricing("gpt-4o", provider="openai")
        cost = openai_pricing.cost_usd(
            input_tokens=0, output_tokens=0, cache_read_tokens=1_000_000, cache_write_tokens=0
        )
        assert cost == openai_pricing.input_price_per_mtok * 0.50


class TestCacheMultipliersTableIsComplete:
    def test_every_entry_is_a_read_write_pair_of_floats(self) -> None:
        for provider, (read_mult, write_mult) in CACHE_MULTIPLIERS.items():
            assert isinstance(provider, str)
            assert isinstance(read_mult, float)
            assert isinstance(write_mult, float)
            assert read_mult >= 0
            assert write_mult >= 0
