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
from app.services.paid_cache.store import BUMPED, CHANGED, GONE, NodePaidResultStore
from tests.support.fake_paid_result_storage import FakePaidResultStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ORG, REC = "org-1", "rec-1"
HOUR = 3_600_000
K = f"ocr_page/{1:064x}.json"
OLD = b'{"v":1,"kind":"ocr_page","value":"OLD"}'


@pytest.fixture(autouse=True)
def _routes_available() -> None:
    store_module.PAID_RESULT_ROUTES.reset()


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
    assert storage.delete_bodies[-1]["etags"][K] == etag


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
    assert result.deleted == len(storage.delete_bodies[-1]["paths"]) - 1


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

        await sweep_record(store, ORG, REC, now_ms=fake.clock_ms)

        assert lists == 3, "snapshot, second snapshot, and the look right before the delete"
        assert K in fake.stored(ORG, REC)
        assert all("etags" not in body for body in fake.delete_bodies)
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
        assert all("etags" not in body for body in fake.delete_bodies)
        assert result.changed == 0
        assert not store.supports_bump
    finally:
        await fake.stop()


async def test_a_delete_reply_without_changed_reads_as_nothing_kept(storage: FakePaidResultStorage) -> None:
    store = _store(storage)
    storage.fixed_delete_reply = {"deleted": 1}                    # a service that knows the route but not the field
    storage._write(storage.key(ORG, REC, K), OLD)

    assert await store.delete_files(ORG, REC, [K], {K: "x"}) == []


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

    assert outcome == BUMPED and etag != before.etag
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
    assert storage.objects[storage.key(ORG, REC, K)][0] == OLD


async def test_a_store_that_answers_412_for_a_missing_key_is_checked_with_a_read_and_restored(
    storage: FakePaidResultStorage,
) -> None:
    storage.gone_status = 412
    queue = await _queue(storage)
    queue.offer(_entry(storage, etag="old"))
    await queue.drain_once()
    assert storage.objects[storage.key(ORG, REC, K)][0] == OLD


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
