"""Unit tests for app.api.routes.entity_sync (KG Clean Rebuild plan, Phase 1/7).

Covers the bounded pagination helper (``_fetch_all_entities_for_sync``), the
entity-type validation in ``trigger_entity_sync``, and (Phase 7) the
persisted sync-status helpers backing the background-sync + ``/status``
endpoints.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.routes.entity_sync import (
    _SYNC_MAX_PAGES,
    _SYNC_PAGE_SIZE,
    _execute_sync,
    _fetch_all_entities_for_sync,
    _sync_status_key,
    _write_sync_status,
)


def _page(n: int, start: int = 0) -> list[dict]:
    return [{"entityId": f"e{start + i}", "entityType": "category", "name": f"n{i}"} for i in range(n)]


class TestFetchAllEntitiesForSyncPagination:
    @pytest.mark.asyncio
    async def test_single_short_page_stops_after_one_call(self):
        graph_provider = MagicMock()
        graph_provider.get_entities_for_sync = AsyncMock(return_value=_page(3))

        result = await _fetch_all_entities_for_sync(
            graph_provider, org_id="org-1", entity_types=None, logger=MagicMock()
        )

        assert len(result) == 3
        graph_provider.get_entities_for_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_page_triggers_next_page_fetch(self):
        graph_provider = MagicMock()
        graph_provider.get_entities_for_sync = AsyncMock(
            side_effect=[_page(_SYNC_PAGE_SIZE), _page(2, start=_SYNC_PAGE_SIZE)]
        )

        result = await _fetch_all_entities_for_sync(
            graph_provider, org_id="org-1", entity_types=None, logger=MagicMock()
        )

        assert len(result) == _SYNC_PAGE_SIZE + 2
        assert graph_provider.get_entities_for_sync.call_count == 2
        second_call_kwargs = graph_provider.get_entities_for_sync.call_args_list[1][1]
        assert second_call_kwargs["offset"] == _SYNC_PAGE_SIZE

    @pytest.mark.asyncio
    async def test_empty_first_page_returns_empty_without_error(self):
        graph_provider = MagicMock()
        graph_provider.get_entities_for_sync = AsyncMock(return_value=[])

        result = await _fetch_all_entities_for_sync(
            graph_provider, org_id="org-1", entity_types=None, logger=MagicMock()
        )

        assert result == []
        graph_provider.get_entities_for_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_hits_page_cap_logs_warning_and_stops(self):
        graph_provider = MagicMock()
        # Always return a full page — pagination would never naturally stop.
        graph_provider.get_entities_for_sync = AsyncMock(return_value=_page(_SYNC_PAGE_SIZE))
        logger = MagicMock()

        result = await _fetch_all_entities_for_sync(
            graph_provider, org_id="org-1", entity_types=None, logger=logger
        )

        assert graph_provider.get_entities_for_sync.call_count == _SYNC_MAX_PAGES
        assert len(result) == _SYNC_PAGE_SIZE * _SYNC_MAX_PAGES
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_entity_types_forwarded_to_every_page_call(self):
        graph_provider = MagicMock()
        graph_provider.get_entities_for_sync = AsyncMock(return_value=[])

        await _fetch_all_entities_for_sync(
            graph_provider, org_id="org-1", entity_types=["category", "topic"], logger=MagicMock()
        )

        call_kwargs = graph_provider.get_entities_for_sync.call_args[1]
        assert call_kwargs["entity_types"] == ["category", "topic"]
        assert call_kwargs["org_id"] == "org-1"


class TestWriteSyncStatus:
    @pytest.mark.asyncio
    async def test_running_status_has_no_completed_timestamp(self) -> None:
        graph_provider = MagicMock()
        graph_provider.upsert_sync_point = AsyncMock(return_value=True)

        await _write_sync_status(graph_provider, "org-1", MagicMock(), status="running", started_at=1000)

        key, data = graph_provider.upsert_sync_point.call_args[0][:2]
        assert key == _sync_status_key("org-1")
        assert data["status"] == "running"
        assert data["startedAtTimestamp"] == 1000
        assert "completedAtTimestamp" not in data

    @pytest.mark.asyncio
    async def test_completed_status_includes_synced_count(self) -> None:
        graph_provider = MagicMock()
        graph_provider.upsert_sync_point = AsyncMock(return_value=True)

        await _write_sync_status(
            graph_provider, "org-1", MagicMock(), status="completed", synced=42, started_at=1000,
        )

        _, data = graph_provider.upsert_sync_point.call_args[0][:2]
        assert data["synced"] == 42
        assert "completedAtTimestamp" in data

    @pytest.mark.asyncio
    async def test_failed_status_includes_error(self) -> None:
        graph_provider = MagicMock()
        graph_provider.upsert_sync_point = AsyncMock(return_value=True)

        await _write_sync_status(
            graph_provider, "org-1", MagicMock(), status="failed", error="boom", started_at=1000,
        )

        _, data = graph_provider.upsert_sync_point.call_args[0][:2]
        assert data["error"] == "boom"

    @pytest.mark.asyncio
    async def test_persist_failure_is_swallowed(self) -> None:
        graph_provider = MagicMock()
        graph_provider.upsert_sync_point = AsyncMock(side_effect=RuntimeError("db down"))
        logger = MagicMock()

        await _write_sync_status(graph_provider, "org-1", logger, status="running")

        logger.warning.assert_called_once()


class TestExecuteSync:
    @pytest.mark.asyncio
    async def test_success_upserts_records_and_writes_completed_status(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_entities_for_sync = AsyncMock(
            return_value=[{"entityId": "e1", "entityType": "category", "name": "Finance"}]
        )
        graph_provider.upsert_sync_point = AsyncMock(return_value=True)
        entity_vector_store = MagicMock()
        entity_vector_store.upsert_entities_batch = AsyncMock(return_value=None)

        result = await _execute_sync(
            graph_provider, entity_vector_store, "org-1", None, MagicMock(), started_at=1000,
        )

        assert result["status"] == "success"
        assert result["synced"] == 1
        entity_vector_store.upsert_entities_batch.assert_awaited_once()
        status_data = graph_provider.upsert_sync_point.call_args[0][1]
        assert status_data["status"] == "completed"
        assert status_data["synced"] == 1

    @pytest.mark.asyncio
    async def test_no_valid_entities_reports_zero_synced(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_entities_for_sync = AsyncMock(return_value=[])
        graph_provider.upsert_sync_point = AsyncMock(return_value=True)
        entity_vector_store = MagicMock()
        entity_vector_store.upsert_entities_batch = AsyncMock(return_value=None)

        result = await _execute_sync(
            graph_provider, entity_vector_store, "org-1", None, MagicMock(), started_at=1000,
        )

        assert result["synced"] == 0
        entity_vector_store.upsert_entities_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_failure_writes_failed_status_and_reraises(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_entities_for_sync = AsyncMock(side_effect=RuntimeError("graph down"))
        graph_provider.upsert_sync_point = AsyncMock(return_value=True)
        entity_vector_store = MagicMock()

        with pytest.raises(RuntimeError):
            await _execute_sync(
                graph_provider, entity_vector_store, "org-1", None, MagicMock(), started_at=1000,
            )

        status_data = graph_provider.upsert_sync_point.call_args[0][1]
        assert status_data["status"] == "failed"
        assert "graph down" in status_data["error"]

    @pytest.mark.asyncio
    async def test_upsert_failure_writes_failed_status_and_reraises(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_entities_for_sync = AsyncMock(
            return_value=[{"entityId": "e1", "entityType": "category", "name": "Finance"}]
        )
        graph_provider.upsert_sync_point = AsyncMock(return_value=True)
        entity_vector_store = MagicMock()
        entity_vector_store.upsert_entities_batch = AsyncMock(side_effect=RuntimeError("vector db down"))

        with pytest.raises(RuntimeError):
            await _execute_sync(
                graph_provider, entity_vector_store, "org-1", None, MagicMock(), started_at=1000,
            )

        status_data = graph_provider.upsert_sync_point.call_args[0][1]
        assert status_data["status"] == "failed"
