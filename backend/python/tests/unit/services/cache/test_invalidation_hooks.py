"""The module-level invalidation hooks and the call sites that fire them.

Two properties matter: the hooks are inert until a service registers an
invalidator (so nothing changes for services that never enable the cache), and
each write path that makes records appear or disappear actually calls one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.services.event_service import EventService
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.services.cache import invalidation_hooks as hooks
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

_PROCESSOR_MODULE = (
    "app.connectors.core.base.data_processor.data_source_entities_processor"
)


@pytest.fixture(autouse=True)
def _reset_hooks():
    hooks.reset_accessible_records_invalidator()
    yield
    hooks.reset_accessible_records_invalidator()


def _register() -> MagicMock:
    invalidator = MagicMock()
    invalidator.on_connector_sync_completed = AsyncMock()
    invalidator.on_kb_records_changed = AsyncMock()
    invalidator.on_record_indexed = AsyncMock()
    hooks._state["invalidator"] = invalidator
    return invalidator


class TestRegistration:
    async def test_hooks_are_inert_before_registration(self) -> None:
        assert hooks.get_accessible_records_invalidator() is None
        await hooks.notify_connector_sync_completed("conn-1")
        await hooks.notify_kb_records_changed("kb-1")
        await hooks.notify_record_indexed(connector_name="KB", connector_id="kb-1")

    def test_init_builds_an_invalidator(self) -> None:
        hooks.init_accessible_records_invalidator(MagicMock(), MagicMock(), MagicMock())
        assert hooks.get_accessible_records_invalidator() is not None

    def test_init_replaces_existing_invalidator(self) -> None:
        hooks.init_accessible_records_invalidator(MagicMock(), MagicMock(), MagicMock())
        first = hooks.get_accessible_records_invalidator()
        hooks.init_accessible_records_invalidator(MagicMock(), MagicMock(), MagicMock())
        assert hooks.get_accessible_records_invalidator() is not first

    async def test_hooks_forward_after_registration(self) -> None:
        invalidator = _register()

        await hooks.notify_connector_sync_completed("conn-1", "org-1")
        await hooks.notify_kb_records_changed("kb-1", "org-1")
        await hooks.notify_record_indexed(connector_name="KB", connector_id="kb-1", org_id="org-1")

        invalidator.on_connector_sync_completed.assert_awaited_once_with("conn-1", "org-1")
        invalidator.on_kb_records_changed.assert_awaited_once_with("kb-1", "org-1")
        invalidator.on_record_indexed.assert_awaited_once()

    async def test_a_raising_invalidator_cannot_break_the_caller(self) -> None:
        invalidator = _register()
        invalidator.on_connector_sync_completed = AsyncMock(side_effect=RuntimeError("boom"))
        invalidator.on_kb_records_changed = AsyncMock(side_effect=RuntimeError("boom"))
        invalidator.on_record_indexed = AsyncMock(side_effect=RuntimeError("boom"))

        await hooks.notify_connector_sync_completed("conn-1")
        await hooks.notify_kb_records_changed("kb-1")
        await hooks.notify_record_indexed(connector_name="KB", connector_id="kb-1")


class TestSyncCompletionSite:
    async def test_fires_after_a_successful_sync(self) -> None:
        service = EventService(MagicMock(), MagicMock(), MagicMock())
        service._update_app_status = AsyncMock()
        connector = MagicMock()
        connector.run_sync = AsyncMock()

        with patch.object(
            hooks, "notify_connector_sync_completed", new=AsyncMock()
        ) as notify:
            # The module imported the symbol directly, so patch it there too.
            with patch(
                "app.connectors.services.event_service.notify_connector_sync_completed",
                new=notify,
            ):
                await service._run_sync_and_clear_status(connector, "conn-1")

        notify.assert_awaited_once_with("conn-1", None)

    async def test_fires_even_when_the_sync_raises(self) -> None:
        service = EventService(MagicMock(), MagicMock(), MagicMock())
        service._update_app_status = AsyncMock()
        connector = MagicMock()
        connector.run_sync = AsyncMock(side_effect=RuntimeError("sync failed"))

        notify = AsyncMock()
        with patch(
            "app.connectors.services.event_service.notify_connector_sync_completed", new=notify
        ):
            with pytest.raises(RuntimeError, match="sync failed"):
                await service._run_sync_and_clear_status(connector, "conn-1", "org-1")

        notify.assert_awaited_once_with("conn-1", "org-1")

    async def test_boot_resume_path_also_fires(self) -> None:
        connector = MagicMock()
        connector.run_sync = AsyncMock()
        connector.data_entities_processor = MagicMock(org_id="org-1")
        invalidator = _register()

        await ConnectorFactory._run_sync_and_invalidate(connector, "conn-1")

        connector.run_sync.assert_awaited_once()
        invalidator.on_connector_sync_completed.assert_awaited_once_with("conn-1", "org-1")


class TestCascadeDeleteSite:
    def _processor(self, result):
        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.logger = MagicMock()
        processor._publish_delete_events = AsyncMock()

        tx_store = MagicMock()
        tx_store.delete_records_recursive = AsyncMock(return_value=result)
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock(return_value=tx_store)
        transaction.__aexit__ = AsyncMock(return_value=False)
        processor.data_store_provider = MagicMock()
        processor.data_store_provider.transaction = MagicMock(return_value=transaction)
        return processor

    async def test_fires_when_records_were_deleted(self) -> None:
        processor = self._processor({"successfully_deleted": 2, "eventData": None})
        notify = AsyncMock()

        with patch(
            f"{_PROCESSOR_MODULE}.notify_kb_records_changed",
            new=notify,
        ):
            await processor.on_records_deleted_cascade(["rec-1", "rec-2"], "kb-1")

        notify.assert_awaited_once_with("kb-1")

    async def test_silent_when_nothing_was_deleted(self) -> None:
        processor = self._processor({"successfully_deleted": 0, "eventData": None})
        notify = AsyncMock()

        with patch(
            f"{_PROCESSOR_MODULE}.notify_kb_records_changed",
            new=notify,
        ):
            await processor.on_records_deleted_cascade(["rec-1"], "kb-1")

        notify.assert_not_called()

    async def test_empty_request_short_circuits(self) -> None:
        processor = self._processor({"successfully_deleted": 0})
        notify = AsyncMock()

        with patch(
            f"{_PROCESSOR_MODULE}.notify_kb_records_changed",
            new=notify,
        ):
            result = await processor.on_records_deleted_cascade([], "kb-1")

        assert result["total_requested"] == 0
        notify.assert_not_called()


class TestIndexingCompletionSite:
    async def test_fires_when_a_record_becomes_searchable(self) -> None:
        orchestrator = SinkOrchestrator.__new__(SinkOrchestrator)
        orchestrator.logger = MagicMock()
        orchestrator.graph_provider = MagicMock()
        orchestrator.graph_provider.batch_upsert_nodes = AsyncMock()

        record = MagicMock()
        record.id = "rec-1"
        record.virtual_record_id = "vr-1"
        record.connector_name = Connectors.KNOWLEDGE_BASE
        record.connector_id = "kb-1"
        record.external_record_group_id = None
        record.org_id = "org-1"
        ctx = MagicMock()
        ctx.record = record

        notify = AsyncMock()
        with patch(
            "app.modules.transformers.sink_orchestrator.notify_record_indexed", new=notify
        ):
            await orchestrator._update_indexing_status(ctx)

        orchestrator.graph_provider.batch_upsert_nodes.assert_awaited_once()
        notify.assert_awaited_once_with(
            connector_name=Connectors.KNOWLEDGE_BASE,
            connector_id="kb-1",
            external_record_group_id=None,
            org_id="org-1",
        )


class TestDeleteRecordResultShape:
    async def test_success_result_carries_the_invalidation_fields(self) -> None:
        """The HTTP delete route reads these to invalidate without a re-read."""
        provider = Neo4jProvider(MagicMock(), MagicMock())
        provider.client = MagicMock()
        provider.get_document = AsyncMock(
            return_value={
                "id": "rec-1",
                "connectorId": "conn-1",
                "orgId": "org-1",
                "connectorName": "DRIVE",
                "origin": "CONNECTOR",
                "virtualRecordId": "vr-1",
            }
        )
        provider.delete_nodes_and_edges = AsyncMock()
        provider.execute_query = AsyncMock(return_value=[])
        provider.client.execute_query = AsyncMock(return_value=[])

        result = await provider.delete_record(record_id="rec-1", user_id="user-1")

        assert result["success"] is True
        assert result["connectorId"] == "conn-1"
        assert result["orgId"] == "org-1"
        assert result["isKb"] is False
