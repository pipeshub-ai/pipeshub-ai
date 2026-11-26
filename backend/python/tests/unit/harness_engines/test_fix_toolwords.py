"""FIX-TOOLWORDS (QA-AUTO FU-5): a gateway call an engine makes is one step, named as the gateway names it.

The durable lane (opencode, scripted model) showed "Used Tools mcp e2esaas create ticket" as a live step beside the
approval for the same ``create_ticket`` call. opencode names a gateway tool ``tools_<tool>``, and its mapper kept the
prefix (Claude Code's and the ACP mappers strip theirs), so the step had no display name, its gateway result and its
approval were keyed by another name, and the ask could not say which step it was.
"""

from __future__ import annotations

from typing import Any

from app.harness.engines.presenter import PipesHubEngineRunPresenter
from harness_core.engines.events import EngineEventMeta, FileChanged, ToolCallSeen
from harness_engines.opencode.mapper import OpencodeEventMapper

TOOL = "mcp_e2esaas_create_ticket"
ARGS = {"title": "UIP2 444fc4", "body": "From UI-P2."}


def _opencode(tool: str, states: list[dict[str, Any]]) -> list[Any]:
    mapper = OpencodeEventMapper(session_id="s", turn_id="t")
    part = {"id": "p", "messageID": "m", "sessionID": "s", "type": "tool", "tool": tool, "callID": "call_e2e_33"}
    events = mapper.feed({"type": "session.status", "properties": {"sessionID": "s", "status": {"type": "busy"}}})
    for state in states:
        events += mapper.feed({"type": "message.part.updated", "properties": {"sessionID": "s", "part": {**part, "state": state}}})
    return events


def test_opencode_names_a_gateway_tool_without_its_server_prefix() -> None:
    events = _opencode(
        f"tools_{TOOL}",
        [{"status": "running", "input": ARGS}, {"status": "completed", "input": ARGS, "output": "filed", "metadata": {}}],
    )
    assert [(e.tool, e.status) for e in events if isinstance(e, ToolCallSeen)] == [(TOOL, "started"), (TOOL, "completed")]


def test_opencode_names_the_tool_a_gateway_call_tool_wrapper_runs() -> None:
    events = _opencode("tools_call_tool", [{"status": "running", "input": {"name": TOOL, "arguments": ARGS}}])
    assert [e.tool for e in events if isinstance(e, ToolCallSeen)] == [TOOL]


def test_opencode_native_tools_keep_their_names_and_a_gateway_edit_is_no_file_change() -> None:
    native = _opencode("edit", [{"status": "running", "input": {"filePath": "/workspace/a.md"}}, {"status": "completed", "input": {"filePath": "/workspace/a.md"}, "metadata": {}}])
    assert [e.tool for e in native if isinstance(e, ToolCallSeen)] == ["edit", "edit"]
    assert any(isinstance(e, FileChanged) for e in native)
    gateway = _opencode("tools_edit", [{"status": "running", "input": {"filePath": "/workspace/a.md"}}, {"status": "completed", "input": {"filePath": "/workspace/a.md"}, "metadata": {}}])
    assert not any(isinstance(e, FileChanged) for e in gateway)


class _Stamp:
    task_id = "run_1"
    turn_id = "trn_0123456789abcdef0123456789abcdef"

    def __init__(self) -> None:
        self._n = 0

    def allocate(self) -> int:
        self._n += 1
        return self._n


def _lines(events: list[Any]) -> list[dict[str, Any]]:
    return [dict(e.data) for e in events if e.type == "x.pipeshub.engine.progress"]


def _presenter() -> PipesHubEngineRunPresenter:
    return PipesHubEngineRunPresenter(display_name=lambda tool: "E2Esaas · create_ticket" if tool == TOOL else None)


def test_the_approval_names_the_step_it_gates_and_the_step_has_the_tools_display_name() -> None:
    presenter, stamp = _presenter(), _Stamp()
    step = _lines(presenter.event(stamp, ToolCallSeen(EngineEventMeta("t", 1), "call_e2e_33", TOOL, "started", arguments=ARGS)))
    ask = _lines(presenter.approval(stamp, "ix.approve.k1", TOOL, ARGS))
    assert step[0]["kind"] == "tool"
    assert step[0]["data"]["displayName"] == "E2Esaas · create_ticket"
    assert ask[0]["kind"] == "approval"
    assert ask[0]["data"]["callId"] == "call_e2e_33"


def test_two_calls_of_one_tool_each_ask_on_their_own_step() -> None:
    presenter, stamp = _presenter(), _Stamp()
    other = {"title": "second", "body": "b"}
    presenter.event(stamp, ToolCallSeen(EngineEventMeta("t", 1), "c1", TOOL, "started", arguments=ARGS))
    presenter.event(stamp, ToolCallSeen(EngineEventMeta("t", 2), "c2", TOOL, "started", arguments=other))
    assert _lines(presenter.approval(stamp, "ix.approve.k2", TOOL, other))[0]["data"]["callId"] == "c2"
    assert _lines(presenter.approval(stamp, "ix.approve.k1", TOOL, ARGS))[0]["data"]["callId"] == "c1"


def test_an_ask_with_no_step_of_its_tool_names_none() -> None:
    presenter, stamp = _presenter(), _Stamp()
    presenter.event(stamp, ToolCallSeen(EngineEventMeta("t", 1), "c1", "read", "started", arguments={"filePath": "a"}))
    assert "callId" not in _lines(presenter.approval(stamp, "ix.approve.k1", TOOL, ARGS))[0]["data"]
