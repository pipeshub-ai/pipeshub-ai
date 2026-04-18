from __future__ import annotations

# Ensure ``typing.override`` exists on Python < 3.12 — required by some
# transitively-imported modules (e.g. messaging consumers) so that test
# collection does not fail at import time.
import typing as _typing

if not hasattr(_typing, "override"):
    try:
        from typing_extensions import override as _override

        _typing.override = _override  # type: ignore[attr-defined]
    except ImportError:  # pragma: no cover
        def _override(fn):  # type: ignore[misc]
            return fn

        _typing.override = _override  # type: ignore[attr-defined]

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.snowflake.connector import SnowflakeConnector


@pytest.mark.asyncio
async def test_ensure_scope_app_edges_team_creates_team_app_edge() -> None:
    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    connector.scope = ConnectorScope.TEAM.value
    connector.connector_id = "conn-1"
    connector.logger = MagicMock()

    tx_store = AsyncMock()

    @asynccontextmanager
    async def _transaction():
        yield tx_store

    connector.data_store_provider = MagicMock()
    connector.data_store_provider.transaction = _transaction

    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.org_id = "org-1"

    await connector._ensure_scope_app_edges()

    tx_store.ensure_team_app_edge.assert_awaited_once_with("conn-1", "org-1")


@pytest.mark.asyncio
async def test_ensure_scope_app_edges_personal_creates_user_app_edge() -> None:
    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    connector.scope = ConnectorScope.PERSONAL.value
    connector.connector_id = "conn-1"
    connector.created_by = "user-1"
    connector.connector_name = "SNOWFLAKE"
    connector.logger = MagicMock()

    creator = SimpleNamespace(
        source_user_id="source-user-1",
        id="user-1",
        email="user@example.com",
        org_id="org-1",
        full_name="User One",
        is_active=True,
        title=None,
    )

    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.org_id = "org-1"
    connector.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=creator)
    connector.data_entities_processor.on_new_app_users = AsyncMock()

    await connector._ensure_scope_app_edges()

    connector.data_entities_processor.get_user_by_user_id.assert_awaited_once_with("user-1")
    connector.data_entities_processor.on_new_app_users.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_scope_app_edges_personal_without_creator_logs_warning() -> None:
    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    connector.scope = ConnectorScope.PERSONAL.value
    connector.created_by = None
    connector.logger = MagicMock()

    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.org_id = "org-1"
    connector.data_entities_processor.get_user_by_user_id = AsyncMock()
    connector.data_entities_processor.on_new_app_users = AsyncMock()

    await connector._ensure_scope_app_edges()

    connector.data_entities_processor.get_user_by_user_id.assert_not_awaited()
    connector.data_entities_processor.on_new_app_users.assert_not_awaited()
    connector.logger.warning.assert_called_once()
