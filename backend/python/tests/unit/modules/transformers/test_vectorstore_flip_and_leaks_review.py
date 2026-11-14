"""A flip is not wrapped by the batch loop, and the summary and class-unit writes check for leaks like the other writes."""

from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.exceptions.indexing_exceptions import VectorStoreError
from app.models.blocks import BlocksContainer
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.collection_pin import FP32, INT8
from tests.unit.modules.transformers.test_vectorstore_point_ids import _make_vectorstore
from tests.unit.modules.transformers.test_vectorstore_precision_records import (  # noqa: F401
    ORG,
    VRID,
    _reset,
    _store,
)
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

_DOC_META = {"virtualRecordId": VRID, "orgId": ORG, "blockId": "b1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("sequential", [True, False])
async def test_a_flip_in_a_batch_is_raised_as_it_is_not_wrapped(sequential: bool) -> None:
    store = _make_vectorstore()
    store._is_local_cpu_embedding = lambda: sequential
    store._batches_by_tokens = lambda: False
    store._embed_and_upsert_documents = AsyncMock(side_effect=PrecisionFlip("the index holds fp32"))
    with pytest.raises(PrecisionFlip):
        await store._process_document_chunks([Document(page_content="a", metadata=dict(_DOC_META))], "rec-1", "records")


@pytest.mark.asyncio
@pytest.mark.parametrize("sequential", [True, False])
async def test_any_other_batch_error_is_still_wrapped(sequential: bool) -> None:
    store = _make_vectorstore()
    store._is_local_cpu_embedding = lambda: sequential
    store._batches_by_tokens = lambda: False
    store._embed_and_upsert_documents = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(VectorStoreError):
        await store._process_document_chunks([Document(page_content="a", metadata=dict(_DOC_META))], "rec-1", "records")


async def _leaking_store():  # noqa: ANN202
    db = _FakeDb()
    store = _store(db, INT8)
    await store._guard._pins.claim("records", INT8)
    real = store._embed_and_upsert_documents

    async def leak_then_store(*a, **k):  # noqa: ANN202
        db.add(FP32, vrid=VRID, org=ORG)  # a peer wrote the other precision between our check and our write
        return await real(*a, **k)

    store._embed_and_upsert_documents = leak_then_store
    return store, db


@pytest.mark.asyncio
async def test_the_record_summary_write_removes_a_leaked_point_and_hands_the_record_back() -> None:
    store, db = await _leaking_store()
    store._build_record_summary_document = lambda *a, **k: Document(page_content="summary", metadata=dict(_DOC_META))
    with pytest.raises(PrecisionFlip):
        await store.index_record_summary("rec-1", VRID, ORG, semantic_metadata=None)
    assert FP32 not in [p["embedPrecision"] for p in db.points]


@pytest.mark.asyncio
async def test_the_class_unit_write_removes_a_leaked_point_and_hands_the_record_back(monkeypatch) -> None:
    store, db = await _leaking_store()
    monkeypatch.setattr(
        "app.modules.transformers.vectorstore.code_documents_for_record",
        lambda *a, **k: [Document(page_content="unit", metadata=dict(_DOC_META))],
    )
    with pytest.raises(PrecisionFlip):
        await store.index_class_units("rec-1", VRID, ORG, BlocksContainer(blocks=[], block_groups=[]))
    assert FP32 not in [p["embedPrecision"] for p in db.points]


@pytest.mark.asyncio
async def test_a_summary_write_with_nothing_leaked_completes() -> None:
    db = _FakeDb()
    store = _store(db, INT8)
    await store._guard._pins.claim("records", INT8)
    store._build_record_summary_document = lambda *a, **k: Document(page_content="summary", metadata=dict(_DOC_META))
    await store.index_record_summary("rec-1", VRID, ORG, semantic_metadata=None)
    assert [p["embedPrecision"] for p in db.points] == [INT8]
