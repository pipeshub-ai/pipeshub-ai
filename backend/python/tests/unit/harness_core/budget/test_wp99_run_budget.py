"""WP99-TREE, WP99-PLAN, WP99-WAIT, WP99-BOUND: the run budget tree (ADR-RUN-01 D4).

Two long research runs found time and money worked out in five places (``allowance.py``, the controller's deadline cap,
the profile limits, ``time_bound.py`` and the kernels), a child's limits not derived from its parent's, and a parent
waiting on a person-held or retired child that kept its own deadline. One ``RunBudget`` now owns the arithmetic.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import RunBudget as GrantBudget
from harness_core.budget.run_budget import (
    ANSWER_RESERVE_S,
    DELIVERY_RESERVE_S,
    MIN_WORKING_S,
    WALL_RESERVE_STEPS,
    Allowance,
    PersonWaits,
    RunBudget,
    budget_scope,
    scoped_bound_s,
)
from harness_core.engines.allowance import allowance_note, plan_words
from harness_core.engines.types import RunLimits
from harness_core.tools.time_bound import DEFAULT_WALL_S, ToolTimeBounds

if TYPE_CHECKING:
    from harness_core.journal.types import Claim

NOW = 1_800_000_000.0
HOUR = 3_600.0
ENGINE = RunLimits(max_steps=1_000, max_wall_s=1_800, max_tokens=10_000_000)


def _claim(*, deadline_at_ms: int | None, depth: int = 0, calendar: str = "2027-01-01T00:00:00Z") -> Claim:
    budgets = SimpleNamespace(max_cost_usd=5.0, max_tokens=10_000_000, calendar_deadline=calendar)
    return SimpleNamespace(deadline_at_ms=deadline_at_ms, depth=depth, spec=SimpleNamespace(budgets=budgets))  # type: ignore[return-value]


# ---- WP99-TREE: one budget at admission; a child's slice of its parent's ----


def test_wp99_tree_1_the_model_grant_and_the_run_hold_one_budget_type() -> None:
    """The grant's pinned caps are the run budget's (one owner): constructing it as the grant does still works."""
    assert GrantBudget is RunBudget
    assert RunBudget(max_cost_usd=2.0, max_tokens=400_000) == RunBudget(2.0, 400_000, None, 0)


def test_wp99_tree_2_a_childs_deadline_is_its_parents_less_the_parents_answer_and_its_money_comes_from_it() -> None:
    root = RunBudget(5.0, 10_000_000, deadline_at_s=NOW + HOUR)
    child = root.child()
    assert child.deadline_at_s == NOW + HOUR - ANSWER_RESERVE_S
    assert (child.max_cost_usd, child.max_tokens, child.depth) == (5.0, 10_000_000, 1)
    share = root.child(0.25)
    assert (share.max_cost_usd, share.max_tokens) == (1.25, 2_500_000)
    grandchild = child.child(0.5)
    assert grandchild.deadline_at_s == NOW + HOUR - 2 * ANSWER_RESERVE_S
    assert grandchild.max_cost_usd == 2.5 and grandchild.depth == 2
    assert RunBudget(None, None).child() == RunBudget(None, None, None, 1)


@pytest.mark.parametrize("share", [0.0, -0.1, 1.5])
def test_wp99_tree_3_a_share_outside_the_parents_budget_is_refused(share: float) -> None:
    with pytest.raises(ValueError, match="above 0 and at most 1"):
        _ = RunBudget(5.0, 100).child(share)


def test_wp99_tree_4_a_claimed_child_is_its_root_taken_down_its_depth() -> None:
    """Node pins a child with its root's deadline: the claim at depth 1 keeps its parent's answer reserve free; its
    own caps stay what Node pinned for it. A journal naming no deadline falls back to the spec's pin."""
    root = RunBudget.of_claim(_claim(deadline_at_ms=int((NOW + HOUR) * 1000)))
    child = RunBudget.of_claim(_claim(deadline_at_ms=int((NOW + HOUR) * 1000), depth=1))
    assert root.deadline_at_s == NOW + HOUR and root.depth == 0
    assert child.deadline_at_s == NOW + HOUR - ANSWER_RESERVE_S and child.depth == 1
    assert (child.max_cost_usd, child.max_tokens) == (5.0, 10_000_000)
    pinned = RunBudget.of_claim(_claim(deadline_at_ms=None, calendar="2027-01-01T00:00:00.123456789Z"))
    assert pinned.deadline_at_s == pytest.approx(1_798_761_600.123456)
    assert RunBudget.of_claim(_claim(deadline_at_ms=None, calendar="never")).deadline_at_s is None


def test_wp99_tree_5_the_engine_child_of_a_research_run_ends_before_its_parent_must_answer() -> None:
    """Run 6's shape: the engine child re-driven 23 minutes before its root's deadline. Its working time ends with
    its own delivery reserve and its parent's answer reserve still ahead; the parent's answer-only turn starts after
    the child's deadline, never before it."""
    deadline_ms = int((NOW + 23 * 60) * 1000)
    child = RunBudget.of_claim(_claim(deadline_at_ms=deadline_ms, depth=1))
    allowance = child.turn(NOW, ENGINE, metered=True)
    assert allowance.wall_s == 23 * 60 - ANSWER_RESERVE_S - DELIVERY_RESERVE_S
    parent = RunBudget.of_claim(_claim(deadline_at_ms=deadline_ms))
    assert child.deadline_at_s is not None and parent.deadline_at_s is not None
    assert not parent.out_of_time(child.deadline_at_s - 1)
    assert parent.out_of_time(child.deadline_at_s)


def test_wp99_tree_6_the_30_minute_engine_working_time_stays() -> None:
    """WP-81 D4 (owner-accepted): a child handed over with an hour left gets the profile's 30 minutes."""
    child = RunBudget.of_claim(_claim(deadline_at_ms=int((NOW + HOUR) * 1000), depth=1))
    assert child.turn(NOW, ENGINE, metered=True).wall_s == 1_800
    assert child.turn(NOW, ENGINE, metered=True).steps == 500
    assert child.turn(NOW, ENGINE, metered=True).tokens == 10_000_000
    assert child.turn(NOW, ENGINE, metered=False) == Allowance(1_000, 1_800, None, 10_000_000)


def test_wp99_tree_7_a_turn_past_its_deadline_still_gets_time_to_write() -> None:
    child = RunBudget(5.0, None, deadline_at_s=NOW + 30)
    assert child.turn(NOW, ENGINE, metered=True).wall_s == MIN_WORKING_S


# ---- WP99-PLAN: gather until write_by, write, deliver ----


def test_wp99_plan_1_the_plan_reserves_two_of_the_longest_steps_to_write_and_the_delivery_before_the_deadline() -> None:
    budget = RunBudget(5.0, None, deadline_at_s=NOW + HOUR)
    plan = budget.plan(NOW, ENGINE, metered=True)
    assert plan is not None
    assert plan.stop_by_s == NOW + 1_800 and plan.minutes == 30
    assert plan.write_by_s == NOW + 1_800 - 270  # 15% of 30 minutes before any step is seen
    slow = budget.plan(NOW, ENGINE, metered=True, longest_step_s=226)
    assert slow is not None and slow.write_by_s == NOW + 1_800 - WALL_RESERVE_STEPS * 226
    assert slow.deliver_by_s == NOW + HOUR
    near = RunBudget(5.0, None, deadline_at_s=NOW + 20 * 60).plan(NOW, ENGINE, metered=True)
    assert near is not None and near.stop_by_s == NOW + 20 * 60 - DELIVERY_RESERVE_S
    assert RunBudget(5.0, None).plan(NOW, RunLimits(), metered=True) is None


def test_wp99_plan_2_the_engine_is_told_its_plan_in_plain_words() -> None:
    """The brief says how long and when to start writing ("about 30 minutes; start writing by 17:20")."""
    budget = RunBudget(5.0, None, deadline_at_s=NOW + HOUR)
    plan = budget.plan(NOW, ENGINE, metered=True)
    assert plan is not None
    words = plan_words(plan)
    assert words == "Time plan: gather until 08:25 UTC at the latest, then write; your work must be finished by 08:30 UTC."
    note = allowance_note(budget.turn(NOW, ENGINE, metered=True), plan)
    assert "about 30 minutes" in note and note.endswith(words)
    assert allowance_note(budget.turn(NOW, ENGINE, metered=True)).endswith("finish with your answer.")


# ---- WP99-WAIT: a person's wait pauses the working time ----


async def test_wp99_wait_1_person_waits_pause_the_clock_however_many_overlap() -> None:
    now = [0.0]
    waits = PersonWaits(lambda: now[0])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_person() -> None:
        async with waits.waiting():
            entered.set()
            await release.wait()

    first = asyncio.ensure_future(wait_for_person())
    await entered.wait()
    now[0] = 10.0
    async with waits.waiting():
        now[0] = 25.0
    assert waits.waited_s() == 25.0, "the overlapping wait is counted once"
    now[0] = 40.0
    release.set()
    await first
    now[0] = 90.0
    assert waits.waited_s() == 40.0


# ---- WP99-BOUND: a tool call never outlives its run's deadline ----


def test_wp99_bound_1_a_tool_call_is_bounded_by_its_runs_deadline() -> None:
    bounds = ToolTimeBounds()
    assert bounds.wall_for("default") == DEFAULT_WALL_S
    budget = RunBudget(None, None, deadline_at_s=NOW + 90)
    with budget_scope(budget, lambda: NOW):
        assert bounds.wall_for("default") == 90
        assert bounds.wall_for(30.0) == 30.0
        # A question to the person or a child's run bounds itself: never cut by the run's clock here.
        assert bounds.wall_for(None) is None
    with budget_scope(budget, lambda: NOW + 120):
        assert scoped_bound_s(600.0) == 0.0
    assert scoped_bound_s(600.0) == 600.0
