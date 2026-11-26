"""SEC-DOS-04 (C6.40): every harness HTTP listener closes a slowloris connection within its read bounds.

Each listener is started the way its process starts it, with tight limits, and a raw socket sends a request head
that never completes (and, separately, a body that stops arriving). Before C6.40 each of these connections stayed
open for as long as the client liked.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import TYPE_CHECKING, Any, cast

import pytest
from aiohttp import web

from app.harness.engines.worker import EngineWorker
from app.sandbox_broker_main import start_broker_listener
from harness_core.gateway.model_server import ModelGatewayServer
from harness_core.http_limits import ReadLimits
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.bindings import InMemoryEgressBindings
from harness_core.sandbox.egress.proxy import EgressProxy, ProxyLimits
from harness_core.sandbox.egress.resolver import StaticResolver
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.tokens import InMemoryTokenSource
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.virtual_time import run_on_virtual_time

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

TIGHT = ReadLimits(header_timeout_s=0.3, request_timeout_s=0.6, min_body_bytes_per_s=1024, max_connections=64)
PARTIAL_HEAD = b"POST /v1/messages HTTP/1.1\r\nHost: x\r\n"
PARTIAL_BODY = b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: 1000\r\n\r\n{"


async def closed_within(port: int, payload: bytes, seconds: float = 3.0) -> float:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(payload)
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        while await asyncio.wait_for(reader.read(65536), seconds):
            pass
    except TimeoutError:
        pytest.fail(f"the listener kept the connection open past {seconds}s")
    except ConnectionResetError:
        pass
    finally:
        writer.close()
    return loop.time() - started


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return cast("int", probe.getsockname()[1])


def _proxy(*, tunnels: bool) -> EgressProxy:
    clock = ManualClock()
    return EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(b"k" * 32, clock),
        resolver=StaticResolver(),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(connect_tunnels=tunnels),
    )


@contextlib.asynccontextmanager
async def model_gateway() -> AsyncIterator[int]:
    server = ModelGatewayServer(cast("Any", object()), read_limits=TIGHT)
    port = await server.start(host="127.0.0.1", port=0)
    try:
        yield port
    finally:
        await server.stop()


@contextlib.asynccontextmanager
async def egress(plane: str, *, tunnels: bool) -> AsyncIterator[int]:
    server = EgressServer(_proxy(tunnels=tunnels), StaticBearerAuth("c"), instance="c640", read_limits=TIGHT)
    data, control = await server.start(host="127.0.0.1", data_port=0, control_port=0, control_host="127.0.0.2")
    try:
        yield data if plane == "data" else control
    finally:
        await server.stop()


@contextlib.asynccontextmanager
async def egress_control() -> AsyncIterator[int]:
    server = EgressServer(_proxy(tunnels=False), StaticBearerAuth("c"), instance="c640", read_limits=TIGHT)
    _, control = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    try:
        yield control
    finally:
        await server.stop()


async def _ok(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


@contextlib.asynccontextmanager
async def broker() -> AsyncIterator[int]:
    app = web.Application()
    _ = app.router.add_post("/v1/messages", _ok)
    runner = await start_broker_listener(app, "127.0.0.1", 0, read_limits=TIGHT)
    try:
        yield cast("tuple[str, int]", runner.addresses[0])[1]
    finally:
        await runner.cleanup()


async def _asgi(scope: dict[str, Any], receive: Callable[[], Awaitable[Any]], send: Callable[[Any], Awaitable[None]]) -> None:
    if scope["type"] != "http":
        return
    while (await receive()).get("more_body"):
        pass
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@contextlib.asynccontextmanager
async def tool_gateway() -> AsyncIterator[int]:
    port = _free_port()
    worker = EngineWorker(kernel=cast("Any", None), gateway=cast("Any", _asgi), port=port, read_limits=TIGHT, host="127.0.0.1")
    async with worker.serving():
        for _ in range(100):
            with contextlib.suppress(OSError):
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                break
            await asyncio.sleep(0.05)
        yield port


LISTENERS: dict[str, Callable[[], contextlib.AbstractAsyncContextManager[int]]] = {
    "model_gateway": model_gateway,
    "egress_data": lambda: egress("data", tunnels=False),
    "egress_data_with_tunnels": lambda: egress("data", tunnels=True),
    "egress_control": egress_control,
    "sandbox_broker": broker,
    "tool_gateway": tool_gateway,
}


def _closed_within_on_virtual_time(listener: str, payload: bytes) -> float:
    """The listener's read bounds are deadlines on the loop's clock, so on a virtual-time loop the bound is checked
    exactly, not against the host's load."""

    async def main() -> float:
        async with LISTENERS[listener]() as port:
            return await closed_within(port, payload)

    return run_on_virtual_time(main)


@pytest.mark.parametrize("listener", sorted(LISTENERS))
def test_sec_dos_04_c6_40_a_slow_head_is_closed_within_the_header_bound(listener: str) -> None:
    assert _closed_within_on_virtual_time(listener, PARTIAL_HEAD) < 1.5


@pytest.mark.parametrize("listener", sorted(LISTENERS))
def test_sec_dos_04_c6_40_a_slow_body_is_closed_within_the_request_bound(listener: str) -> None:
    assert _closed_within_on_virtual_time(listener, PARTIAL_BODY) < 1.5


async def test_sec_dos_04_c6_40_a_connect_tunnel_is_left_to_the_tunnel_handler() -> None:
    async with egress("data", tunnels=True) as port:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT api.example:443 HTTP/1.1\r\nHost: api.example:443\r\n\r\n")
        answer = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
        # Refused by the proxy's own policy (no binding), not cut by the read guard.
        assert answer.startswith(b"HTTP/1.1 ")
        writer.close()
