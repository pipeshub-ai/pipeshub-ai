"""Azure direct transport: behaviour must match the LangChain path it replaces.

The point of this transport is to drop LangChain from the streaming path without
changing what the agent loop sees, so most of these assert equivalence rather
than correctness in isolation.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.responses import StopReason
from app.agent_loop_lib.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ToolCallDeltaEvent,
)
from app.agent_loop_lib.transport.azure_openai import (
    AzureOpenAITransport,
    RequestDefaults,
)

# Terminates an SSE body. Streaming Responses requests are served from raw bytes
# now, so a mock has to produce a well-formed stream rather than a list of
# already-parsed objects.
_DONE = b"data: [DONE]\n\n"

if TYPE_CHECKING:
    from collections.abc import Iterator


def _transport() -> AzureOpenAITransport:
    return AzureOpenAITransport(
        api_key="k",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2024-10-01-preview",
        deployment="gpt-5.6-luna",
    )


def _chunk(content=None, reasoning=None, tool_calls=None, finish=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    if reasoning is not None:
        delta.reasoning_content = reasoning
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)], usage=None
    )


def _tc(index: int, id=None, name=None, args=None):
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=args)
    )


def _scripted(chunks: list):
    class _Stream:
        def __aiter__(self):
            async def gen() -> "Iterator":
                for c in chunks:
                    yield c
            return gen()
    return _Stream()


def _wire(transport: AzureOpenAITransport, chunks: list) -> MagicMock:
    transport._client = MagicMock()
    transport._client.chat.completions.create = AsyncMock(return_value=_scripted(chunks))
    return transport._client


class TestClientConstruction:
    def test_uses_the_azure_client_not_the_plain_one(self) -> None:
        t = _transport()
        assert type(t._client).__name__ == "AsyncAzureOpenAI"
        assert t.provider == "azure_direct"

    def test_model_defaults_to_the_deployment(self) -> None:
        """Azure routes on deployment name, so a public model id would 404."""
        assert _transport().model_name == "gpt-5.6-luna"

    def test_built_from_the_langchain_model_the_other_transport_uses(self) -> None:
        llm = MagicMock()
        llm.azure_endpoint = "https://example.openai.azure.com"
        llm.deployment_name = "gpt-5.6-luna"
        llm.openai_api_key = SecretStr("sk-secret")
        llm.openai_api_version = "2024-10-01-preview"

        t = AzureOpenAITransport.from_langchain_model(llm, model_name="gpt-5.6-luna")

        assert t.provider == "azure_direct"
        assert t.model_name == "gpt-5.6-luna"

    def test_non_azure_model_is_rejected_loudly(self) -> None:
        """Silently falling back would hide a misconfiguration behind LangChain."""
        llm = MagicMock()
        llm.azure_endpoint = None
        llm.deployment_name = None
        llm.openai_api_key = None
        llm.openai_api_version = None
        with pytest.raises(ValueError, match="azure_direct only supports"):
            AzureOpenAITransport.from_langchain_model(llm)


class TestStreamEvents:
    @pytest.mark.asyncio
    async def test_emits_the_same_event_types_as_the_langchain_path(self) -> None:
        t = _transport()
        _wire(t, [
            _chunk(reasoning="pondering"),
            _chunk(content="Hello"),
            _chunk(tool_calls=[_tc(0, id="call_1", name="final_answer", args='{"a"')]),
            _chunk(tool_calls=[_tc(0, args=':1}')]),
            _chunk(finish="tool_calls"),
        ])

        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        kinds = [type(e).__name__ for e in events]

        assert kinds == [
            "ThinkingDeltaEvent", "TextDeltaEvent",
            "ToolCallDeltaEvent", "ToolCallDeltaEvent", "StreamCompleteEvent",
        ]
        assert isinstance(events[-1], StreamCompleteEvent)

    @pytest.mark.asyncio
    async def test_tool_call_fragments_carry_the_index_live_streaming_keys_off(self) -> None:
        """agent/__init__ keys its final_answer extractor off `index`; without
        it the answer only reaches the user when the whole turn ends."""
        t = _transport()
        _wire(t, [
            _chunk(tool_calls=[_tc(0, id="c0", name="final_answer", args='{"x"')]),
            _chunk(tool_calls=[_tc(1, id="c1", name="search", args='{"q"')]),
            _chunk(tool_calls=[_tc(0, args=':1}')]),
            _chunk(finish="tool_calls"),
        ])

        deltas = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        frags = [e for e in deltas if isinstance(e, ToolCallDeltaEvent)]

        assert [f.index for f in frags] == [0, 1, 0]
        # name/id appear only on the opening fragment of each call, matching what
        # the LangChain transport passes through; the loop reads name on the
        # first delta per index only.
        assert [f.name for f in frags] == ["final_answer", "search", None]

    @pytest.mark.asyncio
    async def test_fragments_carrying_nothing_at_all_are_skipped(self) -> None:
        """A fragment with no arguments AND no name/id tells the loop nothing.

        The opening fragment does carry name/id with empty arguments, and that
        one must survive -- the agent reads `name` off the first delta to spot
        final_answer.
        """
        t = _transport()
        _wire(t, [
            _chunk(tool_calls=[_tc(0, id="c0", name="t", args=None)]),
            _chunk(tool_calls=[_tc(0, args="")]),
            _chunk(tool_calls=[_tc(0, args="{}")]),
            _chunk(finish="tool_calls"),
        ])

        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        frags = [e for e in events if isinstance(e, ToolCallDeltaEvent)]

        assert [(f.name, f.arguments_delta) for f in frags] == [("t", ""), (None, "{}")]

    @pytest.mark.asyncio
    async def test_fragments_reassemble_into_the_final_tool_call(self) -> None:
        t = _transport()
        _wire(t, [
            _chunk(tool_calls=[_tc(0, id="c1", name="final_answer", args='{"answer_markdown"')]),
            _chunk(tool_calls=[_tc(0, args=': "done"}')]),
            _chunk(finish="tool_calls"),
        ])

        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        calls = events[-1].response.message.tool_calls

        assert len(calls) == 1
        assert calls[0].name == "final_answer"
        assert calls[0].arguments == {"answer_markdown": "done"}

    @pytest.mark.asyncio
    async def test_text_only_response_still_terminates_with_one_complete(self) -> None:
        t = _transport()
        _wire(t, [_chunk(content="a"), _chunk(content="b"), _chunk(finish="stop")])

        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        assert [type(e).__name__ for e in events] == [
            "TextDeltaEvent", "TextDeltaEvent", "StreamCompleteEvent",
        ]
        # content is normalised to parts by AssistantMessage
        assert "".join(p.text for p in events[-1].response.message.content) == "ab"


class TestRequestShapeFallbacks:
    """Ported from LangChainTransport. These exist because the failures were
    hit in production, so losing them would be a real regression.

    They drive the retry through the transport's captured configuration rather
    than a per-call `effort`: LangChain ignores per-call effort because
    aimodels bakes the reasoning config into the model object, and this
    transport now matches that.
    """

    @pytest.mark.asyncio
    async def test_api_shape_conflict_retries_once_and_switches_endpoint(self) -> None:
        """Reasoning + tools rejected on Chat Completions means this deployment
        wants the Responses API for that combination."""
        t = _transport()
        t._defaults = RequestDefaults(reasoning_effort="low", model="gpt-4o")
        chat_calls: list = []
        resp_calls: list = []

        async def _chat(**kwargs):
            chat_calls.append(kwargs)
            raise RuntimeError("Please use /v1/responses instead")

        # Streaming Responses requests go through with_streaming_response, not
        # responses.create -- the raw-SSE path skips the SDK's per-event
        # validation, so this is where the retry now lands.
        def _resp(**kwargs):
            resp_calls.append(kwargs)
            return _FakeStreamingResponse([_DONE])

        t._client = MagicMock()
        t._client.chat.completions.create = AsyncMock(side_effect=_chat)
        t._client.responses.with_streaming_response.create = MagicMock(side_effect=_resp)

        [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        assert len(chat_calls) == 1, "first attempt on Chat Completions"
        assert chat_calls[0].get("reasoning_effort") == "low"
        assert len(resp_calls) == 1, "retry must go to the Responses API"
        assert resp_calls[0].get("reasoning") == {"effort": "low"}
        assert t._defaults.use_responses_api is True, "the working shape is pinned"

    @pytest.mark.asyncio
    async def test_reasoning_mandatory_conflict_bumps_off_a_disabled_value(self) -> None:
        t = _transport()
        t._defaults = RequestDefaults(reasoning_effort="none", model="gpt-4o")
        calls: list = []

        async def _create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("Reasoning is mandatory for this endpoint")
            return _scripted([_chunk(content="ok"), _chunk(finish="stop")])

        t._client = MagicMock()
        t._client.chat.completions.create = AsyncMock(side_effect=_create)
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        assert len(calls) == 2
        assert calls[1]["reasoning_effort"] == "low"
        assert isinstance(events[-1], StreamCompleteEvent)

    @pytest.mark.asyncio
    async def test_unrelated_errors_are_not_retried(self) -> None:
        t = _transport()
        t._defaults = RequestDefaults(reasoning_effort="low", model="gpt-4o")
        calls: list = []

        async def _create(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("rate limit exceeded")

        t._client = MagicMock()
        t._client.chat.completions.create = AsyncMock(side_effect=_create)
        with pytest.raises(Exception, match="rate limit"):
            [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_no_retry_once_events_have_been_emitted(self) -> None:
        """Re-opening a stream mid-flight would replay deltas to the client."""
        t = _transport()
        t._defaults = RequestDefaults(reasoning_effort="low", model="gpt-4o")
        calls: list = []

        class _Failing:
            def __aiter__(self):
                async def gen():
                    yield _chunk(content="partial")
                    raise RuntimeError("Please use /v1/responses instead")
                return gen()

        async def _create(**kwargs):
            calls.append(kwargs)
            return _Failing()

        t._client = MagicMock()
        t._client.chat.completions.create = AsyncMock(side_effect=_create)
        seen = []
        with pytest.raises(Exception, match="responses"):
            async for e in t.stream(messages=[UserMessage(content="hi")]):
                seen.append(e)

        assert len(calls) == 1
        assert [type(e).__name__ for e in seen] == ["TextDeltaEvent"]


class TestCompleteRetriesLikeStream:
    """complete() is used for non-streaming turns, planners and auto-compact.
    Its retry guard tested the returned dict for truthiness, but dropping
    reasoning_effort leaves an EMPTY dict -- which is exactly the retry case --
    so it never retried while stream() did."""

    @pytest.mark.asyncio
    async def test_api_shape_conflict_retries_once(self) -> None:
        t = _transport()
        t._defaults = RequestDefaults(reasoning_effort="low", model="gpt-4o")
        chat_calls: list = []

        async def _chat(**kwargs):
            chat_calls.append(kwargs)
            raise RuntimeError("Please use /v1/responses instead")

        async def _resp(**kwargs):
            return SimpleNamespace(output=[], usage=None, incomplete_details=None,
                                   status="completed")

        t._client = MagicMock()
        t._client.chat.completions.create = AsyncMock(side_effect=_chat)
        t._client.responses.create = AsyncMock(side_effect=_resp)

        result = await t.complete(messages=[UserMessage(content="hi")])

        assert len(chat_calls) == 1
        assert t._defaults.use_responses_api is True
        assert result is not None

    @pytest.mark.asyncio
    async def test_unrelated_error_still_raises_without_retrying(self) -> None:
        t = _transport()
        t._defaults = RequestDefaults(reasoning_effort="low", model="gpt-4o")
        calls: list = []

        async def _create(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("rate limit exceeded")

        t._client = MagicMock()
        t._client.chat.completions.create = AsyncMock(side_effect=_create)
        with pytest.raises(Exception, match="rate limit"):
            await t.complete(messages=[UserMessage(content="hi")])

        assert len(calls) == 1


class TestToolCallOpeningFragment:
    """Azure opens a tool call with a metadata-only chunk (name + id, empty
    arguments) -- verified against the live service. The agent decides whether a
    call is final_answer from `name` on the first delta it sees, so dropping
    that chunk meant answers only appeared once the turn ended."""

    @pytest.mark.asyncio
    async def test_metadata_only_opening_chunk_is_emitted(self) -> None:
        t = _transport()
        _wire(t, [
            _chunk(tool_calls=[_tc(0, id="c1", name="final_answer", args="")]),
            _chunk(tool_calls=[_tc(0, args='{"answer_markdown": "hi"}')]),
            _chunk(finish="tool_calls"),
        ])

        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        frags = [e for e in events if isinstance(e, ToolCallDeltaEvent)]

        assert frags[0].name == "final_answer", "the agent needs the name on the first delta"
        assert frags[0].arguments_delta == ""
        assert events[-1].response.message.tool_calls[0].arguments == {"answer_markdown": "hi"}

    @pytest.mark.asyncio
    async def test_wholly_empty_fragments_are_still_skipped(self) -> None:
        t = _transport()
        _wire(t, [
            _chunk(tool_calls=[_tc(0, id="c1", name="t", args=None)]),
            _chunk(tool_calls=[_tc(0, args=None)]),
            _chunk(tool_calls=[_tc(0, args="{}")]),
            _chunk(finish="tool_calls"),
        ])

        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        frags = [e for e in events if isinstance(e, ToolCallDeltaEvent)]

        # opener carries name/id so it is emitted; the bare empty one is not
        assert [f.name for f in frags] == ["t", None]


class TestConfigurationCapture:
    """The whole configuration comes off the LangChain model, not just the
    credentials. Reading only the credentials is what made this transport send
    Chat Completions with no reasoning while the LangChain arm used the
    Responses API at effort=high."""

    @staticmethod
    def _llm(**over):
        llm = MagicMock()
        llm.azure_endpoint = "https://e.openai.azure.com"
        llm.deployment_name = over.get("deployment", "my-deployment")
        llm.openai_api_key = SecretStr("sk")
        llm.openai_api_version = "2025-04-01-preview"
        llm.temperature = over.get("temperature", 1)
        llm.reasoning = over.get("reasoning", {"effort": "high"})
        llm.reasoning_effort = over.get("reasoning_effort", None)
        llm.use_responses_api = over.get("use_responses_api", True)
        llm.request_timeout = over.get("timeout", 360.0)
        llm.max_retries = over.get("max_retries", 2)
        return llm

    def test_reasoning_and_endpoint_are_carried(self) -> None:
        t = AzureOpenAITransport.from_langchain_model(self._llm(), model_name="gpt-5.6-luna")
        assert t._wants_responses() is True
        assert t._default_request_kwargs()["reasoning"] == {"effort": "high"}

    def test_timeout_is_carried_to_the_client(self) -> None:
        """LangChain pins DEFAULT_LLM_TIMEOUT; unset, the SDK waits far longer."""
        t = AzureOpenAITransport.from_langchain_model(self._llm(), model_name="m")
        assert t._client.timeout == 360.0

    def test_temperature_dropped_for_gpt5_reasoning_on_responses(self) -> None:
        """langchain_openai strips it before POSTing, so sending it would both
        differ from the LangChain arm and 400 on the deployment."""
        t = AzureOpenAITransport.from_langchain_model(self._llm(), model_name="gpt-5.6-luna")
        assert "temperature" not in t._default_request_kwargs()

    def test_temperature_kept_when_reasoning_is_off(self) -> None:
        t = AzureOpenAITransport.from_langchain_model(
            self._llm(reasoning={"effort": "none"}), model_name="gpt-5.6-luna",
        )
        assert t._default_request_kwargs()["temperature"] == 1

    def test_temperature_kept_for_gpt5_chat(self) -> None:
        t = AzureOpenAITransport.from_langchain_model(self._llm(), model_name="gpt-5-chat")
        assert t._default_request_kwargs()["temperature"] == 1

    def test_chat_completions_model_uses_the_other_spelling(self) -> None:
        t = AzureOpenAITransport.from_langchain_model(
            self._llm(use_responses_api=False, reasoning=None, reasoning_effort="medium"),
            model_name="gpt-4o",
        )
        assert t._wants_responses() is False
        assert t._default_request_kwargs()["reasoning_effort"] == "medium"

    def test_responses_requests_carry_the_deployment_not_the_model(self) -> None:
        """Azure only puts /deployments/{name}/ in the path for Chat
        Completions, so on Responses the deployment must travel in the body --
        sending the model name there is a DeploymentNotFound 404."""
        t = AzureOpenAITransport.from_langchain_model(
            self._llm(deployment="my-deployment"), model_name="gpt-5.6-luna",
        )
        assert t._responses_model_id() == "my-deployment"
        assert t.model_name == "gpt-5.6-luna"

    @pytest.mark.asyncio
    async def test_a_reasoning_model_actually_reaches_the_responses_endpoint(self) -> None:
        """The whole point: this deployment must not silently run without
        reasoning on Chat Completions."""
        t = AzureOpenAITransport.from_langchain_model(self._llm(), model_name="gpt-5.6-luna")
        captured: dict = {}

        def _resp(**kwargs):
            captured.update(kwargs)
            return _FakeStreamingResponse([_DONE])

        t._client = MagicMock()
        t._client.responses.with_streaming_response.create = MagicMock(side_effect=_resp)
        t._client.chat.completions.create = AsyncMock(
            side_effect=AssertionError("must not use Chat Completions")
        )

        [e async for e in t.stream(messages=[UserMessage(content="hi")], system="S")]

        assert captured["model"] == "my-deployment"
        assert captured["reasoning"] == {"effort": "high"}
        assert "stream_options" not in captured, "Chat-Completions-only parameter"
        assert captured["input"][0] == {"type": "message", "role": "system", "content": "S"}


def _sse(events: list[dict], chunk_size: int | None = None) -> list[bytes]:
    """Encode events as an SSE byte stream, optionally split at arbitrary
    boundaries so the framing is exercised the way the network delivers it."""
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    raw = body.encode()
    if chunk_size is None:
        return [raw]
    return [raw[i:i + chunk_size] for i in range(0, len(raw), chunk_size)]


class _FakeStreamingResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> "_FakeStreamingResponse":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def iter_bytes(self):
        for c in self._chunks:
            yield c


def _responses_transport(chunks: list[bytes]) -> AzureOpenAITransport:
    t = _transport()
    t._defaults = RequestDefaults(reasoning={"effort": "high"}, use_responses_api=True,
                                  model="gpt-5.6-luna")
    t._client = MagicMock()
    t._client.responses.with_streaming_response.create = MagicMock(
        return_value=_FakeStreamingResponse(chunks)
    )
    return t


_STREAM = [
    {"type": "response.output_text.delta", "delta": "Hel", "output_index": 0},
    {"type": "response.output_text.delta", "delta": "lo", "output_index": 0},
    {"type": "response.output_item.added", "output_index": 1,
     "item": {"type": "function_call", "name": "search", "call_id": "call_1"}},
    {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"q":'},
    {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '"x"}'},
    {"type": "response.completed", "response": {
        "output": [{"type": "function_call", "name": "search",
                    "arguments": '{"q": "x"}', "call_id": "call_1"}],
        "usage": {"input_tokens": 50, "output_tokens": 9,
                  "input_tokens_details": {"cached_tokens": 32}},
        "incomplete_details": None, "status": "completed",
    }},
]


class TestRawResponsesStream:
    """The Responses stream is decoded from raw SSE, skipping the SDK's
    per-event pydantic validation. These assert the events reaching the agent
    loop are unchanged by that."""

    @pytest.mark.asyncio
    async def test_full_event_sequence(self) -> None:
        t = _responses_transport(_sse(_STREAM))
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]

        assert [e.delta for e in events if isinstance(e, TextDeltaEvent)] == ["Hel", "lo"]
        tool_deltas = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
        # the opener carries the name, which the agent loop reads off the FIRST
        # delta for an index to recognise final_answer
        assert (tool_deltas[0].index, tool_deltas[0].name, tool_deltas[0].arguments_delta) == (
            1, "search", "")
        assert "".join(d.arguments_delta for d in tool_deltas) == '{"q":"x"}'

        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.tool_calls[0].arguments == {"q": "x"}
        assert final.message.tool_calls[0].id == "call_1"
        assert final.stop_reason == StopReason.TOOL_USE
        assert (final.usage.input_tokens, final.usage.output_tokens) == (50, 9)
        assert final.usage.cache_read_tokens == 32

    @pytest.mark.asyncio
    async def test_events_split_across_byte_chunks_still_decode(self) -> None:
        """The network does not deliver one event per read; a 7-byte chunking
        puts every boundary mid-event."""
        t = _responses_transport(_sse(_STREAM, chunk_size=7))
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        assert "".join(e.delta for e in events if isinstance(e, TextDeltaEvent)) == "Hello"
        final = next(e for e in events if isinstance(e, StreamCompleteEvent)).response
        assert final.message.tool_calls[0].arguments == {"q": "x"}

    @pytest.mark.asyncio
    async def test_in_stream_error_payload_raises(self) -> None:
        """Without this the stream would end quietly and the turn would look
        like a short answer instead of a failure."""
        chunks = _sse([
            {"type": "response.output_text.delta", "delta": "par", "output_index": 0},
            {"error": {"message": "upstream exploded"}},
        ])
        t = _responses_transport(chunks)
        with pytest.raises(Exception, match="upstream exploded"):
            [e async for e in t.stream(messages=[UserMessage(content="hi")])]

    @pytest.mark.asyncio
    async def test_done_sentinel_ends_the_stream(self) -> None:
        t = _responses_transport(_sse(_STREAM[:2]))
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        assert isinstance(events[-1], StreamCompleteEvent)

    @pytest.mark.asyncio
    async def test_unknown_event_types_are_ignored(self) -> None:
        """53 event types exist and we act on 10; one Azure adds later must not
        crash the loop."""
        t = _responses_transport(_sse([
            {"type": "response.audio.delta", "delta": "xx"},
            {"type": "response.some.future.thing", "whatever": 1},
            *_STREAM[:2],
        ]))
        events = [e async for e in t.stream(messages=[UserMessage(content="hi")])]
        assert "".join(e.delta for e in events if isinstance(e, TextDeltaEvent)) == "Hello"
