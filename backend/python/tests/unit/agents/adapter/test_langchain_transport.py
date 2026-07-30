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
from app.agents.agent_loop import langchain_transport as transport_module
from app.agents.agent_loop.langchain_transport import LangChainTransport
from app.utils.llm_api_mode_store import REASONING_MANDATORY_FALLBACK_EFFORT, LLMApiMode

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


class _FakeApiShapeModel:
    """Fake OpenAI-family LangChain model exposing `use_responses_api`/
    `reasoning`/`reasoning_effort` fields plus `model_copy(update=...)`
    (both required by `LangChainTransport._api_shape_fallback`), with
    pluggable success/failure behavior keyed off `use_responses_api` so a
    test can simulate "the Responses attempt is rejected, Chat Completions
    works" (and the inverse) without a real provider.

    `fail_when_responses_api`: `True` raises whenever `use_responses_api`
    is truthy, `False` raises whenever it's falsy, `None` never raises.
    """

    def __init__(
        self,
        use_responses_api: bool | None = None,
        reasoning: dict | None = None,
        reasoning_effort: str | None = None,
        fail_when_responses_api: bool | None = None,
        error: Exception | None = None,
        response: AIMessage | None = None,
        stream_chunks: list[AIMessageChunk] | None = None,
    ) -> None:
        self.use_responses_api = use_responses_api
        self.reasoning = reasoning
        self.reasoning_effort = reasoning_effort
        self._fail_when_responses_api = fail_when_responses_api
        self._error = error
        self._response = response or AIMessage(content="ok")
        self._stream_chunks = stream_chunks
        self.bind_tools_calls: list[list[Any]] = []

    def _should_fail(self) -> bool:
        if self._fail_when_responses_api is None:
            return False
        return bool(self.use_responses_api) == self._fail_when_responses_api

    def bind_tools(self, tools: list[Any]) -> "_FakeApiShapeModel":
        self.bind_tools_calls.append(tools)
        return self

    async def ainvoke(self, messages: list, config: Any = None) -> AIMessage:
        if self._should_fail():
            raise self._error
        return self._response

    async def astream(self, messages: list, config: Any = None) -> "AsyncIterator[AIMessageChunk]":
        if self._should_fail():
            raise self._error
        for chunk in self._stream_chunks or []:
            yield chunk

    def model_copy(self, update: dict) -> "_FakeApiShapeModel":
        return _FakeApiShapeModel(
            use_responses_api=update.get("use_responses_api", self.use_responses_api),
            reasoning=update.get("reasoning", self.reasoning),
            reasoning_effort=update.get("reasoning_effort", self.reasoning_effort),
            fail_when_responses_api=self._fail_when_responses_api,
            error=self._error,
            response=self._response,
            stream_chunks=self._stream_chunks,
        )


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

    async def test_529_overloaded_status_code_marks_retryable(self) -> None:
        """Anthropic's non-standard "overloaded_error" status (capacity
        exhaustion across all customers, not this request's fault) — the
        most common transient failure against the Anthropic API in
        practice. `anthropic.APIStatusError` carries it on `.status_code`
        same as any other HTTP status; regression for the bug where it was
        missing from the retryable tuple and every overload failed the
        request outright with zero retries."""
        exc = RuntimeError("Overloaded")
        exc.status_code = 529
        transport = LangChainTransport(_FakeModel(raise_on_invoke=exc))
        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])
        assert exc_info.value.retryable is True
        assert exc_info.value.status_code == 529

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

    async def test_stream_529_overloaded_is_retryable(self) -> None:
        """Same regression as `TestComplete.test_529_overloaded_status_code_
        marks_retryable`, but for the streaming path — this is the branch
        `Agent.step()`'s streaming turn actually calls
        (`app/agent_loop_lib/agent/__init__.py`'s `_call_llm`), and where
        the production incident (mid-stream `anthropic.APIStatusError`,
        `overloaded_error`, status 529, zero retries) was observed."""
        exc = RuntimeError("Overloaded")
        exc.status_code = 529
        transport = LangChainTransport(_FakeModel(raise_on_stream=exc))

        with pytest.raises(TransportError) as exc_info:
            async for _ in transport.stream([UserMessage(content="hi")]):
                pass
        assert exc_info.value.retryable is True
        assert exc_info.value.status_code == 529

    async def test_stream_model_name_resolution(self) -> None:
        transport = LangChainTransport(
            _FakeModel(stream_chunks=[AIMessageChunk(content="ok")]), model_name="my-model",
        )

        events = [e async for e in transport.stream([UserMessage(content="hi")])]

        assert events[-1].response.model == "my-model"


def _requesty_conflict_error() -> RuntimeError:
    """The exact wording from the bug this fallback fixes: a gateway
    (Requesty) accepted a Responses-shaped request and forwarded it to a
    backend that only supports the combination on Chat Completions."""
    exc = RuntimeError(
        "Function tools with reasoning_effort are not supported for "
        "gpt-5.6-luna in /v1/chat/completions. Please use /v1/responses instead."
    )
    exc.status_code = 400
    return exc


def _tool_choice_conflict_error() -> RuntimeError:
    exc = RuntimeError("Invalid parameter: 'tool_choice.function' is not supported here.")
    exc.status_code = 400
    return exc


class _FakeApiModeStore:
    def __init__(self) -> None:
        self.recorded: list[tuple[str | None, str, str]] = []

    async def record(self, model_key: str | None, model_name: str, mode: str) -> None:
        self.recorded.append((model_key, model_name, mode))


class TestApiShapeFallbackComplete:
    """`LangChainTransport.complete()` retrying once against the other API
    shape when a provider rejects reasoning + bound tools — see
    `_api_shape_fallback`'s docstring."""

    async def test_responses_conflict_falls_back_to_no_reasoning_and_pins_llm(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        model = _FakeApiShapeModel(
            use_responses_api=True, reasoning={"effort": "high"},
            fail_when_responses_api=True, error=_requesty_conflict_error(),
            response=AIMessage(content="worked without reasoning"),
        )
        transport = LangChainTransport(model, model_name="gpt-5.6-luna", model_key="model-key-1")
        schema = ToolSchema(name="t", description="d", input_schema={"type": "object", "properties": {}})

        response = await transport.complete([UserMessage(content="hi")], tools=[schema])

        assert response.message.text == "worked without reasoning"
        # Pinned for the rest of the run: no longer the original model.
        pinned = transport._llm
        assert pinned.use_responses_api is None
        assert pinned.reasoning is None
        assert pinned.reasoning_effort is None
        # Tools were (re)bound on the fallback model, not silently dropped.
        assert len(model.bind_tools_calls) == 1
        assert store.recorded == [
            ("model-key-1", "gpt-5.6-luna", LLMApiMode.NO_REASONING_WITH_TOOLS.value),
        ]

    async def test_chat_completions_conflict_falls_back_to_responses_api(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        model = _FakeApiShapeModel(
            use_responses_api=None, reasoning_effort="high",
            fail_when_responses_api=False, error=_tool_choice_conflict_error(),
            response=AIMessage(content="worked via responses api"),
        )
        transport = LangChainTransport(model, model_name="some-model", model_key="model-key-2")

        response = await transport.complete([UserMessage(content="hi")])

        assert response.message.text == "worked via responses api"
        pinned = transport._llm
        assert pinned.use_responses_api is True
        assert pinned.reasoning == {"effort": "high"}
        assert pinned.reasoning_effort is None
        assert store.recorded == [
            ("model-key-2", "some-model", LLMApiMode.RESPONSES.value),
        ]

    async def test_unrelated_500_does_not_trigger_fallback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        exc = RuntimeError("internal server error")
        exc.status_code = 500
        model = _FakeApiShapeModel(use_responses_api=True, fail_when_responses_api=True, error=exc)
        transport = LangChainTransport(model, model_name="gpt-5.6-luna", model_key="model-key-1")

        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])

        assert exc_info.value.retryable is True
        assert transport._llm is model
        assert store.recorded == []

    async def test_non_openai_family_model_has_nothing_to_flip(self) -> None:
        """A model with no `use_responses_api` attribute at all (e.g.
        Anthropic, Gemini, Bedrock) can't be flipped even if its error
        text happens to match one of the conflict markers — the plain
        `TransportError` path applies unchanged."""
        transport = LangChainTransport(_FakeModel(raise_on_invoke=_requesty_conflict_error()))
        with pytest.raises(TransportError):
            await transport.complete([UserMessage(content="hi")])

    async def test_retry_also_failing_raises_the_original_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        class _AlwaysFailingModel(_FakeApiShapeModel):
            """Fails on every `ainvoke`, including the fallback retry's
            freshly-`model_copy`'d instance (overridden here too, since the
            base `model_copy` would otherwise hand back a plain
            `_FakeApiShapeModel` that succeeds)."""

            async def ainvoke(self, messages: list, config: Any = None) -> AIMessage:
                raise _requesty_conflict_error()

            def model_copy(self, update: dict) -> "_AlwaysFailingModel":
                return _AlwaysFailingModel(
                    use_responses_api=update.get("use_responses_api", self.use_responses_api),
                    reasoning=update.get("reasoning", self.reasoning),
                    reasoning_effort=update.get("reasoning_effort", self.reasoning_effort),
                )

        model = _AlwaysFailingModel(use_responses_api=True, reasoning={"effort": "high"})
        transport = LangChainTransport(model, model_name="gpt-5.6-luna", model_key="model-key-1")

        with pytest.raises(TransportError) as exc_info:
            await transport.complete([UserMessage(content="hi")])

        assert "Please use /v1/responses instead" in str(exc_info.value)
        assert store.recorded == []


class TestApiShapeFallbackStream:
    async def test_conflict_before_any_chunk_retries_and_pins_llm(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        model = _FakeApiShapeModel(
            use_responses_api=True, reasoning={"effort": "high"},
            fail_when_responses_api=True, error=_requesty_conflict_error(),
            stream_chunks=[AIMessageChunk(content="fallback "), AIMessageChunk(content="worked")],
        )
        transport = LangChainTransport(model, model_name="gpt-5.6-luna", model_key="model-key-1")

        events = [e async for e in transport.stream([UserMessage(content="hi")])]

        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.delta for e in text_events] == ["fallback ", "worked"]
        assert isinstance(events[-1], StreamCompleteEvent)
        pinned = transport._llm
        assert pinned.use_responses_api is None
        assert store.recorded == [
            ("model-key-1", "gpt-5.6-luna", LLMApiMode.NO_REASONING_WITH_TOOLS.value),
        ]

    async def test_mid_stream_failure_after_chunks_does_not_retry(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 400 raised while opening the stream is safe to retry; the same
        failure AFTER chunks were already yielded is not — retrying then
        would replay text the client already received."""
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        class _MidStreamFailureModel(_FakeApiShapeModel):
            async def astream(self, messages: list, config: Any = None) -> "AsyncIterator[AIMessageChunk]":
                yield AIMessageChunk(content="partial text ")
                raise _requesty_conflict_error()

        model = _MidStreamFailureModel(use_responses_api=True, reasoning={"effort": "high"})
        transport = LangChainTransport(model, model_name="gpt-5.6-luna", model_key="model-key-1")

        with pytest.raises(TransportError):
            events = []
            async for event in transport.stream([UserMessage(content="hi")]):
                events.append(event)

        # No retry happened: the model was never flipped, and nothing was recorded.
        assert transport._llm is model
        assert store.recorded == []

    async def test_stream_retry_also_failing_raises_the_original_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        class _AlwaysFailsBeforeAnyChunk(_FakeApiShapeModel):
            async def astream(self, messages: list, config: Any = None) -> "AsyncIterator[AIMessageChunk]":
                raise _requesty_conflict_error()
                yield  # pragma: no cover - makes this an async generator

            def model_copy(self, update: dict) -> "_AlwaysFailsBeforeAnyChunk":
                return _AlwaysFailsBeforeAnyChunk(
                    use_responses_api=update.get("use_responses_api", self.use_responses_api),
                    reasoning=update.get("reasoning", self.reasoning),
                    reasoning_effort=update.get("reasoning_effort", self.reasoning_effort),
                )

        model = _AlwaysFailsBeforeAnyChunk(use_responses_api=True, reasoning={"effort": "high"})
        transport = LangChainTransport(model, model_name="gpt-5.6-luna", model_key="model-key-1")

        with pytest.raises(TransportError) as exc_info:
            async for _ in transport.stream([UserMessage(content="hi")]):
                pass

        assert "Please use /v1/responses instead" in str(exc_info.value)
        assert store.recorded == []

    async def test_no_model_key_skips_persisting_but_retry_still_applies(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`model_key` not threaded through yet (older call sites) must not
        block the in-request retry — only cross-request persistence is
        skipped. Keeps a real (non-`None`) store so this actually exercises
        the `not self._model_key` branch of `_record_api_mode`'s short-
        circuit, rather than the `store is None` branch a missing store
        would also trigger regardless of `model_key`."""
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        model = _FakeApiShapeModel(
            use_responses_api=True, reasoning={"effort": "high"},
            fail_when_responses_api=True, error=_requesty_conflict_error(),
            response=AIMessage(content="worked without reasoning"),
        )
        transport = LangChainTransport(model, model_name="gpt-5.6-luna")

        response = await transport.complete([UserMessage(content="hi")])

        assert response.message.text == "worked without reasoning"
        assert store.recorded == []


def _reasoning_mandatory_error() -> RuntimeError:
    """The exact wording from the bug this fallback fixes: some
    OpenRouter-proxied Gemini models reject an explicitly disabled
    reasoning effort outright."""
    exc = RuntimeError("Reasoning is mandatory for this endpoint and cannot be disabled.")
    exc.status_code = 400
    return exc


class _FakeReasoningMandatoryModel:
    """Fails on the first `ainvoke`/`astream` call (simulating a provider
    rejecting an explicitly disabled reasoning value), succeeds on the
    `model_copy`'d retry — keyed on a plain "already retried" flag rather
    than `_FakeApiShapeModel`'s `use_responses_api` toggle, since this
    conflict doesn't care about API shape at all."""

    def __init__(
        self,
        reasoning: dict | bool | None = None,
        reasoning_effort: str | None = None,
        already_retried: bool = False,
        response: AIMessage | None = None,
        stream_chunks: list[AIMessageChunk] | None = None,
        always_fail: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.reasoning = reasoning
        self.reasoning_effort = reasoning_effort
        self._already_retried = already_retried
        self._response = response or AIMessage(content="ok")
        self._stream_chunks = stream_chunks
        self._always_fail = always_fail
        # Overridable so a test can simulate a 400 that does NOT match the
        # reasoning-mandatory marker at all (see
        # `test_unrelated_error_does_not_trigger_fallback`), as distinct
        # from the marker matching but there being nothing to bump (see
        # `test_reasoning_already_enabled_has_nothing_to_bump`).
        self._error = error or _reasoning_mandatory_error()
        self.bind_tools_calls: list[Any] = []

    def bind_tools(self, tools: list[Any]) -> "_FakeReasoningMandatoryModel":
        self.bind_tools_calls.append(tools)
        return self

    async def ainvoke(self, messages: list, config: Any = None) -> AIMessage:
        if self._always_fail or not self._already_retried:
            raise self._error
        return self._response

    async def astream(self, messages: list, config: Any = None) -> "AsyncIterator[AIMessageChunk]":
        if self._always_fail or not self._already_retried:
            raise self._error
        for chunk in self._stream_chunks or []:
            yield chunk

    def model_copy(self, update: dict) -> "_FakeReasoningMandatoryModel":
        return _FakeReasoningMandatoryModel(
            reasoning=update.get("reasoning", self.reasoning),
            reasoning_effort=update.get("reasoning_effort", self.reasoning_effort),
            already_retried=True,
            response=self._response,
            stream_chunks=self._stream_chunks,
            always_fail=self._always_fail,
            error=self._error,
        )


class TestReasoningMandatoryFallbackComplete:
    """`LangChainTransport.complete()` retrying once with reasoning bumped
    off an explicitly disabled value when a provider refuses to have it
    disabled at all — see `_reasoning_mandatory_fallback`'s docstring."""

    async def test_none_reasoning_effort_bumps_to_low_and_pins_llm(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        model = _FakeReasoningMandatoryModel(
            reasoning_effort="none", response=AIMessage(content="worked with reasoning on"),
        )
        transport = LangChainTransport(model, model_name="gemini-flash", model_key="model-key-1")

        response = await transport.complete([UserMessage(content="hi")])

        assert response.message.text == "worked with reasoning on"
        pinned = transport._llm
        assert pinned.reasoning_effort == REASONING_MANDATORY_FALLBACK_EFFORT
        assert store.recorded == [
            ("model-key-1", "gemini-flash", LLMApiMode.REASONING_MANDATORY.value),
        ]

    async def test_reasoning_false_bumps_to_low(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        model = _FakeReasoningMandatoryModel(reasoning=False)
        transport = LangChainTransport(model, model_name="local-model", model_key="model-key-2")

        await transport.complete([UserMessage(content="hi")])

        assert transport._llm.reasoning == REASONING_MANDATORY_FALLBACK_EFFORT

    async def test_dict_shaped_reasoning_none_effort_bumps_to_low(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        model = _FakeReasoningMandatoryModel(reasoning={"effort": "none"})
        transport = LangChainTransport(model, model_name="gemini-flash", model_key="model-key-3")

        await transport.complete([UserMessage(content="hi")])

        assert transport._llm.reasoning == {"effort": REASONING_MANDATORY_FALLBACK_EFFORT}

    async def test_reasoning_already_enabled_has_nothing_to_bump(self) -> None:
        """The error text matches, but reasoning wasn't actually disabled on
        this call — nothing to retry, so the original error surfaces."""
        model = _FakeReasoningMandatoryModel(reasoning_effort="high", always_fail=True)
        transport = LangChainTransport(model, model_name="gemini-flash")

        with pytest.raises(TransportError):
            await transport.complete([UserMessage(content="hi")])

    async def test_unrelated_error_does_not_trigger_fallback(self) -> None:
        """An error that matches neither the reasoning-mandatory nor the
        API-shape conflict markers must not trigger any fallback, even on
        a model with a disabled reasoning value that COULD be bumped —
        distinct from `test_reasoning_already_enabled_has_nothing_to_bump`
        (marker matches, nothing to bump) and from
        `TestApiShapeFallbackComplete.test_non_openai_family_model_has_nothing_to_flip`
        (API-shape marker matches, model can't flip it)."""
        exc = RuntimeError("internal server error")
        exc.status_code = 500
        model = _FakeReasoningMandatoryModel(reasoning_effort="none", always_fail=True, error=exc)
        transport = LangChainTransport(model, model_name="gemini-flash")

        with pytest.raises(TransportError):
            await transport.complete([UserMessage(content="hi")])


class TestReasoningMandatoryFallbackStream:
    async def test_conflict_before_any_chunk_retries_and_pins_llm(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        model = _FakeReasoningMandatoryModel(
            reasoning_effort="none",
            stream_chunks=[AIMessageChunk(content="fallback "), AIMessageChunk(content="worked")],
        )
        transport = LangChainTransport(model, model_name="gemini-flash", model_key="model-key-1")

        events = [e async for e in transport.stream([UserMessage(content="hi")])]

        text_events = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [e.delta for e in text_events] == ["fallback ", "worked"]
        assert isinstance(events[-1], StreamCompleteEvent)
        assert transport._llm.reasoning_effort == REASONING_MANDATORY_FALLBACK_EFFORT
        assert store.recorded == [
            ("model-key-1", "gemini-flash", LLMApiMode.REASONING_MANDATORY.value),
        ]

    async def test_mid_stream_failure_after_chunks_does_not_retry(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = _FakeApiModeStore()
        monkeypatch.setattr(transport_module, "get_llm_api_mode_store", lambda: store)

        class _MidStreamFailureModel(_FakeReasoningMandatoryModel):
            async def astream(self, messages: list, config: Any = None) -> "AsyncIterator[AIMessageChunk]":
                yield AIMessageChunk(content="partial text ")
                raise _reasoning_mandatory_error()

        model = _MidStreamFailureModel(reasoning_effort="none")
        transport = LangChainTransport(model, model_name="gemini-flash", model_key="model-key-1")

        with pytest.raises(TransportError):
            async for _ in transport.stream([UserMessage(content="hi")]):
                pass

        assert transport._llm is model
        assert store.recorded == []
