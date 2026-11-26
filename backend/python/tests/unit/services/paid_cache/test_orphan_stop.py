"""A record deleted while its run is in flight stops paying and storing (paid_cache/orphan.py).

The marker is advisory and the graph decides: a marker rewritten for a record that is live again (a redelivered delete of
an earlier incarnation) stops nothing, a real delete stops the run, a read that fails never advances a watermark.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.services.paid_cache import access, orphan
from app.services.paid_cache import cache as cache_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import (
    DELETED_TTL_MS,
    InProcessLocks,
    RedisLocks,
    deleted_name,
)
from app.services.paid_cache.orphan import (
    DeliveryGuard,
    ProbeResult,
    RecordGone,
    bind_guard,
)
from tests.support.paid_cache import MemoryResultStore

ORG, REC = "org-1", "rec-1"


def _key(i: int) -> CacheKey:
    return CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"p{i}")


class Graph:
    """The record as the graph says: a probe whose answer a test changes, and whose reads it counts."""

    def __init__(self) -> None:
        self.answer: ProbeResult | Exception = ProbeResult(True, True, 1)
        self.reads = 0
        self.delay = 0.0

    async def probe(self) -> ProbeResult:
        self.reads += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Paid:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        return "text"


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    store, locks, graph = MemoryResultStore(), InProcessLocks(), Graph()
    cache = PaidResultCache(store, locks)
    clock = [1000.0]
    guard = DeliveryGuard(
        org_id=ORG, record_id=REC, probe=graph.probe, created=1, clock=lambda: clock[0], backend_of=cache._backend,
    )
    guard.mark_read()
    bind_guard(guard)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    yield type("World", (), {"store": store, "locks": locks, "graph": graph, "cache": cache, "guard": guard, "clock": clock})
    guard.finish()


async def _delete_marker(w: Any, value: str = "2000") -> None:  # noqa: ANN401
    await w.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, value)


async def test_an_orphan_stops_paying_after_its_marker_changes_and_the_graph_says_gone(world: Any) -> None:  # noqa: ANN401
    paid = Paid()
    await world.cache.get_or_compute(_key(0), paid)
    await _delete_marker(world)
    world.graph.answer = ProbeResult(found=False)
    for i in range(1, 5):
        with pytest.raises(RecordGone):
            await world.cache.get_or_compute(_key(i), paid)
    assert paid.calls == 1
    assert world.store.paths(ORG, REC) == [_key(0).relative_path()]


async def test_a_result_paid_for_just_before_the_delete_is_not_stored(world: Any) -> None:  # noqa: ANN401
    paid_calls = 0

    async def compute() -> str:
        nonlocal paid_calls
        paid_calls += 1
        await _delete_marker(world)                       # the record is deleted while the call is in flight
        world.graph.answer = ProbeResult(found=False)
        return "text"

    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(0), compute)
    assert paid_calls == 1 and world.store.paths(ORG, REC) == []


async def test_a_marker_rewritten_for_a_record_that_is_live_again_stops_nothing_and_costs_one_read_per_change(world: Any) -> None:  # noqa: ANN401
    """S13d: a delete of an earlier incarnation redelivered after re-creation (a handover retry, a duplicate publish, a
    DLQ replay) writes a newer marker; the record is live, so the run goes on."""
    world.guard.wm_record = "100"                         # the run started under the old incarnation's delete
    world.guard.confirm_first = True
    await _delete_marker(world, "100")
    paid = Paid()
    await world.cache.get_or_compute(_key(0), paid)       # the first call confirms with the graph
    assert world.graph.reads == 1 and paid.calls == 1
    await _delete_marker(world, "200")                    # the old delete arrives again
    await world.cache.get_or_compute(_key(1), paid)
    await _delete_marker(world, "300")
    await world.cache.get_or_compute(_key(2), paid)
    assert (paid.calls, world.graph.reads) == (3, 3)      # 1 + M, never more
    for i in range(3, 20):
        await world.cache.get_or_compute(_key(i), paid)
    assert world.graph.reads == 3                         # unchanged markers cost no read


async def test_a_run_that_started_under_a_marker_reads_the_record_once_even_when_the_marker_never_changes(world: Any) -> None:  # noqa: ANN401
    world.guard.wm_record, world.guard.confirm_first = "100", True
    await _delete_marker(world, "100")
    world.graph.answer = ProbeResult(found=False)         # the graph delete was the later write: the marker said nothing
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(0), Paid())
    assert world.graph.reads == 1


async def test_a_newer_incarnation_stops_the_old_run_and_the_same_incarnation_goes_on(world: Any) -> None:  # noqa: ANN401
    """R4-3: delete plus a quick re-push while the old run is in flight: the graph finds a live record, but not the one
    this run serves."""
    await _delete_marker(world)
    world.graph.answer = ProbeResult(True, True, created=2)
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(0), Paid())
    other = DeliveryGuard(org_id=ORG, record_id=REC, probe=world.graph.probe, created=2, backend_of=world.cache._backend)
    bind_guard(other)
    paid = Paid()
    await world.cache.get_or_compute(_key(1), paid)       # the new incarnation's run, same marker, same record
    assert paid.calls == 1
    other.finish()


async def test_a_failed_read_never_advances_the_watermark_so_a_real_orphan_is_still_stopped(world: Any) -> None:  # noqa: ANN401
    """R4-2: the read fails exactly when the marker changes; the call goes on, the watermark stays, the retry finds it."""
    world.guard.wm_record = "100"
    paid = Paid()
    await _delete_marker(world, "200")                    # the real delete
    world.graph.answer = ConnectionError("graph down")
    await world.cache.get_or_compute(_key(0), paid)       # fails open
    assert world.guard.wm_record == "100" and world.guard.gone is False
    await world.cache.get_or_compute(_key(1), paid)       # inside the retry gap: no second read
    assert world.graph.reads == 1
    world.clock[0] += orphan.RETRY_S + 1
    world.graph.answer = ProbeResult(found=False)
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(2), paid)
    assert paid.calls == 2


async def test_a_lost_marker_is_found_by_the_periodic_graph_read_at_most_once_per_interval(world: Any) -> None:  # noqa: ANN401
    paid = Paid()
    for i in range(50):
        await world.cache.get_or_compute(_key(i), paid)   # a live record, no marker: the run's own start read counts
    assert world.graph.reads == 0
    world.clock[0] += orphan.RECHECK_S + 1
    world.graph.answer = ProbeResult(found=False)
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(60), paid)
    assert world.graph.reads == 1


async def test_parallel_checks_make_one_graph_read(world: Any) -> None:  # noqa: ANN401
    await _delete_marker(world)
    world.graph.delay = 0.05
    backend = await world.cache._backend()
    results = await asyncio.gather(*(world.guard.check(backend) for _ in range(8)))
    assert world.graph.reads == 1 and results == [False] * 8


async def test_a_live_record_pays_with_two_redis_reads_per_call_and_a_failing_redis_fails_open(world: Any) -> None:  # noqa: ANN401
    paid = Paid()
    await world.cache.get_or_compute(_key(0), paid)
    assert paid.calls == 1
    world.locks.marker_value = AsyncMock(side_effect=ConnectionError("redis down"))
    await world.cache.get_or_compute(_key(1), paid)
    assert paid.calls == 2 and world.guard.gone is False


async def test_after_the_first_positive_nothing_is_read_and_a_parser_that_swallows_the_stop_pays_nothing_more(world: Any) -> None:  # noqa: ANN401
    await _delete_marker(world)
    world.graph.answer = ProbeResult(found=False)
    paid = Paid()
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(0), paid)
    reads, markers = world.graph.reads, AsyncMock(side_effect=AssertionError("redis read after the stop"))
    world.locks.marker_value = markers
    for i in range(1, 101):                               # a parser that catches everything and carries on
        try:
            await world.cache.get_or_compute(_key(i), paid)
        except Exception:
            continue
    assert paid.calls == 0 and world.graph.reads == reads and markers.await_count == 0


async def test_a_call_outside_a_delivery_or_with_the_check_off_is_unchanged(world: Any) -> None:  # noqa: ANN401
    world.guard.finish()                                  # a closed guard is no guard
    await _delete_marker(world)
    world.graph.answer = ProbeResult(found=False)
    paid = Paid()
    await world.cache.get_or_compute(_key(0), paid)
    assert paid.calls == 1
    other = DeliveryGuard(org_id=ORG, record_id="another-record", probe=world.graph.probe)
    bind_guard(other)                                     # another record's guard does not apply to this one
    await world.cache.get_or_compute(_key(1), paid)
    assert paid.calls == 2
    other.finish()


async def test_the_cache_off_and_writes_off_paths_check_before_they_pay(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    await _delete_marker(world)
    world.graph.answer = ProbeResult(found=False)
    paid = Paid()
    monkeypatch.setattr(cache_module, "enabled", lambda: False)
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(0), paid)
    monkeypatch.setattr(cache_module, "enabled", lambda: True)
    monkeypatch.setattr(cache_module, "writes_enabled", lambda: False)
    world.guard.gone = False
    world.guard._retry_at = float("-inf")
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(1), paid)
    assert paid.calls == 0


async def test_the_stage_boundary_check_stops_the_embedding_of_a_deleted_record(world: Any) -> None:  # noqa: ANN401
    await orphan.ensure_record_alive(ORG, REC, "embed")             # live: nothing
    await _delete_marker(world)
    world.graph.answer = ProbeResult(found=False)
    with pytest.raises(RecordGone):
        await orphan.ensure_record_alive(ORG, REC, "embed")
    with pytest.raises(RecordGone):
        await orphan.ensure_record_alive(ORG, REC, "blob")          # sticky
    await orphan.ensure_record_alive(ORG, "unrelated", "embed")     # another record: no guard, no check


async def test_the_delete_handler_marks_with_the_delete_time_and_a_legacy_marker_is_only_a_value(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    monkeypatch.setattr(cache_module, "wall_clock", lambda: 1234.5)
    await world.cache.delete_record(ORG, REC)
    assert await world.locks.marker_value(deleted_name(ORG, REC)) == "1234500"
    assert await world.locks.is_marked(deleted_name(ORG, REC))                    # old readers keep working
    await world.locks.mark(deleted_name(ORG, "legacy"), DELETED_TTL_MS)           # an older release's marker
    assert await world.locks.marker_value(deleted_name(ORG, "legacy")) == "1"
    assert await world.locks.marker_value(deleted_name(ORG, "none")) is None


# -- Redis Cluster: the two markers are two single-key reads ---------------------------------------------------------


class _ClusterClient:
    """Refuses a command over keys of different hash tags, as a cluster does; counts the commands it saw."""

    def __init__(self) -> None:
        self.inner = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.seen: list[str] = []

    async def get(self, key: str) -> Any:  # noqa: ANN401
        self.seen.append("GET")
        return await self.inner.get(key)

    async def set(self, key: str, value: str, **kw: Any) -> Any:  # noqa: ANN401
        return await self.inner.set(key, value, **kw)

    async def exists(self, *keys: str) -> Any:  # noqa: ANN401
        self.seen.append("EXISTS")
        return await self.inner.exists(*keys)

    async def mget(self, *keys: str) -> Any:  # noqa: ANN401
        raise ConnectionError("CROSSSLOT Keys in request don't hash to the same slot")


async def test_the_markers_are_read_with_single_key_gets_so_a_cluster_serves_them_and_the_fence_keeps_its_exists() -> None:
    client = _ClusterClient()
    locks = RedisLocks(client, "ns")                                              # type: ignore[arg-type]
    cache = PaidResultCache(MemoryResultStore(), locks)
    await locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "2000")
    assert await cache.read_markers(ORG, REC) == ("2000", None)
    assert client.seen == ["GET", "GET"]                                          # one key per command
    assert await cache._deleted(ORG, REC) is True                                 # the fence is untouched
    assert "EXISTS" in client.seen


async def test_parallel_checks_when_the_read_fails_make_one_read_and_share_the_failure(world: Any) -> None:  # noqa: ANN401
    """QA-G32B-2: the failed read opens the retry window for every caller that waited on it; no one reads again."""
    world.guard.wm_record = "100"
    await _delete_marker(world, "200")
    world.graph.delay = 0.05
    world.graph.answer = ConnectionError("graph down")
    backend = await world.cache._backend()
    results = await asyncio.gather(*(world.guard.check(backend) for _ in range(8)))
    assert world.graph.reads == 1 and results == [False] * 8
    assert world.guard.wm_record == "100" and not world.guard.gone       # R4-2: no watermark advance


async def test_a_hung_graph_read_is_bounded_fails_open_and_stalls_no_caller_beyond_the_bound(world: Any) -> None:  # noqa: ANN401
    """QA-G32B-2: 8 parallel callers, the graph never answers. One read, the timeout fails it open, the watermark stays,
    nothing is stopped, and the next calls inside the retry window do not wait at all."""
    assert DeliveryGuard.__dataclass_fields__["probe_timeout_s"].default == orphan.PROBE_TIMEOUT_S <= 5.0
    world.guard.probe_timeout_s = 0.2
    world.guard.wm_record = "100"
    await _delete_marker(world, "200")
    world.graph.delay = 3600
    backend = await world.cache._backend()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    results = await asyncio.gather(*(world.guard.check(backend) for _ in range(8)))
    # wall-clock-ok: the hung read is 3600 s; eight callers waiting one after another never finish
    assert loop.time() - t0 < 1.5, "8 callers waited one after another on the hung read"
    assert world.graph.reads == 1 and results == [False] * 8
    assert world.guard.wm_record == "100" and not world.guard.gone
    t1 = loop.time()
    paid = Paid()
    await world.cache.get_or_compute(_key(0), paid)                     # inside the retry window: no read, no wait
    # wall-clock-ok: no read happens inside the retry window; the regression waits the 3600 s hung read
    assert loop.time() - t1 < 0.1 and paid.calls == 1 and world.graph.reads == 1
    world.clock[0] += orphan.RETRY_S + 1                                 # the graph is back and the record is gone
    world.graph.delay = 0
    world.graph.answer = ProbeResult(found=False)
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(1), paid)


async def test_a_backend_that_cannot_be_built_is_advisory_the_graph_still_decides(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    """Review: a failing backend factory must not fail a live record's call, and must not hide a confirmed orphan."""

    async def broken() -> Any:  # noqa: ANN401
        raise ConnectionError("redis down")

    monkeypatch.setattr(world.cache, "_locks", broken)
    paid = Paid()
    await world.cache.get_or_compute(_key(0), paid)                     # live: the call is made
    await world.cache.ensure_alive(ORG, REC, "embed")
    assert paid.calls == 1
    world.clock[0] += orphan.RECHECK_S + 1                               # the periodic read finds the delete
    world.graph.answer = ProbeResult(found=False)
    with pytest.raises(RecordGone):
        await world.cache.get_or_compute(_key(1), paid)
    assert paid.calls == 1


async def test_a_check_that_was_reading_markers_when_another_confirmed_the_delete_does_not_let_the_call_through(world: Any) -> None:  # noqa: ANN401
    """Review: check A reads the old markers (slowly), check B confirms the delete meanwhile; A must still stop."""
    backend = await world.cache._backend()
    real = backend.marker_value
    gate = asyncio.Event()

    async def slow_marker(name: str) -> Any:  # noqa: ANN401
        value = await real(name)
        if name == deleted_name(ORG) and not gate.is_set():
            gate.set()
            await asyncio.sleep(0.1)
        return value

    backend.marker_value = slow_marker                                   # type: ignore[method-assign]
    a = asyncio.create_task(world.guard.check(backend))
    await gate.wait()
    world.guard._mark_gone("graph")                                      # check B confirmed the delete
    assert await a is True
