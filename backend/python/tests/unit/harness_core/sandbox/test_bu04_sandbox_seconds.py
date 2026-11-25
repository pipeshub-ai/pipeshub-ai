"""BU-04 (C9.07): sandbox-seconds are metered per lease, acquire to release, on the monotonic clock (C9.07 asks for
1% of the wall clock; read off the meter's own clock it is exact); the time a kept sandbox waits for the next turn is
metered as kept, never as active."""

from __future__ import annotations

import inspect
import time
from dataclasses import replace

import pytest

from harness_core.sandbox.metering import (
    BILLING_LABELS,
    RecordingBrokerMetrics,
    SandboxMeter,
)
from harness_core.sandbox.pool import WarmPoolConfig, shape_of
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox import FakeSandboxBroker

LEASE = LeaseRef("worker-bu04", 1)
SPEC = SandboxSpec(
    image_digest="pxh-bu04:1",
    mode=RuntimeMode.DOCKER_HARDENED,
    resources=Resources(cpus=0.5, memory_mb=512, pids=64, scratch_mb=64),
    mounts=(MountSpec("/workspace"),),
    labels={"orgId": "org-bu04", "sessionId": "s-bu04", "runId": "run-bu04", "attemptId": "a1", "other": "x"},
)


class _Tick:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _broker(metrics: RecordingBrokerMetrics, **kw: object) -> FakeSandboxBroker:
    return FakeSandboxBroker(clock=ManualClock(), metrics=metrics, **kw)  # pyright: ignore[reportArgumentType]


async def test_bu04_a_lease_is_metered_acquire_to_release_on_the_monotonic_clock() -> None:
    metrics, tick = RecordingBrokerMetrics(), _Tick()
    broker = _broker(metrics, monotonic=tick)
    tick.now += 40  # before the acquire is no run's time
    handle = await broker.acquire(SPEC, lease=LEASE, idempotency_key="bu04-wall")
    tick.now += 1.0
    await broker.release(handle)
    tick.now += 40
    (interval,) = metrics.intervals
    assert interval.phase == "active" and interval.sandbox_id == handle.id
    assert interval.seconds == 1.0
    for metered in (SandboxMeter, FakeSandboxBroker):
        assert inspect.signature(metered).parameters["monotonic"].default is time.monotonic, metered


async def test_bu04_the_metered_line_names_who_to_bill_and_no_other_label() -> None:
    metrics = RecordingBrokerMetrics()
    broker = _broker(metrics)
    await broker.release(await broker.acquire(SPEC, lease=LEASE, idempotency_key="bu04-labels"))
    (interval,) = metrics.intervals
    assert dict(interval.labels) == {k: SPEC.labels[k] for k in BILLING_LABELS}
    assert interval.mode == RuntimeMode.DOCKER_HARDENED.value


async def test_bu04_kept_time_is_never_billed_as_active_and_a_turn_taking_it_back_starts_active_again() -> None:
    metrics, tick = RecordingBrokerMetrics(), _Tick()
    broker = _broker(metrics, monotonic=tick)
    handle = await broker.acquire(SPEC, lease=LEASE, idempotency_key="bu04-kept")
    tick.now += 30
    await broker.release(handle, keep_warm_s=600)
    tick.now += 200
    again = await broker.acquire(SPEC, lease=LEASE, idempotency_key="bu04-kept")
    assert again.id == handle.id
    tick.now += 10
    await broker.release(again)
    assert [(i.phase, i.seconds) for i in metrics.intervals] == [("active", 30), ("kept", 200), ("active", 10)]
    assert metrics.seconds("active") == 40


async def test_bu04_the_same_key_twice_while_held_is_one_interval() -> None:
    metrics, tick = RecordingBrokerMetrics(), _Tick()
    broker = _broker(metrics, monotonic=tick)
    first = await broker.acquire(SPEC, lease=LEASE, idempotency_key="bu04-twice")
    tick.now += 5
    _ = await broker.acquire(SPEC, lease=LEASE, idempotency_key="bu04-twice")
    tick.now += 5
    await broker.release(first)
    assert [(i.phase, i.seconds) for i in metrics.intervals] == [("active", 10)]


@pytest.mark.parametrize("ending", ["reap", "sweep"])
async def test_bu04_a_sandbox_the_broker_ends_on_its_own_closes_its_interval(ending: str) -> None:
    metrics, tick = RecordingBrokerMetrics(), _Tick()
    clock = ManualClock()
    broker = FakeSandboxBroker(clock=clock, metrics=metrics, monotonic=tick)
    handle = await broker.acquire(SPEC, lease=LEASE, idempotency_key=f"bu04-{ending}")
    tick.now += 7
    if ending == "reap":
        assert await broker.reap(labels={"runId": "run-bu04"}, older_than_s=0) == 1
        assert [(i.phase, i.seconds) for i in metrics.intervals] == [("active", 7)]
    else:
        await broker.release(handle, keep_warm_s=60)
        tick.now += 60
        _ = clock.advance(61)
        assert broker.sweep_warm() == 1
        assert [(i.phase, i.seconds) for i in metrics.intervals] == [("active", 7), ("kept", 60)]


async def test_bu04_a_warm_sandbox_is_metered_from_its_bind_not_from_its_create() -> None:
    metrics, tick = RecordingBrokerMetrics(), _Tick()
    shape = shape_of(SPEC)
    assert shape is not None
    broker = _broker(
        metrics,
        monotonic=tick,
        warm_pool=WarmPoolConfig(ceiling=1, shapes=(shape,), fraction=1.0, reserve_fraction=0.0),
    )
    assert await broker.fill_warm() == 1
    (warm,) = broker.warm_ids()
    tick.now += 500  # waiting in the pool is no run's time
    handle = await broker.acquire(replace(SPEC), lease=LEASE, idempotency_key="bu04-warm")
    assert handle.id == warm
    tick.now += 3
    await broker.release(handle)
    assert [(i.phase, i.seconds) for i in metrics.intervals] == [("active", 3)]
    assert metrics.claims == ["warm"]


def test_bu04_a_wall_clock_step_never_changes_a_metered_interval() -> None:
    metrics, tick = RecordingBrokerMetrics(), _Tick()
    meter = SandboxMeter(metrics, monotonic=tick)
    meter.bound("sbx-1", SPEC)
    tick.now += 2.5
    assert meter.ended("sbx-1") is not None
    assert meter.ended("sbx-1") is None, "an interval closes once"
    assert metrics.seconds("active") == 2.5
