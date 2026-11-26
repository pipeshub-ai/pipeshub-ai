"""PX-1..PX-6: the proxy core over in-memory adapters (P5.4 §1.2, §1.3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pytest

from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
    TokenUnavailable,
)
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

R = EgressRefusalReason
SECRET = b"px-test-secret"
PH = "ph-model-0123456789abcdef"
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}
POLICY = EgressPolicy(
    destinations=(
        EgressDestination("model-gateway", (8097,)),
        EgressDestination("public.example", (80,)),
        EgressDestination("internal.example", (80,)),
        EgressDestination("nowhere.example", (80,)),
    ),
    token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
)
A = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", POLICY, LABELS)


@dataclass
class FailingTokens:
    calls: list[str] = field(default_factory=list[str])

    async def issue(self, route: TokenRoute, binding: EgressBinding) -> object:
        self.calls.append(route.credential_ref)
        raise TokenUnavailable("down")


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def tokens(clock: ManualClock) -> InMemoryTokenSource:
    return InMemoryTokenSource(SECRET, clock, ttl_s=600)


@pytest.fixture
async def proxy(clock: ManualClock, tokens: InMemoryTokenSource) -> EgressProxy:
    bindings = InMemoryEgressBindings()
    await bindings.register(A)
    return EgressProxy(
        bindings=bindings,
        tokens=tokens,
        resolver=StaticResolver({"public.example": ["93.184.216.34"], "internal.example": ["93.184.216.34", "10.0.0.5"]}),
        upstreams={"model-gateway": "http://upstream-model:9000/base/"},
        clock=clock,
        ids=SeededIds(),
    )


def call(target: str = "/v1/chat", *, host: str = "model-gateway:8097", auth: str | None = f"Bearer {PH}", body: bytes = b"{}") -> ProxyRequest:
    headers = [("Host", host)]
    if auth is not None:
        headers.append(("Authorization", auth))
    return ProxyRequest("POST", target, tuple(headers), body)


async def reason_of(proxy: EgressProxy, request: ProxyRequest, *, local: str = "10.213.0.2", peer: str = "10.213.0.3") -> R:
    with pytest.raises(EgressRefused) as caught:
        _ = await proxy.authorize(request, local=local, peer=peer)
    return caught.value.reason


async def test_px1_the_connection_names_the_sandbox(proxy: EgressProxy) -> None:
    assert await reason_of(proxy, call(), local="10.213.0.10") == R.UNBOUND_SOURCE
    assert await reason_of(proxy, call(), peer="10.213.0.4") == R.SOURCE_MISMATCH


async def test_px2_the_placeholder_becomes_a_run_bound_token_refreshed_before_expiry(
    proxy: EgressProxy, clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    first = await proxy.authorize(call(), local="10.213.0.2", peer="10.213.0.3")
    expected = InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
    assert first.url == "http://upstream-model:9000/base/v1/chat"
    assert ("Authorization", f"Bearer {expected}") in first.headers
    assert first.reflections == ((expected, PH),)
    second = await proxy.authorize(call(), local="10.213.0.2", peer="10.213.0.3")
    assert ("Authorization", f"Bearer {expected}") in second.headers and tokens.issued == 1
    _ = clock.advance(545)
    third = await proxy.authorize(call(), local="10.213.0.2", peer="10.213.0.3")
    refreshed = InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 1)
    assert ("Authorization", f"Bearer {refreshed}") in third.headers and tokens.issued == 2


async def test_px3_other_destinations_are_resolved_checked_and_pinned(proxy: EgressProxy) -> None:
    forward = await proxy.authorize(call("http://public.example/x", auth=None), local="10.213.0.2", peer="10.213.0.3")
    assert forward.url == "http://93.184.216.34:80/x"
    assert ("Host", "public.example") in forward.headers and forward.reflections == ()
    assert await reason_of(proxy, call("http://internal.example/x", auth=None)) == R.DESTINATION_PRIVATE_ADDRESS
    assert await reason_of(proxy, call("http://nowhere.example/x", auth=None)) == R.DESTINATION_UNRESOLVABLE


REFUSALS = [
    (call(auth=None), R.PLACEHOLDER_MISSING),
    (call(auth="Bearer not-mine-0000000000"), R.CALLER_AUTHORIZATION),
    (call("http://evil.example/"), R.DESTINATION_NOT_ALLOWED),
    # A key in a model call's body is hidden (WP-103); in its URL it is refused.
    (call('/v1/chat?k="sk-ant-api03-abcdefghijklmnop"'), R.FORBIDDEN_PATTERN),
    (ProxyRequest("CONNECT", "evil.example:443", (("Host", "evil.example:443"),)), R.TUNNEL_REFUSED),
]


@pytest.mark.parametrize(("request_", "reason"), REFUSALS)
async def test_px4_each_refusal_is_logged_once_without_secrets(
    proxy: EgressProxy, caplog: pytest.LogCaptureFixture, request_: ProxyRequest, reason: R
) -> None:
    token = InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
    _ = await proxy.authorize(call(), local="10.213.0.2", peer="10.213.0.3")
    with caplog.at_level(logging.DEBUG, logger="harness_core.sandbox.egress"):
        assert await reason_of(proxy, request_) == reason
    records = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.refused"]
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    fields = records[0].harness  # type: ignore[attr-defined]
    assert fields["reason"] == reason.value and fields["sandboxId"] == "sbx-a" and fields["proxyRequestId"]
    assert {k: fields[k] for k in LABELS} == LABELS
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (token, PH, "sk-ant-api03", "/v1/chat", '{"k"'):
        assert secret not in text


async def test_px4_a_destination_refusal_names_the_host_but_a_pattern_refusal_does_not(
    proxy: EgressProxy, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="harness_core.sandbox.egress"):
        _ = await reason_of(proxy, call("http://evil.example/"))
        _ = await reason_of(proxy, call("http://sk-abcdefghijklmnopqrstuvwxyz.example/"))
    hosts = [r.harness.get("host") for r in caplog.records]  # type: ignore[attr-defined]
    assert hosts == ["evil.example", None]


async def test_px5_a_failing_token_source_refuses(clock: ManualClock) -> None:
    bindings = InMemoryEgressBindings()
    await bindings.register(A)
    proxy = EgressProxy(
        bindings=bindings, tokens=FailingTokens(), resolver=StaticResolver(), upstreams={"model-gateway": "http://u"},
        clock=clock, ids=SeededIds(),
    )
    assert await reason_of(proxy, call()) == R.TOKEN_UNAVAILABLE


async def test_px6_unregister_drops_tokens_and_the_binding(proxy: EgressProxy) -> None:
    token = InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
    _ = await proxy.authorize(call(), local="10.213.0.2", peer="10.213.0.3")
    assert token in proxy.live_tokens()
    await proxy.bindings.unregister("sbx-a")
    assert token not in proxy.live_tokens()
    assert await reason_of(proxy, call()) == R.UNBOUND_SOURCE


async def test_a_token_seen_by_one_sandbox_is_refused_from_another(proxy: EgressProxy) -> None:
    token = InMemoryTokenSource.derive(SECRET, "sbx-a", "gateway:model", 0)
    _ = await proxy.authorize(call(), local="10.213.0.2", peer="10.213.0.3")
    other = EgressBinding("sbx-b", "10.213.0.10", "10.213.0.11", EgressPolicy(
        destinations=POLICY.destinations, token_routes=(TokenRoute("ph-b-0123456789abcdef", "model-gateway", "gateway:model"),)
    ))
    await proxy.bindings.register(other)
    replay = call(body=f'{{"t": "{token}"}}'.encode(), auth="Bearer ph-b-0123456789abcdef")
    assert await reason_of(proxy, replay, local="10.213.0.10", peer="10.213.0.11") == R.TOKEN_REPLAYED
    assert await reason_of(proxy, call(), local="10.213.0.10", peer="10.213.0.11") == R.PLACEHOLDER_FOREIGN


TOOLS_PH = "ph-tools-0123456789abcdef"


def _tools_policy(upstream: str | None, placeholder: str = TOOLS_PH) -> EgressPolicy:
    return EgressPolicy(
        destinations=(EgressDestination("tool-gateway", (8097,), upstream=upstream),),
        token_routes=(TokenRoute(placeholder, "tool-gateway", "gateway:tools"),),
    )


async def test_eg1_a_sandbox_upstream_wins_over_the_deployment_table(
    clock: ManualClock, tokens: InMemoryTokenSource
) -> None:
    bindings = InMemoryEgressBindings()
    own = EgressBinding("sbx-own", "10.213.0.10", "10.213.0.11", _tools_policy("http://worker-7:8090/gw/k1/"), LABELS)
    shared = EgressBinding("sbx-shared", "10.213.0.18", "10.213.0.19", _tools_policy(None, TOOLS_PH + "-b"), LABELS)
    await bindings.register(own)
    await bindings.register(shared)
    proxy = EgressProxy(
        bindings=bindings,
        tokens=tokens,
        resolver=StaticResolver(),
        upstreams={"tool-gateway": "http://tool-gateway-service:8097"},
        clock=clock,
        ids=SeededIds(),
    )
    first = await proxy.authorize(
        call("/mcp", host="tool-gateway:8097", auth=f"Bearer {TOOLS_PH}"), local=own.proxy_address, peer=own.sandbox_address
    )
    second = await proxy.authorize(
        call("/mcp", host="tool-gateway:8097", auth=f"Bearer {TOOLS_PH}-b"),
        local=shared.proxy_address,
        peer=shared.sandbox_address,
    )
    assert first.url == "http://worker-7:8090/gw/k1/mcp"
    assert second.url == "http://tool-gateway-service:8097/mcp"
    injected = InMemoryTokenSource.derive(SECRET, "sbx-own", "gateway:tools", 0)
    assert dict(first.headers)["Authorization"] == f"Bearer {injected}"


ESCAPES = [
    "/../../runs/r1/claim",
    "/mcp/../../../search",
    "/./mcp",
    "/%2e%2e/runs/r1/claim",
    "/%2E%2e/x",
    "/.%2e/x",
    "/mcp/%2e",
    "/..?q=1",
]


@pytest.mark.parametrize("path", ESCAPES)
async def test_rs6_a_dot_segment_never_leaves_the_upstream_path(
    clock: ManualClock, tokens: InMemoryTokenSource, path: str
) -> None:
    # aiohttp removes these segments (encoded ones too) before it sends, so the gateway's base path would be escaped.
    bindings = InMemoryEgressBindings()
    upstream = "http://tool-gateway:8000/api/v1/harness/tool-gateway/k1"
    own = EgressBinding("sbx-own", "10.213.0.10", "10.213.0.11", _tools_policy(upstream), LABELS)
    await bindings.register(own)
    proxy = EgressProxy(bindings=bindings, tokens=tokens, resolver=StaticResolver(), upstreams={}, clock=clock, ids=SeededIds())
    request = call(path, host="tool-gateway:8097", auth=f"Bearer {TOOLS_PH}")
    assert await reason_of(proxy, request, local=own.proxy_address, peer=own.sandbox_address) == R.PATH_NOT_ALLOWED
    assert tokens.issued == 0


async def test_rs6_public_destinations_refuse_dot_segments_too(proxy: EgressProxy) -> None:
    assert await reason_of(proxy, call("http://public.example/a/../b", auth=None)) == R.PATH_NOT_ALLOWED
    assert await reason_of(proxy, call("/v1/%2e%2e/admin")) == R.PATH_NOT_ALLOWED


@pytest.mark.parametrize("path", ["/v1/file.name", "/a..b/c", "/.x/y", "/v1/chat?q=..", "/v1/...", "/v1/%2e%2e%2e"])
async def test_rs8_dots_inside_a_segment_still_forward(proxy: EgressProxy, path: str) -> None:
    forward = await proxy.authorize(call(path), local="10.213.0.2", peer="10.213.0.3")
    assert forward.url == "http://upstream-model:9000/base" + path
