"""A write whose precision cannot be confirmed is removed and handed back, as a refused one is.

Both writers confirm after the write. When the confirmation cannot read the pin or the epoch, nothing says the batch
is right, so its own points (this record or entities, this precision, this fence's epoch, the writer's organisation)
go, and the original error is raised. A failed removal never replaces that error.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging.precision_flip import FenceMoved, PrecisionFlip
from app.services.vector_db.collection_epoch import CollectionEpochs
from app.services.vector_db.collection_pin import FP32, INT8
from app.services.vector_db.precision_guard import PrecisionUnreadable
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


# -- records ---------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_record_batch_whose_pin_cannot_be_read_is_removed_and_the_error_is_raised() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    _unreadable_after_write(store._guard._pins, written)
    error = await _index(store)
    assert any(isinstance(e, PrecisionUnreadable) for e in _chain(error))
    assert db.points == []


@pytest.mark.asyncio
async def test_a_record_batch_whose_removal_fails_still_raises_the_original_error() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    _unreadable_after_write(store._guard._pins, written)
    real_delete = db.delete_points
    refused = AsyncMock(side_effect=ConnectionError("vector store down"))

    async def delete(*a, **k):  # noqa: ANN202
        return await (refused if written["yes"] else real_delete)(*a, **k)

    db.delete_points = delete
    error = await _index(store)
    assert any(isinstance(e, PrecisionUnreadable) for e in _chain(error))
    refused.assert_awaited()


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
async def test_an_int8_record_batch_beside_an_fp32_claim_with_an_unreadable_pin_leaves_no_int8_points() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    pins = store._guard._pins
    real_upsert = db.upsert_points

    async def write_then_fp32_claims(collection_name, points):  # noqa: ANN202
        await pins._claim_unfenced(RECORDS, FP32)  # a writer of the other precision claims the pin first
        db.add(FP32, n=1, vrid="other", org=ORG)  # and writes its own record
        await real_upsert(collection_name, points)

    db.upsert_points = write_then_fp32_claims
    _unreadable_after_write(pins, written)
    await _index(store)
    assert INT8 not in {p["embedPrecision"] for p in db.points}
    assert [p["virtualRecordId"] for p in db.points] == ["other"]  # another writer's points stay


# -- entities --------------------------------------------------------------------------------------------------------

def _entity_unreadable(world) -> dict:  # noqa: F811
    written = {"yes": False}
    real = world.vdb.upsert_points

    async def upsert_then_flag(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        written["yes"] = True

    world.vdb.upsert_points = upsert_then_flag
    _unreadable_after_write(world.store._guard()._pins, written)
    return written


def _entity_points(world) -> list:  # noqa: F811
    return [p for p in world.vdb.points[ENTITIES] if p.get("entityId") == _entity().entity_id]


@pytest.mark.asyncio
async def test_an_entity_batch_whose_pin_cannot_be_read_is_removed_and_the_error_is_raised(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    await world.pins._claim_unfenced(ENTITIES, INT8)
    _entity_unreadable(world)
    with pytest.raises(PrecisionUnreadable):
        await _entity_write(world)
    assert _entity_points(world) == []


@pytest.mark.asyncio
async def test_an_entity_batch_whose_removal_fails_still_raises_the_original_error(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    await world.pins._claim_unfenced(ENTITIES, INT8)
    _entity_unreadable(world)
    world.vdb.delete_points = AsyncMock(side_effect=ConnectionError("vector store down"))
    with pytest.raises(PrecisionUnreadable):
        await _entity_write(world)
    world.vdb.delete_points.assert_awaited()


@pytest.mark.asyncio
async def test_an_entity_batch_written_across_a_moved_epoch_is_removed(world, monkeypatch) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    await world.pins._claim_unfenced(ENTITIES, INT8)
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
async def test_an_int8_entity_batch_beside_an_fp32_claim_with_an_unreadable_pin_leaves_no_int8_points(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    await world.pins._claim_unfenced(ENTITIES, INT8)
    written = _entity_unreadable(world)
    real = world.vdb.upsert_points

    async def write_then_fp32_claims(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        await world.pins.release(ENTITIES)
        await world.pins._claim_unfenced(ENTITIES, FP32)

    world.vdb.upsert_points = write_then_fp32_claims
    with pytest.raises((PrecisionUnreadable, PrecisionFlip)):
        await _entity_write(world)
    assert written["yes"]
    assert INT8 not in {p["embedPrecision"] for p in _entity_points(world)}


# -- the removal is the batch's own, and only while the writer still owns the record ------------------------------------

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from langchain_core.documents import Document  # noqa: E402

from app.modules.transformers import vectorstore as vs_module  # noqa: E402


def _other_block_point(db: _FakeDb, epoch: str, block: str = "other-block") -> None:
    db.points.append({
        "id": f"p-{block}", "orgId": ORG, "virtualRecordId": VRID, "embedPrecision": INT8, "embedEpoch": epoch, "blockId": block,
    })


def _add_other_block_when_written(db: _FakeDb, written: dict) -> None:
    """A point of the same record, org, precision and epoch for a block this batch did not write."""
    inner = db.upsert_points

    async def upsert(collection_name, points):  # noqa: ANN202
        await inner(collection_name, points)
        _other_block_point(db, points[0].payload["metadata"]["embedEpoch"])

    db.upsert_points = upsert


@pytest.mark.asyncio
async def test_the_rollback_of_a_record_batch_keeps_another_block_of_the_same_record_and_epoch() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    _add_other_block_when_written(db, written)
    _unreadable_after_write(store._guard._pins, written)
    await _index(store)
    assert [p["id"] for p in db.points] == ["p-other-block"]  # the batch's own block went, the other block's confirmed point stayed


@pytest.mark.asyncio
async def test_a_lost_lease_removes_nothing_and_leaves_the_batch_to_the_holder(monkeypatch) -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    _unreadable_after_write(store._guard._pins, written)
    handle = SimpleNamespace(confirm=AsyncMock(side_effect=lambda: not written["yes"]))  # held until the batch has landed
    token = vs_module._HELD_LEASE.set(handle)
    try:
        error = await _index(store)
    finally:
        vs_module._HELD_LEASE.reset(token)
    assert any(isinstance(e, PrecisionUnreadable) for e in _chain(error))
    assert len(db.points) == 1  # nothing was removed: the run that holds the record now owns it
    store.logger.warning.assert_called()


@pytest.mark.asyncio
async def test_a_held_lease_lets_the_batch_remove_its_own_points() -> None:
    db = _FakeDb()
    store, written = _record_store(db)
    _unreadable_after_write(store._guard._pins, written)
    handle = SimpleNamespace(confirm=AsyncMock(return_value=True))
    token = vs_module._HELD_LEASE.set(handle)
    try:
        await _index(store)
    finally:
        vs_module._HELD_LEASE.reset(token)
    assert db.points == [] and handle.confirm.await_count >= 1


@pytest.mark.asyncio
async def test_a_cancellation_while_confirming_removes_the_batch_and_goes_on() -> None:
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
    assert db.points == []


@pytest.mark.asyncio
async def test_a_batch_that_names_no_record_logs_an_error_instead_of_removing_nothing_silently() -> None:
    db = _FakeDb()
    store, _ = _record_store(db)
    guard = SimpleNamespace(
        confirm_after_write=AsyncMock(side_effect=PrecisionUnreadable("no")), delete_written=AsyncMock(),
    )
    with pytest.raises(PrecisionUnreadable):
        await store._confirm_batch(guard, "records", INT8, SimpleNamespace(token="t0"), [Document(page_content="x", metadata={})])
    store.logger.error.assert_called()
    guard.delete_written.assert_not_awaited()


# -- entities --------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_rollback_of_an_entity_batch_keeps_another_entity_of_the_same_epoch(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    await world.pins._claim_unfenced(ENTITIES, INT8)
    token = (await world.store._guard().capture(ENTITIES)).token
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="someone-else", embedEpoch=token)
    _entity_unreadable(world)
    with pytest.raises(PrecisionUnreadable):
        await _entity_write(world)
    assert _entity_points(world) == []
    assert [p["entityId"] for p in world.vdb.points[ENTITIES] if p.get("embedEpoch") == token] == ["someone-else"]


@pytest.mark.asyncio
async def test_a_cancellation_while_confirming_an_entity_batch_removes_it_and_goes_on(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    world.served["precision"] = INT8
    await world.pins._claim_unfenced(ENTITIES, INT8)
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
    assert _entity_points(world) == []


@pytest.mark.asyncio
async def test_an_entity_rollback_that_names_no_entity_logs_an_error(world) -> None:  # noqa: F811
    world.store.logger = MagicMock()
    await world.store._remove_written(ORG, INT8, {}, [], SimpleNamespace(token="t0"))
    world.store.logger.error.assert_called()
