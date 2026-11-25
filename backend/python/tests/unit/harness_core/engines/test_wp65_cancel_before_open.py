"""WP65-CANCEL: cancelling an engine turn that has not opened its engine yet.

A Claude Code turn waited for its person's sign-in when the parent was cancelled. ``EngineController.cancel``
answered False (no turn was registered until the engine opened), so the kernel asked again every poll for about
13 minutes and the run ended ``failed``/``signin_timeout``. A cancel now ends the turn in whatever wait it is in:
signing in, acquiring its sandbox, materializing its files; what it held is released, and the turn says cancelled.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tests.unit.harness_core.engines.test_controller import RUN, World, task
from tests.unit.harness_core.engines.test_controller_signin import (
    OWN,
    FakeRelay,
    _adapter,
    _world,
)

if TYPE_CHECKING:
    from harness_core.engines.controller import EngineTurnOutcome


async def _cancelled(world: World, running: asyncio.Future[EngineTurnOutcome]) -> EngineTurnOutcome:
    assert await world.controller.cancel(world.run(), reason="cancel_requested") is True
    return await asyncio.wait_for(running, 5)


async def test_wp65_cancel_1_a_turn_waiting_for_its_sign_in_ends_cancelled_and_signed_out() -> None:
    relay = FakeRelay()
    world = _world(relay=relay)
    adapter = _adapter(world)
    adapter.sign_in_waits = asyncio.Event()
    running = asyncio.ensure_future(world.controller.run_turn(world.run(own_login=OWN), task(0, "say done")))
    await asyncio.wait_for(adapter.reached["sign_in"].wait(), 5)
    outcome = await _cancelled(world, running)
    assert (outcome.cancelled, outcome.ended, outcome.reason) == (True, False, "interrupted")
    assert adapter.calls == ["own_login", "sign_out", "sign_in", "sign_out"], "never opened, signed out"
    assert world.broker.sandboxes == {} and relay.kept == {}
    assert await world.controller.cancel(world.run(), reason="cancel_requested") is False, "nothing left to stop"


class _HeldBroker:
    """Holds the broker's acquire until released: a sandbox the broker is still starting."""

    def __init__(self, world: World) -> None:
        self.reached = asyncio.Event()
        self.release = asyncio.Event()
        acquire = world.broker.acquire

        async def held(spec, *, lease, idempotency_key):  # type: ignore[no-untyped-def] # noqa: ANN202
            self.reached.set()
            await self.release.wait()
            return await acquire(spec, lease=lease, idempotency_key=idempotency_key)

        world.broker.acquire = held  # type: ignore[method-assign]


async def test_wp65_cancel_2_a_turn_waiting_for_its_sandbox_ends_cancelled_and_releases_it() -> None:
    world = World()
    held = _HeldBroker(world)
    running = asyncio.ensure_future(world.controller.run_turn(world.run(), task(0, "say done")))
    await asyncio.wait_for(held.reached.wait(), 5)
    cancel = asyncio.ensure_future(world.controller.cancel(world.run(), reason="cancel_requested"))
    await asyncio.sleep(0)
    # The broker still finishes starting the sandbox: the turn releases what it was given, never leaks it.
    held.release.set()
    assert await asyncio.wait_for(cancel, 5) is True
    outcome = await asyncio.wait_for(running, 5)
    assert outcome.cancelled is True
    assert [key for _, key in world.broker.acquired] == [f"{RUN}.a1"]
    assert len(world.broker.released) == 1
    assert world.adapter.files_at_open == [], "the engine never opened"  # type: ignore[attr-defined]


async def test_wp65_cancel_3_an_opened_turn_still_stops_through_its_engine() -> None:
    """Once the engine is open, cancel keeps its own path: the engine is interrupted, the turn saves and ends."""
    relay = FakeRelay()
    world = _world(relay=relay)
    adapter = _adapter(world)
    adapter.hold = True
    running = asyncio.ensure_future(world.controller.run_turn(world.run(own_login=OWN), task(0, "say done")))
    await asyncio.wait_for(adapter.reached["open"].wait(), 5)
    # The engine reports itself open a step before the controller registers the turn: no event marks that step.
    while RUN not in world.controller._active:  # pyright: ignore[reportPrivateUsage]  # noqa: ASYNC110
        await asyncio.sleep(0)
    outcome = await _cancelled(world, running)
    assert (outcome.cancelled, outcome.ended) == (True, True)
    assert adapter.calls[:4] == ["own_login", "sign_out", "sign_in", "open"]


async def test_wp65_cancel_4_an_outer_cancellation_is_not_taken_for_a_cancel() -> None:
    """The worker shutting down cancels the turn's task itself: that propagates as it did, it is no outcome."""
    relay = FakeRelay()
    world = _world(relay=relay)
    adapter = _adapter(world)
    adapter.sign_in_waits = asyncio.Event()
    running = asyncio.ensure_future(world.controller.run_turn(world.run(own_login=OWN), task(0, "say done")))
    await asyncio.wait_for(adapter.reached["sign_in"].wait(), 5)
    _ = running.cancel()
    try:
        await asyncio.wait_for(running, 5)
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("an outer cancellation must propagate")
    assert adapter.calls[-1] == "sign_out" and world.broker.sandboxes == {}
