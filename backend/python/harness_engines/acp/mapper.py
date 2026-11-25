"""ACP ``session/update`` notifications and the ``session/prompt`` answer → ``EngineEvent``s for one turn.

| ACP | Engine event |
|---|---|
| ``agent_message_chunk`` (text) | ``TextDelta``; the message closes as ``MessageCompleted`` at a tool call, a new ``messageId`` or the turn's end |
| ``agent_thought_chunk`` (text) | ``ReasoningDelta`` |
| ``tool_call`` / ``tool_call_update``, kind ``execute`` | ``CommandStarted``, then ``CommandOutput`` and ``CommandFinished`` |
| the same, the profile's plan tool or kind ``think`` | nothing (the plan arrives as ``plan``) |
| the same, any other tool | ``ToolCallSeen`` started, then completed or failed; input sent after the start a ``ToolCallUpdated``; a gateway tool named as the activity shows it |
| a completed edit, delete or move | ``FileChanged`` per diff, location or listed file under the workspace; ``added`` for a diff without ``oldText``, a write whose ``rawOutput.metadata.exists`` is false or a file listed as ``add`` |
| ``plan`` | ``PlanUpdated`` (the whole list) |
| ``compaction_update`` completed | ``ContextCompacted`` |
| ``usage_update`` | kept as the context size a compaction reports |
| ``user_message_chunk``, commands, modes, config, session info | nothing |
| any other kind, or a notification of a method ACP does not define | ``NativeUnknown`` (EV-13), never a failure |
| ``session/request_permission`` the policy does not allow | ``ApprovalRequested`` |
| ``PromptResponse`` | ``UsageReported`` (its ``usage``, else the profile's ``usagePath`` in it, else the sum of the turn's ``callUsagePath`` reports) and ``TurnCompleted`` |

``stopReason`` ``end_turn`` completes the turn, ``cancelled`` interrupts it, ``max_tokens`` and
``max_turn_requests`` end it at a limit and ``refusal`` fails it. A ``session/prompt`` error fails it with an
``EngineError`` whose code is the model gateway's when the agent passed it on (``[gateway:<code>]`` or a budget
code in its message), else ``acp_error``; the agent's message itself is never an event or a log field.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from harness_core.engines.events import (
    ApprovalRequested,
    CommandFinished,
    CommandStarted,
    ContextCompacted,
    EngineError,
    FileChanged,
    MessageCompleted,
    ReasoningDelta,
    TextDelta,
    ToolCallSeen,
    ToolCallUpdated,
    TurnCompleted,
    UsageReported,
    command_output,
)
from harness_core.engines.jsonview import as_int, as_list, as_object, as_str
from harness_core.engines.native import recorded, set_recorded, unknown_event
from harness_core.engines.policy import NativeTool
from harness_core.engines.stream import TurnSeq
from harness_core.gateway.disclosure import shown_tool
from harness_engines.acp.permissions import (
    named_at,
    named_object,
    native_kind,
    tool_name,
)
from harness_engines.acp.profiles import WORKDIR
from harness_engines.file_changes import created, listed_changes, workspace_relative
from harness_engines.plans import plan_updated

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.events import EngineEvent, TurnEndReason
    from harness_core.engines.native import NativeTape
    from harness_core.engines.types import JsonValue
    from harness_engines.acp.permissions import SessionPolicy
    from harness_engines.file_changes import FileChange

_GATEWAY_CODE = re.compile(r"\[gateway:([a-z][a-z0-9_]{0,63})\]")
_BUDGET = re.compile(r"\b(budget_exhausted|budget_insufficient)\b")
_STOP: Mapping[str, TurnEndReason] = {
    "end_turn": "completed",
    "cancelled": "interrupted",
    "max_tokens": "limit",
    "max_turn_requests": "limit",
    "refusal": "failed",
}
_WRITES = frozenset({"edit", "delete", "move"})
_MAPPED = frozenset(
    {
        "agent_message_chunk",
        "agent_thought_chunk",
        "tool_call",
        "tool_call_update",
        "plan",
        "usage_update",
        "compaction_update",
    }
)
# Updates ACP defines that a turn has no use for: neither mapped nor unknown.
KNOWN_QUIET: frozenset[str] = frozenset(
    {
        "available_commands_update",
        "config_option_update",
        "current_mode_update",
        "current_model_update",
        "session_info_update",
        "user_message_chunk",
    }
)
# The longest command line an activity row carries.
MAX_COMMAND_CHARS = 4096


def error_code(message: str, data: JsonValue, reasons: Mapping[str, str] | None = None) -> str:
    """The model gateway's code when the agent passed it on in an error; else the code the profile gives the agent's
    own ``data.reason``; else ``acp_error``."""
    text = message + " " + (json.dumps(data) if data is not None else "")
    named = _GATEWAY_CODE.search(text)
    if named is not None:
        return named.group(1)
    budget = _BUDGET.search(text)
    if budget is not None:
        return budget.group(1)
    reason = as_str(data.get("reason")) if isinstance(data, dict) else ""
    return (reasons or {}).get(reason, "acp_error")


def _relative(path: str) -> str:
    return workspace_relative(path, WORKDIR)


def _command(call: _Call) -> str:
    raw = as_object(call.raw_input)
    command = raw.get("command")
    if isinstance(command, list):
        text = " ".join(as_str(part) for part in as_list(command))
    else:
        text = as_str(command) or call.title
    return text[:MAX_COMMAND_CHARS]


def _has_command(call: _Call) -> bool:
    command = as_object(call.raw_input).get("command")
    return bool(as_list(command)) if isinstance(command, list) else bool(as_str(command))


def _text_of(blocks: JsonValue) -> str:
    parts: list[str] = []
    for block in as_list(blocks):
        entry = as_object(block)
        if as_str(entry.get("type")) == "content":
            inner = as_object(entry.get("content"))
            if as_str(inner.get("type")) == "text":
                parts.append(as_str(inner.get("text")))
    return "".join(parts)


# The sections of Gemini CLI's labelled command result (its shell tool's text for the model), each on its own line.
_LABELLED_RESULT = re.compile(
    r"\A[Cc]ommand: .*?^Output: (?P<output>.*?)(?=^(?:Error|Exit Code|Signal|Background PIDs|Process Group PGID): |\Z)",
    re.DOTALL | re.MULTILINE,
)
_LABELLED_NONE = frozenset({"(empty)", "(none)"})


def labelled_output(text: str) -> str:
    """The ``Output:`` section of a labelled command result, empty for ``(empty)``; any other text as it is."""
    match = _LABELLED_RESULT.match(text)
    if match is None:
        return text
    output = match.group("output").rstrip("\n")
    return "" if output.strip() in _LABELLED_NONE else output


# DeepSeek Harness's exit-status marker, the last line of its shell tool's result (dsh-tool-bash ``renderResult``; read
# back as dsh-shell's ``parseExitStatus`` does): ``[exit code: N]`` for a non-zero exit, ``[killed by signal: X]``.
_MARKED_EXIT = re.compile(r"\n\[exit code: (\d+)\]\Z")
_MARKED_SIGNAL = re.compile(r"\n\[killed by signal: [^\]\n]+\]\Z")


@dataclass(frozen=True, slots=True)
class MarkedResult:
    """A marked result read: the output without its marker, the exit code it named (0 without one), and whether a
    signal ended the command (no exit code then)."""

    output: str
    exit_code: int | None
    killed: bool = False


def marked_result(text: str) -> MarkedResult:
    """The output and exit status of a result whose last line may be an exit-status marker."""
    signal = _MARKED_SIGNAL.search(text)
    if signal is not None:
        return MarkedResult(text[: signal.start()], None, killed=True)
    exit_marker = _MARKED_EXIT.search(text)
    if exit_marker is not None:
        return MarkedResult(text[: exit_marker.start()], int(exit_marker.group(1)))
    return MarkedResult(text, 0)


def _exit_code(raw: JsonValue) -> int | None:
    output = as_object(raw)
    # opencode's ACP server passes its bash tool's own metadata on (``metadata.exit``), as its HTTP API does.
    for fields in (output, as_object(output.get("metadata"))):
        for key in ("exitCode", "exit_code", "exit"):
            value = fields.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _facts(call: _Call) -> tuple[str, str, tuple[str, ...]]:
    """What a step says of itself: a change of it after the call was announced is announced again."""
    return json.dumps(call.raw_input, sort_keys=True, default=str), call.title, tuple(call.locations)


# Gemini CLI sends no input for its skill tool; its title names the skill first, quoted: ``"pdf": Use when …``.
_SKILL_TITLE = re.compile(r'\A"([^"\n]{1,128})":')


def _seen(call: _Call, policy: SessionPolicy) -> dict[str, Any]:
    raw = call.raw_input
    arguments = as_object(raw) if isinstance(raw, dict) else None
    named = _SKILL_TITLE.match(call.title) if policy.native_names.get(call.name) is NativeTool.SKILL else None
    if arguments is None and named is not None:
        # Without it the step could not say which skill it read ("Read the PDF skill").
        arguments = {"name": named.group(1)}
    return {
        "arguments": arguments,
        "title": call.title,
        "paths": tuple(_relative(path) for path in call.locations),
    }


@dataclass
class _Call:
    call_id: str
    name: str
    title: str
    kind: str
    raw_input: JsonValue = None
    locations: list[str] = field(default_factory=list[str])
    diffs: dict[str, bool] = field(default_factory=dict[str, bool])
    started: bool = False
    done: bool = False


class AcpEventMapper:
    """Stateful, deterministic mapping for one turn; ``done`` once ``TurnCompleted`` is out."""

    def __init__(self, *, turn_id: str, policy: SessionPolicy) -> None:
        self._seq = TurnSeq(turn_id)
        self._policy = policy
        self.done = False
        self._calls: dict[str, _Call] = {}
        self._message_id: str | None = None
        self._message_text = ""
        self._messages = 0
        self._final_text = ""
        self._context_used: int | None = None
        # The turn's model calls' usage as the agent reported each (``callUsagePath``): input, output, reasoning,
        # cache reads.
        self._call_usage: list[int] | None = None
        self._compactions: set[str] = set()
        self.interrupted = False
        # The turn's native transcript (C2.39), set by the session.
        self.tape: NativeTape | None = None

    @property
    def turn_id(self) -> str:
        return self._seq.turn_id

    # ``session/update``

    def mark_interrupted(self) -> None:
        set_recorded(self, "interrupted", True)

    @recorded
    def update(self, update: Mapping[str, JsonValue]) -> list[EngineEvent]:
        if self.done:
            return []
        kind = as_str(update.get("sessionUpdate"))
        if kind not in _MAPPED and kind not in KNOWN_QUIET:
            return [unknown_event(self._seq, kind, update)]
        if kind == "agent_message_chunk":
            self._add_call_usage(update)
            return self._text(update)
        if kind == "agent_thought_chunk":
            text = self._chunk_text(update)
            message = self._message_id or f"{self.turn_id}:r{self._messages + 1}"
            return [ReasoningDelta(self._seq.next(), message, text)] if text else []
        if kind == "tool_call":
            return self._tool(update, first=True)
        if kind == "tool_call_update":
            return self._tool(update, first=False)
        if kind == "plan":
            return self._plan(update)
        if kind == "usage_update":
            used = update.get("used")
            if isinstance(used, int) and not isinstance(used, bool):
                self._context_used = used
            return []
        if kind == "compaction_update":
            return self._compaction(update)
        return []

    def _add_call_usage(self, update: Mapping[str, JsonValue]) -> None:
        if not self._policy.call_usage_path:
            return
        usage = named_object(update, self._policy.call_usage_path)
        if not usage:
            return
        counts = [as_int(usage.get(k)) for k in ("inputTokens", "outputTokens", "thoughtTokens", "cachedReadTokens")]
        self.add_call_usage(*counts)

    def add_call_usage(self, input_tokens: int, output_tokens: int, reasoning_tokens: int = 0, cache_read: int = 0) -> None:
        """One model call of the turn, as the agent reported it (``callUsagePath``) or recorded it (``sessionRecord``)."""
        counts = (input_tokens, output_tokens, reasoning_tokens, cache_read)
        self._call_usage = [a + b for a, b in zip(self._call_usage or [0, 0, 0, 0], counts, strict=True)]

    @staticmethod
    def _chunk_text(update: Mapping[str, JsonValue]) -> str:
        content = as_object(update.get("content"))
        return as_str(content.get("text")) if as_str(content.get("type")) == "text" else ""

    def _text(self, update: Mapping[str, JsonValue]) -> list[EngineEvent]:
        text = self._chunk_text(update)
        events: list[EngineEvent] = []
        given = as_str(update.get("messageId"))
        if self._message_id is not None and given and given != self._message_id:
            events.extend(self._close_message())
        if self._message_id is None:
            self._messages += 1
            self._message_id = given or f"{self.turn_id}:m{self._messages}"
        if text:
            self._message_text += text
            events.append(TextDelta(self._seq.next(), self._message_id, text))
        return events

    def _close_message(self) -> list[EngineEvent]:
        if self._message_id is None:
            return []
        message_id, text = self._message_id, self._message_text
        self._message_id, self._message_text = None, ""
        self._final_text = text
        return [MessageCompleted(self._seq.next(), message_id, text)]

    def _plan(self, update: Mapping[str, JsonValue]) -> list[EngineEvent]:
        entries = update.get("entries")
        if not isinstance(entries, list):
            return []
        plan = plan_updated(self._seq.next(), entries, ("content",))
        return [plan] if plan is not None else []

    def _compaction(self, update: Mapping[str, JsonValue]) -> list[EngineEvent]:
        compaction = as_str(update.get("compactionId"))
        if as_str(update.get("status")) != "completed" or compaction in self._compactions:
            return []
        self._compactions.add(compaction)
        return [ContextCompacted(self._seq.next(), self._context_used, None)]

    @property
    def context_used(self) -> int | None:
        """The context the agent last reported in use (``usage_update``), if it did."""
        return self._context_used

    def compacted(self, before_tokens: int | None, after_tokens: int | None) -> list[EngineEvent]:
        """A compaction the driver learnt of outside ACP (``compactionNotice``), numbered in the turn's events."""
        if self.done:
            return []
        return [ContextCompacted(self._seq.next(), before_tokens, after_tokens)]

    def _tool(self, update: Mapping[str, JsonValue], *, first: bool) -> list[EngineEvent]:
        call_id = as_str(update.get("toolCallId"))
        if not call_id:
            return []
        call = self._calls.get(call_id)
        if call is None:
            if not first and not (as_str(update.get("title")) or as_str(update.get("name"))):
                return []
            name = tool_name(update, self._policy.call_id_name_separator, self._policy.tool_name_path)
            call = _Call(call_id, name, as_str(update.get("title")), as_str(update.get("kind")) or "other")
            self._calls[call_id] = call
        known = _facts(call)
        self._merge(call, update, self._policy.tool_name_path)
        call.kind = native_kind(self._policy, call.name, call.kind)
        if call.done:
            return []
        events: list[EngineEvent] = []
        status = as_str(update.get("status"))
        # An agent announces a command before its input (opencode: title "bash", rawInput {}): started then, the
        # step would name the tool forever, never the command it ran.
        waits_for_command = call.kind == "execute" and not _has_command(call) and status not in ("completed", "failed")
        if not call.started and not waits_for_command:
            events.extend(self._close_message())
            events.extend(self._started(call))
            call.started = True
        elif call.started and call.kind != "execute" and _facts(call) != known and status not in ("completed", "failed"):
            # Claude Code's adapter announces a call with rawInput {} and sends the input in an update: the same step
            # gains it, or it would never say which page or file (WP-112). One call is one start.
            events.extend(self._updated(call))
        if status in ("completed", "failed"):
            call.done = True
            events.extend(self._finished(call, update, ok=status == "completed"))
        return events

    @staticmethod
    def _merge(call: _Call, update: Mapping[str, JsonValue], name_path: tuple[str, ...]) -> None:
        if as_str(update.get("name")) or named_at(update, name_path):
            call.name = as_str(update.get("name")) or named_at(update, name_path)
        if as_str(update.get("title")):
            call.title = as_str(update.get("title"))
            call.name = call.name or call.title
        if as_str(update.get("kind")):
            call.kind = as_str(update.get("kind"))
        if "rawInput" in update and update.get("rawInput") is not None:
            call.raw_input = update.get("rawInput")
        for location in as_list(update.get("locations")):
            path = as_str(as_object(location).get("path"))
            if path and path not in call.locations:
                call.locations.append(path)
        for block in as_list(update.get("content")):
            entry = as_object(block)
            if as_str(entry.get("type")) == "diff" and as_str(entry.get("path")):
                call.diffs[as_str(entry.get("path"))] = entry.get("oldText") is None

    def _silent(self, call: _Call) -> bool:
        return call.kind == "think" or (self._policy.plan_tool is not None and call.name == self._policy.plan_tool)

    def _shown(self, call: _Call) -> str:
        name = call.name or call.title or call.kind
        if self._policy.is_gateway_tool(name):
            return shown_tool(name, as_object(call.raw_input), prefix=self._policy.gateway_prefix).removeprefix(
                self._policy.gateway_prefix
            )
        return name

    def _started(self, call: _Call) -> list[EngineEvent]:
        if self._silent(call):
            return []
        if call.kind == "execute":
            return [
                CommandStarted(
                    self._seq.next(), call.call_id, _command(call), output_may_be_lost=self._policy.output_may_be_lost
                )
            ]
        return [ToolCallSeen(self._seq.next(), call.call_id, self._shown(call), "started", **_seen(call, self._policy))]

    def _updated(self, call: _Call) -> list[EngineEvent]:
        if self._silent(call):
            return []
        return [ToolCallUpdated(self._seq.next(), call.call_id, self._shown(call), **_seen(call, self._policy))]

    def _finished(self, call: _Call, update: Mapping[str, JsonValue], *, ok: bool) -> list[EngineEvent]:
        if self._silent(call):
            return []
        if call.kind == "execute":
            events: list[EngineEvent] = []
            output = _text_of(update.get("content")) or as_str(as_object(update.get("rawOutput")).get("output"))
            code = _exit_code(update.get("rawOutput"))
            killed = False
            if self._policy.command_result == "labelled":
                output = labelled_output(output)
            elif self._policy.command_result == "marked":
                marked = marked_result(output)
                output, killed = marked.output, marked.killed
                if code is None:
                    code = marked.exit_code
            events.extend(command_output(self._seq.next, call.call_id, output))
            if ok and code is None and not killed:
                code = 0
            status: Literal["completed", "failed"] = "completed" if ok and code == 0 else "failed"
            events.append(CommandFinished(self._seq.next(), call.call_id, code, status))
            return events
        output = _text_of(update.get("content")) or as_str(as_object(update.get("rawOutput")).get("output"))
        events = [
            ToolCallSeen(
                self._seq.next(), call.call_id, self._shown(call), "completed" if ok else "failed",
                **_seen(call, self._policy), result=output or None,
            )
        ]
        if ok and call.kind in _WRITES:
            metadata = as_object(update.get("rawOutput")).get("metadata")
            listed = listed_changes(metadata)
            change: FileChange
            for path in dict.fromkeys([*call.diffs, *call.locations, *listed]):
                if call.kind == "delete":
                    change = "deleted"
                elif path in listed:
                    change = listed[path]
                elif call.diffs.get(path) or created(metadata):
                    change = "added"
                else:
                    change = "modified"
                events.append(FileChanged(self._seq.next(), call.call_id, _relative(path), change))
        return events

    # ``session/request_permission``

    @recorded
    def notice(self, method: str, message: Mapping[str, JsonValue]) -> list[EngineEvent]:
        """A notification of a method ACP does not define (a newer agent's)."""
        return [] if self.done else [unknown_event(self._seq, method, message)]

    def named(self, call: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        """``call`` (a ``session/request_permission`` tool call) with the name the agent gave the same call id when it
        announced it, for an agent whose ask carries only a display title (Goose); unchanged otherwise."""
        known = self._calls.get(as_str(call.get("toolCallId")))
        if known is None or not known.name or as_str(call.get("name")):
            return call
        return {**call, "name": known.name}

    @recorded
    def ask(self, request_id: str, call: Mapping[str, JsonValue], permission: str) -> EngineEvent:
        name = tool_name(call, self._policy.call_id_name_separator, self._policy.tool_name_path)
        call_id = as_str(call.get("toolCallId")) or None
        return ApprovalRequested(
            self._seq.next(), request_id=request_id, permission=permission, patterns=(name,) if name else (), call_id=call_id
        )

    # the turn's end

    @recorded
    def completed(self, response: Mapping[str, JsonValue]) -> list[EngineEvent]:
        if self.done:
            return []
        events = self._close_message()
        usage = as_object(response.get("usage"))
        if usage:
            events.append(
                UsageReported(
                    self._seq.next(),
                    input_tokens=as_int(usage.get("inputTokens")),
                    output_tokens=as_int(usage.get("outputTokens")),
                    reasoning_tokens=as_int(usage.get("thoughtTokens")),
                    cache_read_tokens=as_int(usage.get("cachedReadTokens")),
                    cache_write_tokens=as_int(usage.get("cachedWriteTokens")),
                )
            )
        elif self._policy.usage_path:
            reported = named_object(response, self._policy.usage_path)
            if reported:
                events.append(
                    UsageReported(
                        self._seq.next(),
                        input_tokens=as_int(reported.get("input_tokens")),
                        output_tokens=as_int(reported.get("output_tokens")),
                    )
                )
        elif self._call_usage is not None:
            input_tokens, output_tokens, reasoning, cache_read = self._call_usage
            events.append(
                UsageReported(
                    self._seq.next(),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning,
                    cache_read_tokens=cache_read,
                )
            )
        stop = as_str(response.get("stopReason"))
        reason: TurnEndReason = _STOP.get(stop, "failed")
        if self.interrupted and reason != "completed":
            reason = "interrupted"
        if reason == "failed":
            events.append(EngineError(self._seq.next(), stop or "unknown_stop", "the agent ended the turn without an answer"))
        return [*events, *self._end(reason)]

    @recorded
    def failed(self, code: str, message: str = "the agent failed the turn") -> list[EngineEvent]:
        if self.done:
            return []
        events = self._close_message()
        if self.interrupted:
            return [*events, *self._end("interrupted")]
        events.append(EngineError(self._seq.next(), code, message))
        return [*events, *self._end("failed")]

    def _end(self, reason: TurnEndReason) -> list[EngineEvent]:
        self.done = True
        return [TurnCompleted(self._seq.next(), reason, self._final_text)]


__all__ = ["KNOWN_QUIET", "MAX_COMMAND_CHARS", "AcpEventMapper", "error_code"]
