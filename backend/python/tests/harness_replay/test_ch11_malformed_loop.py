"""CH-11: a model that keeps sending malformed tool-call JSON gets a corrective error each time, the tool never runs,
and the run ends with an answer instead of looping to its step limit, on both loops.

The arguments are what the transport's converter makes of JSON it could not parse or repair (the Qwen3-Coder XML
class included): the ``__malformed_json__`` and ``__parse_error__`` sentinels.
"""

from __future__ import annotations

from harness_core.kernel.core.messages import (
    MALFORMED_TOOL_CALL_ARGS_KEY,
    MALFORMED_TOOL_CALL_ERROR_KEY,
)
from harness_core.kernel.core.stall import STALL_STALE_ROUNDS
from harness_core.kernel.core.turn_limit import cut_short_note
from harness_core.testing import ModelScript
from harness_core.testing.model_script import Reply, ToolCallSpec, Turn
from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.legacy_driver import DriveSpec
from tests.harness_replay.wire import drive_captured, first_difference

ANSWER = "I could not run the ticket search: my calls to it kept coming out malformed."
XML_CLASS = '<function=jira__search_issues><parameter=query>login</parameter></function>'


def _malformed(i: int) -> Turn:
    return Turn(reply=Reply(tool_calls=[ToolCallSpec(
        id=f"m{i}",
        name="jira__search_issues",
        args={
            MALFORMED_TOOL_CALL_ARGS_KEY: XML_CLASS if i % 2 else '{"query": "login",}',
            MALFORMED_TOOL_CALL_ERROR_KEY: "Expecting property name enclosed in double quotes",
        },
    )]))


async def test_ch11_repeated_malformed_calls_end_with_an_answer_on_both_loops() -> None:
    spec = DriveSpec(scenario="ch11-malformed", query="Which ticket is about the login error?", fake_toolsets=("jira",))
    turns = [_malformed(i) for i in range(STALL_STALE_ROUNDS)] + [Turn(reply=Reply(text=ANSWER))]
    turns += [_malformed(99)] * 10  # never reached
    script = ModelScript(id="ch11-malformed", turns=turns)
    legacy = await drive_captured(legacy_driver.drive, spec, script)
    durable = await drive_captured(durable_driver.drive, spec, script)
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
    # The model reads the same on both loops; only the record differs: the durable loop journals each refused call
    # as a tool step (with its corrective error), legacy's recording leaves calls that never ran out.
    assert len(durable.requests) == STALL_STALE_ROUNDS + 1
    # Each malformed call got the corrective error, not the tool's result.
    assert "had invalid arguments" in durable.requests[1].messages[-1]
    assert cut_short_note("stall") in durable.requests[-1].messages[-1]
    for run in (legacy, durable):
        assert (run.result.recording.final.status, run.result.recording.final.answer) == ("completed", ANSWER)
    assert all("had invalid arguments" in str(step) for step in durable.result.recording.tool_steps())
