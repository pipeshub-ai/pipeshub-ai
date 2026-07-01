"""
test_entity_sync.py — Unit tests for the entity sync pipeline.

Covers:
  TestEntitySyncFromExtraction — SinkOrchestrator -> EntityVectorStore after enrichment
  TestEntitySyncFromConnector  — DataSourceEntitiesProcessor -> EntityVectorStore
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_evs():
    """Minimal EntityVectorStore stub."""
    evs = MagicMock()
    evs.upsert_entities_batch = AsyncMock()
    evs.sync_entities_from_metadata = AsyncMock()
    return evs


def _make_entity(entity_type_str: str = "category", name: str = "ML", entity_id: str = "cat1"):
    from app.models.entities import EntityRecord, EntityType

    return EntityRecord(
        entity_id=entity_id,
        entity_type=EntityType(entity_type_str),
        name=name,
        org_id="org1",
    )


def _make_ctx(org_id: str = "org1", record_id: str = "rec1"):
    mock_record = MagicMock()
    mock_record.org_id = org_id
    mock_record.id = record_id
    ctx = MagicMock()
    ctx.record = mock_record
    return ctx


def _make_record_ctx(
    org_id: str = "org1",
    record_id: str = "rec1",
    record_name: str = "Security Compliance Questionnaire",
    connector_id: str | None = "conn1",
):
    """Ctx with a record carrying a real (non-mock) record_name string."""
    mock_record = MagicMock()
    mock_record.org_id = org_id
    mock_record.id = record_id
    mock_record.record_name = record_name
    mock_record.connector_id = connector_id
    ctx = MagicMock()
    ctx.record = mock_record
    return ctx


# ===========================================================================
# 1. Extraction-originated entities (SinkOrchestrator path)
# ===========================================================================


class TestEntitySyncFromExtraction:
    """SinkOrchestrator.enrich() -> EntityVectorStore sync after graph enrichment."""

    @pytest.mark.asyncio
    async def test_new_category_created_during_enrichment_synced_to_vector(self):
        """GraphDBTransformer creates 'Machine Learning' category -> entity vector upserted."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        entity = _make_entity("category", "Machine Learning")
        mock_graphdb = MagicMock()
        mock_graphdb.apply = AsyncMock(return_value=[entity])
        mock_evs = _make_evs()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graphdb = mock_graphdb
        orchestrator.entity_vector_store = mock_evs

        await orchestrator.enrich(_make_ctx())

        mock_evs.sync_entities_from_metadata.assert_awaited_once()
        call = mock_evs.sync_entities_from_metadata.call_args
        assert call.kwargs["org_id"] == "org1"
        assert entity in call.kwargs["new_entities"]

    @pytest.mark.asyncio
    async def test_existing_category_reused_not_duplicated(self):
        """When GraphDBTransformer returns an empty entity list, no sync is triggered."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        mock_graphdb = MagicMock()
        mock_graphdb.apply = AsyncMock(return_value=[])  # no new entities
        mock_evs = _make_evs()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graphdb = mock_graphdb
        orchestrator.entity_vector_store = mock_evs

        await orchestrator.enrich(_make_ctx())

        mock_evs.sync_entities_from_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_topic_created_synced_with_correct_metadata(self):
        """New topic 'OAuth2' -> vector point has entityType='topic'."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        topic_entity = _make_entity("topic", "OAuth2", entity_id="topic_oauth")
        mock_graphdb = MagicMock()
        mock_graphdb.apply = AsyncMock(return_value=[topic_entity])
        mock_evs = _make_evs()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graphdb = mock_graphdb
        orchestrator.entity_vector_store = mock_evs

        await orchestrator.enrich(_make_ctx())

        call = mock_evs.sync_entities_from_metadata.call_args
        entities = call.kwargs["new_entities"]
        assert any(e.entity_type.value == "topic" for e in entities)
        assert any(e.name == "OAuth2" for e in entities)

    @pytest.mark.asyncio
    async def test_department_entity_synced_on_first_record_classification(self):
        """Department entity gets synced when record is classified under it."""
        from app.models.entities import EntityRecord, EntityType
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        dept_entity = EntityRecord(
            entity_id="dept_eng",
            entity_type=EntityType.DEPARTMENT,
            name="Engineering",
            org_id="org1",
        )
        mock_graphdb = MagicMock()
        mock_graphdb.apply = AsyncMock(return_value=[dept_entity])
        mock_evs = _make_evs()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graphdb = mock_graphdb
        orchestrator.entity_vector_store = mock_evs

        await orchestrator.enrich(_make_ctx())

        call = mock_evs.sync_entities_from_metadata.call_args
        entities = call.kwargs["new_entities"]
        assert any(e.entity_id == "dept_eng" for e in entities)

    @pytest.mark.asyncio
    async def test_subcategory_synced_with_parent_reference(self):
        """Subcategory entity should carry parentEntityId."""
        from app.models.entities import EntityRecord, EntityType
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        subcat = EntityRecord(
            entity_id="subcat_react_hooks",
            entity_type=EntityType.SUBCATEGORY,
            name="React Hooks",
            org_id="org1",
            parent_entity_id="cat_frontend",
            parent_entity_type=EntityType.CATEGORY,
        )
        mock_graphdb = MagicMock()
        mock_graphdb.apply = AsyncMock(return_value=[subcat])
        mock_evs = _make_evs()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graphdb = mock_graphdb
        orchestrator.entity_vector_store = mock_evs

        await orchestrator.enrich(_make_ctx())

        call = mock_evs.sync_entities_from_metadata.call_args
        entities = call.kwargs["new_entities"]
        react_entity = next(e for e in entities if e.entity_id == "subcat_react_hooks")
        assert react_entity.parent_entity_id == "cat_frontend"

    @pytest.mark.asyncio
    async def test_enrichment_failure_does_not_affect_extraction_status(self):
        """If entity sync fails, the method does NOT raise (non-blocking)."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        entity = _make_entity()
        mock_graphdb = MagicMock()
        mock_graphdb.apply = AsyncMock(return_value=[entity])

        mock_evs = MagicMock()
        mock_evs.sync_entities_from_metadata = AsyncMock(
            side_effect=RuntimeError("Vector DB unreachable")
        )

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graphdb = mock_graphdb
        orchestrator.entity_vector_store = mock_evs

        # Must not raise — entity sync is non-blocking
        await orchestrator.enrich(_make_ctx())


# ===========================================================================
# 1b. Record-name entities (SinkOrchestrator._sync_record_name_entity)
# ===========================================================================


class TestRecordNameEntitySync:
    """SinkOrchestrator._sync_record_name_entity -> EntityVectorStore.upsert_entity."""

    @pytest.mark.asyncio
    async def test_record_name_synced_after_indexing(self):
        """A record with a name calls upsert_entity with entity_type=RECORD."""
        from app.models.entities import EntityType
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        mock_evs = MagicMock()
        mock_evs.upsert_entity = AsyncMock()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.entity_vector_store = mock_evs

        ctx = _make_record_ctx(record_id="rec1", record_name="Q3 Security Report")

        await orchestrator._sync_record_name_entity(ctx)

        mock_evs.upsert_entity.assert_awaited_once()
        entity = mock_evs.upsert_entity.call_args.args[0]
        assert entity.entity_type == EntityType.RECORD
        assert entity.entity_id == "rec1"
        assert entity.name == "Q3 Security Report"

    @pytest.mark.asyncio
    async def test_record_name_entity_metadata_correct(self):
        """Vector payload has type=record, string value type, and nodeId=recordId."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        mock_evs = MagicMock()
        mock_evs.upsert_entity = AsyncMock()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.entity_vector_store = mock_evs

        ctx = _make_record_ctx(
            org_id="org42", record_id="rec42", record_name="Vendor Onboarding Checklist"
        )

        await orchestrator._sync_record_name_entity(ctx)

        entity = mock_evs.upsert_entity.call_args.args[0]
        payload = entity.to_vector_payload()
        assert payload["entityType"] == "record"
        assert payload["entityId"] == "rec42"
        assert payload["orgId"] == "org42"
        assert payload["name"] == "Vendor Onboarding Checklist"
        assert isinstance(payload["name"], str)

    @pytest.mark.asyncio
    async def test_record_name_sync_skipped_when_no_entity_vector_store(self):
        """No entity_vector_store configured -> no-op, no exception."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.entity_vector_store = None

        ctx = _make_record_ctx()

        # Must not raise
        await orchestrator._sync_record_name_entity(ctx)

    @pytest.mark.asyncio
    async def test_record_name_sync_skipped_when_name_empty(self):
        """Blank/whitespace record_name must not be upserted."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        mock_evs = MagicMock()
        mock_evs.upsert_entity = AsyncMock()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.entity_vector_store = mock_evs

        ctx = _make_record_ctx(record_name="   ")

        await orchestrator._sync_record_name_entity(ctx)

        mock_evs.upsert_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_record_name_sync_failure_is_non_fatal(self):
        """Upsert failure must be swallowed — record indexing must not fail."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        mock_evs = MagicMock()
        mock_evs.upsert_entity = AsyncMock(side_effect=RuntimeError("vector db down"))

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.entity_vector_store = mock_evs

        ctx = _make_record_ctx()

        # Must not raise
        await orchestrator._sync_record_name_entity(ctx)

    @pytest.mark.asyncio
    async def test_record_name_sync_tracks_source_connector(self):
        """source_connectors carries the record's connector_id."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        mock_evs = MagicMock()
        mock_evs.upsert_entity = AsyncMock()

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.entity_vector_store = mock_evs

        ctx = _make_record_ctx(connector_id="drive_1")

        await orchestrator._sync_record_name_entity(ctx)

        entity = mock_evs.upsert_entity.call_args.args[0]
        assert entity.source_connectors == ["drive_1"]
        assert entity.extraction_sources == ["system"]

    @pytest.mark.asyncio
    async def test_index_invokes_record_name_sync_after_successful_indexing(self):
        """SinkOrchestrator.index() calls _sync_record_name_entity once vector
        indexing succeeds and indexingStatus is updated."""
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        ctx = _make_record_ctx(record_id="rec1", record_name="Onboarding Guide")
        ctx.settings = {}
        ctx.record.block_containers.block_groups = []
        ctx.reconciliation_context = None

        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.LIMIT_SQL_ROW_BLOCKS_TO = 10
        orchestrator.blob_storage = MagicMock()
        orchestrator.blob_storage.apply = AsyncMock()
        orchestrator.graph_provider = MagicMock()
        orchestrator.graph_provider.get_document = AsyncMock(
            return_value={"indexingStatus": "NOT_STARTED"}
        )
        orchestrator.graph_provider.batch_upsert_nodes = AsyncMock()
        orchestrator.vector_store = MagicMock()
        orchestrator.vector_store.apply = AsyncMock(return_value=True)
        orchestrator.entity_vector_store = MagicMock()
        orchestrator.entity_vector_store.upsert_entity = AsyncMock()

        await orchestrator.index(ctx)

        orchestrator.entity_vector_store.upsert_entity.assert_awaited_once()
        entity = orchestrator.entity_vector_store.upsert_entity.call_args.args[0]
        assert entity.name == "Onboarding Guide"
        assert entity.entity_id == "rec1"


# ===========================================================================
# 2. Connector-originated entities (DataSourceEntitiesProcessor path)
# ===========================================================================


class TestEntitySyncFromConnector:
    """DataSourceEntitiesProcessor._sync_entities_to_vector_store path."""

    @pytest.mark.asyncio
    async def test_new_person_from_connector_synced_to_vector(self):
        """Entity with entityType='person' gets upserted via _sync_entities_to_vector_store."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.models.entities import EntityRecord, EntityType

        mock_evs = MagicMock()
        mock_evs.upsert_entities_batch = AsyncMock()

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._entity_vector_store = mock_evs

        person = EntityRecord(
            entity_id="user1",
            entity_type=EntityType.PERSON,
            name="Alice Johnson",
            org_id="org1",
            source_connectors=["drive_1"],
        )

        await processor._sync_entities_to_vector_store([person])

        mock_evs.upsert_entities_batch.assert_awaited_once_with([person])

    @pytest.mark.asyncio
    async def test_new_record_group_synced_to_vector(self):
        """Record group entity (Slack channel) gets synced correctly."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.models.entities import EntityRecord, EntityType

        mock_evs = MagicMock()
        mock_evs.upsert_entities_batch = AsyncMock()

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._entity_vector_store = mock_evs

        rg = EntityRecord(
            entity_id="rg_general",
            entity_type=EntityType.RECORD_GROUP,
            name="#general",
            org_id="org1",
            source_connectors=["slack_1"],
        )

        await processor._sync_entities_to_vector_store([rg])

        mock_evs.upsert_entities_batch.assert_awaited_once()
        entities = mock_evs.upsert_entities_batch.call_args.args[0]
        assert entities[0].entity_type.value == "record_group"

    @pytest.mark.asyncio
    async def test_person_source_connectors_tracked(self):
        """sourceConnectors list is populated when syncing person entity."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.models.entities import EntityRecord, EntityType

        mock_evs = MagicMock()
        mock_evs.upsert_entities_batch = AsyncMock()

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._entity_vector_store = mock_evs

        person = EntityRecord(
            entity_id="user_bob",
            entity_type=EntityType.PERSON,
            name="Bob Smith",
            org_id="org1",
            source_connectors=["drive_1", "jira_2"],
        )

        await processor._sync_entities_to_vector_store([person])

        entities = mock_evs.upsert_entities_batch.call_args.args[0]
        assert "drive_1" in entities[0].source_connectors
        assert "jira_2" in entities[0].source_connectors

    @pytest.mark.asyncio
    async def test_sync_noop_when_entity_vector_store_not_configured(self):
        """When entity_vector_store is None, sync is a no-op."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.models.entities import EntityRecord, EntityType

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._entity_vector_store = None

        person = EntityRecord(
            entity_id="user1", entity_type=EntityType.PERSON,
            name="Alice", org_id="org1",
        )

        # Must not raise
        await processor._sync_entities_to_vector_store([person])

    @pytest.mark.asyncio
    async def test_sync_handles_exception_gracefully(self):
        """Upsert exception in _sync_entities_to_vector_store must not propagate."""
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.models.entities import EntityRecord, EntityType

        mock_evs = MagicMock()
        mock_evs.upsert_entities_batch = AsyncMock(side_effect=RuntimeError("DB down"))

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._entity_vector_store = mock_evs

        person = EntityRecord(
            entity_id="user1", entity_type=EntityType.PERSON,
            name="Alice", org_id="org1",
        )

        # Must not raise
        await processor._sync_entities_to_vector_store([person])
