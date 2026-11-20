"""Against each real file store (local, MinIO, Azurite): the sweep deletes by listed version, a reuse bumps the
version with new bytes, and a bump racing the sweep, or finding the key gone, loses nothing.

Runs in the storage job of ``.github/workflows/backend-matrix.yml`` on every leg; that job fails on a skip. The
decisive cases fail on the first design (a copy in place keeps the S3 content-hash ETag) and on a delete that names
no version."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

import pytest

from app.services.paid_cache.bump import BumpEntry, BumpQueue, touched_bytes
from app.services.paid_cache.cache import _FencedStore
from app.services.paid_cache.retention import GRACE_MS, sweep_record
from app.services.paid_cache.run import RunScope
from app.services.paid_cache.store import BUMPED, CHANGED, GONE
from tests.support.paid_results_it import new_org

if TYPE_CHECKING:
    from app.services.paid_cache.store import NodePaidResultStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(300),
    pytest.mark.skipif(not os.environ.get("ARTIFACTS_IT_STORAGE_URL"), reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

REC = "rec-g35"
K = f"ocr_page/{1:064x}.json"
VALUE = b'{"v":1,"kind":"ocr_page","value":"OLD"}'


def _obj(n: int) -> str:
    return f"ocr_page/{n:064x}.json"


async def _revision(store: NodePaidResultStore, org: str, rev: str, n: int) -> None:
    run = RunScope(org, REC, rev, "parse")
    await store.put(org, REC, _obj(n), b"{}")
    await run.note(store, _obj(n), opens=True)
    await run.finish(store)


async def _candidate(store: NodePaidResultStore, org: str) -> None:
    """K stored, then two newer completed revisions that do not list it: the sweep (its clock moved on) deletes it."""
    await store.put(org, REC, K, VALUE)
    time.sleep(1.1)
    await _revision(store, org, "r2", 2)
    time.sleep(1.1)
    await _revision(store, org, "r3", 3)


def _later() -> int:
    return int(time.time() * 1000) + GRACE_MS + 60_000


async def _queue(store: NodePaidResultStore, org: str) -> BumpQueue:
    async def not_deleted(_o: str, _r: str) -> bool:
        return False

    async def no_wait(_s: float) -> None:
        return None

    fenced = _FencedStore(store, not_deleted, logging.getLogger("g35-it"))
    await fenced.presign(org, REC, [(K, "get")])                 # a hit's own read is where capabilities are learned
    return BumpQueue(fenced, sleep=no_wait, autostart=False)  # type: ignore[arg-type]


class _Racing:
    """The sweep's store, with a reuse's bump run at the worst point the store allows: between the last look and the
    delete when the store deletes by version (the delete is then refused), and just before the last look when it does
    not (the pre-delete listing then drops the object; a bump after that look is the documented residual)."""

    def __init__(self, store: NodePaidResultStore, run_bump: Any) -> None:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        self._store, self._run_bump, self._done, self._lists = store, run_bump, False, 0

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        return getattr(self._store, name)

    async def list_files(self, o: str, r: str, prefix: str = "") -> Any:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        self._lists += 1
        if self._lists == 3 and not self._store.supports_conditional_delete and not self._done:
            self._done = True
            await self._run_bump()
        return await self._store.list_files(o, r, prefix)

    async def delete_files(self, o: str, r: str, paths: list[str], etags: Any = None) -> list[str]:  # noqa: ANN401 - a JSON value or opaque signed-URL entry of any shape
        if self._store.supports_conditional_delete and not self._done:
            self._done = True
            await self._run_bump()
        return await self._store.delete_files(o, r, paths, etags)


async def test_the_storage_service_names_what_it_honours(store: NodePaidResultStore) -> None:
    org = new_org()
    await store.put(org, REC, K, VALUE)
    await store.list_files(org, REC)
    # Every store must honour the conditional bump. A conditional delete is honoured by Azure and local storage; a
    # MinIO build ignores If-Match on DeleteObject (recorded in the handoff), and the service then says so.
    assert "bump" in store.capabilities, "this store honours no conditional write (see the storage service log)"
    print(f"paid-result capabilities on this store: {sorted(store.capabilities)}")
    listed = await store.list_files(org, REC)
    assert all(f.etag for f in listed)


async def test_a_delete_naming_a_stale_version_keeps_the_object(store: NodePaidResultStore) -> None:
    org = new_org()
    await store.put(org, REC, K, VALUE)
    before = await store.get_versioned(org, REC, K)
    assert before is not None and before.etag
    signed = await store.presign(org, REC, [(K, "bump", before.etag)])
    outcome, _ = await store.bump_to(signed[(K, "bump")], touched_bytes(VALUE, int(time.time() * 1000)) or b"")
    assert outcome == BUMPED

    changed = await store.delete_files(org, REC, [K], {K: before.etag})

    after = await store.get_versioned(org, REC, K)
    if store.supports_conditional_delete:
        assert changed == [K], "the delete removed a version it was not named for"
        assert after is not None and after.etag != before.etag
    else:
        # The store honours no conditional delete: no ETag is sent, so the delete is unconditional, as before.
        assert changed == [] and after is None


async def test_a_bump_changes_the_etag_and_keeps_the_value(store: NodePaidResultStore) -> None:
    org = new_org()
    await store.put(org, REC, K, VALUE)
    first = await store.get_versioned(org, REC, K)
    assert first is not None and first.etag
    signed = await store.presign(org, REC, [(K, "bump", first.etag)])
    new = touched_bytes(first.data, int(time.time() * 1000)) or b""
    assert (await store.bump_to(signed[(K, "bump")], new))[0] == BUMPED
    second = await store.get_versioned(org, REC, K)
    assert second is not None and second.etag != first.etag and b'"value":"OLD"' in second.data
    # The old version no longer matches: a second bump with it is refused, not applied.
    again = await store.bump_to(signed[(K, "bump")], new)
    assert again[0] == CHANGED


async def test_a_bump_of_a_missing_object_is_not_applied(store: NodePaidResultStore) -> None:
    org = new_org()
    await store.put(org, REC, K, VALUE)
    got = await store.get_versioned(org, REC, K)
    assert got is not None and got.etag
    signed = await store.presign(org, REC, [(K, "bump", got.etag)])
    await store.delete_files(org, REC, [K])
    outcome, _ = await store.bump_to(signed[(K, "bump")], b'{"v":1}')
    # S3 and Azurite may answer 404 or 412 for a conditional write of a missing key (what the queue reads with one
    # more read): either way nothing was created.
    assert outcome in (GONE, CHANGED)
    assert await store.get_versioned(org, REC, K) is None


async def test_a_reuse_landing_between_the_second_listing_and_the_delete_keeps_the_object(store: NodePaidResultStore) -> None:
    org = new_org()
    await _candidate(store, org)
    got = await store.get_versioned(org, REC, K)
    assert got is not None and got.etag and got.last_modified_ms is not None
    queue = await _queue(store, org)

    async def run_bump() -> None:
        assert queue.offer(BumpEntry(org, REC, K, got.data, got.etag, got.last_modified_ms - 3 * 3_600_000, None)) == "queued"
        await queue.drain_once()

    result = await sweep_record(_Racing(store, run_bump), org, REC, now_ms=_later())  # type: ignore[arg-type]

    assert await store.get_versioned(org, REC, K) is not None, "the sweep deleted the version the reuse had just replaced"
    if store.supports_conditional_delete:
        assert result.changed == 1


async def test_an_object_deleted_before_the_bump_is_stored_again_with_the_held_bytes(store: NodePaidResultStore) -> None:
    org = new_org()
    await store.put(org, REC, K, VALUE)
    got = await store.get_versioned(org, REC, K)
    assert got is not None and got.etag and got.last_modified_ms is not None
    queue = await _queue(store, org)
    await store.delete_files(org, REC, [K])                        # the sweep won
    queue.offer(BumpEntry(org, REC, K, got.data, got.etag, got.last_modified_ms - 3 * 3_600_000, None))

    await queue.drain_once()

    again = await store.get_versioned(org, REC, K)
    assert again is not None and b'"value":"OLD"' in again.data and b"originMs" in again.data


async def test_a_refresh_marker_is_written_by_the_cutoff_and_survives_the_purge(store: NodePaidResultStore) -> None:
    org = new_org()
    assert await store.refresh_marker_ms(org, REC) is None
    await store.put(org, REC, K, VALUE)
    cutoff = await store.purge_cutoff(org, REC)
    assert cutoff is not None
    marker = await store.refresh_marker_ms(org, REC)
    assert marker is not None and marker // 1000 <= cutoff // 1000

    await store.purge_record(org, REC, before_ms=cutoff + 5_000)

    assert [f.path for f in await store.list_files(org, REC)] == [], "the purge left the result, or the listing shows the marker"
    assert await store.refresh_marker_ms(org, REC) == marker, "a cutoff purge removed the refresh marker"


async def test_a_bump_of_an_object_older_than_a_refresh_is_not_applied(store: NodePaidResultStore) -> None:
    org = new_org()
    await store.put(org, REC, K, VALUE)
    got = await store.get_versioned(org, REC, K)
    assert got is not None and got.etag and got.last_modified_ms is not None
    time.sleep(1.1)
    assert await store.purge_cutoff(org, REC) is not None          # a refresh began after the object was written
    queue = await _queue(store, org)
    queue.offer(BumpEntry(org, REC, K, got.data, got.etag, got.last_modified_ms, None))

    await queue.drain_once()

    after = await store.get_versioned(org, REC, K)
    assert after is not None and after.etag == got.etag, "a pre-refresh object was re-written past the refresh"


async def test_a_loop_of_reuses_against_a_sweeping_loop_loses_no_reused_object(store: NodePaidResultStore) -> None:
    """Many candidates, half of them reused (bumped) while the sweep runs: every reused object is still there."""
    org = new_org()
    keys = [_obj(100 + i) for i in range(40)]
    for k in keys:
        await store.put(org, REC, k, VALUE)
    time.sleep(1.1)
    await _revision(store, org, "r2", 2)
    time.sleep(1.1)
    await _revision(store, org, "r3", 3)
    reused = keys[::2]
    queue = await _queue(store, org)
    for k in reused:
        got = await store.get_versioned(org, REC, k)
        assert got is not None and got.etag and got.last_modified_ms is not None
        queue.offer(BumpEntry(org, REC, k, got.data, got.etag, got.last_modified_ms - 3 * 3_600_000, None))

    async def run_bumps() -> None:
        await queue.drain_once()

    await sweep_record(_Racing(store, run_bumps), org, REC, now_ms=_later())  # type: ignore[arg-type]

    present = {f.path for f in await store.list_files(org, REC, "ocr_page/")}
    assert set(reused) <= present, f"{len(set(reused) - present)} reused objects lost"
    assert not (set(keys) - set(reused)) & present, "an unused old object was kept"
