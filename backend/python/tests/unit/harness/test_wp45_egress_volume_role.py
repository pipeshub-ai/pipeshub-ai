"""SEC-EG-14 (C6.12, WP-45) in PipesHub's egress proxy role: the volume bounds reach the proxy from its environment
(Compose passes each one), a malformed one stops it, its counts go to the process's metrics and, with durable state,
into the proxy's sealed store."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.egress_proxy_main import create_service
from app.telemetry.modules.egress_metrics import PrometheusVolumeMetrics
from harness_core.ids import RandomIds
from harness_core.sandbox.egress import EgressBinding
from harness_core.sandbox.egress.state import InMemoryEgressStateStore, StateSealer
from harness_core.sandbox.egress.volume import MIB, VOLUME_PREFIX, VolumeLimitsInvalid
from harness_core.testing.clock import ManualClock

SETTINGS = (
    "EGRESS_VOLUME_ALERT_UP_MB",
    "EGRESS_VOLUME_ALERT_DOWN_MB",
    "EGRESS_VOLUME_BASELINE_FACTOR",
    "EGRESS_VOLUME_BASELINE_MIN_RUNS",
    "EGRESS_VOLUME_BASELINE_FLOOR_MB",
    "EGRESS_RUN_MAX_MB",
)
COMPOSE = Path(__file__).resolve().parents[5] / "deployment/docker-compose/docker-compose.yml"


class FakeConfig:
    async def get_config(self, key: str, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        return {
            "/services/agentHarness/workerCredential": {
                "current": "cred-now",
                "previous": "cred-before",
                "previousValidUntil": time.time() * 1000 + 60_000,
            },
            "/services/endpoints": {"modelGateway": {"endpoint": "http://pipeshub-ai:8096/model"}},
        }.get(key)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", "http://harness-state:3002")


async def test_sec_eg_14_the_role_reads_the_bounds_and_exports_to_the_process_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EGRESS_RUN_MAX_MB", "512")
    monkeypatch.setenv("EGRESS_VOLUME_ALERT_UP_MB", "64")
    server = await create_service(FakeConfig(), clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]
    volume = server.proxy.volume
    assert volume.limits.max_run_bytes == 512 * MIB and volume.limits.alert_up_bytes == 64 * MIB
    assert isinstance(volume._metrics, PrometheusVolumeMetrics)  # pyright: ignore[reportPrivateUsage]


async def test_sec_eg_14_the_role_refuses_to_start_on_a_malformed_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGRESS_RUN_MAX_MB", "lots")
    with pytest.raises(VolumeLimitsInvalid, match="EGRESS_RUN_MAX_MB"):
        _ = await create_service(FakeConfig(), clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]


async def test_sec_eg_14_with_durable_state_the_counts_are_kept_beside_the_bindings() -> None:
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    server = await create_service(
        FakeConfig(),  # type: ignore[arg-type]
        clock=clock,
        ids=RandomIds(),
        state=store,
        sealer=StateSealer(b"wp45-role"),
    )
    binding = EgressBinding("sbx-r", "10.0.0.2", "10.0.0.3", labels={"orgId": "o1", "runId": "r1"})
    assert server.proxy.volume.meter(binding, "model-gateway").sent(10)
    _ = await server.proxy.volume.flush()
    assert [key for key, _ in await store.entries() if key.startswith(VOLUME_PREFIX)]


def test_sec_eg_14_compose_hands_every_bound_to_the_proxy() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    environment = compose["services"]["egress-proxy"]["environment"]
    for name in SETTINGS:
        assert environment[name] == f"${{{name}:-}}", name
