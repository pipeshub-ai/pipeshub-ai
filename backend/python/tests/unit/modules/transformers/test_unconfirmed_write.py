"""What a writer does with a batch that is written and then not confirmed.

Both writers confirm after the write. A *definite* verdict (the pin holds the other precision, or the epoch moved) removes the
batch's records' (or entities') points at that precision and epoch, in the writer's organisation: they are invalid whoever wrote
them. Anything else (a pin that cannot be read or is missing, a cancellation) removes nothing and leaves a debt entry that a
later drain settles by reading the pin and the epoch again. The original error is raised either way.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.precision_flip import FenceMoved, PinAbsent, PrecisionFlip
from app.services.vector_db import precision_guard as pg
from app.services.vector_db.collection_epoch import CollectionEpochs
from app.services.vector_db.collection_pin import FP32, INT8
from app.services.vector_db.precision_guard import (
    UNCONFIRMED_WRITE,
    PrecisionDerivationTimeout,
    PrecisionUnreadable,
    is_definite,
)
from tests.unit.modules.transformers.test_fence_writers import _with_epoch
from tests.unit.modules.transformers.test_vectorstore_precision_records import (  # noqa: F401  (the fixture is used by name)
    ORG,
    VRID,
    _blocks,
    _reset,
    _store,
)
from tests.unit.services.messaging.test_precision_switch_converges import (  # noqa: F401
    ENTITIES,
    _entity,
    _entity_write,
    world,
)
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

RECORDS = "records"
log = logging.getLogger("unconfirmed")


def _chain(exc: BaseException):  # noqa: ANN202
    while exc is not None:
        yield exc
        exc = exc.__cause__ or exc.__context__


def _unreadable_after_write(pins, written: dict) -> None:
    """The pin cannot be read once the batch has landed (the check in front of the write read it fine)."""
    real = pins.get

    async def get(*a, **k):  # noqa: ANN202
        if written["yes"]:
            raise ConnectionError("key-value store down")
        return await real(*a, **k)

    pins.get = get


def _record_store(db: _FakeDb):  # noqa: ANN202
    store = _store(db, INT8)
    _with_epoch(store, db)
    written = {"yes": False}
    upsert = db.upsert_points

    async def upsert_then_flag(collection_name, points):  # noqa: ANN202
        await upsert(collection_name, points)
        written["yes"] = True

    db.upsert_points = upsert_then_flag
    return store, written


async def _index(store) -> BaseException:
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    return caught.value



from langchain_core.documents import Document  # noqa: E402

from app.modules.transformers import vectorstore as vs_module  # noqa: E402


def _other_block_point(db: _FakeDb, epoch: str, block: str = "other-block") -> None:
    db.points.append({
        "id": f"p-{block}", "orgId": ORG, "virtualRecordId": VRID, "embedPrecision": INT8, "embedEpoch": epoch, "blockId": block,
    })


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):  # noqa: ANN202
    monkeypatch.setattr(pg, "RECONFIRM_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(pg, "DELETE_DELAYS", (0.0, 0.0))


async def _debts(guard) -> list:
    return await guard._pins.epochs.debts(RECORDS, guard.scope(ORG))


def _unreadable_times(pins, written: dict, times: int, then=None) -> None:
    """The pin cannot be read for the next *times* reads after the batch landed, then reads as *then* (the real pin when None)."""
    real = pins.get
    left = {"n": times}

    async def get(*a, **k):  # noqa: ANN202
        if written["yes"] and left["n"] > 0:
            left["n"] -= 1
            raise ConnectionError("key-value store down")
        if written["yes"] and then is not None:
            return then
        return await real(*a, **k)

    pins.get = get


# -- records: an unknown outcome removes nothing ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_record_batch_whose_pin_cannot_be_read_removes_nothing_and_leaves_debt() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    _unreadable_after_write(store._guard._pins, written)
    error = await _index(store)
    assert any(isinstance(e, PrecisionUnreadable) for e in _chain(error))
    assert len(db.points) == 1  # the batch's point is not known to be wrong, so it stays
    assert len(await _debts(store._guard)) == 1


@pytest.mark.asyncio
async def test_a_pin_that_cannot_be_read_once_is_confirmed_again_and_the_batch_is_kept() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    _unreadable_times(store._guard._pins, written, 1)
    await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)  # does not raise: the second read confirms it
    assert len(db.points) == 1 and await _debts(store._guard) == []


@pytest.mark.asyncio
async def test_a_pin_that_cannot_be_read_and_then_reads_as_the_other_precision_removes_the_batch() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    _unreadable_times(store._guard._pins, written, 1, then=SimpleNamespace(precision=FP32, pinned_at=0, derived=False))
    error = await _index(store)
    assert any(isinstance(e, PrecisionFlip) for e in _chain(error))
    assert db.points == [] and await _debts(store._guard) == []


@pytest.mark.asyncio
async def test_a_cancellation_while_confirming_removes_nothing_leaves_debt_and_goes_on() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    real = store._guard._pins.get

    async def get(*a, **k):  # noqa: ANN202
        if written["yes"]:
            raise asyncio.CancelledError
        return await real(*a, **k)

    store._guard._pins.get = get
    with pytest.raises(asyncio.CancelledError):
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert len(db.points) == 1
    assert len(await _debts(store._guard)) == 1
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "unconfirmed-write-debt" and not t.done()]


@pytest.mark.asyncio
async def test_a_pin_missing_while_the_epoch_has_not_moved_is_not_a_definite_verdict() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    real = store._guard._pins.get

    async def get(*a, **k):  # noqa: ANN202
        return None if written["yes"] else await real(*a, **k)

    store._guard._pins.get = get
    error = await _index(store)
    assert any(isinstance(e, PinAbsent) for e in _chain(error)) and any(isinstance(e, PrecisionFlip) for e in _chain(error))
    assert len(db.points) == 1  # the pin may be made again from exactly these points
    assert len(await _debts(store._guard)) == 1


# -- records: a definite verdict removes the batch's records' points at that precision and epoch -------------------------------

@pytest.mark.asyncio
async def test_a_record_batch_written_across_a_moved_epoch_is_removed(monkeypatch) -> None:
    db = _FakeDb()
    store, written = _record_store(db)

    async def moved(self, fence):  # noqa: ANN202
        return written["yes"]

    monkeypatch.setattr(CollectionEpochs, "moved", moved)
    error = await _index(store)
    assert any(isinstance(e, FenceMoved) for e in _chain(error))
    assert db.points == []


@pytest.mark.asyncio
async def test_an_int8_record_batch_beside_an_fp32_claim_leaves_no_int8_points_of_the_record_and_nobody_elses() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    pins = store._guard._pins
    real_upsert = db.upsert_points

    async def write_then_fp32_claims(collection_name, points):  # noqa: ANN202
        await pins.release(RECORDS)
        await pins._claim_unfenced(RECORDS, FP32)  # a writer of the other precision claims the pin first
        db.add(FP32, n=1, vrid="other", org=ORG)  # and writes its own record
        await real_upsert(collection_name, points)
        _other_block_point(db, points[0].payload["metadata"]["embedEpoch"])  # another block of the same record, same epoch and precision

    db.upsert_points = write_then_fp32_claims
    await _index(store)
    assert [p["virtualRecordId"] for p in db.points if p["embedPrecision"] == INT8] == []  # the record's int8 points are all invalid
    assert [p["virtualRecordId"] for p in db.points] == ["other"]  # another record's points stay


@pytest.mark.asyncio
async def test_a_definite_verdict_removes_without_the_lease_and_leaves_other_epochs_alone(monkeypatch) -> None:
    db = _FakeDb()
    store, written = _record_store(db)

    async def moved(self, fence):  # noqa: ANN202
        return written["yes"]

    monkeypatch.setattr(CollectionEpochs, "moved", moved)
    _other_block_point(db, "another-epoch", "kept")  # another epoch's point of the same record
    handle = SimpleNamespace(confirm=AsyncMock(return_value=False))  # no lease is consulted for a definite verdict
    token = vs_module._HELD_LEASE.set(handle)
    try:
        await _index(store)
    finally:
        vs_module._HELD_LEASE.reset(token)
    assert [p["id"] for p in db.points] == ["p-kept"]


@pytest.mark.asyncio
async def test_a_removal_that_fails_once_is_repeated(monkeypatch) -> None:
    db = _FakeDb()
    store, written = _record_store(db)

    async def moved(self, fence):  # noqa: ANN202
        return written["yes"]

    monkeypatch.setattr(CollectionEpochs, "moved", moved)
    real_delete, calls = db.delete_points, {"n": 0}

    async def flaky(*a, **k):  # noqa: ANN202
        if not written["yes"]:
            return await real_delete(*a, **k)
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("vector store down")
        return await real_delete(*a, **k)

    db.delete_points = flaky
    error = await _index(store)
    assert any(isinstance(e, FenceMoved) for e in _chain(error))
    assert calls["n"] == 2 and db.points == [] and await _debts(store._guard) == []


@pytest.mark.asyncio
async def test_a_removal_that_keeps_failing_becomes_debt_and_the_original_error_is_raised(monkeypatch) -> None:
    db = _FakeDb()
    store, written = _record_store(db)

    async def moved(self, fence):  # noqa: ANN202
        return written["yes"]

    monkeypatch.setattr(CollectionEpochs, "moved", moved)
    refused = AsyncMock(side_effect=ConnectionError("vector store down"))
    real_delete = db.delete_points

    async def refuse_after_the_write(*a, **k):  # noqa: ANN202
        return await (refused if written["yes"] else real_delete)(*a, **k)

    db.delete_points = refuse_after_the_write
    error = await _index(store)
    assert any(isinstance(e, FenceMoved) for e in _chain(error))
    assert refused.await_count == 3 and len(await _debts(store._guard)) == 1


@pytest.mark.asyncio
async def test_a_removal_that_times_out_is_not_repeated_and_becomes_debt(monkeypatch) -> None:
    db = _FakeDb()
    store, written = _record_store(db)

    async def moved(self, fence):  # noqa: ANN202
        return written["yes"]

    monkeypatch.setattr(CollectionEpochs, "moved", moved)
    monkeypatch.setattr(pg, "DELETE_TIMEOUT_SECONDS", 0.05)
    calls = {"n": 0}

    real_delete = db.delete_points

    async def hangs(*a, **k):  # noqa: ANN202
        if not written["yes"]:
            return await real_delete(*a, **k)
        calls["n"] += 1
        await asyncio.sleep(5)
        return None

    db.delete_points = hangs
    await _index(store)
    assert calls["n"] == 1 and len(await _debts(store._guard)) == 1  # it may still be applied, so no second request


@pytest.mark.asyncio
async def test_a_batch_that_names_no_record_logs_an_error_instead_of_removing_nothing_silently() -> None:
    db = _FakeDb()
    store, _ = _record_store(db)
    guard = SimpleNamespace(
        confirm_after_write=AsyncMock(side_effect=PrecisionUnreadable("no")), settle_unconfirmed=AsyncMock(),
    )
    with pytest.raises(PrecisionUnreadable):
        await store._confirm_batch(guard, "records", INT8, SimpleNamespace(token="t0"), [Document(page_content="x", metadata={})])
    store.logger.error.assert_called()
    guard.settle_unconfirmed.assert_not_awaited()


# -- the guard: a removal re-reads the verdict before every attempt, and the drain settles debt ------------------------------------

async def _guard_world():  # noqa: ANN202
    from tests.unit.services.vector_db.test_collection_pin import _guard

    db = _FakeDb()
    guard, pins, config = _guard(db)
    await guard.verify_batch(RECORDS, ORG, INT8, fallback=False, intent=INT8)
    fence = await guard.capture(RECORDS)
    return db, guard, pins, fence


def _written(db: _FakeDb, fence, vrid: str = VRID, precision: str = INT8) -> None:
    db.points.append({"id": f"w-{vrid}", "orgId": ORG, "virtualRecordId": vrid, "embedPrecision": precision, "embedEpoch": fence.token})


@pytest.mark.asyncio
async def test_a_repeated_removal_reads_the_verdict_again_and_flip_and_back_keeps_the_other_writers_points() -> None:
    """The pin was the other precision (a definite flip), the first removal failed, the pin was released and made int8 again and
    another writer wrote the same ids: the second attempt finds the batch's precision is the pin's and deletes nothing."""
    db, guard, pins, fence = await _guard_world()
    await pins.release(RECORDS)
    await pins._claim_unfenced(RECORDS, FP32)
    _written(db, fence)
    real, calls = db.delete_points, {"n": 0}

    async def fails_while_the_pin_goes_back(*a, **k):  # noqa: ANN202
        calls["n"] += 1
        await pins.release(RECORDS)
        await pins._claim_unfenced(RECORDS, INT8)  # flip back
        raise ConnectionError("vector store down")

    db.delete_points = fails_while_the_pin_goes_back
    settled = await guard.settle_unconfirmed(RECORDS, ORG, INT8, fence, [{"virtualRecordId": [VRID]}], PrecisionFlip("fp32"))
    db.delete_points = real
    assert isinstance(settled, PrecisionFlip) and calls["n"] == 1
    assert [p["id"] for p in db.points] == [f"w-{VRID}"] and await _debts(SimpleNamespace(_pins=pins, scope=guard.scope)) == []


@pytest.mark.asyncio
async def test_a_removal_applied_late_after_the_index_emptied_and_was_rewritten_takes_the_rewrite_a_known_limit() -> None:
    """The one schedule a definite-verdict removal cannot rule out, pinned so a change to it is seen: the request was sent (and
    outlived its bound), the index was emptied by someone else, the pin was replaced, another writer wrote the same ids at the same
    epoch, then the store applies the old request. It removes those points; the same-bytes skip or a reindex writes them again."""
    db, guard, pins, fence = await _guard_world()
    await pins.release(RECORDS)
    await pins._claim_unfenced(RECORDS, FP32)
    _written(db, fence)
    gate = asyncio.Event()
    real = db.delete_points
    applied: list = []

    async def sent_then_applied_late(collection, expression):  # noqa: ANN202
        async def apply() -> None:
            await gate.wait()
            await real(collection, expression)  # the filter is evaluated when the store applies it
            applied.append(True)

        task = asyncio.ensure_future(apply())
        await asyncio.shield(task)

    db.delete_points = sent_then_applied_late
    pg.DELETE_TIMEOUT_SECONDS, bound = 0.05, pg.DELETE_TIMEOUT_SECONDS
    try:
        await guard.settle_unconfirmed(RECORDS, ORG, INT8, fence, [{"virtualRecordId": [VRID]}], PrecisionFlip("fp32"))
    finally:
        pg.DELETE_TIMEOUT_SECONDS = bound
    db.points.clear()  # another actor empties the index
    await pins.release(RECORDS)
    await pins._claim_unfenced(RECORDS, INT8)  # the pin is replaced
    _written(db, fence)  # another writer writes the same ids at the same epoch
    gate.set()
    await asyncio.sleep(0.05)
    assert applied and db.points == []  # the known limit


@pytest.mark.asyncio
async def test_a_cancellation_during_a_removal_leaves_debt_for_what_is_not_known_to_be_removed() -> None:
    db, guard, pins, fence = await _guard_world()
    await pins.release(RECORDS)
    await pins._claim_unfenced(RECORDS, FP32)
    _written(db, fence)

    async def hangs(*a, **k):  # noqa: ANN202
        await asyncio.sleep(30)

    db.delete_points = hangs
    task = asyncio.ensure_future(
        guard.settle_unconfirmed(RECORDS, ORG, INT8, fence, [{"virtualRecordId": [VRID]}], PrecisionFlip("fp32")),
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(await _debts(SimpleNamespace(_pins=pins, scope=guard.scope))) == 1


@pytest.mark.asyncio
async def test_the_drain_keeps_points_the_pin_and_the_epoch_call_right_and_removes_wrong_ones() -> None:
    db, guard, pins, fence = await _guard_world()
    _written(db, fence)
    await guard.note_debt(RECORDS, ORG, INT8, fence, {"virtualRecordId": [VRID]})
    scope = SimpleNamespace(_pins=pins, scope=guard.scope)
    assert len(await _debts(scope)) == 1
    await guard.drain_debt(RECORDS, ORG)  # pin int8, epoch current: valid
    assert len(db.points) == 1 and await _debts(scope) == []
    await guard.note_debt(RECORDS, ORG, INT8, fence, {"virtualRecordId": [VRID]})
    await pins.release(RECORDS)
    await pins._claim_unfenced(RECORDS, FP32)
    await guard.drain_debt(RECORDS, ORG)  # pin fp32: the int8 points are wrong
    assert db.points == [] and await _debts(scope) == []


@pytest.mark.asyncio
async def test_the_drain_keeps_an_entry_it_cannot_judge_and_one_of_another_organisation_and_drops_a_gone_collection_or_an_old_entry() -> None:
    db, guard, pins, fence = await _guard_world()
    scope = SimpleNamespace(_pins=pins, scope=guard.scope)
    _written(db, fence)
    await guard.note_debt(RECORDS, ORG, INT8, fence, {"virtualRecordId": [VRID]})
    await guard.note_debt(RECORDS, "someone-else", INT8, fence, {"virtualRecordId": [VRID]})
    real = pins.get
    pins.get = AsyncMock(side_effect=ConnectionError("down"))
    await guard.drain_debt(RECORDS, ORG)
    assert len(await _debts(scope)) == 2  # unreadable: kept
    pins.get = real
    await guard.drain_debt(RECORDS, ORG)
    assert [e["org"] for _, e in await _debts(scope)] == ["someone-else"]  # the other organisation's entry is not ours to settle
    db.get_collection_info = AsyncMock(return_value=SimpleNamespace(exists=False, points_count=0))
    await guard.note_debt(RECORDS, ORG, INT8, fence, {"virtualRecordId": ["v2"]})
    await guard.drain_debt(RECORDS, ORG)
    assert [e["org"] for _, e in await _debts(scope)] == ["someone-else"]  # a collection that is gone: drained
    db.get_collection_info = AsyncMock(return_value=SimpleNamespace(exists=True, points_count=1))
    for path, _ in await _debts(scope):
        await pins.epochs.drop_debt(path)
    await guard.note_debt(RECORDS, ORG, INT8, fence, {"virtualRecordId": ["v3"]})
    pins.epochs._clock = lambda: 4_000_000_000.0  # far past the age cap
    await guard.drain_debt(RECORDS, ORG)
    assert await _debts(scope) == []


@pytest.mark.asyncio
async def test_the_same_batch_noted_twice_is_one_entry_and_a_reset_purges_the_entries() -> None:
    db, guard, pins, fence = await _guard_world()
    scope = SimpleNamespace(_pins=pins, scope=guard.scope)
    for _ in range(3):
        await guard.note_debt(RECORDS, ORG, INT8, fence, {"virtualRecordId": [VRID]})
    assert len(await _debts(scope)) == 1
    ticket = await pins.epochs.begin_reset(RECORDS)
    await pins.epochs.confirm_reset(ticket)
    assert await _debts(scope) == []


@pytest.mark.asyncio
async def test_a_pin_that_is_missing_keeps_the_entry_until_a_pin_says_which_way() -> None:
    db, guard, pins, fence = await _guard_world()
    scope = SimpleNamespace(_pins=pins, scope=guard.scope)
    _written(db, fence)
    await guard.note_debt(RECORDS, ORG, INT8, fence, {"virtualRecordId": [VRID]})
    await pins.release(RECORDS)
    await guard.drain_debt(RECORDS, ORG)
    assert len(db.points) == 1 and len(await _debts(scope)) == 1  # unknown: neither removed nor forgotten
    await pins._claim_unfenced(RECORDS, INT8)
    await guard.drain_debt(RECORDS, ORG)
    assert len(db.points) == 1 and await _debts(scope) == []  # the pin is the batch's: right


def test_is_definite_for_every_member_of_the_unconfirmed_set() -> None:
    assert set(UNCONFIRMED_WRITE) == {PrecisionFlip, PrecisionUnreadable}
    assert is_definite(PrecisionFlip("x")) and is_definite(FenceMoved("x"))
    assert not is_definite(PinAbsent("x")) and not is_definite(PrecisionUnreadable("x")) and not is_definite(PrecisionDerivationTimeout("x"))
    assert not is_definite(asyncio.CancelledError()) and not is_definite(ConnectionError("x"))
    for member in UNCONFIRMED_WRITE:
        subclasses = [member, *member.__subclasses__()]
        for cls in subclasses:
            assert is_definite(cls("x")) == (issubclass(cls, PrecisionFlip) and cls is not PinAbsent)


def test_no_code_treats_a_missing_pin_as_a_moved_epoch() -> None:
    """``PinAbsent`` is a flip that is not definite: a handler that caught ``FenceMoved`` to mean "the index is reset" would lose that."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[4] / "app"
    hits = [
        str(path.relative_to(root)) for path in root.rglob("*.py")
        if re.search(r"except\s*\(?[^\n:]*\bFenceMoved\b|isinstance\([^)]*\bFenceMoved\b", path.read_text(encoding="utf-8"))
    ]
    # The guard's own read path catches it where a claim is refused after a reset (never a confirmation of a written batch).
    assert hits == ["services/vector_db/precision_guard.py"]


# -- entities ---------------------------------------------------------------------------------------------------------------

def _entity_unreadable(world, times: int = 99) -> dict:  # noqa: F811
    written = {"yes": False}
    real = world.vdb.upsert_points

    async def upsert_then_flag(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        written["yes"] = True

    world.vdb.upsert_points = upsert_then_flag
    _unreadable_times(world.store._guard()._pins, written, times)
    return written


def _entity_points(world) -> list:  # noqa: F811
    return [p for p in world.vdb.points[ENTITIES] if p.get("entityId") == _entity().entity_id]


async def _entity_debts(world) -> list:  # noqa: F811
    guard = world.store._guard()
    return await guard._pins.epochs.debts(ENTITIES, guard.scope(ORG))


async def _int8_entities(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    await world.pins._claim_unfenced(ENTITIES, INT8)


@pytest.mark.asyncio
async def test_an_entity_batch_whose_pin_cannot_be_read_removes_nothing_leaves_debt_and_raises(world) -> None:  # noqa: F811
    await _int8_entities(world)
    _entity_unreadable(world)
    with pytest.raises(PrecisionUnreadable):
        await _entity_write(world)
    assert len(_entity_points(world)) == 1  # a shared point is not removed on a guess
    assert len(await _entity_debts(world)) == 1


@pytest.mark.asyncio
async def test_an_entity_confirmation_that_reads_the_second_time_keeps_the_batch(world) -> None:  # noqa: F811
    await _int8_entities(world)
    _entity_unreadable(world, times=1)
    await _entity_write(world)
    assert len(_entity_points(world)) == 1 and await _entity_debts(world) == []


@pytest.mark.asyncio
async def test_a_second_process_that_adopts_an_unconfirmed_entity_point_is_not_undone_by_the_first(world) -> None:  # noqa: F811
    """A writer's batch could not be confirmed; another writer finds the point as it is (same content) and relies on it. Nothing is
    removed, and the drain, reading the pin and the epoch, calls the point right and forgets the entry."""
    await _int8_entities(world)
    _entity_unreadable(world)
    with pytest.raises(PrecisionUnreadable):
        await _entity_write(world)
    world.store._guard()._pins.get = world.pins.get  # the other writer, whose reads work
    await _entity_write(world)
    assert len(_entity_points(world)) == 1
    await world.store._guard().drain_debt(ENTITIES, ORG)
    assert len(_entity_points(world)) == 1 and await _entity_debts(world) == []


@pytest.mark.asyncio
async def test_an_entity_batch_written_across_a_moved_epoch_is_removed(world, monkeypatch) -> None:  # noqa: F811
    await _int8_entities(world)
    written = {"yes": False}
    real = world.vdb.upsert_points

    async def upsert_then_flag(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        written["yes"] = True

    world.vdb.upsert_points = upsert_then_flag

    async def moved(self, fence):  # noqa: ANN202
        return written["yes"]

    monkeypatch.setattr(CollectionEpochs, "moved", moved)
    with pytest.raises(FenceMoved):
        await _entity_write(world)
    assert _entity_points(world) == []


@pytest.mark.asyncio
async def test_an_int8_entity_batch_beside_an_fp32_claim_is_removed(world) -> None:  # noqa: F811
    await _int8_entities(world)
    real = world.vdb.upsert_points

    async def write_then_fp32_claims(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        await world.pins.release(ENTITIES)
        await world.pins._claim_unfenced(ENTITIES, FP32)

    world.vdb.upsert_points = write_then_fp32_claims
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    assert INT8 not in {p["embedPrecision"] for p in _entity_points(world)}


@pytest.mark.asyncio
async def test_a_cancellation_while_confirming_an_entity_batch_removes_nothing_and_leaves_debt(world) -> None:  # noqa: F811
    await _int8_entities(world)
    written = {"yes": False}
    real_upsert = world.vdb.upsert_points
    pins = world.store._guard()._pins
    real_get = pins.get

    async def upsert_then_flag(collection_name, points):  # noqa: ANN202
        await real_upsert(collection_name, points)
        written["yes"] = True

    async def get(*a, **k):  # noqa: ANN202
        if written["yes"]:
            raise asyncio.CancelledError
        return await real_get(*a, **k)

    world.vdb.upsert_points = upsert_then_flag
    pins.get = get
    with pytest.raises(asyncio.CancelledError):
        await _entity_write(world)
    assert len(_entity_points(world)) == 1 and len(await _entity_debts(world)) == 1


@pytest.mark.asyncio
async def test_an_entity_removal_that_fails_once_is_repeated_and_one_that_keeps_failing_becomes_debt(world, monkeypatch) -> None:  # noqa: F811
    await _int8_entities(world)

    async def moved_after(self, fence):  # noqa: ANN202
        return written["yes"]

    written = {"yes": False}
    real = world.vdb.upsert_points

    async def upsert_then_flag(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        written["yes"] = True

    world.vdb.upsert_points = upsert_then_flag
    monkeypatch.setattr(CollectionEpochs, "moved", moved_after)
    real_delete, calls = world.vdb.delete_points, {"n": 0}

    async def flaky(*a, **k):  # noqa: ANN202
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("vector store down")
        return await real_delete(*a, **k)

    world.vdb.delete_points = flaky
    with pytest.raises(FenceMoved):
        await _entity_write(world)
    assert calls["n"] == 2 and _entity_points(world) == [] and await _entity_debts(world) == []
    written["yes"] = False
    world.vdb.delete_points = AsyncMock(side_effect=ConnectionError("down"))
    with pytest.raises(FenceMoved):
        await _entity_write(world)
    assert len(await _entity_debts(world)) == 1


@pytest.mark.asyncio
async def test_upsert_entities_batch_does_not_turn_an_unreadable_confirmation_into_a_count(world) -> None:  # noqa: F811
    await _int8_entities(world)
    _entity_unreadable(world)
    with pytest.raises(PrecisionUnreadable):
        await world.store.upsert_entities_batch([_entity()])


@pytest.mark.asyncio
async def test_an_entity_rollback_that_names_no_entity_logs_an_error(world) -> None:  # noqa: F811
    world.store.logger = MagicMock()
    assert world.store._written_extras(ORG, INT8, {}, []) == []
    world.store.logger.error.assert_called()
