"""WP-102 (C2.41): the scripted model reads a directive that an engine glued to its own context block.

Qwen Code 0.26.5 sends the first prompt as one user message: its ``<system-reminder>`` blocks (the skills list, the
folder structure) and then the prompt, with no line break after the last ``</system-reminder>``. A real model reads
the prompt either way; the scripted model reads directives a line at a time, so a block's end also ends a line.
"""

from __future__ import annotations

from harness_core.testing.engines.script import (
    ChatMessage,
    Directive,
    ScriptedBrain,
    parse_directives,
)

REMINDERS = (
    "<system-reminder>\nThe following skills are available.\n<description>\nUse when asked for a report.\n</description>\n"
    "</system-reminder><system-reminder>\nI'm currently working in /workspace\n</system-reminder>"
)


def test_a_directive_glued_to_an_engines_reminder_block_is_read() -> None:
    assert parse_directives(REMINDERS + 'tool search {"q": 1}\nsay ok') == [
        Directive("tool", 'search {"q": 1}'),
        Directive("say", "ok"),
    ]


def test_the_brain_calls_the_gateway_tool_qwen_lists() -> None:
    reply = ScriptedBrain().reply(
        [ChatMessage("user", REMINDERS + 'tool search {"q": 1}\nsay ok')], ["read_file", "mcp__tools__search"], "c1"
    )
    assert [c.name for c in reply.tool_calls] == ["mcp__tools__search"]


def test_a_write_file_tool_is_given_an_absolute_path_in_the_workspace() -> None:
    """Qwen Code's ``write_file`` refuses a relative ``file_path`` ("File path must be absolute"), as Claude Code's
    ``Write`` does; Gemini CLI's takes either. The scripted model names the file under the sandbox's workspace."""
    reply = ScriptedBrain().reply([ChatMessage("user", "write out/a.md hi")], ["write_file", "edit"], "c1")
    assert [(c.name, c.args()) for c in reply.tool_calls] == [
        ("write_file", {"file_path": "/workspace/out/a.md", "content": "hi"})
    ]


def test_r31jfix_a_write_tool_is_given_the_argument_names_its_schema_declares() -> None:
    """R31-JFIX: the journeys' scripted model named every ``write`` tool's file ``filePath`` (opencode's), so DeepSeek
    Harness's (``file_path``) and Goose's (``path``) refused it and the job's ``cat`` found no file."""
    brain = ScriptedBrain()
    calls = {
        declared: brain.reply([ChatMessage("user", "write out/a.md hi")], ["write"], "c1",
                              {"write": frozenset({declared, "content"})}).tool_calls[0].args()
        for declared in ("filePath", "file_path", "path")
    }
    assert calls == {
        "filePath": {"filePath": "out/a.md", "content": "hi"},
        "file_path": {"file_path": "/workspace/out/a.md", "content": "hi"},
        "path": {"path": "/workspace/out/a.md", "content": "hi"},
    }
