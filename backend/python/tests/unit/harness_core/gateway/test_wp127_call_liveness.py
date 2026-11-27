"""WP-127: a model call stays alive while the model works, and ends when its caller is gone (ADR-GW-01).

Found in a manual test: two Claude Code children each lost about 20 of their 30 working minutes
the same way. The model (gpt-6.1-sol behind ``/v1/messages``) spent minutes composing one large tool call; the
Messages wire sends a tool call whole from the final response, so nothing reached the engine meanwhile, and the
egress proxy's upstream read cut (``UPSTREAM_READ_TIMEOUT_S``, 300 s of silence) dropped the stream. Claude Code
asked again (once without streaming), the model wrote the same call again, and so on until the wall limit. Each
dropped call kept running at the provider until it finished (``model_gateway.client_gone`` only then), and the last
two, non-streamed, ran 18 minutes through the SDK's retries, past the run's end.

The rig stands the egress hop in with the same aiohttp read timeout, scaled down. Fake providers only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any, override

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.gemini_wire import GEMINI_WIRE
from harness_core.gateway.messages_wire import MESSAGES_WIRE
from harness_core.gateway.model_server import (
    HEARTBEAT_S,
    ModelGatewayServer,
    create_model_gateway_app,
)
from harness_core.gateway.responses_wire import RESPONSES_WIRE
from harness_core.kernel.core.messages import AssistantMessage, ToolCall
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.sandbox.egress.server import UPSTREAM_READ_TIMEOUT_S
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    reply_events,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema

# The egress hop's silence cut and the gateway's heartbeat, scaled from 300 s and 15 s.
HOP_SILENCE_S = 0.6
BEAT_S = 0.1
# Long enough that, unbeaten, the hop always cuts first.
COMPOSE_S = 1.5
CONTENT = "x" * 4_000
TOOL = {"name": "Write", "description": "Write a file", "input_schema": {"type": "object"}}


def _tool_call_end() -> StreamCompleteEvent:
    message = AssistantMessage(
        tool_calls=[ToolCall(id="toolu_big", name="Write", arguments={"file_path": "report.md", "content": CONTENT})]
    )
    return StreamCompleteEvent(
        response=ModelResponse(
            message=message, usage=TokenUsage(input_tokens=96_495, output_tokens=27_612), stop_reason=StopReason.TOOL_USE
        )
    )


class ComposingTransport(RecordedTransport):
    """A provider writing one large tool call slowly: hidden reasoning, then argument deltas, ``pause_s`` apart, none
    of which the Messages wire frames. A non-streamed call just takes ``COMPOSE_S``. Notes when either is cut."""

    def __init__(self, *, pause_s: float = COMPOSE_S / 4, steps: int = 4, end: StreamCompleteEvent | None = None) -> None:
        super().__init__([end or _tool_call_end()])
        self.pause_s = pause_s
        self.steps = steps
        self.started = asyncio.Event()
        self.cut = asyncio.Event()
        self.finished = asyncio.Event()

    @override
    async def stream(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._record(messages, tools, system, effort, stream=True, limit=max_output_tokens, tool_choice=tool_choice)
        self.started.set()
        try:
            yield ThinkingDeltaEvent(delta="planning the file")
            for step in range(self.steps):
                await asyncio.sleep(self.pause_s)
                yield ToolCallDeltaEvent(
                    index=0, id="toolu_big" if step == 0 else None, name="Write" if step == 0 else None,
                    arguments_delta=CONTENT[: 10],
                )
            self.finished.set()
            yield self.events[-1]
        except (asyncio.CancelledError, GeneratorExit):
            self.cut.set()
            raise

    @override
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        self._record(messages, tools, system, effort, stream=False, limit=max_output_tokens, tool_choice=tool_choice)
        self.started.set()
        try:
            await asyncio.sleep(self.pause_s * self.steps)
        except asyncio.CancelledError:
            self.cut.set()
            raise
        self.finished.set()
        final = self.events[-1]
        assert isinstance(final, StreamCompleteEvent)
        return final.response


class SilentTransport(ComposingTransport):
    """A provider that streams nothing any wire frames (an empty first delta), then thinks unseen, then answers."""

    @override
    async def stream(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._record(messages, tools, system, effort, stream=True, limit=max_output_tokens, tool_choice=tool_choice)
        self.started.set()
        try:
            yield TextDeltaEvent(delta="")
            await asyncio.sleep(self.pause_s * self.steps)
            self.finished.set()
            yield self.events[-1]
        except (asyncio.CancelledError, GeneratorExit):
            self.cut.set()
            raise


def _hop() -> aiohttp.ClientSession:
    """The egress proxy's upstream session (``upstream_session``), its silence cut scaled down."""
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=HOP_SILENCE_S))


def _messages(*, stream: bool = True) -> dict[str, Any]:
    return {
        "model": ENGINE_MODEL_ID, "max_tokens": 32_000, "stream": stream, "tools": [TOOL],
        "messages": [{"role": "user", "content": "write the report"}],
    }


_ASKS: dict[str, tuple[str, dict[str, Any]]] = {
    "messages": ("/v1/messages", _messages()),
    "chat": ("/v1/chat/completions", {"model": ENGINE_MODEL_ID, "stream": True, "messages": [{"role": "user", "content": "hi"}]}),
    "responses": ("/v1/responses", {"model": ENGINE_MODEL_ID, "stream": True, "input": "hi"}),
    "gemini": (
        f"/v1beta/models/{ENGINE_MODEL_ID}:streamGenerateContent?alt=sse",
        {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    ),
}


def _lines(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, str]]:
    found = [r.getMessage() for r in caplog.records if f"{event} " in r.getMessage()]
    return [dict(re.findall(r"(\w+)=(\S+)", line)) for line in found]


@contextlib.asynccontextmanager
async def _served(rig: GatewayRig) -> AsyncIterator[str]:
    server = TestServer(create_model_gateway_app(rig.gateway, heartbeat_s=BEAT_S), host="127.0.0.1")
    await server.start_server()
    try:
        yield str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


async def test_wp127_live_1_a_tool_call_composed_longer_than_the_hops_silence_cut_arrives_whole() -> None:
    """A recorded call: 27,612 output tokens over 8 minutes, cut by the egress at 5. Here the hop keeps
    the stream because the gateway beats while the provider composes, and Claude Code's SDK reads the call."""
    transport = ComposingTransport()
    rig = GatewayRig(transport=transport)
    token = rig.token(rig.pin(transport=transport))
    async with _served(rig) as url:
        async with _hop() as hop, hop.post(
            f"{url}/v1/messages", json=_messages(), headers={"Authorization": f"Bearer {token}"}
        ) as response:
            raw = (await response.read()).decode()
    frames = [f for f in raw.split("\n\n") if f.strip()]
    pings = [f for f in frames if f.startswith("event: ping\n")]
    assert pings and all(json.loads(f.split("\n")[1].removeprefix("data: ")) == {"type": "ping"} for f in pings)
    assert frames[-1].startswith("event: message_stop"), "the stream ran to its end"
    started = [json.loads(f.split("\n")[1].removeprefix("data: ")) for f in frames if f.startswith("event: content_block_start")]
    assert [s["content_block"]["name"] for s in started if s["content_block"]["type"] == "tool_use"] == ["Write"]
    assert len(transport.calls) == 1, "asked once: nothing repeated"


@pytest.mark.usefixtures("_no_ambient_key")
async def test_wp127_live_2_the_anthropic_sdk_reads_a_stream_with_pings() -> None:
    import anthropic

    transport = ComposingTransport(pause_s=BEAT_S * 2, steps=3)
    rig = GatewayRig(transport=transport)
    token = rig.token(rig.pin(transport=transport))
    async with _served(rig) as url:
        client = anthropic.AsyncAnthropic(auth_token=token, base_url=url, max_retries=0)
        async with client.messages.stream(
            model=ENGINE_MODEL_ID, max_tokens=32_000, tools=[TOOL],  # type: ignore[list-item]
            messages=[{"role": "user", "content": "write the report"}],
        ) as stream:
            final = await stream.get_final_message()
        await client.close()
    calls = [b for b in final.content if b.type == "tool_use"]
    assert [(c.name, c.input) for c in calls] == [("Write", {"file_path": "report.md", "content": CONTENT})]
    assert final.stop_reason == "tool_use"


@pytest.mark.parametrize("api", sorted(_ASKS))
async def test_wp127_live_3_every_sse_wire_an_engine_reads_beats_through_silence(api: str) -> None:
    """Codex (Responses), the Chat Completions engines and Claude Code (Messages): a model thinking unseen for
    longer than the hop's cut still answers. A comment (``:``) is what every SSE reader skips; Messages has ``ping``."""
    transport = SilentTransport(end=reply_events("done"))
    rig = GatewayRig(transport=transport)
    token = rig.token(rig.pin(transport=transport))
    path, body = _ASKS[api]
    async with _served(rig) as url:
        async with _hop() as hop, hop.post(f"{url}{path}", json=body, headers={"Authorization": f"Bearer {token}"}) as response:
            raw = (await response.read()).decode()
    assert "done" in raw and transport.finished.is_set()


@pytest.mark.usefixtures("_no_ambient_key")
async def test_wp127_live_4_the_openai_sdk_skips_the_keepalive_comment() -> None:
    import openai

    transport = SilentTransport(pause_s=BEAT_S * 2, steps=2, end=reply_events("done"))
    rig = GatewayRig(transport=transport)
    token = rig.token(rig.pin(transport=transport))
    async with _served(rig) as url:
        client = openai.AsyncOpenAI(api_key=token, base_url=f"{url}/v1", max_retries=0)
        chat = await client.chat.completions.create(
            model=ENGINE_MODEL_ID, messages=[{"role": "user", "content": "hi"}], stream=True
        )
        text = "".join([c.choices[0].delta.content or "" async for c in chat if c.choices])
        responses = await client.responses.create(model=ENGINE_MODEL_ID, input="hi", stream=True)
        kinds = [event.type async for event in responses]
        await client.close()
    assert text == "done" and kinds[-1] == "response.completed"


def test_wp127_live_5_each_wire_names_its_keepalive() -> None:
    assert MESSAGES_WIRE.keepalive == b'event: ping\ndata: {"type": "ping"}\n\n'
    assert CHAT_WIRE.keepalive == RESPONSES_WIRE.keepalive == b": keepalive\n\n"
    assert GEMINI_WIRE.keepalive == b"data: {}\r\n\r\n"


# The stream reader of the ``@google/genai`` that Gemini CLI 0.63's bundle carries (``pxh-gemini:0.63.0``,
# ``chunk-IVQKNOQE.js``), read from the image: an event is taken only from the start of the buffer.
_GENAI_LINE = re.compile(r"^\s*data: (.*)(?:\n\n|\r\r|\r\n\r\n)")


def _genai_chunks(raw: str) -> tuple[list[dict[str, Any]], str]:
    """The chunks that reader yields from ``raw``, and what it leaves in its buffer (it waits for more there)."""
    chunks: list[dict[str, Any]] = []
    while (match := _GENAI_LINE.match(raw)) is not None:
        chunks.append(json.loads(match.group(1)))
        raw = raw[match.end() :]
    return chunks, raw


def test_wp127_fu1_gemini_clis_reader_takes_the_keepalive_and_a_comment_would_stall_it() -> None:
    """WP127-FU-1: a comment at the head of that reader's buffer is never matched, so every chunk after it waits; an
    empty response (``data: {}``) is matched and holds no candidate, so Gemini CLI's turn adds nothing for it (its
    ``processStreamResponse`` keeps a chunk's parts only when it has a candidate with content, and its usage only when
    it carries ``usageMetadata``)."""
    answer = 'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"done"}]}}]}\r\n\r\n'
    beat = GEMINI_WIRE.keepalive.decode()  # pyright: ignore[reportOptionalMemberAccess]
    chunks, rest = _genai_chunks(beat + beat + answer)
    assert rest == "" and chunks[:2] == [{}, {}] and chunks[2]["candidates"][0]["content"]["parts"] == [{"text": "done"}]
    stalled, left = _genai_chunks(": keepalive\n\n" + answer)
    assert stalled == [] and left.endswith(answer), "why Gemini never got the other wires' comment"


@pytest.mark.usefixtures("_no_ambient_key")
async def test_wp127_fu1_the_google_genai_sdk_reads_a_gemini_stream_through_silence() -> None:
    """A Gemini stream whose model is silent for longer than the hop's cut answers, read by Google's own SDK."""
    from google import genai
    from google.genai import types

    transport = SilentTransport(pause_s=BEAT_S * 2, steps=2, end=reply_events("done"))
    rig = GatewayRig(transport=transport)
    token = rig.token(rig.pin(transport=transport))
    async with _served(rig) as url:
        client = genai.Client(
            api_key="unused",
            # As Gemini CLI on a gateway: its bearer, and an empty API key header.
            http_options=types.HttpOptions(
                base_url=url, headers={"Authorization": f"Bearer {token}", "x-goog-api-key": ""}
            ),
        )
        stream = await client.aio.models.generate_content_stream(model=ENGINE_MODEL_ID, contents="hi")
        text = "".join([chunk.text or "" async for chunk in stream])
    assert text == "done" and transport.finished.is_set()


def test_wp127_live_6_the_heartbeat_is_well_inside_the_hops_silence_cut() -> None:
    assert HEARTBEAT_S * 4 <= UPSTREAM_READ_TIMEOUT_S


@contextlib.asynccontextmanager
async def _production(rig: GatewayRig) -> AsyncIterator[str]:
    """The gateway as ``model_gateway_main`` serves it: ``ModelGatewayServer``, not aiohttp's test server (which
    cancels a handler whose client left on its own, and so hid that production did not)."""
    server = ModelGatewayServer(rig.gateway, heartbeat_s=BEAT_S)
    port = await server.start(host="127.0.0.1", port=0)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        await server.stop()


async def test_wp127_gone_1_a_non_streamed_call_stops_at_the_provider_when_its_caller_leaves(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two recorded calls: Claude Code's non-streamed fallbacks, dropped by the egress at 5 minutes,
    ran 18 minutes each at the provider and ended 7 and 12 minutes after their run. Now the call ends with its caller
    and is charged as a cut call."""
    caplog.set_level(logging.INFO)
    transport = ComposingTransport(pause_s=5.0)
    rig = GatewayRig(transport=transport)
    token = rig.token(rig.pin(transport=transport))
    async with _production(rig) as url:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=HOP_SILENCE_S)) as hop:
            with pytest.raises(TimeoutError):
                async with hop.post(f"{url}/v1/messages", json=_messages(stream=False), headers={"Authorization": f"Bearer {token}"}):
                    pass
        await asyncio.wait_for(transport.cut.wait(), 3.0)
        for _ in range(50):
            if _lines(caplog, "model_gateway.call"):
                break
            await asyncio.sleep(0.02)
    assert not transport.finished.is_set()
    (line,) = _lines(caplog, "model_gateway.call")
    assert (line["status"], line["estimated"]) == ("interrupted", "True")


async def test_wp127_gone_2_a_silent_stream_stops_at_the_provider_when_its_caller_leaves() -> None:
    """A recorded call: the egress dropped the stream at 09:55:50; the gateway learned it at 09:58:36, when the
    provider had finished (and billed) the whole answer. Now the provider's stream is closed when the caller goes."""
    transport = SilentTransport(pause_s=5.0)
    rig = GatewayRig(transport=transport)
    token = rig.token(rig.pin(transport=transport))
    async with _production(rig) as url:
        async with aiohttp.ClientSession() as hop:
            response = await hop.post(f"{url}/v1/messages", json=_messages(), headers={"Authorization": f"Bearer {token}"})
            _ = await response.content.readline()
            response.close()
        await asyncio.wait_for(transport.cut.wait(), 3.0)
    assert not transport.finished.is_set()


async def test_wp127_count_1_claude_codes_token_count_is_answered_not_refused() -> None:
    """Every ``count_tokens`` Claude Code sent carried ``anthropic-beta: token-counting-2024-11-01`` (the
    endpoint's original beta, now general) and was refused as ``unsupported_parameter``, reported each time."""
    rig = GatewayRig()
    token = rig.token(rig.pin())
    async with _served(rig) as url:
        async with aiohttp.ClientSession() as client, client.post(
            f"{url}/v1/messages/count_tokens",
            json={"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hello " * 40}]},
            headers={"Authorization": f"Bearer {token}", "anthropic-beta": "token-counting-2024-11-01"},
        ) as response:
            status, body = response.status, await response.json()
    assert status == 200 and body["input_tokens"] > 40


@pytest.fixture
def _no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
