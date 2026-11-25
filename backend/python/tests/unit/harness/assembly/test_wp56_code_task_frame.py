"""WP-56 items 8 and 9: what a ``code_task`` hand-off's result says, to the parent's model and in the chat.

WP56-FRAME-1..4: the hand-off's call was the one tool whose result the worker never presented, so Node showed its
fallback, the child result document as raw JSON (a recorded run: ``{"error":"commit_rejected","output":"",
"success":false,"workspace":{"fork":{"revision":25,"workspaceId":"ws:…:fork:1b543f14-…"},…}}``). It is now presented
as every other call is: a plain line of what the coding engine did, the answer's text, no ids and no JSON.

WP56-UNFIN-6: a failed child whose work was kept in its own folder (``StoreChildWorkspaces.keep_unfinished``): the
parent is told where, so its answer can say so, instead of "nothing it wrote can be shown here".
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly import handoff_instructions as instructions
from app.harness.assembly.code_task import CodeTaskDelegation
from app.harness.assembly.deep_route import ENGINE_FAILED_WITH_WORK_INSTRUCTION
from harness_core.engines.outcome.briefing import ChildReport
from harness_core.journal.children import child_result_document
from harness_core.kernel.core.messages import ToolCall
from harness_core.workspace.forks import UNFINISHED_WORK_FOLDER, ChildMergeReport

if TYPE_CHECKING:
    from harness_core.kernel.durable.harness_channel import HarnessInstruction


def engine_failed_instruction(content: object) -> HarnessInstruction:
    """WP-89: the failed hand-off's instruction is the outcome briefing's, read once from the child's report."""
    return instructions.engine_failed_briefed(ChildReport.read(content))


RESULT_PRESENTED = "x.pipeshub.tool.result_presented"
CHILD = "1b543f14-99f4-02de-6eca-3e489dbbb693"
FORK = f"ws:6ae77b84165c60cace330ae5:fork:{CHILD}"
FOLDER = f"{UNFINISHED_WORK_FOLDER}/2026-11-01 17.52.38 UTC"
CALL = ToolCall(id="t1.0", name="code_task", arguments={"goal": "Research Onyx and PipesHub"})

RECORDED_FAILED = {"error": "commit_rejected", "output": "", "success": False}


def _failed_report(*, kept: bool) -> ChildMergeReport:
    return ChildMergeReport(
        child_run_id=CHILD,
        fork_id=FORK,
        into="ws:6ae77b84165c60cace330ae5",
        status="not_merged",
        fork_revision=25,
        reason="child_failed",
        **({"unfinished": FOLDER, "kept": ("out/notes.md", "out/report.md"), "revision": 3} if kept else {}),
    )


def _frame(events: tuple[Any, ...]) -> dict[str, Any]:
    presented = [e for e in events if e.type == RESULT_PRESENTED]
    assert len(presented) == 1, [e.type for e in events]
    # The projector reads presentation that follows the outcome: the result's frame comes first.
    assert events[0].type == RESULT_PRESENTED
    return dict(presented[0].data)


def _plain(frame: dict[str, Any]) -> None:
    said = json.dumps(frame.get("content", "")) + json.dumps(frame.get("resultSummary", ""))
    for leak in (CHILD, "ws:", "fork", "{", "commit_rejected", "child_failed", "not_merged"):
        assert leak not in said, (leak, frame)


async def test_wp56_frame_1_a_failed_hand_off_reads_as_a_plain_line_with_its_saved_work() -> None:
    outcome = await CodeTaskDelegation().outcome(CALL, RECORDED_FAILED, workspace=_failed_report(kept=True))
    frame = _frame(outcome.events)
    assert (frame["callId"], frame["status"], frame["content"]) == ("t1.0", "failed", "")
    assert frame["resultSummary"] == (
        f"The coding engine stopped with an error before it finished. Its saved work is in the folder “{FOLDER}”."
    )
    _plain(frame)
    # What the parent's model reads keeps the report, folder included.
    assert outcome.error and outcome.content["workspace"]["unfinished"]["folder"] == FOLDER  # type: ignore[index]


async def test_wp56_frame_1_a_failed_hand_off_with_nothing_kept() -> None:
    outcome = await CodeTaskDelegation().outcome(CALL, RECORDED_FAILED, workspace=_failed_report(kept=False))
    frame = _frame(outcome.events)
    assert frame["resultSummary"] == "The coding engine stopped with an error before it finished."
    _plain(frame)


async def test_wp56_frame_2_an_answered_hand_off_shows_the_answers_text_and_its_files() -> None:
    document = child_result_document(outcome="answered", output="Onyx is open source; PipesHub indexes more sources.")
    document["files"] = [
        {"artifactId": "art-1", "fileName": "report.md"},
        {"artifactId": "art-2", "fileName": "notes.md"},
    ]
    outcome = await CodeTaskDelegation().outcome(CALL, document)
    frame = _frame(outcome.events)
    assert (frame["status"], frame["content"]) == ("completed", "Onyx is open source; PipesHub indexes more sources.")
    assert frame["resultSummary"] == "The coding engine finished the task and saved 2 files."
    _plain(frame)
    one = await CodeTaskDelegation().outcome(CALL, child_result_document(outcome="answered", output="Done."))
    assert _frame(one.events)["resultSummary"] == "The coding engine finished the task."


@pytest.mark.parametrize(
    ("limit", "said"),
    [
        ("budget", "The coding engine stopped early at its spending limit; this is what it had done."),
        ("turns", "The coding engine stopped early at its step limit; this is what it had done."),
        ("time", "The coding engine stopped early when its working time ran out; this is what it had done."),
        ("stall", "The coding engine stopped early because it was not making progress; this is what it had done."),
    ],
)
async def test_wp56_frame_3_a_hand_off_cut_short_says_why_in_plain_words(limit: str, said: str) -> None:
    document = child_result_document(outcome="answered", output="Half the comparison.", truncated_by=limit)
    frame = _frame((await CodeTaskDelegation().outcome(CALL, document)).events)
    assert (frame["status"], frame["content"], frame["resultSummary"]) == ("completed", "Half the comparison.", said)
    _plain(frame)


async def test_wp56_frame_4_a_hand_off_that_asks_and_a_long_answer() -> None:
    asks = child_result_document(outcome="needs_input", output="Which repository?")
    frame = _frame((await CodeTaskDelegation().outcome(CALL, asks)).events)
    assert frame["resultSummary"] == "The coding engine stopped to ask you something."
    long = child_result_document(outcome="answered", output="x" * 5000)
    frame = _frame((await CodeTaskDelegation().outcome(CALL, long)).events)
    # The chat's preview, never the whole answer (it is the next message's text).
    assert 0 < len(frame["content"]) < 5000


def test_wp56_unfin_6_the_parent_is_told_where_the_unfinished_work_is() -> None:
    content = {**RECORDED_FAILED, "workspace": _failed_report(kept=True).as_json()}
    said = engine_failed_instruction(content).text
    assert f"“{FOLDER}”" in said and "conversation's files" in said
    assert "nothing it wrote can be shown here" not in said and "'continue'" in said
    # Saved but not kept (another writer held the workspace): as WP-62 said it.
    kept_apart = {**RECORDED_FAILED, "workspace": _failed_report(kept=False).as_json()}
    assert engine_failed_instruction(kept_apart).text == ENGINE_FAILED_WITH_WORK_INSTRUCTION.format(revisions=25)


async def test_wp56_unfin_7_a_deep_plan_step_that_failed_says_where_its_work_is() -> None:
    """Deep mode's director reads a dispatched coding step's result as text: the folder is in it."""
    from unittest.mock import MagicMock

    from app.agents.agent_loop.loops.orchestrator import PROGRAMMATIC_CALL_PREFIX
    from app.harness.assembly.harness_agent_runner import HarnessAgentRunner

    runner = HarnessAgentRunner(MagicMock(), CodeTaskDelegation())
    step = ToolCall(id=f"{PROGRAMMATIC_CALL_PREFIX}1", name="code_task", arguments={"goal": "Research"})
    shown = await runner.outcome(step, RECORDED_FAILED, workspace=_failed_report(kept=True))
    assert shown.error and shown.content == (
        f"the coding step failed; what it had saved is in the folder “{FOLDER}” in this conversation's files"
    )
    bare = await runner.outcome(step, RECORDED_FAILED, workspace=_failed_report(kept=False))
    assert bare.content == "the coding step failed"
