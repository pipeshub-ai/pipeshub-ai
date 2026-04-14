"""Tests for app.connectors.sources.postgres.connector."""

import json
import logging
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, RecordRelations
from app.connectors.core.registry.filters import FilterOption
from app.connectors.sources.postgres.connector import (
    MAX_ROWS_PER_TABLE_LIMIT,
    PostgreSQLConnector,
    PostgresSchema,
    PostgresTable,
    PostgresTableState,
    SyncStats,
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
        assert connector._record_id_cache == {}

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
            mock_client.connect.return_value = mock_client
            mock_config_cls.return_value.create_client.return_value = mock_client
            mock_ds_cls.return_value = MagicMock()

            result = await connector.init()

        assert result is True
        assert connector.data_source is not None
        assert connector.database_name == "testdb"

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
            mock_client.connect.return_value = mock_client
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
            mock_client.connect.return_value = mock_client
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
            mock_client.connect.return_value = mock_client
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
            mock_client.connect.return_value = mock_client
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
    async def test_creates_app_users(self):
        connector = _make_connector()
        mock_user = MagicMock()
        mock_user.email = "test@example.com"
        mock_user.full_name = "Test User"
        mock_user.source_user_id = "u-1"
        mock_user.id = "u-1"
        mock_user.org_id = "org-pg-1"
        mock_user.is_active = True
        mock_user.title = None

        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[mock_user]
        )
        await connector._create_app_users()
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_on_error(self):
        connector = _make_connector()
        connector.data_entities_processor.get_all_active_users = AsyncMock(
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
    async def test_returns_empty_on_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(
            return_value=_pg_response(False, error="Access denied")
        )
        schemas = await connector._fetch_schemas()
        assert schemas == []


# ===========================================================================
# PostgreSQLConnector._fetch_tables
# ===========================================================================


class TestFetchTables:

    @pytest.mark.asyncio
    async def test_returns_tables(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(True, [
                {"name": "users", "owner": "postgres"},
                {"name": "orders", "owner": "postgres"},
            ])
        )
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_pg_response(True, [])
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_pg_response(True, [{"column_name": "id"}])
        )

        tables = await connector._fetch_tables("public")
        assert len(tables) == 2
        assert tables[0].name == "users"
        assert tables[0].schema_name == "public"
        assert tables[0].primary_keys == ["id"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(False, error="Access denied")
        )
        tables = await connector._fetch_tables("public")
        assert tables == []

    @pytest.mark.asyncio
    async def test_handles_table_info_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(True, [{"name": "t1"}])
        )
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(False, error="Error")
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_pg_response(False, error="Error")
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_pg_response(False, error="Error")
        )

        tables = await connector._fetch_tables("public")
        assert len(tables) == 1
        assert tables[0].columns == []
        assert tables[0].foreign_keys == []
        assert tables[0].primary_keys == []


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
        schemas, tables = connector._get_filter_values()
        assert schemas is None
        assert tables is None

    def test_returns_filter_values(self):
        connector = _make_connector()
        schema_filter = MagicMock()
        schema_filter.value = ["public"]
        table_filter = MagicMock()
        table_filter.value = ["public.users"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda k: {"schemas": schema_filter, "tables": table_filter}.get(k)

        schemas, tables = connector._get_filter_values()
        assert schemas == ["public"]
        assert tables == ["public.users"]


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
    async def test_caches_record_ids(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        tables = [PostgresTable(name="t1", schema_name="public")]
        async for _ in connector._process_tables_generator("public", tables):
            pass

        assert "public.t1" in connector._record_id_cache

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
                return_value={"table_states": "{}"}
            )
            connector.run_incremental_sync = AsyncMock()

            await connector.run_sync()

            connector.run_incremental_sync.assert_awaited_once()


# ===========================================================================
# PostgreSQLConnector._run_full_sync_internal
# ===========================================================================


class TestRunFullSyncInternal:

    @pytest.mark.asyncio
    async def test_full_sync_flow(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        connector._create_app_users = AsyncMock()
        connector._create_database_record_group = AsyncMock()
        connector._fetch_schemas = AsyncMock(return_value=[
            PostgresSchema(name="public"),
        ])
        connector._fetch_tables = AsyncMock(return_value=[
            PostgresTable(name="t1", schema_name="public"),
        ])
        connector._sync_schemas = AsyncMock()
        connector._sync_tables = AsyncMock()
        connector._save_tables_sync_state = AsyncMock()

        await connector._run_full_sync_internal()

        connector._create_app_users.assert_awaited_once()
        connector._create_database_record_group.assert_awaited_once()
        connector._fetch_schemas.assert_awaited_once()
        connector._sync_schemas.assert_awaited_once()
        connector._fetch_tables.assert_awaited_once_with("public")
        connector._sync_tables.assert_awaited_once()
        connector._save_tables_sync_state.assert_awaited_once()
        assert connector.sync_stats.schemas_synced == 1
        assert connector.sync_stats.tables_new == 1

    @pytest.mark.asyncio
    async def test_full_sync_respects_schema_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        schema_filter = MagicMock()
        schema_filter.value = ["app"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda k: {"schemas": schema_filter}.get(k)

        connector._create_app_users = AsyncMock()
        connector._create_database_record_group = AsyncMock()
        connector._fetch_schemas = AsyncMock(return_value=[
            PostgresSchema(name="public"),
            PostgresSchema(name="app"),
        ])
        connector._fetch_tables = AsyncMock(return_value=[])
        connector._sync_schemas = AsyncMock()
        connector._sync_tables = AsyncMock()
        connector._save_tables_sync_state = AsyncMock()

        await connector._run_full_sync_internal()

        call_args = connector._sync_schemas.call_args[0][0]
        assert len(call_args) == 1
        assert call_args[0].name == "app"

    @pytest.mark.asyncio
    async def test_full_sync_respects_table_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        table_filter = MagicMock()
        table_filter.value = ["public.t1"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda k: {"tables": table_filter}.get(k)

        connector._create_app_users = AsyncMock()
        connector._create_database_record_group = AsyncMock()
        connector._fetch_schemas = AsyncMock(return_value=[
            PostgresSchema(name="public"),
        ])
        connector._fetch_tables = AsyncMock(return_value=[
            PostgresTable(name="t1", schema_name="public"),
            PostgresTable(name="t2", schema_name="public"),
        ])
        connector._sync_schemas = AsyncMock()
        connector._sync_tables = AsyncMock()
        connector._save_tables_sync_state = AsyncMock()

        await connector._run_full_sync_internal()

        call_args = connector._sync_tables.call_args[0]
        synced_tables = call_args[1]
        assert len(synced_tables) == 1
        assert synced_tables[0].name == "t1"

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
        await connector._sync_updated_tables("public", [table])

        connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        updated_rec = connector.data_entities_processor.on_record_content_update.call_args[0][0]
        assert updated_rec.id == "rec-1"
        assert updated_rec.version == 3
        assert updated_rec.record_group_type == RecordGroupType.SQL_NAMESPACE.value

    @pytest.mark.asyncio
    async def test_skips_when_no_existing_record(self):
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=None
        )
        table = PostgresTable(name="users", schema_name="public")
        await connector._sync_updated_tables("public", [table])
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()

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
        await connector._sync_updated_tables("public", tables)
        assert connector.data_entities_processor.on_record_content_update.await_count == 1

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


class TestComputeColumnHash:

    @pytest.mark.asyncio
    async def test_returns_hash(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id", "type": "integer"}]})
        )
        result = await connector._compute_column_hash("public", "t1")
        assert len(result) == 32

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(False, error="Not found")
        )
        result = await connector._compute_column_hash("public", "t1")
        assert result == ""

    @pytest.mark.asyncio
    async def test_same_columns_produce_same_hash(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        cols = [{"name": "id", "type": "integer"}, {"name": "name", "type": "text"}]
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": cols})
        )
        h1 = await connector._compute_column_hash("public", "t1")
        h2 = await connector._compute_column_hash("public", "t1")
        assert h1 == h2

    @pytest.mark.asyncio
    async def test_different_columns_produce_different_hash(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id"}]})
        )
        h1 = await connector._compute_column_hash("public", "t1")

        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id"}, {"name": "extra"}]})
        )
        h2 = await connector._compute_column_hash("public", "t1")
        assert h1 != h2


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

    @pytest.mark.asyncio
    async def test_detects_new_changed_deleted_tables(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"

        stored_states = {
            "public.existing": {"column_hash": "abc", "n_tup_ins": 10, "n_tup_upd": 0, "n_tup_del": 0},
            "public.deleted": {"column_hash": "def", "n_tup_ins": 5, "n_tup_upd": 0, "n_tup_del": 0},
        }

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            connector.tables_sync_point.read_sync_point = AsyncMock(
                return_value={"table_states": json.dumps(stored_states)}
            )
            connector._get_current_table_states = AsyncMock(return_value={
                "public.existing": PostgresTableState(column_hash="xyz", n_tup_ins=10, n_tup_upd=0, n_tup_del=0),
                "public.new_table": PostgresTableState(column_hash="ghi", n_tup_ins=0, n_tup_upd=0, n_tup_del=0),
            })
            connector._sync_new_tables = AsyncMock()
            connector._sync_changed_tables = AsyncMock()
            connector._handle_deleted_tables = AsyncMock()
            connector._save_tables_sync_state = AsyncMock()

            await connector.run_incremental_sync()

            connector._sync_new_tables.assert_awaited_once()
            new_tables = connector._sync_new_tables.call_args[0][0]
            assert "public.new_table" in new_tables

            connector._sync_changed_tables.assert_awaited_once()
            changed_tables = connector._sync_changed_tables.call_args[0][0]
            assert "public.existing" in changed_tables

            connector._handle_deleted_tables.assert_awaited_once()
            deleted_tables = connector._handle_deleted_tables.call_args[0][0]
            assert "public.deleted" in deleted_tables


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
        await connector._handle_deleted_tables(["public.t1", "public.t2"])
        assert connector.data_entities_processor.on_record_deleted.await_count == 1


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
        assert exc_info.value.status_code == 500

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
        connector.data_source = MagicMock()
        connector.data_source.get_client.return_value = mock_client
        connector.database_name = "testdb"
        connector._record_id_cache = {"a": "b"}

        await connector.cleanup()

        mock_client.close.assert_called_once()
        assert connector.data_source is None
        assert connector._record_id_cache == {}
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


class TestGetCurrentTableStates:

    @pytest.mark.asyncio
    async def test_returns_states(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_table_stats = AsyncMock(
            return_value=_pg_response(True, [
                {"schema_name": "public", "table_name": "users", "n_live_tup": 100, "n_tup_ins": 50, "n_tup_upd": 10, "n_tup_del": 2},
            ])
        )
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": [{"name": "id"}]})
        )

        states = await connector._get_current_table_states(None, None)
        assert "public.users" in states
        assert states["public.users"].n_tup_ins == 50
        assert states["public.users"].n_tup_upd == 10
        assert states["public.users"].n_tup_del == 2
        assert len(states["public.users"].column_hash) == 32

    @pytest.mark.asyncio
    async def test_returns_empty_on_stats_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_table_stats = AsyncMock(
            return_value=_pg_response(False, error="Error")
        )
        states = await connector._get_current_table_states(None, None)
        assert states == {}

    @pytest.mark.asyncio
    async def test_respects_selected_tables_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_table_stats = AsyncMock(
            return_value=_pg_response(True, [
                {"schema_name": "public", "table_name": "users", "n_live_tup": 10, "n_tup_ins": 1, "n_tup_upd": 0, "n_tup_del": 0},
                {"schema_name": "public", "table_name": "orders", "n_live_tup": 20, "n_tup_ins": 2, "n_tup_upd": 0, "n_tup_del": 0},
            ])
        )
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(True, {"columns": []})
        )

        states = await connector._get_current_table_states(None, ["public.users"])
        assert "public.users" in states
        assert "public.orders" not in states


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

        connector.data_source.get_table_stats = AsyncMock(
            return_value=_pg_response(True, [])
        )
        connector.tables_sync_point.update_sync_point = AsyncMock()

        await connector._save_tables_sync_state("postgres_tables_state")

        connector.tables_sync_point.update_sync_point.assert_awaited_once()
        call_args = connector.tables_sync_point.update_sync_point.call_args
        assert call_args[0][0] == "postgres_tables_state"
        assert "table_states" in call_args[0][1]


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
        connector._sync_tables = AsyncMock()

        await connector._sync_new_tables(["public.new_table"])

        connector._sync_schemas.assert_awaited_once()
        connector._sync_tables.assert_awaited_once()
        assert connector.sync_stats.tables_new == 1


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
        connector._sync_updated_tables = AsyncMock()

        await connector._sync_changed_tables(["public.users"])

        connector._sync_updated_tables.assert_awaited_once()


# ===========================================================================
# PostgreSQLConnector.create_connector (factory method)
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_creates_connector(self):
        logger = logging.getLogger("test")
        dsp = MagicMock()
        cs = MagicMock()

        with patch(
            "app.connectors.sources.postgres.connector.DataSourceEntitiesProcessor"
        ) as mock_dep_cls:
            mock_dep = MagicMock()
            mock_dep.org_id = "org-1"
            mock_dep.initialize = AsyncMock()
            mock_dep_cls.return_value = mock_dep

            connector = await PostgreSQLConnector.create_connector(
                logger=logger,
                data_store_provider=dsp,
                config_service=cs,
                connector_id="conn-test",
            )

        assert isinstance(connector, PostgreSQLConnector)
        assert connector.connector_id == "conn-test"
        mock_dep.initialize.assert_awaited_once()


# ===========================================================================
# MAX_ROWS_PER_TABLE_LIMIT constant
# ===========================================================================


class TestMaxRowsPerTableLimit:

    def test_value(self):
        assert MAX_ROWS_PER_TABLE_LIMIT == 10000
