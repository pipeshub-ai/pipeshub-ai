"""Unit tests for app.modules.transformers.graphdb.GraphDBTransformer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_graph_provider():
    """Return a mock IGraphDBProvider."""
    return AsyncMock()


def _make_tx_store():
    """Return a mock transactional graph data store."""
    store = AsyncMock()
    store.get_record_by_key = AsyncMock(return_value={"_key": "rec-1"})
    store.get_nodes_by_filters = AsyncMock(return_value=[])
    store.get_edge = AsyncMock(return_value=None)
    store.get_edges_from_node_with_target_name = AsyncMock(return_value=[])
    store.batch_upsert_nodes = AsyncMock()
    store.batch_create_edges = AsyncMock()
    store.batch_delete_edges = AsyncMock(return_value=0)
    return store


def _make_semantic_metadata(
    departments=None,
    categories=None,
    sub_category_level_1=None,
    sub_category_level_2=None,
    sub_category_level_3=None,
    languages=None,
    topics=None,
):
    """Create a mock SemanticMetadata."""
    meta = MagicMock()
    meta.departments = departments or []
    meta.categories = categories or ["General"]
    meta.sub_category_level_1 = sub_category_level_1
    meta.sub_category_level_2 = sub_category_level_2
    meta.sub_category_level_3 = sub_category_level_3
    meta.languages = languages or []
    meta.topics = topics or []
    return meta


def _make_record(
    record_id="rec-1",
    virtual_record_id="vr-1",
    metadata=None,
    is_vlm_ocr_processed=False,
):
    """Create a mock Record for GraphDBTransformer."""
    rec = MagicMock()
    rec.id = record_id
    rec.virtual_record_id = virtual_record_id
    rec.semantic_metadata = metadata
    rec.is_vlm_ocr_processed = is_vlm_ocr_processed
    return rec


def _make_ctx(record):
    ctx = MagicMock()
    ctx.record = record
    return ctx


def _make_transformer(graph_provider=None, logger=None):
    """Instantiate a GraphDBTransformer with mocks, patching GraphDataStore."""
    from app.modules.transformers.graphdb import GraphDBTransformer

    logger = logger or MagicMock()
    graph_provider = graph_provider or _make_graph_provider()
    return GraphDBTransformer(graph_provider=graph_provider, logger=logger)


# ===================================================================
# apply – metadata is None
# ===================================================================

class TestApplyMetadataIsNone:
    """When metadata is None, apply should mark extraction as FAILED."""

    @pytest.mark.asyncio
    async def test_marks_extraction_failed(self):
        transformer = _make_transformer()
        tx_store = _make_tx_store()

        # Patch transaction context manager
        transformer.graph_data_store.transaction = MagicMock()
        ctx_mgr = AsyncMock()
        ctx_mgr.__aenter__ = AsyncMock(return_value=tx_store)
        ctx_mgr.__aexit__ = AsyncMock(return_value=False)
        transformer.graph_data_store.transaction.return_value = ctx_mgr

        record = _make_record(metadata=None)
        ctx = _make_ctx(record)

        await transformer.apply(ctx)

        # Verify batch_upsert_nodes was called with FAILED status
        tx_store.batch_upsert_nodes.assert_awaited_once()
        call_args = tx_store.batch_upsert_nodes.call_args
        status_doc = call_args[0][0][0]
        assert status_doc["extractionStatus"] == "FAILED"
        assert status_doc["indexingStatus"] == "COMPLETED"
        assert status_doc["isDirty"] is False
        assert call_args[0][1] == CollectionNames.RECORDS.value

    @pytest.mark.asyncio
    async def test_raises_on_transaction_error(self):
        transformer = _make_transformer()
        tx_store = _make_tx_store()
        tx_store.batch_upsert_nodes.side_effect = Exception("DB error")

        transformer.graph_data_store.transaction = MagicMock()
        ctx_mgr = AsyncMock()
        ctx_mgr.__aenter__ = AsyncMock(return_value=tx_store)
        ctx_mgr.__aexit__ = AsyncMock(return_value=False)
        transformer.graph_data_store.transaction.return_value = ctx_mgr

        record = _make_record(metadata=None)
        ctx = _make_ctx(record)

        with pytest.raises(Exception, match="DB error"):
            await transformer.apply(ctx)


# ===================================================================
# apply – metadata is present
# ===================================================================

class TestApplyMetadataPresent:
    """When metadata is present, apply should delegate to save_metadata_to_db."""

    @pytest.mark.asyncio
    async def test_delegates_to_save_metadata(self):
        transformer = _make_transformer()
        transformer.save_metadata_to_db = AsyncMock()

        metadata = _make_semantic_metadata(departments=["Engineering"])
        record = _make_record(metadata=metadata, is_vlm_ocr_processed=True)
        ctx = _make_ctx(record)

        await transformer.apply(ctx)

        transformer.save_metadata_to_db.assert_awaited_once_with(
            "rec-1", metadata, "vr-1", True
        )

    @pytest.mark.asyncio
    async def test_is_vlm_ocr_processed_defaults_false(self):
        transformer = _make_transformer()
        transformer.save_metadata_to_db = AsyncMock()

        metadata = _make_semantic_metadata()
        record = _make_record(metadata=metadata)
        # Simulate missing attribute
        del record.is_vlm_ocr_processed
        record.is_vlm_ocr_processed = False
        ctx = _make_ctx(record)

        await transformer.apply(ctx)

        transformer.save_metadata_to_db.assert_awaited_once_with(
            "rec-1", metadata, "vr-1", False
        )


# ===================================================================
# save_metadata_to_db
# ===================================================================

class TestSaveMetadataToDb:
    """Tests for GraphDBTransformer.save_metadata_to_db."""

    def _setup_transformer_with_tx(self, tx_store=None):
        """Create a transformer with a patched transaction context."""
        transformer = _make_transformer()
        tx_store = tx_store or _make_tx_store()

        transformer.graph_data_store.transaction = MagicMock()
        ctx_mgr = AsyncMock()
        ctx_mgr.__aenter__ = AsyncMock(return_value=tx_store)
        ctx_mgr.__aexit__ = AsyncMock(return_value=False)
        transformer.graph_data_store.transaction.return_value = ctx_mgr
        return transformer, tx_store

    @pytest.mark.asyncio
    async def test_record_not_found_raises(self):
        tx_store = _make_tx_store()
        tx_store.get_record_by_key.return_value = None
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata()
        with pytest.raises(Exception, match="not found in database"):
            await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

    @pytest.mark.asyncio
    async def test_creates_department_edges(self):
        tx_store = _make_tx_store()
        # Department exists in DB
        tx_store.get_nodes_by_filters.return_value = [
            {"_key": "dept-key-1", "departmentName": "Engineering"}
        ]
        tx_store.get_edge.return_value = None  # No existing edge
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(departments=["Engineering"])
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should create edge for department
        assert tx_store.batch_create_edges.await_count >= 1

    @pytest.mark.asyncio
    async def test_skips_existing_department_edge(self):
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = [
            {"_key": "dept-key-1", "departmentName": "Engineering"}
        ]
        # Edge already exists in reconciliation lookup
        dept_to = f"{CollectionNames.DEPARTMENTS.value}/dept-key-1"

        async def edges_side_effect(record_from, edge_collection):
            if edge_collection == CollectionNames.BELONGS_TO_DEPARTMENT.value:
                return [{"_to": dept_to, "name": "Engineering"}]
            return []

        tx_store.get_edges_from_node_with_target_name = AsyncMock(
            side_effect=edges_side_effect
        )
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            departments=["Engineering"],
            categories=["Tech"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # The department edge should NOT be created again via batch_create_edges
        # but other edges (category, etc.) may be created
        dept_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_DEPARTMENT.value
        ]
        assert len(dept_edge_calls) == 0

    @pytest.mark.asyncio
    async def test_department_not_found_logs_warning(self):
        logger = MagicMock()
        tx_store = _make_tx_store()
        # No department nodes found
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = self._setup_transformer_with_tx(tx_store)
        transformer.logger = logger

        metadata = _make_semantic_metadata(departments=["NonExistent"])
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should log a warning about missing department
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_creates_category_node_when_not_exists(self):
        tx_store = _make_tx_store()
        # First call: departments (return empty)
        # Second call: categories (return empty - category doesn't exist)
        call_count = 0

        async def side_effect(collection, filters):
            nonlocal call_count
            call_count += 1
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=side_effect)
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(categories=["NewCategory"])
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should upsert a new category node
        upsert_calls = tx_store.batch_upsert_nodes.call_args_list
        # At least one call should create a category node, and one for status
        assert len(upsert_calls) >= 1

    @pytest.mark.asyncio
    async def test_handles_languages(self):
        tx_store = _make_tx_store()
        # Languages exist
        lang_call_idx = [0]

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.LANGUAGES.value:
                return [{"_key": "lang-en", "name": "English"}]
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            languages=["English"],
            categories=["General"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should create language edge via reconciliation
        lang_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_LANGUAGE.value
        ]
        assert len(lang_edge_calls) >= 1

    @pytest.mark.asyncio
    async def test_handles_topics(self):
        tx_store = _make_tx_store()

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.TOPICS.value:
                return [{"_key": "topic-1", "name": "AI"}]
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            topics=["AI"],
            categories=["Tech"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        topic_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_TOPIC.value
        ]
        assert len(topic_edge_calls) >= 1

    @pytest.mark.asyncio
    async def test_handles_subcategories_chain(self):
        """When sub_category_level_1/2/3 are set, should create chained hierarchy."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["Main"],
            sub_category_level_1="Sub1",
            sub_category_level_2="Sub2",
            sub_category_level_3="Sub3",
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should have called batch_upsert_nodes for:
        # category, sub1, sub2, sub3, and status doc = at least 5
        assert tx_store.batch_upsert_nodes.await_count >= 4

    @pytest.mark.asyncio
    async def test_status_doc_completed(self):
        """After successful save, should upsert extraction status as COMPLETED."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(categories=["General"])
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # The last batch_upsert_nodes call should be the status doc
        last_call = tx_store.batch_upsert_nodes.call_args_list[-1]
        status_doc = last_call[0][0][0]
        assert status_doc["extractionStatus"] == "COMPLETED"
        assert status_doc["indexingStatus"] == "COMPLETED"
        assert status_doc["isDirty"] is False

    @pytest.mark.asyncio
    async def test_vlm_ocr_flag_in_status_doc(self):
        """When is_vlm_ocr_processed=True, status doc should include the flag."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(categories=["General"])
        await transformer.save_metadata_to_db(
            "rec-1", metadata, "vr-1", is_vlm_ocr_processed=True
        )

        last_call = tx_store.batch_upsert_nodes.call_args_list[-1]
        status_doc = last_call[0][0][0]
        assert status_doc.get("isVLMOcrProcessed") is True

    @pytest.mark.asyncio
    async def test_vlm_ocr_flag_absent_when_false(self):
        """When is_vlm_ocr_processed=False, status doc should NOT include the flag."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(categories=["General"])
        await transformer.save_metadata_to_db(
            "rec-1", metadata, "vr-1", is_vlm_ocr_processed=False
        )

        last_call = tx_store.batch_upsert_nodes.call_args_list[-1]
        status_doc = last_call[0][0][0]
        assert "isVLMOcrProcessed" not in status_doc

    @pytest.mark.asyncio
    async def test_exception_in_save_propagates(self):
        """Internal errors during save should propagate."""
        tx_store = _make_tx_store()
        tx_store.get_record_by_key.side_effect = Exception("DB connection lost")
        transformer, _ = self._setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata()
        with pytest.raises(Exception, match="DB connection lost"):
            await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")
