"""Deeper unit tests for app.modules.transformers.graphdb.GraphDBTransformer.

Covers remaining branches in save_metadata_to_db:
- Subcategory chain with 3 levels and parent edge creation
- Missing department handling
- Empty lists handling
- Transaction error propagation
"""

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
    store.batch_upsert_nodes = AsyncMock()
    store.batch_create_edges = AsyncMock()
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


def _make_transformer(graph_provider=None, logger=None):
    """Instantiate a GraphDBTransformer with mocks."""
    from app.modules.transformers.graphdb import GraphDBTransformer

    logger = logger or MagicMock()
    graph_provider = graph_provider or _make_graph_provider()
    return GraphDBTransformer(graph_provider=graph_provider, logger=logger)


def _setup_transformer_with_tx(tx_store=None):
    """Create a transformer with a patched transaction context."""
    transformer = _make_transformer()
    tx_store = tx_store or _make_tx_store()

    transformer.graph_data_store.transaction = MagicMock()
    ctx_mgr = AsyncMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=tx_store)
    ctx_mgr.__aexit__ = AsyncMock(return_value=False)
    transformer.graph_data_store.transaction.return_value = ctx_mgr
    return transformer, tx_store


# ===================================================================
# Subcategory chain with 3 levels — parent edge creation
# ===================================================================

class TestSubcategoryChainThreeLevels:
    """Test the full subcategory hierarchy: category -> sub1 -> sub2 -> sub3."""

    @pytest.mark.asyncio
    async def test_three_level_subcategory_creates_hierarchy_edges(self):
        """All three subcategory levels create parent-child hierarchy edges."""
        tx_store = _make_tx_store()
        # Return empty for all lookups so new nodes are always created
        tx_store.get_nodes_by_filters.return_value = []
        tx_store.get_edge.return_value = None  # No existing edges
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["MainCategory"],
            sub_category_level_1="SubLevel1",
            sub_category_level_2="SubLevel2",
            sub_category_level_3="SubLevel3",
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Verify batch_upsert_nodes was called for:
        # 1. category node (MainCategory)
        # 2. subcategory1 node (SubLevel1)
        # 3. subcategory2 node (SubLevel2)
        # 4. subcategory3 node (SubLevel3)
        # 5. status doc
        assert tx_store.batch_upsert_nodes.await_count >= 5

        # Verify batch_create_edges was called for:
        # 1. category -> record (BELONGS_TO_CATEGORY)
        # 2. sub1 -> record (BELONGS_TO_CATEGORY)
        # 3. sub1 -> category (INTER_CATEGORY_RELATIONS) hierarchy
        # 4. sub2 -> record (BELONGS_TO_CATEGORY)
        # 5. sub2 -> sub1 (INTER_CATEGORY_RELATIONS) hierarchy
        # 6. sub3 -> record (BELONGS_TO_CATEGORY)
        # 7. sub3 -> sub2 (INTER_CATEGORY_RELATIONS) hierarchy
        assert tx_store.batch_create_edges.await_count >= 4

    @pytest.mark.asyncio
    async def test_sub2_not_created_when_sub1_missing(self):
        """Sub-category level 2 requires sub-category level 1 to exist."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["Main"],
            sub_category_level_1=None,  # No sub1
            sub_category_level_2="Sub2",  # Should be ignored
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Only category node + status doc should be upserted
        upsert_calls = tx_store.batch_upsert_nodes.call_args_list
        # One for category creation, one for status doc = 2
        assert len(upsert_calls) == 2

    @pytest.mark.asyncio
    async def test_sub3_not_created_when_sub2_missing(self):
        """Sub-category level 3 requires sub-category level 2 to exist."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["Main"],
            sub_category_level_1="Sub1",
            sub_category_level_2=None,  # No sub2
            sub_category_level_3="Sub3",  # Should be ignored
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # category + sub1 + status = 3 upserts
        upsert_calls = tx_store.batch_upsert_nodes.call_args_list
        assert len(upsert_calls) == 3

    @pytest.mark.asyncio
    async def test_existing_subcategory_reuses_key(self):
        """When subcategory already exists, its existing key is reused."""
        tx_store = _make_tx_store()

        call_count = [0]

        async def nodes_side_effect(collection, filters):
            nonlocal call_count
            call_count[0] += 1
            if "SUBCATEGORIES1" in collection.upper() or collection == CollectionNames.SUBCATEGORIES1.value:
                return [{"_key": "existing-sub1-key", "name": "Sub1"}]
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["Main"],
            sub_category_level_1="Sub1",
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # The subcategory1 node should NOT be created (it already exists)
        # So we should have: category creation + status doc = 2 upserts
        # (sub1 already exists, so no upsert for it)
        upsert_calls = tx_store.batch_upsert_nodes.call_args_list
        assert len(upsert_calls) == 2

    @pytest.mark.asyncio
    async def test_hierarchy_edge_skipped_when_already_exists(self):
        """When hierarchy edge already exists, it's not created again."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []

        edge_call_count = [0]

        async def get_edge_side_effect(**kwargs):
            edge_call_count[0] += 1
            collection = kwargs.get("collection", "")
            if collection == CollectionNames.INTER_CATEGORY_RELATIONS.value:
                return {"_id": "existing-hierarchy-edge"}  # Already exists
            return None

        tx_store.get_edge = AsyncMock(side_effect=get_edge_side_effect)
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["Main"],
            sub_category_level_1="Sub1",
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should still complete without error
        # Hierarchy edge should NOT be created since it already exists
        inter_cat_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.INTER_CATEGORY_RELATIONS.value
        ]
        assert len(inter_cat_edge_calls) == 0


# ===================================================================
# Missing department handling
# ===================================================================

class TestMissingDepartmentHandling:
    """Test department edge creation when department is not found."""

    @pytest.mark.asyncio
    async def test_missing_department_logs_warning_and_continues(self):
        """When department not found, logs warning and continues."""
        logger = MagicMock()
        tx_store = _make_tx_store()

        # First call: department lookup returns empty
        # Second call: category lookup returns empty
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = _setup_transformer_with_tx(tx_store)
        transformer.logger = logger

        metadata = _make_semantic_metadata(
            departments=["NonExistentDept"],
            categories=["General"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should log warning
        logger.warning.assert_called()

        # Department edge should NOT be created
        dept_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_DEPARTMENT.value
        ]
        assert len(dept_edge_calls) == 0

    @pytest.mark.asyncio
    async def test_multiple_departments_some_missing(self):
        """Multiple departments where some exist and some don't."""
        logger = MagicMock()
        tx_store = _make_tx_store()

        call_count = [0]

        async def nodes_side_effect(collection, filters):
            nonlocal call_count
            call_count[0] += 1
            if collection == CollectionNames.DEPARTMENTS.value:
                dept_name = filters.get("departmentName", "")
                if dept_name == "Engineering":
                    return [{"_key": "dept-eng", "departmentName": "Engineering"}]
                return []  # NonExistent dept
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        tx_store.get_edge.return_value = None
        transformer, _ = _setup_transformer_with_tx(tx_store)
        transformer.logger = logger

        metadata = _make_semantic_metadata(
            departments=["Engineering", "NonExistent"],
            categories=["General"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should log warning for NonExistent
        warning_calls = [
            str(c) for c in logger.warning.call_args_list
        ]
        assert any("NonExistent" in w for w in warning_calls)

    @pytest.mark.asyncio
    async def test_department_error_continues_processing(self):
        """Error during department edge creation should not stop processing."""
        logger = MagicMock()
        tx_store = _make_tx_store()

        dept_call_done = [False]

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.DEPARTMENTS.value:
                return [{"_key": "dept-1"}]
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)

        # Make edge check throw only for the department edge check
        # (first call), then return None for subsequent calls (category, etc.)
        async def get_edge_side_effect(**kwargs):
            to_collection = kwargs.get("to_collection", "")
            if to_collection == CollectionNames.DEPARTMENTS.value:
                raise Exception("edge error")
            return None

        tx_store.get_edge = AsyncMock(side_effect=get_edge_side_effect)
        transformer, _ = _setup_transformer_with_tx(tx_store)
        transformer.logger = logger

        metadata = _make_semantic_metadata(
            departments=["Engineering"],
            categories=["General"],
        )
        # Should not raise - continues processing
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should log the error
        logger.error.assert_called()


# ===================================================================
# Empty lists handling
# ===================================================================

class TestEmptyListsHandling:
    """Test behavior with empty departments, languages, and topics lists."""

    @pytest.mark.asyncio
    async def test_empty_departments_list(self):
        """Empty departments list means no department edges."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            departments=[],
            categories=["General"],
            languages=[],
            topics=[],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # No department-related edge calls
        dept_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_DEPARTMENT.value
        ]
        assert len(dept_edge_calls) == 0

    @pytest.mark.asyncio
    async def test_empty_languages_list(self):
        """Empty languages list means no language edges."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["General"],
            languages=[],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        lang_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_LANGUAGE.value
        ]
        assert len(lang_edge_calls) == 0

    @pytest.mark.asyncio
    async def test_empty_topics_list(self):
        """Empty topics list means no topic edges."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["General"],
            topics=[],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        topic_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_TOPIC.value
        ]
        assert len(topic_edge_calls) == 0

    @pytest.mark.asyncio
    async def test_multiple_languages_create_separate_edges(self):
        """Multiple languages each get their own edge."""
        tx_store = _make_tx_store()

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.LANGUAGES.value:
                name = filters.get("name", "")
                return [{"_key": f"lang-{name}", "name": name}]
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        tx_store.get_edge.return_value = None
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["General"],
            languages=["English", "Spanish", "French"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        lang_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_LANGUAGE.value
        ]
        assert len(lang_edge_calls) == 3

    @pytest.mark.asyncio
    async def test_multiple_topics_create_separate_edges(self):
        """Multiple topics each get their own edge."""
        tx_store = _make_tx_store()

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.TOPICS.value:
                name = filters.get("name", "")
                return [{"_key": f"topic-{name}", "name": name}]
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        tx_store.get_edge.return_value = None
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["General"],
            topics=["AI", "ML", "NLP"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        topic_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_TOPIC.value
        ]
        assert len(topic_edge_calls) == 3


# ===================================================================
# Transaction error propagation
# ===================================================================

class TestTransactionErrorPropagation:
    """Test that errors within the transaction are properly propagated."""

    @pytest.mark.asyncio
    async def test_get_record_by_key_error_propagates(self):
        """Error in get_record_by_key propagates."""
        tx_store = _make_tx_store()
        tx_store.get_record_by_key = AsyncMock(
            side_effect=Exception("Connection lost")
        )
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata()
        with pytest.raises(Exception, match="Connection lost"):
            await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

    @pytest.mark.asyncio
    async def test_batch_upsert_nodes_error_propagates(self):
        """Error in batch_upsert_nodes propagates."""
        tx_store = _make_tx_store()
        tx_store.batch_upsert_nodes = AsyncMock(
            side_effect=Exception("Write failed")
        )
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(categories=["General"])
        with pytest.raises(Exception, match="Write failed"):
            await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

    @pytest.mark.asyncio
    async def test_batch_create_edges_error_in_category_propagates(self):
        """Error in category edge creation propagates."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = []
        tx_store.batch_create_edges = AsyncMock(
            side_effect=Exception("Edge creation failed")
        )
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(categories=["General"])
        with pytest.raises(Exception, match="Edge creation failed"):
            await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

    @pytest.mark.asyncio
    async def test_existing_category_edge_not_recreated(self):
        """When category edge already exists, don't try to create it again."""
        tx_store = _make_tx_store()
        tx_store.get_nodes_by_filters.return_value = [
            {"_key": "cat-1", "name": "Tech"}
        ]
        # Edge already exists for category
        tx_store.get_edge.return_value = {"_id": "existing-cat-edge"}
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(categories=["Tech"])
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # category edge should NOT be created since it already exists
        cat_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_CATEGORY.value
        ]
        assert len(cat_edge_calls) == 0

    @pytest.mark.asyncio
    async def test_language_node_created_when_not_exists(self):
        """Language node is created when it doesn't exist in DB."""
        tx_store = _make_tx_store()

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.LANGUAGES.value:
                return []  # Language doesn't exist
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        tx_store.get_edge.return_value = None
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["General"],
            languages=["Klingon"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should upsert: category, language, status = at least 3
        assert tx_store.batch_upsert_nodes.await_count >= 3

    @pytest.mark.asyncio
    async def test_topic_node_created_when_not_exists(self):
        """Topic node is created when it doesn't exist in DB."""
        tx_store = _make_tx_store()

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.TOPICS.value:
                return []  # Topic doesn't exist
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        tx_store.get_edge.return_value = None
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["General"],
            topics=["Quantum Computing"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        # Should upsert: category, topic, status = at least 3
        assert tx_store.batch_upsert_nodes.await_count >= 3

    @pytest.mark.asyncio
    async def test_existing_language_edge_not_recreated(self):
        """When language edge already exists, it's not created again."""
        tx_store = _make_tx_store()

        async def nodes_side_effect(collection, filters):
            if collection == CollectionNames.LANGUAGES.value:
                return [{"_key": "lang-en", "name": "English"}]
            return []

        tx_store.get_nodes_by_filters = AsyncMock(side_effect=nodes_side_effect)
        # Return existing edge for all edge lookups
        tx_store.get_edge.return_value = {"_id": "existing-edge"}
        transformer, _ = _setup_transformer_with_tx(tx_store)

        metadata = _make_semantic_metadata(
            categories=["General"],
            languages=["English"],
        )
        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        lang_edge_calls = [
            c for c in tx_store.batch_create_edges.call_args_list
            if len(c[0]) > 1 and c[0][1] == CollectionNames.BELONGS_TO_LANGUAGE.value
        ]
        assert len(lang_edge_calls) == 0
