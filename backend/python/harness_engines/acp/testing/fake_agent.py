"""A scripted fake ACP agent for tests: the agent side of ACP v1 (``schema-v1.26.0``), run in a ``FakeSandboxHandle``.

It speaks JSON-RPC 2.0 over its stdin and stdout as an ACP agent does, and its "model" is the conformance gateway's
scripted model, reached through the sandbox's egress with the placeholders the launch gave it, exactly as a real
agent reaches the model gateway. What it does:

- ``initialize``: protocol 1, ``loadSession`` (unless told not to), HTTP MCP, ``agentInfo`` with its version;
- ``session/new``: connects to the one HTTP MCP server it is given (the tool gateway) with the header it is given;
- ``session/load``: reads the session from its store under ``/state/fake-acp`` and replays it as updates;
- ``session/resume`` (when told to advertise ``sessionCapabilities.resume``): the same without the replay;
- ``session/prompt``: runs the shared agent loop. Its tools are opencode-like (``read``, ``glob``, ``grep``,
  ``write``, ``edit``, ``bash``, ``skill``, ``todowrite``) minus those its config denies, plus every gateway tool as
  ``tools_<name>``; before each tool it asks ``session/request_permission`` (a cautious agent: the client's policy
  answers); ``write``/``read`` go through the client's ``fs/*`` when offered; the system prompt is its base prompt
  and the instructions file;
- ``session/cancel``: cancels the running prompt, which answers ``cancelled``.

``Behaviour`` (``/state/fake-acp/behaviour.json``, written by a test before ``open``) makes it misbehave: crash
mid-turn, ignore a cancel, report another version or protocol, offer no HTTP MCP or no ``loadSession``, write raw
lines (hostile output), or call ``fs/*`` on a path of the test's choosing at the start of a prompt.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import posixpath
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.engines.jsonview import as_int, as_list, as_object, as_str
from harness_core.testing.engines.binary import sha256sum_program
from harness_core.testing.engines.loop import (
    ModelEndpoint,
    ModelRefused,
    ToolOutcome,
    call_gateway_tool,
    list_gateway_tools,
    run_agent_loop,
)
from harness_core.testing.engines.script import ChatMessage, ModelUsage, ToolCall

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeSandboxHandle

BINARY_PATH = "/usr/local/bin/fake-acp"
VERSION = "0.4.2"
DIGEST = "sha256:" + "a" * 64
STORE = "/state/fake-acp"
BEHAVIOUR_PATH = f"{STORE}/behaviour.json"
CONFIG_ENV = "FAKE_ACP_CONFIG"
TOKEN_ENV = "FAKE_ACP_TOKEN"
MODEL_URL_ENV = "FAKE_ACP_MODEL_URL"
MODEL_ENV = "FAKE_ACP_MODEL"
INSTRUCTIONS_PATH = "/state/acp/INSTRUCTIONS.md"
BASE_PROMPT = "You are a fake ACP coding agent working in the workspace folder."
WORKDIR = "/workspace"
TOOLS: Mapping[str, str] = {
    "read": "read",
    "glob": "search",
    "grep": "search",
    "write": "edit",
    "edit": "edit",
    "bash": "execute",
    "skill": "other",
    "todowrite": "other",
}
MAX_STEPS = 20
# The context the fake's provider counts past which it compacts on its own (ACP ``compaction_update``).
COMPACT_AT = 200_000
CONTEXT_SIZE = 256_000


@dataclass
class Behaviour:
    crash_after_updates: int | None = None
    ignore_cancel: bool = False
    agent_version: str | None = None
    protocol_version: int = 1
    http_mcp: bool = True
    load_session: bool = True
    resume_session: bool = False
    # False: the prompt's answer carries no ``usage`` (DeepSeek Harness's does not).
    prompt_usage: bool = True
    raw_lines: tuple[str, ...] = ()
    raw_on_prompt: tuple[str, ...] = ()
    fs_read: str | None = None
    fs_write: str | None = None
    agent_info: bool = True
    request_on_init: str | None = None
    ask_on_prompt: Mapping[str, JsonValue] | None = None
    requests_on_prompt: tuple[str, ...] = ()

    @staticmethod
    def read(sandbox: FakeSandboxHandle) -> Behaviour:
        raw = sandbox.files.get(BEHAVIOUR_PATH)
        if raw is None:
            return Behaviour()
        obj = as_object(json.loads(raw))
        crash = obj.get("crashAfterUpdates")
        return Behaviour(
            crash_after_updates=crash if isinstance(crash, int) else None,
            ignore_cancel=obj.get("ignoreCancel") is True,
            agent_version=as_str(obj.get("agentVersion")) or None,
            protocol_version=as_int(obj.get("protocolVersion")) or 1,
            http_mcp=obj.get("httpMcp") is not False,
            load_session=obj.get("loadSession") is not False,
            resume_session=obj.get("resumeSession") is True,
            prompt_usage=obj.get("promptUsage") is not False,
            raw_lines=tuple(as_str(line) for line in as_list(obj.get("rawLines"))),
            raw_on_prompt=tuple(as_str(line) for line in as_list(obj.get("rawOnPrompt"))),
            fs_read=as_str(obj.get("fsRead")) or None,
            fs_write=as_str(obj.get("fsWrite")) or None,
            agent_info=obj.get("agentInfo") is not False,
            request_on_init=as_str(obj.get("requestOnInit")) or None,
            ask_on_prompt=as_object(obj.get("askOnPrompt")) or None,
            requests_on_prompt=tuple(as_str(m) for m in as_list(obj.get("requestsOnPrompt"))),
        )


class _Crash(Exception):
    pass


@dataclass
class _Session:
    id: str
    history: list[ChatMessage] = field(default_factory=list[ChatMessage])
    tools_url: str = ""
    tool_headers: dict[str, str] = field(default_factory=dict[str, str])
    gateway_tools: list[str] = field(default_factory=list[str])
    prompt: asyncio.Task[JsonValue] | None = None


@dataclass
class FakeAcpAgent:
    """The ``fake-acp`` program. ``install`` puts it, and a ``sha256sum`` that vouches for it, in a sandbox."""

    version: str = VERSION
    connections: list[_Connection] = field(default_factory=list["_Connection"])

    async def program(self, ctx: FakeProcessContext) -> int:
        connection = _Connection(self, ctx, Behaviour.read(ctx.handle))
        self.connections.append(connection)
        return await connection.serve()


class _Connection:
    def __init__(self, agent: FakeAcpAgent, ctx: FakeProcessContext, behaviour: Behaviour) -> None:
        self.agent = agent
        self.ctx = ctx
        self.behaviour = behaviour
        self.sessions: dict[str, _Session] = {}
        self.client_fs: dict[str, bool] = {"read": False, "write": False}
        self.pending: dict[int, asyncio.Future[JsonValue]] = {}
        self.next_id = 0
        self.updates = 0
        self.received: list[Mapping[str, JsonValue]] = []
        self.config_options: dict[str, JsonValue] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.crashed = asyncio.Event()
        config = as_object(json.loads(ctx.env.get(CONFIG_ENV, "{}")))
        rules = as_object(config.get("permission"))
        self.native = [name for name in TOOLS if rules.get(name, "deny") == "allow"]
        self.endpoint = ModelEndpoint(
            ctx.env.get(MODEL_URL_ENV, ""),
            ctx.env.get(MODEL_ENV, ENGINE_MODEL_ID),
            {"authorization": f"Bearer {ctx.env.get(TOKEN_ENV, '')}"},
        )

    async def serve(self) -> int:
        for line in self.behaviour.raw_lines:
            await self.ctx.emit(line.encode() + b"\n")
        reader = asyncio.get_running_loop().create_task(self._read())
        crashed = asyncio.get_running_loop().create_task(self.crashed.wait())
        try:
            done, _ = await asyncio.wait({reader, crashed}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # A killed process takes its work with it.
            for task in (reader, crashed, *self.tasks):
                task.cancel()
        return 1 if crashed in done else 0

    async def _read(self) -> None:
        buffer = b""
        while chunk := await self.ctx.read_stdin():
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                if line.strip():
                    self._dispatch(as_object(json.loads(line)))

    def _dispatch(self, message: Mapping[str, JsonValue]) -> None:
        self.received.append(message)
        method = message.get("method")
        if method is None:
            raw_id = message.get("id")
            future = self.pending.pop(raw_id, None) if isinstance(raw_id, int) else None
            if future is not None and not future.done():
                future.set_result(message)
            return
        params = as_object(message.get("params"))
        if "id" not in message:
            if method == "session/cancel":
                session = self.sessions.get(as_str(params.get("sessionId")))
                if session is not None and session.prompt is not None and not self.behaviour.ignore_cancel:
                    session.prompt.cancel()
            return
        task = asyncio.get_running_loop().create_task(self._answer(message.get("id"), as_str(method), params))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def send(self, message: dict[str, JsonValue]) -> None:
        await self.ctx.emit(json.dumps({"jsonrpc": "2.0", **message}).encode() + b"\n")

    async def request(self, method: str, params: dict[str, JsonValue]) -> Mapping[str, JsonValue]:
        self.next_id += 1
        future: asyncio.Future[JsonValue] = asyncio.get_running_loop().create_future()
        self.pending[self.next_id] = future
        await self.send({"id": self.next_id, "method": method, "params": params})
        return as_object(await future)

    async def update(self, session: _Session, update: dict[str, JsonValue]) -> None:
        self.updates += 1
        crash = self.behaviour.crash_after_updates
        if crash is not None and self.updates > crash:
            self.crashed.set()
            raise _Crash
        await self.send({"method": "session/update", "params": {"sessionId": session.id, "update": update}})

    async def _answer(self, request_id: JsonValue, method: str, params: Mapping[str, JsonValue]) -> None:
        try:
            result = await self._handle(method, params)
        except _Crash:
            return
        except asyncio.CancelledError:
            raise
        except _Refused as refused:
            await self.send({"id": request_id, "error": {"code": refused.code, "message": refused.message}})
            return
        await self.send({"id": request_id, "result": result})

    async def _handle(self, method: str, params: Mapping[str, JsonValue]) -> JsonValue:
        if method == "initialize":
            return await self._initialize(params)
        if method == "session/new":
            return await self._new(params)
        if method == "session/load":
            return await self._load(params)
        if method == "session/resume" and self.behaviour.resume_session:
            return await self._load(params, replay=False)
        if method == "session/prompt":
            return await self._prompt(params)
        if method == "session/set_config_option":
            self.config_options[as_str(params.get("configId"))] = params.get("value")
            return {"configOptions": []}
        raise _Refused(-32601, "method not found")

    async def _initialize(self, params: Mapping[str, JsonValue]) -> JsonValue:
        fs = as_object(as_object(params.get("clientCapabilities")).get("fs"))
        self.client_fs = {"read": fs.get("readTextFile") is True, "write": fs.get("writeTextFile") is True}
        if self.behaviour.request_on_init:
            reply = await self.request(self.behaviour.request_on_init, {})
            self.received.append({"earlyReply": reply})  # pyright: ignore[reportArgumentType]
        result: dict[str, JsonValue] = {
            "protocolVersion": self.behaviour.protocol_version,
            "agentCapabilities": {
                "loadSession": self.behaviour.load_session,
                "mcpCapabilities": {"http": self.behaviour.http_mcp, "sse": False},
                "promptCapabilities": {"image": False, "audio": False, "embeddedContext": False},
                "sessionCapabilities": {"resume": {}} if self.behaviour.resume_session else {},
            },
            "authMethods": [],
        }
        if self.behaviour.agent_info:
            result["agentInfo"] = {"name": "fake-acp", "version": self.behaviour.agent_version or self.agent.version}
        return result

    async def _connect(self, session: _Session, params: Mapping[str, JsonValue]) -> None:
        for server in as_list(params.get("mcpServers")):
            entry = as_object(server)
            if as_str(entry.get("type")) != "http":
                continue
            session.tools_url = as_str(entry.get("url"))
            session.tool_headers = {
                as_str(as_object(h).get("name")).lower(): as_str(as_object(h).get("value"))
                for h in as_list(entry.get("headers"))
            }
            prefix = as_str(entry.get("name"))
            listed = await list_gateway_tools(self.ctx.handle.egress, session.tools_url, session.tool_headers)
            session.gateway_tools = [f"{prefix}_{name}" for name, _ in listed]

    async def _new(self, params: Mapping[str, JsonValue]) -> JsonValue:
        session = _Session(id=f"fake-session-{len(self.sessions) + 1}")
        await self._connect(session, params)
        self.sessions[session.id] = session
        self._store(session)
        return {"sessionId": session.id}

    async def _load(self, params: Mapping[str, JsonValue], *, replay: bool = True) -> JsonValue:
        session_id = as_str(params.get("sessionId"))
        raw = self.ctx.handle.files.get(f"{STORE}/sessions/{session_id}.json")
        if raw is None:
            raise _Refused(-32002, "resource not found")
        session = _Session(id=session_id, history=[ChatMessage.from_json(m) for m in as_list(json.loads(raw))])
        await self._connect(session, params)
        self.sessions[session.id] = session
        for message in session.history if replay else ():
            if message.role in ("user", "assistant") and message.content:
                kind = "user_message_chunk" if message.role == "user" else "agent_message_chunk"
                await self.update(session, {"sessionUpdate": kind, "content": {"type": "text", "text": message.content}})
        return {}

    def _store(self, session: _Session) -> None:
        body = json.dumps([m.to_json() for m in session.history]).encode()
        self.ctx.write_file(f"{STORE}/sessions/{session.id}.json", body)

    def _system(self) -> str:
        raw = self.ctx.handle.files.get(INSTRUCTIONS_PATH)
        return BASE_PROMPT + ("\n" + raw.decode() if raw else "")

    async def _prompt(self, params: Mapping[str, JsonValue]) -> JsonValue:
        session = self.sessions.get(as_str(params.get("sessionId")))
        if session is None:
            raise _Refused(-32602, "unknown session")
        for line in self.behaviour.raw_on_prompt:
            await self.ctx.emit(line.encode() + b"\n")
        await self._probe_fs(session)
        text = "\n".join(
            as_str(as_object(block).get("text")) for block in as_list(params.get("prompt"))
            if as_str(as_object(block).get("type")) == "text"
        )
        session.history.append(ChatMessage("user", text))
        hooks = _Hooks(self, session)
        runner = asyncio.get_running_loop().create_task(
            run_agent_loop(
                egress=self.ctx.handle.egress,
                endpoint=self.endpoint,
                system=self._system(),
                history=session.history,
                tools=(*self.native, *session.gateway_tools),
                run_tool=lambda call: self._run_tool(session, call),
                hooks=hooks,
                max_steps=MAX_STEPS,
            )
        )
        session.prompt = runner  # pyright: ignore[reportAttributeAccessIssue]
        try:
            result = await runner
        except ModelRefused as refused:
            self._store(session)
            # As Gemini CLI 0.63 does: the provider's refusal fails the prompt, the gateway's tag in its message.
            raise _Refused(-32603, f"Internal error: the provider refused the call [gateway:{refused.code}]") from None
        except asyncio.CancelledError:
            if runner.cancelled():
                self._store(session)
                return self._answer_of("cancelled", hooks)
            raise
        finally:
            session.prompt = None
        self._store(session)
        stop = "end_turn" if result.reason == "completed" else "max_turn_requests"
        return self._answer_of(stop, hooks)

    def _answer_of(self, stop: str, hooks: _Hooks) -> JsonValue:
        return {"stopReason": stop, "usage": hooks.usage()} if self.behaviour.prompt_usage else {"stopReason": stop}

    async def _probe_fs(self, session: _Session) -> None:
        """A hostile agent's ``fs/*`` call on a path of the test's choosing, an ask for a tool call of the test's
        choosing and requests of methods the client does not serve; each answer is kept for the test."""
        if self.behaviour.ask_on_prompt is not None:
            reply = await self.request(
                "session/request_permission",
                {
                    "sessionId": session.id,
                    "toolCall": dict(self.behaviour.ask_on_prompt),
                    "options": [
                        {"optionId": "always", "name": "Always allow", "kind": "allow_always"},
                        {"optionId": "once", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            )
            self.received.append({"askReply": reply})  # pyright: ignore[reportArgumentType]
        for method in self.behaviour.requests_on_prompt:
            reply = await self.request(method, {"sessionId": session.id})
            self.received.append({"methodReply": reply})  # pyright: ignore[reportArgumentType]
        if self.behaviour.fs_read:
            reply = await self.request("fs/read_text_file", {"sessionId": session.id, "path": self.behaviour.fs_read})
            self.received.append({"fsReadReply": reply})  # pyright: ignore[reportArgumentType]
        if self.behaviour.fs_write:
            reply = await self.request(
                "fs/write_text_file", {"sessionId": session.id, "path": self.behaviour.fs_write, "content": "pwned"}
            )
            self.received.append({"fsWriteReply": reply})  # pyright: ignore[reportArgumentType]

    async def _ask(self, session: _Session, call: ToolCall) -> bool:
        reply = await self.request(
            "session/request_permission",
            {
                "sessionId": session.id,
                "toolCall": {"toolCallId": call.id, "title": call.name, "kind": _kind(call.name), **_locations(call)},
                "options": [
                    {"optionId": "always", "name": "Always allow", "kind": "allow_always"},
                    {"optionId": "once", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "no", "name": "Reject", "kind": "reject_once"},
                ],
            },
        )
        outcome = as_object(as_object(reply.get("result")).get("outcome"))
        return as_str(outcome.get("outcome")) == "selected" and as_str(outcome.get("optionId")) in ("once", "always")

    async def _run_tool(self, session: _Session, call: ToolCall) -> ToolOutcome:
        if not await self._ask(session, call):
            return ToolOutcome("The user rejected permission to use this tool call.", is_error=True)
        await self.update(session, {"sessionUpdate": "tool_call_update", "toolCallId": call.id, "status": "in_progress"})
        args = call.args()
        if call.name in ("write", "edit"):
            path = posixpath.join(WORKDIR, as_str(args.get("filePath")))
            content = as_str(args.get("content"))
            if self.client_fs["write"]:
                reply = await self.request("fs/write_text_file", {"sessionId": session.id, "path": path, "content": content})
                if "error" in reply:
                    return ToolOutcome("write refused", is_error=True)
            else:
                self.ctx.write_file(path, content.encode())
            return ToolOutcome("written")
        if call.name == "read":
            path = posixpath.join(WORKDIR, as_str(args.get("filePath")))
            if self.client_fs["read"]:
                reply = await self.request("fs/read_text_file", {"sessionId": session.id, "path": path})
                return ToolOutcome(as_str(as_object(reply.get("result")).get("content")), is_error="error" in reply)
            data = self.ctx.handle.files.get(path)
            return ToolOutcome(data.decode() if data is not None else "missing", is_error=data is None)
        if call.name == "todowrite":
            entries: list[JsonValue] = [
                {"content": as_str(as_object(t).get("content")), "priority": "medium", "status": as_str(as_object(t).get("status"))}
                for t in as_list(args.get("todos"))
            ]
            await self.update(session, {"sessionUpdate": "plan", "entries": entries})
            return ToolOutcome("planned")
        if call.name in TOOLS:
            return ToolOutcome("(no output)")
        name = call.name.split("_", 1)[1] if "_" in call.name else call.name
        return await call_gateway_tool(
            self.ctx.handle.egress, session.tools_url, session.tool_headers, name, args, timeout_s=3600
        )


class _Refused(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _kind(name: str) -> str:
    return TOOLS.get(name, "other")


def _locations(call: ToolCall) -> dict[str, JsonValue]:
    if call.name not in ("write", "edit", "read"):
        return {}
    with contextlib.suppress(ValueError):
        path = as_str(call.args().get("filePath"))
        if path:
            return {"locations": [{"path": posixpath.join(WORKDIR, path)}]}
    return {}


class _Hooks:
    def __init__(self, connection: _Connection, session: _Session) -> None:
        self._c = connection
        self._s = session
        self._messages = 0
        self._input = 0
        self._output = 0
        self._compactions = 0

    def usage(self) -> JsonValue:
        return {"totalTokens": self._input + self._output, "inputTokens": self._input, "outputTokens": self._output}

    async def step_started(self) -> None:
        return None

    async def text(self, text: str) -> None:
        self._messages += 1
        await self._c.update(
            self._s,
            {"sessionUpdate": "agent_message_chunk", "messageId": f"{self._s.id}-m{len(self._s.history)}",
             "content": {"type": "text", "text": text}},
        )

    async def tool_started(self, call: ToolCall) -> None:
        update: dict[str, JsonValue] = {
            "sessionUpdate": "tool_call",
            "toolCallId": call.id,
            "title": call.name,
            "kind": _kind(call.name),
            "status": "pending",
            **_locations(call),
        }
        with contextlib.suppress(ValueError):
            update["rawInput"] = call.args()
        await self._c.update(self._s, update)

    async def tool_finished(self, call: ToolCall, outcome: ToolOutcome) -> None:
        status: Literal["completed", "failed"] = "failed" if outcome.is_error else "completed"
        update: dict[str, JsonValue] = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": call.id,
            "status": status,
            "content": [{"type": "content", "content": {"type": "text", "text": outcome.content}}],
        }
        if call.name in ("write", "edit") and not outcome.is_error:
            with contextlib.suppress(ValueError):
                args = call.args()
                update["content"] = [
                    {"type": "diff", "path": posixpath.join(WORKDIR, as_str(args.get("filePath"))), "oldText": None,
                     "newText": as_str(args.get("content"))}
                ]
        if call.name == "bash":
            update["rawOutput"] = {"exitCode": 1 if outcome.is_error else 0}
        await self._c.update(self._s, update)

    async def step_finished(self, reason: Literal["tool-calls", "stop"], usage: ModelUsage) -> None:
        self._input += usage.input_tokens
        self._output += usage.output_tokens
        await self._c.update(self._s, {"sessionUpdate": "usage_update", "used": usage.input_tokens, "size": CONTEXT_SIZE})
        if usage.input_tokens > COMPACT_AT:
            self._compactions += 1
            compaction = f"{self._s.id}-c{self._compactions}"
            for status in ("in_progress", "completed"):
                await self._c.update(
                    self._s, {"sessionUpdate": "compaction_update", "compactionId": compaction, "status": status}
                )


def install(sandbox: FakeSandboxHandle, *, version: str = VERSION, digest: str = DIGEST) -> FakeAcpAgent:
    fake = FakeAcpAgent(version=version)
    sandbox.install("sha256sum", sha256sum_program({BINARY_PATH: digest}.get))
    sandbox.install(posixpath.basename(BINARY_PATH), fake.program)
    return fake


def write_behaviour(sandbox: FakeSandboxHandle, behaviour: Mapping[str, JsonValue]) -> None:
    sandbox.put_file(BEHAVIOUR_PATH, json.dumps(behaviour).encode())


__all__ = [
    "BINARY_PATH",
    "BEHAVIOUR_PATH",
    "CONFIG_ENV",
    "DIGEST",
    "INSTRUCTIONS_PATH",
    "MODEL_ENV",
    "MODEL_URL_ENV",
    "TOKEN_ENV",
    "VERSION",
    "Behaviour",
    "FakeAcpAgent",
    "install",
    "write_behaviour",
]
