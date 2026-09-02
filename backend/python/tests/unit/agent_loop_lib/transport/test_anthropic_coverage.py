"""Coverage tests for `app/agent_loop_lib/transport/anthropic.py`.

All tests mock the `anthropic` SDK client entirely (`AsyncAnthropic` is
replaced with a stand-in whose `messages.create`/`messages.stream` are
mocked) — no network calls are made. Response objects are plain
`SimpleNamespace`s since `AnthropicTransport` only ever reads them via
attribute access, never via `isinstance`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anthropic as anthropic_sdk
import httpx
import pytest

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    SystemMessage,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.anthropic import AnthropicTransport


def _transport(**kwargs: Any) -> AnthropicTransport:
    return AnthropicTransport(api_key="sk-test", **kwargs)


def _block(type_: str, **fields: Any) -> SimpleNamespace:
    return SimpleNamespace(type=type_, **fields)


def _response(
    content: list[SimpleNamespace] | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read: int = 0,
    cache_write: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content if content is not None else [_block("text", text="hi")],
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


class _FakeMessageStream:
    """Stand-in for `anthropic.lib.streaming.AsyncMessageStreamManager`."""

    def __init__(self, text_chunks: list[str], final_message: SimpleNamespace, raise_on_iter: Exception | None = None) -> None:
        self._text_chunks = text_chunks
        self._final_message = final_message
        self._raise_on_iter = raise_on_iter

    async def __aenter__(self) -> "_FakeMessageStream":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        return None

    @property
    def text_stream(self) -> "_FakeMessageStream":
        return self

    def __aiter__(self) -> "_FakeMessageStream":
        self._iter_chunks = list(self._text_chunks)
        return self

    async def __anext__(self) -> str:
        if self._raise_on_iter is not None:
            raise self._raise_on_iter
        if not self._iter_chunks:
            raise StopAsyncIteration
        return self._iter_chunks.pop(0)

    async def get_final_message(self) -> SimpleNamespace:
        return self._final_message


class TestInit:
    def test_missing_sdk_raises_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "anthropic":
                raise ImportError("no anthropic")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        with pytest.raises(ImportError, match="agent-loop\\[anthropic\\]"):
            AnthropicTransport(api_key="sk-test")

    def test_default_model_provider_and_max_tokens(self) -> None:
        transport = _transport()
        assert transport.provider == "anthropic"
        assert transport.model_name == AnthropicTransport.DEFAULT_MODEL
        assert transport._max_tokens == AnthropicTransport.DEFAULT_MAX_TOKENS

    def test_cumulative_counters_start_at_zero(self) -> None:
        transport = _transport()
        assert transport.total_input_tokens == 0
        assert transport.total_cache_write_tokens == 0
        assert transport.total_llm_calls == 0


class TestFormatMessage:
    def test_tool_message_includes_step_footer_and_is_error(self) -> None:
        transport = _transport()
        msg = ToolMessage(content="oops", tool_call_id="tc1", is_error=True, step_footer=" [footer]")
        formatted = transport._format_message(msg)
        assert formatted == {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tc1", "content": "oops [footer]", "is_error": True}],
        }

    def test_tool_message_without_error_flag_omits_is_error(self) -> None:
        transport = _transport()
        msg = ToolMessage(content="ok", tool_call_id="tc1")
        formatted = transport._format_message(msg)
        assert "is_error" not in formatted["content"][0]

    def test_multipart_tool_message_formats_text_and_image_blocks(self) -> None:
        transport = _transport()
        msg = ToolMessage(
            content=[
                TextPart(text="[ref1] (image)"),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123")),
            ],
            tool_call_id="tc1",
        )
        formatted = transport._format_message(msg)
        tool_result = formatted["content"][0]
        assert tool_result["content"] == [
            {"type": "text", "text": "[ref1] (image)"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc123"}},
        ]

    def test_multipart_tool_message_appends_step_footer_as_text_block(self) -> None:
        transport = _transport()
        msg = ToolMessage(
            content=[TextPart(text="hello")],
            tool_call_id="tc1",
            step_footer=" [loop: step 1/5]",
        )
        formatted = transport._format_message(msg)
        blocks = formatted["content"][0]["content"]
        assert blocks[-1] == {"type": "text", "text": " [loop: step 1/5]"}

    def test_assistant_message_with_text_and_tool_use(self) -> None:
        transport = _transport()
        msg = AssistantMessage(
            content=[TextPart(text="let me check")],
            tool_calls=[ToolCall(id="1", name="search", arguments={"q": "x"})],
        )
        formatted = transport._format_message(msg)
        assert formatted["role"] == "assistant"
        assert formatted["content"][0] == {"type": "text", "text": "let me check"}
        assert formatted["content"][1] == {"type": "tool_use", "id": "1", "name": "search", "input": {"q": "x"}}

    def test_assistant_message_with_neither_text_nor_tools_gets_empty_text_block(self) -> None:
        transport = _transport()
        msg = AssistantMessage(content=[])
        formatted = transport._format_message(msg)
        assert formatted["content"] == [{"type": "text", "text": ""}]

    def test_user_message_with_list_content_formats_each_part(self) -> None:
        transport = _transport()
        msg = UserMessage(content=[TextPart(text="look at this")])
        formatted = transport._format_message(msg)
        assert formatted == {"role": "user", "content": [{"type": "text", "text": "look at this"}]}

    def test_system_message_plain_string_branch(self) -> None:
        transport = _transport()
        msg = SystemMessage(content="be nice")
        formatted = transport._format_message(msg)
        assert formatted == {"role": "system", "content": "be nice"}

    def test_empty_string_user_content_defaults(self) -> None:
        transport = _transport()
        msg = UserMessage(content="")
        formatted = transport._format_message(msg)
        assert formatted == {"role": "user", "content": ""}


class TestFormatPart:
    def test_text_part(self) -> None:
        result = AnthropicTransport._format_part(TextPart(text="hi"))
        assert result == {"type": "text", "text": "hi"}

    def test_image_part(self) -> None:
        part = ImagePart(source=ImageSource(type="base64", media_type="image/png", data="abc123"))
        result = AnthropicTransport._format_part(part)
        assert result == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "abc123"},
        }

    def test_thinking_part_falls_back_to_text_of_thinking_field(self) -> None:
        part = ThinkingPart(thinking="internal reasoning")
        result = AnthropicTransport._format_part(part)
        assert result == {"type": "text", "text": "internal reasoning"}


class TestParseResponse:
    def test_extracts_text_and_tool_use(self) -> None:
        transport = _transport()
        response = _response(
            content=[
                _block("text", text="here you go"),
                _block("tool_use", id="1", name="search", input={"q": "x"}),
            ]
        )
        msg = transport._parse_response(response)
        assert msg.text == "here you go"
        assert msg.tool_calls == [ToolCall(id="1", name="search", arguments={"q": "x"})]
        assert msg.truncated is False

    def test_thinking_blocks_ignored(self) -> None:
        transport = _transport()
        response = _response(content=[_block("thinking", thinking="hmm"), _block("text", text="answer")])
        msg = transport._parse_response(response)
        assert msg.text == "answer"

    def test_max_tokens_stop_reason_sets_truncated(self) -> None:
        transport = _transport()
        response = _response(content=[_block("text", text="cut off mid")], stop_reason="max_tokens")
        msg = transport._parse_response(response)
        assert msg.truncated is True

    def test_no_content_blocks_yields_no_text_no_tools(self) -> None:
        transport = _transport()
        response = _response(content=[])
        msg = transport._parse_response(response)
        assert msg.text == ""
        assert msg.tool_calls is None


class TestUsageFrom:
    def test_full_usage_extracted_and_accumulated(self) -> None:
        transport = _transport()
        response = _response(input_tokens=100, output_tokens=50, cache_read=10, cache_write=5)
        usage = transport._usage_from(response)
        assert usage.input_tokens == 100
        assert usage.cache_read_tokens == 10
        assert usage.cache_write_tokens == 5
        assert transport.total_cache_write_tokens == 5
        assert transport.total_llm_calls == 1

    def test_missing_usage_defaults_to_zero(self) -> None:
        transport = _transport()
        usage = transport._usage_from(SimpleNamespace(usage=None))
        assert usage.input_tokens == 0
        assert usage.cache_write_tokens == 0

    def test_non_int_field_coerced_to_zero(self) -> None:
        transport = _transport()
        response = SimpleNamespace(usage=SimpleNamespace(input_tokens="oops", output_tokens=None))
        usage = transport._usage_from(response)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


class TestWrapError:
    def test_connection_error_is_retryable(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        exc = anthropic_sdk.APIConnectionError(request=req)
        wrapped = transport._wrap_error(exc, "complete")
        assert wrapped.retryable is True
        assert "Anthropic API error (complete)" in str(wrapped)

    def test_status_500_is_retryable(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=500, request=req)
        exc = anthropic_sdk.APIStatusError("server error", response=resp, body=None)
        wrapped = transport._wrap_error(exc, "stream")
        assert wrapped.status_code == 500
        assert wrapped.retryable is True

    def test_status_400_is_not_retryable(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx.Response(status_code=400, request=req)
        exc = anthropic_sdk.APIStatusError("bad request", response=resp, body=None)
        wrapped = transport._wrap_error(exc, "complete")
        assert wrapped.retryable is False

    def test_generic_exception_not_retryable(self) -> None:
        transport = _transport()
        wrapped = transport._wrap_error(ValueError("boom"), "complete")
        assert wrapped.status_code is None
        assert wrapped.retryable is False


class TestApplyPromptCache:
    def test_noop_when_two_or_fewer_messages(self) -> None:
        transport = _transport()
        formatted = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}]
        transport._apply_prompt_cache(formatted)
        assert "cache_control" not in formatted[0]

    def test_marks_last_large_tool_result_before_final_two_messages(self) -> None:
        transport = _transport()
        big_result = "x" * 400
        formatted = [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": [{"type": "tool_result", "content": big_result}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            {"role": "user", "content": "next"},
        ]
        transport._apply_prompt_cache(formatted)
        assert formatted[1]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in formatted[2]["content"][0]

    def test_small_content_is_not_marked(self) -> None:
        transport = _transport()
        formatted = [
            {"role": "user", "content": [{"type": "tool_result", "content": "small"}]},
            {"role": "user", "content": "x"},
            {"role": "user", "content": "y"},
        ]
        transport._apply_prompt_cache(formatted)
        assert "cache_control" not in formatted[0]["content"][0]

    def test_already_cached_block_is_skipped(self) -> None:
        transport = _transport()
        big = "x" * 400
        formatted = [
            {"role": "user", "content": [{"type": "text", "text": big, "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": [{"type": "tool_result", "content": big}]},
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ]
        transport._apply_prompt_cache(formatted)
        assert "cache_control" in formatted[1]["content"][0]

    def test_non_list_content_is_skipped(self) -> None:
        transport = _transport()
        formatted = [
            {"role": "user", "content": "plain string"},
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ]
        transport._apply_prompt_cache(formatted)  # must not raise


class TestApplyToolCache:
    def test_empty_list_returned_as_is(self) -> None:
        transport = _transport()
        assert transport._apply_tool_cache([]) == []

    def test_marks_last_tool_without_mutating_original(self) -> None:
        transport = _transport()
        tools = [{"name": "a"}, {"name": "b"}]
        result = transport._apply_tool_cache(tools)
        assert result[-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in tools[-1]
        assert "cache_control" not in result[0]


class TestFormatTools:
    def test_none_when_empty(self) -> None:
        transport = _transport()
        assert transport._format_tools(None) is None
        assert transport._format_tools([]) is None

    def test_maps_tool_schema(self) -> None:
        transport = _transport()
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]
        result = transport._format_tools(tools)
        assert result == [{"name": "search", "description": "d", "input_schema": {"type": "object"}}]

    def test_empty_input_schema_becomes_a_well_formed_object(self) -> None:
        """The API rejects `{}`, and a zero-argument tool legitimately has one
        (an MCP server that omits `inputSchema`). Since every tool goes up in
        one call, forwarding `{}` verbatim failed the whole request — the
        OpenAI/Gemini/Ollama transports substitute the same schema."""
        transport = _transport()
        result = transport._format_tools(
            [ToolSchema(name="noargs", description="d", input_schema={})],
        )
        assert result[0]["input_schema"] == {"type": "object", "properties": {}}


class TestBuildSystemKwargs:
    def test_none_when_nothing_supplied(self) -> None:
        transport = _transport()
        assert transport._build_system_kwargs(None, None) is None

    def test_system_blocks_first_block_cached_second_not(self) -> None:
        transport = _transport()
        result = transport._build_system_kwargs(None, ["stable", "volatile"])
        assert result[0] == {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}}
        assert result[1] == {"type": "text", "text": "volatile"}

    def test_system_blocks_skips_empty_entries(self) -> None:
        transport = _transport()
        result = transport._build_system_kwargs(None, ["", "only"])
        assert len(result) == 1
        assert result[0]["text"] == "only"

    def test_system_blocks_all_empty_returns_none(self) -> None:
        transport = _transport()
        assert transport._build_system_kwargs(None, ["", ""]) is None

    def test_legacy_system_string_gets_single_cached_block(self) -> None:
        transport = _transport()
        result = transport._build_system_kwargs("legacy prompt", None)
        assert result == [{"type": "text", "text": "legacy prompt", "cache_control": {"type": "ephemeral"}}]


class TestComplete:
    async def test_maps_kwargs_and_response(self) -> None:
        transport = _transport(model="claude-x")
        response = _response(content=[_block("text", text="42")])
        transport._client.messages.create = AsyncMock(return_value=response)

        result = await transport.complete([UserMessage(content="what is the answer?")])

        transport._client.messages.create.assert_awaited_once()
        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["model"] == "claude-x"
        assert kwargs["max_tokens"] == AnthropicTransport.DEFAULT_MAX_TOKENS
        assert "system" not in kwargs
        assert result.message.text == "42"
        assert result.stop_reason == StopReason.END_TURN
        assert result.usage.input_tokens == 10

    async def test_thinking_budget_grows_max_tokens(self) -> None:
        transport = _transport(max_tokens=1000)
        transport._client.messages.create = AsyncMock(return_value=_response())

        await transport.complete([UserMessage(content="hi")], thinking_budget=5000)

        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 5000}
        assert kwargs["max_tokens"] == 5000 + 1024

    async def test_thinking_budget_does_not_shrink_larger_max_tokens(self) -> None:
        transport = _transport(max_tokens=50_000)
        transport._client.messages.create = AsyncMock(return_value=_response())

        await transport.complete([UserMessage(content="hi")], thinking_budget=100)

        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["max_tokens"] == 50_000

    async def test_disabled_thinking_still_sends_temperature(self) -> None:
        transport = _transport(thinking={"type": "disabled"}, temperature=0.4)
        transport._client.messages.create = AsyncMock(return_value=_response())

        await transport.complete([UserMessage(content="hi")])

        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["thinking"] == {"type": "disabled"}
        assert kwargs["temperature"] == 0.4

    async def test_enabled_thinking_suppresses_temperature(self) -> None:
        transport = _transport(
            thinking={"type": "enabled", "budget_tokens": 1024},
            temperature=0.4,
        )
        transport._client.messages.create = AsyncMock(return_value=_response())

        await transport.complete([UserMessage(content="hi")])

        _, kwargs = transport._client.messages.create.call_args
        assert "temperature" not in kwargs
        assert kwargs["thinking"]["type"] == "enabled"

    async def test_tools_get_cache_breakpoint(self) -> None:
        transport = _transport()
        transport._client.messages.create = AsyncMock(return_value=_response())
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]

        await transport.complete([UserMessage(content="hi")], tools=tools)

        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}

    async def test_system_and_system_blocks_forwarded(self) -> None:
        transport = _transport()
        transport._client.messages.create = AsyncMock(return_value=_response())

        await transport.complete([UserMessage(content="hi")], system="legacy")

        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["system"][0]["text"] == "legacy"

    async def test_stop_reason_mapping(self) -> None:
        transport = _transport()
        response = _response(
            content=[_block("tool_use", id="1", name="search", input={})], stop_reason="tool_use"
        )
        transport._client.messages.create = AsyncMock(return_value=response)
        result = await transport.complete([UserMessage(content="hi")])
        assert result.stop_reason == StopReason.TOOL_USE

    async def test_sdk_error_wrapped_as_transport_error(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        transport._client.messages.create = AsyncMock(side_effect=anthropic_sdk.APIConnectionError(request=req))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True


class TestCompleteStructured:
    async def test_extracts_structured_data(self) -> None:
        transport = _transport()
        response = _response(
            content=[_block("tool_use", id="1", name="structured_output", input={"route": "react"})]
        )
        transport._client.messages.create = AsyncMock(return_value=response)

        result = await transport.complete_structured(
            [UserMessage(content="classify")], output_schema={"type": "object"}, system="be terse"
        )

        assert result.data == {"route": "react"}
        _, kwargs = transport._client.messages.create.call_args
        assert kwargs["tool_choice"] == {"type": "tool", "name": "structured_output"}
        assert kwargs["system"][0]["text"] == "be terse"

    async def test_raises_when_tool_use_block_missing(self) -> None:
        transport = _transport()
        response = _response(content=[_block("text", text="no tool call here")])
        transport._client.messages.create = AsyncMock(return_value=response)
        with pytest.raises(TransportError, match="Structured output tool_use block not found"):
            await transport.complete_structured([UserMessage(content="x")], output_schema={})

    async def test_sdk_error_wrapped(self) -> None:
        transport = _transport()
        transport._client.messages.create = AsyncMock(side_effect=ValueError("boom"))
        with pytest.raises(TransportError):
            await transport.complete_structured([UserMessage(content="x")], output_schema={})


class TestStream:
    async def test_text_deltas_and_final_event(self) -> None:
        transport = _transport()
        final_message = _response(content=[_block("text", text="Hello")])
        fake_stream = _FakeMessageStream(text_chunks=["Hel", "lo"], final_message=final_message)
        transport._client.messages.stream = MagicMock(return_value=fake_stream)

        events = [event async for event in transport.stream([UserMessage(content="hi")])]

        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.delta for e in text_events] == ["Hel", "lo"]
        complete = next(e for e in events if isinstance(e, StreamCompleteEvent))
        assert complete.response.message.text == "Hello"
        assert complete.response.usage.input_tokens == 10

    async def test_final_message_with_tool_use(self) -> None:
        transport = _transport()
        final_message = _response(
            content=[_block("tool_use", id="1", name="search", input={"q": "x"})], stop_reason="tool_use"
        )
        fake_stream = _FakeMessageStream(text_chunks=[], final_message=final_message)
        transport._client.messages.stream = MagicMock(return_value=fake_stream)

        events = [event async for event in transport.stream([UserMessage(content="search")])]

        complete = next(e for e in events if isinstance(e, StreamCompleteEvent))
        assert complete.response.stop_reason == StopReason.TOOL_USE
        assert complete.response.message.tool_calls[0].name == "search"

    async def test_kwargs_forwarded_to_stream_call(self) -> None:
        transport = _transport()
        final_message = _response()
        fake_stream = _FakeMessageStream(text_chunks=[], final_message=final_message)
        transport._client.messages.stream = MagicMock(return_value=fake_stream)
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]

        async for _ in transport.stream(
            [UserMessage(content="hi")], tools=tools, thinking_budget=2000, system_blocks=["A", "B"]
        ):
            pass

        _, kwargs = transport._client.messages.stream.call_args
        assert kwargs["thinking"] == {"type": "enabled", "budget_tokens": 2000}
        assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        assert kwargs["system"][0]["text"] == "A"
        assert kwargs["system"][1]["text"] == "B"

    async def test_error_raised_by_stream_call_is_wrapped(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

        def _raise(**_kwargs: Any) -> Any:
            raise anthropic_sdk.APIConnectionError(request=req)

        transport._client.messages.stream = MagicMock(side_effect=_raise)
        with pytest.raises(TransportError) as exc_info:
            async for _ in transport.stream([UserMessage(content="hi")]):
                pass
        assert exc_info.value.retryable is True

    async def test_error_during_text_iteration_is_wrapped(self) -> None:
        transport = _transport()
        fake_stream = _FakeMessageStream(
            text_chunks=["partial"], final_message=_response(), raise_on_iter=RuntimeError("stream broke")
        )
        transport._client.messages.stream = MagicMock(return_value=fake_stream)
        with pytest.raises(TransportError):
            async for _ in transport.stream([UserMessage(content="hi")]):
                pass
