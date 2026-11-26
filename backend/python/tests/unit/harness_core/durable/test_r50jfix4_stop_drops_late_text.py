"""R50JFIX-4: text the model streams after the person pressed Stop is never their stopped answer.

The lane's /mcp and Slack cancel journeys held the model, cancelled the run, then let the model answer: the reply
ended while the worker's cancel check was still reading the journal, the finished call won the race, its response
was journaled after ``run.cancel_requested`` and the stopped answer held the text the person never asked for
("never-…"). The journal's order decides now, not clocks: a reply that ends while a check that began before it is
reading is decided by that check, and a check that finds the cancel keeps only what streamed before it began.

Real wiring: the durable kernel over the in-memory journal, its cancel watcher reading that journal, a model that
streams, then waits until the test lets it finish.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_core.kernel.durable.ports import RunEnding

from .support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.contracts.generated.common import JsonValue
    from harness_core.kernel.core.messages import AssistantMessage
    from harness_core.kernel.durable import DeltaKind, ModelCall, NewEvent, SampledTurn
    from harness_core.testing.durable import ScriptedModelPort

SAFETY_S = 10.0
LATE = "never-50j4"


@dataclass
class _GatedModel:
    """Streams ``before``, then waits for ``gate``, then answers with the scripted reply (``LATE``)."""

    port: ScriptedModelPort
    before: str = "The Q3 plan "
    provider: str = "scripted"
    transport: str = "scripted"
    started: asyncio.Event = field(default_factory=asyncio.Event)
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    answered: asyncio.Event = field(default_factory=asyncio.Event)

    async def sample(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn:
        await on_delta("text_delta", self.before)
        self.started.set()
        await self.gate.wait()
        sampled = await self.port.sample(call, on_delta)
        self.answered.set()
        return sampled

    def restore(self, raw: bytes, *, provider: str, transport: str) -> Any:  # noqa: ANN401
        return self.port.restore(raw, provider=provider, transport=transport)


@dataclass
class _Wake:
    fired: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self, run_id: str, timeout_s: float) -> bool:
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


class _SlowRead:
    """The journal read the cancel check makes, held in flight until ``go`` (Node answering over the network)."""

    def __init__(self, world: World) -> None:
        self.reading = asyncio.Event()
        self.go = asyncio.Event()
        self.armed = False
        self._read = world.journal.read
        world.journal.read = self  # type: ignore[method-assign]

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if self.armed:
            self.armed = False
            self.reading.set()
            await self.go.wait()
        return await self._read(*args, **kwargs)


async def _race(*, cancel: bool) -> tuple[World, _Recorder, Any]:
    """The lane's race: the check's read is in flight when the held model is let go and its reply ends."""
    world = World()
    world.admit()
    _, port = scripted(reply(LATE))
    model, wake, presenter = _GatedModel(port), _Wake(), _Recorder()
    slow = _SlowRead(world)
    running = asyncio.create_task(world.run(world.kernel(model, cancel_wakeup=wake, presenter=presenter)))  # type: ignore[arg-type]
    await asyncio.wait_for(model.started.wait(), SAFETY_S)
    if cancel:
        world.journal.request_cancel(RUN)
    slow.armed = True
    wake.fired.set()
    await asyncio.wait_for(slow.reading.wait(), SAFETY_S)
    model.gate.set()
    await asyncio.wait_for(model.answered.wait(), SAFETY_S)
    for _ in range(20):
        await asyncio.sleep(0)
    slow.go.set()
    return world, presenter, await asyncio.wait_for(running, SAFETY_S)


async def test_r50jfix4_1_a_reply_that_ends_after_stop_is_not_the_stopped_answer() -> None:
    world, presenter, done = await _race(cancel=True)
    assert done.status == "cancelled"
    [ending] = presenter.endings
    assert ending.outcome == "stopped"
    # What streamed before the worker was told stays (UX3); what came after the request does not.
    assert ending.answer == "The Q3 plan "
    assert all(LATE not in text for text in ending.texts)
    assert world.events("model.response") == []


async def test_r50jfix4_2_a_check_that_finds_no_cancel_lets_the_finished_reply_stand() -> None:
    world, presenter, done = await _race(cancel=False)
    assert done.status == "completed"
    [ending] = presenter.endings
    assert ending.outcome == "answered" and LATE in "".join(ending.texts)
    assert len(world.events("model.response")) == 1
