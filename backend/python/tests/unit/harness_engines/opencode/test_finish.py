"""OC-15 (E0, E5 open item): ``opencode export``/``import`` drain both streams, so a chatty stderr never stalls them.

The sandbox port bounds what a process may buffer; a process whose stderr is
full blocks before it closes stdout. The stub models exactly that.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from harness_engines.opencode.session import finish

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _BoundedProcess:
    """Writes stderr first; stdout is produced only once stderr has been read (a full pipe blocks)."""

    def __init__(self) -> None:
        self._stderr_read = asyncio.Event()
        self.killed = False

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return self._out()

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return self._err()

    async def _out(self) -> AsyncIterator[bytes]:
        await self._stderr_read.wait()
        yield b'{"info": {"id": "s1"}}'

    async def _err(self) -> AsyncIterator[bytes]:
        yield b"log line\n" * 1000
        self._stderr_read.set()

    async def write(self, data: bytes) -> None: ...

    async def close_stdin(self) -> None: ...

    async def signal(self, sig: int) -> None:
        self.killed = True

    async def wait(self, *, timeout_s: float | None = None) -> int:
        await self._stderr_read.wait()
        return 0


async def test_oc15_stdout_is_read_while_stderr_is_drained() -> None:
    out, code = await finish(_BoundedProcess(), timeout_s=2)  # type: ignore[arg-type]
    assert (out, code) == (b'{"info": {"id": "s1"}}', 0)


async def test_oc15_a_stalled_command_is_killed_at_the_timeout() -> None:
    class _Stalled(_BoundedProcess):
        async def _err(self) -> AsyncIterator[bytes]:
            await asyncio.Event().wait()
            yield b""

    process = _Stalled()
    with pytest.raises(TimeoutError):
        await finish(process, timeout_s=0.1)  # type: ignore[arg-type]
    assert process.killed
