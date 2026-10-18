"""Vector point ids are derived from what a point represents.

A retried or partially applied upsert must overwrite its own points, never
leave duplicates beside them.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document

from app.models.blocks import Block
from app.modules.transformers.vectorstore import VectorStore, deterministic_point_id
from tests.support.vector_db import make_collection_registry


def _meta(**overrides: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "virtualRecordId": "vr-1",
        "blockId": "b-1",
        "isBlock": True,
        "isBlockGroup": False,
    }
    metadata.update(overrides)
    return metadata


class TestDeterministicPointId:
    def test_same_block_gets_the_same_id(self) -> None:
        assert deterministic_point_id(_meta(), "text") == deterministic_point_id(_meta(), "text")

    def test_whole_block_point_is_keyed_by_block_not_text(self) -> None:
        # An edited block keeps its point, so the upsert replaces it in place.
        assert deterministic_point_id(_meta(), "old") == deterministic_point_id(_meta(), "new")

    def test_sub_chunks_of_one_block_are_distinct(self) -> None:
        chunk = _meta(isBlock=False)
        assert deterministic_point_id(chunk, "sentence one") != deterministic_point_id(chunk, "sentence two")

    def test_sub_chunk_never_collides_with_the_whole_block_point(self) -> None:
        assert deterministic_point_id(_meta(isBlock=False), "x") != deterministic_point_id(_meta(), "x")

    def test_blocks_and_records_do_not_collide(self) -> None:
        ids = {
            deterministic_point_id(_meta(blockId=block, virtualRecordId=vrid), "t")
            for block in ("b1", "b2")
            for vrid in ("v1", "v2")
        }
        assert len(ids) == 4

    def test_summary_point_is_one_per_record(self) -> None:
        summary = {
            "virtualRecordId": "vr",
            "blockId": "vr_summary",
            "isBlock": False,
            "isRecordSummary": True,
        }
        assert deterministic_point_id(summary, "a") == deterministic_point_id(summary, "b")

    def test_block_group_point_is_one_per_group(self) -> None:
        group = {"virtualRecordId": "vr", "blockId": "g1", "isBlockGroup": True, "isBlock": False}
        assert deterministic_point_id(group, "a") == deterministic_point_id(group, "b")

    def test_id_is_a_uuid(self) -> None:
        uuid.UUID(deterministic_point_id(_meta(), "t"))

    def test_without_identity_uses_a_stable_fallback(self) -> None:
        assert deterministic_point_id({}, "t") == deterministic_point_id({}, "t")
        assert deterministic_point_id({}, "a") != deterministic_point_id({}, "b")
        assert deterministic_point_id({"orgId": "a"}, "t") != deterministic_point_id({"orgId": "b"}, "t")


def _make_vectorstore() -> VectorStore:
    from app.services.vector_db.models import VectorDBCapabilities

    vector_db = AsyncMock()
    vector_db.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
    vector_db.get_service_name = MagicMock(return_value="mock")
    return VectorStore(
        logger=MagicMock(),
        config_service=AsyncMock(),
        graph_provider=AsyncMock(),
        collection_registry=make_collection_registry(),
        vector_db_service=vector_db,
    )


@pytest.mark.asyncio
async def test_a_retried_batch_upserts_the_same_point_ids() -> None:
    store = _make_vectorstore()
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "r1"})
    store._embed_documents_with_retry = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])
    store._compute_sparse_embeddings = AsyncMock(return_value=[None, None])
    documents = [
        Document(page_content="alpha", metadata=_meta(blockId="b1")),
        Document(page_content="beta", metadata=_meta(blockId="b2")),
    ]

    await store._embed_and_upsert_documents(documents, "r1", "records")
    await store._embed_and_upsert_documents(documents, "r1", "records")

    first, second = (
        call.kwargs["points"] for call in store.vector_db_service.upsert_points.await_args_list
    )
    assert [p.id for p in first] == [p.id for p in second]
    assert len({p.id for p in first}) == 2


def _text_block(block_id: str, text: str, index: int) -> Block:
    from app.models.blocks import BlockType, DataFormat

    return Block(id=block_id, index=index, type=BlockType.TEXT, format=DataFormat.TXT, data=text)


def _reconciling_store() -> tuple[VectorStore, MagicMock]:
    """A store whose deletes and upserts are recorded, in order, on one mock."""
    store = _make_vectorstore()
    calls = MagicMock()
    store.get_embedding_model_instance = AsyncMock(return_value=False)
    store._ensure_collection = AsyncMock(return_value="records")
    store._cleanup_orphaned_embeddings_if_needed = AsyncMock()
    store._resync_membership_after_write = AsyncMock()
    store.delete_blocks_by_ids = AsyncMock(side_effect=lambda ids, *_: calls.delete(set(ids)))
    store._process_document_chunks = AsyncMock(
        side_effect=lambda docs, *_: calls.upsert({d.metadata["blockId"] for d in docs})
    )
    return store, calls


class TestReconciliationReplacesStoredPoints:
    """Points stored before ids were derived from the block carry random ids, so an upsert
    cannot overwrite them: a re-embedded block's stored points are removed first."""

    @pytest.mark.asyncio
    async def test_points_of_a_reembedded_block_are_deleted_before_the_upsert(self) -> None:
        from app.models.blocks import BlocksContainer

        store, calls = _reconciling_store()
        container = BlocksContainer(
            blocks=[_text_block("b-new", "a long enough sentence to embed as text", 0)], block_groups=[]
        )

        await store.index_documents(
            container, "org-1", "rec-1", "vr-1",
            block_ids_to_delete={"b-gone"}, is_reconciliation=True,
        )

        ordered = [(c[0], c.args[0]) for c in calls.mock_calls if c[0] in ("delete", "upsert")]
        reindex_delete = ordered.index(("delete", {"b-new"}))
        upsert = next(i for i, (name, _) in enumerate(ordered) if name == "upsert")
        assert reindex_delete < upsert
        assert ("delete", {"b-gone"}) in ordered[upsert:]

    @pytest.mark.asyncio
    async def test_a_block_both_reindexed_and_removed_keeps_its_new_points(self) -> None:
        from app.models.blocks import BlocksContainer

        store, calls = _reconciling_store()
        container = BlocksContainer(
            blocks=[_text_block("b-1", "a long enough sentence to embed as text", 0)], block_groups=[]
        )

        await store.index_documents(
            container, "org-1", "rec-1", "vr-1",
            block_ids_to_delete={"b-1", "b-gone"}, is_reconciliation=True,
        )

        ordered = [(c[0], c.args[0]) for c in calls.mock_calls if c[0] in ("delete", "upsert")]
        upsert = next(i for i, (name, _) in enumerate(ordered) if name == "upsert")
        deleted_after = set().union(*(ids for name, ids in ordered[upsert:] if name == "delete"))
        assert "b-1" not in deleted_after
        assert "b-gone" in deleted_after
