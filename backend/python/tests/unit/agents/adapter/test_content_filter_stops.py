"""A provider content filter that stops a response (rather than rejecting the
request with a 400) reaches the user as the classified `content_filter` error,
not as an empty turn the loop nudges into a canned refusal. A response with no
output is logged with ids and sizes only."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.content_filter import content_filter_error
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.error_classification import classify_error
from app.agents.agent_loop.langchain_transport import LangChainTransport
from tests.unit.agent_loop_lib.transport.test_azure_direct_transport import (
    _chunk,
    _responses_transport,
    _sse,
    _transport,
    _wire,
)
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

_SECRET = "the confidential question text"


class _Model:
    def __init__(self, *, chunks: list[AIMessageChunk] | None = None, response: AIMessage | None = None) -> None:
        self._chunks = chunks or []
        self._response = response

    def bind_tools(self, tools: list[Any]) -> "_Model":
        return self

    async def ainvoke(self, messages: list, config: Any = None) -> AIMessage:  # noqa: ANN401
        return self._response

    async def astream(self, messages: list, config: Any = None):  # noqa: ANN401
        for chunk in self._chunks:
            yield chunk


_INCOMPLETE = {"id": "resp_1", "status": "incomplete", "incomplete_details": {"reason": "content_filter"}}


async def _drain(transport: Any) -> list:  # noqa: ANN401
    return [e async for e in transport.stream([UserMessage(content=_SECRET)])]


class TestLangChainTransport:
    @pytest.mark.asyncio
    async def test_a_filtered_stream_raises_a_content_filter_error(self) -> None:
        transport = LangChainTransport(_Model(chunks=[AIMessageChunk(content="", response_metadata=_INCOMPLETE)]))

        with pytest.raises(TransportError) as info:
            await _drain(transport)

        assert info.value.retryable is False
        assert classify_error(str(info.value))[0] == "content_filter"

    @pytest.mark.asyncio
    async def test_a_chat_finish_reason_content_filter_on_complete_raises(self) -> None:
        response = AIMessage(content="", response_metadata={"finish_reason": "content_filter"})
        transport = LangChainTransport(_Model(response=response))

        with pytest.raises(TransportError) as info:
            await transport.complete([UserMessage(content=_SECRET)])

        assert classify_error(str(info.value))[0] == "content_filter"

    @pytest.mark.asyncio
    async def test_output_the_filter_let_through_is_kept(self) -> None:
        chunk = AIMessageChunk(content="partial answer", response_metadata={"finish_reason": "content_filter"})
        events = await _drain(LangChainTransport(_Model(chunks=[chunk])))

        assert events[-1].response.message.text == "partial answer"

    @pytest.mark.asyncio
    async def test_an_empty_response_is_logged_without_content(self, caplog) -> None:
        caplog.set_level(logging.WARNING)
        metadata = {"id": "resp_9", "status": "completed", "finish_reason": "stop"}

        await _drain(LangChainTransport(_Model(chunks=[AIMessageChunk(content="", response_metadata=metadata)])))

        records = [r.getMessage() for r in caplog.records if "returned no output" in r.getMessage()]
        assert len(records) == 1
        assert "response_id=resp_9" in records[0]
        assert "status=completed" in records[0]
        assert "finish_reason=stop" in records[0]
        assert _SECRET not in caplog.text

    @pytest.mark.asyncio
    async def test_a_filtered_stream_log_names_the_reason(self, caplog) -> None:
        caplog.set_level(logging.WARNING)
        transport = LangChainTransport(_Model(chunks=[AIMessageChunk(content="", response_metadata=_INCOMPLETE)]))

        with pytest.raises(TransportError):
            await _drain(transport)

        assert "incomplete_reason=content_filter" in caplog.text
        assert _SECRET not in caplog.text


class TestDirectOpenAITransport:
    @pytest.mark.asyncio
    async def test_responses_stream_incomplete_for_content_filter_raises(self, caplog) -> None:
        caplog.set_level(logging.WARNING)
        events = [
            {"type": "response.refusal.delta", "delta": "I'm sorry", "output_index": 0},
            {"type": "response.incomplete", "response": {
                "id": "resp_2", "status": "incomplete", "output": [],
                "incomplete_details": {"reason": "content_filter"},
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }},
        ]
        transport = _responses_transport(_sse(events))

        with pytest.raises(TransportError) as info:
            await _drain(transport)

        assert classify_error(str(info.value))[0] == "content_filter"
        assert "response_id=resp_2" in caplog.text
        assert "incomplete_reason=content_filter" in caplog.text
        assert _SECRET not in caplog.text

    @pytest.mark.asyncio
    async def test_chat_stream_finish_reason_content_filter_raises(self) -> None:
        transport = _transport()
        _wire(transport, [_chunk(finish="content_filter")])

        with pytest.raises(TransportError) as info:
            await _drain(transport)

        assert classify_error(str(info.value))[0] == "content_filter"


class TestAgentRun:
    @pytest.mark.asyncio
    async def test_the_run_fails_with_an_error_the_classifier_reads_as_content_filter(self) -> None:
        transport = ScriptedTransport([ScriptedStep(error=content_filter_error("stream", "incomplete_details.reason=content_filter"))])
        registry = TransportRegistry()
        registry.register("scripted", lambda: transport)
        agent = Agent(
            AgentSpec(name="a", system_prompt="s", model=ModelSpec(provider="scripted", model="m"), max_turns=3),
            AgentRuntime(transport_registry=registry, tool_registry=ToolRegistry()),
        )

        result = await agent.run(Goal(description="q"))

        assert result.success is False
        assert classify_error(result.error or "")[0] == "content_filter"
        assert len(transport.calls) == 1
