"""An unreadable stored object is paid once and replaced; a deleted record takes no late writes; ids are validated once."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.core.org_context import spawn_detached  # noqa: F401  (imported so a missing seam fails here)
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import InProcessLocks
from app.services.paid_cache.run import RunScope
from tests.support.paid_cache import CountingModel, MemoryResultStore, record_in_flight

KEY = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")


def _paid() -> tuple[list[int], object]:
    calls: list[int] = []

    async def compute() -> str:
        calls.append(1)
        return "page text"

    return calls, compute


# -- QA-S7a-4: bytes that cannot be read are replaced on the next compute -----------------------------------

@pytest.mark.parametrize("raw", [
    b'{"v":1,"kind":"ocr_page","val', b"\x00\xff", b"[]",
    b'{"v":2,"kind":"ocr_page","value":"x"}', b'{"v":1,"kind":"image_description","value":"x"}',
])
async def test_an_unreadable_object_is_paid_once_then_replaced(raw: bytes) -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    calls, compute = _paid()
    store.files[("org-1", "rec-1", KEY.relative_path())] = (raw, store.now_ms)

    results = [await cache.get_or_compute(KEY, compute) for _ in range(4)]  # type: ignore[arg-type]

    assert results == ["page text"] * 4
    assert len(calls) == 1                                  # paid once; the 3 runs after it hit
    assert json.loads(store.files[("org-1", "rec-1", KEY.relative_path())][0])["value"] == "page text"
    assert store.ops["overwrite"] == 1 and store.ops["put"] == 0


async def test_a_replace_is_for_that_key_only_and_a_good_object_is_never_overwritten() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    calls, compute = _paid()
    await cache.get_or_compute(KEY, compute)  # type: ignore[arg-type]
    other = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "other")
    store.files[("org-1", "rec-1", other.relative_path())] = (b"junk", store.now_ms)

    await cache.get_or_compute(other, compute)  # type: ignore[arg-type]

    assert store.ops["overwrite"] == 1
    assert json.loads(store.files[("org-1", "rec-1", KEY.relative_path())][0])["value"] == "page text"


async def test_a_value_the_caller_cannot_decode_is_a_miss_and_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    calls, compute = _paid()

    def strict(value: object) -> None:
        if value != "page text":
            raise ValueError("schema")

    store.files[("org-1", "rec-1", KEY.relative_path())] = (b'{"v":1,"kind":"ocr_page","value":"old shape"}', 1)
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert await cache.get_or_compute(KEY, compute, validate=strict) == "page text"  # type: ignore[arg-type]

    assert len(calls) == 1
    assert sum("no longer decodes" in r.getMessage() for r in caplog.records) == 1


async def test_a_refresh_run_overwrites_its_results_instead_of_keeping_the_first_writers() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    store.files[("org-1", "rec-1", KEY.relative_path())] = (b'{"v":1,"kind":"ocr_page","value":"stale"}', 1)
    calls, compute = _paid()
    from app.services.paid_cache.run import begin_run, clear_run

    with record_in_flight("org-1", "rec-1"):
        begin_run("org-1", "rec-1", None, "index", refresh=True)
        try:
            assert await cache.get_or_compute(KEY, compute) == "page text"  # type: ignore[arg-type]
        finally:
            clear_run()

    assert json.loads(store.files[("org-1", "rec-1", KEY.relative_path())][0])["value"] == "page text"
    assert store.ops["put"] == 0


# -- QA-S7a-2: a delete racing a running parse leaves nothing behind ------------------------------------------

async def test_a_late_write_after_a_record_delete_is_refused_and_a_landed_one_removed() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    gate = asyncio.Event()
    paid = {"n": 0}

    async def compute() -> str:
        paid["n"] += 1
        await gate.wait()
        return "late page"

    run = RunScope("org-1", "rec-1", "rev", "parse")
    with record_in_flight("org-1", "rec-1"):
        import app.services.paid_cache.run as run_module
        run_module._run.set(run)
        task = asyncio.create_task(cache.get_or_compute(KEY, compute))
        await asyncio.sleep(0.05)
        await cache.delete_record("org-1", "rec-1")          # the deleteRecord handler, while the parse is still paying
        gate.set()
        assert await task == "late page"                      # the parse itself is not failed
        await run.finish(cache.store)                         # nor is its manifest allowed to land

    assert store.paths("org-1", "rec-1") == []
    assert store.paths("org-1", "rec-2") == []


async def test_a_write_in_flight_when_the_marker_is_set_is_swept_after_it_lands() -> None:
    class SlowPut(MemoryResultStore):
        def __init__(self) -> None:
            super().__init__()
            self.landing = asyncio.Event()
            self.go = asyncio.Event()

        async def put(self, org_id: str, record_id: str, path: str, data: bytes) -> bool:
            self.landing.set()
            await self.go.wait()
            return await super().put(org_id, record_id, path, data)

    store = SlowPut()
    cache = PaidResultCache(store)
    _, compute = _paid()
    task = asyncio.create_task(cache.get_or_compute(KEY, compute))  # type: ignore[arg-type]
    await store.landing.wait()                                       # past the marker check, not yet written
    await cache.delete_record("org-1", "rec-1")
    store.go.set()
    await task

    assert store.paths("org-1", "rec-1") == []


async def test_an_org_delete_fences_every_record_of_the_org_and_nothing_of_another() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    calls, compute = _paid()
    await cache.purge_org("org-1")
    await cache.get_or_compute(KEY, compute)  # type: ignore[arg-type]
    mine = CacheKey("org-2", "rec-1", "ocr_page", 1, "m", "p", "q", "i")
    await cache.get_or_compute(mine, compute)  # type: ignore[arg-type]

    assert store.paths("org-1", "rec-1") == []
    assert store.results("org-2", "rec-1") != []


async def test_a_refresh_purge_does_not_fence_the_record() -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    calls, compute = _paid()
    await cache.purge_record("org-1", "rec-1")                       # refreshPaidOutputs: the record goes on
    await cache.get_or_compute(KEY, compute)  # type: ignore[arg-type]
    assert store.results("org-1", "rec-1") != []


async def test_an_unreadable_marker_does_not_stop_a_write() -> None:
    """D-46: Redis is never what correctness rests on; a lock service that is down leaves results durable."""
    class Down(InProcessLocks):
        async def is_marked(self, name: str) -> bool:
            raise ConnectionError("redis down")

    store = MemoryResultStore()
    cache = PaidResultCache(store, Down())
    calls, compute = _paid()
    await cache.get_or_compute(KEY, compute)  # type: ignore[arg-type]
    assert store.results("org-1", "rec-1") != []


# -- QA-S7a-12: record ids are validated once, at write -----------------------------------------------------------

@pytest.mark.parametrize("record_id", ["rec.1", "a:b", "x" * 129, "", "a/b", "../x"])
async def test_an_id_the_routes_cannot_hold_is_refused_at_write_and_a_delete_of_it_is_a_no_op(
    record_id: str, caplog: pytest.LogCaptureFixture,
) -> None:
    store = MemoryResultStore()
    cache = PaidResultCache(store)
    with record_in_flight("org-1", record_id), caplog.at_level(logging.WARNING):
        assert access.ambient_identity() is None                    # no key: its calls are made as before, uncached
        model = CountingModel("m")
        for _ in range(2):
            await access.cached_model_call(
                "table_summary", llm=model, template="t", rendered="r", encode=lambda v: v, decode=str,
                call=_one(model), config_service=None,
            )
    assert model.total == 2 and sum(store.ops.values()) == 0
    await cache.delete_record("org-1", record_id)                    # nothing could be stored: a success, no storage call
    await cache.purge_record("org-1", record_id)
    assert store.ops["purge"] == 0


def _one(model: CountingModel):  # noqa: ANN202
    async def call() -> str:
        await model.ainvoke("x")
        return "summary"

    return call


async def test_a_valid_uuid_record_id_is_accepted() -> None:
    with record_in_flight("org-1", "0b9d8c0e-6f3c-4c0e-9f64-3d8f1c3f6a11"):
        assert access.ambient_identity() == ("org-1", "0b9d8c0e-6f3c-4c0e-9f64-3d8f1c3f6a11")


# -- QA-S7a-10: the lock key carries the <ns>: prefix -----------------------------------------------------------------

async def test_the_redis_lock_and_marker_keys_carry_the_namespace() -> None:
    from app.services.paid_cache.locks import RedisLocks, deleted_name, lock_name

    seen: list[str] = []

    class Client:
        async def set(self, key: str, *_a: object, **_k: object) -> bool:
            seen.append(key)
            return True

        async def exists(self, key: str) -> int:
            seen.append(key)
            return 1

        async def eval(self, _lua: str, _n: int, key: str, *_a: object) -> int:
            seen.append(key)
            return 1

    locks = RedisLocks(Client(), "pipeshub-prod")  # type: ignore[arg-type]
    name = lock_name("org-1", "rec-1", "abc")
    await locks.acquire(name, 1000)
    await locks.renew(name, "t", 1000)
    await locks.release(name, "t")
    await locks.mark(deleted_name("org-1", "rec-1"), 1000)
    await locks.is_marked(deleted_name("org-1", "rec-1"))

    assert set(seen) == {"pipeshub-prod:pc:lock:{org-1:rec-1}:abc", "pipeshub-prod:pc:deleted:{org-1:rec-1}"}
    unnamespaced = RedisLocks(Client())  # type: ignore[arg-type]
    seen.clear()
    await unnamespaced.acquire(name, 1000)
    assert seen == ["pc:lock:{org-1:rec-1}:abc"]
