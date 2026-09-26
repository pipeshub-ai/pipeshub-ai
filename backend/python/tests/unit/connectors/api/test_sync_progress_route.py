"""GET /api/v1/sync-progress: who may read a connector's run counters.

connector_id comes from the query string, and the counters carry failure
reasons and an indexing breakdown, so the route must make the same check as
/stats before reading anything. The real authorize_connector_stats runs here;
only the graph, the registry and the progress store are stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors
from app.connectors.api.router import get_connector_sync_progress_endpoint

STORE = "app.connectors.api.router.get_connector_sync_progress_store"


def _request(*, can_view: bool, role: str = "member") -> MagicMock:
    user = {"userId": "user-1", "orgId": "org-1", "role": role}
    request = MagicMock()
    request.state = SimpleNamespace(user=user)
    request.app.state.connector_registry = SimpleNamespace(
        can_user_view_connector=AsyncMock(return_value=can_view)
    )
    return request


def _graph(app_doc: dict | None) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=app_doc)
    graph.get_connector_stats = AsyncMock(return_value={"success": True, "data": {}})
    return graph


async def _call(request: MagicMock, graph: MagicMock, store: AsyncMock | None = None):
    getter = AsyncMock(return_value=store)
    with patch(STORE, getter):
        result = await get_connector_sync_progress_endpoint(
            request, connector_id="conn-9", include_coverage=True, graph_provider=graph
        )
    return result, getter


@pytest.mark.asyncio
async def test_a_user_who_cannot_view_the_connector_gets_403_and_nothing_is_read() -> None:
    graph = _graph({"_key": "conn-9", "type": "DRIVE"})
    getter = AsyncMock()
    with patch(STORE, getter), pytest.raises(HTTPException) as exc:
        await get_connector_sync_progress_endpoint(
            _request(can_view=False), connector_id="conn-9", include_coverage=True,
            graph_provider=graph,
        )
    assert exc.value.status_code == 403
    getter.assert_not_awaited()
    graph.get_connector_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unknown_connector_is_404_not_500() -> None:
    with patch(STORE, AsyncMock()), pytest.raises(HTTPException) as exc:
        await get_connector_sync_progress_endpoint(
            _request(can_view=True), connector_id="conn-9", include_coverage=True,
            graph_provider=_graph(None),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_kb_reader_without_a_role_on_that_kb_gets_403() -> None:
    graph = _graph({"_key": "conn-9", "type": Connectors.KNOWLEDGE_BASE.value})
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "u-key"})
    graph.get_user_kb_permission = AsyncMock(return_value=None)
    with patch(STORE, AsyncMock()), pytest.raises(HTTPException) as exc:
        await get_connector_sync_progress_endpoint(
            _request(can_view=True), connector_id="conn-9", include_coverage=True,
            graph_provider=graph,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_a_user_who_can_view_it_gets_the_progress() -> None:
    store = MagicMock()
    store.get = AsyncMock(return_value=None)
    store.redis = None
    result, getter = await _call(_request(can_view=True), _graph({"_key": "conn-9", "type": "DRIVE"}), store)
    assert result["success"] is True
    assert result["data"]["connectorId"] == "conn-9"
    getter.assert_awaited_once()
