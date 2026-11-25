"""The E2E stack's model: a scripted OpenAI-compatible server for both the chat loop and the engine.

It is the org's configured ``openAICompatible`` provider. The query service reaches it directly for
the chat loop; the engine reaches it only as opencode → egress proxy → model gateway (E3) → the org's
route. It answers like a provider and refuses what one refuses (wrong key, unknown model, a tool call
without its result, a malformed tool), so a request the real provider would reject fails here too.

Who is asking is read from what the request offers, never from a header the caller could pick:

* opencode's tools offered (``bash``, ``write``): the engine. ``ScriptedBrain`` replies from the
  directives in the engine's prompt.
* the chat loop (any other tools offered) when the turn's user message holds directives: ``ScriptedBrain`` too,
  so a browser spec scripts a durable loop turn (a tool call, a hold, the answer) from what it types. A coder
  agent's loop (``code_task`` offered, CTL-6) hands the whole script to its engine and answers with its ``say``.
* anything else (an unscripted turn, title generation, the add-model health check): a short text answer.

A ``fail <text>`` directive makes the turn's answer fail as a provider that drops mid-answer does: on a chat
completions stream the text arrives, then an ``error`` event (OpenAI's own mid-stream error shape) and the connection
closes with no finish reason and no ``[DONE]``; without streaming, and on the other wires, the call is a 500.

Stage 1's chat loop (``code_task`` offered, the flag-off question) went with stage 1 (S1R).

Every request is logged as one JSON line (no message content) and kept for ``/__control/requests``. A test may
name values that must never reach a model (``/__control/canaries``): each request is searched for them as it
arrives, and only the hit is kept.

``/__control/reached`` is its opposite, for text that must reach the model: a journey names its turn's marker (its
``say`` line) and a phrase (the seeded page's "14 days ahead"); a request that holds both marks the phrase reached for
that marker. It reads what the model was sent whole, where the person's stream carries a tool result's first 2000
characters only (R31CAPFIX-FU-1: the searched records are listed by record id, so a page can sort past the cut).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import sys
import time
from dataclasses import replace
from typing import Any

from aiohttp import web

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from harness_core.testing.engines.script import (
    SHELL_TOOLS,
    WRITE_TOOLS,
    ChatMessage,
    Directive,
    ModelReply,
    ScriptedBrain,
    ToolCall,
    callable_tool,
    current_turn,
    parse_directives,
    scripted_usage,
    single_calls,
)
from wires import WIRES, Credentials, Refused, Wire, WireRequest, vertex_token

API_KEY = os.environ["FAKE_LLM_API_KEY"]
CONTROL_TOKEN = os.environ["FAKE_LLM_CONTROL_TOKEN"]
MODELS = frozenset(m.strip() for m in os.environ.get("FAKE_LLM_MODELS", "e2e-llm,e2e-embedding").split(","))
HOLD_LIMIT_S = 120.0
# The longest a test may ask a hold to last (``/__control/hold-limit``): a lane that reads a held sandbox from inside it
# (R31-SECKEY's whole-filesystem scan on gVisor) needs longer than a spec's default, and still never forever.
HOLD_LIMIT_MAX_S = 1800.0
FAULT_CODE = "e2e_mid_stream"
FAULT_MESSAGE = "The server had an error while processing your request."
# What only an engine offers: its own shell and file tools, under each engine's names (opencode ``bash``/``write``,
# Codex ``exec_command``, Claude Code ``Bash``/``Write``). The chat loop runs code through ``run_code``.
ENGINE_TOOLS = SHELL_TOOLS | WRITE_TOOLS


def _properties(asked: WireRequest) -> dict[str, frozenset[str]]:
    """Each offered tool's argument names, so a scripted call names the ones the engine's tool declares (DeepSeek
    Harness's ``write`` takes ``file_path``, opencode's ``filePath``), as the engines' conformance gateway passes them."""
    return {name: frozenset((schema or {}).get("properties") or {}) for name, schema in asked.schemas.items()
            if isinstance(schema, dict)}


def _is_engine(tools: list[str]) -> bool:
    """An engine's request, whichever engine: a gateway that flattens namespaces names a tool ``<ns>__<tool>``."""
    return any(t in ENGINE_TOOLS or t.rpartition("__")[2] in ENGINE_TOOLS for t in tools)
EMBEDDING_DIM = 384
# The other wires' credentials (QA-6): the lane seeds each provider's model with them.
CREDENTIALS = Credentials(
    api_key=API_KEY,
    aws_access_key_id=os.environ.get("FAKE_LLM_AWS_ACCESS_KEY_ID", "AKIAE2EFAKE0000000000"),
    aws_secret_access_key=os.environ.get("FAKE_LLM_AWS_SECRET_ACCESS_KEY", "e2e-fake-secret"),
    aws_region=os.environ.get("FAKE_LLM_AWS_REGION", "us-east-1"),
    vertex_public_key=os.environ.get("FAKE_LLM_VERTEX_PUBLIC_KEY") or None,
    vertex_client_email=os.environ.get("FAKE_LLM_VERTEX_CLIENT_EMAIL") or None,
)


def _text(content: Any) -> str:  # noqa: ANN401 - OpenAI content: a string or parts
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _error(status: int, message: str, code: str, kind: str = "invalid_request_error") -> web.Response:
    return web.json_response({"error": {"message": message, "type": kind, "code": code}}, status=status)


def _messages(body: dict[str, Any]) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    for raw in body.get("messages") or []:
        role = str(raw.get("role", ""))
        calls = tuple(
            ToolCall(str(c.get("id", "")), str((c.get("function") or {}).get("name", "")),
                     str((c.get("function") or {}).get("arguments", "")))
            for c in raw.get("tool_calls") or []
        )
        tool_call_id = raw.get("tool_call_id")
        out.append(ChatMessage(
            role="system" if role == "developer" else role,  # type: ignore[arg-type]
            content=_text(raw.get("content")),
            tool_calls=calls,
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
        ))
    return out


# A single-shot structured step (the intent rewrite every mode but quick runs, PR-1) offers only this tool.
SINGLE_SHOT_TOOLS = frozenset({"task_complete"})
# The tier a rewrite routes an `auto` turn to: the loop that runs the turn's directives as typed.
REWRITE_TIER = "quick"


def _rewrite(messages: list[ChatMessage], tools: list[str]) -> ModelReply | None:
    """The intent step's answer for a scripted turn: the request as it was asked, and the tier on its own line.

    A real model rewrites the request into the goal the loop then runs; the scripted model keeps it word for word,
    so the directives reach the loop in every mode (react, deep, plan_execute and auto rewrite the goal)."""
    if set(tools) != SINGLE_SHOT_TOOLS:
        return None
    asked = [m.content for m in current_turn(messages) if m.role == "user" and parse_directives(m.content)]
    return ModelReply(content=f"{asked[-1]}\n{REWRITE_TIER}") if asked else None


DELEGATE_TOOL = "spawn_agent"


def _delegate(messages: list[ChatMessage], tools: list[str], call_id: str) -> ModelReply | None:
    """Deep mode's coordinator for a scripted turn: it holds no domain tools, so it plans one step, has the plan
    critiqued (its phase gate), hands the whole request to one sub-agent (whose own loop then runs the directives) and,
    once that returns, answers with the turn's ``say`` text. Only the coordinator: a sub-agent offered
    ``spawn_agent`` (QAFINAL-FU-PW113) runs its own directives, so this rule never stands in for the sub-agent's turn."""
    if DELEGATE_TOOL not in tools or _due_tool_offered(messages, tools) or is_sub_agent(messages):
        return None
    turn = current_turn(messages)
    asked = [m.content for m in turn if m.role == "user" and parse_directives(m.content)]
    if not asked:
        return None
    made = [c.name for m in turn if m.role == "assistant" for c in m.tool_calls]
    goal = asked[-1]
    jobs = _jobs(goal)
    if jobs:
        return _delegate_jobs(jobs, goal, tools, made, call_id)
    for name, args in (
        ("create_plan", {"steps": [{"id": "qa6_task", "description": goal, "domain": "general"}], "confidence": "high"}),
        ("critique_plan", {}),
        (DELEGATE_TOOL, {"role": "researcher", "goal": goal, "task_id": "qa6_task",
                         "reasoning": "The request needs the team's connected tools, which only a sub-agent holds."}),
    ):
        if name in tools and name not in made:
            return ModelReply(tool_calls=(ToolCall(call_id, name, json.dumps(args)),))
    said = [d.arg for d in parse_directives(goal) if d.verb == "say"]
    return ModelReply(content=" ".join(said) or "done")


JOB_LINE = "job"
ANSWER_LINE = "answer"
# A sub-agent's task follows the person's quoted request (``harness_core.kernel.agent.user_request``).
DELEGATED_TASK_HEADER = "## Your delegated task"


# The product's sub-agent system prompt opens with it (``build_sub_agent_prompt``; a unit test holds the two together).
SUB_AGENT_ROLE_LINE = "You are a focused sub-agent for the '"


def is_sub_agent(messages: list[ChatMessage]) -> bool:
    """A sub-agent's request: its system prompt is a sub-agent's, or its task is handed over under the delegated-task
    header, which a person's request never is (the header is left out when the task already quotes the request)."""
    return any(m.role == "system" and SUB_AGENT_ROLE_LINE in m.content for m in messages) or any(
        m.role == "user" and DELEGATED_TASK_HEADER in m.content for m in current_turn(messages))


def _job_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        verb, _, rest = line.strip().partition(" ")
        if verb == JOB_LINE:
            out.append(json.loads(rest))
    return out


def _jobs(request: str) -> list[tuple[str, str]]:
    """The sub-agents a turn hands out at once (BGUI-8), one ``job {"goal": …, "script": [directive, …]}`` line each,
    as ``(task_id, goal)``. A job's directives travel inside its JSON, not as lines of the request: every sub-agent is
    handed the person's whole request ahead of its own task (``with_user_request``), so a directive line there would
    run in every sibling. A sub-agent's own task is read alone, so the quoted request never hands out again."""
    lines = _job_lines(request.rpartition(DELEGATED_TASK_HEADER)[2])
    return [(f"job_{n}", str(job["goal"])) for n, job in enumerate(lines, start=1)]


def _with_job_scripts(messages: list[ChatMessage]) -> list[ChatMessage]:
    """A sub-agent handed one of the turn's jobs runs that job's script: the coordinator hands out only the job's goal,
    as a person would read it in the plan and on the card, and its directives come from the quoted request's line."""
    out = list(messages)
    for index, message in enumerate(messages):
        if message.role != "user" or DELEGATED_TASK_HEADER not in message.content:
            continue
        quoted, _, task = message.content.rpartition(DELEGATED_TASK_HEADER)
        scripts = [list(map(str, job["script"])) for job in _job_lines(quoted) if str(job["goal"]) in task]
        if len(scripts) == 1:
            out[index] = replace(message, content="\n".join([message.content, *scripts[0]]))
    return out


def _delegate_jobs(jobs: list[tuple[str, str]], request: str, tools: list[str], made: list[str],
                   call_id: str) -> ModelReply:
    """The coordinator of a turn with ``job`` lines: a plan step a job, the critique, then every job in one batch of
    ``spawn_agent`` calls (so they run side by side, as a real coordinator's batch does; an approved plan may be
    dispatched by the loop itself instead) and, once they are back, the turn's ``answer`` line."""
    steps = [{"id": task, "description": goal, "domain": "general"} for task, goal in jobs]
    for name, args in (("create_plan", {"steps": steps, "confidence": "high"}), ("critique_plan", {})):
        if name in tools and name not in made:
            return ModelReply(tool_calls=(ToolCall(call_id, name, json.dumps(args)),))
    if DELEGATE_TOOL not in made:
        return ModelReply(tool_calls=tuple(
            ToolCall(f"{call_id}_{n}", DELEGATE_TOOL, json.dumps({"role": "researcher", "goal": goal, "task_id": task}))
            for n, (task, goal) in enumerate(jobs, start=1)))
    answer = [rest for verb, _, rest in (line.strip().partition(" ") for line in request.splitlines()) if verb == ANSWER_LINE]
    return ModelReply(content=" ".join(answer) or "done")


HAND_OFF_TOOL = "code_task"
# BGUI-10: a ``handoff-first <tool> <json>`` line is a call the coder agent's loop makes before it hands off, one per
# step (HANDOFF-CTX: the engine child starts from it); ``handoff-notes <text>`` is what it writes as code_task's goal.
# Neither is a directive, so the engine child, whose task quotes the request, ignores both.
FIRST_LINE = "handoff-first "
NOTES_LINE = "handoff-notes "
# R38-AUTHZ: a ``handoff-then <tool> <json>`` line is a call the loop makes once the job it handed off to has returned,
# one per step, before its answer (R36LIVE-FU-1: the parent repeats a call its job's person declined). Not a directive.
THEN_LINE = "handoff-then "
# R33JGAPS-FU-4: a turn whose request holds this line stays on the parent loop, which plays its directives itself, as
# a real model answers a simple turn without a job (code_task.py's instructions). Not a directive either.
PARENT_LINE = "stay-on-parent"


def _first_call(messages: list[ChatMessage], tools: list[str], spec: str, call_id: str) -> ToolCall | None:
    name, _, raw = spec.partition(" ")
    callable_as = callable_tool(messages, tools, name)
    if callable_as is None:
        return None
    exposed, found = callable_as
    if found is None:
        return ToolCall(call_id, exposed, raw.strip() or "{}")
    return ToolCall(call_id, exposed, json.dumps({"name": found, "arguments": json.loads(raw.strip() or "{}")}))


def _hand_off(messages: list[ChatMessage], tools: list[str], call_id: str) -> ModelReply | None:
    """A coder agent's loop for a scripted turn (CTL-6): it hands the whole request to its engine with ``code_task``
    (whose engine then runs the directives as an engine run did) and, once that returns, answers with the turn's
    ``say`` text."""
    if HAND_OFF_TOOL not in tools:
        return None
    turn = current_turn(messages)
    asked = [m.content for m in turn if m.role == "user" and parse_directives(m.content)]
    if not asked or PARENT_LINE in (line.strip() for line in asked[-1].splitlines()):
        return None
    made = [c for m in turn if m.role == "assistant" for c in m.tool_calls]
    if not any(c.name == HAND_OFF_TOOL for c in made):
        lines = asked[-1].splitlines()
        firsts = [line[len(FIRST_LINE):] for line in lines if line.startswith(FIRST_LINE)]
        if len(made) < len(firsts) and (first := _first_call(messages, tools, firsts[len(made)], call_id)) is not None:
            return ModelReply(tool_calls=(first,))
        notes = [line[len(NOTES_LINE):] for line in lines if line.startswith(NOTES_LINE)]
        goal = notes[-1] if notes else asked[-1]
        return ModelReply(tool_calls=(ToolCall(call_id, HAND_OFF_TOOL, json.dumps({"goal": goal})),))
    after = len(made) - 1 - max(i for i, c in enumerate(made) if c.name == HAND_OFF_TOOL)
    thens = [line[len(THEN_LINE):] for line in asked[-1].splitlines() if line.startswith(THEN_LINE)]
    if after < len(thens) and (then := _first_call(messages, tools, thens[after], call_id)) is not None:
        return ModelReply(tool_calls=(then,))
    said = [d.arg for d in parse_directives(asked[-1]) if d.verb == "say"]
    return ModelReply(content=" ".join(said) or "done")


def _loop_calls(turn: list[ChatMessage]) -> list[Directive]:
    """The turn's gateway tool calls, one per call: a ``tools`` batch counts as each of its calls."""
    return [c for m in turn if m.role == "user" for d in parse_directives(m.content) for c in single_calls(d)
            if c.verb == "tool"]


def _due_tool_offered(messages: list[ChatMessage], tools: list[str]) -> bool:
    turn = current_turn(messages)
    calls = _loop_calls(turn)
    made = sum(len(m.tool_calls) for m in turn if m.role == "assistant")
    if made >= len(calls):
        return False
    return callable_tool(messages, tools, calls[made].arg.partition(" ")[0]) is not None


def _scripted(messages: list[ChatMessage], tools: list[str]) -> bool:
    """A loop call the turn's directives drive: they are in the current turn and, when a tool call is still due, the
    call offers that tool. A step that offers other tools only (a goal or plan step's ``task_complete``) is not the
    loop's turn and gets the fixed reply, so it neither spends a directive nor ends the turn early (QA-6)."""
    turn = current_turn(messages)
    directives = [d for m in turn if m.role == "user" for d in parse_directives(m.content)]
    if not directives:
        return False
    calls = _loop_calls(turn)
    made = sum(len(m.tool_calls) for m in turn if m.role == "assistant")
    if made >= len(calls):
        return True
    return callable_tool(messages, tools, calls[made].arg.partition(" ")[0]) is not None


def _refusal(body: dict[str, Any]) -> web.Response | None:
    """What OpenAI's chat completions refuse before answering."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _error(400, "'messages' must be a non-empty array", "invalid_messages")
    for tool in body.get("tools") or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            return _error(400, "each tool needs function.name", "invalid_tools")
    open_calls: set[str] = set()
    for raw in messages:
        if open_calls and raw.get("role") != "tool":
            return _error(400, "an assistant message with 'tool_calls' must be followed by tool messages "
                          "responding to each 'tool_call_id'", "invalid_messages")
        if raw.get("role") == "tool":
            call_id = raw.get("tool_call_id")
            if call_id not in open_calls:
                return _error(400, "a 'tool' message must answer a preceding tool call", "invalid_messages")
            open_calls.discard(call_id)
        for call in raw.get("tool_calls") or []:
            open_calls.add(str(call.get("id")))
    if open_calls:
        return _error(400, "tool calls without results", "invalid_messages")
    return None


def _first_script_only(messages: list[ChatMessage]) -> list[ChatMessage]:
    """The turn's script as the person typed it once: a loop that repeats the task (react's step prompt in a later
    user message, a goal that quotes the original request beside its rewrite) must not make the scripted model run
    the directives a second time. A script never repeats a line, so a repeated directive line is such an echo."""
    turn_start = len(messages) - len(current_turn(messages))
    seen: set[str] = set()
    out = list(messages)
    for index in range(turn_start, len(messages)):
        message = messages[index]
        if message.role != "user":
            continue
        kept = []
        for line in message.content.splitlines():
            directive = line.strip() if parse_directives(line) else None
            if directive is not None and directive in seen:
                continue
            if directive is not None:
                seen.add(directive)
            kept.append(line)
        out[index] = replace(message, content="\n".join(kept))
    return out


ALTERNATIVE = " || "
# R35-SLACK: the Slack bot sends a message as one line (its whitespace collapsed), so a script typed in Slack marks its
# line breaks with this.
SLACK_LINE_BREAK = " ¶ "


# R36-COVER: the Slack bot puts a thread's earlier messages ahead of a reply (index.ts buildQueryWithThreadContext);
# a model answers the current message, so the earlier messages' directives are history, not this turn's script.
SLACK_CURRENT = ("Current slack message/query: ", "Current user's message: ")


def _slack_current(content: str) -> str:
    for marker in SLACK_CURRENT:
        if marker in content:
            return content.rpartition(marker)[2]
    return content


def _slack_lines(messages: list[ChatMessage]) -> list[ChatMessage]:
    return [replace(m, content=_slack_current(m.content).replace(SLACK_LINE_BREAK, "\n")) if m.role == "user"
            and SLACK_LINE_BREAK in m.content else m for m in messages]


def _offered_alternative(messages: list[ChatMessage], tools: list[str]) -> list[ChatMessage]:
    """``tool a {..} || b {..}``: the first of the alternatives this call offers (the first one when none is).

    A capability reaches the model under different tools by mode: knowledge as a flat search tool or as the
    exploration sub-agent, code as ``run_code`` or as the coding sub-agent. A script names them in order of preference."""
    def choose(line: str) -> str:
        verb, _, rest = line.strip().partition(" ")
        if verb != "tool" or ALTERNATIVE not in rest:
            return line
        options = rest.split(ALTERNATIVE)
        offered = next((o for o in options
                        if any(t == o.partition(" ")[0] or t.endswith("_" + o.partition(" ")[0]) for t in tools)), options[0])
        return f"tool {offered}"

    return [replace(m, content="\n".join(choose(line) for line in m.content.splitlines())) if m.role == "user" else m
            for m in messages]


def _structured_call(asked: WireRequest, call_id: str) -> ModelReply | None:
    """A structured-output step on a wire that asks for it as a tool call (one tool, no script in the turn): the call,
    with arguments that conform to the tool's schema (a reviewer finding nothing wrong)."""
    if len(asked.tools) != 1 or asked.tools[0] in SINGLE_SHOT_TOOLS or _is_engine(asked.tools):
        return None
    if any(parse_directives(m.content) for m in current_turn(asked.messages) if m.role == "user"):
        return None
    name = asked.tools[0]
    schema = asked.schemas.get(name)
    if not isinstance(schema, dict) or not schema.get("properties"):
        return None
    return ModelReply(tool_calls=(ToolCall(call_id, name, json.dumps(conforming(schema))),))


def _lower_types(schema: Any) -> Any:  # noqa: ANN401 - JSON
    """Gemini's OpenAPI-style schema names types in upper case (``OBJECT``, ``BOOLEAN``)."""
    if isinstance(schema, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else _lower_types(v)) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_lower_types(v) for v in schema]
    return schema


def _response_schema(body: dict[str, Any]) -> dict[str, Any] | None:
    """The JSON schema a structured-output request asks the answer to follow (``response_format: json_schema``)."""
    fmt = body.get("response_format")
    if not isinstance(fmt, dict) or fmt.get("type") != "json_schema":
        return None
    schema = (fmt.get("json_schema") or {}).get("schema")
    return schema if isinstance(schema, dict) else None


# The value a scripted structured answer picks from an enum: the most permissive verdict a reviewer can give.
PREFERRED = ("high", "pass", "passed", "approved", "yes", "ok")


def conforming(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:  # noqa: ANN401 - JSON
    """A small value that satisfies ``schema``: what a reviewer step (a plan critique, a result check) answers when
    it finds nothing wrong. Booleans are true, enums take the most permissive level, required properties only."""
    defs = defs if defs is not None else dict(schema.get("$defs") or schema.get("definitions") or {})
    if "$ref" in schema:
        return conforming(defs.get(str(schema["$ref"]).rsplit("/", 1)[-1], {}), defs)
    for key in ("anyOf", "oneOf", "allOf"):
        if schema.get(key):
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return conforming(options[0], defs)
    if "enum" in schema:
        values = list(schema["enum"])
        return next((v for v in PREFERRED if v in values), values[0])
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        props = dict(schema.get("properties") or {})
        required = schema.get("required") or list(props)
        return {k: conforming(props[k], defs) for k in required if k in props}
    return {"boolean": True, "string": "ok", "integer": 1, "number": 1, "array": [], "null": None}.get(str(kind), "ok")


def _counted(request: web.Request, messages: list[ChatMessage], reply: ModelReply) -> ModelReply:
    """The reply with the usage a provider would report for this request (``scripted_usage``, from its body size)."""
    sizes = request.get("sizes") or {}
    return replace(reply, usage=scripted_usage(messages, reply, request_bytes=sizes.get("bodyBytes")))


def _sizes(raw: bytes) -> dict[str, int]:
    """A model request's size on the wire and its tool definitions' (every wire names them ``tools``; Gemini nests
    its declarations one level down)."""
    try:
        body = json.loads(raw)
    except ValueError:
        return {"bodyBytes": len(raw)}
    tools = body.get("tools") if isinstance(body, dict) else None
    if not isinstance(tools, list):
        return {"bodyBytes": len(raw)}
    declared = [d for t in tools if isinstance(t, dict) for d in t.get("functionDeclarations") or t.get("function_declarations") or [t]]
    return {"bodyBytes": len(raw), "toolsBytes": len(json.dumps(tools, separators=(",", ":"))), "toolsCount": len(declared)}


class FakeModel:
    def __init__(self) -> None:
        self.brain = ScriptedBrain()
        self.requests: list[dict[str, Any]] = []
        self._since = 0
        self._calls = 0
        self.holding = asyncio.Event()
        self._release = asyncio.Event()
        # Values a test says must never reach a model (a tool's credential): kept in memory only, reported by digest.
        self._canaries: tuple[bytes, ...] = ()
        self.hold_limit_s = HOLD_LIMIT_S
        self.canary_hits: list[dict[str, Any]] = []
        # (marker, phrase) a journey waits to see reach the model, and those a request carried both of.
        self._reach_watches: set[tuple[bytes, bytes]] = set()
        self.reached: set[tuple[str, str]] = set()

    @web.middleware
    async def watch(self, request: web.Request, handler: Any) -> web.StreamResponse:  # noqa: ANN401 - aiohttp's handler
        """Looks for each canary in everything a caller sent (the body and the header values), keeping no content,
        and measures the request: its size and the size of the tool definitions it carries."""
        if request.path.startswith("/__control/") or request.method != "POST":
            return await handler(request)
        raw = await request.read()
        sent = raw + b"\n" + "\n".join(request.headers.values()).encode()
        for canary in self._canaries:
            if canary in sent:
                self.canary_hits.append({"path": request.path, "canaryDigest": hashlib.sha256(canary).hexdigest()[:16],
                                         "peer": request.remote, "at": time.time()})
        for marker, phrase in self._reach_watches:
            if marker in raw and phrase in raw:
                self.reached.add((marker.decode(), phrase.decode()))
        request["sizes"] = _sizes(raw)
        return await handler(request)

    async def control_canaries(self, request: web.Request) -> web.Response:
        if request.headers.get("X-E2E-Control") != CONTROL_TOKEN:
            return _error(401, "control token required", "unauthorized")
        if request.method == "POST":
            values = (await request.json()).get("values") or []
            self._canaries = tuple(str(v).encode() for v in values if v)
            self.canary_hits = []
        return web.json_response({"watching": len(self._canaries), "hits": self.canary_hits})

    async def control_reached(self, request: web.Request) -> web.Response:
        """POST {marker, phrase}: watch for a request holding both. GET ?marker=: the phrases that reached the model in
        a request holding that marker. Never cleared by a reset: journeys run side by side."""
        if request.headers.get("X-E2E-Control") != CONTROL_TOKEN:
            return _error(401, "control token required", "unauthorized")
        if request.method == "POST":
            body = await request.json()
            marker, phrase = str(body.get("marker") or ""), str(body.get("phrase") or "")
            if not marker or not phrase:
                return _error(400, "marker and phrase are required", "invalid_request")
            self._reach_watches.add((marker.encode(), phrase.encode()))
            return web.json_response({"watching": True})
        marker = request.query.get("marker", "")
        return web.json_response({"reached": sorted(p for m, p in self.reached if m == marker)})

    async def control_hold_limit(self, request: web.Request) -> web.Response:
        if request.headers.get("X-E2E-Control") != CONTROL_TOKEN:
            return _error(401, "control token required", "unauthorized")
        seconds = float((await request.json()).get("seconds") or HOLD_LIMIT_S)
        self.hold_limit_s = min(max(seconds, 1.0), HOLD_LIMIT_MAX_S)
        return web.json_response({"holdLimitSeconds": self.hold_limit_s})

    async def _hold_if_scripted(self, messages: list[ChatMessage]) -> None:
        key = self.brain.pending_hold(messages)
        if key is None:
            return
        self.holding.set()
        try:
            # Bounded: a spec that never releases must not wedge the engine past its own limits.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._release.wait(), self.hold_limit_s)
        finally:
            self.brain.holds.add(key)
            self._release.clear()
            self.holding.clear()

    def _log(self, entry: dict[str, Any]) -> None:
        entry["at"] = time.time()
        self.requests.append(entry)
        print(json.dumps({"fake_llm": entry}), flush=True)

    def _authorized(self, request: web.Request, path: str) -> bool:
        auth = request.headers.get("Authorization", "")
        ok = auth == f"Bearer {API_KEY}"
        if not ok:
            # Only the shape of what was presented, never the value.
            shape = "none" if not auth else ("jwt" if auth.count(".") == 2 else auth.split(" ", 1)[-1][:4] + "…")
            self._log({"path": path, "status": 401, "presented": shape, "peer": request.remote})
        return ok

    async def azure_chat(self, request: web.Request) -> web.StreamResponse:
        """Azure OpenAI: the deployment names the model, the key comes as ``api-key``."""
        if request.headers.get("api-key") != API_KEY:
            self._log({"path": request.path, "status": 401, "wire": "azure_openai", "peer": request.remote})
            return _error(401, "Access denied due to invalid subscription key or wrong API endpoint.", "401")
        if not request.query.get("api-version"):
            return _error(404, "Resource not found", "404")
        return await self._chat(request, "azure_openai", request.match_info["deployment"])

    async def chat(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request, "/v1/chat/completions"):
            return _error(401, "Incorrect API key provided", "invalid_api_key")
        return await self._chat(request, "openai_chat", None)

    async def _chat(self, request: web.Request, wire: str, deployment: str | None) -> web.StreamResponse:
        body = await request.json()
        model = deployment or str(body.get("model", ""))
        if model not in MODELS:
            self._log({"path": "/v1/chat/completions", "status": 404, "model": model, "peer": request.remote})
            return _error(404, f"The model `{model}` does not exist", "model_not_found")
        refused = _refusal(body)
        if refused is not None:
            self._log({"path": "/v1/chat/completions", "status": 400, "model": model, "peer": request.remote})
            return refused
        tools = [str(t["function"]["name"]) for t in body.get("tools") or []]
        asked = WireRequest(wire, model, _messages(body), tools, bool(body.get("stream")),
                            {str(t["function"]["name"]): t["function"].get("parameters") or {} for t in body.get("tools") or []})
        schema = _response_schema(body)
        if schema is not None and not tools:
            reply = _counted(request, asked.messages, ModelReply(content=json.dumps(conforming(schema))))
            self._log({"path": request.path, "status": 200, "kind": "structured", "wire": wire, "model": model,
                       "stream": asked.stream, "tools": [], "peer": request.remote})
            return await _answer(request, body, reply, model)
        reply = await self._decide(request, asked)
        return await _answer(request, body, reply, model)

    async def _decide(self, request: web.Request, asked: WireRequest) -> ModelReply:
        self._calls += 1
        call_id = f"call_e2e_{self._calls}"
        asked = replace(asked, messages=_slack_lines(asked.messages))
        # The rewrite and the delegation pass the script on whole; only the call that runs it picks alternatives.
        typed = replace(asked, messages=_first_script_only(_with_job_scripts(asked.messages)))
        asked = replace(typed, messages=_offered_alternative(typed.messages, asked.tools))
        structured = _structured_call(asked, call_id)
        if structured is None and asked.response_schema is not None and not asked.tools:
            structured = ModelReply(content=json.dumps(conforming(_lower_types(asked.response_schema))))
        if structured is not None:
            self._log({"path": request.path, "status": 200, "kind": "structured", "wire": asked.wire,
                       "model": asked.model, "stream": asked.stream, "tools": sorted(asked.tools), "peer": request.remote})
            return _counted(request, asked.messages, structured)
        if _is_engine(asked.tools):
            await self._hold_if_scripted(asked.messages)
            kind, reply = "engine", self.brain.reply(asked.messages, asked.tools, call_id, _properties(asked))
        elif (rewrite := _rewrite(typed.messages, asked.tools)) is not None:
            kind, reply = "rewrite", rewrite
        elif (handed := _hand_off(typed.messages, asked.tools, call_id)) is not None:
            kind, reply = "hand_off", handed
        elif (delegated := _delegate(typed.messages, asked.tools, call_id)) is not None:
            kind, reply = "delegate", delegated
        elif asked.tools and _scripted(asked.messages, asked.tools):
            # The chat loop offers tools; title generation and health checks offer none and must not spend a hold.
            await self._hold_if_scripted(asked.messages)
            kind, reply = "loop", self.brain.reply(asked.messages, asked.tools, call_id, _properties(asked))
        else:
            kind, reply = "other", ModelReply(content="E2E stack model reply.")
        self._log({
            "path": request.path, "status": 200, "kind": kind, "wire": asked.wire, "model": asked.model,
            "stream": asked.stream, "tools": sorted(asked.tools), "roles": [m.role for m in asked.messages],
            "subAgent": is_sub_agent(typed.messages),
            "directives": sum(len(parse_directives(m.content)) for m in asked.messages if m.role == "user"),
            "lastTool": next((m.tool_calls[-1].name for m in reversed(asked.messages) if m.tool_calls), None),
            "replyToolCalls": [c.name for c in reply.tool_calls], "peer": request.remote,
            **({"fault": "mid_stream"} if reply.fails_mid_stream else {}),
            **request.get("sizes", {}),
        })
        return _counted(request, asked.messages, reply)

    def wire_handler(self, wire: Wire):  # noqa: ANN201 - an aiohttp handler
        async def handle(request: web.Request) -> web.StreamResponse:
            try:
                asked = await wire.read(request, CREDENTIALS)
            except Refused as refused:
                self._log({"path": request.path, "status": refused.response.status, "wire": wire.name, "peer": request.remote})
                return refused.response
            reply = await self._decide(request, asked)
            if reply.fails_mid_stream:
                return _fault()
            return await wire.write(request, asked, reply)

        return handle

    async def token(self, request: web.Request) -> web.Response:
        """Vertex AI's sign-in (Google's token endpoint): logged like a refused call when it refuses, never scripted."""
        try:
            response = await vertex_token(request, CREDENTIALS)
        except Refused as refused:
            self._log({"path": request.path, "status": refused.response.status, "wire": "vertex_ai", "peer": request.remote})
            return refused.response
        self._log({"path": request.path, "status": 200, "wire": "vertex_ai", "signIn": True, "peer": request.remote})
        return response

    async def embeddings(self, request: web.Request) -> web.Response:
        if not self._authorized(request, "/v1/embeddings"):
            return _error(401, "Incorrect API key provided", "invalid_api_key")
        body = await request.json()
        model = str(body.get("model", ""))
        if model not in MODELS:
            return _error(404, f"The model `{model}` does not exist", "model_not_found")
        raw = body.get("input")
        inputs = [raw] if isinstance(raw, str) else list(raw or [])
        if not inputs:
            return _error(400, "'input' must not be empty", "invalid_input")
        self._log({"path": "/v1/embeddings", "status": 200, "model": model, "inputs": len(inputs), "peer": request.remote})
        data = [{"object": "embedding", "index": i, "embedding": _vector(str(text))} for i, text in enumerate(inputs)]
        return web.json_response({"object": "list", "data": data, "model": model,
                                  "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)}})

    async def models(self, request: web.Request) -> web.Response:
        if not self._authorized(request, "/v1/models"):
            return _error(401, "Incorrect API key provided", "invalid_api_key")
        return web.json_response({"object": "list", "data": [{"id": m, "object": "model"} for m in sorted(MODELS)]})

    async def other(self, request: web.Request) -> web.Response:
        self._log({"path": request.path, "status": 404, "method": request.method, "peer": request.remote})
        return _error(404, "not served by the E2E model", "not_found")

    async def control_requests(self, request: web.Request) -> web.Response:
        if request.headers.get("X-E2E-Control") != CONTROL_TOKEN:
            return _error(401, "control token required", "unauthorized")
        # ?all=1: every request since start, for the lane's egress checks across specs that each reset.
        shown = self.requests if request.query.get("all") == "1" else self.requests[self._since:]
        return web.json_response({"requests": shown})

    async def control_reset(self, request: web.Request) -> web.Response:
        if request.headers.get("X-E2E-Control") != CONTROL_TOKEN:
            return _error(401, "control token required", "unauthorized")
        self._since = len(self.requests)
        self.hold_limit_s = HOLD_LIMIT_S
        # A hold is keyed by its place in the conversation, so the next task's would count as spent.
        self.brain = ScriptedBrain()
        # A release sent while nothing was held would otherwise let the next spec's first hold through.
        self._release.clear()
        return web.json_response({"ok": True})

    async def control_holding(self, request: web.Request) -> web.Response:
        if request.headers.get("X-E2E-Control") != CONTROL_TOKEN:
            return _error(401, "control token required", "unauthorized")
        return web.json_response({"holding": self.holding.is_set()})

    async def control_release(self, request: web.Request) -> web.Response:
        """Lets the held call go; ``released`` False when none was held. A release with nothing held is dropped: kept,
        it let the next hold through unheld (R50JFIX-8: a recovered run's new attempt never held after its killed
        attempt's hold was released)."""
        if request.headers.get("X-E2E-Control") != CONTROL_TOKEN:
            return _error(401, "control token required", "unauthorized")
        held = self.holding.is_set()
        if held:
            self._release.set()
        return web.json_response({"ok": True, "released": held})

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response({"ok": True})


def _fault() -> web.Response:
    return _error(500, FAULT_MESSAGE, FAULT_CODE, "server_error")


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [((digest[i % len(digest)] / 255.0) - 0.5) for i in range(EMBEDDING_DIM)]


async def _answer(request: web.Request, body: dict[str, Any], reply: ModelReply, model: str) -> web.StreamResponse:
    usage = {"prompt_tokens": reply.usage.input_tokens, "completion_tokens": reply.usage.output_tokens,
             "total_tokens": reply.usage.input_tokens + reply.usage.output_tokens}
    calls = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
             for c in reply.tool_calls]
    finish = "tool_calls" if calls else "stop"
    base = {"id": "chatcmpl-e2e", "created": int(time.time()), "model": model}
    if reply.fails_mid_stream and not body.get("stream"):
        return _fault()
    if not body.get("stream"):
        message: dict[str, Any] = {"role": "assistant", "content": reply.content or None}
        if calls:
            message["tool_calls"] = calls
        return web.json_response({**base, "object": "chat.completion", "usage": usage,
                                  "choices": [{"index": 0, "message": message, "finish_reason": finish}]})
    chunk = {**base, "object": "chat.completion.chunk"}
    delta: dict[str, Any] = {"role": "assistant"}
    if calls:
        delta["tool_calls"] = [{"index": i, **c} for i, c in enumerate(calls)]
    else:
        delta["content"] = reply.content
    frames = [
        {**chunk, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
    ]
    if (body.get("stream_options") or {}).get("include_usage"):
        frames.append({**chunk, "choices": [], "usage": usage})
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await response.prepare(request)
    if reply.fails_mid_stream:
        await response.write(f"data: {json.dumps(frames[0])}\n\n".encode())
        error = {"error": {"message": FAULT_MESSAGE, "type": "server_error", "code": FAULT_CODE}}
        await response.write(f"data: {json.dumps(error)}\n\n".encode())
        # A client that reads past the error event still sees the stream break, never a finished answer.
        if request.transport is not None:
            request.transport.close()
        return response
    for frame in frames:
        await response.write(f"data: {json.dumps(frame)}\n\n".encode())
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


def app(model: FakeModel | None = None) -> web.Application:
    model = model or FakeModel()
    application = web.Application(middlewares=[model.watch])
    application.router.add_get("/healthz", model.health)
    application.router.add_post("/v1/chat/completions", model.chat)
    application.router.add_post("/openai/deployments/{deployment}/chat/completions", model.azure_chat)
    for wire in WIRES:
        for path in wire.routes():
            application.router.add_post(path, model.wire_handler(wire))
    application.router.add_post("/token", model.token)
    application.router.add_post("/v1/embeddings", model.embeddings)
    application.router.add_get("/v1/models", model.models)
    application.router.add_get("/__control/requests", model.control_requests)
    application.router.add_post("/__control/reset", model.control_reset)
    application.router.add_get("/__control/holding", model.control_holding)
    application.router.add_post("/__control/release", model.control_release)
    application.router.add_route("*", "/__control/canaries", model.control_canaries)
    application.router.add_route("*", "/__control/reached", model.control_reached)
    application.router.add_post("/__control/hold-limit", model.control_hold_limit)
    application.router.add_route("*", "/{tail:.*}", model.other)
    return application


if __name__ == "__main__":
    web.run_app(app(), host="0.0.0.0", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8080, print=None)
