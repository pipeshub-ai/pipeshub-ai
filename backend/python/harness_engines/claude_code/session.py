"""``EngineSession`` over one ``claude -p`` stream-json process in one sandbox."""

from __future__ import annotations

import asyncio
import posixpath
from typing import TYPE_CHECKING

from harness_core.context.user_compact import CompactReport
from harness_core.engines.errors import CompactFailed, EngineSessionClosed
from harness_core.engines.events import TurnCompleted
from harness_core.engines.jsonview import as_int, as_object, as_str
from harness_core.engines.native import set_recorded
from harness_core.engines.stopping import stop_process
from harness_core.engines.types import EngineTask, SessionHealth
from harness_core.sandbox.tar import read_tar_stream
from harness_engines.claude_code.launch import TRANSCRIPT_DIR, WORKDIR, system_prompt
from harness_engines.claude_code.mapper import ClaudeEventMapper
from harness_engines.claude_code.state import encode_state, valid_session_id
from harness_engines.claude_code.stream import (
    ClaudeStreamEnded,
    StreamEnd,
    user_message,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from harness_core.engines.events import EngineEvent, EngineState
    from harness_core.engines.native import NativeTape
    from harness_core.engines.types import EngineVersion, FollowUp, JsonValue
    from harness_core.interactions.ports import InteractionDecision
    from harness_core.logs import HarnessLog
    from harness_core.sandbox import ProcessHandle, SandboxHandle
    from harness_engines.claude_code.stream import ClaudeStream

# What a saved transcript says in place of the person's Claude account email (SIGNIN).
REDACTED_ACCOUNT = b"[the signed-in Claude account]"
TURN_START_TIMEOUT_S = 30.0
STEER_ACK_TIMEOUT_S = 30.0
# A compaction is one summarising model call over the whole session.
COMPACT_TIMEOUT_S = 600.0
# Claude Code's own compaction: no control request runs it in stream-json mode (2.1.294 has none), so the adapter
# writes the command itself. Only ``compact`` writes this, with the focus flattened to one line; user text reaches
# this session through ``LiteralTextSession``, which makes a leading ``/`` text (SLASH0).
COMPACT_COMMAND = "/compact"


class ClaudeSession:
    def __init__(
        self,
        *,
        engine: EngineVersion,
        sandbox: SandboxHandle,
        process: ProcessHandle,
        stream: ClaudeStream,
        reported_version: str,
        resume_session: str | None,
        secrets: frozenset[str],
        log: HarnessLog,
        own_login: bool = False,
        redact: tuple[str, ...] = (),
    ) -> None:
        self._engine = engine
        self._sandbox = sandbox
        self._process = process
        self._stream = stream
        self._reported_version = reported_version
        self.session_id: str | None = resume_session
        self._secrets = secrets
        self._log = log.bind(sandboxId=sandbox.id)
        if resume_session is not None:
            self._log = self._log.bind(engineSessionId=resume_session)
        self._initialized = False
        # The instructions the system prompt was set with: a turn carrying the same ones does not repeat them.
        self._initialized_with = ""
        self._mapper: ClaudeEventMapper | None = None
        self._turn_running = asyncio.Event()
        # Steers sent and not yet taken into a turn, by uuid; each future resolves once Claude Code queues it.
        self._steers: dict[str, asyncio.Future[None]] = {}
        self._closed = False
        # The native transcript of each turn (C2.39), when the controller records one.
        self._tape: NativeTape | None = None
        # On the person's own plan (SIGNIN): sign-in failures get their own codes and no dollar figure is reported.
        self._own_login = own_login
        # Claude Code tells the model the signed-in account's email, so its transcript holds it: never saved (SIGNIN).
        self._redact = tuple(r for r in redact if r)

    async def _initialize(self, instructions: str) -> None:
        """The first turn's instructions go into the system prompt; ``initialize`` replaces any appended prompt."""
        await self._stream.control("initialize", {"appendSystemPrompt": system_prompt(instructions)})
        self._initialized = True
        self._initialized_with = instructions

    def tap_native(self, tape: NativeTape) -> None:
        self._tape = tape

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        if self._closed:
            raise EngineSessionClosed("session is closed")
        instructions = task.instructions if isinstance(task, EngineTask) else ""
        text = task.prompt if isinstance(task, EngineTask) else task.text
        if not self._initialized:
            await self._initialize(instructions)
            if instructions and self.session_id is not None:
                # A resumed session keeps the system prompt it was started with (``initialize`` does not replace
                # it), so a later run's own instructions (its recalled memories among them) travel with its first
                # turn (FIX-P2-B2).
                text = f"{instructions}\n\n{text}"
        elif instructions and instructions != self._initialized_with:
            # The system prompt is fixed for the process; later instructions travel with the turn (E8 D-5).
            text = f"{instructions}\n\n{text}"
        mapper = ClaudeEventMapper(turn_id=task.turn_id, workdir=WORKDIR, own_login=self._own_login, tape=self._tape)
        self._mapper = mapper
        self._log.info("engine.turn.started", turnId=task.turn_id)
        try:
            await self._stream.send(user_message(text))
            self._turn_running.set()
            while not mapper.done:
                message = await self._stream.incoming.get()
                if isinstance(message, StreamEnd):
                    self._log.warning(
                        "engine.stream.failed",
                        turnId=task.turn_id,
                        reasonCode="stream_failed",
                        errorType=type(message.error).__name__ if message.error is not None else "eof",
                    )
                    # The engine went away mid-turn: lost, never completed (LCFIX); the next attempt resumes it.
                    raise ClaudeStreamEnded("the engine event stream ended before the turn completed") from message.error
                self._learn_session(message)
                kind = as_str(message.get("type"))
                if kind == "control_request":
                    await self._refuse(message)
                    continue
                if kind == "command_lifecycle":
                    events = mapper.finish() if self._steer_fate(message) and not self._steers else []
                else:
                    set_recorded(mapper, "steers_waiting", bool(self._steers))
                    events = mapper.feed(message)
                for event in events:
                    if isinstance(event, TurnCompleted):
                        self._log.info(
                            "engine.turn.completed", turnId=task.turn_id, reason=event.reason, events=event.meta.seq + 1
                        )
                        if mapper.denied:
                            self._log.warning(
                                "engine.native_request.refused",
                                turnId=task.turn_id,
                                reasonCode="native_request",
                                count=mapper.denied,
                            )
                    yield event
        finally:
            self._mapper = None
            self._turn_running.clear()
            self._drop_steers("turn_ended")

    async def compact(self, focus: str | None, *, instructions: str = "") -> CompactReport:
        """Claude Code's own compaction of the resumed session (SLASH2 C2): the adapter writes ``/compact [focus]``
        and the compaction is done when Claude Code reports a ``compact_boundary`` and ends with its result."""
        if self._closed:
            raise EngineSessionClosed("session is closed")
        if not self._initialized:
            await self._initialize(instructions)
        text = " ".join((COMPACT_COMMAND, *(focus or "").split()))
        self._log.info("engine.compact.started", focus=focus is not None)
        await self._stream.send(user_message(text))
        try:
            return await asyncio.wait_for(self._compaction(), COMPACT_TIMEOUT_S)
        except TimeoutError as exc:
            raise CompactFailed("Claude Code did not finish compacting") from exc

    async def _compaction(self) -> CompactReport:
        boundary: Mapping[str, JsonValue] | None = None
        while True:
            message = await self._stream.incoming.get()
            if isinstance(message, StreamEnd):
                raise CompactFailed("Claude Code's output ended while compacting")
            self._learn_session(message)
            kind = as_str(message.get("type"))
            if kind == "control_request":
                await self._refuse(message)
            elif kind == "system" and as_str(message.get("subtype")) == "compact_boundary":
                boundary = as_object(message.get("compact_metadata"))
            elif kind == "result":
                if boundary is None or message.get("is_error") is True:
                    raise CompactFailed(f"Claude Code ended without compacting ({as_str(message.get('subtype'))})")
                after = as_int(boundary.get("post_tokens"))
                return CompactReport("compacted", as_int(boundary.get("pre_tokens")) or None, after or None)

    def _learn_session(self, message: Mapping[str, JsonValue]) -> None:
        found = as_str(message.get("session_id"))
        if self.session_id is None and valid_session_id(found):
            self.session_id = found
            self._log = self._log.bind(engineSessionId=found)

    async def _refuse(self, message: Mapping[str, JsonValue]) -> None:
        """Claude Code asks nobody in ``dontAsk``; any request it still sends is answered no without the controller."""
        request_id = as_str(message.get("request_id"))
        request = message.get("request")
        subtype = as_str(request.get("subtype")) if isinstance(request, dict) else ""
        if subtype == "can_use_tool":
            await self._stream.reply(request_id, {"behavior": "deny", "message": "not permitted in this sandbox"})
        else:
            await self._stream.send(
                {
                    "type": "control_response",
                    "response": {"subtype": "error", "request_id": request_id, "error": "not served by this host"},
                }
            )
        self._log.warning("engine.native_request.refused", reasonCode="native_request", method=subtype)

    async def steer(self, text: str) -> None:
        """A user message written while a turn runs joins that turn: at its next model call, or, when the turn
        ends first, in the turn Claude Code starts for it, which this turn runs on through. Returns once Claude
        Code has queued it."""
        await self._running()
        mapper = self._mapper
        if mapper is None or mapper.done:
            raise EngineSessionClosed("the turn has ended")
        message = user_message(text)
        command = as_str(message.get("uuid"))
        queued: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._steers[command] = queued
        try:
            await self._stream.send(message)
            await asyncio.wait_for(queued, STEER_ACK_TIMEOUT_S)
        except BaseException:
            self._steers.pop(command, None)
            raise

    def _steer_fate(self, message: Mapping[str, JsonValue]) -> bool:
        """Track a steer's ``command_lifecycle``; true when Claude Code will never take it into a turn."""
        command, state = as_str(message.get("command_uuid")), as_str(message.get("state"))
        queued = self._steers.get(command)
        if queued is None:
            return False
        if state == "queued":
            if not queued.done():
                queued.set_result(None)
            return False
        del self._steers[command]
        if state == "started":
            return False
        self._log.warning("engine.steer.dropped", reasonCode=state)
        if not queued.done():
            queued.set_exception(EngineSessionClosed(f"Claude Code did not take the message ({state})"))
        return True

    def _drop_steers(self, reason: str) -> None:
        """A turn that ends with a steer not taken in (interrupt, broken stream) took it nowhere: say so."""
        if self._steers:
            self._log.warning("engine.steer.dropped", reasonCode=reason, count=len(self._steers))
        for queued in self._steers.values():
            if not queued.done():
                queued.set_exception(EngineSessionClosed("the turn ended before the message joined it"))
        self._steers.clear()

    async def interrupt(self) -> None:
        if self._mapper is not None:
            if self._mapper.done:
                self._log.info("engine.turn.interrupt_after_end", source="state")
                return
            self._mapper.mark_interrupted()
        self._log.info("engine.turn.interrupt")
        await self._running()
        # Queued steers stop with the turn; left queued, Claude Code would run them as a turn nobody reads.
        await self._stream.control("interrupt", {"cancel_queued": True})

    async def _running(self) -> None:
        await asyncio.wait_for(self._turn_running.wait(), TURN_START_TIMEOUT_S)
        if self._mapper is None:
            raise EngineSessionClosed("no turn is running")

    async def answer(self, request_id: str, decision: InteractionDecision) -> None:
        raise EngineSessionClosed(f"Claude Code raised no ask {request_id} ({decision}): asks are refused at once")

    async def capture_state(self) -> EngineState:
        if self.session_id is None:
            raise RuntimeError("no session to capture: the session ran no turn")
        files = await read_tar_stream(await self._sandbox.get_tar(TRANSCRIPT_DIR))
        name = f"{self.session_id}.jsonl"
        # Directly in the project folder: a sub-agent's transcripts live below it, under the session's id.
        found = [
            content
            for path, content in files.items()
            if posixpath.basename(path) == name and posixpath.dirname(path).strip("./") in ("", _tail(TRANSCRIPT_DIR))
        ]
        if len(found) != 1:
            raise RuntimeError(f"expected one transcript for the session, found {len(found)}")
        transcript = found[0]
        for value in self._redact:
            transcript = transcript.replace(value.encode(), REDACTED_ACCOUNT)
        state = encode_state(self._engine, self.session_id, transcript, secrets=self._secrets)
        self._log.info("engine.state.captured", bytes=len(state.payload), schemaVersion=state.schema_version)
        return state

    async def health(self) -> SessionHealth:
        if self._closed:
            return SessionHealth(alive=False, engine_version=self._engine.version, detail="closed")
        if self._stream.ended:
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
        await self._stream.close()
        code = await stop_process(self._process)
        self._log.info("engine.session.closed", exitCode=code, unparsedLines=self._stream.unparsed_lines)


def _tail(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1]


__all__ = ["ClaudeSession"]
