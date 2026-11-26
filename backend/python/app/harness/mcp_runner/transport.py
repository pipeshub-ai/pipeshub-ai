"""An MCP SDK transport whose STDIO server lives in an MCP runner (P5.10), not in this process.

Entering ``RunnerClientTransport`` opens one runner session (one server process in the
runner) and yields the SDK's read and write streams over it: newline-delimited JSON-RPC,
exactly what the SDK's own STDIO client writes to a child's stdin.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast, override

import anyio
import mcp_types
from mcp.shared.message import SessionMessage

from harness_core.refusals import HarnessRefusal
from harness_core.sandbox.mcp_runner.errors import MCPRunnerRefused

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
    from mcp.client._transport import TransportStreams

    from harness_core.sandbox.mcp_runner.ports import MCPRunner
    from harness_core.sandbox.mcp_runner.types import RunnerRequest
    from harness_core.sandbox.ports import ByteStream

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunnerTarget:
    """Where a STDIO instance runs: the process's runner and the request naming its runner."""

    runner: MCPRunner
    request: RunnerRequest

    async def open(self) -> ByteStream:
        return await self.runner.open(self.request)


async def _read_messages(stream: ByteStream, sink: MemoryObjectSendStream[SessionMessage | Exception]) -> None:
    pending = b""
    async with sink:
        while True:
            chunk = await stream.read()
            if not chunk:
                return
            *lines, pending = (pending + chunk).split(b"\n")
            for line in lines:
                if not line.strip():
                    continue
                try:
                    message = mcp_types.jsonrpc_message_adapter.validate_json(line, by_name=False)
                except Exception as exc:
                    await sink.send(exc)
                    continue
                await sink.send(SessionMessage(message))


async def _write_messages(stream: ByteStream, source: MemoryObjectReceiveStream[SessionMessage]) -> None:
    async with source:
        async for item in source:
            payload = item.message.model_dump_json(by_alias=True, exclude_unset=True)
            await stream.write(payload.encode("utf-8") + b"\n")


@contextlib.asynccontextmanager
async def streams_over(stream: ByteStream) -> AsyncIterator[TransportStreams]:
    """The SDK's read and write streams over one runner session; the stream is closed when they end."""
    to_client, from_server = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    to_server, from_client = anyio.create_memory_object_stream[SessionMessage](0)
    try:
        async with anyio.create_task_group() as group:
            group.start_soon(_read_messages, stream, to_client)
            group.start_soon(_write_messages, stream, from_client)
            try:
                yield from_server, to_server
            finally:
                group.cancel_scope.cancel()
    finally:
        await stream.aclose()


def wrapped_refusal(exc: BaseException) -> HarnessRefusal | None:
    """A typed refusal behind an error the MCP client wrapped (causes, contexts, exception groups)."""
    stack: list[BaseException] = [exc]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, HarnessRefusal):
            return current
        if isinstance(current, BaseExceptionGroup):
            stack.extend(cast("BaseExceptionGroup[BaseException]", current).exceptions)
        stack.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return None


def runner_refusal(exc: BaseException) -> MCPRunnerRefused | None:
    """The runner's typed refusal behind an error the MCP client wrapped."""
    refused = wrapped_refusal(exc)
    return refused if isinstance(refused, MCPRunnerRefused) else None


class RunnerClientTransport(contextlib.AbstractAsyncContextManager["TransportStreams"]):
    """An SDK ``Transport``: one runner session per entry."""

    def __init__(self, target: RunnerTarget) -> None:
        self._target = target
        self._streams: contextlib.AbstractAsyncContextManager[TransportStreams] | None = None

    @override
    async def __aenter__(self) -> TransportStreams:
        self._streams = streams_over(await self._target.open())
        return await self._streams.__aenter__()

    @override
    async def __aexit__(self, *exc: Any) -> bool | None:
        streams, self._streams = self._streams, None
        return await streams.__aexit__(*exc) if streams is not None else None

    @override
    def __repr__(self) -> str:
        return f"<RunnerClientTransport server={self._target.request.key.server_id}>"


__all__ = ["RunnerClientTransport", "RunnerTarget", "runner_refusal", "streams_over", "wrapped_refusal"]
