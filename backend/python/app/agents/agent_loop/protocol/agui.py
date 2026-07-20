"""AG-UI wire vocabulary and framing helper.

Event names match AG-UI's CURRENT spec (https://docs.ag-ui.com/concepts/events)
— in particular the `REASONING_*` suite, not the `THINKING_*` names AG-UI
removed in its 1.0 release.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any


class AGUIEventType(str, Enum):
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"

    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"

    REASONING_START = "REASONING_START"
    REASONING_MESSAGE_START = "REASONING_MESSAGE_START"
    REASONING_MESSAGE_CONTENT = "REASONING_MESSAGE_CONTENT"
    REASONING_MESSAGE_END = "REASONING_MESSAGE_END"
    REASONING_END = "REASONING_END"

    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"

    STATE_DELTA = "STATE_DELTA"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"

    CUSTOM = "CUSTOM"
    HEARTBEAT = "HEARTBEAT"


def new_id(prefix: str) -> str:
    """Short, prefixed id for AG-UI `messageId`/`toolCallId` fields where
    no upstream identity already exists (`ToolCall.id` is reused directly
    when present — see `agui_emitter.py`)."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def frame(event_type: AGUIEventType, **fields: Any) -> dict[str, Any]:
    """One AG-UI event, hybrid-framed as PipesHub's existing `{event, data}`
    SSE dict: `event` carries the AG-UI type name (so Node's line-based
    `event:` interception and the frontend's `parseSSEBuffer`, both of
    which require an `event:` line, keep working unchanged) while `data`
    carries the FULL AG-UI-compliant JSON, including `"type"` — the
    payload itself is byte-for-byte what a stock `@ag-ui/client` expects
    once the redundant `event:` line is dropped. A deliberate deviation
    from the stock `ag-ui-protocol` `EventEncoder` (data-only frames) —
    see the migration plan's "Wire framing decision" section. Returning
    this exact `{"event", "data"}` shape means `stream_bridge.py`'s
    queue/yield loop needs no protocol-specific branch at all.
    """
    return {"event": event_type.value, "data": {"type": event_type.value, **fields}}


__all__ = ["AGUIEventType", "new_id", "frame"]
