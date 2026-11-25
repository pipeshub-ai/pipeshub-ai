"""SEC-DOS-04 (C6.40): the shared request-read guard every harness HTTP listener runs behind.

A slowloris client holds a connection by sending its request head, or its body, a byte at a time. The guard
closes such a connection within its bound and leaves a request that arrived in time alone, however long its
response streams.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import pytest
from aiohttp import web

from harness_core.http_limits import (
    ENV_HEADER_TIMEOUT_S,
    ENV_MAX_CONNECTIONS,
    ENV_MIN_BODY_BYTES_PER_S,
    ENV_REQUEST_TIMEOUT_S,
    InvalidReadLimit,
    ReadLimits,
    RequestFraming,
    start_guarded_site,
)
from harness_core.testing.virtual_time import run_on_virtual_time

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

TIGHT = ReadLimits(header_timeout_s=0.3, request_timeout_s=0.6, min_body_bytes_per_s=1024, max_connections=3)


async def closed_within(reader: asyncio.StreamReader, seconds: float) -> float:
    """Seconds until the server closed the connection; fails if it is still open after ``seconds``."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        while await asyncio.wait_for(reader.read(65536), seconds):
            pass
    except TimeoutError:
        pytest.fail(f"the connection was still open after {seconds}s")
    except ConnectionResetError:
        pass
    return loop.time() - started


async def _echo(request: web.Request) -> web.Response:
    return web.Response(body=await request.read())


async def _stream(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    for n in range(8):
        await asyncio.sleep(0.15)
        await response.write(f"data: {n}\n\n".encode())
    await response.write_eof()
    return response


@contextlib.asynccontextmanager
async def _guarded_site() -> AsyncIterator[int]:
    app = web.Application()
    _ = app.router.add_post("/echo", _echo)
    _ = app.router.add_get("/stream", _stream)
    runner = web.AppRunner(app)
    await runner.setup()
    bound = await start_guarded_site(runner, "127.0.0.1", 0, limits=TIGHT, listener="test")
    try:
        yield bound
    finally:
        await runner.cleanup()


def on_guarded_site(body: Callable[[int], Awaitable[None]]) -> Callable[[], None]:
    """Runs ``body`` against the guarded site on a virtual-time loop: the guard's bounds are deadlines on the loop's
    clock and the client's drips are its sleeps, so a bound is checked exactly, not against the host's load."""

    def test() -> None:
        async def main() -> None:
            async with _guarded_site() as port:
                await body(port)

        run_on_virtual_time(main)

    test.__name__ = body.__name__
    test.__doc__ = body.__doc__
    return test


@on_guarded_site
async def test_sec_dos_04_c6_40_a_head_that_never_completes_is_closed_within_the_header_bound(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"POST /echo HTTP/1.1\r\nHost: x\r\n")
    assert await closed_within(reader, 3) < 1.5
    writer.close()


@on_guarded_site
async def test_sec_dos_04_c6_40_a_connection_that_sends_nothing_is_closed_within_the_header_bound(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    assert await closed_within(reader, 3) < 1.5
    writer.close()


@on_guarded_site
async def test_sec_dos_04_c6_40_a_head_dripped_a_byte_at_a_time_is_closed(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    loop = asyncio.get_running_loop()
    started = loop.time()
    head = b"POST /echo HTTP/1.1\r\nHost: x\r\nX-Pad: " + b"a" * 200
    with pytest.raises((ConnectionError, OSError)):
        for byte in head:
            writer.write(bytes([byte]))
            await writer.drain()
            await asyncio.sleep(0.05)
            if reader.at_eof() or loop.time() - started > 5:
                raise ConnectionResetError
    assert loop.time() - started < 2
    writer.close()


@on_guarded_site
async def test_sec_dos_04_c6_40_a_body_that_stops_arriving_is_closed_within_the_request_bound(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n" + b"x" * 10)
    elapsed = await closed_within(reader, 3)
    assert elapsed < 1.5


@on_guarded_site
async def test_sec_dos_04_c6_40_a_chunked_body_that_stops_arriving_is_closed(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"POST /echo HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n")
    assert await closed_within(reader, 3) < 1.5


@on_guarded_site
async def test_sec_dos_04_c6_40_a_body_sent_at_the_minimum_rate_may_outlast_the_request_bound(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    size = 4 * 1024
    writer.write(f"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: {size}\r\n\r\n".encode())
    loop = asyncio.get_running_loop()
    started = loop.time()
    # 2 KiB/s against a 1 KiB/s floor: two seconds, well past the 0.6 s request bound.
    for _ in range(8):
        writer.write(b"y" * 512)
        await writer.drain()
        await asyncio.sleep(0.25)
    answer = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
    assert answer.startswith(b"HTTP/1.1 200")
    assert loop.time() - started > TIGHT.request_timeout_s
    writer.close()


@on_guarded_site
async def test_sec_dos_04_c6_40_a_response_streams_past_every_bound_once_the_request_is_in(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /stream HTTP/1.1\r\nHost: x\r\n\r\n")
    loop = asyncio.get_running_loop()
    started = loop.time()
    body = b""
    while b"data: 7" not in body:
        chunk = await asyncio.wait_for(reader.read(65536), 3)
        assert chunk, "the stream was cut"
        body += chunk
    assert loop.time() - started > TIGHT.request_timeout_s
    writer.close()


@on_guarded_site
async def test_sec_dos_04_c6_40_keep_alive_requests_each_get_their_own_bound(port: int) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    for _ in range(3):
        writer.write(b"POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\nok")
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
        assert head.startswith(b"HTTP/1.1 200")
        assert await reader.readexactly(2) == b"ok"
        await asyncio.sleep(0.4)
    writer.close()


@on_guarded_site
async def test_sec_dos_04_c6_40_connections_past_the_cap_are_refused(port: int) -> None:
    held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(TIGHT.max_connections)]
    await asyncio.sleep(0.05)
    reader, _ = await asyncio.open_connection("127.0.0.1", port)
    assert await closed_within(reader, 1) < 0.25
    for _, writer in held:
        writer.close()


def test_sec_dos_04_c6_40_the_framing_follows_lengths_chunks_and_tunnels() -> None:
    framing = RequestFraming()
    assert framing.feed(b"POST / HTTP/1.1\r\nContent-Length: 3\r\n\r\nab") == ["start", "head"]
    assert framing.feed(b"cGET / HTTP/1.1\r\n\r\n") == ["done", "start", "head", "done"]
    assert framing.feed(b"POST / HTTP/1.1\r\nTransfer-Encoding: gzip, chunked\r\n\r\n3;x=y\r\nabc\r\n") == [
        "start",
        "head",
    ]
    assert framing.feed(b"0\r\nTrailer: v\r\n\r\n") == ["done"]
    assert framing.feed(b"CONNECT api.example:443 HTTP/1.1\r\n\r\n\x16\x03\x01") == ["start", "head", "passive"]
    assert framing.state == RequestFraming.PASSIVE
    upgrade = RequestFraming()
    assert upgrade.feed(b"GET /ws HTTP/1.1\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n") == [
        "start",
        "head",
        "passive",
    ]


def test_sec_dos_04_c6_40_the_limits_read_their_settings_strictly() -> None:
    assert ReadLimits.from_env({}) == ReadLimits()
    configured = ReadLimits.from_env(
        {
            ENV_HEADER_TIMEOUT_S: "5",
            ENV_REQUEST_TIMEOUT_S: "30",
            ENV_MIN_BODY_BYTES_PER_S: "1000",
            ENV_MAX_CONNECTIONS: "10",
        }
    )
    assert configured == ReadLimits(5, 30, 1000, 10)
    for env in (
        {ENV_HEADER_TIMEOUT_S: "soon"},
        {ENV_REQUEST_TIMEOUT_S: "0"},
        {ENV_MAX_CONNECTIONS: "1.5"},
        {ENV_HEADER_TIMEOUT_S: "120"},
        {ENV_MIN_BODY_BYTES_PER_S: "inf"},
    ):
        with pytest.raises(InvalidReadLimit):
            _ = ReadLimits.from_env(env)
