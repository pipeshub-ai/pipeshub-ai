"""SR-1..SR-9 (WP-06, OWD-1, SEC-CR-01): credentials a sandbox needs are injected by the egress proxy, never held.

A route's credential is sealed by the trusted caller and opened by the proxy; the sandbox sends a placeholder as a
Basic password (a package registry), or anywhere in its headers and URL (an MCP server's own key), and HTTPS to
such a host is ended by the proxy with its own authority so the credential can be put on the request inside.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import ssl
from typing import TYPE_CHECKING

import pytest
from aiohttp import web

from harness_core.clock import SystemClock
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
    TokenUnavailable,
)
from harness_core.sandbox.egress.interception import EgressAuthority, load_authority
from harness_core.sandbox.egress.policy import ReflectionScrubbers, policy_errors
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.state import AUTHORITY_KEY, StateSealer
from harness_core.sandbox.egress.types import EGRESS_CA_PATH
from harness_core.sandbox.types import (
    CredentialInjection,
    EgressDestination,
    EgressPolicy,
    TokenRoute,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

R = EgressRefusalReason
SEALER = CredentialSealer(b"deployment-secret")
REGISTRY = "pypi.corp.example"
API = "api.vendor.example"
MIRROR = "mirror.vendor.example"
DOCS = "docs.example.org"
PH_REG = "pxh-ph-registry-0123456789abcdef"
PH_KEY = "pxh-ph-apikey-0123456789abcdef00"
PH_ID = "pxh-ph-clientid-0123456789abcdef"
REG_CREDENTIAL = "ci-bot:registry-pass-0123456789"
API_KEY = "apikey-secret-value-0123456789"
CLIENT_ID = "client-id-value-0123456789abcd"
PUBLIC = "93.184.216.34"


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def sealed_route(placeholder: str, host: str, credential: str, injection: CredentialInjection) -> TokenRoute:
    return TokenRoute(placeholder, host, SEALER.seal(placeholder, credential), injection)


POLICY = EgressPolicy(
    destinations=(
        EgressDestination(REGISTRY, (80, 443)),
        EgressDestination(API, (80, 443)),
        EgressDestination(MIRROR, (80, 443)),
        EgressDestination(DOCS, (80, 443)),
    ),
    token_routes=(
        sealed_route(PH_REG, REGISTRY, REG_CREDENTIAL, CredentialInjection.BASIC),
        sealed_route(PH_KEY, API, API_KEY, CredentialInjection.SUBSTITUTE),
        sealed_route(PH_ID, API, CLIENT_ID, CredentialInjection.SUBSTITUTE),
    ),
)
# The same secret to a second host: the same placeholder and sealed value, another destination.
POLICY = EgressPolicy(
    destinations=POLICY.destinations,
    token_routes=(*POLICY.token_routes, TokenRoute(PH_KEY, MIRROR, POLICY.token_routes[1].credential_ref, CredentialInjection.SUBSTITUTE)),
)
LABELS = {"orgId": "o1", "mcpServer": "m1"}
A = EgressBinding("sbx-a", "127.0.0.2", "127.0.0.1", POLICY, LABELS)


def make_proxy(*, sealed: bool = True, intercepts: bool = False) -> EgressProxy:
    clock = ManualClock()
    tokens = RoutedTokenSource(InMemoryTokenSource(b"t", clock), SealedTokenSource(SEALER, clock) if sealed else None)
    return EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=tokens,
        resolver=StaticResolver({REGISTRY: [PUBLIC], API: [PUBLIC], MIRROR: [PUBLIC], DOCS: [PUBLIC]}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(intercepts=intercepts),
    )


@pytest.fixture
async def proxy() -> EgressProxy:
    built = make_proxy()
    await built.bindings.register(A)
    return built


def req(url: str, headers: dict[str, str] | None = None, method: str = "GET") -> ProxyRequest:
    return ProxyRequest(method, url, tuple((headers or {}).items()))


async def authorize(proxy: EgressProxy, request: ProxyRequest):  # noqa: ANN201 - Forward
    return await proxy.authorize(request, local=A.proxy_address, peer=A.sandbox_address)


async def reason(proxy: EgressProxy, request: ProxyRequest) -> R:
    with pytest.raises(EgressRefused) as caught:
        _ = await authorize(proxy, request)
    return caught.value.reason


class TestSR1Sealing:
    def test_a_sealed_credential_opens_only_on_its_own_route(self) -> None:
        ref = SEALER.seal(PH_KEY, API_KEY)
        assert API_KEY not in ref and SEALER.open(TokenRoute(PH_KEY, API, ref)) == API_KEY
        with pytest.raises(TokenUnavailable):
            _ = SEALER.open(TokenRoute(PH_ID, API, ref))
        with pytest.raises(TokenUnavailable):
            _ = CredentialSealer(b"another-deployment").open(TokenRoute(PH_KEY, API, ref))

    async def test_a_proxy_without_the_key_refuses_rather_than_forwarding_bare(self) -> None:
        keyless = make_proxy(sealed=False)
        await keyless.bindings.register(A)
        request = req(f"http://{API}/v1?key={PH_KEY}")
        assert await reason(keyless, request) == R.TOKEN_UNAVAILABLE

    async def test_run_bound_gateway_routes_still_come_from_the_token_authority(self) -> None:
        clock = ManualClock()
        routed = RoutedTokenSource(InMemoryTokenSource(b"t", clock), SealedTokenSource(SEALER, clock))
        issued = await routed.issue(TokenRoute("ph-model-0123456789abcdef", "model-gateway", "gateway:model"), A)
        assert issued.value == InMemoryTokenSource.derive(b"t", A.sandbox_id, "gateway:model", 0)


class TestSR2Basic:
    async def test_the_registry_gets_the_operators_credential_not_the_sandboxs(self, proxy: EgressProxy) -> None:
        forward = await authorize(proxy, req(f"http://{REGISTRY}/simple/x/", {"Authorization": basic("anyone", PH_REG)}))
        assert dict(forward.headers)["Authorization"] == "Basic " + base64.b64encode(REG_CREDENTIAL.encode()).decode()
        # SEC-C-15: the whole credential too, which a registry may echo as the Basic header it received.
        assert forward.reflections == ((REG_CREDENTIAL, PH_REG), ("registry-pass-0123456789", PH_REG))

    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ({}, R.PLACEHOLDER_MISSING),
            ({"Authorization": basic("ci-bot", "guessed-password")}, R.CALLER_AUTHORIZATION),
            ({"Authorization": f"Bearer {PH_REG}"}, R.CALLER_AUTHORIZATION),
        ],
    )
    async def test_refusals(self, proxy: EgressProxy, headers: dict[str, str], expected: R) -> None:
        assert await reason(proxy, req(f"http://{REGISTRY}/simple/", headers)) == expected

    async def test_the_registry_placeholder_to_another_host_is_refused(self, proxy: EgressProxy) -> None:
        request = req(f"http://{DOCS}/", {"Authorization": basic("x", PH_REG)})
        assert await reason(proxy, request) == R.PLACEHOLDER_WRONG_DESTINATION


class TestSR3Substitute:
    async def test_every_placeholder_in_headers_and_url_becomes_its_credential(self, proxy: EgressProxy) -> None:
        request = req(
            f"http://{API}/v1/items?key={PH_KEY}&id={PH_ID}",
            {"Authorization": f"token {PH_KEY}", "X-Client": PH_ID, "Accept": "application/json"},
        )
        forward = await authorize(proxy, request)
        headers = dict(forward.headers)
        assert forward.url == f"http://{PUBLIC}:80/v1/items?key={API_KEY}&id={CLIENT_ID}"
        assert headers["Authorization"] == f"token {API_KEY}" and headers["X-Client"] == CLIENT_ID
        assert set(forward.reflections) == {(API_KEY, PH_KEY), (CLIENT_ID, PH_ID)}

    async def test_a_request_without_a_placeholder_goes_out_without_a_credential(self, proxy: EgressProxy) -> None:
        forward = await authorize(proxy, req(f"http://{API}/health"))
        assert forward.reflections == () and "Authorization" not in dict(forward.headers)

    async def test_one_secret_reaches_each_host_it_is_routed_to(self, proxy: EgressProxy) -> None:
        forward = await authorize(proxy, req(f"http://{MIRROR}/x", {"X-Key": PH_KEY}))
        assert dict(forward.headers)["X-Key"] == API_KEY

    @pytest.mark.parametrize(
        "request_",
        [
            req(f"http://{DOCS}/?k={PH_KEY}"),
            req(f"http://{DOCS}/", {"X-Leak": f"prefix {PH_ID}"}),
            req(f"http://{MIRROR}/", {"X-Id": PH_ID}),
            req(f"http://{API}/", {"Authorization": basic("x", PH_REG)}),
        ],
    )
    async def test_a_placeholder_sent_where_it_is_not_routed_is_refused(
        self, proxy: EgressProxy, request_: ProxyRequest
    ) -> None:
        assert await reason(proxy, request_) == R.PLACEHOLDER_WRONG_DESTINATION

    async def test_another_sandboxs_placeholder_is_refused(self, proxy: EgressProxy) -> None:
        other = EgressBinding("sbx-b", "127.0.0.4", "127.0.0.3", EgressPolicy(
            destinations=(EgressDestination(API, (80,)),),
            token_routes=(sealed_route("pxh-ph-other-0123456789abcdef00", API, "x" * 20, CredentialInjection.SUBSTITUTE),),
        ))
        await proxy.bindings.register(other)
        request = req(f"http://{API}/", {"Authorization": "Bearer pxh-ph-other-0123456789abcdef00"})
        assert await reason(proxy, request) == R.PLACEHOLDER_FOREIGN

    async def test_the_credential_coming_back_is_scrubbed_and_a_short_one_is_left_alone(self) -> None:
        scrubber = ReflectionScrubbers(((API_KEY, PH_KEY), (CLIENT_ID, PH_ID)))
        stream = f'{{"echo": "{API_KEY}", "id": "{CLIENT_ID}"}}'.encode()
        out = b"".join(scrubber.feed(stream[i : i + 7]) for i in range(0, len(stream), 7)) + scrubber.finish()
        assert json.loads(out) == {"echo": PH_KEY, "id": PH_ID}

    async def test_a_short_credential_is_not_a_replay_tripwire(self) -> None:
        short = EgressPolicy(
            destinations=(EgressDestination(API, (80,)), EgressDestination(DOCS, (80,))),
            token_routes=(sealed_route(PH_KEY, API, "admin", CredentialInjection.SUBSTITUTE),),
        )
        proxy = make_proxy()
        await proxy.bindings.register(EgressBinding("sbx-s", "127.0.0.2", "127.0.0.1", short))
        _ = await authorize(proxy, req(f"http://{API}/", {"X-Key": PH_KEY}))
        forward = await authorize(proxy, req(f"http://{DOCS}/admin"))
        assert forward.url.endswith("/admin")


class TestSR4Policy:
    def test_the_shared_secret_policy_binds(self) -> None:
        assert policy_errors(POLICY) == []

    def test_a_placeholder_reused_for_another_credential_or_kind_is_refused(self) -> None:
        clash = EgressPolicy(
            destinations=POLICY.destinations,
            token_routes=(
                sealed_route(PH_KEY, API, API_KEY, CredentialInjection.SUBSTITUTE),
                sealed_route(PH_KEY, MIRROR, "another-secret-0123456789", CredentialInjection.SUBSTITUTE),
            ),
        )
        assert any("reuses a placeholder" in e for e in policy_errors(clash))

    def test_two_routes_to_one_host_only_when_both_substitute(self) -> None:
        mixed = EgressPolicy(
            destinations=POLICY.destinations,
            token_routes=(
                sealed_route(PH_REG, REGISTRY, REG_CREDENTIAL, CredentialInjection.BASIC),
                sealed_route(PH_KEY, REGISTRY, API_KEY, CredentialInjection.SUBSTITUTE),
            ),
        )
        assert any("second route for its destination" in e for e in policy_errors(mixed))

    @pytest.mark.parametrize(
        ("ports", "upstream", "fragment"),
        [
            ((5432,), "postgres://app@db.internal:5433/?sslmode=require", None),
            ((5433,), "postgres://app@db.internal:5433/", "must use port 5432"),
            ((5432,), "https://db.internal/", "needs a postgres:// upstream"),
            ((5432,), "postgres://app:pw@db.internal/", "upstream that is not"),
            ((5432,), "postgres://app@db.internal/?password=x", "upstream that is not"),
        ],
    )
    def test_a_database_route(self, ports: tuple[int, ...], upstream: str, fragment: str | None) -> None:
        policy = EgressPolicy(
            destinations=(EgressDestination("pxh-database", ports, upstream=upstream),),
            token_routes=(TokenRoute("pxh-ph-db-0123456789abcdef", "pxh-database", "sealed:v1.x", CredentialInjection.POSTGRES),),
        )
        errors = policy_errors(policy)
        assert (errors == []) if fragment is None else any(fragment in e for e in errors), errors

    async def test_http_to_a_database_destination_is_refused(self) -> None:
        policy = EgressPolicy(
            destinations=(EgressDestination("pxh-database", (5432,), upstream="postgres://app@db/"),),
            token_routes=(sealed_route("pxh-ph-db-0123456789abcdef", "pxh-database", "{}", CredentialInjection.POSTGRES),),
        )
        proxy = make_proxy()
        await proxy.bindings.register(EgressBinding("sbx-d", "127.0.0.2", "127.0.0.1", policy))
        assert await reason(proxy, req("http://pxh-database:5432/")) == R.SCHEME_NOT_ALLOWED


class TestSR5Intercepted:
    def connect(self, target: str) -> ProxyRequest:
        return ProxyRequest("CONNECT", target, (("Host", target),))

    async def test_a_credentialed_connect_is_intercepted_only_when_the_proxy_can(self) -> None:
        on, off = make_proxy(intercepts=True), make_proxy(intercepts=False)
        for built in (on, off):
            await built.bindings.register(A)
        tunnel = await on.authorize_tunnel(self.connect(f"{API}:443"), local=A.proxy_address, peer=A.sandbox_address)
        assert tunnel.intercept and tunnel.connect_to == ()
        with pytest.raises(EgressRefused) as caught:
            _ = await off.authorize_tunnel(self.connect(f"{API}:443"), local=A.proxy_address, peer=A.sandbox_address)
        assert caught.value.reason is R.TUNNEL_REFUSED

    async def test_inside_the_tls_the_request_goes_to_the_host_over_tls_by_its_pinned_address(self) -> None:
        proxy = make_proxy(intercepts=True)
        await proxy.bindings.register(A)
        request = ProxyRequest("GET", f"/v1?key={PH_KEY}", (("Host", API), ("X-Id", PH_ID)))
        forward = await proxy.authorize_intercepted(request, host=API, port=443, local="127.0.0.2", peer="127.0.0.1")
        assert forward.url == f"https://{PUBLIC}:443/v1?key={API_KEY}" and forward.server_hostname == API
        assert dict(forward.headers)["Host"] == API and dict(forward.headers)["X-Id"] == CLIENT_ID

    async def test_inside_the_tls_another_host_is_refused(self) -> None:
        proxy = make_proxy(intercepts=True)
        await proxy.bindings.register(A)
        request = ProxyRequest("GET", "/", (("Host", DOCS),))
        with pytest.raises(EgressRefused) as caught:
            _ = await proxy.authorize_intercepted(request, host=API, port=443, local="127.0.0.2", peer="127.0.0.1")
        assert caught.value.reason is R.DESTINATION_NOT_ALLOWED


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def put(self, key: str, value: str, *, ttl_s: int | None) -> None:
        self.values[key] = value

    async def put_if_absent(self, key: str, value: str) -> str:
        return self.values.setdefault(key, value)

    async def delete(self, key: str) -> None:
        _ = self.values.pop(key, None)

    async def entries(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self.values.items()))


class TestSR6Authority:
    async def test_every_replica_signs_with_the_first_ones_authority_and_the_key_is_sealed(self) -> None:
        store, sealer, clock = _Store(), StateSealer(b"k"), SystemClock()
        first = await load_authority(clock, store, sealer)
        second = await load_authority(clock, store, sealer)
        assert first.certificate_pem == second.certificate_pem
        assert b"PRIVATE KEY" not in store.values[AUTHORITY_KEY].encode()

    async def test_an_authority_sealed_under_another_key_is_replaced(self) -> None:
        store, clock = _Store(), SystemClock()
        old = await load_authority(clock, store, StateSealer(b"old"))
        new = await load_authority(clock, store, StateSealer(b"new"))
        assert new.certificate_pem != old.certificate_pem


@pytest.fixture
async def upstream() -> AsyncIterator[tuple[int, list[dict[str, str]]]]:
    seen: list[dict[str, str]] = []

    async def handle(request: web.Request) -> web.Response:
        seen.append({"path": request.path_qs, **dict(request.headers.items())})
        return web.json_response({"echo": request.headers.get("X-Key", "")})

    app = web.Application()
    _ = app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield port, seen
    await runner.cleanup()


async def _https_through(
    data_port: int, ca: bytes, host: str, path: str, headers: dict[str, str]
) -> tuple[int, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
    writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
    await writer.drain()
    assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
    context = ssl.create_default_context(cadata=ca.decode())
    await writer.start_tls(context, server_hostname=host)
    lines = "".join(f"{k}: {v}\r\n" for k, v in {"Host": host, "Connection": "close", **headers}.items())
    writer.write(f"GET {path} HTTP/1.1\r\n{lines}\r\n".encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), body


async def test_sr7_https_from_the_sandbox_carries_the_credential_only_past_the_proxy(
    upstream: tuple[int, list[dict[str, str]]],
) -> None:
    """The sandbox trusts the proxy's authority (read from the proxy), speaks HTTPS with placeholders; the service
    sees the secret; what comes back has the placeholder again."""
    port, seen = upstream
    clock = SystemClock()
    policy = EgressPolicy(
        destinations=(EgressDestination(API, (443,), upstream=f"http://127.0.0.1:{port}"),),
        token_routes=(sealed_route(PH_KEY, API, API_KEY, CredentialInjection.SUBSTITUTE),),
    )
    proxy = make_proxy(intercepts=True)
    await proxy.bindings.register(EgressBinding("sbx-h", "127.0.0.2", "127.0.0.1", policy, LABELS))
    authority = EgressAuthority.generate(clock)
    server = EgressServer(proxy, StaticBearerAuth("c"), instance="sr", authority=authority)
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
        writer.write(f"GET {EGRESS_CA_PATH} HTTP/1.1\r\nHost: egress-proxy\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        ca = (await reader.read()).partition(b"\r\n\r\n")[2]
        writer.close()
        assert ca == authority.certificate_pem
        status, body = await _https_through(data_port, ca, API, f"/v1?key={PH_KEY}", {"X-Key": PH_KEY})
    finally:
        await server.stop()
    assert status == 200
    assert seen[0]["path"] == f"/v1?key={API_KEY}" and seen[0]["X-Key"] == API_KEY
    assert API_KEY.encode() not in body and json.loads(body.split(b"\r\n")[1] if b"\r\n" in body else body) == {"echo": PH_KEY}


async def test_sr8_an_unbound_source_cannot_read_the_authority() -> None:
    proxy = make_proxy(intercepts=True)
    server = EgressServer(proxy, StaticBearerAuth("c"), instance="sr", authority=EgressAuthority.generate(SystemClock()))
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    try:
        reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
        writer.write(f"GET {EGRESS_CA_PATH} HTTP/1.1\r\nHost: egress-proxy\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        raw = await reader.read()
        writer.close()
    finally:
        await server.stop()
    assert raw.startswith(b"HTTP/1.1 403") and b"unbound_source" in raw


def test_sr9_the_authority_signs_certificates_a_strict_client_accepts() -> None:
    """Python's default context checks key usage and key identifiers; a leaf it rejects would break every client."""
    authority = EgressAuthority.generate(SystemClock())
    client = ssl.create_default_context(cadata=authority.certificate_pem.decode())
    server = authority.server_context(API)
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    s_in, s_out = ssl.MemoryBIO(), ssl.MemoryBIO()
    c = client.wrap_bio(incoming, outgoing, server_hostname=API)
    s = server.wrap_bio(s_in, s_out, server_side=True)
    for _ in range(10):
        for side in (c, s):
            with contextlib.suppress(ssl.SSLWantReadError):
                side.do_handshake()
        s_in.write(outgoing.read())
        incoming.write(s_out.read())
    assert c.version() is not None and c.getpeercert()["subjectAltName"] == (("DNS", API),)
