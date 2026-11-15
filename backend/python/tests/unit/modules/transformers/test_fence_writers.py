"""The real record and entity writers against a cleanup and against a writer that died before it confirmed.

The record writer is ``VectorStore.index_documents`` and the entity writer ``EntityVectorStore._upsert_batch``; the
guard, the pin store and the epochs are the real ones, over in-memory vector stores. Each race also runs with the
fence disabled as a control: the violation must then appear.
"""


import pytest

from app.services.messaging.precision_flip import PrecisionFlip, precision_flip_in
from app.services.vector_db.collection_epoch import CollectionEpochs
from app.services.vector_db.collection_pin import FP32, INT8
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


def _with_epoch(store, db: _FakeDb) -> None:
    """The test store keeps the epoch tag too, and the point id."""
    async def upsert(collection_name, points):
        for p in points:
            meta = p.payload["metadata"]
            db.points.append({
                "id": p.id, "orgId": meta.get("orgId"), "virtualRecordId": meta.get("virtualRecordId"),
                "embedPrecision": meta.get("embedPrecision"), "embedEpoch": meta.get("embedEpoch"),
            })
            store.stored.append(meta.get("embedPrecision"))

    db.upsert_points = upsert


async def _cleanup_records(store, db: _FakeDb) -> None:
    pins = store._guard._pins
    ticket = await pins.epochs.begin_reset(RECORDS)
    db.points.clear()
    await pins.epochs.confirm_reset(ticket)
    await pins.release_collection(RECORDS)


def _fence_off(monkeypatch) -> None:
    async def never(self, fence):
        return False

    monkeypatch.setattr(CollectionEpochs, "moved", never)


# -- records ------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_record_embedded_before_a_cleanup_writes_nothing_and_claims_nothing() -> None:
    db = _FakeDb()
    store = _store(db, INT8)
    _with_epoch(store, db)
    real = store.dense_embeddings.aembed_documents_witnessed

    async def embed_then_cleanup(*a, **k):
        result = await real(*a, **k)
        await _cleanup_records(store, db)  # the cleanup runs while the batch is embedded
        return result

    store.dense_embeddings.aembed_documents_witnessed = embed_then_cleanup
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert precision_flip_in(caught.value) is not None
    assert db.points == [] and await store._guard._pins.get(RECORDS, fresh=True) is None


@pytest.mark.asyncio
async def test_control_without_the_fence_the_record_claims_the_recreated_index(monkeypatch) -> None:
    db = _FakeDb()
    store = _store(db, INT8)
    _with_epoch(store, db)
    real = store.dense_embeddings.aembed_documents_witnessed

    async def embed_then_cleanup(*a, **k):
        result = await real(*a, **k)
        await _cleanup_records(store, db)
        return result

    store.dense_embeddings.aembed_documents_witnessed = embed_then_cleanup
    _fence_off(monkeypatch)
    await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert (await store._guard._pins.get(RECORDS, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_a_cleanup_after_a_batch_is_written_removes_it_and_hands_the_record_back() -> None:
    db = _FakeDb()
    store = _store(db, INT8)
    _with_epoch(store, db)
    written = db.upsert_points

    async def write_then_cleanup(collection_name, points):
        await written(collection_name, points)
        await _cleanup_records(store, db)

    db.upsert_points = write_then_cleanup
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert precision_flip_in(caught.value) is not None
    assert db.points == [] and await store._guard._pins.get(RECORDS, fresh=True) is None


@pytest.mark.asyncio
async def test_a_writer_that_died_before_it_confirmed_is_cleaned_by_the_redelivery() -> None:
    """int8 points of the record under an fp32 pin (a writer that died before its confirmation): the redelivery ends clean."""
    db = _FakeDb()
    db.add(INT8, n=2, vrid=VRID, org=ORG)
    store = _store(db, FP32)
    _with_epoch(store, db)
    await store._guard._pins._claim_unfenced(RECORDS, FP32)
    for _ in range(3):  # the leak check hands the record back once after it has removed the leftovers
        try:
            await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
            break
        except Exception as exc:
            assert precision_flip_in(exc) is not None
    assert {p["embedPrecision"] for p in db.points} == {FP32}


@pytest.mark.asyncio
@pytest.mark.parametrize("reconciliation", [False, True])
async def test_every_record_that_finishes_reaches_the_leak_check(monkeypatch, reconciliation) -> None:
    db = _FakeDb()
    db.add(FP32, n=2, vrid=VRID, org=ORG)
    store = _store(db, FP32)
    _with_epoch(store, db)
    await store._guard._pins._claim_unfenced(RECORDS, FP32)
    calls: list[str] = []
    real = store._check_for_leaks

    async def counting(*a, **k):
        calls.append("checked")
        return await real(*a, **k)

    store._check_for_leaks = counting
    await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID, is_reconciliation=reconciliation)
    assert calls == ["checked"]


@pytest.mark.asyncio
async def test_a_reconciled_record_whose_blocks_are_all_unchanged_reaches_the_leak_check() -> None:
    db = _FakeDb()
    db.add(INT8, n=1, vrid=VRID, org=ORG)
    db.add(FP32, n=1, vrid=VRID, org=ORG)  # a leftover of the other precision
    store = _store(db, INT8)
    _with_epoch(store, db)
    await store._guard._pins._claim_unfenced(RECORDS, INT8)
    calls: list[str] = []
    real = store._check_for_leaks

    async def counting(*a, **k):
        calls.append("checked")
        return await real(*a, **k)

    store._check_for_leaks = counting
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks(), ORG, "rec-1", VRID, is_reconciliation=True)  # nothing new to write
    assert calls == ["checked"] and precision_flip_in(caught.value) is not None
    assert {p["embedPrecision"] for p in db.points} == {INT8}


# -- entities ------------------------------------------------------------------------------------------------------

async def _cleanup_entities(world) -> None:
    ticket = await world.pins.epochs.begin_reset(ENTITIES)
    world.vdb.points[ENTITIES] = []
    await world.pins.epochs.confirm_reset(ticket)
    await world.pins.release_collection(ENTITIES)


@pytest.mark.asyncio
async def test_an_entity_batch_embedded_before_a_cleanup_claims_nothing(world) -> None:
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    real_embed = world.store._embed_changed

    async def embed_then_cleanup(entities, states, fingerprint, precision=None):
        result = await real_embed(entities, states, fingerprint, precision)
        await _cleanup_entities(world)
        return result

    world.store._embed_changed = embed_then_cleanup
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    assert world.vdb.points[ENTITIES] == [] and await world.pins.get(ENTITIES, fresh=True) is None


@pytest.mark.asyncio
async def test_control_without_the_fence_the_entity_batch_claims_the_recreated_index(world, monkeypatch) -> None:
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    real_embed = world.store._embed_changed

    async def embed_then_cleanup(entities, states, fingerprint, precision=None):
        result = await real_embed(entities, states, fingerprint, precision)
        await _cleanup_entities(world)
        return result

    world.store._embed_changed = embed_then_cleanup
    _fence_off(monkeypatch)
    await _entity_write(world)
    assert (await world.pins.get(ENTITIES, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_an_entity_batch_written_across_a_cleanup_is_removed_and_no_pin_is_made(world) -> None:
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    real = world.vdb.upsert_points

    async def write_then_cleanup(collection_name, points):
        await real(collection_name, points)
        await _cleanup_entities(world)

    world.vdb.upsert_points = write_then_cleanup
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    assert world.vdb.points[ENTITIES] == [] and await world.pins.get(ENTITIES, fresh=True) is None


async def _stored_states(world, payloads):
    async def fetch(collection, entities):
        states = {}
        for e in entities:
            pid = world.store._point_id(e.org_id, e.entity_type.value, e.entity_id)
            payload = payloads.get(pid)
            states[pid] = (
                {"exists": False, "connectorIds": [], "recordGroupIds": [], "page_content": None, "metadata": {}}
                if payload is None else
                {"exists": True, "connectorIds": payload["connectorIds"], "recordGroupIds": payload["recordGroupIds"],
                 "page_content": payload["page_content"], "metadata": dict(payload["metadata"])}
            )
        return states

    world.store._fetch_existing_states = fetch


@pytest.mark.asyncio
@pytest.mark.parametrize("handling", ["precision_aware", "none"])
async def test_an_entity_left_by_a_writer_that_died_is_written_again_at_the_pinned_precision(world, handling, monkeypatch) -> None:
    """W (int8) wrote the entity and died before it confirmed; the pin is fp32. The redelivered batch finds the stored
    content the same and must still replace the point, not keep it and not delete it."""
    payloads: dict = {}
    real = world.vdb.upsert_points

    async def keep_payloads(collection_name, points):
        for p in points:
            payloads[p.id] = p.payload
        await real(collection_name, points)

    world.vdb.upsert_points = keep_payloads
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    await _entity_write(world)  # the writer that died: its point is stored, tagged int8
    await world.pins.release(ENTITIES)
    await world.pins._claim_unfenced(ENTITIES, FP32)
    world.served["precision"] = FP32
    await _stored_states(world, payloads)
    if handling == "none":  # the control: the stored point is "the same" whatever its precision
        original = type(world.store)._same_content
        monkeypatch.setattr(
            type(world.store), "_same_content",
            staticmethod(lambda existing, entity, fingerprint, precision=None: original(existing, entity, fingerprint)),
        )
    await _entity_write(world)
    mine = [p for p in world.vdb.points[ENTITIES] if p.get("entityId") == _entity().entity_id]
    if handling == "none":
        assert [p["embedPrecision"] for p in mine] == [INT8]  # the violation: an int8 point under an fp32 pin
    else:
        assert [p["embedPrecision"] for p in mine] == [FP32]  # present, with the pinned precision's tag


@pytest.mark.asyncio
async def test_a_delete_only_fix_would_lose_the_entity(world) -> None:
    """The control for the choice above: deleting the other precision's point leaves the entity in no index at all."""
    world.vdb.add(ENTITIES, INT8, entityType="record", entityId="r1")
    await world.vdb.delete_points(ENTITIES, {"entityId": ["r1"], "embedPrecision": [INT8]})
    assert [p for p in world.vdb.points[ENTITIES] if p.get("entityId") == "r1"] == []


@pytest.mark.asyncio
async def test_a_cleanup_whose_epoch_cannot_be_written_drops_nothing_and_keeps_the_pin(world, monkeypatch) -> None:
    from tests.unit.services.messaging.test_precision_switch_converges import (
        RECORDS as REC,
    )
    from tests.unit.services.messaging.test_precision_switch_converges import (
        _seed_int8_index,
    )

    await _seed_int8_index(world)

    async def failing(self, collection):
        raise ConnectionError("kv down")

    monkeypatch.setattr(CollectionEpochs, "begin_reset", failing)
    with pytest.raises(ConnectionError):
        await world.registry.recreate_records_collections(4)
    assert len(world.vdb.points[REC]) == 3 and (await world.pins.get(REC, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_the_cleanups_tail_sweeps_the_points_of_retired_epochs(world) -> None:
    from tests.unit.services.messaging.test_precision_switch_converges import (
        RECORDS as REC,
    )
    from tests.unit.services.messaging.test_precision_switch_converges import (
        _seed_int8_index,
    )

    await _seed_int8_index(world)
    old = await world.pins.epochs.capture(REC)
    await world.registry.recreate_records_collections(4)  # the cleanup: the old epoch is retired
    world.vdb.add(REC, INT8, embedEpoch=old.token)  # a straggler lands in the new index
    world.vdb.add(REC, FP32, embedEpoch=(await world.pins.epochs.capture(REC)).token)
    await world.registry.sweep_dead_epochs()
    assert {p["embedPrecision"] for p in world.vdb.points[REC]} == {FP32}
