"""Round 11 item E (C6.12, SEC-EG-14 x SIGNIN-5): what WP-45's volume meter did to kept sandboxes, lost alerts and
memory, and how the cap is told.

R11-EGV-1 a sandbox kept for a conversation (SIGNIN-5: no ``runId``, the run it serves under ``pipeshub.bound.runId``)
          is metered, alerted, kept and folded into the org's baseline as that run, never as a sandbox of its own.
R11-EGV-2 a refusal of such a sandbox goes on the bound run's journal too.
R11-EGV-3 an alert whose send failed is raised again at the next flush; it is counted in the metrics once.
R11-EGV-4 an alert crossed since the last flush is journaled when the binding goes, with the token the binding had
          (Node issues none once the attempt no longer holds the run).
R11-EGV-5 a sandbox no run owns leaves memory once its binding is gone and it has been idle past ``ended_after_s``.
R11-EGV-6 the cap is kept at the chunk that crosses it, before any flush: a proxy that restarts refuses the run.
R11-EGV-7 the cap's refusal says in plain words that the run reached its data limit and must not retry.
R11-EGV-8 ``EGRESS_VOLUME_BASELINE_MIN_RUNS=0`` arms the baseline from the org's first ended run, as 1 does.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, override

import pytest

from harness_core.gateway.refusal_reports import (
    EgressVolumeAlert,
    InMemoryRefusalSink,
    RefusalReport,
    RefusalReporter,
)
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.server import refusal_response
from harness_core.sandbox.egress.state import InMemoryEgressStateStore, StateSealer
from harness_core.sandbox.egress.tokens import TokenUnavailable
from harness_core.sandbox.egress.volume import (
    ENV_BASELINE_MIN_RUNS,
    EgressVolume,
    VolumeLimits,
    volume_limits_from_env,
)
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from harness_core.gateway.refusal_reports import RecordOutcome
    from harness_core.sandbox.egress.types import IssuedToken

from .test_wp45_egress_volume import (
    API,
    PH_A,
    PUBLIC,
    SECRET,
    FakeMetrics,
    Rig,
    gateway_call,
)


def _policy(placeholder: str) -> EgressPolicy:
    return EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)), EgressDestination("tool-gateway", (8097,))),
        token_routes=(TokenRoute(placeholder, "model-gateway", "gateway:model"),),
    )


KEPT = EgressBinding(
    "sbx-kept",
    "127.0.5.2",
    "127.0.0.1",
    _policy(PH_A),
    {"orgId": "o1", "pipeshub.bound.runId": "r1", "pipeshub.bound.attemptId": "a1"},
)
OWN = EgressBinding("sbx-own", "127.0.5.3", "127.0.0.1", _policy("ph-model-dddddddddddddddddddd"), {"orgId": "o1", "runId": "r2", "attemptId": "a2"})


class _NodeTokens(InMemoryTokenSource):
    """Issues a binding's gateway token only while the binding is held, as Node issues one only to the attempt that
    holds its run."""

    def __init__(self, bindings: InMemoryEgressBindings, clock: ManualClock) -> None:
        super().__init__(SECRET, clock, ttl_s=600)
        self._held = bindings

    @override
    async def issue(self, route: TokenRoute, binding: EgressBinding) -> IssuedToken:
        if binding.sandbox_id not in {b.sandbox_id for b in self._held.held()}:
            raise TokenUnavailable("the attempt no longer holds its run")
        return await super().issue(route, binding)


def _rig(limits: VolumeLimits, *, store: InMemoryEgressStateStore | None = None, sealer: StateSealer | None = None) -> Rig:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    runs = {"sbx-kept": "r1", "sbx-own": "r2"}

    async def run_of(token: str) -> str:
        for sandbox, run in runs.items():
            for generation in range(4):
                if token == InMemoryTokenSource.derive(SECRET, sandbox, "gateway:model", generation):
                    return run
        return ""

    sink = InMemoryRefusalSink(authorize=run_of)
    reporter = RefusalReporter(sink)
    metrics = FakeMetrics()
    proxy = EgressProxy(
        bindings=bindings,
        tokens=_NodeTokens(bindings, clock),
        resolver=StaticResolver({API: [PUBLIC]}),
        upstreams={"model-gateway": "http://127.0.0.1:1", "tool-gateway": "http://127.0.0.1:1"},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(),
        refusals=reporter,
        volume=EgressVolume(limits, clock=clock, bindings=bindings, metrics=metrics, store=store, sealer=sealer),
    )
    return Rig(clock, proxy, sink, reporter, metrics)


async def test_r11_egv_1_a_kept_sandbox_is_metered_alerted_kept_and_folded_as_its_bound_run() -> None:
    clock_store = ManualClock()
    store = InMemoryEgressStateStore(clock_store)
    rig = _rig(VolumeLimits(alert_up_bytes=10, alert_down_bytes=0, baseline_factor=0), store=store, sealer=StateSealer(b"k"))
    await rig.proxy.bindings.register(KEPT)
    assert rig.volume.meter(KEPT, "other").sent(100)
    assert await rig.flush() == 1
    counted = rig.volume.volume(org_id="o1", run_id="r1")
    assert counted is not None and counted.up == 100
    assert rig.sink.volume_alerts == [("r1", EgressVolumeAlert("run_threshold", "up", 100, 10, "other"))]
    assert len(await store.entries()) == 1, "the run's record is kept, so a restart keeps its count"
    await rig.proxy.bindings.unregister(KEPT.sandbox_id)
    _ = rig.clock.advance(rig.volume.limits.ended_after_s)
    _ = await rig.flush()
    assert rig.volume.baseline("o1") == (1, 100), "the run joined its org's baseline"


async def test_r11_egv_2_a_kept_sandbox_refusal_goes_on_its_bound_run() -> None:
    rig = _rig(VolumeLimits(max_run_bytes=10, alert_up_bytes=0, alert_down_bytes=0, baseline_factor=0))
    await rig.proxy.bindings.register(KEPT)
    assert not rig.volume.meter(KEPT, "other").sent(11)
    outside = ProxyRequest("POST", f"http://{API}/upload", (("Host", API),), b"x")
    with pytest.raises(EgressRefused):
        _ = await rig.proxy.authorize(outside, local=KEPT.proxy_address, peer=KEPT.sandbox_address)
    await rig.reporter.drain()
    assert [(r.run_id, r.report) for r in rig.sink.records] == [("r1", RefusalReport("egress", "volume_cap_exceeded", 403))]


async def test_r11_egv_3_an_alert_whose_send_failed_is_raised_again_and_counted_once() -> None:
    rig = _rig(VolumeLimits(alert_up_bytes=10, alert_down_bytes=0, baseline_factor=0))
    await rig.proxy.bindings.register(OWN)
    real = rig.sink.volume
    tries = 0

    async def flaky(run_id: str, token: str, alert: EgressVolumeAlert) -> RecordOutcome:
        nonlocal tries
        tries += 1
        if tries == 1:
            raise ConnectionError("node restarting")
        return await real(run_id, token, alert)

    rig.sink.volume = flaky  # type: ignore[method-assign]
    assert rig.volume.meter(OWN, "other").sent(100)
    assert await rig.flush() == 1
    assert rig.sink.volume_alerts == []
    await asyncio.sleep(0)
    assert await rig.flush() == 0, "a resend is not a new alert"
    assert rig.sink.volume_alerts == [("r2", EgressVolumeAlert("run_threshold", "up", 100, 10, "other"))]
    assert rig.metrics.alerts == [("run_threshold", "up")]
    _ = await rig.flush()
    assert tries == 2, "once taken, it is not sent again"


async def test_r11_egv_4_an_alert_crossed_as_the_binding_goes_is_journaled_with_its_token() -> None:
    rig = _rig(VolumeLimits(max_run_bytes=1000, alert_up_bytes=0, alert_down_bytes=0, baseline_factor=0))
    await rig.proxy.bindings.register(OWN)
    # A request through the gateway route: the binding's token is issued while it is live.
    forward = await rig.proxy.authorize(gateway_call(OWN), local=OWN.proxy_address, peer=OWN.sandbox_address)
    assert forward.meter is not None and forward.meter.sent(1001), "the gateways never trip the cap (WP-61)"
    assert not rig.volume.meter(OWN, "tunnel").sent(1001)
    # The cut stream ends the engine, and the sandbox is released before the next flush.
    await rig.proxy.bindings.unregister(OWN.sandbox_id)
    await rig.reporter.drain()
    assert rig.sink.volume_alerts == [("r2", EgressVolumeAlert("run_cap", "total", 1001, 1000, "tunnel"))]
    assert await rig.flush() == 0


async def test_r11_egv_5_a_sandbox_no_run_owns_leaves_memory_once_gone_and_idle() -> None:
    rig = _rig(VolumeLimits())
    for i in range(10):
        chat = EgressBinding(f"sbx-{i}", f"127.0.6.{i + 1}", "127.0.0.1", _policy("ph-model-" + str(i) * 20), {"orgId": "o1"})
        await rig.proxy.bindings.register(chat)
        assert rig.volume.meter(chat, "other").sent(1)
        if i < 9:
            await rig.proxy.bindings.unregister(chat.sandbox_id)
    _ = await rig.flush()
    assert rig.volume.runs_metered == 10
    _ = rig.clock.advance(rig.volume.limits.ended_after_s)
    _ = await rig.flush()
    assert rig.volume.runs_metered == 1, "only the sandbox still bound is kept"
    assert rig.volume.baseline("o1") is None, "a sandbox no run owns is no run of its org"


async def test_r11_egv_6_the_cap_is_kept_at_the_crossing_chunk_before_any_flush() -> None:
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    sealer = StateSealer(b"k")
    limits = VolumeLimits(max_run_bytes=100, alert_up_bytes=0, alert_down_bytes=0, baseline_factor=0)
    bindings = InMemoryEgressBindings()
    await bindings.register(OWN)
    first = EgressVolume(limits, clock=clock, bindings=bindings, store=store, sealer=sealer)
    assert not first.meter(OWN, "tunnel").sent(101)
    for _ in range(3):
        await asyncio.sleep(0)
    restarted = EgressVolume(limits, clock=clock, bindings=bindings, store=store, sealer=sealer)
    assert await restarted.load() == 1
    with pytest.raises(EgressRefused):
        restarted.check(OWN)


def test_r11_egv_7_the_cap_refusal_says_in_plain_words_not_to_retry() -> None:
    refused = EgressRefused(R.VOLUME_CAP_EXCEEDED, "the run moved more than the deployment's egress cap")
    refused.request_id = "req-1"
    body = json.loads(refusal_response(refused).text or "{}")["error"]
    assert body["reason"] == "volume_cap_exceeded"
    assert "Do not retry" in body["message"] and "data limit" in body["message"]
    assert not any(ch.isdigit() for ch in body["message"])
    other = json.loads(refusal_response(EgressRefused(R.UNBOUND_SOURCE, "x")).text or "{}")["error"]
    assert "message" not in other


def test_r11_egv_8_min_runs_zero_arms_the_baseline_from_the_first_ended_run() -> None:
    assert volume_limits_from_env({ENV_BASELINE_MIN_RUNS: "0"}).baseline_min_runs == 1
    assert volume_limits_from_env({ENV_BASELINE_MIN_RUNS: "1"}).baseline_min_runs == 1
