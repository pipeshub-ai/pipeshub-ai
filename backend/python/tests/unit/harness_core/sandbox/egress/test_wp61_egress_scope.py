"""WP-61 (C6.12, SEC-EG-14): the egress cap and the org baseline count only egress to outside
destinations; PipesHub's own Model Gateway and Tool Gateway count for metrics only.

R11D-EGS-1 gateway bytes, up or down, never trip ``EGRESS_RUN_MAX_MB``: a gateway stream is never cut; outside bytes
           past the cap are.
R11D-EGS-2 once outside traffic caps the run, an outside request is refused ``volume_cap_exceeded`` while a Model
           Gateway request goes on, marked ``X-Egress-Capped: volume`` so the gateway ends the run with
           ``egress_volume_exceeded``; a Tool Gateway request goes on unmarked.
R11D-EGS-3 a sandbox cannot send the mark itself: its own ``X-Egress-Capped`` is dropped.
R11D-EGS-4 the run threshold alerts and the org baseline count outside bytes only: a run that moved mostly model
           traffic folds only its outside bytes into the baseline and raises no alert for its model traffic.
R11D-EGS-5 a run's outside and gateway totals are told apart, and a restored run's cap counts outside bytes only.
"""

from __future__ import annotations

import pytest

from harness_core.gateway.refusal_reports import EgressVolumeAlert
from harness_core.sandbox.egress import EgressBinding, EgressRefused, ProxyRequest
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.policy import EGRESS_CAPPED_HEADER
from harness_core.sandbox.egress.state import InMemoryEgressStateStore, StateSealer
from harness_core.sandbox.egress.volume import MIB, EgressVolume, VolumeLimits
from harness_core.testing.clock import ManualClock

from .test_wp45_egress_volume import API, A, B, _policy, gateway_call, make_rig

NO_ALERTS = {"alert_up_bytes": 0, "alert_down_bytes": 0, "baseline_factor": 0.0}


def _headers(forward_headers: tuple[tuple[str, str], ...], name: str) -> list[str]:
    return [value for key, value in forward_headers if key.lower() == name.lower()]


def _outside(binding: EgressBinding) -> ProxyRequest:
    return ProxyRequest("CONNECT", f"{API}:443", (("Host", f"{API}:443"),))


async def test_r11d_egs_1_gateway_bytes_never_trip_the_cap_and_outside_bytes_do() -> None:
    rig = make_rig(VolumeLimits(max_run_bytes=1000, **NO_ALERTS))
    await rig.proxy.bindings.register(A)
    model = rig.volume.meter(A, "model-gateway")
    tools = rig.volume.meter(A, "tool-gateway")
    assert model.sent(50 * MIB) and model.received(200 * MIB), "a model stream is never cut by the cap"
    assert tools.sent(5 * MIB) and tools.received(5 * MIB)
    moved = rig.volume.volume(org_id="o1", run_id="r1")
    assert moved is not None and not moved.capped and (moved.outside_up, moved.outside_down) == (0, 0)
    outside = rig.volume.meter(A, "tunnel")
    assert outside.sent(600)
    assert not outside.received(401), "outside bytes past the cap are cut"
    assert outside.stopped and not model.stopped
    assert model.received(10 * MIB), "the model stream in flight still goes on"
    _ = await rig.flush()
    [(run_id, alert)] = rig.sink.volume_alerts
    assert (run_id, alert) == ("r1", EgressVolumeAlert("run_cap", "total", 1001, 1000, "tunnel"))


async def test_r11d_egs_2_a_capped_run_is_refused_outside_and_its_model_calls_are_marked() -> None:
    rig = make_rig(VolumeLimits(max_run_bytes=1000, **NO_ALERTS), tunnels=True)
    await rig.proxy.bindings.register(A)
    await rig.proxy.bindings.register(B)
    before = await rig.proxy.authorize(gateway_call(A), local=A.proxy_address, peer=A.sandbox_address)
    assert _headers(before.headers, EGRESS_CAPPED_HEADER) == []
    assert not rig.volume.meter(A, "other").sent(1001)
    with pytest.raises(EgressRefused) as caught:
        _ = await rig.proxy.authorize_tunnel(_outside(A), local=A.proxy_address, peer=A.sandbox_address)
    assert caught.value.reason is R.VOLUME_CAP_EXCEEDED
    model = await rig.proxy.authorize(gateway_call(A), local=A.proxy_address, peer=A.sandbox_address)
    assert _headers(model.headers, EGRESS_CAPPED_HEADER) == ["volume"]
    assert model.meter is not None and model.meter.sent(4096) and not model.meter.stopped
    tools = await rig.proxy.authorize(gateway_call(A, "tool-gateway"), local=A.proxy_address, peer=A.sandbox_address)
    assert _headers(tools.headers, EGRESS_CAPPED_HEADER) == []
    other = await rig.proxy.authorize(gateway_call(B), local=B.proxy_address, peer=B.sandbox_address)
    assert _headers(other.headers, EGRESS_CAPPED_HEADER) == [], "another run is not capped"


async def test_r11d_egs_3_a_sandbox_cannot_send_the_mark_itself() -> None:
    rig = make_rig(VolumeLimits(**NO_ALERTS))
    await rig.proxy.bindings.register(A)
    call = gateway_call(A)
    forged = ProxyRequest(call.method, call.target, (*call.headers, (EGRESS_CAPPED_HEADER, "volume")), call.body)
    forward = await rig.proxy.authorize(forged, local=A.proxy_address, peer=A.sandbox_address)
    assert _headers(forward.headers, EGRESS_CAPPED_HEADER) == []


async def test_r11d_egs_4_alerts_and_the_baseline_count_outside_bytes_only() -> None:
    limits = VolumeLimits(
        alert_up_bytes=1 * MIB, alert_down_bytes=1 * MIB, baseline_factor=10, baseline_min_runs=1, baseline_floor_bytes=1
    )
    rig = make_rig(limits)
    old = EgressBinding("sbx-old", "127.0.6.1", "127.0.0.1", _policy("ph-model-dddddddddddddddddddd"), {"orgId": "o1", "runId": "old"})
    await rig.proxy.bindings.register(old)
    assert rig.volume.meter(old, "model-gateway").sent(50 * MIB)
    assert rig.volume.meter(old, "model-gateway").received(500 * MIB)
    assert rig.volume.meter(old, "other").sent(100)
    assert await rig.flush() == 0, "model traffic raises no threshold alert"
    await rig.proxy.bindings.unregister(old.sandbox_id)
    rig.clock.advance(limits.ended_after_s)
    _ = await rig.flush()
    assert rig.volume.baseline("o1") == (1, 100), "only the outside bytes join the baseline"
    await rig.proxy.bindings.register(A)
    assert rig.volume.meter(A, "tool-gateway").sent(30 * MIB)
    assert await rig.flush() == 0
    assert rig.volume.meter(A, "tunnel").sent(1001)
    assert await rig.flush() == 1
    [(_, alert)] = rig.sink.volume_alerts
    assert alert == EgressVolumeAlert("org_baseline", "up", 1001, 1000, "tunnel", baseline_bytes=100)
    ended = {(scope, direction): count for scope, direction, count in rig.metrics.ended}
    assert ended == {("gateway", "up"): 50 * MIB, ("gateway", "down"): 500 * MIB, ("outside", "up"): 100, ("outside", "down"): 0}


async def test_r11d_egs_5_outside_and_gateway_totals_are_told_apart_and_survive_a_restart() -> None:
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    sealer = StateSealer(b"wp61-state")
    limits = VolumeLimits(max_run_bytes=1000, **NO_ALERTS)
    first = EgressVolume(limits, clock=clock, store=store, sealer=sealer)
    assert first.meter(A, "model-gateway").sent(900) and first.meter(A, "model-gateway").received(900)
    assert first.meter(A, "other").sent(500)
    moved = first.volume(org_id="o1", run_id="r1")
    assert moved is not None
    assert (moved.up, moved.down, moved.outside_up, moved.outside_down) == (1400, 900, 500, 0)
    _ = await first.flush()

    second = EgressVolume(limits, clock=clock, store=store, sealer=sealer)
    assert await second.load() == 1
    restored = second.volume(org_id="o1", run_id="r1")
    assert restored is not None and (restored.outside_up, restored.capped) == (500, False)
    outside = second.meter(A, "other")
    assert outside.sent(500), "the restored run has 500 outside bytes left, whatever its model traffic"
    assert not outside.sent(1)
