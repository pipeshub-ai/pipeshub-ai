"""Proves the `PromptCacheStrategy` injection seam on `AnthropicTransport`
end to end through `complete()`/`stream()`/`complete_structured()`,
rather than only at the strategy's own unit-test level
(`test_strategy_anthropic.py`).

Phase 1 proved this seam with a strategy that was BYTE-IDENTICAL to
the transport's legacy unconditional default. Phase 3 intentionally
hardened the strategy (allocator budget, cumulative floor, two
advancing message breakpoints, gateway stand-down) — so these tests no
longer assert equivalence with the legacy default; they assert the
hardened strategy's own documented behavior, reached through the real
transport plumbing.

`app.llm.prompt_cache` is a PipesHub-level package, so this test
necessarily imports across the seam — that is exactly the composition
production wiring does; this file is what proves it is safe to do.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent_loop_lib.core.messages import ToolMessage, UserMessage
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.anthropic import AnthropicTransport
from app.llm.prompt_cache.capabilities import CacheCapability
from app.llm.prompt_cache.factory import resolve_strategy
from app.llm.prompt_cache.strategy.anthropic import AnthropicCacheStrategy


def _permissive_capability() -> CacheCapability:
    """`min_prefix_tokens=0` so every non-empty block clears the
    floor — isolates these tests to "does the seam route the payload
    through the strategy and back correctly" rather than re-testing
    the floor math (`test_strategy_anthropic.py` owns that)."""
    return CacheCapability(
        mode="explicit", min_prefix_tokens=0, max_breakpoints=4,
        default_ttl="5m", extended_ttl="1h", write_multiplier=1.25,
        read_multiplier=0.10, can_cache_tools=True, can_cache_system=True,
    )


def _response(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        ),
    )


def _messages() -> list:
    big_result = "x" * 400
    return [
        UserMessage(content="turn 1"),
        ToolMessage(content=big_result, tool_call_id="tc1"),
        UserMessage(content="turn 2"),
        UserMessage(content="turn 3"),
    ]


def _tools() -> list[ToolSchema]:
    return [ToolSchema(name="search", description="d", input_schema={"type": "object"})]


class TestCompleteRoutesThroughInjectedStrategy:
    async def test_marks_tool_system_and_the_one_eligible_message_breakpoint(self) -> None:
        transport = AnthropicTransport(
            api_key="sk-test", cache_strategy=AnthropicCacheStrategy(_permissive_capability())
        )
        transport._client.messages.create = AsyncMock(return_value=_response())
        await transport.complete(_messages(), tools=_tools(), system="be terse")
        _, kwargs = transport._client.messages.create.call_args

        assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        # _messages() candidates (excluding the final two) are a plain
        # string "turn 1" message (no block to attach cache_control to,
        # so ineligible) and the tool_result message — only the latter
        # is an eligible breakpoint.
        assert "cache_control" not in kwargs["messages"][0]
        assert kwargs["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    async def test_no_strategy_falls_back_to_legacy_unconditional_default(self) -> None:
        transport = AnthropicTransport(api_key="sk-test")
        transport._client.messages.create = AsyncMock(return_value=_response())
        await transport.complete(_messages(), tools=_tools(), system="be terse")
        _, kwargs = transport._client.messages.create.call_args

        assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}

    async def test_system_blocks_split_marks_first_stable_block(self) -> None:
        transport = AnthropicTransport(
            api_key="sk-test", cache_strategy=AnthropicCacheStrategy(_permissive_capability())
        )
        transport._client.messages.create = AsyncMock(return_value=_response())
        await transport.complete([UserMessage(content="hi")], system_blocks=["stable", "volatile"])
        _, kwargs = transport._client.messages.create.call_args

        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in kwargs["system"][1]

    def test_gateway_standdown_leaves_tools_and_system_unmarked(self) -> None:
        """A pre-existing `cache_control` marker on a tool schema
        (simulating a LiteLLM gateway that already annotated the
        request before it reached this transport) makes the strategy
        stand down entirely — via the same `_apply_cache_strategy`
        integration point `complete()`/`stream()` call internally, not
        a reimplementation of it."""
        transport = AnthropicTransport(
            api_key="sk-test", cache_strategy=AnthropicCacheStrategy(_permissive_capability())
        )
        pre_marked_tools = [{"name": "search", "cache_control": {"type": "ephemeral"}}]

        result = transport._apply_cache_strategy(
            formatted_messages=[{"role": "user", "content": "hi"}],
            system_blocks_formatted=[{"type": "text", "text": "stable"}],
            formatted_tools=pre_marked_tools,
        )

        assert result.system[0].get("cache_control") is None
        assert result.tools == pre_marked_tools


class TestCompleteStructuredNoLongerCachesByDefault:
    """Phase 2 bug fix: `complete_structured()` used to unconditionally
    add `cache_control` to the system block, paying a 1.25x write
    premium on every one-shot structured call regardless of whether
    the prefix would ever be re-read. Default behavior now caches
    nothing; injecting a strategy is required to opt back in."""

    async def test_default_no_strategy_system_is_not_cached(self) -> None:
        transport = AnthropicTransport(api_key="sk-test")
        response = SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="1", name="structured_output", input={"a": 1})],
            stop_reason="tool_use",
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        )
        transport._client.messages.create = AsyncMock(return_value=response)

        await transport.complete_structured(
            [UserMessage(content="classify")], output_schema={"type": "object"}, system="be terse",
        )

        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["system"] == [{"type": "text", "text": "be terse"}]
        assert "cache_control" not in kwargs["system"][0]

    async def test_default_no_strategy_messages_are_not_cached(self) -> None:
        transport = AnthropicTransport(api_key="sk-test")
        big_result = "x" * 400
        response = SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="1", name="structured_output", input={})],
            stop_reason="tool_use",
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        )
        transport._client.messages.create = AsyncMock(return_value=response)

        await transport.complete_structured(
            [
                UserMessage(content="turn 1"),
                ToolMessage(content=big_result, tool_call_id="tc1"),
                UserMessage(content="turn 2"),
            ],
            output_schema={},
        )

        _, kwargs = transport._client.messages.create.call_args
        for msg in kwargs["messages"]:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    assert "cache_control" not in block

    async def test_injected_strategy_can_opt_back_into_caching(self) -> None:
        transport = AnthropicTransport(
            api_key="sk-test", cache_strategy=AnthropicCacheStrategy(_permissive_capability())
        )
        response = SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="1", name="structured_output", input={})],
            stop_reason="tool_use",
            usage=SimpleNamespace(
                input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0,
            ),
        )
        transport._client.messages.create = AsyncMock(return_value=response)

        await transport.complete_structured(
            [UserMessage(content="classify")], output_schema={}, system="be terse",
        )

        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


class TestStreamMatchesCompletePath:
    async def test_stream_and_complete_agree_on_tool_and_system_breakpoints(self) -> None:
        """Parity between `complete()` and `stream()` is one of the
        plan's explicit test requirements for the Anthropic strategy."""
        from tests.unit.agent_loop_lib.transport.test_anthropic_coverage import _FakeMessageStream

        captured_kwargs: dict = {}

        def _capture_stream(**kwargs: object) -> "_FakeMessageStream":
            captured_kwargs.update(kwargs)
            return _FakeMessageStream(text_chunks=[], final_message=_response())

        transport = AnthropicTransport(
            api_key="sk-test", cache_strategy=AnthropicCacheStrategy(_permissive_capability())
        )
        transport._client.messages.stream = _capture_stream
        async for _ in transport.stream(_messages(), tools=_tools(), system="be terse"):
            pass

        assert captured_kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert captured_kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert captured_kwargs["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}


class TestRealResolvedCapabilityEndToEnd:
    """One test using `resolve_strategy` (the actual production
    factory) against a real Claude model name, with content sized to
    clear Sonnet/Opus's real 1,024-token floor — proves the wiring
    that Phase 8 call sites will actually use, not just a permissive
    test fixture."""

    async def test_large_tool_result_is_cached_under_real_sonnet_capability(self) -> None:
        strategy = resolve_strategy("anthropic", "claude-sonnet-4-6")
        transport = AnthropicTransport(api_key="sk-test", cache_strategy=strategy)
        transport._client.messages.create = AsyncMock(return_value=_response())

        large_tool_result = "x" * 5000  # well above the 1,024-token (~4,096-char) floor
        await transport.complete(
            [
                UserMessage(content="turn 1"),
                ToolMessage(content=large_tool_result, tool_call_id="tc1"),
                UserMessage(content="turn 2"),
                UserMessage(content="turn 3"),
            ],
        )
        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    async def test_short_content_under_real_sonnet_capability_is_not_cached(self) -> None:
        strategy = resolve_strategy("anthropic", "claude-sonnet-4-6")
        transport = AnthropicTransport(api_key="sk-test", cache_strategy=strategy)
        transport._client.messages.create = AsyncMock(return_value=_response())

        await transport.complete(
            [
                UserMessage(content="turn 1"),
                ToolMessage(content="short result", tool_call_id="tc1"),
                UserMessage(content="turn 2"),
                UserMessage(content="turn 3"),
            ],
        )
        _, kwargs = transport._client.messages.create.call_args
        assert "cache_control" not in kwargs["messages"][1]["content"][0]
