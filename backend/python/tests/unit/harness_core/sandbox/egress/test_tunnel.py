"""CT-1..CT-13: CONNECT tunnels to allowlisted hosts, resolved once and pinned, opaque, audited (P5.4b)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import aiohttp
import pytest
from aiohttp import web

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.errors import status_for
from harness_core.sandbox.egress.policy import parse_networks
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from tests.support.tls_certs import make_pki

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

R = EgressRefusalReason
PH = "ph-model-0123456789abcdef"
API = "api.example.test"
PUBLIC = "93.184.216.34"
VENDOR = "ghp_" + "Z" * 36
POLICY = EgressPolicy(
    destinations=(
        EgressDestination("model-gateway", (8097,)),
        EgressDestination(API, (443,)),
        EgressDestination("static-gw.example", (443,)),
        EgressDestination("private.example", (443,)),
        EgressDestination("loopback.example", (443,)),
        EgressDestination("metadata.example", (443,)),
        EgressDestination("cgnat.example", (443,)),
        EgressDestination("blocked.example", (443,)),
        EgressDestination("mixed.example", (443,)),
        EgressDestination("nowhere.example", (443,)),
        EgressDestination("rebind.example", (443,)),
        EgressDestination("1.2.3.4", (443,)),
        EgressDestination("2606:4700::1111", (443,)),
    ),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)
LABELS = {"orgId": "o1", "runId": "r1", "attemptId": "a1"}
A = EgressBinding("sbx-a", "127.0.0.2", "127.0.0.1", POLICY, LABELS)
TABLE = {
    API: [PUBLIC],
    "private.example": ["10.1.2.3"],
    "loopback.example": ["127.0.0.1"],
    "metadata.example": ["169.254.169.254"],
    "cgnat.example": ["100.64.0.9"],
    "blocked.example": ["198.51.100.20"],
    "mixed.example": [PUBLIC, "10.0.0.5"],
}


@dataclass
class CountingResolver:
    """``StaticResolver`` that counts lookups; ``rebind`` answers a public address once, then a private one."""

    calls: list[str] = field(default_factory=list[str])
    inner: StaticResolver = field(default_factory=lambda: StaticResolver(TABLE))

    async def resolve(self, host: str) -> tuple[str, ...]:
        self.calls.append(host)
        if host == "rebind.example":
            return (PUBLIC,) if self.calls.count(host) == 1 else ("10.9.9.9",)
        return await self.inner.resolve(host)


def make_proxy(resolver: CountingResolver, *, tunnels: bool = True) -> EgressProxy:
    clock = ManualClock()
    return EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(b"ct", clock),
        resolver=resolver,
        upstreams={"model-gateway": "http://127.0.0.1:1", "static-gw.example": "http://127.0.0.1:1"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(connect_tunnels=tunnels, blocked_networks=parse_networks(["198.51.100.0/24"])),
    )


def connect(target: str, *headers: tuple[str, str]) -> ProxyRequest:
    return ProxyRequest("CONNECT", target, (("Host", target), *headers))


async def tunnel_reason(proxy: EgressProxy, request: ProxyRequest) -> R:
    with pytest.raises(EgressRefused) as caught:
        _ = await proxy.authorize_tunnel(request, local=A.proxy_address, peer=A.sandbox_address)
    return caught.value.reason


@pytest.fixture
def resolver() -> CountingResolver:
    return CountingResolver()


@pytest.fixture
async def proxy(resolver: CountingResolver) -> EgressProxy:
    built = make_proxy(resolver)
    await built.bindings.register(A)
    return built


async def test_ct1_with_the_flag_off_connect_is_refused_as_before(resolver: CountingResolver) -> None:
    off = make_proxy(resolver, tunnels=False)
    await off.bindings.register(A)
    assert await tunnel_reason(off, connect(f"{API}:443")) == R.TUNNEL_REFUSED
    assert resolver.calls == []


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        (f"{API}", R.MALFORMED_REQUEST),
        (f"user@{API}:443", R.MALFORMED_REQUEST),
        (f"{API}:443/x", R.MALFORMED_REQUEST),
        (f"{API}:99999", R.MALFORMED_REQUEST),
        ("1.2.3.4:443", R.TUNNEL_IP_LITERAL),
        ("[2606:4700::1111]:443", R.TUNNEL_IP_LITERAL),
    ],
)
async def test_ct2_the_target_is_a_name_and_a_port(proxy: EgressProxy, target: str, reason: R) -> None:
    assert await tunnel_reason(proxy, connect(target)) == reason


async def test_ct2_an_allowlisted_name_and_port_is_accepted(proxy: EgressProxy, resolver: CountingResolver) -> None:
    tunnel = await proxy.authorize_tunnel(connect(f"{API.upper()}.:443"), local=A.proxy_address, peer=A.sandbox_address)
    assert (tunnel.host, tunnel.port, tunnel.connect_to, tunnel.sandbox_id) == (API, 443, ((PUBLIC, 443),), "sbx-a")
    assert dict(tunnel.labels) == LABELS


@pytest.mark.parametrize("target", ["other.example:443", f"{API}:8443"])
async def test_ct3_a_host_or_port_off_the_allowlist_is_refused(proxy: EgressProxy, target: str) -> None:
    assert await tunnel_reason(proxy, connect(target)) == R.DESTINATION_NOT_ALLOWED


async def test_ct4_credentialed_and_gateway_destinations_are_not_tunnelled(proxy: EgressProxy) -> None:
    allow_gw = EgressPolicy(destinations=(EgressDestination("model-gateway", (8097,)),), token_routes=POLICY.token_routes)
    assert await tunnel_reason(proxy, connect("static-gw.example:443")) == R.TUNNEL_REFUSED
    await proxy.bindings.register(EgressBinding("sbx-a", A.proxy_address, A.sandbox_address, allow_gw, LABELS))
    assert await tunnel_reason(proxy, connect("model-gateway:8097")) == R.TUNNEL_REFUSED


@pytest.mark.parametrize(
    ("host", "reason"),
    [
        ("private.example", R.DESTINATION_PRIVATE_ADDRESS),
        ("loopback.example", R.DESTINATION_PRIVATE_ADDRESS),
        ("metadata.example", R.DESTINATION_PRIVATE_ADDRESS),
        ("cgnat.example", R.DESTINATION_PRIVATE_ADDRESS),
        ("blocked.example", R.DESTINATION_PRIVATE_ADDRESS),
        ("mixed.example", R.DESTINATION_PRIVATE_ADDRESS),
        ("nowhere.example", R.DESTINATION_UNRESOLVABLE),
    ],
)
async def test_ct5_every_answer_must_be_public(proxy: EgressProxy, host: str, reason: R) -> None:
    assert await tunnel_reason(proxy, connect(f"{host}:443")) == reason


async def test_ct7_what_the_proxy_can_see_is_still_scanned(proxy: EgressProxy) -> None:
    assert await tunnel_reason(proxy, connect(f"{API}:443", ("Proxy-Authorization", f"Basic {VENDOR}"))) == (
        R.FORBIDDEN_PATTERN
    )
    other = EgressBinding("sbx-b", "127.0.0.3", "127.0.0.1", POLICY, LABELS)
    await proxy.bindings.register(
        EgressBinding("sbx-b", other.proxy_address, other.sandbox_address,
                      EgressPolicy(destinations=POLICY.destinations,
                                   token_routes=(TokenRoute(PH + "-b", "model-gateway", "gateway:model"),)), LABELS)
    )
    forward = await proxy.authorize(
        ProxyRequest("POST", "/v1", (("Host", "model-gateway:8097"), ("Authorization", f"Bearer {PH}"))),
        local=A.proxy_address,
        peer=A.sandbox_address,
    )
    token = next(v for k, v in forward.headers if k == "Authorization").removeprefix("Bearer ")
    with pytest.raises(EgressRefused) as caught:
        _ = await proxy.authorize_tunnel(
            connect(f"{API}:443", ("X-Carry", token)), local=other.proxy_address, peer=other.sandbox_address
        )
    assert caught.value.reason == R.TOKEN_REPLAYED


def test_ct13_the_new_reason_is_unique_and_has_a_status() -> None:
    values = [reason.value for reason in EgressRefusalReason]
    assert values.count("tunnel_ip_literal") == 1 and status_for(R.TUNNEL_IP_LITERAL) == 403


# --- over the real listener ------------------------------------------------------------------------------


@dataclass
class TlsApi:
    port: int
    ca: bytes
    received: list[bytes] = field(default_factory=list[bytes])


@pytest.fixture
async def tls_api(tmp_path: object) -> AsyncIterator[TlsApi]:
    from pathlib import Path

    pki = make_pki(API)
    folder = Path(str(tmp_path))
    (folder / "cert.pem").write_bytes(pki.cert_pem)
    (folder / "key.pem").write_bytes(pki.key_pem)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(folder / "cert.pem", folder / "key.pem")
    api = TlsApi(0, pki.ca_pem)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            head = await reader.readuntil(b"\r\n\r\n")
            api.received.append(head)
            body = b"tls ok"
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)
    api.port = server.sockets[0].getsockname()[1]
    yield api
    server.close()
    await server.wait_closed()


@dataclass
class Dialled:
    calls: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])


@pytest.fixture
async def served(
    proxy: EgressProxy, tls_api: TlsApi
) -> AsyncIterator[tuple[EgressServer, int, Dialled]]:
    dialled = Dialled()

    async def connector(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # The pinned public address stands for the TLS server on loopback; nothing else is reachable.
        dialled.calls.append((address, port))
        if (address, port) != (PUBLIC, 443):
            raise ConnectionRefusedError(address)
        return await asyncio.open_connection("127.0.0.1", tls_api.port)

    server = EgressServer(proxy, StaticBearerAuth("c"), instance="ct", connector=connector)
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    yield server, data_port, dialled
    await server.stop()


async def open_tunnel(data_port: int, target: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
    writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    return reader, writer, head


async def https_get(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, ca: bytes) -> bytes:
    context = ssl.create_default_context(cadata=ca.decode())
    await writer.start_tls(context, server_hostname=API)
    writer.write(f"GET /v1 HTTP/1.1\r\nHost: {API}\r\nAuthorization: Bearer {VENDOR}\r\n\r\n".encode())
    await writer.drain()
    return await asyncio.wait_for(reader.read(), 10)


async def test_ct8_ct9_an_opaque_tls_tunnel_to_the_pinned_address_audited_once(
    served: tuple[EgressServer, int, Dialled], tls_api: TlsApi, caplog: pytest.LogCaptureFixture
) -> None:
    _, data_port, dialled = served
    with caplog.at_level(logging.INFO, logger="harness_core.sandbox.egress"):
        reader, writer, head = await open_tunnel(data_port, f"{API}:443")
        assert head.startswith(b"HTTP/1.1 200")
        answer = await https_get(reader, writer, tls_api.ca)
        writer.close()
        await asyncio.sleep(0.2)
    assert answer.endswith(b"tls ok")
    assert dialled.calls == [(PUBLIC, 443)]
    # Opaque: the vendor-shaped credential crossed inside TLS (the documented trade-off).
    assert VENDOR.encode() in tls_api.received[0]
    opened = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.tunnel_opened"]
    closed = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.tunnel_closed"]
    assert len(opened) == 1 and len(closed) == 1
    fields = opened[0].harness  # type: ignore[attr-defined]
    assert (fields["sandboxId"], fields["host"], fields["port"], fields["address"]) == ("sbx-a", API, 443, PUBLIC)
    assert {k: fields[k] for k in LABELS} == LABELS
    assert closed[0].harness["bytesUp"] > 0 and closed[0].harness["bytesDown"] > 0  # type: ignore[attr-defined]
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert VENDOR not in text and "/v1" not in text


async def test_ct6_resolved_once_and_the_rebinding_answer_never_used(
    served: tuple[EgressServer, int, Dialled], resolver: CountingResolver
) -> None:
    _, data_port, dialled = served
    _, writer, head = await open_tunnel(data_port, "rebind.example:443")
    writer.close()
    assert head.startswith(b"HTTP/1.1 200")
    assert resolver.calls == ["rebind.example"]
    assert dialled.calls == [(PUBLIC, 443)]
    _, writer, head = await open_tunnel(data_port, "rebind.example:443")
    writer.close()
    assert b"403" in head.split(b"\r\n")[0] and b"destination_private_address" in head
    assert dialled.calls == [(PUBLIC, 443)], "the private answer was never dialled"


async def test_ct10_unbinding_the_sandbox_closes_its_tunnel(served: tuple[EgressServer, int, Dialled]) -> None:
    server, data_port, _ = served
    reader, writer, head = await open_tunnel(data_port, f"{API}:443")
    assert head.startswith(b"HTTP/1.1 200")
    await server.proxy.bindings.unregister("sbx-a")
    assert await asyncio.wait_for(reader.read(), 5) == b""
    writer.close()


async def test_ct11_an_upstream_that_refuses_is_502_logged_once(
    proxy: EgressProxy, caplog: pytest.LogCaptureFixture
) -> None:
    async def refusing(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        raise ConnectionRefusedError(address)

    server = EgressServer(proxy, StaticBearerAuth("c"), instance="ct", connector=refusing)
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    try:
        with caplog.at_level(logging.WARNING, logger="harness_core.sandbox.egress"):
            _, writer, head = await open_tunnel(data_port, f"{API}:443")
            writer.close()
    finally:
        await server.stop()
    assert head.startswith(b"HTTP/1.1 502") and b"upstream_unavailable" in head
    refusals = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.refused"]
    assert len(refusals) == 1 and refusals[0].harness["sandboxId"] == "sbx-a"  # type: ignore[attr-defined]


async def test_ct1_over_http_the_flag_off_listener_refuses_connect(resolver: CountingResolver) -> None:
    off = make_proxy(resolver, tunnels=False)
    await off.bindings.register(A)
    server = EgressServer(off, StaticBearerAuth("c"), instance="ct")
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    try:
        _, writer, head = await open_tunnel(data_port, f"{API}:443")
        writer.close()
    finally:
        await server.stop()
    assert head.startswith(b"HTTP/1.1 403") and b"tunnel_refused" in head.lower()
    assert resolver.calls == []


async def test_ct12_plain_http_is_unchanged_with_tunnels_on(proxy: EgressProxy) -> None:
    seen: list[str] = []

    async def handle(request: web.Request) -> web.Response:
        seen.append(request.headers.get("Authorization", ""))
        return web.Response(text=f"echo {request.headers.get('Authorization', '')}")

    app = web.Application()
    _ = app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    up_port = runner.addresses[0][1]
    _ = proxy.replace_upstreams({"model-gateway": f"http://127.0.0.1:{up_port}"})
    server = EgressServer(proxy, StaticBearerAuth("c"), instance="ct")
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    try:
        connector = aiohttp.TCPConnector(local_addr=("127.0.0.1", 0))
        async with aiohttp.ClientSession(connector=connector) as session, session.get(
            f"http://127.0.0.2:{data_port}/v1/models", headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"}
        ) as response:
            assert response.status == 200
            assert await response.text() == f"echo Bearer {PH}"
        assert seen[0].startswith("Bearer pxt_")
        # A CONNECT after a request on the same kept-alive connection reaches the HTTP handler: refused.
        reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
        writer.write(f"GET /v1/models HTTP/1.1\r\nHost: model-gateway:8097\r\nAuthorization: Bearer {PH}\r\n\r\n".encode())
        await writer.drain()
        first = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        assert first.startswith(b"HTTP/1.1 200")
        # The proxy streams replies (chunked): read to the last chunk.
        _ = await asyncio.wait_for(reader.readuntil(b"0\r\n\r\n"), 10)
        writer.write(f"CONNECT {API}:443 HTTP/1.1\r\nHost: {API}:443\r\n\r\n".encode())
        await writer.drain()
        second = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        assert second.startswith(b"HTTP/1.1 403") and b"tunnel_refused" in second.lower()
        writer.close()
    finally:
        await server.stop()
        await runner.cleanup()
