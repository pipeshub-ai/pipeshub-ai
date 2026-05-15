"""
Unit tests for the internal /api/v1/connectors/internal/all-scheduled endpoint
and the _fetch_connector_sync_block helper.

Covered:
- _fetch_connector_sync_block: missing config, non-dict config, non-dict sync, success
- get_all_scheduled_connector_instances_internal:
    empty org list → 404
    no active connectors → empty items
    connector missing connectorId/type → skipped
    connector with MANUAL strategy → filtered out
    connector with SCHEDULED strategy → included in response
    pagination: hasMore=True when probe returns limit+1 docs
    pagination: hasMore=False on the last page
    exception in graph_provider → 500
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api.router import (
    _fetch_connector_sync_block,
    get_all_scheduled_connector_instances_internal,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    orgs=None,
    page_docs=None,
    sync_configs=None,
) -> MagicMock:
    """
    Build a minimal FastAPI Request mock that wires up:
    - request.app.container.logger()
    - request.app.container.config_service()
    - request.app.state.graph_provider
    """
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()

    config_service = AsyncMock()
    if sync_configs is not None:
        config_service.get_config = AsyncMock(side_effect=sync_configs)
    else:
        config_service.get_config = AsyncMock(return_value=None)

    graph_provider = AsyncMock()
    graph_provider.get_all_orgs = AsyncMock(return_value=orgs if orgs is not None else [{"_key": "org-1"}])
    graph_provider.get_documents_paginated = AsyncMock(return_value=page_docs if page_docs is not None else [])

    container = MagicMock()
    container.logger = MagicMock(return_value=logger)
    container.config_service = MagicMock(return_value=config_service)

    state = SimpleNamespace(graph_provider=graph_provider)

    app = MagicMock()
    app.container = container
    app.state = state

    request = MagicMock()
    request.app = app
    return request


def _active_doc(connector_id: str, connector_type: str, created_by: str = "user-1") -> dict:
    return {
        "_key": connector_id,
        "type": connector_type,
        "isActive": True,
        "createdBy": created_by,
    }


def _scheduled_sync(interval_minutes: int = 30) -> dict:
    return {
        "sync": {
            "selectedStrategy": "SCHEDULED",
            "scheduledConfig": {"intervalMinutes": interval_minutes, "timezone": "UTC"},
        }
    }


def _manual_sync() -> dict:
    return {"sync": {"selectedStrategy": "MANUAL"}}


# ---------------------------------------------------------------------------
# _fetch_connector_sync_block
# ---------------------------------------------------------------------------


class TestFetchConnectorSyncBlock:
    @pytest.mark.asyncio
    async def test_missing_config_returns_none(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        logger = MagicMock()

        result = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert result is None
        logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_non_dict_config_returns_none(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not a dict")
        logger = MagicMock()

        result = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert result is None

    @pytest.mark.asyncio
    async def test_config_with_no_sync_key_returns_empty_dict(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"auth": {}})
        logger = MagicMock()

        result = await _fetch_connector_sync_block("conn-1", config_service, logger)

        # sync = config.get("sync") or {} → empty dict, but isinstance({}, dict) is True
        assert result == {}

    @pytest.mark.asyncio
    async def test_non_dict_sync_returns_none(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": "not-a-dict"})
        logger = MagicMock()

        result = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert result is None
        logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_valid_sync_block_returned(self):
        sync_block = {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": 5}}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": sync_block})
        logger = MagicMock()

        result = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert result == sync_block

    @pytest.mark.asyncio
    async def test_config_service_exception_returns_none(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        logger = MagicMock()

        result = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert result is None
        logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — no org
# ---------------------------------------------------------------------------


class TestAllScheduledNoOrg:
    @pytest.mark.asyncio
    async def test_no_orgs_raises_404(self):
        request = _make_request(orgs=[])

        with pytest.raises(HTTPException) as exc_info:
            await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — empty / filtered pages
# ---------------------------------------------------------------------------


class TestAllScheduledEmptyPage:
    @pytest.mark.asyncio
    async def test_no_active_connectors_returns_empty_items(self):
        request = _make_request(page_docs=[])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["success"] is True
        assert result["items"] == []
        assert result["hasMore"] is False

    @pytest.mark.asyncio
    async def test_connector_without_key_is_skipped(self):
        # Document missing _key and id
        bad_doc = {"type": "Confluence", "isActive": True}
        request = _make_request(page_docs=[bad_doc], sync_configs=[None])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_connector_without_type_is_skipped(self):
        bad_doc = {"_key": "conn-1", "isActive": True}
        request = _make_request(page_docs=[bad_doc], sync_configs=[None])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == []


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — strategy filtering
# ---------------------------------------------------------------------------


class TestAllScheduledStrategyFiltering:
    @pytest.mark.asyncio
    async def test_manual_strategy_is_excluded(self):
        doc = _active_doc("conn-1", "Confluence")
        request = _make_request(
            page_docs=[doc],
            sync_configs=[_manual_sync()],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_scheduled_strategy_is_included(self):
        doc = _active_doc("conn-1", "Confluence", created_by="user-42")
        request = _make_request(
            page_docs=[doc],
            sync_configs=[_scheduled_sync(interval_minutes=15)],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["connectorId"] == "conn-1"
        assert item["type"] == "Confluence"
        assert item["orgId"] == "org-1"
        assert item["ownerUserId"] == "user-42"
        assert item["isActive"] is True
        assert item["sync"]["selectedStrategy"] == "SCHEDULED"

    @pytest.mark.asyncio
    async def test_missing_sync_config_excluded(self):
        doc = _active_doc("conn-1", "Slack")
        request = _make_request(
            page_docs=[doc],
            sync_configs=[None],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_mixed_connectors_only_scheduled_returned(self):
        docs = [
            _active_doc("conn-1", "Confluence"),
            _active_doc("conn-2", "Slack"),
            _active_doc("conn-3", "Jira"),
        ]
        # conn-1: SCHEDULED, conn-2: MANUAL, conn-3: no config
        sync_configs = [
            _scheduled_sync(),
            _manual_sync(),
            None,
        ]
        request = _make_request(page_docs=docs, sync_configs=sync_configs)

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert len(result["items"]) == 1
        assert result["items"][0]["connectorId"] == "conn-1"


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — pagination
# ---------------------------------------------------------------------------


class TestAllScheduledPagination:
    @pytest.mark.asyncio
    async def test_has_more_true_when_probe_returns_limit_plus_one(self):
        # limit=2, probe_limit=3; return 3 docs → has_more=True, items trimmed to 2
        docs = [
            _active_doc("conn-1", "Confluence"),
            _active_doc("conn-2", "Slack"),
            _active_doc("conn-3", "Jira"),  # the probe doc
        ]
        sync_configs = [_scheduled_sync(), _scheduled_sync(), _scheduled_sync()]
        request = _make_request(page_docs=docs, sync_configs=sync_configs)

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=2)

        assert result["hasMore"] is True
        assert len(result["items"]) <= 2

    @pytest.mark.asyncio
    async def test_has_more_false_on_last_page(self):
        docs = [_active_doc("conn-1", "Confluence")]
        request = _make_request(page_docs=docs, sync_configs=[_scheduled_sync()])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["hasMore"] is False

    @pytest.mark.asyncio
    async def test_skip_calculated_correctly_for_page_2(self):
        request = _make_request(page_docs=[])

        await get_all_scheduled_connector_instances_internal(request, page=2, limit=10)

        # skip = (page-1) * limit = 10
        call = request.app.state.graph_provider.get_documents_paginated.call_args
        assert call[1]["skip"] == 10

    @pytest.mark.asyncio
    async def test_skip_zero_for_page_1(self):
        request = _make_request(page_docs=[])

        await get_all_scheduled_connector_instances_internal(request, page=1, limit=10)

        call = request.app.state.graph_provider.get_documents_paginated.call_args
        assert call[1]["skip"] == 0

    @pytest.mark.asyncio
    async def test_filters_isactive_true_passed_to_provider(self):
        request = _make_request(page_docs=[])

        await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        call = request.app.state.graph_provider.get_documents_paginated.call_args
        assert call[1]["filters"] == {"isActive": True}


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — error handling
# ---------------------------------------------------------------------------


class TestAllScheduledErrorHandling:
    @pytest.mark.asyncio
    async def test_graph_provider_exception_raises_500(self):
        request = _make_request()
        request.app.state.graph_provider.get_documents_paginated = AsyncMock(
            side_effect=RuntimeError("DB unreachable")
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_propagated_unchanged(self):
        request = _make_request()
        request.app.state.graph_provider.get_all_orgs = AsyncMock(
            side_effect=HTTPException(status_code=503, detail="graph DB down")
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert exc_info.value.status_code == 503
