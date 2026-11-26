"""WP65-PART: a run that handed its task off up front, whose engine failed after saving work.

The run had gathered nothing of its own (the hand-off came before any tool call), and the parent was told to "answer
from what this conversation has gathered": there was nothing true to say beyond the failure, and the 151 files the
engine saved were mentioned nowhere. The run's answer is now written without a model: partial, why, what was saved
and where, and how to try again. A hand-off later in a run (escalation) still leaves the answer to the model, which
has its own results to answer from.
"""

from __future__ import annotations

from app.harness.assembly.deep_route import (
    ENGINE_FAILED_INSTRUCTION,
    HANDOFF_SLOT,
    Handoff,
)
from harness_core.engines.outcome.briefing import BRIEFING, ChildReport
from harness_core.engines.outcome.words import ENGLISH
from harness_core.kernel.durable.ports import TurnResult
from tests.unit.harness.assembly.test_deeproute import _director, _state

UPFRONT = Handoff(turn=1, trigger="upfront", reason="analysis")
RUN_2 = {
    "error": "capability_lost",
    "output": "",
    "success": False,
    "workspace": {"fork": {"revision": 71, "workspaceId": "ws:s:fork:c"}, "reason": "child_failed", "status": "not_merged"},
}


def _failed(content: object, status: str = "ok") -> TurnResult:
    return TurnResult(name="code_task", call_id="c1.0", status=status, content=content)


# WP-89: the answer is the outcome briefing's (ADR-RUN-01 D1), read once from the child's report.
ENGINE_FAILED_PARTIAL_LEAD = ENGLISH("briefing.answer.lead")


def engine_failed_answer(content: object) -> str:
    return BRIEFING.failed_answer(ChildReport.read(content))


def test_wp65_part_1_an_up_front_hand_off_whose_engine_failed_answers_partial_with_what_it_saved() -> None:
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: UPFRONT.slot()})
    plan = _director().before(2, state, [_failed(RUN_2)])
    assert plan.inject == () and plan.synthetic is not None and not plan.synthetic.tool_calls
    text = plan.synthetic.content[0].text  # type: ignore[union-attr]
    assert text.startswith(ENGINE_FAILED_PARTIAL_LEAD)
    assert "71 versions of its working files" in text and '"continue"' in text
    assert "capability_lost" not in text and "ws:s:fork:c" not in text, "no codes or ids"
    # Answered: a later boundary asks nothing more of the model.
    assert Handoff.from_slot(plan.slots[HANDOFF_SLOT]) == Handoff(1, "upfront", "analysis", answered=2)


def test_wp65_part_2_kept_unfinished_work_is_named_with_its_folder_and_files() -> None:
    """WP-56 keeps a failed child's own changes under "Unfinished work/<time>" and names them in the result."""
    files = [f"notes/source-{n:02d}.md" for n in range(14)]
    content = {**RUN_2, "workspace": {**RUN_2["workspace"], "unfinished": {"folder": "Unfinished work/2026-11-02 00.31.31 UTC", "files": files}}}
    text = engine_failed_answer(content)
    assert "“Unfinished work/2026-11-02 00.31.31 UTC”" in text
    assert "notes/source-00.md" in text and "notes/source-09.md" in text and "and 4 more" in text
    assert "notes/source-10.md" not in text


def test_wp65_part_3_a_failure_that_saved_nothing_is_still_the_models_to_tell_in_the_persons_language() -> None:
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: UPFRONT.slot()})
    for content, status in (({"error": "crash_loop", "output": "", "success": False}, "ok"), ("code_task failed", "error")):
        plan = _director().before(2, state, [_failed(content, status)])
        assert plan.synthetic is None and [i.text for i in plan.harness] == [ENGINE_FAILED_INSTRUCTION]
    assert engine_failed_answer({"success": False}) == f'{ENGINE_FAILED_PARTIAL_LEAD}\n\nSend "continue" to try the task again.'


def test_wp65_part_4_an_escalated_hand_off_still_leaves_the_answer_to_the_model() -> None:
    asked = Handoff(turn=6, trigger="escalation", reason="many_steps")
    state = _state([(4, 9_000)] * 5 + [(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: asked.slot()})
    plan = _director().before(7, state, [_failed(RUN_2)])
    assert plan.synthetic is None and len(plan.harness) == 1 and "71 versions" in plan.harness[0].text
