"""``NodePaidResultStore.purge_records``: the batch purge route, its fallback to one purge per record, and the page hop
that uses it. Against a local stand-in for the storage service (real sockets)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import pytest

from app.plugins.telemetry.backend import METRICS_BACKEND
from app.services.paid_cache import store as store_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.store import (
    BATCH_REPROBE_BASE_S,
    BATCH_REPROBE_MAX_S,
    PURGE_BATCH_MAX,
    NodePaidResultStore,
    PaidResultStoreError,
)
from tests.support.fake_paid_result_storage import FakePaidResultStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PATH = f"ocr_page/{'a' * 64}.json"


@pytest.fixture(autouse=True)
def _clean_state() -> None:
    store_module.PAID_RESULT_ROUTES.reset()
    store_module.PURGE_BATCH_ROUTE.reset()
    METRICS_BACKEND.reset()


@pytest.fixture
async def storage() -> AsyncIterator[FakePaidResultStorage]:
    fake = FakePaidResultStorage()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(store_module.PURGE_BATCH_ROUTE, "_clock", lambda: now[0])
    return now


def _store(fake: FakePaidResultStorage, logger: logging.Logger | None = None) -> NodePaidResultStore:
    return NodePaidResultStore(fake.blob_storage(), logger or logging.getLogger("purge-batch-test"))


def _metric(name: str, **labels: str) -> float:
    """The value of one series of the process's metrics (0 when it has not been touched)."""
    want = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    for line in METRICS_BACKEND.serialize().splitlines():
        m = re.fullmatch(rf"{name}\{{(.*)\}} (\S+)", line)
        if m and ",".join(sorted(m.group(1).split(","))) == want:
            return float(m.group(2))
    return 0.0


async def _seed(fake: FakePaidResultStorage, org: str, records: list[str]) -> None:
    store = _store(fake)
    for rec in records:
        await store.put(org, rec, PATH, b"x")


async def test_a_batch_purges_every_record_in_one_request_and_counts_it(storage: FakePaidResultStorage) -> None:
    ids = [f"rec-{i}" for i in range(30)]
    await _seed(storage, "org-1", ids)
    await _seed(storage, "org-2", ["rec-0"])

    outcomes = await _store(storage).purge_records("org-1", ids)

    assert outcomes == dict.fromkeys(ids)
    assert [c for c in storage.calls if c[0] in ("purge_batch", "purge_record")] == [("purge_batch", "org-1")]
    assert storage.batch_bodies == [{"recordIds": ids}]          # no org in the body: it is the token's
    assert all(storage.stored("org-1", r) == [] for r in ids)
    assert storage.stored("org-2", "rec-0") == [PATH]
    assert _metric("pipeshub_paid_purge_storage_requests_total", route="batch") == 1
    assert _metric("pipeshub_paid_purge_storage_requests_total", route="record") == 0
    assert _metric("pipeshub_paid_purge_records_total", route="batch", outcome="purged") == 30


async def test_more_than_the_batch_bound_goes_in_several_requests_never_over_the_bound(storage: FakePaidResultStorage) -> None:
    ids = [f"rec-{i}" for i in range(PURGE_BATCH_MAX * 2 + 7)]

    outcomes = await _store(storage).purge_records("org-1", ids)

    assert set(outcomes) == set(ids) and not any(outcomes.values())
    assert [len(b["recordIds"]) for b in storage.batch_bodies] == [PURGE_BATCH_MAX, PURGE_BATCH_MAX, 7]
    assert _metric("pipeshub_paid_purge_storage_requests_total", route="batch") == 3


async def test_a_partial_failure_is_reported_per_id_and_the_others_are_purged(storage: FakePaidResultStorage) -> None:
    ids = ["a", "b", "c"]
    await _seed(storage, "org-1", ids)
    storage.batch_failing = {"b"}

    outcomes = await _store(storage).purge_records("org-1", ids)

    assert outcomes["a"] is None and outcomes["c"] is None
    assert isinstance(outcomes["b"], PaidResultStoreError)
    assert storage.stored("org-1", "a") == [] and storage.stored("org-1", "b") == [PATH]
    assert _metric("pipeshub_paid_purge_records_total", route="batch", outcome="failed") == 1


async def test_an_id_the_reply_leaves_out_is_a_failure_not_a_success(storage: FakePaidResultStorage) -> None:
    store = _store(storage)

    async def short(*_a: object, **_k: object) -> dict[str, object]:
        return {"results": [{"recordId": "a", "purged": True}]}

    store._call = short  # type: ignore[method-assign]
    outcomes = await store.purge_records("org-1", ["a", "b"])
    assert outcomes["a"] is None and isinstance(outcomes["b"], PaidResultStoreError)

    async def garbage(*_a: object, **_k: object) -> dict[str, object]:
        return {"results": "nope"}

    store._call = garbage  # type: ignore[method-assign]
    assert all(isinstance(e, PaidResultStoreError) for e in (await store.purge_records("org-1", ["a"])).values())


async def test_a_404_falls_back_to_one_purge_per_record_and_keeps_the_cache_on(
    storage: FakePaidResultStorage, caplog: pytest.LogCaptureFixture,
) -> None:
    ids = [f"rec-{i}" for i in range(12)]
    await _seed(storage, "org-1", ids)
    storage.batch_present = False

    with caplog.at_level(logging.WARNING):
        outcomes = await _store(storage).purge_records("org-1", ids)

    assert outcomes == dict.fromkeys(ids)
    assert all(storage.stored("org-1", r) == [] for r in ids)
    assert [c[0] for c in storage.calls].count("purge_record") == 12
    # The cache is NOT switched off (a missing newer route says nothing about the cache), and the miss warns once.
    assert store_module.PAID_RESULT_ROUTES.available
    assert len([r for r in caplog.records if "batch purge route" in r.message]) == 1
    assert _metric("pipeshub_paid_purge_storage_requests_total", route="record") == 12
    assert _metric("pipeshub_paid_purge_storage_requests_total", route="batch") == 1        # the probe


async def test_the_fallback_is_not_latched_it_asks_again_after_a_backoff_and_returns_to_the_batch(
    storage: FakePaidResultStorage, clock: list[float],
) -> None:
    store = _store(storage)
    storage.batch_present = False
    await store.purge_records("org-1", ["a"])
    assert len(storage.batch_bodies) == 1

    # Inside the pause the route is not asked again.
    clock[0] += BATCH_REPROBE_BASE_S - 1
    await store.purge_records("org-1", ["b"])
    assert len(storage.batch_bodies) == 1

    # After it the route is probed; the storage service has been upgraded meanwhile: the batch route is back.
    clock[0] += 2
    storage.batch_present = True
    await store.purge_records("org-1", ["c"])
    assert len(storage.batch_bodies) == 2 and storage.batch_bodies[-1] == {"recordIds": ["c"]}
    assert store_module.PURGE_BATCH_ROUTE.usable()
    await store.purge_records("org-1", ["d"])
    assert len(storage.batch_bodies) == 3


async def test_the_pause_doubles_while_the_route_stays_missing_and_is_capped(
    storage: FakePaidResultStorage, clock: list[float],
) -> None:
    store = _store(storage)
    storage.batch_present = False
    pauses = []
    for _ in range(8):
        before = clock[0]
        await store.purge_records("org-1", ["a"])
        pauses.append(store_module.PURGE_BATCH_ROUTE._retry_at - before)
        clock[0] = store_module.PURGE_BATCH_ROUTE._retry_at
    assert pauses[:3] == [BATCH_REPROBE_BASE_S, BATCH_REPROBE_BASE_S * 2, BATCH_REPROBE_BASE_S * 4]
    assert max(pauses) == BATCH_REPROBE_MAX_S <= 600           # never anywhere near the 600 s cache latch


@pytest.mark.parametrize("status", [500, 503, 401, 400])
async def test_any_other_error_is_a_retryable_failure_for_the_chunk_not_a_fallback(
    storage: FakePaidResultStorage, status: int,
) -> None:
    ids = ["a", "b"]
    await _seed(storage, "org-1", ids)
    storage.batch_error = status

    outcomes = await _store(storage).purge_records("org-1", ids)

    assert set(outcomes) == set(ids) and all(isinstance(e, Exception) for e in outcomes.values())
    assert [c[0] for c in storage.calls].count("purge_record") == 0          # no per-record loop on an error
    assert storage.stored("org-1", "a") == [PATH]
    assert store_module.PURGE_BATCH_ROUTE.usable() and store_module.PAID_RESULT_ROUTES.available


async def test_after_an_error_the_next_call_still_uses_the_batch_route(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    storage.batch_error = 503
    await store.purge_records("org-1", ["a"])
    storage.batch_error = None
    assert await store.purge_records("org-1", ["a"]) == {"a": None}
    assert [c[0] for c in storage.calls].count("purge_record") == 0


async def test_a_failing_record_in_the_fallback_is_reported_for_that_record_only(storage: FakePaidResultStorage) -> None:
    storage.batch_present = False

    class Flaky(NodePaidResultStore):
        async def purge_record(self, org_id: str, record_id: str, *, before_ms: int | None = None) -> None:
            if record_id == "bad":
                raise ConnectionError("storage down")

    outcomes = await Flaky(storage.blob_storage()).purge_records("org-1", ["ok", "bad"])
    assert outcomes["ok"] is None and isinstance(outcomes["bad"], ConnectionError)


async def test_duplicates_are_asked_once_and_an_empty_list_makes_no_request(storage: FakePaidResultStorage) -> None:
    assert await _store(storage).purge_records("org-1", []) == {}
    assert await _store(storage).purge_records("org-1", ["a", "a"]) == {"a": None}
    assert storage.batch_bodies == [{"recordIds": ["a"]}]


async def test_the_cache_drops_ids_no_result_can_be_stored_under_and_never_sends_them(storage: FakePaidResultStorage) -> None:
    cache = PaidResultCache(_store(storage))
    ids = ["good", "../x", "org", "a/b", ""]

    outcomes = await cache.purge_records("org-1", ids)

    assert outcomes == dict.fromkeys(ids)
    assert storage.batch_bodies == [{"recordIds": ["good"]}]


async def test_the_pause_never_overflows_however_long_the_route_stays_missing() -> None:
    for _ in range(1100):
        store_module.PURGE_BATCH_ROUTE.missing()
    assert store_module.PURGE_BATCH_ROUTE._retry_at - store_module.PURGE_BATCH_ROUTE._clock() <= BATCH_REPROBE_MAX_S


async def test_a_batch_probe_the_storage_service_answers_404_is_counted_as_a_batch_request(storage: FakePaidResultStorage) -> None:
    storage.batch_present = False
    await _store(storage).purge_records("org-1", ["a"])
    assert _metric("pipeshub_paid_purge_storage_requests_total", route="batch") == 1
    assert _metric("pipeshub_paid_purge_storage_requests_total", route="record") == 1


async def test_the_batch_deadline_covers_the_retries_and_ends_in_a_failure_of_the_chunk(
    storage: FakePaidResultStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    store = _store(storage)

    async def stalls(*_a: object, **_k: object) -> dict[str, object]:
        await asyncio.sleep(30)
        return {}

    store._call = stalls  # type: ignore[method-assign]
    monkeypatch.setattr(store_module, "PURGE_BATCH_TIMEOUT_S", 0.05)
    outcomes = await store.purge_records("org-1", ["a", "b"])
    assert all(isinstance(e, asyncio.TimeoutError) for e in outcomes.values()) and set(outcomes) == {"a", "b"}
    assert store_module.PURGE_BATCH_ROUTE.usable()          # a stall is no evidence the route is missing
