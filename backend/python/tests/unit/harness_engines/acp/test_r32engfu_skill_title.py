"""R31GVENG-FU-2's journey line on Gemini CLI (R32-ENGFU): a skill step names the skill it read.

Gemini CLI 0.63.0 sends no ``rawInput`` for its ``activate_skill`` call, only a title that names the skill first,
quoted (``"report": Use when asked for a report.``, the real binary under gVisor), so the job's step could not say
which skill it read and the journey's ``skills.preload`` line on Gemini went red once the engine loaded the skill its
own way. The mapper takes the name from the title for the agent's skill tool when the input is missing.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from harness_core.engines.events import ToolCallSeen
from harness_core.engines.policy import NativeTool
from harness_engines.acp.mapper import AcpEventMapper

from .test_file_changes import POLICY

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue

GEMINI = dataclasses.replace(POLICY, native_names={**POLICY.native_names, "activate_skill": NativeTool.SKILL})
TITLE = '"report": Use when asked for a report.'


def _seen(policy: object, title: str, raw_input: JsonValue = None, call_id: str = "activate_skill__call_1") -> list[ToolCallSeen]:
    mapper = AcpEventMapper(turn_id="t-1", policy=policy)  # type: ignore[arg-type]
    call: dict[str, JsonValue] = {"sessionUpdate": "tool_call", "toolCallId": call_id, "status": "pending",
                                  "title": title, "kind": "other", "content": []}
    if raw_input is not None:
        call["rawInput"] = raw_input
    events = mapper.update(call)
    events += mapper.update({"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "completed",
                             "title": title, "content": [{"type": "content", "content": {"type": "text", "text": "ok"}}]})
    return [e for e in events if isinstance(e, ToolCallSeen)]


def test_r32engfu_s1_a_skill_call_without_input_is_named_from_its_title() -> None:
    seen = _seen(dataclasses.replace(GEMINI, call_id_name_separator="__"), TITLE)
    assert [(e.tool, e.status, e.arguments) for e in seen] == [
        ("activate_skill", "started", {"name": "report"}),
        ("activate_skill", "completed", {"name": "report"}),
    ]


def test_r32engfu_s2_the_agents_own_input_wins_and_other_tools_keep_none() -> None:
    gemini = dataclasses.replace(GEMINI, call_id_name_separator="__")
    assert _seen(gemini, TITLE, {"name": "pdf"})[0].arguments == {"name": "pdf"}
    assert _seen(gemini, TITLE, call_id="read_file__call_2")[0].arguments is None
    assert _seen(gemini, "report: no quotes")[0].arguments is None
