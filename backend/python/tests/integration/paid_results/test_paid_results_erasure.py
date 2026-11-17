"""Record and org purges against real storage: only the named folder goes, and a repeat or an empty one is a success."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from tests.support.paid_results_it import new_org

if TYPE_CHECKING:
    from app.services.paid_cache.store import NodePaidResultStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(120),
    pytest.mark.skipif(not os.environ.get("ARTIFACTS_IT_STORAGE_URL"), reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]


def _key(org: str, record: str, n: int, kind: str = "ocr_page") -> CacheKey:
    return CacheKey(org, record, kind, 1, "model", "prompt", "params", f"input-{n}")


async def _fill(store: NodePaidResultStore, org: str, record: str, n: int = 3) -> list[CacheKey]:
    cache = PaidResultCache(store)
    keys = [_key(org, record, i) for i in range(n)]
    for key in keys:
        async def compute(k: CacheKey = key) -> str:
            return f"result of {k.input_digest}"

        await cache.get_or_compute(key, compute)
    return keys


async def _listed(store: NodePaidResultStore, org: str, record: str) -> list[str]:
    return [f.path for f in await store.list_files(org, record)]


async def test_record_and_org_purge(store: NodePaidResultStore) -> None:
    org, other = new_org(), new_org()
    a = await _fill(store, org, "rec-a")
    b = await _fill(store, org, "rec-b")
    c = await _fill(store, other, "rec-a")
    # The record's manifests sit in its folder and go with it.
    await store.replace(org, "rec-a", "manifests/rev1/parse.json", b"{}")

    await store.purge_record(org, "rec-a")

    assert await _listed(store, org, "rec-a") == []
    assert await _listed(store, org, "rec-b") == sorted(k.relative_path() for k in b)
    assert await _listed(store, other, "rec-a") == sorted(k.relative_path() for k in c)
    assert a and await store.get(org, "rec-a", a[0].relative_path()) is None

    await store.purge_record(org, "rec-a")           # a repeat (the broker redelivered): still a success
    await store.purge_record(org, "never-stored")    # nothing there: a success too

    await store.purge_org(org)

    assert await _listed(store, org, "rec-b") == []
    assert await _listed(store, other, "rec-a") == sorted(k.relative_path() for k in c)  # another org is untouched
    await store.purge_org(org)                       # and so is a repeat


async def test_a_purged_record_is_paid_for_again_not_served_stale(store: NodePaidResultStore) -> None:
    org = new_org()
    cache = PaidResultCache(store)
    key = _key(org, "rec-x", 1)
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        return f"paid {calls}"

    assert await cache.get_or_compute(key, compute) == "paid 1"
    assert await cache.get_or_compute(key, compute) == "paid 1"
    await store.purge_record(org, "rec-x")
    assert await cache.get_or_compute(key, compute) == "paid 2"
    assert calls == 2


async def test_the_listing_pages_through_more_files_than_one_page(store: NodePaidResultStore) -> None:
    org = new_org()
    for i in range(7):
        await store.put(org, "rec-p", f"ocr_page/{i:064x}.json", b"{}")

    assert len(await store.list_files(org, "rec-p")) == 7
    assert [f.path for f in await store.list_files(org, "rec-p", "ocr_page/")] == [f"ocr_page/{i:064x}.json" for i in range(7)]
    assert await store.list_files(org, "rec-p", "classify/") == []
