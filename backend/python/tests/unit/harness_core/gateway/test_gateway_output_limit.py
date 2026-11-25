"""P4.17 GW-1..GW-3: an engine's call through the model gateway carries an output limit on every wire, is reserved
at it, and a looping upstream stream ends with the wire's own max-tokens stop and an estimated charge."""

from __future__ import annotations

import math
from typing import Any

import pytest

from harness_core.access.spend import cache_read_weight, output_estimate
from harness_core.access.types import RunBudget
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.messages_wire import MESSAGES_WIRE
from harness_core.gateway.model_gateway import ChatCompletion, ChatStream
from harness_core.gateway.responses_wire import RESPONSES_WIRE
from harness_core.kernel.core.streaming import StreamEvent, TextDeltaEvent
from harness_core.models.output_bound import OFF
from harness_core.testing.model_gateway import GatewayRig, body_of, reply_events
from tests.unit.harness_core.gateway.test_messages_gateway import BETAS
from tests.unit.harness_core.gateway.test_model_gateway import (
    GPT,
    account,
    spent,
    spied,
)
from tests.unit.harness_core.gateway.test_responses_gateway import (
    chat_body,
    messages_body,
    responses_body,
)

# (wire, body builder, the field an engine names its own limit with, headers)
WIRES: dict[str, tuple[Any, Any, str, dict[str, str]]] = {
    "chat": (CHAT_WIRE, chat_body, "max_tokens", {}),
    "chat-completion-tokens": (CHAT_WIRE, chat_body, "max_completion_tokens", {}),
    "responses": (RESPONSES_WIRE, responses_body, "max_output_tokens", {}),
    "messages": (MESSAGES_WIRE, messages_body, "max_tokens", BETAS),
}


async def _serve(rig: GatewayRig, wire: Any, payload: object, headers: dict[str, str]) -> ChatCompletion | ChatStream:  # noqa: ANN401
    binding = rig.pin(routes=(GPT,), budget=RunBudget(max_cost_usd=100.0, max_tokens=None))
    return await rig.gateway.serve(wire, rig.token(binding), headers, body_of(payload))


async def _finish(outcome: ChatCompletion | ChatStream) -> list[dict[str, Any]]:
    if isinstance(outcome, ChatStream):
        return [chunk async for chunk in outcome.chunks]
    return [outcome.body]


@pytest.mark.parametrize("name", list(WIRES))
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(("asked", "sent"), [(4_000, 4_000), (100_000, 32_768), (None, 32_768)])
async def test_gw1_every_wire_sends_the_engines_limit_capped_at_the_engine_limit(
    name: str, stream: bool, asked: int | None, sent: int
) -> None:
    wire, make, field, headers = WIRES[name]
    rig = GatewayRig()
    payload = make(stream=stream)
    payload.pop("max_tokens", None)
    if asked is not None:
        payload[field] = asked
    _ = await _finish(await _serve(rig, wire, payload, headers))
    assert [call.max_output_tokens for call in rig.transport.calls] == [sent]


async def test_gw1_with_limits_off_only_the_engines_own_limit_goes_out() -> None:
    rig = GatewayRig(limits=OFF)
    _ = await _finish(await _serve(rig, CHAT_WIRE, chat_body(max_tokens=100_000), {}))
    _ = await _finish(await _serve(rig, CHAT_WIRE, chat_body(), {}))
    assert [call.max_output_tokens for call in rig.transport.calls] == [100_000, None]


async def test_gw2_the_reservation_is_sized_at_the_calls_limit() -> None:
    rig, spy = spied()
    _ = await _finish(await _serve(rig, CHAT_WIRE, chat_body(max_tokens=4_000), {}))
    _ = await _finish(await _serve(rig, CHAT_WIRE, chat_body(), {}))
    off, off_spy = spied(limits=OFF)
    _ = await _finish(await _serve(off, CHAT_WIRE, chat_body(), {}))
    held = [asked for kind, asked in [*spy.asked, *off_spy.asked] if kind == "reserve"]
    prompt = held[0][0].tokens - 4_000
    # The token minimum is that prompt read from the cache (ENGBUDGET), the same on every call.
    floor = math.ceil(prompt * cache_read_weight(GPT.model, prompt_tokens=prompt))
    assert {asked[1].tokens for asked in held} == {floor}
    reserved = [asked[0].tokens - prompt for asked in held[:2]]
    unlimited = [asked[0].tokens - prompt for asked in held[2:]]
    assert reserved == [4_000, 32_768]
    # Without a limit on the wire the model's published maximum is what bounds the call (gpt-5.4: 128,000).
    assert unlimited == [128_000]


def _looping() -> list[StreamEvent]:
    final = reply_events("never sent")
    return [TextDeltaEvent(delta="The renewal date has passed. ") for _ in range(2_000)] + [final]


@pytest.mark.parametrize(
    ("name", "stopped"),
    [
        ("chat", lambda chunks: any(c.get("choices") and c["choices"][0].get("finish_reason") == "length" for c in chunks)),
        ("responses", lambda chunks: any(c.get("type") == "response.incomplete" for c in chunks)),
        ("messages", lambda chunks: any(c.get("type") == "message_delta" and c["delta"].get("stop_reason") == "max_tokens" for c in chunks)),
    ],
)
async def test_gw3_a_looping_upstream_ends_with_the_wires_max_tokens_stop_and_an_estimate(name: str, stopped: Any) -> None:  # noqa: ANN401
    wire, make, _, headers = WIRES[name]
    rig = GatewayRig()
    rig.transport.events = _looping()
    chunks = await _finish(await _serve(rig, wire, make(stream=True), headers))
    assert stopped(chunks)
    # 2,000 copies were offered; the cut stops the stream after about 150 (a wire's closing events repeat its text).
    text = "".join(str(c) for c in chunks)
    assert text.count("The renewal date has passed.") < 1_000
    # The cut stopped the call at about 150 copies; it is charged at least those (by gpt-5.4's own tokenizer,
    # WP-66), and its budget holds the rest of the wire's bound until the hold's time.
    charged = await spent(rig)
    assert charged.tokens > output_estimate("The renewal date has passed. " * 100, GPT.model) and charged.usd_micros > 0
    left = await rig.ledger.remaining(account())
    sent = rig.transport.calls[-1].max_output_tokens
    assert sent is not None and left.estimated == 1
    assert left.committed.tokens + left.reserved.tokens >= max(sent, charged.tokens)


async def test_gw3_with_limits_off_the_stream_passes_unchanged() -> None:
    rig = GatewayRig(limits=OFF)
    rig.transport.events = _looping()
    chunks = await _finish(await _serve(rig, CHAT_WIRE, chat_body(stream=True), {}))
    assert not any(c.get("choices") and c["choices"][0].get("finish_reason") == "length" for c in chunks)
