"""PR-1: the egress proxy process role (worker credential on the control plane, upstreams from endpoints)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest
from aiohttp import web

from app.egress_proxy_main import create_service
from app.harness.sandbox.egress_config import control_endpoint, gateway_upstreams
from harness_core.ids import RandomIds
from harness_core.sandbox.egress import EgressBinding
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PH = "ph-main-0123456789abcdef"
NODE_TOKEN = "gw-token-from-node"


class FakeConfig:
    def __init__(self, documents: dict[str, Any]) -> None:
        self.documents = documents

    async def get_config(self, key: str, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        return self.documents.get(key)


def _config(upstream: str) -> FakeConfig:
    return FakeConfig(
        {
            "/services/agentHarness/workerCredential": {
                "current": "cred-now",
                "previous": "cred-before",
                "previousValidUntil": time.time() * 1000 + 60_000,
            },
            "/services/endpoints": {
                "modelGateway": {"endpoint": upstream},
                "egressProxy": {"controlEndpoint": "http://egress-proxy:8098"},
            },
        }
    )


@pytest.fixture
async def upstream() -> AsyncIterator[tuple[list[str], str]]:
    seen: list[str] = []

    async def handle(request: web.Request) -> web.Response:
        if request.path.endswith("/gateway-token"):
            # Node's gateway-token route (E2): the proxy presents the worker credential.
            assert request.headers.get("Authorization") == "Bearer cred-now"
            return web.json_response({"token": NODE_TOKEN, "expiresAt": int(time.time() * 1000) + 600_000})
        if request.path.endswith("/egress"):
            # Node's egress-decision route (OB-03): the proxy reports the destination it let through.
            return web.json_response({"outcome": "recorded"})
        seen.append(request.headers.get("Authorization", ""))
        return web.json_response({"ok": True})

    app = web.Application()
    _ = app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield seen, f"http://127.0.0.1:{runner.addresses[0][1]}"
    await runner.cleanup()


async def test_pr1_endpoints_come_from_the_endpoints_document() -> None:
    configured = _config("http://worker:8096/model")
    assert await gateway_upstreams(configured) == {"model-gateway": "http://worker:8096/model"}  # type: ignore[arg-type]
    assert await control_endpoint(configured) == "http://egress-proxy:8098"  # type: ignore[arg-type]
    assert await control_endpoint(FakeConfig({})) == "http://localhost:8098"  # type: ignore[arg-type]


async def test_pr1_the_process_role_guards_its_control_plane_and_forwards_to_the_gateway(
    upstream: tuple[list[str], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen, url = upstream
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", url)
    server = await create_service(_config(url), clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]
    data_port, control_port = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    base = f"http://127.0.0.1:{control_port}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base + "/healthz") as live:
                assert live.status == 200
            for credential, status in (("cred-now", 200), ("cred-before", 200), ("cred-stolen", 401), ("", 401)):
                async with session.get(base + "/v1/bindings", headers={"Authorization": f"Bearer {credential}"}) as r:
                    assert r.status == status, credential
            policy = EgressPolicy(
                destinations=(EgressDestination("model-gateway", (8097,)),),
                token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
            )
            labels = {"orgId": "o1", "runId": "run-1", "attemptId": "run-1.a1"}
            await server.proxy.bindings.register(EgressBinding("sbx-1", "127.0.0.2", "127.0.0.1", policy, labels, attestation="v1.broker-proof"))
        # The binding's proxy address is 127.0.0.2, so the control plane on 127.0.0.1 stays reachable here.
        async with aiohttp.ClientSession() as session, session.get(
            f"http://127.0.0.2:{data_port}/v1/models", headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"}
        ) as response:
            assert response.status == 200
        # E0: the token the gateway receives is the one Node issued for the sandbox's attempt.
        assert seen == [f"Bearer {NODE_TOKEN}"]
    finally:
        await server.stop()


@pytest.mark.parametrize("configured", [None, "0.0.0.0"])
async def test_ec4_the_process_role_refuses_to_start_without_a_confined_control_host(
    monkeypatch: pytest.MonkeyPatch, configured: str | None
) -> None:
    """E2E4: before anything else is built, a proxy with no ``EGRESS_CONTROL_HOST`` (or a wildcard) does not start."""
    from app import egress_proxy_main
    from harness_core.sandbox.egress.errors import ControlPlaneUnconfined

    def never(*_: Any, **__: Any) -> Any:  # noqa: ANN401
        raise AssertionError("the proxy built its services without a confined control plane")

    monkeypatch.setattr(egress_proxy_main, "validate_env_flags", never)
    if configured is None:
        monkeypatch.delenv("EGRESS_CONTROL_HOST", raising=False)
    else:
        monkeypatch.setenv("EGRESS_CONTROL_HOST", configured)
    with pytest.raises(ControlPlaneUnconfined):
        await egress_proxy_main.run_proxy()
