"""SEC-EG-13 (G-21) and SEC-ISO-09 (G-38): the deployment's upstream proxy, private host:port entries, and PipesHub's
own addresses refused whatever is listed. Decisions over in-memory adapters; chaining over real loopback sockets."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import aiohttp
import pytest

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
from harness_core.sandbox.egress.internals import host_networks, host_of, route_gateways
from harness_core.sandbox.egress.policy import parse_networks
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.upstream_proxy import (
    METADATA_ADDRESSES,
    PrivateAllow,
    UpstreamEgress,
    UpstreamEgressInvalid,
    never_allowed,
    parse_private_allow,
    parse_upstream_proxy,
    upstream_egress_from_env,
)
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

R = EgressRefusalReason
PUBLIC = "93.184.216.34"
PH = "ph-model-0123456789abcdef"
PROXY_SECRET = "corp-proxy-password-0123"
INTERNAL_GW = "10.20.0.7"
TABLE = {
    "public.example": [PUBLIC],
    "direct.example": [PUBLIC],
    "git.corp": ["10.1.2.3"],
    "artifacts.corp": ["10.1.2.4"],
    "listed-addr.corp": ["10.1.2.5"],
    "unlisted.corp": ["10.9.9.9"],
    "cgnat.corp": ["100.64.0.9"],
    "alibaba-meta.corp": ["100.100.100.200"],
    "metadata.corp": ["169.254.169.254"],
    "loop.corp": ["127.0.0.1"],
    "gateway-alias.corp": [INTERNAL_GW],
    "pool.corp": ["10.213.0.3"],
    "mixed.corp": ["10.1.2.3", "10.9.9.9"],
    "public-internal.example": ["203.0.113.50"],
}
HOSTS = [*TABLE, "model-gateway", "tools.example"]
POLICY = EgressPolicy(
    destinations=(
        EgressDestination("model-gateway", (8097,)),
        EgressDestination("tools.example", (80,), upstream="http://tool-upstream:9000/t"),
        *(EgressDestination(h, (80, 443, 8443)) for h in TABLE),
    ),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)
LABELS = {"orgId": "o1", "runId": "r1", "attemptId": "a1"}
A = EgressBinding("sbx-a", "127.0.0.2", "127.0.0.1", POLICY, LABELS)
UPSTREAM_ENV = {
    "EGRESS_UPSTREAM_PROXY": "http://corp-proxy.example:3128",
    "EGRESS_UPSTREAM_PROXY_AUTH": f"svc-pipeshub:{PROXY_SECRET}",
    "EGRESS_UPSTREAM_NO_PROXY": "direct.example,.nearby.example,198.18.0.0/15",
    "EGRESS_PRIVATE_ALLOW": (
        "git.corp:443,10.1.2.5:8443,100.64.0.0/10:443,alibaba-meta.corp:443,metadata.corp:443,"
        "10.0.0.0/8:8443,gateway-alias.corp:443,pool.corp:443"
    ),
}
# PipesHub's own addresses (internals): a gateway's address, the sandbox pool, an internal service on a public address.
INTERNALS = (*parse_networks([f"{INTERNAL_GW}/32", "10.213.0.0/16", "203.0.113.50/32"]),)


def make_proxy(
    *, env: dict[str, str] | None = None, tunnels: bool = False, resolver: StaticResolver | None = None
) -> EgressProxy:
    clock = ManualClock()
    return EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(b"g21", clock),
        resolver=resolver or StaticResolver(TABLE),
        upstreams={"model-gateway": "http://model-upstream:9000"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(connect_tunnels=tunnels),
        upstream_egress=upstream_egress_from_env(UPSTREAM_ENV if env is None else env),
        internal_networks=INTERNALS,
    )


@pytest.fixture
async def proxy() -> EgressProxy:
    built = make_proxy(tunnels=True)
    await built.bindings.register(A)
    return built


def get(url: str, *headers: tuple[str, str]) -> ProxyRequest:
    return ProxyRequest("GET", url, headers)


async def refused(proxy: EgressProxy, request: ProxyRequest) -> R:
    with pytest.raises(EgressRefused) as caught:
        if request.method == "CONNECT":
            _ = await proxy.authorize_tunnel(request, local=A.proxy_address, peer=A.sandbox_address)
        else:
            _ = await proxy.authorize(request, local=A.proxy_address, peer=A.sandbox_address)
    return caught.value.reason


# --- settings -----------------------------------------------------------------------------------------------------


def test_sec_eg_13_settings_unset_means_no_upstream_and_no_private_entry() -> None:
    assert upstream_egress_from_env({}) == UpstreamEgress()


def test_sec_eg_13_the_upstream_proxy_setting_is_parsed_and_its_credential_never_shown() -> None:
    upstream = parse_upstream_proxy("http://Corp-Proxy.example:8080/", "svc:pw-123456", "a.example, .corp,10.0.0.0/8")
    assert upstream is not None
    assert (upstream.host, upstream.port, upstream.url) == ("corp-proxy.example", 8080, "http://corp-proxy.example:8080")
    assert upstream.headers() == (("Proxy-Authorization", "Basic c3ZjOnB3LTEyMzQ1Ng=="),)
    assert "pw-123456" not in repr(upstream) and "c3Zj" not in repr(upstream)
    assert parse_upstream_proxy("http://proxy:3128") is not None
    assert parse_upstream_proxy("http://proxy").port == 3128  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("url", "auth", "no_proxy", "names"),
    [
        ("http://user:pw@proxy:3128", None, None, "EGRESS_UPSTREAM_PROXY_AUTH"),
        ("https://proxy:3128", None, None, "http://host:port"),
        ("socks5://proxy:1080", None, None, "http://host:port"),
        ("http://proxy:3128/path", None, None, "http://host:port"),
        ("http://proxy:99999", None, None, "port"),
        (None, "user:pw", None, "without"),
        ("http://proxy:3128", "no-colon", None, "user:password"),
        ("http://proxy:3128", None, "*", "EGRESS_UPSTREAM_NO_PROXY"),
        ("http://proxy:3128", None, "bad host!", "EGRESS_UPSTREAM_NO_PROXY"),
    ],
)
def test_sec_eg_13_a_malformed_upstream_setting_stops_the_proxy_naming_the_setting_not_the_secret(
    url: str | None, auth: str | None, no_proxy: str | None, names: str
) -> None:
    with pytest.raises(UpstreamEgressInvalid) as caught:
        _ = parse_upstream_proxy(url, auth, no_proxy)
    assert names in str(caught.value)
    assert "pw" not in str(caught.value).replace("password", "")


def test_sec_eg_13_private_entries_parse_names_addresses_and_networks() -> None:
    entries = parse_private_allow(" git.corp:443 , 10.1.2.3:22,[fd12::1]:443, 10.1.0.0/16:8443,git.corp:443")
    assert entries == (
        PrivateAllow(443, host="git.corp"),
        PrivateAllow(22, network=ipaddress.ip_network("10.1.2.3/32")),
        PrivateAllow(443, network=ipaddress.ip_network("fd12::1/128")),
        PrivateAllow(8443, network=ipaddress.ip_network("10.1.0.0/16")),
    )
    assert [str(e) for e in entries] == ["git.corp:443", "10.1.2.3/32:22", "[fd12::1/128]:443", "10.1.0.0/16:8443"]


@pytest.mark.parametrize(
    "entry",
    ["git.corp", "git.corp:0", "git.corp:http", "10.1.2.3", "127.0.0.1:443", "169.254.169.254:80", "0.0.0.0/0:443",
     "[::1]:443", "[fe80::1]:443", "bad host:443", "gіt.corp:443"],
)
def test_sec_eg_13_an_entry_that_is_malformed_or_names_what_is_never_opened_is_refused(entry: str) -> None:
    with pytest.raises(UpstreamEgressInvalid, match="EGRESS_PRIVATE_ALLOW"):
        _ = parse_private_allow(entry)


def test_sec_eg_13_never_allowed_covers_metadata_loopback_and_their_ipv6_wrappings() -> None:
    for address in ("169.254.169.254", "127.0.0.1", "0.0.0.0", "::1", "fe80::1", "224.0.0.1", "::ffff:127.0.0.1",
                    "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "100.100.100.200", "168.63.129.16", "fd00:ec2::254",
                    "not-an-address"):
        assert never_allowed(address), address
    for address in ("10.1.2.3", "100.64.0.9", "192.168.1.5", "fd12::1", PUBLIC):
        assert not never_allowed(address), address


def test_sec_eg_13_the_metadata_list_matches_the_p2_8_ssrf_policy() -> None:
    from app.utils import url_fetcher

    assert url_fetcher._CLOUD_METADATA_ADDRESSES == METADATA_ADDRESSES  # pyright: ignore[reportPrivateUsage]


# --- chaining decisions --------------------------------------------------------------------------------------------


async def test_sec_eg_13_a_public_destination_goes_through_the_upstream_proxy_by_its_checked_address(
    proxy: EgressProxy,
) -> None:
    sent = get("http://public.example/x?q=1", ("Proxy-Authorization", "Basic c2FuZGJveDpndWVzcw=="))
    forward = await proxy.authorize(sent, local=A.proxy_address, peer=A.sandbox_address)
    assert forward.url == f"http://{PUBLIC}:80/x?q=1"
    assert forward.proxy == "http://corp-proxy.example:3128"
    assert ("Host", "public.example") in forward.headers
    assert dict(forward.proxy_headers)["Proxy-Authorization"].startswith("Basic ")
    # The sandbox's own Proxy-Authorization never reaches anyone; the proxy's credential only the upstream proxy.
    assert not any(k.lower() == "proxy-authorization" for k, _ in forward.headers)
    assert PROXY_SECRET not in repr(forward)


async def test_sec_eg_13_gateways_and_trusted_upstreams_never_go_through_the_upstream_proxy(proxy: EgressProxy) -> None:
    gateway = ProxyRequest("POST", "/v1/messages", (("Host", "model-gateway:8097"), ("Authorization", f"Bearer {PH}")))
    forward = await proxy.authorize(gateway, local=A.proxy_address, peer=A.sandbox_address)
    assert forward.url == "http://model-upstream:9000/v1/messages" and forward.proxy is None
    tools = await proxy.authorize(get("http://tools.example/mcp"), local=A.proxy_address, peer=A.sandbox_address)
    assert tools.url == "http://tool-upstream:9000/t/mcp" and tools.proxy is None and tools.proxy_headers == ()


@pytest.mark.parametrize("host", ["direct.example"])
async def test_sec_eg_13_no_proxy_entries_go_direct(proxy: EgressProxy, host: str) -> None:
    forward = await proxy.authorize(get(f"http://{host}/"), local=A.proxy_address, peer=A.sandbox_address)
    assert forward.proxy is None and forward.url == f"http://{PUBLIC}:80/"


def test_sec_eg_13_no_proxy_matches_names_suffixes_and_networks() -> None:
    upstream = parse_upstream_proxy("http://p:3128", None, "a.example,.corp.example,198.18.0.0/15")
    assert upstream is not None
    assert upstream.bypassed("A.example.")
    assert upstream.bypassed("git.corp.example") and upstream.bypassed("corp.example")
    assert not upstream.bypassed("evilcorp.example") and not upstream.bypassed("b.example")
    assert upstream.bypassed("any.example", "198.19.1.1") and not upstream.bypassed("any.example", PUBLIC)


async def test_sec_eg_13_tunnels_to_public_hosts_are_chained_and_private_entries_are_not(proxy: EgressProxy) -> None:
    public = await proxy.authorize_tunnel(
        ProxyRequest("CONNECT", "public.example:443", ()), local=A.proxy_address, peer=A.sandbox_address
    )
    assert public.connect_to == ((PUBLIC, 443),) and public.via_upstream_proxy
    private = await proxy.authorize_tunnel(
        ProxyRequest("CONNECT", "git.corp:443", ()), local=A.proxy_address, peer=A.sandbox_address
    )
    assert private.connect_to == (("10.1.2.3", 443),) and not private.via_upstream_proxy


async def test_sec_eg_13_without_an_upstream_proxy_nothing_is_chained() -> None:
    plain = make_proxy(env={"EGRESS_PRIVATE_ALLOW": "git.corp:443"}, tunnels=True)
    await plain.bindings.register(A)
    forward = await plain.authorize(get("http://public.example/"), local=A.proxy_address, peer=A.sandbox_address)
    assert forward.proxy is None and forward.proxy_headers == ()


# --- private entries --------------------------------------------------------------------------------------------


async def test_sec_eg_13_an_explicit_private_host_port_is_reached_direct(proxy: EgressProxy) -> None:
    forward = await proxy.authorize(get("http://git.corp:443/repo.git/info/refs"), local=A.proxy_address, peer=A.sandbox_address)
    assert forward.url == "http://10.1.2.3:443/repo.git/info/refs" and forward.proxy is None
    by_address = await proxy.authorize(get("http://listed-addr.corp:8443/"), local=A.proxy_address, peer=A.sandbox_address)
    assert by_address.url == "http://10.1.2.5:8443/"
    cgnat = await proxy.authorize(get("http://cgnat.corp:443/"), local=A.proxy_address, peer=A.sandbox_address)
    assert cgnat.url == "http://100.64.0.9:443/"


@pytest.mark.parametrize(
    "url",
    [
        "http://git.corp:80/",  # listed for 443 only
        "http://unlisted.corp:443/",  # private, no entry
        "http://listed-addr.corp:443/",  # its address is listed for 8443 only
        "http://mixed.corp:443/",  # one answer listed, one not: all refused
        "http://artifacts.corp/",
    ],
)
async def test_sec_eg_13_private_addresses_stay_denied_by_default(proxy: EgressProxy, url: str) -> None:
    assert await refused(proxy, get(url)) == R.DESTINATION_PRIVATE_ADDRESS


@pytest.mark.parametrize(
    "url",
    [
        "http://metadata.corp:443/latest/meta-data/",  # listed by name, resolves to link-local metadata
        "http://alibaba-meta.corp:443/",  # listed by name, CGNAT entry too: the metadata address is never opened
        "http://loop.corp:443/",
    ],
)
async def test_sec_eg_13_metadata_and_loopback_are_never_opened_even_when_listed(proxy: EgressProxy, url: str) -> None:
    assert await refused(proxy, get(url)) == R.DESTINATION_PRIVATE_ADDRESS


@pytest.mark.parametrize(
    "url",
    [
        "http://gateway-alias.corp:443/",  # listed by name; the address is a gateway's
        "http://pool.corp:443/",  # listed by name; the address is another sandbox's
        f"http://{INTERNAL_GW}:8443/",  # inside the listed 10.0.0.0/8:8443
        "http://10.213.0.3:8443/",
    ],
)
async def test_sec_iso_09_pipeshub_internals_are_denied_even_when_listed(url: str) -> None:
    built = make_proxy()
    policy = EgressPolicy(destinations=(*POLICY.destinations, EgressDestination(INTERNAL_GW, (8443,)),
                                        EgressDestination("10.213.0.3", (8443,))))
    await built.bindings.register(EgressBinding("sbx-a", "127.0.0.2", "127.0.0.1", policy, LABELS))
    assert await refused(built, get(url)) == R.DESTINATION_PRIVATE_ADDRESS


async def test_sec_iso_09_an_internal_service_on_a_public_address_is_denied_too(proxy: EgressProxy) -> None:
    assert await refused(proxy, get("http://public-internal.example/")) == R.DESTINATION_PRIVATE_ADDRESS


async def test_sec_iso_09_internals_are_replaced_when_containers_move(proxy: EgressProxy) -> None:
    assert proxy.replace_internal_networks(parse_networks(["10.1.2.3/32"]))
    assert not proxy.replace_internal_networks(parse_networks(["10.1.2.3/32"]))
    assert await refused(proxy, get("http://git.corp:443/")) == R.DESTINATION_PRIVATE_ADDRESS


async def test_sec_eg_13_the_private_name_is_looked_up_by_the_private_resolver_only_after_the_public_one_refuses() -> None:
    @dataclass
    class Refusing:
        calls: list[str] = field(default_factory=list[str])

        async def resolve(self, host: str) -> tuple[str, ...]:
            from harness_core.sandbox.egress.errors import AddressRefused

            self.calls.append(host)
            raise AddressRefused(host)

    public, private = Refusing(), StaticResolver(TABLE)
    clock = ManualClock()
    built = EgressProxy(
        bindings=InMemoryEgressBindings(), tokens=InMemoryTokenSource(b"g21", clock), resolver=public,
        private_resolver=private, upstreams={}, clock=clock, ids=SeededIds(),
        upstream_egress=upstream_egress_from_env({"EGRESS_PRIVATE_ALLOW": "git.corp:443"}),
    )
    await built.bindings.register(A)
    forward = await built.authorize(get("http://git.corp:443/"), local=A.proxy_address, peer=A.sandbox_address)
    assert forward.url == "http://10.1.2.3:443/" and public.calls == ["git.corp"]
    # No entry can open it: the private resolver is not consulted, the public refusal stands.
    assert await refused(built, get("http://unlisted.corp:443/")) == R.DESTINATION_PRIVATE_ADDRESS


# --- internals discovery ---------------------------------------------------------------------------------------------


def test_sec_iso_09_the_proxy_its_docker_host_and_route_gateways_are_internal(tmp_path: Path) -> None:
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
    )
    gateways = route_gateways(route)
    assert gateways == (ipaddress.IPv4Address("172.18.0.1"),)
    nets = host_networks([ipaddress.IPv4Interface("10.30.0.5/24")], gateways, [ipaddress.IPv6Address("fd00::5")])
    assert [str(n) for n in nets] == ["10.30.0.5/32", "10.30.0.1/32", "172.18.0.1/32", "fd00::5/128"]


def test_sec_iso_09_internal_names_are_taken_from_urls_and_host_ports() -> None:
    assert [host_of(v) for v in ("http://harness-state:3002", "redis:6379", "etcd", "http://[fd00::1]:8095/x")] == [
        "harness-state", "redis", "etcd", "fd00::1"
    ]


# --- chaining on the wire ------------------------------------------------------------------------------------------


@dataclass
class FakeUpstreamProxy:
    """A corporate forward proxy on loopback: records each request head, answers plain HTTP itself, and for CONNECT
    answers 200 then echoes what it is sent, so the test sees exactly what reached it."""

    heads: list[str] = field(default_factory=list[str])
    port: int = 0
    refuse: bool = False

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            self.heads.append(head)
            if self.refuse:
                writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")
                await writer.drain()
            elif head.startswith("CONNECT "):
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                data = await reader.read(1024)
                writer.write(b"echo:" + data)
                await writer.drain()
            else:
                body = b"via corporate proxy"
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def chained(caplog: pytest.LogCaptureFixture) -> AsyncIterator[tuple[int, FakeUpstreamProxy]]:
    upstream = FakeUpstreamProxy()
    listener = await asyncio.start_server(upstream.handle, "127.0.0.1", 0)
    upstream.port = listener.sockets[0].getsockname()[1]
    env = {**UPSTREAM_ENV, "EGRESS_UPSTREAM_PROXY": f"http://127.0.0.1:{upstream.port}"}
    proxy = make_proxy(env=env, tunnels=True)
    await proxy.bindings.register(A)

    async def connector(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # Only the upstream proxy is reachable: anything dialled direct would fail here.
        if (address, port) != ("127.0.0.1", upstream.port):
            raise ConnectionRefusedError(address)
        return await asyncio.open_connection(address, port)

    server = EgressServer(proxy, StaticBearerAuth("c"), instance="g21", connector=connector)
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    with caplog.at_level(logging.INFO, logger="harness_core.sandbox.egress"):
        yield data_port, upstream
    await server.stop()
    listener.close()
    with contextlib.suppress(Exception):
        await listener.wait_closed()


async def test_sec_eg_13_http_is_forwarded_through_the_upstream_proxy_with_its_credential(
    chained: tuple[int, FakeUpstreamProxy], caplog: pytest.LogCaptureFixture
) -> None:
    data_port, upstream = chained
    connector = aiohttp.TCPConnector(local_addr=("127.0.0.1", 0))
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.get(
            "http://public.example/page", proxy=f"http://127.0.0.2:{data_port}",
            proxy_headers={"Proxy-Authorization": "Basic c2FuZGJveDpndWVzcw=="},
        ) as response:
            body = await response.read()
    assert response.status == 200 and body == b"via corporate proxy"
    (head,) = upstream.heads
    # aiohttp leaves the default port out of the absolute form; the name is never in the request line.
    assert head.startswith(f"GET http://{PUBLIC}/page HTTP/1.1\r\n")
    assert "Host: public.example\r\n" in head
    assert "Proxy-Authorization: Basic c3ZjLXBpcGVzaHViOmNvcnAtcHJveHktcGFzc3dvcmQtMDEyMw==\r\n" in head
    assert "c2FuZGJveDpndWVzcw" not in head, "the sandbox's own proxy credential is dropped"
    text = "\n".join(r.getMessage() + repr(getattr(r, "harness", "")) for r in caplog.records)
    assert PROXY_SECRET not in text and "c3ZjLXBpcGVzaHVi" not in text


async def test_sec_eg_13_connect_is_chained_to_the_checked_address(
    chained: tuple[int, FakeUpstreamProxy], caplog: pytest.LogCaptureFixture
) -> None:
    data_port, upstream = chained
    reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
    writer.write(b"CONNECT public.example:443 HTTP/1.1\r\nHost: public.example:443\r\n\r\n")
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    assert head.startswith(b"HTTP/1.1 200")
    writer.write(b"client hello")
    await writer.drain()
    assert await asyncio.wait_for(reader.read(1024), 10) == b"echo:client hello"
    writer.close()
    (connect,) = upstream.heads
    assert connect.startswith(f"CONNECT {PUBLIC}:443 HTTP/1.1\r\n")
    assert "Proxy-Authorization: Basic " in connect
    opened = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.tunnel_opened"]
    assert opened and opened[0].harness["viaUpstreamProxy"] is True  # type: ignore[attr-defined]


async def test_sec_eg_13_a_private_entry_is_dialled_direct_not_through_the_upstream_proxy(
    chained: tuple[int, FakeUpstreamProxy],
) -> None:
    data_port, upstream = chained
    reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
    writer.write(b"CONNECT git.corp:443 HTTP/1.1\r\nHost: git.corp:443\r\n\r\n")
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    writer.close()
    # The test's connector reaches only the upstream proxy, so a direct dial of 10.1.2.3 fails: 502, nothing chained.
    assert b" 502 " in head.split(b"\r\n")[0] and b"upstream_unavailable" in head
    assert upstream.heads == []


async def test_sec_eg_13_an_upstream_proxy_that_refuses_connect_is_502(chained: tuple[int, FakeUpstreamProxy]) -> None:
    data_port, upstream = chained
    upstream.refuse = True
    reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
    writer.write(b"CONNECT public.example:443 HTTP/1.1\r\nHost: public.example:443\r\n\r\n")
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    writer.close()
    assert b" 502 " in head.split(b"\r\n")[0] and b"upstream_unavailable" in head


@dataclass
class RecordingSession:
    """``aiohttp.ClientSession.request`` as the data plane calls it, recorded; the call then fails like a dead host."""

    calls: list[dict[str, object]] = field(default_factory=list[dict[str, object]])

    async def request(self, method: str, url: str, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, **kwargs})
        raise aiohttp.ClientConnectionError("recorded")


@pytest.mark.parametrize("secure", [False, True])
async def test_sec_eg_13_the_proxy_credential_rides_only_the_hop_to_the_upstream_proxy(secure: bool) -> None:
    from harness_core.sandbox.egress.server import EgressDataPlane
    from harness_core.sandbox.egress.types import Forward

    proxy = make_proxy()
    session = RecordingSession()
    plane = EgressDataPlane(proxy, session)  # type: ignore[arg-type]
    url = f"https://{PUBLIC}:443/x" if secure else f"http://{PUBLIC}:80/x"
    forward = Forward(
        request_id="egr_1", sandbox_id="sbx-a", method="GET", url=url, headers=(("Host", "public.example"),), body=b"",
        server_hostname="public.example" if secure else None, proxy="http://corp-proxy.example:3128",
        proxy_headers=(("Proxy-Authorization", "Basic abc"),),
    )

    class _Request:
        transport = None

    _ = await plane._forward(_Request(), forward, A.proxy_address, A.sandbox_address)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    (call,) = session.calls
    assert call["proxy"] == "http://corp-proxy.example:3128"
    if secure:
        # HTTPS: the credential goes on the CONNECT to the upstream proxy only, never inside the TLS to the host.
        assert call["proxy_headers"] == {"Proxy-Authorization": "Basic abc"}
        assert ("Proxy-Authorization", "Basic abc") not in call["headers"]  # type: ignore[operator]
        assert call["server_hostname"] == "public.example"
    else:
        # Plain HTTP is sent to the upstream proxy itself, which reads the credential off the request.
        assert call["proxy_headers"] is None
        assert ("Proxy-Authorization", "Basic abc") in call["headers"]  # type: ignore[operator]
