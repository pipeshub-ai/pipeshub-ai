"""Phase 8: verifies `_ainvoke_throttled` resolves and forwards prompt-cache
invoke kwargs, and that `invoke_with_structured_output_and_reflection`
threads `reuse_class`/`cache_key`/`shared_static_enabled` through to it
without changing any existing (`ONE_SHOT_UNIQUE`-default) caller's
behavior."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from app.llm.prompt_cache.config import CacheConfig
from app.llm.prompt_cache.decision import CacheReuseClass
from app.utils.streaming import (
    _ainvoke_throttled,
    invoke_with_structured_output_and_reflection,
)


class _TinySchema(BaseModel):
    x: int


class _FakeAnthropicChatModel:
    """Named so `detect_langchain_provider` resolves it to `"anthropic"`
    without needing to import/construct a real `ChatAnthropic`."""


class TestAinvokeThrottledCacheKwargs:
    async def test_default_reuse_class_sends_no_cache_kwargs(self) -> None:
        """Every existing caller relies on `ONE_SHOT_UNIQUE` staying a
        true no-op: `ainvoke` must be called with no extra kwargs."""
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))

        await _ainvoke_throttled(llm, [], provider="anthropic", model="claude-sonnet-4-6")

        llm.ainvoke.assert_awaited_once_with([])

    async def test_multi_turn_anthropic_gets_cache_control_kwarg(self) -> None:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))

        with patch(
            "app.utils.streaming.resolve_cache_config",
            return_value=CacheConfig(enabled=True, source="env"),
        ):
            await _ainvoke_throttled(
                llm, [], provider="anthropic", model="claude-sonnet-4-6",
                reuse_class=CacheReuseClass.MULTI_TURN,
            )

        llm.ainvoke.assert_awaited_once_with([], cache_control={"type": "ephemeral"})

    async def test_shared_static_stays_disabled_without_explicit_opt_in(self) -> None:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))

        with patch(
            "app.utils.streaming.resolve_cache_config",
            return_value=CacheConfig(enabled=True, source="env"),
        ):
            await _ainvoke_throttled(
                llm, [], provider="anthropic", model="claude-sonnet-4-6",
                reuse_class=CacheReuseClass.SHARED_STATIC, cache_key="org-1",
            )

        llm.ainvoke.assert_awaited_once_with([])

    async def test_shared_static_opt_in_sends_kwargs_for_openai(self) -> None:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))
        # `resolve_cache_provider` downgrades "openai" to "unknown" unless
        # the model's base URL actually points at api.openai.com — a bare
        # MagicMock attribute is truthy and would trip that downgrade.
        llm.openai_api_base = "https://api.openai.com/v1"
        llm.base_url = "https://api.openai.com/v1"

        with patch(
            "app.utils.streaming.resolve_cache_config",
            return_value=CacheConfig(enabled=True, source="env"),
        ):
            await _ainvoke_throttled(
                llm, [], provider="openai", model="gpt-4o",
                reuse_class=CacheReuseClass.SHARED_STATIC, cache_key="org-1",
                shared_static_enabled=True,
            )

        llm.ainvoke.assert_awaited_once_with([], prompt_cache_key="org-1")

    async def test_global_kill_switch_disables_kwargs_even_for_multi_turn(self) -> None:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))

        with patch(
            "app.utils.streaming.resolve_cache_config",
            return_value=CacheConfig(enabled=False, source="env"),
        ):
            await _ainvoke_throttled(
                llm, [], provider="anthropic", model="claude-sonnet-4-6",
                reuse_class=CacheReuseClass.MULTI_TURN,
            )

        llm.ainvoke.assert_awaited_once_with([])


class TestAinvokeThrottledRecordsResolvedCacheIdentity:
    async def test_logs_downgraded_provider_and_detected_model(self) -> None:
        llm = MagicMock()
        llm.ainvoke = AsyncMock(
            return_value=AIMessage(
                content="ok",
                usage_metadata={
                    "input_tokens": 10, "output_tokens": 1, "total_tokens": 11,
                },
            )
        )
        llm.openai_api_base = "https://openrouter.ai/api/v1"
        llm.base_url = "https://openrouter.ai/api/v1"
        llm.model = "gpt-4o"
        llm.model_name = "gpt-4o"

        with patch("app.utils.streaming.log_cache_usage") as mock_log, patch(
            "app.utils.streaming.resolve_cache_config",
            return_value=CacheConfig(enabled=True, source="env"),
        ):
            await _ainvoke_throttled(
                llm, [], provider="openai", model="",
                reuse_class=CacheReuseClass.MULTI_TURN,
            )

        sample = mock_log.call_args.args[0]
        assert sample.provider == "unknown"
        assert sample.model == "gpt-4o"


class TestReuseClassThreadedFromStructuredOutputReflection:
    async def test_reuse_class_and_cache_key_reach_ainvoke_throttled(self) -> None:
        mock_throttled = AsyncMock(return_value=AIMessage(content='{"x": 1}'))

        with patch("app.utils.streaming._ainvoke_throttled", mock_throttled):
            out = await invoke_with_structured_output_and_reflection(
                MagicMock(),
                [],
                _TinySchema,
                max_retries=0,
                call_site="document_metadata_extraction",
                reuse_class=CacheReuseClass.SHARED_STATIC,
                cache_key="org-1",
                shared_static_enabled=True,
            )

        assert out is not None and out.x == 1
        mock_throttled.assert_awaited_once()
        kwargs = mock_throttled.await_args.kwargs
        assert kwargs["reuse_class"] is CacheReuseClass.SHARED_STATIC
        assert kwargs["cache_key"] == "org-1"
        assert kwargs["shared_static_enabled"] is True
        assert kwargs["call_site"] == "document_metadata_extraction"

    async def test_omitting_reuse_class_keeps_legacy_no_kwargs_behavior(self) -> None:
        """Existing callers that never pass `reuse_class` must see byte
        identical `ainvoke` calls to before Phase 8."""
        wrapped = MagicMock()
        wrapped.ainvoke = AsyncMock(return_value=AIMessage(content='{"x": 2}'))

        with patch(
            "app.utils.streaming._apply_structured_output", return_value=wrapped
        ):
            out = await invoke_with_structured_output_and_reflection(
                MagicMock(), [], _TinySchema, max_retries=0,
            )

        assert out is not None and out.x == 2
        wrapped.ainvoke.assert_awaited_once_with([])
