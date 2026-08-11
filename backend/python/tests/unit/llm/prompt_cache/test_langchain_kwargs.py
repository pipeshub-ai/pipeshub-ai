from __future__ import annotations

from types import SimpleNamespace

from app.llm.prompt_cache.config import CacheConfig
from app.llm.prompt_cache.decision import CacheReuseClass
from app.llm.prompt_cache.langchain_kwargs import (
    resolve_cache_provider,
    resolve_langchain_cache_kwargs,
)

_ENABLED = CacheConfig(enabled=True, source="env")
_DISABLED = CacheConfig(enabled=False, source="env")


class TestSharedStaticEnabledOverride:
    """Phase 8: `SHARED_STATIC` is off by default even with the global kill
    switch enabled — a caller must explicitly opt in per call site via
    `shared_static_enabled=True`."""

    def test_shared_static_defaults_to_off(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-sonnet-4-6",
            reuse_class=CacheReuseClass.SHARED_STATIC, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is False
        assert decision.reason == "shared_static_off_by_default"

    def test_shared_static_enabled_true_gets_cache_kwargs_on_anthropic(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-sonnet-4-6",
            reuse_class=CacheReuseClass.SHARED_STATIC, cache_config=_ENABLED,
            shared_static_enabled=True,
        )
        assert kwargs == {"cache_control": {"type": "ephemeral"}}
        assert decision.enabled is True

    def test_shared_static_enabled_true_gets_prompt_cache_key_on_openai(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="openai", model="gpt-4o",
            reuse_class=CacheReuseClass.SHARED_STATIC, cache_config=_ENABLED,
            shared_static_enabled=True, cache_key="org-1",
        )
        assert kwargs == {"prompt_cache_key": "org-1"}
        assert decision.enabled is True

    def test_shared_static_enabled_true_has_no_effect_on_multi_turn(self) -> None:
        """The override is scoped to SHARED_STATIC only — it must not
        change MULTI_TURN's already-on-by-default behavior."""
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-sonnet-4-6",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
            shared_static_enabled=True,
        )
        assert kwargs == {"cache_control": {"type": "ephemeral"}}
        assert decision.enabled is True


class TestAnthropic:
    def test_multi_turn_gets_cache_control_invoke_kwarg(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-sonnet-4-6",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {"cache_control": {"type": "ephemeral"}}
        assert decision.enabled is True

    def test_one_shot_unique_gets_no_kwargs(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-sonnet-4-6",
            reuse_class=CacheReuseClass.ONE_SHOT_UNIQUE, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is False

    def test_kill_switch_disables_kwargs(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-sonnet-4-6",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_DISABLED,
        )
        assert kwargs == {}
        assert decision.enabled is False

    def test_unrecognized_legacy_model_gets_no_kwargs(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-2.1",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is False

    def test_returns_a_fresh_dict_each_call(self) -> None:
        """Callers may mutate the returned dict (e.g. to merge with
        other invoke kwargs) — a shared mutable default would leak
        mutations across calls."""
        kwargs_1, _ = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-sonnet-4-6",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        kwargs_1["extra"] = "mutated"
        kwargs_2, _ = resolve_langchain_cache_kwargs(
            provider="anthropic", model="claude-sonnet-4-6",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert "extra" not in kwargs_2


class TestOpenAI:
    def test_multi_turn_gets_prompt_cache_key_when_provided(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="openai", model="gpt-4o-mini",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
            cache_key="org-1:user-1",
        )
        assert kwargs == {"prompt_cache_key": "org-1:user-1"}
        assert decision.enabled is True

    def test_no_cache_key_means_no_kwargs_even_when_enabled(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="openai", model="gpt-4o-mini",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is True

    def test_never_sends_prompt_cache_options_or_breakpoints(self) -> None:
        """Explicit mode without restructured content would disable
        implicit caching outright — this path must never send
        `prompt_cache_options`."""
        kwargs, _ = resolve_langchain_cache_kwargs(
            provider="openai", model="gpt-5.6-terra",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
            cache_key="org-1:user-1",
        )
        assert "prompt_cache_options" not in kwargs
        assert "prompt_cache_breakpoint" not in kwargs

    def test_azure_openai_treated_the_same_as_openai(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="azure_openai", model="gpt-4o-mini",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
            cache_key="org-1:user-1",
        )
        assert kwargs == {"prompt_cache_key": "org-1:user-1"}
        assert decision.enabled is True

    def test_kill_switch_disables_even_with_cache_key(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="openai", model="gpt-4o-mini",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_DISABLED,
            cache_key="org-1:user-1",
        )
        assert kwargs == {}
        assert decision.enabled is False


class TestGoogle:
    """Phase 9: Google/Gemini's caching is fully implicit — no invoke kwarg
    exists at all (see `resolve_langchain_cache_kwargs`'s module docstring
    and `capabilities._gemini_capability`). The decision should still
    report `enabled=True` (the model IS eligible and Google may be
    discounting silently even when `cache_read` reports zero — see the
    plan's corner case), but `invoke_kwargs` must stay empty since there
    is nothing to send."""

    def test_multi_turn_gemini_3x_is_enabled_with_no_invoke_kwargs(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="google", model="gemini-3-flash",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is True

    def test_multi_turn_gemini_2x_is_enabled_with_no_invoke_kwargs(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="google", model="gemini-2.5-pro",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is True

    def test_unrecognized_gemini_model_disables(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="google", model="gemini-1.0-pro",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is False
        assert decision.reason == "capability_mode_none"

    def test_kill_switch_disables_gemini_too(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="google", model="gemini-3-flash",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_DISABLED,
        )
        assert kwargs == {}
        assert decision.enabled is False


class TestBedrock:
    """Phase 9 spike verdict (see `capabilities._BEDROCK_CAPABILITY`):
    PipesHub constructs `ChatBedrock` (the InvokeModel path), for which
    `cache_control` support is unconfirmed — resolves to `mode="none"`
    until validated, rather than guessing a request shape Bedrock's
    InvokeModel API might 400 on."""

    def test_bedrock_never_gets_cache_kwargs_regardless_of_model(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="bedrock", model="anthropic.claude-sonnet-4-6-v1:0",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is False
        assert decision.reason == "capability_mode_none"


class TestResolveCacheProvider:
    """`detect_langchain_provider` maps every `ChatOpenAI`-class instance
    to `"openai"` by class name alone — `resolve_cache_provider` is the
    guard that downgrades that label to `"unknown"` whenever the instance
    wasn't actually pointed at OpenAI's own API, so gateways that merely
    speak the same protocol (LM Studio, LiteLLM proxy, OpenRouter,
    MiniMax, a generic OpenAI-compatible entry) never get an OpenAI-only
    invoke kwarg like `prompt_cache_key` sent to a backend that may 400
    on it."""

    def test_non_openai_label_passes_through_unchanged(self) -> None:
        llm = SimpleNamespace(openai_api_base=None)
        assert resolve_cache_provider(llm, "anthropic") == "anthropic"

    def test_default_endpoint_stays_openai(self) -> None:
        llm = SimpleNamespace(openai_api_base=None)
        assert resolve_cache_provider(llm, "openai") == "openai"

    def test_explicit_api_openai_com_stays_openai(self) -> None:
        llm = SimpleNamespace(openai_api_base="https://api.openai.com/v1")
        assert resolve_cache_provider(llm, "openai") == "openai"

    def test_local_lm_studio_endpoint_is_downgraded(self) -> None:
        llm = SimpleNamespace(openai_api_base="http://localhost:1234/v1")
        assert resolve_cache_provider(llm, "openai") == "unknown"

    def test_litellm_proxy_endpoint_is_downgraded(self) -> None:
        llm = SimpleNamespace(openai_api_base="https://litellm.internal.example.com")
        assert resolve_cache_provider(llm, "openai") == "unknown"

    def test_falls_back_to_base_url_attribute_when_openai_api_base_absent(self) -> None:
        llm = SimpleNamespace(base_url="http://localhost:1234/v1")
        assert resolve_cache_provider(llm, "openai") == "unknown"

    def test_azure_openai_label_is_never_downgraded(self) -> None:
        """`AzureChatOpenAI` is a distinct LangChain class already
        labeled `azure_openai` — its endpoint is inherently a custom
        Azure resource URL, so it must not be run through the
        OpenAI-host check at all."""
        llm = SimpleNamespace(openai_api_base="https://my-resource.openai.azure.com")
        assert resolve_cache_provider(llm, "azure_openai") == "azure_openai"

    def test_downgraded_provider_resolves_to_no_cache_kwargs(self) -> None:
        llm = SimpleNamespace(openai_api_base="http://localhost:1234/v1")
        provider = resolve_cache_provider(llm, "openai")
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider=provider, model="some-local-model",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
            cache_key="org-1:user-1",
        )
        assert kwargs == {}
        assert decision.enabled is False


class TestAutomaticNoKwargProviders:
    def test_google_gets_no_kwargs_but_is_still_enabled(self) -> None:
        """Gemini's automatic caching needs no invoke kwarg at all —
        `decision.enabled=True` with `{}` is the CORRECT result, not a
        bug: ordering alone earns the cache hit."""
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="google", model="gemini-2.5-pro",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is True

    def test_unsupported_provider_gets_no_kwargs_and_is_disabled(self) -> None:
        kwargs, decision = resolve_langchain_cache_kwargs(
            provider="ollama", model="llama3",
            reuse_class=CacheReuseClass.MULTI_TURN, cache_config=_ENABLED,
        )
        assert kwargs == {}
        assert decision.enabled is False
