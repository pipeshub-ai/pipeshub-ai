"""P4.3 BR-2..BR-4, BR-6: the Bedrock Converse transport against a fake runtime that checks the whole SigV4 signature."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from aiohttp.test_utils import TestServer

from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ProviderState,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import StopReason
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.models.eventstream import EventStreamDecoder, EventStreamError
from harness_core.models.provider_state import BEDROCK_CONVERSE
from harness_core.models.sigv4 import AwsCredentials
from harness_core.models.transports.bedrock_converse import BedrockConverseTransport
from harness_core.testing.cloud_providers import (
    ConverseReply,
    FakeBedrock,
    converse_body,
    eventstream_frame,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

KEY_ID = "AKIA" + "P43TESTKEYID0000"
SECRET = "p43/test+secret" + "S" * 25
REGION = "eu-west-1"
MODEL = "us.meta.llama3-2-90b-instruct-v1:0"
NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)
REASONING = {"reasoningContent": {"reasoningText": {"text": "check first", "signature": "sig=="}}}


@pytest.fixture
async def runtime() -> AsyncIterator[tuple[FakeBedrock, str]]:
    fake = FakeBedrock(KEY_ID, REGION, secret_access_key=SECRET)
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


def _transport(url: str, *, secret: str = SECRET, keep: bool = False) -> BedrockConverseTransport:
    transport = BedrockConverseTransport(
        region=REGION, credentials=AwsCredentials(KEY_ID, secret), model=MODEL, base_url=url, clock=lambda: NOW
    )
    if keep:
        assert transport.keep_provider_state()
    return transport


TOOLS = [ToolSchema(name="lookup", description="Look up", input_schema={"type": "object", "properties": {"q": {"type": "string"}}})]


def _history() -> list[object]:
    return [
        UserMessage(content="find it"),
        AssistantMessage(
            content=[TextPart(text="looking")],
            tool_calls=[ToolCall(id="tooluse_1", name="lookup", arguments={"q": "a"}), ToolCall(id="tooluse_2", name="lookup", arguments={"q": "b"})],
            provider_state=ProviderState(wire=BEDROCK_CONVERSE, blocks=[REASONING]),
        ),
        ToolMessage(content="A", tool_call_id="tooluse_1"),
        ToolMessage(content="boom", tool_call_id="tooluse_2", is_error=True),
        UserMessage(content="and then?"),
    ]


async def test_br2_complete_is_signed_shaped_and_parsed(runtime: tuple[FakeBedrock, str]) -> None:
    fake, url = runtime
    fake.converse.append(
        ConverseReply(
            body=converse_body(
                stop="tool_use",
                content=[REASONING, {"text": "filing"}, {"toolUse": {"toolUseId": "tooluse_3", "name": "lookup", "input": {"q": "c"}}}],
            )
        )
    )
    response = await _transport(url, keep=True).complete(_history(), tools=TOOLS, system="be brief")  # type: ignore[arg-type]
    seen = fake.seen[-1]
    assert seen.path == f"/model/{MODEL}/converse"
    assert seen.headers["authorization"].startswith(f"AWS4-HMAC-SHA256 Credential={KEY_ID}/20261005/{REGION}/bedrock/aws4_request")
    assert SECRET not in str(seen.headers)
    body = seen.body
    assert body["system"] == [{"text": "be brief"}]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assistant, results = body["messages"][1]["content"], body["messages"][2]["content"]
    assert assistant[0] == REASONING and assistant[1] == {"text": "looking"}
    assert [b["toolUse"]["toolUseId"] for b in assistant[2:]] == ["tooluse_1", "tooluse_2"]
    assert results == [
        {"toolResult": {"toolUseId": "tooluse_1", "content": [{"text": "A"}]}},
        {"toolResult": {"toolUseId": "tooluse_2", "content": [{"text": "boom"}], "status": "error"}},
        {"text": "and then?"},
    ]
    assert body["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"]["json"]["properties"] == {"q": {"type": "string"}}
    assert body["inferenceConfig"] == {"maxTokens": BedrockConverseTransport.DEFAULT_MAX_TOKENS}
    message = response.message
    assert message.text == "filing" and response.stop_reason == StopReason.TOOL_USE
    assert message.tool_calls == [ToolCall(id="tooluse_3", name="lookup", arguments={"q": "c"})]
    assert message.provider_state == ProviderState(wire=BEDROCK_CONVERSE, blocks=[REASONING])
    # Converse counts the 3 cached reads apart from its 12 inputTokens; TokenUsage counts the whole prompt.
    assert (response.usage.input_tokens, response.usage.cache_read_tokens, response.usage.output_tokens) == (15, 3, 7)


async def test_br2_without_keeping_state_reasoning_is_neither_sent_nor_kept(runtime: tuple[FakeBedrock, str]) -> None:
    fake, url = runtime
    fake.converse.append(ConverseReply(body=converse_body(content=[REASONING, {"text": "ok"}])))
    response = await _transport(url).complete(_history())  # type: ignore[arg-type]
    assert "reasoningContent" not in fake.seen[-1].body["messages"][1]["content"][0]
    assert response.message.provider_state is None


STREAM = (
    ("messageStart", {"role": "assistant"}),
    ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"reasoningContent": {"text": "think"}}}),
    ("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"reasoningContent": {"signature": "sig=="}}}),
    ("contentBlockStop", {"contentBlockIndex": 0}),
    ("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"text": "Hel"}}),
    ("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"text": "lo"}}),
    ("contentBlockStop", {"contentBlockIndex": 1}),
    ("contentBlockStart", {"contentBlockIndex": 2, "start": {"toolUse": {"toolUseId": "tooluse_9", "name": "lookup"}}}),
    ("contentBlockDelta", {"contentBlockIndex": 2, "delta": {"toolUse": {"input": '{"q":'}}}),
    ("contentBlockDelta", {"contentBlockIndex": 2, "delta": {"toolUse": {"input": '"z"}'}}}),
    ("contentBlockStop", {"contentBlockIndex": 2}),
    ("messageStop", {"stopReason": "tool_use"}),
    ("metadata", {"usage": {"inputTokens": 20, "outputTokens": 9, "cacheReadInputTokens": 0}, "metrics": {"latencyMs": 1}}),
)


async def test_br3_stream_decodes_frames_and_assembles_the_turn(runtime: tuple[FakeBedrock, str]) -> None:
    fake, url = runtime
    fake.converse.append(ConverseReply(events=STREAM))
    events = [e async for e in _transport(url, keep=True).stream([UserMessage(content="hi")], tools=TOOLS)]
    assert fake.seen[-1].path == f"/model/{MODEL}/converse-stream"
    assert "".join(e.delta for e in events if isinstance(e, TextDeltaEvent)) == "Hello"
    assert [e.delta for e in events if isinstance(e, ThinkingDeltaEvent)] == ["think"]
    calls = [e for e in events if isinstance(e, ToolCallDeltaEvent)]
    assert calls[0].id == "tooluse_9" and "".join(c.arguments_delta for c in calls) == '{"q":"z"}'
    final = events[-1]
    assert isinstance(final, StreamCompleteEvent)
    assert final.response.message.text == "Hello"
    assert final.response.message.tool_calls == [ToolCall(id="tooluse_9", name="lookup", arguments={"q": "z"})]
    assert final.response.message.provider_state == ProviderState(
        wire=BEDROCK_CONVERSE, blocks=[{"reasoningContent": {"reasoningText": {"text": "think", "signature": "sig=="}}}]
    )
    assert final.response.usage.input_tokens == 20


@pytest.mark.parametrize(("status", "kind", "retryable"), [(503, "ServiceUnavailableException", True), (429, "ThrottlingException", True), (400, "ValidationException", False)])
async def test_br4_http_errors_carry_their_status(runtime: tuple[FakeBedrock, str], status: int, kind: str, retryable: bool) -> None:
    fake, url = runtime
    fake.converse.append(ConverseReply(status=status, error_type=kind))
    with pytest.raises(TransportError) as raised:
        await _transport(url).complete([UserMessage(content="hi")])
    assert (raised.value.status_code, raised.value.retryable) == (status, retryable)
    assert kind in str(raised.value)


async def test_br4_an_exception_frame_mid_stream_fails_after_the_deltas_it_let_through(runtime: tuple[FakeBedrock, str]) -> None:
    fake, url = runtime
    fake.converse.append(ConverseReply(events=STREAM[4:5], exception=("internalServerException", "boom")))
    seen: list[object] = []
    with pytest.raises(TransportError) as raised:
        async for event in _transport(url).stream([UserMessage(content="hi")]):
            seen.append(event)  # noqa: PERF401 - the events before the failure are what is checked
    assert raised.value.status_code == 500 and raised.value.retryable
    assert [e.delta for e in seen if isinstance(e, TextDeltaEvent)] == ["Hel"]
    assert not any(isinstance(e, StreamCompleteEvent) for e in seen)


@pytest.mark.parametrize("stop", ["content_filtered", "guardrail_intervened"])
async def test_br4_a_filtered_answer_is_a_content_filter_error(runtime: tuple[FakeBedrock, str], stop: str) -> None:
    fake, url = runtime
    fake.converse.append(ConverseReply(body=converse_body(stop=stop)))
    with pytest.raises(TransportError, match="content_filter") as raised:
        await _transport(url).complete([UserMessage(content="hi")])
    assert not raised.value.retryable


def test_br4_a_bad_checksum_or_a_cut_frame_is_an_error() -> None:
    frame = bytearray(eventstream_frame(b'{"x":1}', "metadata"))
    frame[-1] ^= 0xFF
    with pytest.raises(EventStreamError, match="checksum"):
        EventStreamDecoder().feed(bytes(frame))
    decoder = EventStreamDecoder()
    whole = eventstream_frame(b'{"x":1}', "metadata")
    assert decoder.feed(whole[:7]) == [] and len(decoder.feed(whole[7:])) == 1
    decoder.feed(whole[:20])
    with pytest.raises(EventStreamError, match="inside a message"):
        decoder.finish()


async def test_br6_the_fake_refuses_a_request_signed_with_another_secret(runtime: tuple[FakeBedrock, str]) -> None:
    _, url = runtime
    with pytest.raises(TransportError) as raised:
        await _transport(url, secret="not-the-account-secret").complete([UserMessage(content="hi")])
    assert raised.value.status_code == 403
