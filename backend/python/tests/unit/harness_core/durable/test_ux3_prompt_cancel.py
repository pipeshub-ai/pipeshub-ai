"""UX3 ST-3..ST-5: Stop interrupts the model call or the reads in flight, and the
stopped answer is the text the user saw stream.

No sleeps: the model blocks until it is cancelled, and the wake is an event
the test sets after Node journals the cancel.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.kernel.durable.ports import RunEnding

from .support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.contracts.generated.common import JsonValue
    from harness_core.kernel.core.messages import AssistantMessage
    from harness_core.kernel.durable import DeltaKind, ModelCall, NewEvent, SampledTurn
    from harness_core.tools import ToolContext, ToolExecution

SAFETY_S = 10.0


@dataclass
class _HangingModel:
    """Streams ``streamed`` then waits for the call to be cancelled."""

    streamed: tuple[str, ...] = ("The Q3 plan ", "is to grow")
    provider: str = "scripted"
    transport: str = "scripted"
    started: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False

    async def sample(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        for text in self.streamed:
            await on_delta("text_delta", text)
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


@dataclass
class _Wake:
    """``InteractionWakeup`` a test fires; never times out on its own."""

    fired: asyncio.Event = field(default_factory=asyncio.Event)
    waits: int = 0

    async def wait(self, run_id: str, timeout_s: float) -> bool:
        self.waits += 1
        await self.fired.wait()
        self.fired.clear()
        return True


@dataclass
class _Recorder:
    endings: list[RunEnding] = field(default_factory=list[RunEnding])

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        return []

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        return []

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        self.endings.append(ending)
        return []


def _world() -> World:
    world = World()
    world.admit()
    return world


async def test_st3_stop_cancels_the_model_call_in_flight() -> None:
    world = _world()
    model, wake, presenter = _HangingModel(), _Wake(), _Recorder()
    kernel = world.kernel(model, cancel_wakeup=wake, presenter=presenter)  # type: ignore[arg-type]
    running = asyncio.create_task(world.run(kernel))
    await asyncio.wait_for(model.started.wait(), SAFETY_S)
    world.journal.request_cancel(RUN)
    wake.fired.set()
    done = await asyncio.wait_for(running, SAFETY_S)
    assert done.status == "cancelled"
    assert model.cancelled
    assert world.journal.status(RUN) == "cancelled"
    assert world.events("model.response") == []


async def test_st5_the_stopped_answer_is_what_streamed() -> None:
    world = _world()
    model, wake, presenter = _HangingModel(), _Wake(), _Recorder()
    kernel = world.kernel(model, cancel_wakeup=wake, presenter=presenter)  # type: ignore[arg-type]
    running = asyncio.create_task(world.run(kernel))
    await asyncio.wait_for(model.started.wait(), SAFETY_S)
    world.journal.request_cancel(RUN)
    wake.fired.set()
    _ = await asyncio.wait_for(running, SAFETY_S)
    [ending] = presenter.endings
    assert ending.outcome == "stopped"
    assert ending.answer == "The Q3 plan is to grow"


async def test_st3_a_wake_without_a_cancel_lets_the_call_go_on() -> None:
    world = _world()
    model, wake = _HangingModel(), _Wake()
    kernel = world.kernel(model, cancel_wakeup=wake)  # type: ignore[arg-type]
    running = asyncio.create_task(world.run(kernel))
    await asyncio.wait_for(model.started.wait(), SAFETY_S)
    waits = wake.waits
    wake.fired.set()
    for _ in range(50):
        if wake.waits > waits:
            break
        await asyncio.sleep(0)
    assert not running.done() and not model.cancelled
    world.journal.request_cancel(RUN)
    wake.fired.set()
    assert (await asyncio.wait_for(running, SAFETY_S)).status == "cancelled"


async def test_st5_stopped_between_steps_keeps_no_partial() -> None:
    world = _world()
    presenter = _Recorder()
    _, port = scripted(reply("", ("lookup", {"q": "a"})), reply("done"))
    world.on_read = lambda w: w.journal.request_cancel(RUN)
    done = await world.run(world.kernel(port, presenter=presenter))
    assert done.status == "cancelled"
    [ending] = presenter.endings
    assert ending.outcome == "stopped" and ending.answer is None


async def test_st4_stop_cancels_reads_in_flight() -> None:
    world = _world()
    wake = _Wake()
    reading = asyncio.Event()
    cancelled: list[str] = []

    async def slow_lookup(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        reading.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(ctx.invocation_id)
            raise
        raise AssertionError("unreachable")

    world.tools.add("slow_lookup", slow_lookup, tags=[("category", "read")])
    _, port = scripted(reply("", ("slow_lookup", {"q": "a"})), reply("done"))
    running = asyncio.create_task(world.run(world.kernel(port, cancel_wakeup=wake)))
    await asyncio.wait_for(reading.wait(), SAFETY_S)
    world.journal.request_cancel(RUN)
    wake.fired.set()
    done = await asyncio.wait_for(running, SAFETY_S)
    assert done.status == "cancelled"
    assert cancelled == ["t1.0"]
    outcomes = {str(e.data["invocationId"]): str(e.data["status"]) for e in world.events("tool.outcome")}
    assert outcomes == {"t1.0": "cancelled"}
