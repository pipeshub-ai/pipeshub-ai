"""``MCPRunner`` with in-process servers: no sandbox, for tests and embedders' unit suites.

A server program is looked up by the spec's ``command`` and runs as a task per
session, reading and writing newline-delimited JSON-RPC on its end of an
in-memory stream, with the runner's environment (base plus secrets).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, override

from harness_core.sandbox.mcp_runner.errors import RunnerStartFailed
from harness_core.sandbox.mcp_runner.table import RunnerTable
from harness_core.sandbox.streams import memory_stream_pair

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from harness_core.clock import Clock
    from harness_core.ids import IdSource
    from harness_core.sandbox.mcp_runner.types import RunnerConfig, RunnerRequest
    from harness_core.sandbox.ports import ByteStream

    InProcessServer = Callable[[ByteStream, Mapping[str, str], Sequence[str]], Awaitable[None]]


class _MemoryRunner:
    def __init__(self, program: InProcessServer, env: Mapping[str, str], argv: Sequence[str]) -> None:
        self._program = program
        self._env = dict(env)
        self._argv = list(argv)
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = False

    @property
    def sandbox_id(self) -> str | None:
        return None

    def alive(self) -> bool:
        return not self._stopped

    async def connect(self) -> ByteStream:
        client, server = memory_stream_pair()
        task = asyncio.ensure_future(self._serve(server))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return client

    async def _serve(self, stream: ByteStream) -> None:
        try:
            await self._program(stream, self._env, self._argv)
        finally:
            await stream.aclose()

    async def stop(self) -> None:
        self._stopped = True
        for task in list(self._tasks):
            _ = task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


class InMemoryMCPRunner(RunnerTable):
    def __init__(
        self,
        programs: Mapping[str, InProcessServer],
        *,
        clock: Clock,
        ids: IdSource,
        config: RunnerConfig | None = None,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(clock=clock, ids=ids, config=config)
        self._programs = dict(programs)
        self._base_env = dict(base_env or {})

    @override
    async def _start(self, runner_id: str, request: RunnerRequest) -> _MemoryRunner:
        program = self._programs.get(request.spec.command)
        if program is None:
            raise RunnerStartFailed(f"no in-process server for {request.spec.command!r}")
        env = {**self._base_env, **request.secrets.values()}
        return _MemoryRunner(program, env, [request.spec.command, *request.spec.args])


__all__ = ["InMemoryMCPRunner"]
