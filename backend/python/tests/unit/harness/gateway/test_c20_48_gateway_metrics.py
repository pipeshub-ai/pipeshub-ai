"""C20.48 (OB-08, OB-09): the Model Gateway's and Tool Gateway's request metrics, and the Tool Gateway's health route.

OB-08: every request either gateway answers is one observation by route name and outcome, with its latency; a refusal
is counted by its reason code and a tool call by its effect class and disposition. No label carries what a caller
sent (a path, a tool name, an org or run id), and every label space is fixed. OB-09: the Tool Gateway's listener
answers ``GET /healthz`` without a token and with nothing but that it is up, as the Model Gateway's does.
"""

from __future__ import annotations

import contextlib
import re
from typing import TYPE_CHECKING, get_args

import httpx
import pytest
from aiohttp.test_utils import TestServer

from app.harness.engines.worker import GATEWAY_MOUNT, tool_gateway_api
from app.harness.gateway.mcp import ToolGatewayApp
from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.harness_gateway_metrics import (
    BackendGatewayMetrics,
    BackendReadCacheMetrics,
)
from harness_core.gateway import GatewayTokenVerifier, model_server
from harness_core.gateway.model_server import create_model_gateway_app
from harness_core.gateway.types import CallDisposition
from harness_core.telemetry.metrics import (
    DISPOSITIONS,
    MAX_REASONS,
    MODEL_ROUTES,
    NOOP_GATEWAY_METRICS,
    NOOP_READ_CACHE_METRICS,
    GatewayRequest,
    NoopGatewayMetrics,
    NoopReadCacheMetrics,
    RecordingGatewayMetrics,
    RecordingReadCacheMetrics,
    ToolCallObserved,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.gateway_metrics import (
    GatewayMetricsContract,
    ReadCacheMetricsContract,
)
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    build_endpoint,
    claims_for,
)
from harness_core.testing.gateway import TokenMint
from harness_core.testing.model_gateway import GatewayRig, RecordedTransport
from tests.support.loopback_client import loopback_client
from tests.unit.harness.gateway.support import GatewayServer, served
from tests.unit.harness_core.gateway.test_chat_wire import OPENCODE_BODY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

_LABEL = re.compile(r'="([^"]*)"')


def _seen(metrics: RecordingGatewayMetrics) -> list[tuple[str, str, str | None]]:
    return [(r.route, r.outcome, r.reason) for r in metrics.requests]


async def _model_gateway(metrics: RecordingGatewayMetrics) -> tuple[TestServer, str, str]:
    rig = GatewayRig()
    rig.transport.events = RecordedTransport().events
    binding = rig.pin()
    server = TestServer(create_model_gateway_app(rig.gateway, metrics), host="127.0.0.1")
    await server.start_server()
    return server, str(server.make_url("")).rstrip("/"), rig.token(binding)


async def test_ob08_the_model_gateway_observes_each_request_by_route_and_outcome() -> None:
    metrics = RecordingGatewayMetrics()
    server, url, token = await _model_gateway(metrics)
    try:
        async with loopback_client() as http:
            assert (await http.get(f"{url}/healthz")).status_code == 200
            # A streamed answer is observed once, at its end.
            async with http.stream(
                "POST", f"{url}/v1/chat/completions", json=OPENCODE_BODY, headers={"Authorization": f"Bearer {token}"}
            ) as streamed:
                assert streamed.status_code == 200
                _ = [chunk async for chunk in streamed.aiter_bytes()]
            refused = await http.post(f"{url}/v1/messages", json={})
            assert refused.status_code == 401
            # A caller may put anything in a path: an org id here is never a label.
            unserved = await http.get(f"{url}/v1/org-1/run-1/secrets")
            assert unserved.status_code == 404
    finally:
        await server.close()
    assert _seen(metrics) == [
        ("healthz", "ok", None),
        ("chat_completions", "ok", None),
        ("messages", "refused", "token_missing"),
        ("unmatched", "refused", "route_not_allowed"),
    ]
    assert all(r.gateway == "model" and r.seconds >= 0 for r in metrics.requests)


async def test_ob08_the_tool_gateway_observes_requests_and_tool_calls_by_family() -> None:
    metrics = RecordingGatewayMetrics()
    world = build_endpoint(metrics=metrics)
    async with GatewayServer(metrics=metrics).running() as server, served(server, world.endpoint) as client:
        _ = await client.call_tool(claims_for(BINDING), "search", {"q": "q3"})
        _ = await client.call_tool(claims_for(BINDING), "create", {"q": "acme"})
        async with loopback_client() as http:
            no_token = await http.post(
                server.url(client.key), json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert no_token.status_code == 401
            not_a_binding = await http.post(server.url("org-1-run-1"), json={})
            assert not_a_binding.status_code == 404
            token = server.mint.mint(BINDING)
            get = await http.get(server.url(client.key), headers={"Authorization": f"Bearer {token}"})
            assert get.status_code == 405
    seen = _seen(metrics)
    assert {("mcp", "ok", None)} == {s for s in seen if s[1] == "ok"}
    assert ("mcp", "refused", "token_missing") in seen
    assert ("unmatched", "not_found", None) in seen
    assert ("mcp", "refused", "method_not_allowed") in seen
    assert {r.gateway for r in metrics.requests} == {"tool"}
    assert [(c.family, c.disposition) for c in metrics.calls] == [("read", "executed"), ("write", "executed")]


def test_ob08_every_label_stays_in_its_fixed_space() -> None:
    metrics = RecordingGatewayMetrics()
    metrics.request(GatewayRequest("model", "/v1/org-1/anything", "refused", -1.0, reason="Not A Code: org-1"))
    for n in range(MAX_REASONS + 5):
        metrics.request(GatewayRequest("tool", "mcp", "refused", 0.1, reason=f"reason_{n}"))
    metrics.request(GatewayRequest("tool", "mcp", "ok", 0.1, reason="token_missing"))
    metrics.tool_call(ToolCallObserved("slack.post_message", "exploded", 0.2))
    first = metrics.requests[0]
    assert (first.route, first.reason, first.seconds) == ("unmatched", "other", 0.0)
    reasons = {r.reason for r in metrics.requests[1:-1]}
    assert len(reasons - {"other"}) == MAX_REASONS and "other" in reasons
    assert metrics.requests[-1].reason is None, "only a refusal carries a reason"
    assert (metrics.calls[0].family, metrics.calls[0].disposition) == ("other", "other")
    assert set(get_args(CallDisposition)) == DISPOSITIONS


def test_ob08_the_backend_adapter_exports_only_bounded_labels() -> None:
    adapter = BackendGatewayMetrics()
    adapter.request(GatewayRequest("model", "chat_completions", "refused", 0.3, reason="budget_exhausted"))
    adapter.request(GatewayRequest("model", "/v1/org-77/x", "error", 1.5))
    adapter.tool_call(ToolCallObserved("write", "deferred", 2.0))
    text = METRICS_BACKEND.serialize()
    lines = [line for line in text.splitlines() if line.startswith("pipeshub_harness_")]
    assert any(
        line.startswith("pipeshub_harness_gateway_refusals_total{")
        and 'reason="budget_exhausted"' in line and 'route="chat_completions"' in line
        for line in lines
    )
    assert any('route="unmatched"' in line and 'outcome="error"' in line for line in lines)
    assert any(
        line.startswith("pipeshub_harness_tool_gateway_calls_total{") and 'family="write"' in line for line in lines
    )
    assert any(line.startswith("pipeshub_harness_gateway_request_duration_seconds_bucket{") for line in lines)
    assert not any("org-77" in line for line in lines)


def test_wp91_fu1_the_read_cache_counter_is_exported_beside_the_gateways() -> None:
    from harness_core.telemetry.metrics import ReadCacheObserved

    adapter = BackendReadCacheMetrics()
    adapter.lookup(ReadCacheObserved("loop", "hit"))
    adapter.lookup(ReadCacheObserved("engine", "expired"))
    adapter.lookup(ReadCacheObserved("org-77", "run-9"))  # pyright: ignore[reportArgumentType]
    lines = [
        line for line in METRICS_BACKEND.serialize().splitlines()
        if line.startswith("pipeshub_harness_tool_read_cache_lookups_total{")
    ]
    assert any('executor="loop"' in line and 'outcome="hit"' in line for line in lines), lines
    assert any('executor="engine"' in line and 'outcome="expired"' in line for line in lines), lines
    assert not any("org-77" in line or "run-9" in line for line in lines)


@contextlib.asynccontextmanager
async def _tool_api() -> AsyncIterator[httpx.AsyncClient]:
    clock = ManualClock()
    api = tool_gateway_api(ToolGatewayApp(GatewayTokenVerifier(TokenMint(clock).source, clock)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://worker") as http:
        yield http


def test_ob08_every_model_gateway_route_has_a_metric_name() -> None:
    app = create_model_gateway_app(GatewayRig().gateway)
    paths = {resource.canonical for resource in app.router.resources()}
    assert paths == set(model_server._ROUTE_NAMES)
    assert set(model_server._ROUTE_NAMES.values()) == set(MODEL_ROUTES)


async def test_ob09_the_tool_gateway_listener_answers_liveness_without_a_token_or_data() -> None:
    async with _tool_api() as http:
        health = await http.get("/healthz")
        assert (health.status_code, health.json()) == (200, {"ok": True})
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await http.get(path)).status_code == 404, path
        unknown = await http.post(f"{GATEWAY_MOUNT}/no-such-key/mcp", json={})
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "GATEWAY_NOT_FOUND"


class TestRecordingGatewayMetrics(GatewayMetricsContract):
    @pytest.fixture
    def metrics(self) -> RecordingGatewayMetrics:
        return RecordingGatewayMetrics()

    @pytest.fixture
    def labels(self, metrics: RecordingGatewayMetrics) -> Callable[[], set[str]]:
        def seen() -> set[str]:
            values = {v for r in metrics.requests for v in (r.gateway, r.route, r.outcome, r.reason) if v is not None}
            return values | {v for c in metrics.calls for v in (c.family, c.disposition)}

        return seen


class TestBackendGatewayMetrics(GatewayMetricsContract):
    """The production adapter, read back from the process's exposition text."""

    @pytest.fixture
    def metrics(self) -> BackendGatewayMetrics:
        return BackendGatewayMetrics()

    @pytest.fixture
    def labels(self) -> Callable[[], set[str]]:
        def seen() -> set[str]:
            lines = (line for line in METRICS_BACKEND.serialize().splitlines() if line.startswith("pipeshub_harness_"))
            return {value for line in lines for value in _LABEL.findall(line) if not value.replace(".", "").isdigit()}

        return seen


class TestNoopGatewayMetrics(GatewayMetricsContract):
    """The default where nothing is bound: accepts everything, exports nothing."""

    @pytest.fixture
    def metrics(self) -> NoopGatewayMetrics:
        return NOOP_GATEWAY_METRICS

    @pytest.fixture
    def labels(self) -> Callable[[], set[str]]:
        return set


class TestRecordingReadCacheMetrics(ReadCacheMetricsContract):
    @pytest.fixture
    def read_metrics(self) -> RecordingReadCacheMetrics:
        return RecordingReadCacheMetrics()

    @pytest.fixture
    def labels(self, read_metrics: RecordingReadCacheMetrics) -> Callable[[], set[str]]:
        return lambda: {v for o in read_metrics.lookups for v in (o.executor, o.outcome)}


class TestBackendReadCacheMetrics(ReadCacheMetricsContract):
    @pytest.fixture
    def read_metrics(self) -> BackendReadCacheMetrics:
        return BackendReadCacheMetrics()

    @pytest.fixture
    def labels(self) -> Callable[[], set[str]]:
        def seen() -> set[str]:
            lines = (line for line in METRICS_BACKEND.serialize().splitlines() if line.startswith("pipeshub_harness_"))
            return {value for line in lines for value in _LABEL.findall(line) if not value.replace(".", "").isdigit()}

        return seen


class TestNoopReadCacheMetrics(ReadCacheMetricsContract):
    @pytest.fixture
    def read_metrics(self) -> NoopReadCacheMetrics:
        return NOOP_READ_CACHE_METRICS

    @pytest.fixture
    def labels(self) -> Callable[[], set[str]]:
        return set
