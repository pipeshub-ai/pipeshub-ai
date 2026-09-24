"""Unit tests for app.modules.transformers.sink_orchestrator.SinkOrchestrator."""

import logging
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.models.entities import EntityRecord, EntityType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(record_id="rec-001"):
    """Build a mock TransformContext with a record carrying the given id."""
    record = MagicMock()
    record.id = record_id
    ctx = MagicMock()
    ctx.record = record
    ctx.settings = {}
    return ctx


def _make_orchestrator(
    graph_doc=None,
    vector_result=None,
):
    """Build a SinkOrchestrator with all sub-transformers mocked.

    Args:
        graph_doc: The document returned by graph_provider.get_document.
                   If None the record is treated as not found.
        vector_result: The return value of vector_store.apply.  Defaults to None
                       (truthy enough to continue).
    """
    graphdb = AsyncMock()
    blob_storage = AsyncMock()
    vector_store = AsyncMock()
    vector_store.apply = AsyncMock(return_value=vector_result)
    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(return_value=graph_doc)
    graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)

    orch = SinkOrchestrator(
        graphdb=graphdb,
        blob_storage=blob_storage,
        vector_store=vector_store,
        graph_provider=graph_provider,
        logger=logging.getLogger("test-sink-orc"),
        config_service=MagicMock(),
    )
    # The Transformer base class does not set self.logger automatically in
    # all code paths.  Provide one so log calls don't blow up.
    orch.logger = MagicMock()

    return orch


# =========================================================================
# apply
# =========================================================================
class TestApply:
    """Tests for SinkOrchestrator.apply."""

    @pytest.mark.asyncio
    async def test_blob_storage_always_called_first(self):
        orch = _make_orchestrator(graph_doc={"indexingStatus": "COMPLETED"})
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.blob_storage.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_record_not_found_raises(self):
        orch = _make_orchestrator(graph_doc=None)
        ctx = _make_ctx("missing-id")

        with pytest.raises(Exception, match="not found"):
            await orch.apply(ctx)

    @pytest.mark.asyncio
    async def test_completed_skips_vector_still_enriches(self):
        """index() skips vector when already COMPLETED; enrich() still runs."""
        orch = _make_orchestrator(graph_doc={"indexingStatus": "COMPLETED"})
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_not_awaited()
        orch.graphdb.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_not_completed_runs_vector_and_graph(self):
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "IN_PROGRESS"},
            vector_result=None,  # None is not False, so processing continues
        )
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_awaited_once_with(ctx)
        orch.graphdb.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_missing_indexing_status_runs_vector_and_graph(self):
        """If the document has no indexingStatus field it is not 'COMPLETED'."""
        orch = _make_orchestrator(
            graph_doc={"someOtherField": "x"},
            vector_result=None,
        )
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_awaited_once()
        orch.graphdb.apply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_vector_store_returns_false_fails_pipeline(self):
        from app.exceptions.indexing_exceptions import IndexingError

        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "QUEUED"},
            vector_result=False,
        )
        ctx = _make_ctx()

        with pytest.raises(IndexingError, match="did not index"):
            await orch.apply(ctx)

        orch.vector_store.apply.assert_awaited_once()
        orch.graphdb.apply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vector_store_returns_true_continues_to_graph(self):
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "NOT_STARTED"},
            vector_result=True,
        )
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_awaited_once()
        orch.graphdb.apply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_provider_called_with_correct_args(self):
        orch = _make_orchestrator(graph_doc={"indexingStatus": "COMPLETED"})
        ctx = _make_ctx("my-record-id")

        await orch.apply(ctx)

        orch.graph_provider.get_document.assert_awaited_once_with(
            "my-record-id", "records"
        )

    @pytest.mark.asyncio
    async def test_failed_completed_status_write_is_non_fatal(self):
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "IN_PROGRESS"},
            vector_result=True,
        )
        orch.graph_provider.batch_upsert_nodes.return_value = False

        await orch.index(_make_ctx())

        orch.vector_store.apply.assert_awaited_once()
        orch.graph_provider.batch_upsert_nodes.assert_awaited()


class TestSkipBlob:
    @pytest.mark.asyncio
    async def test_skip_blob_does_not_write_storage(self):
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "NOT_STARTED"},
            vector_result=True,
        )
        ctx = _make_ctx()
        ctx.settings = {"skip_blob": True}

        await orch.index(ctx)

        orch.blob_storage.apply.assert_not_awaited()
        orch.vector_store.apply.assert_awaited_once_with(ctx)
        orch.graph_provider.batch_upsert_nodes.assert_awaited()

    @pytest.mark.asyncio
    async def test_skip_blob_reindexes_even_if_completed(self):
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "COMPLETED"},
            vector_result=True,
        )
        ctx = _make_ctx()
        ctx.settings = {"skip_blob": True}

        await orch.index(ctx)

        orch.blob_storage.apply.assert_not_awaited()
        orch.vector_store.apply.assert_awaited_once_with(ctx)


# =========================================================================
# enrich — entities-collection sync
# =========================================================================
class TestEnrichEntitySync:
    """Tests for the entity vector sync at the end of SinkOrchestrator.enrich."""

    @pytest.mark.asyncio
    async def test_shared_entity_is_written_for_every_record(self):
        """A second record referencing an entity the first record just wrote
        must still reach the store — it carries membership the point lacks."""
        orch = _make_orchestrator()
        orch.entity_vector_store = AsyncMock()
        from_drive = EntityRecord(
            entity_id="eng", entity_type=EntityType.DEPARTMENT, name="Engineering",
            org_id="org-1", connector_ids=["gdrive"], record_group_ids=["rg-A"],
        )
        from_confluence = EntityRecord(
            entity_id="eng", entity_type=EntityType.DEPARTMENT, name="Engineering",
            org_id="org-1", connector_ids=["confluence"], record_group_ids=["rg-B"],
        )
        orch.graphdb.apply = AsyncMock(side_effect=[[from_drive], [from_confluence]])

        await orch.enrich(_make_ctx("rec-A"))
        await orch.enrich(_make_ctx("rec-B"))

        calls = orch.entity_vector_store.upsert_entities_batch.await_args_list
        assert [call.args[0] for call in calls] == [[from_drive], [from_confluence]]


# =========================================================================
# _sync_record_group_entity (Phase 2 — Layer-0 RecordGroup sync)
# =========================================================================
class TestSyncRecordGroupEntity:
    """Tests for SinkOrchestrator._sync_record_group_entity."""

    def _make_ctx_with_group(self, record_group_id="rg-1", org_id="org-1", connector_id="conn-1"):
        record = MagicMock()
        record.id = "rec-001"
        record.record_group_id = record_group_id
        record.org_id = org_id
        record.connector_id = connector_id
        ctx = MagicMock()
        ctx.record = record
        return ctx

    def _make_orchestrator_with_evs(self, group_doc=None):
        graph_provider = AsyncMock()
        graph_provider.get_record_group_by_id = AsyncMock(return_value=group_doc)
        entity_vector_store = AsyncMock()
        orch = SinkOrchestrator(
            graphdb=AsyncMock(),
            blob_storage=AsyncMock(),
            vector_store=AsyncMock(),
            graph_provider=graph_provider,
            logger=MagicMock(),
            config_service=MagicMock(),
            entity_vector_store=entity_vector_store,
        )
        return orch

    @pytest.mark.asyncio
    async def test_no_entity_vector_store_is_noop(self):
        orch = SinkOrchestrator(
            graphdb=AsyncMock(),
            blob_storage=AsyncMock(),
            vector_store=AsyncMock(),
            graph_provider=AsyncMock(),
            logger=MagicMock(),
            config_service=MagicMock(),
        )
        await orch._sync_record_group_entity(self._make_ctx_with_group())
        # No exception, and no graph provider lookup attempted.
        orch.graph_provider.get_record_group_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_record_group_id_skips_lookup(self):
        orch = self._make_orchestrator_with_evs()
        ctx = self._make_ctx_with_group(record_group_id=None)

        await orch._sync_record_group_entity(ctx)

        orch.graph_provider.get_record_group_by_id.assert_not_awaited()
        orch.entity_vector_store.upsert_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_not_found_skips_sync(self):
        orch = self._make_orchestrator_with_evs(group_doc=None)
        ctx = self._make_ctx_with_group()

        await orch._sync_record_group_entity(ctx)

        orch.entity_vector_store.upsert_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_found_syncs_record_group_entity(self):
        orch = self._make_orchestrator_with_evs(
            group_doc={"groupName": "Engineering Space", "connectorId": "conn-1"}
        )
        ctx = self._make_ctx_with_group(record_group_id="rg-42", org_id="org-9")

        await orch._sync_record_group_entity(ctx)

        orch.graph_provider.get_record_group_by_id.assert_awaited_once_with("rg-42")
        orch.entity_vector_store.upsert_entity.assert_awaited_once()
        synced_entity = orch.entity_vector_store.upsert_entity.call_args[0][0]
        assert synced_entity.entity_id == "rg-42"
        assert synced_entity.entity_type == EntityType.RECORD_GROUP
        assert synced_entity.name == "Engineering Space"
        assert synced_entity.org_id == "org-9"
        # Self-reference: a record group entity's own id must be in its
        # own recordGroupIds, or it would only be reachable via the
        # connectorIds should-clause (see search_entities permission plan).
        assert synced_entity.record_group_ids == ["rg-42"]

    @pytest.mark.asyncio
    async def test_group_with_blank_name_is_skipped(self):
        orch = self._make_orchestrator_with_evs(group_doc={"groupName": "   "})
        ctx = self._make_ctx_with_group()

        await orch._sync_record_group_entity(ctx)

        orch.entity_vector_store.upsert_entity.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lookup_failure_is_non_fatal(self):
        orch = self._make_orchestrator_with_evs()
        orch.graph_provider.get_record_group_by_id = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        ctx = self._make_ctx_with_group()

        # Must not raise — this is best-effort.
        await orch._sync_record_group_entity(ctx)

        orch.entity_vector_store.upsert_entity.assert_not_awaited()


# =========================================================================
# sync_entities_for_duplicate (MD5-dedup entities-collection reconciliation)
# =========================================================================
class TestSyncEntitiesForDuplicate:
    """Tests for SinkOrchestrator.sync_entities_for_duplicate."""

    _RECORD_DOC = {
        "_key": "rec-dup-1",
        "orgId": "org-9",
        "connectorId": "conn-b",
        "recordGroupId": "rg-2",
        "recordName": "Q3 Plan.pdf",
    }

    def _make_orchestrator(
        self,
        taxonomy_rows=None,
        group_doc=None,
        entity_vector_store=None,
    ):
        graph_provider = AsyncMock()
        graph_provider.get_taxonomy_entities_for_record = AsyncMock(
            return_value=taxonomy_rows or []
        )
        graph_provider.get_record_group_by_id = AsyncMock(return_value=group_doc)
        if entity_vector_store is None:
            entity_vector_store = AsyncMock()
        orch = SinkOrchestrator(
            graphdb=AsyncMock(),
            blob_storage=AsyncMock(),
            vector_store=AsyncMock(),
            graph_provider=graph_provider,
            logger=MagicMock(),
            config_service=MagicMock(),
            entity_vector_store=entity_vector_store,
        )
        return orch

    @pytest.mark.asyncio
    async def test_no_entity_vector_store_is_noop(self):
        orch = SinkOrchestrator(
            graphdb=AsyncMock(),
            blob_storage=AsyncMock(),
            vector_store=AsyncMock(),
            graph_provider=AsyncMock(),
            logger=MagicMock(),
            config_service=MagicMock(),
        )
        await orch.sync_entities_for_duplicate(self._RECORD_DOC)

        orch.graph_provider.get_taxonomy_entities_for_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_key_or_org_is_noop(self):
        orch = self._make_orchestrator()

        await orch.sync_entities_for_duplicate({"orgId": "org-9"})
        await orch.sync_entities_for_duplicate({"_key": "rec-dup-1"})

        orch.graph_provider.get_taxonomy_entities_for_record.assert_not_called()
        orch.entity_vector_store.upsert_entities_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_taxonomy_rows_carry_duplicates_membership(self):
        """Taxonomy entities read from the graph are upserted with *this*
        record's connectorId/recordGroupId, not any other record's."""
        orch = self._make_orchestrator(
            taxonomy_rows=[
                {"entityId": "cat-1", "entityType": "category", "name": "Finance"},
                {"entityId": "sub-1", "entityType": "subcategory", "name": "Budgets"},
            ]
        )

        await orch.sync_entities_for_duplicate(self._RECORD_DOC)

        orch.graph_provider.get_taxonomy_entities_for_record.assert_awaited_once_with(
            "rec-dup-1"
        )
        orch.entity_vector_store.upsert_entities_batch.assert_awaited_once()
        synced = orch.entity_vector_store.upsert_entities_batch.call_args[0][0]
        taxonomy_entities = [e for e in synced if e.entity_type != EntityType.RECORD]
        assert len(taxonomy_entities) == 2
        for entity in taxonomy_entities:
            assert entity.org_id == "org-9"
            assert entity.connector_ids == ["conn-b"]
            assert entity.record_group_ids == ["rg-2"]

    @pytest.mark.asyncio
    async def test_malformed_taxonomy_row_is_skipped(self):
        orch = self._make_orchestrator(
            taxonomy_rows=[
                {"entityId": "cat-1", "entityType": "not-a-real-type", "name": "Bad"},
                {"entityType": "category", "name": "No id"},
            ]
        )

        await orch.sync_entities_for_duplicate(self._RECORD_DOC)

        synced = orch.entity_vector_store.upsert_entities_batch.call_args[0][0]
        taxonomy_entities = [e for e in synced if e.entity_type != EntityType.RECORD]
        assert taxonomy_entities == []

    @pytest.mark.asyncio
    async def test_creates_record_point(self):
        orch = self._make_orchestrator()

        await orch.sync_entities_for_duplicate(self._RECORD_DOC)

        synced = orch.entity_vector_store.upsert_entities_batch.call_args[0][0]
        record_entities = [e for e in synced if e.entity_type == EntityType.RECORD]
        assert len(record_entities) == 1
        record_entity = record_entities[0]
        assert record_entity.entity_id == "rec-dup-1"
        assert record_entity.name == "Q3 Plan.pdf"
        assert record_entity.connector_ids == ["conn-b"]
        assert record_entity.record_group_ids == ["rg-2"]

    @pytest.mark.asyncio
    async def test_creates_record_group_point_when_group_found(self):
        orch = self._make_orchestrator(
            group_doc={"groupName": "Finance Drive", "connectorId": "conn-b"}
        )

        await orch.sync_entities_for_duplicate(self._RECORD_DOC)

        orch.graph_provider.get_record_group_by_id.assert_awaited_once_with("rg-2")
        synced = orch.entity_vector_store.upsert_entities_batch.call_args[0][0]
        group_entities = [e for e in synced if e.entity_type == EntityType.RECORD_GROUP]
        assert len(group_entities) == 1
        assert group_entities[0].entity_id == "rg-2"
        assert group_entities[0].name == "Finance Drive"
        assert group_entities[0].record_group_ids == ["rg-2"]

    @pytest.mark.asyncio
    async def test_no_record_group_id_skips_group_lookup(self):
        doc = dict(self._RECORD_DOC)
        doc.pop("recordGroupId")
        orch = self._make_orchestrator()

        await orch.sync_entities_for_duplicate(doc)

        orch.graph_provider.get_record_group_by_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_not_found_skips_group_point(self):
        orch = self._make_orchestrator(group_doc=None)

        await orch.sync_entities_for_duplicate(self._RECORD_DOC)

        synced = orch.entity_vector_store.upsert_entities_batch.call_args[0][0]
        assert all(e.entity_type != EntityType.RECORD_GROUP for e in synced)

    @pytest.mark.asyncio
    async def test_nothing_to_sync_skips_upsert(self):
        doc = {"_key": "rec-dup-1", "orgId": "org-9"}
        orch = self._make_orchestrator()

        await orch.sync_entities_for_duplicate(doc)

        orch.entity_vector_store.upsert_entities_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_graph_read_failure_is_non_fatal(self):
        orch = self._make_orchestrator()
        orch.graph_provider.get_taxonomy_entities_for_record = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        # Must not raise — this is best-effort, matching the other
        # entity-vector sync helpers on this class.
        await orch.sync_entities_for_duplicate(self._RECORD_DOC)

        orch.entity_vector_store.upsert_entities_batch.assert_not_awaited()
