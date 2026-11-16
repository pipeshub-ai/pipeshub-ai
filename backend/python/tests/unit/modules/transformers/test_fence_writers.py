"""The real record and entity writers against a cleanup and against a writer that died before it confirmed.

The record writer is ``VectorStore.index_documents`` and the entity writer ``EntityVectorStore._upsert_batch``; the
guard, the pin store and the epochs are the real ones, over in-memory vector stores. Each race also runs with the
fence disabled as a control: the violation must then appear.
"""


import logging

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
    async def upsert(collection_name, points):  # noqa: ANN202
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
    async def never(self, fence):  # noqa: ANN202
        return False

    monkeypatch.setattr(CollectionEpochs, "moved", never)


# -- records ------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_record_embedded_before_a_cleanup_writes_nothing_and_claims_nothing() -> None:
    db = _FakeDb()
    store = _store(db, INT8)
    _with_epoch(store, db)
    real = store.dense_embeddings.aembed_documents_witnessed

    async def embed_then_cleanup(*a, **k):  # noqa: ANN202
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

    async def embed_then_cleanup(*a, **k):  # noqa: ANN202
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

    async def write_then_cleanup(collection_name, points):  # noqa: ANN202
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
    else:
        pytest.fail("no redelivery completed")  # three hand-backs would leave the points as they were, which also pass below
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

    async def counting(*a, **k):  # noqa: ANN202
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

    async def counting(*a, **k):  # noqa: ANN202
        calls.append("checked")
        return await real(*a, **k)

    store._check_for_leaks = counting
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks(), ORG, "rec-1", VRID, is_reconciliation=True)  # nothing new to write
    assert calls == ["checked"] and precision_flip_in(caught.value) is not None
    assert {p["embedPrecision"] for p in db.points} == {INT8}


# -- entities ------------------------------------------------------------------------------------------------------

async def _cleanup_entities(world) -> None:  # noqa: F811
    ticket = await world.pins.epochs.begin_reset(ENTITIES)
    world.vdb.points[ENTITIES] = []
    await world.pins.epochs.confirm_reset(ticket)
    await world.pins.release_collection(ENTITIES)


@pytest.mark.asyncio
async def test_an_entity_batch_embedded_before_a_cleanup_claims_nothing(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    real_embed = world.store._embed_changed

    async def embed_then_cleanup(entities, states, fingerprint, precision=None):  # noqa: ANN202
        result = await real_embed(entities, states, fingerprint, precision)
        await _cleanup_entities(world)
        return result

    world.store._embed_changed = embed_then_cleanup
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    assert world.vdb.points[ENTITIES] == [] and await world.pins.get(ENTITIES, fresh=True) is None


@pytest.mark.asyncio
async def test_control_without_the_fence_the_entity_batch_claims_the_recreated_index(world, monkeypatch) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    real_embed = world.store._embed_changed

    async def embed_then_cleanup(entities, states, fingerprint, precision=None):  # noqa: ANN202
        result = await real_embed(entities, states, fingerprint, precision)
        await _cleanup_entities(world)
        return result

    world.store._embed_changed = embed_then_cleanup
    _fence_off(monkeypatch)
    await _entity_write(world)
    assert (await world.pins.get(ENTITIES, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_an_entity_batch_written_across_a_cleanup_is_removed_and_no_pin_is_made(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    real = world.vdb.upsert_points

    async def write_then_cleanup(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        await _cleanup_entities(world)

    world.vdb.upsert_points = write_then_cleanup
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    assert world.vdb.points[ENTITIES] == [] and await world.pins.get(ENTITIES, fresh=True) is None


async def _stored_states(world, payloads):  # noqa: ANN202, F811
    async def fetch(collection, entities, fence=None):  # noqa: ANN202
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
async def test_an_entity_left_by_a_writer_that_died_is_written_again_at_the_pinned_precision(world, handling, monkeypatch) -> None:  # noqa: F811
    """W (int8) wrote the entity and died before it confirmed; the pin is fp32. The redelivered batch finds the stored
    content the same and must still replace the point, not keep it and not delete it."""
    payloads: dict = {}
    real = world.vdb.upsert_points

    async def keep_payloads(collection_name, points):  # noqa: ANN202
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
async def test_a_delete_only_fix_would_lose_the_entity(world) -> None:  # noqa: F811
    """The control for the choice above: deleting the other precision's point leaves the entity in no index at all."""
    world.vdb.add(ENTITIES, INT8, entityType="record", entityId="r1")
    await world.vdb.delete_points(ENTITIES, {"entityId": ["r1"], "embedPrecision": [INT8]})
    assert [p for p in world.vdb.points[ENTITIES] if p.get("entityId") == "r1"] == []


@pytest.mark.asyncio
async def test_a_cleanup_whose_epoch_cannot_be_written_drops_nothing_and_keeps_the_pin(world, monkeypatch) -> None:  # noqa: F811
    from tests.unit.services.messaging.test_precision_switch_converges import (
        RECORDS as REC,
    )
    from tests.unit.services.messaging.test_precision_switch_converges import (
        _seed_int8_index,
    )

    await _seed_int8_index(world)

    async def failing(self, collection):  # noqa: ANN202
        raise ConnectionError("kv down")

    monkeypatch.setattr(CollectionEpochs, "begin_reset", failing)
    with pytest.raises(ConnectionError):
        await world.registry.recreate_records_collections(4)
    assert len(world.vdb.points[REC]) == 3 and (await world.pins.get(REC, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_the_cleanups_tail_sweeps_the_points_of_retired_epochs(world) -> None:  # noqa: F811
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
    await world.registry.sweep_dead_epochs([ORG])
    assert {p["embedPrecision"] for p in world.vdb.points[REC]} == {FP32}


# -- a batch that confirms against another writer's pin of its own precision ------------------------------------------

@pytest.mark.asyncio
async def test_a_record_confirmed_after_a_cleanup_and_a_claim_of_its_own_precision_is_removed() -> None:
    db = _FakeDb()
    store = _store(db, INT8)
    _with_epoch(store, db)
    written = db.upsert_points

    async def cleanup_claim_then_write(collection_name, points):  # noqa: ANN202
        await _cleanup_records(store, db)
        await store._guard._pins._claim_unfenced(RECORDS, INT8)  # another writer's pin, of the same precision
        await written(collection_name, points)

    db.upsert_points = cleanup_claim_then_write
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert precision_flip_in(caught.value) is not None
    assert db.points == [] and (await store._guard._pins.get(RECORDS, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_an_entity_batch_confirmed_after_a_cleanup_and_a_claim_of_its_own_precision_is_removed(world) -> None:  # noqa: F811
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    real = world.vdb.upsert_points

    async def cleanup_claim_then_write(collection_name, points):  # noqa: ANN202
        await _cleanup_entities(world)
        await world.pins._claim_unfenced(ENTITIES, INT8)
        await real(collection_name, points)

    world.vdb.upsert_points = cleanup_claim_then_write
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    assert world.vdb.points[ENTITIES] == [] and (await world.pins.get(ENTITIES, fresh=True)).precision == INT8


# -- what a point carries --------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_record_point_carries_the_epoch_it_was_written_under() -> None:
    db = _FakeDb()
    store = _store(db, FP32)
    _with_epoch(store, db)
    await store.index_documents(_blocks("b1", "b2"), ORG, "rec-1", VRID)
    current = await store._guard._pins.epochs.current(RECORDS)
    assert current and [p["embedEpoch"] for p in db.points] == [current] * len(db.points) and db.points


@pytest.mark.asyncio
async def test_the_entity_index_carries_a_payload_index_on_the_epoch_tag() -> None:
    from unittest.mock import AsyncMock

    from app.modules.transformers.entity_vectorstore import EntityVectorStore
    from app.services.vector_db.const.const import EMBED_EPOCH_FIELD

    store = object.__new__(EntityVectorStore)
    store.logger = logging.getLogger("epoch-index")
    store.vector_db_service = AsyncMock()
    await store._ensure_payload_indexes("entities")
    assert EMBED_EPOCH_FIELD in [c.kwargs["field_name"] for c in store.vector_db_service.create_index.await_args_list]


# -- the rollback of a rejected batch ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_record_rollback_that_fails_still_hands_the_record_back() -> None:
    """The delete's own error must not replace the flip: the record would be counted as written."""
    db = _FakeDb()
    store = _store(db, INT8)
    _with_epoch(store, db)
    written = db.upsert_points

    async def failing_delete(collection, expression):  # noqa: ANN202
        raise ConnectionError("vector store down")

    async def write_then_cleanup(collection_name, points):  # noqa: ANN202
        await written(collection_name, points)
        await _cleanup_records(store, db)
        db.delete_points = failing_delete

    db.upsert_points = write_then_cleanup
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert precision_flip_in(caught.value) is not None
    # the flip is what is raised and handled: the delete's own error is logged, not the cause of the hand-back
    link, errors = caught.value, []
    while link is not None and len(errors) < 20:
        errors.append(type(link))
        link = link.__cause__
    assert ConnectionError not in errors


@pytest.mark.asyncio
async def test_an_entity_rollback_leaves_the_same_entity_written_under_another_epoch(world) -> None:  # noqa: F811
    """Same precision, same point id, but written under the epoch that replaced this batch's: not this batch's to remove."""
    world.vdb.add(ENTITIES, INT8, entityType="person", entityId="p0")
    await world.pins._claim_unfenced(ENTITIES, INT8)
    world.served["precision"] = INT8
    real = world.vdb.upsert_points
    newer: list[str] = []

    async def write_then_a_newer_writer_replaces_it(collection_name, points):  # noqa: ANN202
        await real(collection_name, points)
        await world.pins.epochs.confirm_reset(await world.pins.epochs.begin_reset(ENTITIES))
        token = (await world.pins.epochs.capture(ENTITIES)).token
        newer.append(token)
        for point in points:
            meta = {**point.payload["metadata"], "embedEpoch": token}
            await real(collection_name, [type(point)(id=point.id, dense_vector=point.dense_vector, sparse_vector=None, payload={**point.payload, "metadata": meta})])

    world.vdb.upsert_points = write_then_a_newer_writer_replaces_it
    with pytest.raises(PrecisionFlip):
        await _entity_write(world)
    kept = [p for p in world.vdb.points[ENTITIES] if p.get("entityId") == _entity().entity_id]
    assert [p["embedEpoch"] for p in kept] == newer


# -- the sweep at the end of a record ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_record_end_sweeps_a_straggler_of_a_retired_epoch() -> None:
    db = _FakeDb()
    store = _store(db, FP32)
    _with_epoch(store, db)
    pins = store._guard._pins
    old = await pins.epochs.capture(RECORDS)
    await _cleanup_records(store, db)
    await pins._claim_unfenced(RECORDS, FP32)  # the new index is claimed: only the record's end can sweep now
    db.points.append({"id": "s", "orgId": ORG, "virtualRecordId": "other", "embedPrecision": FP32, "embedEpoch": old.token})
    await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert all(p["embedEpoch"] != old.token for p in db.points) and db.points


# -- the real drop ------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_drop_that_fails_retires_no_token_and_releases_no_pin(world) -> None:  # noqa: F811
    from tests.unit.services.messaging.test_precision_switch_converges import (
        RECORDS as REC,
    )
    from tests.unit.services.messaging.test_precision_switch_converges import (
        _seed_int8_index,
    )

    await _seed_int8_index(world)
    old = await world.pins.epochs.capture(REC)

    async def failing(name):  # noqa: ANN202
        raise RuntimeError("vector store down")

    world.vdb.delete_collection = failing
    with pytest.raises(RuntimeError):
        await world.registry.delete_collection(REC)
    states = await world.pins.epochs.states(REC)
    assert states[old.token][0] == "live" and await world.pins.epochs.dead(REC) == []
    assert (await world.pins.get(REC, fresh=True)).precision == INT8 and len(world.vdb.points[REC]) == 3


@pytest.mark.asyncio
async def test_the_old_token_is_retired_only_after_the_drop_has_returned(world) -> None:  # noqa: F811
    from tests.unit.services.messaging.test_precision_switch_converges import (
        RECORDS as REC,
    )
    from tests.unit.services.messaging.test_precision_switch_converges import (
        _seed_int8_index,
    )

    await _seed_int8_index(world)
    old = await world.pins.epochs.capture(REC)
    seen: list[tuple[str, bool]] = []
    real = world.vdb.delete_collection

    async def watching(name):  # noqa: ANN202
        seen.append(((await world.pins.epochs.states(REC))[old.token][0], await world.pins.epochs.moved(old)))
        await real(name)

    world.vdb.delete_collection = watching
    await world.registry.delete_collection(REC)
    assert seen == [("live", True)]  # a new epoch is current, the old token is not yet retired
    assert (await world.pins.epochs.states(REC))[old.token][0] == "retired"


# -- the pin an entity batch judges stored points against ----------------------------------------------------------

@pytest.mark.asyncio
async def test_an_entity_stored_with_the_precision_the_pin_has_left_is_written_again(world) -> None:  # noqa: F811
    """The pin moves while the batch waits for the entity locks: the point stored with the old precision is not 'unchanged'."""
    from types import SimpleNamespace

    from app.modules.indexing.embed_resume import POINT_PRECISION
    from app.modules.transformers.entity_vectorstore import EMBEDDING_MODEL_FIELD

    entity = _entity()
    world.vdb.dims[ENTITIES], world.vdb.points[ENTITIES] = 4, []
    await world.pins._claim_unfenced(ENTITIES, INT8)
    stored = {
        "exists": True, "page_content": entity.embedding_text, "connectorIds": ["c1"], "recordGroupIds": ["g1"],
        "metadata": {**entity.to_vector_payload(), EMBEDDING_MODEL_FIELD: "m:4", POINT_PRECISION: INT8},
    }
    world.store._fetch_existing_states.side_effect = [{}, {world.store._point_id(entity.org_id, entity.entity_type.value, entity.entity_id): stored}]

    class _MovesThePin:
        async def __aenter__(self):  # noqa: ANN204
            await world.pins.release(ENTITIES, None)  # a reset while this batch waits for the lock
            await world.pins._claim_unfenced(ENTITIES, FP32)

        async def __aexit__(self, *_a):  # noqa: ANN204
            return False

    world.store._entity_lock = lambda key: _MovesThePin()
    world.served["precision"] = FP32
    outcome = SimpleNamespace(written=0, unchanged=0, membership_only=0)
    await world.store._upsert_batch(ENTITIES, [entity], outcome, set(), merge_membership=False)
    assert (outcome.written, outcome.unchanged) == (1, 0)
    assert {p["embedPrecision"] for p in world.vdb.points[ENTITIES]} == {FP32}


# -- the cleanup's tail has no organisation bound -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_tail_sweeps_each_organisation_the_graph_lists_under_its_own_scope(world) -> None:  # noqa: F811
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core import org_context
    from app.services.messaging.kafka.handlers.record import RecordEventHandler
    from app.services.vector_db.precision_guard import PrecisionGuard
    from tests.unit.services.messaging.test_precision_switch_converges import (
        RECORDS as REC,
    )
    from tests.unit.services.messaging.test_precision_switch_converges import (
        _seed_int8_index,
    )

    await _seed_int8_index(world)
    graph = SimpleNamespace(get_all_orgs=AsyncMock(return_value=[{"_key": "o1"}, {"id": "o2"}, {}]))
    handler = object.__new__(RecordEventHandler)
    handler.logger = logging.getLogger("tail")
    handler.event_processor = SimpleNamespace(graph_provider=graph)

    swept: list[tuple[str, str]] = []

    async def sweep(self, collection, org, **_k):  # noqa: ANN202
        swept.append((org, org_context.current_org_id()))  # the org is bound while its sweep runs

    async def tail() -> list[str]:
        orgs = await handler._org_ids_to_sweep()
        await world.registry.sweep_dead_epochs(orgs)
        return orgs

    old = PrecisionGuard.sweep_dead
    PrecisionGuard.sweep_dead = sweep
    try:
        orgs = await org_context.spawn_detached(tail())  # the cleanup event binds no organisation
    finally:
        PrecisionGuard.sweep_dead = old
    assert orgs == ["o1", "o2"]
    assert swept == [("o1", "o1"), ("o2", "o2")] and REC


@pytest.mark.asyncio
async def test_a_tail_that_runs_bound_to_an_organisation_sweeps_only_that_one() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core import org_context
    from app.services.messaging.kafka.handlers.record import RecordEventHandler

    graph = SimpleNamespace(get_all_orgs=AsyncMock(return_value=[{"_key": "o1"}, {"_key": "o2"}]))
    handler = object.__new__(RecordEventHandler)
    handler.logger = logging.getLogger("tail")
    handler.event_processor = SimpleNamespace(graph_provider=graph)

    async def bound() -> list[str]:
        with org_context.for_org("o1", job="test"):
            return await handler._org_ids_to_sweep()

    assert await org_context.spawn_detached(bound()) == ["o1"]
    graph.get_all_orgs.assert_not_awaited()  # another organisation's points are not this event's to reach


@pytest.mark.asyncio
async def test_a_listing_that_fails_leaves_the_tail_with_nothing_to_sweep_and_does_not_fail_the_cleanup() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.messaging.kafka.handlers.record import RecordEventHandler

    handler = object.__new__(RecordEventHandler)
    handler.logger = logging.getLogger("tail")
    handler.event_processor = SimpleNamespace(graph_provider=SimpleNamespace(get_all_orgs=AsyncMock(side_effect=ConnectionError("down"))))
    from app.core import org_context

    assert await org_context.spawn_detached(handler._org_ids_to_sweep()) == []
