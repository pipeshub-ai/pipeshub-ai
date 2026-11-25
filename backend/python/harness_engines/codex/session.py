"""``EngineSession`` over one ``codex-app-server`` process in one sandbox (one process per run, F2 §10)."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from harness_core.context.user_compact import CompactReport
from harness_core.engines.errors import (
    CompactFailed,
    EngineSessionClosed,
    EngineUnhealthy,
)
from harness_core.engines.events import ApprovalRequested, TurnCompleted
from harness_core.engines.jsonview import as_int, as_object, as_str
from harness_core.engines.stopping import stop_process
from harness_core.engines.types import EngineTask, SessionHealth
from harness_core.sandbox.tar import read_tar_stream
from harness_engines.codex.launch import SESSIONS_DIR, thread_settings
from harness_engines.codex.mapper import ASKS, CodexEventMapper, answer_for
from harness_engines.codex.rpc import (
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    CodexRpcError,
    CodexStreamEnded,
    StreamEnd,
)
from harness_engines.codex.state import encode_state, rollout_path

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.native import NativeTape
    from harness_core.engines.types import EngineVersion, FollowUp, JsonValue
    from harness_core.interactions.ports import InteractionDecision
    from harness_core.logs import HarnessLog
    from harness_core.sandbox import ProcessHandle, SandboxHandle
    from harness_engines.codex.rpc import CodexRpc

TURN_ID_TIMEOUT_S = 30.0
# A compaction is one summarising model call over the whole thread.
COMPACT_TIMEOUT_S = 600.0
_ROLLOUT_SUFFIX = re.compile(r"(\d{4}/\d{2}/\d{2}/rollout-[^/]+\.jsonl)$")


class CodexSession:
    def __init__(
        self,
        *,
        engine: EngineVersion,
        sandbox: SandboxHandle,
        process: ProcessHandle,
        rpc: CodexRpc,
        reported_version: str,
        resume_thread: str | None,
        secrets: frozenset[str],
        log: HarnessLog,
    ) -> None:
        self._engine = engine
        self._sandbox = sandbox
        self._process = process
        self._rpc = rpc
        self._reported_version = reported_version
        self._resume_thread = resume_thread
        self.thread_id: str | None = None
        # The instructions the thread was opened with: a turn carrying the same ones does not send them again.
        self._opened_with: str | None = None
        self._secrets = secrets
        self._log = log.bind(sandboxId=sandbox.id)
        self._mapper: CodexEventMapper | None = None
        self._turn_known = asyncio.Event()
        self._children: set[str] = set()
        # Codex's asks waiting for the controller: request id → (Codex's id, method, params).
        self._asks: dict[str, tuple[JsonValue, str, Mapping[str, JsonValue]]] = {}
        self._closed = False
        # The native transcript of each turn (C2.39), when the controller records one.
        self._tape: NativeTape | None = None

    async def _open_thread(self, instructions: str) -> str:
        """Start the run's thread at its first turn, or resume the restored one, with the task's instructions."""
        settings = thread_settings(instructions)
        method = "thread/start" if self._resume_thread is None else "thread/resume"
        # Codex 0.160 deprecates hydrating every turn on resume (E8 FB-1).
        params: dict[str, JsonValue] = (
            settings
            if self._resume_thread is None
            else {**settings, "threadId": self._resume_thread, "excludeTurns": True}
        )
        try:
            result = as_object(await self._rpc.request(method, params))
        except CodexRpcError as error:
            # The thread opens only once the tool gateway answered (``MCP_REQUIRED``, E2E3-F6): a gateway that never
            # did is an engine that never came up, so the attempt is released and a later one resumes the run.
            self._log.warning("engine.thread.unopened", method=method, code=error.code, reasonCode=EngineUnhealthy.reason_code)
            raise EngineUnhealthy(f"codex-app-server could not open the run's thread ({method})") from error
        thread_id = as_str(as_object(result.get("thread")).get("id"))
        if not thread_id:
            raise RuntimeError("Codex returned no thread id")
        self.thread_id = thread_id
        self._opened_with = instructions
        self._log = self._log.bind(engineSessionId=thread_id)
        return thread_id

    def tap_native(self, tape: NativeTape) -> None:
        self._tape = tape

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        if self._closed:
            raise EngineSessionClosed("session is closed")
        first = self.thread_id is None
        resumed = first and self._resume_thread is not None
        instructions = task.instructions if isinstance(task, EngineTask) else ""
        thread_id = self.thread_id or await self._open_thread(instructions)
        mapper = CodexEventMapper(thread_id=thread_id, turn_id=task.turn_id, children=self._children)
        mapper.tape = self._tape
        self._mapper = mapper
        self._turn_known.clear()
        text = task.prompt if isinstance(task, EngineTask) else task.text
        items: list[JsonValue] = []
        if instructions and (resumed or (not first and instructions != self._opened_with)):
            # Developer instructions are fixed per thread in Codex's stable API; later ones travel with the turn. A
            # resumed thread keeps the ones it was started with (``thread/resume`` does not replace them), so a
            # later run's own (its recalled memories among them) travel with its first turn (FIX-P2-B2).
            items.append({"type": "text", "text": instructions})
        items.append({"type": "text", "text": text})
        self._log.info("engine.turn.started", turnId=task.turn_id)
        try:
            started = as_object(await self._rpc.request("turn/start", {"threadId": thread_id, "input": items}))
            mapper.begin(as_str(as_object(started.get("turn")).get("id")))
            self._turn_known.set()
            while not mapper.done:
                message = await self._rpc.incoming.get()
                if isinstance(message, StreamEnd):
                    self._log.warning(
                        "engine.stream.failed",
                        turnId=task.turn_id,
                        reasonCode="stream_failed",
                        errorType=type(message.error).__name__ if message.error is not None else "eof",
                    )
                    # The engine went away mid-turn: lost, never completed (LCFIX); the next attempt resumes it.
                    raise CodexStreamEnded("the engine event stream ended before the turn completed") from message.error
                events = mapper.feed(message)
                await self._settle_request(message, events)
                for event in events:
                    if isinstance(event, TurnCompleted):
                        self._log.info(
                            "engine.turn.completed", turnId=task.turn_id, reason=event.reason, events=event.meta.seq + 1
                        )
                    yield event
        finally:
            self._mapper = None
            self._turn_known.clear()

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        """Codex's own ``thread/compact/start`` on the thread (SLASH2 C2), done at its compaction turn's
        ``turn/completed``. Codex's compaction takes no focus; its compacted history keeps the person's messages
        after the summary. ``before_tokens``: the summarising call's input, the thread as it was."""
        if self._closed:
            raise EngineSessionClosed("session is closed")
        thread_id = self.thread_id or await self._open_thread(instructions)
        self._log.info("engine.compact.started", focus=focus is not None)
        try:
            await self._rpc.request("thread/compact/start", {"threadId": thread_id})
            return await asyncio.wait_for(self._compaction(thread_id), COMPACT_TIMEOUT_S)
        except CodexRpcError as error:
            raise CompactFailed(f"Codex refused thread/compact/start ({error.code})") from error
        except TimeoutError as exc:
            raise CompactFailed("Codex did not finish compacting") from exc

    async def _compaction(self, thread_id: str) -> CompactReport:
        turn_id = ""
        compacted = False
        before: int | None = None
        while True:
            message = await self._rpc.incoming.get()
            if isinstance(message, StreamEnd):
                raise CompactFailed("the app-server's output ended while compacting")
            await self._settle_request(message, [])
            method, params = as_str(message.get("method")), as_object(message.get("params"))
            if as_str(params.get("threadId")) != thread_id:
                continue
            turn = as_object(params.get("turn"))
            if method == "turn/started":
                turn_id = as_str(turn.get("id"))
            elif method == "thread/compacted" or (
                method == "item/completed" and as_str(as_object(params.get("item")).get("type")) == "contextCompaction"
            ):
                compacted = True
            elif method == "thread/tokenUsage/updated":
                before = as_int(as_object(as_object(params.get("tokenUsage")).get("last")).get("inputTokens")) or None
            elif method == "turn/completed" and turn_id in ("", as_str(turn.get("id"))):
                status = as_str(turn.get("status"))
                if status != "completed" or not compacted:
                    raise CompactFailed(f"Codex ended its compaction turn {status or 'unknown'}")
                return CompactReport("compacted", before, None, user_messages_kept=True)

    async def _settle_request(self, message: Mapping[str, JsonValue], events: list[EngineEvent]) -> None:
        """Every server request gets an answer: an ask of this turn waits for the controller; any other
        (a sub-agent's ask, a dynamic tool call, auth refresh) is answered no at once."""
        if "id" not in message or "method" not in message:
            return
        request_id, method = message.get("id"), as_str(message.get("method"))
        params = as_object(message.get("params"))
        if any(isinstance(e, ApprovalRequested) for e in events):
            self._asks[str(request_id)] = (request_id, method, params)
            return
        if method in ASKS:
            await self._rpc.reply(request_id, answer_for(method, params, "denied"))
        else:
            await self._rpc.reply_error(request_id, METHOD_NOT_FOUND, "not served by this client")
        self._log.warning("engine.native_request.refused", reasonCode="native_request", method=method)

    async def steer(self, text: str) -> None:
        turn_id = await self._active_turn()
        await self._rpc.request(
            "turn/steer",
            {"threadId": self.thread_id, "input": [{"type": "text", "text": text}], "expectedTurnId": turn_id},
        )

    async def interrupt(self) -> None:
        """Interrupting a turn that has already ended is a no-op."""
        mapper = self._mapper
        if mapper is not None:
            if mapper.done:
                self._log.info("engine.turn.interrupt_after_end", source="state")
                return
            mapper.mark_interrupted()
        self._log.info("engine.turn.interrupt")
        turn_id = await self._active_turn()
        try:
            await self._rpc.request("turn/interrupt", {"threadId": self.thread_id, "turnId": turn_id})
        except CodexRpcError as error:
            # Codex ended the turn itself before our request reached it; its turn/completed is still queued.
            if error.code != INVALID_REQUEST:
                raise
            self._log.info("engine.turn.interrupt_after_end", source="engine", code=error.code)

    async def _active_turn(self) -> str:
        await asyncio.wait_for(self._turn_known.wait(), TURN_ID_TIMEOUT_S)
        mapper = self._mapper
        if mapper is None or mapper.codex_turn_id is None:
            raise EngineSessionClosed("no turn is running")
        return mapper.codex_turn_id

    async def answer(self, request_id: str, decision: InteractionDecision) -> None:
        raw_id, method, params = self._asks.pop(request_id)
        await self._rpc.reply(raw_id, answer_for(method, params, decision))

    async def capture_state(self) -> EngineState:
        if self.thread_id is None:
            raise RuntimeError("no thread to capture: the session ran no turn")
        files = await read_tar_stream(await self._sandbox.get_tar(SESSIONS_DIR))
        suffix = f"-{self.thread_id}.jsonl"
        found = [
            (match.group(1), content)
            for name, content in files.items()
            if name.endswith(suffix) and (match := _ROLLOUT_SUFFIX.search(name)) is not None
        ]
        if len(found) != 1 or rollout_path(found[0][0], self.thread_id) is None:
            raise RuntimeError(f"expected one rollout for the thread, found {len(found)}")
        path, rollout = found[0]
        state = encode_state(self._engine, self.thread_id, path, rollout, secrets=self._secrets)
        self._log.info("engine.state.captured", bytes=len(state.payload), schemaVersion=state.schema_version)
        return state

    async def health(self) -> SessionHealth:
        if self._closed:
            return SessionHealth(alive=False, engine_version=self._engine.version, detail="closed")
        if self._rpc.ended:
            return SessionHealth(alive=False, detail="stream_ended")
        try:
            code = await self._process.wait(timeout_s=0.05)
        except TimeoutError:
            return SessionHealth(alive=True, engine_version=self._reported_version)
        return SessionHealth(alive=False, detail=f"exited_{code}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._rpc.close()
        code = await stop_process(self._process)
        self._log.info("engine.session.closed", exitCode=code, unparsedLines=self._rpc.unparsed_lines)


__all__ = ["CodexSession"]
