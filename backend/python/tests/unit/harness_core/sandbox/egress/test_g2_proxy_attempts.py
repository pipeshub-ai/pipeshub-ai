"""G2 H-1..H-5, C-2..C-6: the attempt table's egress rows the P5.4 suites left open, on the proxy core.

The real ``EgressProxy`` (binding → target → content → destination → credentials) over in-memory bindings and
an in-memory token source, with C-13's two-gateway allowlist: each attempt is refused with the code the
attempt table names, and the refusal is logged once. No sockets and no Docker (the P5.4 SEC-* and C-13 lanes
own those); this pins the decision itself for each input class.
"""

from __future__ import annotations

import logging

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
)
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

R = EgressRefusalReason
SECRET = b"g2-proxy-secret"
VENDOR_KEY = "sk-ant-api03-" + "Q" * 40
MODEL, TOOLS = "model-gateway:8097", "tool-gateway:8097"
LABELS = {"orgId": "o1", "sessionId": "s1", "runId": "r1", "attemptId": "a1"}


def _policy(suffix: str) -> EgressPolicy:
    return EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)), EgressDestination("tool-gateway", (8097,))),
        token_routes=(
            TokenRoute(f"ph-model-{suffix}-0123456789abcdef", "model-gateway", "gateway:model"),
            TokenRoute(f"ph-tools-{suffix}-0123456789abcdef", "tool-gateway", "gateway:tools"),
        ),
    )


A = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", _policy("a"), LABELS)
B = EgressBinding("sbx-b", "10.213.0.10", "10.213.0.11", _policy("b"), LABELS)
A_MODEL, A_TOOLS = (r.placeholder for r in A.policy.token_routes)
B_MODEL, B_TOOLS = (r.placeholder for r in B.policy.token_routes)


async def _proxy() -> EgressProxy:
    bindings = InMemoryEgressBindings()
    await bindings.register(A)
    await bindings.register(B)
    return EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(SECRET, ManualClock(), ttl_s=600),
        resolver=StaticResolver(),
        upstreams={"model-gateway": "http://e3:8097/", "tool-gateway": "http://e2:8097/"},
        clock=ManualClock(),
        ids=SeededIds(),
    )


def _request(
    target: str = "/v1/chat/completions",
    *,
    host: str | tuple[str, ...] = MODEL,
    auth: str | None = A_MODEL,
    body: bytes = b"{}",
    method: str = "POST",
) -> ProxyRequest:
    hosts = (host,) if isinstance(host, str) else host
    headers = [("Host", h) for h in hosts]
    if auth is not None:
        headers.append(("Authorization", f"Bearer {auth}"))
    return ProxyRequest(method, target, tuple(headers), body)


def _token(sandbox: str, route: str) -> str:
    return InMemoryTokenSource.derive(SECRET, sandbox, route, 0)


async def _refusal(proxy: EgressProxy, request: ProxyRequest, binding: EgressBinding = A) -> R:
    with pytest.raises(EgressRefused) as caught:
        _ = await proxy.authorize(request, local=binding.proxy_address, peer=binding.sandbox_address)
    return caught.value.reason


async def _issue_tokens(proxy: EgressProxy, binding: EgressBinding) -> None:
    model, tools = (r.placeholder for r in binding.policy.token_routes)
    for request in (_request(auth=model), _request("/mcp", host=TOOLS, auth=tools)):
        _ = await proxy.authorize(request, local=binding.proxy_address, peer=binding.sandbox_address)


ATTEMPTS = [
    # H-1: by DNS name, absolute-form (Node, a public host, the gateway's port on loopback)
    ("H-1", _request("http://harness-state:3000/api/v1/agent-harness/gateway-tokens", auth=None), R.DESTINATION_NOT_ALLOWED),
    ("H-1", _request("http://example.com/", auth=None), R.DESTINATION_NOT_ALLOWED),
    ("H-1", _request("http://localhost:8097/v1/chat/completions", auth=None), R.DESTINATION_NOT_ALLOWED),
    # H-2: by IP literal: a gateway's own port on another address, Node's port
    ("H-2", _request("http://172.18.0.5:8097/v1/chat/completions", auth=None), R.DESTINATION_NOT_ALLOWED),
    ("H-2", _request("http://172.18.0.7:3000/", auth=None), R.DESTINATION_NOT_ALLOWED),
    # H-3: the cloud metadata address, not on the allowlist
    ("H-3", _request("http://169.254.169.254/latest/meta-data/", auth=None, method="GET"), R.DESTINATION_NOT_ALLOWED),
    # H-4: forged Host: origin-form to Node; absolute-form wins over a gateway Host; two Host headers
    ("H-4", _request("/", host="harness-state:3000", auth=None, method="GET"), R.DESTINATION_NOT_ALLOWED),
    ("H-4", _request("http://172.18.0.9:9999/exfiltrate", host=TOOLS, auth=A_TOOLS), R.DESTINATION_NOT_ALLOWED),
    ("H-4", _request("/v1/chat/completions", host=(MODEL, "harness-state:3000")), R.MALFORMED_REQUEST),
    # H-5: a tunnel to a gateway and to a public host; https absolute-form
    ("H-5", ProxyRequest("CONNECT", TOOLS, (("Host", TOOLS),)), R.TUNNEL_REFUSED),
    ("H-5", ProxyRequest("CONNECT", "example.com:443", (("Host", "example.com:443"),)), R.TUNNEL_REFUSED),
    ("H-5", _request("https://tool-gateway:8097/mcp", host=TOOLS, auth=A_TOOLS), R.SCHEME_NOT_ALLOWED),
    # C-2: a vendor key in a tool argument to the tool gateway (in a chat message to the model gateway it is hidden,
    # WP-103: ``test_g2_c2_a_vendor_key_in_a_chat_message_never_reaches_the_model_gateway``)
    (
        "C-2",
        _request(
            "/mcp",
            host=TOOLS,
            auth=A_TOOLS,
            body=f'{{"method": "tools/call", "params": {{"arguments": {{"note": "{VENDOR_KEY}"}}}}}}'.encode(),
        ),
        R.FORBIDDEN_PATTERN,
    ),
    # C-6: each placeholder to the other gateway, another sandbox's placeholder, none at all
    ("C-6", _request("/mcp", host=TOOLS, auth=A_MODEL), R.PLACEHOLDER_WRONG_DESTINATION),
    ("C-6", _request(auth=A_TOOLS), R.PLACEHOLDER_WRONG_DESTINATION),
    ("C-6", _request("/mcp", host=TOOLS, auth=B_TOOLS), R.PLACEHOLDER_FOREIGN),
    ("C-6", _request("/mcp", host=TOOLS, auth=None), R.PLACEHOLDER_MISSING),
]


@pytest.mark.parametrize(("row", "request_", "reason"), ATTEMPTS, ids=[f"{a[0]}-{i}" for i, a in enumerate(ATTEMPTS)])
async def test_g2_each_egress_attempt_is_refused_with_its_code_and_logged_once(
    row: str, request_: ProxyRequest, reason: R, caplog: pytest.LogCaptureFixture
) -> None:
    proxy = await _proxy()
    with caplog.at_level(logging.WARNING, logger="harness_core.sandbox.egress"):
        assert await _refusal(proxy, request_) == reason, row
    refused = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.refused"]
    assert [r.harness["reason"] for r in refused] == [reason.value]  # type: ignore[attr-defined]
    assert VENDOR_KEY not in caplog.text


async def test_g2_c2_a_vendor_key_in_a_chat_message_never_reaches_the_model_gateway() -> None:
    """WP-103: the model call goes on with the key hidden; the key itself still never leaves the proxy."""
    proxy = await _proxy()
    body = f'{{"messages": [{{"role": "user", "content": "{VENDOR_KEY}"}}]}}'.encode()
    forward = await proxy.authorize(_request(body=body), local=A.proxy_address, peer=A.sandbox_address)
    assert VENDOR_KEY.encode() not in forward.body and forward.url == "http://e3:8097/v1/chat/completions"


async def test_g2_c3_another_sandboxs_live_token_is_replayed_in_authorization_and_in_a_tool_call() -> None:
    proxy = await _proxy()
    await _issue_tokens(proxy, B)
    stolen = _token("sbx-b", "gateway:tools")
    assert await _refusal(proxy, _request(auth=stolen)) == R.TOKEN_REPLAYED
    body = f'{{"method": "tools/call", "params": {{"arguments": {{"t": "{stolen}"}}}}}}'.encode()
    assert await _refusal(proxy, _request("/mcp", host=TOOLS, auth=A_TOOLS, body=body)) == R.TOKEN_REPLAYED


async def test_g2_c4_a_sandboxs_own_live_token_is_replayed() -> None:
    proxy = await _proxy()
    await _issue_tokens(proxy, A)
    own = _token("sbx-a", "gateway:model")
    assert own in proxy.live_tokens()
    assert await _refusal(proxy, _request(auth=own)) == R.TOKEN_REPLAYED
    assert await _refusal(proxy, _request("/mcp", host=TOOLS, auth=_token("sbx-a", "gateway:tools"))) == R.TOKEN_REPLAYED


async def test_g2_c5_a_released_sandboxs_token_is_caller_authorization() -> None:
    proxy = await _proxy()
    await _issue_tokens(proxy, B)
    stale = _token("sbx-b", "gateway:model")
    await proxy.bindings.unregister("sbx-b")
    assert stale not in proxy.live_tokens()
    assert await _refusal(proxy, _request(auth=stale)) == R.CALLER_AUTHORIZATION
    assert await _refusal(proxy, _request("/mcp", host=TOOLS, auth=stale)) == R.CALLER_AUTHORIZATION


async def test_g2_positive_control_each_placeholder_reaches_its_own_gateway() -> None:
    proxy = await _proxy()
    model = await proxy.authorize(_request(), local=A.proxy_address, peer=A.sandbox_address)
    tools = await proxy.authorize(_request("/mcp", host=TOOLS, auth=A_TOOLS), local=A.proxy_address, peer=A.sandbox_address)
    assert model.url == "http://e3:8097/v1/chat/completions" and tools.url == "http://e2:8097/mcp"
    assert ("Authorization", f"Bearer {_token('sbx-a', 'gateway:model')}") in model.headers
