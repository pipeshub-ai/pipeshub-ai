"""BU-06 (C8.08 as amended in ADR-OWD-05): a runaway or stalled run ends its turn with what it gathered, on both loops.

Before, nothing stopped a model that kept making the same call: the stall hook only advised it and no durable run
installed it, so a loop ran to the step limit (15 turns of the same call) and read as "turn limit reached". Now the
rounds are counted on both loops: ``STALL_REPEAT_ROUNDS`` rounds in a row with the same calls and the same results,
or ``STALL_STALE_ROUNDS`` rounds whose results were all errors or empty, and the next turn is the answer-only one,
told why, and the answer is flagged ``truncatedBy: stall`` so the chat says it stopped and how to carry on.
"""

from __future__ import annotations

import re

from harness_core.kernel.core.stall import STALL_REPEAT_ROUNDS, STALL_STALE_ROUNDS
from harness_core.kernel.core.turn_limit import cut_short_note
from harness_core.testing import ModelScript
from harness_core.testing.model_script import Reply, ToolCallSpec, Turn
from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.legacy_driver import DriveResult, DriveSpec
from tests.harness_replay.wire import (
    drive_captured,
    first_difference,
    outputs_difference,
)

ANSWER = "No ticket matches the login error; searching again did not change that."


def _spec() -> DriveSpec:
    return DriveSpec(scenario="bu06-stall", query="Which ticket is about the login error?", fake_toolsets=("jira",))


def _same_call(i: int, query: str = "login error") -> Turn:
    return Turn(reply=Reply(
        text="Searching the tickets once more.",
        tool_calls=[ToolCallSpec(id=f"g{i}", name="jira__search_issues", args={"query": query})],
    ))


def _truncated_by(result: DriveResult) -> str | None:
    """The limit that cut the answer short: the durable run's ``run.completed``, the legacy route's final frame."""
    if result.events is not None:
        done = [e for e in result.events if getattr(e, "type", None) == "run.completed"]
        return (getattr(done[-1], "data", None) or {}).get("truncatedBy") if done else None
    found = re.findall(r'"truncatedBy":\s*"(\w+)"', result.sse)
    return found[-1] if found else None


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


async def test_bu06_a_runaway_call_ends_the_turn_with_an_answer_on_both_loops() -> None:
    turns = [_same_call(i) for i in range(STALL_REPEAT_ROUNDS)] + [Turn(reply=Reply(text=ANSWER))]
    turns += [_same_call(99)] * 10  # never reached: the loop would have gone on without the stop
    legacy, durable = await _both(ModelScript(id="bu06-runaway", turns=turns))
    assert len(durable.requests) == STALL_REPEAT_ROUNDS + 1
    last = durable.requests[-1]
    assert cut_short_note("stall") in last.messages[-1]
    assert "tool_choice=none" in last.kind
    for run in (legacy, durable):
        final = run.result.recording.final
        assert (final.status, final.answer) == ("completed", ANSWER)
        assert _truncated_by(run.result) == "stall"


async def test_bu06_the_same_call_with_new_arguments_is_progress_and_runs_on() -> None:
    """Changing arguments is a search, not a loop: no stop before the model answers on its own."""
    turns = [_same_call(i, query=f"login error {i}") for i in range(STALL_REPEAT_ROUNDS + 2)]
    turns.append(Turn(reply=Reply(text=ANSWER)))
    _, durable = await _both(ModelScript(id="bu06-search", turns=turns))
    assert len(durable.requests) == STALL_REPEAT_ROUNDS + 3
    assert _truncated_by(durable.result) is None
    assert all(cut_short_note("stall") not in m for r in durable.requests for m in r.messages)


async def test_bu06_rounds_of_unknown_tools_stall_and_end_with_an_answer() -> None:
    """Every result an error (a tool that does not exist) for ``STALL_STALE_ROUNDS`` rounds: the run answers."""
    turns = [
        Turn(reply=Reply(tool_calls=[ToolCallSpec(id=f"u{i}", name="jira__no_such_tool", args={"n": i})]))
        for i in range(STALL_STALE_ROUNDS)
    ]
    turns += [Turn(reply=Reply(text=ANSWER))] + [_same_call(99)] * 10
    legacy, durable = await _both(ModelScript(id="bu06-stale", turns=turns))
    assert len(durable.requests) == STALL_STALE_ROUNDS + 1
    assert cut_short_note("stall") in durable.requests[-1].messages[-1]
    assert _truncated_by(legacy.result) == _truncated_by(durable.result) == "stall"
