"""`LangChainTransport` (`app/agents/agent_loop/langchain_transport.py`) —
`complete()`/`complete_structured()`/`stream()` produce valid agent-loop
response types from a fake LangChain `BaseChatModel`, with no network."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agents.agent_loop.langchain_transport import LangChainTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _FakeModel:
    def __init__(
        self,
        response: AIMessage | None = None,
        raise_on_invoke: Exception | None = None,
        stream_chunks: list[AIMessageChunk] | None = None,
        raise_on_stream: Exception | None = None,
        structured_llm: Any = None,
    ) -> None:
        self._response = response or AIMessage(content="hi")
        self._raise = raise_on_invoke
        self._stream_chunks = stream_chunks
        self._raise_on_stream = raise_on_stream
        self._structured_llm = structured_llm
        self.bind_tools_called_with: list[Any] | None = None
        self.structured_output_schema: Any = None

    def bind_tools(self, tools: list[Any]) -> "_FakeModel":
        self.bind_tools_called_with = tools
        return self

    async def ainvoke(self, messages: list, config: Any = None) -> AIMessage:
        if self._raise is not None:
            raise self._raise
        return self._response

    async def astream(self, messages: list, config: Any = None) -> AsyncIterator[AIMessageChunk]:
        if self._raise_on_stream is not None:
            raise self._raise_on_stream
        for chunk in self._stream_chunks or []:
            yield chunk

    def with_structured_output(self, schema: Any, include_raw: bool = False) -> Any:
        self.structured_output_schema = schema
        if self._structured_llm is not None:
            return self._structured_llm
        return _FakeStructuredModel(parsed={"route": "react"}, raw=self._response)


class _FakeStructuredModel:
    def __init__(self, parsed: dict, raw: AIMessage) -> None:
        self._parsed = parsed
        self._raw = raw

    async def ainvoke(self, messages: list, config: Any = None) -> dict:
        return {"parsed": self._parsed, "raw": self._raw}


class TestComplete:
    async def test_plain_text_response(self) -> None:
        transport = LangChainTransport(_FakeModel(AIMessage(content="The answer is 42.")))
        response = await transport.complete([UserMessage(content="what is the answer?")])
        assert response.message.text == "The answer is 42."
        assert response.stop_reason == StopReason.END_TURN

    async def test_tool_use_stop_reason(self) -> None:
        ai_message = AIMessage(content="", tool_calls=[{"name": "search", "args": {}, "id": "1"}])
        transport = LangChainTransport(_FakeModel(ai_message))
        response = await transport.complete([UserMessage(content="search something")])
        assert response.stop_reason == StopReason.TOOL_USE
        assert response.message.tool_calls[0].name == "search"

    async def test_binds_tools_when_provided(self) -> None:
        model = _FakeModel()
        transport = LangChainTransport(model)
        schema = ToolSchema(name="t", description="d", input_schema={"type": "object", "properties": {}})
        await transport.complete([UserMessage(content="hi")], tools=[schema])
        assert model.bind_tools_called_with is not None
        assert len(model.bind_tools_called_with) == 1

    async def test_no_tools_does_not_bind(self) -> None:
        model = _FakeModel()
        transport = LangChainTransport(model)
        await transport.complete([UserMessage(content="hi")])
        assert model.bind_tools_called_with is None

    async def test_transport_error_wraps_underlying_exception(self) -> None:
        transport = LangChainTransport(_FakeModel(raise_on_invoke=RuntimeError("boom")))
        with pytest.raises(TransportError):
            await transport.complete([UserMessage(content="hi")])

    async def test_429_status_code_marks_retryable(self) -> None:
        exc = RuntimeError("rate limited")
        exc.status_code = 429
        transport = LangChainTransport(_FakeModel(raise_on_invoke=exc))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True
        assert exc_info.value.status_code == 429

    async def test_400_status_code_is_not_retryable(self) -> None:
        exc = RuntimeError("bad request")
        exc.status_code = 400
        transport = LangChainTransport(_FakeModel(raise_on_invoke=exc))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is False

    async def test_connection_error_with_no_status_code_is_retryable(self) -> None:
        """A dropped connection never reaches the provider, so there's no
        HTTP status — but it's just as transient as a 429/5xx and must
        still be retried (see `_is_network_error`)."""
        transport = LangChainTransport(_FakeModel(raise_on_invoke=ConnectionError("reset by peer")))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True
        assert exc_info.value.status_code is None

    async def test_timeout_error_with_no_status_code_is_retryable(self) -> None:
        transport = LangChainTransport(_FakeModel(raise_on_invoke=TimeoutError("read timed out")))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True

    async def test_sdk_style_connection_error_name_is_retryable(self) -> None:
        """SDK-specific exception classes (openai.APIConnectionError,
        anthropic's equivalent, ...) aren't imported here since this
        transport is provider-agnostic — matched by naming convention
        instead, see `_NETWORK_ERROR_NAME_HINTS`."""
        class APIConnectionError(Exception):
            pass

        transport = LangChainTransport(_FakeModel(raise_on_invoke=APIConnectionError("boom")))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True

    async def test_unrelated_exception_with_no_status_code_is_not_retryable(self) -> None:
        transport = LangChainTransport(_FakeModel(raise_on_invoke=ValueError("bad input")))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is False

    async def test_model_name_resolution(self) -> None:
        transport = LangChainTransport(_FakeModel(), model_name="my-model")
        response = await transport.complete([UserMessage(content="hi")])
        assert response.model == "my-model"
        response_override = await transport.complete([UserMessage(content="hi")], model="override-model")
        assert response_override.model == "override-model"

    async def test_provider_is_langchain(self) -> None:
        transport = LangChainTransport(_FakeModel())
        assert transport.provider == "langchain"

    async def test_max_tokens_stop_reason_from_truncated_finish_reason(self) -> None:
        ai_message = AIMessage(content="cut off mid-sen", response_metadata={"finish_reason": "length"})
        transport = LangChainTransport(_FakeModel(ai_message))
        response = await transport.complete([UserMessage(content="write an essay")])
        assert response.stop_reason == StopReason.MAX_TOKENS
        assert response.message.truncated is True

    async def test_tool_use_stop_reason_from_finish_reason_metadata_without_tool_calls(self) -> None:
        """Some providers report `finish_reason="tool_calls"` in metadata
        even on an intermediate/malformed response with no parsed
        `tool_calls` yet — `_stop_reason_from` still surfaces TOOL_USE from
        that metadata alone (see `_STOP_REASON_TOOL_CALLS`)."""
        ai_message = AIMessage(content="", response_metadata={"finish_reason": "tool_calls"})
        transport = LangChainTransport(_FakeModel(ai_message))
        response = await transport.complete([UserMessage(content="hi")])
        assert response.stop_reason == StopReason.TOOL_USE


class TestBindToolsRaisesOnFailure:
    """`_bind_tools()` used to fail-soft (catch the exception, log a
    WARNING, and run the turn with zero tools) — that made a turn which
    explicitly needed a tool indistinguishable from "the model just chose
    not to call one", and could run all the way to `max_turns` with no way
    to ever satisfy it. It now raises `TransportError` instead (see
    `langchain_transport.py::_bind_tools`'s docstring)."""

    async def test_bind_tools_exception_raises_transport_error(self) -> None:
        class _NoBindModel(_FakeModel):
            def bind_tools(self, tools: list[Any]) -> "_NoBindModel":
                raise RuntimeError("this provider can't bind tools")

        model = _NoBindModel(AIMessage(content="fallback ok"))
        transport = LangChainTransport(model)
        schema = ToolSchema(name="t", description="d", input_schema={"type": "object", "properties": {}})
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")], tools=[schema])
        assert exc_info.value.retryable is False
        assert "this provider can't bind tools" in str(exc_info.value)


class TestCompleteStructured:
    async def test_structured_output_parsed_from_raw_schema(self) -> None:
        transport = LangChainTransport(_FakeModel())
        result = await transport.complete_structured(
            [UserMessage(content="classify this")],
            output_schema={"type": "object", "properties": {"route": {"type": "string"}}},
        )
        assert result.data == {"route": "react"}

    async def test_falls_back_to_pydantic_model_when_raw_schema_attempt_fails(self) -> None:
        """Some LangChain integrations reject a raw JSON-schema dict passed
        to `with_structured_output()` — `_invoke_structured` must silently
        fall back to building a Pydantic model from the schema instead of
        propagating that first failure (see its docstring)."""
        class _RawSchemaRejectingModel(_FakeModel):
            def with_structured_output(self, schema: Any, include_raw: bool = False) -> Any:
                if isinstance(schema, dict):
                    raise TypeError("this model requires a Pydantic model, not a dict")
                return _FakeStructuredModel(parsed={"route": "quick"}, raw=self._response)

        transport = LangChainTransport(_RawSchemaRejectingModel())
        result = await transport.complete_structured(
            [UserMessage(content="classify this")],
            output_schema={"type": "object", "properties": {"route": {"type": "string"}}},
        )
        assert result.data == {"route": "quick"}

    async def test_raises_transport_error_when_pydantic_fallback_also_fails(self) -> None:
        class _AlwaysFailingModel(_FakeModel):
            def with_structured_output(self, schema: Any, include_raw: bool = False) -> Any:
                raise RuntimeError("no structured output support at all")

        transport = LangChainTransport(_AlwaysFailingModel())
        with pytest.raises(TransportError):
            await transport.complete_structured(
                [UserMessage(content="classify this")],
                output_schema={"type": "object", "properties": {"route": {"type": "string"}}},
            )

    async def test_raises_transport_error_when_both_attempts_parse_to_none(self) -> None:
        class _NoneParsedModel(_FakeModel):
            def with_structured_output(self, schema: Any, include_raw: bool = False) -> Any:
                return _FakeStructuredModel(parsed=None, raw=self._response)

        transport = LangChainTransport(_NoneParsedModel())
        with pytest.raises(TransportError):
            await transport.complete_structured(
                [UserMessage(content="classify this")],
                output_schema={"type": "object", "properties": {"route": {"type": "string"}}},
            )

    async def test_usage_extracted_from_raw_ai_message(self) -> None:
        from langchain_core.messages.ai import UsageMetadata

        raw_response = AIMessage(
            content="ok",
            usage_metadata=UsageMetadata(input_tokens=12, output_tokens=4, total_tokens=16),
        )
        transport = LangChainTransport(_FakeModel(raw_response))
        result = await transport.complete_structured(
            [UserMessage(content="classify this")],
            output_schema={"type": "object", "properties": {"route": {"type": "string"}}},
        )
        assert result.usage.input_tokens == 12
        assert result.usage.output_tokens == 4


class TestStream:
    async def test_yields_text_deltas_then_final_complete_event(self) -> None:
        chunks = [AIMessageChunk(content="Hello "), AIMessageChunk(content="world")]
        transport = LangChainTransport(_FakeModel(stream_chunks=chunks))

        events = [e async for e in transport.stream([UserMessage(content="hi")])]

        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.delta for e in text_events] == ["Hello ", "world"]
        assert isinstance(events[-1], StreamCompleteEvent)
        assert events[-1].response.message.text == "Hello world"
        assert events[-1].response.stop_reason == StopReason.END_TURN

    async def test_merges_tool_call_chunks_into_final_tool_use_response(self) -> None:
        chunks = [
            AIMessageChunk(content="", tool_call_chunks=[
                {"name": "search", "args": "", "id": "1", "index": 0},
            ]),
            AIMessageChunk(content="", tool_call_chunks=[
                {"name": None, "args": '{"query": "cats"}', "id": None, "index": 0},
            ]),
        ]
        transport = LangChainTransport(_FakeModel(stream_chunks=chunks))

        events = [e async for e in transport.stream([UserMessage(content="search for cats")])]

        final = events[-1]
        assert isinstance(final, StreamCompleteEvent)
        assert final.response.stop_reason == StopReason.TOOL_USE
        assert final.response.message.tool_calls[0].name == "search"
        assert final.response.message.tool_calls[0].arguments == {"query": "cats"}

    async def test_binds_tools_before_streaming(self) -> None:
        model = _FakeModel(stream_chunks=[AIMessageChunk(content="ok")])
        transport = LangChainTransport(model)
        schema = ToolSchema(name="t", description="d", input_schema={"type": "object", "properties": {}})

        _ = [e async for e in transport.stream([UserMessage(content="hi")], tools=[schema])]

        assert model.bind_tools_called_with is not None
        assert len(model.bind_tools_called_with) == 1

    async def test_no_chunks_yields_empty_final_message(self) -> None:
        transport = LangChainTransport(_FakeModel(stream_chunks=[]))

        events = [e async for e in transport.stream([UserMessage(content="hi")])]

        assert len(events) == 1
        final = events[0]
        assert isinstance(final, StreamCompleteEvent)
        assert final.response.message.text == ""

    async def test_stream_error_is_wrapped_as_transport_error(self) -> None:
        transport = LangChainTransport(_FakeModel(raise_on_stream=RuntimeError("stream broke")))

        with pytest.raises(TransportError):
            async for _ in transport.stream([UserMessage(content="hi")]):
                pass

    async def test_stream_model_name_resolution(self) -> None:
        transport = LangChainTransport(
            _FakeModel(stream_chunks=[AIMessageChunk(content="ok")]), model_name="my-model",
        )

        events = [e async for e in transport.stream([UserMessage(content="hi")])]

        assert events[-1].response.model == "my-model"
