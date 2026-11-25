"""RUN01-D1-BRIEF: ``OutcomeBriefing`` reads a child's result once, typed, and gives the parent its instruction (the
content a harness-channel message renders, ADR-RUN-01 D5) or its answer written without a model."""

from __future__ import annotations

import pytest

from harness_core.engines.outcome.briefing import BRIEFING, ChildReport
from harness_core.engines.outcome.presenter import PRESENTER

KEPT = {"success": False, "workspace": {"unfinished": {"folder": "Unfinished work", "files": [f"f{n}.md" for n in range(12)], "count": 151}}}
UNKEPT = {"success": False, "workspace": {"fork": {"revision": 25}}}
NOTHING = {"success": False}


@pytest.mark.parametrize(
    ("content", "says"),
    [
        (KEPT, "in the folder “Unfinished work”"),
        (UNKEPT, "It had saved 25 versions of its working files"),
        (NOTHING, "failed and returned no answer"),
    ],
)
def test_run01_d1_brief_1_a_failed_child_tells_the_parent_what_its_fork_kept(content: dict[str, object], says: str) -> None:
    brief = BRIEFING.brief(ChildReport.read(content))
    assert brief.situation == "failed" and brief.answer is None
    assert brief.instruction is not None and says in brief.instruction
    assert not brief.instruction.startswith("[PipesHub]"), "the harness channel's frame says whose words they are"


def test_run01_d1_brief_2_an_upfront_hand_off_with_kept_work_is_answered_without_a_model() -> None:
    brief = BRIEFING.brief(ChildReport.read(KEPT), upfront=True)
    assert brief.instruction is None and brief.answer is not None
    assert brief.answer.startswith("**This answer is partial.**")
    assert "f0.md, f1.md" in brief.answer and ", and 141 more." in brief.answer
    assert brief.answer.endswith('Send "continue" to try the task again.')
    assert BRIEFING.brief(ChildReport.read(NOTHING), upfront=True).answer is None, "nothing to point to: the model says it"
    assert BRIEFING.brief(ChildReport.read({"success": True}, call_ok=False)).situation == "failed"


def test_run01_d1_brief_3_whether_the_child_answered_is_its_typed_outcome_not_its_words() -> None:
    unanswered = {"success": True, "output": "Anything at all", "truncatedBy": "time",
                  "outcome": {"kind": "partial", "reason": "time", "answered": False}}
    assert BRIEFING.brief(ChildReport.read(unanswered)) .situation == "unanswered"
    answered = {**unanswered, "outcome": {"kind": "partial", "reason": "time", "answered": True}}
    brief = BRIEFING.brief(ChildReport.read(answered))
    assert brief.situation == "partial" and brief.reason == "time"
    legacy = {"success": True, "output": PRESENTER.no_answer("turns")}
    assert BRIEFING.brief(ChildReport.read(legacy)).situation == "unanswered", "a child settled before the ledger"
    assert BRIEFING.brief(ChildReport.read({"success": True, "output": "Done."})).situation == "answered"


def test_run01_d1_brief_4_the_hand_off_instruction_is_the_briefings() -> None:
    """WP-93's harness channel renders the briefing's content (ADR-RUN-01 D5 seam)."""
    from app.harness.assembly.handoff_instructions import (
        ENGINE_FAILED_KEY,
        engine_failed,
    )

    for kwargs, content in (({"folder": "Unfinished work"}, KEPT), ({"revisions": 25}, UNKEPT), ({}, NOTHING)):
        instruction = engine_failed(**kwargs)  # type: ignore[arg-type]
        assert instruction.key == ENGINE_FAILED_KEY
        assert instruction.text == BRIEFING.brief(ChildReport.read(content)).instruction
