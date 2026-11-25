"""WP56-WIRE (after WP-62): the Chat Completions and Messages wires pass what the engine asks of the provider.

WP-62 made the Responses wire (Codex) pass ``parallel_tool_calls``, an org-scoped ``prompt_cache_key`` and the
provider's encrypted reasoning. The other two wires an engine speaks still dropped them:

- Chat Completions (opencode, any OpenAI-compatible engine) named ``parallel_tool_calls`` and ``prompt_cache_key``
  dropped. They now reach a transport that can send them, the cache key as the org's routing key, never the engine's.
- Messages (Claude Code) never handed back the provider's thinking. Anthropic signs each thinking block; with
  extended thinking on, a tool turn's assistant message must carry its thinking back verbatim or the provider refuses
  the tool result. The answer now carries each thinking block with the provider's block sealed to the run's org and
  route (in ``signature``, or ``data`` for redacted thinking); replayed, the gateway opens it and the Anthropic
  transport sends the provider its own block first in the turn. Sealed for another org or route, or never issued by
  the gateway, it never reaches the provider. The call still takes its slot on the route's upstream key (WP-61) and
  the route is resolved as before (WP-58 region pins).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.gateway.chat_wire import parse_chat_request
from harness_core.gateway.errors import InvalidChatRequest
from harness_core.gateway.messages_wire import MESSAGES_WIRE, parse_messages_request
from harness_core.gateway.priority_gate import PriorityGate
from harness_core.gateway.reasoning_state import reasoning_tag, seal_anthropic_block
from harness_core.gateway.wire import WireCall
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.models.provider_state import ANTHROPIC_MESSAGES, with_state
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.openai import OpenAITransport
from harness_core.models.transports.prompt_cache import (
    OpenAICachePlan,
    PromptCacheSettings,
    routing_key,
)
from harness_core.testing import ManualClock
from harness_core.testing.model_gateway import ROUTE, GatewayRig
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_chat_wire import OPENCODE_BODY
from tests.unit.harness_core.gateway.test_messages_wire import claude_code_request
from tests.unit.harness_core.gateway.test_model_server import (
    ENGINE_HEADERS,
    ORG_KEY,
    FakeProvider,
    _serve,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TENANT = "org-1"
ENGINE_KEY = "ses_opencode_cache_1"
THOUGHT = {"type": "thinking", "thinking": "Planning the write.", "signature": "SIG-1"}
REDACTED = {"type": "redacted_thinking", "data": "REDACTED-1"}


def _anthropic_events(blocks: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Anthropic's stream for a thinking model's tool turn: the thinking blocks (text, then signature), the text and
    a tool call."""
    message = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5", "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 20, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    }
    events: list[tuple[str, dict[str, Any]]] = [("message_start", {"type": "message_start", "message": message})]
    index = 0
    for block in blocks:
        if block["type"] == "thinking":
            events += [
                ("content_block_start", {"type": "content_block_start", "index": index,
                                         "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": index,
                                         "delta": {"type": "thinking_delta", "thinking": block["thinking"]}}),
                ("content_block_delta", {"type": "content_block_delta", "index": index,
                                         "delta": {"type": "signature_delta", "signature": block["signature"]}}),
            ]
        else:
            events.append(("content_block_start", {"type": "content_block_start", "index": index, "content_block": block}))
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))
        index += 1
    events += [
        ("content_block_start", {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": "Writing files."}}),
        ("content_block_stop", {"type": "content_block_stop", "index": index}),
        ("content_block_start", {"type": "content_block_start", "index": index + 1,
                                 "content_block": {"type": "tool_use", "id": "call_a", "name": "write", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": index + 1,
                                 "delta": {"type": "input_json_delta", "partial_json": '{"filePath":"a.txt"}'}}),
        ("content_block_stop", {"type": "content_block_stop", "index": index + 1}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                           "usage": {"output_tokens": 30}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return events


class ThinkingProvider(FakeProvider):
    blocks: list[dict[str, Any]]

    def __init__(self, blocks: list[dict[str, Any]]) -> None:
        super().__init__()
        self.blocks = blocks

    async def anthropic(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.seen.append((request.path, {k.lower(): v for k, v in request.headers.items()}, body))
        payload = b"".join(
            f"event: {name}\ndata: {json.dumps(data)}\n\n".encode() for name, data in _anthropic_events(self.blocks)
        )
        return web.Response(body=payload, content_type="text/event-stream")


@pytest.fixture
async def thinking() -> AsyncIterator[tuple[ThinkingProvider, str]]:
    fake = ThinkingProvider([THOUGHT, REDACTED])
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


@pytest.fixture
async def openai_provider() -> AsyncIterator[tuple[FakeProvider, str]]:
    fake = FakeProvider()
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


def _anthropic(upstream: str, monkeypatch: pytest.MonkeyPatch) -> AnthropicTransport:
    """As the gateway's resolver builds an Anthropic route: the provider's thinking kept on the answer."""
    monkeypatch.setenv("ANTHROPIC_BASE_URL", upstream)
    transport = AnthropicTransport(api_key=ORG_KEY, model="claude-sonnet-4-5")
    assert transport.keep_provider_state() is True
    return transport


def _tag() -> str:
    return reasoning_tag(TENANT, ROUTE.route_id)


# --- Chat Completions -----------------------------------------------------------------------------------------


def test_wp56_wire_1_the_chat_wire_reads_parallel_tool_calls_and_the_cache_key() -> None:
    parsed = parse_chat_request({**OPENCODE_BODY, "parallel_tool_calls": False, "prompt_cache_key": ENGINE_KEY})
    assert parsed.parallel_tool_calls is False and parsed.cache_key == ENGINE_KEY
    assert "parallel_tool_calls" not in parsed.dropped and "prompt_cache_key" not in parsed.dropped
    plain = parse_chat_request(OPENCODE_BODY)
    assert plain.parallel_tool_calls is None and plain.cache_key is None
    with pytest.raises(InvalidChatRequest):
        parse_chat_request({**OPENCODE_BODY, "parallel_tool_calls": "no"})
    with pytest.raises(InvalidChatRequest):
        parse_chat_request({**OPENCODE_BODY, "prompt_cache_key": 7})


async def test_wp56_wire_2_an_opencode_request_reaches_the_provider_with_its_options_on_the_upstream_key(
    openai_provider: tuple[FakeProvider, str], caplog: pytest.LogCaptureFixture
) -> None:
    fake, upstream = openai_provider
    caplog.set_level(logging.INFO)
    transport = OpenAITransport(
        api_key=ORG_KEY, model="gpt-4o", base_url=f"{upstream}/v1",
        cache_plan=OpenAICachePlan({"prompt_cache_key": "pipeshub:model-default:0"}),
    )
    gate = PriorityGate(4, floor_every=0, monotonic=ManualClock().monotonic)
    asked: list[str] = []
    original = gate.acquire

    async def recording(key: str, cls: Any, *, org: str | None = None) -> Any:  # noqa: ANN401
        asked.append(key)
        return await original(key, cls, org=org)

    gate.acquire = recording  # type: ignore[method-assign]
    server, out = await _serve(GatewayRig(gate=gate), transport)
    body = {**OPENCODE_BODY, "parallel_tool_calls": False, "prompt_cache_key": ENGINE_KEY, "stream": True}
    try:
        async with loopback_client() as client, client.stream(
            "POST", f"{out.url}/v1/chat/completions", json=body,
            headers={**ENGINE_HEADERS, "Authorization": f"Bearer {out.token}"},
        ) as response:
            assert response.status_code == 200, await response.aread()
            _ = await response.aread()
    finally:
        await server.close()
    _, _, sent = fake.seen[0]
    assert sent["parallel_tool_calls"] is False
    shards = PromptCacheSettings.from_env().routing_shards
    assert sent["prompt_cache_key"] == routing_key(TENANT, ENGINE_KEY, shards, principal_id="user-1")
    assert ENGINE_KEY not in json.dumps(sent)
    assert asked == ["openai"], "one slot, on the route's key (the rig's route names no account: the provider's)"
    dropped = [r.getMessage() for r in caplog.records if "model_gateway.fields_dropped" in r.getMessage()]
    assert not any("parallel_tool_calls" in line or "prompt_cache_key" in line for line in dropped), dropped


# --- Messages -------------------------------------------------------------------------------------------------


async def _messages(url: str, token: str, body: dict[str, Any], headers: dict[str, str]) -> list[dict[str, Any]]:
    send = {k: v for k, v in headers.items() if k not in ("authorization", "x-api-key", "content-length", "host")}
    async with loopback_client() as client, client.stream(
        "POST", f"{url}/v1/messages", json=body, headers={**send, "Authorization": f"Bearer {token}"}
    ) as response:
        assert response.status_code == 200, await response.aread()
        raw = (await response.aread()).decode()
    events = []
    for frame in raw.split("\n\n"):
        data = [line[6:] for line in frame.split("\n") if line.startswith("data: ")]
        if data:
            events.append(json.loads(data[0]))
    return events


def _content(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The assistant content a Messages client assembles from the stream."""
    blocks: dict[int, dict[str, Any]] = {}
    for event in events:
        if event["type"] == "content_block_start":
            blocks[event["index"]] = dict(event["content_block"])
        elif event["type"] == "content_block_delta":
            block, delta = blocks[event["index"]], event["delta"]
            if delta["type"] == "text_delta":
                block["text"] += delta["text"]
            elif delta["type"] == "thinking_delta":
                block["thinking"] += delta["thinking"]
            elif delta["type"] == "signature_delta":
                block["signature"] = delta["signature"]
            elif delta["type"] == "input_json_delta":
                block["_json"] = block.get("_json", "") + delta["partial_json"]
    out = []
    for _, block in sorted(blocks.items()):
        if "_json" in block:
            block["input"] = json.loads(block.pop("_json"))
        out.append(block)
    return out


async def test_wp56_wire_3_claude_codes_thinking_round_trips_to_the_provider_verbatim(
    thinking: tuple[ThinkingProvider, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake, upstream = thinking
    caplog.set_level(logging.INFO)
    server, out = await _serve(GatewayRig(), _anthropic(upstream, monkeypatch))
    first, headers = claude_code_request("first_turn")
    try:
        content = _content(await _messages(out.url, out.token, first, headers))
        kinds = [b["type"] for b in content]
        assert kinds.count("thinking") == 1 and kinds.count("redacted_thinking") == 1, kinds
        shown = next(b for b in content if b["type"] == "thinking")
        # display: omitted, as Claude Code asks: no thinking text, and no signature the engine could read.
        assert shown["thinking"] == "" and "SIG-1" not in shown["signature"] and shown["signature"]
        hidden = next(b for b in content if b["type"] == "redacted_thinking")
        assert "REDACTED-1" not in hidden["data"]
        replay = {
            **first,
            "messages": [
                *first["messages"],
                {"role": "assistant", "content": content},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_a", "content": "written"}]},
            ],
        }
        await _messages(out.url, out.token, replay, headers)
    finally:
        await server.close()
    _, _, resent = fake.seen[1]
    turn = next(m for m in resent["messages"] if m["role"] == "assistant")
    assert turn["content"][:2] == [THOUGHT, REDACTED], turn["content"]
    assert [b["type"] for b in turn["content"][2:]] == ["text", "tool_use"]
    assert "pxh1." not in json.dumps(resent)
    assert not [r for r in caplog.records if "model_gateway.reasoning_dropped" in r.getMessage()]
    assert "SIG-1" not in caplog.text


async def test_wp56_wire_4_thinking_sealed_for_another_route_or_forged_never_reaches_the_provider(
    thinking: tuple[ThinkingProvider, str], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake, upstream = thinking
    caplog.set_level(logging.INFO)
    server, out = await _serve(GatewayRig(), _anthropic(upstream, monkeypatch))
    first, headers = claude_code_request("first_turn")
    other = seal_anthropic_block({"type": "thinking", "thinking": "x", "signature": "SIG-OTHER"},
                                 reasoning_tag("org-2", ROUTE.route_id), shown=False)
    forged = {"type": "thinking", "thinking": "y", "signature": "SIG-FORGED"}
    replay = {
        **first,
        "messages": [
            *first["messages"],
            {"role": "assistant", "content": [other, forged, {"type": "text", "text": "ok"}]},
            {"role": "user", "content": "again"},
        ],
    }
    try:
        await _messages(out.url, out.token, replay, headers)
    finally:
        await server.close()
    _, _, sent = fake.seen[0]
    text = json.dumps(sent)
    assert "SIG-OTHER" not in text and "SIG-FORGED" not in text and "pxh1." not in text
    lines = [r.getMessage() for r in caplog.records if "model_gateway.reasoning_dropped" in r.getMessage()]
    assert len(lines) == 1 and "items=2" in lines[0], lines


def test_wp56_wire_5_a_whole_answer_puts_the_providers_thinking_first_sealed_and_shown_once() -> None:
    shown = parse_messages_request(
        {"model": "engine-model", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}],
         "thinking": {"type": "enabled", "budget_tokens": 32}}
    )
    response = ModelResponse(
        message=with_state(AssistantMessage(content=[TextPart(text="done")]), ANTHROPIC_MESSAGES, [THOUGHT]),
        usage=TokenUsage(input_tokens=10, output_tokens=2),
        stop_reason=StopReason.END_TURN,
    )
    body = MESSAGES_WIRE.body(shown, response, WireCall("engine-model", "msg_1", 1, state_tag="t1"))
    assert body["content"][0]["type"] == "thinking" and body["content"][0]["thinking"] == "Planning the write."
    assert body["content"][0]["signature"] == seal_anthropic_block(THOUGHT, "t1", shown=True)["signature"]
    assert [b["type"] for b in body["content"]] == ["thinking", "text"]
    untagged = MESSAGES_WIRE.body(shown, response, WireCall("engine-model", "msg_1", 1))
    assert all(b["signature"] == "" for b in untagged["content"] if b["type"] == "thinking")
