"""Contract suite for ``ProviderAdmission`` adapters (G-45, GA-24): each provider's model calls in flight, by class.

Subclass ``ProviderAdmissionContract`` with an ``admit`` fixture: a callable that returns a fresh adapter of the
given limit with the floor grant off (``floor_every=0``), so the order a slot is served in is the class order alone.
Every check waits on real event-loop time, bounded by ``WAIT_S``, so an adapter that polls (the shared Redis gate)
passes the same suite as one that wakes its waiters at once.

WP56-FAIR: orgs that share one upstream key (one account a deployment serves many orgs on) share its limit fairly.
Inside a class, a freed slot goes to the waiting call of the org holding fewest of the key's slots, oldest first
among equals, so one org's burst never queues another org's call behind all of it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.gateway.priority_gate import AdmissionSlot, ProviderAdmission
    from harness_core.priority import PriorityClass

    Admit = Callable[[int], ProviderAdmission]

WAIT_S = 3.0
# Long enough for a polling adapter to have asked again several times.
STILL_S = 0.6


async def _granted(pending: asyncio.Future[AdmissionSlot]) -> AdmissionSlot:
    return await asyncio.wait_for(pending, WAIT_S)


async def _still_waiting(pending: asyncio.Future[AdmissionSlot]) -> None:
    await asyncio.sleep(STILL_S)
    assert not pending.done(), "granted past the limit"


class ProviderAdmissionContract:
    async def test_pa1_a_provider_takes_at_most_its_limit_and_a_release_lets_the_next_call_in(
        self, admit: Admit
    ) -> None:
        gate = admit(2)
        held = [await _granted(asyncio.ensure_future(gate.acquire("openai", "interactive"))) for _ in range(2)]
        third = asyncio.ensure_future(gate.acquire("openai", "interactive"))
        await _still_waiting(third)
        held[0].release()
        slot = await _granted(third)
        assert slot.waited, "the call queued, and its slot says so"
        for s in (held[1], slot):
            s.release()

    async def test_pa2_a_freed_slot_goes_to_a_person_before_older_background_calls(self, admit: Admit) -> None:
        gate = admit(1)
        held = await _granted(asyncio.ensure_future(gate.acquire("openai", "background")))
        order: list[str] = []

        async def call(name: str, cls: PriorityClass) -> None:
            slot = await gate.acquire("openai", cls)
            order.append(name)
            await asyncio.sleep(0.05)
            slot.release()

        background = asyncio.ensure_future(call("background", "batch"))
        await asyncio.sleep(STILL_S / 2)
        person = asyncio.ensure_future(call("person", "approval_resume"))
        await asyncio.sleep(STILL_S / 2)
        held.release()
        await asyncio.wait_for(asyncio.gather(background, person), 2 * WAIT_S)
        assert order == ["person", "background"]

    async def test_pa3_background_calls_leave_a_quarter_of_the_slots_for_a_person(self, admit: Admit) -> None:
        gate = admit(4)
        held = [await _granted(asyncio.ensure_future(gate.acquire("openai", "background"))) for _ in range(3)]
        fourth = asyncio.ensure_future(gate.acquire("openai", "background"))
        await _still_waiting(fourth)
        person = await _granted(asyncio.ensure_future(gate.acquire("openai", "interactive")))
        for s in (*held, person):
            s.release()
        (await _granted(fourth)).release()

    async def test_pa4_providers_are_limited_apart_and_a_release_is_idempotent(self, admit: Admit) -> None:
        gate = admit(1)
        openai = await _granted(asyncio.ensure_future(gate.acquire("openai", "background")))
        anthropic = await _granted(asyncio.ensure_future(gate.acquire("anthropic", "background")))
        openai.release()
        openai.release()
        again = await _granted(asyncio.ensure_future(gate.acquire("openai", "background")))
        blocked = asyncio.ensure_future(gate.acquire("openai", "background"))
        await _still_waiting(blocked)
        _ = blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
        for s in (again, anthropic):
            s.release()

    async def test_pa5_a_cancelled_waiter_takes_no_slot(self, admit: Admit) -> None:
        gate = admit(1)
        held = await _granted(asyncio.ensure_future(gate.acquire("openai", "interactive")))
        gone = asyncio.ensure_future(gate.acquire("openai", "interactive"))
        await asyncio.sleep(STILL_S / 2)
        _ = gone.cancel()
        with pytest.raises(asyncio.CancelledError):
            await gone
        held.release()
        (await _granted(asyncio.ensure_future(gate.acquire("openai", "background")))).release()

    async def test_pa6_limit_zero_is_no_limit(self, admit: Admit) -> None:
        gate = admit(0)
        assert gate.limit == 0
        slots = [await _granted(asyncio.ensure_future(gate.acquire("openai", "batch"))) for _ in range(50)]
        for s in slots:
            s.release()

    async def test_wp56_fair_1_a_busy_org_does_not_queue_another_orgs_call_behind_its_burst(
        self, admit: Admit
    ) -> None:
        gate = admit(2)
        held = [await _granted(asyncio.ensure_future(gate.acquire("openai", "interactive", org="a"))) for _ in range(2)]
        burst = [asyncio.ensure_future(gate.acquire("openai", "interactive", org="a")) for _ in range(4)]
        await asyncio.sleep(STILL_S / 2)
        other = asyncio.ensure_future(gate.acquire("openai", "interactive", org="b"))
        await _still_waiting(other)
        held[0].release()
        b = await _granted(other)
        assert not any(w.done() for w in burst), "the freed slot went to org b, which held none, not to a's burst"
        b.release()
        held[1].release()
        for w in burst:
            (await _granted(w)).release()

    async def test_wp56_fair_2_class_order_still_comes_before_org_fairness(self, admit: Admit) -> None:
        gate = admit(1)
        held = await _granted(asyncio.ensure_future(gate.acquire("openai", "interactive", org="a")))
        background = asyncio.ensure_future(gate.acquire("openai", "background", org="b"))
        await asyncio.sleep(STILL_S / 2)
        person = asyncio.ensure_future(gate.acquire("openai", "interactive", org="a"))
        await asyncio.sleep(STILL_S / 2)
        held.release()
        first = await _granted(person)
        assert not background.done(), "a person's call of the busier org still goes before another org's background"
        first.release()
        (await _granted(background)).release()

    async def test_wp56_fair_3_an_org_alone_on_a_key_uses_the_whole_limit(self, admit: Admit) -> None:
        gate = admit(3)
        slots = [await _granted(asyncio.ensure_future(gate.acquire("openai", "interactive", org="a"))) for _ in range(3)]
        fourth = asyncio.ensure_future(gate.acquire("openai", "interactive", org="a"))
        await _still_waiting(fourth)
        slots[0].release()
        for s in (*slots[1:], await _granted(fourth)):
            s.release()

    async def test_wp56_fair_4_orgs_alternate_while_both_wait(self, admit: Admit) -> None:
        gate = admit(2)
        held = [await _granted(asyncio.ensure_future(gate.acquire("openai", "interactive", org="a"))) for _ in range(2)]
        order: list[str] = []

        async def call(org: str) -> None:
            slot = await gate.acquire("openai", "interactive", org=org)
            order.append(org)
            await asyncio.sleep(0.15)
            slot.release()

        many = [asyncio.ensure_future(call("a")) for _ in range(4)]
        await asyncio.sleep(STILL_S / 2)
        few = [asyncio.ensure_future(call("b")) for _ in range(2)]
        await asyncio.sleep(STILL_S / 2)
        for s in held:
            s.release()
        await asyncio.wait_for(asyncio.gather(*many, *few), 4 * WAIT_S)
        assert order[:2].count("b") >= 1 and order[:4].count("b") == 2, order


__all__ = ["ProviderAdmissionContract"]
