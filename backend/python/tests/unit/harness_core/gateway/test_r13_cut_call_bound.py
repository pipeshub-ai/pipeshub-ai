"""R13-EST: a model call the gateway cut short is never charged less than the provider can bill for it.

WP-65 keeps cut calls estimated rather than draining the provider's stream (draining pays for the rest of the
answer). That is safe for a run's budget only when the estimate is an upper bound. WP62-EST charged the output that
streamed and credited the run's last cached prompt; both undercount: reasoning the provider never streamed, and what
it generated after the last streamed event, are billed as output, and a prompt cache is the provider's to keep or
drop. What bounds a cut call's output is the limit it sent on the wire, else the model's published maximum (P4.17);
the gateway's own ``cap_tokens`` only shrinks reservations and bounds nothing the provider does.

WP-66 keeps the bound as what the budget holds (``boundOutputTokens``, the ledger's hold) and charges the person a
best estimate beside it (``outputTokens``, ``test_wp66_honest_meter.py``).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from harness_core.access.spend import OutputAllowance, usage_spend
from harness_core.budget.types import RunAccountRef
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.model_gateway import ChatStream
from harness_core.kernel.budget.pricing import max_output_tokens
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.testing.model_gateway import ROUTE, GatewayRig, body_of, reply_events
from tests.unit.harness_core.gateway.test_wp62_codex_passthrough import (
    _call_lines,
    _chat_body,
    _estimate,
)

if TYPE_CHECKING:
    import pytest

MODEL = ROUTE.config_model or ROUTE.model


def _engine_bound(rig: GatewayRig, asked: int | None = None) -> int:
    # A model the price table does not know is bounded by the allowance's stand-in, as its reservation is.
    published = max_output_tokens(MODEL) or OutputAllowance().default_tokens
    limit = rig.limits.for_engine(asked, MODEL)
    return published if limit is None else min(published, limit)


async def _cut(rig: GatewayRig, token: str, body: dict[str, Any], streamed: str = "partial answer") -> None:
    rig.transport.events = [TextDeltaEvent(delta=streamed), reply_events("never")]
    outcome = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(body))
    assert isinstance(outcome, ChatStream)
    while streamed not in json.dumps(await anext(outcome.chunks)):
        pass
    await outcome.chunks.aclose()  # pyright: ignore[reportAttributeAccessIssue]


async def test_r13_est_1_a_cut_call_is_charged_its_output_bound_never_only_what_streamed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    await _cut(rig, token, _chat_body("z" * 4_000, stream=True))
    line = _call_lines(caplog)[-1]
    assert (line["status"], line["estimated"]) == ("interrupted", "True")
    assert int(line["boundOutputTokens"]) == _engine_bound(rig) > len("partial answer") // 4
    assert int(line["inputTokens"]) == _estimate("z" * 4_000)
    # The budget holds the bound: what is charged plus what stays held is the bound's whole charge.
    left = await rig.ledger.remaining(RunAccountRef("org-1", "run-1"))
    assert left.committed.usd_micros + left.reserved.usd_micros == int(line["costMicroUsd"]) + int(line["heldMicroUsd"])
    assert int(line["heldMicroUsd"]) > 0


async def test_r13_est_2_a_cut_calls_bound_takes_no_cache_credit_the_provider_did_not_report(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Amended by WP-81 (WP81-MGW-1): the bound the budget holds credits no cache; the estimate charged beside it
    credits the run's last cached prompt, as an ended call's does."""
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    long = "x" * 40_000
    rig.transport.events = [reply_events("ok", usage=TokenUsage(input_tokens=9_000, output_tokens=5, cache_read_tokens=8_000))]
    await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_chat_body(long, stream=False)))
    rig.clock.advance(60)
    await _cut(rig, token, _chat_body(long + " and more", stream=True))
    line = _call_lines(caplog)[-1]
    assert line["estimated"] == "True"
    prompt = _estimate(long + " and more")
    assert int(line["inputTokens"]) == prompt
    assert int(line["cacheReadTokens"]) == min(prompt, 9_000)
    bound = usage_spend(TokenUsage(input_tokens=prompt, output_tokens=int(line["boundOutputTokens"])), MODEL, charges_usd=True)
    assert int(line["costMicroUsd"]) + int(line["heldMicroUsd"]) == bound.usd_micros


async def test_r13_est_3_the_limit_the_call_sent_bounds_it(caplog: pytest.LogCaptureFixture) -> None:
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    await _cut(rig, token, {**_chat_body("q", stream=True), "max_tokens": 300})
    assert int(_call_lines(caplog)[-1]["boundOutputTokens"]) == _engine_bound(rig, 300) == 300


async def test_r13_est_4_a_reservation_cap_does_not_shrink_what_a_cut_call_is_charged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = GatewayRig(allowance=OutputAllowance(cap_tokens=1_000))
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    await _cut(rig, token, _chat_body("q", stream=True))
    assert int(_call_lines(caplog)[-1]["boundOutputTokens"]) == _engine_bound(rig) > 1_000


async def test_r13_est_5_streamed_output_beyond_the_bound_is_still_charged(caplog: pytest.LogCaptureFixture) -> None:
    """A transport that ignored the limit: what streamed is billed whatever the bound says."""
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    streamed = "w" * 4 * 400
    await _cut(rig, token, {**_chat_body("q", stream=True), "max_tokens": 50}, streamed=streamed)
    line = _call_lines(caplog)[-1]
    assert int(line["outputTokens"]) == int(line["boundOutputTokens"]) == 400


async def test_r13_est_6_reported_usage_still_wins(caplog: pytest.LogCaptureFixture) -> None:
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    usage = TokenUsage(input_tokens=500, output_tokens=7, cache_read_tokens=256)
    rig.transport.events = [TextDeltaEvent(delta="hi"), reply_events("hi", usage=usage)]
    outcome = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_chat_body("q", stream=True)))
    assert isinstance(outcome, ChatStream)
    _ = [c async for c in outcome.chunks]
    line = _call_lines(caplog)[-1]
    assert (line["estimated"], int(line["outputTokens"]), int(line["cacheReadTokens"])) == ("False", 7, 256)


async def test_r13_est_7_a_messages_call_cut_short_is_bounded_too(caplog: pytest.LogCaptureFixture) -> None:
    """WP-56's Messages passthrough (Claude Code): its cut call settles through the same bound, at its max_tokens,
    held on the budget as WP-66 holds the Chat wire's (R14-EST-7)."""
    from harness_core.gateway.messages_wire import MESSAGES_WIRE

    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    rig.transport.events = [TextDeltaEvent(delta="partial answer"), reply_events("never")]
    body = {"model": "engine-model", "max_tokens": 700, "stream": True, "messages": [{"role": "user", "content": "q"}]}
    outcome = await rig.gateway.serve(MESSAGES_WIRE, token, {}, body_of(body))
    chunks = outcome.chunks  # type: ignore[union-attr]
    while "partial answer" not in json.dumps(await anext(chunks)):
        pass
    await chunks.aclose()
    line = _call_lines(caplog)[-1]
    assert (line["status"], line["estimated"], int(line["cacheReadTokens"])) == ("interrupted", "True", 0)
    # WP-66 x WP-56 (round 14): the passthrough's cut call holds its bound like the Chat wire's, beside its estimate.
    assert int(line["boundOutputTokens"]) == _engine_bound(rig, 700)
    assert int(line["outputTokens"]) <= int(line["boundOutputTokens"])
    assert int(line["heldMicroUsd"]) > 0
    left = await rig.ledger.remaining(RunAccountRef("org-1", "run-1"))
    assert left.estimated == 1
    assert left.committed.usd_micros + left.reserved.usd_micros == int(line["costMicroUsd"]) + int(line["heldMicroUsd"])
