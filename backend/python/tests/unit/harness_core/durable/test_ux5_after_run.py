"""UX5 FU-9..FU-11 (kernel half): work after the answer never delays or changes how the run ends."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.kernel.durable.driver import _DETACHED

from .support import World, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.durable.ports import RunEnding


class Slow:
    """An after-run step whose work waits for the test: the slow suggestions call, deterministically."""

    key = "slow"

    def __init__(self, *, prepare_fails: bool = False, run_fails: bool = False, nothing: bool = False) -> None:
        self.release = asyncio.Event()
        self.prepared: list[str] = []
        self.ran: list[str] = []
        self.discarded: list[str] = []
        self._prepare_fails = prepare_fails
        self._run_fails = run_fails
        self._nothing = nothing

    async def prepare(self, ending: RunEnding) -> object | None:
        if self._prepare_fails:
            raise RuntimeError("ledger down")
        if self._nothing or ending.outcome != "answered":
            return None
        self.prepared.append(ending.texts[-1])
        return ending.texts[-1]

    async def run(self, prepared: object) -> None:
        _ = await self.release.wait()
        if self._run_fails:
            raise RuntimeError("model down")
        self.ran.append(str(prepared))

    async def discard(self, prepared: object) -> None:
        self.discarded.append(str(prepared))


async def _settle() -> None:
    await asyncio.gather(*list(_DETACHED))


async def test_fu9_the_run_completes_while_the_after_run_work_still_waits() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("the answer"))
    step = Slow()
    done = await world.run(world.kernel(port, after_run=[step]))
    assert done.status == "completed"
    assert world.events()[-1].type == "run.completed"
    assert step.prepared == ["the answer"]
    assert step.ran == []
    step.release.set()
    await _settle()
    assert step.ran == ["the answer"]
    assert world.events()[-1].type == "run.completed"


async def test_fu10_after_run_failures_never_change_the_ending() -> None:
    for step in (Slow(prepare_fails=True), Slow(run_fails=True), Slow(nothing=True)):
        world = World()
        world.admit()
        _, port = scripted(reply("the answer"))
        step.release.set()
        done = await world.run(world.kernel(port, after_run=[step]))
        await _settle()
        assert done.status == "completed"
        assert step.ran == []


async def test_fu11_a_step_is_only_started_by_a_landed_terminal_commit() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("the answer"))
    step = Slow()
    step.release.set()
    done = await world.run(world.kernel(port, after_run=[step]))
    await _settle()
    assert done.status == "completed"
    assert step.prepared == step.ran == ["the answer"]
    assert step.discarded == []


async def test_fu11_a_refused_completion_discards_and_the_follow_up_answer_prepares_again() -> None:
    from typing import Any

    from harness_core.journal.types import Complete

    from .support import RUN

    world = World()
    world.admit()
    original = world.journal.commit
    raced: list[bool] = []

    async def commit(request: Any) -> Any:  # noqa: ANN401
        if isinstance(request.transition, Complete) and not raced:
            raced.append(True)
            _ = world.journal.queue_input(RUN, "and also")
        return await original(request)

    world.journal.commit = commit  # type: ignore[method-assign]
    _, port = scripted(reply("first answer"), reply("second answer"))
    step = Slow()
    step.release.set()
    done = await world.run(world.kernel(port, after_run=[step]))
    await _settle()
    assert done.status == "completed"
    assert step.discarded == ["first answer"]
    assert step.ran == ["second answer"]
