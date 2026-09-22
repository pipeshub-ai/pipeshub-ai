"""Coverage-gap tests for app.modules.transformers.graphdb.GraphDBTransformer.

Targets specific uncovered lines/branches left by test_graphdb.py and
test_graphdb_deep.py:

- apply(): metadata is None AND batch_update_nodes returns False (warning +
  early return, extraction_status left untouched).
- _reconcile_edges: stale edge deletion branch (edges present in the graph
  that are no longer in the new target set get deleted).
- save_metadata_to_db: final COMPLETED status update fails (warning +
  early return, no exception).
- handle_subcategory (nested in save_metadata_to_db): the two branch misses
  around hierarchy-edge creation --
    * parent_key falsy -> skip the existing-edge check entirely
    * existing_edge found -> skip creating a duplicate hierarchy edge
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus


# ---------------------------------------------------------------------------
# Helpers (mirrors test_graphdb.py / test_graphdb_deep.py conventions)
# ---------------------------------------------------------------------------

def _make_graph_provider():
    return AsyncMock()


def _make_tx_store():
    store = AsyncMock()
    store.get_record_by_key = AsyncMock(return_value={"_key": "rec-1"})
    store.get_nodes_by_filters = AsyncMock(return_value=[])
    store.get_edge = AsyncMock(return_value=None)
    store.get_edges_from_node_with_target_name = AsyncMock(return_value=[])
    store.batch_upsert_nodes = AsyncMock()
    store.batch_update_nodes = AsyncMock(return_value=True)
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
    rec = MagicMock()
    rec.id = record_id
    rec.virtual_record_id = virtual_record_id
    rec.semantic_metadata = metadata
    rec.is_vlm_ocr_processed = is_vlm_ocr_processed
    rec.extraction_status = "PENDING"
    return rec


def _make_ctx(record):
    ctx = MagicMock()
    ctx.record = record
    return ctx


def _make_transformer(graph_provider=None, logger=None):
    from app.modules.transformers.graphdb import GraphDBTransformer

    logger = logger or MagicMock()
    graph_provider = graph_provider or _make_graph_provider()
    return GraphDBTransformer(graph_provider=graph_provider, logger=logger)


def _setup_transformer_with_tx(tx_store=None):
    transformer = _make_transformer()
    tx_store = tx_store or _make_tx_store()

    transformer.graph_data_store.transaction = MagicMock()
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=tx_store)
    ctx_mgr.__aexit__ = AsyncMock(return_value=False)
    transformer.graph_data_store.transaction.return_value = ctx_mgr
    return transformer, tx_store


# ===================================================================
# apply() – metadata is None, batch_update_nodes returns False
# (covers lines 57-61)
# ===================================================================

class TestApplyMetadataNoneUpdateFails:
    @pytest.mark.asyncio
    async def test_warns_and_returns_without_setting_extraction_status(self):
        logger = MagicMock()
        transformer, tx_store = _setup_transformer_with_tx()
        transformer.logger = logger
        tx_store.batch_update_nodes = AsyncMock(return_value=False)

        record = _make_record(metadata=None)
        ctx = _make_ctx(record)

        await transformer.apply(ctx)

        logger.warning.assert_called_once()
        warning_args = logger.warning.call_args[0]
        assert "record may not exist" in warning_args[0] or "Failed to update" in warning_args[0]
        assert record.id in warning_args

        # extraction_status must remain untouched — the early return happens
        # before `record.extraction_status = ProgressStatus.FAILED.value`
        assert record.extraction_status == "PENDING"
        assert record.extraction_status != ProgressStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_success_path_still_sets_failed_status(self):
        """Sanity check: when batch_update_nodes returns True, status IS set."""
        transformer, tx_store = _setup_transformer_with_tx()
        tx_store.batch_update_nodes = AsyncMock(return_value=True)

        record = _make_record(metadata=None)
        ctx = _make_ctx(record)

        await transformer.apply(ctx)

        assert record.extraction_status == ProgressStatus.FAILED.value


# ===================================================================
# _reconcile_edges – stale edge deletion
# (covers lines 159-179)
# ===================================================================

class TestReconcileEdgesStaleDeletion:
    @pytest.mark.asyncio
    async def test_deletes_edges_no_longer_in_new_set(self):
        transformer, tx_store = _setup_transformer_with_tx()

        existing_edges = [
            {"_to": "departments/dept-eng", "name": "Engineering"},
            {"_to": "departments/dept-mkt", "name": "Marketing"},
        ]
        tx_store.get_edges_from_node_with_target_name = AsyncMock(
            return_value=existing_edges
        )
        tx_store.batch_delete_edges = AsyncMock(return_value=1)

        # Only Engineering remains in the new set -> Marketing is stale
        new_tos = {"departments/dept-eng": "Engineering"}

        await transformer._reconcile_edges(
            tx_store,
            "rec-1",
            "records/rec-1",
            CollectionNames.BELONGS_TO_DEPARTMENT.value,
            new_tos,
            "department",
        )

        tx_store.batch_delete_edges.assert_awaited_once()
        call_args = tx_store.batch_delete_edges.call_args
        stale_edges = call_args[0][0]
        assert call_args[0][1] == CollectionNames.BELONGS_TO_DEPARTMENT.value
        assert len(stale_edges) == 1
        assert stale_edges[0]["to_id"] == "dept-mkt"
        assert stale_edges[0]["to_collection"] == CollectionNames.DEPARTMENTS.value
        assert stale_edges[0]["from_id"] == "rec-1"

        # Engineering already existed -> should NOT be recreated
        tx_store.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletes_multiple_stale_edges_sorted_by_name(self):
        transformer, tx_store = _setup_transformer_with_tx()

        existing_edges = [
            {"_to": "topics/t-zeta", "name": "Zeta"},
            {"_to": "topics/t-alpha", "name": "Alpha"},
        ]
        tx_store.get_edges_from_node_with_target_name = AsyncMock(
            return_value=existing_edges
        )
        tx_store.batch_delete_edges = AsyncMock(return_value=2)

        # New set is empty -> both existing edges are stale
        await transformer._reconcile_edges(
            tx_store,
            "rec-1",
            "records/rec-1",
            CollectionNames.BELONGS_TO_TOPIC.value,
            {},
            "topic",
        )

        tx_store.batch_delete_edges.assert_awaited_once()
        stale_edges = tx_store.batch_delete_edges.call_args[0][0]
        # Sorted by adjacent node name: Alpha before Zeta
        assert [e["to_id"] for e in stale_edges] == ["t-alpha", "t-zeta"]

    @pytest.mark.asyncio
    async def test_no_deletion_when_nothing_stale(self):
        transformer, tx_store = _setup_transformer_with_tx()

        existing_edges = [{"_to": "topics/t-ai", "name": "AI"}]
        tx_store.get_edges_from_node_with_target_name = AsyncMock(
            return_value=existing_edges
        )

        new_tos = {"topics/t-ai": "AI"}
        await transformer._reconcile_edges(
            tx_store,
            "rec-1",
            "records/rec-1",
            CollectionNames.BELONGS_TO_TOPIC.value,
            new_tos,
            "topic",
        )

        tx_store.batch_delete_edges.assert_not_awaited()


# ===================================================================
# save_metadata_to_db – final status update fails
# (covers lines 357-361)
# ===================================================================

class TestSaveMetadataFinalStatusUpdateFails:
    @pytest.mark.asyncio
    async def test_warns_and_returns_without_raising(self):
        logger = MagicMock()
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        tx_store.batch_update_nodes = AsyncMock(return_value=False)
        transformer, _ = _setup_transformer_with_tx(tx_store)
        transformer.logger = logger

        metadata = _make_semantic_metadata(categories=["General"])

        # Should not raise despite the failed status update
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        logger.warning.assert_called_once()
        warning_args = logger.warning.call_args[0]
        assert "record may not exist" in warning_args[0] or "Failed to update" in warning_args[0]
        assert "rec-1" in warning_args


# ===================================================================
# handle_subcategory branch gaps
# (covers 257->274 and 263->274 partial-branch misses)
# ===================================================================

class TestSubcategoryHierarchyBranchGaps:
    @pytest.mark.asyncio
    async def test_falsy_parent_key_skips_hierarchy_edge_check(self):
        """
        If the category node lookup returns a malformed doc with neither
        `_key` nor `id`, `_find_or_create_node` -> `_node_key` resolves to
        None. That None is threaded in as `parent_key` for sub-category
        level 1, so `handle_subcategory` must skip the existing-edge check
        (get_edge / batch_create_edges) entirely and fall straight through
        to `return key` (line 257 -> 274, false branch).
        """
        tx_store = _make_tx_store()

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.CATEGORIES.value:
                # Existing doc with no usable key -> _node_key returns None
                return [{"name": "MainCat"}]
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["MainCat"],
            sub_category_level_1="Sub1",
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # get_edge (existing-edge lookup) must never be reached since
        # parent_key was falsy for sub-category level 1
        tx_store.get_edge.assert_not_awaited()

        # No INTER_CATEGORY_RELATIONS edge should be created
        inter_cat_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.INTER_CATEGORY_RELATIONS.value
        ]
        assert len(inter_cat_calls) == 0
        tx_store.batch_update_nodes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_existing_hierarchy_edge_skips_creation(self):
        """
        When `get_edge` finds an existing hierarchy edge between the
        sub-category and its parent, `handle_subcategory` must skip the
        `batch_create_edges` call for INTER_CATEGORY_RELATIONS (263 -> 274,
        false branch) while still creating the BELONGS_TO_CATEGORY edge for
        the sub-category itself.
        """
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        # Existing hierarchy edge already present
        tx_store.get_edge = AsyncMock(return_value={"_key": "existing-edge"})
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["Main"],
            sub_category_level_1="Sub1",
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        tx_store.get_edge.assert_awaited()

        inter_cat_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.INTER_CATEGORY_RELATIONS.value
        ]
        assert len(inter_cat_calls) == 0

        # The BELONGS_TO_CATEGORY edges (category + sub1 -> record) are
        # still created via reconciliation.
        belongs_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_CATEGORY.value
        ]
        assert len(belongs_calls) >= 1
        tx_store.batch_update_nodes.assert_awaited_once()
