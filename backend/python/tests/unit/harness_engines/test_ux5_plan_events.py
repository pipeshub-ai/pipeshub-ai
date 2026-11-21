"""UX5 EG-3: each engine's own task list becomes one normalized ``PlanUpdated``."""

from __future__ import annotations

from harness_core.engines.events import PlanItem, PlanUpdated, ToolCallSeen
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.codex.mapper import CodexEventMapper
from harness_engines.opencode.mapper import OpencodeEventMapper

PLAN = (
    PlanItem("Read the failing test", "completed"),
    PlanItem("Fix the parser", "in_progress"),
    PlanItem("Run the tests", "pending"),
)


def _plans(events: list[object]) -> list[tuple[PlanItem, ...]]:
    return [e.items for e in events if isinstance(e, PlanUpdated)]


def test_eg3_codex_plan_updates() -> None:
    mapper = CodexEventMapper(thread_id="th_1", turn_id="trn_1")
    mapper.begin("cx_1")
    events = mapper.feed(
        {
            "method": "turn/plan/updated",
            "params": {
                "threadId": "th_1",
                "turnId": "cx_1",
                "plan": [
                    {"step": "Read the failing test", "status": "completed"},
                    {"step": "Fix the parser", "status": "inProgress"},
                    {"step": "Run the tests", "status": "pending"},
                ],
            },
        }
    )
    assert _plans(events) == [PLAN]


def test_eg3_claude_code_todowrite_is_a_plan_not_a_tool_call() -> None:
    mapper = ClaudeEventMapper(turn_id="trn_1")
    todos = [
        {"content": "Read the failing test", "status": "completed", "activeForm": "Reading"},
        {"content": "Fix the parser", "status": "in_progress", "activeForm": "Fixing"},
        {"content": "Run the tests", "status": "pending", "activeForm": "Running"},
    ]
    events = mapper.feed(
        {
            "type": "assistant",
            "message": {"id": "m1", "content": [{"type": "tool_use", "id": "tu_1", "name": "TodoWrite", "input": {"todos": todos}}]},
        }
    )
    events += mapper.feed(
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}]}}
    )
    assert _plans(events) == [PLAN]
    assert [e for e in events if isinstance(e, ToolCallSeen)] == []


def test_eg3_opencode_todowrite() -> None:
    mapper = OpencodeEventMapper(session_id="ses_1", turn_id="trn_1")
    _ = mapper.feed({"type": "session.status", "properties": {"sessionID": "ses_1", "status": {"type": "busy"}}})
    todos = [
        {"content": "Read the failing test", "status": "completed", "priority": "high", "id": "1"},
        {"content": "Fix the parser", "status": "in_progress", "priority": "high", "id": "2"},
        {"content": "Run the tests", "status": "pending", "priority": "low", "id": "3"},
    ]
    part = {
        "type": "tool",
        "id": "p1",
        "messageID": "msg_1",
        "callID": "call_1",
        "tool": "todowrite",
        "state": {"status": "running", "input": {"todos": todos}},
    }
    events = mapper.feed({"type": "message.part.updated", "properties": {"sessionID": "ses_1", "part": part}})
    assert _plans(events) == [PLAN]
    assert [e for e in events if isinstance(e, ToolCallSeen)] == []


def test_eg3_untitled_items_skipped_and_unknown_statuses_pending() -> None:
    from harness_core.engines.events import EngineEventMeta
    from harness_engines.plans import plan_updated

    plan = plan_updated(
        EngineEventMeta("t", 1), [{"content": " x "}, {"status": "completed"}, "junk", {"content": "y", "status": "??"}], ("content",)
    )
    assert plan is not None
    assert plan.items == (PlanItem("x", "pending"), PlanItem("y", "pending"))
    assert plan_updated(EngineEventMeta("t", 1), "not a list", ("content",)) is None
