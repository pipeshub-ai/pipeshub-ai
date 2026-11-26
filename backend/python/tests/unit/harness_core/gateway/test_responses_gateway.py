"""E3b RG-1..RG-8: the model gateway core on the Responses wire, over E3's in-memory rig.

The security cases run on both wires from one table, so the Responses route is
held to exactly what the chat route already refuses.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.errors import BudgetExhausted, BudgetInsufficient
from harness_core.access.types import (
    ENGINE_MODEL_ID,
    PinnedRoute,
    RunBudget,
    route_digest,
)
from harness_core.budget.types import ZERO, Amount
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.messages_wire import MESSAGES_WIRE
from harness_core.gateway.model_gateway import (
    ChatCompletion,
    ChatStream,
    ProviderCallFailed,
)
from harness_core.gateway.responses_wire import RESPONSES_WIRE
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import StreamCompleteEvent, TextDeltaEvent
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from tests.unit.harness_core.gateway.test_model_gateway import (
    ANTHROPIC_KEY,
    GOOGLE_KEY,
    OPENAI_KEY,
    account,
    prespend,
    spent,
)

if TYPE_CHECKING:
    from harness_core.gateway.wire import GatewayWire, WireRequest

TOOL = {"type": "function", "name": "get_time", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}


def responses_body(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": ENGINE_MODEL_ID, "input": [{"role": "user", "content": "hi"}], "store": False, **extra}


def chat_body(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}], **extra}


def messages_body(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": ENGINE_MODEL_ID, "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}], **extra}


# E3c MG-3: every engine wire is held to the same security cases.
WIRES: dict[str, tuple[GatewayWire[Any], Any]] = {
    "chat": (CHAT_WIRE, chat_body),
    "responses": (RESPONSES_WIRE, responses_body),
    "messages": (MESSAGES_WIRE, messages_body),
}


async def respond(
    rig: GatewayRig, token: str | None, payload: object, headers: dict[str, str] | None = None,
    wire: GatewayWire[WireRequest] = RESPONSES_WIRE,  # pyright: ignore[reportArgumentType]
) -> ChatCompletion | ChatStream:
    return await rig.gateway.serve(wire, token, headers or {}, body_of(payload))


@pytest.fixture
def rig() -> GatewayRig:
    return GatewayRig()


async def test_rg1_non_streaming_answer_under_the_neutral_id(rig: GatewayRig) -> None:
    binding = rig.pin()
    outcome = await respond(rig, rig.token(binding), responses_body(instructions="be brief", tools=[TOOL]))
    assert isinstance(outcome, ChatCompletion)
    assert outcome.body["object"] == "response" and outcome.body["model"] == ENGINE_MODEL_ID
    assert outcome.body["output"][0]["content"][0]["text"] == "hello"  # type: ignore[index]
    call = rig.transport.calls[0]
    assert call.system == "be brief" and not call.stream
    assert call.messages == [UserMessage(content="hi")]
    assert call.tools is not None and call.tools[0].name == "get_time"


async def test_rg1_a_tool_round_trip_reaches_the_transport_as_one_turn(rig: GatewayRig) -> None:
    binding = rig.pin()
    history = [
        {"role": "user", "content": "time in Paris?"},
        {"type": "function_call", "call_id": "c1", "name": "get_time", "arguments": '{"city":"Paris"}'},
        {"type": "function_call_output", "call_id": "c1", "output": "12:00"},
    ]
    await respond(rig, rig.token(binding), responses_body(input=history, tools=[TOOL]))
    assert rig.transport.calls[0].messages == [
        UserMessage(content="time in Paris?"),
        AssistantMessage(tool_calls=[ToolCall(id="c1", name="get_time", arguments={"city": "Paris"})]),
        ToolMessage(content="12:00", tool_call_id="c1"),
    ]


async def test_rg2_streaming_is_charged_once_under_the_response_id(rig: GatewayRig, caplog: pytest.LogCaptureFixture) -> None:
    rig.transport.events = [TextDeltaEvent(delta="hel"), TextDeltaEvent(delta="lo"),
                            reply_events("hello", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin()
    caplog.set_level(logging.INFO)
    outcome = await respond(rig, rig.token(binding), responses_body(stream=True))
    assert isinstance(outcome, ChatStream) and outcome.terminal == b""
    events = [e async for e in outcome.chunks]
    assert events[0]["type"] == "response.created" and events[-1]["type"] == "response.completed"
    assert events[-1]["response"]["id"].startswith("resp_")  # type: ignore[index]
    assert outcome.frame(events[0]).startswith(b"event: response.created\n")
    assert await spent(rig) == Amount(450, 110)
    assert caplog.text.count("model_gateway.call") == 1 and "api=responses" in caplog.text


def _bad_token(rig: GatewayRig, binding: Any, case: str) -> str | None:  # noqa: ANN401
    match case:
        case "token_missing":
            return None
        case "token_malformed":
            return "not.a.token"
        case "token_expired":
            return rig.token(binding, issued_at=0, ttl_s=60)
        case "token_unknown_key":
            return rig.token(binding, header={"kid": "hgk_unknown"})
        case _:
            return rig.token(replace(binding, routes_digest=route_digest(())))


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("case", ["token_missing", "token_malformed", "token_expired", "token_unknown_key", "binding_mismatch"])
async def test_rg3_bad_tokens_are_refused_on_both_wires(rig: GatewayRig, wire: str, case: str) -> None:
    binding = rig.pin()
    codec, body = WIRES[wire]
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, _bad_token(rig, binding, case), body(), wire=codec)
    assert refused.value.reason_code == case
    assert rig.transport.calls == []


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_rg3_another_runs_token_reaches_only_that_runs_grant(rig: GatewayRig, wire: str) -> None:
    codec, body = WIRES[wire]
    other = RecordedTransport([reply_events("from run 2")])
    first = rig.pin("run-1")
    second = rig.pin("run-2", routes=(PinnedRoute("openai", "gpt-y", config_key="k2", config_model="gpt-y"),), transport=other)
    await respond(rig, rig.token(second), body(), wire=codec)
    assert len(other.calls) == 1 and rig.transport.calls == []
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, rig.token(replace(first, routes_digest=second.routes_digest)), body(), wire=codec)
    assert refused.value.reason_code == "binding_mismatch"


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize(
    ("headers", "in_body", "reason"),
    [
        ({"x-custom": ANTHROPIC_KEY}, None, "forbidden_credential"),
        ({"session-id": OPENAI_KEY}, None, "forbidden_credential"),
        ({"api-key": "0123456789abcdef"}, None, "caller_credential_header"),
        ({"x-api-key": "0123456789abcdef"}, None, "caller_credential_header"),
    ],
)
async def test_rg3_credentials_are_refused_on_both_wires(
    rig: GatewayRig, wire: str, headers: dict[str, str], in_body: str | None, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    binding = rig.pin()
    codec, body = WIRES[wire]
    payload = body(user=in_body) if in_body else body()
    caplog.set_level(logging.WARNING)
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, rig.token(binding), payload, headers, wire=codec)
    assert refused.value.reason_code == reason
    assert rig.transport.calls == []
    assert caplog.text.count("model_gateway.refused") == 1
    for secret in (ANTHROPIC_KEY, OPENAI_KEY, GOOGLE_KEY, "0123456789abcdef", rig.token(binding)):
        assert secret not in caplog.text


@pytest.mark.parametrize("wire", sorted(WIRES))
async def test_rg3_wp103_a_key_in_the_body_is_hidden_on_both_wires(rig: GatewayRig, wire: str) -> None:
    binding = rig.pin()
    codec, body = WIRES[wire]
    await respond(rig, rig.token(binding), body(user=GOOGLE_KEY), wire=codec)
    assert len(rig.transport.calls) == 1 and GOOGLE_KEY not in repr(rig.transport.calls)


@pytest.mark.parametrize("wire", sorted(WIRES))
@pytest.mark.parametrize("model", ["gpt-4o", "gpt-x", "claude-sonnet-4-6"])
async def test_rg3_a_model_outside_the_grant_is_refused(rig: GatewayRig, wire: str, model: str) -> None:
    binding = rig.pin()
    codec, body = WIRES[wire]
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, rig.token(binding), body(model=model), wire=codec)
    assert refused.value.reason_code == "model_not_granted"
    assert rig.transport.calls == []


async def test_rg4_budgets_refuse_before_the_call(rig: GatewayRig) -> None:
    binding = rig.pin(budget=RunBudget(max_cost_usd=1.0, max_tokens=None))
    await prespend(rig, Amount(1_000_000, 10))
    with pytest.raises(BudgetExhausted):
        await respond(rig, rig.token(binding), responses_body())
    tight = rig.pin("run-2", budget=RunBudget(max_cost_usd=None, max_tokens=10))
    with pytest.raises(BudgetInsufficient):
        await respond(rig, rig.token(tight), responses_body(input="x" * 400))
    assert rig.transport.calls == []


async def test_rg5_reasoning_the_transport_cannot_carry_is_dropped_and_logged(
    rig: GatewayRig, caplog: pytest.LogCaptureFixture
) -> None:
    binding = rig.pin()
    caplog.set_level(logging.INFO)
    history = [
        {"role": "user", "content": "hi"},
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque-blob"},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]},
        {"role": "user", "content": "again"},
    ]
    outcome = await respond(rig, rig.token(binding), responses_body(input=history, include=["reasoning.encrypted_content"]))
    assert isinstance(outcome, ChatCompletion)
    lines = [r.getMessage() for r in caplog.records if "model_gateway.reasoning_dropped" in r.getMessage()]
    assert len(lines) == 1
    assert "transport_has_no_reasoning_state" in lines[0] and "items=1" in lines[0]
    assert "opaque-blob" not in caplog.text


@pytest.mark.parametrize(("pinned", "asked", "sent"), [("high", "low", "high"), (None, "low", "low"), (None, None, None)])
async def test_rg6_the_pinned_effort_wins(rig: GatewayRig, pinned: str | None, asked: str | None, sent: str | None) -> None:
    binding = rig.pin(effort=pinned)
    reasoning = {"effort": asked, "summary": "auto"} if asked else None
    await respond(rig, rig.token(binding), responses_body(reasoning=reasoning))
    assert rig.transport.calls[0].effort == sent


async def test_rg7_a_failure_before_output_is_an_http_error(rig: GatewayRig) -> None:
    rig.transport.fail = TransportError("Rate limit", status_code=429)
    binding = rig.pin()
    with pytest.raises(ProviderCallFailed) as failed:
        await respond(rig, rig.token(binding), responses_body(stream=True))
    assert (failed.value.status, failed.value.code) == (429, "rate_limited")
    # P6.5: the provider bills no call it refused, so the reservation goes back (E3 charged the input).
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved) == (ZERO, ZERO)


async def test_rg7_a_failure_after_output_fails_the_response_and_charges_once(rig: GatewayRig) -> None:
    rig.transport.events = [TextDeltaEvent(delta="partial " * 10), reply_events("never")]
    rig.transport.fail = TransportError("boom", status_code=503)
    rig.transport.fail_after = 1
    binding = rig.pin()
    outcome = await respond(rig, rig.token(binding), responses_body(stream=True))
    assert isinstance(outcome, ChatStream)
    events = [e async for e in outcome.chunks]
    assert events[-1]["type"] == "response.failed"
    assert events[-1]["response"]["error"]["code"] == "provider_unavailable"  # type: ignore[index]
    charged = await spent(rig)
    assert charged.tokens > 0 and charged.usd_micros > 0


async def test_rg7_a_cut_answer_is_incomplete(rig: GatewayRig) -> None:
    rig.transport.events = [StreamCompleteEvent(response=ModelResponse(
        message=AssistantMessage(content="trunc"), stop_reason=StopReason.MAX_TOKENS))]  # pyright: ignore[reportArgumentType]
    binding = rig.pin()
    outcome = await respond(rig, rig.token(binding), responses_body())
    assert isinstance(outcome, ChatCompletion) and outcome.body["status"] == "incomplete"


async def test_rg8_a_stateful_request_is_refused_once_after_the_token(rig: GatewayRig, caplog: pytest.LogCaptureFixture) -> None:
    binding = rig.pin()
    caplog.set_level(logging.WARNING)
    with pytest.raises(HarnessRefusal) as refused:
        await respond(rig, rig.token(binding), responses_body(previous_response_id="resp_1"))
    assert refused.value.reason_code == "response_state_unsupported"
    assert caplog.text.count("model_gateway.refused") == 1 and "param=previous_response_id" in caplog.text
    assert "runId=run-1" in caplog.text and rig.transport.calls == []
    with pytest.raises(HarnessRefusal) as unauthenticated:
        await respond(rig, None, responses_body(previous_response_id="resp_1"))
    assert unauthenticated.value.reason_code == "token_missing"


# P6.5 GW-13, GW-14: a Responses call is budgeted exactly as a chat call.


@pytest.mark.parametrize("stream", [False, True])
async def test_gw13_a_responses_call_over_what_is_left_is_refused_before_the_provider(
    rig: GatewayRig, stream: bool
) -> None:
    binding = rig.pin(budget=RunBudget(max_cost_usd=0.001, max_tokens=None))
    await prespend(rig, Amount(990, 0))
    with pytest.raises(BudgetInsufficient) as refused:
        await respond(rig, rig.token(binding), responses_body(input="x" * 400, stream=stream))
    assert refused.value.http_status == 402
    assert rig.transport.calls == []
    assert (await rig.ledger.remaining(account())).reserved == ZERO


@pytest.mark.parametrize("stream", [False, True])
async def test_gw14_a_responses_call_is_reserved_then_charged_its_usage(rig: GatewayRig, stream: bool) -> None:
    rig.transport.events = [reply_events("hello", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin()
    outcome = await respond(rig, rig.token(binding), responses_body(stream=stream))
    if isinstance(outcome, ChatStream):
        _ = [e async for e in outcome.chunks]
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved) == (Amount(450, 110), ZERO)
    held = next(iter(rig.ledger._held.values())).reservation  # pyright: ignore[reportPrivateUsage]
    assert held.key.startswith("resp_") and held.granted.usd_micros > 450

