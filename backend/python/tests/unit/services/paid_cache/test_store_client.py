"""``NodePaidResultStore`` against a local stand-in for the storage service."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.modules.artifacts.store import ArtifactRoutesUnavailable
from app.services.paid_cache import store as store_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.store import (
    MAX_FILES_PER_CALL,
    NodePaidResultStore,
    PaidResultStoreError,
)
from tests.support.fake_paid_result_storage import SIGNATURE, FakePaidResultStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

DIGEST = "a" * 64
PATH = f"ocr_page/{DIGEST}.json"


@pytest.fixture(autouse=True)
def _routes_available() -> None:
    store_module.PAID_RESULT_ROUTES.reset()


@pytest.fixture
async def storage() -> AsyncIterator[FakePaidResultStorage]:
    fake = FakePaidResultStorage()
    await fake.start()
    yield fake
    await fake.stop()


def _store(fake: FakePaidResultStorage, logger: logging.Logger | None = None) -> NodePaidResultStore:
    return NodePaidResultStore(fake.blob_storage(), logger or logging.getLogger("paid-store-test"))


async def test_put_is_conditional_and_the_first_writer_wins(storage: FakePaidResultStorage) -> None:
    store = _store(storage)

    assert await store.put("org-1", "rec-1", PATH, b"first") is True
    assert await store.put("org-1", "rec-1", PATH, b"second") is False

    assert await store.get("org-1", "rec-1", PATH) == b"first"
    # The signed headers went out exactly as given, including the condition.
    assert all(h.get("If-None-Match") == "*" for h in storage.put_headers)


async def test_replace_stores_over_the_earlier_file(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    await store.replace("org-1", "rec-1", "manifests/rev1/parse.open.json", b"v1")
    await store.replace("org-1", "rec-1", "manifests/rev1/parse.open.json", b"v2")

    assert await store.get("org-1", "rec-1", "manifests/rev1/parse.open.json") == b"v2"
    assert not any("If-None-Match" in h for h in storage.put_headers)


async def test_overwrite_signs_the_refresh_mode_for_that_key_and_stores_over_a_result(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    assert await store.put("org-1", "rec-1", PATH, b"unreadable") is True
    await store.overwrite("org-1", "rec-1", PATH, b"repaired")

    assert await store.get("org-1", "rec-1", PATH) == b"repaired"
    modes = [(f["path"], f["mode"]) for call in storage.presign_calls for f in call["files"]]
    assert (PATH, "refresh") in modes and (PATH, "replace") not in modes
    assert storage.put_headers[-1].get("If-None-Match") is None


async def test_a_missing_file_is_none_whether_storage_says_404_or_403(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    for status in (404, 403):
        storage.missing_status = status
        assert await store.get("org-1", "rec-1", PATH) is None


async def test_an_org_sees_only_its_own_files(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    await store.put("org-1", "rec-1", PATH, b"org one")

    assert await store.get("org-2", "rec-1", PATH) is None
    assert await store.list_files("org-2", "rec-1") == []
    assert [f.path for f in await store.list_files("org-1", "rec-1")] == [PATH]


async def test_old_storage_turns_cache_off(storage: FakePaidResultStorage, caplog: pytest.LogCaptureFixture) -> None:
    storage.presign_status = 404
    store = _store(storage)

    with caplog.at_level(logging.WARNING, logger="paid-store-test"):
        for _ in range(3):
            with pytest.raises(ArtifactRoutesUnavailable):
                await store.get("org-1", "rec-1", PATH)

    assert not store_module.PAID_RESULT_ROUTES.available
    assert len([r for r in caplog.records if "no paid-result routes" in r.message]) == 1


async def test_the_cache_stops_asking_while_off_and_asks_again_after_ten_minutes(
    storage: FakePaidResultStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(store_module, "PAID_RESULT_ROUTES", store_module._Availability(lambda: now[0]))
    monkeypatch.setattr("app.services.paid_cache.cache.PAID_RESULT_ROUTES", store_module.PAID_RESULT_ROUTES)
    storage.presign_status = 405
    cache = PaidResultCache(_store(storage))
    key = CacheKey("org-1", "rec-1", "ocr_page", 1, "m", "p", "q", "i")
    calls = 0

    async def compute() -> str:
        nonlocal calls
        calls += 1
        return "paid"

    assert await cache.get_or_compute(key, compute) == "paid"   # the route is missing: computed, cache goes off
    presigns = len(storage.presign_calls)
    assert await cache.get_or_compute(key, compute) == "paid"   # off: not even asked
    assert len(storage.presign_calls) == presigns
    assert calls == 2

    now[0] += store_module.OFF_SECONDS + 1
    storage.presign_status = 200                                 # storage upgraded meanwhile
    assert await cache.get_or_compute(key, compute) == "paid"
    assert await cache.get_or_compute(key, compute) == "paid"
    assert calls == 3                                            # the second reuses the stored result


async def test_presign_splits_at_the_per_call_cap(storage: FakePaidResultStorage) -> None:
    files = [(f"ocr_page/{i:064x}.json", "get") for i in range(MAX_FILES_PER_CALL * 2 + 1)]

    signed = await _store(storage).presign("org-1", "rec-1", files)

    assert [len(c["files"]) for c in storage.presign_calls] == [1000, 1000, 1]
    assert len(signed) == len(files)


async def test_listing_follows_the_cursor_over_every_page(storage: FakePaidResultStorage) -> None:
    storage.list_page = 2
    store = _store(storage)
    for i in range(5):
        await store.put("org-1", "rec-1", f"ocr_page/{i:064x}.json", b"x")

    listed = await store.list_files("org-1", "rec-1", "ocr_page/")

    assert [f.path for f in listed] == [f"ocr_page/{i:064x}.json" for i in range(5)]
    assert all(f.size == 1 and f.last_modified_ms == storage.clock_ms for f in listed)
    assert storage.calls.count(("list", "org-1")) == 3


async def test_delete_files_and_purges(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    for rec in ("rec-1", "rec-2"):
        for i in range(3):
            await store.put("org-1", rec, f"ocr_page/{i:064x}.json", b"x")
    await store.put("org-2", "rec-1", PATH, b"other org")

    await store.delete_files("org-1", "rec-1", [f"ocr_page/{0:064x}.json"])
    assert len(storage.stored("org-1", "rec-1")) == 2
    await store.purge_record("org-1", "rec-1")
    assert storage.stored("org-1", "rec-1") == []
    assert len(storage.stored("org-1", "rec-2")) == 3
    await store.purge_org("org-1")
    assert storage.stored("org-1", "rec-2") == []
    assert storage.stored("org-2", "rec-1") == [PATH]


async def test_purging_against_storage_without_the_routes_is_done(storage: FakePaidResultStorage) -> None:
    # A storage service without the routes never held a result, so there is nothing to remove.
    class Missing(NodePaidResultStore):
        async def _call(self, org_id: str, method: str, route: str, **kw: object) -> dict[str, object]:
            raise ArtifactRoutesUnavailable(404)

    missing = Missing(storage.blob_storage())
    await missing.purge_record("org-1", "rec-1")
    await missing.purge_org("org-1")


async def test_a_storage_failure_on_a_purge_raises_so_the_broker_retries(storage: FakePaidResultStorage) -> None:
    class Down(NodePaidResultStore):
        async def _call(self, org_id: str, method: str, route: str, **kw: object) -> dict[str, object]:
            raise ConnectionError("storage down")

    with pytest.raises(ConnectionError):
        await Down(storage.blob_storage()).purge_record("org-1", "rec-1")


async def test_a_malformed_presign_reply_is_an_error_without_the_signed_url(storage: FakePaidResultStorage) -> None:
    store = _store(storage)

    async def bad(*_a: object, **_k: object) -> dict[str, object]:
        return {"files": [{"path": PATH, "mode": "get", "url": f"http://x/?sig={SIGNATURE}", "headers": "nope"}]}

    store._call = bad  # type: ignore[method-assign]
    with pytest.raises(PaidResultStoreError) as raised:
        await store.presign("org-1", "rec-1", [(PATH, "get")])
    assert SIGNATURE not in str(raised.value)
