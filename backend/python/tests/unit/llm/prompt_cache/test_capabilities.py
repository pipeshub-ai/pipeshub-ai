"""`resolve_capability` must be resolved by (provider, model) — not
provider alone — per the plan's review note 1. These tests pin the
exact cases the plan calls out: GPT-5.6+ explicit vs GPT-5.5
automatic, Gemini 2.5 vs 3.x floors, Claude tiers, and the
conservative "unknown model degrades to none" fallback.
"""

from __future__ import annotations

from app.llm.prompt_cache.capabilities import resolve_capability


class TestAnthropic:
    def test_sonnet_floor_is_1024(self) -> None:
        cap = resolve_capability("anthropic", "claude-sonnet-4-6")
        assert cap.mode == "explicit"
        assert cap.min_prefix_tokens == 1024
        assert cap.max_breakpoints == 4
        assert cap.can_cache_tools is True
        assert cap.can_cache_system is True

    def test_opus_floor_is_1024(self) -> None:
        cap = resolve_capability("anthropic", "claude-opus-4-1")
        assert cap.min_prefix_tokens == 1024

    def test_haiku_floor_is_2048(self) -> None:
        cap = resolve_capability("anthropic", "claude-3-5-haiku-20241022")
        assert cap.min_prefix_tokens == 2048

    def test_legacy_claude_2_is_not_cacheable(self) -> None:
        cap = resolve_capability("anthropic", "claude-2.1")
        assert cap.mode == "none"

    def test_unknown_model_name_degrades_to_conservative_floor(self) -> None:
        cap = resolve_capability("anthropic", "claude-future-model-9")
        assert cap.mode == "explicit"
        assert cap.min_prefix_tokens == 2048

    def test_no_model_name_at_all_degrades_to_none(self) -> None:
        cap = resolve_capability("anthropic", None)
        assert cap.mode == "none"

    def test_supports_extended_ttl_capability(self) -> None:
        cap = resolve_capability("anthropic", "claude-sonnet-4-6")
        assert cap.default_ttl == "5m"
        assert cap.extended_ttl == "1h"


class TestOpenAI:
    def test_gpt_5_6_is_explicit(self) -> None:
        cap = resolve_capability("openai", "gpt-5.6-luna")
        assert cap.mode == "explicit"
        assert cap.max_breakpoints == 4

    def test_gpt_5_5_is_automatic(self) -> None:
        cap = resolve_capability("openai", "gpt-5.5-mini")
        assert cap.mode == "automatic"
        assert cap.max_breakpoints == 0

    def test_gpt_4o_is_automatic(self) -> None:
        cap = resolve_capability("openai", "gpt-4o-mini")
        assert cap.mode == "automatic"

    def test_azure_openai_uses_same_rules_as_openai(self) -> None:
        cap = resolve_capability("azure_openai", "gpt-5.6-luna")
        assert cap.mode == "explicit"

    def test_openai_does_not_charge_write_premium(self) -> None:
        cap = resolve_capability("openai", "gpt-5.6-luna")
        assert cap.write_multiplier == 1.0

    def test_explicit_ttl_is_thirty_minutes_the_only_supported_value(self) -> None:
        cap = resolve_capability("openai", "gpt-5.6-luna")
        assert cap.default_ttl == "30m"

    def test_cannot_cache_tools_independently_of_message_content(self) -> None:
        """`prompt_cache_breakpoint` lands on a message content block,
        never on the `tools` array — unlike Anthropic's per-tool
        `cache_control`."""
        explicit = resolve_capability("openai", "gpt-5.6-luna")
        automatic = resolve_capability("openai", "gpt-4o-mini")
        assert explicit.can_cache_tools is False
        assert automatic.can_cache_tools is False


class TestGoogle:
    def test_gemini_2x_floor_is_2048(self) -> None:
        cap = resolve_capability("google", "gemini-2.5-pro")
        assert cap.mode == "automatic"
        assert cap.min_prefix_tokens == 2048

    def test_gemini_3x_floor_is_4096(self) -> None:
        cap = resolve_capability("google", "gemini-3-pro")
        assert cap.min_prefix_tokens == 4096

    def test_unknown_gemini_generation_is_none(self) -> None:
        cap = resolve_capability("google", "gemini-1.0-pro")
        assert cap.mode == "none"

    def test_automatic_mode_has_zero_breakpoints(self) -> None:
        cap = resolve_capability("google", "gemini-2.5-flash")
        assert cap.max_breakpoints == 0


class TestBedrockAndUnknownProviders:
    def test_bedrock_resolves_to_none_pending_phase_9_spike(self) -> None:
        cap = resolve_capability("bedrock", "anthropic.claude-sonnet-4-6")
        assert cap.mode == "none"

    def test_unrecognized_provider_is_none(self) -> None:
        cap = resolve_capability("some_future_provider", "model-x")
        assert cap.mode == "none"

    def test_empty_provider_is_none(self) -> None:
        cap = resolve_capability("", "model-x")
        assert cap.mode == "none"

    def test_provider_lookup_is_case_insensitive(self) -> None:
        cap = resolve_capability("Anthropic", "claude-sonnet-4-6")
        assert cap.mode == "explicit"
