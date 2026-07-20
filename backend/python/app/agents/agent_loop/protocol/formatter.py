"""`ProtocolFormatter`: wire-shape strategy for the handful of producers
that write directly to an `EventSink` and never see a full `AgentEvent`
(`TerminalAnswerStreamer`, `AnswerFinalizer`, `emit_pre_run_clarification`,
the `ask_user_question` hook, `tool_adapter`'s tool_unavailable emission,
sandbox artifact emission) — `AGUIEventEmitter` (`agui_emitter.py`) can't
cover these because they fire from deep inside tool execution / post-run
finalization, below the `Agent.emit()` layer that carries `run_context`.

Each producer keeps saying only WHAT happened (an answer delta arrived, a
question needs asking, an artifact was produced); this decides the wire
SHAPE. Selected once per request off `AgentContext.protocol` (see
`AgentContext.formatter`), defaulting to `LegacyFormatter` so background/
test runs that never set `protocol` see no behavior change.

Every method returns a `list[dict]` (occasionally more than one frame —
e.g. `AGUIFormatter.answer_final` emits both `STATE_SNAPSHOT` and
`RUN_FINISHED`) so every call site can write with the same
`for evt in context.formatter.xxx(...): await event_sink.write(evt)` loop
regardless of protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.protocol.agui import AGUIEventType, frame

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext


class ProtocolFormatter(ABC):
    @abstractmethod
    def answer_delta(
        self, context: "AgentContext", *, chunk: str, accumulated: str,
        citations: list[Any], confidence: str | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def answer_final(self, context: "AgentContext", *, completion_data: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def ask_user_question(self, context: "AgentContext", *, status: str, tool_data: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def artifact(self, context: "AgentContext", *, artifact_data: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def tool_unavailable(
        self, context: "AgentContext", *, tool: str | None, toolset: str | None,
        reason: str | None, message: str | None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def error(self, context: "AgentContext", *, message: str, code: str) -> list[dict[str, Any]]: ...


class LegacyFormatter(ProtocolFormatter):
    """Byte-identical to today's hand-built dict literals — the default
    for every request that doesn't explicitly negotiate `agui`."""

    def answer_delta(self, context, *, chunk, accumulated, citations, confidence=None):
        data: dict[str, Any] = {"chunk": chunk, "accumulated": accumulated, "citations": citations}
        if confidence is not None:
            data["confidence"] = confidence
        return [{"event": "answer_chunk", "data": data}]

    def answer_final(self, context, *, completion_data):
        return [{"event": "complete", "data": completion_data}]

    def ask_user_question(self, context, *, status, tool_data):
        return [{"event": "ask_user_question", "data": {"status": status, "toolData": tool_data}}]

    def artifact(self, context, *, artifact_data):
        return [{"event": "artifact", "data": artifact_data}]

    def tool_unavailable(self, context, *, tool, toolset, reason, message):
        return [{
            "event": "tool_unavailable",
            "data": {"tool": tool, "toolset": toolset, "reason": reason, "message": message},
        }]

    def error(self, context, *, message, code):
        return [{"event": "error", "data": {"message": message, "type": code}}]


class AGUIFormatter(ProtocolFormatter):
    """AG-UI-shaped equivalents.

    `answer_delta` rides on `STATE_DELTA`, NOT `TEXT_MESSAGE_CONTENT`: the
    raw per-token text already streams via `AGUIEventEmitter` reacting to
    the SAME underlying `TEXT_MESSAGE_CONTENT` `AgentEvent` `Agent.step()`
    emits. Re-emitting the normalized/citation-rewritten text on that
    channel would violate AG-UI's append-only `TEXT_MESSAGE_CONTENT`
    contract (citation refs mutate mid-stream, preamble turns reset to
    "") — see the migration plan's Phase 1e. `answer_final`'s
    `STATE_SNAPSHOT` is the authoritative correction the frontend swaps in,
    exactly mirroring how `complete` supersedes `answer_chunk` today.
    """

    def answer_delta(self, context, *, chunk, accumulated, citations, confidence=None):
        value: dict[str, Any] = {"citations": citations, "normalizedAnswer": accumulated}
        if confidence is not None:
            value["confidence"] = confidence
        return [frame(
            AGUIEventType.STATE_DELTA,
            runId=context.run_id,
            delta=[{"op": "replace", "path": f"/{key}", "value": val} for key, val in value.items()],
        )]

    def answer_final(self, context, *, completion_data):
        return [
            frame(AGUIEventType.STATE_SNAPSHOT, runId=context.run_id, snapshot={"final": True, **completion_data}),
            frame(
                AGUIEventType.RUN_FINISHED, runId=context.run_id, threadId=context.conversation_id,
                result=completion_data,
            ),
        ]

    def ask_user_question(self, context, *, status, tool_data):
        return [frame(
            AGUIEventType.CUSTOM, name="ask_user_question",
            value={"status": status, "toolData": tool_data}, runId=context.run_id,
        )]

    def artifact(self, context, *, artifact_data):
        return [frame(AGUIEventType.CUSTOM, name="artifact", value=artifact_data, runId=context.run_id)]

    def tool_unavailable(self, context, *, tool, toolset, reason, message):
        return [frame(
            AGUIEventType.CUSTOM, name="tool_unavailable",
            value={"tool": tool, "toolset": toolset, "reason": reason, "message": message},
            runId=context.run_id,
        )]

    def error(self, context, *, message, code):
        return [frame(AGUIEventType.RUN_ERROR, runId=context.run_id, message=message, code=code)]


__all__ = ["ProtocolFormatter", "LegacyFormatter", "AGUIFormatter"]
