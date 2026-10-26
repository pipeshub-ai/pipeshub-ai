"""Tests for app.connectors.sources.mariadb.connector."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import FilterOption
from app.connectors.sources.mariadb.connector import (
    MAX_ROWS_PER_TABLE_LIMIT,
    MariaDBConnector,
    MariaDBTable,
    SyncStats,
)
from app.models.entities import (
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
    dep.get_records_by_record_type = AsyncMock(return_value=[])
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
    data: Any | None = None,
    error: str | None = None,
) -> MariaDBResponse:
    return MariaDBResponse(success=success, data=data if data is not None else {}, error=error)


# ===========================================================================
# MariaDBTable dataclass
# ===========================================================================


class TestMariaDBTable:

    def test_default_fields(self) -> None:
        t = MariaDBTable(name="users", database_name="mydb")
        assert t.name == "users"
        assert t.database_name == "mydb"
        assert t.row_count is None
        assert t.columns == []
        assert t.foreign_keys == []
        assert t.primary_keys == []

    def test_fqn_property(self) -> None:
        t = MariaDBTable(name="orders", database_name="shop")
        assert t.fqn == "shop.orders"

    def test_with_columns(self) -> None:
        cols = [ColumnInfo(name="id", data_type="int")]
        t = MariaDBTable(name="t", database_name="db", columns=cols)
        assert len(t.columns) == 1
        assert t.columns[0].name == "id"

    def test_with_foreign_keys(self) -> None:
        fks = [ForeignKeyInfo(constraint_name="fk1", column_name="user_id")]
        t = MariaDBTable(name="t", database_name="db", foreign_keys=fks)
        assert len(t.foreign_keys) == 1
        assert t.foreign_keys[0].constraint_name == "fk1"

    def test_with_primary_keys(self) -> None:
        t = MariaDBTable(name="t", database_name="db", primary_keys=["id", "name"])
        assert t.primary_keys == ["id", "name"]

    def test_with_row_count(self) -> None:
        t = MariaDBTable(name="t", database_name="db", row_count=42)
        assert t.row_count == 42


# ===========================================================================
# SyncStats dataclass
# ===========================================================================


class TestSyncStats:

    def test_defaults(self) -> None:
        s = SyncStats()
        assert s.tables_new == 0
        assert s.errors == 0

    def test_to_dict(self) -> None:
        s = SyncStats(tables_new=5, errors=1)
        d = s.to_dict()
        assert d == {"tables_new": 5, "tables_renamed": 0, "tables_removed": 0, "errors": 1}

    def test_log_summary(self) -> None:
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

    def test_connector_initializes(self) -> None:
        connector = _make_connector()
        assert connector.connector_id == "conn-mdb-1"
        assert connector.connector_name == Connectors.MARIADB
        assert connector.data_source is None

    def test_defaults(self) -> None:
        connector = _make_connector()
        assert connector.database_name is None
        assert connector.batch_size == 100
        # Kept from the constructor, not reset.
        assert connector.connector_scope == "team"

    def test_sync_stats_initialized(self) -> None:
        connector = _make_connector()
        assert isinstance(connector.sync_stats, SyncStats)
        assert connector.sync_stats.tables_new == 0

    def test_sync_point_created(self) -> None:
        connector = _make_connector()
        assert connector.tables_sync_point is not None

    def test_filter_collections_initialized(self) -> None:
        connector = _make_connector()
        assert connector.sync_filters is not None
        assert connector.indexing_filters is not None


# ===========================================================================
# MariaDBConnector.init
# ===========================================================================


class TestMariaDBConnectorInitMethod:

    @pytest.mark.asyncio
    async def test_returns_false_when_no_config(self) -> None:
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_missing_required_fields(self) -> None:
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"host": "", "database": "", "username": ""},
        })
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_host(self) -> None:
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"database": "mydb", "username": "user", "password": "pw"},
        })
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self) -> None:
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
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client
            mock_ds_cls.return_value = MagicMock()

            result = await connector.init()

        assert result is True
        assert connector.data_source is not None
        assert connector.database_name == "testdb"

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self) -> None:
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            side_effect=Exception("Service error")
        )
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_sets_connector_scope(self) -> None:
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
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client

            await connector.init()

        assert connector.connector_scope == "INDIVIDUAL"

    @pytest.mark.asyncio
    async def test_sets_created_by(self) -> None:
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
            mock_client.connect = AsyncMock(return_value=mock_client)
            mock_config_cls.return_value.create_client.return_value = mock_client

            await connector.init()

        assert connector.created_by == "user-123"

    @pytest.mark.asyncio
    async def test_default_port_when_not_specified(self) -> None:
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
            mock_client.connect = AsyncMock(return_value=mock_client)
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
    async def test_returns_false_when_data_source_not_initialized(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.test_connection = AsyncMock(
            return_value=_mdb_response(True, {"version": "10.11.6"})
        )
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_api_failure(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.test_connection = AsyncMock(
            return_value=_mdb_response(False, error="Connection refused")
        )
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_exception(self) -> None:
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

    def test_returns_none(self) -> None:
        connector = _make_connector()
        record = MagicMock()
        result = connector.get_signed_url(record)
        assert result is None


# ===========================================================================
# MariaDBConnector.handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    def test_raises_not_implemented(self) -> None:
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({"type": "test"})


# ===========================================================================
# MariaDBConnector.get_app_users
# ===========================================================================


class TestGetAppUsers:

    def test_converts_users(self) -> None:
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

    def test_skips_users_without_email(self) -> None:
        connector = _make_connector()
        user = MagicMock()
        user.email = None
        result = connector.get_app_users([user])
        assert len(result) == 0

    def test_empty_list(self) -> None:
        connector = _make_connector()
        result = connector.get_app_users([])
        assert result == []

    def test_defaults_is_active_to_true(self) -> None:
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

    def test_uses_org_id_fallback(self) -> None:
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
    async def test_team_scope_creates_team_app_edge(self) -> None:
        connector = _make_connector()
        connector.scope = "team"
        connector.data_entities_processor.ensure_team_app_edge = AsyncMock()

        await connector._create_app_users()

        connector.data_entities_processor.ensure_team_app_edge.assert_awaited_once_with(
            "conn-mdb-1"
        )
        connector.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_personal_scope_creates_user_app_edge_for_creator(self) -> None:
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
    async def test_personal_scope_skips_when_creator_not_found(self) -> None:
        connector = _make_connector()
        connector.scope = "personal"
        connector.created_by = "ghost"
        connector.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=None)

        await connector._create_app_users()

        connector.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_personal_scope_skips_when_no_created_by(self) -> None:
        connector = _make_connector()
        connector.scope = "personal"
        connector.created_by = None
        connector.data_entities_processor.get_user_by_user_id = AsyncMock()

        await connector._create_app_users()

        connector.data_entities_processor.get_user_by_user_id.assert_not_called()
        connector.data_entities_processor.on_new_app_users.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_on_error(self) -> None:
        connector = _make_connector()
        connector.scope = "team"
        connector.data_entities_processor.ensure_team_app_edge = AsyncMock(
            side_effect=Exception("DB error")
        )

        with pytest.raises(Exception, match="DB error"):
            await connector._create_app_users()


# ===========================================================================
# MariaDBConnector._get_permissions
# ===========================================================================


class TestGetPermissions:

    @pytest.mark.asyncio
    async def test_returns_org_owner_permission(self) -> None:
        connector = _make_connector()
        perms = await connector._get_permissions()
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"


# ===========================================================================
# MariaDBConnector._fetch_tables
# ===========================================================================


# ===========================================================================
# MariaDBConnector._ensure_database_record_groups
# ===========================================================================


class TestEnsureDatabaseRecordGroups:

    @pytest.mark.asyncio
    async def test_creates_record_groups(self) -> None:
        connector = _make_connector()
        await connector._ensure_database_record_groups(["db1", "db2"])
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0][0].name == "db1"
        assert call_args[0][0].group_type == RecordGroupType.SQL_DATABASE

    @pytest.mark.asyncio
    async def test_empty_databases_list(self) -> None:
        connector = _make_connector()
        await connector._ensure_database_record_groups([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()


# ===========================================================================
# MariaDBConnector._get_filter_values
# ===========================================================================


class TestGetFilterValues:

    def test_returns_none_when_no_filter(self) -> None:
        connector = _make_connector()
        selected, op = connector._get_filter_values()
        assert selected is None
        # Default operator when filter is absent is IN.
        assert op == "in"

    def test_returns_filter_values(self) -> None:
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.value = ["mydb.users", "mydb.orders"]
        mock_filter.operator_value = "in"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        selected, op = connector._get_filter_values()
        assert selected == ["mydb.users", "mydb.orders"]
        assert op == "in"

    def test_returns_not_in_operator(self) -> None:
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.value = ["mydb.audit_log"]
        mock_filter.operator_value = "not_in"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter
        selected, op = connector._get_filter_values()
        assert selected == ["mydb.audit_log"]
        assert op == "not_in"


# ===========================================================================
# MariaDBConnector._sync_tables
# ===========================================================================


# ===========================================================================
# MariaDBConnector._process_tables_generator
# ===========================================================================


# ===========================================================================
# MariaDBConnector.run_sync
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_sync()

    async def test_an_incremental_sync_is_the_one_sync(self) -> None:
        # Every sync compares the catalog with the saved state.
        connector = _make_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    async def test_a_failed_sync_reports_nothing_from_the_one_before(self) -> None:
        connector = _make_connector()
        connector.sync_outcome.restructured.add("stale")
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_sync()
        assert not connector.sync_outcome.restructured


# Removals, renames and the saved state are covered over an in-memory world
# in test_mariadb_sync.py.


# ===========================================================================
# MariaDBConnector.stream_record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_raises_when_data_source_not_initialized(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_table_info_failure_does_not_stream_empty_table(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "testdb.users"
        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(False, None, error="Table not found")
        )

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_row_read_failure_does_not_stream_empty_rows(self) -> None:
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
        connector.data_source.get_foreign_keys = AsyncMock(return_value=_mdb_response(True, []))
        connector.data_source.get_primary_keys = AsyncMock(return_value=_mdb_response(True, []))

        denied = Exception("access denied")
        denied.errno = 1142
        connector.data_source.fetch_table_rows = AsyncMock(side_effect=denied)

        from fastapi import HTTPException
        with patch(
            "app.connectors.sources.mariadb.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_sync = MagicMock()
            mock_sync.get_value.return_value = 100
            mock_load.return_value = (mock_sync, MagicMock())

            with pytest.raises(HTTPException) as exc_info:
                await connector.stream_record(record)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_constraint_failure_does_not_stream_hollow_table(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "testdb.users"

        connector.data_source.get_table_info = AsyncMock(
            return_value=_mdb_response(True, {"columns": [{"name": "id"}]})
        )
        connector.data_source.get_foreign_keys = AsyncMock(
            return_value=_mdb_response(
                False,
                None,
                error="SELECT command denied to user 'x'@'y' for table 'key_column_usage'",
            )
        )

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_for_unsupported_record_type(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        record = MagicMock()
        record.record_type = RecordType.FILE

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_raises_for_invalid_fqn(self) -> None:
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
    async def test_streams_sql_table(self) -> None:
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
    async def test_cleanup_closes_client(self) -> None:
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
    async def test_cleanup_when_no_data_source(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        await connector.cleanup()
        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_handles_error(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.get_client.side_effect = Exception("Close error")
        await connector.cleanup()


# ===========================================================================
# MariaDBConnector.reindex_records
# ===========================================================================


class TestReindexRecords:

    @pytest.mark.asyncio
    async def test_reindex_empty_list(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        await connector.reindex_records([])
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reindex_publishes_events(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        records = [MagicMock(), MagicMock()]
        await connector.reindex_records(records)
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)

    @pytest.mark.asyncio
    async def test_reindex_raises_when_not_initialized(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_reindex_propagates_error(self) -> None:
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
    async def test_unknown_filter_key(self) -> None:
        connector = _make_connector()
        result = await connector.get_filter_options("unknown_key")
        assert result.success is False
        assert "Unknown filter key" in result.message

    @pytest.mark.asyncio
    async def test_returns_tables(self) -> None:
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
    async def test_pagination(self) -> None:
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
    async def test_search_filter(self) -> None:
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
    async def test_cursor_pagination(self) -> None:
        connector = _make_connector()
        connector._table_filter_cache = [
            FilterOption(id=f"db.t{i}", label=f"db.t{i}") for i in range(5)
        ]

        result = await connector.get_filter_options("tables", limit=2, cursor="2", search="db")
        assert result.success is True
        assert len(result.options) == 2
        assert result.options[0].id == "db.t2"

    @pytest.mark.asyncio
    async def test_handles_error(self) -> None:
        connector = _make_connector()
        connector.data_source = None

        result = await connector.get_filter_options("tables")
        assert result.success is False


# ===========================================================================
# MariaDBConnector._populate_filter_cache
# ===========================================================================


class TestPopulateFilterCache:

    @pytest.mark.asyncio
    async def test_populates_cache(self) -> None:
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
    async def test_raises_when_data_source_not_initialized(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(RuntimeError):
            await connector._populate_filter_cache()

    @pytest.mark.asyncio
    async def test_raises_when_database_not_configured(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = None
        with pytest.raises(RuntimeError):
            await connector._populate_filter_cache()

    @pytest.mark.asyncio
    async def test_raises_when_list_tables_fails(self) -> None:
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


# ===========================================================================
# MariaDBConnector._save_tables_sync_state
# ===========================================================================


# ===========================================================================
# MariaDBConnector._sync_new_tables
# ===========================================================================


# ===========================================================================
# MariaDBConnector._sync_changed_tables
# ===========================================================================


# ===========================================================================
# MariaDBConnector.create_connector (factory method)
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_creates_connector(self) -> None:
        logger = logging.getLogger("test")
        dsp = MagicMock()
        cs = MagicMock()
        processor = MagicMock()
        processor.org_id = "org-1"

        connector = await MariaDBConnector.create_connector(
            logger=logger,
            data_store_provider=dsp,
            config_service=cs,
            connector_id="conn-test",
            data_entities_processor=processor,
        )

        assert isinstance(connector, MariaDBConnector)
        assert connector.connector_id == "conn-test"


# ===========================================================================
# MAX_ROWS_PER_TABLE_LIMIT constant
# ===========================================================================


class TestMaxRowsPerTableLimit:

    def test_value(self) -> None:
        assert MAX_ROWS_PER_TABLE_LIMIT == 10000
