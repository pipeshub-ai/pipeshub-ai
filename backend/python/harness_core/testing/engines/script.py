"""A scripted model for engine tests, driven by directives in the user's messages.

Engines under test (``FakeEngine``, a recorded fake of a real engine) run a
real agent loop against the conformance gateway's model endpoint. The model's
next move is read from directives, one per line, in the user messages of the
current turn, the way the probe captures were made (``CALLTOOL:`` prompts):

=================  ==========================================================
``say <text>``     the final reply includes ``<text>``
``tool <n> <json>`` call gateway tool ``<n>`` (exposed as ``<n>`` or ``*_<n>``); one
                   the list lacks but a ``search_tools`` result of the turn
                   named, through ``call_tool`` (R4: a chat's list is fixed)
``tools <n> [<json>, …]`` one step calls gateway tool ``<n>`` once per object, as
                   a model's batch of parallel calls does
``shell <cmd>``    call the engine's shell tool
``write <p> <t>``  call the engine's file-writing tool
``spawn <prompt>`` call the engine's sub-agent tool; the child's prompt is
                   ``<prompt>`` (itself directives)
``skill <name>``   call the engine's skill-loading tool for skill ``<name>``; an
                   engine without one reads the skill's ``SKILL.md`` with its shell
``todo <json>``    call the engine's own task-list tool with ``[{title, status}]``
``clones``         one shell call of each ``ph git clone <url>`` command the message
                   gives in backticks (a task's named repositories)
``hold``         the model call blocks until the test releases it
``usage <n>``      the provider reports ``<n>`` prompt tokens for the turn's calls
``fail <text>``    the final reply streams ``<text>``, then the provider fails
                   mid-stream (``ModelReply.fails_mid_stream``); without
                   streaming the whole call fails
``overflow``       the turn's next model call fails with the provider's
                   context-overflow error (MG-10), once per message that asks
=================  ==========================================================

Lines that are not directives are ordinary text. After the last tool directive
the model replies with the ``say`` texts (or ``done``). A deferred tool result
ends the turn with ``waiting for approval``.

Usage is reported as a provider counts it, about four bytes a token: the prompt from the request (its body on the
wire when the caller knows it), the completion from the reply. Context management acts on these counts, so a
growing conversation reaches its compaction threshold as it would on a real model; ``usage <n>`` names the
count outright.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal, cast

from harness_core.engines.jsonview import as_int, as_list, as_object, as_str

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.engines.types import JsonValue

# Each engine's own names: opencode's lower case, Codex's ``exec_command``/``spawn_agent``, Claude Code's capitalised,
# Gemini CLI's snake case.
SHELL_TOOLS = frozenset({"bash", "shell", "exec_command", "Bash", "run_shell_command"})
WRITE_TOOLS = frozenset({"write", "edit", "Write", "Edit", "write_file"})
SUBAGENT_TOOLS = frozenset({"task", "subagent", "spawn_agent", "Agent"})
SKILL_TOOLS = frozenset({"skill", "Skill", "activate_skill"})
# Each engine's task-list tool: opencode's, Claude Code's, Codex's plan.
TODO_TOOLS = frozenset({"todowrite", "TodoWrite", "update_plan"})
# Claude Code's and Qwen Code's file tools want an absolute path; their sandboxes work in this folder.
FILE_TOOL_WORKDIR = "/workspace"
# Sub-agent name engines register for spawned helpers; the scripted model asks for it.
SUBAGENT_NAME = "helper"
DEFERRED_REPLY = "waiting for approval"
NO_TOOL_REPLY = "tool unavailable"

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def args(self) -> dict[str, JsonValue]:
        value = json.loads(self.arguments or "{}")
        if not isinstance(value, dict):
            raise ValueError("tool arguments must be a JSON object")
        return cast("dict[str, JsonValue]", value)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments} for c in self.tool_calls
            ]
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        return out

    @staticmethod
    def from_json(value: JsonValue) -> ChatMessage:
        obj = as_object(value)
        calls = tuple(
            ToolCall(id=as_str(c.get("id")), name=as_str(c.get("name")), arguments=as_str(c.get("arguments")))
            for c in (as_object(item) for item in as_list(obj.get("tool_calls")))
        )
        tool_call_id = obj.get("tool_call_id")
        return ChatMessage(
            role=cast("Role", as_str(obj.get("role"))),
            content=as_str(obj.get("content")),
            tool_calls=calls,
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
        )


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int = 10
    output_tokens: int = 5


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: ModelUsage = field(default_factory=ModelUsage)
    # ``fail``: whoever serves the reply streams ``content`` and then fails the call, as a provider that drops
    # mid-answer does.
    fails_mid_stream: bool = False
    # The provider refused the call with this failure (``CONTEXT_OVERFLOW``); whoever serves the reply answers with the
    # provider's own error for it, never ``content``.
    error: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {
            "content": self.content,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in self.tool_calls],
            "usage": {"input": self.usage.input_tokens, "output": self.usage.output_tokens},
        }
        if self.fails_mid_stream:
            out["failsMidStream"] = True
        if self.error is not None:
            out["error"] = self.error
        return out

    @staticmethod
    def from_json(value: JsonValue) -> ModelReply:
        obj = as_object(value)
        usage = as_object(obj.get("usage"))
        return ModelReply(
            content=as_str(obj.get("content")),
            tool_calls=tuple(
                ToolCall(id=as_str(c.get("id")), name=as_str(c.get("name")), arguments=as_str(c.get("arguments")))
                for c in (as_object(item) for item in as_list(obj.get("tool_calls")))
            ),
            usage=ModelUsage(as_int(usage.get("input")), as_int(usage.get("output"))),
            fails_mid_stream=obj.get("failsMidStream") is True,
            error=error if isinstance(error := obj.get("error"), str) else None,
        )


@dataclass(frozen=True, slots=True)
class Directive:
    verb: Literal[
        "say", "tool", "tools", "shell", "write", "spawn", "skill", "todo", "hold", "usage", "fail", "overflow"
    ]
    arg: str = ""


_CLONE_COMMAND = re.compile(r"`ph git clone (https://[^`\s]+)`")
_VERBS = ("say", "tool", "tools", "shell", "write", "spawn", "skill", "todo", "hold", "usage", "fail", "overflow")
# The gateway's code for a provider's context overflow (``classify_provider_failure``).
CONTEXT_OVERFLOW = "context_length_exceeded"
BYTES_PER_TOKEN = 4
_CALLS = ("tool", "tools", "shell", "write", "spawn", "skill", "todo")


def _batch_items(arg: str) -> list[dict[str, JsonValue]] | None:
    """The argument objects of ``tools <n> [<json>, …]``; None when the list is not a non-empty list of objects."""
    _, _, raw = arg.partition(" ")
    try:
        items: JsonValue = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        return None
    return cast("list[dict[str, JsonValue]]", items)


# Where an engine's own context block ends in a user message: Qwen Code glues the prompt to it with no line break.
_REMINDER_END = "</system-reminder>"


def parse_directives(text: str) -> list[Directive]:
    out: list[Directive] = []
    for raw in text.replace(_REMINDER_END, _REMINDER_END + "\n").splitlines():
        line = raw.strip()
        verb, _, rest = line.partition(" ")
        if verb == "tools" and _batch_items(rest.strip()) is None:
            continue
        if verb == "clones":
            # A model that does what its task says: each clone command the message gives, run in its shell (R41-CLONE).
            out.extend(Directive("shell", f"ph git clone {url}") for url in _CLONE_COMMAND.findall(text))
            continue
        if verb in _VERBS:
            out.append(Directive(verb=verb, arg=rest.strip()))
    return out


def single_calls(directive: Directive) -> list[Directive]:
    """A ``tools`` batch as its ``tool`` calls, in order; any other directive as itself."""
    if directive.verb != "tools":
        return [directive]
    name = directive.arg.partition(" ")[0]
    return [Directive("tool", f"{name} {json.dumps(item)}") for item in _batch_items(directive.arg) or []]


def current_turn(messages: Sequence[ChatMessage]) -> list[ChatMessage]:
    """Messages after the previous turn's final reply (an assistant message without tool calls)."""
    start = 0
    for index, message in enumerate(messages):
        if message.role == "assistant" and not message.tool_calls:
            start = index + 1
    return list(messages[start:])


def tool_properties(tool: Mapping[str, JsonValue]) -> frozenset[str]:
    """The argument names a request's tool entry declares, in whichever shape the wire carries its schema."""
    function = as_object(tool.get("function"))
    for schema in (tool.get("input_schema"), tool.get("parameters"), function.get("parameters")):
        properties = as_object(as_object(schema).get("properties"))
        if properties:
            return frozenset(properties)
    return frozenset()


def _pick(tools: Sequence[str], names: frozenset[str]) -> str | None:
    # A whole-file write takes {filePath, content}; an engine's edit tool (offered too) wants a diff.
    if names is WRITE_TOOLS:
        whole = next((t for t in ("write", "Write", "write_file") if t in tools), None)
        if whole is not None:
            return whole
    # A gateway that flattens namespaced tools names them ``<namespace>__<tool>`` (E3b).
    return next((t for t in tools if t in names or t.rpartition("__")[2] in names), None)


def _gateway_tool(tools: Sequence[str], name: str) -> str | None:
    """The listed tool that is gateway tool ``name``: itself, else one exposed under the gateway's MCP server
    (``tools_<n>``, ``mcp_tools_<n>``, ``mcp__tools__<n>``) before any other ``*_<n>``: an engine's own tool may end
    the same way (Gemini CLI's ``grep_search``)."""
    if name in tools:
        return name
    suffixed = [t for t in tools if t.endswith("_" + name)]
    served = [t for t in suffixed if t[: -len(name)].rstrip("_").endswith("tools")]
    return (served or suffixed or [None])[0]


CALL_TOOL = "call_tool"
# Codex hands a tool's result back as a JSON string inside its own JSON, so the quotes may arrive escaped.
_ANSWERED_NAME = re.compile(r'\\*"name\\*"\s*:\s*\\*"([^"\\]+)\\*"')


def _found(messages: Sequence[ChatMessage], name: str) -> str | None:
    """The exact name a tool result of the current turn (``search_tools``, ``fetch_tools``) answered for ``name``."""
    for message in current_turn(messages):
        if message.role != "tool":
            continue
        found = next((n for n in _ANSWERED_NAME.findall(message.content) if n == name or n.endswith("_" + name)), None)
        if found is not None:
            return found
    return None


def callable_tool(messages: Sequence[ChatMessage], tools: Sequence[str], name: str) -> tuple[str, str | None] | None:
    """How this request can call gateway tool ``name``: ``(listed tool, None)`` when the list has it, else
    ``(call_tool as listed, name)`` when the list has ``call_tool`` (a chat's list is fixed at its first request,
    R4; a tool not listed is called through ``call_tool``, as the product tells a model): by the exact name the turn's
    search found, else as given, as a model calls a tool whose name it has from the run's sources or the task
    (R27REST-FU-2: a call naming a tool of a granted server is its first use). ``None``: this request cannot call it."""
    listed = _gateway_tool(tools, name)
    if listed is not None:
        return listed, None
    via = _gateway_tool(tools, CALL_TOOL)
    return None if via is None else (via, _found(messages, name) or name)


@dataclass
class ScriptedBrain:
    """Decides the model's reply from the directives of the current turn.

    ``holds`` keys the ``hold`` directives already released, so a hold blocks
    once even though every later model call re-reads the same directives.
    """

    holds: set[tuple[int, int]] = field(default_factory=set[tuple[int, int]])
    # The user messages whose ``overflow`` was served, keyed by their directives: an engine's compaction moves the
    # message it keeps and may wrap it in other text, and its summary request and retry carry the same message again.
    overflowed: set[str] = field(default_factory=set[str])
    # The ``say`` texts of the last message whose call overflowed. An engine's summary carries the task on, and the
    # call that continues from it may hold no directive at all (opencode's "continue" after its own compaction).
    carried: list[str] = field(default_factory=list[str])
    # ADR-RUN-06: the deferred calls already called again once their approval reached the turn.
    recalled: set[str] = field(default_factory=set[str])

    def pending_hold(self, messages: Sequence[ChatMessage]) -> tuple[int, int] | None:
        turn = current_turn(messages)
        calls_made = _scheduled_calls(turn)
        offset = len(messages) - len(turn)
        position = 0
        for index, message in enumerate(turn):
            if message.role != "user":
                continue
            for d_index, directive in enumerate(parse_directives(message.content)):
                if directive.verb == "hold":
                    key = (offset + index, d_index)
                    if position <= calls_made and key not in self.holds:
                        return key
                elif directive.verb in _CALLS:
                    position += len(single_calls(directive))
        return None

    def reply(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[str],
        call_id: str,
        properties: Mapping[str, frozenset[str]] | None = None,
    ) -> ModelReply:
        """``properties``: each listed tool's argument names, where the request carried its schema; a call then names
        its arguments as the tool does (DeepSeek Harness's ``write`` takes ``file_path``, opencode's ``filePath``)."""
        asking = next(
            (
                (key, directives) for m in current_turn(messages) if m.role == "user"
                for directives in (parse_directives(m.content),)
                if any(d.verb == "overflow" for d in directives)
                for key in ("\n".join(f"{d.verb} {d.arg}" for d in directives),)
                if key not in self.overflowed
            ),
            None,
        )
        if asking is not None:
            self.overflowed.add(asking[0])
            self.carried = [d.arg for d in asking[1] if d.verb == "say"]
            return ModelReply(error=CONTEXT_OVERFLOW, usage=scripted_usage(messages, ModelReply()))
        reply = self._reply(messages, tools, call_id, properties or {})
        return replace(reply, usage=scripted_usage(messages, reply))

    def _reply(
        self, messages: Sequence[ChatMessage], tools: Sequence[str], call_id: str, properties: Mapping[str, frozenset[str]]
    ) -> ModelReply:
        turn = current_turn(messages)
        directives = [d for m in turn if m.role == "user" for d in parse_directives(m.content)]
        results = [m for m in turn if m.role == "tool"]
        waiting = bool(results) and _is_deferred(results[-1].content)
        if waiting and KEEP_WORKING_MARK not in results[-1].content:
            return ModelReply(content=DEFERRED_REPLY)
        recall = self._recall(messages, turn, call_id)
        if recall is not None:
            return ModelReply(tool_calls=(recall,))
        calls_made = _scheduled_calls(turn)
        done = 0
        for step in (single_calls(d) for d in directives if d.verb in _CALLS):
            if calls_made < done + len(step):
                due = step[calls_made - done :]
                calls = [
                    _call_for(d, tools, call_id if n == 0 else f"{call_id}_{n}", messages, properties)
                    for n, d in enumerate(due)
                ]
                if any(c is None for c in calls):
                    return ModelReply(content=NO_TOOL_REPLY)
                return ModelReply(tool_calls=tuple(c for c in calls if c is not None))
            done += len(step)
        if waiting:
            # Nothing left to do without the held call: the turn ends waiting for its decision.
            return ModelReply(content=DEFERRED_REPLY)
        # A question skipped by a new message leaves turn 1 without a final reply, so one turn holds both messages;
        # the answer is the latest message's ``say``, as a model answers what it was just sent.
        failing = [d.arg for d in directives if d.verb == "fail"]
        if failing:
            return ModelReply(content=failing[-1], fails_mid_stream=True)
        said_by_message = [[d.arg for d in parse_directives(m.content) if d.verb == "say"] for m in turn if m.role == "user"]
        said = next((s for s in reversed(said_by_message) if s), self.carried)
        return ModelReply(content=" ".join(said) if said else "done")


    def _recall(
        self, messages: Sequence[ChatMessage], turn: Sequence[ChatMessage], call_id: str
    ) -> ToolCall | None:
        """A model that kept working while a call waited calls it again once it is told it was approved (in this turn,
        or in the next turn's message when its first turn had ended)."""
        approved = {ix for m in turn if m.role in ("user", "tool") for ix in _approved_in(m.content)}
        if not approved:
            return None
        calls = {c.id: c for m in messages if m.role == "assistant" for c in m.tool_calls}
        for message in messages:
            ix = _held_interaction(message.content) if message.role == "tool" else None
            call = calls.get(message.tool_call_id or "")
            if ix in approved and ix not in self.recalled and call is not None:
                self.recalled.add(ix)
                return ToolCall(f"{call_id}{RECALL_SUFFIX}", call.name, call.arguments)
        return None


# ADR-RUN-06 D1: the held call's words that tell a model to go on with the rest of its task.
KEEP_WORKING_MARK = "Keep working on the rest of the task meanwhile"
# A re-call is not a directive's call: the directives' count skips it.
RECALL_SUFFIX = "_recall"
_APPROVED = re.compile(r"The approvals you were waiting for are decided:[^\n]*?Approved: ((?:\S+, )*\S+?)\.(?:\s|\]|$)")


def _scheduled_calls(turn: Sequence[ChatMessage]) -> int:
    return sum(1 for m in turn if m.role == "assistant" for c in m.tool_calls if not c.id.endswith(RECALL_SUFFIX))


def _approved_in(content: str) -> list[str]:
    return [ix.strip() for found in _APPROVED.finditer(content) for ix in found.group(1).split(",")]


_HELD_TEXT = re.compile(r"\(interaction (\S+?)\)\. " + re.escape(KEEP_WORKING_MARK))


def _held_interaction(content: str) -> str | None:
    """The interaction a held call's result names: the conformance gateway's JSON, or the tool gateway's own words."""
    if _is_deferred(content):
        for candidate in (content, content.rsplit("\n", 1)[-1]):
            try:
                value = json.loads(candidate)
            except ValueError:
                continue
            found = cast("dict[str, JsonValue]", value).get("interactionId") if isinstance(value, dict) else None
            return found if isinstance(found, str) else None
    held = _HELD_TEXT.search(content)
    return held.group(1) if held else None


def scripted_usage(
    messages: Sequence[ChatMessage], reply: ModelReply, *, request_bytes: int | None = None
) -> ModelUsage:
    """The usage a provider would report for ``reply``: the turn's last ``usage <n>`` names the prompt tokens;
    otherwise the request's size (``request_bytes``, else its messages') at four bytes a token."""
    named = [
        int(d.arg) for m in current_turn(messages) if m.role == "user"
        for d in parse_directives(m.content) if d.verb == "usage" and d.arg.isdigit()
    ]
    sent = request_bytes if request_bytes is not None else sum(len(json.dumps(m.to_json())) for m in messages)
    written = len(reply.content.encode()) + sum(len(c.name) + len(c.arguments) for c in reply.tool_calls)
    return ModelUsage(named[-1] if named else max(1, sent // BYTES_PER_TOKEN), max(1, written // BYTES_PER_TOKEN))


def _call_for(
    directive: Directive,
    tools: Sequence[str],
    call_id: str,
    messages: Sequence[ChatMessage] = (),
    properties: Mapping[str, frozenset[str]] | None = None,
) -> ToolCall | None:
    if directive.verb == "tool":
        name, _, raw_args = directive.arg.partition(" ")
        callable_as = callable_tool(messages, tools, name)
        if callable_as is None:
            return None
        exposed, found = callable_as
        if found is None:
            return ToolCall(call_id, exposed, raw_args.strip() or "{}")
        arguments = cast("JsonValue", json.loads(raw_args.strip() or "{}"))
        return ToolCall(call_id, exposed, json.dumps({"name": found, "arguments": arguments}))
    if directive.verb == "shell":
        return _shell_call(tools, directive.arg, call_id)
    if directive.verb == "skill":
        exposed = _pick(tools, SKILL_TOOLS)
        if exposed is None:
            return _shell_call(tools, _skill_read(messages, directive.arg), call_id)
        declared = (properties or {}).get(exposed, frozenset())
        # Claude Code's ``Skill`` and Qwen Code's ``skill`` take ``skill``; opencode's and Gemini CLI's ``name``.
        key = "skill" if exposed == "Skill" or ("skill" in declared and "name" not in declared) else "name"
        return ToolCall(call_id, exposed, json.dumps({key: directive.arg}))
    if directive.verb == "todo":
        exposed = _pick(tools, TODO_TOOLS)
        return None if exposed is None else ToolCall(call_id, exposed, json.dumps(_todo_args(exposed, directive.arg)))
    if directive.verb == "write":
        exposed = _pick(tools, WRITE_TOOLS)
        path, _, content = directive.arg.partition(" ")
        if exposed is None:
            # An engine without a file-writing tool (Codex) writes through its shell. A file tool creates the
            # folders it names; a bare redirect into a missing out/ fails, and the run commits no file (RL-1 on Codex).
            folder = posixpath.dirname(path)
            make = f"mkdir -p {shlex.quote(folder)} && " if folder else ""
            return _shell_call(tools, f"{make}printf %s {shlex.quote(content)} > {shlex.quote(path)}", call_id)
        declared = (properties or {}).get(exposed, frozenset())
        args: dict[str, JsonValue] = (
            {"file_path": posixpath.join(FILE_TOOL_WORKDIR, path), "content": content}
            if exposed in ("Write", "write_file") or "file_path" in declared
            # Goose's ``write`` names its file ``path``.
            else {"path": posixpath.join(FILE_TOOL_WORKDIR, path), "content": content}
            if "path" in declared and "filePath" not in declared
            else {"filePath": path, "content": content}
        )
    else:
        exposed = _pick(tools, SUBAGENT_TOOLS)
        args = (
            {"message": directive.arg}
            if exposed is not None and exposed.endswith("spawn_agent")
            else {"description": "scripted", "prompt": directive.arg, "subagent_type": SUBAGENT_NAME}
        )
    return None if exposed is None else ToolCall(call_id, exposed, json.dumps(args))


def _skill_read(messages: Sequence[ChatMessage], name: str) -> str:
    """How a model on an engine without a skill tool (Codex) loads skill ``name``: it reads the
    ``SKILL.md`` the engine listed (Codex names each skill's file), else asks ``ph`` as the run's AGENTS.md says.
    Answering "tool unavailable" instead ended the job before its first step (R31GVENG-FU-2)."""
    listed = re.compile(rf"(/[^\s()'\"`<>]*/{re.escape(name)}/SKILL\.md)")
    for message in messages:
        if message.role in ("system", "user"):
            found = listed.search(message.content)
            if found is not None:
                return f"cat {shlex.quote(found.group(1))}"
    return f"ph skill show {shlex.quote(name)}"


def _todo_args(tool: str, raw: str) -> dict[str, JsonValue]:
    """The list in each engine's own shape: Codex's ``plan`` of steps, the todo tools' items with their required fields."""
    items = [as_object(item) for item in as_list(cast("JsonValue", json.loads(raw)))]
    if tool.rpartition("__")[2] == "update_plan":
        return {"plan": [{"step": as_str(i.get("title")), "status": as_str(i.get("status"))} for i in items]}
    claude = tool.rpartition("__")[2] == "TodoWrite"

    def extra(n: int, item: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        return {"activeForm": as_str(item.get("title"))} if claude else {"priority": "medium", "id": str(n)}

    return {
        "todos": [
            {"content": as_str(i.get("title")), "status": as_str(i.get("status")), **extra(n, i)}
            for n, i in enumerate(items, start=1)
        ]
    }


def _shell_call(tools: Sequence[str], command: str, call_id: str) -> ToolCall | None:
    exposed = _pick(tools, SHELL_TOOLS)
    if exposed is None:
        return None
    # Codex's ``exec_command`` takes ``cmd``; opencode's ``bash`` takes ``command``.
    args = {"cmd": command} if exposed == "exec_command" else {"command": command, "description": "scripted"}
    return ToolCall(call_id, exposed, json.dumps(args))


def deferred_result(interaction_id: str, text: str = "") -> str:
    """A held call's result as an engine reads it; ``text``: what the gateway told the engine to do meanwhile."""
    return json.dumps({"deferred": True, "interactionId": interaction_id, **({"text": text} if text else {})})


def _is_deferred(content: str) -> bool:
    # Engines frame a tool's output (Codex: "Wall time: …\nOutput:\n<result>"); the result is the last line.
    for candidate in (content, content.rsplit("\n", 1)[-1]):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        return isinstance(value, dict) and cast("dict[str, JsonValue]", value).get("deferred") is True
    return False


__all__ = [
    "CONTEXT_OVERFLOW",
    "DEFERRED_REPLY",
    "NO_TOOL_REPLY",
    "SHELL_TOOLS",
    "SKILL_TOOLS",
    "SUBAGENT_NAME",
    "SUBAGENT_TOOLS",
    "TODO_TOOLS",
    "WRITE_TOOLS",
    "ChatMessage",
    "Directive",
    "ModelReply",
    "ModelUsage",
    "ScriptedBrain",
    "tool_properties",
    "ToolCall",
    "callable_tool",
    "current_turn",
    "deferred_result",
    "parse_directives",
    "scripted_usage",
    "single_calls",
]
