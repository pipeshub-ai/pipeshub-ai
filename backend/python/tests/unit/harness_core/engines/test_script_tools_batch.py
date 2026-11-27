"""The scripted model's ``tools`` directive (QAFIX-5): one step calls a tool once per item, as a model's batch does."""

from __future__ import annotations

import json

from harness_core.testing.engines.script import (
    NO_TOOL_REPLY,
    ChatMessage,
    ScriptedBrain,
    ToolCall,
    parse_directives,
)

SCRIPT = 'tools create_ticket [{"title": "a"}, {"title": "b"}, {"title": "c"}]\ntool create_ticket {"title": "d"}\nsay ok'
TOOLS = ["mcp_e2esaas_create_ticket"]


def _turn(*steps: tuple[ToolCall, ...]) -> list[ChatMessage]:
    messages = [ChatMessage("user", SCRIPT)]
    for calls in steps:
        messages.append(ChatMessage("assistant", tool_calls=calls))
        messages += [ChatMessage("tool", "x", tool_call_id=c.id) for c in calls]
    return messages


def test_tools_is_one_step_of_n_calls_then_the_script_goes_on() -> None:
    brain = ScriptedBrain()
    batch = brain.reply(_turn(), TOOLS, "c1")
    assert [(c.name, json.loads(c.arguments)) for c in batch.tool_calls] == [
        ("mcp_e2esaas_create_ticket", {"title": "a"}),
        ("mcp_e2esaas_create_ticket", {"title": "b"}),
        ("mcp_e2esaas_create_ticket", {"title": "c"}),
    ]
    assert len({c.id for c in batch.tool_calls}) == 3
    single = brain.reply(_turn(batch.tool_calls), TOOLS, "c2")
    assert [json.loads(c.arguments) for c in single.tool_calls] == [{"title": "d"}]
    assert brain.reply(_turn(batch.tool_calls, single.tool_calls), TOOLS, "c3").content == "ok"


def test_a_hold_after_a_batch_waits_for_the_whole_batch() -> None:
    messages = [ChatMessage("user", 'tools create_ticket [{"title": "a"}, {"title": "b"}]\nhold\nsay ok')]
    brain = ScriptedBrain()
    assert brain.pending_hold(messages) is None
    calls = brain.reply(messages, TOOLS, "c1").tool_calls
    after = [*messages, ChatMessage("assistant", tool_calls=calls), *(ChatMessage("tool", "x", tool_call_id=c.id) for c in calls)]
    assert brain.pending_hold(after) == (0, 1)


def test_a_batch_whose_tool_is_not_offered_gets_no_call_and_a_bad_list_is_not_a_directive() -> None:
    assert ScriptedBrain().reply(_turn(), ["bash"], "c1").content == NO_TOOL_REPLY
    assert parse_directives('tools create_ticket {"title": "a"}') == []
