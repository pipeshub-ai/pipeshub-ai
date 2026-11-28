"""A Docker exec's process settles its exit however its multiplexed stream ends (no daemon needed)."""

from __future__ import annotations

import asyncio
import struct

import pytest

from app.harness.sandbox.docker_broker import DockerProcess
from harness_core.sandbox.streams import collect


class _Broker:
    """The two calls a process makes on its broker: the exec's exit code and a signal."""

    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code_value = exit_code
        self.signals: list[int] = []

    async def exit_code(self, exec_id: str) -> int:
        return self.exit_code_value

    async def send_signal(self, sandbox: object, pidfile: str, sig: int) -> None:
        self.signals.append(sig)


class _Writer:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _frame(stream: int, payload: bytes) -> bytes:
    return struct.pack(">BxxxL", stream, len(payload)) + payload


def _process(reader: asyncio.StreamReader, broker: _Broker, writer: _Writer) -> DockerProcess:
    return DockerProcess(
        broker,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        "exec-1",
        "/tmp/pid",
        reader,
        writer,  # type: ignore[arg-type]
        stdin_open=False,
    )


async def test_fx2_1_a_stream_that_breaks_mid_exec_fails_wait_at_once_and_ends_the_output() -> None:
    reader = asyncio.StreamReader()
    broker, writer = _Broker(), _Writer()
    process = _process(reader, broker, writer)
    stdout = process.stdout
    reader.feed_data(_frame(1, b"first frame"))
    assert await anext(stdout) == b"first frame"
    reader.set_exception(ConnectionResetError("daemon connection reset"))

    with pytest.raises(ConnectionResetError):
        _ = await process.wait(timeout_s=5)

    assert await collect(stdout) == b""
    assert await collect(process.stderr) == b""
    assert writer.closed
    await process.signal(9)
    assert broker.signals == []


async def test_fx2_1_a_stream_that_ends_cleanly_settles_with_the_exec_exit_code() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(_frame(1, b"out") + _frame(2, b"err"))
    reader.feed_eof()
    process = _process(reader, _Broker(exit_code=3), _Writer())

    assert await process.wait(timeout_s=5) == 3
    assert (await collect(process.stdout), await collect(process.stderr)) == (b"out", b"err")
