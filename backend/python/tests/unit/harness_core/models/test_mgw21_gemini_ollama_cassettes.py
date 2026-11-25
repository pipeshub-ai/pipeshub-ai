"""MGW-21: Gemini and Ollama parse their providers' recorded wire responses like the other transports do.

The cassettes under ``cassettes/`` follow each provider's documented wire format (Gemini ``generateContent`` JSON and
``streamGenerateContent?alt=sse``; Ollama ``/api/chat`` JSON and NDJSON). The transport runs its real request path;
only the socket is replaced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import pytest

from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import Message, UserMessage
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    StreamEvent,
    TextDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.models.transports.gemini import thought_signatures
from tests.unit.harness_core.models.transport_wire import (
    Handler,
    cassette,
    cassette_text,
    gemini_over,
    ollama_over,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.models.transports.base import LLMTransport

_MESSAGES: list[Message] = [UserMessage(content="What is the weather in Paris?")]
_WEATHER = ToolSchema(
    name="get_weather",
    description="Current weather for a city.",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}, "unit": {"type": "string"}}},
)


@dataclass(frozen=True)
class Provider:
    name: str
    build: Callable[[Handler], LLMTransport]
    stream_suffix: str
    stream_media_type: str
    is_stream: Callable[[httpx.Request], bool]
    declared_tools: Callable[[httpx.Request], list[str]]
    text_usage: TokenUsage
    tool_usage: TokenUsage
    stream_usage: TokenUsage
    stream_tool_usage: TokenUsage
    errors: tuple[tuple[str, int, bool], ...]


def _gemini_tools(request: httpx.Request) -> list[str]:
    body = json.loads(request.content)
    return [d["name"] for tool in body.get("tools", []) for d in tool.get("functionDeclarations", [])]


def _ollama_tools(request: httpx.Request) -> list[str]:
    return [tool["function"]["name"] for tool in json.loads(request.content).get("tools", [])]


_GEMINI = Provider(
    name="gemini",
    build=gemini_over,
    stream_suffix="sse",
    stream_media_type="text/event-stream",
    is_stream=lambda r: r.url.path.endswith(":streamGenerateContent") and r.url.params.get("alt") == "sse",
    declared_tools=_gemini_tools,
    # thoughtsTokenCount is billed output that never shows as text.
    text_usage=TokenUsage(input_tokens=11, output_tokens=31, cache_read_tokens=4, reasoning_tokens=24),
    tool_usage=TokenUsage(input_tokens=30, output_tokens=12),
    stream_usage=TokenUsage(input_tokens=9, output_tokens=10, reasoning_tokens=6),
    stream_tool_usage=TokenUsage(input_tokens=30, output_tokens=8),
    errors=(("error_429.json", 429, True), ("error_400.json", 400, False)),
)
_OLLAMA = Provider(
    name="ollama",
    build=ollama_over,
    stream_suffix="ndjson",
    stream_media_type="application/x-ndjson",
    is_stream=lambda r: r.url.path == "/api/chat" and json.loads(r.content)["stream"] is True,
    declared_tools=_ollama_tools,
    text_usage=TokenUsage(input_tokens=26, output_tokens=9),
    tool_usage=TokenUsage(input_tokens=140, output_tokens=22),
    stream_usage=TokenUsage(input_tokens=18, output_tokens=4),
    stream_tool_usage=TokenUsage(input_tokens=140, output_tokens=19),
    errors=(("error_503.json", 503, True), ("error_404.json", 404, False)),
)
_PROVIDERS = pytest.mark.parametrize("provider", [_GEMINI, _OLLAMA], ids=lambda p: p.name)


class _Replay:
    """Answers every request with one cassette and keeps what was sent."""

    def __init__(self, provider: Provider, name: str, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._provider, self._name, self._status = provider, name, status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._name.endswith(".json"):
            return httpx.Response(self._status, json=cassette(self._provider.name, self._name))
        return httpx.Response(
            self._status,
            text=cassette_text(self._provider.name, self._name),
            headers={"content-type": self._provider.stream_media_type},
        )


async def _streamed(transport: LLMTransport, tools: list[ToolSchema] | None = None) -> list[StreamEvent]:
    return [event async for event in transport.stream(_MESSAGES, tools=tools)]


def _final(events: list[StreamEvent]) -> ModelResponse:
    completes = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(completes) == 1 and events[-1] is completes[0], "exactly one completion, after every delta"
    return completes[0].response


@_PROVIDERS
async def test_mgw21_completion_returns_the_text_and_its_usage(provider: Provider) -> None:
    replay = _Replay(provider, "complete_text.json")

    response = await provider.build(replay).complete(_MESSAGES)

    assert response.message.text == "Paris is the capital of France."
    assert response.stop_reason == StopReason.END_TURN and not response.message.tool_calls
    assert response.usage == provider.text_usage
    assert [provider.is_stream(r) for r in replay.requests] == [False]


@_PROVIDERS
async def test_mgw21_completion_returns_the_tool_call(provider: Provider) -> None:
    replay = _Replay(provider, "complete_tool_call.json")

    response = await provider.build(replay).complete(_MESSAGES, tools=[_WEATHER])

    calls = response.message.tool_calls or []
    assert [(c.name, c.arguments) for c in calls] == [("get_weather", {"city": "Paris", "unit": "celsius"})]
    assert calls[0].id
    assert response.stop_reason == StopReason.TOOL_USE
    assert response.usage == provider.tool_usage
    assert provider.declared_tools(replay.requests[0]) == ["get_weather"]


@_PROVIDERS
async def test_mgw21_stream_yields_text_deltas_then_usage_at_the_end(provider: Provider) -> None:
    replay = _Replay(provider, f"stream_text.{provider.stream_suffix}")

    events = await _streamed(provider.build(replay))

    assert [e.delta for e in events if isinstance(e, TextDeltaEvent)] == ["Hello", ", world", "!"]
    final = _final(events)
    assert final.message.text == "Hello, world!"
    assert final.stop_reason == StopReason.END_TURN
    assert final.usage == provider.stream_usage
    assert [provider.is_stream(r) for r in replay.requests] == [True]


@_PROVIDERS
async def test_mgw21_stream_assembles_the_tool_call(provider: Provider) -> None:
    replay = _Replay(provider, f"stream_tool_call.{provider.stream_suffix}")

    events = await _streamed(provider.build(replay), tools=[_WEATHER])

    final = _final(events)
    calls = final.message.tool_calls or []
    assert [(c.name, c.arguments) for c in calls] == [("get_weather", {"city": "Paris"})]
    assert final.stop_reason == StopReason.TOOL_USE
    assert final.usage == provider.stream_tool_usage
    deltas = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
    assert all(d.id == calls[0].id and d.name == "get_weather" for d in deltas)


@_PROVIDERS
@pytest.mark.parametrize("streamed", [False, True], ids=["complete", "stream"])
async def test_mgw21_error_response_is_a_classified_transport_error(provider: Provider, streamed: bool) -> None:
    for name, status, retryable in provider.errors:
        transport = provider.build(_Replay(provider, name, status))

        with pytest.raises(TransportError) as raised:
            if streamed:
                _ = await _streamed(transport)
            else:
                _ = await transport.complete(_MESSAGES)

        assert (raised.value.status_code, raised.value.retryable) == (status, retryable), name


async def test_mgw21_gemini_tool_call_keeps_its_thought_signature() -> None:
    complete = await gemini_over(_Replay(_GEMINI, "complete_tool_call.json")).complete(_MESSAGES, tools=[_WEATHER])
    streamed = _final(await _streamed(gemini_over(_Replay(_GEMINI, "stream_tool_call.sse")), tools=[_WEATHER]))

    for response, signature in ((complete, b"sig-mgw21"), (streamed, b"sig-stream")):
        call_id = (response.message.tool_calls or [])[0].id
        assert thought_signatures(response.message) == {call_id: signature}
