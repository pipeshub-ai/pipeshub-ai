"""WP-62 (DEEPRUN-FIX items 8 and 9a): what Codex asks of the provider reaches it, and a call cut short is charged
what it honestly cost.

WP62-MGW-n: Codex 0.160's Responses request (the CX-1 fixture, built from its source) through the gateway over HTTP
to a fake Azure Responses deployment, with the real ``AzureOpenAITransport`` configured as the gateway's resolver
builds it (the cache policy's key, provider state kept): ``parallel_tool_calls`` and an org-scoped
``prompt_cache_key`` reach the provider, ``client_metadata`` does not; the provider's encrypted reasoning comes back
sealed to the run's org and route and goes back to the provider verbatim on the next request; reasoning sealed for
another route never reaches it. Without this, every Codex call logged ``fields_dropped`` (client_metadata,
parallel_tool_calls, prompt_cache_key) and ``reasoning_dropped`` (transport_has_no_reasoning_state).

WP62-EST-n: a call the provider never reported usage for (a recorded call: ``status=interrupted estimated=True``, USD
0.0818 for a 40,916-token prompt, 90% of which the provider had cached the call before) is charged its input with
the run's last cached prompt at the cache-read price, and only the output that streamed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.access.spend import input_estimate, usage_spend
from harness_core.budget.types import LedgerUsage, RunAccountRef
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.model_gateway import ChatStream
from harness_core.gateway.reasoning_state import reasoning_tag, seal
from harness_core.gateway.responses_wire import (
    RESPONSES_WIRE,
    ResponseEncoder,
    parse_responses_request,
)
from harness_core.gateway.wire import WireCall
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.models.call_options import CallOptions, call_options
from harness_core.models.provider_state import OPENAI_RESPONSES, with_state
from harness_core.models.transports.azure_openai import (
    AzureOpenAITransport,
    RequestDefaults,
)
from harness_core.models.transports.openai import OpenAITransport
from harness_core.models.transports.prompt_cache import (
    OpenAICachePlan,
    PromptCacheSettings,
    routing_key,
)
from harness_core.testing.model_gateway import ROUTE, GatewayRig, body_of, reply_events
from tests.support.loopback_client import loopback_client
from tests.unit.harness_core.gateway.test_model_server import (
    ORG_KEY,
    FakeProvider,
    _serve,
)
from tests.unit.harness_core.gateway.test_responses_wire import (
    assemble,
    codex_headers,
    codex_request,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

TENANT = "org-1"
SESSION = "0199a213-81c0-7800-8aa1-bbab2a035a53"
TOOL = {
    "type": "function",
    "name": "write",
    "description": "Write a file",
    "strict": False,
    "parameters": {"type": "object", "properties": {"filePath": {"type": "string"}}},
}


def _upstream(model: str, encrypted: str) -> list[dict[str, Any]]:
    """Azure's Responses stream for a reasoning model asked for encrypted reasoning: a reasoning item (summary
    streamed, encrypted content on its done event and on the final response), a message and a function call."""
    reasoning = {"id": "rs_up_1", "type": "reasoning", "status": "completed", "encrypted_content": encrypted,
                 "summary": [{"type": "summary_text", "text": "Planning the write."}]}
    msg = {"id": "msg_up", "type": "message", "status": "completed", "role": "assistant",
           "content": [{"type": "output_text", "text": "Writing files.", "annotations": []}]}
    call = {"id": "fc_up", "type": "function_call", "status": "completed", "call_id": "call_a", "name": "write",
            "arguments": '{"filePath":"a.txt"}'}
    response = {"id": "resp_up", "object": "response", "created_at": 1, "status": "completed", "model": model,
                "output": [reasoning, msg, call], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                "usage": {"input_tokens": 1200, "input_tokens_details": {"cached_tokens": 1024}, "output_tokens": 60,
                          "output_tokens_details": {"reasoning_tokens": 20}, "total_tokens": 1260}}
    events = [
        {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": {**reasoning, "summary": [], "encrypted_content": None}},
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "item_id": "rs_up_1", "summary_index": 0,
         "delta": "Planning the write."},
        {"type": "response.output_item.done", "output_index": 0, "item": reasoning},
        {"type": "response.output_item.added", "output_index": 1, "item": {**msg, "content": []}},
        {"type": "response.output_text.delta", "output_index": 1, "item_id": "msg_up", "content_index": 0, "delta": "Writing files."},
        {"type": "response.output_item.done", "output_index": 1, "item": msg},
        {"type": "response.output_item.added", "output_index": 2, "item": {**call, "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "output_index": 2, "item_id": "fc_up", "delta": '{"filePath":"a.txt"}'},
        {"type": "response.output_item.done", "output_index": 2, "item": call},
        {"type": "response.completed", "response": response},
    ]
    return [{**e, "sequence_number": i} for i, e in enumerate(events)]


class ReasoningProvider(FakeProvider):
    """An Azure reasoning deployment on the Responses API; each answer's encrypted reasoning is ``ENC-<n>``."""

    async def responses(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.seen.append((request.path, {k.lower(): v for k, v in request.headers.items()}, body))
        events = _upstream(body["model"], f"ENC-{len(self.seen)}")
        payload = b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)
        return web.Response(body=payload, content_type="text/event-stream")

    def app(self) -> web.Application:
        app = super().app()
        app.router.add_post("/openai/responses", self.responses)
        app.router.add_post("/openai/v1/responses", self.responses)
        return app


@pytest.fixture
async def provider() -> AsyncIterator[tuple[ReasoningProvider, str]]:
    fake = ReasoningProvider()
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("")).rstrip("/")
    await server.close()


def _azure(upstream: str) -> AzureOpenAITransport:
    """As ``build_configured_transport`` builds an Azure reasoning deployment for the gateway (WP-62)."""
    transport = AzureOpenAITransport(
        api_key=ORG_KEY, azure_endpoint=upstream, api_version="2025-04-01-preview", deployment="dep-1",
        model="gpt-6.1-sol", defaults=RequestDefaults(use_responses_api=True, reasoning={"effort": "low"}, model="gpt-6.1-sol"),
        cache_plan=OpenAICachePlan({"prompt_cache_key": "pipeshub:model-default:0"}),
    )
    assert transport.keep_provider_state() is True
    return transport


async def _events(url: str, token: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    headers = {k: v for k, v in codex_headers().items() if k != "authorization"}
    async with loopback_client() as client, client.stream(
        "POST", f"{url}/v1/responses", json=body, headers={**headers, "Authorization": f"Bearer {token}"}
    ) as response:
        assert response.status_code == 200, await response.aread()
        raw = (await response.aread()).decode()
    return [json.loads(frame.split("\n")[1].removeprefix("data: ")) for frame in raw.split("\n\n") if frame.strip()]


def _tag() -> str:
    return reasoning_tag(TENANT, ROUTE.route_id)


async def test_wp62_mgw3_codex_wire_round_trips_reasoning_and_passes_its_options(
    provider: tuple[ReasoningProvider, str], caplog: pytest.LogCaptureFixture
) -> None:
    """WP62-MGW-3 (and MGW-5 for a transport that honours both options): Codex's request reaches the provider with
    its tool and cache options, the answer carries the provider's encrypted reasoning sealed to the run's org and
    route, and Codex replaying that answer hands the provider its own reasoning back, in the provider's order."""
    fake, upstream = provider
    caplog.set_level(logging.INFO)
    server, out = await _serve(GatewayRig(), _azure(upstream))
    try:
        first_body = {**codex_request(), "tools": [TOOL]}
        events = await _events(out.url, out.token, first_body)
        assembled = assemble(events)
        output = events[-1]["response"]["output"]
        second_body = {
            **first_body,
            "input": [*first_body["input"], *output,
                      {"type": "function_call_output", "call_id": "call_a", "output": "written"}],
        }
        await _events(out.url, out.token, second_body)
    finally:
        await server.close()

    _, _, sent = fake.seen[0]
    assert sent["parallel_tool_calls"] is True
    shards = PromptCacheSettings.from_env().routing_shards
    # The cache route follows the run's actor (the rig pins ``user-1``), never only the org.
    assert sent["prompt_cache_key"] == routing_key(TENANT, SESSION, shards, principal_id="user-1")
    assert sent["prompt_cache_key"] != routing_key(TENANT, SESSION, shards)
    assert sent["prompt_cache_key"] != routing_key(TENANT, SESSION, shards, principal_id="user-2")
    assert SESSION not in json.dumps(sent), "the engine's session id reached the provider"
    assert "client_metadata" not in sent and "11111111-2222-4333-8444-555555555555" not in json.dumps(sent)
    assert sent["include"] == ["reasoning.encrypted_content"] and sent["store"] is False
    # Codex's display copy (a replayed summary with no encrypted content) is not sent: the provider cannot read it.
    assert [i for i in sent["input"] if i.get("type") == "reasoning"] == []

    reasoning = [i for i in output if i["type"] == "reasoning"]
    assert [r.get("encrypted_content") for r in reasoning] == [None, seal("ENC-1", _tag())]
    assert reasoning[1]["id"] == "rs_up_1" and reasoning[1]["summary"] == []
    assert [i["type"] for i in assembled["output"]] == ["reasoning", "message", "function_call", "reasoning"]

    _, _, resent = fake.seen[1]
    kinds = [(i.get("type"), i.get("role")) for i in resent["input"]]
    turn = kinds.index(("reasoning", None))
    assert kinds[turn + 1:turn + 3] == [("message", "assistant"), ("function_call", None)], kinds
    replayed = resent["input"][turn]
    assert replayed == {"type": "reasoning", "id": "rs_up_1", "encrypted_content": "ENC-1", "summary": []}
    assert "pxh1." not in json.dumps(resent)

    dropped = [r.getMessage() for r in caplog.records if "model_gateway.fields_dropped" in r.getMessage()]
    assert dropped and all("fields=[client_metadata]" in line for line in dropped), dropped
    assert not [r for r in caplog.records if "model_gateway.reasoning_dropped" in r.getMessage()]
    assert "ENC-1" not in caplog.text


async def test_wp62_mgw4_reasoning_sealed_for_another_route_never_reaches_the_provider(
    provider: tuple[ReasoningProvider, str], caplog: pytest.LogCaptureFixture
) -> None:
    """WP62-MGW-4: content sealed for another org's route, or never issued by the gateway, is dropped and named."""
    fake, upstream = provider
    caplog.set_level(logging.INFO)
    server, out = await _serve(GatewayRig(), _azure(upstream))
    history = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "go"}]},
        {"type": "reasoning", "id": "rs_a", "summary": [], "encrypted_content": seal("ENC-OTHER", reasoning_tag("org-2", ROUTE.route_id))},
        {"type": "reasoning", "id": "rs_b", "summary": [], "encrypted_content": "ENC-FORGED"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]},
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "again"}]},
    ]
    try:
        await _events(out.url, out.token, {**codex_request(), "input": history, "tools": [TOOL]})
    finally:
        await server.close()
    _, _, sent = fake.seen[0]
    assert [i for i in sent["input"] if i.get("type") == "reasoning"] == []
    assert "ENC-OTHER" not in json.dumps(sent) and "ENC-FORGED" not in json.dumps(sent)
    lines = [r.getMessage() for r in caplog.records if "model_gateway.reasoning_dropped" in r.getMessage()]
    assert len(lines) == 1 and "reason=sealed_for_another_route" in lines[0] and "items=2" in lines[0]
    assert "ENC-OTHER" not in caplog.text and "ENC-FORGED" not in caplog.text


async def test_wp62_mgw5_options_a_transport_cannot_send_are_named_dropped(caplog: pytest.LogCaptureFixture) -> None:
    """WP62-MGW-5: a transport that sends neither option (the recording double, as Anthropic's or Gemini's) gets
    the request unchanged and the one ``fields_dropped`` line names them with the wire's own drops."""
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    binding = rig.pin()
    outcome = await rig.gateway.serve(RESPONSES_WIRE, rig.token(binding), {}, body_of({**codex_request(), "stream": False}))
    assert outcome is not None
    lines = [r.getMessage() for r in caplog.records if "model_gateway.fields_dropped" in r.getMessage()]
    assert len(lines) == 1
    assert "fields=[client_metadata,parallel_tool_calls,prompt_cache_key]" in lines[0]


def test_wp62_mgw6_the_transport_sends_options_only_inside_a_call_scope() -> None:
    """WP62-MGW-6: the OpenAI transport puts the call's options on both wires, the call's key replaces its
    default only where its cache policy sends one, and outside a scope every request is as before."""
    responses = _azure("https://example.openai.azure.com")
    chat = OpenAITransport(api_key=ORG_KEY, model="gpt-4o", base_url="https://example.invalid/v1")
    from harness_core.kernel.core.messages import UserMessage
    from harness_core.kernel.core.tool_schema import ToolSchema

    tools = [ToolSchema(name="write", description="", input_schema={"type": "object"})]
    plain = responses._build_responses_kwargs([UserMessage(content="hi")], tools, None, None, stream=True)
    assert "parallel_tool_calls" not in plain and plain["prompt_cache_key"] == "pipeshub:model-default:0"
    with call_options(CallOptions(parallel_tool_calls=False, cache_key="pipeshub:abc:3")):
        scoped = responses._build_responses_kwargs([UserMessage(content="hi")], tools, None, None, stream=True)
        without_tools = responses._build_responses_kwargs([UserMessage(content="hi")], None, None, None, stream=True)
    assert scoped["parallel_tool_calls"] is False and scoped["prompt_cache_key"] == "pipeshub:abc:3"
    assert "parallel_tool_calls" not in without_tools
    assert responses.honoured_call_options == {"parallel_tool_calls", "prompt_cache_key"}
    assert responses.provider_state_wire == OPENAI_RESPONSES
    # No cache parameters for this host: the call's key is not sent, and the gateway names it dropped.
    assert chat.honoured_call_options == {"parallel_tool_calls"}
    assert chat.provider_state_wire is None


def test_wp62_mgw7_a_whole_answer_puts_the_providers_reasoning_first_and_sealed() -> None:
    """WP62-MGW-7: the non-streaming answer (and a stream whose final response is all there is) carries each
    reasoning item the provider produced, its id and summary, first, its content sealed; without ``include`` none."""
    asked = parse_responses_request({"model": "engine-model", "input": "hi", "include": ["reasoning.encrypted_content"]})
    block = {"type": "reasoning", "id": "rs_p", "encrypted_content": "ENC", "summary": [{"type": "summary_text", "text": "s"}]}
    response = ModelResponse(
        message=with_state(AssistantMessage(content=[TextPart(text="done")]), OPENAI_RESPONSES, [block]),
        usage=TokenUsage(input_tokens=10, output_tokens=2),
        stop_reason=StopReason.END_TURN,
    )
    call = WireCall("engine-model", "resp_1", 1, state_tag="t1")
    body = RESPONSES_WIRE.body(asked, response, call)
    assert body["output"][0] == {"id": "rs_p", "type": "reasoning", "summary": block["summary"], "encrypted_content": seal("ENC", "t1")}
    assert body["output"][1]["type"] == "message"
    plain = parse_responses_request({"model": "engine-model", "input": "hi"})
    assert [i["type"] for i in RESPONSES_WIRE.body(plain, response, call)["output"]] == ["message"]
    untagged = RESPONSES_WIRE.body(asked, response, WireCall("engine-model", "resp_1", 1))
    assert [i["type"] for i in untagged["output"]] == ["message"]
    assert ResponseEncoder(asked, call).response_object("completed")["parallel_tool_calls"] is True


def test_wp62_mgw8_stored_state_stays_refused() -> None:
    """WP62-MGW-8: ``previous_response_id`` names a response the gateway would have to keep (``store: false`` and no
    state, E3b): it stays an explicit 400, never a silent drop. The reasoning travels in the history instead."""
    from harness_core.gateway.errors import ResponseStateUnsupported

    with pytest.raises(ResponseStateUnsupported):
        parse_responses_request({"model": "engine-model", "input": "hi", "previous_response_id": "resp_1"})


# --- 9a: a call the provider never reported usage for -----------------------------------------------------------


def _chat_body(text: str, *, stream: bool) -> dict[str, Any]:
    return {"model": "engine-model", "messages": [{"role": "user", "content": text}], "stream": stream}


def _call_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    lines = [r.getMessage() for r in caplog.records if "model_gateway.call " in r.getMessage()]
    return [dict(re.findall(r"(\w+)=(\S+)", line)) for line in lines]


def _estimate(text: str) -> int:
    parsed = CHAT_WIRE.parse(_chat_body(text, stream=True))
    return input_estimate(parsed.messages, parsed.system, parsed.tools, ROUTE.config_model or ROUTE.model)


async def _unreported(rig: GatewayRig, token: str, text: str) -> None:
    """A call that ends with its whole answer, from a transport that reports no usage."""
    rig.transport.events = [TextDeltaEvent(delta="whole answer"), reply_events("whole answer", usage=TokenUsage(input_tokens=0, output_tokens=0))]
    outcome = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_chat_body(text, stream=True)))
    assert isinstance(outcome, ChatStream)
    _ = [c async for c in outcome.chunks]


async def test_wp62_est1_an_unreported_call_credits_the_prompt_the_provider_had_cached(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WP62-EST-1: after a reported call that the cache served, a call on the same run and route that ended with no
    usage reported is charged the last prompt at the cache-read price, the rest fresh, and the output that streamed;
    the ledger records the counts the charge was made from. A call cut short takes no credit (R13-EST-2)."""
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    binding = rig.pin()
    token = rig.token(binding)
    long = "x" * 40_000
    rig.transport.events = [reply_events("ok", usage=TokenUsage(input_tokens=9_000, output_tokens=5, cache_read_tokens=8_000))]
    await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_chat_body(long, stream=False)))
    before = (await rig.ledger.remaining(RunAccountRef(TENANT, "run-1"))).used
    rig.clock.advance(60)
    await _unreported(rig, token, long + " and more")

    line = _call_lines(caplog)[-1]
    estimate = _estimate(long + " and more")
    expected = TokenUsage(input_tokens=estimate, output_tokens=len("whole answer") // 4, cache_read_tokens=9_000)
    assert line["status"] == "ok" and line["estimated"] == "True"
    assert (int(line["inputTokens"]), int(line["cacheReadTokens"]), int(line["outputTokens"])) == (
        expected.input_tokens, expected.cache_read_tokens, expected.output_tokens)
    charged = usage_spend(expected, ROUTE.config_model or ROUTE.model, charges_usd=True)
    uncached = usage_spend(expected.model_copy(update={"cache_read_tokens": 0}), ROUTE.config_model or ROUTE.model, charges_usd=True)
    assert int(line["costMicroUsd"]) == charged.usd_micros < uncached.usd_micros
    after = (await rig.ledger.remaining(RunAccountRef(TENANT, "run-1"))).used
    assert after == LedgerUsage(
        before.input_tokens + expected.input_tokens, before.output_tokens + expected.output_tokens,
        before.cache_read_tokens + expected.cache_read_tokens, before.cache_write_tokens,
    )


async def test_wp62_est2_without_a_cached_prompt_the_input_is_charged_fresh(caplog: pytest.LogCaptureFixture) -> None:
    """WP62-EST-2: no reported call before, a stale one (past the cache window), or one the cache did not serve: the
    input is charged in full, as it was sent, and the output as far as it streamed."""
    for case in ("first", "stale", "uncached"):
        rig = GatewayRig()
        caplog.clear()
        caplog.set_level(logging.INFO)
        token = rig.token(rig.pin())
        if case != "first":
            cached = 0 if case == "uncached" else 8_000
            rig.transport.events = [reply_events("ok", usage=TokenUsage(input_tokens=9_000, output_tokens=5, cache_read_tokens=cached))]
            await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_chat_body("y" * 40_000, stream=False)))
            rig.clock.advance(301 if case == "stale" else 10)
        await _unreported(rig, token, "y" * 40_000)
        line = _call_lines(caplog)[-1]
        assert line["estimated"] == "True", case
        assert int(line["cacheReadTokens"]) == 0, case
        assert int(line["inputTokens"]) == _estimate("y" * 40_000), case
        assert int(line["outputTokens"]) == len("whole answer") // 4, case


async def test_wp62_est3_reported_usage_is_never_replaced_by_an_estimate(caplog: pytest.LogCaptureFixture) -> None:
    """WP62-EST-3: a stream that ends with the provider's usage is charged exactly that usage."""
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    usage = TokenUsage(input_tokens=500, output_tokens=7, cache_read_tokens=256)
    rig.transport.events = [TextDeltaEvent(delta="hi"), reply_events("hi", usage=usage)]
    outcome = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_chat_body("q", stream=True)))
    assert isinstance(outcome, ChatStream)
    _ = [c async for c in outcome.chunks]
    line = _call_lines(caplog)[-1]
    assert line["estimated"] == "False" and line["status"] == "ok"
    assert int(line["costMicroUsd"]) == usage_spend(usage, ROUTE.config_model or ROUTE.model, charges_usd=True).usd_micros
