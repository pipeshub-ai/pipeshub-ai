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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.snowflake.connector import SnowflakeConnector
from app.models.permission import EntityType, PermissionType


def _make_connector() -> SnowflakeConnector:
    connector = SnowflakeConnector.__new__(SnowflakeConnector)
    connector.connector_id = "conn-1"
    connector.connector_name = "SNOWFLAKE"
    connector.scope = ConnectorScope.PERSONAL.value
    connector.created_by = "user-1"
    connector.logger = MagicMock()
    connector.config_service = MagicMock()
    connector.config_service.get_config = AsyncMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.org_id = "org-1"
    connector.data_entities_processor.get_user_by_user_id = AsyncMock()
    connector.data_entities_processor.on_new_app_users = AsyncMock()
    connector.data_store_provider = MagicMock()
    connector._run_full_sync_internal = AsyncMock()
    connector.sync_filters = {}
    connector.indexing_filters = {}
    connector.data_fetcher = object()
    connector.sync_stats = MagicMock()
    return connector


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


def test_get_app_users_maps_fields_and_skips_missing_email() -> None:
    connector = _make_connector()
    connector.data_entities_processor.org_id = "org-1"

    users = [
        SimpleNamespace(
            source_user_id=None,
            id="user-1",
            email="user@example.com",
            org_id=None,
            full_name=None,
            is_active=None,
            title="Engineer",
        ),
        SimpleNamespace(
            source_user_id="source-user-2",
            id="user-2",
            email="",
            org_id="org-2",
            full_name="User Two",
            is_active=True,
            title=None,
        ),
    ]

    app_users = connector.get_app_users(users)

    assert len(app_users) == 1
    app_user = app_users[0]
    assert app_user.source_user_id == "user-1"
    assert app_user.org_id == "org-1"
    assert app_user.full_name == "user@example.com"
    assert app_user.is_active is True
    assert app_user.title == "Engineer"


def test_get_filter_values_returns_only_present_values() -> None:
    connector = _make_connector()
    connector.sync_filters = {
        "databases": SimpleNamespace(value=["DB1"]),
        "schemas": SimpleNamespace(value=[]),
        "tables": SimpleNamespace(value=["DB1.PUBLIC.T1"]),
        "views": None,
        "stages": SimpleNamespace(value=["DB1.PUBLIC.STAGE1"]),
    }

    selected_dbs, selected_schemas, selected_tables, selected_views, selected_stages, selected_files = connector._get_filter_values()

    assert selected_dbs == ["DB1"]
    assert selected_schemas is None
    assert selected_tables == ["DB1.PUBLIC.T1"]
    assert selected_views is None
    assert selected_stages == ["DB1.PUBLIC.STAGE1"]
    assert selected_files is None


@pytest.mark.asyncio
async def test_get_permissions_returns_org_owner_permission() -> None:
    connector = _make_connector()

    permissions = await connector._get_permissions()

    assert len(permissions) == 1
    assert permissions[0].type == PermissionType.OWNER
    assert permissions[0].entity_type == EntityType.ORG


@pytest.mark.asyncio
async def test_create_app_user_delegates_to_ensure_scope_app_edges() -> None:
    connector = _make_connector()
    connector._ensure_scope_app_edges = AsyncMock()

    await connector._create_app_user()

    connector._ensure_scope_app_edges.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_returns_false_when_config_missing() -> None:
    connector = _make_connector()
    connector.config_service.get_config = AsyncMock(return_value=None)

    ok = await connector.init()

    assert ok is False
    connector.logger.error.assert_called()


@pytest.mark.asyncio
async def test_init_returns_false_without_account_identifier() -> None:
    connector = _make_connector()
    connector.config_service.get_config = AsyncMock(return_value={
        "auth": {"warehouse": "WH"},
        "credentials": {},
    })

    ok = await connector.init()

    assert ok is False
    connector.logger.error.assert_called()


@pytest.mark.asyncio
async def test_init_returns_false_without_auth_method() -> None:
    connector = _make_connector()
    connector.config_service.get_config = AsyncMock(return_value={
        "auth": {"accountIdentifier": "acct.us-east-1", "warehouse": "WH"},
        "credentials": {},
        "scope": ConnectorScope.PERSONAL.value,
        "created_by": "user-1",
    })

    ok = await connector.init()

    assert ok is False
    connector.logger.error.assert_called()


@pytest.mark.asyncio
async def test_init_with_pat_auth_success() -> None:
    connector = _make_connector()
    connector.config_service.get_config = AsyncMock(return_value={
        "auth": {
            "accountIdentifier": "acct.us-east-1",
            "warehouse": "WH",
            "patToken": "pat-token",
        },
        "credentials": {},
        "scope": ConnectorScope.TEAM.value,
        "created_by": "user-1",
    })

    with patch("app.connectors.sources.snowflake.connector.SnowflakePATConfig") as pat_config_cls, \
         patch("app.connectors.sources.snowflake.connector.SnowflakeClient") as client_cls, \
         patch("app.connectors.sources.snowflake.connector.SnowflakeDataSource") as data_source_cls, \
         patch("app.connectors.sources.snowflake.connector.SnowflakeDataFetcher") as data_fetcher_cls, \
         patch("app.connectors.sources.snowflake.connector.load_connector_filters", new=AsyncMock(return_value=({}, {}))):
        pat_config = MagicMock()
        pat_config.create_client.return_value = "raw-client"
        pat_config_cls.return_value = pat_config
        client_cls.return_value = "wrapped-client"
        data_source_cls.return_value = "data-source"
        data_fetcher_cls.return_value = "data-fetcher"

        ok = await connector.init()

    assert ok is True
    assert connector.connector_scope == ConnectorScope.TEAM.value
    assert connector.created_by == "user-1"
    assert connector.data_source == "data-source"
    assert connector.data_fetcher == "data-fetcher"


@pytest.mark.asyncio
async def test_run_sync_raises_when_data_fetcher_missing() -> None:
    connector = _make_connector()
    connector.data_fetcher = None

    with pytest.raises(ConnectionError, match="Snowflake connector not initialized"):
        await connector.run_sync()


@pytest.mark.asyncio
async def test_run_sync_loads_filters_and_runs_full_sync() -> None:
    connector = _make_connector()
    connector.config_service = MagicMock()

    stats_instance = MagicMock()
    with patch(
        "app.connectors.sources.snowflake.connector.load_connector_filters",
        new=AsyncMock(return_value=({"k": "v"}, {"ik": "iv"})),
    ), patch(
        "app.connectors.sources.snowflake.connector.SyncStats",
        return_value=stats_instance,
    ):
        await connector.run_sync()

    connector._run_full_sync_internal.assert_awaited_once()
    assert connector.sync_filters == {"k": "v"}
    assert connector.indexing_filters == {"ik": "iv"}
    stats_instance.log_summary.assert_called_once_with(connector.logger)


@pytest.mark.asyncio
async def test_run_sync_reraises_on_internal_failure() -> None:
    connector = _make_connector()
    connector._run_full_sync_internal = AsyncMock(side_effect=RuntimeError("boom"))

    with patch(
        "app.connectors.sources.snowflake.connector.load_connector_filters",
        new=AsyncMock(return_value=({}, {})),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await connector.run_sync()

    connector.logger.error.assert_called()
