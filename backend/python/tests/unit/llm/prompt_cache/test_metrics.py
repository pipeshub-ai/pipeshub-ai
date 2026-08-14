"""Phase 0: `app.llm.prompt_cache.metrics` is pure observation — these tests
assert it never raises, never mutates its input, and normalizes
`input_tokens` to exclude cached tokens (unlike the legacy converter this
package intentionally does not reuse — see `metrics.py`'s module docstring)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.llm.prompt_cache.capabilities import resolve_capability
from app.llm.prompt_cache.decision import CacheDecision, CacheReuseClass
from app.llm.prompt_cache.metrics import (
    CacheUsageSample,
    detect_langchain_provider,
    log_cache_usage,
    model_name_of,
    usage_from_ai_message,
)


class _FakeUnknown:
    pass


def _ai_message(usage_metadata=None, **attrs):
    return SimpleNamespace(usage_metadata=usage_metadata, **attrs)


class TestDetectLangchainProvider:
    def test_unmapped_class_falls_back_to_lowercased_name(self) -> None:
        llm = _FakeUnknown()
        assert detect_langchain_provider(llm) == "_fakeunknown"

    def test_mapped_provider_class(self) -> None:
        class ChatAnthropic:
            pass

        assert detect_langchain_provider(ChatAnthropic()) == "anthropic"

    def test_mapped_openai_class(self) -> None:
        class ChatOpenAI:
            pass

        assert detect_langchain_provider(ChatOpenAI()) == "openai"


class TestModelNameOf:
    def test_prefers_model_attr(self) -> None:
        llm = SimpleNamespace(model="claude-x", model_name="ignored")
        assert model_name_of(llm) == "claude-x"

    def test_falls_back_to_model_name(self) -> None:
        llm = SimpleNamespace(model_name="gpt-x")
        assert model_name_of(llm) == "gpt-x"

    def test_returns_empty_string_when_nothing_found(self) -> None:
        assert model_name_of(SimpleNamespace()) == ""


class TestUsageFromAiMessage:
    def test_returns_none_without_usage_metadata(self) -> None:
        assert usage_from_ai_message(
            _ai_message(usage_metadata=None), provider="anthropic", model="m", call_site="x"
        ) is None

    def test_returns_none_for_plain_object_with_no_attribute(self) -> None:
        assert usage_from_ai_message(
            object(), provider="anthropic", model="m", call_site="x"
        ) is None

    def test_normalizes_input_tokens_to_exclude_cache_read_and_write(self) -> None:
        # LangChain's `usage_metadata.input_tokens` sums ALL input types
        # (`langchain_anthropic._create_usage_metadata` adds cache_read
        # AND cache_creation onto Anthropic's native input_tokens) — both
        # must be subtracted so the sample's `input_tokens` means
        # "uncached" everywhere.
        usage = {
            "input_tokens": 1000,
            "output_tokens": 50,
            "input_token_details": {"cache_read": 400, "cache_creation": 100},
        }
        sample = usage_from_ai_message(
            _ai_message(usage_metadata=usage), provider="anthropic", model="claude", call_site="agent_loop"
        )
        assert sample == CacheUsageSample(
            provider="anthropic",
            model="claude",
            call_site="agent_loop",
            input_tokens=500,
            output_tokens=50,
            cache_read_tokens=400,
            cache_write_tokens=100,
        )

    def test_missing_input_token_details_defaults_to_zero_cache(self) -> None:
        usage = {"input_tokens": 200, "output_tokens": 10}
        sample = usage_from_ai_message(
            _ai_message(usage_metadata=usage), provider="openai", model="gpt", call_site="query_rewrite"
        )
        assert sample.cache_read_tokens == 0
        assert sample.cache_write_tokens == 0
        assert sample.input_tokens == 200

    def test_attaches_decision_fields_when_decision_given(self) -> None:
        decision = CacheDecision(
            enabled=True, reason="multi_turn_default_on", reuse_class=CacheReuseClass.MULTI_TURN,
            capability=resolve_capability("anthropic", "claude"), ttl="5m",
        )
        usage = {"input_tokens": 200, "output_tokens": 10}
        sample = usage_from_ai_message(
            _ai_message(usage_metadata=usage), provider="anthropic", model="claude",
            call_site="agent_loop", decision=decision,
        )
        assert sample.decision_enabled is True
        assert sample.decision_reason == "multi_turn_default_on"

    def test_decision_fields_default_to_none_without_a_decision(self) -> None:
        usage = {"input_tokens": 200, "output_tokens": 10}
        sample = usage_from_ai_message(
            _ai_message(usage_metadata=usage), provider="anthropic", model="claude", call_site="x"
        )
        assert sample.decision_enabled is None
        assert sample.decision_reason is None

    def test_never_produces_negative_input_tokens(self) -> None:
        # Defensive: a provider reporting cache_read > input_tokens should
        # clamp to zero rather than log a nonsensical negative count.
        usage = {
            "input_tokens": 50,
            "output_tokens": 5,
            "input_token_details": {"cache_read": 500},
        }
        sample = usage_from_ai_message(
            _ai_message(usage_metadata=usage), provider="anthropic", model="claude", call_site="x"
        )
        assert sample.input_tokens == 0


class TestLogCacheUsage:
    def test_none_sample_is_a_silent_no_op(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="app.llm.prompt_cache.metrics"):
            log_cache_usage(None)
        assert caplog.records == []

    def test_logs_one_info_line_with_hit_rate(self, caplog: pytest.LogCaptureFixture) -> None:
        sample = CacheUsageSample(
            provider="anthropic", model="claude", call_site="agent_loop",
            input_tokens=100, output_tokens=20, cache_read_tokens=300, cache_write_tokens=0,
        )
        with caplog.at_level(logging.INFO, logger="app.llm.prompt_cache.metrics"):
            log_cache_usage(sample)
        assert len(caplog.records) == 1
        message = caplog.records[0].getMessage()
        assert "provider=anthropic" in message
        assert "call_site=agent_loop" in message
        assert "hit_rate=0.750" in message

    def test_hit_rate_denominator_includes_cache_writes(self, caplog: pytest.LogCaptureFixture) -> None:
        sample = CacheUsageSample(
            provider="anthropic", model="claude", call_site="agent_loop",
            input_tokens=100, output_tokens=20, cache_read_tokens=300, cache_write_tokens=100,
        )
        with caplog.at_level(logging.INFO, logger="app.llm.prompt_cache.metrics"):
            log_cache_usage(sample)
        # 300 / (100 + 300 + 100) = 0.600 — writes are prompt tokens too.
        assert "hit_rate=0.600" in caplog.records[0].getMessage()

    def test_zero_total_input_does_not_divide_by_zero(self, caplog: pytest.LogCaptureFixture) -> None:
        sample = CacheUsageSample(
            provider="anthropic", model="claude", call_site="x",
            input_tokens=0, output_tokens=5, cache_read_tokens=0, cache_write_tokens=0,
        )
        with caplog.at_level(logging.INFO, logger="app.llm.prompt_cache.metrics"):
            log_cache_usage(sample)
        assert "hit_rate=0.000" in caplog.records[0].getMessage()

    def test_logs_net_savings_tokens_for_a_known_provider(self, caplog: pytest.LogCaptureFixture) -> None:
        sample = CacheUsageSample(
            provider="anthropic", model="claude-sonnet-5", call_site="agent_loop",
            input_tokens=100, output_tokens=20, cache_read_tokens=1000, cache_write_tokens=0,
        )
        with caplog.at_level(logging.INFO, logger="app.llm.prompt_cache.metrics"):
            log_cache_usage(sample)
        message = caplog.records[0].getMessage()
        capability = resolve_capability("anthropic", "claude-sonnet-5")
        expected = 1000 * (1 - capability.read_multiplier)
        assert f"net_savings_tokens={expected:.1f}" in message

    def test_net_savings_tokens_is_na_for_an_unresolvable_provider(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.llm.prompt_cache.metrics as metrics_module

        def _broken_resolve_capability(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "app.llm.prompt_cache.capabilities.resolve_capability", _broken_resolve_capability
        )
        sample = CacheUsageSample(
            provider="anthropic", model="claude-sonnet-5", call_site="x",
            input_tokens=100, output_tokens=20, cache_read_tokens=1000, cache_write_tokens=0,
        )
        with caplog.at_level(logging.INFO, logger="app.llm.prompt_cache.metrics"):
            log_cache_usage(sample)
        assert "net_savings_tokens=n/a" in caplog.records[0].getMessage()

    def test_decision_fields_absent_by_default(self, caplog: pytest.LogCaptureFixture) -> None:
        sample = CacheUsageSample(
            provider="anthropic", model="claude", call_site="x",
            input_tokens=10, output_tokens=1, cache_read_tokens=0, cache_write_tokens=0,
        )
        with caplog.at_level(logging.INFO, logger="app.llm.prompt_cache.metrics"):
            log_cache_usage(sample)
        assert "decision_enabled" not in caplog.records[0].getMessage()

    def test_decision_fields_present_when_attached(self, caplog: pytest.LogCaptureFixture) -> None:
        sample = CacheUsageSample(
            provider="anthropic", model="claude", call_site="x",
            input_tokens=10, output_tokens=1, cache_read_tokens=0, cache_write_tokens=0,
            decision_enabled=True, decision_reason="multi_turn_default_on",
        )
        with caplog.at_level(logging.INFO, logger="app.llm.prompt_cache.metrics"):
            log_cache_usage(sample)
        message = caplog.records[0].getMessage()
        assert "decision_enabled=True" in message
        assert "decision_reason=multi_turn_default_on" in message

    def test_never_raises_even_with_a_broken_sample(self) -> None:
        class _BadSample:
            provider = "p"
            model = "m"
            call_site = "c"
            output_tokens = 1
            cache_write_tokens = 0

            @property
            def input_tokens(self):
                raise RuntimeError("boom")

            @property
            def cache_read_tokens(self):
                raise RuntimeError("boom")

        log_cache_usage(_BadSample())  # must not raise
