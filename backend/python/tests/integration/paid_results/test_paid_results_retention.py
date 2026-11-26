"""Retention by reference against real storage, with the sweep's clock moved to simulate days."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

from app.services.paid_cache.retention import sweep_record
from app.services.paid_cache.run import OPEN_PROTECTS_MS, RunScope
from tests.support.paid_results_it import new_org

if TYPE_CHECKING:
    from app.services.paid_cache.store import NodePaidResultStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(300),
    pytest.mark.skipif(not os.environ.get("ARTIFACTS_IT_STORAGE_URL"), reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

HOUR = 3600 * 1000
REC = "rec-r"


def _obj(n: int) -> str:
    return f"ocr_page/{n:064x}.json"


async def _revision(store: NodePaidResultStore, org: str, rev: str, objects: list[int]) -> None:
    run = RunScope(org, REC, rev, "parse")
    for n in objects:
        await store.put(org, REC, _obj(n), b"{}")
        await run.note(store, _obj(n), opens=True)
    await run.finish(store)


async def _results(store: NodePaidResultStore, org: str) -> list[str]:
    return [f.path for f in await store.list_files(org, REC, "ocr_page/")]


async def test_two_newest_revisions_kept(store: NodePaidResultStore) -> None:
    org = new_org()
    for i, rev in enumerate(("r1", "r2", "r3", "r4"), start=1):
        await _revision(store, org, rev, [1, 2, 10 + i])
        time.sleep(0.05)

    result = await sweep_record(store, org, REC, now_ms=int(time.time() * 1000) + 3 * HOUR)

    assert await _results(store, org) == sorted([_obj(1), _obj(2), _obj(13), _obj(14)])
    manifests = [f.path for f in await store.list_files(org, REC, "manifests/")]
    assert manifests == ["manifests/r3/parse.json", "manifests/r4/parse.json"]
    assert result.deleted == 4


async def test_sweep_spares_an_in_flight_revision(store: NodePaidResultStore) -> None:
    org = new_org()
    await _revision(store, org, "r1", [1])
    time.sleep(0.05)
    await _revision(store, org, "r2", [2])
    time.sleep(0.05)
    await _revision(store, org, "r3", [3])
    # The next revision is mid-parse: 40 pages stored, the open manifest flushed at 30.
    inflight = RunScope(org, REC, "r4", "parse")
    for n in range(1000, 1040):
        await store.put(org, REC, _obj(n), b"{}")
        if n < 1030:
            await inflight.note(store, _obj(n), opens=True)
    await inflight.flush(store)

    # R3's sweep runs now (the real clock): the open manifest covers 30, the grace covers the last 10.
    await sweep_record(store, org, REC)
    assert {_obj(n) for n in range(1000, 1040)} <= set(await _results(store, org))

    # Three hours on, the grace has passed: the 30 the open manifest lists are still protected (it is under
    # 24 h old), and R1, beyond the two newest revisions, goes.
    await sweep_record(store, org, REC, now_ms=int(time.time() * 1000) + 3 * HOUR)
    survivors = set(await _results(store, org))
    assert {_obj(n) for n in range(1000, 1030)} <= survivors
    assert _obj(1) not in survivors
    assert {_obj(2), _obj(3)} <= survivors


async def test_a_failed_run_keeps_its_objects_until_two_newer_revisions_complete(store: NodePaidResultStore) -> None:
    org = new_org()
    await _revision(store, org, "r1", [1])
    time.sleep(0.05)
    failed = RunScope(org, REC, "r2", "parse")
    for n in (20, 21):
        await store.put(org, REC, _obj(n), b"{}")
        await failed.note(store, _obj(n), opens=True)
    await failed.flush(store)
    now = int(time.time() * 1000)

    await sweep_record(store, org, REC, now_ms=now + 3 * HOUR)
    assert {_obj(20), _obj(21)} <= set(await _results(store, org))

    await sweep_record(store, org, REC, now_ms=now + OPEN_PROTECTS_MS + 3 * HOUR)   # the open manifest is stale
    assert {_obj(20), _obj(21)} <= set(await _results(store, org))                  # fewer than two newer completed revisions

    for rev, n in (("r3", 3), ("r4", 4)):
        time.sleep(0.05)
        await _revision(store, org, rev, [n])
    await sweep_record(store, org, REC, now_ms=int(time.time() * 1000) + OPEN_PROTECTS_MS + 3 * HOUR)
    assert await _results(store, org) == sorted([_obj(3), _obj(4)])


async def test_revisions_written_within_one_storage_second_are_ordered_by_their_manifests(store: NodePaidResultStore) -> None:
    """QA-S7a-1: Azure and S3 list write times in whole seconds, so five quick revisions tie on them."""
    org = new_org()
    base = int(time.time() * 1000)
    for i in range(1, 6):
        run = RunScope(org, REC, f"r{i}", "parse", clock=lambda i=i: (base + i * 10) / 1000)
        await store.put(org, REC, _obj(i), b"{}")
        await run.note(store, _obj(i), opens=True)
        await run.finish(store)

    await sweep_record(store, org, REC, now_ms=base + 3 * HOUR)

    assert await _results(store, org) == sorted([_obj(4), _obj(5)])
    assert [f.path for f in await store.list_files(org, REC, "manifests/")] == ["manifests/r4/parse.json", "manifests/r5/parse.json"]


async def test_a_record_delete_while_a_parse_is_paying_leaves_nothing(store: NodePaidResultStore) -> None:
    """QA-S7a-2 on real storage: the late result and the late manifest of a deleted record never land."""
    from app.services.paid_cache.cache import PaidResultCache
    from app.services.paid_cache.keys import CacheKey

    org = new_org()
    cache = PaidResultCache(store)
    run = RunScope(org, REC, "rev", "parse")
    await cache.store.put(org, REC, _obj(1), b"{}")                      # a page stored before the delete
    await run.note(cache.store, _obj(1), opens=True)
    await cache.delete_record(org, REC)

    key = CacheKey(org, REC, "ocr_page", 1, "m", "p", "q", "late")
    assert await cache.get_or_compute(key, _late) == "late page"          # the parse itself is not failed
    await run.finish(cache.store)

    assert await store.list_files(org, REC) == []


async def _late() -> str:
    return "late page"
