"""EV-07 / CM-01: an engine's own helper (Claude Code's Agent tool, a Codex sub-agent thread, an opencode task
session) has its steps mapped and named as its own, so the chat can nest them under it; its text and its task list
never become the run's."""

from __future__ import annotations

from typing import Any

from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    EngineEvent,
    FileChanged,
    MessageCompleted,
    PlanUpdated,
    SubAgentFinished,
    SubAgentStarted,
    ToolCallSeen,
    TurnCompleted,
)
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.opencode.mapper import OpencodeEventMapper

STEPS = (CommandStarted, CommandOutput, CommandFinished, FileChanged, ToolCallSeen)


def _steps(events: list[EngineEvent]) -> list[tuple[str, str | None]]:
    return [(type(e).__name__, e.sub_session_id) for e in events if isinstance(e, STEPS)]


def _contiguous(events: list[EngineEvent]) -> None:
    assert [e.meta.seq for e in events] == list(range(len(events)))


# ---- Claude Code (stream-json, 2.1.289 shapes: agent.jsonl) ----

_SPAWN = "toolu_spawn"
_TASK = "a3a8c506faecf4a2b"


def _claude(*messages: dict[str, Any]) -> list[EngineEvent]:
    mapper = ClaudeEventMapper(turn_id="trn_ev07")
    return [e for m in messages for e in mapper.feed(m)]


def _assistant(content: list[dict[str, Any]], *, parent: str | None = None, mid: str = "m") -> dict[str, Any]:
    return {"type": "assistant", "parent_tool_use_id": parent, "message": {"id": mid, "content": content}}


def _results(blocks: list[dict[str, Any]], *, parent: str | None = None, detail: Any = None) -> dict[str, Any]:  # noqa: ANN401
    out: dict[str, Any] = {"type": "user", "parent_tool_use_id": parent, "message": {"content": blocks}}
    if detail is not None:
        out["tool_use_result"] = detail
    return out


_CLAUDE_TURN = (
    _assistant([{"type": "tool_use", "id": _SPAWN, "name": "Agent", "input": {"subagent_type": "helper", "prompt": "p"}}]),
    {"type": "system", "subtype": "task_started", "task_id": _TASK, "tool_use_id": _SPAWN, "subagent_type": "helper",
     "description": "List the files"},
    _assistant(
        [
            {"type": "text", "text": "helper says"},
            {"type": "tool_use", "id": "toolu_todo", "name": "TodoWrite", "input": {"todos": [{"content": "x", "status": "pending"}]}},
            {"type": "tool_use", "id": "toolu_sh", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_use", "id": "toolu_w", "name": "Write", "input": {"file_path": "/workspace/a.md"}},
            {"type": "tool_use", "id": "toolu_kb", "name": "mcp__tools__search", "input": {"q": "x"}},
        ],
        parent=_SPAWN,
        mid="m-helper",
    ),
    _results([{"type": "tool_result", "tool_use_id": "toolu_sh", "content": "a.md"}], parent=_SPAWN, detail={"stdout": "a.md", "stderr": ""}),
    _results([{"type": "tool_result", "tool_use_id": "toolu_w", "content": "ok"}], parent=_SPAWN, detail={"type": "create"}),
    _results([{"type": "tool_result", "tool_use_id": "toolu_kb", "content": "hits"}], parent=_SPAWN),
    {"type": "system", "subtype": "task_notification", "task_id": _TASK, "tool_use_id": _SPAWN, "status": "completed"},
    _results([{"type": "tool_result", "tool_use_id": _SPAWN, "content": "deep"}]),
    _assistant([{"type": "tool_use", "id": "toolu_own", "name": "Bash", "input": {"command": "pwd"}}], mid="m2"),
    _results([{"type": "tool_result", "tool_use_id": "toolu_own", "content": "/workspace"}], detail={"stdout": "/workspace", "stderr": ""}),
    _assistant([{"type": "text", "text": "top"}], mid="m3"),
    {"type": "result", "subtype": "success", "is_error": False, "result": "top", "usage": {}},
)


def test_ev07_claude_a_helpers_tool_calls_are_its_steps() -> None:
    events = _claude(*_CLAUDE_TURN)
    _contiguous(events)
    assert _steps(events) == [
        ("CommandStarted", _TASK),
        ("ToolCallSeen", _TASK),
        ("ToolCallSeen", _TASK),
        ("CommandOutput", _TASK),
        ("CommandFinished", _TASK),
        ("ToolCallSeen", _TASK),
        ("FileChanged", _TASK),
        ("ToolCallSeen", _TASK),
        ("CommandStarted", None),
        ("CommandOutput", None),
        ("CommandFinished", None),
    ]
    (changed,) = [e for e in events if isinstance(e, FileChanged)]
    assert (changed.path, changed.change) == ("a.md", "added")
    started = [e for e in events if isinstance(e, ToolCallSeen) and e.status == "started"]
    assert [(e.tool, e.call_id) for e in started] == [("Write", "toolu_w"), ("search", "toolu_kb")]


def test_ev07_claude_a_helpers_text_and_task_list_stay_its_own() -> None:
    events = _claude(*_CLAUDE_TURN)
    assert [e.text for e in events if isinstance(e, MessageCompleted)] == ["top"]
    assert not [e for e in events if isinstance(e, PlanUpdated)]
    ended = events[-1]
    assert isinstance(ended, TurnCompleted) and ended.final_text == "top"
    assert [type(e).__name__ for e in events if isinstance(e, (SubAgentStarted, SubAgentFinished))] == [
        "SubAgentStarted",
        "SubAgentFinished",
    ]
    (started,) = [e for e in events if isinstance(e, SubAgentStarted)]
    assert (started.agent, started.task) == ("helper", "List the files")


def test_ev07_claude_a_message_of_a_helper_never_started_is_not_mapped() -> None:
    orphan = _assistant([{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "ls"}}], parent="toolu_x")
    assert _claude(orphan) == []


# ---- Codex (app-server, 0.160 shapes: spawn.jsonl) ----

THREAD = "11111111-1111-1111-1111-111111111111"
CHILD = "33333333-3333-3333-3333-333333333333"
CODEX_TURN = "22222222-2222-2222-2222-222222222222"


def _note(method: str, thread: str = THREAD, **params: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"method": method, "params": {"threadId": thread, "turnId": CODEX_TURN, **params}}


def test_ev07_codex_a_sub_agent_threads_items_are_its_steps() -> None:
    mapper = CodexEventMapper(thread_id=THREAD, turn_id="trn_ev07")
    mapper.begin(CODEX_TURN)
    spawn = {
        "type": "collabAgentToolCall",
        "id": "call_1",
        "tool": "spawnAgent",
        "status": "completed",
        "prompt": "List the files",
        "receiverThreadIds": [CHILD],
    }
    command = {"type": "commandExecution", "id": "c1", "command": "ls", "status": "inProgress"}
    tool = {"type": "mcpToolCall", "id": "k1", "tool": "search", "arguments": {}, "status": "inProgress"}
    change = {"type": "fileChange", "id": "f1", "status": "completed", "changes": [{"path": "/workspace/a.md", "kind": {"type": "add"}}]}
    feed = [
        _note("item/completed", item=spawn),
        _note("item/started", CHILD, item=command),
        _note("item/commandExecution/outputDelta", CHILD, itemId="c1", delta="a.md\n"),
        _note("item/completed", CHILD, item={**command, "status": "completed", "exitCode": 0}),
        _note("item/started", CHILD, item=tool),
        _note("item/completed", CHILD, item={**tool, "status": "completed"}),
        _note("item/completed", CHILD, item=change),
        _note("item/completed", CHILD, item={"type": "agentMessage", "id": "am", "text": "helper says"}),
        _note("turn/plan/updated", CHILD, plan=[{"step": "x", "status": "pending"}]),
        _note("turn/completed", CHILD, turn={"id": "t-child", "status": "completed"}),
        _note("item/completed", item={"type": "agentMessage", "id": "am2", "text": "top"}),
        _note("turn/completed", turn={"id": CODEX_TURN, "status": "completed"}),
    ]
    events = [e for m in feed for e in mapper.feed(m)]
    _contiguous(events)
    assert _steps(events) == [
        ("CommandStarted", CHILD),
        ("CommandOutput", CHILD),
        ("CommandFinished", CHILD),
        ("ToolCallSeen", CHILD),
        ("ToolCallSeen", CHILD),
        ("FileChanged", CHILD),
    ]
    assert [e.text for e in events if isinstance(e, MessageCompleted)] == ["top"]
    assert not [e for e in events if isinstance(e, PlanUpdated)]
    ended = events[-1]
    assert isinstance(ended, TurnCompleted) and ended.final_text == "top"
    (started,) = [e for e in events if isinstance(e, SubAgentStarted)]
    assert (started.sub_session_id, started.task) == (CHILD, "List the files")


# ---- opencode (/event SSE, 1.18.34 shapes: native-default-task.events.jsonl) ----


def _oc(kind: str, session: str, **props: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"type": kind, "properties": {"sessionID": session, **props}}


def _tool_part(session: str, call: str, tool: str, status: str, **state: Any) -> dict[str, Any]:  # noqa: ANN401
    return _oc("message.part.updated", session, part={"type": "tool", "callID": call, "tool": tool, "state": {"status": status, **state}})


def test_ev07_opencode_a_task_sessions_tool_calls_are_its_steps() -> None:
    mapper = OpencodeEventMapper(session_id="ses_001", turn_id="trn_ev07")
    feed = [
        _oc("session.status", "ses_001", status={"type": "busy"}),
        _oc("session.created", "ses_002", info={"id": "ses_002", "parentID": "ses_001"}),
        _tool_part("ses_001", "call_1", "task", "running", input={"subagent_type": "general", "description": "List the files"}, metadata={"sessionId": "ses_002"}),
        _tool_part("ses_002", "h1", "bash", "running", input={"command": "ls"}),
        _tool_part("ses_002", "h1", "bash", "completed", input={"command": "ls"}, output="a.md", metadata={"exit": 0}),
        _tool_part("ses_002", "h2", "tools_search", "running", input={}),
        _tool_part("ses_002", "h2", "tools_search", "completed", input={}),
        _tool_part("ses_002", "h3", "todowrite", "running", input={"todos": [{"content": "x", "status": "pending"}]}),
        _tool_part("ses_002", "h4", "task", "error", input={}),
        _tool_part("ses_001", "call_1", "task", "completed", input={}, metadata={"sessionId": "ses_002"}),
        _oc("session.idle", "ses_001"),
    ]
    events = [e for m in feed for e in mapper.feed(m)]
    _contiguous(events)
    assert _steps(events) == [
        ("CommandStarted", "ses_002"),
        ("CommandOutput", "ses_002"),
        ("CommandFinished", "ses_002"),
        ("ToolCallSeen", "ses_002"),
        ("ToolCallSeen", "ses_002"),
    ]
    assert not [e for e in events if isinstance(e, PlanUpdated)]
    assert [(type(e).__name__, e.sub_session_id) for e in events if isinstance(e, (SubAgentStarted, SubAgentFinished))] == [
        ("SubAgentStarted", "ses_002"),
        ("SubAgentFinished", "ses_002"),
    ]
    (started,) = [e for e in events if isinstance(e, SubAgentStarted)]
    assert (started.agent, started.task) == ("general", "List the files")
