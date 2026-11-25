"""UXR-B1 B1-11, B1-12: deep mode journals the plan ``create_plan`` stores, and moves its steps."""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.assembly.deep import (
    DEEP_PHASE_SLOT,
    DeepDirector,
    DeepPhase,
    programmatic_calls,
)
from app.harness.assembly.loop import GOAL_SLOT
from harness_core.engines.events import PlanItem
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.ports import ToolContext, TurnResult
from harness_core.kernel.fold import RunState
from harness_core.kernel.task_ledger import TaskLedger
from harness_core.logs import HarnessLog

from . import world as w
from .test_parity import _durable_turn_one, _full_grant

CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "key")
STEPS = [
    {"id": "find", "description": "Find the open bugs", "domain": "jira", "tool_names": ["jira__search_issues"]},
    {
        "id": "file",
        "description": "File the summary",
        "domain": "jira",
        "tool_names": ["jira__create_issue"],
        "depends_on": ["find"],
    },
]


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def test_b1_11_create_plan_journals_its_steps_pending() -> None:
    agent = w.agent_doc()
    services = w.Services(agent=agent)
    await services.config.set_config("/services/agentHarness/approvals", {"default": {"writes": "ask"}})
    runtime, _, _ = await _durable_turn_one(services, w.pinned_spec(**_full_grant(agent), max_children=w.DEEP_CHILDREN), "deep", "triage")
    done = await runtime.tools.execute(ToolCall(id="c1", name="create_plan", arguments={"steps": STEPS}), CTX)
    assert done.status == "ok"
    tasks = [e for e in done.events if e.type.startswith("task.")]
    assert [(e.type, e.data["taskId"], e.data["status"], e.data["deps"], e.data.get("asksFirst")) for e in tasks] == [
        ("task.created", "find", "pending", [], False),
        ("task.created", "file", "pending", ["find"], True),
    ]


def _director(ledger: TaskLedger) -> DeepDirector:
    return DeepDirector(planning_message="PLAN", max_turns=15, log=HarnessLog.named("test"), plan=ledger)


def _state(phase: DeepPhase) -> RunState:
    from app.agent_loop_lib.core.types import Goal
    from app.agent_loop_lib.modules.pipeline.planner.base import Plan, PlanStep
    from app.harness.assembly.deep import DEEP_PLAN_SLOT

    plan = Plan(goal=Goal(description="triage"), text="t", steps=[PlanStep(**s) for s in STEPS])
    return RunState(
        run_id="run-1",
        slots={
            GOAL_SLOT: {"description": "triage", "loopKind": "orchestrator", "mode": "deep", "clarifyingQuestions": []},
            DEEP_PHASE_SLOT: phase.slot(),
            DEEP_PLAN_SLOT: plan.model_dump(mode="json"),
        },
    )


def test_b1_12_dispatch_starts_the_steps_and_the_join_ends_them() -> None:
    ledger = TaskLedger()
    _ = ledger.replace([PlanItem(s["description"], "pending", task_id=s["id"]) for s in STEPS])
    director = _director(ledger)
    critique = TurnResult("critique_plan", "c2", "ok", '{"passed": true}')
    dispatched = director.before(3, _state(DeepPhase()), [TurnResult("create_plan", "c1", "ok", "{}"), critique])
    assert dispatched.synthetic is not None
    assert [(e.data["taskId"], e.data["status"]) for e in dispatched.events] == [
        ("find", "in_progress"),
        ("file", "in_progress"),
    ]
    from app.agent_loop_lib.modules.pipeline.planner.base import PlanStep

    calls = programmatic_calls([PlanStep(**s) for s in STEPS], 3)
    joined = director.before(
        4,
        _state(DeepPhase(phase="programmatic", approved=True)),
        [TurnResult("spawn_agent", calls[0].id, "ok", "found"), TurnResult("spawn_agent", calls[1].id, "error", "x")],
    )
    assert [(e.data["taskId"], e.data["status"]) for e in joined.events] == [("find", "completed"), ("file", "cancelled")]
    assert "plan.tasks" in joined.slots


def test_a_director_without_a_ledger_journals_no_tasks() -> None:
    director = DeepDirector(planning_message="PLAN", max_turns=15, log=HarnessLog.named("test"))
    critique = TurnResult("critique_plan", "c2", "ok", '{"passed": true}')
    assert director.before(3, _state(DeepPhase()), [critique]).events == ()
