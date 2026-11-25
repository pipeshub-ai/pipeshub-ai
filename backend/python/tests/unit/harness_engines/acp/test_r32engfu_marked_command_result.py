"""R31JFIX-FU-1 (R32-ENGFU): DeepSeek Harness reports a failed command's exit code only in its result text.

Journey ``J-job.acp-dsh-owner_pinned-use`` (a ``cat`` of a missing file) showed the output ``[exit code: 1]`` and a step
that read exit 0, completed: dsh's bash tool appends ``[exit code: N]`` (or ``[killed by signal: X]``) as the result's
last line and its ACP ``rawOutput`` carries no code. The profile now says so (``commandResult: marked``) and the
mapper reads the code from the marker and drops the marker from the output, as dsh's own ``parseExitStatus`` does.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.events import CommandFinished, CommandOutput
from harness_engines.acp.mapper import AcpEventMapper, MarkedResult, marked_result
from harness_engines.acp.permissions import SessionPolicy
from harness_engines.acp.registry import packaged_acp_agents

from .test_file_changes import POLICY

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue

# What dsh 0.2.2-rc.1 rendered for the journey's command (dsh-tool-bash ``renderResult``: stdout, a ``[stderr]``
# section, then the markers, each on its own line).
FAILED = "[stderr]\ncat: journey-814e36.txt: No such file or directory\n[exit code: 1]"
MARKED = dataclasses.replace(POLICY, command_result="marked")


def _run(policy: SessionPolicy, text: str, raw_output: JsonValue = None) -> list[object]:
    mapper = AcpEventMapper(turn_id="t-1", policy=policy)
    call: dict[str, JsonValue] = {"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "bash",
                                  "kind": "execute", "status": "pending", "rawInput": {"command": "cat x"}}
    events = mapper.update(call)
    update: dict[str, JsonValue] = {"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
                                    "content": [{"type": "content", "content": {"type": "text", "text": text}}]}
    if raw_output is not None:
        update["rawOutput"] = raw_output
    return events + mapper.update(update)


def _finished(events: list[object]) -> tuple[str, int | None]:
    (done,) = [e for e in events if isinstance(e, CommandFinished)]
    return done.status, done.exit_code


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (FAILED, MarkedResult("[stderr]\ncat: journey-814e36.txt: No such file or directory", 1)),
        ("hi\n", MarkedResult("hi\n", 0)),
        ("(no output)\n[exit code: 3]", MarkedResult("(no output)", 3)),
        ("out\n[timed out after 5ms]\n[killed by signal: SIGTERM]", MarkedResult("out\n[timed out after 5ms]", None, killed=True)),
        # Output that only mentions a marker mid-text is output: the marker is the last line or nothing.
        ("[exit code: 2] is what it said\n", MarkedResult("[exit code: 2] is what it said\n", 0)),
    ],
)
def test_r32engfu_m1_the_last_lines_marker_is_the_exit_status(text: str, expected: MarkedResult) -> None:
    assert marked_result(text) == expected


def test_r32engfu_m2_a_marked_agents_failed_command_reads_failed_with_its_code_and_no_marker() -> None:
    events = _run(MARKED, FAILED)
    assert _finished(events) == ("failed", 1)
    assert [e.text for e in events if isinstance(e, CommandOutput)] == [
        "[stderr]\ncat: journey-814e36.txt: No such file or directory"
    ]
    assert _finished(_run(MARKED, "hi\n")) == ("completed", 0)
    assert _finished(_run(MARKED, "x\n[killed by signal: SIGKILL]")) == ("failed", None)
    # The protocol's own code, where an agent sends one, wins over its text.
    assert _finished(_run(MARKED, FAILED, {"exitCode": 2})) == ("failed", 2)


def test_r32engfu_m3_a_plain_text_agent_is_unchanged() -> None:
    assert _finished(_run(POLICY, FAILED)) == ("completed", 0)


def test_r32engfu_m4_dshs_profile_says_marked_and_the_rendered_policy_carries_it() -> None:
    agents = {a.id: a for a in packaged_acp_agents(unsigned_ok=True)}
    assert agents["dsh"].command_result == "marked"
    assert SessionPolicy.from_json(json.dumps({"commandResult": "marked"}).encode()).command_result == "marked"


def test_r32engfu_m5_opencodes_acp_exit_code_is_in_its_tools_metadata() -> None:
    """opencode's ACP server passes its bash tool's metadata on: ``rawOutput.metadata.exit`` (the shared conformance
    case found its failed command read exit 0, completed, on the real binary)."""
    raw: JsonValue = {"output": "out-r32\nerr-r32\n", "metadata": {"output": "out-r32\nerr-r32\n", "exit": 3}}
    assert _finished(_run(POLICY, "out-r32\nerr-r32\n", raw)) == ("failed", 3)
    assert _finished(_run(POLICY, "ok\n", {"metadata": {"exit": 0}})) == ("completed", 0)
