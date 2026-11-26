"""A recorded fake of ``opencode`` v1.18.34 (``serve``, ``export``, ``import``) for tests.

It runs inside a ``FakeSandboxHandle`` as the ``opencode`` program. The HTTP
routes, SSE payload shapes and event order are the ones captured from the real
binary (``research/engines/opencode-probes/captures``); ``test_fake_fidelity``
compares the fake's sequences with the captures. Behaviour follows F4: tools
hidden by permission rules, MCP tools named ``<server>_<tool>``, Basic auth,
per-session permission rules appended after the config's, ``permission.asked``
held until ``/permission/:id/reply``, a ``task`` call runs a child session on
the same stream, export/import of ``{info, messages[{info, parts}]}``, and an
interrupted tool replayed as ``[Tool execution was interrupted]``.

The model is the conformance gateway's scripted model, reached through the
sandbox's egress exactly as opencode would reach the model gateway.
"""

from __future__ import annotations

import asyncio
import base64
import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

import h11

from harness_core.engines.jsonview import as_int, as_list, as_object, as_str
from harness_core.testing.engines.binary import sha256sum_program
from harness_core.testing.engines.loop import (
    ModelEndpoint,
    ModelRefused,
    ToolOutcome,
    call_gateway_tool,
    call_model,
    list_gateway_tools,
    run_agent_loop,
)
from harness_core.testing.engines.script import (
    CONTEXT_OVERFLOW,
    ChatMessage,
    ModelUsage,
    ToolCall,
)
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS
from harness_engines.opencode.launch import BINARY_PATH
from harness_engines.opencode.permissions import Rule, evaluate, flatten, visible_tools

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.engines.types import JsonValue
    from harness_core.sandbox import ByteStream
    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeSandboxHandle

VERSION = "1.18.34"
DIGEST = "sha256:" + "5" * 64
STORE = "opencode/fake-store.json"
BUILTIN_TOOLS = ("bash", "edit", "glob", "grep", "read", "task", "todowrite", "webfetch", "write")
ASK_CLIENTS = frozenset({"cli", "app", "desktop"})
INTERRUPTED = "[Tool execution was interrupted]"
REJECTED = "The user rejected permission to use this specific tool call."
_ENV_REF = re.compile(r"\{env:([A-Za-z0-9_]+)\}")
_EPOCH_MS = 1_790_938_900_000
SUMMARY_PROMPT = "Provide a detailed summary of the conversation above so it can continue from the summary."


def _substitute(value: str, env: Mapping[str, str]) -> str:
    return _ENV_REF.sub(lambda m: env.get(m.group(1), ""), value)


@dataclass
class _Store:
    """opencode's data dir, as a JSON file in the sandbox so ``export``/``import`` share it."""

    path: str
    sessions: dict[str, dict[str, JsonValue]] = field(default_factory=dict[str, dict[str, "JsonValue"]])
    counters: dict[str, int] = field(default_factory=dict[str, int])

    @staticmethod
    def load(sandbox: FakeSandboxHandle, data_home: str) -> _Store:
        path = posixpath.join(data_home, STORE)
        store = _Store(path=path)
        raw = sandbox.files.get(path)
        if raw is not None:
            body = as_object(json.loads(raw))
            store.sessions = {
                k: dict(as_object(v)) for k, v in as_object(body.get("sessions")).items()
            }
            store.counters = {k: v for k, v in as_object(body.get("counters")).items() if isinstance(v, int)}
        return store

    def save(self, sandbox: FakeSandboxHandle) -> None:
        body: dict[str, JsonValue] = {"sessions": self.sessions, "counters": dict(self.counters)}
        sandbox.files[self.path] = json.dumps(body).encode()

    def next_id(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}_{self.counters[prefix]:03d}"

    def now(self) -> int:
        self.counters["clock"] = self.counters.get("clock", 0) + 1
        return _EPOCH_MS + self.counters["clock"]

    def messages(self, session_id: str) -> list[dict[str, JsonValue]]:
        return cast("list[dict[str, JsonValue]]", self.sessions[session_id]["messages"])

    def bump_past(self, ident: str) -> None:
        prefix, _, number = ident.partition("_")
        if number.isdigit():
            self.counters[prefix] = max(self.counters.get(prefix, 0), int(number))


@dataclass
class FakeOpencode:
    """The ``opencode`` program. Install with ``sandbox.install("opencode", fake.program)``."""

    version: str = VERSION
    servers: list[FakeOpencodeServer] = field(default_factory=list["FakeOpencodeServer"])

    async def program(self, ctx: FakeProcessContext) -> int:
        command = ctx.argv[1] if len(ctx.argv) > 1 else ""
        data_home = ctx.env.get("XDG_DATA_HOME", "/state")
        if command == "serve":
            return await self._serve(ctx, data_home)
        store = _Store.load(ctx.handle, data_home)
        if command == "export" and len(ctx.argv) > 2:
            export = _export(store, ctx.argv[2])
            if export is None:
                await ctx.emit(b"Session not found\n", stderr=True)
                return 1
            await ctx.emit(export)
            return 0
        if command == "import" and len(ctx.argv) > 2:
            raw = ctx.handle.files.get(ctx.argv[2])
            if raw is None:
                return 1
            body = as_object(json.loads(raw))
            info = as_object(body.get("info"))
            session_id = as_str(info.get("id"))
            store.sessions[session_id] = {"info": dict(info), "messages": list(as_list(body.get("messages")))}
            store.bump_past(session_id)
            for message in as_list(body.get("messages")):
                message_info = as_object(as_object(message).get("info"))
                store.bump_past(as_str(message_info.get("id")))
                for part in as_list(as_object(message).get("parts")):
                    store.bump_past(as_str(as_object(part).get("id")))
            store.save(ctx.handle)
            return 0
        await ctx.emit(f"unknown command {command}\n".encode(), stderr=True)
        return 2

    async def shell(self, ctx: FakeProcessContext) -> int:
        """``sh -c <script> <name> <session> <path>`` for the one script the adapter runs, the export into a file;
        any other script is the fake sandbox's own ``sh`` (the harness's probes, CF-09's stall)."""
        script = ctx.argv[2] if len(ctx.argv) > 2 else ""
        if "opencode export" not in script:
            return await FAKE_PROGRAMS["sh"](ctx)
        if len(ctx.argv) < 6:
            await ctx.emit(b"sh: the export script takes a session and a path\n", stderr=True)
            return 127
        export = _export(_Store.load(ctx.handle, ctx.env.get("XDG_DATA_HOME", "/state")), ctx.argv[4])
        if export is None:
            await ctx.emit(b"Session not found\n", stderr=True)
            return 1
        ctx.handle.files[ctx.argv[5]] = export
        return 0

    async def cat(self, ctx: FakeProcessContext) -> int:
        content = ctx.handle.files.get(ctx.argv[1]) if len(ctx.argv) > 1 else None
        if content is None:
            await ctx.emit(b"cat: no such file\n", stderr=True)
            return 1
        await ctx.emit(content)
        return 0

    async def _serve(self, ctx: FakeProcessContext, data_home: str) -> int:
        port = int(ctx.argv[ctx.argv.index("--port") + 1])
        config = as_object(json.loads(ctx.env.get("OPENCODE_CONFIG_CONTENT", "{}")))
        server = FakeOpencodeServer(
            sandbox=ctx.handle,
            env=ctx.env,
            config=config,
            version=self.version,
            store=_Store.load(ctx.handle, data_home),
        )
        self.servers.append(server)
        await server.connect_mcp()
        ctx.handle.listen(port, server.handle)
        try:
            await asyncio.Event().wait()
        finally:
            ctx.handle.unlisten(port)
            await server.shutdown()
        return 0


@dataclass
class _Turn:
    session_id: str
    task: asyncio.Task[None]
    assistant: dict[str, JsonValue] | None = None


class FakeOpencodeServer:
    def __init__(
        self,
        *,
        sandbox: FakeSandboxHandle,
        env: Mapping[str, str],
        config: Mapping[str, JsonValue],
        version: str,
        store: _Store,
    ) -> None:
        self.sandbox = sandbox
        self._env = env
        self._config = config
        self.version = version
        self.store = store
        self._subscribers: list[asyncio.Queue[dict[str, JsonValue] | None]] = []
        self._turns: dict[str, _Turn] = {}
        self._asks: dict[str, asyncio.Future[str]] = {}
        self._mcp_tools: list[str] = []
        self._events = 0
        self.requests: list[tuple[str, str]] = []
        password = env.get("OPENCODE_SERVER_PASSWORD")
        self._auth = (
            None
            if password is None
            else "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
        )

    # ---- configuration as opencode reads it

    def _mcp(self) -> tuple[str, Mapping[str, JsonValue]]:
        servers = as_object(self._config.get("mcp"))
        name = next(iter(servers), "")
        return name, as_object(servers.get(name))

    def _mcp_headers(self) -> dict[str, str]:
        _, server = self._mcp()
        return {k: _substitute(as_str(v), self._env) for k, v in as_object(server.get("headers")).items()}

    async def connect_mcp(self) -> None:
        name, server = self._mcp()
        url = as_str(server.get("url"))
        if not url:
            return
        tools = await list_gateway_tools(self.sandbox.egress, url, self._mcp_headers())
        self._mcp_tools = [f"{name}_{tool}" for tool, _ in tools]

    def _model(self, session_id: str) -> ModelEndpoint:
        model_ref = as_str(self._config.get("model"))
        provider_id, _, model = model_ref.partition("/")
        options = as_object(as_object(as_object(self._config.get("provider")).get(provider_id)).get("options"))
        headers = {
            "authorization": f"Bearer {_substitute(as_str(options.get('apiKey')), self._env)}",
            "x-opencode-session-id": session_id,
            "x-session-id": session_id,
            "x-session-affinity": session_id,
        }
        for key, value in as_object(options.get("headers")).items():
            headers[key.lower()] = _substitute(as_str(value), self._env)
        return ModelEndpoint(as_str(options.get("baseURL")), model, headers)

    def _skills(self) -> list[str]:
        paths = as_list(as_object(self._config.get("skills")).get("paths"))
        found: list[str] = []
        for raw in paths:
            root = _substitute(as_str(raw), self._env).rstrip("/") + "/"
            found.extend(p for p in sorted(self.sandbox.files) if p.startswith(root) and p.endswith("/SKILL.md"))
        return found

    def _rules(self, session_id: str) -> list[Rule]:
        rules = [Rule("*", "*", "allow")]
        rules.extend(flatten(as_object(self._config.get("permission"))))
        info = as_object(self.store.sessions[session_id]["info"])
        for rule in as_list(info.get("permission")):
            entry = as_object(rule)
            action = as_str(entry.get("action"))
            if action in ("allow", "ask", "deny"):
                rules.append(Rule(as_str(entry.get("permission")), as_str(entry.get("pattern")), action))
        return rules

    def _tools(self, session_id: str, *, child: bool) -> list[str]:
        universe = [*BUILTIN_TOOLS, *self._mcp_tools]
        if self._skills():
            universe.append("skill")
        if self._env.get("OPENCODE_CLIENT", "cli") in ASK_CLIENTS and not child:
            universe.append("question")
        if child:
            universe = [t for t in universe if t not in ("task", "todowrite")]
        return visible_tools(self._rules(session_id), universe)

    def _agent(self) -> Mapping[str, JsonValue]:
        name = as_str(self._config.get("default_agent")) or "build"
        return as_object(as_object(self._config.get("agent")).get(name))

    def _system(self, extra: str) -> str:
        parts = [as_str(self._agent().get("prompt")) or "STOCK-PROMPT"]
        parts.append("<env>\n  Working directory: /workspace\n</env>")
        skills = self._skills()
        if skills:
            listed = "".join(f"  <skill><location>{p}</location></skill>\n" for p in skills)
            parts.append(f"<available_skills>\n{listed}</available_skills>")
        if extra:
            parts.append(extra)
        return "\n".join(parts)

    # ---- events

    async def publish(self, kind: str, properties: Mapping[str, JsonValue]) -> None:
        self._events += 1
        event: dict[str, JsonValue] = {"id": f"evt_{self._events:03d}", "type": kind, "properties": dict(properties)}
        for queue in self._subscribers:
            queue.put_nowait(event)

    async def emit_part(self, session_id: str, part: Mapping[str, JsonValue]) -> None:
        await self.publish("message.part.updated", {"sessionID": session_id, "part": dict(part), "time": self.store.now()})

    async def emit_message(self, session_id: str, info: Mapping[str, JsonValue]) -> None:
        await self.publish("message.updated", {"sessionID": session_id, "info": dict(info)})

    async def emit_status(self, session_id: str, status: str) -> None:
        await self.publish("session.status", {"sessionID": session_id, "status": {"type": status}})

    # ---- sessions

    async def create_session(
        self, *, title: str, parent: str | None = None, permission: Sequence[JsonValue] = ()
    ) -> dict[str, JsonValue]:
        session_id = self.store.next_id("ses")
        now = self.store.now()
        info: dict[str, JsonValue] = {
            "id": session_id,
            "version": self.version,
            "directory": "/workspace",
            "title": title,
            "time": {"created": now, "updated": now},
        }
        if parent is not None:
            info["parentID"] = parent
        if permission:
            info["permission"] = list(permission)
        self.store.sessions[session_id] = {"info": info, "messages": []}
        self.store.save(self.sandbox)
        await self.publish("session.created", {"sessionID": session_id, "info": info})
        return info

    def history(self, session_id: str) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        for message in self.store.messages(session_id):
            info = as_object(message.get("info"))
            parts = [as_object(p) for p in as_list(message.get("parts"))]
            text = "".join(as_str(p.get("text")) for p in parts if as_str(p.get("type")) == "text")
            if as_str(info.get("role")) == "user":
                out.append(ChatMessage("user", text))
                continue
            tools = [p for p in parts if as_str(p.get("type")) == "tool"]
            if not tools:
                if text:
                    out.append(ChatMessage("assistant", text))
                continue
            calls = tuple(
                ToolCall(as_str(p.get("callID")), as_str(p.get("tool")), json.dumps(as_object(as_object(p.get("state")).get("input"))))
                for p in tools
            )
            out.append(ChatMessage("assistant", text, calls))
            for p in tools:
                state = as_object(p.get("state"))
                status = as_str(state.get("status"))
                result = (
                    as_str(state.get("output"))
                    if status == "completed"
                    else as_str(state.get("error"))
                    if status == "error"
                    else INTERRUPTED
                )
                out.append(ChatMessage("tool", result, tool_call_id=as_str(p.get("callID"))))
        return out

    async def prompt(self, session_id: str, body: Mapping[str, JsonValue]) -> None:
        if session_id in self._turns:
            raise RuntimeError("session is busy")
        task = asyncio.get_running_loop().create_task(self._run_prompt(session_id, body))
        self._turns[session_id] = _Turn(session_id, task)
        task.add_done_callback(lambda _t: self._turns.pop(session_id, None))

    async def _run_prompt(self, session_id: str, body: Mapping[str, JsonValue]) -> None:
        text = "".join(as_str(as_object(p).get("text")) for p in as_list(body.get("parts")))
        await self._user_message(session_id, text)
        await self.emit_status(session_id, "busy")
        turn = self._turns[session_id]
        system = self._system(as_str(body.get("system")))
        try:
            try:
                await self._loop(session_id, turn, system=system, child=False)
            except ModelRefused as refused:
                await self._refused(session_id, refused)
                if refused.code == CONTEXT_OVERFLOW:
                    # As opencode 1.18 does on a context overflow: compact, ask the person's message again, go on.
                    await self.summarize(session_id)
                    await self.publish("session.compacted", {"sessionID": session_id})
                    await self._user_message(session_id, text)
                    try:
                        await self._loop(session_id, turn, system=system, child=False)
                    except ModelRefused as again:
                        await self._refused(session_id, again)
        except asyncio.CancelledError:
            await self._aborted(session_id, turn)
            raise
        await self.emit_status(session_id, "idle")
        await self.publish("session.idle", {"sessionID": session_id})

    async def _user_message(self, session_id: str, text: str) -> None:
        user_id = self.store.next_id("msg")
        user: dict[str, JsonValue] = {"id": user_id, "role": "user", "sessionID": session_id, "time": {"created": self.store.now()}}
        part: dict[str, JsonValue] = {"type": "text", "text": text, "messageID": user_id, "sessionID": session_id, "id": self.store.next_id("prt")}
        self.store.messages(session_id).append({"info": user, "parts": [part]})
        self.store.save(self.sandbox)
        await self.emit_message(session_id, user)
        await self.emit_part(session_id, part)

    async def _refused(self, session_id: str, refused: ModelRefused) -> None:
        name = "ContextOverflowError" if refused.code == CONTEXT_OVERFLOW else "APIError"
        await self.publish("session.error", {"sessionID": session_id, "error": {"name": name, "data": {}}})

    async def summarize(self, session_id: str) -> bool:
        """``POST /session/{id}/summarize``: one summarising model call; the session then starts from its summary,
        an assistant message marked ``summary`` carrying the call's tokens, as opencode's compaction leaves it."""
        history = self.history(session_id)
        reply = await call_model(
            self.sandbox.egress, self._model(session_id), [*history, ChatMessage("user", SUMMARY_PROMPT)], ()
        )
        info: dict[str, JsonValue] = {
            "id": self.store.next_id("msg"),
            "role": "assistant",
            "sessionID": session_id,
            "summary": True,
            "time": {"created": self.store.now(), "completed": self.store.now()},
            "tokens": {"input": reply.usage.input_tokens, "output": reply.usage.output_tokens},
        }
        part: dict[str, JsonValue] = {
            "type": "text", "text": reply.content, "messageID": info["id"], "sessionID": session_id,
            "id": self.store.next_id("prt"),
        }
        self.store.sessions[session_id]["messages"] = [{"info": info, "parts": [part]}]
        self.store.save(self.sandbox)
        await self.emit_message(session_id, info)
        return True

    async def _loop(self, session_id: str, turn: _Turn, *, system: str, child: bool) -> str:
        steps = self._agent().get("steps")
        hooks = _Hooks(self, session_id, turn)
        result = await run_agent_loop(
            egress=self.sandbox.egress,
            endpoint=self._model(session_id),
            system=system,
            history=self.history(session_id),
            tools=self._tools(session_id, child=child),
            run_tool=lambda call: self._run_tool(session_id, turn, call, child=child),
            hooks=hooks,
            max_steps=steps if isinstance(steps, int) else 50,
        )
        return result.text

    async def _aborted(self, session_id: str, turn: _Turn) -> None:
        if turn.assistant is not None:
            info = turn.assistant
            info["error"] = {"name": "MessageAbortedError", "data": {"message": "Aborted"}}
            info["time"] = {**as_object(info.get("time")), "completed": self.store.now()}
            self.store.save(self.sandbox)
            await self.emit_message(session_id, info)
        await self.emit_status(session_id, "idle")
        await self.publish("session.idle", {"sessionID": session_id})

    async def _run_tool(self, session_id: str, turn: _Turn, call: ToolCall, *, child: bool) -> ToolOutcome:
        part = _find_part(self.store, session_id, call.id)
        args = call.args()
        if call.name not in self._tools(session_id, child=child):
            return ToolOutcome(f"Model tried to call unavailable tool '{call.name}'.", is_error=True)

        async def running(metadata: Mapping[str, JsonValue] | None = None) -> None:
            if part is None:
                return
            new_state: dict[str, JsonValue] = {"status": "running", "input": dict(args), "time": {"start": self.store.now()}}
            if metadata is not None:
                new_state["metadata"] = dict(metadata)
            part["state"] = new_state
            await self.emit_part(session_id, part)

        mcp_name, server = self._mcp()
        if call.name.startswith(f"{mcp_name}_"):
            await running()
            return await call_gateway_tool(
                self.sandbox.egress,
                as_str(server.get("url")),
                self._mcp_headers(),
                call.name[len(mcp_name) + 1 :],
                dict(args),
                timeout_s=(as_int(server.get("timeout")) or 60_000) / 1000,
            )
        if call.name == "bash":
            await running()
            command = as_str(args.get("command"))
            action = evaluate(self._rules(session_id), "bash", command)
            if action == "ask":
                reply = await self._ask(session_id, call, command)
                if reply == "reject":
                    return ToolOutcome(REJECTED, is_error=True)
                await running()
            elif action == "deny":
                return ToolOutcome(REJECTED, is_error=True)
            return ToolOutcome("(no output)")
        if call.name in ("write", "edit"):
            await running()
            path = posixpath.join("/workspace", as_str(args.get("filePath")))
            self.sandbox.files[path] = as_str(args.get("content")).encode()
            return ToolOutcome("Wrote file successfully.")
        if call.name == "task":
            child_info = await self.create_session(
                title=f"{as_str(args.get('description'))} (@{as_str(args.get('subagent_type'))} subagent)",
                parent=session_id,
            )
            child_id = as_str(child_info.get("id"))
            await running({"parentSessionId": session_id, "sessionId": child_id})
            self.store.messages(child_id).append(
                {
                    "info": {"id": self.store.next_id("msg"), "role": "user", "sessionID": child_id},
                    "parts": [{"type": "text", "text": as_str(args.get("prompt")), "id": self.store.next_id("prt")}],
                }
            )
            await self.emit_status(child_id, "busy")
            text = await self._loop(child_id, _Turn(child_id, turn.task), system=self._system(""), child=True)
            await self.emit_status(child_id, "idle")
            await self.publish("session.idle", {"sessionID": child_id})
            return ToolOutcome(f'<task id="{child_id}" state="completed">\n<task_result>\n{text}\n</task_result>\n</task>')
        await running()
        return ToolOutcome("")

    async def _ask(self, session_id: str, call: ToolCall, command: str) -> str:
        request_id = self.store.next_id("per")
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._asks[request_id] = future
        part = _find_part(self.store, session_id, call.id)
        message_id = as_str(part.get("messageID")) if part is not None else ""
        await self.publish(
            "permission.asked",
            {
                "id": request_id,
                "sessionID": session_id,
                "permission": "bash",
                "patterns": [command],
                "metadata": {"command": command},
                "always": [command.split(" ")[0] + " *"],
                "tool": {"messageID": message_id, "callID": call.id},
            },
        )
        try:
            reply = await future
        finally:
            self._asks.pop(request_id, None)
        await self.publish("permission.replied", {"sessionID": session_id, "requestID": request_id, "reply": reply})
        return reply

    # ---- HTTP

    async def handle(self, stream: ByteStream) -> None:
        connection = h11.Connection(h11.SERVER)
        try:
            while True:
                request = await _next_request(connection, stream)
                if request is None:
                    return
                method, target, headers, body = request
                self.requests.append((method, target))
                if self._auth is not None and headers.get("authorization") != self._auth:
                    await _respond(connection, stream, 401, {"error": "unauthorized"})
                elif method == "GET" and target == "/event":
                    await self._stream_events(connection, stream)
                    return
                else:
                    status, payload = await self._route(method, target, body)
                    await _respond(connection, stream, status, payload)
                if connection.our_state is h11.MUST_CLOSE:
                    return
                connection.start_next_cycle()
        except (ConnectionError, h11.ProtocolError):
            return
        finally:
            await stream.aclose()

    async def _stream_events(self, connection: h11.Connection, stream: ByteStream) -> None:
        queue: asyncio.Queue[dict[str, JsonValue] | None] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            await stream.write(
                connection.send(h11.Response(status_code=200, headers=[("content-type", "text/event-stream")])) or b""
            )
            queue.put_nowait({"id": "evt_000", "type": "server.connected", "properties": {}})
            while True:
                event = await queue.get()
                if event is None:
                    return
                await stream.write(connection.send(h11.Data(data=f"data: {json.dumps(event)}\n\n".encode())) or b"")
        finally:
            self._subscribers.remove(queue)

    async def _route(self, method: str, target: str, body: JsonValue) -> tuple[int, JsonValue]:
        parts = [p for p in target.split("?")[0].split("/") if p]
        request = as_object(body)
        if method == "GET" and parts == ["global", "health"]:
            return 200, {"healthy": True, "version": self.version}
        if method == "POST" and parts == ["session"]:
            info = await self.create_session(
                title=as_str(request.get("title")) or "New session",
                permission=as_list(request.get("permission")),
            )
            return 200, info
        if len(parts) == 3 and parts[0] == "session" and parts[1] in self.store.sessions:
            session_id = parts[1]
            if method == "POST" and parts[2] == "prompt_async":
                await self.prompt(session_id, request)
                return 204, None
            if method == "POST" and parts[2] == "abort":
                turn = self._turns.get(session_id)
                if turn is not None:
                    turn.task.cancel()
                return 200, True
            if method == "GET" and parts[2] == "message":
                return 200, self.store.messages(session_id)
            if method == "POST" and parts[2] == "summarize":
                return 200, await self.summarize(session_id)
        if method == "GET" and parts == ["mcp"]:
            # The fake connects its one MCP server as it starts (``connect_mcp``), or does not start at all.
            name, _ = self._mcp()
            return 200, {name: {"status": "connected"}} if name else {}
        if method == "POST" and len(parts) == 3 and parts[0] == "mcp" and parts[2] == "connect":
            await self.connect_mcp()
            return 200, True
        if method == "POST" and len(parts) == 3 and parts[0] == "permission" and parts[2] == "reply":
            future = self._asks.get(parts[1])
            if future is None:
                return 404, {"error": "not found"}
            future.set_result(as_str(request.get("reply")))
            return 200, True
        return 404, {"error": "not found"}

    def drop_event_streams(self) -> None:
        """Cut every ``/event`` connection mid-stream, as a crashed proxy or engine would."""
        for queue in self._subscribers:
            queue.put_nowait(None)

    async def shutdown(self) -> None:
        for turn in list(self._turns.values()):
            turn.task.cancel()


class _Hooks:
    """Agent-loop steps → opencode's message, part and status events, in the captured order."""

    def __init__(self, server: FakeOpencodeServer, session_id: str, turn: _Turn) -> None:
        self._server = server
        self._session = session_id
        self._turn = turn
        self.store = server.store
        self._message: dict[str, JsonValue] = {}
        self._parts: list[JsonValue] = []

    def _new_part(self, **fields: JsonValue) -> dict[str, JsonValue]:
        part: dict[str, JsonValue] = {
            "id": self.store.next_id("prt"),
            "messageID": as_str(self._message.get("id")),
            "sessionID": self._session,
            **fields,
        }
        self._parts.append(part)
        return part

    async def step_started(self) -> None:
        messages = self.store.messages(self._session)
        parent = as_str(as_object(as_object(messages[-1]).get("info")).get("id")) if messages else ""
        self._message = {
            "id": self.store.next_id("msg"),
            "parentID": parent,
            "role": "assistant",
            "sessionID": self._session,
            "time": {"created": self.store.now()},
        }
        self._parts = []
        messages.append({"info": self._message, "parts": self._parts})
        self._turn.assistant = self._message
        await self._server.emit_message(self._session, self._message)
        await self._server.emit_status(self._session, "busy")
        await self._server.emit_part(self._session, self._new_part(type="step-start"))

    async def text(self, text: str) -> None:
        part = self._new_part(type="text", text="")
        await self._server.emit_part(self._session, part)
        if text:
            await self._server.publish(
                "message.part.delta",
                {"sessionID": self._session, "messageID": part["messageID"], "partID": part["id"], "field": "text", "delta": text},
            )
        part["text"] = text
        part["time"] = {"start": self.store.now(), "end": self.store.now()}
        await self._server.emit_part(self._session, part)

    async def tool_started(self, call: ToolCall) -> None:
        part = self._new_part(type="tool", tool=call.name, callID=call.id, state={"status": "pending", "input": {}, "raw": ""})
        self.store.save(self._server.sandbox)
        await self._server.emit_part(self._session, part)

    async def tool_finished(self, call: ToolCall, outcome: ToolOutcome) -> None:
        part = _find_part(self.store, self._session, call.id)
        if part is None:
            return
        previous = as_object(part.get("state"))
        state: dict[str, JsonValue] = {"input": previous.get("input") or {}, "time": {"start": self.store.now(), "end": self.store.now()}}
        if "metadata" in previous:
            state["metadata"] = previous["metadata"]
        if outcome.is_error:
            state.update(status="error", error=outcome.content)
        else:
            state.update(status="completed", output=outcome.content, title="")
            if call.name == "bash":
                state["metadata"] = {"exit": 0, "output": outcome.content}
        part["state"] = state
        self.store.save(self._server.sandbox)
        await self._server.emit_part(self._session, part)

    async def step_finished(self, reason: Literal["tool-calls", "stop"], usage: ModelUsage) -> None:
        tokens: dict[str, JsonValue] = {
            "total": usage.input_tokens + usage.output_tokens,
            "input": usage.input_tokens,
            "output": usage.output_tokens,
            "reasoning": 0,
            "cache": {"write": 0, "read": 0},
        }
        await self._server.emit_part(self._session, self._new_part(type="step-finish", reason=reason, tokens=tokens, cost=0))
        self._message.update(
            finish=reason,
            tokens=tokens,
            cost=0,
            time={**as_object(self._message.get("time")), "completed": self.store.now()},
        )
        self.store.save(self._server.sandbox)
        for _ in range(2):
            await self._server.emit_message(self._session, self._message)
        await self._server.emit_status(self._session, "busy")


def _find_part(store: _Store, session_id: str, call_id: str) -> dict[str, JsonValue] | None:
    for message in reversed(store.messages(session_id)):
        for part in as_list(message.get("parts")):
            entry = as_object(part)
            if as_str(entry.get("type")) == "tool" and as_str(entry.get("callID")) == call_id:
                return cast("dict[str, JsonValue]", entry)
    return None


async def _next_request(
    connection: h11.Connection, stream: ByteStream
) -> tuple[str, str, dict[str, str], JsonValue] | None:
    request: h11.Request | None = None
    body = b""
    while True:
        event = connection.next_event()
        if event is h11.NEED_DATA:
            data = await stream.read()
            connection.receive_data(data)
            continue
        if isinstance(event, h11.Request):
            request = event
        elif isinstance(event, h11.Data):
            body += bytes(event.data)
        elif isinstance(event, h11.EndOfMessage) and request is not None:
            headers = {k.decode().lower(): v.decode() for k, v in request.headers}
            value: JsonValue = json.loads(body) if body else None
            return request.method.decode(), request.target.decode(), headers, value
        elif isinstance(event, h11.ConnectionClosed) or event is h11.PAUSED:
            return None


async def _respond(connection: h11.Connection, stream: ByteStream, status: int, payload: JsonValue) -> None:
    content = b"" if status == 204 else json.dumps(payload).encode()
    headers = [("content-length", str(len(content)))]
    if content:
        headers.append(("content-type", "application/json"))
    out = connection.send(h11.Response(status_code=status, headers=headers)) or b""
    if content:
        out += connection.send(h11.Data(data=content)) or b""
    out += connection.send(h11.EndOfMessage()) or b""
    await stream.write(out)


def _export(store: _Store, session_id: str) -> bytes | None:
    session = store.sessions.get(session_id)
    if session is None:
        return None
    export: dict[str, JsonValue] = {"info": session["info"], "messages": session["messages"]}
    return json.dumps(export, indent=1).encode()


def install(sandbox: FakeSandboxHandle, *, version: str = VERSION, digest: str = DIGEST) -> FakeOpencode:
    fake = FakeOpencode(version=version)
    sandbox.install("sha256sum", sha256sum_program({BINARY_PATH: digest}.get))
    sandbox.install("opencode", fake.program)
    sandbox.install("sh", fake.shell)
    sandbox.install("cat", fake.cat)
    return fake


__all__ = ["DIGEST", "VERSION", "FakeOpencode", "FakeOpencodeServer", "install"]
