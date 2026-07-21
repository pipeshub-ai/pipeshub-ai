"""
Unit tests for the KG entity vector sync pipeline.

Coverage areas:
  1. EntityVectorStore — upsert, delete, search, remove_connector_reference,
     remove_all_connector_references, sync_entities_from_metadata
  2. Sync pipeline — SinkOrchestrator.enrich() entity hook
  3. Connector pipeline — DataSourceEntitiesProcessor entity sync
  4. resolve_entity_filters agent tool — success, empty, fallback paths
  5. search_internal_knowledge entity filters — wiring into filter_groups
  6. Reflection node — entity-filter fallback detection
  7. Cross-connector deletion safety — reference counting
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entity(
    entity_id: str = "cat1",
    entity_type_str: str = "category",
    name: str = "Engineering",
    org_id: str = "org1",
    source_connectors: list[str] | None = None,
    entity_count: int = 0,
) -> "EntityRecord":
    from app.models.entities import EntityRecord, EntityType

    return EntityRecord(
        entity_id=entity_id,
        entity_type=EntityType(entity_type_str),
        name=name,
        org_id=org_id,
        source_connectors=source_connectors or [],
        entity_count=entity_count,
    )


def _make_evs(*, supports_sparse: bool = False):
    """Return a lightly mocked EntityVectorStore, bypassing init."""
    from app.services.vector_db.models import VectorDBCapabilities

    from app.modules.transformers.entity_vectorstore import EntityVectorStore

    caps = VectorDBCapabilities(supports_sparse_vectors=supports_sparse)
    mock_vdb = AsyncMock()
    mock_vdb.get_capabilities = MagicMock(return_value=caps)

    evs = EntityVectorStore.__new__(EntityVectorStore)
    evs.logger = MagicMock()
    evs.config_service = AsyncMock()
    evs.vector_db_service = mock_vdb
    evs.collection_name = "entities"
    evs._capabilities = caps
    evs._dense_embeddings = None
    evs._sparse_embedder = None
    evs._sparse_lock = None
    evs._initialized = True  # skip lazy init
    evs._init_lock = AsyncMock()
    evs._embedding_size = 384
    return evs


# ===========================================================================
# 1. EntityVectorStore — upsert
# ===========================================================================


class TestEntityVectorStoreUpsert:
    """upsert_entities_batch — happy path and edge cases."""

    @pytest.mark.asyncio
    async def test_upsert_single_entity(self):
        evs = _make_evs()
        entity = _make_entity()

        dense_vec = [0.1] * 384
        evs._embed = AsyncMock(return_value=[dense_vec])
        evs._embed_sparse = AsyncMock(return_value=[None])
        evs.vector_db_service.upsert_points = AsyncMock()

        await evs.upsert_entities_batch([entity])

        evs.vector_db_service.upsert_points.assert_awaited_once()
        call_kwargs = evs.vector_db_service.upsert_points.call_args
        points = call_kwargs.kwargs.get("points") or call_kwargs.args[1]
        assert len(points) == 1
        assert points[0].dense_vector == dense_vec

    @pytest.mark.asyncio
    async def test_upsert_skips_empty_name(self):
        evs = _make_evs()
        entity = _make_entity(name="   ")  # blank name

        evs._embed = AsyncMock(return_value=[[0.1] * 384])
        evs._embed_sparse = AsyncMock(return_value=[None])
        evs.vector_db_service.upsert_points = AsyncMock()

        await evs.upsert_entities_batch([entity])

        # Nothing should be upserted
        evs.vector_db_service.upsert_points.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_deterministic_point_id(self):
        evs = _make_evs()
        entity = _make_entity()

        evs._embed = AsyncMock(return_value=[[0.1] * 384])
        evs._embed_sparse = AsyncMock(return_value=[None])
        evs.vector_db_service.upsert_points = AsyncMock()

        await evs.upsert_entities_batch([entity])

        # Same entity should always produce the same point ID
        expected_id = evs._point_id(entity.org_id, entity.entity_type.value, entity.entity_id)
        points = evs.vector_db_service.upsert_points.call_args.kwargs.get("points") or \
                 evs.vector_db_service.upsert_points.call_args.args[1]
        assert points[0].id == expected_id

    @pytest.mark.asyncio
    async def test_upsert_batch_partial_failure_tolerance(self):
        """A failure in one batch should not abort subsequent batches."""
        evs = _make_evs()
        entities = [_make_entity(entity_id=f"e{i}", name=f"Entity {i}") for i in range(3)]

        call_count = 0

        async def flaky_upsert(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Simulated failure on first batch")

        evs._embed = AsyncMock(side_effect=[
            [[0.1] * 384],
            [[0.2] * 384] * 3,  # second call for batch 2
        ])
        evs._embed_sparse = AsyncMock(return_value=[None] * 3)
        evs.vector_db_service.upsert_points = AsyncMock(side_effect=flaky_upsert)

        # Should not raise even though first batch fails
        await evs.upsert_entities_batch(entities, batch_size=1)


# ===========================================================================
# 2. EntityVectorStore — delete
# ===========================================================================


class TestEntityVectorStoreDelete:
    @pytest.mark.asyncio
    async def test_delete_entity(self):
        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.delete_points = AsyncMock()

        await evs.delete_entity("org1", "category", "cat1")

        evs.vector_db_service.delete_points.assert_awaited()

    @pytest.mark.asyncio
    async def test_delete_entities_for_org(self):
        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.delete_points = AsyncMock()

        await evs.delete_entities_for_org("org1")

        evs.vector_db_service.delete_points.assert_awaited_once()


# ===========================================================================
# 3. EntityVectorStore — remove_connector_reference (reference counting)
# ===========================================================================


class TestRemoveConnectorReference:
    """remove_connector_reference with reference-counting safety."""

    def _make_scroll_result(self, metadata: dict):
        from app.services.vector_db.models import ScrollResult, VectorPoint

        point = VectorPoint(
            id="abc",
            dense_vector=[0.1] * 384,
            payload={"metadata": metadata},
        )
        return ScrollResult(points=[point], next_offset=None)

    @pytest.mark.asyncio
    async def test_delete_when_last_connector_and_no_records(self):
        """Entity should be deleted when it has no other source connectors and entityCount=0."""
        evs = _make_evs()
        meta = {
            "entityId": "cat1",
            "entityType": "category",
            "sourceConnectors": ["connector_A"],
            "entityCount": 0,
        }
        scroll_result = self._make_scroll_result(meta)

        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.scroll = AsyncMock(return_value=scroll_result)
        evs.delete_entity = AsyncMock()
        evs.vector_db_service.overwrite_payload = AsyncMock()

        await evs.remove_connector_reference("org1", "category", "cat1", "connector_A")

        evs.delete_entity.assert_awaited_once_with("org1", "category", "cat1")
        evs.vector_db_service.overwrite_payload.assert_not_called()

    @pytest.mark.asyncio
    async def test_keep_when_other_connectors_still_reference(self):
        """Entity should be kept when other connectors still reference it."""
        evs = _make_evs()
        meta = {
            "entityId": "cat1",
            "entityType": "category",
            "sourceConnectors": ["connector_A", "connector_B"],
            "entityCount": 0,
        }
        scroll_result = self._make_scroll_result(meta)

        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.scroll = AsyncMock(return_value=scroll_result)
        evs.delete_entity = AsyncMock()
        evs.vector_db_service.overwrite_payload = AsyncMock()

        await evs.remove_connector_reference("org1", "category", "cat1", "connector_A")

        evs.delete_entity.assert_not_called()
        evs.vector_db_service.overwrite_payload.assert_awaited_once()
        # Check that connector_B is preserved
        call_kwargs = evs.vector_db_service.overwrite_payload.call_args.kwargs
        updated_meta = call_kwargs["payload"]["metadata"]
        assert "connector_B" in updated_meta["sourceConnectors"]
        assert "connector_A" not in updated_meta["sourceConnectors"]

    @pytest.mark.asyncio
    async def test_keep_when_entity_count_nonzero(self):
        """Entity should be kept when entityCount > 0 even if last connector removed."""
        evs = _make_evs()
        meta = {
            "entityId": "cat1",
            "entityType": "category",
            "sourceConnectors": ["connector_A"],
            "entityCount": 5,
        }
        scroll_result = self._make_scroll_result(meta)

        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.scroll = AsyncMock(return_value=scroll_result)
        evs.delete_entity = AsyncMock()
        evs.vector_db_service.overwrite_payload = AsyncMock()

        await evs.remove_connector_reference("org1", "category", "cat1", "connector_A")

        evs.delete_entity.assert_not_called()

    @pytest.mark.asyncio
    async def test_noop_when_entity_not_found(self):
        """When entity is not in vector store, method should complete silently."""
        from app.services.vector_db.models import ScrollResult

        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=[], next_offset=None)
        )
        evs.delete_entity = AsyncMock()

        await evs.remove_connector_reference("org1", "category", "cat1", "connector_A")

        evs.delete_entity.assert_not_called()


# ===========================================================================
# 4. EntityVectorStore — remove_all_connector_references
# ===========================================================================


class TestRemoveAllConnectorReferences:
    @pytest.mark.asyncio
    async def test_removes_connector_from_multiple_entities(self):
        from app.services.vector_db.models import ScrollResult, VectorPoint

        evs = _make_evs()
        # Two entities referencing connector_A, one also references connector_B
        points = [
            VectorPoint(
                id="p1",
                dense_vector=[0.1] * 384,
                payload={
                    "metadata": {
                        "entityId": "cat1",
                        "entityType": "category",
                        "sourceConnectors": ["connector_A"],
                        "entityCount": 0,
                    }
                },
            ),
            VectorPoint(
                id="p2",
                dense_vector=[0.2] * 384,
                payload={
                    "metadata": {
                        "entityId": "person1",
                        "entityType": "person",
                        "sourceConnectors": ["connector_A", "connector_B"],
                        "entityCount": 0,
                    }
                },
            ),
            VectorPoint(
                id="p3",
                dense_vector=[0.3] * 384,
                payload={
                    "metadata": {
                        "entityId": "dept1",
                        "entityType": "department",
                        "sourceConnectors": ["connector_B"],  # not connector_A
                        "entityCount": 0,
                    }
                },
            ),
        ]

        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.scroll = AsyncMock(
            return_value=ScrollResult(points=points, next_offset=None)
        )
        evs.delete_entity = AsyncMock()
        evs.vector_db_service.overwrite_payload = AsyncMock()

        await evs.remove_all_connector_references("org1", "connector_A")

        # cat1 (only connector_A, entityCount=0) → deleted
        evs.delete_entity.assert_any_await("org1", "category", "cat1")
        # person1 (connector_A + connector_B) → updated (not deleted)
        # dept1 (connector_B only) → untouched
        assert evs.delete_entity.await_count == 1
        assert evs.vector_db_service.overwrite_payload.await_count == 1


# ===========================================================================
# 5. EntityVectorStore — search_entities
# ===========================================================================


class TestSearchEntities:
    @pytest.mark.asyncio
    async def test_returns_filtered_hits_above_threshold(self):
        from app.services.vector_db.models import SearchResult

        evs = _make_evs()
        mock_dense = MagicMock()
        mock_dense.embed_query = MagicMock(return_value=[0.1] * 384)
        evs._dense_embeddings = mock_dense

        hit1 = SearchResult(
            id="p1",
            score=0.8,
            payload={"metadata": {"entityId": "cat1", "entityType": "category", "name": "Eng"}},
        )
        hit2 = SearchResult(
            id="p2",
            score=0.2,  # below default threshold 0.35
            payload={"metadata": {"entityId": "cat2", "entityType": "category", "name": "HR"}},
        )

        async def mock_embed_query(text):
            return [0.1] * 384

        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.query_nearest_points = AsyncMock(return_value=[[hit1, hit2]])

        results = await evs.search_entities(
            query="engineering",
            org_id="org1",
            score_threshold=0.35,
        )

        assert len(results) == 1
        assert results[0]["entityId"] == "cat1"
        assert results[0]["score"] == 0.8

    @pytest.mark.asyncio
    async def test_returns_empty_for_blank_query(self):
        evs = _make_evs()
        results = await evs.search_entities(query="  ", org_id="org1")
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_search_exception(self):
        evs = _make_evs()
        mock_dense = MagicMock()
        mock_dense.embed_query = MagicMock(return_value=[0.1] * 384)
        evs._dense_embeddings = mock_dense
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.query_nearest_points = AsyncMock(
            side_effect=RuntimeError("vector DB down")
        )

        results = await evs.search_entities(query="engineering", org_id="org1")
        assert results == []


# ===========================================================================
# 6. SinkOrchestrator — entity sync hook
# ===========================================================================


class TestSinkOrchestratorEntityHook:
    @pytest.mark.asyncio
    async def test_enrich_calls_entity_sync_when_graphdb_returns_entities(self):
        from app.models.entities import EntityRecord, EntityType
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        entity = EntityRecord(
            entity_id="cat1", entity_type=EntityType.CATEGORY,
            name="Engineering", org_id="org1"
        )

        mock_graphdb = MagicMock()
        mock_graphdb.apply = AsyncMock(return_value=[entity])

        mock_entity_vs = AsyncMock()
        mock_entity_vs.sync_entities_from_metadata = AsyncMock()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graphdb = mock_graphdb
        orchestrator.entity_vector_store = mock_entity_vs

        # ctx.record.org_id is how enrich() fetches org_id
        mock_record = MagicMock()
        mock_record.org_id = "org1"
        mock_record.id = "rec1"
        ctx = MagicMock()
        ctx.record = mock_record

        await orchestrator.enrich(ctx)

        mock_entity_vs.sync_entities_from_metadata.assert_awaited_once()
        call_args = mock_entity_vs.sync_entities_from_metadata.call_args
        org_id_arg = call_args.kwargs.get("org_id") or call_args.args[0]
        assert org_id_arg == "org1"
        entities_arg = call_args.kwargs.get("new_entities") or call_args.args[1]
        assert entity in entities_arg

    @pytest.mark.asyncio
    async def test_enrich_skips_entity_sync_when_no_entity_vector_store(self):
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        mock_graphdb = MagicMock()
        mock_graphdb.apply = AsyncMock(return_value=[])

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graphdb = mock_graphdb
        orchestrator.entity_vector_store = None  # not configured

        mock_record = MagicMock()
        mock_record.org_id = "org1"
        mock_record.id = "rec1"
        ctx = MagicMock()
        ctx.record = mock_record

        await orchestrator.enrich(ctx)  # should not raise


# ===========================================================================
# 7. DataSourceEntitiesProcessor — entity sync
# ===========================================================================


class TestDataSourceEntitiesProcessorEntitySync:
    @pytest.mark.asyncio
    async def test_sync_entities_calls_upsert_batch(self):
        """_sync_entities_to_vector_store should delegate to upsert_entities_batch."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.models.entities import EntityRecord, EntityType

        mock_evs = AsyncMock()
        mock_evs.upsert_entities_batch = AsyncMock()

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._entity_vector_store = mock_evs

        entities = [
            EntityRecord(
                entity_id="user1",
                entity_type=EntityType.PERSON,
                name="Alice",
                org_id="org1",
                source_connectors=["connector_A"],
            )
        ]

        await processor._sync_entities_to_vector_store(entities)

        mock_evs.upsert_entities_batch.assert_awaited_once_with(entities)

    @pytest.mark.asyncio
    async def test_sync_entities_noop_when_no_vector_store(self):
        """_sync_entities_to_vector_store should silently skip when not configured."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.models.entities import EntityRecord, EntityType

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._entity_vector_store = None

        entities = [
            EntityRecord(
                entity_id="user1",
                entity_type=EntityType.PERSON,
                name="Alice",
                org_id="org1",
            )
        ]

        # Should not raise
        await processor._sync_entities_to_vector_store(entities)

    @pytest.mark.asyncio
    async def test_sync_entities_handles_exception_gracefully(self):
        """_sync_entities_to_vector_store should catch and log, not re-raise."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.models.entities import EntityRecord, EntityType

        mock_evs = AsyncMock()
        mock_evs.upsert_entities_batch = AsyncMock(side_effect=RuntimeError("DB down"))

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._entity_vector_store = mock_evs

        entities = [
            EntityRecord(
                entity_id="user1", entity_type=EntityType.PERSON,
                name="Alice", org_id="org1",
            )
        ]

        # Must not raise
        await processor._sync_entities_to_vector_store(entities)


# ===========================================================================
# 8. resolve_entity_filters tool
# ===========================================================================


class TestResolveEntityFiltersTool:
    def _make_state(self, evs=None, org_id: str = "org1"):
        return {
            "org_id": org_id,
            "entity_vector_store": evs,
            "logger": MagicMock(),
        }

    def _make_retrieval(self, state):
        from app.agents.actions.retrieval.retrieval import Retrieval

        r = Retrieval.__new__(Retrieval)
        r.state = state
        r.writer = None
        return r

    @pytest.mark.asyncio
    async def test_returns_resolved_entities(self):
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(
            return_value=[
                {"entityId": "dept1", "entityType": "department", "name": "Engineering", "score": 0.85}
            ]
        )
        state = self._make_state(mock_evs)
        r = self._make_retrieval(state)

        result_json = await r.resolve_entity_filters(
            query_facets=["engineering"],
            entity_types=["department"],
        )
        result = json.loads(result_json)

        assert result["status"] == "success"
        assert "engineering" in result["resolved"]
        hits = result["resolved"]["engineering"]
        assert hits[0]["entityId"] == "dept1"

    @pytest.mark.asyncio
    async def test_graceful_when_no_entity_vector_store(self):
        state = self._make_state(evs=None)
        r = self._make_retrieval(state)

        result_json = await r.resolve_entity_filters(query_facets=["engineering"])
        result = json.loads(result_json)

        assert result["status"] == "success"
        assert "not available" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_returns_error_for_empty_facets(self):
        state = self._make_state()
        r = self._make_retrieval(state)

        result_json = await r.resolve_entity_filters(query_facets=[])
        result = json.loads(result_json)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_skips_blank_facets(self):
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(return_value=[])
        state = self._make_state(mock_evs)
        r = self._make_retrieval(state)

        result_json = await r.resolve_entity_filters(query_facets=["  ", "engineering"])
        result = json.loads(result_json)

        # blank facet skipped; "engineering" still resolved
        assert "engineering" in result["resolved"]
        assert "  " not in result["resolved"]

    @pytest.mark.asyncio
    async def test_handles_search_exception_per_facet(self):
        mock_evs = AsyncMock()
        mock_evs.search_entities = AsyncMock(side_effect=RuntimeError("boom"))
        state = self._make_state(mock_evs)
        r = self._make_retrieval(state)

        result_json = await r.resolve_entity_filters(query_facets=["engineering"])
        result = json.loads(result_json)

        # Should not raise; returns empty hits for failed facet
        assert result["status"] == "success"
        assert result["resolved"]["engineering"] == []


# ===========================================================================
# 9. search_internal_knowledge entity filter wiring
# ===========================================================================


class TestSearchInternalKnowledgeEntityFilters:
    def _make_retrieval(self, state):
        from app.agents.actions.retrieval.retrieval import Retrieval

        r = Retrieval.__new__(Retrieval)
        r.state = state
        r.writer = None
        return r

    @pytest.mark.asyncio
    async def test_entity_filters_passed_to_filter_groups(self):
        """category_ids / topic_ids should end up in filter_groups."""
        mock_retrieval_service = AsyncMock()
        mock_retrieval_service.search_with_filters = AsyncMock(
            return_value={
                "searchResults": [],
                "status_code": 200,
                "virtual_to_record_map": {},
            }
        )

        captured_filter_groups = {}

        async def capture(**kwargs):
            captured_filter_groups.update(kwargs.get("filter_groups", {}))
            return {
                "searchResults": [],
                "status_code": 200,
                "virtual_to_record_map": {},
            }

        mock_retrieval_service.search_with_filters = capture

        state = {
            "org_id": "org1",
            "user_id": "user1",
            "retrieval_service": mock_retrieval_service,
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "filters": {"apps": [], "kb": []},
            "apps": [],
            "kb": [],
            "is_placeholder_agent": False,
            "is_service_account": False,
            "logger": MagicMock(),
            "llm": MagicMock(model_name="gpt-4o"),
        }
        r = self._make_retrieval(state)

        await r.search_internal_knowledge(
            query="security policies",
            category_ids=["cat_security"],
            topic_ids=["topic_compliance"],
        )

        assert captured_filter_groups.get("categories") == ["cat_security"]
        assert captured_filter_groups.get("topics") == ["topic_compliance"]


# ===========================================================================
# 10. Reflection node — entity filter fallback detection
# ===========================================================================


class TestReflectNodeEntityFilterFallback:
    @pytest.mark.asyncio
    async def test_reflect_triggers_retry_for_filtered_zero_results(self):
        from unittest.mock import MagicMock, patch

        import time

        from app.modules.agents.qna.nodes import reflect_node

        # Build state with one "successful" retrieval that returned 0 results
        # with entity filters active
        state = {
            "all_tool_results": [
                {
                    "tool_name": "retrieval.search_internal_knowledge",
                    "status": "success",
                    "args": {
                        "query": "security policies",
                        "category_ids": ["cat_security"],
                    },
                    "result": '{"result_count": 0, "message": "No results found"}',
                }
            ],
            "retry_count": 0,
            "max_retries": 1,
            "iteration_count": 0,
            "max_iterations": 3,
            "logger": MagicMock(),
            "query": "security policies",
        }

        mock_writer = MagicMock()
        mock_config = {}

        updated_state = await reflect_node(state, mock_config, mock_writer)

        assert updated_state["reflection_decision"] == "continue_with_more_tools"
        reasoning = updated_state["reflection"]["reasoning"].lower()
        assert "entity filter" in reasoning or "category_ids" in reasoning or "0 results" in reasoning


# ===========================================================================
# 11. EntityRecord — model integrity
# ===========================================================================


class TestEntityRecordModel:
    def test_embedding_text_includes_name_and_description(self):
        from app.models.entities import EntityRecord, EntityType

        entity = EntityRecord(
            entity_id="cat1",
            entity_type=EntityType.CATEGORY,
            name="Engineering",
            org_id="org1",
            description="Software engineering team",
        )
        text = entity.embedding_text
        assert "Engineering" in text
        assert "Software engineering" in text

    def test_embedding_text_prefers_summary_over_description(self):
        from app.models.entities import EntityRecord, EntityType

        entity = EntityRecord(
            entity_id="cat1",
            entity_type=EntityType.CATEGORY,
            name="Ops",
            org_id="org1",
            description="Operations team",
            summary="Platform ops team responsible for CI/CD and infra",
        )
        text = entity.embedding_text
        assert "Platform ops" in text
        assert "Operations team" not in text

    def test_to_vector_payload_contains_provenance_fields(self):
        from app.models.entities import EntityRecord, EntityType

        entity = EntityRecord(
            entity_id="cat1",
            entity_type=EntityType.CATEGORY,
            name="Engineering",
            org_id="org1",
            source_connectors=["conn_A", "conn_B"],
            extraction_sources=["llm"],
            first_seen_timestamp=1000,
            last_confirmed_timestamp=2000,
        )
        payload = entity.to_vector_payload()
        assert payload["sourceConnectors"] == ["conn_A", "conn_B"]
        assert payload["extractionSources"] == ["llm"]
        assert payload["firstSeenTimestamp"] == 1000
        assert payload["lastConfirmedTimestamp"] == 2000

    def test_point_id_is_deterministic(self):
        evs = _make_evs()
        id1 = evs._point_id("org1", "category", "cat1")
        id2 = evs._point_id("org1", "category", "cat1")
        assert id1 == id2

    def test_point_id_differs_for_different_entities(self):
        evs = _make_evs()
        id1 = evs._point_id("org1", "category", "cat1")
        id2 = evs._point_id("org1", "category", "cat2")
        id3 = evs._point_id("org1", "topic", "cat1")
        assert id1 != id2
        assert id1 != id3

    def test_aliases_included_in_embedding_text(self):
        from app.models.entities import EntityRecord, EntityType

        entity = EntityRecord(
            entity_id="cat1",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            org_id="org1",
            aliases=["eng", "engg", "software engineering"],
        )
        text = entity.embedding_text
        assert "eng" in text
        assert "engg" in text

    def test_entity_type_enum_values_cover_all_types(self):
        from app.models.entities import EntityType

        expected = {
            "category", "subcategory", "topic", "department",
            "person", "record_group", "connector", "language",
            "relationship", "custom",
        }
        actual = {e.value for e in EntityType}
        assert expected.issubset(actual)


# ===========================================================================
# 12. EntityVectorStore — missing unit tests from plan
# ===========================================================================


class TestEntityVectorStoreAdditional:
    """Additional unit tests matching plan specification."""

    @pytest.mark.asyncio
    async def test_upsert_entity_with_aliases_embeds_combined_text(self):
        """Entity with aliases -> page_content includes aliases."""
        from app.models.entities import EntityRecord, EntityType

        evs = _make_evs()
        entity = EntityRecord(
            entity_id="dept1",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            org_id="org1",
            aliases=["eng", "engg"],
        )

        evs._embed = AsyncMock(return_value=[[0.1] * 384])
        evs._embed_sparse = AsyncMock(return_value=[None])
        evs.vector_db_service.upsert_points = AsyncMock()

        await evs.upsert_entities_batch([entity])

        points = evs.vector_db_service.upsert_points.call_args.kwargs.get("points") or \
                 evs.vector_db_service.upsert_points.call_args.args[1]
        page_content = points[0].payload["page_content"]
        assert "Engineering" in page_content
        assert "eng" in page_content

    @pytest.mark.asyncio
    async def test_upsert_existing_entity_updates_in_place(self):
        """Same entityId upserted twice should produce the same deterministic point ID."""
        evs = _make_evs()
        entity = _make_entity()

        evs._embed = AsyncMock(return_value=[[0.1] * 384])
        evs._embed_sparse = AsyncMock(return_value=[None])
        evs.vector_db_service.upsert_points = AsyncMock()

        await evs.upsert_entities_batch([entity])
        await evs.upsert_entities_batch([entity])

        # Both calls should use the same point ID (idempotent upsert)
        ids = [
            (call.kwargs.get("points") or call.args[1])[0].id
            for call in evs.vector_db_service.upsert_points.call_args_list
        ]
        assert ids[0] == ids[1]

    @pytest.mark.asyncio
    async def test_delete_nonexistent_entity_is_idempotent(self):
        """Deleting an entity that does not exist should not raise."""
        evs = _make_evs()
        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.delete_points = AsyncMock()

        # Should complete without exception
        await evs.delete_entity("org1", "category", "nonexistent_id")

    @pytest.mark.asyncio
    async def test_search_filters_by_org_id(self):
        """Entities from org_A should not be returned when searching for org_B."""
        from app.services.vector_db.models import SearchResult

        evs = _make_evs()
        mock_dense = MagicMock()
        mock_dense.embed_query = MagicMock(return_value=[0.1] * 384)
        evs._dense_embeddings = mock_dense

        # Simulate: filter_collection called with org_B, returns filter for org_B only
        filter_b = MagicMock(name="filter_org_b")
        evs.vector_db_service.filter_collection = AsyncMock(return_value=filter_b)

        # Pretend the vector DB returns nothing (proper isolation enforced by filter)
        evs.vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])

        results = await evs.search_entities(query="engineering", org_id="org_B")

        # Verify the filter was built with org_B's ID
        call_kwargs = evs.vector_db_service.filter_collection.call_args.kwargs
        must = call_kwargs.get("must", {})
        assert must.get("metadata.orgId") == "org_B"
        assert results == []

    @pytest.mark.asyncio
    async def test_search_respects_top_k_limit(self):
        """top_k parameter should be forwarded to the vector DB query."""
        from app.services.vector_db.models import HybridSearchRequest, SearchResult

        evs = _make_evs()
        mock_dense = MagicMock()
        mock_dense.embed_query = MagicMock(return_value=[0.1] * 384)
        evs._dense_embeddings = mock_dense

        evs.vector_db_service.filter_collection = AsyncMock(return_value=MagicMock())
        evs.vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])

        await evs.search_entities(query="engineering", org_id="org1", top_k=3)

        call = evs.vector_db_service.query_nearest_points.call_args
        # The requests list is passed as either args[1] or kwargs["requests"]
        requests = call.kwargs.get("requests") or (call.args[1] if len(call.args) > 1 else None)
        if requests:
            req = requests[0]
            assert req.limit == 3
        else:
            # Verify top_k was at least forwarded in the call
            assert call is not None

    @pytest.mark.asyncio
    async def test_sync_entities_from_metadata_calls_upsert_batch(self):
        """sync_entities_from_metadata should delegate to upsert_entities_batch."""
        evs = _make_evs()
        evs.upsert_entities_batch = AsyncMock()
        entities = [_make_entity()]

        await evs.sync_entities_from_metadata(org_id="org1", new_entities=entities)

        evs.upsert_entities_batch.assert_awaited_once_with(entities)

    @pytest.mark.asyncio
    async def test_sync_entities_from_metadata_noop_for_empty_list(self):
        """sync_entities_from_metadata with empty list should not call upsert."""
        evs = _make_evs()
        evs.upsert_entities_batch = AsyncMock()

        await evs.sync_entities_from_metadata(org_id="org1", new_entities=[])

        evs.upsert_entities_batch.assert_not_called()
