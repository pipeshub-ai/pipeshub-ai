"""WP70-RELAY: a parent never relays a child's "not written yet" as its answer; it writes the deliverable itself.

In a recorded deep run the child's answer was a progress remark and DEEPROUTE relayed it word for word in a 59 ms turn, with
8.9 MB of merged notes and 155 recorded citations unread. A cut-short child now always answers from what it gathered
when it can (WP70-CUT); when it could not, its answer says the file is not written yet and lists its sources. The
parent then takes the turn back and writes the answer on its own wrap-up reserve (BUDGETEND), with the citation rules;
with no budget left that turn says so and offers "continue".
"""

from __future__ import annotations

from app.harness.assembly.deep_route import HANDOFF_SLOT, Handoff
from harness_core.engines.outcome.ledger import SourceGathered
from harness_core.engines.outcome.outcome import Delivered, Partial
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.kernel.core.turn_limit import is_cut_short_fallback
from tests.unit.harness.assembly.test_deeproute import _director, _state
from tests.unit.harness.assembly.test_engbudget_deeproute import _result

UPFRONT = Handoff(turn=1, trigger="upfront", reason="research")
SOURCES = (SourceGathered("https://docs.onyx.app/connectors", "fetch_url", "https://docs.onyx.app/connectors", "Connectors"),)


def _unwritten() -> str:
    return PRESENTER.answer(Partial("turns", Delivered(sources=SOURCES, kept=True), ("report.md",)))


def test_wp70_relay_an_upfront_hand_off_whose_child_owes_its_report_is_taken_back() -> None:
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: UPFRONT.slot()})
    plan = _director().before(2, state, [_result(_unwritten(), "turns")])
    assert plan.synthetic is None, "the parent writes the answer; the child's note is never relayed"
    assert plan.cut_short == "turns"
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(1, "upfront", "research", answered=2)


def test_wp70_relay_a_code_task_the_model_called_itself_leaves_the_owed_report_to_the_model() -> None:
    state = _state([(1, 9_000)], tool="code_task")
    plan = _director().before(2, state, [_result(_unwritten(), "turns")])
    assert plan.synthetic is None and plan.cut_short is None


def test_wp70_relay_the_not_written_answer_is_plain_and_names_its_sources() -> None:
    answer = _unwritten()
    first = answer.splitlines()[0]
    assert first.startswith("**report.md isn't written yet.**") and "step limit" in first
    assert "[Connectors](https://docs.onyx.app/connectors)" in answer and "Fetch url (1 call)" in answer
    assert '"continue"' in answer and is_cut_short_fallback(answer)
    # A child that wrote its report is relayed, flagged, as before.
    assert not is_cut_short_fallback("# Onyx vs PipesHub\n\nOnyx documents its connectors.")


def test_wp70_relay_a_child_that_ended_without_its_file_is_answered_by_the_model_not_relayed() -> None:
    """WP70-DONE: the child completed normally (no limit) and still owed report.md; the model takes the turn."""
    owed = PRESENTER.answer(Partial("unfinished", Delivered(sources=SOURCES, kept=True), ("report.md",)))
    assert owed.startswith("**report.md isn't written yet.** This task ended its turn") and is_cut_short_fallback(owed)
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: UPFRONT.slot()})
    plan = _director().before(2, state, [_result(owed, None)])
    assert plan.synthetic is None and plan.cut_short is None
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(1, "upfront", "research", answered=2)
