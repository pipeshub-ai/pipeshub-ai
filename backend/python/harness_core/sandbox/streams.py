"""Byte-stream plumbing shared by broker adapters, the remote client and the test kit."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.sandbox.errors import InvalidSandboxRequest, SandboxError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable
    from typing import IO

# Chunks buffered per output stream before the producer waits for the reader.
STREAM_BUFFER_CHUNKS = 1024


class ChunkChannel:
    """A bounded single-consumer byte channel; iteration ends when the producer closes it."""

    def __init__(self, max_chunks: int = STREAM_BUFFER_CHUNKS) -> None:
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(max_chunks)
        self._closed = False
        self._closer: asyncio.Task[None] | None = None

    async def put(self, chunk: bytes) -> None:
        if chunk and not self._closed:
            await self._queue.put(chunk)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            # The sentinel must still arrive after the buffered chunks.
            self._closer = asyncio.get_running_loop().create_task(self._queue.put(None))

    @property
    def closed(self) -> bool:
        return self._closed

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                # Leave the sentinel for any later iterator so it ends too.
                self._queue.put_nowait(None)
                return
            yield chunk


async def iter_bytes(data: bytes, chunk_size: int = 65536) -> AsyncIterator[bytes]:
    for start in range(0, len(data), chunk_size):
        yield data[start : start + chunk_size]


async def iter_chunks(chunks: Iterable[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def collect(stream: AsyncIterator[bytes], *, limit: int | None = None) -> bytes:
    """Read a stream to its end; ``limit`` bounds memory and raises ``ValueError`` past it."""
    parts: list[bytes] = []
    size = 0
    async for chunk in stream:
        size += len(chunk)
        if limit is not None and size > limit:
            raise ValueError(f"stream exceeds {limit} bytes")
        parts.append(chunk)
    return b"".join(parts)


async def spool(
    stream: AsyncIterator[bytes], file: IO[bytes], *, in_memory_bytes: int, max_bytes: int | None = None
) -> int:
    """Writes ``stream`` into ``file`` and rewinds it; answers its size. Writes past ``in_memory_bytes``, where a
    spooled temporary file rolls over to disk, run in a thread so a large archive never blocks the event loop.
    Past ``max_bytes`` it raises ``InvalidSandboxRequest``."""
    size = 0
    async for chunk in stream:
        size += len(chunk)
        if max_bytes is not None and size > max_bytes:
            raise InvalidSandboxRequest(f"archive exceeds {max_bytes} bytes")
        if size > in_memory_bytes:
            _ = await asyncio.to_thread(file.write, chunk)
        else:
            _ = file.write(chunk)
    _ = file.seek(0)
    return size


class BytePipe:
    """One direction of an in-memory connection: ``channel`` is the writer's end, ``read`` the reader's."""

    def __init__(self) -> None:
        self.channel = ChunkChannel()
        self._buffer = b""
        self._iterator: AsyncIterator[bytes] | None = None

    async def read(self, max_bytes: int) -> bytes:
        if not self._buffer:
            if self._iterator is None:
                self._iterator = aiter(self.channel)
            try:
                self._buffer = await anext(self._iterator)
            except StopAsyncIteration:
                return b""
        chunk, self._buffer = self._buffer[:max_bytes], self._buffer[max_bytes:]
        return chunk


class MemoryByteStream:
    """One end of an in-memory TCP-like connection (a ``ByteStream``)."""

    def __init__(self, inbound: BytePipe, outbound: BytePipe) -> None:
        self._in = inbound
        self._out = outbound

    async def read(self, max_bytes: int = 65536) -> bytes:
        return await self._in.read(max_bytes)

    async def write(self, data: bytes) -> None:
        if self._out.channel.closed:
            raise SandboxError("stream is closed for writing")
        await self._out.channel.put(data)

    async def write_eof(self) -> None:
        self._out.channel.close()

    async def aclose(self) -> None:
        self._out.channel.close()


def memory_stream_pair() -> tuple[MemoryByteStream, MemoryByteStream]:
    """Two connected ends: what one writes, the other reads."""
    a_to_b, b_to_a = BytePipe(), BytePipe()
    return MemoryByteStream(b_to_a, a_to_b), MemoryByteStream(a_to_b, b_to_a)


__all__ = [
    "STREAM_BUFFER_CHUNKS",
    "BytePipe",
    "ChunkChannel",
    "MemoryByteStream",
    "collect",
    "iter_bytes",
    "iter_chunks",
    "memory_stream_pair",
    "spool",
]
