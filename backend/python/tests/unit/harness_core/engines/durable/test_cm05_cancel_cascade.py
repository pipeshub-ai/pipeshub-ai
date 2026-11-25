"""CM-05 (C2.64): cancelling a parent cascades to its running engine children, every child interrupted within 5 s.

The parent and two engine children each hold a live engine turn (their model call blocks on ``hold``), driven by
the real ``EngineKernel`` with the production poll interval (``POLL_S``) over ``InMemoryJournal``, whose
``request_cancel`` cascades as Node's ``cancelChildren`` does. Measured from the cancel to every
run's journal reading ``cancelled`` with its attempt over, on a virtual-time loop (the poll and the hold are the
loop's timers, so the bound is exact and not the host's load): once with the wake Node sends each cancelled run
(``wakeOnCancel``), once with every wake lost, when only the journal poll finds the cancel.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.interactions.journal import POLL_S
from harness_core.interactions.memory import NoWakeup
from harness_core.journal.children import ChildExecutorRequest, ChildRequest
from harness_core.testing.engines.durable import EngineWorld, engine_run_spec
from harness_core.testing.virtual_time import run_on_virtual_time

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.journal.types import JournalEvent

PARENT = "run-cm05-parent"
CASCADE_BOUND_S = 5.0
HELD = "hold\nsay never"


def _parent_spec() -> RunSpec:
    """An engine run that may admit two children, each on its own fork of the session's workspace."""
    spec = engine_run_spec(max_children=2).model_dump(mode="json", by_alias=True)
    spec["extensions"] = {
        "x.pipeshub": {
            "knowledge": {"apps": [], "kb": [], "strictScope": False},
            "mcp": [],
            "skills": [],
            "mounts": ["/workspace"],
            "via": "web",
        }
    }
    return validate_contract(RunSpec, spec)


async def _held_family(world: EngineWorld) -> list[str]:
    """The parent admits two engine children, then every run of the family is held mid-turn in its model call."""
    world.admit(PARENT, HELD, spec=_parent_spec())
    claim = await world.journal.claim(PARENT, worker_id="w-spawn")
    assert claim is not None
    children: list[str] = []
    for i in range(2):
        request = ChildRequest(f"t1.{i}", HELD, executor=ChildExecutorRequest())
        children.append((await world.journal.spawn(claim.token, request)).child_run_id)
    await world.journal.release(claim.token, reason="spawned")
    return children


async def _cascade(*, woken: bool) -> tuple[float, EngineWorld, list[str]]:
    world = EngineWorld()
    children = await _held_family(world)
    family = [PARENT, *children]
    kernel_overrides: dict[str, object] = {"poll_s": POLL_S}
    if not woken:
        kernel_overrides["interactions"] = NoWakeup()
    attempts = {run: asyncio.ensure_future(world.attempt(run, kernel=world.kernel(**kernel_overrides))) for run in family}
    try:
        await asyncio.wait_for(_all_held(world, family), 10)
        loop = asyncio.get_running_loop()
        started = loop.time()
        world.journal.request_cancel(PARENT)
        if woken:
            for run in family:
                world.wakeup.wake(run)
        _ = await asyncio.wait_for(asyncio.gather(*attempts.values()), CASCADE_BOUND_S + 5)
        elapsed = loop.time() - started
    finally:
        for attempt in attempts.values():
            _ = attempt.cancel()
    return elapsed, world, children


async def _all_held(world: EngineWorld, family: list[str]) -> None:
    """Every run of the family is running with its engine's model call held."""
    # Three runs' claims and three model calls: no one event says all of them happened.
    for _ in range(1000):
        claimed = all(world.journal.status(run) == "running" for run in family)
        if claimed and len(world.gateway.model_calls) >= len(family):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the family never got held mid-turn")


async def _inline[T](fn: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    return fn(*args, **kwargs)


def _events(world: EngineWorld, run: str) -> list[JournalEvent]:
    """The run's events, on whichever stream it writes (a child writes its own)."""
    return [e for stream in world.journal.stream_ids() for e in world.journal.events(stream) if e.run_id == run]


@pytest.mark.parametrize("woken", [True, False], ids=["woken_as_node_does", "wake_lost_poll_only"])
def test_cm05_cancelling_the_parent_interrupts_every_running_child_within_5_s(
    woken: bool, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="harness_core.engines.durable")
    # The workspace sync spools on a thread; the virtual clock would race ahead of it to the kernel's next timer.
    monkeypatch.setattr(asyncio, "to_thread", _inline)
    elapsed, world, children = run_on_virtual_time(lambda: _cascade(woken=woken))
    assert elapsed <= CASCADE_BOUND_S, f"the cascade took {elapsed:.2f} s"
    family = (PARENT, *children)
    for run in family:
        assert world.journal.status(run) == "cancelled", run
        types = [e.type for e in _events(world, run)]
        assert types.count("run.cancel_requested") == 1, (run, types)
        assert types.count("run.cancelled") == 1, (run, types)
        # A child that ends before its parent is joined as cancelled, never as an answer.
        joined = [e.data["outcome"] for e in _events(world, run) if e.type == "child.completed"]
        assert set(joined) <= {"cancelled"}, (run, joined)
        finished = [e.data for e in _events(world, run) if e.data.get("kind") == "finished"]
        assert [f["data"] for f in finished] == [{"success": False, "reason": "cancelled"}], (run, finished)
    # Each engine turn was stopped mid-call (interrupted), not left to answer.
    ended = [r.getMessage() for r in caplog.records if r.getMessage().startswith("engine.kernel.turn_ended")]
    for run in family:
        (line,) = [m for m in ended if f"runId={run} " in m]
        assert "reason=interrupted" in line and "cancelled=True" in line, line
