"""Tests for app.connectors.sources.postgres.connector."""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, RecordRelations
from app.connectors.core.registry.filters import FilterCollection, FilterOption
from app.connectors.sources.postgres.connector import (
    MAX_ROWS_PER_TABLE_LIMIT,
    MAX_TABLE_DOCUMENT_BYTES,
    SYNC_STATE_KEY,
    SYNC_STATE_VERSION,
    PostgreSQLConnector,
    PostgresSchema,
    PostgresTable,
    PostgresTableState,
    SyncStats,
    _column_hash,
    split_table_fqn,
    table_fqn,
)
from app.models.entities import (
    ProgressStatus,
    RecordGroupType,
    RecordType,
)
from app.sources.client.postgres.postgres import PostgreSQLResponse
from app.sources.external.postgres.postgres_ import ColumnInfo, ForeignKeyInfo


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.postgres")

    dep = MagicMock()
    dep.org_id = "org-pg-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.reindex_existing_records = AsyncMock()

    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()

    return logger, dep, dsp, cs


def _make_connector() -> PostgreSQLConnector:
    logger, dep, dsp, cs = _make_mock_deps()
    connector = PostgreSQLConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="conn-pg-1",
    )
    return connector


def _pg_response(
    success: bool = True,
    data: Optional[Any] = None,
    error: Optional[str] = None,
) -> PostgreSQLResponse:
    return PostgreSQLResponse(success=success, data=data if data is not None else {}, error=error)


# ===========================================================================
# PostgresSchema dataclass
# ===========================================================================


class TestPostgresSchema:

    def test_default_fields(self):
        s = PostgresSchema(name="public")
        assert s.name == "public"
        assert s.owner is None

    def test_with_owner(self):
        s = PostgresSchema(name="myschema", owner="admin")
        assert s.owner == "admin"


# ===========================================================================
# PostgresTable dataclass
# ===========================================================================


class TestPostgresTable:

    def test_default_fields(self):
        t = PostgresTable(name="users", schema_name="public")
        assert t.name == "users"
        assert t.schema_name == "public"
        assert t.row_count is None
        assert t.owner is None
        assert t.columns == []
        assert t.foreign_keys == []
        assert t.primary_keys == []

    def test_fqn_property(self):
        t = PostgresTable(name="orders", schema_name="sales")
        assert t.fqn == "sales.orders"

    def test_with_columns(self):
        cols = [ColumnInfo(name="id", data_type="integer")]
        t = PostgresTable(name="t", schema_name="public", columns=cols)
        assert len(t.columns) == 1
        assert t.columns[0].name == "id"

    def test_with_foreign_keys(self):
        fks = [ForeignKeyInfo(constraint_name="fk1", column_name="user_id", foreign_table_name="other", foreign_column_name="id")]
        t = PostgresTable(name="t", schema_name="public", foreign_keys=fks)
        assert len(t.foreign_keys) == 1
        assert t.foreign_keys[0].constraint_name == "fk1"

    def test_with_primary_keys(self):
        t = PostgresTable(name="t", schema_name="public", primary_keys=["id", "name"])
        assert t.primary_keys == ["id", "name"]

    def test_with_row_count_and_owner(self):
        t = PostgresTable(name="t", schema_name="public", row_count=42, owner="postgres")
        assert t.row_count == 42
        assert t.owner == "postgres"


# ===========================================================================
# SyncStats dataclass
# ===========================================================================


class TestSyncStats:

    def test_defaults(self):
        s = SyncStats()
        assert s.schemas_synced == 0
        assert s.tables_new == 0
        assert s.errors == 0

    def test_to_dict(self):
        s = SyncStats(schemas_synced=2, tables_new=5, errors=1)
        d = s.to_dict()
        assert d == {"schemas_synced": 2, "tables_new": 5, "errors": 1}

    def test_log_summary(self):
        s = SyncStats(schemas_synced=1, tables_new=3, errors=0)
        mock_logger = MagicMock()
        s.log_summary(mock_logger)
        mock_logger.info.assert_called_once()
        call_str = mock_logger.info.call_args[0][0]
        assert "3" in call_str
        assert "1" in call_str


# ===========================================================================
# PostgreSQLConnector.__init__
# ===========================================================================


class TestPostgresConnectorInit:

    def test_connector_initializes(self):
        connector = _make_connector()
        assert connector.connector_id == "conn-pg-1"
        assert connector.connector_name == Connectors.POSTGRESQL
        assert connector.data_source is None

    def test_defaults(self):
        connector = _make_connector()
        assert connector.database_name is None
        assert connector.batch_size == 100
        assert connector.connector_scope is None
        assert connector.created_by is None

    def test_sync_stats_initialized(self):
        connector = _make_connector()
        assert isinstance(connector.sync_stats, SyncStats)
        assert connector.sync_stats.tables_new == 0
        assert connector.sync_stats.schemas_synced == 0

    def test_sync_point_created(self):
        connector = _make_connector()
        assert connector.tables_sync_point is not None

    def test_filter_collections_initialized(self):
        connector = _make_connector()
        assert connector.sync_filters is not None
        assert connector.indexing_filters is not None

    def test_filter_caches_initialized_empty(self):
        connector = _make_connector()
        assert connector._schema_filter_cache == []
        assert connector._table_filter_cache == []
        assert connector._filter_cache_rebuild_event is None


# ===========================================================================
# PostgreSQLConnector.init
# ===========================================================================


class TestPostgresConnectorInitMethod:

    @pytest.mark.asyncio
    async def test_returns_false_when_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_missing_required_fields(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"host": "", "database": "", "username": ""},
        })
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_host(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"database": "mydb", "username": "user", "password": "pw"},
        })
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success_with_individual_fields(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "localhost",
                "port": "5432",
                "database": "testdb",
                "username": "postgres",
                "password": "secret",
            },
        })
        with patch(
            "app.connectors.sources.postgres.connector.PostgreSQLConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.postgres.connector.PostgreSQLDataSource"
        ) as mock_ds_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client
            mock_ds_cls.return_value = MagicMock()

            result = await connector.init()

        assert result is True
        assert connector.data_source is not None
        assert connector.database_name == "testdb"

    @staticmethod
    async def _init_with(connector, auth):
        connector.config_service.get_config = AsyncMock(return_value={"auth": auth})
        with patch(
            "app.connectors.sources.postgres.connector.PostgreSQLConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.postgres.connector.PostgreSQLDataSource"
        ):
            mock_client = MagicMock()
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client
            assert await connector.init() is True
        return mock_config_cls.call_args.kwargs

    @pytest.mark.asyncio
    async def test_connection_string_sslmode_is_used(self):
        # Ignoring it connected with "prefer": no certificate check, and a
        # silent fallback to an unencrypted connection.
        kwargs = await self._init_with(_make_connector(), {
            "connectionString": "postgresql://u:p@db.example:5432/app?sslmode=verify-full",
        })
        assert kwargs["sslmode"] == "verify-full"

    @pytest.mark.asyncio
    async def test_sslmode_defaults_to_prefer(self):
        kwargs = await self._init_with(_make_connector(), {
            "connectionString": "postgresql://u:p@db.example:5432/app",
        })
        assert kwargs["sslmode"] == "prefer"

    @pytest.mark.asyncio
    async def test_returns_true_on_success_with_connection_string(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "connectionString": "postgresql://postgres:secret@localhost:5432/testdb",
            },
        })
        with patch(
            "app.connectors.sources.postgres.connector.PostgreSQLConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.postgres.connector.PostgreSQLDataSource"
        ) as mock_ds_cls:
            mock_client = MagicMock()
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client
            mock_ds_cls.return_value = MagicMock()

            result = await connector.init()

        assert result is True
        assert connector.database_name == "testdb"

    @pytest.mark.asyncio
    async def test_returns_false_on_invalid_connection_string(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "connectionString": "not-a-valid-uri",
            },
        })
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            side_effect=Exception("Service error")
        )
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_sets_connector_scope(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "localhost",
                "port": "5432",
                "database": "testdb",
                "username": "postgres",
                "password": "secret",
            },
            "scope": "INDIVIDUAL",
        })
        with patch(
            "app.connectors.sources.postgres.connector.PostgreSQLConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.postgres.connector.PostgreSQLDataSource"
        ):
            mock_client = MagicMock()
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client

            await connector.init()

        assert connector.connector_scope == "INDIVIDUAL"

    @pytest.mark.asyncio
    async def test_sets_created_by(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "localhost",
                "port": "5432",
                "database": "testdb",
                "username": "postgres",
                "password": "secret",
            },
            "created_by": "user-456",
        })
        with patch(
            "app.connectors.sources.postgres.connector.PostgreSQLConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.postgres.connector.PostgreSQLDataSource"
        ):
            mock_client = MagicMock()
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client

            await connector.init()

        assert connector.created_by == "user-456"

    @pytest.mark.asyncio
    async def test_default_port_when_not_specified(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "localhost",
                "database": "testdb",
                "username": "postgres",
                "password": "secret",
            },
        })
        with patch(
            "app.connectors.sources.postgres.connector.PostgreSQLConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.postgres.connector.PostgreSQLDataSource"
        ):
            mock_client = MagicMock()
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client

            result = await connector.init()

        assert result is True


# ===========================================================================
# PostgreSQLConnector.test_connection_and_access
# ===========================================================================


class TestTestConnectionAndAccess:

    @pytest.mark.asyncio
    async def test_returns_false_when_data_source_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.test_connection = AsyncMock(
            return_value=_pg_response(True, {"version": "PostgreSQL 16.1"})
        )
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_api_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.test_connection = AsyncMock(
            return_value=_pg_response(False, error="Connection refused")
        )
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.test_connection = AsyncMock(
            side_effect=Exception("Network error")
        )
        result = await connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# PostgreSQLConnector.get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    def test_returns_none(self):
        connector = _make_connector()
        record = MagicMock()
        result = connector.get_signed_url(record)
        assert result is None


# ===========================================================================
# PostgreSQLConnector.handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    def test_raises_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({"type": "test"})


# ===========================================================================
# PostgreSQLConnector.get_app_users
# ===========================================================================


class TestGetAppUsers:

    def test_converts_users(self):
        connector = _make_connector()
        user = MagicMock()
        user.email = "alice@example.com"
        user.full_name = "Alice"
        user.source_user_id = "u-1"
        user.id = "u-1"
        user.org_id = "org-pg-1"
        user.is_active = True
        user.title = "Engineer"

        result = connector.get_app_users([user])
        assert len(result) == 1
        assert result[0].email == "alice@example.com"
        assert result[0].full_name == "Alice"
        assert result[0].app_name == Connectors.POSTGRESQL
        assert result[0].connector_id == "conn-pg-1"

    def test_skips_users_without_email(self):
        connector = _make_connector()
        user = MagicMock()
        user.email = None
        result = connector.get_app_users([user])
        assert len(result) == 0

    def test_empty_list(self):
        connector = _make_connector()
        result = connector.get_app_users([])
        assert result == []

    def test_defaults_is_active_to_true(self):
        connector = _make_connector()
        user = MagicMock()
        user.email = "bob@example.com"
        user.full_name = "Bob"
        user.source_user_id = "u-2"
        user.id = "u-2"
        user.org_id = None
        user.is_active = None
        user.title = None

        result = connector.get_app_users([user])
        assert len(result) == 1
        assert result[0].is_active is True

    def test_uses_org_id_fallback(self):
        connector = _make_connector()
        user = MagicMock()
        user.email = "carol@example.com"
        user.full_name = "Carol"
        user.source_user_id = "u-3"
        user.id = "u-3"
        user.org_id = None
        user.is_active = True
        user.title = None

        result = connector.get_app_users([user])
        assert result[0].org_id == "org-pg-1"


# ===========================================================================
# PostgreSQLConnector._create_app_users
# ===========================================================================


class TestCreateAppUsers:

    @pytest.mark.asyncio
    async def test_team_scope_creates_team_app_edge(self):
        connector = _make_connector()
        connector.scope = "team"
        connector.data_entities_processor.ensure_team_app_edge = AsyncMock()

        await connector._create_app_users()

        connector.data_entities_processor.ensure_team_app_edge.assert_awaited_once_with(
            "conn-pg-1"
        )
        connector.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_personal_scope_creates_user_app_edge_for_creator(self):
        connector = _make_connector()
        connector.scope = "personal"
        connector.created_by = "user-42"

        mock_user = MagicMock()
        mock_user.email = "creator@example.com"
        mock_user.full_name = "Creator"
        mock_user.source_user_id = "user-42"
        mock_user.id = "user-42"
        mock_user.org_id = "org-pg-1"
        mock_user.is_active = True
        mock_user.title = None

        connector.data_entities_processor.get_user_by_user_id = AsyncMock(
            return_value=mock_user
        )

        await connector._create_app_users()

        connector.data_entities_processor.get_user_by_user_id.assert_awaited_once_with("user-42")
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()
        app_users_arg = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(app_users_arg) == 1
        assert app_users_arg[0].email == "creator@example.com"

    @pytest.mark.asyncio
    async def test_personal_scope_skips_when_creator_not_found(self):
        connector = _make_connector()
        connector.scope = "personal"
        connector.created_by = "ghost"
        connector.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=None)

        await connector._create_app_users()

        connector.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_personal_scope_skips_when_no_created_by(self):
        connector = _make_connector()
        connector.scope = "personal"
        connector.created_by = None
        connector.data_entities_processor.get_user_by_user_id = AsyncMock()

        await connector._create_app_users()

        connector.data_entities_processor.get_user_by_user_id.assert_not_called()
        connector.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_on_error(self):
        connector = _make_connector()
        connector.scope = "team"
        connector.data_entities_processor.ensure_team_app_edge = AsyncMock(
            side_effect=Exception("DB error")
        )

        with pytest.raises(Exception, match="DB error"):
            await connector._create_app_users()


# ===========================================================================
# PostgreSQLConnector._get_permissions
# ===========================================================================


class TestGetPermissions:

    @pytest.mark.asyncio
    async def test_returns_org_owner_permission(self):
        connector = _make_connector()
        perms = await connector._get_permissions()
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"


# ===========================================================================
# PostgreSQLConnector._fetch_schemas
# ===========================================================================


class TestFetchSchemas:

    @pytest.mark.asyncio
    async def test_returns_schemas(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(
            return_value=_pg_response(True, [
                {"name": "public", "owner": "postgres"},
                {"name": "app", "owner": "admin"},
            ])
        )

        schemas = await connector._fetch_schemas()
        assert len(schemas) == 2
        assert schemas[0].name == "public"
        assert schemas[0].owner == "postgres"

    @pytest.mark.asyncio
    async def test_raises_on_failure(self):
        # An empty list would read as "no schemas" and a full sync would then
        # remove every table.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(
            return_value=_pg_response(False, error="Access denied")
        )
        with pytest.raises(RuntimeError, match="Access denied"):
            await connector._fetch_schemas()


# ===========================================================================
# PostgreSQLConnector._list_table_names / _load_table / _load_tables
# ===========================================================================


def _mock_table_metadata(connector, info=None, fks=None, pks=None):
    connector.data_source.get_table_info = AsyncMock(
        return_value=info or _pg_response(True, {"columns": [{"name": "id"}]})
    )
    connector.data_source.get_foreign_keys = AsyncMock(
        return_value=fks or _pg_response(True, [])
    )
    connector.data_source.get_primary_keys = AsyncMock(
        return_value=pks or _pg_response(True, [{"column_name": "id"}])
    )


class TestListTableNames:

    @pytest.mark.asyncio
    async def test_returns_names(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(True, [{"name": "users"}, {"name": "orders"}])
        )
        assert await connector._list_table_names("public") == ["users", "orders"]

    @pytest.mark.asyncio
    async def test_raises_on_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(False, error="Access denied")
        )
        with pytest.raises(RuntimeError, match="Access denied"):
            await connector._list_table_names("public")


class TestLoadTable:

    @pytest.mark.asyncio
    async def test_loads_columns_and_keys(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        _mock_table_metadata(connector)
        state = PostgresTableState(column_hash="h", n_live_tup=42)

        table = await connector._load_table("public", "users", state)

        assert table.name == "users"
        assert table.schema_name == "public"
        assert [c.name for c in table.columns] == ["id"]
        assert table.primary_keys == ["id"]
        assert table.row_count == 42
        assert table.state is state

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", ["info", "fks", "pks"])
    async def test_raises_when_any_metadata_fails(self, failing):
        # Syncing the table anyway would index it as though it had no columns or keys.
        connector = _make_connector()
        connector.data_source = MagicMock()
        _mock_table_metadata(connector, **{failing: _pg_response(False, error="boom")})

        with pytest.raises(RuntimeError, match="boom"):
            await connector._load_table("public", "users")

    @pytest.mark.asyncio
    async def test_load_tables_skips_and_counts_failures(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        _mock_table_metadata(connector)
        connector.data_source.get_table_info = AsyncMock(side_effect=[
            _pg_response(False, error="gone"),
            _pg_response(True, {"columns": []}),
        ])

        tables = await connector._load_tables("public", ["t1", "t2"], {})

        assert [t.name for t in tables] == ["t2"]
        assert connector.sync_stats.errors == 1


# ===========================================================================
# PostgreSQLConnector._create_database_record_group
# ===========================================================================


class TestCreateDatabaseRecordGroup:

    @pytest.mark.asyncio
    async def test_creates_record_group(self):
        connector = _make_connector()
        connector.database_name = "testdb"
        await connector._create_database_record_group()
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(call_args) == 1
        rg = call_args[0][0]
        assert rg.name == "testdb"
        assert rg.group_type == RecordGroupType.SQL_DATABASE


# ===========================================================================
# PostgreSQLConnector._sync_schemas
# ===========================================================================


class TestSyncSchemas:

    @pytest.mark.asyncio
    async def test_syncs_schemas(self):
        connector = _make_connector()
        connector.database_name = "testdb"
        schemas = [PostgresSchema(name="public"), PostgresSchema(name="app")]
        await connector._sync_schemas(schemas)
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(call_args) == 2
        rg = call_args[0][0]
        assert rg.group_type == RecordGroupType.SQL_NAMESPACE
        assert rg.parent_external_group_id == "testdb"

    @pytest.mark.asyncio
    async def test_schema_named_after_database_gets_its_own_group(self):
        # Groups are looked up by external id alone; sharing one with the
        # database group would overwrite it and drop its org permission.
        connector = _make_connector()
        connector.database_name = "app"
        await connector._sync_schemas([PostgresSchema(name="app"), PostgresSchema(name="public")])

        groups = [rg for rg, _ in connector.data_entities_processor.on_new_record_groups.call_args[0][0]]
        assert groups[0].external_group_id == "app.app"
        assert groups[0].name == "app"
        assert groups[0].parent_external_group_id == "app"
        assert groups[1].external_group_id == "public"

    @pytest.mark.asyncio
    async def test_skips_empty_schemas_list(self):
        connector = _make_connector()
        await connector._sync_schemas([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()


# ===========================================================================
# PostgreSQLConnector._get_filter_values
# ===========================================================================


class TestGetFilterValues:

    def test_returns_none_none_when_no_filters(self):
        connector = _make_connector()
        schemas, schemas_op, tables, tables_op = connector._get_filter_values()
        assert schemas is None
        assert tables is None
        # Default operator when filter is absent is IN
        assert schemas_op == "in"
        assert tables_op == "in"

    def test_returns_filter_values(self):
        connector = _make_connector()
        schema_filter = MagicMock()
        schema_filter.value = ["public"]
        schema_filter.operator_value = "in"
        table_filter = MagicMock()
        table_filter.value = ["public.users"]
        table_filter.operator_value = "in"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda k: {"schemas": schema_filter, "tables": table_filter}.get(k)

        schemas, schemas_op, tables, tables_op = connector._get_filter_values()
        assert schemas == ["public"]
        assert schemas_op == "in"
        assert tables == ["public.users"]
        assert tables_op == "in"

    def test_returns_not_in_operator(self):
        connector = _make_connector()
        schema_filter = MagicMock()
        schema_filter.value = ["pg_catalog", "information_schema"]
        schema_filter.operator_value = "not_in"
        table_filter = MagicMock()
        table_filter.value = ["public.audit_log"]
        table_filter.operator_value = "not_in"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda k: {"schemas": schema_filter, "tables": table_filter}.get(k)

        schemas, schemas_op, tables, tables_op = connector._get_filter_values()
        assert schemas == ["pg_catalog", "information_schema"]
        assert schemas_op == "not_in"
        assert tables == ["public.audit_log"]
        assert tables_op == "not_in"


# ===========================================================================
# PostgreSQLConnector._sync_tables
# ===========================================================================


class TestSyncTables:

    @pytest.mark.asyncio
    async def test_syncs_tables_in_batches(self):
        connector = _make_connector()
        connector.batch_size = 2
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        tables = [
            PostgresTable(name=f"t{i}", schema_name="public") for i in range(5)
        ]
        await connector._sync_tables("public", tables)
        assert connector.data_entities_processor.on_new_records.await_count == 3

    @pytest.mark.asyncio
    async def test_skips_empty_tables_list(self):
        connector = _make_connector()
        await connector._sync_tables("public", [])
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# PostgreSQLConnector._process_tables_generator
# ===========================================================================


class TestProcessTablesGenerator:

    @pytest.mark.asyncio
    async def test_yields_records(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        tables = [PostgresTable(name="users", schema_name="public")]
        records = []
        async for record, perms in connector._process_tables_generator("public", tables):
            records.append((record, perms))

        assert len(records) == 1
        rec = records[0][0]
        assert rec.record_type == RecordType.SQL_TABLE
        assert rec.external_record_id == "public.users"
        assert rec.record_name == "users"
        assert rec.connector_name == Connectors.POSTGRESQL
        assert rec.mime_type == MimeTypes.SQL_TABLE.value
        assert rec.version == 1
        assert rec.record_group_type == RecordGroupType.SQL_NAMESPACE.value

    @pytest.mark.asyncio
    async def test_adds_foreign_key_relations(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        fk = ForeignKeyInfo(
            foreign_table_schema="public",
            foreign_table_name="departments",
            column_name="dept_id",
            foreign_column_name="id",
            constraint_name="fk_dept",
        )
        tables = [PostgresTable(name="users", schema_name="public", foreign_keys=[fk])]
        records = []
        async for record, perms in connector._process_tables_generator("public", tables):
            records.append(record)

        assert len(records) == 1
        assert len(records[0].related_external_records) == 1
        rel = records[0].related_external_records[0]
        assert rel.external_record_id == "public.departments"
        assert rel.relation_type == RecordRelations.FOREIGN_KEY
        assert rel.source_column == "dept_id"
        assert rel.target_column == "id"

    @pytest.mark.asyncio
    async def test_sets_auto_index_off(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        tables = [PostgresTable(name="t1", schema_name="public")]
        records = []
        async for record, perms in connector._process_tables_generator("public", tables):
            records.append(record)

        assert records[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_existing_table_keeps_its_id_and_link(self):
        # A fresh id per sync left the stored link pointing at a record that
        # does not exist, since the save keeps the stored id but the new link.
        connector = _make_connector()
        connector._frontend_url = "https://app.example"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        existing = MagicMock(id="rec-1", external_revision_id="old", source_created_at=1000)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        tables = [PostgresTable(name="t1", schema_name="public")]
        records = [r async for r, _ in connector._process_tables_generator("public", tables)]

        assert records[0].id == "rec-1"
        assert records[0].weburl == "https://app.example/record/rec-1"
        assert records[0].source_created_at == 1000
        # 0 lets the save bump the stored version only on a real change.
        assert records[0].version == 0

    @pytest.mark.asyncio
    async def test_revision_comes_from_table_state(self):
        # A timestamp revision re-indexed every table on every full sync.
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        state = PostgresTableState(column_hash="h", n_tup_ins=5)
        existing = MagicMock(id="rec-1", external_revision_id=state.revision(), source_updated_at=777)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        tables = [PostgresTable(name="t1", schema_name="public", state=state)]
        records = [r async for r, _ in connector._process_tables_generator("public", tables)]

        assert records[0].external_revision_id == state.revision()
        assert records[0].source_updated_at == 777

    def test_revision_ignores_live_row_estimate(self):
        a = PostgresTableState(column_hash="h", n_tup_ins=5, n_live_tup=10)
        b = PostgresTableState(column_hash="h", n_tup_ins=5, n_live_tup=99)
        c = PostgresTableState(column_hash="h", n_tup_ins=6, n_live_tup=10)
        assert a.revision() == b.revision()
        assert a.revision() != c.revision()

    @pytest.mark.asyncio
    async def test_table_in_schema_named_after_database_uses_its_group(self):
        connector = _make_connector()
        connector.database_name = "app"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        tables = [PostgresTable(name="t1", schema_name="app")]
        records = [r async for r, _ in connector._process_tables_generator("app", tables)]

        assert records[0].external_record_group_id == "app.app"
        assert records[0].external_record_id == "app.t1"

    @pytest.mark.asyncio
    async def test_skips_fk_with_no_target_table(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        fk = ForeignKeyInfo(
            foreign_table_schema="public",
            foreign_table_name="",
            column_name="col",
            foreign_column_name="id",
            constraint_name="fk_bad",
        )
        tables = [PostgresTable(name="t1", schema_name="public", foreign_keys=[fk])]
        records = []
        async for record, perms in connector._process_tables_generator("public", tables):
            records.append(record)

        assert len(records[0].related_external_records) == 0

    @pytest.mark.asyncio
    async def test_fk_uses_default_schema_when_not_specified(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        fk = ForeignKeyInfo(
            foreign_table_name="departments",
            column_name="dept_id",
            foreign_column_name="id",
            constraint_name="fk_dept",
        )
        tables = [PostgresTable(name="users", schema_name="myschema", foreign_keys=[fk])]
        records = []
        async for record, perms in connector._process_tables_generator("myschema", tables):
            records.append(record)

        rel = records[0].related_external_records[0]
        assert rel.external_record_id == "myschema.departments"


# ===========================================================================
# PostgreSQLConnector.run_sync
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_runs_full_sync_when_no_stored_state(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            connector.tables_sync_point.read_sync_point = AsyncMock(return_value=None)
            connector._run_full_sync_internal = AsyncMock()

            await connector.run_sync()

            connector._run_full_sync_internal.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_runs_incremental_sync_when_state_exists(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            connector.tables_sync_point.read_sync_point = AsyncMock(
                return_value={"table_states": "{}", "state_version": SYNC_STATE_VERSION}
            )
            connector.run_incremental_sync = AsyncMock()

            await connector.run_sync()

            connector.run_incremental_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_runs_full_sync_when_state_is_from_an_older_version(self):
        # States saved by earlier versions may list tables that were never
        # synced; one full sync repairs what they left behind.
        connector = _make_connector()
        connector.data_source = MagicMock()

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            connector.tables_sync_point.read_sync_point = AsyncMock(
                return_value={"table_states": '{"public.t1": {}}'}
            )
            connector._run_full_sync_internal = AsyncMock()
            connector.run_incremental_sync = AsyncMock()

            await connector.run_sync()

            connector._run_full_sync_internal.assert_awaited_once()
            connector.run_incremental_sync.assert_not_awaited()


# ===========================================================================
# PostgreSQLConnector._run_full_sync_internal
# ===========================================================================


def _full_sync_connector(tables_by_schema, snapshot=None, synced=None):
    """A connector whose full sync runs over mocked listing and saving steps."""
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector.database_name = "testdb"
    connector.indexing_filters = MagicMock()
    connector.indexing_filters.is_enabled.return_value = True

    connector._create_app_users = AsyncMock()
    connector._create_database_record_group = AsyncMock()
    connector._get_current_table_states = AsyncMock(return_value=(snapshot or {}, set()))
    connector._fetch_schemas = AsyncMock(
        return_value=[PostgresSchema(name=n) for n in tables_by_schema]
    )
    connector._list_table_names = AsyncMock(side_effect=lambda schema: tables_by_schema[schema])
    connector._load_tables = AsyncMock(
        side_effect=lambda schema, names, states: [PostgresTable(name=n, schema_name=schema) for n in names]
    )
    connector._sync_schemas = AsyncMock()
    connector._sync_tables = AsyncMock(
        side_effect=lambda schema, tables: {
            f"{schema}.{t.name}" for t in tables
            if synced is None or f"{schema}.{t.name}" in synced
        }
    )
    connector._remove_stale_tables = AsyncMock(return_value={})
    connector._remove_stale_schema_groups = AsyncMock()
    connector._remove_database_group_self_links = AsyncMock()
    connector._save_tables_sync_state = AsyncMock()
    return connector


class TestRunFullSyncInternal:

    @pytest.mark.asyncio
    async def test_full_sync_flow(self):
        snapshot = {"public.t1": PostgresTableState(column_hash="h")}
        connector = _full_sync_connector({"public": ["t1"]}, snapshot)

        await connector._run_full_sync_internal()

        connector._create_app_users.assert_awaited_once()
        connector._create_database_record_group.assert_awaited_once()
        connector._sync_schemas.assert_awaited_once()
        connector._list_table_names.assert_awaited_once_with("public")
        connector._sync_tables.assert_awaited_once()
        connector._remove_stale_tables.assert_awaited_once_with({"public.t1"})
        connector._save_tables_sync_state.assert_awaited_once_with(snapshot)
        assert connector.sync_stats.schemas_synced == 1
        assert connector.sync_stats.tables_new == 1

    @pytest.mark.asyncio
    async def test_full_sync_respects_schema_filter(self):
        connector = _full_sync_connector({"public": [], "app": []})
        schema_filter = MagicMock()
        schema_filter.value = ["app"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda k: {"schemas": schema_filter}.get(k)

        await connector._run_full_sync_internal()

        call_args = connector._sync_schemas.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0].name == "app"

    @pytest.mark.asyncio
    async def test_full_sync_respects_table_filter(self):
        connector = _full_sync_connector({"public": ["t1", "t2"]})
        table_filter = MagicMock()
        table_filter.value = ["public.t1"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda k: {"tables": table_filter}.get(k)

        await connector._run_full_sync_internal()

        synced_tables = connector._sync_tables.call_args[0][1]
        assert [t.name for t in synced_tables] == ["t1"]
        # t2 is filtered out, so its record (if any) is stale.
        connector._remove_stale_tables.assert_awaited_once_with({"public.t1"})

    @pytest.mark.asyncio
    async def test_saved_state_lists_only_synced_tables(self):
        # A table in the saved state is never treated as new again, so one that
        # failed to sync must be left out for the next run to retry.
        snapshot = {
            "public.ok": PostgresTableState(column_hash="a"),
            "public.failed": PostgresTableState(column_hash="b"),
        }
        connector = _full_sync_connector({"public": ["ok", "failed"]}, snapshot, synced={"public.ok"})

        await connector._run_full_sync_internal()

        connector._save_tables_sync_state.assert_awaited_once_with({"public.ok": snapshot["public.ok"]})
        # Failing to sync is not the same as being gone: it stays out of stale removal.
        connector._remove_stale_tables.assert_awaited_once_with({"public.ok", "public.failed"})

    @pytest.mark.asyncio
    async def test_saved_state_keeps_stale_tables_whose_delete_failed(self):
        # Left out of the state, incremental sync would never look at the table
        # again and its record would stay searchable.
        snapshot = {"public.t1": PostgresTableState(column_hash="h")}
        connector = _full_sync_connector({"public": ["t1"]}, snapshot)
        placeholder = PostgresTableState(schema_name="public", table_name="old")
        connector._remove_stale_tables = AsyncMock(return_value={"public.old": placeholder})

        await connector._run_full_sync_internal()

        connector._save_tables_sync_state.assert_awaited_once_with(
            {"public.t1": snapshot["public.t1"], "public.old": placeholder}
        )

    @pytest.mark.asyncio
    async def test_listing_failure_removes_and_saves_nothing(self):
        connector = _full_sync_connector({"public": ["t1"]})
        connector._list_table_names = AsyncMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError):
            await connector._run_full_sync_internal()

        connector._remove_stale_tables.assert_not_awaited()
        connector._save_tables_sync_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_increments_error_count_on_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector._create_app_users = AsyncMock(side_effect=Exception("fail"))

        with pytest.raises(Exception):
            await connector._run_full_sync_internal()

        assert connector.sync_stats.errors == 1


# ===========================================================================
# PostgreSQLConnector._sync_updated_tables
# ===========================================================================


class TestSyncUpdatedTables:

    @pytest.mark.asyncio
    async def test_skips_empty_list(self):
        connector = _make_connector()
        await connector._sync_updated_tables("public", [])
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_record(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        existing_record = MagicMock()
        existing_record.id = "rec-1"
        existing_record.weburl = "http://example.com/rec-1"
        existing_record.source_created_at = 1000
        existing_record.version = 2

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing_record
        )

        table = PostgresTable(name="users", schema_name="public")
        synced = await connector._sync_updated_tables("public", [table])

        assert synced == {"public.users"}
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        updated_rec = connector.data_entities_processor.on_record_content_update.call_args[0][0]
        assert updated_rec.id == "rec-1"
        # The save bumps the stored version when the revision changed.
        assert updated_rec.version == 0
        assert updated_rec.record_group_type == RecordGroupType.SQL_NAMESPACE.value

    @pytest.mark.asyncio
    async def test_creates_record_when_none_exists(self):
        # Skipping it left the table unsearchable for good: it was already in
        # the saved state, so it would never come up as new either.
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=None
        )
        table = PostgresTable(name="users", schema_name="public")

        synced = await connector._sync_updated_tables("public", [table])

        assert synced == {"public.users"}
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        created = connector.data_entities_processor.on_new_records.call_args[0][0][0][0]
        assert created.external_record_id == "public.users"

    @pytest.mark.asyncio
    async def test_continues_on_error(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=[Exception("DB error"), MagicMock(id="r2", weburl="", source_created_at=1, version=1)]
        )
        tables = [
            PostgresTable(name="t1", schema_name="public"),
            PostgresTable(name="t2", schema_name="public"),
        ]
        synced = await connector._sync_updated_tables("public", tables)
        assert connector.data_entities_processor.on_record_content_update.await_count == 1
        assert synced == {"public.t2"}
        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_adds_foreign_keys_to_updated_record(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        existing_record = MagicMock()
        existing_record.id = "rec-1"
        existing_record.weburl = ""
        existing_record.source_created_at = 1000
        existing_record.version = 1

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing_record
        )

        fk = ForeignKeyInfo(
            foreign_table_schema="public",
            foreign_table_name="depts",
            column_name="dept_id",
            foreign_column_name="id",
            constraint_name="fk_dept",
        )
        table = PostgresTable(name="users", schema_name="public", foreign_keys=[fk])
        await connector._sync_updated_tables("public", [table])

        updated_rec = connector.data_entities_processor.on_record_content_update.call_args[0][0]
        assert len(updated_rec.related_external_records) == 1
        assert updated_rec.related_external_records[0].external_record_id == "public.depts"


# ===========================================================================
# PostgreSQLConnector._has_table_changed
# ===========================================================================


class TestHasTableChanged:

    def test_detects_column_hash_change(self):
        connector = _make_connector()
        current = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=0, n_tup_del=0)
        stored = PostgresTableState(column_hash="xyz", n_tup_ins=10, n_tup_upd=0, n_tup_del=0)
        assert connector._has_table_changed(current, stored) is True

    def test_no_change_when_identical(self):
        connector = _make_connector()
        state = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=5, n_tup_del=2)
        assert connector._has_table_changed(state, state.model_copy()) is False

    def test_detects_inserts(self):
        connector = _make_connector()
        current = PostgresTableState(column_hash="abc", n_tup_ins=15, n_tup_upd=0, n_tup_del=0)
        stored = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=0, n_tup_del=0)
        assert connector._has_table_changed(current, stored) is True

    def test_detects_updates(self):
        connector = _make_connector()
        current = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=5, n_tup_del=0)
        stored = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=0, n_tup_del=0)
        assert connector._has_table_changed(current, stored) is True

    def test_detects_deletes(self):
        connector = _make_connector()
        current = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=0, n_tup_del=3)
        stored = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=0, n_tup_del=0)
        assert connector._has_table_changed(current, stored) is True

    def test_detects_stats_reset_inserts_decreased(self):
        connector = _make_connector()
        current = PostgresTableState(column_hash="abc", n_tup_ins=2, n_tup_upd=0, n_tup_del=0)
        stored = PostgresTableState(column_hash="abc", n_tup_ins=100, n_tup_upd=0, n_tup_del=0)
        assert connector._has_table_changed(current, stored) is True

    def test_detects_stats_reset_updates_decreased(self):
        connector = _make_connector()
        current = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=1, n_tup_del=0)
        stored = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=50, n_tup_del=0)
        assert connector._has_table_changed(current, stored) is True

    def test_detects_stats_reset_deletes_decreased(self):
        connector = _make_connector()
        current = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=0, n_tup_del=0)
        stored = PostgresTableState(column_hash="abc", n_tup_ins=10, n_tup_upd=0, n_tup_del=30)
        assert connector._has_table_changed(current, stored) is True

    def test_handles_none_values(self):
        connector = _make_connector()
        current = PostgresTableState(column_hash="abc")
        stored = PostgresTableState(column_hash="abc")
        assert connector._has_table_changed(current, stored) is False


# ===========================================================================
# PostgreSQLConnector._compute_column_hash
# ===========================================================================


class TestColumnHash:

    def test_same_columns_produce_same_hash(self):
        cols = [ColumnInfo(name="id", data_type="integer"), ColumnInfo(name="name", data_type="text")]
        assert _column_hash(cols) == _column_hash([c.model_copy() for c in cols])
        assert len(_column_hash(cols)) == 32

    def test_different_columns_produce_different_hash(self):
        assert _column_hash([ColumnInfo(name="id")]) != _column_hash(
            [ColumnInfo(name="id"), ColumnInfo(name="extra")]
        )


# ===========================================================================
# PostgreSQLConnector.run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_incremental_sync()

    @pytest.mark.asyncio
    async def test_falls_back_to_full_sync_when_no_state(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            connector.tables_sync_point.read_sync_point = AsyncMock(return_value=None)
            connector._run_full_sync_internal = AsyncMock()
            connector._save_tables_sync_state = AsyncMock()

            await connector.run_incremental_sync()

            connector._run_full_sync_internal.assert_awaited_once()

    @staticmethod
    def _incremental_connector(stored, current, unreadable=frozenset()):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value={
            "state_version": SYNC_STATE_VERSION,
            "table_states": json.dumps({k: v.model_dump() for k, v in stored.items()}),
        })
        connector._get_current_table_states = AsyncMock(return_value=(current, set(unreadable)))
        # Nothing is listed unless a test says otherwise: stats-missing tables are gone.
        connector._list_table_names = AsyncMock(return_value=[])
        connector._sync_new_tables = AsyncMock(side_effect=lambda fqns, states: set(fqns))
        connector._sync_changed_tables = AsyncMock(side_effect=lambda fqns, states: set(fqns))
        connector._handle_deleted_tables = AsyncMock(side_effect=lambda fqns: set(fqns))
        connector._remove_stale_schema_groups = AsyncMock()
        connector._save_tables_sync_state = AsyncMock()
        return connector

    @staticmethod
    async def _run(connector):
        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await connector.run_incremental_sync()

    @pytest.mark.asyncio
    async def test_detects_new_changed_deleted_tables(self):
        stored = {
            "public.existing": PostgresTableState(column_hash="abc", n_tup_ins=10),
            "public.deleted": PostgresTableState(column_hash="def", n_tup_ins=5),
        }
        current = {
            "public.existing": PostgresTableState(column_hash="xyz", n_tup_ins=10),
            "public.new_table": PostgresTableState(column_hash="ghi"),
        }
        connector = self._incremental_connector(stored, current)

        await self._run(connector)

        assert connector._sync_new_tables.call_args[0][0] == ["public.new_table"]
        assert connector._sync_changed_tables.call_args[0][0] == ["public.existing"]
        assert connector._handle_deleted_tables.call_args[0][0] == ["public.deleted"]
        connector._save_tables_sync_state.assert_awaited_once_with(current)

    @pytest.mark.asyncio
    async def test_stats_failure_deletes_nothing(self):
        # Returning {} here used to mark every stored table as dropped.
        stored = {"public.t1": PostgresTableState(column_hash="a")}
        connector = self._incremental_connector(stored, {})
        connector._get_current_table_states = AsyncMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError):
            await self._run(connector)

        connector._handle_deleted_tables.assert_not_awaited()
        connector._save_tables_sync_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unreadable_table_is_neither_dropped_nor_new(self):
        stored = {"public.t1": PostgresTableState(column_hash="a")}
        connector = self._incremental_connector(stored, {}, unreadable={"public.t1"})

        await self._run(connector)

        assert connector._handle_deleted_tables.call_args[0][0] == []
        assert connector._sync_new_tables.call_args[0][0] == []
        connector._save_tables_sync_state.assert_awaited_once_with(stored)

    @pytest.mark.asyncio
    async def test_saved_state_retries_what_failed(self):
        stored = {
            "public.changed_ok": PostgresTableState(column_hash="a", n_tup_ins=1),
            "public.changed_bad": PostgresTableState(column_hash="b", n_tup_ins=1),
            "public.dropped_bad": PostgresTableState(column_hash="c"),
        }
        current = {
            "public.changed_ok": PostgresTableState(column_hash="a", n_tup_ins=2),
            "public.changed_bad": PostgresTableState(column_hash="b", n_tup_ins=2),
            "public.new_ok": PostgresTableState(column_hash="d"),
            "public.new_bad": PostgresTableState(column_hash="e"),
        }
        connector = self._incremental_connector(stored, current)
        connector._sync_new_tables = AsyncMock(return_value={"public.new_ok"})
        connector._sync_changed_tables = AsyncMock(return_value={"public.changed_ok"})
        connector._handle_deleted_tables = AsyncMock(return_value=set())

        await self._run(connector)

        saved = connector._save_tables_sync_state.call_args[0][0]
        assert saved == {
            # Synced: the snapshot this run compared against.
            "public.changed_ok": current["public.changed_ok"],
            "public.new_ok": current["public.new_ok"],
            # Failed: the old state, so the next run sees the change again.
            "public.changed_bad": stored["public.changed_bad"],
            "public.dropped_bad": stored["public.dropped_bad"],
            # "public.new_bad" is left out, so the next run finds it new again.
        }

    @pytest.mark.asyncio
    async def test_empty_stats_do_not_delete_tables_that_are_still_listed(self):
        # A successful but empty (or partial) stats answer must not read as
        # "every table dropped": the listing a full sync uses has to agree.
        stored = {
            "public.users": PostgresTableState(column_hash="a", schema_name="public", table_name="users"),
            "public.gone": PostgresTableState(column_hash="b", schema_name="public", table_name="gone"),
        }
        connector = self._incremental_connector(stored, {})
        connector._list_table_names = AsyncMock(return_value=["users"])

        await self._run(connector)

        connector._list_table_names.assert_awaited_once_with("public")
        assert connector._handle_deleted_tables.call_args[0][0] == ["public.gone"]
        connector._save_tables_sync_state.assert_awaited_once_with({"public.users": stored["public.users"]})

    @pytest.mark.asyncio
    async def test_table_excluded_by_filters_is_dropped_without_listing(self):
        stored = {"secret.pay": PostgresTableState(column_hash="a", schema_name="secret", table_name="pay")}
        connector = self._incremental_connector(stored, {})
        connector._get_filter_values = MagicMock(return_value=(["secret"], "not_in", None, "in"))

        await self._run(connector)

        connector._list_table_names.assert_not_awaited()
        assert connector._handle_deleted_tables.call_args[0][0] == ["secret.pay"]

    @pytest.mark.asyncio
    async def test_listing_failure_while_confirming_deletes_nothing(self):
        stored = {"public.t1": PostgresTableState(column_hash="a", schema_name="public", table_name="t1")}
        connector = self._incremental_connector(stored, {})
        connector._list_table_names = AsyncMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError):
            await self._run(connector)

        connector._handle_deleted_tables.assert_not_awaited()
        connector._save_tables_sync_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_state_from_older_version_runs_full_sync(self):
        connector = self._incremental_connector({}, {})
        connector.tables_sync_point.read_sync_point = AsyncMock(
            return_value={"table_states": "{}"}
        )
        connector._run_full_sync_internal = AsyncMock()

        await self._run(connector)

        connector._run_full_sync_internal.assert_awaited_once()
        connector._get_current_table_states.assert_not_awaited()


# ===========================================================================
# PostgreSQLConnector._handle_deleted_tables
# ===========================================================================


class TestHandleDeletedTables:

    @pytest.mark.asyncio
    async def test_deletes_record(self):
        connector = _make_connector()
        mock_record = MagicMock()
        mock_record.id = "rec-1"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=mock_record
        )
        await connector._handle_deleted_tables(["public.t1"])
        connector.data_entities_processor.on_record_deleted.assert_awaited_once_with("rec-1")

    @pytest.mark.asyncio
    async def test_skips_when_no_record_found(self):
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=None
        )
        await connector._handle_deleted_tables(["public.t1"])
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_on_error(self):
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=[Exception("error"), MagicMock(id="r2")]
        )
        handled = await connector._handle_deleted_tables(["public.t1", "public.t2"])
        assert connector.data_entities_processor.on_record_deleted.await_count == 1
        assert handled == {"public.t2"}


# ===========================================================================
# PostgreSQLConnector.stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_raises_when_data_source_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_table_info_failure_does_not_stream_empty_table(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "public.users"
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(False, None, error="Table not found")
        )

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_row_read_failure_does_not_stream_empty_rows(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.connector_name = Connectors.POSTGRESQL

        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "public.users"

        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(return_value=_pg_response(True, []))
        connector.data_source.get_primary_keys = AsyncMock(return_value=_pg_response(True, []))

        class InsufficientPrivilegeError(Exception):
            pass

        connector.data_source.fetch_table_rows = AsyncMock(
            side_effect=InsufficientPrivilegeError("permission denied for table users")
        )

        from fastapi import HTTPException
        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_sync = MagicMock()
            mock_sync.get_value.return_value = 100
            mock_load.return_value = (mock_sync, MagicMock())

            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_constraint_failure_does_not_stream_hollow_table(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "public.users"

        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_pg_response(
                False, None, error="permission denied for table table_constraints"
            )
        )

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_for_unsupported_record_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.FILE

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_for_invalid_fqn(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "invalid_no_dot"

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_streams_sql_table(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.connector_name = Connectors.POSTGRESQL

        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "public.users"

        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_pg_response(True, [])
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_pg_response(True, [])
        )
        connector.data_source.fetch_table_rows = AsyncMock(return_value=[{"id": 1}])
        connector.data_source.get_table_ddl = AsyncMock(
            return_value=_pg_response(True, {"ddl": "CREATE TABLE users (...)"})
        )

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_sync = MagicMock()
            mock_sync.get_value.return_value = 100
            mock_load.return_value = (mock_sync, MagicMock())

            result = await connector.stream_record(record)
            assert result is not None


# ===========================================================================
# PostgreSQLConnector.cleanup
# ===========================================================================


class TestCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        connector.data_source = MagicMock()
        connector.data_source.get_client.return_value = mock_client
        connector.database_name = "testdb"

        await connector.cleanup()

        mock_client.close.assert_awaited_once()
        assert connector.data_source is None
        assert connector.database_name is None

    @pytest.mark.asyncio
    async def test_cleanup_when_no_data_source(self):
        connector = _make_connector()
        connector.data_source = None
        await connector.cleanup()
        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_handles_error(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_client.side_effect = Exception("Close error")
        await connector.cleanup()


# ===========================================================================
# PostgreSQLConnector.reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_reindex_empty_list(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        await connector.reindex_records([])
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reindex_publishes_events(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        records = [MagicMock(), MagicMock()]
        await connector.reindex_records(records)
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)

    @pytest.mark.asyncio
    async def test_reindex_raises_when_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_reindex_propagates_error(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=Exception("Reindex error")
        )
        with pytest.raises(Exception, match="Reindex error"):
            await connector.reindex_records([MagicMock()])


# ===========================================================================
# PostgreSQLConnector.get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_unknown_filter_key(self):
        connector = _make_connector()
        result = await connector.get_filter_options("unknown_key")
        assert result.success is False
        assert "Unknown filter key" in result.message

    @pytest.mark.asyncio
    async def test_returns_schemas(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(
            return_value=_pg_response(True, [
                {"name": "public"},
                {"name": "app"},
            ])
        )
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(True, [{"name": "users"}])
        )

        result = await connector.get_filter_options("schemas")
        assert result.success is True
        assert len(result.options) == 2
        assert result.options[0].label == "public"

    @pytest.mark.asyncio
    async def test_returns_tables(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(
            return_value=_pg_response(True, [{"name": "public"}])
        )
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(True, [
                {"name": "users"},
                {"name": "orders"},
            ])
        )

        result = await connector.get_filter_options("tables")
        assert result.success is True
        assert len(result.options) == 2
        assert result.options[0].label == "public.users"

    @pytest.mark.asyncio
    async def test_pagination(self):
        connector = _make_connector()
        connector._schema_filter_cache = [
            FilterOption(id=f"s{i}", label=f"s{i}") for i in range(25)
        ]
        connector._table_filter_cache = [
            FilterOption(id=f"s.t{i}", label=f"s.t{i}") for i in range(25)
        ]

        result = await connector.get_filter_options("schemas", page=1, limit=10, search="s")
        assert result.success is True
        assert len(result.options) == 10
        assert result.has_more is True
        assert result.cursor is not None

    @pytest.mark.asyncio
    async def test_search_filter_schemas(self):
        connector = _make_connector()
        connector._schema_filter_cache = [
            FilterOption(id="public", label="public"),
            FilterOption(id="app", label="app"),
            FilterOption(id="private", label="private"),
        ]
        connector._table_filter_cache = []

        result = await connector.get_filter_options("schemas", search="pub")
        assert result.success is True
        assert len(result.options) == 1
        assert result.options[0].label == "public"

    @pytest.mark.asyncio
    async def test_search_filter_tables(self):
        connector = _make_connector()
        connector._schema_filter_cache = [FilterOption(id="public", label="public")]
        connector._table_filter_cache = [
            FilterOption(id="public.users", label="public.users"),
            FilterOption(id="public.orders", label="public.orders"),
            FilterOption(id="public.products", label="public.products"),
        ]

        result = await connector.get_filter_options("tables", search="order")
        assert result.success is True
        assert len(result.options) == 1
        assert result.options[0].label == "public.orders"

    @pytest.mark.asyncio
    async def test_cursor_pagination(self):
        connector = _make_connector()
        connector._schema_filter_cache = [FilterOption(id="s", label="s")]
        connector._table_filter_cache = [
            FilterOption(id=f"s.t{i}", label=f"s.t{i}") for i in range(5)
        ]

        result = await connector.get_filter_options("tables", limit=2, cursor="2", search="s")
        assert result.success is True
        assert len(result.options) == 2
        assert result.options[0].id == "s.t2"

    @pytest.mark.asyncio
    async def test_handles_error(self):
        connector = _make_connector()
        connector.data_source = None

        result = await connector.get_filter_options("tables")
        assert result.success is False


# ===========================================================================
# PostgreSQLConnector._populate_filter_cache
# ===========================================================================


class TestPopulateFilterCache:

    @pytest.mark.asyncio
    async def test_populates_cache(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(
            return_value=_pg_response(True, [{"name": "public"}, {"name": "app"}])
        )
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(True, [{"name": "users"}])
        )

        await connector._populate_filter_cache()
        assert len(connector._schema_filter_cache) == 2
        assert len(connector._table_filter_cache) == 2
        assert connector._schema_filter_cache[0].id == "public"
        assert connector._table_filter_cache[0].id == "public.users"

    @pytest.mark.asyncio
    async def test_raises_when_data_source_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(RuntimeError):
            await connector._populate_filter_cache()

    @pytest.mark.asyncio
    async def test_raises_when_list_schemas_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(
            return_value=_pg_response(False, error="DB error")
        )
        with pytest.raises(RuntimeError):
            await connector._populate_filter_cache()


# ===========================================================================
# PostgreSQLConnector._get_current_table_states
# ===========================================================================


def _stat(schema, table, **counters):
    return {"schema_name": schema, "table_name": table, "n_live_tup": 0,
            "n_tup_ins": 0, "n_tup_upd": 0, "n_tup_del": 0, **counters}


def _states_connector(stats, columns=None, standby=False):
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector.data_source.get_table_stats = AsyncMock(return_value=_pg_response(True, stats))
    connector.data_source.get_columns = AsyncMock(return_value=_pg_response(True, columns or []))
    connector.data_source.is_in_recovery = AsyncMock(
        return_value=_pg_response(True, {"in_recovery": standby})
    )
    connector.data_source.get_primary_keys_by_table = AsyncMock(return_value=_pg_response(True, []))
    connector.data_source.get_sample_hash = AsyncMock(
        return_value=_pg_response(True, {"sample_hash": "s" * 32})
    )
    return connector


class TestGetCurrentTableStates:

    @pytest.mark.asyncio
    async def test_returns_states(self):
        connector = _states_connector(
            [_stat("public", "users", n_live_tup=100, n_tup_ins=50, n_tup_upd=10, n_tup_del=2)],
            [{"schema_name": "public", "table_name": "users", "name": "id"}],
        )

        states, unreadable = await connector._get_current_table_states(None)

        assert unreadable == set()
        state = states["public.users"]
        assert (state.n_live_tup, state.n_tup_ins, state.n_tup_upd, state.n_tup_del) == (100, 50, 10, 2)
        assert (state.schema_name, state.table_name) == ("public", "users")
        assert state.column_hash == _column_hash([ColumnInfo(name="id")])
        assert state.sample_hash == ""
        connector.data_source.get_sample_hash.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reads_all_columns_in_one_query(self):
        # One round trip for the whole catalog, not one per table.
        connector = _states_connector(
            [_stat("public", "a"), _stat("public", "b")],
            [
                {"schema_name": "public", "table_name": "a", "name": "id"},
                {"schema_name": "public", "table_name": "b", "name": "id"},
                {"schema_name": "public", "table_name": "b", "name": "extra"},
            ],
        )

        states, _ = await connector._get_current_table_states(["public"])

        connector.data_source.get_columns.assert_awaited_once_with(["public"])
        assert states["public.a"].column_hash != states["public.b"].column_hash

    @pytest.mark.asyncio
    async def test_raises_on_stats_failure(self):
        connector = _states_connector([])
        connector.data_source.get_table_stats = AsyncMock(return_value=_pg_response(False, error="Error"))
        with pytest.raises(RuntimeError):
            await connector._get_current_table_states(None)

    @pytest.mark.asyncio
    async def test_raises_on_columns_failure(self):
        connector = _states_connector([_stat("public", "t")])
        connector.data_source.get_columns = AsyncMock(return_value=_pg_response(False, error="timeout"))
        with pytest.raises(RuntimeError, match="timeout"):
            await connector._get_current_table_states(None)

    @pytest.mark.asyncio
    async def test_standby_hashes_each_sample_in_key_order(self):
        # Replayed writes leave a standby's counters still; without the sample
        # hash no change would ever be seen there.
        connector = _states_connector(
            [_stat("public", "t")],
            [{"schema_name": "public", "table_name": "t", "name": "id"}],
            standby=True,
        )
        connector.data_source.get_primary_keys_by_table = AsyncMock(return_value=_pg_response(True, [
            {"schema_name": "public", "table_name": "t", "column_name": "a"},
            {"schema_name": "public", "table_name": "t", "column_name": "b"},
        ]))

        states, _ = await connector._get_current_table_states(None)

        assert states["public.t"].sample_hash == "s" * 32
        connector.data_source.get_sample_hash.assert_awaited_once_with(
            "public", "t", limit=1000, order_by=["a", "b"], max_bytes=MAX_TABLE_DOCUMENT_BYTES
        )

    @pytest.mark.asyncio
    async def test_standby_table_without_a_key_is_still_hashed_by_the_data_source(self):
        connector = _states_connector([_stat("public", "t")], standby=True)
        connector.data_source.get_primary_keys_by_table = AsyncMock(return_value=_pg_response(True, []))

        states, _ = await connector._get_current_table_states(None)

        assert states["public.t"].sample_hash == "s" * 32
        connector.data_source.get_sample_hash.assert_awaited_once_with(
            "public", "t", limit=1000, order_by=None, max_bytes=MAX_TABLE_DOCUMENT_BYTES
        )

    @pytest.mark.asyncio
    async def test_standby_table_that_cannot_be_sampled_is_unreadable(self):
        connector = _states_connector([_stat("public", "t")], standby=True)
        connector.data_source.get_sample_hash = AsyncMock(return_value=_pg_response(False, error="denied"))

        states, unreadable = await connector._get_current_table_states(None)

        assert states == {}
        assert unreadable == {"public.t"}

    def test_sample_hash_changes_revision_and_detection(self):
        connector = _make_connector()
        a = PostgresTableState(column_hash="h", sample_hash="x")
        b = PostgresTableState(column_hash="h", sample_hash="y")
        assert a.revision() != b.revision()
        assert connector._has_table_changed(b, a) is True
        # A primary's state has no sample hash; its revision is unchanged by the field.
        plain = PostgresTableState(column_hash="h")
        assert plain.revision() == hashlib.md5(b"h:0:0:0").hexdigest()

    @pytest.mark.asyncio
    async def test_schema_with_a_dot_keeps_its_name(self):
        connector = _states_connector([_stat("my.schema", "t")])

        states, _ = await connector._get_current_table_states(None)

        assert states['"my.schema".t'].schema_name == "my.schema"
        assert states['"my.schema".t'].table_name == "t"

    @pytest.mark.asyncio
    async def test_respects_selected_tables_filter(self):
        connector = _states_connector([_stat("public", "users"), _stat("public", "orders")])

        states, _ = await connector._get_current_table_states(None, selected_tables=["public.users"])

        assert set(states) == {"public.users"}

    @pytest.mark.asyncio
    async def test_tables_not_in_filter_excludes_matches(self):
        connector = _states_connector([_stat("public", "users"), _stat("public", "orders")])

        states, _ = await connector._get_current_table_states(
            None, selected_tables=["public.orders"], tables_op="not_in"
        )

        assert set(states) == {"public.users"}

    @pytest.mark.asyncio
    async def test_schemas_not_in_fetches_all_then_excludes_client_side(self):
        """NOT_IN cannot be pushed down through get_table_stats(schema_list); we must
        fetch all and filter client-side. Verifies the scope=None decision path."""
        connector = _states_connector([_stat("public", "users"), _stat("pg_catalog", "pg_class")])

        states, _ = await connector._get_current_table_states(["pg_catalog"], schemas_op="not_in")

        connector.data_source.get_table_stats.assert_awaited_once_with(None)
        assert set(states) == {"public.users"}


# ===========================================================================
# PostgreSQLConnector._save_tables_sync_state
# ===========================================================================


class TestSaveTablesSyncState:

    @pytest.mark.asyncio
    async def test_saves_state(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = None

        connector.data_source.get_table_stats = AsyncMock()
        connector.tables_sync_point.update_sync_point = AsyncMock()
        states = {"public.t1": PostgresTableState(column_hash="h", n_tup_ins=3)}

        await connector._save_tables_sync_state(states)

        # Saves what it is given; re-reading the database here recorded tables
        # the sync never handled.
        connector.data_source.get_table_stats.assert_not_awaited()
        key, payload = connector.tables_sync_point.update_sync_point.call_args[0]
        assert key == SYNC_STATE_KEY
        assert payload["state_version"] == SYNC_STATE_VERSION
        assert json.loads(payload["table_states"]) == {"public.t1": states["public.t1"].model_dump()}


# ===========================================================================
# PostgreSQLConnector._sync_new_tables
# ===========================================================================


class TestSyncNewTables:

    @pytest.mark.asyncio
    async def test_syncs_new_tables_and_creates_schema_groups(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_pg_response(True, [])
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_pg_response(True, [])
        )
        connector._sync_schemas = AsyncMock()
        connector._sync_tables = AsyncMock(return_value={"public.new_table"})
        states = {"public.new_table": PostgresTableState(schema_name="public", table_name="new_table")}

        synced = await connector._sync_new_tables(["public.new_table"], states)

        assert synced == {"public.new_table"}
        connector._sync_schemas.assert_awaited_once()
        connector._sync_tables.assert_awaited_once()
        assert connector.sync_stats.tables_new == 1

    @pytest.mark.asyncio
    async def test_unreadable_new_table_is_not_created(self):
        # Creating it without columns would index an empty table.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(False, error="Table not found")
        )
        connector.data_source.get_foreign_keys = AsyncMock(return_value=_pg_response(True, []))
        connector.data_source.get_primary_keys = AsyncMock(return_value=_pg_response(True, []))
        connector._sync_schemas = AsyncMock()
        connector._sync_tables = AsyncMock()
        states = {"public.mv": PostgresTableState(schema_name="public", table_name="mv")}

        synced = await connector._sync_new_tables(["public.mv"], states)

        assert synced == set()
        connector._sync_tables.assert_not_awaited()
        assert connector.sync_stats.errors == 1


# ===========================================================================
# PostgreSQLConnector._sync_changed_tables
# ===========================================================================


class TestSyncChangedTables:

    @pytest.mark.asyncio
    async def test_syncs_changed_tables(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": []})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_pg_response(True, [])
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_pg_response(True, [])
        )
        connector._sync_updated_tables = AsyncMock(return_value={"public.users"})
        states = {"public.users": PostgresTableState(schema_name="public", table_name="users")}

        synced = await connector._sync_changed_tables(["public.users"], states)

        assert synced == {"public.users"}
        connector._sync_updated_tables.assert_awaited_once()
        table = connector._sync_updated_tables.call_args[0][1][0]
        assert table.state is states["public.users"]


# ===========================================================================
# PostgreSQLConnector._remove_stale_tables
# ===========================================================================


class TestRemoveStaleTables:

    @pytest.mark.asyncio
    async def test_deletes_records_of_tables_no_longer_synced(self):
        connector = _make_connector()
        connector.data_entities_processor.get_records_by_record_type = AsyncMock(return_value=[
            MagicMock(id="keep", external_record_id="public.users"),
            MagicMock(id="dropped", external_record_id="public.old"),
            MagicMock(id="excluded", external_record_id="secret.salaries"),
        ])

        await connector._remove_stale_tables({"public.users"})

        connector.data_entities_processor.get_records_by_record_type.assert_awaited_once_with(
            "conn-pg-1", RecordType.SQL_TABLE
        )
        deleted = [c.args[0] for c in connector.data_entities_processor.on_record_deleted.await_args_list]
        assert deleted == ["dropped", "excluded"]

    @pytest.mark.asyncio
    async def test_continues_after_a_failed_delete(self):
        connector = _make_connector()
        connector.data_entities_processor.get_records_by_record_type = AsyncMock(return_value=[
            MagicMock(id="a", external_record_id="public.a", external_record_group_id="public"),
            MagicMock(id="b", external_record_id="public.b", external_record_group_id="public"),
        ])
        connector.data_entities_processor.on_record_deleted = AsyncMock(
            side_effect=[Exception("db"), None]
        )

        undeleted = await connector._remove_stale_tables(set())

        assert connector.data_entities_processor.on_record_deleted.await_count == 2
        assert connector.sync_stats.errors == 1
        assert undeleted == {"public.a": PostgresTableState(schema_name="public", table_name="a")}


# ===========================================================================
# PostgreSQLConnector._split_table_fqn
# ===========================================================================


class TestSplitTableFqn:

    def _record(self, fqn, group_id):
        return MagicMock(external_record_id=fqn, external_record_group_id=group_id)

    def test_plain_names(self):
        connector = _make_connector()
        connector.database_name = "db"
        assert connector._split_table_fqn(self._record("public.users", "public")) == ("public", "users")

    def test_schema_with_a_dot(self):
        connector = _make_connector()
        connector.database_name = "db"
        assert connector._split_table_fqn(self._record("my.schema.t", "my.schema")) == ("my.schema", "t")

    def test_schema_named_after_database(self):
        connector = _make_connector()
        connector.database_name = "app"
        assert connector._split_table_fqn(self._record("app.t", "app.app")) == ("app", "t")

    def test_falls_back_to_first_dot_without_group(self):
        connector = _make_connector()
        connector.database_name = "db"
        assert connector._split_table_fqn(self._record("public.users", None)) == ("public", "users")


# ===========================================================================
# PostgreSQLConnector.create_connector (factory method)
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_creates_connector(self):
        logger = logging.getLogger("test")
        dsp = MagicMock()
        cs = MagicMock()

        processor = MagicMock()
        processor.org_id = "org-1"

        connector = await PostgreSQLConnector.create_connector(
            logger=logger,
            data_store_provider=dsp,
            config_service=cs,
            connector_id="conn-test",
            data_entities_processor=processor,
        )

        assert isinstance(connector, PostgreSQLConnector)
        assert connector.connector_id == "conn-test"


# ===========================================================================
# MAX_ROWS_PER_TABLE_LIMIT constant
# ===========================================================================


class TestMaxRowsPerTableLimit:

    def test_value(self):
        assert MAX_ROWS_PER_TABLE_LIMIT == 10000


class TestFilterCacheRefresh:

    @pytest.mark.asyncio
    async def test_later_pages_read_the_cache(self):
        # Every page used to relist every schema and table.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(
            return_value=_pg_response(True, [{"name": f"s{i}"} for i in range(30)])
        )
        connector.data_source.list_tables = AsyncMock(return_value=_pg_response(True, []))

        first = await connector.get_filter_options("schemas", page=1, limit=20)
        second = await connector.get_filter_options("schemas", page=2, limit=20, cursor=first.cursor)

        assert connector.data_source.list_schemas.await_count == 1
        assert [o.id for o in second.options] == [f"s{i}" for i in range(20, 30)]

    @pytest.mark.asyncio
    async def test_reopening_the_list_refreshes_it(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(return_value=_pg_response(True, [{"name": "a"}]))
        connector.data_source.list_tables = AsyncMock(return_value=_pg_response(True, []))

        await connector.get_filter_options("schemas")
        await connector.get_filter_options("schemas")

        assert connector.data_source.list_schemas.await_count == 2


class TestStreamRecordRowLimit:

    @pytest.mark.asyncio
    async def test_non_positive_max_rows_reads_at_least_one_row(self):
        # A negative LIMIT is a Postgres error, which failed every download.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        record = MagicMock(record_type=RecordType.SQL_TABLE, external_record_id="public.users",
                           external_record_group_id="public")
        _mock_table_metadata(connector)
        connector.data_source.fetch_table_rows = AsyncMock(return_value=[])
        connector.data_source.get_table_ddl = AsyncMock(return_value=_pg_response(True, {"ddl": ""}))

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            sync_filters = MagicMock()
            sync_filters.get_value.return_value = -5
            mock_load.return_value = (sync_filters, MagicMock())
            await connector.stream_record(record)

        connector.data_source.fetch_table_rows.assert_awaited_once_with(
            "public", "users", limit=1, order_by=["id"], max_bytes=MAX_TABLE_DOCUMENT_BYTES
        )


class TestStreamRecordRows:

    @staticmethod
    async def _stream(connector, record):
        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            sync_filters = MagicMock()
            sync_filters.get_value.return_value = 100
            mock_load.return_value = (sync_filters, MagicMock())
            response = await connector.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        return json.loads(body)

    @pytest.mark.asyncio
    async def test_rows_are_ordered_by_primary_key_and_json_safe(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        record = MagicMock(record_type=RecordType.SQL_TABLE, external_record_id="public.docs",
                           external_record_group_id="public")
        _mock_table_metadata(
            connector,
            info=_pg_response(True, {"columns": [
                {"name": "id", "data_type": "integer"},
                {"name": "doc", "data_type": "jsonb"},
                {"name": "blob", "data_type": "bytea"},
            ]}),
        )
        connector.data_source.fetch_table_rows = AsyncMock(return_value=[
            {"id": 1, "doc": '{"k": [1, 2]}', "blob": b"\x89PNG"},
        ])
        connector.data_source.get_table_ddl = AsyncMock(return_value=_pg_response(True, {"ddl": ""}))

        data = await self._stream(connector, record)

        connector.data_source.fetch_table_rows.assert_awaited_once_with(
            "public", "docs", limit=100, order_by=["id"], max_bytes=MAX_TABLE_DOCUMENT_BYTES
        )
        # json arrives from asyncpg as text; left alone it was encoded twice.
        assert data["rows"][0]["doc"] == {"k": [1, 2]}
        # bytea in Postgres's hex form, not a Python bytes repr.
        assert data["rows"][0]["blob"] == "\\x89504e47"


class TestJsonDefault:

    def test_range(self):
        from app.connectors.sources.postgres.connector import _json_default

        rng = MagicMock(lower=1, upper=5, lower_inc=True, upper_inc=False, isempty=False)
        assert _json_default(rng) == {
            "lower": 1, "upper": 5, "lower_inc": True, "upper_inc": False, "empty": False,
        }

    def test_composite_record(self):
        from app.connectors.sources.postgres.connector import _json_default

        class FakeRecord:
            def items(self):
                return [("street", "Main"), ("no", 5)]

        assert _json_default(FakeRecord()) == {"street": "Main", "no": 5}

    def test_other_values_fall_back_to_str(self):
        from decimal import Decimal

        from app.connectors.sources.postgres.connector import _json_default

        assert _json_default(Decimal("1.50")) == "1.50"


class TestTableIds:

    def test_plain_names_are_unchanged(self):
        # Existing ids must not move: every ordinary table keeps its id.
        assert table_fqn("public", "users") == "public.users"
        assert table_fqn("s", 'we"ird') == 's.we"ird'

    def test_names_with_dots_no_longer_collide(self):
        a = table_fqn("a.b", "c")
        b = table_fqn("a", "b.c")
        assert a == '"a.b".c'
        assert b == 'a."b.c"'
        assert a != b

    @pytest.mark.parametrize("schema,table", [
        ("public", "users"), ("a.b", "c"), ("a", "b.c"), ('"q', "t"), ("s", '"t"'), ('x"."y', "z.z"),
    ])
    def test_round_trip(self, schema, table):
        assert split_table_fqn(table_fqn(schema, table)) == (schema, table)

    def test_rejects_what_it_did_not_write(self):
        with pytest.raises(ValueError):
            split_table_fqn("my.schema.t")
        with pytest.raises(ValueError):
            split_table_fqn('"unterminated.t')

    def test_legacy_dotted_id_is_split_by_its_group(self):
        connector = _make_connector()
        connector.database_name = "db"
        record = MagicMock(external_record_id="my.schema.t", external_record_group_id="my.schema")
        assert connector._split_table_fqn(record) == ("my.schema", "t")


class _FakeTx:
    def __init__(self, groups=None, group=None):
        self.groups = groups or []
        self.group = group
        self.deleted_edges = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_nodes_by_filters(self, collection, filters):
        self.filters = (collection, filters)
        return self.groups

    async def get_record_group_by_external_id(self, connector_id, external_id):
        return self.group

    async def delete_edge(self, from_id, from_collection, to_id, to_collection, collection):
        self.deleted_edges.append((from_id, to_id, collection))


class TestSchemaGroupCleanup:

    @pytest.mark.asyncio
    async def test_groups_of_unsynced_schemas_are_deleted(self):
        connector = _make_connector()
        connector.database_name = "app"
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=True)
        tx = _FakeTx(groups=[
            {"externalGroupId": "public"},
            {"externalGroupId": "excluded"},
            {"externalGroupId": "app.app"},
        ])
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_stale_schema_groups(synced_schemas=["public", "app"])

        assert tx.filters == ("recordGroups", {"connectorId": "conn-pg-1", "groupType": "SQL_NAMESPACE"})
        connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            "excluded", "conn-pg-1"
        )

    @pytest.mark.asyncio
    async def test_the_database_group_is_kept_even_when_saved_as_a_schema_group(self):
        # Before schema groups got distinct ids, a schema named after its database
        # was upserted onto the database group, leaving it typed SQL_NAMESPACE.
        connector = _make_connector()
        connector.database_name = "app"
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=True)
        tx = _FakeTx(groups=[
            {"externalGroupId": "app"},
            {"externalGroupId": "app.app"},
            {"externalGroupId": "leftover"},
        ])
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_stale_schema_groups(synced_schemas=["app"])

        connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            "leftover", "conn-pg-1"
        )

    @pytest.mark.asyncio
    async def test_failure_is_counted_not_raised(self):
        connector = _make_connector()
        connector.data_store_provider.transaction = MagicMock(side_effect=Exception("graph down"))

        await connector._remove_stale_schema_groups(synced_schemas=["public"])

        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_self_links_left_by_the_name_clash_are_removed(self):
        connector = _make_connector()
        connector.database_name = "app"
        tx = _FakeTx(group=MagicMock(id="g1"))
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_database_group_self_links()

        assert tx.deleted_edges == [("g1", "g1", "belongsTo"), ("g1", "g1", "inheritPermissions")]

    @pytest.mark.asyncio
    async def test_full_sync_repairs_only_when_a_schema_is_named_after_the_database(self):
        connector = _full_sync_connector({"public": []})
        await connector._run_full_sync_internal()
        connector._remove_database_group_self_links.assert_not_awaited()

        connector = _full_sync_connector({"testdb": []})
        await connector._run_full_sync_internal()
        connector._remove_database_group_self_links.assert_awaited_once()
        connector._remove_stale_schema_groups.assert_awaited_once_with(synced_schemas=["testdb"])
