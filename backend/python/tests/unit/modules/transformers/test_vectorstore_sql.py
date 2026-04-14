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


def _make_vectorstore():
    with patch.object(_vs_mod, "FastEmbedSparse"), patch.object(
        _vs_mod, "_get_shared_nlp"
    ):
        vs = _vs_mod.VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_name="test-sql",
            vector_db_service=AsyncMock(),
        )
    return vs


# ---------------------------------------------------------------------------
# SQL block groups  (lines 1154-1234)
# ---------------------------------------------------------------------------


class TestSqlBlockGroups:
    @pytest.mark.asyncio
    async def test_sql_table_with_ddl_and_summary_embeds(self):
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

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
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain"
            )

        assert result is True
        vs._create_embeddings.assert_awaited_once()
        chunks = vs._create_embeddings.call_args[0][0]
        # At least one document with the DDL in its content should be queued
        assert any("CREATE TABLE" in c.page_content for c in chunks)
        assert any("Summary here" in c.page_content for c in chunks)

    @pytest.mark.asyncio
    async def test_sql_table_missing_ddl_skips(self):
        """SQL table group without DDL emits no embedding."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        bg = BlockGroup(
            index=0,
            type=GroupType.TABLE,
            sub_type=GroupSubType.SQL_TABLE,
            data={"fqn": "db.schema.t", "table_summary": "only summary"},
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain"
            )
        # Nothing to embed -> returns True but no chunks created
        assert result is True
        vs._create_embeddings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sql_view_with_definition_embeds(self):
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

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
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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
        """A view with no meaningful fields should not be embedded."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._create_embeddings = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        bg = BlockGroup(
            index=0,
            type=GroupType.VIEW,
            sub_type=GroupSubType.SQL_VIEW,
            data={"fqn": "v", "definition": "", "source_tables": [], "source_tables_summary": ""},
        )
        container = BlocksContainer(blocks=[], block_groups=[bg])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        row = Block(
            index=0,
            type=BlockType.TABLE_ROW,
            sub_type=BlockSubType.CHILD_RECORD,  # ignored; sub_type must be SQL_* for routing
        )
        # Manually set sub_type to a SQL one (not in BlockSubType enum - use string)
        row.sub_type = "sql_table"
        row.data = {"row_natural_language_text": "Row payload"}
        container = BlocksContainer(blocks=[row], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        table_block = Block(
            index=0, type=BlockType.TABLE, format="txt",
            data={"table_summary": "Summary text"},
        )
        container = BlocksContainer(blocks=[table_block], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
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
    @pytest.mark.asyncio
    async def test_is_reconciliation_with_text_and_images_routes_correctly(self):
        """When is_reconciliation=True, text goes through _process_document_chunks
        and images go through _process_image_embeddings + _store_image_points."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=True)  # multimodal
        vs._process_document_chunks = AsyncMock()
        vs._process_image_embeddings = AsyncMock(return_value=[{"fake": "point"}])
        vs._store_image_points = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        text_block = Block(index=0, type=BlockType.TEXT, format="txt", data="text")
        img_block = Block(index=1, type=BlockType.IMAGE, format="bin", data={"uri": "abc"})
        container = BlocksContainer(blocks=[text_block, img_block], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain",
                is_reconciliation=True,
            )

        assert result is True
        vs._process_document_chunks.assert_awaited_once()
        vs._process_image_embeddings.assert_awaited_once()
        vs._store_image_points.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_is_reconciliation_deletes_removed_block_ids(self):
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs._process_document_chunks = AsyncMock()
        vs.delete_blocks_by_ids = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        text_block = Block(index=0, type=BlockType.TEXT, format="txt", data="some text")
        container = BlocksContainer(blocks=[text_block], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain",
                is_reconciliation=True,
                block_ids_to_delete={"old-block-1"},
            )

        assert result is True
        vs.delete_blocks_by_ids.assert_awaited_once_with(
            {"old-block-1"}, "vr",
        )

    @pytest.mark.asyncio
    async def test_block_ids_to_delete_called_when_no_docs_to_embed(self):
        """When every block is filtered out but block_ids_to_delete is set,
        delete_blocks_by_ids must still run."""
        vs = _make_vectorstore()
        vs.get_embedding_model_instance = AsyncMock(return_value=False)
        vs.delete_blocks_by_ids = AsyncMock()
        vs.nlp = MagicMock(return_value=MagicMock(sents=[]))

        # A divider isn't embedded -> no documents_to_embed
        divider = Block(index=0, type=BlockType.DIVIDER, format="txt", data="x")
        container = BlocksContainer(blocks=[divider], block_groups=[])

        with patch(
            "app.modules.transformers.vectorstore.get_llm",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value = (MagicMock(), {"isMultimodal": False})
            result = await vs.index_documents(
                container, "org", "rec", "vr", "text/plain",
                block_ids_to_delete={"stale"},
            )
        assert result is True
        vs.delete_blocks_by_ids.assert_awaited_once_with({"stale"}, "vr")
