"""WP-66: a cut call holds its bound on the budget and charges the person a best estimate.

R13-EST made a call the gateway cut short (the engine left, the run was stopped, the stream failed) charge the most
the provider could bill. The cap stays as safe with two numbers: the budget keeps that bound held, and the person
and the cost ledger are charged what streamed plus what the model made unseen at its observed rate since its last
token. A provider that kept the response is asked once what it billed, through the transport that made the call,
and that replaces the estimate; otherwise the hold goes after its time and the estimate stands. Fake providers only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any, override

import pytest

from harness_core.access.spend import input_estimate, output_estimate, usage_spend
from harness_core.access.types import RunBudget
from harness_core.budget.types import ZERO, RunAccountRef
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.gateway.cut_calls import (
    DEFAULT_HOLD_TTL_S,
    DEFAULT_OUTPUT_TPS,
    CutCalls,
)
from harness_core.gateway.model_gateway import ChatStream
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.streaming import TextDeltaEvent
from harness_core.refusals import HarnessRefusal
from harness_core.testing.model_gateway import (
    ROUTE,
    GatewayRig,
    RecordedTransport,
    StoringTransport,
    body_of,
    reply_events,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.testing.clock import ManualClock

MODEL = ROUTE.config_model or ROUTE.model
STREAMED = "partial answer " * 20
PROMPT = "summarise the quarter"


def _body(text: str = PROMPT, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": "engine-model", "messages": [{"role": "user", "content": text}], "stream": True, **extra}


def _usd(input_tokens: int, output_tokens: int) -> int:
    return usage_spend(TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens), MODEL, charges_usd=True).usd_micros


def _prompt_tokens(text: str = PROMPT) -> int:
    parsed = CHAT_WIRE.parse(_body(text))
    return input_estimate(parsed.messages, parsed.system, parsed.tools, MODEL)


def _lines(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, str]]:
    found = [r.getMessage() for r in caplog.records if f"{event} " in r.getMessage()]
    return [dict(re.findall(r"(\w+)=(\S+)", line)) for line in found]


def account(run_id: str = "run-1", tenant: str = "org-1") -> RunAccountRef:
    return RunAccountRef(tenant, run_id)


async def _open(rig: GatewayRig, token: str, body: dict[str, Any] | None = None) -> ChatStream:
    outcome = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(body or _body()))
    assert isinstance(outcome, ChatStream)
    while STREAMED.strip() not in json.dumps(await anext(outcome.chunks)):
        pass
    return outcome


async def _cut(rig: GatewayRig, token: str, *, after_s: float = 0.0, body: dict[str, Any] | None = None) -> None:
    """A call cut ``after_s`` seconds after its last streamed token (the engine went away)."""
    outcome = await _open(rig, token, body)
    rig.clock.advance(after_s)
    await outcome.chunks.aclose()  # pyright: ignore[reportAttributeAccessIssue]


def _cut_events() -> list[StreamEvent]:
    return [TextDeltaEvent(delta=STREAMED), reply_events("never")]


class _Timed(RecordedTransport):
    """A provider whose answer takes ``seconds`` on the rig's clock before it completes."""

    def __init__(self, clock: ManualClock, seconds: float, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(**kwargs)
        self._clock, self._seconds = clock, seconds

    @override
    async def stream(self, messages: Any, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:  # pyright: ignore[reportIncompatibleMethodOverride]
        async for event in super().stream(messages, *args, **kwargs):
            if event is self.events[-1]:
                self._clock.advance(self._seconds)
            yield event


async def test_wp66_hold_1_a_cut_call_holds_the_bound_while_the_person_is_charged_the_estimate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    rig.transport.events = _cut_events()
    await _cut(rig, token, after_s=2.0)

    produced = output_estimate(STREAMED, MODEL)
    best = produced + int(DEFAULT_OUTPUT_TPS * 2.0)
    line = _lines(caplog, "model_gateway.call")[-1]
    assert (line["estimated"], int(line["outputTokens"]), int(line["boundOutputTokens"])) == ("True", best, 32_768)
    prompt = _prompt_tokens()
    left = await rig.ledger.remaining(account())
    assert left.committed.usd_micros == _usd(prompt, best) == int(line["costMicroUsd"])
    assert left.reserved.usd_micros == _usd(prompt, 32_768) - _usd(prompt, best) == int(line["heldMicroUsd"])
    assert left.estimated == 1
    assert left.used.output_tokens == best, "the run's usage is the estimate it was charged"


async def test_wp66_hold_2_the_estimate_learns_each_models_rate_and_stops_at_the_bound() -> None:
    rig = GatewayRig()
    rig.transport = _Timed(rig.clock, 10.0)
    rig.pin(transport=rig.transport)
    token = rig.token(rig.pin())
    rig.transport.events = [TextDeltaEvent(delta="x"), reply_events("x", usage=TokenUsage(input_tokens=9, output_tokens=2_000))]
    finished = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_body()))
    assert isinstance(finished, ChatStream)
    _ = [chunk async for chunk in finished.chunks]
    before = (await rig.ledger.remaining(account())).committed

    rig.transport.events = _cut_events()
    await _cut(rig, token, after_s=3.0)
    produced = output_estimate(STREAMED, MODEL)
    # 2,000 tokens in 10 s: 200 a second, not the cold-start default.
    charged = (await rig.ledger.remaining(account())).committed - before
    assert charged.usd_micros == _usd(_prompt_tokens(), produced + 600)

    rig.transport.events = _cut_events()
    before = (await rig.ledger.remaining(account())).committed
    await _cut(rig, token, after_s=100_000.0, body=_body(max_tokens=300))
    charged = (await rig.ledger.remaining(account())).committed - before
    assert charged.usd_micros == _usd(_prompt_tokens(), 300), "never above the bound the call ran under"


async def test_wp66_hold_3_a_call_cut_before_its_first_token_counts_from_its_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reasoning the provider never streamed is billed: the unseen time runs from the request."""
    rig = GatewayRig()
    caplog.set_level(logging.INFO)
    token = rig.token(rig.pin())
    rig.transport.events = [reply_events("never")]
    outcome = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_body()))
    assert isinstance(outcome, ChatStream)
    rig.clock.advance(5.0)
    await outcome.aclose()
    line = _lines(caplog, "model_gateway.call")[-1]
    assert (line["status"], int(line["outputTokens"])) == ("interrupted", int(DEFAULT_OUTPUT_TPS * 5.0))


async def test_wp66_recon_1_the_providers_usage_replaces_the_estimate(caplog: pytest.LogCaptureFixture) -> None:
    rig = GatewayRig(cut_calls=CutCalls(reconcile_delay_s=0.05))
    caplog.set_level(logging.INFO)
    provider = StoringTransport(credential="org-1-key", events=_cut_events())
    token = rig.token(rig.pin(transport=provider))
    billed = TokenUsage(input_tokens=40, output_tokens=1_234, cache_read_tokens=0)
    provider.billed["resp_1"] = billed
    await _cut(rig, token, after_s=1.0)
    estimated = await rig.ledger.remaining(account())
    assert estimated.estimated == 1 and estimated.reserved != ZERO, "estimated until the provider says"

    await rig.gateway.reconciled()
    left = await rig.ledger.remaining(account())
    assert left.committed.usd_micros == usage_spend(billed, MODEL, charges_usd=True).usd_micros
    assert (left.reserved, left.estimated) == (ZERO, 0)
    assert (left.used.input_tokens, left.used.output_tokens) == (40, 1_234)
    assert provider.lookups == [("resp_1", "org-1-key")]
    assert _lines(caplog, "model_gateway.reconciled")[-1]["outputTokens"] == "1234"


@pytest.mark.parametrize("answer", ["unknown", "fails", "slow"])
async def test_wp66_recon_2_a_lookup_that_cannot_say_leaves_the_estimate_and_its_hold(
    answer: str, caplog: pytest.LogCaptureFixture
) -> None:
    rig = GatewayRig(cut_calls=CutCalls(reconcile_delay_s=0, lookup_timeout_s=0.05))
    caplog.set_level(logging.INFO)
    provider = StoringTransport(credential="org-1-key", events=_cut_events())
    if answer == "fails":

        async def broken(_response_id: str) -> TokenUsage | None:
            raise ConnectionError("lookup unreachable")

        provider.reported_usage = broken  # type: ignore[method-assign]
    elif answer == "slow":

        async def slow(_response_id: str) -> TokenUsage | None:
            await asyncio.sleep(5)
            return TokenUsage(input_tokens=1, output_tokens=1)

        provider.reported_usage = slow  # type: ignore[method-assign]
    token = rig.token(rig.pin(transport=provider))
    await _cut(rig, token)
    held = await rig.ledger.remaining(account())
    await rig.gateway.reconciled()
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved, left.estimated) == (held.committed, held.reserved, 1)
    event = "model_gateway.reconcile_unavailable" if answer == "unknown" else "model_gateway.reconcile_failed"
    assert _lines(caplog, event)


async def test_wp66_ttl_1_without_a_lookup_the_hold_drops_to_the_estimate_after_its_time() -> None:
    rig = GatewayRig()
    token = rig.token(rig.pin())
    rig.transport.events = _cut_events()
    await _cut(rig, token, after_s=1.0)
    await rig.gateway.reconciled()
    charged = (await rig.ledger.remaining(account())).committed
    rig.clock.advance(DEFAULT_HOLD_TTL_S - 1)
    assert (await rig.ledger.remaining(account())).reserved != ZERO
    rig.clock.advance(2)
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved, left.estimated) == (charged, ZERO, 1), "still shown as estimated"


async def test_wp66_ttl_2_the_hold_time_is_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_GATEWAY_CUT_HOLD_TTL_S", "120")
    monkeypatch.setenv("MODEL_GATEWAY_CUT_OUTPUT_TPS", "25")
    assert (CutCalls.from_env().hold_ttl_s, CutCalls.from_env().default_tps) == (120, 25.0)
    for bad in ("0", "abc", "4000", "-5"):
        monkeypatch.setenv("MODEL_GATEWAY_CUT_HOLD_TTL_S", bad)
        assert CutCalls.from_env().hold_ttl_s == DEFAULT_HOLD_TTL_S
    monkeypatch.setenv("MODEL_GATEWAY_CUT_OUTPUT_TPS", "nan")
    assert CutCalls.from_env().default_tps == DEFAULT_OUTPUT_TPS


async def test_wp66_cap_1_concurrent_cut_calls_never_let_the_cap_be_passed() -> None:
    """Two calls cut at once each keep their bound held, so a third is refused as R13-EST refused it; their
    estimates stay well under the cap, and once the holds' time is up the run may spend again."""
    rig = GatewayRig()
    bound = _usd(_prompt_tokens(), 32_768)
    binding = rig.pin(budget=RunBudget(max_cost_usd=1.8 * bound / 1_000_000, max_tokens=None))
    token = rig.token(binding)
    rig.transport.events = _cut_events()
    first, second = await _open(rig, token), await _open(rig, token)
    rig.clock.advance(1.0)
    await asyncio.gather(first.aclose(), second.aclose())
    left = await rig.ledger.remaining(account())
    assert left.estimated == 2
    assert left.committed.usd_micros + left.reserved.usd_micros >= 2 * bound - 2
    assert left.committed.usd_micros < bound
    assert left.available.usd_micros == 0
    with pytest.raises(HarnessRefusal):
        await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_body()))
    rig.clock.advance(DEFAULT_HOLD_TTL_S + 1)
    rig.transport.events = [reply_events("ok")]
    assert await rig.gateway.serve(CHAT_WIRE, rig.token(binding), {}, body_of(_body()))


async def test_wp66_iso_1_each_lookup_goes_to_the_calls_own_transport_and_account() -> None:
    """Two orgs' cut calls: each provider is asked only for its own response, with its own key; one org's billed
    usage reconciles only its own run."""
    rig = GatewayRig()
    one = StoringTransport(credential="org-1-key", prefix="one", events=_cut_events())
    two = StoringTransport(credential="org-2-key", prefix="two", events=_cut_events())
    token_one = rig.token(rig.pin(tenant="org-1", transport=one))
    token_two = rig.token(rig.pin("run-2", tenant="org-2", transport=two))
    two.billed["two_1"] = TokenUsage(input_tokens=10, output_tokens=77)
    one.billed["two_1"] = TokenUsage(input_tokens=10, output_tokens=99_999)
    await asyncio.gather(_cut(rig, token_one), _cut(rig, token_two))
    await rig.gateway.reconciled()
    assert one.lookups == [("one_1", "org-1-key")]
    assert two.lookups == [("two_1", "org-2-key")]
    org_one, org_two = await rig.ledger.remaining(account()), await rig.ledger.remaining(account("run-2", "org-2"))
    assert (org_one.estimated, org_two.estimated) == (1, 0)
    assert org_two.used.output_tokens == 77
    assert org_one.used.output_tokens != 99_999


async def test_wp66_iso_2_interleaved_calls_on_one_transport_keep_their_own_receipts() -> None:
    rig = GatewayRig()
    shared = StoringTransport(credential="org-1-key", events=_cut_events())
    token_a = rig.token(rig.pin("run-a", transport=shared))
    token_b = rig.token(rig.pin("run-b", transport=shared))
    a = await _open(rig, token_a)
    b = await _open(rig, token_b)
    shared.billed["resp_1"] = TokenUsage(input_tokens=5, output_tokens=111)
    shared.billed["resp_2"] = TokenUsage(input_tokens=5, output_tokens=222)
    await asyncio.gather(b.aclose(), a.aclose())
    await rig.gateway.reconciled()
    assert sorted(shared.lookups) == [("resp_1", "org-1-key"), ("resp_2", "org-1-key")]
    assert (await rig.ledger.remaining(account("run-a"))).used.output_tokens == 111
    assert (await rig.ledger.remaining(account("run-b"))).used.output_tokens == 222


async def test_wp66_iso_3_a_finished_call_is_never_looked_up() -> None:
    rig = GatewayRig()
    provider = StoringTransport(credential="org-1-key", events=[TextDeltaEvent(delta="hi"), reply_events("hi")])
    token = rig.token(rig.pin(transport=provider))
    outcome = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(_body()))
    assert isinstance(outcome, ChatStream)
    _ = [chunk async for chunk in outcome.chunks]
    await rig.gateway.reconciled()
    assert provider.lookups == []
    assert (await rig.ledger.remaining(account())).estimated == 0
