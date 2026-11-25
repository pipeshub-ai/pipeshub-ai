"""DUR2-S2, S6, S7: post-run steps run at most once per run, journaled as ``post_run.<key>`` slots."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.failpoints import Raise
from harness_core.kernel.core.scope import RunScope
from harness_core.kernel.durable.slots import (
    POST_RUN_SLOT_PREFIX,
    restore_persisted_slots,
)
from harness_core.kernel.fold import RunState
from harness_core.testing import armed

from .support import Crash, World, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.kernel.durable.ports import RunEnding


class Recorder:
    key = "learn"

    def __init__(self, fail: bool = False) -> None:
        self.endings: list[RunEnding] = []
        self._fail = fail

    async def run(self, ending: RunEnding) -> JsonValue:
        self.endings.append(ending)
        if self._fail:
            raise RuntimeError("store down")
        return {"seen": len(ending.tool_calls)}


def _world() -> World:
    world = World()
    world.admit()
    return world


def _slots(world: World) -> list[dict[str, JsonValue]]:
    return [
        {k: v for k, v in dict(e.data["slots"]).items() if k.startswith(POST_RUN_SLOT_PREFIX)}  # type: ignore[arg-type]
        for e in world.events("loop.state_saved")
        if any(k.startswith(POST_RUN_SLOT_PREFIX) for k in dict(e.data["slots"]))  # type: ignore[arg-type]
    ]


async def test_dur2_s7_a_post_run_step_runs_once_with_the_runs_ending() -> None:
    world = _world()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("the answer"))
    step = Recorder()
    done = await world.run(world.kernel(port, post_run=[step]))
    assert done.status == "completed"
    (ending,) = step.endings
    assert ending.outcome == "answered"
    assert [(c.name, c.arguments) for c in ending.tool_calls] == [("lookup", {"q": "x"})]
    assert ending.texts[-1] == "the answer"
    assert _slots(world) == [
        {"post_run.learn": {"state": "started"}},
        {"post_run.learn": {"state": "done", "result": {"seen": 1}}},
    ]
    terminal = world.events()[-1]
    assert terminal.type == "run.completed"


async def test_dur2_s2_a_crash_after_the_start_never_runs_the_step_again() -> None:
    world = _world()
    _, port = scripted(reply("the answer"), reply("again"))
    step = Recorder()
    with armed({"worker.postrun.after_commit": Raise(Crash)}):
        await world.crash(world.kernel(port, post_run=[step]))
    assert step.endings == []
    done = await world.run(world.kernel(port, post_run=[step]))
    assert done.status == "completed"
    assert step.endings == []
    assert _slots(world) == [{"post_run.learn": {"state": "started"}}]


async def test_dur2_s6_a_step_that_raises_never_changes_the_ending() -> None:
    world = _world()
    _, port = scripted(reply("the answer"))
    done = await world.run(world.kernel(port, post_run=[Recorder(fail=True)]))
    assert done.status == "completed"
    assert _slots(world)[-1] == {"post_run.learn": {"state": "failed", "errorType": "RuntimeError"}}


async def test_dur2_s7_without_steps_nothing_is_journaled() -> None:
    world = _world()
    _, port = scripted(reply("the answer"))
    assert (await world.run(world.kernel(port))).status == "completed"
    assert _slots(world) == []


def test_dur2_s7_restored_scope_slots_leave_post_run_state_out() -> None:
    scope = RunScope.__new__(RunScope)
    restored: list[dict[str, JsonValue]] = []
    scope.restore_extensions = lambda values, _known: restored.append(dict(values))  # type: ignore[method-assign]
    state = RunState(run_id="run1", slots={"post_run.learn": {"state": "done"}, "plan": [1]})
    restore_persisted_slots(scope, state)
    assert restored == [{"plan": [1]}]
