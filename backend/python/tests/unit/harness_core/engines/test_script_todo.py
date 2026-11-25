"""UXR-B1: the scripted model's ``todo`` directive calls each engine's own task-list tool in its own shape."""

from __future__ import annotations

import json

import pytest

from harness_core.testing.engines.script import (
    NO_TOOL_REPLY,
    ChatMessage,
    ScriptedBrain,
)

PLAN = '[{"title": "Read", "status": "in_progress"}, {"title": "Fix", "status": "pending"}]'


@pytest.mark.parametrize(
    ("tools", "expected"),
    [
        (["bash", "todowrite"], ("todowrite", {"todos": [
            {"content": "Read", "status": "in_progress", "priority": "medium", "id": "1"},
            {"content": "Fix", "status": "pending", "priority": "medium", "id": "2"},
        ]})),
        (["Bash", "TodoWrite"], ("TodoWrite", {"todos": [
            {"content": "Read", "status": "in_progress", "activeForm": "Read"},
            {"content": "Fix", "status": "pending", "activeForm": "Fix"},
        ]})),
        (["exec_command", "update_plan"], ("update_plan", {"plan": [
            {"step": "Read", "status": "in_progress"},
            {"step": "Fix", "status": "pending"},
        ]})),
    ],
)
def test_todo_directive_uses_the_engines_own_tool(tools: list[str], expected: tuple[str, object]) -> None:
    reply = ScriptedBrain().reply([ChatMessage("user", f"todo {PLAN}\nsay ok")], tools, "c1")
    assert [(c.name, json.loads(c.arguments)) for c in reply.tool_calls] == [expected]


def test_an_engine_without_a_task_list_tool_gets_no_call() -> None:
    assert ScriptedBrain().reply([ChatMessage("user", f"todo {PLAN}")], ["bash"], "c1").content == NO_TOOL_REPLY
