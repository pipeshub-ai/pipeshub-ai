"""Claude Code stream-json messages → ``EngineEvent``s for one turn.

Built on transcripts recorded from 2.1.289 (``tests/unit/harness_engines/claude_code/fixtures``).
Shapes relied on:

- a turn runs from the user message we send to its ``result`` message; one
  ``result`` per turn (``success``, or ``error_*`` with ``is_error``), carrying
  the turn's own ``usage`` and ``modelUsage`` (``costBasis`` says whether Claude
  Code knows the model's price);
- ``assistant`` messages are whole (no partial deltas are asked for): ``text``,
  ``thinking`` and ``tool_use`` blocks; a message carrying ``error`` is Claude
  Code's own text about a failed call, not the model's;
- tool results come back as ``user`` messages of ``tool_result`` blocks, with a
  ``tool_use_result`` detail (Bash: stdout and stderr, or the error text with
  its exit code; Write: ``type`` create or update; Agent: the helper's usage);
- a sub-agent runs in the foreground (background tasks are off): ``system``
  ``task_started`` and ``task_notification`` name it by ``task_id`` and the
  spawning ``tool_use_id``; its own messages carry ``parent_tool_use_id`` and
  are not the parent's: its tool calls and their results (forwarded by default) are
  its steps, its text and thinking (forwarded only with ``--forward-subagent-text``)
  never the run's;
- ``system`` ``permission_denied`` is a native permission Claude Code refused on
  its own (``dontAsk``); the model is told in the tool result;
- ``system`` ``compact_boundary`` is Claude Code's own compaction of the session,
  ``compact_metadata`` carrying ``pre_tokens`` (and ``post_tokens`` when known);
- a steer Claude Code has not taken in by a turn's ``result`` starts its next
  turn, so a turn with one still waiting goes on through that turn (2.1.294);
- a message ``type`` in neither the mapped nor the ``KNOWN_QUIET`` set is a
  newer Claude Code's: ``NativeUnknown`` (EV-13), never a failure.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from harness_core.engines.events import (
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
from harness_engines.plans import plan_updated

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.events import EngineEvent, TurnEndReason
    from harness_core.engines.native import NativeTape
    from harness_core.engines.types import JsonValue

SHELL_TOOL = "Bash"
TODO_TOOL = "TodoWrite"
SUB_AGENT_TOOL = "Agent"
FILE_TOOLS = frozenset({"Write", "Edit"})
GATEWAY_PREFIX = "mcp__tools__"
_EXIT_CODE = re.compile(r"Exit code (\d+)")
_EXIT_LINE = re.compile(r"\A(?:Error: )?Exit code \d+(?:\n|\Z)")
# The model gateway names its refusal code at the end of the error text Claude Code reports (E9 FB-3).
_GATEWAY_CODE = re.compile(r"\[gateway:([a-z][a-z0-9_]{0,63})\]")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# The model gateway answers 402 only when spending must stop (P6.5 ledger, E3's provider quota). Claude Code
# reports the status (``api_error_status``), not the gateway's code; both ledger codes stop the controller alike.
_GATEWAY_STATUS: Mapping[int, str] = {402: "budget_exhausted"}
_MAPPED = frozenset({"assistant", "user", "result", "system"})
# Message types stream-json carries that a turn has no use for (control replies, keep-alives, progress and
# rate-limit notices): neither mapped nor unknown. ``control_request`` and ``command_lifecycle`` are the session's.
KNOWN_QUIET: frozenset[str] = frozenset(
    {
        "auth_status",
        "command_lifecycle",
        "control_cancel_request",
        "control_request",
        "control_response",
        "keep_alive",
        "rate_limit_event",
        "stream_event",
        "tool_progress",
    }
)


@dataclass
class _Call:
    tool: str
    path: str = ""
    arguments: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])


def own_login_error(result: Mapping[str, JsonValue]) -> str | None:
    """A failed turn on the person's own login, as the chat names it (SP-6); ``None``: not a sign-in failure."""
    status = result.get("api_error_status")
    if status == 429:
        return "plan_limit_reached"
    if status == 401:
        return "account_expired"
    text = result.get("result")
    if status is None and isinstance(text, str) and text.startswith("Not logged in"):
        return "signin_required"
    return None


# The model gateway's code for a provider's context overflow.
CONTEXT_OVERFLOW = "context_length_exceeded"


def error_code(result: Mapping[str, JsonValue]) -> str:
    """A short identifier for a failed turn, never its message: the gateway's own code when it named one."""
    named = _GATEWAY_CODE.search(as_str(result.get("result")))
    if named is not None:
        return named.group(1)
    if as_str(result.get("terminal_reason")) == "prompt_too_long":
        # Claude Code 2.1.294 words the gateway's overflow its own way and drops the tag (MG-10).
        return CONTEXT_OVERFLOW
    status = as_int(result.get("api_error_status"))
    if status:
        return _GATEWAY_STATUS.get(status, f"http_{status}")
    subtype = as_str(result.get("subtype")).removeprefix("error_")
    code = subtype if subtype and subtype != "success" else as_str(result.get("terminal_reason"))
    return code if _CODE.match(code) else "engine_error"


def _text(content: JsonValue) -> str:
    if isinstance(content, str):
        return content
    return "".join(as_str(as_object(part).get("text")) for part in as_list(content))


@dataclass
class ClaudeEventMapper:
    """Stateful, deterministic mapping for one turn; ``done`` once ``TurnCompleted`` is out."""

    turn_id: str
    workdir: str = "/workspace"
    # The person's own Claude plan (SIGNIN): Claude Code's dollar figure is not PipesHub's cost, and an expired
    # login or a plan limit is named as such (SP-6).
    own_login: bool = False
    done: bool = False
    denied: int = 0
    steers_waiting: bool = False
    _seq: TurnSeq = field(init=False)
    _calls: dict[str, _Call] = field(default_factory=dict[str, _Call])
    _children: dict[str, str] = field(default_factory=dict[str, str])
    _interrupted: bool = False
    _final_text: str = ""
    _held: TurnEndReason | None = None
    # The turn's native transcript (C2.39), set by the session.
    tape: NativeTape | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        self._seq = TurnSeq(self.turn_id)

    @recorded
    def mark_interrupted(self) -> None:
        self._interrupted = True

    @recorded
    def feed(self, message: Mapping[str, JsonValue]) -> list[EngineEvent]:
        if self.done:
            return []
        kind = as_str(message.get("type"))
        if kind not in _MAPPED and kind not in KNOWN_QUIET:
            return [unknown_event(self._seq, kind, message)]
        if kind == "result":
            return self._result(message)
        if kind == "system":
            return self._system(message)
        if message.get("parent_tool_use_id"):
            return self._helper(message)
        if kind == "assistant":
            return self._assistant(message)
        if kind == "user":
            return self._tool_results(message)
        return []

    def _assistant(self, message: Mapping[str, JsonValue]) -> list[EngineEvent]:
        if "error" in message:
            return []
        body = as_object(message.get("message"))
        message_id = as_str(body.get("id"))
        events: list[EngineEvent] = []
        for block in (as_object(b) for b in as_list(body.get("content"))):
            kind = as_str(block.get("type"))
            if kind == "text":
                text = as_str(block.get("text"))
                if text:
                    self._final_text = text
                    events += [TextDelta(self._seq.next(), message_id, text), MessageCompleted(self._seq.next(), message_id, text)]
            elif kind == "thinking":
                thought = as_str(block.get("thinking"))
                if thought:
                    events.append(ReasoningDelta(self._seq.next(), message_id, thought))
            elif kind == "tool_use":
                events += self._tool_use(block)
        return events

    def _helper(self, message: Mapping[str, JsonValue]) -> list[EngineEvent]:
        """A helper's own message: its tool calls and their results are its steps (EV-07); its text, thinking and
        task list are its own, never the run's answer or plan."""
        spawned_by = as_str(message.get("parent_tool_use_id"))
        task = next((t for t, call_id in self._children.items() if call_id == spawned_by), None)
        if task is None:
            return []
        if as_str(message.get("type")) == "user":
            return taken_by(task, self._tool_results(message))
        if as_str(message.get("type")) != "assistant" or "error" in message:
            return []
        events: list[EngineEvent] = []
        for block in (as_object(b) for b in as_list(as_object(message.get("message")).get("content"))):
            if as_str(block.get("type")) == "tool_use" and as_str(block.get("name")) not in (TODO_TOOL, SUB_AGENT_TOOL):
                events += self._tool_use(block)
        return taken_by(task, events)

    def _tool_use(self, block: Mapping[str, JsonValue]) -> list[EngineEvent]:
        call_id, tool = as_str(block.get("id")), as_str(block.get("name"))
        tool_input = as_object(block.get("input"))
        if tool == TODO_TOOL:
            plan = plan_updated(self._seq.next(), tool_input.get("todos"), ("content",))
            return [plan] if plan is not None else []
        tool = shown_tool(tool, tool_input, prefix=GATEWAY_PREFIX)
        self._calls[call_id] = _Call(tool, as_str(tool_input.get("file_path")), tool_input)
        if tool == SHELL_TOOL:
            return [CommandStarted(self._seq.next(), call_id, as_str(tool_input.get("command")))]
        if tool == SUB_AGENT_TOOL:
            return []
        return [ToolCallSeen(self._seq.next(), call_id, tool.removeprefix(GATEWAY_PREFIX), "started", arguments=tool_input)]

    def _tool_results(self, message: Mapping[str, JsonValue]) -> list[EngineEvent]:
        detail = message.get("tool_use_result")
        events: list[EngineEvent] = []
        blocks = [as_object(b) for b in as_list(as_object(message.get("message")).get("content"))]
        for block in (b for b in blocks if as_str(b.get("type")) == "tool_result"):
            call_id = as_str(block.get("tool_use_id"))
            call = self._calls.pop(call_id, None)
            if call is None:
                continue
            failed = block.get("is_error") is True
            content = _text(block.get("content"))
            if call.tool == SHELL_TOOL:
                events += self._command_done(call_id, content, detail, failed=failed)
            elif call.tool == SUB_AGENT_TOOL:
                events += self._child_usage(detail)
            else:
                done: Literal["completed", "failed"] = "failed" if failed else "completed"
                events.append(
                    ToolCallSeen(
                        self._seq.next(), call_id, call.tool.removeprefix(GATEWAY_PREFIX), done,
                        arguments=call.arguments, result=content,
                    )
                )
                if call.tool in FILE_TOOLS and not failed:
                    change = "added" if as_str(as_object(detail).get("type")) == "create" else "modified"
                    events.append(FileChanged(self._seq.next(), call_id, self._relative(call.path), change))
        return events

    def _command_done(self, call_id: str, content: str, detail: JsonValue, *, failed: bool) -> list[EngineEvent]:
        result = as_object(detail)
        stdout, stderr = (as_str(result.get("stdout")), as_str(result.get("stderr"))) if result else (content, "")
        code: int | None = 0
        if failed:
            found = _EXIT_CODE.search(content)
            code = int(found.group(1)) if found is not None else None
            # A failed command's result is its status line, then the output (``Exit code 3\n<stdout>\n<stderr>``):
            # the line is the step's status, not output (R31JFIX-FU-1's conformance case on the real binary).
            status_line = _EXIT_LINE.match(content)
            if not result and status_line is not None:
                stdout = content[status_line.end() :]
        events: list[EngineEvent] = []
        events.extend(command_output(self._seq.next, call_id, stdout))
        events.extend(command_output(self._seq.next, call_id, stderr, "stderr"))
        events.append(CommandFinished(self._seq.next(), call_id, code, "failed" if failed else "completed"))
        return events

    def _child_usage(self, detail: JsonValue) -> list[EngineEvent]:
        result = as_object(detail)
        usage = as_object(result.get("usage"))
        if not usage:
            return []
        return [self._usage(usage, None, sub_session_id=as_str(result.get("agentId")) or None)]

    def _system(self, message: Mapping[str, JsonValue]) -> list[EngineEvent]:
        subtype = as_str(message.get("subtype"))
        call_id = as_str(message.get("tool_use_id"))
        task = as_str(message.get("task_id"))
        if subtype == "task_started" and call_id in self._calls and task not in self._children:
            self._children[task] = call_id
            agent, asked = as_str(message.get("subagent_type")), as_str(message.get("description"))
            return [SubAgentStarted(self._seq.next(), task, call_id, agent, asked)]
        if subtype == "task_notification" and self._children.get(task) == call_id:
            del self._children[task]
            ok = as_str(message.get("status")) == "completed"
            return [SubAgentFinished(self._seq.next(), task, call_id, "completed" if ok else "failed")]
        if subtype == "permission_denied":
            self.denied += 1
        if subtype == "compact_boundary" and not message.get("parent_tool_use_id"):
            meta = as_object(message.get("compact_metadata"))
            return [
                ContextCompacted(
                    self._seq.next(), as_int(meta.get("pre_tokens")) or None, as_int(meta.get("post_tokens")) or None
                )
            ]
        return []

    def _usage(
        self, usage: Mapping[str, JsonValue], cost: float | None, *, sub_session_id: str | None = None
    ) -> UsageReported:
        return UsageReported(
            self._seq.next(),
            input_tokens=as_int(usage.get("input_tokens")),
            output_tokens=as_int(usage.get("output_tokens")),
            reasoning_tokens=as_int(as_object(usage.get("output_tokens_details")).get("thinking_tokens")),
            cache_read_tokens=as_int(usage.get("cache_read_input_tokens")),
            cache_write_tokens=as_int(usage.get("cache_creation_input_tokens")),
            cost_usd=cost,
            sub_session_id=sub_session_id,
        )

    def _result(self, result: Mapping[str, JsonValue]) -> list[EngineEvent]:
        events: list[EngineEvent] = []
        usage = as_object(result.get("usage"))
        if any(as_int(usage.get(k)) for k in ("input_tokens", "output_tokens")):
            events.append(self._usage(usage, None if self.own_login else _known_cost(result)))
        reason: TurnEndReason
        if result.get("is_error") is not True and as_str(result.get("subtype")) == "success":
            reason = "completed"
            self._final_text = as_str(result.get("result")) or self._final_text
        elif self._interrupted or as_str(result.get("terminal_reason")).startswith("aborted"):
            reason = "interrupted"
        else:
            reason = "failed"
            code = (own_login_error(result) if self.own_login else None) or error_code(result)
            events.append(EngineError(self._seq.next(), code, "the engine reported a turn error"))
        if self.steers_waiting and not self._interrupted and reason != "interrupted":
            self._held = reason
            return events
        return [*events, *self._complete(reason)]

    @recorded
    def finish(self) -> list[EngineEvent]:
        """End a turn held open for a steer that Claude Code then dropped."""
        return [] if self._held is None else self._complete(self._held)

    def _complete(self, reason: TurnEndReason) -> list[EngineEvent]:
        self.done = True
        return [TurnCompleted(self._seq.next(), reason, self._final_text)]

    def _relative(self, path: str) -> str:
        if path.startswith(self.workdir.rstrip("/") + "/"):
            return posixpath.relpath(path, self.workdir)
        return path


def _known_cost(result: Mapping[str, JsonValue]) -> float | None:
    """Claude Code's cost only when it knows every model's price; with the neutral model id it guesses."""
    models = [as_object(m) for m in as_object(result.get("modelUsage")).values()]
    if not models or any(as_str(m.get("costBasis")) in ("", "unknown") for m in models):
        return None
    return as_float(result.get("total_cost_usd"))


__all__ = [
    "FILE_TOOLS",
    "GATEWAY_PREFIX",
    "SHELL_TOOL",
    "SUB_AGENT_TOOL",
    "ClaudeEventMapper",
    "error_code",
    "own_login_error",
]
