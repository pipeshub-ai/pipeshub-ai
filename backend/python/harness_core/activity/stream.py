"""``BoundedCommandActivity``: sandbox command output, redacted, live on the activity bus and bounded for the journal.

A command never waits for the bus. Frames go into a bounded queue that one pump publishes; queued output for the
same command and stream merges up to a frame's size, and what does not fit is counted and announced once the bus
catches up (``dropped``), while the tail and the log keep it. The bus is best effort: a failure is logged once and
later frames are dropped, the record is unaffected.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.activity.redaction import StreamRedactor, shared_redactor
from harness_core.activity.types import (
    COMMAND_FINISHED,
    COMMAND_OUTPUT,
    COMMAND_STARTED,
    ActivityLimits,
    CommandRecord,
    CommandSpec,
    CommandStatus,
    OutputStream,
    OutputTail,
)
from harness_core.journal.types import LiveFrame
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Generator

    from pydantic import JsonValue

    from harness_core.activity.ports import CommandRecorder
    from harness_core.activity.redaction import Redactor
    from harness_core.clock import Clock
    from harness_core.journal.ports import ContentStorePort, LiveSinkPort
    from harness_core.journal.types import ContentRef

_RECORDER: ContextVar[CommandRecorder | None] = ContextVar("harness_command_recorder", default=None)


def current_command_recorder() -> CommandRecorder | None:
    """The recorder of the tool call running in this task, if its route set one (``recording``)."""
    return _RECORDER.get()


@contextlib.contextmanager
def recording(recorder: CommandRecorder | None) -> Generator[None]:
    token = _RECORDER.set(recorder)
    try:
        yield
    finally:
        _RECORDER.reset(token)


@dataclass
class _Queued:
    kind: str
    data: dict[str, JsonValue]


@dataclass
class _Stream:
    redactor: StreamRedactor
    tail: str = ""
    chars: int = 0
    bytes: int = 0
    unshown: int = 0


def _epoch_ms(clock: Clock) -> int:
    return int(clock.now().timestamp() * 1000)


class BoundedCommandActivity:
    def __init__(
        self,
        bus: LiveSinkPort | None,
        *,
        run_id: str,
        attempt_id: str,
        fence: int,
        clock: Clock,
        content: ContentStorePort | None = None,
        redactor: Redactor | None = None,
        limits: ActivityLimits | None = None,
        log: HarnessLog | None = None,
    ) -> None:
        self._bus = bus
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._fence = fence
        self._clock = clock
        self._content = content
        self._redact = redactor or shared_redactor()
        self.limits = limits or ActivityLimits()
        self._log = log or HarnessLog.named("harness_core.activity")
        self._queue: deque[_Queued] = deque()
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._pump: asyncio.Task[None] | None = None
        self._n = 0
        self._degraded = False

    def begin(self, spec: CommandSpec) -> _Recorder:
        shown = CommandSpec(
            command_id=spec.command_id,
            source=spec.source,
            command=self._redact(spec.command)[: self.limits.command_chars],
            invocation_id=spec.invocation_id,
            task_id=spec.task_id,
            language=spec.language,
            output_may_be_lost=spec.output_may_be_lost,
        )
        recorder = _Recorder(self, shown, _epoch_ms(self._clock), self._clock.monotonic())
        started: dict[str, JsonValue] = {
            "commandId": shown.command_id,
            "source": shown.source,
            "command": shown.command,
            "startedAt": recorder.started_at_ms,
            "attemptId": self._attempt_id,
        }
        optional = {"invocationId": shown.invocation_id, "taskId": shown.task_id, "language": shown.language}
        started.update({key: value for key, value in optional.items() if value is not None})
        if shown.output_may_be_lost:
            started["outputMayBeLost"] = True
        _ = self.offer(COMMAND_STARTED, started, essential=True)
        return recorder

    def stream_redactor(self) -> StreamRedactor:
        return StreamRedactor(self._redact, hold_chars=self.limits.hold_chars, key_hold_chars=self.limits.key_hold_chars)

    def offer(self, kind: str, data: dict[str, JsonValue], *, essential: bool = False) -> bool:
        """Queue a frame; ``False`` when output would overflow the queue (essential frames always fit)."""
        if self._bus is None or self._degraded:
            return essential
        last = self._queue[-1] if self._queue else None
        if (
            kind == COMMAND_OUTPUT
            and last is not None
            and last.kind == COMMAND_OUTPUT
            and "text" in data
            and "text" in last.data
            and last.data["commandId"] == data["commandId"]
            and last.data["stream"] == data["stream"]
            and len(str(last.data["text"])) + len(str(data["text"])) <= self.limits.frame_chars
        ):
            last.data["text"] = str(last.data["text"]) + str(data["text"])
            return True
        if not essential and len(self._queue) >= self.limits.queue_frames:
            return False
        self._queue.append(_Queued(kind, data))
        self._kick()
        return True

    def _kick(self) -> None:
        self._idle.clear()
        self._wake.set()
        if self._pump is None:
            try:
                self._pump = asyncio.get_running_loop().create_task(self._run())
            except RuntimeError:
                return

    async def _run(self) -> None:
        bus = self._bus
        assert bus is not None
        while True:
            _ = await self._wake.wait()
            self._wake.clear()
            while self._queue:
                item = self._queue.popleft()
                self._n += 1
                try:
                    await bus.publish(LiveFrame(self._run_id, self._fence, 0, self._n, item.kind, item.data))  # pyright: ignore[reportArgumentType]
                except Exception as exc:
                    self._degraded = True
                    self._queue.clear()
                    self._log.warning("activity.live_degraded", runId=self._run_id, errorType=type(exc).__name__)
                    break
            self._idle.set()

    def has_room(self) -> bool:
        return len(self._queue) < self.limits.queue_frames

    def elapsed_ms(self, since: float) -> int:
        return max(0, int((self._clock.monotonic() - since) * 1000))

    async def drained(self) -> None:
        """Every queued frame published (for tests and ``aclose``)."""
        _ = await self._idle.wait()

    async def aclose(self) -> None:
        if self._pump is None:
            return
        try:
            _ = await asyncio.wait_for(self._idle.wait(), timeout=self.limits.drain_timeout_s)
        except TimeoutError:
            self._log.info("activity.drain_timed_out", runId=self._run_id, queued=len(self._queue))
        _ = self._pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._pump
        self._pump = None

    async def store_log(self, data: bytes) -> ContentRef | None:
        if self._content is None or not data:
            return None
        try:
            return await self._content.put(data)
        except Exception as exc:
            self._log.warning("activity.log_unstored", runId=self._run_id, errorType=type(exc).__name__)
            return None


class _Recorder:
    def __init__(self, owner: BoundedCommandActivity, spec: CommandSpec, started_at_ms: int, started: float) -> None:
        self._owner = owner
        self.spec = spec
        self.started_at_ms = started_at_ms
        self._started = started
        self._streams: dict[OutputStream, _Stream] = {
            "stdout": _Stream(owner.stream_redactor()),
            "stderr": _Stream(owner.stream_redactor()),
        }
        self._log: list[str] = []
        self._log_bytes = 0
        self._log_truncated = False
        self._live_chars = 0
        self._capped = False
        self._dropped = 0
        self._finished: CommandRecord | None = None

    def write(self, stream: OutputStream, text: str) -> None:
        if self._finished is not None or not text:
            return
        state = self._streams[stream]
        state.bytes += len(text.encode("utf-8", "replace"))
        shown = state.redactor.feed(text)
        if shown:
            self._accept(stream, shown)

    def _accept(self, stream: OutputStream, shown: str) -> None:
        limits = self._owner.limits
        state = self._streams[stream]
        state.chars += len(shown)
        state.tail = (state.tail + shown)[-limits.tail_chars :]
        self._keep_log(shown)
        for start in range(0, len(shown), limits.frame_chars):
            self._publish(stream, shown[start : start + limits.frame_chars])

    def _keep_log(self, shown: str) -> None:
        if self._log_truncated:
            return
        encoded = shown.encode("utf-8", "replace")
        room = self._owner.limits.log_bytes - self._log_bytes
        if len(encoded) > room:
            self._log.append(encoded[:room].decode("utf-8", "ignore"))
            self._log_bytes += room
            self._log_truncated = True
            return
        self._log.append(shown)
        self._log_bytes += len(encoded)

    def _publish(self, stream: OutputStream, piece: str) -> None:
        state = self._streams[stream]
        size = len(piece.encode("utf-8", "replace"))
        if self._capped or self._live_chars + len(piece) > self._owner.limits.live_chars_per_command:
            # Past the cap nothing more is shown live, so the panel never shows output with a hole in it.
            self._capped = True
            state.unshown += size
            return
        self._announce_unshown(stream)
        if state.unshown:
            state.unshown += size
            return
        data: dict[str, JsonValue] = {"commandId": self.spec.command_id, "stream": stream, "text": piece}
        if self._owner.offer(COMMAND_OUTPUT, data):
            self._live_chars += len(piece)
        else:
            state.unshown += size

    def _announce_unshown(self, stream: OutputStream) -> None:
        state = self._streams[stream]
        if state.unshown and self._owner.has_room():
            marker: dict[str, JsonValue] = {"commandId": self.spec.command_id, "stream": stream, "dropped": state.unshown}
            _ = self._owner.offer(COMMAND_OUTPUT, marker, essential=True)
            self._dropped += state.unshown
            state.unshown = 0

    async def finish(
        self, *, exit_code: int | None, status: CommandStatus, duration_ms: int | None = None
    ) -> CommandRecord:
        if self._finished is not None:
            return self._finished
        owner = self._owner
        for stream, state in self._streams.items():
            rest = state.redactor.flush()
            if rest:
                self._accept(stream, rest)
        for stream, state in self._streams.items():
            if state.unshown:
                marker: dict[str, JsonValue] = {"commandId": self.spec.command_id, "stream": stream, "dropped": state.unshown}
                _ = owner.offer(COMMAND_OUTPUT, marker, essential=True)
                self._dropped += state.unshown
                state.unshown = 0
        if duration_ms is None:
            duration_ms = owner.elapsed_ms(self._started)
        _ = owner.offer(
            COMMAND_FINISHED,
            {"commandId": self.spec.command_id, "status": status, "exitCode": exit_code, "durationMs": duration_ms},
            essential=True,
        )
        log_ref = await owner.store_log("".join(self._log).encode("utf-8", "replace"))
        self._finished = CommandRecord(
            spec=self.spec,
            status=status,
            exit_code=exit_code,
            started_at_ms=self.started_at_ms,
            duration_ms=duration_ms,
            stdout=self._tail("stdout"),
            stderr=self._tail("stderr"),
            live_dropped=self._dropped,
            log=log_ref,
            log_truncated=self._log_truncated,
        )
        return self._finished

    def _tail(self, stream: OutputStream) -> OutputTail:
        state = self._streams[stream]
        return OutputTail(tail=state.tail, bytes=state.bytes, truncated=state.chars > len(state.tail))


__all__ = ["BoundedCommandActivity", "current_command_recorder", "recording"]
