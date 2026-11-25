"""``EngineSession`` over one ``opencode serve`` process in one sandbox (one process per run, F4 §8)."""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import TYPE_CHECKING

import httpcore

from harness_core.context.user_compact import CompactReport
from harness_core.engines.errors import (
    CompactFailed,
    EngineCrashed,
    EngineSessionClosed,
    SteeringUnsupported,
    killed_by_signal,
)
from harness_core.engines.events import TurnCompleted
from harness_core.engines.jsonview import as_int, as_list, as_object, as_str
from harness_core.engines.stopping import SERVER_STOP_SCHEDULE, stop_process
from harness_core.engines.stream import DEFAULT_BUFFER
from harness_core.engines.types import EngineTask, FollowUp, SessionHealth
from harness_core.sandbox.streams import collect
from harness_engines.opencode.launch import (
    AGENT,
    BINARY_PATH,
    DATA_HOME,
    MODEL,
    PROVIDER,
)
from harness_engines.opencode.mapper import OpencodeEventMapper
from harness_engines.opencode.state import encode_state
from harness_engines.opencode_api.http import OpencodeHttpError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.native import NativeTape
    from harness_core.engines.types import EngineVersion, JsonValue
    from harness_core.interactions.ports import InteractionDecision
    from harness_core.logs import HarnessLog
    from harness_core.sandbox import ProcessHandle, SandboxHandle
    from harness_engines.opencode_api.http import OpencodeHttp

EXPORT_TIMEOUT_S = 60.0
# The engine's runtime (Bun) cuts what a process writes to a pipe at 64 KiB when it exits, so a long session's
# export is written to a file in the data-home mount and read back with cat (EV2-23).
EXPORT_PATH = f"{DATA_HOME}/harness-export/session.json"
_EXPORT_TO_FILE = f'mkdir -p "${{2%/*}}" && exec {BINARY_PATH} export "$1" > "$2"'
# ``summarize`` answers once its one summarising model call over the session is done.
COMPACT_TIMEOUT_S = 600.0
_HTTP_FAILURES = (OpencodeHttpError, OSError, httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError)


class _StreamEnded:
    def __init__(self, error: BaseException | None) -> None:
        self.error = error


class OpencodeSession:
    def __init__(
        self,
        *,
        engine: EngineVersion,
        sandbox: SandboxHandle,
        process: ProcessHandle,
        http: OpencodeHttp,
        session_id: str,
        env: Mapping[str, str],
        secrets: frozenset[str],
        log: HarnessLog,
        output: asyncio.Future[None] | None = None,
    ) -> None:
        self._engine = engine
        self._sandbox = sandbox
        self._process = process
        # What reads the server's output to its end (``adapter._watch_listening``); ended with the server.
        self._output = output
        self._http = http
        self.session_id = session_id
        self._env = dict(env)
        self._secrets = secrets
        self._log = log.bind(engineSessionId=session_id, sandboxId=sandbox.id)
        self._queue: asyncio.Queue[Mapping[str, JsonValue] | _StreamEnded] = asyncio.Queue(
            maxsize=DEFAULT_BUFFER
        )
        self._reader: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._mapper: OpencodeEventMapper | None = None
        self._closed = False
        # The native transcript of each turn (C2.39), when the controller records one.
        self._tape: NativeTape | None = None

    async def start(self, *, connect_timeout_s: float) -> None:
        """Subscribe to ``/event`` before any prompt; opencode's stream has no replay."""
        self._reader = asyncio.get_running_loop().create_task(self._read())
        await asyncio.wait_for(self._connected.wait(), connect_timeout_s)

    async def _read(self) -> None:
        error: BaseException | None = None
        try:
            async for payload in self._http.events():
                if as_str(payload.get("type")) == "server.connected":
                    self._connected.set()
                    continue
                await self._queue.put(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the stream's failure is reported to the turn below
            error = exc
        await self._queue.put(_StreamEnded(error))

    def tap_native(self, tape: NativeTape) -> None:
        self._tape = tape

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        if self._closed:
            raise EngineSessionClosed("session is closed")
        self._discard_tail()
        mapper = OpencodeEventMapper(session_id=self.session_id, turn_id=task.turn_id)
        mapper.tape = self._tape
        self._mapper = mapper
        text = task.prompt if isinstance(task, EngineTask) else task.text
        body: dict[str, JsonValue] = {"parts": [{"type": "text", "text": text}], "agent": AGENT}
        if isinstance(task, EngineTask) and task.instructions:
            body["system"] = task.instructions
        self._log.info("engine.turn.started", turnId=task.turn_id)
        await self._http.request("POST", f"/session/{self.session_id}/prompt_async", body)
        try:
            while not mapper.done:
                item = await self._queue.get()
                if isinstance(item, _StreamEnded):
                    self._stream_failed(task.turn_id, item.error)
                    # The engine went away mid-turn: lost, never completed (LCFIX); the next attempt resumes it.
                    raise EngineCrashed("the engine event stream ended before the turn completed") from item.error
                for event in mapper.feed(item):
                    if isinstance(event, TurnCompleted):
                        self._log.info(
                            "engine.turn.completed", turnId=task.turn_id, reason=event.reason, events=event.meta.seq + 1
                        )
                    yield event
        finally:
            self._mapper = None

    def _discard_tail(self) -> None:
        """Drop the previous turn's trailing events; a dead stream stays visible to the next turn."""
        kept: list[_StreamEnded] = []
        while not self._queue.empty():
            item = self._queue.get_nowait()
            if isinstance(item, _StreamEnded):
                kept.append(item)
        for item in kept:
            self._queue.put_nowait(item)

    def _stream_failed(self, turn_id: str, error: BaseException | None) -> None:
        self._log.warning(
            "engine.stream.failed",
            turnId=turn_id,
            reasonCode="stream_failed",
            errorType=type(error).__name__ if error is not None else "eof",
        )

    async def steer(self, text: str) -> None:
        raise SteeringUnsupported("opencode's v1 API has no steering; v2's is experimental")

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        """opencode's own ``POST /session/{id}/summarize`` (SLASH2 C2), which answers once the summary is in the
        session. It takes no focus. The counts are the summary message's: its input (the session as it was) and its
        output (the summary the next turn starts from)."""
        if self._closed:
            raise EngineSessionClosed("session is closed")
        self._log.info("engine.compact.started", focus=focus is not None)
        try:
            done = await self._http.request(
                "POST",
                f"/session/{self.session_id}/summarize",
                {"providerID": PROVIDER, "modelID": MODEL},
                timeout_s=COMPACT_TIMEOUT_S,
            )
        except _HTTP_FAILURES as exc:
            raise CompactFailed(f"opencode could not summarize ({type(exc).__name__})") from exc
        if done is not True:
            raise CompactFailed("opencode did not summarize the session")
        tokens = await self._summary_tokens()
        return CompactReport("compacted", as_int(tokens.get("input")) or None, as_int(tokens.get("output")) or None)

    async def _summary_tokens(self) -> Mapping[str, JsonValue]:
        try:
            messages = as_list(await self._http.request("GET", f"/session/{self.session_id}/message"))
        except _HTTP_FAILURES:
            return {}
        for message in reversed(messages):
            info = as_object(as_object(message).get("info"))
            if info.get("summary") is True and as_str(info.get("role")) == "assistant":
                return as_object(info.get("tokens"))
        return {}

    async def interrupt(self) -> None:
        if self._mapper is not None:
            self._mapper.mark_interrupted()
        self._log.info("engine.turn.interrupt")
        await self._http.request("POST", f"/session/{self.session_id}/abort")

    async def answer(self, request_id: str, decision: InteractionDecision) -> None:
        reply = "once" if decision == "approved" else "reject"
        await self._http.request("POST", f"/permission/{request_id}/reply", {"reply": reply})

    async def capture_state(self) -> EngineState:
        process = await self._sandbox.exec(
            ["sh", "-c", _EXPORT_TO_FILE, "export", self.session_id, EXPORT_PATH], env=self._env
        )
        _, code = await finish(process, timeout_s=EXPORT_TIMEOUT_S)
        if code != 0:
            raise _export_failed(f"opencode export exited with {code}", code)
        reader = await self._sandbox.exec(["cat", EXPORT_PATH], env=self._env)
        out, code = await finish(reader, timeout_s=EXPORT_TIMEOUT_S)
        if code != 0:
            raise _export_failed(f"reading the opencode export exited with {code}", code)
        state = encode_state(self._engine, self.session_id, out, secrets=self._secrets)
        self._log.info("engine.state.captured", bytes=len(state.payload), schemaVersion=state.schema_version)
        return state

    async def health(self) -> SessionHealth:
        if self._closed:
            return SessionHealth(alive=False, engine_version=self._engine.version, detail="closed")
        try:
            body = as_object(await self._http.request("GET", "/global/health"))
        except (OpencodeHttpError, OSError) as exc:
            return SessionHealth(alive=False, detail=type(exc).__name__)
        return SessionHealth(
            alive=body.get("healthy") is True, engine_version=as_str(body.get("version")) or None
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        await self._http.aclose()
        code = await stop_process(self._process, schedule=SERVER_STOP_SCHEDULE)
        if self._output is not None:
            _ = self._output.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._output
        self._log.info("engine.session.closed", exitCode=code)

def _export_failed(message: str, code: int) -> Exception:
    # Killed (the engine binary crash-looping, P9 FM-10): the turn is lost, not the kernel's error.
    return EngineCrashed(message) if killed_by_signal(code) else RuntimeError(message)


async def finish(process: ProcessHandle, *, timeout_s: float) -> tuple[bytes, int]:
    """Stdout and exit code of a short command. Both streams are drained: the port bounds what
    it buffers, so an unread stderr stalls the process (the E5 ``opencode export`` stall)."""
    try:
        out, _ = await asyncio.wait_for(asyncio.gather(collect(process.stdout), collect(process.stderr)), timeout_s)
        return out, await process.wait(timeout_s=timeout_s)
    except TimeoutError:
        await process.signal(signal.SIGKILL)
        raise


__all__ = ["OpencodeSession", "finish"]
