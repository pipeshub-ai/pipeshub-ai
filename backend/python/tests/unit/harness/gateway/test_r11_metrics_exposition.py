"""Round 11 item B (C9.04, C20.48): the internal-only services serve their metrics to the worker credential.

The egress proxy, the Model Gateway and the Tool Gateway listen on no published port, and their metrics otherwise
leave only through the opt-in telemetry push. Each now serves ``GET /metrics`` (its process's registry, Prometheus
text), which Node's one control-plane route (``GET /api/v1/agent-harness/internal/metrics``) scrapes and merges.

R11-MX-1 the Model Gateway serves its metrics to the worker credential only: none, a wrong one and a run's gateway
         token (all a sandbox ever carries through the egress proxy) are refused 401; the scrape is a measured route.
R11-MX-2 the Tool Gateway listener does the same, beside its token-free ``/healthz``.
R11-MX-3 the egress proxy's control plane serves them under the credential its other routes take, and refuses a
         request from a sandbox's address as it refuses every control request.
R11-MX-4 a listener built without an exposition serves no ``/metrics`` at all.
R11-MX-5 PipesHub's adapter reads the worker credential Node provisions (current, then previous in its grace) and
         the process's own registry.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import aiohttp
import httpx
from aiohttp.test_utils import TestServer

from app.harness.credential_roles import ROLE_DOCUMENTS
from app.harness.engines.worker import tool_gateway_api
from app.harness.gateway.mcp import ToolGatewayApp
from app.harness.metrics_exposition import worker_metrics_exposition
from harness_core.gateway import GatewayTokenVerifier
from harness_core.gateway.model_server import create_model_gateway_app
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.types import EgressPolicy
from harness_core.telemetry.exposition import (
    METRICS_PATH,
    PROMETHEUS_TEXT,
    MetricsExposition,
)
from harness_core.telemetry.metrics import MODEL_ROUTES, RecordingGatewayMetrics
from harness_core.testing.clock import ManualClock
from harness_core.testing.gateway import TokenMint
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import GatewayRig
from tests.support.loopback_client import loopback_client

if TYPE_CHECKING:
    from collections.abc import Mapping

CREDENTIAL = "worker-credential-r11"
TEXT = '# HELP pxh_r11_total A test counter\n# TYPE pxh_r11_total counter\npxh_r11_total{outcome="ok"} 3.0\n'


def _exposition() -> MetricsExposition:
    return MetricsExposition(StaticBearerAuth(CREDENTIAL), lambda: TEXT)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_r11_mx1_the_model_gateway_serves_metrics_to_the_worker_credential_only() -> None:
    rig = GatewayRig()
    run_token = rig.token(rig.pin())
    metrics = RecordingGatewayMetrics()
    server = TestServer(create_model_gateway_app(rig.gateway, metrics, exposition=_exposition()), host="127.0.0.1")
    await server.start_server()
    url = str(server.make_url(METRICS_PATH))
    try:
        async with loopback_client() as http:
            for headers in ({}, _bearer("wrong"), _bearer(run_token)):
                refused = await http.get(url, headers=headers)
                assert refused.status_code == 401, headers
                assert "pxh_r11_total" not in refused.text
            served = await http.get(url, headers=_bearer(CREDENTIAL))
    finally:
        await server.close()
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/plain")
    assert served.text == TEXT
    assert "metrics" in MODEL_ROUTES
    assert [(r.route, r.outcome) for r in metrics.requests] == [("metrics", "refused")] * 3 + [("metrics", "ok")]


async def test_r11_mx2_the_tool_gateway_listener_serves_metrics_to_the_worker_credential_only() -> None:
    clock = ManualClock()
    mint = TokenMint(clock)
    api = tool_gateway_api(ToolGatewayApp(GatewayTokenVerifier(mint.source, clock)), exposition=_exposition())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://worker") as http:
        assert (await http.get("/healthz")).status_code == 200
        for headers in ({}, _bearer("wrong")):
            refused = await http.get(METRICS_PATH, headers=headers)
            assert refused.status_code == 401
            assert "pxh_r11_total" not in refused.text
        served = await http.get(METRICS_PATH, headers=_bearer(CREDENTIAL))
    assert served.status_code == 200
    assert served.headers["content-type"] == PROMETHEUS_TEXT
    assert served.text == TEXT


async def test_r11_mx3_the_egress_control_plane_serves_metrics_under_its_credential() -> None:
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=InMemoryTokenSource(b"r11-secret", clock),
        resolver=StaticResolver(),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(max_body_bytes=1024),
    )
    server = EgressServer(proxy, StaticBearerAuth(CREDENTIAL), instance="egress-r11", metrics=lambda: TEXT)
    _, control_port = await server.start(host="127.0.0.1", data_port=0, control_port=0)
    url = f"http://127.0.0.1:{control_port}{METRICS_PATH}"
    try:
        async with aiohttp.ClientSession() as session:
            for headers in ({}, _bearer("wrong")):
                async with session.get(url, headers=headers) as refused:
                    assert refused.status == 401
            async with session.get(url, headers=_bearer(CREDENTIAL)) as served:
                assert served.status == 200
                assert served.headers["Content-Type"].startswith("text/plain")
                assert await served.text() == TEXT
            # A sandbox's address never reaches the control plane, metrics included.
            await proxy.bindings.register(
                EgressBinding("sbx-r11", "127.0.0.1", "127.0.0.1", EgressPolicy(destinations=(), token_routes=()))
            )
            async with session.get(url, headers=_bearer(CREDENTIAL)) as from_sandbox:
                assert from_sandbox.status == 403
    finally:
        await server.stop()


async def test_r11_mx4_a_listener_without_an_exposition_serves_no_metrics() -> None:
    rig = GatewayRig()
    server = TestServer(create_model_gateway_app(rig.gateway), host="127.0.0.1")
    await server.start_server()
    try:
        async with loopback_client() as http:
            assert (await http.get(str(server.make_url(METRICS_PATH)), headers=_bearer(CREDENTIAL))).status_code == 404
    finally:
        await server.close()
    clock = ManualClock()
    api = tool_gateway_api(ToolGatewayApp(GatewayTokenVerifier(TokenMint(clock).source, clock)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://worker") as http:
        assert (await http.get(METRICS_PATH, headers=_bearer(CREDENTIAL))).status_code == 404


class _Config:
    """The role documents Node provisions, by key (ADR-CRED-01)."""

    def __init__(self, documents: Mapping[str, Mapping[str, object]]) -> None:
        self.documents = documents

    async def get_config(self, key: str, **_: object) -> object:
        return self.documents.get(key)


async def test_r11_mx5_the_adapter_takes_the_provisioned_scrape_credential_and_the_process_registry() -> None:
    later = int(time.time() * 1000) + 60_000
    exposition = worker_metrics_exposition(
        _Config({  # type: ignore[arg-type]
            ROLE_DOCUMENTS["scrape"]: {"current": "cur-cred", "previous": "old-cred", "previousValidUntil": later},
            ROLE_DOCUMENTS["worker"]: {"current": "worker-cred"},
            ROLE_DOCUMENTS["egress-proxy"]: {"current": "proxy-cred"},
        })
    )
    assert await exposition.verifier.verify(_bearer("cur-cred"), path=METRICS_PATH)
    assert await exposition.verifier.verify(_bearer("old-cred"), path=METRICS_PATH)
    # SEC-C-10: only the scraper reads metrics, and the scrape credential opens nothing else.
    assert not await exposition.verifier.verify(_bearer("worker-cred"), path=METRICS_PATH)
    assert not await exposition.verifier.verify(_bearer("proxy-cred"), path=METRICS_PATH)
    assert not await exposition.verifier.verify(_bearer("cur-cred"), path="/sandboxes")
    assert not await exposition.verifier.verify(_bearer("other"), path=METRICS_PATH)
    assert not await exposition.verifier.verify({}, path=METRICS_PATH)
    assert isinstance(exposition.text(), str)
