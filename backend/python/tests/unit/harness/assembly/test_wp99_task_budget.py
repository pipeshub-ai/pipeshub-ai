"""WP99-TASK: a hand-off's TaskSpec carries the child's slice of its parent's budget (ADR-RUN-01 D4, D5).

WP-93 left the budget out of ``TaskSpec`` until D4's ``RunBudget`` existed: the engine child was told "about 30
minutes" by its own allowance but not by when its parent needs the result.
"""

from __future__ import annotations

from app.harness.assembly.task_spec import TaskSpec
from harness_core.budget.run_budget import ANSWER_RESERVE_S, RunBudget

NOW = 1_800_000_000.0  # 08:00 UTC
REQUEST = "Research the Onyx changelog and write report.md"


def test_wp99_task_1_the_child_is_told_by_when_its_parent_needs_the_result_and_its_money() -> None:
    parent = RunBudget(4.0, 2_000_000, deadline_at_s=NOW + 3_600)
    spec = TaskSpec.compose(
        request=REQUEST, goal="dig into the docs", context=None, deliverable="report.md", budget=parent, now_s=NOW
    )
    assert spec.budget == parent.child()
    assert spec.budget is not None and spec.budget.deadline_at_s == NOW + 3_600 - ANSWER_RESERVE_S
    text = spec.render()
    line = "This task's time and money: deliver your result by 08:56 UTC (about 56 minutes from now), with at most USD 4.00 of model use."
    assert line in text
    # After the request and the file, before the parent's notes: the person's words come first.
    assert text.index(REQUEST) < text.index("The file to deliver") < text.index(line) < text.index("dig into the docs")


def test_wp99_task_2_a_parent_without_a_deadline_hands_on_no_time_line() -> None:
    # No deadline, or one too far away to bind the hand-off (a clock time without its date would mislead).
    for budget in (None, RunBudget(4.0, 2_000_000), RunBudget(4.0, 2_000_000, deadline_at_s=NOW + 2 * 86_400)):
        spec = TaskSpec.compose(request=REQUEST, goal=None, context=None, budget=budget, now_s=NOW)
        assert spec.budget is None
        assert "time and money" not in spec.render()
