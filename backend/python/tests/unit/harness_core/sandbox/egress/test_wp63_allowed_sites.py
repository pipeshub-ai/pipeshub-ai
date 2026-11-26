"""WP-63 (C6.11, SEC-EG-07): allowed websites in the egress proxy, never a git remote (git uses its fetch tool).

An allowed website is reached only as the admin named it: its TLS is ended by the proxy's authority so every request
is seen; read-only admits GET, HEAD and OPTIONS, never a write, and drops the sandbox's
own credentials; path limits hold; the CONNECT name, the TLS name and the Host are one (421 otherwise); a read-only-
listed host is never written to; a host-level-only entry is an opaque tunnel whose ClientHello must name its host.
Every request is reported for the run's activity with its path (no query), and every refusal names what it did.
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from typing import TYPE_CHECKING

import pytest

from harness_core.clock import SystemClock
from harness_core.gateway.refusal_reports import (
    InMemoryRefusalSink,
    RefusalReporter,
    SiteReport,
)
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
from harness_core.sandbox.egress.interception import EgressAuthority
from harness_core.sandbox.egress.policy import check_destination, policy_errors
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.sites import (
    GIT_HOSTS,
    PRESET_HOSTS,
    READ_ONLY_HOSTS,
    client_hello_sni,
    git_request,
    host_covers,
    shown_path,
    site_use,
)
from harness_core.sandbox.egress.types import Target
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    SiteAccess,
    SiteMode,
    TokenRoute,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from tests.support.tls_certs import make_pki

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

R = EgressRefusalReason
PUBLIC = "93.184.216.34"
PH = "ph-model-0123456789abcdef"
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}
READ = SiteAccess(SiteMode.READ)
WRITE = SiteAccess(SiteMode.READ_WRITE)
POLICY = EgressPolicy(
    destinations=(
        EgressDestination("model-gateway", (8097,)),
        EgressDestination("docs.example.com", (443,), site=SiteAccess(SiteMode.READ, paths=("/api/",))),
        EgressDestination("*.docs.example.org", (443,), site=READ),
        EgressDestination("mirror.example.org", (443,), site=WRITE),
        EgressDestination("*.amazonaws.com", (443,), site=WRITE),
        EgressDestination("pinned.example.org", (443,), site=SiteAccess(SiteMode.READ_WRITE, opaque=True)),
    ),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)
BOUND = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", POLICY, LABELS)
HOSTS = ["docs.example.com", "a.docs.example.org", "mirror.example.org", "b.s3.amazonaws.com", "pinned.example.org", "evil.example"]


async def _proxy(*, intercepts: bool = True, tunnels: bool = False) -> tuple[EgressProxy, InMemoryRefusalSink, RefusalReporter]:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    await bindings.register(BOUND)
    sink = InMemoryRefusalSink()
    reporter = RefusalReporter(sink)
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(b"wp63", clock, ttl_s=600),
        resolver=StaticResolver(dict.fromkeys(HOSTS, [PUBLIC])),
        upstreams={"model-gateway": "http://upstream-model:9000/"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(intercepts=intercepts, connect_tunnels=tunnels),
        refusals=reporter,
    )
    return proxy, sink, reporter


def _inside(path: str, method: str = "GET", headers: dict[str, str] | None = None, body: bytes = b"", host: str = "docs.example.com") -> ProxyRequest:
    return ProxyRequest(method, path, (("Host", host), *(headers or {}).items()), body)


async def _intercepted(proxy: EgressProxy, request: ProxyRequest, host: str = "docs.example.com"):  # noqa: ANN202 - Forward
    return await proxy.authorize_intercepted(request, host=host, port=443, local=BOUND.proxy_address, peer=BOUND.sandbox_address)


async def _refused(proxy: EgressProxy, request: ProxyRequest, host: str = "docs.example.com") -> EgressRefused:
    with pytest.raises(EgressRefused) as caught:
        _ = await _intercepted(proxy, request, host)
    return caught.value


async def _connect(proxy: EgressProxy, target: str):  # noqa: ANN202 - Tunnel
    request = ProxyRequest("CONNECT", target, (("Host", target),))
    return await proxy.authorize_tunnel(request, local=BOUND.proxy_address, peer=BOUND.sandbox_address)


class TestSitesInPolicy:
    def test_c6_11_a_wildcard_covers_subdomains_on_a_label_boundary_never_the_apex(self) -> None:
        assert host_covers("*.docs.example.org", "a.docs.example.org")
        assert not host_covers("*.docs.example.org", "docs.example.org")
        assert not host_covers("*.docs.example.org", "evildocs.example.org")
        site = check_destination(Target("a.docs.example.org", 443, "/"), POLICY)
        assert site.site == READ
        with pytest.raises(EgressRefused) as caught:
            _ = check_destination(Target("docs.example.org", 443, "/"), POLICY)
        assert caught.value.reason is R.DESTINATION_NOT_ALLOWED

    def test_c6_11_a_site_is_https_on_443_only(self) -> None:
        with pytest.raises(EgressRefused):
            _ = check_destination(Target("docs.example.com", 80, "/"), POLICY)

    def test_sec_eg_07_a_broker_refuses_a_site_it_cannot_enforce(self) -> None:
        bad = [
            EgressDestination("*", (443,), site=READ),
            EgressDestination("*.com.", (443,), site=READ),
            EgressDestination("10.0.0.1", (443,), site=READ),
            EgressDestination("docs.example.com", (80,), site=READ),
            EgressDestination("docs.example.com", (443,), site=SiteAccess(SiteMode.READ, opaque=True)),
            EgressDestination("pypi.org", (443,), site=SiteAccess(SiteMode.READ_WRITE, opaque=True)),
            EgressDestination("pastebin.com", (443,), site=SiteAccess(SiteMode.READ_WRITE, opaque=True)),
            # Git hosts are never websites, in any mode.
            EgressDestination("github.com", (443,), site=READ),
            EgressDestination("*.githubusercontent.com", (443,), site=READ),
            EgressDestination("*.github.com", (443,), site=READ),
            EgressDestination("gitlab.com", (443,), site=WRITE),
            EgressDestination("*.example.org", (443,), site=SiteAccess(SiteMode.READ_WRITE, opaque=True)),
            EgressDestination("x.example.org", (443,), site=SiteAccess(SiteMode.READ, paths=("nope",))),
            EgressDestination("x.example.org", (443,), site=SiteAccess(SiteMode.READ, paths=("/a/../b",))),
        ]
        for destination in bad:
            assert policy_errors(EgressPolicy(destinations=(destination,))), destination
        assert policy_errors(POLICY) == []

    def test_the_release_data_lists_echo_hosts_and_presets(self) -> None:
        assert "pastebin.com" in READ_ONLY_HOSTS and "*.s3.amazonaws.com" in READ_ONLY_HOSTS
        assert "pypi.org" in PRESET_HOSTS and "github.com" not in PRESET_HOSTS
        assert {"github.com", "gitlab.com", "*.githubusercontent.com"} <= set(GIT_HOSTS)


class TestTunnelsToSites:
    async def test_c6_11_an_allowed_website_is_inspected(self) -> None:
        proxy, _, _ = await _proxy()
        tunnel = await _connect(proxy, "docs.example.com:443")
        assert tunnel.intercept and not tunnel.require_sni

    async def test_c6_11_without_the_authority_a_site_is_refused_never_tunnelled_blind(self) -> None:
        proxy, _, _ = await _proxy(intercepts=False, tunnels=True)
        with pytest.raises(EgressRefused) as caught:
            _ = await _connect(proxy, "docs.example.com:443")
        assert caught.value.reason is R.TUNNEL_REFUSED

    async def test_c6_11_host_level_only_is_an_opaque_tunnel_that_checks_its_tls_name(self) -> None:
        proxy, sink, reporter = await _proxy(tunnels=True)
        tunnel = await _connect(proxy, "pinned.example.org:443")
        assert not tunnel.intercept and tunnel.require_sni
        assert tunnel.connect_to == ((PUBLIC, 443),)
        await reporter.drain()
        assert [a.site for _, a in sink.allowances] == [SiteReport("/", "host-level", "write")]

    async def test_c6_11_host_level_only_needs_the_deployments_tunnel_switch(self) -> None:
        proxy, _, _ = await _proxy(tunnels=False)
        with pytest.raises(EgressRefused):
            _ = await _connect(proxy, "pinned.example.org:443")

    async def test_sec_eg_06_a_site_is_never_opened_to_an_address_or_an_unlisted_host(self) -> None:
        proxy, _, _ = await _proxy(tunnels=True)
        for target, reason in (("93.184.216.34:443", R.TUNNEL_IP_LITERAL), ("evil.example:443", R.DESTINATION_NOT_ALLOWED)):
            with pytest.raises(EgressRefused) as caught:
                _ = await _connect(proxy, target)
            assert caught.value.reason is reason


class TestReadOnly:
    async def test_c6_11_a_read_goes_through_with_the_sandboxs_credentials_dropped(self) -> None:
        proxy, sink, reporter = await _proxy()
        planted = {"Authorization": "token ghp_attacker00000000000000000000000000", "Cookie": "session=attacker"}
        for request in (
            _inside("/api/reference?q=x", headers=planted),
            _inside("/api/reference", "HEAD", planted),
            _inside("/api/reference", "OPTIONS"),
        ):
            forward = await _intercepted(proxy, request)
            sent = {k.lower() for k, _ in forward.headers}
            assert "authorization" not in sent and "cookie" not in sent
            # Resolved once and checked: the forward dials the checked address, the name kept for TLS.
            assert forward.url.startswith(f"https://{PUBLIC}:443/api/reference")
            assert forward.server_hostname == "docs.example.com"
        await reporter.drain()
        shown = {(a.host, a.site) for _, a in sink.allowances}
        assert shown == {("docs.example.com", SiteReport("/api/reference", "read", "read"))}

    @pytest.mark.parametrize(
        ("request_", "reason"),
        [
            (_inside("/api/repo.git/info/refs?service=git-upload-pack"), R.SITE_GIT_REFUSED),
            (_inside("/api/repo.git/info/refs?service=git-receive-pack"), R.SITE_GIT_REFUSED),
            (_inside("/api/repo.git/git-upload-pack", "POST", {"Content-Type": "application/x-git-upload-pack-request"}, b"0000"), R.SITE_GIT_REFUSED),
            (_inside("/api/repo.git/git-receive-pack", "POST", {"Content-Type": "application/x-git-receive-pack-request"}, b"0000"), R.SITE_GIT_REFUSED),
            (_inside("/api/issues", "POST", {"Content-Type": "application/json"}, b"{}"), R.SITE_READ_ONLY),
            (_inside("/api/reference", "PUT", body=b"x"), R.SITE_READ_ONLY),
            (_inside("/api/reference", "DELETE"), R.SITE_READ_ONLY),
            (_inside("/api/reference", "GET", body=b"smuggled"), R.SITE_READ_ONLY),
            (_inside("/blog/post"), R.SITE_PATH_NOT_ALLOWED),
            (_inside("/api%2f..%2fblog/post"), R.SITE_PATH_NOT_ALLOWED),
            (_inside("/apix/reference"), R.SITE_PATH_NOT_ALLOWED),
        ],
    )
    async def test_c6_11_sec_eg_07_git_a_write_or_another_path_is_refused(self, request_: ProxyRequest, reason: R) -> None:
        proxy, sink, reporter = await _proxy()
        refused = await _refused(proxy, request_)
        assert refused.reason is reason and refused.status == 403
        await reporter.drain()
        assert [r.report.site is not None for r in sink.records] == [True]

    async def test_c6_11_a_refused_upload_is_reported_for_the_activity_as_a_write(self) -> None:
        proxy, sink, reporter = await _proxy()
        _ = await _refused(proxy, _inside("/api/upload", "POST", body=b"secret"))
        await reporter.drain()
        (record,) = sink.records
        assert record.report.reason == "site_read_only" and record.report.host == "docs.example.com"
        assert record.report.site == SiteReport("/api/upload", "read", "write")

    async def test_owner_2026_11_01_git_is_refused_even_at_a_read_write_site(self) -> None:
        proxy, _, _ = await _proxy()
        refused = await _refused(
            proxy, _inside("/r.git/info/refs?service=git-receive-pack", host="mirror.example.org"), host="mirror.example.org"
        )
        assert refused.reason is R.SITE_GIT_REFUSED

    async def test_c6_11_a_request_naming_another_host_than_its_connection_is_421(self) -> None:
        proxy, _, _ = await _proxy()
        refused = await _refused(proxy, _inside("/api/reference", host="evil.example"))
        assert refused.reason is R.HOST_MISMATCH and refused.status == 421
        absolute = ProxyRequest("GET", "https://evil.example/x", (("Host", "evil.example"),))
        assert (await _refused(proxy, absolute)).status == 421


class TestReadWrite:
    async def test_c6_11_read_write_admits_every_method_with_the_sandboxs_own_sign_in(self) -> None:
        proxy, sink, reporter = await _proxy()
        forward = await _intercepted(
            proxy,
            _inside("/upload", "POST", {"Authorization": "Bearer mirror-own-0123456789", "Content-Type": "application/json"}, b"{}", host="mirror.example.org"),
            host="mirror.example.org",
        )
        assert forward.method == "POST" and ("Authorization", "Bearer mirror-own-0123456789") in forward.headers
        await reporter.drain()
        assert sink.allowances[-1][1].site == SiteReport("/upload", "read-write", "write")

    async def test_sec_eg_07_a_read_only_listed_host_is_never_written_whatever_the_entry(self) -> None:
        proxy, _, _ = await _proxy()
        bucket = "b.s3.amazonaws.com"
        refused = await _refused(proxy, _inside("/", "POST", body=b"secret", host=bucket), host=bucket)
        assert refused.reason is R.SITE_READ_ONLY
        assert (await _intercepted(proxy, _inside("/x", host=bucket), host=bucket)).method == "GET"


class TestShownAndParsed:
    def test_the_activity_path_drops_the_query_and_masks_tokens(self) -> None:
        assert shown_path("/search?q=secret") == "/search"
        assert "ghp_" not in shown_path("/x/ghp_0123456789abcdefghij0123456789abcdef")
        assert len(shown_path("/" + "a" * 500)) == 120

    def test_git_smart_http_is_recognised_in_every_spelling(self) -> None:
        for path in ("/o/r.git/info/refs?service=git-upload-pack", "/o/r/info/refs?x=1&service=git-receive-pack",
                     "/o/r.git/git-upload-pack", "/o/r/git-receive-pack"):
            assert git_request(Target("docs.example.com", 443, path)), path
        assert not git_request(Target("docs.example.com", 443, "/docs/info/refs-guide"))
        target = Target("docs.example.com", 443, "/a")
        assert site_use(ProxyRequest("GET", "/a", ()), target) == "read"
        assert site_use(ProxyRequest("POST", "/a", (), b"x"), target) == "write"

    def test_a_client_hello_names_its_host(self) -> None:
        hello = _client_hello("pinned.example.org")
        assert client_hello_sni(hello) == "pinned.example.org"
        assert client_hello_sni(hello[:20]) is None
        assert client_hello_sni(b"GET / HTTP/1.1\r\n\r\n") is None

    def test_c6_11_the_authority_ends_tls_only_for_the_name_it_was_asked_for(self) -> None:
        authority = EgressAuthority.generate(SystemClock())
        assert _handshake(authority, "github.com", "github.com") is None
        failed = _handshake(authority, "github.com", "evil.example")
        assert failed is not None


def _client_hello(name: str) -> bytes:
    client = ssl.create_default_context()
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = client.wrap_bio(incoming, outgoing, server_hostname=name)
    with pytest.raises(ssl.SSLWantReadError):
        tls.do_handshake()
    return outgoing.read()


def _handshake(authority: EgressAuthority, connected: str, asked: str) -> ssl.SSLError | None:
    """A TLS handshake in memory: the proxy's context for ``connected``, a client asking for ``asked``."""
    server_ctx = authority.server_context(connected)
    # The client checks nothing itself, so a failure is the proxy's own name check.
    client_ctx = ssl.create_default_context(cadata=authority.certificate_pem.decode())
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    s_in, s_out, c_in, c_out = ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO(), ssl.MemoryBIO()
    server = server_ctx.wrap_bio(s_in, s_out, server_side=True)
    client = client_ctx.wrap_bio(c_in, c_out, server_hostname=asked)
    for _ in range(10):
        for side, out, into in ((client, c_out, s_in), (server, s_out, c_in)):
            try:
                side.do_handshake()
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLError as exc:
                return exc
            into.write(out.read())
        if client.version() is not None and server.version() is not None:
            return None
    return None


# --- over the real listener ------------------------------------------------------------------------------------


@pytest.fixture
async def pinned_api(tmp_path: Path) -> AsyncIterator[tuple[int, bytes, list[bytes]]]:
    """A TLS server for pinned.example.org on loopback: what a host-level-only site's tunnel reaches."""
    pki = make_pki("pinned.example.org")
    (tmp_path / "cert.pem").write_bytes(pki.cert_pem)
    (tmp_path / "key.pem").write_bytes(pki.key_pem)
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            received.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=context)
    yield server.sockets[0].getsockname()[1], pki.ca_pem, received
    server.close()
    await server.wait_closed()


@pytest.fixture
async def listener(pinned_api: tuple[int, bytes, list[bytes]]) -> AsyncIterator[tuple[int, RefusalReporter, InMemoryRefusalSink]]:
    port = pinned_api[0]
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    await bindings.register(EgressBinding("sbx-a", "127.0.0.2", "127.0.0.1", POLICY, LABELS))
    sink = InMemoryRefusalSink()
    reporter = RefusalReporter(sink)
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(b"wp63", clock, ttl_s=600),
        resolver=StaticResolver(dict.fromkeys(HOSTS, [PUBLIC])),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(intercepts=True, connect_tunnels=True),
        refusals=reporter,
    )

    async def connector(address: str, dialled: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if (address, dialled) != (PUBLIC, 443):
            raise ConnectionRefusedError(address)
        return await asyncio.open_connection("127.0.0.1", port)

    server = EgressServer(proxy, StaticBearerAuth("c"), instance="wp63", connector=connector)
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    yield data_port, reporter, sink
    await server.stop()


async def _tunnel_get(data_port: int, ca: bytes, name: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
    writer.write(b"CONNECT pinned.example.org:443 HTTP/1.1\r\nHost: pinned.example.org:443\r\n\r\n")
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    assert head.startswith(b"HTTP/1.1 200")
    context = ssl.create_default_context(cadata=ca.decode())
    context.check_hostname = False
    try:
        await writer.start_tls(context, server_hostname=name)
        writer.write(b"GET / HTTP/1.1\r\nHost: pinned.example.org\r\n\r\n")
        await writer.drain()
        return await asyncio.wait_for(reader.read(), 10)
    except (ssl.SSLError, ConnectionError, asyncio.IncompleteReadError):
        return b""
    finally:
        writer.close()


async def test_c6_11_a_host_level_only_tunnel_carries_tls_only_to_its_own_name(
    listener: tuple[int, RefusalReporter, InMemoryRefusalSink], pinned_api: tuple[int, bytes, list[bytes]]
) -> None:
    data_port, reporter, sink = listener
    _, ca, received = pinned_api
    assert (await _tunnel_get(data_port, ca, "pinned.example.org")).endswith(b"ok")
    assert len(received) == 1
    assert await _tunnel_get(data_port, ca, "fronted.example.net") == b""
    assert len(received) == 1, "nothing reached the upstream under another TLS name"
    await reporter.drain()
    assert [r.report.reason for r in sink.records] == ["host_mismatch"]
