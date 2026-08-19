"""LangChain vs direct-SDK transport: same input, same events out.

The direct transport exists to remove LangChain from the streaming path without
changing what the agent loop sees. Individual unit tests check each transport in
isolation; this one drives both with the same logical response and asserts the
emitted event stream is equivalent, which is the property that actually matters.

The two SDKs shape their chunks differently, so the fixtures differ by
construction -- what is compared is the events, not the inputs.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessageChunk

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ToolCallDeltaEvent,
)
from app.agent_loop_lib.transport.azure_openai import AzureOpenAITransport
from app.agents.agent_loop.langchain_transport import LangChainTransport

_DONE = b"data: [DONE]\n\n"

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _FakeLangChainModel:
    def __init__(self, chunks: list[AIMessageChunk]) -> None:
        self._chunks = chunks

    def bind_tools(self, tools: Any) -> "_FakeLangChainModel":
        return self

    async def astream(self, messages: list, config: Any = None) -> "AsyncIterator[AIMessageChunk]":
        for c in self._chunks:
            yield c


def _openai_chunk(content=None, tool_calls=None, finish=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content, tool_calls=tool_calls),
            finish_reason=finish,
        )],
        usage=None,
    )


def _openai_tc(index, id=None, name=None, args=None):
    return SimpleNamespace(index=index, id=id,
                           function=SimpleNamespace(name=name, arguments=args))


def _azure_transport(chunks: list) -> AzureOpenAITransport:
    t = AzureOpenAITransport(
        api_key="k", azure_endpoint="https://e.openai.azure.com",
        api_version="2024-10-01-preview", deployment="dep",
    )

    class _Stream:
        def __aiter__(self):
            async def gen() -> None:
                for c in chunks:
                    yield c
            return gen()

    t._client = MagicMock()
    t._client.chat.completions.create = AsyncMock(return_value=_Stream())
    return t


async def _events(transport: object) -> list:
    return [e async for e in transport.stream(messages=[UserMessage(content="hi")])]


def _shape(events: list) -> list[tuple]:
    """Comparable summary: event type plus the fields the agent loop reads."""
    out = []
    for e in events:
        if isinstance(e, TextDeltaEvent):
            out.append(("text", e.delta))
        elif isinstance(e, ToolCallDeltaEvent):
            out.append(("tool_delta", e.index, e.name, e.arguments_delta))
        elif isinstance(e, StreamCompleteEvent):
            calls = e.response.message.tool_calls or []
            out.append(("complete", tuple((c.name, tuple(sorted(c.arguments))) for c in calls)))
        else:
            out.append((type(e).__name__,))
    return out


class TestStreamParity:
    @pytest.mark.asyncio
    async def test_text_only_response(self) -> None:
        lc = LangChainTransport(
            _FakeLangChainModel([AIMessageChunk(content="Hello "), AIMessageChunk(content="world")]),
            model_name="m",
        )
        az = _azure_transport([
            _openai_chunk(content="Hello "), _openai_chunk(content="world"),
            _openai_chunk(finish="stop"),
        ])

        assert _shape(await _events(lc)) == _shape(await _events(az))

    @pytest.mark.asyncio
    async def test_tool_call_streamed_in_fragments(self) -> None:
        """The final_answer case: fragments must arrive with matching index,
        name and payload, because live answer streaming is driven off them."""
        lc = LangChainTransport(
            _FakeLangChainModel([
                AIMessageChunk(content="", tool_call_chunks=[
                    {"name": "final_answer", "args": '{"answer_markdown"', "id": "c1", "index": 0},
                ]),
                AIMessageChunk(content="", tool_call_chunks=[
                    {"name": None, "args": ': "done"}', "id": None, "index": 0},
                ]),
            ]),
            model_name="m",
        )
        az = _azure_transport([
            _openai_chunk(tool_calls=[_openai_tc(0, id="c1", name="final_answer",
                                                 args='{"answer_markdown"')]),
            _openai_chunk(tool_calls=[_openai_tc(0, args=': "done"}')]),
            _openai_chunk(finish="tool_calls"),
        ])

        lc_shape, az_shape = _shape(await _events(lc)), _shape(await _events(az))

        # both must stream two fragments for index 0 and assemble one call
        assert [s for s in lc_shape if s[0] == "tool_delta"] == [
            s for s in az_shape if s[0] == "tool_delta"
        ]
        assert lc_shape[-1] == az_shape[-1] == (
            "complete", (("final_answer", ("answer_markdown",)),)
        )

    @pytest.mark.asyncio
    async def test_both_end_with_exactly_one_complete_event(self) -> None:
        lc = LangChainTransport(_FakeLangChainModel([AIMessageChunk(content="x")]), model_name="m")
        az = _azure_transport([_openai_chunk(content="x"), _openai_chunk(finish="stop")])

        for events in (await _events(lc), await _events(az)):
            completes = [e for e in events if isinstance(e, StreamCompleteEvent)]
            assert len(completes) == 1
            assert isinstance(events[-1], StreamCompleteEvent)


class _ConfiguredAzureModel:
    """Stands in for the `AzureChatOpenAI` that `aimodels.get_generator_model`
    builds for Azure + a reasoning model (`aimodels.py:1077-1095` merged with
    `_reasoning_effort_kwargs`, `aimodels.py:888-897`).

    Attribute names are langchain_openai's, not the constructor kwargs':
    `azure_deployment=` lands on `.deployment_name`, `api_key=` on
    `.openai_api_key`, `timeout=` on `.request_timeout`.
    """

    def __init__(self, **overrides: Any) -> None:
        self.azure_endpoint = "https://e.openai.azure.com"
        self.deployment_name = "dep"
        self.openai_api_key = "k"
        self.openai_api_version = "2024-10-01-preview"
        self.temperature = 1.0
        self.request_timeout = 360.0
        self.reasoning = {"effort": "high"}
        self.use_responses_api = True
        self.stream_usage = True
        for key, value in overrides.items():
            setattr(self, key, value)


async def _captured_request(llm: Any, model_name: str = "gpt-4o") -> dict:
    """The kwargs `azure_direct` would put on the wire for one streamed turn."""
    transport = AzureOpenAITransport.from_langchain_model(llm, model_name=model_name)
    captured: dict[str, Any] = {}

    class _Empty:
        def __aiter__(self):
            async def gen():
                return
                yield
            return gen()

    class _EmptyStream:
        """A closed SSE body. Streaming Responses requests are read as raw bytes
        (the SDK's per-event validation is skipped), so the recorder has to sit
        on with_streaming_response rather than responses.create."""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def iter_bytes(self):
            yield _DONE

    async def _create(**kwargs):
        captured.update(kwargs)
        return _Empty()

    def _create_stream(**kwargs):
        captured.update(kwargs)
        return _EmptyStream()

    transport._client = MagicMock()
    transport._client.responses.create = AsyncMock(side_effect=_create)
    transport._client.responses.with_streaming_response.create = MagicMock(side_effect=_create_stream)
    transport._client.chat.completions.create = AsyncMock(side_effect=_create)
    await _events(transport)
    return captured


class TestRequestParity:
    """Compares the *outbound request*, not the emitted events.

    `TestStreamParity` above passed throughout the period when `azure_direct`
    was sending Chat Completions with no reasoning while `langchain` sent
    Responses at effort=high — both transports turn their own chunks into the
    same event shapes, so event-level parity cannot see a request-level
    divergence. These tests read what would actually be POSTed.
    """

    @pytest.mark.asyncio
    async def test_reasoning_model_config_reaches_the_request(self) -> None:
        transport = AzureOpenAITransport.from_langchain_model(
            _ConfiguredAzureModel(), model_name="gpt-5.4-mini",
        )
        assert transport._wants_responses(), "reasoning model must use /v1/responses"

        kwargs = await _captured_request(_ConfiguredAzureModel(), model_name="gpt-5.4-mini")
        assert kwargs["reasoning"] == {"effort": "high"}
        assert kwargs["model"] == "dep", "Azure keys the request by DEPLOYMENT, not model name"
        assert "temperature" not in kwargs, "gpt-5 rejects temperature on the Responses API"
        assert "stream_options" not in kwargs, "Chat-Completions-only field"

    @pytest.mark.asyncio
    async def test_non_reasoning_model_keeps_chat_completions_and_temperature(self) -> None:
        llm = _ConfiguredAzureModel(reasoning=None, use_responses_api=False, temperature=0.3)
        kwargs = await _captured_request(llm)
        assert "input" not in kwargs, "must stay on Chat Completions"
        assert kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_timeout_is_carried_onto_the_client(self) -> None:
        """360s, not the SDK's 600s default: a turn that would hang past the
        LangChain arm's limit has to fail the same way here."""
        transport = AzureOpenAITransport.from_langchain_model(_ConfiguredAzureModel())
        assert transport._client.timeout == 360.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("attribute", "value"),
        [
            ("temperature", 0.42),
            ("reasoning", {"effort": "low"}),
            ("use_responses_api", False),
            ("request_timeout", 12.0),
        ],
    )
    async def test_every_configured_knob_changes_the_request(
        self, attribute: str, value: Any
    ) -> None:
        """The drift guard. Each entry is a setting `aimodels` puts on the model
        rather than passing per call; if `from_langchain_model` stops reading
        one -- or a new one is added there and not here -- changing it stops
        changing the request and this fails.
        """
        baseline = await _captured_request(_ConfiguredAzureModel())
        changed = await _captured_request(_ConfiguredAzureModel(**{attribute: value}))
        if attribute == "request_timeout":
            base_t = AzureOpenAITransport.from_langchain_model(_ConfiguredAzureModel())
            new_t = AzureOpenAITransport.from_langchain_model(
                _ConfiguredAzureModel(request_timeout=value)
            )
            assert base_t._client.timeout != new_t._client.timeout
        else:
            assert baseline != changed, f"{attribute} is not reaching the request"
