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

from pydantic import BaseModel, ConfigDict

from app.agents.agent_loop.protocol.agui import AGUIEventType, frame
from app.agents.agent_loop.protocol.retrieval_context import (
    RETRIEVAL_CONTEXT_EVENT_NAME,
)
from app.agents.agent_loop.protocol.run_usage import RUN_USAGE_EVENT_NAME
from app.modules.retrieval import degraded_notice

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.protocol.retrieval_context import RetrievalContextPayload
    from app.agents.agent_loop.protocol.run_usage import RunUsagePayload


class ArtifactSSEPayload(BaseModel):
    """Wire payload for a live "artifact produced" event — shared by every
    producer (today: `sandbox_bridge._emit_artifact_event`; future
    producers should build one of these instead of hand-rolling a dict)
    and by both `ProtocolFormatter.artifact()` implementations, replacing
    the previously untyped `dict[str, Any]`.

    Field names are camelCase to match the existing wire contract exactly
    (`frontend/.../agui-event-handler.ts`'s `SSEArtifactEvent`,
    `streaming.ts`'s `onArtifact`) — this is a one-way producer-side
    payload with no incoming parse step that would need snake_case +
    alias support."""

    model_config = ConfigDict(extra="forbid")

    artifactId: str
    fileName: str
    mimeType: str
    sizeBytes: int
    downloadUrl: str
    artifactType: str
    isTemporary: bool = False
    recordId: str | None = None
    version: int | None = None
    derivedFromCodeArtifactId: str | None = None
    # Defensive signal for the frontend — Python already gates emission so
    # STAGING artifacts never reach this payload in production, but the
    # field allows a frontend guard to catch any accidental bypass.
    visibility: str = "VISIBLE"

    def to_wire_dict(self) -> dict[str, Any]:
        """`exclude_none` mirrors the previous hand-built dict's behavior
        of omitting optional keys (e.g. `derivedFromCodeArtifactId`)
        entirely when unset, rather than sending them as JSON `null`."""
        return self.model_dump(exclude_none=True)


class ProtocolFormatter(ABC):
    @abstractmethod
    def answer_delta(
        self, context: "AgentContext", *, chunk: str, accumulated: str,
        citations: list[Any], confidence: str | None = None,
        raw_length: int = 0, discarded: bool = False,
    ) -> list[dict[str, Any]]:
        """`discarded`: the answer as it stands after a model call that
        failed mid-stream was taken back; it supersedes every earlier delta."""

    @abstractmethod
    def answer_final(self, context: "AgentContext", *, completion_data: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def ask_user_question(self, context: "AgentContext", *, status: str, tool_data: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    def artifact(self, context: "AgentContext", *, artifact_data: ArtifactSSEPayload) -> list[dict[str, Any]]: ...

    @abstractmethod
    def tool_unavailable(
        self, context: "AgentContext", *, tool: str | None, toolset: str | None,
        reason: str | None, message: str | None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def retrieval_context(
        self, context: "AgentContext", *, payload: "RetrievalContextPayload",
    ) -> list[dict[str, Any]]:
        """Which records and blocks reached the model. Opt-in; identifiers
        only, never block text."""

    @abstractmethod
    def run_usage(
        self, context: "AgentContext", *, payload: "RunUsagePayload",
    ) -> list[dict[str, Any]]:
        """Token and loop-outcome totals for the whole run."""

    @abstractmethod
    def error(self, context: "AgentContext", *, message: str, code: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def tool_progress(
        self, context: "AgentContext", *, tool: str, progress: float, total: float | None, message: str | None,
    ) -> list[dict[str, Any]]: ...

    def provider_wait(self, context: "AgentContext", *, retry_at: str, message: str) -> list[dict[str, Any]]:
        """The model provider is limiting requests and the call is retried at ``retry_at`` (ERR-UX2)."""
        ...

    def context_compacted(
        self, context: "AgentContext", *, before_tokens: int, after_tokens: int, message: str,
    ) -> list[dict[str, Any]]:
        """The request's history was reduced to fit the window (QAFIX4: the person is told, as on the durable loop)."""
        return [{
            "event": "status",
            "data": {
                "status": CONTEXT_COMPACTED_STATUS,
                "message": message,
                "beforeTokens": before_tokens,
                "afterTokens": after_tokens,
            },
        }]


# The stream status while a rate-limited provider's wait runs, as Node projects it for a durable run (ERR-UX2).
PROVIDER_WAIT_STATUS = "waiting_provider"
CONTEXT_COMPACTED_STATUS = "context_compacted"


def compacted_view(before_tokens: int, after_tokens: int) -> dict[str, Any]:
    """Today's loop's reduction as the durable timeline's `context_compacted` view (Node's `ContextCompactedView`):
    the live frame and the saved part carry the same one. L4 folds older user messages into notes, so not all are
    kept."""
    return {
        "executor": "loop",
        "outcome": "compacted",
        "beforeTokens": before_tokens,
        "afterTokens": after_tokens,
        "userMessagesKept": False,
    }


class LegacyFormatter(ProtocolFormatter):
    """Byte-identical to today's hand-built dict literals — the default
    for every request that doesn't explicitly negotiate `agui`."""

    def answer_delta(self, context, *, chunk, accumulated, citations, confidence=None, raw_length=0, discarded: bool = False):
        data: dict[str, Any] = {"chunk": chunk, "accumulated": accumulated, "citations": citations}
        if confidence is not None:
            data["confidence"] = confidence
        return [{"event": "answer_chunk", "data": data}]

    def answer_final(self, context, *, completion_data):
        return [{"event": "complete", "data": degraded_notice.flag(completion_data)}]

    def ask_user_question(self, context, *, status, tool_data):
        return [{"event": "ask_user_question", "data": {"status": status, "toolData": tool_data}}]

    def artifact(self, context, *, artifact_data):
        return [{"event": "artifact", "data": artifact_data.to_wire_dict()}]

    def tool_unavailable(self, context, *, tool, toolset, reason, message):
        return [{
            "event": "tool_unavailable",
            "data": {"tool": tool, "toolset": toolset, "reason": reason, "message": message},
        }]

    def retrieval_context(
        self, context: "AgentContext", *, payload: "RetrievalContextPayload",
    ) -> list[dict[str, Any]]:
        return [{"event": RETRIEVAL_CONTEXT_EVENT_NAME, "data": payload.to_wire_dict()}]

    def run_usage(
        self, context: "AgentContext", *, payload: "RunUsagePayload",
    ) -> list[dict[str, Any]]:
        return [{"event": RUN_USAGE_EVENT_NAME, "data": payload.to_wire_dict()}]

    def error(self, context, *, message, code):
        return [{"event": "error", "data": {"message": message, "type": code}}]

    def provider_wait(self, context: "AgentContext", *, retry_at: str, message: str) -> list[dict[str, Any]]:
        return [{"event": "status", "data": {"status": "retrying", "message": message}}]
    def tool_progress(
        self, context: "AgentContext", *, tool: str, progress: float, total: float | None, message: str | None,
    ) -> list[dict[str, Any]]:
        return [{
            "event": "status",
            "data": {"status": "running_tool", "tool": tool, "progress": progress, "total": total, "message": message},
        }]


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

    def answer_delta(self, context, *, chunk, accumulated, citations, confidence=None, raw_length=0, discarded: bool = False):
        value: dict[str, Any] = {"citations": citations, "normalizedAnswer": accumulated, "rawLength": raw_length}
        if confidence is not None:
            value["confidence"] = confidence
        if discarded:
            # Snapshots computed before the discard can reach the client after
            # its `message_discarded` frame; this marks the one that ends them.
            value["discarded"] = True
        return [frame(
            AGUIEventType.STATE_DELTA,
            runId=context.run_id,
            delta=[{"op": "replace", "path": f"/{key}", "value": val} for key, val in value.items()],
        )]

    def answer_final(self, context, *, completion_data):
        degraded_notice.flag(completion_data)
        return [
            # The browser swaps the snapshot in; the answer's label is Node's to record, never shown (FG2).
            frame(
                AGUIEventType.STATE_SNAPSHOT,
                runId=context.run_id,
                snapshot={"final": True, **{k: v for k, v in completion_data.items() if k != "label"}},
            ),
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
        """`STATE_DELTA` with an `add` op on `/artifacts/-` rather than
        `CUSTOM`: a well-known JSON Patch path lets stock AG-UI clients
        accumulate artifact state without knowing about a PipesHub-specific
        `CUSTOM` event name. `QueueEventSink._coalesce_key` special-cases
        `add` ops so this is never merged into/dropped by an adjacent
        answer-delta `STATE_DELTA` snapshot — see that module."""
        return [frame(
            AGUIEventType.STATE_DELTA,
            runId=context.run_id,
            delta=[{"op": "add", "path": "/artifacts/-", "value": artifact_data.to_wire_dict()}],
        )]

    def tool_unavailable(self, context, *, tool, toolset, reason, message):
        return [frame(
            AGUIEventType.CUSTOM, name="tool_unavailable",
            value={"tool": tool, "toolset": toolset, "reason": reason, "message": message},
            runId=context.run_id,
        )]

    def retrieval_context(
        self, context: "AgentContext", *, payload: "RetrievalContextPayload",
    ) -> list[dict[str, Any]]:
        return [frame(
            AGUIEventType.CUSTOM, name=RETRIEVAL_CONTEXT_EVENT_NAME,
            value=payload.to_wire_dict(), runId=context.run_id,
        )]

    def run_usage(
        self, context: "AgentContext", *, payload: "RunUsagePayload",
    ) -> list[dict[str, Any]]:
        return [frame(
            AGUIEventType.CUSTOM, name=RUN_USAGE_EVENT_NAME,
            value=payload.to_wire_dict(), runId=context.run_id,
        )]

    def error(self, context, *, message, code):
        return [frame(AGUIEventType.RUN_ERROR, runId=context.run_id, message=message, code=code)]

    def context_compacted(
        self, context: "AgentContext", *, before_tokens: int, after_tokens: int, message: str,
    ) -> list[dict[str, Any]]:
        # The frame Node projects from a durable run's `x.pipeshub.context.compacted`, so the chat's existing row
        # renders it in the reader's language. L4 folds older user messages into notes: they are not all kept.
        return [frame(
            AGUIEventType.CUSTOM, name=CONTEXT_COMPACTED_STATUS, runId=context.run_id,
            value={**compacted_view(before_tokens, after_tokens), "message": message},
        )]

    def provider_wait(self, context: "AgentContext", *, retry_at: str, message: str) -> list[dict[str, Any]]:
        # The durable projection's frame for `run.yielded{llm_backoff}` (contracts/harness/vectors/provider-wait.json):
        # the web app words the wait from `retry_at`, never from server text.
        return [frame(
            AGUIEventType.STATE_SNAPSHOT,
            runId=context.run_id,
            snapshot={"status": PROVIDER_WAIT_STATUS, "retry_at": retry_at},
        )]
    def tool_progress(
        self, context: "AgentContext", *, tool: str, progress: float, total: float | None, message: str | None,
    ) -> list[dict[str, Any]]:
        """A `running_tool` snapshot like `write_state`'s, with the server's progress on it."""
        snapshot: dict[str, Any] = {"status": "running_tool", "current_tool": tool, "progress": progress}
        if total is not None:
            snapshot["total"] = total
        if message:
            snapshot["progress_message"] = message
        return [frame(AGUIEventType.STATE_SNAPSHOT, runId=context.run_id, snapshot=snapshot)]


# Both formatters are stateless (no per-instance state, no `__init__`
# args) — one instance each, shared across every request, instead of
# `AgentContext.formatter` allocating a new one on every single access
# (it's read multiple times per streamed chunk).
LEGACY_FORMATTER = LegacyFormatter()
AGUI_FORMATTER = AGUIFormatter()

__all__ = [
    "PROVIDER_WAIT_STATUS",
    "ArtifactSSEPayload",
    "ProtocolFormatter",
    "LegacyFormatter",
    "AGUIFormatter",
    "LEGACY_FORMATTER",
    "AGUI_FORMATTER",
]
