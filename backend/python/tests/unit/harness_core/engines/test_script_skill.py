"""The scripted model's ``skill`` directive (SK1): it calls the engine's skill-loading tool, in order."""

from __future__ import annotations

import json

from harness_core.testing.engines.script import (
    NO_TOOL_REPLY,
    ChatMessage,
    ScriptedBrain,
    ToolCall,
)


def _turn(*calls: ToolCall) -> list[ChatMessage]:
    messages = [ChatMessage("user", "skill pdf\nshell ls /skills\nsay ok")]
    for call in calls:
        messages += [ChatMessage("assistant", tool_calls=(call,)), ChatMessage("tool", "x", tool_call_id=call.id)]
    return messages


def test_skill_directive_calls_the_skill_tool_then_the_next_directive() -> None:
    brain = ScriptedBrain()
    first = brain.reply(_turn(), ["bash", "skill", "read"], "c1")
    assert [(c.name, json.loads(c.arguments)) for c in first.tool_calls] == [("skill", {"name": "pdf"})]
    second = brain.reply(_turn(first.tool_calls[0]), ["bash", "skill"], "c2")
    assert [c.name for c in second.tool_calls] == ["bash"]
    third = brain.reply(_turn(first.tool_calls[0], second.tool_calls[0]), ["bash", "skill"], "c3")
    assert third.content == "ok"


def test_r31gveng_fu2_an_engine_without_a_skill_tool_reads_the_skill_file_it_listed() -> None:
    """Codex has no skill tool and lists each skill's file; a model reads it with the shell. The brain answered "tool
    unavailable" instead, so a Codex job given ``skill xlsx`` ran no step at all (R31GVENG-FU-2)."""
    listing = ChatMessage("system", "## Skills\n- pdf: Read PDFs. (file: /pipeshub/skills/pdf/SKILL.md)\n- pdfx: other")
    reply = ScriptedBrain().reply([listing, *_turn()], ["exec_command", "write_stdin"], "c1")
    assert [(c.name, json.loads(c.arguments)) for c in reply.tool_calls] == [
        ("exec_command", {"cmd": "cat /pipeshub/skills/pdf/SKILL.md"})
    ]


def test_r31gveng_fu2_an_unlisted_skill_is_read_with_ph() -> None:
    reply = ScriptedBrain().reply(_turn(), ["bash"], "c1")
    assert [json.loads(c.arguments)["command"] for c in reply.tool_calls] == ["ph skill show pdf"]


def test_an_engine_without_a_skill_tool_or_a_shell_gets_no_call() -> None:
    assert ScriptedBrain().reply(_turn(), ["read"], "c1").content == NO_TOOL_REPLY


def test_r32engfu_the_skill_argument_is_named_as_the_tool_declares_it() -> None:
    """Qwen Code's ``skill`` tool takes ``skill``: given ``name`` it refused the call ("Parameter \"skill\" must be a
    non-empty string"), so a Qwen job never loaded the skill (the journey's skills.preload line on Qwen)."""
    qwen = ScriptedBrain().reply(_turn(), ["run_shell_command", "skill"], "c1", {"skill": frozenset({"skill"})})
    assert [json.loads(c.arguments) for c in qwen.tool_calls] == [{"skill": "pdf"}]
    opencode = ScriptedBrain().reply(_turn(), ["bash", "skill"], "c1", {"skill": frozenset({"name"})})
    assert [json.loads(c.arguments) for c in opencode.tool_calls] == [{"name": "pdf"}]
