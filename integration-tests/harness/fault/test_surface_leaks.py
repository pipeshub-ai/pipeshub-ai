"""What each surface's audience may learn of the chat run's tool call (C-5, C-20)."""

from __future__ import annotations

import json

from test_crash_points import surface_leaks

RUN = "run-1"


def sse(*frames: dict) -> str:
    return "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)


# The Slack bot's view of the chat script's read (projection/audiences.ts `slack`): the call's name, never its input or output.
NAMED_CALL = sse(
    {"type": "TOOL_CALL_START", "toolCallId": "call_t1_0", "toolCallName": "lookup", "runId": RUN},
    {"type": "STATE_SNAPSHOT", "snapshot": {"status": "running_tool", "current_tool": "lookup"}},
    {"type": "TOOL_CALL_RESULT", "toolCallId": "call_t1_0", "status": "completed", "runId": RUN},
)
ARGUMENTS = sse({"type": "TOOL_CALL_ARGS", "toolCallId": "call_t1_0", "delta": '{"q": "refund policy"}'})
RESULT = sse({"type": "TOOL_CALL_RESULT", "toolCallId": "call_t1_0", "content": json.dumps({"found": "status", "blob": "x" * 64})})


def test_slack_may_name_the_call() -> None:
    assert surface_leaks("slack", NAMED_CALL) == []


def test_slack_never_gets_the_call_arguments_or_result() -> None:
    assert surface_leaks("slack", NAMED_CALL + ARGUMENTS) != []
    assert surface_leaks("slack", NAMED_CALL + RESULT) != []


def test_mcp_gets_nothing_of_the_call() -> None:
    assert surface_leaks("mcp", NAMED_CALL) != []
    assert surface_leaks("mcp", ARGUMENTS) != []
