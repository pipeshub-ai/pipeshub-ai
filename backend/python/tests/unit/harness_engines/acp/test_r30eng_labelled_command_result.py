"""QAFINAL-FU-ENG (R30-ENG): Qwen Code's finished command reads as Gemini CLI's labelled envelope.

On the durable lane (``E2E_ENGINE=acp-qwen run.sh durable-up``, CJ-E1) a command that printed nothing showed a step
whose output was ``Command: …\\nDirectory: (root)\\nOutput: (empty)``: the envelope's labels reached the person and
the empty result was not empty (R25OPS-FU-2). The profile now says how the agent writes a result
(``commandResult: labelled``) and the mapper keeps only the ``Output:`` section.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.events import CommandFinished, CommandOutput
from harness_engines.acp.mapper import AcpEventMapper, labelled_output
from harness_engines.acp.permissions import SessionPolicy
from harness_engines.acp.registry import packaged_acp_agents

from .test_file_changes import POLICY

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue

# What the lane's Qwen Code 0.26.5 sent for CJ-E1's command (`mkdir … && printf … > out/report.pdf`).
EMPTY = "Command: mkdir -p out && printf 'x' > out/report.pdf\nDirectory: (root)\nOutput: (empty)"
PRINTED = "Command: echo hi; echo there\nDirectory: (root)\nOutput: hi\nthere\nError: (none)\nExit Code: 0"


def _run(policy: SessionPolicy, text: str) -> list[object]:
    mapper = AcpEventMapper(turn_id="t-1", policy=policy)
    call: dict[str, JsonValue] = {"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "run_shell_command",
                                  "kind": "execute", "status": "pending", "rawInput": {"command": "echo hi"}}
    events = mapper.update(call)
    events += mapper.update({"sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
                             "content": [{"type": "content", "content": {"type": "text", "text": text}}]})
    return events


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (EMPTY, ""),
        (PRINTED, "hi\nthere"),
        ("Command: true\nDirectory: (root)\nOutput: (none)\nExit Code: 0", ""),
        ("plain output, no envelope\n", "plain output, no envelope\n"),
        ("Output: not an envelope\n", "Output: not an envelope\n"),
    ],
)
def test_r30eng_l1_the_output_section_of_a_labelled_result_is_the_output(text: str, expected: str) -> None:
    assert labelled_output(text) == expected


def test_r30eng_l2_a_labelled_agents_empty_result_is_no_output_step_and_a_printed_one_is_its_text() -> None:
    labelled = dataclasses.replace(POLICY, command_result="labelled")
    assert not [e for e in _run(labelled, EMPTY) if isinstance(e, CommandOutput)]
    assert any(isinstance(e, CommandFinished) for e in _run(labelled, EMPTY))
    shown = [e.text for e in _run(labelled, PRINTED) if isinstance(e, CommandOutput)]
    assert shown == ["hi\nthere"]


def test_r30eng_l3_an_agent_that_writes_plain_text_keeps_its_output_as_it_is() -> None:
    shown = [e.text for e in _run(POLICY, PRINTED) if isinstance(e, CommandOutput)]
    assert shown == [PRINTED]


def test_r30eng_l4_qwen_codes_profile_says_labelled_and_the_rendered_policy_carries_it() -> None:
    agents = {a.id: a for a in packaged_acp_agents(unsigned_ok=True)}
    assert agents["qwen"].command_result == "labelled"
    assert {a.command_result for i, a in agents.items() if i not in ("qwen", "dsh")} == {"text"}
    assert SessionPolicy.from_json(json.dumps({"commandResult": "labelled"}).encode()).command_result == "labelled"
    assert SessionPolicy.from_json(b"{}").command_result == "text"
