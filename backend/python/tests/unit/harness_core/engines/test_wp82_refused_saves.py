"""WP82-SAVE-1..3, WP82-WALL-1..2: a save the store refuses never ends the turn, a call after the turn ended runs
nothing, and a turn's working time never runs past its run's deadline.

In a long research run, from 5 minutes in, every save of the child's workspace was refused (400). Each
refused save before a tool call failed that call, three of them ran one after another after the engine had already
finished (11.5 minutes), and the refused save at the turn's end raised out of the turn: the attempt was released, the
re-driven attempt began from nothing with a fresh 30 minutes, and the run's deadline ended it while it wrote.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Any, override

import pytest

from harness_core.budget.run_budget import DELIVERY_RESERVE_S as DEADLINE_RESERVE_S
from harness_core.budget.run_budget import Allowance, RunBudget, working_s
from harness_core.engines.controller import (
    TURN_ENDED_TEXT,
    _SavingEndpoint,  # pyright: ignore[reportPrivateUsage]
    _Turn,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.engines.types import RunLimits
from harness_core.gateway.types import McpResult
from harness_core.testing.clock import ManualClock
from harness_core.workspace.memory import InMemoryWorkspaceStore
from harness_core.workspace.types import InvalidChangeSet
from tests.unit.harness_core.engines.test_controller import World, task

if TYPE_CHECKING:
    from harness_core.workspace.changes import ChangeSet


class OutputsOnlyStore(InMemoryWorkspaceStore):
    """Refuses, as Node refused run 6's saves, every commit that writes anything outside the outputs folder."""

    def __init__(self, *, clock: ManualClock) -> None:
        super().__init__(clock=clock)
        self.refused = 0

    @override
    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        changes: ChangeSet = kwargs["changes"]
        if any(not path.startswith("out/") for path in changes.paths):
            self.refused += 1
            raise InvalidChangeSet("WORKSPACE_TOO_LARGE")
        return await super().commit(*args, **kwargs)


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, Any]]:
    return [r.harness for r in caplog.records if getattr(r, "harness_event", "") == name]  # type: ignore[attr-defined]


async def test_wp82_save_1_a_refused_save_never_ends_the_turn_and_the_deliverable_is_still_saved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    store = OutputsOnlyStore(clock=clock)
    world = World(clock=clock, store=store)
    prompt = 'write repos/onyx/README.md # Onyx\ntool lookup {"q": "y"}\nwrite out/report.md # Report\nsay done'
    with caplog.at_level(logging.WARNING):
        outcome = await world.controller.run_turn(world.run(), task(0, prompt))
    assert (outcome.reason, outcome.final_text) == ("completed", "done")
    # The save before the call was refused; the call still ran.
    assert world.endpoint_world.runs == ["lookup"]
    assert await world.head_files() == {"out/report.md": b"# Report"}
    assert [(p.path, p.lifecycle) for p in outcome.published] == [("report.md", "draft")]
    assert [e["reason"] for e in _events(caplog, "engine.turn.save_refused")] == ["before_effect", "turn_end"]
    assert store.refused == 2


async def test_wp82_save_2_when_even_the_deliverables_are_refused_the_turn_keeps_its_last_revision(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()

    class RefusingStore(InMemoryWorkspaceStore):
        @override
        async def commit(self, *args: Any, **kwargs: Any) -> Any:
            raise InvalidChangeSet("WORKSPACE_TOO_LARGE")

    world = World(clock=clock, store=RefusingStore(clock=clock))
    with caplog.at_level(logging.WARNING):
        outcome = await world.controller.run_turn(world.run(), task(0, "write out/report.md # R\nsay done"))
    assert outcome.reason == "completed"
    assert _events(caplog, "engine.turn.outputs_save_refused")


async def test_wp82_save_3_a_call_that_reaches_the_gateway_after_the_turn_ended_runs_nothing() -> None:
    turn = object.__new__(_Turn)
    saves: list[str] = []

    async def save(reason: str) -> None:
        saves.append(reason)
        # The engine finished while this call waited for the save before it.
        turn.ended = True

    turn.ended = False
    turn.save = save  # type: ignore[method-assign]
    assert await turn.before_call() == TURN_ENDED_TEXT
    assert await turn.before_call() == TURN_ENDED_TEXT
    assert saves == ["before_effect"]

    ran: list[str] = []

    class Inner:
        async def call_tool(self, *args: object, **kwargs: object) -> McpResult:
            ran.append("called")
            return McpResult("ok", is_error=False, disposition="executed", retryable=False)

    endpoint = _SavingEndpoint(Inner())  # type: ignore[arg-type]
    endpoint.before = turn.before_call
    answer = await endpoint.call_tool(object(), "lookup", {})  # type: ignore[arg-type]
    assert (answer.text, answer.disposition, ran) == (TURN_ENDED_TEXT, "refused", [])


@pytest.mark.parametrize(
    ("wall", "left", "expected"),
    [
        (1800, None, 1800),
        (1800, 3600.0, 1800),
        (1800, 23 * 60.0, 23 * 60 - DEADLINE_RESERVE_S),
        (None, 900.0, 900 - DEADLINE_RESERVE_S),
        (1800, 100.0, 60),
        (1800, -5.0, 60),
    ],
)
def test_wp82_wall_1_a_turns_working_time_ends_before_its_runs_deadline(
    wall: int | None, left: float | None, expected: int
) -> None:
    # ADR-RUN-01 D4: the run budget owns the arithmetic (``working_s``, which ``RunBudget.turn`` applies).
    assert working_s(wall, left) == expected


def test_wp82_wall_2_the_engine_is_told_and_cut_on_the_deadline_s_time() -> None:
    run = World().run()
    now = 1_000_000.0
    run = dataclasses.replace(
        run,
        profile=dataclasses.replace(run.profile, limits=RunLimits(max_wall_s=1800)),
        budget=RunBudget(None, None, deadline_at_s=now + 23 * 60.0),
    )
    allowance = run.allowance(now)
    assert allowance == Allowance(allowance.steps, 23 * 60 - DEADLINE_RESERVE_S, allowance.usd)
    # The writing reserve comes due on that time, not on the profile's 30 minutes.
    wall, reserve = allowance.wall_s, allowance.synthesis_wall_s()
    assert wall is not None and reserve is not None
    assert allowance.synthesis_due(steps=0, elapsed_s=wall - reserve - 1) is None
    assert allowance.synthesis_due(steps=0, elapsed_s=wall - reserve) == "wall_time"
