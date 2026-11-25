"""SEC-EG-14 (C6.12, WP-45): the egress proxy counts what each run's sandboxes move, per destination alias and
direction; an unusual volume (past a per-run threshold or many times the org's usual run) is an alert on the run's
journal, once; an optional hard cap stops the run's outside streams and tunnels with ``volume_cap_exceeded``. The
alerts, the baseline and the cap judge outside traffic only; the gateways' is counted for the metrics (WP-61,
``test_wp61_egress_scope.py``). The hot path only adds integers: metrics, alerts and the state store are the flush's."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import aiohttp
import pytest
from aiohttp import web

from harness_core.gateway.refusal_reports import (
    EgressVolumeAlert,
    InMemoryRefusalSink,
    RefusalReport,
    RefusalReporter,
)
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
)
from harness_core.sandbox.egress.tunnel import copy_both
from harness_core.sandbox.egress.volume import (
    DOWN,
    MIB,
    UP,
    VOLUME_PREFIX,
    EgressVolume,
    VolumeLimits,
    VolumeLimitsInvalid,
    alias_for,
    volume_limits_from_env,
)
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from tests.support.cpu_budget import thread_cpu

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

R = EgressRefusalReason
SECRET = b"wp45-secret"
PH_A = "ph-model-aaaaaaaaaaaaaaaaaaaa"
PH_B = "ph-model-bbbbbbbbbbbbbbbbbbbb"
API = "api.example.test"
PUBLIC = "93.184.216.34"


def _policy(placeholder: str) -> EgressPolicy:
    return EgressPolicy(
        destinations=(
            EgressDestination("model-gateway", (8097,)),
            EgressDestination("tool-gateway", (8097,)),
            EgressDestination(API, (80, 443)),
        ),
        token_routes=(TokenRoute(placeholder, "model-gateway", "gateway:model"),),
    )


A = EgressBinding("sbx-a", "127.0.0.2", "127.0.0.1", _policy(PH_A), {"orgId": "o1", "runId": "r1", "attemptId": "a1"})
B = EgressBinding("sbx-b", "127.0.0.3", "127.0.0.1", _policy(PH_B), {"orgId": "o1", "runId": "r2", "attemptId": "a2"})


@dataclass
class FakeMetrics:
    moved: dict[tuple[str, str], int] = field(default_factory=dict[tuple[str, str], int])
    alerts: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    ended: list[tuple[str, str, int]] = field(default_factory=list[tuple[str, str, int]])

    def transferred(self, alias: str, direction: str, count: int) -> None:
        self.moved[alias, direction] = self.moved.get((alias, direction), 0) + count

    def alerted(self, kind: str, direction: str) -> None:
        self.alerts.append((kind, direction))

    def run_ended(self, scope: str, direction: str, count: int) -> None:
        self.ended.append((scope, direction, count))


class CountingStore(InMemoryEgressStateStore):
    def __init__(self, clock: ManualClock) -> None:
        super().__init__(clock)
        self.puts = 0

    async def put(self, key: str, value: str, *, ttl_s: int | None) -> None:
        self.puts += 1
        await super().put(key, value, ttl_s=ttl_s)


@dataclass
class Rig:
    clock: ManualClock
    proxy: EgressProxy
    sink: InMemoryRefusalSink
    reporter: RefusalReporter
    metrics: FakeMetrics

    @property
    def volume(self) -> EgressVolume:
        return self.proxy.volume

    async def flush(self) -> int:
        raised = await self.volume.flush()
        await self.reporter.drain()
        return raised


TOKENS = {
    InMemoryTokenSource.derive(SECRET, binding.sandbox_id, "gateway:model", 0): binding.labels["runId"] for binding in (A, B)
}


async def _run_of(token: str) -> str:
    return TOKENS.get(token, "")


def make_rig(limits: VolumeLimits, *, tunnels: bool = False, upstream: str = "http://127.0.0.1:1") -> Rig:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    # Accepts only the run's own gateway token for that run, as Node does.
    sink = InMemoryRefusalSink(authorize=_run_of)
    reporter = RefusalReporter(sink)
    metrics = FakeMetrics()
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(SECRET, clock, ttl_s=600),
        resolver=StaticResolver({API: [PUBLIC]}),
        upstreams={"model-gateway": upstream, "tool-gateway": upstream},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(connect_tunnels=tunnels),
        refusals=reporter,
        volume=EgressVolume(limits, clock=clock, bindings=bindings, metrics=metrics),
    )
    return Rig(clock, proxy, sink, reporter, metrics)


def gateway_call(binding: EgressBinding, host: str = "model-gateway", body: bytes = b"") -> ProxyRequest:
    headers = [("Host", f"{host}:8097")]
    if host == "model-gateway":
        headers.append(("Authorization", f"Bearer {next(iter(binding.placeholders()))}"))
    return ProxyRequest("POST", f"http://{host}:8097/v1/messages", tuple(headers), body)


# --- the meter and the flush, without a transport ------------------------------------------------------------


def test_sec_eg_14_aliases_are_a_fixed_set_never_a_host() -> None:
    assert alias_for("model-gateway", "http") == "model-gateway"
    assert alias_for("tool-gateway", "http") == "tool-gateway"
    assert alias_for(API, "http") == "other"
    assert alias_for(API, "tunnel") == "tunnel"
    assert alias_for(API, "intercepted") == "intercepted"
    assert alias_for("db.example", "database") == "database"
    # A gateway name on another route is not the gateway.
    assert alias_for("model-gateway", "tunnel") == "tunnel"


async def test_sec_eg_14_each_run_is_counted_per_alias_and_direction_and_exported_as_deltas() -> None:
    rig = make_rig(VolumeLimits())
    await rig.proxy.bindings.register(A)
    await rig.proxy.bindings.register(B)
    forward = await rig.proxy.authorize(gateway_call(A), local=A.proxy_address, peer=A.sandbox_address)
    assert forward.meter is not None
    assert forward.meter.sent(1000) and forward.meter.received(5000)
    tool = await rig.proxy.authorize(gateway_call(A, "tool-gateway"), local=A.proxy_address, peer=A.sandbox_address)
    assert tool.meter is not None and tool.meter.sent(70)
    other = await rig.proxy.authorize(gateway_call(B), local=B.proxy_address, peer=B.sandbox_address)
    assert other.meter is not None and other.meter.sent(3)
    one = rig.volume.volume(org_id="o1", run_id="r1")
    assert one is not None
    assert (one.up, one.down) == (1070, 5000)
    assert one.by_alias["model-gateway"] == (1000, 5000) and one.by_alias["tool-gateway"] == (70, 0)
    two = rig.volume.volume(org_id="o1", run_id="r2")
    assert two is not None and (two.up, two.down) == (3, 0)
    assert rig.metrics.moved == {}, "nothing is exported on the request path"
    _ = await rig.flush()
    assert rig.metrics.moved == {("model-gateway", "up"): 1003, ("model-gateway", "down"): 5000, ("tool-gateway", "up"): 70}
    assert forward.meter.sent(7)
    _ = await rig.flush()
    assert rig.metrics.moved["model-gateway", "up"] == 1010, "the second flush exports only what moved since"


async def test_sec_eg_14_a_run_past_its_threshold_is_journaled_once_with_its_own_token() -> None:
    rig = make_rig(VolumeLimits(alert_up_bytes=10 * MIB, alert_down_bytes=40 * MIB, baseline_factor=0))
    await rig.proxy.bindings.register(A)
    forward = await rig.proxy.authorize(gateway_call(A), local=A.proxy_address, peer=A.sandbox_address)
    assert forward.meter is not None
    assert forward.meter.sent(80 * MIB), "the gateways' traffic is never judged (WP-61)"
    assert rig.volume.meter(A, "other").sent(10 * MIB)
    assert await rig.flush() == 0, "at the threshold is not past it"
    tunnel = rig.volume.meter(A, "tunnel")
    assert tunnel.sent(5 * MIB) and tunnel.sent(6 * MIB)
    assert await rig.flush() == 1
    assert await rig.flush() == 0
    [(run_id, alert)] = rig.sink.volume_alerts
    assert run_id == "r1"
    assert alert == EgressVolumeAlert("run_threshold", "up", 21 * MIB, 10 * MIB, "tunnel")
    assert rig.metrics.alerts == [("run_threshold", "up")]
    assert tunnel.received(41 * MIB)
    assert await rig.flush() == 1
    assert rig.sink.volume_alerts[-1][1] == EgressVolumeAlert("run_threshold", "down", 41 * MIB, 40 * MIB, "tunnel")


async def test_sec_eg_14_an_alert_carries_counts_and_an_alias_never_a_host_path_or_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rig = make_rig(VolumeLimits(alert_up_bytes=1 * MIB, baseline_factor=0))
    await rig.proxy.bindings.register(A)
    secret_path = "/exfil?d=workspace-secret"
    forward = await rig.proxy.authorize(
        ProxyRequest("POST", f"http://{API}{secret_path}", (("Host", API),), b"x"),
        local=A.proxy_address,
        peer=A.sandbox_address,
    )
    assert forward.meter is not None and forward.meter.sent(2 * MIB)
    with caplog.at_level(logging.WARNING, logger="harness_core.sandbox.egress"):
        _ = await rig.flush()
    [record] = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.volume_alert"]
    fields = record.harness  # type: ignore[attr-defined]
    assert (fields["orgId"], fields["runId"], fields["kind"], fields["alias"]) == ("o1", "r1", "run_threshold", "other")
    assert "workspace-secret" not in repr(rig.sink.volume_alerts) + repr(fields)


async def test_sec_eg_14_the_org_baseline_arms_after_enough_ended_runs_and_flags_a_run_far_past_it() -> None:
    limits = VolumeLimits(
        alert_up_bytes=0, alert_down_bytes=0, baseline_factor=10, baseline_min_runs=3, baseline_floor_bytes=1 * MIB
    )
    rig = make_rig(limits)
    for i in range(3):
        run = EgressBinding(f"sbx-{i}", f"127.0.1.{i}", "127.0.0.1", _policy(f"ph-model-{i:020d}"), {"orgId": "o1", "runId": f"old{i}"})
        await rig.proxy.bindings.register(run)
        assert rig.volume.meter(run, "other").sent(2 * MIB)
        _ = await rig.flush()
        await rig.proxy.bindings.unregister(run.sandbox_id)
        rig.clock.advance(limits.ended_after_s)
        _ = await rig.flush()
    assert rig.volume.baseline("o1") == (3, 2 * MIB)
    assert sorted(rig.metrics.ended) == sorted(
        [("gateway", "down", 0), ("gateway", "up", 0), ("outside", "down", 0), ("outside", "up", 2 * MIB)] * 3
    )
    await rig.proxy.bindings.register(A)
    meter = rig.volume.meter(A, "tunnel")
    assert meter.sent(20 * MIB)
    assert await rig.flush() == 0, "ten times the usual run is the bound, not past it"
    assert meter.sent(1)
    assert await rig.flush() == 1
    [(_, alert)] = rig.sink.volume_alerts
    assert alert == EgressVolumeAlert("org_baseline", "up", 20 * MIB + 1, 20 * MIB, "tunnel", baseline_bytes=2 * MIB)
    # Another org has no baseline yet: the same volume is not unusual there.
    other = EgressBinding("sbx-x", "127.0.2.1", "127.0.0.1", _policy("ph-model-xxxxxxxxxxxxxxxxxxxx"), {"orgId": "o2", "runId": "rx"})
    await rig.proxy.bindings.register(other)
    assert rig.volume.meter(other, "tunnel").sent(50 * MIB)
    assert await rig.flush() == 0


async def test_sec_eg_14_a_run_that_still_has_a_binding_is_not_ended() -> None:
    limits = VolumeLimits(ended_after_s=60)
    rig = make_rig(limits)
    await rig.proxy.bindings.register(A)
    assert rig.volume.meter(A, "other").sent(10)
    rig.clock.advance(3600)
    _ = await rig.flush()
    assert rig.volume.baseline("o1") is None
    await rig.proxy.bindings.unregister(A.sandbox_id)
    _ = await rig.flush()
    assert rig.volume.baseline("o1") is None, "it moved bytes at the last flush: not ended yet"
    rig.clock.advance(60)
    _ = await rig.flush()
    assert rig.volume.baseline("o1") == (1, 10)
    rig.clock.advance(600)
    _ = await rig.flush()
    assert rig.volume.baseline("o1") == (1, 10), "a run joins the baseline once"


async def test_sec_eg_14_past_the_cap_the_run_is_refused_and_journaled_and_other_runs_go_on() -> None:
    rig = make_rig(VolumeLimits(max_run_bytes=1000, alert_up_bytes=0, alert_down_bytes=0, baseline_factor=0))
    await rig.proxy.bindings.register(A)
    await rig.proxy.bindings.register(B)
    meter = rig.volume.meter(A, "tunnel")
    assert meter.sent(600)
    assert not meter.received(401), "the chunk that crosses the cap is not passed on"
    assert meter.stopped and not meter.sent(1)
    with pytest.raises(EgressRefused) as caught:
        _ = await rig.proxy.authorize(
            ProxyRequest("POST", f"http://{API}/upload", (("Host", API),), b"x"), local=A.proxy_address, peer=A.sandbox_address
        )
    assert caught.value.reason is R.VOLUME_CAP_EXCEEDED and caught.value.status == 403
    with pytest.raises(EgressRefused):
        _ = await rig.proxy.authorize_tunnel(
            ProxyRequest("CONNECT", f"{API}:443", (("Host", f"{API}:443"),)), local=A.proxy_address, peer=A.sandbox_address
        )
    fine = await rig.proxy.authorize(gateway_call(B), local=B.proxy_address, peer=B.sandbox_address)
    assert fine.meter is not None and fine.meter.sent(999)
    assert await rig.flush() == 1
    await rig.reporter.drain()
    assert [r.report for r in rig.sink.records] == [RefusalReport("egress", "volume_cap_exceeded", 403)]
    [(run_id, alert)] = rig.sink.volume_alerts
    assert (run_id, alert) == ("r1", EgressVolumeAlert("run_cap", "total", 1001, 1000, "tunnel"))


async def test_sec_eg_14_a_sandbox_no_run_owns_is_capped_on_its_own_and_journals_nothing() -> None:
    rig = make_rig(VolumeLimits(max_run_bytes=100, alert_up_bytes=10))
    chat = EgressBinding("sbx-chat", "127.0.3.1", "127.0.0.1", _policy("ph-model-cccccccccccccccccccc"), {"orgId": "o1"})
    await rig.proxy.bindings.register(chat)
    meter = rig.volume.meter(chat, "other")
    assert meter.sent(50) and not meter.sent(51)
    assert await rig.flush() == 2
    assert rig.sink.volume_alerts == []
    assert rig.metrics.alerts == [("run_threshold", "up"), ("run_cap", "total")]


async def test_sec_eg_14_counts_caps_and_baselines_survive_a_restart_in_sealed_records() -> None:
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    sealer = StateSealer(b"wp45-state")
    limits = VolumeLimits(max_run_bytes=1000, alert_up_bytes=500, baseline_factor=0)
    bindings = InMemoryEgressBindings()
    await bindings.register(A)
    first = EgressVolume(limits, clock=clock, bindings=bindings, store=store, sealer=sealer)
    assert not first.meter(A, "tunnel").sent(1200)
    old = EgressBinding("sbx-o", "127.0.4.1", "127.0.0.1", _policy("ph-model-oooooooooooooooooooo"), {"orgId": "o1", "runId": "old"})
    assert first.meter(old, "other").sent(300)
    _ = await first.flush()
    clock.advance(limits.ended_after_s)
    _ = await first.flush()
    keys = [key for key, _ in await store.entries()]
    assert all(key.startswith(VOLUME_PREFIX) for key in keys) and len(keys) == 3
    assert all("r1" not in key and "o1" not in key for key in keys), "ids are only inside the sealed record"
    assert all("1200" not in value for _, value in await store.entries())

    second = EgressVolume(limits, clock=clock, bindings=bindings, store=store, sealer=sealer)
    assert await second.load() == 3
    with pytest.raises(EgressRefused):
        second.check(A)
    restored = second.volume(org_id="o1", run_id="r1")
    assert restored is not None and (restored.up, restored.capped) == (1200, True)
    assert second.baseline("o1") == (1, 300), "the run that ended joined the baseline; the live one did not"
    alerts: list[EgressVolumeAlert] = []
    second.on_alert(lambda _, alert: alerts.append(alert))
    _ = second.meter(A, "tunnel")
    _ = await second.flush()
    assert alerts == [], "an alert raised before the restart is not raised again"

    # The binding table's own load leaves the volume records alone: nothing unverified, the instance kept.
    durable = DurableEgressBindings(store, sealer, clock)
    loaded = await durable.load("egress-new")
    assert loaded.unverified == 0
    assert len([key for key, _ in await store.entries() if key.startswith(VOLUME_PREFIX)]) == 3


async def test_sec_eg_14_a_record_that_does_not_open_is_dropped() -> None:
    clock = ManualClock()
    store = InMemoryEgressStateStore(clock)
    await store.put(VOLUME_PREFIX + "run/" + "0" * 32, "v1.not-sealed", ttl_s=None)
    volume = EgressVolume(VolumeLimits(), clock=clock, store=store, sealer=StateSealer(b"k"))
    assert await volume.load() == 0
    assert await store.entries() == ()


async def test_sec_eg_14_the_hot_path_writes_nothing_and_costs_a_few_integer_adds() -> None:
    clock = ManualClock()
    store = CountingStore(clock)
    volume = EgressVolume(
        VolumeLimits(max_run_bytes=1 << 62), clock=clock, store=store, sealer=StateSealer(b"k"), metrics=FakeMetrics()
    )
    meter = volume.meter(A, "tunnel")
    chunk = 64 * 1024
    calls = 200_000

    def metered() -> float:
        with thread_cpu() as cpu:
            for _ in range(calls):
                meter.add(UP, chunk)
        return cpu.seconds

    def bare() -> float:
        counts = [0, 0]
        with thread_cpu() as cpu:
            for _ in range(calls):
                counts[0] += chunk
        return cpu.seconds

    per_call = min(metered() for _ in range(5)) / calls
    floor = min(bare() for _ in range(5)) / calls
    assert store.puts == 0, "no state write on the request path"
    # One call per 64 KiB chunk, about 200 ns of CPU alone: far below the cost of moving the chunk itself.
    assert per_call < 5e-6, f"{per_call * 1e9:.0f} ns per chunk"
    print(f"meter per 64 KiB chunk: {per_call * 1e9:.0f} ns (bare counter {floor * 1e9:.0f} ns)")
    _ = await volume.flush()
    assert store.puts == 1, "one record for the run, at the flush"
    assert volume.meter(A, "tunnel").sent(1)
    _ = await volume.flush()
    _ = await volume.flush()
    assert store.puts == 2, "a flush writes only what changed"


async def test_sec_eg_14_a_metered_copy_moves_bytes_as_fast_as_an_unmetered_one() -> None:
    payload = b"z" * (8 * MIB)

    async def copy(metered: bool) -> float:
        volume = EgressVolume(VolumeLimits(), clock=ManualClock())

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(payload)
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(handle, "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]

        async def front(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", up_port)
            await copy_both(reader, writer, up_reader, up_writer, [0, 0], meter=volume.meter(A, "tunnel") if metered else None)
            writer.close()

        proxy = await asyncio.start_server(front, "127.0.0.1", 0)
        port = proxy.sockets[0].getsockname()[1]
        started = time.perf_counter()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        got = 0
        while data := await reader.read(1 << 20):
            got += len(data)
        writer.close()
        elapsed = time.perf_counter() - started
        proxy.close()
        upstream.close()
        assert got == len(payload)
        if metered:
            moved = volume.volume(org_id="o1", run_id="r1")
            assert moved is not None and moved.down == len(payload)
        return elapsed

    plain = min([await copy(False) for _ in range(3)])
    metered = min([await copy(True) for _ in range(3)])
    print(f"8 MiB through a tunnel: {plain * 1e3:.1f} ms plain, {metered * 1e3:.1f} ms metered")
    assert metered < plain * 1.5 + 0.05


def test_sec_eg_14_settings_are_mib_a_factor_and_a_count_and_a_malformed_one_refuses_to_start() -> None:
    assert volume_limits_from_env({}) == VolumeLimits()
    limits = volume_limits_from_env(
        {
            "EGRESS_VOLUME_ALERT_UP_MB": "64",
            "EGRESS_VOLUME_ALERT_DOWN_MB": "0",
            "EGRESS_VOLUME_BASELINE_FACTOR": "5.5",
            "EGRESS_VOLUME_BASELINE_MIN_RUNS": "10",
            "EGRESS_VOLUME_BASELINE_FLOOR_MB": "8",
            "EGRESS_RUN_MAX_MB": "1024",
        }
    )
    assert limits == VolumeLimits(
        alert_up_bytes=64 * MIB,
        alert_down_bytes=0,
        baseline_factor=5.5,
        baseline_min_runs=10,
        baseline_floor_bytes=8 * MIB,
        max_run_bytes=1024 * MIB,
    )
    for name, value in (
        ("EGRESS_RUN_MAX_MB", "-1"),
        ("EGRESS_RUN_MAX_MB", "1GB"),
        ("EGRESS_VOLUME_ALERT_UP_MB", "1e3"),
        ("EGRESS_VOLUME_BASELINE_FACTOR", "0.5"),
        ("EGRESS_VOLUME_BASELINE_FACTOR", "nan"),
        ("EGRESS_VOLUME_BASELINE_FACTOR", "lots"),
    ):
        with pytest.raises(VolumeLimitsInvalid, match=name):
            _ = volume_limits_from_env({name: value})


def test_sec_eg_14_the_new_reason_is_unique_and_refuses_with_403() -> None:
    values = [reason.value for reason in EgressRefusalReason]
    assert values.count("volume_cap_exceeded") == 1


def test_sec_eg_14_an_alert_outside_its_bounds_is_never_built() -> None:
    assert EgressVolumeAlert.of("run_threshold", "up", 2, 1, "tunnel") is not None
    assert EgressVolumeAlert.of("dns_entropy", "up", 2, 1, "tunnel") is None
    assert EgressVolumeAlert.of("run_threshold", "sideways", 2, 1, "tunnel") is None
    assert EgressVolumeAlert.of("run_threshold", "up", 2, 1, "attacker.example") is None
    assert EgressVolumeAlert.of("run_threshold", "up", -1, 1, "tunnel") is None
    assert EgressVolumeAlert.of("run_threshold", "up", 2**53, 1, "tunnel") is None


# --- over the real listener ----------------------------------------------------------------------------------


class Gateway:
    """A gateway upstream that answers ``size`` bytes in 64 KiB chunks."""

    def __init__(self) -> None:
        self.size = 1024
        self.calls = 0
        self.capped: list[str] = []

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.calls += 1
        self.capped.append(request.headers.get("X-Egress-Capped", ""))
        _ = await request.read()
        response = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
        _ = await response.prepare(request)
        left = self.size
        while left > 0:
            step = min(left, 64 * 1024)
            await response.write(b"g" * step)
            left -= step
            await asyncio.sleep(0)
        await response.write_eof()
        return response


@pytest.fixture
async def gateway() -> AsyncIterator[tuple[Gateway, int]]:
    gw = Gateway()
    app = web.Application(client_max_size=64 * MIB)
    app.router.add_route("*", "/{tail:.*}", gw.handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield gw, runner.addresses[0][1]
    await runner.cleanup()


@contextlib.asynccontextmanager
async def served(rig: Rig, **kwargs: object) -> AsyncIterator[int]:
    server = EgressServer(rig.proxy, StaticBearerAuth("c"), instance="wp45", **kwargs)  # pyright: ignore[reportArgumentType]
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    try:
        yield data_port
    finally:
        await server.stop()


async def _post(data_port: int, body: bytes) -> tuple[int, dict[str, str], bytes]:
    connector = aiohttp.TCPConnector(local_addr=("127.0.0.1", 0))
    async with aiohttp.ClientSession(connector=connector) as session, session.post(
        f"http://127.0.0.2:{data_port}/v1/messages",
        headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH_A}"},
        data=body,
    ) as response:
        try:
            payload = await response.read()
        except aiohttp.ClientPayloadError:
            payload = b"<cut>"
        return response.status, dict(response.headers), payload


async def _post_outside(data_port: int) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
    writer.write(f"POST http://{API}/upload HTTP/1.1\r\nHost: {API}\r\nContent-Length: 1\r\nConnection: close\r\n\r\nx".encode())
    await writer.drain()
    answer = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    return answer


async def test_sec_eg_14_over_http_a_gateway_call_is_counted_both_ways_never_cut_and_marked_once_capped(
    gateway: tuple[Gateway, int],
) -> None:
    gw, port = gateway
    rig = make_rig(VolumeLimits(max_run_bytes=600 * 1024, alert_up_bytes=0, alert_down_bytes=0, baseline_factor=0), upstream=f"http://127.0.0.1:{port}")
    await rig.proxy.bindings.register(A)
    async with served(rig) as data_port:
        gw.size = 100 * 1024
        status, _, payload = await _post(data_port, b"q" * 4096)
        assert status == 200 and len(payload) == 100 * 1024
        moved = rig.volume.volume(org_id="o1", run_id="r1")
        assert moved is not None
        assert moved.by_alias["model-gateway"][DOWN] == 100 * 1024
        assert 4096 < moved.by_alias["model-gateway"][UP] < 4096 + 1024, "the body and the request head"
        # WP-61: a model answer past the cap's size is not cut: the gateways are never judged by it.
        gw.size = 1024 * 1024
        status, _, payload = await _post(data_port, b"")
        assert status == 200 and len(payload) == gw.size
        assert gw.capped == ["", ""]
        # Outside traffic caps the run: an outside request is refused, a model call goes on marked.
        assert not rig.volume.meter(A, "other").sent(600 * 1024 + 1)
        answer = await _post_outside(data_port)
        assert b" 403 " in answer.split(b"\r\n")[0] and b"volume_cap_exceeded" in answer
        status, _, payload = await _post(data_port, b"after")
        assert status == 200 and gw.capped[-1] == "volume"
        _ = await rig.flush()
    await rig.reporter.drain()
    assert RefusalReport("egress", "volume_cap_exceeded", 403) in [r.report for r in rig.sink.records]
    [(_, alert)] = rig.sink.volume_alerts
    assert (alert.kind, alert.direction, alert.alias) == ("run_cap", "total", "other")


async def test_sec_eg_14_over_a_tunnel_the_cap_closes_it_with_its_reason(caplog: pytest.LogCaptureFixture) -> None:
    sent = b"t" * (256 * 1024)

    async def api(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            writer.write(sent)
            await writer.drain()
            await reader.read()
        writer.close()

    upstream = await asyncio.start_server(api, "127.0.0.1", 0)
    api_port = upstream.sockets[0].getsockname()[1]

    async def connector(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        assert (address, port) == (PUBLIC, 443)
        return await asyncio.open_connection("127.0.0.1", api_port)

    rig = make_rig(VolumeLimits(max_run_bytes=100 * 1024, alert_up_bytes=0, alert_down_bytes=0), tunnels=True)
    await rig.proxy.bindings.register(A)
    try:
        with caplog.at_level(logging.INFO, logger="harness_core.sandbox.egress"):
            async with served(rig, connector=connector) as data_port:
                reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
                writer.write(f"CONNECT {API}:443 HTTP/1.1\r\nHost: {API}:443\r\n\r\n".encode())
                await writer.drain()
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
                assert head.startswith(b"HTTP/1.1 200")
                got = b""
                while data := await asyncio.wait_for(reader.read(65536), 10):
                    got += data
                writer.close()
                assert len(got) <= 100 * 1024, "nothing past the cap reached the sandbox"
                await asyncio.sleep(0.1)
                reader, writer = await asyncio.open_connection("127.0.0.2", data_port, local_addr=("127.0.0.1", 0))
                writer.write(f"CONNECT {API}:443 HTTP/1.1\r\nHost: {API}:443\r\n\r\n".encode())
                await writer.drain()
                again = await asyncio.wait_for(reader.read(), 10)
                writer.close()
                assert b" 403 " in again.split(b"\r\n")[0] and b"volume_cap_exceeded" in again
    finally:
        upstream.close()
    closed = [r for r in caplog.records if getattr(r, "harness_event", None) == "egress.tunnel_closed"]
    assert [r.harness.get("reason") for r in closed] == ["volume_cap_exceeded"]  # type: ignore[attr-defined]
