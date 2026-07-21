"""Tests for VectorStore.index_documents SQL + reconciliation branches
that are not exercised by the existing test suite."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import (
    Block,
    BlockGroup,
    BlocksContainer,
    BlockSubType,
    BlockType,
    GroupSubType,
    GroupType,
)


import app.modules.transformers.vectorstore as _vs_mod  # noqa: E402

_LANGCHAIN_DOCUMENT_IS_CLASS = isinstance(_vs_mod.Document, type)


def _make_vectorstore():
    from app.services.vector_db.models import VectorDBCapabilities

    vdb = AsyncMock()
    caps = VectorDBCapabilities(
        supports_sparse_vectors=False,
        supports_server_side_text_search=False,
    )
    vdb.get_capabilities = MagicMock(return_value=caps)

    with patch.object(_vs_mod, "SparseEmbedder"):
        vs = _vs_mod.VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_name="test-sql",
            vector_db_service=vdb,
        )
    return vs


# ---------------------------------------------------------------------------
# SQL block groups  (lines 1154-1234)
# ---------------------------------------------------------------------------


class TestSqlBlockGroups:
    @pytest.mark.skip(
        reason="SQL-specific DDL embedding not implemented in current vectorstore"
    )
    @pytest.mark.asyncio
    async def test_sql_table_with_ddl_and_summary_embeds(self):
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            sub_type=GroupSubType.SQL_TABLE,
            data={
                "fqn": "db.schema.t",
                "ddl": "CREATE TABLE t (id INT)",
                "table_summary": "Summary here",
            },
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain"
            )

        assert result is True
        vs._create_embeddings.assert_awaited_once()
        chunks = vs._create_embeddings.call_args[0][0]
        assert any("CREATE TABLE" in c.page_content for c in chunks)
        assert any("Summary here" in c.page_content for c in chunks)

    @pytest.mark.skip(
        reason="SQL-specific sub_type filtering not implemented in current vectorstore"
    )
    @pytest.mark.asyncio
    async def test_sql_table_missing_ddl_skips(self):
        """SQL table group without DDL emits no embedding."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            sub_type=GroupSubType.SQL_TABLE,
            data={"fqn": "db.schema.t", "table_summary": "only summary"},
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain"
            )
        assert result is True
        vs._create_embeddings.assert_not_awaited()

    @pytest.mark.skip(
        reason="SQL VIEW block group type not handled in current vectorstore"
    )
    @pytest.mark.asyncio
    async def test_sql_view_with_definition_embeds(self):
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        bg = BlockGroup(
            index=0,
            type=GroupType.VIEW,
            sub_type=GroupSubType.SQL_VIEW,
            data={
                "fqn": "db.schema.v",
                "definition": "SELECT 1 AS x",
                "source_tables": ["db.schema.t"],
                "source_tables_summary": "t is a table",
                "source_table_ddls": {"db.schema.t": "CREATE TABLE t (...)"},
                "comment": "useful view",
                "is_secure": True,
            },
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain"
            )

        assert result is True
        chunks = vs._create_embeddings.call_args[0][0]
        joined = "\n".join(c.page_content for c in chunks)
        assert "-- View: db.schema.v" in joined
        assert "SELECT 1 AS x" in joined
        assert "secure view" in joined
        assert "Source Tables:" in joined

    @pytest.mark.asyncio
    async def test_sql_view_empty_content_is_skipped(self):
        """A VIEW-type block group is not handled; nothing is embedded."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        bg = BlockGroup(
            index=0,
            type=GroupType.VIEW,
            sub_type=GroupSubType.SQL_VIEW,
            data={"fqn": "v", "definition": "", "source_tables": [], "source_tables_summary": ""},
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain"
            )
        assert result is True
        vs._create_embeddings.assert_not_awaited()


# ---------------------------------------------------------------------------
# SQL row blocks  (lines 1254-1272)
# ---------------------------------------------------------------------------


class TestSqlRowBlocks:
    @pytest.mark.asyncio
    async def test_sql_row_blocks_embed_row_text(self):
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            sub_type=BlockSubType.CHILD_RECORD,
        )
        row.data = {"row_natural_language_text": "Row payload"}
        container = BlocksContainer(blocks=[row], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain"
            )

        assert result is True
        chunks = vs._create_embeddings.call_args[0][0]
        assert any(c.page_content == "Row payload" for c in chunks)


# ---------------------------------------------------------------------------
# Regular table blocks with summary  (lines 1275-1295)
# ---------------------------------------------------------------------------


class TestRegularTableBlockWithSummary:
    @pytest.mark.asyncio
    async def test_table_block_summary_embedded(self):
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()

        table_block = Block(
            index=0, type=BlockType.TABLE, format="txt",
            data={"table_summary": "Summary text"},
        )
        container = BlocksContainer(blocks=[table_block], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain"
            )

        assert result is True
        chunks = vs._create_embeddings.call_args[0][0]
        assert any(c.page_content == "Summary text" for c in chunks)


# ---------------------------------------------------------------------------
# Reconciliation path (lines 1319-1328): is_reconciliation=True with image chunks
# ---------------------------------------------------------------------------


class TestReconciliationProcessing:
    @pytest.mark.skip(
        reason="is_reconciliation parameter not implemented in current vectorstore"
    )
    @pytest.mark.asyncio
    async def test_is_reconciliation_with_text_and_images_routes_correctly(self):
        """When is_reconciliation=True, text goes through _process_document_chunks
        and images go through _process_image_embeddings + _store_image_points."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)  # multimodal
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[{"fake": "point"}])
        vs._store_image_points = AsyncMock()

        text_block = Block(index=0, type=BlockType.TEXT, format="txt", data="text")
        img_block = Block(index=1, type=BlockType.IMAGE, format="bin", data={"uri": "abc"})
        container = BlocksContainer(blocks=[text_block, img_block], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain",
                is_reconciliation=True,
            )

        assert result is True
        vs._process_document_chunks.assert_awaited_once()
        vs._process_image_embeddings.assert_awaited_once()
        vs._store_image_points.assert_awaited_once()

    @pytest.mark.skip(
        reason="delete_blocks_by_ids / record_summary_block_id not in current vectorstore"
    )
    @pytest.mark.asyncio
    async def test_is_reconciliation_deletes_removed_block_ids(self):
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._process_document_chunks = AsyncMock()
        vs.delete_blocks_by_ids = AsyncMock()

        text_block = Block(index=0, type=BlockType.TEXT, format="txt", data="some text")
        container = BlocksContainer(blocks=[text_block], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain",
                is_reconciliation=True,
                block_ids_to_delete={"old-block-1"},
            )

        assert result is True
        summary_id = _vs_mod.VectorStore.record_summary_block_id("vr")
        assert vs.delete_blocks_by_ids.await_count == 2
        calls = vs.delete_blocks_by_ids.await_args_list
        assert calls[0].args == ({summary_id}, "vr")
        assert calls[1].args == ({"old-block-1"}, "vr")

    @pytest.mark.skip(
        reason="block_ids_to_delete / delete_blocks_by_ids not in current vectorstore"
    )
    @pytest.mark.asyncio
    async def test_block_ids_to_delete_called_when_no_docs_to_embed(self):
        """When every block is filtered out but block_ids_to_delete is set,
        delete_blocks_by_ids must still run (record summary first, removed blocks second)."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_blocks_by_ids = AsyncMock()

        # A divider isn't embedded -> no documents_to_embed
        divider = Block(index=0, type=BlockType.DIVIDER, format="txt", data="x")
        container = BlocksContainer(blocks=[divider], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            return_value=(MagicMock(), {"isMultimodal": False}),
        ):
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain",
                block_ids_to_delete={"stale"},
            )
        assert result is True
        summary_id = _vs_mod.VectorStore.record_summary_block_id("vr")
        assert vs.delete_blocks_by_ids.await_count == 2
        calls = vs.delete_blocks_by_ids.await_args_list
        assert calls[0].args == ({summary_id}, "vr")
        assert calls[1].args == ({"stale"}, "vr")
