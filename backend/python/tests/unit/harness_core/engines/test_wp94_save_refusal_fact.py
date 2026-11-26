"""RUN01-SC-05b / ADR-RUN-01 D1, D6: a save the store refuses during a turn reaches the run's outcome ledger.

The research-run lane found it (``test_run01_research_scenario.py::test_run01_sc05b_*``): a save refused mid-turn was
logged (``engine.turn.save_refused``) and the turn went on (WP-82), but the outcome ledger only ever recorded a
refusal of the handoff's own save, so the run's outcome did not say a save had been refused. The turn now reports
each distinct refusal reason on its outcome, and the kernel records it as ``SaveRefused`` when it builds the ledger.
"""

from __future__ import annotations

from typing import Any, override

from harness_core.testing.clock import ManualClock
from harness_core.workspace.memory import InMemoryWorkspaceStore
from harness_core.workspace.types import InvalidChangeSet
from tests.unit.harness_core.engines.test_controller import World, task


class _RefusesNotes(InMemoryWorkspaceStore):
    @override
    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        if any(p.lstrip("/").startswith("notes/") for p in kwargs["changes"].paths):
            raise InvalidChangeSet("WORKSPACE_TOO_LARGE")
        return await super().commit(*args, **kwargs)


async def test_wp94_save_1_a_turn_reports_each_refused_save_on_its_outcome() -> None:
    clock = ManualClock()
    world = World(clock=clock, store=_RefusesNotes(clock=clock))
    prompt = 'write notes/a.md one\ntool lookup {"q": "y"}\nwrite notes/b.md two\ntool lookup {"q": "z"}\nsay done'
    outcome = await world.controller.run_turn(world.run(), task(0, prompt))
    assert outcome.reason == "completed"
    assert len(outcome.save_refusals) == 1 and outcome.save_refusals[0], outcome.save_refusals


async def test_wp94_save_2_a_turn_whose_saves_all_land_reports_none() -> None:
    world = World()
    outcome = await world.controller.run_turn(world.run(), task(0, "write out/r.md x\nsay done"))
    assert outcome.save_refusals == ()
