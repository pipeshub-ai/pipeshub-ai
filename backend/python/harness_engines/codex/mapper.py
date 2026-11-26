"""``codex-app-server`` notifications and server requests → ``EngineEvent``s for one turn.

Built on transcripts recorded from 0.160.0 (``tests/unit/harness_engines/codex/fixtures``).
Shapes relied on:

- a turn runs from the ``turn/start`` response (its ``turn.id``) to
  ``turn/completed`` of that turn on the run's thread, ``turn.status`` one of
  ``completed``, ``interrupted``, ``failed``;
- items go ``item/started`` → deltas → ``item/completed``; agent text arrives as
  ``item/agentMessage/delta`` and whole on ``item/completed``;
- ``thread/tokenUsage/updated`` reports each model call's tokens in ``last``,
  emitted after the call's tool calls have finished (``turn.rs``: token counts
  are sent once in-flight tools drain), so its position among a turn's events
  is deterministic;
- a sub-agent is a thread of its own: ``spawnAgent`` names it in
  ``receiverThreadIds`` and its events share the connection; its usage, its end
  and its steps (commands, tool calls and file changes, named as its own) are mapped. The parent's turn may complete before a child's does
  (observed both ways on 0.160), so a completed turn is reported once every
  child it spawned has finished: the task includes its helpers' work;
- an image Codex looked at (``view_image``) is an ``imageView`` item with its ``path``, started and completed once the
  file was read; a call the policy hook refused has no item;
- Codex's own asks are server requests carrying ``itemId`` and ``turnId``;
- Codex's own compaction mid-turn is a ``contextCompaction`` item and/or the
  older ``thread/compacted`` notification: one compaction, whichever arrives;
- a method in neither the handled nor the ``KNOWN_QUIET`` set is a newer
  Codex's: ``NativeUnknown`` (EV-13), never a failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from harness_core.engines.events import (
    ApprovalRequested,
    CommandFinished,
    CommandOutput,
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
from harness_core.engines.jsonview import as_int, as_list, as_object, as_str
from harness_core.engines.native import recorded, unknown_event
from harness_core.engines.stream import TurnSeq
from harness_core.gateway.disclosure import shown_tool
from harness_engines.codex.launch import IMAGE_TOOL
from harness_engines.plans import plan_updated

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.engines.events import EngineEvent, TurnEndReason
    from harness_core.engines.native import NativeTape
    from harness_core.engines.types import JsonValue
    from harness_core.interactions.ports import InteractionDecision

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")
# The model gateway is the only provider Codex reaches, and it answers 402 only when spending must stop
# (P6.5 ledger: budget_exhausted or budget_insufficient; E3: the org's provider quota). Codex exposes the
# status, not the gateway's code: it reads an error body only for 400 and 429 (codex-api api_bridge.rs),
# and the X-Gateway-Refusal header never reaches the client. Both ledger codes stop the controller alike.
_GATEWAY_STATUS: Mapping[int, str] = {402: "budget_exhausted"}
# The gateway's own code, which its Responses wire puts in a refusal's message (the part of the body Codex keeps).
_GATEWAY_CODE = re.compile(r"\[gateway:([a-z][a-z0-9_]{0,63})\]")
_FILE_CHANGE: Mapping[str, Literal["added", "modified", "deleted"]] = {
    "add": "added",
    "update": "modified",
    "delete": "deleted",
}


@dataclass(frozen=True, slots=True)
class Ask:
    """What Codex asked and how to answer it."""

    permission: str
    approve: Callable[[Mapping[str, JsonValue]], JsonValue]
    deny: Callable[[Mapping[str, JsonValue]], JsonValue]


def _decision(value: str) -> Callable[[Mapping[str, JsonValue]], JsonValue]:
    return lambda _params: {"decision": value}


ASKS: Mapping[str, Ask] = {
    "item/commandExecution/requestApproval": Ask("command", _decision("accept"), _decision("decline")),
    "item/fileChange/requestApproval": Ask("file_change", _decision("accept"), _decision("decline")),
    "item/permissions/requestApproval": Ask(
        "permissions",
        lambda params: {"permissions": params.get("permissions") or {}, "scope": "turn"},
        lambda _params: {"permissions": {}},
    ),
    "mcpServer/elicitation/request": Ask(
        "elicitation", lambda _params: {"action": "accept"}, lambda _params: {"action": "decline"}
    ),
    "item/tool/requestUserInput": Ask("user_input", lambda _params: {"answers": {}}, lambda _params: {"answers": {}}),
    "execCommandApproval": Ask("command", _decision("approved"), _decision("denied")),
    "applyPatchApproval": Ask("file_change", _decision("approved"), _decision("denied")),
}


# Methods Codex sends that a turn has no use for (status, account, startup and per-item progress notices), as
# recorded from 0.160 and named by its app-server protocol: neither mapped nor unknown.
KNOWN_QUIET: frozenset[str] = frozenset(
    {
        "account/login/completed",
        "account/rateLimits/updated",
        "account/updated",
        # Server requests the session answers "not served" (a sub-agent's dynamic tool, an auth refresh).
        "account/chatgptAuthTokens/refresh",
        "attestation/generate",
        "item/tool/call",
        "authStatusChange",
        "configWarning",
        "deprecationNotice",
        # WP65-NATIVE (about 44 a turn, otherwise logged as unknown). The run's own managed policy hook (G-25) runs
        # before each of the engine's tool calls; the gateway answers it and logs a refusal with its reason.
        "hook/started",
        "hook/completed",
        # Codex wrote to a running command's stdin: possibly a secret typed at a prompt that does not echo, so
        # never shown; what it changed shows in the command's own output, which is streamed.
        "item/commandExecution/terminalInteraction",
        "item/fileChange/outputDelta",
        "item/mcpToolCall/progress",
        "item/reasoning/summaryPartAdded",
        "item/updated",
        "loginChatGptComplete",
        "mcpServer/oauthLogin/completed",
        "mcpServer/startupStatus/updated",
        "remoteControl/status/changed",
        "serverRequest/resolved",
        "sessionConfigured",
        # The skills Codex found changed: the run's skills are mounted before the turn and fixed for it.
        "skills/changed",
        "thread/archived",
        "thread/closed",
        "thread/name/updated",
        "thread/started",
        "thread/status/changed",
        "thread/unarchived",
        "turn/diff/updated",
        "turn/started",
        "warning",
    }
)


def answer_for(method: str, params: Mapping[str, JsonValue], decision: InteractionDecision) -> JsonValue:
    """The reply to a Codex ask; anything but an approval answers no."""
    ask = ASKS[method]
    return ask.approve(params) if decision == "approved" else ask.deny(params)


def error_code(error: Mapping[str, JsonValue]) -> str:
    """A short identifier for a Codex turn error (``codexErrorInfo``), never its message.

    A string variant becomes snake case (``usageLimitExceeded`` → ``usage_limit_exceeded``); an
    object variant carrying an upstream HTTP status becomes ``http_<status>``, except the model
    gateway's spend stop. A refusal whose message names the gateway's code (``[gateway:<code>]``) is that code."""
    named = _GATEWAY_CODE.search(as_str(error.get("message")))
    if named is not None:
        return named.group(1)
    info = error.get("codexErrorInfo")
    if isinstance(info, str):
        code = _CAMEL.sub("_", info).lower()
        return code if _CODE.match(code) else "engine_error"
    for name, detail in as_object(info).items():
        status = as_int(as_object(detail).get("httpStatusCode"))
        if status:
            return _GATEWAY_STATUS.get(status, f"http_{status}")
        code = _CAMEL.sub("_", name).lower()
        return code if _CODE.match(code) else "engine_error"
    return "engine_error"


class CodexEventMapper:
    """Stateful, deterministic mapping for one turn of one thread; ``done`` once ``TurnCompleted`` is out."""

    def __init__(self, *, thread_id: str, turn_id: str, children: set[str] | None = None) -> None:
        self.thread_id = thread_id
        self._seq = TurnSeq(turn_id)
        self.codex_turn_id: str | None = None
        self.done = False
        self._interrupted = False
        self._errored = False
        # Sub-agent threads, kept across the session's turns: a child may outlive the turn that spawned it.
        self.children: set[str] = children if children is not None else set()
        self._spawned_by: dict[str, str] = {}
        self._finished: set[str] = set()
        self._held_end: TurnEndReason | None = None
        self._final_text = ""
        self._last_input: int | None = None
        # Commands whose output streamed: one whose output never did shows its aggregated output when it ends.
        self._streamed: set[str] = set()
        # How many compactions each of Codex's two signals reported: a pair is one compaction.
        self._compactions = {"item": 0, "notice": 0}
        # The turn's native transcript (C2.39), set by the session; its unknown messages (C2.40).
        self.tape: NativeTape | None = None

    @recorded
    def begin(self, codex_turn_id: str) -> None:
        self.codex_turn_id = codex_turn_id

    @recorded
    def mark_interrupted(self) -> None:
        self._interrupted = True

    @recorded
    def feed(self, message: Mapping[str, JsonValue]) -> list[EngineEvent]:
        if self.done:
            return []
        method = as_str(message.get("method"))
        params = as_object(message.get("params"))
        if method and method not in _HANDLED and method not in ASKS and method not in KNOWN_QUIET:
            return [unknown_event(self._seq, method, message)]
        thread = as_str(params.get("threadId"))
        if thread in self.children:
            return self._child(method, params, thread)
        if thread != self.thread_id or not self._this_turn(params):
            return []
        if "id" in message and method in ASKS:
            return [self._ask(method, message.get("id"), params)]
        handler = {
            "item/started": self._item_started,
            "item/completed": self._item_completed,
            "item/agentMessage/delta": self._text_delta,
            "item/reasoning/summaryTextDelta": self._reasoning_delta,
            "item/reasoning/textDelta": self._reasoning_delta,
            "item/commandExecution/outputDelta": self._command_output,
            "turn/plan/updated": self._plan,
            "thread/tokenUsage/updated": self._usage,
            "thread/compacted": lambda _: self._compacted("notice"),
            "error": self._error,
            "turn/completed": self._turn_completed,
        }.get(method)
        return handler(params) if handler is not None else []

    def _this_turn(self, params: Mapping[str, JsonValue]) -> bool:
        turn = as_str(params.get("turnId")) or as_str(as_object(params.get("turn")).get("id"))
        return not turn or turn == self.codex_turn_id

    def _ask(self, method: str, request_id: JsonValue, params: Mapping[str, JsonValue]) -> EngineEvent:
        command = as_str(params.get("command"))
        return ApprovalRequested(
            self._seq.next(),
            request_id=str(request_id),
            permission=ASKS[method].permission,
            patterns=(command,) if command else (),
            call_id=as_str(params.get("itemId")) or None,
        )

    def _item_started(self, params: Mapping[str, JsonValue]) -> list[EngineEvent]:
        item = as_object(params.get("item"))
        kind, call_id = as_str(item.get("type")), as_str(item.get("id"))
        if kind == "commandExecution":
            return [CommandStarted(self._seq.next(), call_id, as_str(item.get("command")))]
        if kind in ("mcpToolCall", "dynamicToolCall"):
            arguments = as_object(item.get("arguments"))
            tool = shown_tool(as_str(item.get("tool")), arguments)
            return [ToolCallSeen(self._seq.next(), call_id, tool, "started", arguments=arguments)]
        if kind == "imageView":
            return [self._image_view(call_id, item, "started")]
        return []

    def _image_view(self, call_id: str, item: Mapping[str, JsonValue], status: Literal["started", "completed"]) -> EngineEvent:
        return ToolCallSeen(self._seq.next(), call_id, IMAGE_TOOL, status, arguments={"path": as_str(item.get("path"))})

    def _item_completed(self, params: Mapping[str, JsonValue]) -> list[EngineEvent]:
        item = as_object(params.get("item"))
        kind, call_id = as_str(item.get("type")), as_str(item.get("id"))
        status = as_str(item.get("status"))
        if kind == "agentMessage":
            text = as_str(item.get("text"))
            self._final_text = text
            return [MessageCompleted(self._seq.next(), call_id, text)]
        if kind == "commandExecution":
            exit_code = item.get("exitCode")
            code = exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None
            ok = status == "completed" and code == 0
            ended: list[EngineEvent] = []
            output = as_str(item.get("aggregatedOutput"))
            if call_id not in self._streamed:
                ended.extend(command_output(self._seq.next, call_id, output))
            self._streamed.discard(call_id)
            ended.append(CommandFinished(self._seq.next(), call_id, code, "completed" if ok else "failed"))
            return ended
        if kind in ("mcpToolCall", "dynamicToolCall"):
            done = "completed" if status == "completed" else "failed"
            arguments = as_object(item.get("arguments"))
            tool = shown_tool(as_str(item.get("tool")), arguments)
            return [ToolCallSeen(self._seq.next(), call_id, tool, done, arguments=arguments)]
        if kind == "fileChange":
            return [
                FileChanged(
                    self._seq.next(),
                    call_id,
                    as_str(change.get("path")),
                    _FILE_CHANGE.get(as_str(as_object(change.get("kind")).get("type")), "modified"),
                )
                for change in (as_object(c) for c in as_list(item.get("changes")))
            ]
        if kind == "imageView":
            return [self._image_view(call_id, item, "completed")]
        if kind == "contextCompaction":
            return self._compacted("item")
        if kind == "collabAgentToolCall" and as_str(item.get("tool")) == "spawnAgent":
            events: list[EngineEvent] = []
            for child in (as_str(t) for t in as_list(item.get("receiverThreadIds"))):
                if child and child not in self.children:
                    self.children.add(child)
                    self._spawned_by[child] = call_id
                    events.append(SubAgentStarted(self._seq.next(), child, call_id, "spawn_agent", as_str(item.get("prompt"))))
            return events
        return []

    def _text_delta(self, params: Mapping[str, JsonValue]) -> list[EngineEvent]:
        delta = as_str(params.get("delta"))
        return [TextDelta(self._seq.next(), as_str(params.get("itemId")), delta)] if delta else []

    def _reasoning_delta(self, params: Mapping[str, JsonValue]) -> list[EngineEvent]:
        delta = as_str(params.get("delta"))
        return [ReasoningDelta(self._seq.next(), as_str(params.get("itemId")), delta)] if delta else []

    def _command_output(self, params: Mapping[str, JsonValue]) -> list[EngineEvent]:
        delta = as_str(params.get("delta"))
        if not delta:
            return []
        call_id = as_str(params.get("itemId"))
        self._streamed.add(call_id)
        return [CommandOutput(self._seq.next(), call_id, delta)]

    def _plan(self, params: Mapping[str, JsonValue]) -> list[EngineEvent]:
        plan = plan_updated(self._seq.next(), params.get("plan"), ("step",))
        return [plan] if plan is not None else []

    def _compacted(self, signal: Literal["item", "notice"]) -> list[EngineEvent]:
        other = "notice" if signal == "item" else "item"
        self._compactions[signal] += 1
        if self._compactions[signal] <= self._compactions[other]:
            return []
        return [ContextCompacted(self._seq.next(), self._last_input, None, user_messages_kept=True)]

    def _usage(self, params: Mapping[str, JsonValue], sub_session_id: str | None = None) -> list[EngineEvent]:
        last = as_object(as_object(params.get("tokenUsage")).get("last"))
        if sub_session_id is None:
            self._last_input = as_int(last.get("inputTokens")) or None
        return [
            UsageReported(
                self._seq.next(),
                input_tokens=as_int(last.get("inputTokens")),
                output_tokens=as_int(last.get("outputTokens")),
                reasoning_tokens=as_int(last.get("reasoningOutputTokens")),
                cache_read_tokens=as_int(last.get("cachedInputTokens")),
                cache_write_tokens=as_int(last.get("cacheWriteInputTokens")),
                sub_session_id=sub_session_id,
            )
        ]

    def _error(self, params: Mapping[str, JsonValue]) -> list[EngineEvent]:
        if params.get("willRetry") is True:
            return []
        self._errored = True
        code = error_code(as_object(params.get("error")))
        return [EngineError(self._seq.next(), code, "the engine reported a turn error")]

    def _turn_completed(self, params: Mapping[str, JsonValue]) -> list[EngineEvent]:
        turn = as_object(params.get("turn"))
        status = as_str(turn.get("status"))
        events: list[EngineEvent] = []
        reason: TurnEndReason
        if status == "interrupted" or (self._interrupted and status != "completed"):
            reason = "interrupted"
        elif status == "completed":
            reason = "completed"
        else:
            reason = "failed"
            if not self._errored:
                code = error_code(as_object(turn.get("error")))
                events.append(EngineError(self._seq.next(), code, "the engine reported a turn error"))
        if reason == "completed" and set(self._spawned_by) - self._finished:
            self._held_end = reason
            return events
        events.append(TurnCompleted(self._seq.next(), reason, self._final_text))
        self.done = True
        return events

    def _child(self, method: str, params: Mapping[str, JsonValue], child: str) -> list[EngineEvent]:
        if method == "thread/tokenUsage/updated":
            return self._usage(params, sub_session_id=child)
        if method in ("item/started", "item/completed") and as_str(as_object(params.get("item")).get("type")) in _STEPS:
            step = self._item_started if method == "item/started" else self._item_completed
            return taken_by(child, step(params))
        if method == "item/commandExecution/outputDelta":
            return taken_by(child, self._command_output(params))
        if method == "turn/completed" and child not in self._finished and child in self._spawned_by:
            self._finished.add(child)
            ok = as_str(as_object(params.get("turn")).get("status")) == "completed"
            events: list[EngineEvent] = [
                SubAgentFinished(self._seq.next(), child, self._spawned_by[child], "completed" if ok else "failed")
            ]
            if self._held_end is not None and not set(self._spawned_by) - self._finished:
                events.append(TurnCompleted(self._seq.next(), self._held_end, self._final_text))
                self.done = True
            return events
        return []


# A sub-agent thread's items that are its steps (EV-07); its messages, plan and compactions stay its own.
_STEPS = frozenset({"commandExecution", "mcpToolCall", "dynamicToolCall", "fileChange", "imageView"})

_HANDLED = frozenset(
    {
        "item/started",
        "item/completed",
        "item/agentMessage/delta",
        "item/reasoning/summaryTextDelta",
        "item/reasoning/textDelta",
        "item/commandExecution/outputDelta",
        "turn/plan/updated",
        "thread/tokenUsage/updated",
        "thread/compacted",
        "error",
        "turn/completed",
    }
)


__all__ = ["ASKS", "KNOWN_QUIET", "Ask", "CodexEventMapper", "answer_for", "error_code"]
