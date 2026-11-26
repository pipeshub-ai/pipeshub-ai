"""DUR3-A4 (J-15, main #3808): a run whose last allowed turn called tools gets one answer-only turn, on both loops.

Legacy (``loops._finish_after_max_turns``): after ``max_turns`` (15) turns, when the last one called tools, it injects
``FINAL_ANSWER_NOTE`` on the newest result's footer (main #4106) and asks the model once more with the tool list; only calls that end the run are executed,
so the reply's text is the answer. A turn that fails or yields no text falls back to the last turn's text as a
degraded answer, or fails the run when that text is too short. Each case drives both loops on one script and compares
every request and every output.
"""

from __future__ import annotations

import pytest

from harness_core.kernel.core.turn_limit import FINAL_ANSWER_NOTE
from harness_core.testing import ModelScript
from harness_core.testing.model_script import ErrorSpec, Reply, ToolCallSpec, Turn
from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.legacy_driver import DriveSpec
from tests.harness_replay.wire import (
    drive_captured,
    first_difference,
    outputs_difference,
)

MAX_TURNS = 15
NARRATION = "Checking the next ticket in the queue to see whether it is the one you meant."


def _spec() -> DriveSpec:
    return DriveSpec(scenario="dur3-turn-limit", query="Which ticket is about the login error?", fake_toolsets=("jira",))


def _tool_turns(narrate: bool) -> list[Turn]:
    return [
        Turn(
            reply=Reply(
                text=NARRATION if narrate else "",
                # A new search each turn that answers (an empty list is an answer): the run makes progress, so only
                # the turn limit stops it. Fifteen failing lookups would be a stall, which ends earlier (BU-06).
                tool_calls=[ToolCallSpec(id=f"g{i}", name="jira__search_issues", args={"query": f"login error {i}"})],
            )
        )
        for i in range(MAX_TURNS)
    ]


def _script(final: Turn | None, *, narrate: bool = True) -> ModelScript:
    turns = _tool_turns(narrate)
    if final is not None:
        turns.append(final)
    return ModelScript(id="dur3-turn-limit", turns=turns)


async def _both(script: ModelScript):  # noqa: ANN202
    legacy = await drive_captured(legacy_driver.drive, _spec(), script)
    durable = await drive_captured(durable_driver.drive, _spec(), script)
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
    assert outputs_difference(legacy.result, durable.result) is None, outputs_difference(
        legacy.result, durable.result
    )
    return legacy, durable


async def test_dur3_a4_the_answer_only_turn_answers_on_both_loops() -> None:
    _, durable = await _both(_script(Turn(reply=Reply(text="PROJ-42 is the login error ticket."))))
    assert len(durable.requests) == MAX_TURNS + 1
    last = durable.requests[-1]
    assert FINAL_ANSWER_NOTE in last.messages[-1]
    assert "jira__search_issues" in last.tool_names
    assert durable.result.recording.final.answer == "PROJ-42 is the login error ticket."
    assert durable.result.recording.final.status == "completed"


async def test_dur3_a4_a_final_turn_that_only_calls_tools_ends_on_the_degraded_answer() -> None:
    final = Turn(reply=Reply(tool_calls=[ToolCallSpec(id="late", name="jira__get_issue", args={"issue_key": "PROJ-99"})]))
    _, durable = await _both(_script(final))
    assert len(durable.requests) == MAX_TURNS + 1
    # The call on the answer-only turn is never executed.
    assert [s.call_id for s in durable.result.recording.tool_steps()][-1] == f"g{MAX_TURNS - 1}"
    assert durable.result.recording.final.answer == NARRATION


async def test_dur3_a4_narration_beside_a_dropped_call_is_not_the_answer_on_both_loops() -> None:
    """Main #4379: the answer-only turn asks for no tool calls (``tool_choice="none"``, on both loops' wire), and
    text written before a call it was told not to make is narration, so the run ends as on an empty final turn."""
    final = Turn(reply=Reply(
        text="I'll check PROJ-99 next.",
        tool_calls=[ToolCallSpec(id="late", name="jira__get_issue", args={"issue_key": "PROJ-99"})],
    ))
    _, durable = await _both(_script(final))
    assert "tool_choice=none" in durable.requests[-1].kind
    assert all("tool_choice" not in r.kind for r in durable.requests[:-1])
    assert durable.result.recording.final.answer == NARRATION


async def test_dur3_a4_a_failed_final_turn_ends_on_the_degraded_answer() -> None:
    final = Turn(reply=Reply(error=ErrorSpec(kind="malformed", message="rejected")))
    _, durable = await _both(_script(final))
    assert durable.result.recording.final.answer == NARRATION


async def test_dur3_a4_no_answer_and_no_narration_answers_with_what_was_gathered_on_both() -> None:
    """BUDGETEND: with no text to answer with, both loops complete on the same answer written without a model,
    naming the tools the run called and that it stopped at its step limit, never a bare failure."""
    script = _script(Turn(reply=Reply(text="")), narrate=False)
    legacy = await drive_captured(legacy_driver.drive, _spec(), script)
    durable = await drive_captured(durable_driver.drive, _spec(), script)
    assert first_difference(legacy.requests, durable.requests) is None
    assert durable.result.recording.final.status == legacy.result.recording.final.status == "completed"
    # R50JFIX-1: the durable answer is what the run did, the words every surface shows; the stop and how to go on
    # are the chat's own (the settled outcome). The legacy loop's English holds the same facts between them.
    answer, legacy_answer = durable.result.recording.final.answer, legacy.result.recording.final.answer
    assert answer == "What it had gathered so far:\n- Search issues (15 calls)"
    assert answer in legacy_answer and "step limit" in legacy_answer


@pytest.mark.parametrize("narrate", [True])
async def test_dur3_a4_a_last_turn_without_tool_calls_takes_no_extra_turn(narrate: bool) -> None:
    """The answer-only turn is only for a last turn that called tools: a text reply ends the run in its turn."""
    turns = [*_tool_turns(narrate)[: MAX_TURNS - 1], Turn(reply=Reply(text="PROJ-42 is the login error ticket."))]
    _, durable = await _both(ModelScript(id="dur3-in-budget", turns=turns))
    assert len(durable.requests) == MAX_TURNS
