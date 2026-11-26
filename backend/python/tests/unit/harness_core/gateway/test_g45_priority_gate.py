"""G-45 / GA-24: the model gateway's per-provider concurrency, served by the run's priority class.

A burst of scheduled or batch engine runs could hold every call a provider takes from the gateway while a person's
turn waited behind them. Each provider now has a limit of calls in flight; a freed slot goes to the highest class
waiting, every fifth to the oldest of any class, and background calls leave a quarter of the slots for the classes a
person waits on.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.model_gateway import ChatCompletion, ChatStream
from harness_core.gateway.priority_gate import PriorityGate
from harness_core.kernel.core.streaming import StreamCompleteEvent
from harness_core.testing import ManualClock
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)
from tests.support.settle import settle

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.priority import PriorityClass


def _gate(limit: int, **options: Any) -> PriorityGate:  # noqa: ANN401
    return PriorityGate(limit, monotonic=ManualClock().monotonic, **options)


async def _turns(count: int = 20) -> None:
    for _ in range(count):
        await asyncio.sleep(0)


async def _until(condition: Callable[[], bool], what: str) -> None:
    # The gateway estimates a call's tokens in a worker thread before it asks for a slot, so a fixed number of loop
    # turns is not enough on a loaded host: wait, for up to 5 s, until the gate shows the call.
    for _ in range(5000):
        if condition():
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"the gate never showed {what}")


async def test_ga24_a_freed_slot_goes_to_the_interactive_call_before_older_background_ones() -> None:
    gate = _gate(1, floor_every=0)
    held = await gate.acquire("openai", "background")
    order: list[str] = []

    async def call(name: str, cls: PriorityClass) -> None:
        slot = await gate.acquire("openai", cls)
        order.append(name)
        slot.release()

    waiting = [asyncio.create_task(call(f"bg-{i}", "background")) for i in range(3)]
    await _turns()
    waiting.append(asyncio.create_task(call("person", "interactive")))
    await _turns()
    assert gate.waiting("openai") == 4
    held.release()
    await asyncio.gather(*waiting)
    assert order == ["person", "bg-0", "bg-1", "bg-2"]


async def test_ga24_floor_every_fifth_grant_goes_to_the_oldest_waiter_so_background_moves() -> None:
    gate = _gate(1, floor_every=5)
    held = await gate.acquire("openai", "interactive")
    order: list[str] = []

    async def call(name: str, cls: PriorityClass) -> None:
        slot = await gate.acquire("openai", cls)
        order.append(name)
        await asyncio.sleep(0)
        slot.release()

    waiting = [asyncio.create_task(call("firing", "background"))]
    await _turns()
    waiting += [asyncio.create_task(call(f"person-{i}", "interactive")) for i in range(6)]
    await _turns()
    held.release()
    await asyncio.gather(*waiting)
    # Grants 2..5 after the held one: the fifth grant in all is the floor's, to the oldest waiter.
    assert order == ["person-0", "person-1", "person-2", "firing", "person-3", "person-4", "person-5"]


async def test_ga24_background_calls_leave_a_quarter_of_the_slots_for_a_person() -> None:
    gate = _gate(8, floor_every=0)
    background = [await gate.acquire("openai", "background") for _ in range(6)]
    blocked = asyncio.create_task(gate.acquire("openai", "batch"))
    await _turns()
    assert not blocked.done(), "6 of 8 in flight: the last 2 are a person's"
    person = await settle(gate.acquire("openai", "interactive"))
    answered = await settle(gate.acquire("openai", "approval_resume"))
    assert gate.in_flight("openai") == 8
    for slot in (person, answered, *background):
        slot.release()
    (await blocked).release()
    assert gate.in_flight("openai") == 0


async def test_providers_are_limited_apart_and_limit_zero_is_no_limit() -> None:
    gate = _gate(1)
    openai = await gate.acquire("openai", "background")
    anthropic = await settle(gate.acquire("anthropic", "background"))
    openai.release()
    anthropic.release()
    unlimited = _gate(0)
    slots = [await unlimited.acquire("openai", "batch") for _ in range(100)]
    assert unlimited.in_flight("openai") == 0
    for slot in slots:
        slot.release()


async def test_a_cancelled_waiter_leaves_the_queue_and_a_release_is_idempotent() -> None:
    gate = _gate(1)
    held = await gate.acquire("openai", "interactive")
    gone = asyncio.create_task(gate.acquire("openai", "interactive"))
    await _turns()
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    assert gate.waiting("openai") == 0
    held.release()
    held.release()
    assert gate.in_flight("openai") == 0


async def test_a_slot_never_given_back_is_taken_back_after_max_hold() -> None:
    clock = ManualClock()
    gate = PriorityGate(1, monotonic=clock.monotonic, max_hold_s=60)
    _leaked = await gate.acquire("openai", "background")
    clock.advance(61)
    again = await settle(gate.acquire("openai", "interactive"))
    assert gate.in_flight("openai") == 1
    again.release()


class _HeldTransport(RecordedTransport):
    """Streams once ``go`` is set: the call holds its provider slot until then."""

    def __init__(self, go: asyncio.Event) -> None:
        super().__init__([reply_events("ok")])
        self.go = go

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[StreamEvent]:  # noqa: ANN401  # pyright: ignore[reportIncompatibleMethodOverride]
        await self.go.wait()
        async for event in super().stream(*args, **kwargs):
            yield event


def _body(stream: bool) -> dict[str, Any]:
    return {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "stream": stream}


async def test_ga24_the_gateway_holds_a_provider_slot_per_call_and_serves_the_interactive_run_first() -> None:
    go = asyncio.Event()
    rig = GatewayRig(transport=_HeldTransport(go), gate=_gate(1, floor_every=0))
    tokens = {
        name: rig.token(rig.pin(name, priority_class=cls))
        for name, cls in (("firing-1", "background"), ("firing-2", "background"), ("turn", "interactive"))
    }
    answered: list[str] = []

    async def call(name: str) -> None:
        outcome = await rig.gateway.chat(tokens[name], {}, body_of(_body(stream=True)))
        assert isinstance(outcome, ChatStream)
        async for _ in outcome.chunks:
            pass
        answered.append(name)

    gate = rig.gateway._gate  # pyright: ignore[reportPrivateUsage]
    assert isinstance(gate, PriorityGate)
    first = asyncio.create_task(call("firing-1"))
    await _until(lambda: gate.in_flight("openai") == 1, "firing-1 holding the slot")
    queued = [asyncio.create_task(call("firing-2"))]
    await _until(lambda: gate.waiting("openai") == 1, "firing-2 queued")
    queued.append(asyncio.create_task(call("turn")))
    await _until(lambda: gate.waiting("openai") == 2, "the turn queued")
    go.set()
    await asyncio.gather(first, *queued)
    assert answered == ["firing-1", "turn", "firing-2"]
    assert gate.in_flight("openai") == 0


async def test_a_completion_and_a_provider_failure_both_give_the_slot_back() -> None:
    rig = GatewayRig(gate=_gate(1))
    token = rig.token(rig.pin("run-1", priority_class="batch"))
    outcome = await rig.gateway.chat(token, {}, body_of(_body(stream=False)))
    assert isinstance(outcome, ChatCompletion)
    gate = rig.gateway._gate  # pyright: ignore[reportPrivateUsage]
    assert gate is not None and gate.in_flight("openai") == 0
    rig.transport.fail = RuntimeError("provider down")
    for stream in (False, True):
        with pytest.raises(Exception):  # noqa: B017 - the provider's failure, as the gateway classifies it
            await rig.gateway.chat(token, {}, body_of(_body(stream=stream)))
        assert gate.in_flight("openai") == 0
    assert isinstance(rig.transport.events[-1], StreamCompleteEvent)
