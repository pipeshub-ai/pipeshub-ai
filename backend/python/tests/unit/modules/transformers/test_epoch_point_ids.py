"""A batch never replaces a point it did not write under its own epoch: the id of a point depends on the epoch.

The stores here key their points by id and replace on a second write of an id, as every vector store does. Each
race is driven by a hook at a named point; the control turns the epoch out of the id and must show the loss.
"""

import contextlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.transformers.vectorstore import deterministic_point_id
from app.services.messaging.precision_flip import PrecisionFlip, precision_flip_in
from app.services.vector_db.collection_epoch import RESET_TOKEN_PREFIX, Fence
from app.services.vector_db.collection_pin import FP32, INT8
from tests.unit.modules.transformers.test_fence_writers import (  # noqa: F401  (the fixture is used by name)
    ENTITIES,
    RECORDS,
    _entity,
    world,
)
from tests.unit.modules.transformers.test_vectorstore_precision_records import (  # noqa: F401
    ORG,
    VRID,
    _blocks,
    _reset,
    _store,
)
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

log = logging.getLogger("epoch-ids")


def _id_keyed(db: _FakeDb) -> None:
    """Points by id: a second write of an id replaces the first, whatever it carried."""
    async def upsert(collection_name, points):  # noqa: ANN202
        for p in points:
            meta = p.payload["metadata"]
            db.points = [x for x in db.points if x["id"] != p.id]
            db.points.append({
                "id": p.id, "orgId": meta.get("orgId"), "virtualRecordId": meta.get("virtualRecordId"),
                "embedPrecision": meta.get("embedPrecision"), "embedEpoch": meta.get("embedEpoch"),
            })

    db.upsert_points = upsert


def _without_the_epoch_in_the_id(monkeypatch) -> None:
    monkeypatch.setattr(Fence, "point_id", lambda self, base: base)


async def _reset_the_collection(store, db: _FakeDb) -> None:
    pins = store._guard._pins
    ticket = await pins.epochs.begin_reset(RECORDS)
    db.points.clear()
    await pins.epochs.confirm_reset(ticket)
    await pins.release_collection(RECORDS)


async def _race(store, db: _FakeDb, *, server: bool):  # noqa: ANN202
    """A batch accepted before a reset; between its check and its write a later batch writes the same chunk."""
    docs = _blocks("b1")
    from langchain_core.documents import Document

    documents = [Document(page_content="text of b1", metadata={"virtualRecordId": VRID, "blockId": "b1", "orgId": ORG})]
    fired: list[bool] = []
    real = store._compute_sparse_embeddings

    async def sparse_then_rewrite(texts):  # noqa: ANN202
        if not fired:
            fired.append(True)
            await _reset_the_collection(store, db)
            await store._embed_and_upsert_documents(documents, "rec-1", RECORDS)  # the re-index, under the new epoch
        return await real(texts)

    store._compute_sparse_embeddings = sparse_then_rewrite
    assert docs
    return documents


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [True, False])
async def test_a_stale_batch_does_not_replace_the_point_a_later_batch_wrote_for_the_same_chunk(server) -> None:
    db = _FakeDb()
    store = _store(db, INT8)
    _id_keyed(db)
    if not server:
        store.dense_embeddings = MagicMock()
        store.dense_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
    documents = await _race(store, db, server=server)
    if server:
        with pytest.raises(Exception) as caught:
            await store._embed_and_upsert_documents(documents, "rec-1", RECORDS)
        assert precision_flip_in(caught.value) is not None  # the stale batch is handed back
    else:
        await store._embed_and_upsert_documents(documents, "rec-1", RECORDS)
    new = await store._guard._pins.epochs.capture(RECORDS)
    assert new.token.startswith(RESET_TOKEN_PREFIX)
    survivors = {p["id"] for p in db.points}
    assert deterministic_point_id(documents[0].metadata, "text of b1", new) in survivors  # the later batch's point is still there


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [True, False])
async def test_control_with_the_epoch_out_of_the_id_the_stale_batch_replaces_the_later_point(monkeypatch, server) -> None:
    _without_the_epoch_in_the_id(monkeypatch)
    db = _FakeDb()
    store = _store(db, INT8)
    _id_keyed(db)
    if not server:
        store.dense_embeddings = MagicMock()
        store.dense_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
    documents = await _race(store, db, server=server)
    with contextlib.suppress(PrecisionFlip):  # the stale batch is refused; what it left is checked below
        await store._embed_and_upsert_documents(documents, "rec-1", RECORDS)
    new = await store._guard._pins.epochs.capture(RECORDS)
    later = [p for p in db.points if p["embedEpoch"] == new.token]
    # the violation: the one point is the stale batch's (or was removed with it), never the later batch's
    assert later == []


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [True, False])
async def test_the_ids_a_record_wants_are_the_ids_its_batches_write_even_when_a_reset_lands_between(server) -> None:
    """The fence taken for the record is the one every batch writes under: a later capture would write other ids."""
    db = _FakeDb()
    store = _store(db, INT8)
    _id_keyed(db)
    if not server:
        store.dense_embeddings = MagicMock()
        store.dense_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
    from langchain_core.documents import Document

    documents = [Document(page_content="text of b1", metadata={"virtualRecordId": VRID, "blockId": "b1", "orgId": ORG})]
    fence = await store._guard.capture(RECORDS)
    await _reset_the_collection(store, db)  # between the record's capture and its first batch
    if server:
        with pytest.raises(Exception) as caught:
            await store._process_document_chunks(documents, "rec-1", RECORDS, fence)
        assert precision_flip_in(caught.value) is not None and db.points == []
    else:
        await store._process_document_chunks(documents, "rec-1", RECORDS, fence)
        assert [p["id"] for p in db.points] == [deterministic_point_id(documents[0].metadata, "text of b1", fence)]


@pytest.mark.asyncio
async def test_a_record_hands_the_epoch_it_captured_to_its_batches_even_when_a_reset_lands_after_the_capture() -> None:
    """``_embed_resumably`` derives the record's ids from one capture; its batches must write under that same one."""
    from langchain_core.documents import Document

    from app.modules.indexing.embed_resume import EmbedAttempt

    db = _FakeDb()
    store = _store(db, INT8)
    record_fences: list[Fence] = []
    real_capture = store._guard.capture

    async def capture_then_reset(collection):  # noqa: ANN202
        fence = await real_capture(collection)
        record_fences.append(fence)
        await _reset_the_collection(store, db)  # a cleanup lands right after the record's capture
        return fence

    received: list[Fence | None] = []

    async def batches(_docs, _record_id, _collection, fence=None):  # noqa: ANN202
        received.append(fence)

    store._guard.capture = capture_then_reset
    store._process_document_chunks = batches
    store.require_matching_precision_before_delete = AsyncMock()
    store._write_embed_marker = AsyncMock()
    store.delete_embeddings = AsyncMock()
    store._check_for_leaks = AsyncMock()
    documents = [Document(page_content="text of b1", metadata={"virtualRecordId": VRID, "blockId": "b1", "orgId": ORG})]

    await store._embed_resumably(EmbedAttempt(delivery_id="d-1", rev="1", vrid=VRID), documents, "rec-1", VRID, RECORDS, ORG)

    assert len(record_fences) == 1 and received == [record_fences[0]]  # not a fresh capture after the reset, and not none


# -- entities -----------------------------------------------------------------------------------------------------

def _entity_logical_id(world_) -> str:
    e = _entity()
    return world_.store._point_id(e.org_id, e.entity_type.value, e.entity_id)


@pytest.mark.asyncio
async def test_an_entity_batch_under_a_reset_token_counts_every_entity_written_and_none_unwritten(world) -> None:  # noqa: F811
    world.vdb.dims[ENTITIES], world.vdb.points[ENTITIES] = 4, []
    await world.pins.epochs.capture(ENTITIES)
    await world.pins.epochs.confirm_reset(await world.pins.epochs.begin_reset(ENTITIES))
    fence = await world.pins.epochs.capture(ENTITIES)
    assert fence.token.startswith(RESET_TOKEN_PREFIX)
    outcome, settled = SimpleNamespace(written=0, unchanged=0, membership_only=0), set()
    await world.store._upsert_batch(ENTITIES, [_entity()], outcome, settled, merge_membership=False)
    logical = _entity_logical_id(world)
    assert outcome.written == 1 and settled == {logical}  # the key the caller tests is the logical one
    assert [p["id"] for p in world.vdb.points[ENTITIES]] == [fence.point_id(logical)] != [logical]


@pytest.mark.asyncio
async def test_a_stored_entity_is_found_under_the_id_of_its_epoch(world) -> None:  # noqa: F811
    from app.modules.transformers.entity_vectorstore import EntityVectorStore

    e = _entity()
    logical = world.store._point_id(e.org_id, e.entity_type.value, e.entity_id)
    fence = Fence(ENTITIES, RESET_TOKEN_PREFIX + "ab" * 15)
    seen: list[list[str]] = []

    async def retrieve(collection, org, ids):  # noqa: ANN202
        seen.append(list(ids))
        return [SimpleNamespace(id=fence.point_id(logical), payload={"page_content": "x", "connectorIds": ["c"], "recordGroupIds": []})]

    world.store._retrieve = retrieve
    states = await EntityVectorStore._fetch_existing_states(world.store, ENTITIES, [e], fence)
    assert seen == [[fence.point_id(logical)]] and states[logical]["exists"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("fenced", [True, False])
async def test_a_stale_entity_batch_does_not_replace_a_point_another_writer_wrote_under_the_new_epoch(world, monkeypatch, fenced) -> None:  # noqa: F811
    """Two processes: the entity lock of this one does not exclude the other."""
    if not fenced:
        monkeypatch.setattr(Fence, "point_id", lambda self, base: base)
    world.vdb.dims[ENTITIES], world.vdb.points[ENTITIES] = 4, []
    world.served["precision"] = INT8
    await world.pins._claim_unfenced(ENTITIES, INT8)
    logical = _entity_logical_id(world)
    other: dict = {}

    async def reset_and_the_other_writes(*_a) -> None:
        ticket = await world.pins.epochs.begin_reset(ENTITIES)
        world.vdb.points[ENTITIES] = []
        await world.pins.epochs.confirm_reset(ticket)
        await world.pins.release_collection(ENTITIES)
        new = await world.pins.epochs.capture(ENTITIES)
        other["fence"] = new
        await world.pins._claim_unfenced(ENTITIES, INT8)
        world.vdb.points[ENTITIES].append({
            "id": new.point_id(logical), "entityType": "record", "entityId": _entity().entity_id,
            "embedPrecision": INT8, "embedEpoch": new.token,
        })

    world.store._raise_if_config_moved = AsyncMock(side_effect=reset_and_the_other_writes)
    with pytest.raises(PrecisionFlip):
        await world.store._upsert_batch(
            ENTITIES, [_entity()], SimpleNamespace(written=0, unchanged=0, membership_only=0), set(), merge_membership=False,
        )
    survivors = [p for p in world.vdb.points[ENTITIES] if p["embedEpoch"] == other["fence"].token]
    assert bool(survivors) is fenced
    assert FP32  # (the other precision is not involved)
