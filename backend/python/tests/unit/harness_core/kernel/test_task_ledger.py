"""UXR-B1 B1-1..B1-5: a run's plan as task events, whatever executor states it."""

from __future__ import annotations

from harness_core.engines.events import PlanItem
from harness_core.kernel.task_ledger import PLAN_SLOT, TaskLedger


def _shape(events) -> list[tuple[object, ...]]:
    return [(e.type, e.data["taskId"], e.data["title"], e.data["status"], e.critical) for e in events]


def test_b1_1_new_list_creates_each_task_positionally() -> None:
    events = TaskLedger().replace([PlanItem("Find invoices", "in_progress"), PlanItem("Draft", "pending")])
    assert _shape(events) == [
        ("task.created", "task-1", "Find invoices", "in_progress", False),
        ("task.created", "task-2", "Draft", "pending", False),
    ]


def test_b1_2_the_same_list_again_journals_nothing() -> None:
    ledger = TaskLedger()
    plan = [PlanItem("Find invoices", "in_progress")]
    _ = ledger.replace(plan)
    assert ledger.replace(plan) == []


def test_b1_3_only_changed_items_are_updated() -> None:
    ledger = TaskLedger()
    _ = ledger.replace([PlanItem("Find", "in_progress"), PlanItem("Draft", "pending")])
    events = ledger.replace([PlanItem("Find", "completed"), PlanItem("Draft", "pending")])
    assert _shape(events) == [("task.updated", "task-1", "Find", "completed", False)]


def test_b1_4_a_dropped_step_is_cancelled_once() -> None:
    ledger = TaskLedger()
    _ = ledger.replace([PlanItem("Find", "completed"), PlanItem("Match", "pending"), PlanItem("Send", "pending")])
    events = ledger.replace([PlanItem("Find", "completed"), PlanItem("Match", "in_progress")])
    assert _shape(events) == [
        ("task.updated", "task-2", "Match", "in_progress", False),
        ("task.updated", "task-3", "Send", "cancelled", False),
    ]
    assert ledger.replace([PlanItem("Find", "completed"), PlanItem("Match", "in_progress")]) == []


def test_b1_5_a_restored_ledger_knows_the_journaled_plan() -> None:
    first = TaskLedger()
    plan = [PlanItem("Find", "completed", deps=("x",)), PlanItem("Draft", "in_progress", asks_first=True)]
    _ = first.replace(plan)
    resumed = TaskLedger()
    resumed.restore(first.slot())
    assert resumed.replace(plan) == []
    assert PLAN_SLOT in first.slot()


def test_step_ids_deps_and_asks_first() -> None:
    events = TaskLedger().replace(
        [
            PlanItem("Find", "pending", task_id="step_1"),
            PlanItem("Send", "pending", task_id="bad id!", deps=("step_1", "step_1", "bad id!"), asks_first=True),
            PlanItem("   ", "pending"),
        ]
    )
    assert [(e.data["taskId"], e.data["deps"], e.data.get("asksFirst")) for e in events] == [
        ("step_1", [], None),
        ("task-2", ["step_1"], True),
    ]


def test_asks_first_is_never_written_when_the_journal_does_not_accept_it() -> None:
    events = TaskLedger(asks_first=False).replace([PlanItem("Send", "pending", asks_first=True)])
    assert "asksFirst" not in events[0].data


def test_move_changes_known_steps_only() -> None:
    ledger = TaskLedger()
    _ = ledger.replace([PlanItem("Find", "pending", task_id="s1")])
    assert _shape(ledger.move({"s1": "in_progress", "nope": "completed"})) == [
        ("task.updated", "s1", "Find", "in_progress", False)
    ]
    assert ledger.move({"s1": "in_progress"}) == []
