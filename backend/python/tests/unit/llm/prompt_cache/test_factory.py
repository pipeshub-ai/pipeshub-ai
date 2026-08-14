"""`resolve_strategy` composes `resolve_capability` with the concrete
strategy table — `mode="none"` (unsupported provider, unrecognized
model, or a capability-supported-but-not-yet-wired provider) must
always degrade to the inert Noop, never raise or guess."""

from __future__ import annotations

from app.agent_loop_lib.cache.base import NoopStrategy
from app.llm.prompt_cache.factory import resolve_strategy
from app.llm.prompt_cache.strategy.anthropic import AnthropicCacheStrategy
from app.llm.prompt_cache.strategy.openai import OpenAICacheStrategy


class TestResolveStrategy:
    def test_anthropic_resolves_to_anthropic_strategy(self) -> None:
        strategy = resolve_strategy("anthropic", "claude-sonnet-4-6")
        assert isinstance(strategy, AnthropicCacheStrategy)

    def test_unsupported_provider_resolves_to_noop(self) -> None:
        strategy = resolve_strategy("some_future_provider", "model-x")
        assert isinstance(strategy, NoopStrategy)

    def test_unrecognized_anthropic_legacy_model_resolves_to_noop(self) -> None:
        strategy = resolve_strategy("anthropic", "claude-2.1")
        assert isinstance(strategy, NoopStrategy)

    def test_openai_explicit_model_resolves_to_openai_strategy(self) -> None:
        strategy = resolve_strategy("openai", "gpt-5.6-luna")
        assert isinstance(strategy, OpenAICacheStrategy)

    def test_openai_automatic_model_also_resolves_to_openai_strategy(self) -> None:
        # Automatic mode still gets a strategy — it applies
        # `prompt_cache_key` even though it places no breakpoints.
        strategy = resolve_strategy("openai", "gpt-4o-mini")
        assert isinstance(strategy, OpenAICacheStrategy)

    def test_azure_openai_resolves_to_openai_strategy(self) -> None:
        strategy = resolve_strategy("azure_openai", "gpt-5.6-luna")
        assert isinstance(strategy, OpenAICacheStrategy)

    def test_unrecognized_openai_model_resolves_to_noop(self) -> None:
        strategy = resolve_strategy("openai", "not-a-real-gpt")
        assert isinstance(strategy, NoopStrategy)

    def test_no_model_name_still_resolves(self) -> None:
        strategy = resolve_strategy("anthropic", None)
        assert isinstance(strategy, NoopStrategy)

    def test_unsupported_google_provider_still_resolves_to_noop_until_phase_9(self) -> None:
        strategy = resolve_strategy("google", "gemini-2.5-pro")
        assert isinstance(strategy, NoopStrategy)
