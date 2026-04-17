"""Tests for app.connectors.sources.mariadb.connector."""

import json
import logging
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, RecordRelations
from app.connectors.core.registry.filters import FilterOption
from app.connectors.sources.mariadb.connector import (
    MAX_ROWS_PER_TABLE_LIMIT,
    MariaDBConnector,
    MariaDBTable,
    MariaDBTableState,
    SyncStats,
)
from app.models.entities import (
    ProgressStatus,
    RecordGroupType,
    RecordType,
)
from app.sources.client.mariadb.mariadb import MariaDBResponse
from app.sources.external.mariadb.mariadb_ import ColumnInfo, ForeignKeyInfo


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.mariadb")

    dep = MagicMock()
    dep.org_id = "org-mdb-1"
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


def _make_connector() -> MariaDBConnector:
    logger, dep, dsp, cs = _make_mock_deps()
    connector = MariaDBConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="conn-mdb-1",
    )
    return connector


def _mdb_response(
    success: bool = True,
    data: Optional[Any] = None,
    error: Optional[str] = None,
) -> MariaDBResponse:
    return MariaDBResponse(success=success, data=data if data is not None else {}, error=error)


# ===========================================================================
# MariaDBTable dataclass
# ===========================================================================


class TestMariaDBTable:

    def test_default_fields(self):
        t = MariaDBTable(name="users", database_name="mydb")
        assert t.name == "users"
        assert t.database_name == "mydb"
        assert t.row_count is None
        assert t.columns == []
        assert t.foreign_keys == []
        assert t.primary_keys == []

    def test_fqn_property(self):
        t = MariaDBTable(name="orders", database_name="shop")
        assert t.fqn == "shop.orders"

    def test_with_columns(self):
        cols = [ColumnInfo(name="id", data_type="int")]
        t = MariaDBTable(name="t", database_name="db", columns=cols)
        assert len(t.columns) == 1
        assert t.columns[0].name == "id"

    def test_with_foreign_keys(self):
        fks = [ForeignKeyInfo(constraint_name="fk1", column_name="user_id")]
        t = MariaDBTable(name="t", database_name="db", foreign_keys=fks)
        assert len(t.foreign_keys) == 1
        assert t.foreign_keys[0].constraint_name == "fk1"

    def test_with_primary_keys(self):
        t = MariaDBTable(name="t", database_name="db", primary_keys=["id", "name"])
        assert t.primary_keys == ["id", "name"]

    def test_with_row_count(self):
        t = MariaDBTable(name="t", database_name="db", row_count=42)
        assert t.row_count == 42


# ===========================================================================
# SyncStats dataclass
# ===========================================================================


class TestSyncStats:

    def test_defaults(self):
        s = SyncStats()
        assert s.tables_new == 0
        assert s.errors == 0

    def test_to_dict(self):
        s = SyncStats(tables_new=5, errors=1)
        d = s.to_dict()
        assert d == {"tables_new": 5, "errors": 1}

    def test_log_summary(self):
        s = SyncStats(tables_new=3, errors=0)
        mock_logger = MagicMock()
        s.log_summary(mock_logger)
        mock_logger.info.assert_called_once()
        call_str = mock_logger.info.call_args[0][0]
        assert "3" in call_str


# ===========================================================================
# MariaDBConnector.__init__
# ===========================================================================


class TestMariaDBConnectorInit:

    def test_connector_initializes(self):
        connector = _make_connector()
        assert connector.connector_id == "conn-mdb-1"
        assert connector.connector_name == Connectors.MARIADB
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

    def test_sync_point_created(self):
        connector = _make_connector()
        assert connector.tables_sync_point is not None

    def test_filter_collections_initialized(self):
        connector = _make_connector()
        assert connector.sync_filters is not None
        assert connector.indexing_filters is not None


# ===========================================================================
# MariaDBConnector.init
# ===========================================================================


class TestMariaDBConnectorInitMethod:

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
    async def test_returns_true_on_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "localhost",
                "port": "3306",
                "database": "testdb",
                "username": "root",
                "password": "secret",
            },
        })
        with patch(
            "app.connectors.sources.mariadb.connector.MariaDBConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.mariadb.connector.MariaDBDataSource"
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
                "port": "3306",
                "database": "testdb",
                "username": "root",
                "password": "secret",
            },
            "scope": "INDIVIDUAL",
        })
        with patch(
            "app.connectors.sources.mariadb.connector.MariaDBConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.mariadb.connector.MariaDBDataSource"
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
                "port": "3306",
                "database": "testdb",
                "username": "root",
                "password": "secret",
            },
            "created_by": "user-123",
        })
        with patch(
            "app.connectors.sources.mariadb.connector.MariaDBConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.mariadb.connector.MariaDBDataSource"
        ):
            mock_client = MagicMock()
            mock_client.connect.return_value = mock_client
            mock_config_cls.return_value.create_client.return_value = mock_client

            await connector.init()

        assert connector.created_by == "user-123"

    @pytest.mark.asyncio
    async def test_default_port_when_not_specified(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "host": "localhost",
                "database": "testdb",
                "username": "root",
                "password": "secret",
            },
        })
        with patch(
            "app.connectors.sources.mariadb.connector.MariaDBConfig"
        ) as mock_config_cls, patch(
            "app.connectors.sources.mariadb.connector.MariaDBDataSource"
        ):
            mock_client = MagicMock()
            mock_client.connect.return_value = mock_client
            mock_config_cls.return_value.create_client.return_value = mock_client

            result = await connector.init()

        assert result is True
        call_kwargs = mock_config_cls.call_args
        assert call_kwargs[1]["port"] == 3306 or call_kwargs.kwargs.get("port") == 3306


# ===========================================================================
# MariaDBConnector.test_connection_and_access
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
            return_value=_mdb_response(True, {"version": "10.11.6"})
        )
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_api_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.test_connection = AsyncMock(
            return_value=_mdb_response(False, error="Connection refused")
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
# MariaDBConnector.get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    def test_returns_none(self):
        connector = _make_connector()
        record = MagicMock()
        result = connector.get_signed_url(record)
        assert result is None


# ===========================================================================
# MariaDBConnector.handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    def test_raises_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({"type": "test"})


# ===========================================================================
# MariaDBConnector.get_app_users
# ===========================================================================


class TestGetAppUsers:

    def test_converts_users(self):
        connector = _make_connector()
        user = MagicMock()
        user.email = "alice@example.com"
        user.full_name = "Alice"
        user.source_user_id = "u-1"
        user.id = "u-1"
        user.org_id = "org-mdb-1"
        user.is_active = True
        user.title = "Engineer"

        result = connector.get_app_users([user])
        assert len(result) == 1
        assert result[0].email == "alice@example.com"
        assert result[0].full_name == "Alice"
        assert result[0].app_name == Connectors.MARIADB
        assert result[0].connector_id == "conn-mdb-1"

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
        assert result[0].org_id == "org-mdb-1"


# ===========================================================================
# MariaDBConnector._create_app_users
# ===========================================================================


class TestCreateAppUsers:

    @pytest.mark.asyncio
    async def test_team_scope_creates_team_app_edge(self):
        connector = _make_connector()
        connector.scope = "team"

        tx_store = MagicMock()
        tx_store.ensure_team_app_edge = AsyncMock()
        tx_ctx = MagicMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=tx_store)
        tx_ctx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction = MagicMock(return_value=tx_ctx)

        await connector._create_app_users()

        tx_store.ensure_team_app_edge.assert_awaited_once_with(
            "conn-mdb-1", "org-mdb-1"
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
        mock_user.org_id = "org-mdb-1"
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

        tx_store = MagicMock()
        tx_store.ensure_team_app_edge = AsyncMock(side_effect=Exception("DB error"))
        tx_ctx = MagicMock()
        tx_ctx.__aenter__ = AsyncMock(return_value=tx_store)
        tx_ctx.__aexit__ = AsyncMock(return_value=None)
        connector.data_store_provider.transaction = MagicMock(return_value=tx_ctx)

        with pytest.raises(Exception, match="DB error"):
            await connector._create_app_users()


# ===========================================================================
# MariaDBConnector._get_permissions
# ===========================================================================


class TestGetPermissions:

    @pytest.mark.asyncio
    async def test_returns_org_owner_permission(self):
        connector = _make_connector()
        perms = await connector._get_permissions()
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"


# ===========================================================================
# MariaDBConnector._fetch_tables
# ===========================================================================


class TestFetchTables:

    @pytest.mark.asyncio
    async def test_returns_tables(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_mdb_response(True, [
                {"name": "users"},
                {"name": "orders"},
            ])
        )
        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_mdb_response(True, [])
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_mdb_response(True, [{"column_name": "id"}])
        )

        tables = await connector._fetch_tables("mydb")
        assert len(tables) == 2
        assert tables[0].name == "users"
        assert tables[0].database_name == "mydb"
        assert tables[0].primary_keys == ["id"]

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_mdb_response(False, error="Access denied")
        )
        tables = await connector._fetch_tables("mydb")
        assert tables == []

    @pytest.mark.asyncio
    async def test_handles_table_info_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_mdb_response(True, [{"name": "t1"}])
        )
        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(False, error="Error")
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_mdb_response(False, error="Error")
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_mdb_response(False, error="Error")
        )

        tables = await connector._fetch_tables("mydb")
        assert len(tables) == 1
        assert tables[0].columns == []
        assert tables[0].foreign_keys == []
        assert tables[0].primary_keys == []


# ===========================================================================
# MariaDBConnector._ensure_database_record_groups
# ===========================================================================


class TestEnsureDatabaseRecordGroups:

    @pytest.mark.asyncio
    async def test_creates_record_groups(self):
        connector = _make_connector()
        await connector._ensure_database_record_groups(["db1", "db2"])
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0][0].name == "db1"
        assert call_args[0][0].group_type == RecordGroupType.SQL_DATABASE

    @pytest.mark.asyncio
    async def test_empty_databases_list(self):
        connector = _make_connector()
        await connector._ensure_database_record_groups([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()


# ===========================================================================
# MariaDBConnector._get_filter_values
# ===========================================================================


class TestGetFilterValues:

    def test_returns_none_when_no_filter(self):
        connector = _make_connector()
        result = connector._get_filter_values()
        assert result is None

    def test_returns_filter_values(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.value = ["mydb.users", "mydb.orders"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        result = connector._get_filter_values()
        assert result == ["mydb.users", "mydb.orders"]


# ===========================================================================
# MariaDBConnector._sync_tables
# ===========================================================================


class TestSyncTables:

    @pytest.mark.asyncio
    async def test_syncs_tables_in_batches(self):
        connector = _make_connector()
        connector.batch_size = 2
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        tables = [
            MariaDBTable(name=f"t{i}", database_name="db") for i in range(5)
        ]
        await connector._sync_tables("db", tables)
        assert connector.data_entities_processor.on_new_records.await_count == 3

    @pytest.mark.asyncio
    async def test_skips_empty_tables_list(self):
        connector = _make_connector()
        await connector._sync_tables("db", [])
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# MariaDBConnector._process_tables_generator
# ===========================================================================


class TestProcessTablesGenerator:

    @pytest.mark.asyncio
    async def test_yields_records(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        tables = [MariaDBTable(name="users", database_name="mydb")]
        records = []
        async for record, perms in connector._process_tables_generator("mydb", tables):
            records.append((record, perms))

        assert len(records) == 1
        rec = records[0][0]
        assert rec.record_type == RecordType.SQL_TABLE
        assert rec.external_record_id == "mydb.users"
        assert rec.record_name == "users"
        assert rec.connector_name == Connectors.MARIADB
        assert rec.mime_type == MimeTypes.SQL_TABLE.value
        assert rec.version == 1

    @pytest.mark.asyncio
    async def test_adds_foreign_key_relations(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        fk = ForeignKeyInfo(
            foreign_database="mydb",
            foreign_table_name="departments",
            column_name="dept_id",
            foreign_column_name="id",
            constraint_name="fk_dept",
        )
        tables = [MariaDBTable(name="users", database_name="mydb", foreign_keys=[fk])]
        records = []
        async for record, perms in connector._process_tables_generator("mydb", tables):
            records.append(record)

        assert len(records) == 1
        assert len(records[0].related_external_records) == 1
        rel = records[0].related_external_records[0]
        assert rel.external_record_id == "mydb.departments"
        assert rel.relation_type == RecordRelations.FOREIGN_KEY
        assert rel.source_column == "dept_id"
        assert rel.target_column == "id"

    @pytest.mark.asyncio
    async def test_sets_auto_index_off(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        tables = [MariaDBTable(name="t1", database_name="db")]
        records = []
        async for record, perms in connector._process_tables_generator("db", tables):
            records.append(record)

        assert records[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_caches_record_ids(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        tables = [MariaDBTable(name="t1", database_name="db")]
        async for _ in connector._process_tables_generator("db", tables):
            pass

        assert "db.t1" in connector._record_id_cache

    @pytest.mark.asyncio
    async def test_skips_fk_with_no_target_table(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        fk = ForeignKeyInfo(
            foreign_database="mydb",
            foreign_table_name="",
            column_name="col",
            foreign_column_name="id",
            constraint_name="fk_bad",
        )
        tables = [MariaDBTable(name="t1", database_name="mydb", foreign_keys=[fk])]
        records = []
        async for record, perms in connector._process_tables_generator("mydb", tables):
            records.append(record)

        assert len(records[0].related_external_records) == 0


# ===========================================================================
# MariaDBConnector.run_sync
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
            "app.connectors.sources.mariadb.connector.load_connector_filters",
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
            "app.connectors.sources.mariadb.connector.load_connector_filters",
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
# MariaDBConnector._run_full_sync_internal
# ===========================================================================


class TestRunFullSyncInternal:

    @pytest.mark.asyncio
    async def test_raises_when_no_database_name(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = None
        connector._create_app_users = AsyncMock()

        with pytest.raises(ValueError, match="Database name must be configured"):
            await connector._run_full_sync_internal()

    @pytest.mark.asyncio
    async def test_full_sync_flow(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        connector._create_app_users = AsyncMock()
        connector._ensure_database_record_groups = AsyncMock()
        connector._fetch_tables = AsyncMock(return_value=[
            MariaDBTable(name="t1", database_name="testdb"),
        ])
        connector._sync_tables = AsyncMock()
        connector._save_tables_sync_state = AsyncMock()

        await connector._run_full_sync_internal()

        connector._create_app_users.assert_awaited_once()
        connector._ensure_database_record_groups.assert_awaited_once()
        connector._fetch_tables.assert_awaited_once_with("testdb")
        connector._sync_tables.assert_awaited_once()
        connector._save_tables_sync_state.assert_awaited_once()
        assert connector.sync_stats.tables_new == 1

    @pytest.mark.asyncio
    async def test_full_sync_respects_table_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        mock_filter = MagicMock()
        mock_filter.value = ["testdb.t1"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        connector._create_app_users = AsyncMock()
        connector._ensure_database_record_groups = AsyncMock()
        connector._fetch_tables = AsyncMock(return_value=[
            MariaDBTable(name="t1", database_name="testdb"),
            MariaDBTable(name="t2", database_name="testdb"),
        ])
        connector._sync_tables = AsyncMock()
        connector._save_tables_sync_state = AsyncMock()

        await connector._run_full_sync_internal()

        call_args = connector._sync_tables.call_args[0]
        synced_tables = call_args[1]
        assert len(synced_tables) == 1
        assert synced_tables[0].name == "t1"


# ===========================================================================
# MariaDBConnector._sync_updated_tables
# ===========================================================================


class TestSyncUpdatedTables:

    @pytest.mark.asyncio
    async def test_skips_empty_list(self):
        connector = _make_connector()
        await connector._sync_updated_tables("db", [])
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

        table = MariaDBTable(name="users", database_name="mydb")
        await connector._sync_updated_tables("mydb", [table])

        connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        updated_rec = connector.data_entities_processor.on_record_content_update.call_args[0][0]
        assert updated_rec.id == "rec-1"
        assert updated_rec.version == 3

    @pytest.mark.asyncio
    async def test_skips_when_no_existing_record(self):
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=None
        )
        table = MariaDBTable(name="users", database_name="mydb")
        await connector._sync_updated_tables("mydb", [table])
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
            MariaDBTable(name="t1", database_name="db"),
            MariaDBTable(name="t2", database_name="db"),
        ]
        await connector._sync_updated_tables("db", tables)
        assert connector.data_entities_processor.on_record_content_update.await_count == 1


# ===========================================================================
# MariaDBConnector._has_table_changed
# ===========================================================================


class TestHasTableChanged:

    def test_detects_column_hash_change(self):
        connector = _make_connector()
        current = MariaDBTableState(column_hash="abc", n_live_tup=10)
        stored = MariaDBTableState(column_hash="xyz", n_live_tup=10)
        assert connector._has_table_changed(current, stored) is True

    def test_no_change_when_identical(self):
        connector = _make_connector()
        state = MariaDBTableState(column_hash="abc", n_live_tup=10, last_updated="2024-01-01", auto_increment=5)
        assert connector._has_table_changed(state, state.model_copy()) is False

    def test_detects_row_count_change(self):
        connector = _make_connector()
        current = MariaDBTableState(column_hash="abc", n_live_tup=20)
        stored = MariaDBTableState(column_hash="abc", n_live_tup=10)
        assert connector._has_table_changed(current, stored) is True

    def test_detects_last_updated_change(self):
        connector = _make_connector()
        current = MariaDBTableState(column_hash="abc", n_live_tup=10, last_updated="2024-06-01")
        stored = MariaDBTableState(column_hash="abc", n_live_tup=10, last_updated="2024-01-01")
        assert connector._has_table_changed(current, stored) is True

    def test_detects_auto_increment_change(self):
        connector = _make_connector()
        current = MariaDBTableState(column_hash="abc", n_live_tup=10, auto_increment=10)
        stored = MariaDBTableState(column_hash="abc", n_live_tup=10, auto_increment=5)
        assert connector._has_table_changed(current, stored) is True

    def test_detects_update_time_appeared(self):
        connector = _make_connector()
        current = MariaDBTableState(column_hash="abc", n_live_tup=10, last_updated="2024-01-01")
        stored = MariaDBTableState(column_hash="abc", n_live_tup=10)
        assert connector._has_table_changed(current, stored) is True

    def test_detects_update_time_wiped(self):
        connector = _make_connector()
        current = MariaDBTableState(column_hash="abc", n_live_tup=10)
        stored = MariaDBTableState(column_hash="abc", n_live_tup=10, last_updated="2024-01-01")
        assert connector._has_table_changed(current, stored) is True

    def test_handles_none_values(self):
        connector = _make_connector()
        current = MariaDBTableState(column_hash="abc")
        stored = MariaDBTableState(column_hash="abc")
        assert connector._has_table_changed(current, stored) is False


# ===========================================================================
# MariaDBConnector._compute_column_hash
# ===========================================================================


class TestComputeColumnHash:

    @pytest.mark.asyncio
    async def test_returns_hash(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": [{"name": "id", "type": "int"}]})
        )
        result = await connector._compute_column_hash("db", "t1")
        assert len(result) == 32  # MD5 hex digest length

    @pytest.mark.asyncio
    async def test_returns_empty_on_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(False, error="Not found")
        )
        result = await connector._compute_column_hash("db", "t1")
        assert result == ""

    @pytest.mark.asyncio
    async def test_same_columns_produce_same_hash(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        cols = [{"name": "id", "type": "int"}, {"name": "name", "type": "varchar"}]
        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": cols})
        )
        h1 = await connector._compute_column_hash("db", "t1")
        h2 = await connector._compute_column_hash("db", "t1")
        assert h1 == h2

    @pytest.mark.asyncio
    async def test_different_columns_produce_different_hash(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": [{"name": "id"}]})
        )
        h1 = await connector._compute_column_hash("db", "t1")

        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": [{"name": "id"}, {"name": "extra"}]})
        )
        h2 = await connector._compute_column_hash("db", "t1")
        assert h1 != h2


# ===========================================================================
# MariaDBConnector.run_incremental_sync
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
            "app.connectors.sources.mariadb.connector.load_connector_filters",
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
            "testdb.existing": MariaDBTableState(column_hash="abc", n_live_tup=10),
            "testdb.deleted": MariaDBTableState(column_hash="def", n_live_tup=5),
        }

        with patch(
            "app.connectors.sources.mariadb.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            connector.tables_sync_point.read_sync_point = AsyncMock(
                return_value={"table_states": json.dumps(
                    {fqn: state.model_dump() for fqn, state in stored_states.items()}
                )}
            )
            connector._get_current_table_states = AsyncMock(return_value={
                "testdb.existing": MariaDBTableState(column_hash="xyz", n_live_tup=10),
                "testdb.new_table": MariaDBTableState(column_hash="ghi"),
            })
            connector._sync_new_tables = AsyncMock()
            connector._sync_changed_tables = AsyncMock()
            connector._handle_deleted_tables = AsyncMock()
            connector._save_tables_sync_state = AsyncMock()

            await connector.run_incremental_sync()

            connector._sync_new_tables.assert_awaited_once()
            new_tables = connector._sync_new_tables.call_args[0][0]
            assert "testdb.new_table" in new_tables

            connector._sync_changed_tables.assert_awaited_once()
            changed_tables = connector._sync_changed_tables.call_args[0][0]
            assert "testdb.existing" in changed_tables

            connector._handle_deleted_tables.assert_awaited_once()
            deleted_tables = connector._handle_deleted_tables.call_args[0][0]
            assert "testdb.deleted" in deleted_tables


# ===========================================================================
# MariaDBConnector._handle_deleted_tables
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
        await connector._handle_deleted_tables(["db.t1"])
        connector.data_entities_processor.on_record_deleted.assert_awaited_once_with("rec-1")

    @pytest.mark.asyncio
    async def test_skips_when_no_record_found(self):
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=None
        )
        await connector._handle_deleted_tables(["db.t1"])
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_on_error(self):
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=[Exception("error"), MagicMock(id="r2")]
        )
        await connector._handle_deleted_tables(["db.t1", "db.t2"])
        assert connector.data_entities_processor.on_record_deleted.await_count == 1


# ===========================================================================
# MariaDBConnector.stream_record
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
        connector.connector_name = Connectors.MARIADB

        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "testdb.users"

        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_mdb_response(True, [])
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_mdb_response(True, [])
        )
        connector.data_source.fetch_table_rows = AsyncMock(return_value=[{"id": 1}])
        connector.data_source.get_table_ddl = AsyncMock(
            return_value=_mdb_response(True, {"ddl": "CREATE TABLE users (...)"})
        )

        with patch(
            "app.connectors.sources.mariadb.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_sync = MagicMock()
            mock_sync.get_value.return_value = 100
            mock_load.return_value = (mock_sync, MagicMock())

            result = await connector.stream_record(record)
            assert result is not None


# ===========================================================================
# MariaDBConnector.cleanup
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
# MariaDBConnector.reindex_records
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
# MariaDBConnector.get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_unknown_filter_key(self):
        connector = _make_connector()
        result = await connector.get_filter_options("unknown_key")
        assert result.success is False
        assert "Unknown filter key" in result.message

    @pytest.mark.asyncio
    async def test_returns_tables(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.data_source.list_tables = AsyncMock(
            return_value=_mdb_response(True, [
                {"name": "users"},
                {"name": "orders"},
            ])
        )

        result = await connector.get_filter_options("tables")
        assert result.success is True
        assert len(result.options) == 2
        assert result.options[0].label == "testdb.users"

    @pytest.mark.asyncio
    async def test_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.data_source.list_tables = AsyncMock(
            return_value=_mdb_response(True, [
                {"name": f"t{i}"} for i in range(25)
            ])
        )

        result = await connector.get_filter_options("tables", page=1, limit=10)
        assert result.success is True
        assert len(result.options) == 10
        assert result.has_more is True
        assert result.cursor is not None

    @pytest.mark.asyncio
    async def test_search_filter(self):
        connector = _make_connector()
        connector._table_filter_cache = [
            FilterOption(id="db.users", label="db.users"),
            FilterOption(id="db.orders", label="db.orders"),
            FilterOption(id="db.products", label="db.products"),
        ]

        result = await connector.get_filter_options("tables", search="order")
        assert result.success is True
        assert len(result.options) == 1
        assert result.options[0].label == "db.orders"

    @pytest.mark.asyncio
    async def test_cursor_pagination(self):
        connector = _make_connector()
        connector._table_filter_cache = [
            FilterOption(id=f"db.t{i}", label=f"db.t{i}") for i in range(5)
        ]

        result = await connector.get_filter_options("tables", limit=2, cursor="2", search="db")
        assert result.success is True
        assert len(result.options) == 2
        assert result.options[0].id == "db.t2"

    @pytest.mark.asyncio
    async def test_handles_error(self):
        connector = _make_connector()
        connector.data_source = None

        result = await connector.get_filter_options("tables")
        assert result.success is False


# ===========================================================================
# MariaDBConnector._populate_filter_cache
# ===========================================================================


class TestPopulateFilterCache:

    @pytest.mark.asyncio
    async def test_populates_cache(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.data_source.list_tables = AsyncMock(
            return_value=_mdb_response(True, [
                {"name": "users"},
                {"name": "orders"},
            ])
        )

        await connector._populate_filter_cache()
        assert len(connector._table_filter_cache) == 2
        assert connector._table_filter_cache[0].id == "testdb.users"

    @pytest.mark.asyncio
    async def test_raises_when_data_source_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(RuntimeError):
            await connector._populate_filter_cache()

    @pytest.mark.asyncio
    async def test_raises_when_database_not_configured(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = None
        with pytest.raises(RuntimeError):
            await connector._populate_filter_cache()

    @pytest.mark.asyncio
    async def test_raises_when_list_tables_fails(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.data_source.list_tables = AsyncMock(
            return_value=_mdb_response(False, error="DB error")
        )
        with pytest.raises(RuntimeError):
            await connector._populate_filter_cache()


# ===========================================================================
# MariaDBConnector._get_current_table_states
# ===========================================================================


class TestGetCurrentTableStates:

    @pytest.mark.asyncio
    async def test_returns_states(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.data_source.get_table_stats = AsyncMock(
            return_value=_mdb_response(True, [
                {"database_name": "testdb", "table_name": "users", "n_live_tup": 100, "last_updated": None, "auto_increment": 5},
            ])
        )
        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": [{"name": "id"}]})
        )

        states = await connector._get_current_table_states(None)
        assert "testdb.users" in states
        assert states["testdb.users"].n_live_tup == 100
        assert len(states["testdb.users"].column_hash) == 32

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_database(self):
        connector = _make_connector()
        connector.database_name = None
        states = await connector._get_current_table_states(None)
        assert states == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_stats_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.data_source.get_table_stats = AsyncMock(
            return_value=_mdb_response(False, error="Error")
        )
        states = await connector._get_current_table_states(None)
        assert states == {}

    @pytest.mark.asyncio
    async def test_respects_selected_tables_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.data_source.get_table_stats = AsyncMock(
            return_value=_mdb_response(True, [
                {"database_name": "testdb", "table_name": "users", "n_live_tup": 10, "last_updated": None, "auto_increment": 0},
                {"database_name": "testdb", "table_name": "orders", "n_live_tup": 20, "last_updated": None, "auto_increment": 0},
            ])
        )
        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": []})
        )

        states = await connector._get_current_table_states(["testdb.users"])
        assert "testdb.users" in states
        assert "testdb.orders" not in states


# ===========================================================================
# MariaDBConnector._save_tables_sync_state
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
            return_value=_mdb_response(True, [])
        )
        connector.tables_sync_point.update_sync_point = AsyncMock()

        await connector._save_tables_sync_state("mariadb_tables_state")

        connector.tables_sync_point.update_sync_point.assert_awaited_once()
        call_args = connector.tables_sync_point.update_sync_point.call_args
        assert call_args[0][0] == "mariadb_tables_state"
        assert "table_states" in call_args[0][1]


# ===========================================================================
# MariaDBConnector._sync_new_tables
# ===========================================================================


class TestSyncNewTables:

    @pytest.mark.asyncio
    async def test_syncs_new_tables(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_mdb_response(True, [])
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_mdb_response(True, [])
        )
        connector._ensure_database_record_groups = AsyncMock()
        connector._sync_tables = AsyncMock()

        await connector._sync_new_tables(["mydb.new_table"])

        connector._ensure_database_record_groups.assert_awaited_once()
        connector._sync_tables.assert_awaited_once()
        assert connector.sync_stats.tables_new == 1


# ===========================================================================
# MariaDBConnector._sync_changed_tables
# ===========================================================================


class TestSyncChangedTables:

    @pytest.mark.asyncio
    async def test_syncs_changed_tables(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": []})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_mdb_response(True, [])
        )
        connector.data_source.get_primary_keys = AsyncMock(
            return_value=_mdb_response(True, [])
        )
        connector._sync_updated_tables = AsyncMock()

        await connector._sync_changed_tables(["mydb.users"])

        connector._sync_updated_tables.assert_awaited_once()


# ===========================================================================
# MariaDBConnector.create_connector (factory method)
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_creates_connector(self):
        logger = logging.getLogger("test")
        dsp = MagicMock()
        cs = MagicMock()

        with patch(
            "app.connectors.sources.mariadb.connector.DataSourceEntitiesProcessor"
        ) as mock_dep_cls:
            mock_dep = MagicMock()
            mock_dep.org_id = "org-1"
            mock_dep.initialize = AsyncMock()
            mock_dep_cls.return_value = mock_dep

            connector = await MariaDBConnector.create_connector(
                logger=logger,
                data_store_provider=dsp,
                config_service=cs,
                connector_id="conn-test",
            )

        assert isinstance(connector, MariaDBConnector)
        assert connector.connector_id == "conn-test"
        mock_dep.initialize.assert_awaited_once()


# ===========================================================================
# MAX_ROWS_PER_TABLE_LIMIT constant
# ===========================================================================


class TestMaxRowsPerTableLimit:

    def test_value(self):
        assert MAX_ROWS_PER_TABLE_LIMIT == 10000
