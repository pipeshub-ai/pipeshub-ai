"""Vector point ids are derived from what a point represents.

A retried or partially applied upsert must overwrite its own points, never
leave duplicates beside them.
"""

import json
import random
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

    def test_pieces_of_one_whole_block_are_distinct(self) -> None:
        # An over-long row is split into pieces that share the block's metadata.
        assert deterministic_point_id(_meta(), "piece one") != deterministic_point_id(_meta(), "piece two")

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

    def test_summary_pieces_are_distinct(self) -> None:
        summary = {
            "virtualRecordId": "vr",
            "blockId": "vr_summary",
            "isBlock": False,
            "isRecordSummary": True,
        }
        assert deterministic_point_id(summary, "a") == deterministic_point_id(summary, "a")
        assert deterministic_point_id(summary, "a") != deterministic_point_id(summary, "b")

    def test_block_group_pieces_are_distinct(self) -> None:
        group = {"virtualRecordId": "vr", "blockId": "g1", "isBlockGroup": True, "isBlock": False}
        assert deterministic_point_id(group, "a") != deterministic_point_id(group, "b")

    def test_id_is_a_uuid(self) -> None:
        uuid.UUID(deterministic_point_id(_meta(), "t"))

    def test_without_identity_uses_a_stable_fallback(self) -> None:
        assert deterministic_point_id({}, "t") == deterministic_point_id({}, "t")
        assert deterministic_point_id({}, "a") != deterministic_point_id({}, "b")
        assert deterministic_point_id({"orgId": "a"}, "t") != deterministic_point_id({"orgId": "b"}, "t")


def _id_before_the_salt_existed(metadata: dict[str, object], page_content: str) -> str:
    """``deterministic_point_id`` as origin/main has it: the ids every point already stored carries are these."""
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, "pipeshub:vector-point")
    virtual_record_id = metadata.get("virtualRecordId")
    block_id = metadata.get("blockId")
    if not virtual_record_id or not block_id:
        canonical = json.dumps(metadata, sort_keys=True, default=str, separators=(",", ":"))
        name = f"anon|{canonical}|{page_content}"
    else:
        is_sub_chunk = (
            metadata.get("isBlock") is False
            and not metadata.get("isBlockGroup")
            and not metadata.get("isRecordSummary")
        )
        name = f"{virtual_record_id}|{block_id}|{'chunk' if is_sub_chunk else 'unit'}|{page_content}"
    return str(uuid.uuid5(namespace, name))


class TestUnsaltedPointIdsNeverChange:
    """A write with no salt keeps the ids it was indexed under: a changed id would orphan every stored point."""

    def test_golden_vector_of_an_unsalted_point(self) -> None:
        # Computed with origin/main's function; do not regenerate it from this build.
        assert deterministic_point_id({"virtualRecordId": "v1", "blockId": "b1"}, "hello") == "96daa659-5f0c-549e-a6ea-a2aa3a94c604"

    def test_the_reference_formula_reproduces_the_golden_vector(self) -> None:
        assert _id_before_the_salt_existed({"virtualRecordId": "v1", "blockId": "b1"}, "hello") == "96daa659-5f0c-549e-a6ea-a2aa3a94c604"

    @pytest.mark.parametrize("seed", [91731, 5, 20240])
    def test_unsalted_ids_equal_the_ones_before_the_salt_existed(self, seed: int) -> None:
        rnd = random.Random(seed)
        cases: list[tuple[dict[str, object], str]] = [
            ({}, ""), ({"virtualRecordId": "v", "blockId": None}, "x"), ({"orgId": "o", "k": [1, 2]}, "z"),
            ({"virtualRecordId": "v", "blockId": "b", "isBlock": False}, "t"),
            ({"virtualRecordId": "v", "blockId": "b", "isBlock": False, "isRecordSummary": True}, "t"),
        ]
        for _ in range(200):
            md: dict[str, object] = {"virtualRecordId": str(uuid.UUID(int=rnd.getrandbits(128))), "blockId": str(rnd.randint(0, 9999))}
            if rnd.random() < 0.3:
                md["isBlock"] = rnd.random() < 0.5
            if rnd.random() < 0.2:
                md.pop("blockId")
            cases.append((md, "".join(chr(rnd.randint(32, 0x2FFF)) for _ in range(rnd.randint(0, 40)))))
        for md, text in cases:
            expected = _id_before_the_salt_existed(dict(md), text)
            assert deterministic_point_id(dict(md), text) == expected
            assert deterministic_point_id(dict(md), text, "") == expected

    def test_a_salted_id_differs_from_the_unsalted_one_and_across_generations(self) -> None:
        rnd = random.Random(7)
        for _ in range(100):
            md: dict[str, object] = {"virtualRecordId": f"v{rnd.randint(0, 99)}", "blockId": f"b{rnd.randint(0, 99)}"}
            text = f"t{rnd.randint(0, 999)}"
            plain = deterministic_point_id(md, text)
            first = deterministic_point_id(md, text, "scope|g1")
            second = deterministic_point_id(md, text, "scope|g2")
            other_scope = deterministic_point_id(md, text, "scope2|g1")
            assert len({plain, first, second, other_scope}) == 4


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
    store.delete_blocks_by_ids = AsyncMock(side_effect=lambda ids, *_, **__: calls.delete(set(ids)))
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


class TestSplitBlocksKeepEveryPiece:
    """`_bounded_documents` splits over-long table and SQL text into pieces that share one
    block's metadata. Each piece needs its own point, or the upsert keeps only one."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("source", ["table_row", "table_group", "sql_row", "sql_table", "sql_view"])
    async def test_every_piece_of_a_split_block_is_stored(
        self, source: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.models.blocks import (
            BlockGroup,
            BlocksContainer,
            BlockType,
            DataFormat,
            GroupSubType,
            GroupType,
        )
        from tests.unit.modules.transformers.test_vectorstore_point_metadata import (
            _capturing_vectorstore,
        )

        monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", "512")
        vs, points = _capturing_vectorstore()
        # Numbered rows, so no two pieces carry the same text.
        text = " ".join(f"row {i}: value {i * 7}," for i in range(1_500))
        blocks, groups = [], []
        if source in ("table_row", "sql_row"):
            row = Block(index=0, type=BlockType.TABLE_ROW, format=DataFormat.JSON,
                        data={"row_natural_language_text": text})
            if source == "sql_row":
                row.sub_type = "sql_table"
            blocks.append(row)
        elif source == "table_group":
            groups.append(BlockGroup(type=GroupType.TABLE, index=0, data={"table_summary": text}))
        elif source == "sql_table":
            groups.append(BlockGroup(type=GroupType.TABLE, sub_type=GroupSubType.SQL_TABLE, index=0,
                                     data={"fqn": "db.s.t", "ddl": text}))
        else:
            groups.append(BlockGroup(type=GroupType.VIEW, sub_type=GroupSubType.SQL_VIEW, index=0,
                                     data={"fqn": "db.s.v", "definition": text}))

        await vs.index_documents(
            BlocksContainer(blocks=blocks, block_groups=groups), "org-1", "rec-1", "vr-1",
        )

        stored = {p.id: p for p in points}  # an upsert keeps one point per id
        assert len(points) > 1
        assert len(stored) == len(points)
        assert text in "".join(p.payload["page_content"] for p in points)
        position = "blockIndex" if blocks else "blockGroupIndex"
        assert all(p.payload["metadata"][position] == 0 for p in points)
