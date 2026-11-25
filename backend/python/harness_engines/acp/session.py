"""``EngineSession`` over one ACP agent process in one sandbox: the client side of the protocol.

The agent is started (and ``initialize`` answered) by ``AcpAdapter.open``; its ACP session is opened at the run's
first turn, when the task's instructions are known: ``session/load`` when the state carries a session the agent
can load, else ``session/new``, and the PipesHub tool gateway is the one MCP server it is given (HTTP, the tools
placeholder as its bearer). A turn is one ``session/prompt``; its ``session/update`` notifications become engine
events as they arrive and its answer ends the turn. ``interrupt`` is ``session/cancel``: pending asks are answered
``cancelled`` and the agent answers the prompt ``cancelled``; one that does not within ``CANCEL_GRACE_S`` is
stopped, and the turn ends interrupted.

What the agent asks of the client is served here, from the pump (``AcpRpc``): ``session/request_permission`` by
the harness's policy (``permissions``), ``fs/read_text_file`` and ``fs/write_text_file`` inside the workspace only
and as the sandbox user (``harness_core.sandbox.workspace_fs``), when the profile offers them. ``terminal/*`` is
not offered (``clientCapabilities.terminal`` is false): a terminal the broker started would sit outside the
agent's process tree, where a cut turn's commands are found and stopped (LCFIX), so the agent runs commands with
its own shell tool, inside the same sandbox. Any other request is answered ``Method not found``.
"""

from __future__ import annotations

import asyncio
import contextlib
import posixpath
from typing import TYPE_CHECKING

from harness_core.context.user_compact import CompactReport
from harness_core.engines.errors import (
    CompactFailed,
    CompactUnsupported,
    EngineSessionClosed,
    EngineUnhealthy,
    SteeringUnsupported,
)
from harness_core.engines.events import TurnCompleted
from harness_core.engines.jsonview import as_object, as_str
from harness_core.engines.stopping import INTERRUPT_GRACE_S, stop_process
from harness_core.engines.stream import EventBuffer
from harness_core.engines.types import EngineTask, SessionHealth
from harness_core.sandbox.errors import SandboxError
from harness_core.sandbox.tar import put_config, read_tar_stream
from harness_core.sandbox.workspace_fs import (
    WorkspacePathRefused,
    read_workspace_text,
    write_workspace_text,
)
from harness_engines.acp.compaction import (
    AgentCompaction,
    opencode_summaries,
    qwen_records_path,
    qwen_summaries,
)
from harness_engines.acp.dsh_record import AgentRecord, dsh_record
from harness_engines.acp.gateway_probe import await_gateway_tools
from harness_engines.acp.mapper import AcpEventMapper, error_code
from harness_engines.acp.permissions import CANCELLED, classify, option_for
from harness_engines.acp.profiles import WORKDIR
from harness_engines.acp.protocol import (
    FS_READ,
    FS_WRITE,
    INVALID_PARAMS,
    MAX_FS_BYTES,
    METHOD_NOT_FOUND,
    REQUEST_PERMISSION,
    RESOURCE_NOT_FOUND,
    SESSION_CANCEL,
    SESSION_LOAD,
    SESSION_NEW,
    SESSION_PROMPT,
    SESSION_RESUME,
    SESSION_SET_CONFIG,
    SESSION_UPDATE,
)
from harness_engines.acp.rpc import AcpRpcError, AcpStreamEnded
from harness_engines.acp.state import AcpState, encode_state, recap_text
from harness_engines.opencode_api.http import OpencodeHttp
from harness_engines.opencode_api.readiness import GATEWAY_TIMEOUT_S, await_tool_gateway

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine, Mapping

    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.native import NativeTape
    from harness_core.engines.ports import ToolListings
    from harness_core.engines.types import EngineVersion, FollowUp, JsonValue
    from harness_core.interactions.ports import InteractionDecision
    from harness_core.logs import HarnessLog
    from harness_core.sandbox import ProcessHandle, SandboxHandle
    from harness_engines.acp.permissions import SessionPolicy
    from harness_engines.acp.profiles import AcpAgentProfile
    from harness_engines.acp.rpc import AcpRpc, Incoming
    from harness_engines.acp.state import Role

STOP_TIMEOUT_S = 10.0
SESSION_TIMEOUT_S = 120.0
CANCEL_GRACE_S = INTERRUPT_GRACE_S
# An ask nobody answers is refused after this long; the controller answers every one at once.
PERMISSION_TIMEOUT_S = 300.0
# How often the Tool Gateway's count is read while an agent that connects in the background has not listed its tools.
WITNESS_POLL_S = 0.05
# What a summary's size is told as, in tokens (an estimate, as ``CompactReport`` says).
_CHARS_PER_TOKEN = 4
# The first block of the prompt after a stopped turn. An agent keeps the stopped prompt in its history with no answer,
# and Qwen Code joins it to the next one with no break ("say nevershell echo", R23PERF-FU-1): the model then reads one
# garbled request and may carry on with the stopped one. The blank lines keep both prompts whole in an agent that joins
# text blocks, and the words tell the model the earlier request was stopped, as Claude Code's own history does.
STOPPED_TURN_NOTICE = "\n\n[The person stopped the previous request before it finished.]\n\n"


def mcp_server_entry(policy: SessionPolicy) -> list[JsonValue]:
    """The tool gateway as ``session/new`` and ``session/load`` name it: HTTP, its URL and the tools placeholder, and
    the profile's rendered ``_meta`` when it has one."""
    entry: dict[str, JsonValue] = {
        "type": "http",
        "name": policy.mcp_server,
        "url": policy.tools_url,
        "headers": [{"name": "Authorization", "value": f"Bearer {policy.tools_placeholder}"}],
    }
    if policy.mcp_meta:
        entry["_meta"] = dict(policy.mcp_meta)
    return [entry]

class AcpSession:
    def __init__(
        self,
        *,
        agent: AcpAgentProfile,
        engine: EngineVersion,
        sandbox: SandboxHandle,
        process: ProcessHandle,
        rpc: AcpRpc,
        policy: SessionPolicy,
        load_session: bool,
        restored: AcpState | None,
        log: HarnessLog,
        permission_timeout_s: float = PERMISSION_TIMEOUT_S,
        cancel_grace_s: float = CANCEL_GRACE_S,
        gateway_timeout_s: float = GATEWAY_TIMEOUT_S,
    ) -> None:
        self._agent = agent
        self._engine = engine
        self._sandbox = sandbox
        self._process = process
        self._rpc = rpc
        self._policy = policy
        self._load_session = load_session
        self._restored = restored
        self._log = log.bind(sandboxId=sandbox.id)
        self._permission_timeout_s = permission_timeout_s
        self._cancel_grace_s = cancel_grace_s
        self._gateway_timeout_s = gateway_timeout_s
        self.session_id: str | None = None
        self._opened_with: str | None = None
        self._recap: list[tuple[Role, str]] = list(restored.recap) if restored is not None else []
        self._recap_pending = False
        self._after_stop = False
        self._mapper: AcpEventMapper | None = None
        self._out: EventBuffer | None = None
        self._cancelled = asyncio.Event()
        # Asks waiting for the controller: our request id → (the agent's id, its options, the refusal timer).
        self._asks: dict[str, tuple[JsonValue, JsonValue, asyncio.TimerHandle]] = {}
        self._ask_count = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False
        self._broken: str | None = None
        self._stopping: asyncio.Task[int | None] | None = None
        # The Tool Gateway's count of tool lists served to this attempt (E2E3-F6), and its value before the session
        # opened; set where the profile asks the gateway itself (``mcp.status`` gateway).
        self._listings: ToolListings | None = None
        self._listed_before: int | None = None
        # The native transcript of each turn (C2.39), when the controller records one.
        self._tape: NativeTape | None = None
        # The context the agent last said it used, for a restart's report (MG-10).
        self._context_used: int | None = None
        # The agent's own compaction summaries already told (``compactionNotice``), by message id.
        self._summaries_seen: set[str] = set()
        # The model calls of the agent's session log already counted (``sessionRecord``), by id, and the log's last
        # event read.
        self._calls_seen: set[str] = set()
        self._record_seq = -1

    # The pump's handler: never waits on the agent.

    async def handle(self, message: Incoming) -> None:
        if message.method == SESSION_UPDATE and not message.is_request:
            await self._on_update(message.params)
        elif message.method == REQUEST_PERMISSION and message.is_request:
            await self._on_permission(message)
        elif message.method in (FS_READ, FS_WRITE) and message.is_request:
            self._spawn(self._on_fs(message))
        elif message.is_request:
            await self._rpc.reply_error(message.id, METHOD_NOT_FOUND, "method not found")
            self._log.info("engine.native_request.refused", reasonCode="native_request", method=message.method[:64])
        else:
            await self._on_notice(message)

    def ended(self, error: BaseException | None) -> None:
        """The agent's stream ended: a turn waiting on it fails (as a lost turn), every pending ask is dropped."""
        self._broken = self._broken or ("stream_ended" if error is None else type(error).__name__)
        for _raw, _options, timer in self._asks.values():
            timer.cancel()
        self._asks.clear()

    def _spawn(self, work: Coroutine[object, object, None]) -> None:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _ours(self, params: Mapping[str, JsonValue]) -> bool:
        return self.session_id is not None and as_str(params.get("sessionId")) == self.session_id

    async def _on_update(self, params: Mapping[str, JsonValue]) -> None:
        mapper, out = self._mapper, self._out
        # A load replays the session's history before any turn: nothing of it is this run's.
        if mapper is None or out is None or not self._ours(params):
            return
        for event in mapper.update(as_object(params.get("update"))):
            await out.put(event)

    async def _on_notice(self, message: Incoming) -> None:
        """A notification ACP does not define: kept as the turn's ``NativeUnknown`` (EV-13), never acted on."""
        mapper, out = self._mapper, self._out
        if mapper is None or out is None:
            return
        for event in mapper.notice(message.method, {"method": message.method, "params": dict(message.params)}):
            await out.put(event)

    async def _on_permission(self, message: Incoming) -> None:
        params = message.params
        mapper, out = self._mapper, self._out
        call = as_object(params.get("toolCall"))
        options = params.get("options")
        if mapper is None or out is None or mapper.done or not self._ours(params) or self._cancelled.is_set():
            await self._rpc.reply(message.id, CANCELLED)
            return
        call = mapper.named(call)
        verdict, permission = classify(self._policy, call)
        if verdict == "allow":
            await self._rpc.reply(message.id, option_for(options, "allow"))
            return
        self._ask_count += 1
        request_id = f"acp-ask-{self._ask_count}"
        timer = asyncio.get_running_loop().call_later(
            self._permission_timeout_s, lambda: self._spawn(self._expire(request_id))
        )
        self._asks[request_id] = (message.id, options, timer)
        self._log.info("engine.native_approval.asked", reasonCode="native_approval", permission=permission[:32])
        await out.put(mapper.ask(request_id, call, permission))

    async def _expire(self, request_id: str) -> None:
        entry = self._asks.pop(request_id, None)
        if entry is None:
            return
        raw_id, options, _timer = entry
        self._log.warning("engine.native_approval.expired", reasonCode="native_approval_timeout")
        await self._rpc.reply(raw_id, option_for(options, "denied"))

    async def _on_fs(self, message: Incoming) -> None:
        params = message.params
        writing = message.method == FS_WRITE
        allowed = self._policy.client_fs_write if writing else self._policy.client_fs_read
        if not allowed:
            await self._rpc.reply_error(message.id, METHOD_NOT_FOUND, "method not found")
            return
        if not self._ours(params):
            await self._rpc.reply_error(message.id, INVALID_PARAMS, "unknown session")
            return
        path = as_str(params.get("path"))
        try:
            if writing:
                content = params.get("content")
                if not isinstance(content, str):
                    await self._rpc.reply_error(message.id, INVALID_PARAMS, "content is not text")
                    return
                await write_workspace_text(
                    self._sandbox, WORKDIR, path, content.encode("utf-8", "surrogatepass"), max_bytes=MAX_FS_BYTES
                )
                await self._rpc.reply(message.id, {})
                return
            data = await read_workspace_text(self._sandbox, WORKDIR, path, max_bytes=MAX_FS_BYTES)
            await self._rpc.reply(message.id, {"content": _lines(data.decode("utf-8", "replace"), params)})
        except WorkspacePathRefused as refused:
            self._log.warning("engine.client_fs.refused", reasonCode=refused.reason_code, write=writing)
            code = RESOURCE_NOT_FOUND if refused.reason_code == "not_found" else INVALID_PARAMS
            await self._rpc.reply_error(message.id, code, refused.reason_code)
        except SandboxError as error:
            self._log.warning("engine.client_fs.failed", errorType=type(error).__name__, write=writing)
            await self._rpc.reply_error(message.id, INVALID_PARAMS, "unavailable")

    # The ACP session.

    def _mcp_servers(self) -> list[JsonValue]:
        if self._agent.mcp_declare == "config":
            # The rendered config declares the gateway, with what ACP's server entry cannot carry (the run's tool
            # timeout, trust off); an entry sent here would replace it (Gemini CLI, WP-59).
            return []
        return mcp_server_entry(self._policy)

    async def _write_instructions(self, instructions: str) -> None:
        path = self._agent.instructions_file
        if path is None or instructions == self._opened_with:
            return
        await put_config(self._sandbox, {path: instructions.encode()})

    async def _open_session(self, instructions: str) -> str:
        await self._write_instructions(instructions)
        await self._await_tool_gateway(opened=False)
        restored = self._restored
        if restored is not None and restored.session_id and self._load_session:
            method = SESSION_RESUME if self._agent.resume == "resume" else SESSION_LOAD
            try:
                await self._rpc.request(
                    method,
                    {"sessionId": restored.session_id, "cwd": WORKDIR, "mcpServers": self._mcp_servers()},
                    timeout_s=SESSION_TIMEOUT_S,
                )
                self.session_id = restored.session_id
                await self._note_summaries()
            except AcpRpcError as error:
                # The saved session is gone or refused: the workspace and the recap carry the run on.
                self._log.warning("engine.session.load_refused", reasonCode="session_load_refused", code=error.code)
        if self.session_id is None:
            result = as_object(
                await self._rpc.request(
                    SESSION_NEW, {"cwd": WORKDIR, "mcpServers": self._mcp_servers()}, timeout_s=SESSION_TIMEOUT_S
                )
            )
            session_id = as_str(result.get("sessionId"))
            if not session_id:
                raise AcpStreamEnded("the agent opened no session")
            self.session_id = session_id
            self._recap_pending = bool(self._recap)
        await self._await_tool_gateway(opened=True)
        for config_id, value in self._agent.session_config.items():
            params: dict[str, JsonValue] = {"sessionId": self.session_id, "configId": config_id, "value": value}
            if isinstance(value, bool):
                params["type"] = "boolean"
            _ = await self._rpc.request(SESSION_SET_CONFIG, params, timeout_s=SESSION_TIMEOUT_S)
        self._opened_with = instructions
        self._log = self._log.bind(engineSessionId=self.session_id)
        return self.session_id

    async def _await_tool_gateway(self, *, opened: bool) -> None:
        """The session holds the tool gateway before any prompt (E2E3-F6): opencode and Gemini CLI each open one
        without a gateway that refused them, and the prompt would go out without any gateway tool. Asked as the
        profile says (``mcp.status``): on opencode's own API once the session is open, and of the gateway itself,
        over the agent's route, before Gemini CLI opens it (it connects only then and never retries)."""
        status = self._agent.mcp_status
        if status is not None and status.api == "gateway" and opened:
            await self._check_witness(wait=status.waits_for_witness)
        if status is None or status.before_session == opened:
            return
        if status.api == "gateway":
            assert status.runtime is not None
            await await_gateway_tools(
                self._sandbox,
                self._log,
                runtime=status.runtime,
                server=self._agent.mcp_server,
                url=self._policy.tools_url,
                placeholder=self._policy.tools_placeholder,
                timeout_s=self._gateway_timeout_s,
            )
            # Nothing else in the sandbox lists the tools between here and the session's answer: an agent idle
            # before its first prompt, and the probe has returned.
            self._listed_before = self._witnessed()
            return
        assert status.port is not None
        http = OpencodeHttp(self._sandbox, status.port, password="")
        try:
            await await_tool_gateway(http, self._log, server=self._agent.mcp_server, timeout_s=self._gateway_timeout_s)
        finally:
            await http.aclose()

    def _witnessed(self) -> int | None:
        """The gateway's count the profile's ``mcp.status.witness`` names; ``None`` without a witness."""
        listings, status = self._listings, self._agent.mcp_status
        if listings is None:
            return None
        return listings.connected() if status is not None and status.witness == "handshake" else listings.listed()

    def witness_tool_gateway(self, listings: ToolListings) -> None:
        """What the Tool Gateway saw of this attempt (``WitnessesToolGateway``): the controller's, before any turn."""
        self._listings = listings

    async def _check_witness(self, *, wait: bool) -> None:
        """The gateway listed its tools to the agent (or, for an agent that lists them only once prompted, answered
        its handshake) while it opened the session. The probe proves the gateway was up a moment before; only the
        gateway can tell that the agent's own connect also got through, which the agent never reports (a refusal
        leaves it without the tools for the whole session). An agent that connects after answering ``session/new``
        (``wait``, Qwen Code) is waited for, at most the gateway timeout."""
        before = self._listed_before
        if self._listings is None or before is None:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (self._gateway_timeout_s if wait else 0.0)
        while (self._witnessed() or 0) <= before:
            if loop.time() >= deadline:
                break
            await asyncio.sleep(WITNESS_POLL_S)
        else:
            return
        self._log.warning(
            "engine.tool_gateway.unlisted",
            server=self._agent.mcp_server,
            reasonCode=EngineUnhealthy.reason_code,
        )
        raise EngineUnhealthy("the agent opened its session without listing the tool gateway's tools")

    def _prompt(self, task: EngineTask | FollowUp, instructions: str, *, first: bool) -> list[JsonValue]:
        blocks: list[JsonValue] = []
        if self._recap_pending:
            blocks.append({"type": "text", "text": recap_text(self._recap)})
            self._recap_pending = False
        if self._after_stop:
            blocks.append({"type": "text", "text": STOPPED_TURN_NOTICE})
            self._after_stop = False
        if instructions and self._agent.instructions_file is None and (first or instructions != self._opened_with):
            blocks.append({"type": "text", "text": instructions})
        text = task.prompt if isinstance(task, EngineTask) else task.text
        blocks.append({"type": "text", "text": text})
        return blocks

    def tap_native(self, tape: NativeTape) -> None:
        self._tape = tape

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        if self._closed:
            raise EngineSessionClosed("session is closed")
        if self._broken is not None:
            raise AcpStreamEnded("the agent has stopped")
        instructions = task.instructions if isinstance(task, EngineTask) else ""
        first = self.session_id is None
        mapper = AcpEventMapper(turn_id=task.turn_id, policy=self._policy)
        mapper.tape = self._tape
        out = EventBuffer()
        # A session opened again after a restart keeps the run's instructions, a follow-up carrying none.
        opening = instructions or self._opened_with or ""
        try:
            session_id = self.session_id or await self._open_session(opening)
        except AcpRpcError as error:
            self._log.warning("engine.session.refused", reasonCode="session_refused", code=error.code)
            for event in mapper.failed("auth_required" if error.code == -32000 else "acp_session_refused"):
                yield event
            return
        if not first and instructions and instructions != self._opened_with:
            await self._write_instructions(instructions)
        prompt = self._prompt(task, opening if first else instructions, first=first)
        text = task.prompt if isinstance(task, EngineTask) else task.text
        self._cancelled.clear()
        self._mapper, self._out = mapper, out
        self._log.info("engine.turn.started", turnId=task.turn_id)
        driver = asyncio.get_running_loop().create_task(self._drive(session_id, prompt, mapper, out))
        lost: BaseException | None = None
        try:
            async for event in out.drain():
                if isinstance(event, TurnCompleted):
                    self._log.info("engine.turn.completed", turnId=task.turn_id, reason=event.reason, events=event.meta.seq + 1)
                    self._recap.append(("user", text))
                    self._after_stop = event.reason == "interrupted"
                    if event.reason == "completed":
                        self._recap.append(("assistant", event.final_text))
                yield event
            _ = await asyncio.wait({driver}, timeout=STOP_TIMEOUT_S)
            lost = driver.exception() if driver.done() and not driver.cancelled() else None
        finally:
            self._context_used = mapper.context_used or self._context_used
            self._mapper, self._out = None, None
            for request_id in list(self._asks):
                raw_id, _options, timer = self._asks.pop(request_id)
                timer.cancel()
                await self._rpc.reply(raw_id, CANCELLED)
            if not driver.done():
                driver.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await driver
        if lost is not None:
            self._log.warning("engine.stream.failed", turnId=task.turn_id, reasonCode="stream_failed", errorType=type(lost).__name__)
            raise AcpStreamEnded("the agent's stream ended before the turn completed") from lost

    async def _drive(self, session_id: str, prompt: list[JsonValue], mapper: AcpEventMapper, out: EventBuffer) -> None:
        request = asyncio.ensure_future(
            self._rpc.request(SESSION_PROMPT, {"sessionId": session_id, "prompt": prompt}, timeout_s=None)
        )
        try:
            while True:
                cancelled = asyncio.ensure_future(self._cancelled.wait())
                done, _ = await asyncio.wait({request, cancelled}, return_when=asyncio.FIRST_COMPLETED)
                cancelled.cancel()
                if request in done:
                    break
                try:
                    await asyncio.wait_for(asyncio.shield(request), self._cancel_grace_s)
                except TimeoutError:
                    self._log.warning("engine.turn.cancel_unanswered", reasonCode="cancel_unanswered")
                    self._broken = "cancel_unanswered"
                    for event in mapper.failed("cancel_unanswered"):
                        await out.put(event)
                    # The turn ends now (CF-09); the escalation runs on, and ``close`` waits for it.
                    _ = self._stop_process()
                    return
                except Exception:
                    pass
                break
            try:
                response = as_object(request.result())
            except AcpRpcError as error:
                events = mapper.failed(error_code(error.detail, error.data, self._policy.error_reasons), "the agent refused the prompt")
            else:
                events = [*await self._compaction_notices(session_id, mapper), *mapper.completed(response)]
            for event in events:
                await out.put(event)
        except AcpStreamEnded:
            await out.close()
            raise
        finally:
            if not request.done():
                request.cancel()

    async def steer(self, text: str) -> None:
        raise SteeringUnsupported("ACP has no way to add to a running prompt")

    async def interrupt(self) -> None:
        """``session/cancel``; interrupting a turn that has already ended is a no-op."""
        mapper = self._mapper
        if mapper is None or mapper.done or self.session_id is None:
            self._log.info("engine.turn.interrupt_after_end", source="state")
            return
        mapper.mark_interrupted()
        self._log.info("engine.turn.interrupt")
        for request_id in list(self._asks):
            raw_id, _options, timer = self._asks.pop(request_id)
            timer.cancel()
            await self._rpc.reply(raw_id, CANCELLED)
        with contextlib.suppress(Exception):
            await self._rpc.notify(SESSION_CANCEL, {"sessionId": self.session_id})
        self._cancelled.set()

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        raise CompactUnsupported("ACP has no request to compact a session")

    async def restart_from_summary(self, task: EngineTask | FollowUp) -> CompactReport:
        """MG-10 for an agent that fails the prompt on a context overflow (``RestartsFromSummary``): the next turn opens
        a fresh ACP session in this process, its first prompt the conversation so far as the driver saw it (each
        request and answer, newest kept: ``recap_text``), and never loads the overflowed one again. The task being
        retried is left out of the summary: it is the retry's own prompt."""
        if self.session_id is None or self._closed or self._broken is not None:
            raise CompactFailed("no open session to restart")
        text = task.prompt if isinstance(task, EngineTask) else task.text
        if self._recap and self._recap[-1] == ("user", text):
            _ = self._recap.pop()
        before, overflowed = self._context_used, self.session_id
        self.session_id, self._restored = None, None
        self._summaries_seen.clear()
        self._calls_seen.clear()
        self._record_seq = -1
        summary = recap_text(self._recap) if self._recap else ""
        self._log.info("engine.session.restarted", reasonCode="context_restart", entries=len(self._recap))
        self._log = self._log.bind(restartedFrom=overflowed)
        return CompactReport(
            "compacted",
            before,
            len(summary) // _CHARS_PER_TOKEN or None,
            user_messages_kept=any(role == "user" for role, _ in self._recap),
        )

    async def _record(self, session_id: str) -> AgentRecord:
        """What the agent's own session log adds since the last read (``sessionRecord``); nothing when the profile
        names none or the log could not be read."""
        status = self._agent.mcp_status
        if self._agent.session_record != "dsh" or status is None or status.runtime is None:
            return AgentRecord()
        record = await dsh_record(
            self._sandbox,
            runtime=status.runtime,
            root=self._agent.state_paths[0],
            session_id=session_id,
            after_seq=self._record_seq,
        )
        if record is None:
            return AgentRecord()
        self._record_seq = max(self._record_seq, record.last_seq)
        return record

    async def _summaries(self, session_id: str) -> tuple[AgentCompaction, ...]:
        """The agent's own compaction summaries, where the profile names where to ask (``compactionNotice``); none
        when it names nothing or the agent could not be asked."""
        status = self._agent.mcp_status
        notice = self._agent.compaction_notice
        if notice == "opencode" and status is not None and status.port is not None:
            http = OpencodeHttp(self._sandbox, status.port, password="")
            try:
                return await opencode_summaries(http, session_id) or ()
            finally:
                await http.aclose()
        if notice == "qwen" and status is not None and status.runtime is not None and self._agent.state_paths:
            path = qwen_records_path(self._agent.state_paths[0], session_id)
            if path is not None:
                return await qwen_summaries(self._sandbox, runtime=status.runtime, path=path) or ()
        return ()

    async def _note_summaries(self) -> None:
        """A loaded session's earlier compactions and model calls are not this run's to tell again."""
        assert self.session_id is not None
        self._summaries_seen.update(s.message_id for s in await self._summaries(self.session_id))
        record = await self._record(self.session_id)
        self._summaries_seen.update(c.message_id for c in record.compactions)
        self._calls_seen.update(c.call_id for c in record.calls)

    async def _compaction_notices(self, session_id: str, mapper: AcpEventMapper) -> list[EngineEvent]:
        """One ``ContextCompacted`` for each summary the agent wrote during the turn, which ACP did not report; the
        turn's model calls its session log recorded are the turn's usage (``sessionRecord``)."""
        events: list[EngineEvent] = []
        record = await self._record(session_id)
        for call in record.calls:
            if call.call_id in self._calls_seen:
                continue
            self._calls_seen.add(call.call_id)
            mapper.add_call_usage(call.input_tokens, call.output_tokens, cache_read=call.cache_read_tokens)
        for summary in (*await self._summaries(session_id), *record.compactions):
            if summary.message_id in self._summaries_seen:
                continue
            self._summaries_seen.add(summary.message_id)
            self._log.info("engine.context.compacted_by_agent", beforeTokens=summary.before_tokens)
            events.extend(mapper.compacted(summary.before_tokens, summary.after_tokens))
        return events

    async def answer(self, request_id: str, decision: InteractionDecision) -> None:
        entry = self._asks.pop(request_id, None)
        if entry is None:
            raise LookupError(f"no pending engine request {request_id}")
        raw_id, options, timer = entry
        timer.cancel()
        await self._rpc.reply(raw_id, option_for(options, decision))

    async def capture_state(self) -> EngineState:
        if self.session_id is None:
            raise RuntimeError("no session to capture: the session ran no turn")
        files: dict[str, bytes] = {}
        if self._agent.resumes_from_files:
            for root in self._agent.state_paths:
                try:
                    found = await read_tar_stream(await self._sandbox.get_tar(root))
                except SandboxError:
                    continue
                parent = posixpath.dirname(root.rstrip("/"))
                files.update({posixpath.join(parent, name): content for name, content in found.items()})
        state = encode_state(
            self._engine,
            AcpState(agent=self._agent.id, session_id=self.session_id, files=files, recap=tuple(self._recap)),
            roots=self._agent.state_paths,
            secrets=(),
        )
        self._log.info("engine.state.captured", bytes=len(state.payload), schemaVersion=state.schema_version)
        return state

    async def health(self) -> SessionHealth:
        if self._closed:
            return SessionHealth(alive=False, engine_version=self._engine.version, detail="closed")
        if self._broken is not None or self._rpc.ended:
            return SessionHealth(alive=False, detail=self._broken or "stream_ended")
        try:
            code = await self._process.wait(timeout_s=0.05)
        except TimeoutError:
            return SessionHealth(alive=True, engine_version=self._engine.version)
        return SessionHealth(alive=False, detail=f"exited_{code}")

    def _stop_process(self) -> asyncio.Future[int | None]:
        if self._stopping is None:
            self._stopping = asyncio.get_running_loop().create_task(self._stop_once())
        return asyncio.shield(self._stopping)

    async def _stop_once(self) -> int | None:
        await self._rpc.close()
        return await stop_process(self._process)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        for _raw, _options, timer in self._asks.values():
            timer.cancel()
        self._asks.clear()
        code = await self._stop_process()
        self._log.info(
            "engine.session.closed",
            exitCode=code,
            unparsedLines=self._rpc.unparsed_lines,
            invalidMessages=self._rpc.invalid_messages,
        )


def _lines(text: str, params: Mapping[str, JsonValue]) -> str:
    """``line`` (1-based) and ``limit`` of ``fs/read_text_file``: the file from that line, that many lines."""
    line, limit = params.get("line"), params.get("limit")
    if not isinstance(line, int) and not isinstance(limit, int):
        return text
    lines = text.splitlines(keepends=True)
    start = max(line - 1, 0) if isinstance(line, int) and not isinstance(line, bool) else 0
    end = start + limit if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0 else None
    return "".join(lines[start:end])


__all__ = ["CANCEL_GRACE_S", "PERMISSION_TIMEOUT_S", "AcpSession", "mcp_server_entry"]
