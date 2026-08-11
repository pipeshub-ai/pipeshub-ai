"""Proves the `PromptCacheStrategy` injection seam on `OpenAITransport`
end to end through `complete()`/`stream()`, mirroring
`test_anthropic_cache_strategy_seam.py`. `OpenAITransport` never
cached anything before this seam existed, so there is no legacy
default behavior to preserve here — `cache_strategy=None` is simply
"no caching kwargs, no restructuring", which these tests also pin.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.openai import OpenAITransport
from app.llm.prompt_cache.capabilities import CacheCapability
from app.llm.prompt_cache.factory import resolve_strategy
from app.llm.prompt_cache.strategy.openai import OpenAICacheStrategy

from tests.unit.agent_loop_lib.transport.test_openai_coverage import (
    _AsyncChunkIterator,
    _chat_response,
    _stream_chunk,
)


def _permissive_capability(mode: str = "explicit") -> CacheCapability:
    return CacheCapability(
        mode=mode,  # type: ignore[arg-type]
        min_prefix_tokens=0, max_breakpoints=4, default_ttl="30m", extended_ttl=None,
        write_multiplier=1.0, read_multiplier=0.5, can_cache_tools=False,
        can_cache_system=mode == "explicit",
    )


def _messages() -> list:
    return [
        UserMessage(content="x" * 50),
        UserMessage(content="x" * 50),
        UserMessage(content="turn n-1"),
        UserMessage(content="turn n"),
    ]


class TestNoStrategyIsInert:
    async def test_default_no_strategy_adds_no_cache_kwargs(self) -> None:
        transport = OpenAITransport(api_key="sk-test")
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())
        await transport.complete(_messages())
        _, kwargs = transport._client.chat.completions.create.call_args
        assert "prompt_cache_key" not in kwargs
        assert "prompt_cache_options" not in kwargs
        for msg in kwargs["messages"]:
            assert isinstance(msg["content"], str)


class TestExplicitStrategyViaComplete:
    async def test_marks_two_eligible_boundaries_and_adds_request_kwargs(self) -> None:
        strategy = OpenAICacheStrategy(_permissive_capability(), cache_key="org-1:user-1")
        transport = OpenAITransport(api_key="sk-test", cache_strategy=strategy)
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())

        await transport.complete(_messages())

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["prompt_cache_key"] == "org-1:user-1"
        assert kwargs["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
        # 4 messages -> candidates are indices 0 and 1 (both 50-char
        # user turns); both clear a zero floor and both fit the
        # 2-breakpoint budget.
        assert kwargs["messages"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert kwargs["messages"][1]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert isinstance(kwargs["messages"][2]["content"], str)
        assert isinstance(kwargs["messages"][3]["content"], str)

    async def test_tools_forwarded_unmarked_since_openai_cannot_cache_tools_independently(self) -> None:
        strategy = OpenAICacheStrategy(_permissive_capability())
        transport = OpenAITransport(api_key="sk-test", cache_strategy=strategy)
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]

        await transport.complete(_messages(), tools=tools)

        _, kwargs = transport._client.chat.completions.create.call_args
        assert "cache_control" not in kwargs["tools"][0]
        assert "prompt_cache_breakpoint" not in kwargs["tools"][0]


class TestAutomaticStrategyViaComplete:
    async def test_only_cache_key_added_no_restructuring(self) -> None:
        strategy = OpenAICacheStrategy(_permissive_capability(mode="automatic"), cache_key="org-1:user-1")
        transport = OpenAITransport(api_key="sk-test", cache_strategy=strategy)
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())

        await transport.complete(_messages())

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["prompt_cache_key"] == "org-1:user-1"
        assert "prompt_cache_options" not in kwargs
        for msg in kwargs["messages"]:
            assert isinstance(msg["content"], str)


class TestStreamMatchesCompletePath:
    async def test_stream_marks_same_boundaries_as_complete(self) -> None:
        strategy = OpenAICacheStrategy(_permissive_capability(), cache_key="org-1:user-1")
        transport = OpenAITransport(api_key="sk-test", cache_strategy=strategy)
        transport._client.chat.completions.create = AsyncMock(
            return_value=_AsyncChunkIterator([_stream_chunk(finish_reason="stop")])
        )

        async for _ in transport.stream(_messages()):
            pass

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["prompt_cache_key"] == "org-1:user-1"
        assert kwargs["messages"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}
        assert kwargs["messages"][1]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}


class TestGatewayStanddown:
    def test_pre_existing_marker_on_tools_stands_down(self) -> None:
        transport = OpenAITransport(api_key="sk-test", cache_strategy=OpenAICacheStrategy(_permissive_capability()))
        pre_marked_tools = [{"type": "function", "function": {"name": "a", "cache_control": {"type": "ephemeral"}}}]

        result = transport._apply_cache_strategy(
            formatted_messages=[{"role": "user", "content": "x" * 50}, {"role": "user", "content": "x" * 50}, {"role": "user", "content": "n"}],
            formatted_tools=pre_marked_tools,
        )

        assert result.tools == pre_marked_tools
        for msg in result.messages:
            assert isinstance(msg["content"], str)


class TestRealResolvedCapabilityEndToEnd:
    async def test_gpt_5_6_via_factory_marks_large_prefix(self) -> None:
        strategy = resolve_strategy("openai", "gpt-5.6-terra", cache_key="org-1:user-1")
        transport = OpenAITransport(api_key="sk-test", cache_strategy=strategy)
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())

        await transport.complete(
            [
                UserMessage(content="x" * 5000),  # well above the 1,024-token floor
                UserMessage(content="turn n-1"),
                UserMessage(content="turn n"),
            ]
        )

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["messages"][0]["content"][0]["prompt_cache_breakpoint"] == {"mode": "explicit"}

    async def test_gpt_4o_via_factory_only_adds_cache_key(self) -> None:
        strategy = resolve_strategy("openai", "gpt-4o-mini", cache_key="org-1:user-1")
        transport = OpenAITransport(api_key="sk-test", cache_strategy=strategy)
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())

        await transport.complete([UserMessage(content="x" * 5000)])

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["prompt_cache_key"] == "org-1:user-1"
        assert "prompt_cache_options" not in kwargs
        assert isinstance(kwargs["messages"][0]["content"], str)
