"""Coverage tests for `app/agent_loop_lib/transport/openai.py`.

All tests mock the `openai` SDK client entirely (`AsyncOpenAI` is replaced
with a stand-in whose `chat.completions.create` is an `AsyncMock`) — no
network calls are made. Response/chunk objects are plain `SimpleNamespace`s
since `OpenAITransport` only ever reads them via attribute access, never
via `isinstance`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import openai as openai_sdk
import pytest

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    SystemMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.transport.openai import OpenAITransport


def _transport(**kwargs: Any) -> OpenAITransport:
    return OpenAITransport(api_key="sk-test", **kwargs)


def _chat_response(
    content: str | None = "hello",
    tool_calls: list | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cached_tokens: int = 0,
) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )
    return SimpleNamespace(choices=[choice], usage=usage)


def _raw_tool_call(id_: str = "call_1", name: str = "search", arguments: str = '{"q": "x"}') -> SimpleNamespace:
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=arguments))


class _AsyncChunkIterator:
    def __init__(self, chunks: list) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> "_AsyncChunkIterator":
        return self

    async def __anext__(self) -> Any:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _ExplodingAsyncIterator:
    def __aiter__(self) -> "_ExplodingAsyncIterator":
        return self

    async def __anext__(self) -> Any:
        raise RuntimeError("stream exploded")


def _stream_chunk(
    content: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = None,
    usage: SimpleNamespace | None = None,
) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _tc_delta(index: int, id_: str | None = None, name: str | None = None, arguments: str | None = None) -> SimpleNamespace:
    fn = SimpleNamespace(name=name, arguments=arguments) if (name is not None or arguments is not None) else None
    return SimpleNamespace(index=index, id=id_, function=fn)


class TestInit:
    def test_missing_sdk_raises_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "openai":
                raise ImportError("no openai")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        with pytest.raises(ImportError, match="agent-loop\\[openai\\]"):
            OpenAITransport(api_key="sk-test")

    def test_base_url_forwarded_to_client(self) -> None:
        transport = _transport(base_url="https://my-proxy.example.com/v1")
        assert transport._client.base_url == httpx.URL("https://my-proxy.example.com/v1/")

    def test_default_model_and_provider(self) -> None:
        transport = _transport()
        assert transport.provider == "openai"
        assert transport.model_name == OpenAITransport.DEFAULT_MODEL

    def test_cumulative_counters_start_at_zero(self) -> None:
        transport = _transport()
        assert transport.total_input_tokens == 0
        assert transport.total_output_tokens == 0
        assert transport.total_llm_calls == 0
        assert transport.total_cache_read_tokens == 0


class TestFormatMessage:
    def test_tool_message_includes_step_footer(self) -> None:
        transport = _transport()
        msg = ToolMessage(content="result", tool_call_id="call_1", step_footer=" [footer]")
        formatted = transport._format_message(msg)
        assert formatted == {"role": "tool", "tool_call_id": "call_1", "content": "result [footer]"}

    def test_assistant_message_with_text_and_tool_calls(self) -> None:
        transport = _transport()
        msg = AssistantMessage(
            content=[TextPart(text="thinking...")],
            tool_calls=[ToolCall(id="1", name="search", arguments={"q": "x"})],
        )
        formatted = transport._format_message(msg)
        assert formatted["role"] == "assistant"
        assert formatted["content"] == "thinking..."
        assert formatted["tool_calls"] == [
            {"id": "1", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}}
        ]

    def test_assistant_message_without_text_or_tool_calls_is_none(self) -> None:
        transport = _transport()
        msg = AssistantMessage(content=[])
        formatted = transport._format_message(msg)
        assert formatted["content"] is None
        assert formatted["tool_calls"] is None

    def test_user_message_with_list_content_joins_text_parts(self) -> None:
        transport = _transport()
        msg = UserMessage(content=[TextPart(text="hello"), TextPart(text="world")])
        formatted = transport._format_message(msg)
        assert formatted == {"role": "user", "content": "hello world"}

    def test_plain_string_content_message(self) -> None:
        transport = _transport()
        msg = SystemMessage(content="be nice")
        formatted = transport._format_message(msg)
        assert formatted == {"role": "system", "content": "be nice"}

    def test_empty_content_falls_back_to_empty_string(self) -> None:
        transport = _transport()
        msg = UserMessage(content="")
        formatted = transport._format_message(msg)
        assert formatted["content"] == ""


class TestFormatMessages:
    def test_prepends_system_message_when_given(self) -> None:
        transport = _transport()
        result = transport._format_messages([UserMessage(content="hi")], system="be terse")
        assert result[0] == {"role": "system", "content": "be terse"}
        assert result[1] == {"role": "user", "content": "hi"}

    def test_no_system_key_when_system_is_none(self) -> None:
        transport = _transport()
        result = transport._format_messages([UserMessage(content="hi")], system=None)
        assert len(result) == 1
        assert result[0]["role"] == "user"


class TestFormatTools:
    def test_none_when_no_tools(self) -> None:
        assert _transport()._format_tools(None) is None
        assert _transport()._format_tools([]) is None

    def test_maps_tool_schema_to_openai_function_shape(self) -> None:
        transport = _transport()
        tools = [ToolSchema(name="search", description="search the web", input_schema={"type": "object"})]
        result = transport._format_tools(tools)
        assert result == [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "search the web",
                    "parameters": {"type": "object"},
                },
            }
        ]

    def test_missing_input_schema_defaults_to_empty_object(self) -> None:
        transport = _transport()
        tools = [ToolSchema(name="noop", description="does nothing", input_schema={})]
        result = transport._format_tools(tools)
        assert result[0]["function"]["parameters"] == {"type": "object", "properties": {}}


class TestParseToolCalls:
    def test_parses_valid_json_arguments(self) -> None:
        transport = _transport()
        result = transport._parse_tool_calls([_raw_tool_call(arguments='{"q": "cats"}')])
        assert result == [ToolCall(id="call_1", name="search", arguments={"q": "cats"})]

    def test_malformed_json_falls_back_to_empty_dict(self) -> None:
        transport = _transport()
        result = transport._parse_tool_calls([_raw_tool_call(arguments="{not json")])
        assert result[0].arguments == {}

    def test_empty_arguments_string_becomes_empty_dict(self) -> None:
        transport = _transport()
        result = transport._parse_tool_calls([_raw_tool_call(arguments="")])
        assert result[0].arguments == {}

    def test_none_input_returns_empty_list(self) -> None:
        assert _transport()._parse_tool_calls(None) == []


class TestParseResponse:
    def test_text_only(self) -> None:
        transport = _transport()
        msg = transport._parse_response(SimpleNamespace(content="hi there", tool_calls=None))
        assert msg.text == "hi there"
        assert msg.tool_calls is None

    def test_with_tool_calls(self) -> None:
        transport = _transport()
        msg = transport._parse_response(SimpleNamespace(content=None, tool_calls=[_raw_tool_call()]))
        assert msg.tool_calls[0].name == "search"
        assert msg.text == ""


class TestUsageFrom:
    def test_full_usage_extracted_and_accumulated(self) -> None:
        transport = _transport()
        response = _chat_response(prompt_tokens=100, completion_tokens=50, cached_tokens=20)
        usage = transport._usage_from(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_read_tokens == 20
        assert usage.cache_write_tokens == 0
        assert transport.total_input_tokens == 100
        assert transport.total_output_tokens == 50
        assert transport.total_cache_read_tokens == 20
        assert transport.total_llm_calls == 1

        transport._usage_from(response)
        assert transport.total_llm_calls == 2
        assert transport.total_input_tokens == 200

    def test_missing_usage_defaults_to_zero(self) -> None:
        transport = _transport()
        usage = transport._usage_from(SimpleNamespace(usage=None))
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert transport.total_llm_calls == 1

    def test_non_int_field_coerced_to_zero(self) -> None:
        transport = _transport()
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens="oops", completion_tokens=None))
        usage = transport._usage_from(response)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


class TestWrapError:
    def test_connection_error_is_retryable(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        exc = openai_sdk.APIConnectionError(request=req)
        wrapped = transport._wrap_error(exc, "complete")
        assert isinstance(wrapped, TransportError)
        assert wrapped.retryable is True
        assert "complete" in str(wrapped)

    def test_timeout_error_is_retryable(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        exc = openai_sdk.APITimeoutError(request=req)
        wrapped = transport._wrap_error(exc, "stream")
        assert wrapped.retryable is True

    def test_status_429_is_retryable(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        resp = httpx.Response(status_code=429, request=req)
        exc = openai_sdk.APIStatusError("rate limited", response=resp, body=None)
        wrapped = transport._wrap_error(exc, "complete")
        assert wrapped.status_code == 429
        assert wrapped.retryable is True

    def test_status_401_is_not_retryable(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        resp = httpx.Response(status_code=401, request=req)
        exc = openai_sdk.APIStatusError("bad key", response=resp, body=None)
        wrapped = transport._wrap_error(exc, "complete")
        assert wrapped.status_code == 401
        assert wrapped.retryable is False

    def test_generic_exception_has_no_status_code_and_not_retryable(self) -> None:
        transport = _transport()
        wrapped = transport._wrap_error(ValueError("boom"), "complete")
        assert wrapped.status_code is None
        assert wrapped.retryable is False


class TestComplete:
    async def test_maps_kwargs_and_response(self) -> None:
        transport = _transport(model="gpt-4o-mini")
        response = _chat_response(content="42", finish_reason="stop")
        transport._client.chat.completions.create = AsyncMock(return_value=response)

        result = await transport.complete([UserMessage(content="what is the answer?")])

        transport._client.chat.completions.create.assert_awaited_once()
        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["messages"][-1] == {"role": "user", "content": "what is the answer?"}
        assert "tools" not in kwargs
        assert result.message.text == "42"
        assert result.stop_reason == StopReason.END_TURN
        assert result.usage.input_tokens == 10
        assert result.model == "gpt-4o-mini"

    async def test_default_model_used_when_not_overridden(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())
        result = await transport.complete([UserMessage(content="hi")])
        assert result.model == OpenAITransport.DEFAULT_MODEL

    async def test_tool_calls_map_to_tool_use_stop_reason(self) -> None:
        transport = _transport()
        response = _chat_response(content=None, tool_calls=[_raw_tool_call()], finish_reason="tool_calls")
        transport._client.chat.completions.create = AsyncMock(return_value=response)
        result = await transport.complete([UserMessage(content="search cats")])
        assert result.stop_reason == StopReason.TOOL_USE
        assert result.message.tool_calls[0].name == "search"

    async def test_length_finish_reason_maps_to_max_tokens(self) -> None:
        transport = _transport()
        response = _chat_response(finish_reason="length")
        transport._client.chat.completions.create = AsyncMock(return_value=response)
        result = await transport.complete([UserMessage(content="hi")])
        assert result.stop_reason == StopReason.MAX_TOKENS

    async def test_tools_and_effort_forwarded(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]

        await transport.complete([UserMessage(content="hi")], tools=tools, effort="high")

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["tools"][0]["function"]["name"] == "search"
        assert kwargs["reasoning_effort"] == "high"

    async def test_system_blocks_joined_when_system_not_given(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())

        await transport.complete([UserMessage(content="hi")], system_blocks=["Block A", "Block B"])

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["messages"][0] == {"role": "system", "content": "Block A\n\nBlock B"}

    async def test_explicit_system_wins_over_system_blocks(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(return_value=_chat_response())

        await transport.complete([UserMessage(content="hi")], system="explicit", system_blocks=["ignored"])

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["messages"][0] == {"role": "system", "content": "explicit"}

    async def test_sdk_error_wrapped_as_transport_error(self) -> None:
        transport = _transport()
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        transport._client.chat.completions.create = AsyncMock(
            side_effect=openai_sdk.APIConnectionError(request=req)
        )
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True


class TestCompleteStructured:
    async def test_extracts_structured_data_from_tool_call(self) -> None:
        transport = _transport()
        message = SimpleNamespace(
            tool_calls=[_raw_tool_call(name="structured_output", arguments='{"route": "react"}')]
        )
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
            usage=SimpleNamespace(
                prompt_tokens=3, completion_tokens=2, prompt_tokens_details=SimpleNamespace(cached_tokens=0)
            ),
        )
        transport._client.chat.completions.create = AsyncMock(return_value=response)

        result = await transport.complete_structured([UserMessage(content="classify")], output_schema={"type": "object"})

        assert result.data == {"route": "react"}
        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["tool_choice"] == {"type": "function", "function": {"name": "structured_output"}}

    async def test_malformed_json_becomes_empty_dict(self) -> None:
        transport = _transport()
        message = SimpleNamespace(tool_calls=[_raw_tool_call(name="structured_output", arguments="{bad")])
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
            usage=SimpleNamespace(
                prompt_tokens=1, completion_tokens=1, prompt_tokens_details=SimpleNamespace(cached_tokens=0)
            ),
        )
        transport._client.chat.completions.create = AsyncMock(return_value=response)
        result = await transport.complete_structured([UserMessage(content="x")], output_schema={})
        assert result.data == {}

    async def test_raises_when_tool_calls_present_but_none_match(self) -> None:
        transport = _transport()
        message = SimpleNamespace(tool_calls=[_raw_tool_call(name="other_tool", arguments="{}")])
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
            usage=SimpleNamespace(
                prompt_tokens=1, completion_tokens=1, prompt_tokens_details=SimpleNamespace(cached_tokens=0)
            ),
        )
        transport._client.chat.completions.create = AsyncMock(return_value=response)
        with pytest.raises(TransportError, match="structured_output tool call not found"):
            await transport.complete_structured([UserMessage(content="x")], output_schema={})

    async def test_raises_when_tool_call_not_found(self) -> None:
        transport = _transport()
        message = SimpleNamespace(tool_calls=None)
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(
                prompt_tokens=1, completion_tokens=1, prompt_tokens_details=SimpleNamespace(cached_tokens=0)
            ),
        )
        transport._client.chat.completions.create = AsyncMock(return_value=response)
        with pytest.raises(TransportError, match="structured_output tool call not found"):
            await transport.complete_structured([UserMessage(content="x")], output_schema={})

    async def test_sdk_error_wrapped(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(side_effect=ValueError("boom"))
        with pytest.raises(TransportError):
            await transport.complete_structured([UserMessage(content="x")], output_schema={})


class TestStream:
    async def test_text_deltas_accumulate_and_final_event_assembled(self) -> None:
        transport = _transport()
        chunks = [
            _stream_chunk(content="Hel"),
            _stream_chunk(content="lo"),
            _stream_chunk(finish_reason="stop", usage=SimpleNamespace(
                prompt_tokens=7, completion_tokens=2, prompt_tokens_details=SimpleNamespace(cached_tokens=0)
            )),
        ]
        transport._client.chat.completions.create = AsyncMock(return_value=_AsyncChunkIterator(chunks))

        events = [event async for event in transport.stream([UserMessage(content="hi")])]

        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.delta for e in text_events] == ["Hel", "lo"]
        complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
        assert len(complete_events) == 1
        final = complete_events[0].response
        assert final.message.text == "Hello"
        assert final.usage.input_tokens == 7
        assert final.stop_reason == StopReason.END_TURN

    async def test_tool_call_deltas_accumulate_across_chunks_by_index(self) -> None:
        transport = _transport()
        chunks = [
            _stream_chunk(tool_calls=[_tc_delta(0, id_="call_1", name="search", arguments="")]),
            _stream_chunk(tool_calls=[_tc_delta(0, arguments='{"q":')]),
            _stream_chunk(tool_calls=[_tc_delta(0, arguments='"cats"}')]),
            _stream_chunk(finish_reason="tool_calls"),
        ]
        transport._client.chat.completions.create = AsyncMock(return_value=_AsyncChunkIterator(chunks))

        events = [event async for event in transport.stream([UserMessage(content="find cats")])]

        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.stop_reason == StopReason.TOOL_USE
        assert len(final.message.tool_calls) == 1
        tc = final.message.tool_calls[0]
        assert tc.id == "call_1"
        assert tc.name == "search"
        assert tc.arguments == {"q": "cats"}

    async def test_malformed_tool_call_json_becomes_empty_args(self) -> None:
        transport = _transport()
        chunks = [
            _stream_chunk(tool_calls=[_tc_delta(0, id_="call_1", name="search", arguments="{not json")]),
        ]
        transport._client.chat.completions.create = AsyncMock(return_value=_AsyncChunkIterator(chunks))
        events = [event async for event in transport.stream([UserMessage(content="x")])]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.tool_calls[0].arguments == {}

    async def test_tool_call_missing_id_gets_generated_placeholder(self) -> None:
        transport = _transport()
        chunks = [_stream_chunk(tool_calls=[_tc_delta(0, name="search", arguments="{}")])]
        transport._client.chat.completions.create = AsyncMock(return_value=_AsyncChunkIterator(chunks))
        events = [event async for event in transport.stream([UserMessage(content="x")])]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.tool_calls[0].id == "call_0"

    async def test_chunk_with_no_choices_is_skipped(self) -> None:
        transport = _transport()
        chunks = [SimpleNamespace(choices=[], usage=None), _stream_chunk(content="ok", finish_reason="stop")]
        transport._client.chat.completions.create = AsyncMock(return_value=_AsyncChunkIterator(chunks))
        events = [event async for event in transport.stream([UserMessage(content="x")])]
        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.delta for e in text_events] == ["ok"]

    async def test_no_text_produces_none_content(self) -> None:
        transport = _transport()
        chunks = [_stream_chunk(finish_reason="stop")]
        transport._client.chat.completions.create = AsyncMock(return_value=_AsyncChunkIterator(chunks))
        events = [event async for event in transport.stream([UserMessage(content="x")])]
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.content == []
        assert final.message.tool_calls is None

    async def test_error_during_iteration_wrapped_as_transport_error(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(return_value=_ExplodingAsyncIterator())
        with pytest.raises(TransportError):
            async for _ in transport.stream([UserMessage(content="x")]):
                pass

    async def test_system_blocks_joined_when_streaming(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(
            return_value=_AsyncChunkIterator([_stream_chunk(finish_reason="stop")])
        )

        async for _ in transport.stream([UserMessage(content="hi")], system_blocks=["A", "B"]):
            pass

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["messages"][0] == {"role": "system", "content": "A\n\nB"}

    async def test_tools_forwarded_in_stream_kwargs(self) -> None:
        transport = _transport()
        transport._client.chat.completions.create = AsyncMock(
            return_value=_AsyncChunkIterator([_stream_chunk(finish_reason="stop")])
        )
        tools = [ToolSchema(name="search", description="d", input_schema={"type": "object"})]

        async for _ in transport.stream([UserMessage(content="x")], tools=tools, effort="low"):
            pass

        _, kwargs = transport._client.chat.completions.create.call_args
        assert kwargs["tools"][0]["function"]["name"] == "search"
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["stream"] is True
        assert kwargs["stream_options"] == {"include_usage": True}
