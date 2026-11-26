"""C6.17 / OP-08 (WP-91): rotating a model key or a git credential needs no sandbox change.

A sandbox holds placeholders only (OWD-1): the Model Gateway resolves the org's key at each call and the egress proxy
opens a git credential at each request, so a rotation is picked up at the next call while the sandbox, its engine
process and the placeholder it sends stay as they were.

- WP91-ROT-1: the real Model Gateway (as ``app.model_gateway_main`` wires it, ``ConfiguredModelAccessResolver``): two
  calls with one run's token, the org's key rotated in ``aiModels`` between them; the provider gets the old key, then
  the new one, and never the old one again. Nothing the sandbox sends changes.
- WP91-ROT-2: a key removed between two calls refuses the next one (``route_not_configured``) rather than serving
  the old key it last built a transport with.
- WP91-ROT-5: the proxy's authority rotated between two commands of one sandbox is in the trust bundle of the next.
- WP91-ROT-3: the egress proxy, one sandbox binding: a git token rotated by re-binding the same sandbox (as the
  controller's phase switch re-binds it, ``GitCheckout``) reaches the host at the next request with the same
  placeholder; the old token is never sent again.

The real-engine case (an engine process mid-turn, the key rotated between its two model calls) is the Docker lane's
``tests/unit/harness/engines/test_wp91_real_key_rotation.py``.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from harness_core.access.types import ENGINE_MODEL_ID, PinnedRoute
from harness_core.engines.git_checkout import run_in
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.trust import TRUST_BUNDLE, trust_env, trusted_command
from harness_core.sandbox.egress.types import EGRESS_CA_PATH
from harness_core.sandbox.types import (
    CredentialInjection,
    EgressDestination,
    EgressPolicy,
    TokenRoute,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from tests.support.git_host import HostSandbox
from tests.support.loopback_client import loopback_client
from tests.unit.harness.access.support import start_stack

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx

OLD_KEY = "org-key-before-rotation-0123456789"
NEW_KEY = "org-key-after-rotation-9876543210"
CHAT = {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "ping"}]}
ROUTE = PinnedRoute("openaicompatible", "local-model", config_key="key-compat", config_model="local-model")


@pytest.fixture
async def provider() -> AsyncIterator[tuple[list[str], str]]:
    """An OpenAI-compatible provider that records the ``Authorization`` of every call."""
    seen: list[str] = []

    async def chat(request: web.Request) -> web.Response:
        seen.append(request.headers.get("Authorization", ""))
        body = await request.json()
        return web.json_response(
            {
                "id": "up",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    yield seen, str(server.make_url("/v1"))
    await server.close()


def _models(upstream: str, key: str) -> dict[str, Any]:
    return {
        "llm": [
            {
                "provider": "openAICompatible",
                "modelKey": "key-compat",
                "isDefault": True,
                "configuration": {"model": "local-model", "apiKey": key, "endpoint": upstream},
            }
        ]
    }


async def _call(url: str, token: str) -> httpx.Response:
    async with loopback_client() as client:
        return await client.post(f"{url}/v1/chat/completions", headers={"Authorization": f"Bearer {token}"}, json=CHAT)


async def test_wp91_rot_1_a_rotated_org_key_is_used_at_the_next_call_of_the_same_run(
    provider: tuple[list[str], str],
) -> None:
    seen, upstream = provider
    stack = await start_stack(_models(upstream, OLD_KEY), ROUTE)
    try:
        first = await _call(stack.url, stack.token)
        # The admin saves a new key: Node rewrites the entry, nothing of the run (its token, its sandbox) changes.
        stack.config.ai_models = _models(upstream, NEW_KEY)
        second = await _call(stack.url, stack.token)
        third = await _call(stack.url, stack.token)
    finally:
        await stack.close()
    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 200], second.text
    assert seen == [f"Bearer {OLD_KEY}", f"Bearer {NEW_KEY}", f"Bearer {NEW_KEY}"]
    # The run's grant was read once per call as before; no new token, no new grant was needed for the new key.
    assert stack.node.grants["run-1"][0] == stack.token


async def test_wp91_rot_2_a_removed_key_refuses_the_next_call(provider: tuple[list[str], str]) -> None:
    seen, upstream = provider
    stack = await start_stack(_models(upstream, OLD_KEY), ROUTE)
    try:
        assert (await _call(stack.url, stack.token)).status_code == 200
        stack.config.ai_models = {"llm": []}
        refused = await _call(stack.url, stack.token)
    finally:
        await stack.close()
    assert refused.status_code >= 400
    assert "route_not_configured" in refused.text
    assert seen == [f"Bearer {OLD_KEY}"]


GIT_HOST = "git.example.test"
GIT_PH = "pxh-git-0123456789abcdef0123456789"
SEALER = CredentialSealer(b"rotation-deployment-secret")
OLD_GIT = "x-access-token:git-token-before-0123456789"
NEW_GIT = "x-access-token:git-token-after-9876543210"


def _git_binding(credential: str) -> EgressBinding:
    policy = EgressPolicy(
        destinations=(EgressDestination(GIT_HOST, (80, 443)),),
        token_routes=(TokenRoute(GIT_PH, GIT_HOST, SEALER.seal(GIT_PH, credential), CredentialInjection.BASIC),),
    )
    return EgressBinding("sbx-git", "127.0.0.2", "127.0.0.1", policy, {"orgId": "o1"})


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


async def test_wp91_rot_3_a_rotated_git_token_reaches_the_host_with_the_same_placeholder() -> None:
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=RoutedTokenSource(InMemoryTokenSource(b"t", clock), SealedTokenSource(SEALER, clock)),
        resolver=StaticResolver({GIT_HOST: ["93.184.216.34"]}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
    )
    before = _git_binding(OLD_GIT)
    await proxy.bindings.register(before)
    # What the sandbox's git sends never changes: the placeholder as its Basic password.
    request = ProxyRequest("GET", f"http://{GIT_HOST}/acme/widgets.git/info/refs", (("Authorization", _basic("pxh", GIT_PH)),))
    sent = await proxy.authorize(request, local=before.proxy_address, peer=before.sandbox_address)
    after = _git_binding(NEW_GIT)
    # The token is rotated: the worker re-binds the same sandbox (same id and addresses) with the newly sealed one.
    await proxy.bindings.register(after)
    rotated = await proxy.authorize(request, local=after.proxy_address, peer=after.sandbox_address)
    assert dict(sent.headers)["Authorization"] == "Basic " + base64.b64encode(OLD_GIT.encode()).decode()
    assert dict(rotated.headers)["Authorization"] == "Basic " + base64.b64encode(NEW_GIT.encode()).decode()
    assert (before.sandbox_id, before.sandbox_address) == (after.sandbox_id, after.sandbox_address)


async def test_wp91_rot_5_a_rotated_proxy_authority_is_trusted_at_the_next_command(tmp_path: Path) -> None:
    """The proxy's CA: each trusted command (every git, site or setup step) and each turn's start reads the proxy's
    current authority into the trust bundle, so a rotated CA reaches the sandbox without a new sandbox."""
    served = {"ca": b"-----BEGIN CERTIFICATE-----\nOLD-AUTHORITY\n-----END CERTIFICATE-----\n"}

    async def authority(_: web.Request) -> web.Response:
        return web.Response(body=served["ca"], content_type="application/x-pem-file")

    app = web.Application()
    app.router.add_get(EGRESS_CA_PATH, authority)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    home = tmp_path / "home"
    home.mkdir()
    sandbox = HostSandbox(home, proxy_url=str(server.make_url("")).rstrip("/"))
    argv = trusted_command(["sh", "-c", f'cat "{TRUST_BUNDLE}"', "pxh-ca"])
    try:
        before = await run_in(sandbox, argv, trust_env())  # type: ignore[arg-type]
        served["ca"] = b"-----BEGIN CERTIFICATE-----\nNEW-AUTHORITY\n-----END CERTIFICATE-----\n"
        after = await run_in(sandbox, argv, trust_env())  # type: ignore[arg-type]
    finally:
        await server.close()
    assert before[0] == 0 and b"OLD-AUTHORITY" in before[1].encode()
    assert after[0] == 0 and b"NEW-AUTHORITY" in after[1].encode() and b"OLD-AUTHORITY" not in after[1].encode()
