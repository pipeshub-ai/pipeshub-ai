"""WP56-STOP-1 (C9.06): a coding engine's run cut short by a shared budget names its scope, as the native loop's
does. The model gateway saw Node's refusal, not the engine's kernel: the kernel reads whose budget binds the run from
the run's own budget account (Node's read names it, ``Remaining.budget_scope``) when it ends the turn cut short."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.testing.engines.durable import (
    PROGRESS,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)

if TYPE_CHECKING:
    from harness_core.journal.types import Event

RUN = "run-wp56"


def _finished(world: EngineWorld) -> list[dict[str, object]]:
    return [
        e.data["data"]  # type: ignore[index]
        for e in world.journal.events(SESSION)
        if e.type == PROGRESS and e.data.get("kind") == "finished"
    ]


def _completed(world: EngineWorld) -> list[Event]:
    return [e for e in world.journal.events(SESSION) if e.type == "run.completed"]


@pytest.mark.parametrize("scope", ["pool-a", "p", None])
async def test_wp56_stop_1_an_engine_run_cut_by_a_shared_budget_names_it(scope: str | None) -> None:
    world = EngineWorld(metered=True)
    world.gateway.budget_scope = scope
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    adapter.fail_with.append("budget_exhausted")
    world.admit(RUN, "say x")
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    assert [e.data for e in _completed(world)] == [{"outcome": "answered", "truncatedBy": "budget"}]
    [finished] = _finished(world)
    assert finished.get("budgetScope") == scope



@pytest.mark.parametrize(("exhausted", "code", "truncated_by"), [
    (True, "acp_error", "budget"),
    (True, None, "budget"),
    (False, "budget_exhausted", "budget"),
])
async def test_r31journey_fu8_a_budget_cut_is_the_budget_whatever_the_engine_reports(
    exhausted: bool, code: str | None, truncated_by: str,
) -> None:
    """R31JOURNEY-FU-8: Qwen Code 0.26.5 over ACP ends the turn the gateway refused for budget with its own error, not
    the gateway's code, and the job read as an engine error. The run's ledger, at its cap, says it was the budget."""
    world = EngineWorld(metered=True)
    world.gateway.exhausted = exhausted
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    adapter.fail_with.append(code or "")
    world.admit(RUN, "say x")
    disposition = await world.attempt(RUN)
    assert disposition.status == "completed"
    assert [e.data for e in _completed(world)] == [{"outcome": "answered", "truncatedBy": truncated_by}]


async def test_r31journey_fu8_an_engine_error_with_budget_left_is_the_engines() -> None:
    world = EngineWorld(metered=True)
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    adapter.fail_with.append("acp_error")
    world.admit(RUN, "say x")
    disposition = await world.attempt(RUN)
    assert all(e.data.get("truncatedBy") != "budget" for e in _completed(world)), disposition
