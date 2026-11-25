"""WP-112: every engine's mapper keeps a tool step's facts (its arguments, and its own tool's result).

Before, ``ToolCallSeen`` had no room for them, so each mapper dropped what it had in hand: Claude Code's ``tool_use``
input and ``tool_result`` text, Codex's item arguments, opencode's state input, title and output, an ACP agent's
``rawInput``, ``title``, ``locations`` and content (Gemini CLI, Goose, Qwen Code, DeepSeek Harness, opencode over ACP).
"""

from __future__ import annotations

import dataclasses
from importlib import resources
from typing import Any

import pytest

from harness_core.access.types import AccessRouteRef
from harness_core.engines.events import ToolCallSeen, ToolCallUpdated
from harness_core.engines.types import EngineMode, EngineProfile
from harness_core.testing.engines.contract import ENDPOINTS
from harness_engines.acp.launch import SESSION_POLICY_PATH, render_launch
from harness_engines.acp.mapper import AcpEventMapper
from harness_engines.acp.permissions import SessionPolicy
from harness_engines.acp.profiles import parse_acp_agents
from harness_engines.acp.registry import AGENTS_RESOURCE
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.opencode.mapper import OpencodeEventMapper

SHIPPED = resources.files("harness_engines").joinpath(AGENTS_RESOURCE).read_bytes()


def _seen(events: list[Any]) -> list[ToolCallSeen]:
    return [e for e in events if isinstance(e, ToolCallSeen)]


def test_claude_code_keeps_the_input_and_its_own_tools_result() -> None:
    mapper = ClaudeEventMapper(turn_id="t")
    use = {"type": "tool_use", "id": "call_g", "name": "Glob", "input": {"pattern": "**/*.md"}}
    started = _seen(mapper.feed({"type": "assistant", "message": {"id": "m1", "content": [use]}}))
    done = {"type": "tool_result", "tool_use_id": "call_g", "content": "/workspace/a.md\n/workspace/b.md"}
    ended = _seen(mapper.feed({"type": "user", "message": {"content": [done]}}))
    assert started[0].arguments == {"pattern": "**/*.md"}
    assert (ended[0].status, ended[0].arguments, ended[0].result) == (
        "completed", {"pattern": "**/*.md"}, "/workspace/a.md\n/workspace/b.md"
    )


def test_claude_code_keeps_a_gateway_tools_arguments() -> None:
    mapper = ClaudeEventMapper(turn_id="t")
    use = {"type": "tool_use", "id": "c1", "name": "mcp__tools__dynamic__fetch_url", "input": {"url": "https://onyx.app"}}
    started = _seen(mapper.feed({"type": "assistant", "message": {"id": "m1", "content": [use]}}))
    assert (started[0].tool, started[0].arguments) == ("dynamic__fetch_url", {"url": "https://onyx.app"})


def test_codex_keeps_the_items_arguments() -> None:
    mapper = CodexEventMapper(thread_id="th", turn_id="t")
    mapper.begin("ct")
    item = {"type": "mcpToolCall", "id": "call_1", "tool": "web_search", "arguments": {"query": "PipesHub"}, "status": "inProgress"}
    started = _seen(mapper.feed({"method": "item/started", "params": {"threadId": "th", "turnId": "ct", "item": item}}))
    assert started[0].arguments == {"query": "PipesHub"}


def test_opencode_keeps_input_title_and_output() -> None:
    mapper = OpencodeEventMapper(session_id="s", turn_id="t")
    part = {"id": "p", "messageID": "m", "sessionID": "s", "type": "tool", "tool": "read", "callID": "c"}
    events = mapper.feed({"type": "session.status", "properties": {"sessionID": "s", "status": {"type": "busy"}}})
    for state in (
        {"status": "running", "input": {"filePath": "/workspace/a.md"}, "title": "a.md"},
        {"status": "completed", "input": {"filePath": "/workspace/a.md"}, "output": "# A", "title": "a.md", "metadata": {}},
    ):
        events += mapper.feed({"type": "message.part.updated", "properties": {"sessionID": "s", "part": {**part, "state": state}}})
    started, ended = _seen(events)
    assert (started.arguments, started.title) == ({"filePath": "/workspace/a.md"}, "a.md")
    assert ended.result == "# A"


def _acp(agent_id: str) -> AcpEventMapper:
    agent = next(p for p in parse_acp_agents(SHIPPED) if p.id == agent_id)
    profile = EngineProfile(engine=agent.engine, mode=EngineMode.BUILD, access=AccessRouteRef(route_id="r", digest="d"))
    launch = render_launch(agent, profile, dataclasses.replace(ENDPOINTS, model_api=agent.model_api), "/skills")
    return AcpEventMapper(turn_id="t", policy=SessionPolicy.from_json(launch.files[SESSION_POLICY_PATH]))


@pytest.mark.parametrize("agent_id", ["gemini", "qwen", "goose", "dsh", "opencode"])
def test_an_acp_agent_keeps_input_title_locations_and_content(agent_id: str) -> None:
    mapper = _acp(agent_id)
    call = {
        "sessionUpdate": "tool_call", "toolCallId": "c1", "title": "ReadFile notes.md", "kind": "read",
        "status": "pending", "rawInput": {"absolute_path": "/workspace/notes.md"},
        "locations": [{"path": "/workspace/notes.md"}],
    }
    started = _seen(mapper.update(call))
    done = {
        "sessionUpdate": "tool_call_update", "toolCallId": "c1", "status": "completed",
        "content": [{"type": "content", "content": {"type": "text", "text": "# Notes"}}],
    }
    ended = _seen(mapper.update(done))
    assert started[0].arguments == {"absolute_path": "/workspace/notes.md"}
    assert (started[0].title, started[0].paths) == ("ReadFile notes.md", ("notes.md",))
    assert ended[0].result == "# Notes"


def test_an_acp_call_whose_input_comes_later_has_one_start_and_an_update() -> None:
    """Claude Code's ACP adapter shape: ``tool_call`` with rawInput {} and the input in a ``tool_call_update``. One
    call is one start (the conformance suite's CF-4 holds every engine to it); the late input is an update."""
    mapper = _acp("gemini")
    events = mapper.update({"sessionUpdate": "tool_call", "toolCallId": "c2", "title": "web_fetch", "kind": "fetch",
                            "status": "pending", "rawInput": {}})
    events += mapper.update({"sessionUpdate": "tool_call_update", "toolCallId": "c2", "status": "in_progress",
                             "rawInput": {"url": "https://onyx.app"}})
    events += mapper.update({"sessionUpdate": "tool_call_update", "toolCallId": "c2", "status": "completed",
                             "content": [{"type": "content", "content": {"type": "text", "text": "page"}}]})
    assert [e.status for e in _seen(events)] == ["started", "completed"]
    updated = [e for e in events if isinstance(e, ToolCallUpdated)]
    assert [(e.call_id, e.arguments) for e in updated] == [("c2", {"url": "https://onyx.app"})]
    assert _seen(events)[1].arguments == {"url": "https://onyx.app"}


def test_an_update_that_changes_nothing_is_not_sent() -> None:
    mapper = _acp("qwen")
    call = {"sessionUpdate": "tool_call", "toolCallId": "c3", "title": "read_file", "kind": "read", "status": "pending",
            "rawInput": {"absolute_path": "/workspace/a.md"}}
    events = mapper.update(call) + mapper.update({**call, "sessionUpdate": "tool_call_update", "status": "in_progress"})
    assert [type(e).__name__ for e in events] == ["ToolCallSeen"]
