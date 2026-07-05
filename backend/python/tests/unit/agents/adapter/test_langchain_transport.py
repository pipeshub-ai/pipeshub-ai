"""`LangChainTransport` (`app/agents/agent_loop/langchain_transport.py`) —
`complete()`/`complete_structured()`/`stream()` produce valid agent-loop
response types from a fake LangChain `BaseChatModel`, with no network."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agents.agent_loop.langchain_transport import LangChainTransport


class _FakeModel:
    def __init__(self, response: AIMessage | None = None, raise_on_invoke: Exception | None = None) -> None:
        self._response = response or AIMessage(content="hi")
        self._raise = raise_on_invoke
        self.bind_tools_called_with: list[Any] | None = None
        self.structured_output_schema: Any = None

    def bind_tools(self, tools: list[Any]) -> "_FakeModel":
        self.bind_tools_called_with = tools
        return self

    async def ainvoke(self, messages: list) -> AIMessage:
        if self._raise is not None:
            raise self._raise
        return self._response

    def with_structured_output(self, schema: Any, include_raw: bool = False) -> "_FakeStructuredModel":
        self.structured_output_schema = schema
        return _FakeStructuredModel(parsed={"route": "react"}, raw=self._response)


class _FakeStructuredModel:
    def __init__(self, parsed: dict, raw: AIMessage) -> None:
        self._parsed = parsed
        self._raw = raw

    async def ainvoke(self, messages: list) -> dict:
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


class TestBindToolsFailSoft:
    async def test_bind_tools_exception_degrades_to_plain_completion(self) -> None:
        class _NoBindModel(_FakeModel):
            def bind_tools(self, tools: list[Any]) -> "_NoBindModel":
                raise RuntimeError("this provider can't bind tools")

        model = _NoBindModel(AIMessage(content="fallback ok"))
        transport = LangChainTransport(model)
        schema = ToolSchema(name="t", description="d", input_schema={"type": "object", "properties": {}})
        response = await transport.complete([UserMessage(content="hi")], tools=[schema])
        assert response.message.text == "fallback ok"


class TestCompleteStructured:
    async def test_structured_output_parsed_from_raw_schema(self) -> None:
        transport = LangChainTransport(_FakeModel())
        result = await transport.complete_structured(
            [UserMessage(content="classify this")],
            output_schema={"type": "object", "properties": {"route": {"type": "string"}}},
        )
        assert result.data == {"route": "react"}
