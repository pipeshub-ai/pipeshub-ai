"""C9.07 / C9.04 (BU-04, OB-04): the sandbox broker's own metrics, scrapeable like the gateways'.

BU-04-M1 the broker's ``GET /metrics`` serves its registry to the worker credential only (none or a wrong one: 401).
BU-04-M2 a broker app built without a registry serves no ``/metrics``.
BU-04-M3 PipesHub's adapter counts sandbox-seconds by mode and phase, warm claims by outcome, and sets pool depth;
         no org, run or sandbox id is a label.
BM-1..BM-3 the ``BrokerMetrics`` contract on the recording, no-op and PipesHub adapters.
"""

from __future__ import annotations

import re

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.sandbox_broker_metrics import BackendBrokerMetrics
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.metering import (
    NOOP_BROKER_METRICS,
    MeteredInterval,
    RecordingBrokerMetrics,
)
from harness_core.sandbox.remote.server import create_broker_app
from harness_core.telemetry.exposition import METRICS_PATH, PROMETHEUS_TEXT
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.broker_metrics import BrokerMetricsContract
from harness_core.testing.sandbox import FakeSandboxBroker

TOKEN = "worker-credential-c907"
TEXT = '# HELP pxh_c907_total A test counter\n# TYPE pxh_c907_total counter\npxh_c907_total 1.0\n'


async def _get(server: TestServer, headers: dict[str, str]) -> tuple[int, str, str]:
    async with aiohttp.ClientSession() as session, session.get(server.make_url(METRICS_PATH), headers=headers) as r:
        return r.status, r.headers.get("Content-Type", ""), await r.text()


async def test_bu04_m1_the_broker_serves_its_metrics_to_the_worker_credential_only() -> None:
    app = create_broker_app(FakeSandboxBroker(clock=ManualClock()), StaticBearerAuth(TOKEN), metrics=lambda: TEXT)
    server = TestServer(app)
    await server.start_server()
    try:
        status, kind, body = await _get(server, {"Authorization": f"Bearer {TOKEN}"})
        assert (status, body) == (200, TEXT)
        assert kind.startswith(PROMETHEUS_TEXT.split(";")[0])
        for headers in ({}, {"Authorization": "Bearer someone-else"}):
            status, _, body = await _get(server, headers)
            assert status == 401 and "pxh_c907_total" not in body
    finally:
        await server.close()


async def test_bu04_m2_a_broker_without_a_registry_serves_no_metrics() -> None:
    server = TestServer(create_broker_app(FakeSandboxBroker(clock=ManualClock()), StaticBearerAuth(TOKEN)))
    await server.start_server()
    try:
        status, _, _ = await _get(server, {"Authorization": f"Bearer {TOKEN}"})
        assert status == 404
    finally:
        await server.close()


def _value(text: str, series: str) -> float:
    for line in text.splitlines():
        if line.startswith(series + " "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def test_bu04_m3_the_adapter_counts_by_mode_phase_and_outcome_only() -> None:
    metrics = BackendBrokerMetrics()
    active = 'pipeshub_harness_sandbox_seconds_total{mode="docker",phase="active"}'
    kept = 'pipeshub_harness_sandbox_seconds_total{mode="docker",phase="kept"}'
    capped = 'pipeshub_harness_sandbox_warm_claims_total{outcome="org_capped"}'
    odd = 'pipeshub_harness_sandbox_seconds_total{mode="other",phase="active"}'
    before = METRICS_BACKEND.serialize()
    labels = {"orgId": "org-secret", "runId": "run-secret"}
    metrics.metered(MeteredInterval("sbx-secret", "active", "docker", 12.5, labels))
    metrics.metered(MeteredInterval("sbx-secret", "kept", "docker", 3.0, labels))
    metrics.metered(MeteredInterval("sbx-secret", "active", "a-mode-nobody-ships", 1.0, labels))
    metrics.warm_claim("org_capped")
    metrics.pool_depth(3)
    after = METRICS_BACKEND.serialize()
    assert _value(after, active) - _value(before, active) == 12.5
    assert _value(after, kept) - _value(before, kept) == 3.0
    assert _value(after, odd) - _value(before, odd) == 1.0, "an unknown mode is counted as other"
    assert _value(after, capped) - _value(before, capped) == 1.0
    assert _value(after, "pipeshub_harness_sandbox_warm_pool_depth") == 3.0
    ours = "\n".join(line for line in after.splitlines() if line.startswith("pipeshub_harness_sandbox_"))
    assert "secret" not in ours and "a-mode-nobody-ships" not in ours


_LABEL = re.compile(r'="([^"]*)"')


class TestRecordingBrokerMetrics(BrokerMetricsContract):
    @pytest.fixture
    def metrics(self) -> RecordingBrokerMetrics:
        return RecordingBrokerMetrics()

    @pytest.fixture
    def labels(self, metrics: RecordingBrokerMetrics):  # noqa: ANN201 - a fixture returning a callable
        return metrics.labels


class TestNoopBrokerMetrics(BrokerMetricsContract):
    @pytest.fixture
    def metrics(self) -> object:
        return NOOP_BROKER_METRICS

    @pytest.fixture
    def labels(self):  # noqa: ANN201 - a fixture returning a callable
        return set


class TestBackendBrokerMetrics(BrokerMetricsContract):
    @pytest.fixture
    def metrics(self) -> BackendBrokerMetrics:
        return BackendBrokerMetrics()

    @pytest.fixture
    def labels(self):  # noqa: ANN201 - a fixture returning a callable
        def seen() -> set[str]:
            lines = (line for line in METRICS_BACKEND.serialize().splitlines() if line.startswith("pipeshub_harness_sandbox_"))
            return {value for line in lines for value in _LABEL.findall(line)}

        return seen
