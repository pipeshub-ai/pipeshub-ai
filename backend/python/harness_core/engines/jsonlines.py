"""JSON objects, one per line, over an engine process's stdin and stdout (Codex, Claude Code).

The reader hands every parsed object to the owner's callback in arrival order
and never blocks on a consumer: the owner queues what a turn reads. Stderr is
drained and counted, its tail kept for diagnosis; it is never logged (it can
carry request details). Lines are read at no more than the channel's event
rate (SEC-DOS-02): past it the reader waits, so the engine blocks on its own
output, and an engine that floods for longer than the throttle allows ends the
stream with ``EngineEventFlood``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING

from harness_core.engines.jsonview import as_object
from harness_core.engines.rate import EventRate, EventRateLimiter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.sandbox import ProcessHandle

STDERR_TAIL_BYTES = 4096


class OversizedLine(Exception):
    """A line longer than the channel's bound: the peer is not speaking the protocol, so the stream stops."""


class StreamEnd:
    """Queued after the last message: the reader has stopped."""

    def __init__(self, error: BaseException | None) -> None:
        self.error = error


def split_lines(buffer: bytes, chunk: bytes) -> tuple[list[bytes], bytes]:
    """Complete lines of ``buffer + chunk`` and the unterminated rest."""
    data = buffer + chunk
    *lines, rest = data.split(b"\n")
    return [line for line in lines if line.strip()], rest


class JsonLinesChannel:
    """``on_message`` gets each object; ``on_end`` once, with the error that stopped the reader (or ``None``)."""

    def __init__(
        self,
        process: ProcessHandle,
        on_message: Callable[[Mapping[str, JsonValue]], Awaitable[None]],
        on_end: Callable[[BaseException | None], Awaitable[None]],
        *,
        max_line_bytes: int | None = None,
        rate: EventRate | None = None,
    ) -> None:
        self._process = process
        # The queue the owner keeps never blocks this reader, so the rate is what bounds it (SEC-DOS-02).
        self._rate = EventRateLimiter(rate or EventRate(), clock=time.monotonic, sleep=asyncio.sleep)
        # ``None``: unbounded, as the native engines' channels have always been.
        self._max_line = max_line_bytes
        self._on_message = on_message
        self._on_end = on_end
        self._write_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self.ended = False
        self.unparsed_lines = 0
        self.stderr_bytes = 0
        self.stderr_tail = b""

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._read()), loop.create_task(self._drain_stderr())]

    async def _read(self) -> None:
        error: BaseException | None = None
        buffer = b""
        try:
            async for chunk in self._process.stdout:
                lines, buffer = split_lines(buffer, chunk)
                if self._max_line is not None and (
                    len(buffer) > self._max_line or any(len(line) > self._max_line for line in lines)
                ):
                    raise OversizedLine(f"a line passed {self._max_line} bytes")
                for line in lines:
                    await self._rate.take()
                    await self._dispatch(line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # reported to the owner, which fails what waits on the stream
            error = exc
        self.ended = True
        await self._on_end(error)

    async def _dispatch(self, line: bytes) -> None:
        try:
            value: JsonValue = json.loads(line)
        except ValueError:
            self.unparsed_lines += 1
            return
        message = as_object(value)
        if not message:
            self.unparsed_lines += 1
            return
        await self._on_message(message)

    async def _drain_stderr(self) -> None:
        with contextlib.suppress(Exception):
            async for chunk in self._process.stderr:
                self.stderr_bytes += len(chunk)
                self.stderr_tail = (self.stderr_tail + chunk)[-STDERR_TAIL_BYTES:]

    async def write(self, message: Mapping[str, JsonValue]) -> None:
        data = json.dumps(message, separators=(",", ":")).encode() + b"\n"
        async with self._write_lock:
            await self._process.write(data)

    async def close(self) -> None:
        """Close stdin (the engine exits on EOF) and stop reading."""
        with contextlib.suppress(Exception):
            await self._process.close_stdin()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.ended = True


__all__ = ["STDERR_TAIL_BYTES", "JsonLinesChannel", "OversizedLine", "StreamEnd", "split_lines"]
