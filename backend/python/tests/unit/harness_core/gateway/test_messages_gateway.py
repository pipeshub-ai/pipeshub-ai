"""E3c MG-1..MG-9: the model gateway core on the Messages wire, over E3's in-memory rig.

MG-3 (E3's security cases) runs from E3b's table in ``test_responses_gateway.py``,
where ``WIRES`` holds every engine wire.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.errors import BudgetExhausted, BudgetInsufficient
from harness_core.access.types import ENGINE_MODEL_ID, RunBudget
from harness_core.budget.types import ZERO, Amount
from harness_core.gateway.cut_calls import DEFAULT_HOLD_TTL_S
from harness_core.gateway.messages_wire import MESSAGES_WIRE
from harness_core.gateway.model_gateway import (
    ChatCompletion,
    ChatStream,
    ProviderCallFailed,
)
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import GatewayRig, body_of, reply_events
from tests.unit.harness_core.gateway.test_messages_wire import claude_code_request
from tests.unit.harness_core.gateway.test_model_gateway import account, prespend, spent
from tests.unit.harness_core.gateway.test_responses_gateway import messages_body

if TYPE_CHECKING:
    from harness_core.gateway.wire import GatewayWire, WireRequest

TOOL = {"name": "get_time", "description": "Time in a city", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}
BETAS = {"anthropic-version": "2023-06-01", "anthropic-beta": "claude-code-20250219,effort-2025-11-24"}


async def send(
    rig: GatewayRig, token: str | None, payload: object, headers: dict[str, str] | None = None
) -> ChatCompletion | ChatStream:
    wire: GatewayWire[WireRequest] = MESSAGES_WIRE  # pyright: ignore[reportAssignmentType]
    return await rig.gateway.serve(wire, token, BETAS if headers is None else headers, body_of(payload))


@pytest.fixture
def rig() -> GatewayRig:
    return GatewayRig()


async def test_mg1_non_streaming_answer_under_the_neutral_id(rig: GatewayRig) -> None:
    binding = rig.pin()
    outcome = await send(rig, rig.token(binding), messages_body(system="be brief", tools=[TOOL]))
    assert isinstance(outcome, ChatCompletion)
    assert outcome.body["type"] == "message" and outcome.body["model"] == ENGINE_MODEL_ID
    assert outcome.body["content"] == [{"type": "text", "text": "hello"}]
    assert str(outcome.body["id"]).startswith("msg_")
    call = rig.transport.calls[0]
    assert call.system == "be brief" and not call.stream
    assert call.messages == [UserMessage(content="hi")]
    assert call.tools is not None and call.tools[0].name == "get_time"


async def test_mg1_claude_codes_tool_round_trip_reaches_the_transport_in_kernel_types(rig: GatewayRig) -> None:
    binding = rig.pin()
    body, headers = claude_code_request()
    headers = {k: v for k, v in headers.items() if k != "authorization"}
    outcome = await send(rig, rig.token(binding), body, headers)
    assert isinstance(outcome, ChatStream)
    _ = [e async for e in outcome.chunks]
    sent = rig.transport.calls[0].messages
    assert [type(m) for m in sent] == [UserMessage, SystemMessage, AssistantMessage, ToolMessage, SystemMessage]
    assert sent[2] == AssistantMessage(tool_calls=[ToolCall(id="toolu_1", name="Bash", arguments={
        "command": "echo hi", "description": "<redacted>"})])


async def test_mg2_streaming_is_committed_once_under_the_message_id(rig: GatewayRig, caplog: pytest.LogCaptureFixture) -> None:
    rig.transport.events = [TextDeltaEvent(delta="hel"), TextDeltaEvent(delta="lo"),
                            reply_events("hello", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin()
    caplog.set_level(logging.INFO)
    outcome = await send(rig, rig.token(binding), messages_body(stream=True))
    assert isinstance(outcome, ChatStream) and outcome.terminal == b""
    events = [e async for e in outcome.chunks]
    assert events[0]["type"] == "message_start" and events[-1]["type"] == "message_stop"
    assert events[-2]["usage"] == {"input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 0,
                                   "cache_read_input_tokens": 0}
    assert outcome.frame(events[0]).startswith(b"event: message_start\n")
    assert await spent(rig) == Amount(450, 110)
    assert caplog.text.count("model_gateway.call") == 1 and "api=anthropic_messages" in caplog.text
    held = next(iter(rig.ledger._held.values())).reservation  # pyright: ignore[reportPrivateUsage]
    assert held.key.startswith("msg_")


# MG-4 / P6.5 GW-13, GW-14 and S1a MG-27, MG-28: a Messages call is budgeted exactly as a chat call.


@pytest.mark.parametrize("stream", [False, True])
async def test_mg4_budgets_refuse_before_the_provider(rig: GatewayRig, stream: bool) -> None:
    binding = rig.pin(budget=RunBudget(max_cost_usd=1.0, max_tokens=None))
    await prespend(rig, Amount(1_000_000, 10))
    with pytest.raises(BudgetExhausted) as exhausted:
        await send(rig, rig.token(binding), messages_body(stream=stream))
    assert exhausted.value.http_status == 402
    tight = rig.pin("run-2", budget=RunBudget(max_cost_usd=None, max_tokens=10))
    with pytest.raises(BudgetInsufficient):
        await send(rig, rig.token(tight), messages_body(stream=stream, messages=[{"role": "user", "content": "x" * 400}]))
    assert rig.transport.calls == []
    assert (await rig.ledger.remaining(account())).reserved == ZERO


@pytest.mark.parametrize("stream", [False, True])
async def test_mg4_a_call_is_reserved_then_committed_its_usage(rig: GatewayRig, stream: bool) -> None:
    rig.transport.events = [reply_events("hello", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin()
    outcome = await send(rig, rig.token(binding), messages_body(stream=stream))
    if isinstance(outcome, ChatStream):
        _ = [e async for e in outcome.chunks]
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved) == (Amount(450, 110), ZERO)
    held = next(iter(rig.ledger._held.values())).reservation  # pyright: ignore[reportPrivateUsage]
    assert held.key.startswith("msg_") and held.granted.usd_micros > 450


async def test_mg5_thinking_in_the_history_is_dropped_once_never_logged(
    rig: GatewayRig, caplog: pytest.LogCaptureFixture
) -> None:
    binding = rig.pin()
    caplog.set_level(logging.INFO)
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "secret-reasoning", "signature": "opaque-signature-blob"},
            {"type": "redacted_thinking", "data": "opaque-redacted-blob"},
            {"type": "text", "text": "hello"},
        ]},
        {"role": "user", "content": "again"},
    ]
    outcome = await send(rig, rig.token(binding), messages_body(messages=history, thinking={"type": "adaptive"}))
    assert isinstance(outcome, ChatCompletion)
    lines = [r.getMessage() for r in caplog.records if "model_gateway.reasoning_dropped" in r.getMessage()]
    assert len(lines) == 1 and "items=2" in lines[0]
    for blob in ("secret-reasoning", "opaque-signature-blob", "opaque-redacted-blob"):
        assert blob not in caplog.text
    assert rig.transport.calls[0].messages[1] == AssistantMessage(content="hello")  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(("pinned", "asked", "sent"), [("high", "low", "high"), (None, "low", "low"), (None, None, None)])
async def test_mg6_the_pinned_effort_wins(rig: GatewayRig, pinned: str | None, asked: str | None, sent: str | None) -> None:
    binding = rig.pin(effort=pinned)
    config = {"effort": asked} if asked else None
    await send(rig, rig.token(binding), messages_body(output_config=config))
    assert rig.transport.calls[0].effort == sent


async def test_mg7_a_failure_before_output_releases_the_reservation(rig: GatewayRig) -> None:
    rig.transport.fail = TransportError("Rate limit", status_code=429)
    binding = rig.pin()
    with pytest.raises(ProviderCallFailed) as failed:
        await send(rig, rig.token(binding), messages_body(stream=True))
    assert (failed.value.status, failed.value.code) == (429, "rate_limited")
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved) == (ZERO, ZERO)


async def test_mg7_a_failure_after_output_is_an_error_event_charged_once(rig: GatewayRig) -> None:
    rig.transport.events = [TextDeltaEvent(delta="partial " * 10), reply_events("never")]
    rig.transport.fail = TransportError("boom", status_code=503)
    rig.transport.fail_after = 1
    binding = rig.pin()
    outcome = await send(rig, rig.token(binding), messages_body(stream=True))
    assert isinstance(outcome, ChatStream)
    events = [e async for e in outcome.chunks]
    assert events[-1] == {"type": "error", "error": {"type": "api_error", "message": "The provider is unavailable [gateway:provider_unavailable]"}}
    assert not any(e["type"] == "message_stop" for e in events)
    charged = await spent(rig)
    assert charged.tokens > 0 and charged.usd_micros > 0
    # The call's reservation went back; what stays held is its bound's rest, until the hold's time (WP-66).
    left = await rig.ledger.remaining(account())
    assert left.reserved.usd_micros > 0 and left.estimated == 1
    rig.clock.advance(DEFAULT_HOLD_TTL_S + 1)
    assert (await rig.ledger.remaining(account())).reserved == ZERO


@pytest.mark.parametrize(
    ("headers", "extra", "param"),
    [
        ({"anthropic-beta": "claude-code-20250219,afk-mode-2026-01-31,context-1m-2025-08-07"}, {},
         "anthropic-beta"),
        (BETAS, {"safeguards": [{"type": "dangerous_tool_use", "classifier_context": {"home_dir": "/home/secret-user"}}]},
         "safeguards"),
        (BETAS, {"stop_sequences": ["SECRET-STOP"]}, "stop_sequences"),
    ],
)
async def test_mg8_refusals_are_logged_once_with_names_never_values(
    rig: GatewayRig, headers: dict[str, str], extra: dict[str, Any], param: str, caplog: pytest.LogCaptureFixture
) -> None:
    binding = rig.pin()
    token = rig.token(binding)
    caplog.set_level(logging.WARNING)
    with pytest.raises(HarnessRefusal) as refused:
        await send(rig, token, messages_body(**extra), headers)
    assert refused.value.reason_code == "unsupported_parameter"
    lines = [r.getMessage() for r in caplog.records if "model_gateway.refused" in r.getMessage()]
    assert len(lines) == 1 and f"param={param}" in lines[0] and "runId=run-1" in lines[0]
    if param == "anthropic-beta":
        assert "detail=afk-mode-2026-01-31,context-1m-2025-08-07" in lines[0]
    for value in ("/home/secret-user", "SECRET-STOP", token):
        assert value not in caplog.text
    assert rig.transport.calls == []


async def test_mg9_count_tokens_admits_without_a_provider_or_a_charge(
    rig: GatewayRig, caplog: pytest.LogCaptureFixture
) -> None:
    binding = rig.pin()
    caplog.set_level(logging.INFO)
    tokens = await rig.gateway.count_tokens(
        MESSAGES_WIRE, rig.token(binding), BETAS, body_of({"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hello " * 50}], "tools": [TOOL]})
    )
    assert tokens > 50
    assert rig.transport.calls == []
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved) == (ZERO, ZERO)
    assert "model_gateway.count_tokens" in caplog.text
    with pytest.raises(HarnessRefusal) as missing:
        await rig.gateway.count_tokens(MESSAGES_WIRE, None, BETAS, body_of(messages_body()))
    assert missing.value.reason_code == "token_missing"
    with pytest.raises(HarnessRefusal) as other:
        await rig.gateway.count_tokens(MESSAGES_WIRE, rig.token(binding), BETAS, body_of(messages_body(model="claude-opus-4-8")))
    assert other.value.reason_code == "model_not_granted"
