"""On the wire: the conditional delete, the version bump, the refresh marker and the rolling-deploy cases,
through ``NodePaidResultStore`` against the stand-in storage service (real sockets, signed URLs)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pytest

from app.services.paid_cache import store as store_module
from app.services.paid_cache.bump import BumpEntry, BumpQueue, touched_bytes
from app.services.paid_cache.retention import GRACE_MS, sweep_record
from app.services.paid_cache.run import RunScope
from app.services.paid_cache.store import (
    BUMPED,
    CHANGED,
    GONE,
    GuardUnsupported,
    NodePaidResultStore,
    PaidResultStoreError,
)
from tests.support.fake_paid_result_storage import FakePaidResultStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ORG, REC = "org-1", "rec-1"
HOUR = 3_600_000
K = f"ocr_page/{1:064x}.json"
OLD = b'{"v":1,"kind":"ocr_page","value":"OLD"}'


@pytest.fixture(autouse=True)
def _routes_available(monkeypatch: pytest.MonkeyPatch) -> None:
    store_module.PAID_RESULT_ROUTES.reset()
    monkeypatch.setenv("PAID_CACHE_WRITE", "true")      # the guarded delete is for a cache that writes (see the off test)


@pytest.fixture
async def storage() -> AsyncIterator[FakePaidResultStorage]:
    fake = FakePaidResultStorage(capabilities=("conditionalDelete", "bump"))
    await fake.start()
    yield fake
    await fake.stop()


def _store(fake: FakePaidResultStorage) -> NodePaidResultStore:
    return NodePaidResultStore(fake.blob_storage(), logging.getLogger("g35"))


async def _doomed_setup(fake: FakePaidResultStorage, store: NodePaidResultStore) -> None:
    """K is old and unlisted by the two newest completed revisions: a sweep deletes it."""
    fake.clock_ms -= 3 * HOUR
    fake._write(fake.key(ORG, REC, K), OLD)
    fake.clock_ms += 3 * HOUR
    for rev, n in (("r2", 2), ("r3", 3)):
        path = f"ocr_page/{n:064x}.json"
        fake._write(fake.key(ORG, REC, path), b"{}")
        run = RunScope(ORG, REC, rev, "parse", clock=lambda: fake.clock_ms / 1000)
        await run.note(store, path, opens=True)  # type: ignore[arg-type]
        await run.finish(store)  # type: ignore[arg-type]
        fake.clock_ms += 1000
    fake.clock_ms += GRACE_MS


async def test_the_sweep_names_the_listed_etag_and_deletes_an_unchanged_object(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    await _doomed_setup(storage, store)
    etag = storage.etag_of(ORG, REC, K)

    result = await sweep_record(store, ORG, REC, now_ms=storage.clock_ms)

    assert K not in storage.stored(ORG, REC)
    assert result.changed == 0
    guarded = [b for b in storage.delete_bodies if "items" in b]
    assert {i["path"]: i["etag"] for b in guarded for i in b["items"]}[K] == etag


async def test_an_object_re_written_after_the_listing_is_kept_and_counted(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    await _doomed_setup(storage, store)

    async def reuse_between_listing_and_delete() -> None:
        storage.clock_ms += 10
        storage._write(storage.key(ORG, REC, K), touched_bytes(OLD, storage.clock_ms) or b"")

    storage.hooks["delete"] = reuse_between_listing_and_delete

    result = await sweep_record(store, ORG, REC, now_ms=storage.clock_ms)

    assert K in storage.stored(ORG, REC), "the store deletes a version the sweep did not list"
    assert result.changed == 1
    assert result.deleted == len(storage.delete_bodies[-1]["items"]) - 1


async def test_a_store_that_honours_no_conditional_delete_is_listed_once_more_right_before_the_delete() -> None:
    """MinIO ignores If-Match on delete: the service names only ``bump``. The sweep still sends no ETag, and a
    reuse that lands before the last look keeps the object (one that lands after it is the documented residual)."""
    fake = FakePaidResultStorage(capabilities=("bump",))
    await fake.start()
    try:
        store = _store(fake)
        await _doomed_setup(fake, store)
        lists = 0

        async def bump_before_the_last_look() -> None:
            nonlocal lists
            lists += 1
            if lists == 3:
                fake.clock_ms += 10
                fake._write(fake.key(ORG, REC, K), touched_bytes(OLD, fake.clock_ms) or b"")

        fake.hooks["list"] = bump_before_the_last_look
        seen = len(fake.calls)

        await sweep_record(store, ORG, REC, now_ms=fake.clock_ms)

        assert lists == 3, "snapshot, second snapshot, and the look right before the delete"
        assert K in fake.stored(ORG, REC)
        kinds = [c[0] for c in fake.calls[seen:] if c[0].startswith("delete")]
        assert kinds[0] == "delete_if_match", "the guarded route is asked first; its 'cannot' answer sends the sweep to the look"
    finally:
        await fake.stop()


async def test_a_service_without_capabilities_is_never_sent_etags_and_deletes_as_before() -> None:
    """Rolling deploy, new Python on an older storage service: it lists no etag and reports no capabilities."""
    fake = FakePaidResultStorage(capabilities=None)
    await fake.start()
    try:
        store = _store(fake)
        await _doomed_setup(fake, store)

        result = await sweep_record(store, ORG, REC, now_ms=fake.clock_ms)

        assert K not in fake.stored(ORG, REC)
        assert not any(c[0] == "delete_if_match" for c in fake.calls), "a guarded delete was sent to an older service"
        assert result.changed == 0
        assert not store.supports_bump
    finally:
        await fake.stop()


async def test_a_guarded_delete_reply_that_names_no_kept_list_is_an_error_not_nothing_kept(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    storage.fixed_delete_reply = {"deleted": 1}
    storage._write(storage.key(ORG, REC, K), OLD)

    with pytest.raises(PaidResultStoreError):
        await store.delete_files_if_match(ORG, REC, {K: "x"})


async def test_a_replica_without_the_guarded_route_never_deletes_and_the_sweep_falls_back_to_the_look() -> None:
    """Rolling deploy: the listing came from a new replica (it has etags), the delete reaches an old one. The old replica
    404s the guarded route, where it would have ignored an ``etags`` field of the plain one and deleted without the guard."""
    fake = FakePaidResultStorage(capabilities=("conditionalDelete", "bump"), guard_route=False)
    await fake.start()
    try:
        store = _store(fake)
        await _doomed_setup(fake, store)
        with pytest.raises(GuardUnsupported):
            await store.delete_files_if_match(ORG, REC, {K: "x"})
        assert K in fake.stored(ORG, REC) and store_module.PAID_RESULT_ROUTES.available
        lists = 0

        async def reuse_before_the_last_look() -> None:
            nonlocal lists
            lists += 1
            if lists == 3:
                fake.clock_ms += 10
                fake._write(fake.key(ORG, REC, K), touched_bytes(OLD, fake.clock_ms) or b"")

        fake.hooks["list"] = reuse_before_the_last_look
        await sweep_record(store, ORG, REC, now_ms=fake.clock_ms)
        assert K in fake.stored(ORG, REC)
        assert all("etags" not in b for b in fake.delete_bodies)
    finally:
        await fake.stop()


async def test_capabilities_are_read_from_each_reply_and_never_latched(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    await store.presign(ORG, REC, [(K, "get")])
    assert store.supports_bump
    storage.capabilities = None                                    # the next replica is an earlier release
    await store.presign(ORG, REC, [(K, "get")])
    assert not store.supports_bump
    storage.capabilities = ("conditionalDelete", "bump")
    await store.presign(ORG, REC, [(K, "get")])
    assert store.supports_bump
    assert store_module.PAID_RESULT_ROUTES.available


async def test_a_bump_replaces_the_bytes_and_changes_the_etag_even_where_the_etag_is_a_content_hash(
    storage: FakePaidResultStorage,
) -> None:
    store = _store(storage)
    storage._write(storage.key(ORG, REC, K), OLD)
    before = await store.get_versioned(ORG, REC, K)
    assert before is not None and before.etag and before.last_modified_ms
    signed = await store.presign(ORG, REC, [(K, "bump", before.etag)])
    new = touched_bytes(before.data, 1_700_000_000_123)
    assert new is not None and new != before.data

    outcome, etag = await store.bump_to(signed[(K, "bump")], new)

    after = await store.get_versioned(ORG, REC, K)
    assert outcome == BUMPED and etag, "the bump answered no new ETag"
    assert after is not None and etag == after.etag != before.etag, "the ETag returned is not the stored object's"
    assert json.loads(storage.objects[storage.key(ORG, REC, K)][0])["value"] == "OLD"
    assert storage.put_headers[-1]["If-Match"] == f'"{before.etag}"'


async def test_a_bump_of_a_changed_object_is_refused_and_of_a_missing_one_is_gone(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    storage._write(storage.key(ORG, REC, K), OLD)
    signed = await store.presign(ORG, REC, [(K, "bump", "not-the-etag")])
    assert (await store.bump_to(signed[(K, "bump")], b"{}"))[0] == CHANGED
    del storage.objects[storage.key(ORG, REC, K)]
    assert (await store.bump_to(signed[(K, "bump")], b"{}"))[0] == GONE
    # A store's own 404 is not a missing route: nothing was switched off.
    assert store_module.PAID_RESULT_ROUTES.available


async def test_the_refresh_marker_is_absent_only_on_a_404(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    assert await store.refresh_marker_ms(ORG, REC) is None
    storage.begin_refresh(ORG, REC)
    assert await store.refresh_marker_ms(ORG, REC) == storage.clock_ms - storage.clock_ms % 1000
    # S3 answers 403 for a missing key when the signer may not list: for the marker that is not "no refresh".
    storage.objects.clear()
    storage.missing_status = 403
    raised: Exception | None = None
    try:
        await store.refresh_marker_ms(ORG, REC)
    except Exception as exc:  # any failure but a clean None
        raised = exc
    assert raised is not None, "an unreadable marker read as no refresh"


def _entry(storage: FakePaidResultStorage, **kw: object) -> BumpEntry:
    return BumpEntry(ORG, REC, K, OLD, str(kw.pop("etag", "e")), int(kw.pop("origin_ms", storage.clock_ms - 2 * HOUR)), None, **kw)  # type: ignore[arg-type]


async def _queue(storage: FakePaidResultStorage) -> BumpQueue:
    from app.services.paid_cache.cache import _FencedStore

    async def never(_o: str, _r: str) -> bool:
        return False

    async def no_sleep(_s: float) -> None:
        return None

    fenced = _FencedStore(_store(storage), never, logging.getLogger("g35"))
    await fenced.presign(ORG, REC, [(K, "get")])      # the hit's own read, which is where capabilities are learned
    return BumpQueue(fenced, sleep=no_sleep, autostart=False)  # type: ignore[arg-type]


async def test_the_queue_bumps_through_the_store_and_the_sweep_then_keeps_the_object(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    await _doomed_setup(storage, store)
    got = await store.get_versioned(ORG, REC, K)
    assert got is not None and got.etag and got.last_modified_ms is not None
    queue = await _queue(storage)

    async def sweep_after_bump_but_with_the_old_listing() -> None:
        # The sweep listed before the bump (its etag is the old one): its delete must now be refused.
        await queue.drain_once()

    storage.hooks["delete"] = sweep_after_bump_but_with_the_old_listing
    result = await sweep_record(store, ORG, REC, now_ms=storage.clock_ms) if queue.offer(
        BumpEntry(ORG, REC, K, got.data, got.etag, got.last_modified_ms, None),
    ) == "queued" else None

    assert result is not None and result.changed == 1
    assert K in storage.stored(ORG, REC)


async def test_a_gone_object_is_restored_with_the_held_bytes(storage: FakePaidResultStorage) -> None:
    queue = await _queue(storage)
    queue.offer(_entry(storage, etag="old"))
    await queue.drain_once()
    stored = json.loads(storage.objects[storage.key(ORG, REC, K)][0])
    assert stored["value"] == "OLD" and "originMs" in stored and "touchedMs" in stored


async def test_a_store_that_answers_412_for_a_missing_key_is_checked_with_a_read_and_restored(
    storage: FakePaidResultStorage,
) -> None:
    storage.gone_status = 412
    queue = await _queue(storage)
    queue.offer(_entry(storage, etag="old"))
    await queue.drain_once()
    stored = json.loads(storage.objects[storage.key(ORG, REC, K)][0])
    assert stored["value"] == "OLD" and "originMs" in stored and "touchedMs" in stored


async def test_a_refresh_since_the_object_was_written_stops_both_the_bump_and_the_restore(storage: FakePaidResultStorage) -> None:
    storage.begin_refresh(ORG, REC)
    queue = await _queue(storage)
    queue.offer(_entry(storage, etag="old", origin_ms=storage.clock_ms - 2 * HOUR))
    await queue.drain_once()
    assert [p for p in storage.stored(ORG, REC) if not p.startswith("refreshed/")] == []
    # Not written and removed again: nothing was written at all (the marker check before the write is what stops it).
    assert storage.put_headers == [], "a bump or a re-store was sent although a refresh had begun"


async def test_an_unreadable_refresh_marker_writes_nothing(storage: FakePaidResultStorage) -> None:
    storage.missing_status = 403
    queue = await _queue(storage)
    queue.offer(_entry(storage, etag="old"))
    await queue.drain_once()
    assert storage.stored(ORG, REC) == []


async def test_a_service_that_cannot_bump_is_never_asked(storage: FakePaidResultStorage) -> None:
    storage.capabilities = ()
    queue = await _queue(storage)
    # supports_bump reads the last reply: nothing was asked yet, so ask once the way a hit's read does.
    await queue._store.get_versioned(ORG, REC, K)  # type: ignore[attr-defined]
    queue.offer(_entry(storage, etag="old"))
    await queue.drain_once()
    assert not any(call["files"][0]["mode"] == "bump" for call in storage.presign_calls)
    assert storage.stored(ORG, REC) == []


async def test_a_rejected_bump_mode_from_an_older_replica_drops_the_batch_without_latching(storage: FakePaidResultStorage) -> None:
    queue = await _queue(storage)
    await queue._store.get_versioned(ORG, REC, K)  # type: ignore[attr-defined]  # a replica of the new release answered
    storage.capabilities = None                    # the presign of the batch lands on an older one: 400 on `bump`
    queue.offer(_entry(storage, etag="old"))
    await queue.drain_once()
    assert store_module.PAID_RESULT_ROUTES.available


async def test_the_queue_is_bounded_and_repeats_inside_the_window_are_dropped(storage: FakePaidResultStorage) -> None:
    from app.services.paid_cache import bump

    queue = await _queue(storage)
    assert queue.offer(_entry(storage)) == "queued"
    assert queue.offer(_entry(storage)) == "recent"
    big = BumpEntry(ORG, REC, "ocr_page/" + "b" * 64 + ".json", b"x" * (bump.MAX_OBJECT_BYTES + 1), "e", 0, None)
    assert queue.offer(big) == "too_large"


async def test_a_stale_queue_entry_is_dropped(storage: FakePaidResultStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache import bump

    queue = await _queue(storage)
    entry = _entry(storage, etag="old")
    queue.offer(entry)
    entry.enqueued -= bump.ENTRY_MAX_AGE_S + 1
    await queue.drain_once()
    assert storage.stored(ORG, REC) == []


async def test_nothing_is_written_for_a_deleted_record() -> None:
    from app.services.paid_cache.cache import _FencedStore

    fake = FakePaidResultStorage(capabilities=("conditionalDelete", "bump"))
    await fake.start()
    try:
        async def deleted(_o: str, _r: str) -> bool:
            return True

        async def no_sleep(_s: float) -> None:
            return None

        fenced = _FencedStore(_store(fake), deleted, logging.getLogger("g35"))
        await fenced.presign(ORG, REC, [(K, "get")])
        queue = BumpQueue(fenced, sleep=no_sleep, autostart=False)  # type: ignore[arg-type]
        # The object is there with the ETag the run read: a bump would re-write it, a restore would be refused by the
        # fence anyway; only the check before the write stops the bump.
        fake._write(fake.key(ORG, REC, K), OLD)
        etag = fake.etag_of(ORG, REC, K)
        queue.offer(_entry(fake, etag=etag or "x"))
        await queue.drain_once()
        assert fake.etag_of(ORG, REC, K) == etag, "a bump was written for a deleted record"
        assert fake.put_headers == []
    finally:
        await fake.stop()


def test_touched_bytes_always_change_the_bytes_and_keep_the_value() -> None:
    first = touched_bytes(OLD, 1000)
    assert first is not None
    again = touched_bytes(first, 1000)                             # the same millisecond again
    assert again is not None and again != first
    assert json.loads(again)["value"] == "OLD" and json.loads(again)["touchedMs"] == 1001
    assert touched_bytes(b"[1]", 1) is None and touched_bytes(b"not json", 1) is None


async def test_nothing_is_bumped_or_restored_past_the_stage_deadline(storage: FakePaidResultStorage) -> None:
    """The re-store keeps the bound a paid store has: a write after the stage deadline lands after the last re-purge."""
    scope = RunScope(ORG, REC, "rev1", "parse", clock=lambda: storage.clock_ms / 1000, stage_deadline_s=60)
    storage.clock_ms += 120_000                                  # the stage began two minutes ago; its deadline was one
    queue = await _queue(storage)

    # The object is gone: a restore would store the bytes the run holds.
    queue.offer(BumpEntry(ORG, REC, K, OLD, "old", 0, scope))
    await queue.drain_once()
    assert storage.stored(ORG, REC) == []

    # The object is there: a bump would re-write it.
    storage._write(storage.key(ORG, REC, K), OLD)
    etag = storage.etag_of(ORG, REC, K)
    queue.offer(BumpEntry(ORG, REC, K, OLD, etag or "", 0, scope))
    await queue.drain_once()
    assert storage.etag_of(ORG, REC, K) == etag, "a bump wrote past the deadline"


async def test_a_restore_is_refused_when_the_deadline_passes_while_the_bump_is_in_flight(storage: FakePaidResultStorage) -> None:
    """The deadline is checked again at the write: the bump request itself can run past it."""
    scope = RunScope(ORG, REC, "rev1", "parse", clock=lambda: storage.clock_ms / 1000, stage_deadline_s=60)
    queue = await _queue(storage)

    async def the_stage_deadline_passes() -> None:
        storage.clock_ms += 120_000

    storage.hooks["bump_put"] = the_stage_deadline_passes
    queue.offer(BumpEntry(ORG, REC, K, OLD, "old", 0, scope))          # the object is gone: the bump is answered 404

    await queue.drain_once()

    assert storage.stored(ORG, REC) == [], "the bytes were stored again after the stage deadline"


# -- the refresh marker comes with the cutoff, or the refresh does not go on (mixed storage fleet) -----------------------

async def test_with_writes_on_a_cutoff_from_a_service_that_writes_no_marker_fails_the_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh that took its cutoff on a storage replica of an earlier release has no marker for a bump to see: the
    purge would keep a pre-refresh result a reuse bumped. The refresh fails and is retried until storage is updated."""
    from app.services.paid_cache.cache import PaidResultCache, PurgeCutoffUnavailable
    from app.services.paid_cache.locks import InProcessLocks

    fake = FakePaidResultStorage(capabilities=("conditionalDelete", "bump"), cutoff_marker=False)
    await fake.start()
    try:
        cache = PaidResultCache(_store(fake), InProcessLocks())
        monkeypatch.setenv("PAID_CACHE_WRITE", "true")
        with pytest.raises(PurgeCutoffUnavailable):
            await cache.require_purge_cutoff(ORG, REC)
        fake.cutoff_marker = True                                   # the next replica is a current one
        cutoff = await cache.require_purge_cutoff(ORG, REC)
        assert cutoff is not None and await _store(fake).refresh_marker_ms(ORG, REC) is not None
        # With writes off nothing is bumped, so the older replica's cutoff is fine (as before this change).
        monkeypatch.setenv("PAID_CACHE_WRITE", "off")
        fake.cutoff_marker = False
        assert await cache.require_purge_cutoff(ORG, REC) is not None
    finally:
        await fake.stop()


# -- test gaps found by QA ---------------------------------------------------------------------------------------------

def test_a_refresh_in_the_same_second_as_the_write_counts_as_since() -> None:
    """Whole-second stamps: a tie is not "before"."""
    from app.services.paid_cache.bump import _refreshed_since

    assert _refreshed_since(5_000_900, 5_000_100) is True
    assert _refreshed_since(5_000_000, 5_001_000) is False
    assert _refreshed_since(None, 5_000_000) is False


async def test_an_exhausted_409_is_checked_with_a_read_like_a_412(storage: FakePaidResultStorage) -> None:
    storage.gone_status = 409
    queue = await _queue(storage)
    queue.offer(_entry(storage, etag="old"))
    await queue.drain_once()
    assert json.loads(storage.objects[storage.key(ORG, REC, K)][0])["value"] == "OLD"
    # and when the object is there (someone else's version) and every bump answers 409, nothing is written over it
    storage._write(storage.key(ORG, REC, K), b'{"v":1,"value":"theirs"}')
    storage.conflict_all_bumps = True
    other = BumpEntry(ORG, REC, K, OLD, "old", 0, None)
    queue2 = await _queue(storage)
    queue2.offer(other)
    await queue2.drain_once()
    assert b"theirs" in storage.objects[storage.key(ORG, REC, K)][0]


async def test_an_object_re_written_between_the_two_listings_is_kept_even_if_its_write_time_did_not_move(
    storage: FakePaidResultStorage,
) -> None:
    store = _store(storage)
    await _doomed_setup(storage, store)
    lists = 0
    old_stamp = storage.objects[storage.key(ORG, REC, K)][1]

    async def rewrite_before_the_second_listing() -> None:
        nonlocal lists
        lists += 1
        if lists == 2:
            storage._write(storage.key(ORG, REC, K), touched_bytes(OLD, 1) or b"")
            storage.objects[storage.key(ORG, REC, K)] = (storage.objects[storage.key(ORG, REC, K)][0], old_stamp)

    storage.hooks["list"] = rewrite_before_the_second_listing
    await sweep_record(store, ORG, REC, now_ms=storage.clock_ms)
    assert K in storage.stored(ORG, REC), "the sweep deleted a version that appeared after its first listing"


# -- a bump that may have landed, and a result that predates a refresh --------------------------------------------------

async def test_a_bump_that_landed_before_a_refused_retry_is_checked_like_any_other_write() -> None:
    from tests.support.versioned_store import VersionedStore

    store = VersionedStore(stamp_ms=1)
    store.write(ORG, REC, K, OLD)
    got = await store.get_versioned(ORG, REC, K)
    assert got is not None and got.etag
    original = store.bump_to

    async def lands_then_reports_a_lost_race(entry: object, data: bytes, charge: object = None, eligible: object = None) -> tuple[str, str | None]:
        await original(entry, data, charge, eligible)  # type: ignore[arg-type]
        store.begin_refresh(ORG, REC)                # a refresh begins right after the write was sent
        return CHANGED, None

    store.bump_to = lands_then_reports_a_lost_race  # type: ignore[method-assign]

    async def no_wait(_s: float) -> None:
        return None

    queue = BumpQueue(store, sleep=no_wait, autostart=False)  # type: ignore[arg-type]
    queue.offer(BumpEntry(ORG, REC, K, got.data, got.etag, 0, None))
    await queue.drain_once()
    assert (ORG, REC, K) not in store.files, "a landed bump was not checked after a refused retry"


async def test_a_bumped_result_that_predates_a_refresh_is_never_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop between a bump and its check leaves a pre-refresh result in place: the next reuse finds ``originMs`` and
    the marker, and computes again."""
    from unittest.mock import AsyncMock

    from app.services.paid_cache.cache import PaidResultCache, _encode
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.locks import InProcessLocks
    from app.services.paid_cache.run import begin_run, clear_run
    from tests.support.paid_cache import record_in_flight
    from tests.support.versioned_store import VersionedStore

    monkeypatch.setenv("PAID_CACHE_WRITE", "true")
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    store = VersionedStore(stamp_ms=1000)
    store.write(ORG, REC, key.relative_path(), touched_bytes(_encode(key, "old text"), 5_000, origin_ms=store.now_ms - 10_000) or b"")
    store.now_ms += 5_000
    store.begin_refresh(ORG, REC)                          # a refresh began after the object was first written
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    cache.bumps._autostart = False
    try:
        with record_in_flight(ORG, REC):
            begin_run(ORG, REC, "rev1", "parse")
            paid = AsyncMock(return_value="fresh text")
            assert await cache.get_or_compute(key, paid) == "fresh text"
            paid.assert_awaited_once()
        # a result written after the refresh is reused as before
        store.now_ms += 5_000
        store.write(ORG, REC, key.relative_path(), touched_bytes(_encode(key, "new text"), 9_000, origin_ms=store.now_ms) or b"")
        cache._markers.clear()
        with record_in_flight(ORG, REC):
            paid2 = AsyncMock(return_value="unused")
            assert await cache.get_or_compute(key, paid2) == "new text"
            paid2.assert_not_called()
        # and a marker that cannot be read is not "no refresh"
        store.unreadable_marker = True
        cache._markers.clear()
        with record_in_flight(ORG, REC):
            paid3 = AsyncMock(return_value="again")
            assert await cache.get_or_compute(key, paid3) == "again"
    finally:
        clear_run()


# -- throughput: one wait per batch, records at once, and protection that is not applied is counted and logged ----------

async def test_many_records_are_written_at_once_with_one_wait_for_the_batch() -> None:
    from tests.support.versioned_store import VersionedStore

    store = VersionedStore(stamp_ms=1)
    waits: list[float] = []

    async def count_wait(seconds: float) -> None:
        waits.append(seconds)

    queue = BumpQueue(store, sleep=count_wait, autostart=False)  # type: ignore[arg-type]
    for r in range(40):
        store.write(ORG, f"rec-{r}", K, OLD)
        got = await store.get_versioned(ORG, f"rec-{r}", K)
        assert got is not None and got.etag
        queue.offer(BumpEntry(ORG, f"rec-{r}", K, got.data, got.etag, 0, None))
    await queue.drain_once()
    assert len(waits) == 1 and waits[0] <= 2.5, "the post-write wait is taken once per batch, not once per record"
    assert all(b"touchedMs" in store.files[(ORG, f"rec-{r}", K)][0] for r in range(40))
    assert store.ops.count("marker") == 80             # two reads per record: before and after, whatever the batch size


async def test_protection_lost_to_a_full_queue_is_counted_and_logged(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache import bump
    from tests.support.versioned_store import VersionedStore

    monkeypatch.setattr(bump, "QUEUE_MAX", 2)
    queue = BumpQueue(VersionedStore(), autostart=False)  # type: ignore[arg-type]
    with caplog.at_level("WARNING", logger="app.services.paid_cache.bump"):
        outcomes = [queue.offer(BumpEntry(ORG, REC, f"ocr_page/{n:064x}.json", b"{}", "e", 0, None)) for n in range(6)]
    assert outcomes.count("dropped") == 4
    warnings = [r for r in caplog.records if "behind" in r.getMessage()]
    assert len(warnings) == 1 and "dropped=1" in warnings[0].getMessage(), "logged once, then rate limited, with counts"


# -- review round: capabilities per org, mixed batches, validated hits, marker reads, strict cleanup --------------------

async def test_a_reply_for_another_org_does_not_change_what_this_orgs_storage_honours(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    await store.presign("org-a", REC, [(K, "get")])
    storage.capabilities = None                                    # another org's storage: nothing conditional
    await store.presign("org-b", REC, [(K, "get")])
    assert store.honours("org-a", "conditionalDelete") and store.honours("org-a", "bump")
    assert not store.honours("org-b", "conditionalDelete")
    assert not store.honours("org-c", "bump"), "an org that was never asked honours nothing"


async def test_a_path_with_no_listed_version_is_looked_at_again_even_when_others_have_one() -> None:
    """Mixed batch: one object is guarded by its ETag, the other cannot be; the second is re-listed before the delete."""
    from tests.support.versioned_store import VersionedStore

    store = VersionedStore(stamp_ms=1, now_ms=1_700_000_000_000)
    k1, k2 = f"ocr_page/{11:064x}.json", f"ocr_page/{12:064x}.json"
    for k in (k1, k2):
        store.write(ORG, REC, k, OLD, age_ms=3 * HOUR)
    for rev, n in (("r2", 2), ("r3", 3)):
        run = RunScope(ORG, REC, rev, "parse", clock=lambda: store.now_ms / 1000)
        other = f"ocr_page/{n:064x}.json"
        store.write(ORG, REC, other, b"{}")
        await run.note(store, other, opens=True)  # type: ignore[arg-type]
        await run.finish(store)  # type: ignore[arg-type]
        store.now_ms += 1000
    store.now_ms += GRACE_MS
    real_list = store.list_files
    calls = 0

    async def list_without_k2_etag(o: str, r: str, prefix: str = "") -> list:
        nonlocal calls
        calls += 1
        if calls == 3:                                              # the look right before the delete: k2 was re-written
            store.now_ms += 5
            store.write(ORG, REC, k2, touched_bytes(OLD, 1) or b"")
        return [type(f)(f.path, f.size, f.last_modified_ms, None if f.path == k2 else f.etag) for f in await real_list(o, r, prefix)]

    store.list_files = list_without_k2_etag  # type: ignore[method-assign]
    await sweep_record(store, ORG, REC, now_ms=store.now_ms)  # type: ignore[arg-type]
    assert (ORG, REC, k2) in store.files, "an object with no listed version was deleted without a last look"
    assert (ORG, REC, k1) not in store.files


async def test_only_a_version_the_caller_accepted_is_kept_for_a_bump(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache.cache import PaidResultCache, _encode
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.locks import InProcessLocks
    from tests.support.versioned_store import VersionedStore

    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    store = VersionedStore(stamp_ms=1)
    store.write(ORG, REC, key.relative_path(), _encode(key, "rejected"))
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    cache._store.honours = lambda *_a: True  # type: ignore[method-assign,attr-defined]
    hit: list = []

    def reject(_value: object) -> None:
        raise ValueError("no longer decodes")

    found, _value, _unusable = await cache._lookup(key, key.relative_path(), reject, hit_out=hit)
    assert not found and hit == [], "the bytes the caller rejected were kept as what a bump or a restore may write"


async def test_a_marker_read_that_did_not_reject_is_never_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from app.services.paid_cache.cache import PaidResultCache, _encode
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.locks import InProcessLocks
    from app.services.paid_cache.run import begin_run, clear_run
    from tests.support.paid_cache import record_in_flight
    from tests.support.versioned_store import VersionedStore

    monkeypatch.setenv("PAID_CACHE_WRITE", "true")
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    store = VersionedStore(stamp_ms=1000)
    origin = store.now_ms - 10_000
    store.write(ORG, REC, key.relative_path(), touched_bytes(_encode(key, "text"), 1, origin_ms=origin) or b"")
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    cache.bumps._autostart = False
    try:
        with record_in_flight(ORG, REC):
            begin_run(ORG, REC, "rev1", "parse")
            paid = AsyncMock(return_value="recomputed")
            assert await cache.get_or_compute(key, paid) == "text"          # no refresh yet: reused, marker read as absent
            paid.assert_not_called()
            store.now_ms += 5_000
            store.begin_refresh(ORG, REC)                                   # a refresh begins right after
            assert await cache.get_or_compute(key, paid) == "recomputed", "an earlier 'no refresh' read was reused"
    finally:
        clear_run()


async def test_a_bump_is_not_undone_by_an_unconditional_delete_on_a_store_without_conditional_delete() -> None:
    from tests.support.versioned_store import VersionedStore

    store = VersionedStore(stamp_ms=1, conditional_delete=False)
    store.write(ORG, REC, K, OLD)
    got = await store.get_versioned(ORG, REC, K)
    assert got is not None and got.etag
    deleted: list = []
    real = store.delete_files

    async def spy(*a: object, **kw: object) -> list[str]:
        deleted.append(a)
        return await real(*a, **kw)  # type: ignore[arg-type]

    store.delete_files = spy  # type: ignore[method-assign]
    original = store.bump_to

    async def bump_then_refresh(entry: object, data: bytes, charge: object = None, eligible: object = None) -> tuple[str, str | None]:
        result = await original(entry, data, charge, eligible)  # type: ignore[arg-type]
        store.begin_refresh(ORG, REC)                   # a refresh begins after the write: the check would remove it
        return result

    store.bump_to = bump_then_refresh  # type: ignore[method-assign]

    async def no_wait(_s: float) -> None:
        return None

    queue = BumpQueue(store, sleep=no_wait, autostart=False)  # type: ignore[arg-type]
    queue.offer(BumpEntry(ORG, REC, K, got.data, got.etag, 0, None))
    await queue.drain_once()
    assert b"touchedMs" in store.files[(ORG, REC, K)][0], "the bump never landed: the test says nothing"
    assert deleted == [], "a delete that cannot name the version was sent"


async def test_a_storage_that_cannot_delete_by_version_for_any_reason_sweeps_by_the_look_and_never_fails(storage: FakePaidResultStorage) -> None:
    """A probe that is denied or cannot tell reads as "unsupported" at the storage: the sweep takes its look and goes on."""
    store = _store(storage)
    await _doomed_setup(storage, store)
    storage.capabilities = ("bump",)
    await sweep_record(store, ORG, REC, now_ms=storage.clock_ms)
    assert K not in storage.stored(ORG, REC)


async def test_with_writes_off_the_sweep_sends_no_guarded_delete_and_behaves_as_before(
    storage: FakePaidResultStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PAID_CACHE_WRITE", "off")
    store = _store(storage)
    await _doomed_setup(storage, store)
    seen = len(storage.calls)
    await sweep_record(store, ORG, REC, now_ms=storage.clock_ms)
    assert K not in storage.stored(ORG, REC)
    assert not any(c[0] == "delete_if_match" for c in storage.calls[seen:]), "a guarded delete was sent with writes off"


# -- review round 3 ----------------------------------------------------------------------------------------------------

async def test_a_guarded_delete_is_refused_or_unsupported_never_plain(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    storage._write(storage.key(ORG, REC, K), OLD)
    storage.capabilities = ("bump",)
    with pytest.raises(GuardUnsupported):
        await store.delete_files_if_match(ORG, REC, {K: "abc"})
    assert K in storage.stored(ORG, REC), "a guarded delete on a store that cannot honour it deleted"
    storage.capabilities = ("conditionalDelete", "bump")
    assert await store.delete_files_if_match(ORG, REC, {K: "stale"}) == [K]
    assert K in storage.stored(ORG, REC)


async def test_a_path_whose_bump_presign_failed_is_offered_again_by_a_later_hit() -> None:
    from tests.support.versioned_store import VersionedStore

    store = VersionedStore(stamp_ms=1)
    real = store.presign
    failures = [True]

    async def flaky_presign(*a: object, **kw: object) -> dict:
        if failures and any(len(f) > 2 for f in a[2]):  # type: ignore[index]
            failures.pop()
            raise ConnectionError("an older replica refused the mode")
        return await real(*a, **kw)  # type: ignore[arg-type]

    store.presign = flaky_presign  # type: ignore[method-assign]

    async def no_wait(_s: float) -> None:
        return None

    queue = BumpQueue(store, sleep=no_wait, autostart=False)  # type: ignore[arg-type]
    store.write(ORG, REC, K, OLD)
    got = await store.get_versioned(ORG, REC, K)
    assert got is not None and got.etag
    entry = BumpEntry(ORG, REC, K, got.data, got.etag, 0, None)
    assert queue.offer(entry) == "queued"
    await queue.drain_once()                                     # the presign failed: nothing was bumped
    assert queue.offer(BumpEntry(ORG, REC, K, got.data, got.etag, 0, None)) == "queued", "a failed batch suppressed the key"
    await queue.drain_once()
    assert b"touchedMs" in store.files[(ORG, REC, K)][0]
    assert queue.offer(BumpEntry(ORG, REC, K, got.data, got.etag, 0, None)) == "recent", "a bumped key is not repeated at once"


def test_the_hourly_byte_budget_is_shared_by_queues_and_promised_bytes_count() -> None:
    from app.services.paid_cache.bump import ByteBudget
    from tests.support.versioned_store import VersionedStore

    budget = ByteBudget(per_hour=500)
    one = BumpQueue(VersionedStore(), autostart=False, budget=budget)  # type: ignore[arg-type]
    two = BumpQueue(VersionedStore(), autostart=False, budget=budget)  # type: ignore[arg-type]
    entry = lambda n: BumpEntry(ORG, REC, f"ocr_page/{n:064x}.json", b"x" * 100, "e", 0, None)  # noqa: E731 - a JSON value or opaque signed-URL entry of any shape
    assert one.offer(entry(1)) == "queued" and two.offer(entry(2)) == "queued"      # 2 x 196 reserved
    assert one.offer(entry(3)) == "over_budget" and two.offer(entry(4)) == "over_budget"


async def test_a_batch_is_checked_again_before_each_storage_request_for_paths_with_no_listed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.paid_cache import retention
    from tests.support.versioned_store import VersionedStore

    monkeypatch.setattr(retention, "MAX_FILES_PER_CALL", 2)
    store = VersionedStore(stamp_ms=1, now_ms=1_700_000_000_000, conditional_delete=False)
    keys = [f"ocr_page/{20 + n:064x}.json" for n in range(3)]
    for k in keys:
        store.write(ORG, REC, k, OLD, age_ms=3 * HOUR)
    for rev, n in (("r2", 2), ("r3", 3)):
        run = RunScope(ORG, REC, rev, "parse", clock=lambda: store.now_ms / 1000)
        other = f"ocr_page/{n:064x}.json"
        store.write(ORG, REC, other, b"{}")
        await run.note(store, other, opens=True)  # type: ignore[arg-type]
        await run.finish(store)  # type: ignore[arg-type]
        store.now_ms += 1000
    store.now_ms += GRACE_MS
    real_delete = store.delete_files
    first = True

    async def delete_then_a_reuse_lands(o: str, r: str, paths: object) -> None:
        nonlocal first
        result = await real_delete(o, r, paths)  # type: ignore[arg-type]
        if first:
            first = False
            store.now_ms += 5
            store.write(ORG, REC, keys[2], touched_bytes(OLD, 1) or b"")     # the third path, in the next request
        return result

    store.delete_files = delete_then_a_reuse_lands  # type: ignore[method-assign]
    await sweep_record(store, ORG, REC, now_ms=store.now_ms)  # type: ignore[arg-type]
    assert (ORG, REC, keys[2]) in store.files, "a path of a later request was deleted on the first request's listing"
    assert (ORG, REC, keys[0]) not in store.files


async def test_a_result_made_before_the_cutoff_cannot_be_stored_again_after_the_marker_written_at_the_cutoff() -> None:
    """The marker is written again once the cutoff is known: a run still holding a result written between the first
    marker and the cutoff is refused when it would store it again."""
    from tests.support.versioned_store import VersionedStore

    store = VersionedStore(stamp_ms=1000)
    store.begin_refresh(ORG, REC)                        # the first marker, second 0
    store.now_ms += 2_000
    held_origin = store.now_ms                           # a normal run's result, written at second 2 (before the cutoff)
    store.now_ms += 1_000
    store.begin_refresh(ORG, REC)                        # the marker rewritten at the cutoff, second 3
    async def no_wait(_s: float) -> None:
        return None

    queue = BumpQueue(store, sleep=no_wait, autostart=False)  # type: ignore[arg-type]
    queue.offer(BumpEntry(ORG, REC, K, OLD, "gone", held_origin, None))
    await queue.drain_once()
    assert (ORG, REC, K) not in store.files, "a pre-cutoff result was stored again after the purge"


async def test_an_unreadable_marker_neither_reuses_nor_overwrites_a_bumped_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from app.services.paid_cache.cache import PaidResultCache, _encode
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.locks import InProcessLocks
    from app.services.paid_cache.run import begin_run, clear_run
    from tests.support.paid_cache import record_in_flight
    from tests.support.versioned_store import VersionedStore

    monkeypatch.setenv("PAID_CACHE_WRITE", "true")
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    store = VersionedStore(stamp_ms=1000)
    store.write(ORG, REC, key.relative_path(), touched_bytes(_encode(key, "kept"), 1, origin_ms=store.now_ms - 10_000) or b"")
    before = store.files[(ORG, REC, key.relative_path())][1]
    store.unreadable_marker = True
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    cache.bumps._autostart = False
    try:
        with record_in_flight(ORG, REC):
            begin_run(ORG, REC, "rev1", "parse")
            paid = AsyncMock(return_value="fresh")
            assert await cache.get_or_compute(key, paid) == "fresh"
        assert store.files[(ORG, REC, key.relative_path())][1] == before, "a result was overwritten on an unknown marker"
    finally:
        clear_run()


# -- review round 4 ----------------------------------------------------------------------------------------------------

async def test_every_upload_attempt_of_a_bump_is_charged_to_the_byte_budget(storage: FakePaidResultStorage) -> None:
    from app.services.paid_cache.bump import ByteBudget

    storage._write(storage.key(ORG, REC, K), OLD)
    storage.conflict_all_bumps = True                       # three attempts, all answered 409
    queue = await _queue(storage)
    queue._budget = ByteBudget()
    etag = storage.etag_of(ORG, REC, K)
    entry = BumpEntry(ORG, REC, K, OLD, etag or "", 0, None)
    queue.offer(entry)
    await queue.drain_once()
    sent = len([h for h in storage.put_headers if "If-Match" in h])
    assert sent == 3
    assert queue._budget.used(__import__("time").monotonic()) >= 3 * len(OLD), "attempts after the first were not charged"


async def test_queued_reservations_are_given_back_when_the_queue_is_closed() -> None:
    from app.services.paid_cache.bump import ByteBudget
    from tests.support.versioned_store import VersionedStore

    budget = ByteBudget(per_hour=1000)
    queue = BumpQueue(VersionedStore(), autostart=False, budget=budget)  # type: ignore[arg-type]
    entry = lambda n: BumpEntry(ORG, REC, f"ocr_page/{n:064x}.json", b"x" * 100, "e", 0, None)  # noqa: E731 - a JSON value or opaque signed-URL entry of any shape
    assert queue.offer(entry(1)) == "queued" and queue.offer(entry(2)) == "queued"
    assert budget._reserved > 0
    queue.close()
    assert budget._reserved == 0, "a closed queue kept what it had been promised"
    assert queue.offer(entry(1)) == "queued", "a key of a closed queue stayed pending"


async def test_a_restore_reads_the_refresh_marker_again_before_it_stores(storage: FakePaidResultStorage) -> None:
    queue = await _queue(storage)

    async def a_refresh_begins_while_the_bump_is_in_flight() -> None:
        storage.begin_refresh(ORG, REC)

    storage.hooks["bump_put"] = a_refresh_begins_while_the_bump_is_in_flight
    queue.offer(_entry(storage, etag="old", origin_ms=storage.clock_ms - 2 * HOUR))     # the object is gone: 404
    await queue.drain_once()
    assert [p for p in storage.stored(ORG, REC) if not p.startswith("refreshed/")] == [], "pre-refresh bytes were restored"
    # Not "restored and then removed by the post-check": never sent. Only the re-read of the marker stops it.
    assert not any(h.get("If-None-Match") == "*" for h in storage.put_headers), "the restore was sent before the marker was re-read"


async def test_an_undo_the_store_reports_as_changed_is_not_counted_as_done() -> None:
    from app.services.paid_cache import bump
    from tests.support.versioned_store import VersionedStore

    counted: list[str] = []
    store = VersionedStore(stamp_ms=1)
    store.write(ORG, REC, K, OLD)
    got = await store.get_versioned(ORG, REC, K)
    assert got is not None and got.etag
    original = store.bump_to

    async def bump_then_refresh(entry: object, data: bytes, charge: object = None, eligible: object = None) -> tuple[str, str | None]:
        result = await original(entry, data, charge, eligible)  # type: ignore[arg-type]
        store.begin_refresh(ORG, REC)
        return result

    store.bump_to = bump_then_refresh  # type: ignore[method-assign]
    real_if_match = store.delete_files_if_match

    async def replaced_before_the_delete(o: str, r: str, items: dict) -> list[str]:
        store.write(o, r, K, b'{"v":1,"value":"newer"}')           # a writer replaced it after the undo read it
        return await real_if_match(o, r, items)

    store.delete_files_if_match = replaced_before_the_delete  # type: ignore[method-assign]

    async def no_wait(_s: float) -> None:
        return None

    queue = BumpQueue(store, sleep=no_wait, autostart=False)  # type: ignore[arg-type]
    queue._count = lambda outcome: counted.append(outcome) or outcome  # type: ignore[method-assign,func-returns-value]
    queue.offer(BumpEntry(ORG, REC, K, got.data, got.etag, 0, None))
    await queue.drain_once()
    assert "undone" not in counted and "undo_kept" in counted
    assert b"newer" in store.files[(ORG, REC, K)][0]
    assert bump is not None


async def test_the_read_that_discovers_bump_support_carries_the_version_too(storage: FakePaidResultStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache.cache import PaidResultCache, _encode
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.locks import InProcessLocks

    monkeypatch.setenv("PAID_CACHE_WRITE", "true")
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    cache = PaidResultCache(_store(storage), InProcessLocks())
    storage._write(storage.key(ORG, REC, key.relative_path()), _encode(key, "text"))
    hit: list = []
    found, _value, _unusable = await cache._lookup(key, key.relative_path(), None, hit_out=hit)     # first call for the org
    assert found and hit and hit[0].etag, "the first read of an org dropped the version a bump needs"


async def test_the_marker_fence_is_the_cutoff_once_the_storage_has_taken_it(storage: FakePaidResultStorage) -> None:
    from app.services.paid_cache.bump import _refreshed_since
    from app.services.paid_cache.store import FinalFence

    store = _store(storage)
    storage._write(storage.key(ORG, REC, "refreshed/marker.json"), json.dumps({"startedMs": 1, "cutoffMs": 10_800}).encode())
    storage.objects[storage.key(ORG, REC, "refreshed/marker.json")] = (
        storage.objects[storage.key(ORG, REC, "refreshed/marker.json")][0], 1_700_000_900_000,   # written long after the cutoff
    )
    fence = await store.refresh_marker_ms(ORG, REC)
    assert isinstance(fence, FinalFence) and fence == 10_800
    # exact, by the stamps the purge compares: 10 200 is older than the cutoff (purged, stale) even in the same second
    assert _refreshed_since(fence, 10_200) is True, "a millisecond-stamped result older than the cutoff was taken for current"
    assert _refreshed_since(fence, 10_800) is False and _refreshed_since(fence, 11_500) is False
    # before the cutoff is known the marker's own time is the conservative fence (whole seconds, a tie is stale)
    storage._write(storage.key(ORG, REC, "refreshed/marker.json"), b'{"startedMs":1}')
    provisional = await store.refresh_marker_ms(ORG, REC)
    assert not isinstance(provisional, FinalFence)


# -- review round 6 ----------------------------------------------------------------------------------------------------

async def test_a_provisional_marker_rejection_is_never_cached() -> None:
    from app.services.paid_cache.cache import PaidResultCache, _encode
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.locks import InProcessLocks
    from tests.support.versioned_store import VersionedStore

    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    store = VersionedStore(stamp_ms=1000)
    raw = touched_bytes(_encode(key, "text"), 1, origin_ms=store.now_ms) or b""
    store.write(ORG, REC, key.relative_path(), raw)
    store.begin_refresh(ORG, REC)                       # provisional: a tie with the object's second rejects it
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    assert await cache._predates_refresh(key, raw) is True
    assert cache._markers == {}, "a provisional rejection was kept for the whole window"


async def test_a_bump_whose_request_failed_after_it_committed_is_checked_like_any_write() -> None:
    from tests.support.versioned_store import VersionedStore

    store = VersionedStore(stamp_ms=1)
    store.write(ORG, REC, K, OLD)
    got = await store.get_versioned(ORG, REC, K)
    assert got is not None and got.etag
    original = store.bump_to

    async def commits_then_fails(entry: object, data: bytes, charge: object = None, eligible: object = None) -> tuple[str, str | None]:
        await original(entry, data, charge, eligible)  # type: ignore[arg-type]
        store.begin_refresh(ORG, REC)                   # a refresh begins right after; the check must remove the write
        raise ConnectionError("the answer was cut off")

    store.bump_to = commits_then_fails  # type: ignore[method-assign]

    async def no_wait(_s: float) -> None:
        return None

    queue = BumpQueue(store, sleep=no_wait, autostart=False)  # type: ignore[arg-type]
    queue.offer(BumpEntry(ORG, REC, K, got.data, got.etag, 0, None))
    await queue.drain_once()
    assert (ORG, REC, K) not in store.files, "a write that committed before the error escaped its check"


async def test_a_restore_is_refused_when_the_marker_read_itself_runs_past_the_deadline() -> None:
    from tests.support.versioned_store import VersionedStore

    clock = [1_700_000_000.0]
    store = VersionedStore(stamp_ms=1)
    scope = RunScope(ORG, REC, "rev1", "parse", clock=lambda: clock[0], stage_deadline_s=60)
    real_marker = store.refresh_marker_ms

    reads = 0

    async def slow_marker(o: str, r: str) -> int | None:
        nonlocal reads
        reads += 1
        if reads == 2:                                   # the restore's own read (the first was before the bump)
            clock[0] += 120                              # took two minutes: the stage deadline passes inside it
        return await real_marker(o, r)

    store.refresh_marker_ms = slow_marker  # type: ignore[method-assign]

    async def no_wait(_s: float) -> None:
        return None

    queue = BumpQueue(store, sleep=no_wait, autostart=False)  # type: ignore[arg-type]
    queue.offer(BumpEntry(ORG, REC, K, OLD, "gone", 0, scope))
    await queue.drain_once()
    assert reads >= 2, "the restore never read the marker again: the test says nothing"
    assert (ORG, REC, K) not in store.files


async def test_no_bump_attempt_starts_after_the_stage_deadline(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    storage._write(storage.key(ORG, REC, K), OLD)
    etag = storage.etag_of(ORG, REC, K)
    signed = await store.presign(ORG, REC, [(K, "bump", etag or "")])
    expired = False

    async def the_deadline_passes_during_the_first_attempt() -> None:
        nonlocal expired
        expired = True                                   # the first attempt is answered 409; the deadline then passes

    storage.conflict_all_bumps = True
    storage.hooks["bump_put"] = the_deadline_passes_during_the_first_attempt
    outcome, _ = await store.bump_to(signed[(K, "bump")], b"{}", None, lambda: not expired)
    assert outcome == "deadline"
    attempts = len([h for h in storage.put_headers if "If-Match" in h])
    assert attempts == 1, f"{attempts} attempts were sent: one started after the deadline"


async def test_two_encodings_of_the_same_result_are_different_bytes() -> None:
    from app.services.paid_cache.cache import _encode
    from app.services.paid_cache.keys import CacheKey

    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    assert _encode(key, "same") != _encode(key, "same"), "a delete naming a version could match a later identical write"


async def test_a_non_rejecting_marker_read_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache.cache import PaidResultCache, _encode
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.locks import InProcessLocks
    from tests.support.versioned_store import VersionedStore

    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    store = VersionedStore(stamp_ms=1000)
    store.begin_refresh(ORG, REC)                         # an old refresh, before the object
    store.now_ms += 10_000
    raw = touched_bytes(_encode(key, "text"), 1, origin_ms=store.now_ms) or b""
    store.write(ORG, REC, key.relative_path(), raw)
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    assert await cache._predates_refresh(key, raw) is False
    assert cache._markers == {}, "a read that did not reject was kept"
    store.now_ms += 10_000
    store.begin_refresh(ORG, REC)                         # a newer refresh begins
    assert await cache._predates_refresh(key, raw) is True, "an earlier 'no refresh' read hid a later refresh"


async def test_with_writes_off_a_reuse_creates_no_queue_and_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from app.services.paid_cache.bump import BUMP_AFTER_MS
    from app.services.paid_cache.cache import PaidResultCache, _encode
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.locks import InProcessLocks
    from app.services.paid_cache.run import begin_run, clear_run
    from tests.support.paid_cache import record_in_flight
    from tests.support.versioned_store import VersionedStore

    monkeypatch.setenv("PAID_CACHE_WRITE", "off")
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "page")
    store = VersionedStore(stamp_ms=1)
    store.write(ORG, REC, key.relative_path(), _encode(key, "text"), age_ms=BUMP_AFTER_MS + 5 * 60_000)
    cache = PaidResultCache(store, InProcessLocks())  # type: ignore[arg-type]
    try:
        with record_in_flight(ORG, REC):
            begin_run(ORG, REC, "rev1", "parse")
            assert await cache.get_or_compute(key, AsyncMock()) == "text"
    finally:
        clear_run()
    assert cache._bumps == {}, "a bump queue was made with writes off"
    assert "bump_put" not in store.ops and "presign" not in store.ops
