from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.context import RunContext


class EventType(str, Enum):
    AGENT_START = "agent_start"
    AGENT_COMPLETE = "agent_complete"
    TURN_START = "turn_start"
    TURN_COMPLETE = "turn_complete"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_BLOCKED = "tool_blocked"
    CHECKPOINT_SAVED = "checkpoint_saved"
    BUDGET_WARNING = "budget_warning"
    CANCELLATION = "cancellation"
    ERROR = "error"
    STATUS_CHANGE  = "status_change"
    CHILD_SPAWNED  = "child_spawned"
    CHILD_COMPLETE = "child_complete"
    HIL_REQUESTED  = "hil_requested"
    HIL_RESPONDED  = "hil_responded"

    # AG-UI-aligned vocabulary (Phase 4 streaming — see agent/streaming.py and
    # Agent._emit's _AG_UI_ALIASES table in agent/__init__.py). Fired ALONGSIDE
    # the legacy event types above at the same call sites — never instead of
    # them — so existing EventEmitter consumers matching on the old names see
    # no behavior change; these are purely additive for AG-UI-shaped frontends
    # (https://ag-ui.com): RUN_STARTED/FINISHED/ERROR, TEXT_MESSAGE_*,
    # TOOL_CALL_START/END, STATE_SNAPSHOT/DELTA.
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_ERROR = "run_error"
    TEXT_MESSAGE_START = "text_message_start"
    TEXT_MESSAGE_CONTENT = "text_message_content"
    TEXT_MESSAGE_END = "text_message_end"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    STATE_SNAPSHOT = "state_snapshot"
    STATE_DELTA = "state_delta"


class AgentEvent(BaseModel):
    """Structured event emitted at every significant state change.

    Consumers (UI, monitoring, tests) subscribe via EventEmitter implementations.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: EventType
    run_context: RunContext
    payload: dict[str, Any] = Field(default_factory=dict)


class EventEmitter(ABC):
    """Pluggable event sink.

    Backends: LoggingEventEmitter, WebSocketEmitter, RedisStreamEmitter,
              CompositeEmitter (fan-out to multiple sinks).
    """

    @abstractmethod
    async def emit(self, event: AgentEvent) -> None: ...


class CompositeEmitter(EventEmitter):
    """Fan-out to multiple EventEmitter backends."""

    def __init__(self, emitters: list[EventEmitter] | None = None) -> None:
        self._emitters: list[EventEmitter] = list(emitters) if emitters is not None else []

    def add(self, emitter: EventEmitter) -> None:
        self._emitters.append(emitter)

    async def emit(self, event: AgentEvent) -> None:
        last_exc: BaseException | None = None
        for emitter in self._emitters:
            try:
                await emitter.emit(event)
            except Exception as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
