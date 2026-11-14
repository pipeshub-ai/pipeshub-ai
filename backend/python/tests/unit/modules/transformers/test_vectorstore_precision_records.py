"""What a record's write does with the index's precision, through the real ``index_documents`` and its helpers.

The vector store, the guard, the pin store and the batch wrappers are the real ones; the embedding server (answering
with the precision it served), the graph node and the vector service are in-memory stand-ins.
"""

import logging
from unittest.mock import AsyncMock

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.core import org_context
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.indexing.embed_resume import EmbedAttempt, begin_attempt, end_attempt
from app.services.messaging.precision_flip import PrecisionFlip, precision_flip_in
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard
from app.utils.embedding_server_client import EmbeddingServerEmbeddings
from app.utils.precision_witness import PrecisionWitness, set_precision_witness
from tests.unit.modules.transformers.test_vectorstore_point_ids import _make_vectorstore
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

log = logging.getLogger("records-precision")
ORG, VRID = "o1", "vr-1"


class _Server(EmbeddingServerEmbeddings):
    def __init__(self, precision: str) -> None:
        self.model = "m"
        self.aembed_documents_witnessed = AsyncMock(return_value=([[0.1, 0.2]], precision, False))

    def bulk_gate(self):  # noqa: ANN202
        return None


def _store(db: _FakeDb, serving: str):  # noqa: ANN202
    set_precision_witness(PrecisionWitness())
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    store = _make_vectorstore()
    store.dense_embeddings = _Server(serving)
    store.embedding_provider = "embeddingServer"
    store.vector_db_service = db
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})
    store.graph_provider.update_node = AsyncMock(return_value=True)
    store._is_local_cpu_embedding = lambda: False
    store._guard = PrecisionGuard(CollectionPinStore(config, log), db, log)
    store.get_embedding_model_instance = AsyncMock(return_value=False)
    store._ensure_collection = AsyncMock(return_value="records")
    store._bind_membership = AsyncMock(return_value=None)
    store._resync_membership_after_write = AsyncMock()
    store._cleanup_orphaned_embeddings_if_needed = AsyncMock()
    store._compute_sparse_embeddings = AsyncMock(side_effect=lambda texts: [None] * len(texts))
    store.stored = []

    async def upsert(collection_name, points):  # noqa: ANN202
        for p in points:
            meta = p.payload["metadata"]
            db.points.append({"orgId": meta.get("orgId"), "virtualRecordId": meta.get("virtualRecordId"), "embedPrecision": meta.get("embedPrecision")})
            store.stored.append(meta.get("embedPrecision"))

    db.upsert_points = upsert
    return store


def _blocks(*ids: str) -> BlocksContainer:
    return BlocksContainer(
        blocks=[Block(id=i, index=n, type=BlockType.TEXT, format=DataFormat.TXT, data=f"text of {i}") for n, i in enumerate(ids)],
        block_groups=[],
    )


@pytest.fixture(autouse=True)
def _reset(monkeypatch):  # noqa: ANN202
    monkeypatch.setattr("app.modules.transformers.vectorstore.reset_membership_context", lambda _tokens: None)
    end_attempt()
    with org_context.for_org(ORG, job="test"):
        yield
    end_attempt()
    set_precision_witness(None)


@pytest.mark.asyncio
async def test_a_leaked_batch_of_a_reconciled_record_is_removed_and_the_unchanged_blocks_are_kept() -> None:
    db = _FakeDb()
    db.add(INT8, n=3, vrid=VRID, org=ORG)  # the unchanged blocks of the record
    db.add(FP32, n=1, vrid=VRID, org=ORG)  # one batch a claim race let in
    store = _store(db, INT8)
    await store._guard._pins.claim("records", INT8)
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b-new"), ORG, "rec-1", VRID, is_reconciliation=True)
    assert precision_flip_in(caught.value) is not None
    assert sorted(p["embedPrecision"] for p in db.points) == [INT8] * 4  # three unchanged and the new block
    store.graph_provider.update_node.assert_not_awaited()  # the reconciliation's own metadata is untouched


@pytest.mark.asyncio
async def test_a_reconciled_record_with_nothing_leaked_completes() -> None:
    db = _FakeDb()
    db.add(INT8, n=3, vrid=VRID, org=ORG)
    store = _store(db, INT8)
    await store._guard._pins.claim("records", INT8)
    assert await store.index_documents(_blocks("b-new"), ORG, "rec-1", VRID, is_reconciliation=True) is True


@pytest.mark.asyncio
async def test_a_full_replace_without_a_resume_attempt_also_checks_for_leaks_at_its_end() -> None:
    db = _FakeDb()
    store = _store(db, INT8)
    await store._guard._pins.claim("records", INT8)
    real = store._embed_and_upsert_documents

    async def leak_then_store(*a, **k):  # noqa: ANN202
        db.add(FP32, vrid=VRID, org=ORG)  # a peer wrote the other precision between our batches
        return await real(*a, **k)

    store._embed_and_upsert_documents = leak_then_store
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert precision_flip_in(caught.value) is not None
    assert {p["embedPrecision"] for p in db.points} == {INT8}


@pytest.mark.asyncio
@pytest.mark.parametrize("with_attempt", [True, False], ids=["resumable", "full-replace"])
@pytest.mark.parametrize(("pinned", "serving"), [(FP32, INT8), (INT8, FP32)])
async def test_the_other_precision_is_refused_before_any_point_or_marker(with_attempt, pinned, serving) -> None:
    db = _FakeDb()
    db.add(pinned, n=2, vrid=VRID, org=ORG)  # the record's points from an earlier run
    store = _store(db, serving)
    await store._guard._pins.claim("records", pinned, ORG)
    if with_attempt:
        begin_attempt(EmbedAttempt(delivery_id="d1", rev="1", vrid=VRID))
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert isinstance(precision_flip_in(caught.value), PrecisionFlip)
    assert [p["embedPrecision"] for p in db.points] == [pinned] * 2  # the old points were not deleted, none was added
    assert store.stored == []
    store.graph_provider.update_node.assert_not_awaited()  # no resume marker was written


@pytest.mark.asyncio
async def test_a_flip_after_the_delete_is_repaired_on_redelivery() -> None:
    db = _FakeDb()
    db.add(INT8, n=2, vrid=VRID, org=ORG)  # what the first indexing left
    store = _store(db, INT8)
    await store._guard._pins.claim("records", INT8)
    # The delete is allowed (the probe says int8), then the first batch comes back fp32: the server was restarted.
    store.dense_embeddings.aembed_documents_witnessed = AsyncMock(side_effect=[([[0.1, 0.2]], INT8, False), ([[0.1, 0.2]], FP32, False)])
    with pytest.raises(Exception) as caught:
        await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID)
    assert precision_flip_in(caught.value) is not None
    assert db.points == [] and store.stored == []  # the record's points are gone and nothing of the other precision came in
    # The redelivery, with the server back on int8, embeds the record again.
    set_precision_witness(PrecisionWitness())
    store.dense_embeddings.aembed_documents_witnessed = AsyncMock(return_value=([[0.1, 0.2]], INT8, False))
    assert await store.index_documents(_blocks("b1"), ORG, "rec-1", VRID) is True
    assert store.stored == [INT8]


@pytest.mark.asyncio
async def test_duplicates_need_no_special_case() -> None:
    """The same record reached twice (an old message and a reindex), and a twin that shares its content, end alike."""
    db = _FakeDb()
    store = _store(db, FP32)
    for record_id, vrid in (("rec-1", VRID), ("rec-1", VRID), ("rec-2", "vr-2")):
        assert await store.index_documents(_blocks("b1"), ORG, record_id, vrid) is True
    by_record = {}
    for point in db.points:
        by_record.setdefault(point["virtualRecordId"], []).append(point["embedPrecision"])
    assert by_record == {VRID: [FP32], "vr-2": [FP32]}  # one set of points per record, one precision, no leftovers
    store.graph_provider.update_node.assert_not_awaited()  # no special status is written
