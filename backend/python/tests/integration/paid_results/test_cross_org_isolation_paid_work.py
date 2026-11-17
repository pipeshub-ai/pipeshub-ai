"""Two orgs with identical corpora, against real storage: nothing is shared and each pays its own calls."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest

from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from tests.support.paid_results_it import new_org

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from app.services.paid_cache.store import NodePaidResultStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(300),
    pytest.mark.skipif(not os.environ.get("ARTIFACTS_IT_STORAGE_URL"), reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

FILES = 200   # the oracle's size: 2 orgs x 200 files, 200 paid each, 0 shared (QA ran it on all three stores)


async def test_identical_corpora_share_nothing(store: NodePaidResultStore) -> None:
    org_a, org_b = new_org(), new_org()
    cache = PaidResultCache(store)
    paid: dict[str, int] = {org_a: 0, org_b: 0}

    async def index_file(org: str, n: int) -> None:
        key = CacheKey(org, "record-1", "ocr_page", 1, "model", "prompt", "params", f"identical-file-{n}")

        async def compute() -> str:
            paid[org] += 1
            return f"text of file {n}"

        assert await cache.get_or_compute(key, compute) == f"text of file {n}"

    work = [index_file(org, n) for n in range(FILES) for org in (org_a, org_b)]
    sem = asyncio.Semaphore(8)

    async def bounded(coro: Coroutine[object, object, None]) -> None:  # type: ignore[no-untyped-def]
        async with sem:
            await coro

    await asyncio.gather(*(bounded(w) for w in work))

    assert paid == {org_a: FILES, org_b: FILES}            # each org paid for every file itself
    listed_a = {f.path for f in await store.list_files(org_a, "record-1")}
    listed_b = {f.path for f in await store.list_files(org_b, "record-1")}
    assert len(listed_a) == len(listed_b) == FILES
    assert listed_a.isdisjoint(listed_b)                   # not one shared object name
    # A second pass over the same corpus pays nothing, per org.
    await asyncio.gather(*(bounded(index_file(org, n)) for n in range(FILES) for org in (org_a, org_b)))
    assert paid == {org_a: FILES, org_b: FILES}


async def test_another_orgs_token_cannot_read_a_result_by_its_path(store: NodePaidResultStore) -> None:
    org_a, org_b = new_org(), new_org()
    key = CacheKey(org_a, "rec", "ocr_page", 1, "m", "p", "q", "secret page")
    await store.put(org_a, "rec", key.relative_path(), b'{"v":1,"kind":"ocr_page","value":"A only"}')

    assert await store.get(org_b, "rec", key.relative_path()) is None
    assert await store.list_files(org_b, "rec") == []
    await store.delete_files(org_b, "rec", [key.relative_path()])          # org B's delete reaches org B's folder only
    assert await store.get(org_a, "rec", key.relative_path()) is not None
