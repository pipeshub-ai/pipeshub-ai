"""SEC-EG-14 (C6.12, WP-45): the egress proxy's volume as metrics, bounded labels only (an alias, a scope, a
direction, an alert kind), driven through the proxy's own flush. WP-61: ``scope`` tells the gateways' traffic
(``gateway``, metrics only) from the traffic the cap, the alerts and the baseline judge (``outside``)."""

from __future__ import annotations

import re

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.egress_metrics import PrometheusVolumeMetrics
from harness_core.sandbox.egress import EgressBinding, InMemoryEgressBindings
from harness_core.sandbox.egress.volume import MIB, EgressVolume, VolumeLimits
from harness_core.testing.clock import ManualClock

_ALLOWED_LABELS = {"alias", "scope", "direction", "kind", "le"}


def _series() -> list[str]:
    return [line for line in METRICS_BACKEND.serialize().splitlines() if line.startswith("pipeshub_egress_")]


async def test_sec_eg_14_the_flush_exports_bytes_alerts_and_ended_runs_without_ids() -> None:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    volume = EgressVolume(
        VolumeLimits(alert_up_bytes=1 * MIB, ended_after_s=0), clock=clock, bindings=bindings, metrics=PrometheusVolumeMetrics()
    )
    binding = EgressBinding("sbx-m", "10.0.0.2", "10.0.0.3", labels={"orgId": "org-secret-1", "runId": "run-secret-1"})
    meter = volume.meter(binding, "tunnel")
    assert meter.sent(2 * MIB) and meter.received(3)
    _ = await volume.flush()
    text = "\n".join(_series())
    assert 'pipeshub_egress_bytes_total{alias="tunnel",direction="up",scope="outside"}' in text
    assert 'pipeshub_egress_bytes_total{alias="tunnel",direction="down",scope="outside"}' in text
    assert 'pipeshub_egress_volume_alerts_total{direction="up",kind="run_threshold"}' in text
    assert 'pipeshub_egress_run_bytes_count{direction="up",scope="outside"}' in text
    assert "secret" not in text
    for line in _series():
        for name in re.findall(r'(\w+)="', line):
            assert name in _ALLOWED_LABELS, line


async def test_r11d_egs_6_gateway_traffic_is_its_own_scope_in_the_metrics() -> None:
    clock = ManualClock()
    volume = EgressVolume(VolumeLimits(ended_after_s=0), clock=clock, metrics=PrometheusVolumeMetrics())
    binding = EgressBinding("sbx-g", "10.0.0.4", "10.0.0.5", labels={"orgId": "o", "runId": "r-gateway-scope"})
    assert volume.meter(binding, "model-gateway").received(5 * MIB)
    assert volume.meter(binding, "tool-gateway").sent(7)
    _ = await volume.flush()
    text = "\n".join(_series())
    assert 'pipeshub_egress_bytes_total{alias="model-gateway",direction="down",scope="gateway"}' in text
    assert 'pipeshub_egress_bytes_total{alias="tool-gateway",direction="up",scope="gateway"}' in text
    assert 'pipeshub_egress_run_bytes_count{direction="down",scope="gateway"}' in text
    assert not re.search(r'alias="(model|tool)-gateway",direction="\w+",scope="outside"', text)
