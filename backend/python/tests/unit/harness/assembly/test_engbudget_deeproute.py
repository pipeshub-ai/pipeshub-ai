"""ENGBUDGET: a hand-off whose engine stopped early is not passed through as the run's whole answer.

A recorded parent gathered six turns of Jira results, handed off, and presented the engine's
"This reply stopped early ... before an answer could be written." as its answer.
"""

from __future__ import annotations

from app.harness.assembly.deep_route import HANDOFF_SLOT, Handoff
from harness_core.engines.outcome.outcome import Delivered, Partial
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.kernel.core.turn_limit import cut_short_fallback
from harness_core.kernel.durable.ports import TurnResult
from tests.unit.harness.assembly.test_deeproute import _director, _state

ASKED = Handoff(turn=6, trigger="escalation", reason="many_steps")


def _result(output: str, truncated_by: str | None) -> TurnResult:
    content: dict[str, object] = {"success": True, "output": output}
    if truncated_by is not None:
        content["truncatedBy"] = truncated_by
        content["stopped_early"] = "This sub-task stopped early at its spending limit; its output is partial."
    return TurnResult(name="code_task", call_id="c7.0", status="ok", content=content)  # type: ignore[arg-type]


def _plan(result: TurnResult):  # noqa: ANN202
    state = _state([(4, 9_000)] * 5 + [(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: ASKED.slot()})
    return _director().before(7, state, [result])


def test_an_empty_cut_short_engine_gives_the_turn_back_flagged() -> None:
    plan = _plan(_result(cut_short_fallback("budget", ()), "budget"))
    assert plan.synthetic is None, "the model answers, not the fallback"
    assert plan.cut_short == "budget"
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(6, "escalation", "many_steps", answered=7)


def test_any_limit_is_taken_back_with_its_own_reason() -> None:
    assert _plan(_result(cut_short_fallback("turns", ("search",)), "turns")).cut_short == "turns"


def test_a_cut_short_engine_that_answered_is_the_answer_flagged() -> None:
    answer = PRESENTER.answer(Partial("budget", Delivered()), body="Pick up PP-7 first.")
    plan = _plan(_result(answer, "budget"))
    assert plan.synthetic is not None and plan.synthetic.content[0].text == answer  # type: ignore[union-attr]
    assert plan.cut_short == "budget"


def test_an_engine_that_finished_is_passed_through_unflagged() -> None:
    plan = _plan(_result("Pick up PP-7 first.", None))
    assert plan.synthetic is not None and plan.cut_short is None


def test_the_fallback_is_recognised_whatever_it_gathered() -> None:
    from harness_core.kernel.core.turn_limit import is_cut_short_fallback

    for reason in ("budget", "turns", "time"):
        assert is_cut_short_fallback(cut_short_fallback(reason, ("jira", "jira", "web")))  # type: ignore[arg-type]
    assert not is_cut_short_fallback(PRESENTER.answer(Partial("budget", Delivered()), body="Pick up PP-7 first."))
