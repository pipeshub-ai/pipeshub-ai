"""RP-1..RP-4 (E8): JSON-RPC with the app-server over a process's stdin and stdout."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from harness_engines.codex.rpc import (
    CodexRpc,
    CodexRpcError,
    CodexStreamEnded,
    StreamEnd,
    split_lines,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.engines.types import JsonValue


class ScriptedProcess:
    """A ``ProcessHandle`` whose stdout the test feeds and whose stdin it reads."""

    def __init__(self) -> None:
        self.written: list[dict[str, JsonValue]] = []
        self._out: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.stdin_closed = False
        self.wrote = asyncio.Event()

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return self._stdout()

    async def _stdout(self) -> AsyncIterator[bytes]:
        while (chunk := await self._out.get()) is not None:
            yield chunk

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return self._stderr()

    async def _stderr(self) -> AsyncIterator[bytes]:
        yield b"some diagnostics\n"

    def emit(self, chunk: bytes) -> None:
        self._out.put_nowait(chunk)

    def end(self) -> None:
        self._out.put_nowait(None)

    async def write(self, data: bytes) -> None:
        for line in data.splitlines():
            self.written.append(json.loads(line))
        self.wrote.set()

    async def close_stdin(self) -> None:
        self.stdin_closed = True

    async def signal(self, sig: int) -> None:
        self.end()

    async def wait(self, *, timeout_s: float | None = None) -> int:
        return 0


async def _sent(process: ScriptedProcess, count: int) -> None:
    while len(process.written) < count:
        process.wrote.clear()
        await process.wrote.wait()


def test_rp1_lines_split_across_chunks_and_several_per_chunk() -> None:
    lines, rest = split_lines(b"", b'{"a":1}\n{"b"')
    assert (lines, rest) == ([b'{"a":1}'], b'{"b"')
    lines, rest = split_lines(rest, b':2}\n\n{"c":3}\n')
    assert (lines, rest) == ([b'{"b":2}', b'{"c":3}'], b"")


async def test_rp1_requests_correlate_by_id_out_of_order() -> None:
    process = ScriptedProcess()
    rpc = CodexRpc(process)
    rpc.start()
    first = asyncio.ensure_future(rpc.request("a", {"x": 1}))
    second = asyncio.ensure_future(rpc.request("b"))
    await _sent(process, 2)
    assert [m["method"] for m in process.written] == ["a", "b"]
    assert "jsonrpc" not in process.written[0]
    ids = [m["id"] for m in process.written]
    process.emit(json.dumps({"id": ids[1], "result": "B"}).encode() + b"\n" + json.dumps({"id": ids[0], "result": {"ok": True}}).encode()[:5])
    process.emit(json.dumps({"id": ids[0], "result": {"ok": True}}).encode()[5:] + b"\n")
    assert await second == "B"
    assert await first == {"ok": True}
    await rpc.close()
    assert process.stdin_closed


async def test_rp2_an_error_response_raises_with_the_code_only() -> None:
    process = ScriptedProcess()
    rpc = CodexRpc(process)
    rpc.start()
    pending = asyncio.ensure_future(rpc.request("thread/start", {}))
    await _sent(process, 1)
    process.emit(json.dumps({"id": process.written[0]["id"], "error": {"code": -32600, "message": "secret detail"}}).encode() + b"\n")
    with pytest.raises(CodexRpcError) as failed:
        await pending
    assert failed.value.code == -32600
    assert "secret detail" not in str(failed.value)
    await rpc.close()


async def test_rp3_notifications_and_server_requests_arrive_in_order_and_replies_carry_the_id() -> None:
    process = ScriptedProcess()
    rpc = CodexRpc(process)
    rpc.start()
    process.emit(b'{"method":"turn/started","params":{"n":1}}\nnot json\n{"id":7,"method":"item/fileChange/requestApproval","params":{}}\n')
    first, second = await rpc.incoming.get(), await rpc.incoming.get()
    assert not isinstance(first, StreamEnd) and not isinstance(second, StreamEnd)
    assert (first["method"], second["method"], second["id"]) == ("turn/started", "item/fileChange/requestApproval", 7)
    assert rpc.unparsed_lines == 1
    await rpc.reply(7, {"decision": "decline"})
    await rpc.reply_error(8, -32601, "not served")
    assert process.written == [
        {"id": 7, "result": {"decision": "decline"}},
        {"id": 8, "error": {"code": -32601, "message": "not served"}},
    ]
    await rpc.close()


async def test_rp4_when_stdout_ends_pending_requests_fail_and_the_reader_sees_the_end() -> None:
    process = ScriptedProcess()
    rpc = CodexRpc(process)
    rpc.start()
    pending = asyncio.ensure_future(rpc.request("initialize", {}))
    await _sent(process, 1)
    process.end()
    with pytest.raises(CodexStreamEnded):
        await pending
    assert isinstance(await rpc.incoming.get(), StreamEnd)
    assert rpc.ended
    with pytest.raises(CodexStreamEnded):
        await rpc.request("thread/start", {})
    await asyncio.sleep(0)
    assert rpc.stderr_tail.endswith(b"diagnostics\n")
    await rpc.close()
