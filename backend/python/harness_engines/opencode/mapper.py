"""opencode ``/event`` SSE payloads → ``EngineEvent``s for one turn of one session.

Built and tested on the probe captures (``research/engines/opencode-probes``).
Shapes relied on (v1.18.34):

- a turn of the root session runs from ``session.status{busy}`` to the first
  ``session.status{idle}`` or ``session.idle`` after it; anything before
  ``busy`` is a previous turn's tail;
- assistant text arrives as ``message.part.delta{field: text}`` and as the
  part's full text on ``message.part.updated``; only the unseen suffix is emitted;
- ``message.updated`` for a completed assistant message arrives twice, and the first can precede
  the message's text part (seen on the real binary under load: CF-6);
- tool parts go ``pending → running → completed|error``; ``running`` may repeat;
- a ``task`` tool part carries the child session id in ``state.metadata.sessionId``;
  the child's events share the stream; its usage, approvals and tool steps (named as its own) are mapped;
- ``step-finish`` parts carry the step's tokens and cost;
- a completed ``write`` says in ``state.metadata.exists`` whether the file was there; ``apply_patch`` has no
  ``filePath`` and lists each file in ``state.metadata.files`` with its ``type`` (add, update, move, delete);
- opencode's own compaction mid-turn leaves an assistant message marked
  ``summary`` (its ``tokens``: input the session as it was, output the summary),
  never the turn's answer, and then ``session.compacted``;
- an event ``type`` in neither the mapped nor the ``KNOWN_QUIET`` set is a
  newer opencode's: ``NativeUnknown`` (EV-13), never a failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from harness_core.engines.events import (
    ApprovalRequested,
    CommandFinished,
    CommandStarted,
    ContextCompacted,
    EngineError,
    FileChanged,
    MessageCompleted,
    ReasoningDelta,
    SubAgentFinished,
    SubAgentStarted,
    TextDelta,
    ToolCallSeen,
    TurnCompleted,
    UsageReported,
    command_output,
    taken_by,
)
from harness_core.engines.jsonview import as_float, as_int, as_list, as_object, as_str
from harness_core.engines.native import recorded, unknown_event
from harness_core.engines.stream import TurnSeq
from harness_core.gateway.disclosure import shown_tool
from harness_engines.file_changes import created, listed_changes, workspace_relative
from harness_engines.opencode.launch import MCP_SERVER, PLAN_TOOL, WORKDIR
from harness_engines.plans import plan_updated

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.events import EngineEvent, TurnEndReason
    from harness_core.engines.native import NativeTape
    from harness_core.engines.types import JsonValue

_EDIT_TOOLS = frozenset({"edit", "write", "apply_patch"})


def _relative(path: str) -> str:
    return workspace_relative(path, WORKDIR)


@dataclass
class _Message:
    role: str
    parts: dict[str, str] = field(default_factory=dict[str, str])
    completed: bool = False
    emitted: bool = False
    # opencode's compaction summary: the session's new start, never the turn's answer.
    summary: bool = False


_MAPPED = frozenset(
    {
        "session.created",
        "session.status",
        "session.idle",
        "message.updated",
        "message.part.updated",
        "message.part.delta",
        "permission.asked",
        "session.error",
        "session.compacted",
    }
)
# Bus events opencode publishes that a turn has no use for (server, catalog, file-watch, LSP and housekeeping
# notices), as captured from 1.18.34 and named by its event bus: neither mapped nor unknown.
KNOWN_QUIET: frozenset[str] = frozenset(
    {
        "catalog.updated",
        "command.executed",
        "file.edited",
        "file.watcher.updated",
        "ide.installed",
        "installation.update-available",
        "installation.updated",
        "integration.updated",
        "lsp.client.diagnostics",
        "lsp.updated",
        "message.part.removed",
        "message.removed",
        "permission.replied",
        "plugin.added",
        "project.updated",
        "reference.updated",
        "server.connected",
        "server.heartbeat",
        "session.deleted",
        "session.diff",
        "session.updated",
        "storage.write",
        "todo.updated",
        "vcs.branch.updated",
    }
)


class OpencodeEventMapper:
    """Stateful, deterministic mapping for one turn; ``done`` once ``TurnCompleted`` is out."""

    def __init__(self, *, session_id: str, turn_id: str) -> None:
        self.session_id = session_id
        self._seq = TurnSeq(turn_id)
        self._started = False
        self.done = False
        self._interrupted = False
        self._failed = False
        # A context overflow opencode said it would recover from by compacting (MG-10): reported only if it did not.
        self._overflow = False
        self._children: set[str] = set()
        self._messages: dict[str, _Message] = {}
        self._part_kind: dict[str, str] = {}
        self._emitted: dict[str, int] = {}
        self._tool_state: dict[str, str] = {}
        self._sub_started: set[str] = set()
        self._final_text = ""
        self._summary_tokens: Mapping[str, JsonValue] = {}
        # The turn's native transcript (C2.39), set by the session.
        self.tape: NativeTape | None = None

    @recorded
    def mark_interrupted(self) -> None:
        self._interrupted = True

    @recorded
    def feed(self, payload: Mapping[str, JsonValue]) -> list[EngineEvent]:
        if self.done:
            return []
        kind = as_str(payload.get("type"))
        if kind not in _MAPPED and kind not in KNOWN_QUIET and not kind.startswith("tui."):
            return [unknown_event(self._seq, kind, payload)]
        props = as_object(payload.get("properties"))
        session = as_str(props.get("sessionID"))
        if kind == "session.created":
            info = as_object(props.get("info"))
            parent = as_str(info.get("parentID"))
            if parent and (parent == self.session_id or parent in self._children):
                self._children.add(as_str(info.get("id")))
            return []
        if session in self._children:
            return self._child(kind, props, session)
        if session != self.session_id:
            return []
        if kind == "session.status":
            status = as_str(as_object(props.get("status")).get("type"))
            if status == "busy":
                self._started = True
                return []
            if status == "idle" and self._started:
                return self._finish()
            return []
        if not self._started:
            return []
        handler = {
            "session.idle": lambda: self._finish(),
            "message.updated": lambda: self._message(as_object(props.get("info"))),
            "message.part.updated": lambda: self._part(as_object(props.get("part"))),
            "message.part.delta": lambda: self._delta(props),
            "permission.asked": lambda: self._permission(props),
            "session.error": lambda: self._error(props),
            "session.compacted": lambda: self._compacted(),
        }.get(kind)
        return handler() if handler is not None else []

    def _finish(self) -> list[EngineEvent]:
        reason: TurnEndReason = "completed"
        if self._interrupted:
            reason = "interrupted"
        elif self._failed or self._overflow:
            reason = "failed"
        # A completion opencode reported before the message's text part arrived is emitted now, not lost.
        pending = [self._emit(message_id, m) for message_id, m in self._messages.items() if self._unsent(m)]
        if self._overflow:
            pending.append(EngineError(self._seq.next(), CONTEXT_OVERFLOW, "the engine reported a session error"))
        self.done = True
        return [*pending, TurnCompleted(self._seq.next(), reason, self._final_text)]

    @staticmethod
    def _unsent(message: _Message) -> bool:
        return (
            message.role == "assistant"
            and message.completed
            and not message.emitted
            and not message.summary
            and bool(message.parts)
        )

    def _compacted(self) -> list[EngineEvent]:
        """The summary message's tokens: its input is the session as it was, its output what the session starts from."""
        tokens = self._summary_tokens
        self._overflow = False
        return [ContextCompacted(self._seq.next(), as_int(tokens.get("input")) or None, as_int(tokens.get("output")) or None)]

    def _emit(self, message_id: str, message: _Message) -> EngineEvent:
        message.emitted = True
        text = "".join(message.parts.values())
        self._final_text = text
        return MessageCompleted(self._seq.next(), message_id, text)

    def _message(self, info: Mapping[str, JsonValue]) -> list[EngineEvent]:
        message_id = as_str(info.get("id"))
        message = self._messages.setdefault(message_id, _Message(role=as_str(info.get("role"))))
        error = as_object(info.get("error"))
        if as_str(error.get("name")) == "MessageAbortedError":
            self._interrupted = True
        if as_object(info.get("time")).get("completed") is not None:
            message.completed = True
        if info.get("summary") is True:
            message.summary = True
            self._summary_tokens = as_object(info.get("tokens"))
        # The completed update arrives twice and can precede the text part: emit once, when there is text.
        return [self._emit(message_id, message)] if self._unsent(message) else []

    def _part(self, part: Mapping[str, JsonValue]) -> list[EngineEvent]:
        part_type = as_str(part.get("type"))
        part_id = as_str(part.get("id"))
        message = self._messages.get(as_str(part.get("messageID")))
        if message is not None and (message.role == "user" or message.summary):
            return []
        if part_type in ("text", "reasoning"):
            self._part_kind[part_id] = part_type
            text = as_str(part.get("text"))
            if part_type == "text" and message is not None:
                message.parts[part_id] = text
            return self._suffix(part_id, as_str(part.get("messageID")), text)
        if part_type == "tool":
            return self._tool(part)
        if part_type == "step-finish":
            return [self._usage(part, None)]
        return []

    def _suffix(self, part_id: str, message_id: str, text: str) -> list[EngineEvent]:
        seen = self._emitted.get(part_id, 0)
        if len(text) <= seen:
            return []
        self._emitted[part_id] = len(text)
        return [self._text_event(part_id, message_id, text[seen:])]

    def _text_event(self, part_id: str, message_id: str, text: str) -> EngineEvent:
        if self._part_kind.get(part_id) == "reasoning":
            return ReasoningDelta(self._seq.next(), message_id, text)
        return TextDelta(self._seq.next(), message_id, text)

    def _delta(self, props: Mapping[str, JsonValue]) -> list[EngineEvent]:
        if as_str(props.get("field")) != "text":
            return []
        part_id = as_str(props.get("partID"))
        delta = as_str(props.get("delta"))
        message = self._messages.get(as_str(props.get("messageID")))
        if not delta or (message is not None and message.summary):
            return []
        self._emitted[part_id] = self._emitted.get(part_id, 0) + len(delta)
        return [self._text_event(part_id, as_str(props.get("messageID")), delta)]

    def _tool(self, part: Mapping[str, JsonValue]) -> list[EngineEvent]:
        call_id = as_str(part.get("callID"))
        tool = as_str(part.get("tool"))
        state = as_object(part.get("state"))
        status = as_str(state.get("status"))
        previous = self._tool_state.get(call_id)
        if status == previous or status == "pending":
            return []
        self._tool_state[call_id] = status
        tool_input = as_object(state.get("input"))
        meta = as_object(state.get("metadata"))
        if tool == "bash":
            return self._bash(call_id, status, tool_input, state, meta)
        if tool == PLAN_TOOL:
            plan = plan_updated(self._seq.next(), tool_input.get("todos"), ("content",))
            return [plan] if plan is not None and status in ("running", "completed") else []
        if tool == "task":
            return self._task(call_id, status, tool_input, meta)
        gateway = f"{MCP_SERVER}_"
        native = not tool.startswith(gateway)
        # A gateway tool goes by the gateway's name, as on Claude Code and ACP: its approval, display name and result
        # are keyed by it.
        tool = shown_tool(tool, tool_input, prefix=gateway).removeprefix(gateway)
        title = as_str(state.get("title"))
        if status == "running":
            return [ToolCallSeen(self._seq.next(), call_id, tool, "started", arguments=tool_input, title=title)]
        if status not in ("completed", "error"):
            return []
        done: Literal["completed", "failed"] = "completed" if status == "completed" else "failed"
        output = as_str(state.get("output")) if done == "completed" else as_str(state.get("error"))
        events: list[EngineEvent] = [
            ToolCallSeen(
                self._seq.next(), call_id, tool, done, arguments=tool_input, title=title, result=output or None
            )
        ]
        if native and tool in _EDIT_TOOLS and done == "completed":
            listed = listed_changes(meta)
            if not listed:
                path = as_str(tool_input.get("filePath"))
                listed = {path: "added" if created(meta) else "modified"}
            events.extend(
                FileChanged(self._seq.next(), call_id, _relative(path), change) for path, change in listed.items()
            )
        return events

    def _bash(
        self,
        call_id: str,
        status: str,
        tool_input: Mapping[str, JsonValue],
        state: Mapping[str, JsonValue],
        meta: Mapping[str, JsonValue],
    ) -> list[EngineEvent]:
        if status == "running":
            return [CommandStarted(self._seq.next(), call_id, as_str(tool_input.get("command")))]
        if status == "completed":
            exit_code = meta.get("exit")
            code = exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else 0
            return [
                *command_output(self._seq.next, call_id, as_str(state.get("output"))),
                CommandFinished(self._seq.next(), call_id, code, "completed" if code == 0 else "failed"),
            ]
        if status == "error":
            return [CommandFinished(self._seq.next(), call_id, None, "failed")]
        return []

    def _task(
        self,
        call_id: str,
        status: str,
        tool_input: Mapping[str, JsonValue],
        meta: Mapping[str, JsonValue],
    ) -> list[EngineEvent]:
        child = as_str(meta.get("sessionId"))
        if status == "running":
            if child and call_id not in self._sub_started:
                self._sub_started.add(call_id)
                self._children.add(child)
                agent = as_str(tool_input.get("subagent_type"))
                task = as_str(tool_input.get("description"))
                return [SubAgentStarted(self._seq.next(), child, call_id, agent, task)]
            if not child:
                return [ToolCallSeen(self._seq.next(), call_id, "task", "started")]
            return []
        if status not in ("completed", "error"):
            return []
        done: Literal["completed", "failed"] = "completed" if status == "completed" else "failed"
        if call_id in self._sub_started:
            return [SubAgentFinished(self._seq.next(), child, call_id, done)]
        return [ToolCallSeen(self._seq.next(), call_id, "task", done)]

    def _usage(self, part: Mapping[str, JsonValue], sub_session: str | None) -> EngineEvent:
        tokens = as_object(part.get("tokens"))
        cache = as_object(tokens.get("cache"))
        return UsageReported(
            self._seq.next(),
            input_tokens=as_int(tokens.get("input")),
            output_tokens=as_int(tokens.get("output")),
            reasoning_tokens=as_int(tokens.get("reasoning")),
            cache_read_tokens=as_int(cache.get("read")),
            cache_write_tokens=as_int(cache.get("write")),
            cost_usd=as_float(part.get("cost")),
            sub_session_id=sub_session,
        )

    def _permission(self, props: Mapping[str, JsonValue]) -> list[EngineEvent]:
        tool = as_object(props.get("tool"))
        call_id = as_str(tool.get("callID"))
        return [
            ApprovalRequested(
                self._seq.next(),
                request_id=as_str(props.get("id")),
                permission=as_str(props.get("permission")),
                patterns=tuple(as_str(p) for p in as_list(props.get("patterns"))),
                call_id=call_id or None,
            )
        ]

    def _error(self, props: Mapping[str, JsonValue]) -> list[EngineEvent]:
        code = error_code(props)
        if code == CONTEXT_OVERFLOW:
            # opencode 1.18 compacts on a context overflow and goes on with the turn (``session.compacted``).
            self._overflow = True
            return []
        self._failed = True
        return [EngineError(self._seq.next(), code=code, message="the engine reported a session error")]

    def _child(self, kind: str, props: Mapping[str, JsonValue], session: str) -> list[EngineEvent]:
        if not self._started:
            return []
        if kind == "message.part.updated":
            part = as_object(props.get("part"))
            if as_str(part.get("type")) == "step-finish":
                return [self._usage(part, session)]
            # A helper's tool calls are its steps (EV-07); its task list and its own helpers (denied) are not the run's.
            if as_str(part.get("type")) == "tool" and as_str(part.get("tool")) not in (PLAN_TOOL, "task"):
                return taken_by(session, self._tool(part))
        if kind == "permission.asked":
            return self._permission(props)
        return []


def parse_event(data: str) -> Mapping[str, JsonValue] | None:
    """One SSE ``data`` payload; ``None`` for anything that is not a JSON object."""
    try:
        value: JsonValue = json.loads(data)
    except ValueError:
        return None
    return as_object(value) if isinstance(value, dict) else None



_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# The model gateway's code for a context overflow; opencode names the provider's own ``ContextOverflowError``.
CONTEXT_OVERFLOW = "context_length_exceeded"


def error_code(props: Mapping[str, JsonValue]) -> str:
    """A session error's code. For a gateway's HTTP refusal it is the gateway's reason code
    (``budget_exhausted`` from the model gateway, E3), read from OpenAI's error envelope in the
    response body; only a short identifier is taken, never the message."""
    error = as_object(props.get("error"))
    data = as_object(error.get("data"))
    status = as_int(data.get("statusCode"))
    if status:
        try:
            body: JsonValue = json.loads(as_str(data.get("responseBody")) or "null")
        except ValueError:
            body = None
        code = as_str(as_object(as_object(body).get("error")).get("code"))
        return code if _CODE.match(code) else f"http_{status}"
    name = as_str(error.get("name"))
    return CONTEXT_OVERFLOW if name == "ContextOverflowError" else name or "session_error"

__all__ = ["OpencodeEventMapper", "error_code", "parse_event"]
