"""PS-1..PS-6: the proxy's data and control planes over real HTTP on loopback (P5.4 §1.4)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp
import pytest
from aiohttp import web

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.errors import BrokerUnauthorized, InvalidSandboxSpec
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SECRET = b"ps-secret"
PH = "ph-model-0123456789abcdef"
CONTROL = "control-credential"
POLICY = EgressPolicy(
    destinations=(EgressDestination("model-gateway", (8097,)), EgressDestination("down-gateway", (8097,))),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)


class Upstream:
    def __init__(self) -> None:
        self.seen: list[dict[str, str]] = []

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.seen.append(dict(request.headers))
        auth = request.headers.get("Authorization", "")
        response = web.StreamResponse(headers={"X-Echo": auth, "Content-Type": "text/event-stream"})
        _ = await response.prepare(request)
        text = f"data: you sent {auth} ok\n\n".encode()
        for i in range(0, len(text), 7):
            await response.write(text[i : i + 7])
            await asyncio.sleep(0)
        await response.write_eof()
        return response


async def _site(app: web.Application) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, runner.addresses[0][1]


@pytest.fixture
async def upstream() -> AsyncIterator[tuple[Upstream, int]]:
    up = Upstream()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", up.handle)
    runner, port = await _site(app)
    yield up, port
    await runner.cleanup()


@pytest.fixture
async def served(upstream: tuple[Upstream, int]) -> AsyncIterator[tuple[EgressServer, int, int]]:
    _, up_port = upstream
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(SECRET, clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": f"http://127.0.0.1:{up_port}", "down-gateway": "http://127.0.0.1:1"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(max_body_bytes=1024),
    )
    server = EgressServer(proxy, StaticBearerAuth(CONTROL), instance="egress-test")
    data_port, control_port = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    yield server, data_port, control_port
    await server.stop()


async def _bind_loopback(server: EgressServer) -> None:
    await server.proxy.bindings.register(EgressBinding("sbx-a", "127.0.0.1", "127.0.0.1", POLICY, {"runId": "r1"}))


async def test_ps1_the_upstream_gets_the_token_and_the_sandbox_gets_the_placeholder_back(
    served: tuple[EgressServer, int, int], upstream: tuple[Upstream, int]
) -> None:
    server, data_port, _ = served
    await _bind_loopback(server)
    token = InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
    async with aiohttp.ClientSession() as session, session.post(
        f"http://127.0.0.1:{data_port}/v1/chat",
        headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"},
        json={"q": 1},
    ) as response:
        body = await response.text()
        assert response.status == 200
        assert response.headers["X-Echo"] == f"Bearer {PH}"
    assert body == f"data: you sent Bearer {PH} ok\n\n"
    assert upstream[0].seen[-1]["Authorization"] == f"Bearer {token}"
    assert upstream[0].seen[-1]["Accept-Encoding"] == "identity"


async def test_ps1_absolute_form_through_the_proxy_works_too(served: tuple[EgressServer, int, int]) -> None:
    server, data_port, _ = served
    await _bind_loopback(server)
    async with aiohttp.ClientSession() as session, session.get(
        "http://model-gateway:8097/v1/models", proxy=f"http://127.0.0.1:{data_port}", headers={"Authorization": f"Bearer {PH}"}
    ) as response:
        assert response.status == 200


@pytest.mark.parametrize(
    ("method", "url", "reason"),
    [("GET", "/", "destination_not_allowed"), ("CONNECT", "evil.example:443", "tunnel_refused")],
)
async def test_ps2_refusals_are_typed_over_http(
    served: tuple[EgressServer, int, int], method: str, url: str, reason: str
) -> None:
    server, data_port, _ = served
    await _bind_loopback(server)
    reader, writer = await asyncio.open_connection("127.0.0.1", data_port)
    writer.write(f"{method} {url} HTTP/1.1\r\nHost: evil.example\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 403")
    assert f"X-Egress-Refusal: {reason}".encode() in head
    assert f'"reason": "{reason}"'.encode() in body


async def test_ps3_the_control_plane(served: tuple[EgressServer, int, int]) -> None:
    server, _, control_port = served
    base = f"http://127.0.0.1:{control_port}"
    async with aiohttp.ClientSession() as session:
        async with session.get(base + "/healthz") as live:
            assert live.status == 200
        async with session.get(base + "/v1/bindings") as anonymous:
            assert anonymous.status == 401
    with pytest.raises(BrokerUnauthorized):
        _ = await RemoteEgressBindings(base, StaticBearerAuth("wrong")).bound()
    client = RemoteEgressBindings(base, StaticBearerAuth(CONTROL))
    try:
        binding = EgressBinding("sbx-b", "10.213.0.2", "10.213.0.3", POLICY, {"runId": "r2"})
        await client.register(binding)
        await client.register(binding)
        assert await client.bound() == {"sbx-b"}
        with pytest.raises(InvalidSandboxSpec) as clash:
            await client.register(EgressBinding("sbx-c", "10.213.0.10", "10.213.0.11", POLICY))
        assert clash.value.reason == "placeholder_in_use"
        health = await client.health()
        assert (health.ok, health.instance, health.bindings) == (True, "egress-test", 1)
        await client.unregister("sbx-b")
        await client.unregister("sbx-b")
        assert await client.bound() == frozenset()
    finally:
        await client.aclose()


async def test_ps3_the_control_plane_refuses_requests_from_a_sandbox_address(served: tuple[EgressServer, int, int]) -> None:
    server, _, control_port = served
    await _bind_loopback(server)
    async with aiohttp.ClientSession() as session, session.get(
        f"http://127.0.0.1:{control_port}/v1/bindings", headers={"Authorization": f"Bearer {CONTROL}"}
    ) as response:
        assert response.status == 403
        assert response.headers["X-Egress-Refusal"] == "control_from_sandbox"


async def test_ps4_a_body_over_the_cap_is_refused(served: tuple[EgressServer, int, int]) -> None:
    server, data_port, _ = served
    await _bind_loopback(server)
    async with aiohttp.ClientSession() as session, session.post(
        f"http://127.0.0.1:{data_port}/v1/chat",
        headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"},
        data=b"x" * 2048,
    ) as response:
        assert response.status == 413
        assert response.headers["X-Egress-Refusal"] == "body_too_large"


async def test_ps5_an_upstream_that_is_down_is_a_typed_502(served: tuple[EgressServer, int, int]) -> None:
    server, data_port, _ = served
    await _bind_loopback(server)
    async with aiohttp.ClientSession() as session, session.get(
        f"http://127.0.0.1:{data_port}/v1", headers={"Host": "down-gateway:8097"}
    ) as response:
        assert response.status == 502
        assert response.headers["X-Egress-Refusal"] == "upstream_unavailable"


async def test_ps6_no_log_line_carries_a_request_url(
    served: tuple[EgressServer, int, int], caplog: pytest.LogCaptureFixture
) -> None:
    server, data_port, _ = served
    await _bind_loopback(server)
    with caplog.at_level(logging.DEBUG):
        async with aiohttp.ClientSession() as session, session.get(
            f"http://127.0.0.1:{data_port}/v1/x?key=secret-in-url",
            headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"},
        ) as response:
            assert response.status == 200
            _ = await response.read()
        # Stopping waits for every handler in flight, so each line this request will ever log is written.
        await server.stop()
    assert all("secret-in-url" not in record.getMessage() for record in caplog.records)


async def test_ps7_a_sandbox_that_hangs_up_cancels_the_upstream_call() -> None:
    """Found on the real binary (E0): an engine's interrupt closed its connection to the proxy, but the
    proxy kept waiting on the model gateway, which kept the call (and its spend) running."""
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def hold(request: web.Request) -> web.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return web.Response()

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", hold)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(SECRET, clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": f"http://127.0.0.1:{runner.addresses[0][1]}"},
        clock=clock,
        ids=SeededIds(),
    )
    server = EgressServer(proxy, StaticBearerAuth(CONTROL), instance="egress-test")
    data_port, _ = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    try:
        await _bind_loopback(server)
        reader, writer = await asyncio.open_connection("127.0.0.1", data_port)
        writer.write(
            f"POST /v1/chat HTTP/1.1\r\nHost: model-gateway:8097\r\nAuthorization: Bearer {PH}\r\n"
            "Content-Length: 2\r\n\r\n{}".encode()
        )
        await writer.drain()
        await asyncio.wait_for(started.wait(), 5)
        writer.close()
        await asyncio.wait_for(cancelled.wait(), 5)
        del reader
    finally:
        await server.stop()
        await runner.cleanup()


async def _raw(port: int, target: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        f"GET {target} HTTP/1.1\r\nHost: model-gateway:8097\r\nAuthorization: Bearer {PH}\r\nConnection: close\r\n\r\n".encode()
    )
    await writer.drain()
    raw = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return raw


async def test_rs7_a_sandbox_cannot_walk_out_of_the_gateway_base_path() -> None:
    paths: list[str] = []

    async def record(request: web.Request) -> web.Response:
        paths.append(request.path)
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", record)
    runner, port = await _site(app)
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(SECRET, clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": f"http://127.0.0.1:{port}/api/v1/harness/tool-gateway/k1"},
        clock=clock,
        ids=SeededIds(),
    )
    server = EgressServer(proxy, StaticBearerAuth(CONTROL), instance="egress-test")
    data_port, _ = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    try:
        await _bind_loopback(server)
        assert (await _raw(data_port, "/mcp")).startswith(b"HTTP/1.1 200")
        for escape in ("/../../runs/r1/claim", "/%2e%2e/%2e%2e/runs/r1/claim"):
            head, _, _ = (await _raw(data_port, escape)).partition(b"\r\n\r\n")
            assert head.startswith(b"HTTP/1.1 403")
            assert b"X-Egress-Refusal: path_not_allowed" in head
        assert paths == ["/api/v1/harness/tool-gateway/k1/mcp"]
    finally:
        await server.stop()
        await runner.cleanup()


async def test_wp127_fu3_an_upstream_silent_mid_stream_is_named_with_its_sandbox_and_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """WP127-FU-3: a stream cut for silence was aiohttp's bare ``Error handling request``, naming neither the sandbox
    nor the request, which hid WP-127's fault. The cut is now ``egress.upstream_silent`` and the sandbox's connection
    ends, so its client sees the stream stop rather than a hang."""
    from harness_core.sandbox.egress import server as server_module

    monkeypatch.setattr(server_module, "UPSTREAM_READ_TIMEOUT_S", 0.3)

    async def silent(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        _ = await response.prepare(request)
        await response.write(b"data: first\n\n")
        await asyncio.sleep(30)
        return response

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", silent)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    port = runner.addresses[0][1]
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(SECRET, clock),
        resolver=StaticResolver(),
        upstreams={"model-gateway": f"http://127.0.0.1:{port}"},
        clock=clock,
        ids=SeededIds(),
    )
    server = EgressServer(proxy, StaticBearerAuth(CONTROL), instance="egress-test")
    data_port, _ = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    try:
        await _bind_loopback(server)
        with caplog.at_level(logging.DEBUG):
            got = b""
            async with aiohttp.ClientSession() as session, session.post(
                f"http://127.0.0.1:{data_port}/v1/messages",
                headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"},
                data=b"{}",
            ) as response:
                assert response.status == 200
                with pytest.raises(aiohttp.ClientPayloadError):
                    async for chunk in response.content.iter_any():
                        got += chunk
            assert got == b"data: first\n\n"
            await server.stop()
        silent_lines = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.upstream_silent"]
        assert len(silent_lines) == 1
        fields = silent_lines[0].harness  # type: ignore[attr-defined]
        assert fields["sandboxId"] == "sbx-a"
        assert fields["proxyRequestId"]
        assert fields["silenceS"] == 0.3
        assert not [r for r in caplog.records if "Error handling request" in r.getMessage()]
    finally:
        await server.stop()
        await runner.cleanup()
