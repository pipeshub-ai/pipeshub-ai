"""G-45 / GA-24: one provider limit for the model gateway and every worker process, over Redis.

The gateway's own in-process gate limited only the engine calls it served; a loop run's calls left from the worker
and were never counted, and each worker replica would have kept its own limit. ``RedisProviderGate`` keeps the one
limit, its queue and its class order in Redis. Two gate instances over one (fake) Redis here are two processes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.provider_gate import (
    ENV_PROVIDER_CONCURRENCY,
    RedisProviderGate,
    provider_admission,
    provider_limit,
)
from harness_core.gateway.priority_gate import DEFAULT_LIMIT, PriorityGate
from harness_core.priority import PRIORITY_CLASSES, preemptible, priority_rank
from harness_core.testing import ManualClock
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider
from tests.support.settle import settle

if TYPE_CHECKING:
    from harness_core.gateway.priority_gate import AdmissionSlot
    from harness_core.priority import PriorityClass


class _Rig:
    def __init__(self, limit: int, **options: Any) -> None:  # noqa: ANN401
        self.clock = ManualClock()
        self.redis = FakeRedisConnectionProvider(is_cluster=True, key_namespace="ns")
        self.options = options
        self.limit = limit

    def process(self, **options: Any) -> RedisProviderGate:  # noqa: ANN401
        return RedisProviderGate(
            self.redis,
            self.limit,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
            now_ms=lambda: int(self.clock.monotonic() * 1000),
            **{**self.options, **options},
        )

    async def turns(self, n: int = 200) -> None:
        for _ in range(n):
            await asyncio.sleep(0)

    async def tick(self, seconds: float = 0.05, n: int = 200) -> None:
        _ = self.clock.advance(seconds)
        await self.turns(n)

    async def until(self, done: asyncio.Future[Any] | asyncio.Task[Any], seconds: float = 5.0) -> None:
        for _ in range(int(seconds / 0.05)):
            if done.done():
                return
            await self.tick()
        assert done.done(), "not granted in time"


def test_ga24_every_class_a_person_waits_on_ranks_before_every_preemptible_one() -> None:
    """The shared script decides on the head of the class-ordered queue alone, which is right only so."""
    for waits in (c for c in PRIORITY_CLASSES if not preemptible(c)):
        for background in (c for c in PRIORITY_CLASSES if preemptible(c)):
            assert priority_rank(waits) < priority_rank(background)


async def test_ga24_two_processes_share_one_limit_and_a_slot_freed_in_one_reaches_the_other() -> None:
    rig = _Rig(2, floor_every=0)
    worker, gateway = rig.process(), rig.process()
    held = [await worker.acquire("openai", "background"), await gateway.acquire("openai", "interactive")]
    blocked = asyncio.ensure_future(gateway.acquire("openai", "interactive"))
    await rig.turns()
    for _ in range(10):
        await rig.tick()
    assert not blocked.done(), "two calls in flight across both processes: the limit is the deployment's"
    held[0].release()
    await rig.until(blocked)
    assert blocked.result().waited
    for slot in (held[1], blocked.result()):
        slot.release()
    await rig.turns()
    assert worker.held_here("openai") == gateway.held_here("openai") == 0
    await worker.aclose()
    await gateway.aclose()


async def test_ga24_an_interactive_call_in_one_process_goes_before_older_background_calls_in_another() -> None:
    rig = _Rig(1, floor_every=0)
    gateway, worker = rig.process(), rig.process()
    held = await gateway.acquire("openai", "background")
    order: list[str] = []

    async def call(gate: RedisProviderGate, name: str, cls: PriorityClass) -> None:
        slot = await gate.acquire("openai", cls)
        order.append(name)
        await rig.clock.sleep(0.1)
        slot.release()

    calls = [asyncio.ensure_future(call(gateway, f"engine-bg-{i}", "background")) for i in range(3)]
    await rig.tick()
    calls.append(asyncio.ensure_future(call(worker, "loop-turn", "interactive")))
    await rig.tick()
    held.release()
    for task in calls:
        await rig.until(task, seconds=10)
    assert order == ["loop-turn", "engine-bg-0", "engine-bg-1", "engine-bg-2"]
    await gateway.aclose()
    await worker.aclose()


async def test_ga24_background_calls_leave_a_quarter_of_the_shared_slots_for_a_person() -> None:
    rig = _Rig(8, floor_every=0)
    a, b = rig.process(), rig.process()
    background = [await (a if i % 2 else b).acquire("openai", "background") for i in range(6)]
    blocked = asyncio.ensure_future(a.acquire("openai", "batch"))
    for _ in range(5):
        await rig.tick()
    assert not blocked.done(), "6 of 8 in flight: the last 2 are a person's"
    person = await b.acquire("openai", "interactive")
    answered = await a.acquire("openai", "approval_resume")
    for slot in (person, answered, *background):
        slot.release()
    await rig.until(blocked)
    blocked.result().release()
    await a.aclose()
    await b.aclose()


async def test_ga24_every_fifth_shared_grant_goes_to_the_oldest_waiter_so_background_moves() -> None:
    rig = _Rig(1, floor_every=5)
    worker, gateway = rig.process(), rig.process()
    held = await gateway.acquire("openai", "interactive")
    order: list[str] = []

    async def call(gate: RedisProviderGate, name: str, cls: PriorityClass) -> None:
        slot = await gate.acquire("openai", cls)
        order.append(name)
        await rig.clock.sleep(0.1)
        slot.release()

    calls = [asyncio.ensure_future(call(worker, "firing", "background"))]
    await rig.tick()
    for i in range(6):
        calls.append(asyncio.ensure_future(call(gateway, f"person-{i}", "interactive")))
        await rig.tick(0.001)
    held.release()
    for task in calls:
        await rig.until(task, seconds=20)
    assert order == ["person-0", "person-1", "person-2", "firing", "person-3", "person-4", "person-5"]
    await worker.aclose()
    await gateway.aclose()


async def test_ga24_a_waiter_whose_process_died_leaves_the_queue_after_its_beat() -> None:
    rig = _Rig(1, floor_every=0, beat_s=1.0)
    dead, alive = rig.process(), rig.process()
    held = await alive.acquire("openai", "background")
    ghost = asyncio.ensure_future(dead.acquire("openai", "interactive"))
    await rig.tick()
    # The process dies: it stops asking and never says it left.

    async def never(*_: object) -> None:
        return None

    dead._leave = never  # type: ignore[method-assign]
    _ = ghost.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ghost
    waiting = asyncio.ensure_future(alive.acquire("openai", "background"))
    held.release()
    await rig.until(waiting, seconds=3)
    waiting.result().release()
    await alive.aclose()


async def test_ga24_a_cancelled_waiter_leaves_the_shared_queue_at_once() -> None:
    rig = _Rig(1, floor_every=0)
    a, b = rig.process(), rig.process()
    held = await a.acquire("openai", "background")
    gone = asyncio.ensure_future(b.acquire("openai", "interactive"))
    await rig.tick()
    _ = gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    await rig.turns()
    later = asyncio.ensure_future(a.acquire("openai", "background"))
    held.release()
    await rig.until(later, seconds=0.5)
    later.result().release()
    await a.aclose()
    await b.aclose()


async def test_ga24_a_crashed_process_slots_come_back_after_their_hold_and_a_live_one_is_renewed() -> None:
    rig = _Rig(2, floor_every=0, hold_s=3.0)
    crashed, alive = rig.process(), rig.process()
    lost = await crashed.acquire("openai", "background")
    # The process is gone: nothing renews or gives back its slot.
    _ = crashed._renewer.cancel() if crashed._renewer is not None else None  # pyright: ignore[reportPrivateUsage]
    kept = await alive.acquire("openai", "background")
    blocked = asyncio.ensure_future(alive.acquire("openai", "interactive"))
    for _ in range(int(2.5 / 0.05)):
        await rig.tick()
    assert not blocked.done()
    await rig.until(blocked, seconds=2)
    for _ in range(int(10 / 0.05)):
        await rig.tick()
    third = asyncio.ensure_future(alive.acquire("openai", "interactive"))
    for _ in range(20):
        await rig.tick()
    assert not third.done(), "the live process's slots are renewed past their hold"
    kept.release()
    await rig.until(third)
    for slot in (blocked.result(), third.result()):
        slot.release()
    assert lost is not None
    await alive.aclose()


async def test_ga24_redis_unreachable_falls_back_to_the_process_gate_with_the_same_limit() -> None:
    rig = _Rig(1, floor_every=0)
    gate = rig.process()

    async def down(*_: object) -> str:
        raise ConnectionError("redis down")

    rig.redis.load_script = down  # type: ignore[method-assign]
    first = await gate.acquire("openai", "background")
    blocked = asyncio.ensure_future(gate.acquire("openai", "background"))
    await rig.turns()
    assert not blocked.done(), "the process gate still holds the limit"
    first.release()
    slot: AdmissionSlot = await settle(blocked)
    slot.release()


async def test_providers_are_limited_apart_in_cluster_slots_of_their_own() -> None:
    rig = _Rig(1)
    gate = rig.process()
    openai = await gate.acquire("openai", "background")
    anthropic = await settle(gate.acquire("anthropic", "background"))
    for slot in (openai, anthropic):
        slot.release()
    await gate.aclose()


@pytest.mark.parametrize(("raw", "expected"), [(None, DEFAULT_LIMIT), ("0", 0), ("12", 12), ("x", DEFAULT_LIMIT)])
def test_the_limit_parses_and_falls_back(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int) -> None:
    if raw is None:
        monkeypatch.delenv(ENV_PROVIDER_CONCURRENCY, raising=False)
    else:
        monkeypatch.setenv(ENV_PROVIDER_CONCURRENCY, raw)
    assert provider_limit() == expected


class _Config:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def get_redis_config(self) -> dict[str, Any]:
        if self.fail:
            raise ConnectionError("no redis")
        return {"host": "redis", "port": 6379}


async def test_ga24_the_process_admission_is_shared_over_redis_and_local_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeRedisConnectionProvider()
    monkeypatch.setattr("app.services.redis.connection_provider_factory.get_redis_provider", lambda *_a, **_k: provider)
    monkeypatch.delenv(ENV_PROVIDER_CONCURRENCY, raising=False)
    clock = ManualClock()
    assert isinstance(await provider_admission(_Config(fail=False), clock), RedisProviderGate)  # type: ignore[arg-type]
    assert isinstance(await provider_admission(_Config(fail=True), clock), PriorityGate)  # type: ignore[arg-type]
    monkeypatch.setenv(ENV_PROVIDER_CONCURRENCY, "0")
    unlimited = await provider_admission(_Config(fail=False), clock)  # type: ignore[arg-type]
    assert isinstance(unlimited, PriorityGate) and unlimited.limit == 0
