"""Tests for app.connectors.sources.postgres.connector."""

import hashlib
import json
import logging
from types import SimpleNamespace
from typing import Any, Never
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordRelations,
)
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import FilterCollection, FilterOption
from app.connectors.core.sql.column_records import column_revision as _column_revision
from app.connectors.sources.postgres.catalog_diff import TableChange, diff_catalog
from app.connectors.sources.postgres.connector import (
    MAX_COLUMN_VALUES_STREAMED,
    MAX_ROWS_PER_TABLE_LIMIT,
    MAX_TABLE_DOCUMENT_BYTES,
    SYNC_STATE_KEY,
    SYNC_STATE_VERSION,
    PostgreSQLConnector,
    PostgresSchema,
    PostgresTable,
    PostgresTableState,
    SyncOutcome,
    SyncStats,
    _column_hash,
    _table_revision,
    split_table_fqn,
    table_fqn,
)
from app.models.entities import (
    ProgressStatus,
    RecordGroupType,
    RecordType,
    SQLColumnRecord,
    SQLTableRecord,
)
from app.sources.client.postgres.postgres import PostgreSQLResponse
from app.sources.external.postgres.postgres_ import (
    ColumnInfo,
    ColumnProfile,
    ForeignKeyInfo,
    TableColumnStatistics,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps() -> tuple:
    logger = logging.getLogger("test.postgres")

    dep = MagicMock()
    dep.org_id = "org-pg-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_sql_records_deleted = AsyncMock(return_value=[])
    dep.on_sql_record_groups_deleted = AsyncMock()
    dep.on_sql_records_renamed = AsyncMock()
    dep.on_sql_table_renamed = AsyncMock()
    dep.sql_tables_to_relate_again = AsyncMock(return_value=set())
    dep.get_typed_records_by_ids = AsyncMock(return_value={})
    dep.move_incoming_relations = AsyncMock(return_value=0)
    dep.on_record_content_update = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.reindex_existing_records = AsyncMock()

    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()

    return logger, dep, dsp, cs


_DB_OID = 16384


def _make_connector() -> PostgreSQLConnector:
    logger, dep, dsp, cs = _make_mock_deps()
    connector = PostgreSQLConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="conn-pg-1",
    )
    # As if read on connect, so a sync does not ask the mocked data source.
    connector.database_oid = _DB_OID
    return connector


def _pg_response(
    success: bool = True,
    data: Any | None = None,
    error: str | None = None,
) -> PostgreSQLResponse:
    return PostgreSQLResponse(success=success, data=data if data is not None else {}, error=error)


def _stored(record_id: str, external_id: str, parent: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=record_id,
        external_record_id=external_id,
        parent_external_record_id=parent,
        record_name=external_id.rsplit(".", 1)[-1],
    )


class _FakeRecordStore:
    """What ``data_store_provider.transaction()`` yields, over a list of stored records."""

    def __init__(self, records) -> None:
        self.records = list(records)
        self.updates: list[tuple] = []
        # Lookups of these external ids fail, as a graph error would.
        self.failing: set = set()

    async def __aenter__(self) -> "_FakeRecordStore":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get_record_by_external_id(self, connector_id, external_id, *, raise_on_error=False) -> object:
        if external_id in self.failing:
            if raise_on_error:
                raise RuntimeError("graph down")
            return None
        return next((r for r in self.records if r.external_record_id == external_id), None)

    async def get_records_by_parent(self, connector_id, parent_external_record_id, record_type=None) -> list:
        return [r for r in self.records if r.parent_external_record_id == parent_external_record_id]

    async def batch_update_nodes(self, nodes, collection) -> bool:
        self.updates.append((nodes, collection))
        by_id = {r.id: r for r in self.records}
        for node in nodes:
            record = by_id[node["id"]]
            record.external_record_id = node["externalRecordId"]
            record.parent_external_record_id = node.get("externalParentId", record.parent_external_record_id)
        return True


def _saves(connector: PostgreSQLConnector) -> tuple[list, list]:
    """The states a run saved: the checkpoints it took as records moved, and the final one."""
    calls = connector._save_tables_sync_state.await_args_list
    checkpoints = [c.args[0] for c in calls if c.kwargs.get("checkpoint")]
    final = [c.args[0] for c in calls if not c.kwargs.get("checkpoint")]
    return checkpoints, final


def _record_store(connector: PostgreSQLConnector, *records) -> _FakeRecordStore:
    store = _FakeRecordStore(records)
    connector.data_store_provider.transaction = MagicMock(return_value=store)
    return store


# ===========================================================================
# PostgresSchema dataclass
# ===========================================================================


class TestPostgresSchema:

    def test_default_fields(self) -> None:
        s = PostgresSchema(name="public")
        assert s.name == "public"
        assert s.owner is None

    def test_with_owner(self) -> None:
        s = PostgresSchema(name="myschema", owner="admin")
        assert s.owner == "admin"


# ===========================================================================
# PostgresTable dataclass
# ===========================================================================


class TestPostgresTable:

    def test_default_fields(self) -> None:
        t = PostgresTable(name="users", schema_name="public")
        assert t.name == "users"
        assert t.schema_name == "public"
        assert t.row_count is None
        assert t.owner is None
        assert t.columns == []
        assert t.foreign_keys == []
        assert t.primary_keys == []

    def test_fqn_property(self) -> None:
        t = PostgresTable(name="orders", schema_name="sales")
        assert t.fqn == "sales.orders"

    def test_with_columns(self) -> None:
        cols = [ColumnInfo(name="id", data_type="integer")]
        t = PostgresTable(name="t", schema_name="public", columns=cols)
        assert len(t.columns) == 1
        assert t.columns[0].name == "id"

    def test_with_foreign_keys(self) -> None:
        fks = [ForeignKeyInfo(constraint_name="fk1", column_name="user_id", foreign_table_name="other", foreign_column_name="id")]
        t = PostgresTable(name="t", schema_name="public", foreign_keys=fks)
        assert len(t.foreign_keys) == 1
        assert t.foreign_keys[0].constraint_name == "fk1"

    def test_with_primary_keys(self) -> None:
        t = PostgresTable(name="t", schema_name="public", primary_keys=["id", "name"])
        assert t.primary_keys == ["id", "name"]

    def test_with_row_count_and_owner(self) -> None:
        t = PostgresTable(name="t", schema_name="public", row_count=42, owner="postgres")
        assert t.row_count == 42
        assert t.owner == "postgres"


# ===========================================================================
# SyncStats dataclass
# ===========================================================================


class TestSyncStats:

    def test_defaults(self) -> None:
        s = SyncStats()
        assert s.schemas_synced == 0
        assert s.tables_new == 0
        assert s.errors == 0

    def test_to_dict(self) -> None:
        s = SyncStats(schemas_synced=2, tables_new=5, errors=1)
        d = s.to_dict()
        assert d == {"schemas_synced": 2, "tables_new": 5, "errors": 1}

    def test_log_summary(self) -> None:
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

    def test_connector_initializes(self) -> None:
        connector = _make_connector()
        assert connector.connector_id == "conn-pg-1"
        assert connector.connector_name == Connectors.POSTGRESQL
        assert connector.data_source is None

    def test_defaults(self) -> None:
        connector = _make_connector()
        assert connector.database_name is None
        assert connector.batch_size == 100
        assert connector.connector_scope == "team"
        assert connector.created_by is None

    def test_a_personal_connector_keeps_its_creator(self) -> None:
        # The factory passes both from the App doc; config never carries them,
        # and without the creator the owner never gets an edge to the app.
        logger, dep, dsp, cs = _make_mock_deps()
        connector = PostgreSQLConnector(
            logger=logger,
            data_entities_processor=dep,
            data_store_provider=dsp,
            config_service=cs,
            connector_id="conn-pg-1",
            scope="personal",
            created_by="user-7",
        )
        assert (connector.scope, connector.connector_scope, connector.created_by) == (
            "personal", "personal", "user-7",
        )

    def test_both_scopes_are_offered(self) -> None:
        metadata = PostgreSQLConnector._connector_metadata
        assert set(metadata["connectorScopes"]) == {"personal", "team"}

    def test_sync_can_be_scheduled(self) -> None:
        sync = PostgreSQLConnector._connector_metadata["config"]["sync"]
        assert sync["supportedStrategies"] == ["SCHEDULED", "MANUAL"]
        assert sync["scheduledConfig"]["intervalMinutes"] == 120

    def test_sync_stats_initialized(self) -> None:
        connector = _make_connector()
        assert isinstance(connector.sync_stats, SyncStats)
        assert connector.sync_stats.tables_new == 0
        assert connector.sync_stats.schemas_synced == 0

    def test_sync_outcome_starts_empty(self) -> None:
        connector = _make_connector()
        assert connector.sync_outcome == SyncOutcome()

    def test_sync_point_created(self) -> None:
        connector = _make_connector()
        assert connector.tables_sync_point is not None

    def test_filter_collections_initialized(self) -> None:
        connector = _make_connector()
        assert connector.sync_filters is not None
        assert connector.indexing_filters is not None

    def test_filter_caches_initialized_empty(self) -> None:
        connector = _make_connector()
        assert connector._schema_filter_cache == []
        assert connector._table_filter_cache == []
        assert connector._filter_cache_rebuild_event is None


# ===========================================================================
# PostgreSQLConnector.init
# ===========================================================================


class TestPostgresConnectorInitMethod:

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
    async def test_returns_true_on_success_with_individual_fields(self) -> None:
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
        assert connector.server_address == "localhost:5432"
        # A new connection may reach another database under the name: read its oid again.
        assert connector.database_oid is None

    @staticmethod
    async def _init_with(connector, auth) -> dict:
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
    async def test_connection_string_sslmode_is_used(self) -> None:
        # Ignoring it connected with "prefer": no certificate check, and a
        # silent fallback to an unencrypted connection.
        kwargs = await self._init_with(_make_connector(), {
            "connectionString": "postgresql://u:p@db.example:5432/app?sslmode=verify-full",
        })
        assert kwargs["sslmode"] == "verify-full"

    @pytest.mark.asyncio
    async def test_sslmode_defaults_to_prefer(self) -> None:
        kwargs = await self._init_with(_make_connector(), {
            "connectionString": "postgresql://u:p@db.example:5432/app",
        })
        assert kwargs["sslmode"] == "prefer"

    @pytest.mark.asyncio
    async def test_returns_true_on_success_with_connection_string(self) -> None:
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
    async def test_returns_false_on_invalid_connection_string(self) -> None:
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "connectionString": "not-a-valid-uri",
            },
        })
        result = await connector.init()
        assert result is False

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
    async def test_sets_created_by(self) -> None:
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
    async def test_default_port_when_not_specified(self) -> None:
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
            return_value=_pg_response(True, {"version": "PostgreSQL 16.1"})
        )
        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_api_failure(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.test_connection = AsyncMock(
            return_value=_pg_response(False, error="Connection refused")
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
# PostgreSQLConnector.get_signed_url
# ===========================================================================


class TestGetSignedUrl:

    def test_returns_none(self) -> None:
        connector = _make_connector()
        record = MagicMock()
        result = connector.get_signed_url(record)
        assert result is None


# ===========================================================================
# PostgreSQLConnector.handle_webhook_notification
# ===========================================================================


class TestHandleWebhookNotification:

    def test_raises_not_implemented(self) -> None:
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({"type": "test"})


# ===========================================================================
# PostgreSQLConnector.get_app_users
# ===========================================================================


class TestGetAppUsers:

    def test_converts_users(self) -> None:
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
        assert result[0].org_id == "org-pg-1"


# ===========================================================================
# PostgreSQLConnector._create_app_users
# ===========================================================================


class TestCreateAppUsers:

    @pytest.mark.asyncio
    async def test_team_scope_creates_team_app_edge(self) -> None:
        connector = _make_connector()
        connector.scope = "team"
        connector.data_entities_processor.ensure_team_app_edge = AsyncMock()

        await connector._create_app_users()

        connector.data_entities_processor.ensure_team_app_edge.assert_awaited_once_with(
            "conn-pg-1"
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
# PostgreSQLConnector._get_permissions
# ===========================================================================


class TestGetPermissions:

    @pytest.mark.asyncio
    async def test_returns_org_owner_permission(self) -> None:
        connector = _make_connector()
        perms = await connector._get_permissions()
        assert len(perms) == 1
        assert perms[0].entity_type.value == "ORG"

    @staticmethod
    def _personal(email: str | None) -> PostgreSQLConnector:
        connector = _make_connector()
        connector.scope = ConnectorScope.PERSONAL.value
        connector.created_by = "user-1"
        tx_store = MagicMock()
        tx_store.get_user_by_user_id = AsyncMock(return_value={"email": email} if email else None)
        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock(return_value=tx_store)
        transaction.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=transaction)
        return connector

    @pytest.mark.asyncio
    async def test_personal_scope_reaches_the_creator_only(self) -> None:
        perms = await self._personal("owner@example.com")._get_permissions()

        assert [(p.entity_type.value, p.email, p.external_id) for p in perms] == [
            ("USER", "owner@example.com", "user-1"),
        ]

    @pytest.mark.asyncio
    async def test_personal_scope_without_a_creator_email_reaches_no_one(self) -> None:
        assert await self._personal(None)._get_permissions() == []


# ===========================================================================
# PostgreSQLConnector._fetch_schemas
# ===========================================================================


class TestFetchSchemas:

    @pytest.mark.asyncio
    async def test_returns_schemas(self) -> None:
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
    async def test_raises_on_failure(self) -> None:
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


def _mock_table_metadata(connector, info=None, fks=None, pks=None) -> None:
    connector.data_source.get_table_info = AsyncMock(
        return_value=info or _pg_response(True, {"columns": [{"name": "id"}], "primary_keys": ["id"]})
    )
    connector.data_source.get_foreign_keys = AsyncMock(
        return_value=fks or _pg_response(True, [])
    )
    connector.data_source.get_primary_keys = AsyncMock(
        return_value=pks or _pg_response(True, [{"column_name": "id"}])
    )
    connector.data_source.fetch_column_statistics = AsyncMock(return_value=None)
    connector.data_source.fetch_table_row_and_column_stats = AsyncMock(return_value=(None, None))
    connector.data_source.profile_columns = AsyncMock(return_value={})


class TestListTableNames:

    @pytest.mark.asyncio
    async def test_returns_names(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(True, [{"name": "users"}, {"name": "orders"}])
        )
        assert await connector._list_table_names("public") == ["users", "orders"]

    @pytest.mark.asyncio
    async def test_raises_on_failure(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_tables = AsyncMock(
            return_value=_pg_response(False, error="Access denied")
        )
        with pytest.raises(RuntimeError, match="Access denied"):
            await connector._list_table_names("public")


class TestLoadTable:

    @pytest.mark.asyncio
    async def test_loads_columns_and_keys(self) -> None:
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
    async def test_raises_when_any_metadata_fails(self, failing) -> None:
        # Syncing the table anyway would index it as though it had no columns or keys.
        connector = _make_connector()
        connector.data_source = MagicMock()
        _mock_table_metadata(connector, **{failing: _pg_response(False, error="boom")})

        with pytest.raises(RuntimeError, match="boom"):
            await connector._load_table("public", "users")

    @pytest.mark.asyncio
    async def test_load_tables_skips_and_counts_failures(self) -> None:
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
# Column stats and value profiles
# ===========================================================================


def _profiled_connector(columns, statistics, stats=(None, None), profiles=None) -> PostgreSQLConnector:
    connector = _make_connector()
    connector.data_source = MagicMock()
    _mock_table_metadata(connector, info=_pg_response(True, {"columns": columns}))
    connector.data_source.fetch_column_statistics = AsyncMock(return_value=statistics)
    connector.data_source.fetch_table_row_and_column_stats = AsyncMock(return_value=stats)
    connector.data_source.profile_columns = AsyncMock(return_value=profiles or {})
    return connector


def _table_record(revision: str = "rev-1", **fields) -> SQLTableRecord:
    return SQLTableRecord(
        id="tbl-1",
        record_name="account",
        record_type=RecordType.SQL_TABLE,
        record_group_type=RecordGroupType.SQL_NAMESPACE.value,
        external_record_group_id="public",
        external_record_id="public.account",
        external_revision_id=revision,
        origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.POSTGRESQL,
        connector_id="conn-pg-1",
        mime_type=MimeTypes.SQL_TABLE.value,
        version=1,
        **fields,
    )


class TestProfileTable:

    @pytest.mark.asyncio
    async def test_small_table_gets_exact_stats_and_profiles(self) -> None:
        frequency = ColumnProfile(
            top_values=["POPLATEK MESICNE", "POPLATEK TYDNE"],
            top_value_frequencies=[0.926, 0.074],
            numeric_text=False,
            profiled_at=1700000000000,
        )
        stats_row = (4500, (4500, [{"name": "frequency", "distinct_count": 2, "null_count": 0,
                                    "cardinality_ratio": 2 / 4500}]))
        connector = _profiled_connector(
            [{"name": "frequency", "data_type": "text"}],
            TableColumnStatistics(reltuples=4500.0),
            stats=stats_row,
            profiles={"frequency": frequency},
        )

        table = await connector._load_table("public", "account", PostgresTableState(n_live_tup=4500))

        connector.data_source.fetch_table_row_and_column_stats.assert_awaited_once_with(
            "public", "account", ["frequency"]
        )
        kwargs = connector.data_source.profile_columns.await_args.kwargs
        assert kwargs["row_count"] == 4500
        assert kwargs["exact_stats"]["frequency"]["distinct_count"] == 2
        assert table.column_profiles == {"frequency": frequency}

        [record] = connector._build_column_records(_table_record(), table)
        assert record.top_values == ["POPLATEK MESICNE", "POPLATEK TYDNE"]
        assert record.top_value_frequencies == [0.926, 0.074]
        assert record.numeric_text is False
        assert record.profiled_at == 1700000000000
        assert record.distinct_count == 2

    @pytest.mark.asyncio
    async def test_large_table_skips_the_exact_scan(self) -> None:
        # COUNT(DISTINCT) over every column of a big table took minutes.
        columns = [{"name": f"c{i}", "data_type": "integer"} for i in range(10)]
        connector = _profiled_connector(columns, TableColumnStatistics(reltuples=1_000_000.0))

        table = await connector._load_table("public", "trans", PostgresTableState(n_live_tup=1_000_000))

        connector.data_source.fetch_table_row_and_column_stats.assert_not_awaited()
        kwargs = connector.data_source.profile_columns.await_args.kwargs
        assert kwargs["row_count"] == 1_000_000
        assert kwargs["exact_stats"] is None
        assert table.column_stats is None

    @pytest.mark.asyncio
    async def test_table_indexing_off_stores_no_values(self) -> None:
        connector = _profiled_connector(
            [{"name": "frequency", "data_type": "text"}], TableColumnStatistics(reltuples=4500.0),
        )
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        table = await connector._load_table("public", "account", PostgresTableState(n_live_tup=4500))

        connector.data_source.profile_columns.assert_not_awaited()
        assert table.column_profiles is None

    @pytest.mark.asyncio
    async def test_unknown_size_skips_the_exact_scan(self) -> None:
        connector = _profiled_connector([{"name": "a"}], None)

        table = await connector._load_table("public", "t")

        connector.data_source.fetch_table_row_and_column_stats.assert_not_awaited()
        connector.data_source.profile_columns.assert_not_awaited()
        assert table.column_profiles is None

    @pytest.mark.asyncio
    async def test_zero_estimates_are_unknown_not_empty(self) -> None:
        # n_live_tup is 0 after a stats reset and reltuples -1 before ANALYZE,
        # whatever the table holds: neither may green-light a full COUNT(DISTINCT).
        connector = _profiled_connector([{"name": "a"}], TableColumnStatistics(reltuples=-1.0))

        await connector._load_table("public", "t", PostgresTableState(n_live_tup=0))

        connector.data_source.fetch_table_row_and_column_stats.assert_not_awaited()
        assert connector.data_source.profile_columns.await_args.kwargs["row_count"] is None

    @pytest.mark.asyncio
    async def test_counted_empty_table_is_profiled_as_empty(self) -> None:
        # The estimates still remember the deleted rows; the count is what holds.
        connector = _profiled_connector(
            [{"name": "a"}],
            TableColumnStatistics(reltuples=5000.0),
            stats=(0, (0, [{"name": "a", "distinct_count": 0, "null_count": 0, "cardinality_ratio": None}])),
        )

        await connector._load_table("public", "t", PostgresTableState(n_live_tup=5000))

        assert connector.data_source.profile_columns.await_args.kwargs["row_count"] == 0

    @pytest.mark.asyncio
    async def test_columns_without_equality_are_not_counted(self) -> None:
        # COUNT(DISTINCT json) is an error that loses the stats of every column.
        connector = _profiled_connector(
            [{"name": "id", "udt_name": "int8"}, {"name": "doc", "udt_name": "json"},
             {"name": "pts", "udt_name": "_point"}],
            TableColumnStatistics(reltuples=10.0),
        )

        await connector._load_table("public", "t", PostgresTableState(n_live_tup=10))

        connector.data_source.fetch_table_row_and_column_stats.assert_awaited_once_with("public", "t", ["id"])

    @pytest.mark.asyncio
    async def test_profiling_failure_does_not_fail_the_table(self) -> None:
        connector = _profiled_connector([{"name": "a"}], None)
        connector.data_source.fetch_column_statistics = AsyncMock(side_effect=RuntimeError("boom"))

        table = await connector._load_table("public", "t", PostgresTableState(n_live_tup=5))

        assert [c.name for c in table.columns] == ["a"]
        assert table.column_profiles is None
        [record] = connector._build_column_records(_table_record(), table)
        assert record.top_values is None
        assert record.profiled_at is None
        assert record.external_revision_id == "rev-1"


class TestReanalyzedTables:

    @pytest.mark.asyncio
    async def test_only_column_records_are_saved(self) -> None:
        # A content update on the table record publishes updateRecord, which
        # would re-index every table autovacuum analyzed.
        connector = _profiled_connector([{"name": "a"}], TableColumnStatistics(reltuples=10.0))
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        state = PostgresTableState(column_hash="h", n_tup_ins=5, analyze_count=3,
                                   schema_name="public", table_name="t")
        existing = MagicMock(id="rec-1", external_revision_id=_table_revision(state), source_updated_at=1)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        connector._sync_table_columns = AsyncMock()

        synced = await connector._sync_reanalyzed_tables(["public.t"], {"public.t": state})

        assert synced == {"public.t"}
        connector._sync_table_columns.assert_awaited_once()
        record, table = connector._sync_table_columns.await_args.args
        assert record.id == "rec-1" and record.external_revision_id == _table_revision(state)
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_is_not_counted_as_synced(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._load_table = AsyncMock(side_effect=RuntimeError("gone"))
        state = PostgresTableState(schema_name="public", table_name="t")

        assert await connector._sync_reanalyzed_tables(["public.t"], {"public.t": state}) == set()
        assert connector.sync_stats.errors == 1

    def test_analyze_count_is_not_part_of_the_revision(self) -> None:
        a = PostgresTableState(column_hash="h", n_tup_ins=5, analyze_count=1)
        b = PostgresTableState(column_hash="h", n_tup_ins=5, analyze_count=2)
        assert a.revision() == b.revision()

    @pytest.mark.asyncio
    async def test_incremental_sync_reprofiles_analyzed_tables(self) -> None:
        stored = {
            "public.analyzed": PostgresTableState(column_hash="a", n_tup_ins=1, analyze_count=1),
            "public.analyzed_bad": PostgresTableState(column_hash="b", n_tup_ins=1, analyze_count=1),
            "public.written": PostgresTableState(column_hash="c", n_tup_ins=1, analyze_count=1),
        }
        current = {
            "public.analyzed": PostgresTableState(column_hash="a", n_tup_ins=1, analyze_count=2),
            "public.analyzed_bad": PostgresTableState(column_hash="b", n_tup_ins=1, analyze_count=2),
            "public.written": PostgresTableState(column_hash="c", n_tup_ins=2, analyze_count=2),
        }
        connector = TestRunIncrementalSync._incremental_connector(stored, current)
        connector._sync_reanalyzed_tables = AsyncMock(return_value={"public.analyzed"})

        await TestRunIncrementalSync._run(connector)

        assert connector._sync_changed_tables.call_args[0][0] == {"public.written": TableChange(data=True)}
        assert connector._sync_reanalyzed_tables.call_args[0][0] == ["public.analyzed", "public.analyzed_bad"]
        saved = connector._save_tables_sync_state.call_args[0][0]
        assert saved["public.analyzed"].analyze_count == 2
        # Kept at the old count so the next run tries again.
        assert saved["public.analyzed_bad"].analyze_count == 1

    @pytest.mark.asyncio
    async def test_table_states_carry_the_analyze_count(self) -> None:
        connector = _states_connector([_stat("public", "users", analyze_count=7)])

        states, _ = await connector._get_current_table_states(None)

        assert states["public.users"].analyze_count == 7


class TestColumnRevision:

    def test_without_stats_or_profile_is_the_table_revision(self) -> None:
        assert _column_revision("rev", {}, None) == "rev"

    def test_follows_the_profile_but_not_its_timestamp(self) -> None:
        # Records are rewritten only when their revision changes, so a profile
        # refreshed by ANALYZE would otherwise never reach the graph.
        a = ColumnProfile(top_values=["x"], top_value_frequencies=[1.0], profiled_at=1)
        b = ColumnProfile(top_values=["x"], top_value_frequencies=[1.0], profiled_at=2)
        c = ColumnProfile(top_values=["y"], top_value_frequencies=[1.0], profiled_at=1)

        assert _column_revision("rev", {}, a) == _column_revision("rev", {}, b)
        assert _column_revision("rev", {}, a) != _column_revision("rev", {}, c)
        assert _column_revision("rev", {}, a).startswith("rev:")
        assert _column_revision("rev", {"distinct_count": 3}, None) != "rev"


class TestSQLColumnRecordProfileRoundTrip:

    def test_profile_fields_round_trip(self) -> None:
        record = SQLColumnRecord(
            id="col-1",
            org_id="org-1",
            record_name="frequency",
            record_type=RecordType.SQL_COLUMN,
            external_record_id="db.public.account.frequency",
            origin=OriginTypes.CONNECTOR.value,
            connector_name=Connectors.POSTGRESQL,
            connector_id="conn-pg-1",
            mime_type=MimeTypes.SQL_COLUMN.value,
            version=1,
            fqn="db.public.account.frequency",
            table_fqn="public.account",
            top_values=["POPLATEK MESICNE", "POPLATEK TYDNE"],
            top_value_frequencies=[0.926, 0.074],
            min_value="1",
            max_value="9",
            numeric_text=True,
            profiled_at=1700000000000,
        )

        doc = record.to_arango_record()
        assert doc["topValues"] == ["POPLATEK MESICNE", "POPLATEK TYDNE"]
        assert doc["topValueFrequencies"] == [0.926, 0.074]
        assert (doc["minValue"], doc["maxValue"], doc["numericText"], doc["profiledAt"]) == (
            "1", "9", True, 1700000000000,
        )

        base = record.to_arango_base_record()
        restored = SQLColumnRecord.from_arango_record(doc, base)
        for field in ("top_values", "top_value_frequencies", "min_value", "max_value",
                      "numeric_text", "profiled_at"):
            assert getattr(restored, field) == getattr(record, field)

    def test_old_documents_have_no_profile(self) -> None:
        base = {
            "id": "col-1", "orgId": "org-1", "recordName": "c", "recordType": "SQL_COLUMN",
            "externalRecordId": "db.s.t.c", "version": 1, "origin": OriginTypes.CONNECTOR.value,
            "connectorId": "conn-pg-1", "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }
        restored = SQLColumnRecord.from_arango_record({"fqn": "db.s.t.c", "tableFqn": "s.t"}, base)
        assert restored.top_values is None and restored.profiled_at is None

    def test_arango_schema_accepts_profile_fields(self) -> None:
        # additionalProperties is False there: an unlisted key rejects the write.
        import jsonschema

        from app.schema.arango.documents import sql_column_record_schema

        record = SQLColumnRecord(
            id="col-1", org_id="org-1", record_name="c", record_type=RecordType.SQL_COLUMN,
            external_record_id="db.s.t.c", origin=OriginTypes.CONNECTOR.value,
            connector_name=Connectors.POSTGRESQL, connector_id="conn-pg-1",
            mime_type=MimeTypes.SQL_COLUMN.value, version=1, fqn="db.s.t.c", table_fqn="s.t",
            top_values=["a"], top_value_frequencies=[1.0], numeric_text=False, profiled_at=1,
        )
        doc = record.to_arango_record()
        doc.pop("_key")
        jsonschema.validate(doc, sql_column_record_schema["rule"])


# ===========================================================================
# PostgreSQLConnector._create_database_record_group
# ===========================================================================


class TestCreateDatabaseRecordGroup:

    @pytest.mark.asyncio
    async def test_creates_record_group(self) -> None:
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
    async def test_syncs_schemas(self) -> None:
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
    async def test_schema_named_after_database_gets_its_own_group(self) -> None:
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
    async def test_skips_empty_schemas_list(self) -> None:
        connector = _make_connector()
        await connector._sync_schemas([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()


# ===========================================================================
# PostgreSQLConnector._get_filter_values
# ===========================================================================


class TestGetFilterValues:

    def test_returns_none_none_when_no_filters(self) -> None:
        connector = _make_connector()
        schemas, schemas_op, tables, tables_op = connector._get_filter_values()
        assert schemas is None
        assert tables is None
        # Default operator when filter is absent is IN
        assert schemas_op == "in"
        assert tables_op == "in"

    def test_returns_filter_values(self) -> None:
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

    def test_returns_not_in_operator(self) -> None:
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
    async def test_syncs_tables_in_batches(self) -> None:
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
    async def test_skips_empty_tables_list(self) -> None:
        connector = _make_connector()
        await connector._sync_tables("public", [])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reports_the_tables_not_their_columns(self) -> None:
        # The batches hold each table's column records too; reporting those as
        # synced tables made incremental sync look them up as tables and fail.
        connector = _make_connector()
        connector.batch_size = 3
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        tables = [
            PostgresTable(name=name, schema_name="public", columns=[ColumnInfo(name="id"), ColumnInfo(name="note")])
            for name in ("a", "b")
        ]

        synced = await connector._sync_tables("public", tables)

        assert synced == {"public.a", "public.b"}


# ===========================================================================
# PostgreSQLConnector._process_tables_generator
# ===========================================================================


class TestProcessTablesGenerator:

    @pytest.mark.asyncio
    async def test_yields_records(self) -> None:
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
    async def test_adds_foreign_key_relations(self) -> None:
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
    async def test_sets_auto_index_off(self) -> None:
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = False

        tables = [PostgresTable(name="t1", schema_name="public")]
        records = []
        async for record, perms in connector._process_tables_generator("public", tables):
            records.append(record)

        assert records[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_existing_table_keeps_its_id_and_link(self) -> None:
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
    async def test_revision_comes_from_table_state(self) -> None:
        # A timestamp revision re-indexed every table on every full sync.
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        state = PostgresTableState(column_hash="h", n_tup_ins=5)
        existing = MagicMock(id="rec-1", external_revision_id=_table_revision(state), source_updated_at=777)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        tables = [PostgresTable(name="t1", schema_name="public", state=state)]
        records = [r async for r, _ in connector._process_tables_generator("public", tables)]

        assert records[0].external_revision_id == f"{state.structure()}:{state.revision()}"
        assert records[0].source_updated_at == 777

    def test_revision_ignores_live_row_estimate(self) -> None:
        a = PostgresTableState(column_hash="h", n_tup_ins=5, n_live_tup=10)
        b = PostgresTableState(column_hash="h", n_tup_ins=5, n_live_tup=99)
        c = PostgresTableState(column_hash="h", n_tup_ins=6, n_live_tup=10)
        assert a.revision() == b.revision()
        assert a.revision() != c.revision()

    @pytest.mark.asyncio
    async def test_table_in_schema_named_after_database_uses_its_group(self) -> None:
        connector = _make_connector()
        connector.database_name = "app"
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True

        tables = [PostgresTable(name="t1", schema_name="app")]
        records = [r async for r, _ in connector._process_tables_generator("app", tables)]

        assert records[0].external_record_group_id == "app.app"
        assert records[0].external_record_id == "app.t1"

    @pytest.mark.asyncio
    async def test_skips_fk_with_no_target_table(self) -> None:
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
    async def test_fk_uses_default_schema_when_not_specified(self) -> None:
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


class TestRestructuredTables:
    """The tables a sync reports as restructured: sync_outcome.restructured_tables."""

    @staticmethod
    def _state(**overrides) -> PostgresTableState:
        values = {"schema_name": "public", "table_name": "account", "column_hash": "cols",
                  "constraint_hash": "keys", "n_tup_ins": 5}
        return PostgresTableState(**{**values, **overrides})

    @staticmethod
    async def _build(existing, state) -> tuple:
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        table = PostgresTable(name="account", schema_name="public", state=state)
        record, _ = await connector._build_table_record("public", table)
        return connector, record

    @pytest.mark.asyncio
    async def test_new_table(self) -> None:
        connector, record = await self._build(None, self._state())

        assert connector.sync_outcome.restructured_tables == {"public.account"}

    @pytest.mark.asyncio
    async def test_placeholder_becoming_a_real_table(self) -> None:
        # A stand-in left by a table that references this one: nothing was ever
        # derived from it, whatever revision it carries.
        state = self._state()
        existing = _table_record(_table_revision(state), is_placeholder=True)

        connector, _ = await self._build(existing, state)

        assert connector.sync_outcome.restructured_tables == {"public.account"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("before", [
        {"column_hash": "other"},
        {"constraint_hash": "other"},
        {"table_name": "accounts_old"},
    ])
    async def test_changed_structure(self, before) -> None:
        existing = _table_record(_table_revision(self._state(**before)))

        connector, _ = await self._build(existing, self._state())

        assert connector.sync_outcome.restructured_tables == {"public.account"}

    @pytest.mark.asyncio
    async def test_changed_rows_alone_leave_the_structure_as_it_was(self) -> None:
        existing = _table_record(_table_revision(self._state(n_tup_ins=1, storage_id="1")))

        connector, record = await self._build(existing, self._state(n_tup_ins=9, storage_id="2"))

        assert connector.sync_outcome.restructured_tables == set()
        # The rows did change, so the table is still indexed again.
        assert record.external_revision_id != existing.external_revision_id

    @pytest.mark.asyncio
    async def test_legacy_revision_is_not_taken_for_a_change(self) -> None:
        # Saved before revisions carried the structure: counting it would
        # report every table as changed on upgrade.
        existing = _table_record(hashlib.md5(b"cols:5:0:0").hexdigest())

        connector, _ = await self._build(existing, self._state())

        assert connector.sync_outcome.restructured_tables == set()

    @pytest.mark.asyncio
    async def test_ids_are_those_of_the_records_saved(self) -> None:
        # A table saved after one that references it takes over the stand-in
        # that reference created, so its record id is the stand-in's, not the
        # one it was built with.
        connector = _make_connector()
        connector.sync_outcome.restructured_tables = {"public.orders", "public.gone"}
        saved = {"public.orders": _table_record().model_copy(update={"id": "stand-in-1"})}
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id, **_kw: saved.get(external_record_id),
        )

        await connector._resolve_restructured()

        assert connector.sync_outcome.restructured == {"stand-in-1"}
        # A graph error must not read as "no record": the table would never be reported.
        assert all(
            call.kwargs.get("raise_on_error") is True
            for call in connector.data_entities_processor.get_record_by_external_id.await_args_list
        )

    @pytest.mark.asyncio
    async def test_a_lookup_that_fails_once_is_tried_again(self) -> None:
        connector = _make_connector()
        connector.sync_outcome.restructured_tables = {"public.a"}
        answers = [RuntimeError("graph down"), _table_record().model_copy(update={"id": "rec-a"})]

        async def lookup(connector_id, external_record_id, **_kw):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=lookup)

        with patch("app.connectors.sources.postgres.connector.asyncio.sleep", AsyncMock()):
            await connector._resolve_restructured()

        assert connector.sync_outcome.restructured == {"rec-a"}

    @pytest.mark.asyncio
    async def test_a_lookup_that_keeps_failing_drops_only_that_table(self) -> None:
        connector = _make_connector()
        connector.sync_outcome.restructured_tables = {"public.a", "public.b"}

        async def lookup(connector_id, external_record_id, **_kw):
            if external_record_id == "public.a":
                raise RuntimeError("graph down")
            return _table_record().model_copy(update={"id": "rec-b"})

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=lookup)

        with patch("app.connectors.sources.postgres.connector.asyncio.sleep", AsyncMock()):
            await connector._resolve_restructured()

        assert connector.sync_outcome.restructured == {"rec-b"}
        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_a_sync_that_fails_part_way_still_reports_what_it_saved(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value=None)

        async def full_sync() -> None:
            connector.sync_outcome.restructured_tables.add("public.a")
            raise RuntimeError("lost the connection")

        connector._run_full_sync_internal = AsyncMock(side_effect=full_sync)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_table_record().model_copy(update={"id": "rec-a"}),
        )

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ), pytest.raises(RuntimeError):
            await connector.run_sync()

        assert connector.sync_outcome.restructured == {"rec-a"}

    def test_table_without_a_state_cannot_be_compared(self) -> None:
        existing = _table_record(_table_revision(self._state()))

        assert PostgreSQLConnector._is_restructured(existing, None) is False
        assert PostgreSQLConnector._is_restructured(None, None) is True


class TestRevisionsSavedBeforeAnUpgrade:
    """The first full sync after an upgrade must not re-index every table."""

    @staticmethod
    def _state(**overrides) -> PostgresTableState:
        values = {"schema_name": "public", "table_name": "account", "column_hash": "cols",
                  "constraint_hash": "keys", "n_tup_ins": 5, "storage_id": "7"}
        return PostgresTableState(**{**values, **overrides})

    @staticmethod
    async def _revision(existing, state, *, full_sync: bool) -> str:
        connector = _make_connector()
        connector._keep_legacy_revisions = full_sync
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        record, _ = await connector._build_table_record(
            "public", PostgresTable(name="account", schema_name="public", state=state),
        )
        return record.external_revision_id

    @pytest.mark.asyncio
    async def test_an_unchanged_table_keeps_its_old_revision(self) -> None:
        state = self._state()
        existing = _table_record(state.legacy_revision())

        assert await self._revision(existing, state, full_sync=True) == state.legacy_revision()

    @pytest.mark.asyncio
    async def test_a_table_whose_rows_changed_gets_a_new_one(self) -> None:
        existing = _table_record(self._state().legacy_revision())
        state = self._state(n_tup_ins=6)

        assert await self._revision(existing, state, full_sync=True) == _table_revision(state)

    @pytest.mark.asyncio
    async def test_an_incremental_sync_writes_a_new_one(self) -> None:
        # It reaches a table only when something changed, which may be what the
        # old revision cannot show (a constraint, a rename).
        state = self._state(constraint_hash="other")
        existing = _table_record(state.legacy_revision())

        assert await self._revision(existing, state, full_sync=False) == _table_revision(state)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("structure_changed", [True, False])
    async def test_the_first_change_after_an_upgrade_counts_when_the_diff_says_so(
        self, structure_changed: bool,
    ) -> None:
        # The kept revision cannot show the structure, so without the diff the
        # table would never be reported as restructured.
        state = self._state(constraint_hash="other")
        connector = _make_connector()
        connector._structure_changed = {"public.account"} if structure_changed else set()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_table_record(state.legacy_revision()),
        )

        await connector._build_table_record(
            "public", PostgresTable(name="account", schema_name="public", state=state),
        )

        assert connector.sync_outcome.restructured_tables == ({"public.account"} if structure_changed else set())

    @pytest.mark.asyncio
    async def test_a_full_sync_stops_keeping_them_when_it_ends(self) -> None:
        connector = _make_connector()
        connector._fetch_schemas = AsyncMock(side_effect=RuntimeError("down"))
        connector._create_app_users = AsyncMock()
        connector._create_database_record_group = AsyncMock()
        connector._get_current_table_states = AsyncMock(return_value=({}, set()))

        with pytest.raises(RuntimeError):
            await connector._run_full_sync_internal()

        assert connector._keep_legacy_revisions is False


# ===========================================================================
# PostgreSQLConnector.run_sync
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_runs_full_sync_when_no_stored_state(self) -> None:
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
    async def test_runs_incremental_sync_when_state_exists(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            connector.tables_sync_point.read_sync_point = AsyncMock(return_value={
                "table_states": "{}", "state_version": SYNC_STATE_VERSION, "database": "testdb",
                "database_oid": _DB_OID,
            })
            connector.run_incremental_sync = AsyncMock()

            await connector.run_sync()

            connector.run_incremental_sync.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("saved_for", [
        {"database": "otherdb", "server": "db1:5432", "database_oid": _DB_OID},
        # The same name on another server.
        {"database": "testdb", "server": "db2:5432", "database_oid": _DB_OID},
        # Dropped and created again under its name.
        {"database": "testdb", "server": "db1:5432", "database_oid": _DB_OID + 1},
        # Saved before the server and oid were kept.
        {"database": "testdb"},
    ])
    async def test_runs_full_sync_when_state_was_saved_for_another_database(self, saved_for) -> None:
        # Its table oids would match unrelated tables here, as renames.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.server_address = "db1:5432"

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            connector.tables_sync_point.read_sync_point = AsyncMock(return_value={
                "table_states": '{"public.t1": {}}', "state_version": SYNC_STATE_VERSION, **saved_for,
            })
            connector._run_full_sync_internal = AsyncMock()
            connector.run_incremental_sync = AsyncMock()

            await connector.run_sync()

        connector._run_full_sync_internal.assert_awaited_once()
        connector.run_incremental_sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_database_oid_is_read_once_per_connection(self) -> None:
        connector = _make_connector()
        connector.database_oid = None
        connector.data_source = MagicMock(get_database_oid=AsyncMock(return_value=_DB_OID))
        connector.database_name = "testdb"
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value={
            "table_states": "{}", "state_version": SYNC_STATE_VERSION, "database": "testdb", "database_oid": _DB_OID,
        })
        connector.run_incremental_sync = AsyncMock()

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await connector.run_sync()
            await connector.run_sync()

        connector.data_source.get_database_oid.assert_awaited_once()
        assert connector.run_incremental_sync.await_count == 2

    @pytest.mark.asyncio
    async def test_each_run_reports_only_what_it_changed(self) -> None:
        # The connector object outlives a sync; whatever runs after it
        # reads sync_outcome, so the last run's tables must not linger in it.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_outcome = SyncOutcome(
            restructured={"tbl-old"}, removed={"col-old"}, restructured_tables={"public.old"},
        )
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector._run_full_sync_internal = AsyncMock()

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(MagicMock(), MagicMock()),
        ):
            await connector.run_sync()

        assert connector.sync_outcome == SyncOutcome()

    @pytest.mark.asyncio
    async def test_a_sync_that_fails_before_it_starts_reports_nothing_of_the_last_one(self) -> None:
        # sync_outcome is read after a failed sync too; the last
        # run's tables would be described again.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_outcome = SyncOutcome(restructured={"tbl-old"}, restructured_tables={"public.old"})
        connector._structure_changed = {"public.old"}

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
            side_effect=RuntimeError("config store down"),
        ), pytest.raises(RuntimeError):
            await connector.run_sync()

        assert connector.sync_outcome == SyncOutcome()
        assert connector._structure_changed == set()

    @pytest.mark.asyncio
    async def test_runs_full_sync_when_state_is_from_an_older_version(self) -> None:
        # States saved by earlier versions may list tables that were never
        # synced; one full sync repairs what they left behind.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = (MagicMock(), MagicMock())
            connector.tables_sync_point.read_sync_point = AsyncMock(return_value={
                "table_states": '{"public.t1": {}}',
                "state_version": SYNC_STATE_VERSION - 1,
                "database": "testdb",
            })
            connector._run_full_sync_internal = AsyncMock()
            connector.run_incremental_sync = AsyncMock()

            await connector.run_sync()

            connector._run_full_sync_internal.assert_awaited_once()
            connector.run_incremental_sync.assert_not_awaited()


# ===========================================================================
# PostgreSQLConnector._run_full_sync_internal
# ===========================================================================


def _full_sync_connector(tables_by_schema, snapshot=None, synced=None) -> PostgreSQLConnector:
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
    connector._remove_dropped_columns = AsyncMock()
    connector._remove_stale_tables = AsyncMock(return_value={})
    connector._remove_stale_schema_groups = AsyncMock()
    connector._remove_database_group_self_links = AsyncMock()
    connector._save_tables_sync_state = AsyncMock()
    return connector


class TestRunFullSyncInternal:

    @pytest.mark.asyncio
    async def test_full_sync_flow(self) -> None:
        snapshot = {"public.t1": PostgresTableState(column_hash="h")}
        connector = _full_sync_connector({"public": ["t1"]}, snapshot)

        await connector._run_full_sync_internal()

        connector._create_app_users.assert_awaited_once()
        connector._create_database_record_group.assert_awaited_once()
        connector._sync_schemas.assert_awaited_once()
        connector._list_table_names.assert_awaited_once_with("public")
        connector._sync_tables.assert_awaited_once()
        connector._remove_dropped_columns.assert_awaited_once_with(
            "public", connector._sync_tables.await_args.args[1],
        )
        connector._remove_stale_tables.assert_awaited_once_with({"public.t1"})
        connector._save_tables_sync_state.assert_awaited_once_with(snapshot)
        assert connector.sync_stats.schemas_synced == 1
        assert connector.sync_stats.tables_new == 1

    @pytest.mark.asyncio
    async def test_full_sync_respects_schema_filter(self) -> None:
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
    async def test_full_sync_respects_table_filter(self) -> None:
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
    async def test_saved_state_lists_only_synced_tables(self) -> None:
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
    async def test_saved_state_keeps_stale_tables_whose_delete_failed(self) -> None:
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
    async def test_listing_failure_removes_and_saves_nothing(self) -> None:
        connector = _full_sync_connector({"public": ["t1"]})
        connector._list_table_names = AsyncMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError):
            await connector._run_full_sync_internal()

        connector._remove_stale_tables.assert_not_awaited()
        connector._save_tables_sync_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_increments_error_count_on_failure(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector._create_app_users = AsyncMock(side_effect=Exception("fail"))

        with pytest.raises(Exception):
            await connector._run_full_sync_internal()

        assert connector.sync_stats.errors == 1


class TestRemoveDroppedColumns:
    """A full sync saves each table's current columns; the ones it no longer has are left over."""

    @staticmethod
    def _connector(*records) -> PostgreSQLConnector:
        connector = _make_connector()
        connector.database_name = "testdb"
        _record_store(connector, *records)
        return connector

    @staticmethod
    def _table(name: str, *columns: str) -> PostgresTable:
        return PostgresTable(name=name, schema_name="public", columns=[ColumnInfo(name=c) for c in columns])

    @pytest.mark.asyncio
    async def test_columns_that_are_gone_are_removed_in_one_call_with_their_table(self) -> None:
        connector = self._connector(
            _stored("col-id", "testdb.public.account.id", "public.account"),
            _stored("col-a", "testdb.public.account.a", "public.account"),
            _stored("col-b", "testdb.public.account.b", "public.account"),
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_table_record())
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(return_value=["col-a", "col-b"])

        await connector._remove_dropped_columns("public", [self._table("account", "id")])

        connector.data_entities_processor.get_record_by_external_id.assert_awaited_once_with(
            connector_id="conn-pg-1", external_record_id="public.account",
        )
        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once_with(
            ["col-a", "col-b"], "conn-pg-1", table_record_id="tbl-1",
        )
        assert connector.sync_outcome.removed == {"col-a", "col-b"}
        assert connector.sync_stats.errors == 0

    @pytest.mark.asyncio
    async def test_table_with_all_its_columns_is_not_even_looked_up(self) -> None:
        connector = self._connector(
            _stored("col-id", "testdb.public.account.id", "public.account"),
        )

        await connector._remove_dropped_columns("public", [self._table("account", "id", "added")])

        connector.data_entities_processor.get_record_by_external_id.assert_not_awaited()
        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure_on_one_table_is_counted_and_the_next_is_still_handled(self) -> None:
        connector = self._connector(
            _stored("col-a", "testdb.public.first.a", "public.first"),
            _stored("col-b", "testdb.public.second.b", "public.second"),
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            side_effect=[RuntimeError("graph down"), ["col-b"]]
        )

        await connector._remove_dropped_columns(
            "public", [self._table("first", "id"), self._table("second", "id")],
        )

        assert connector.sync_stats.errors == 1
        removed = connector.data_entities_processor.on_sql_records_deleted.await_args_list
        assert [call.args[0] for call in removed] == [["col-a"], ["col-b"]]
        assert connector.sync_outcome.removed == {"col-b"}


# ===========================================================================
# PostgreSQLConnector._sync_updated_tables
# ===========================================================================


class TestSyncUpdatedTables:

    @pytest.mark.asyncio
    async def test_skips_empty_list(self) -> None:
        connector = _make_connector()
        await connector._sync_updated_tables("public", [])
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_record(self) -> None:
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
    async def test_creates_record_when_none_exists(self) -> None:
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
    async def test_continues_on_error(self) -> None:
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
    @pytest.mark.parametrize(("change", "refreshed"), [
        (TableChange(previous_fqn="public.people", structure=True), True),
        (TableChange(structure=True, column_renames={"name": "full_name"}), True),
        (TableChange(structure=True), False),
        (None, False),
    ])
    async def test_the_processor_is_told_of_a_rename(self, change, refreshed) -> None:
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_table_record().model_copy(update={"id": "rec-1"}),
        )
        connector._sync_table_columns = AsyncMock()
        changes = {"public.users": change} if change else None

        refresh = connector.data_entities_processor.on_sql_table_renamed
        await connector._sync_updated_tables("public", [PostgresTable(name="users", schema_name="public")], changes)

        if refreshed:
            refresh.assert_awaited_once_with("rec-1")
        else:
            refresh.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("change", "renamed", "relations_only"), [
        (TableChange(previous_fqn="public.people", structure=True, names_only=True), True, True),
        (TableChange(structure=True, column_renames={"name": "full_name"}, names_only=True), True, True),
        # Moved by a run whose sync of it failed.
        (TableChange(structure=True, names_only=True, rename_pending=True), True, True),
        # Restructured as well: its relations follow from its structure.
        (TableChange(previous_fqn="public.people", structure=True), True, False),
        # A constraint, or a table it references, renamed: its own names are as they were.
        (TableChange(structure=True, names_only=True), False, False),
    ], ids=["table", "column", "unfinished", "restructured", "constraint"])
    async def test_a_renamed_table_is_reported_as_renamed(self, change, renamed, relations_only) -> None:
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_table_record().model_copy(update={"id": "rec-1"}),
        )
        connector._sync_table_columns = AsyncMock()

        await connector._sync_updated_tables(
            "public", [PostgresTable(name="users", schema_name="public")], {"public.users": change},
        )

        assert connector.sync_outcome.renamed == ({"rec-1"} if renamed else set())
        assert connector.sync_outcome.relations_only == ({"rec-1"} if relations_only else set())

    @pytest.mark.asyncio
    async def test_a_rename_the_processor_cannot_handle_does_not_fail_the_table(self) -> None:
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled.return_value = True
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_table_record().model_copy(update={"id": "rec-1"}),
        )
        connector._sync_table_columns = AsyncMock()
        changes = {"public.users": TableChange(previous_fqn="public.people", structure=True)}

        connector.data_entities_processor.on_sql_table_renamed = AsyncMock(side_effect=RuntimeError("graph down"))
        synced = await connector._sync_updated_tables(
            "public", [PostgresTable(name="users", schema_name="public")], changes,
        )

        assert synced == {"public.users"}
        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_adds_foreign_keys_to_updated_record(self) -> None:
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

    @pytest.mark.asyncio
    async def test_column_renames_of_a_changed_table_reach_its_columns(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_table_record()
        )
        connector._sync_table_columns = AsyncMock()
        users = PostgresTable(name="users", schema_name="public")
        orders = PostgresTable(name="orders", schema_name="public")
        changes = {"public.users": TableChange(structure=True, column_renames={"status": "state"})}

        await connector._sync_updated_tables("public", [users, orders], changes)

        renames = [call.args[2] for call in connector._sync_table_columns.await_args_list]
        assert renames == [{"status": "state"}, None]


# ===========================================================================
# PostgreSQLConnector._sync_table_columns / _rename_column_records
# ===========================================================================


class TestSyncTableColumns:

    @staticmethod
    def _connector() -> PostgreSQLConnector:
        connector = _make_connector()
        connector.database_name = "testdb"
        return connector

    @staticmethod
    def _table(*columns: str) -> PostgresTable:
        return PostgresTable(
            name="account", schema_name="public", columns=[ColumnInfo(name=c) for c in columns],
        )

    @pytest.mark.asyncio
    async def test_dropped_columns_are_removed_in_one_call_with_their_table(self) -> None:
        # The table id tells the processor whose columns these are.
        connector = self._connector()
        _record_store(
            connector,
            _stored("col-id", "testdb.public.account.id", "public.account"),
            _stored("col-a", "testdb.public.account.a", "public.account"),
            _stored("col-b", "testdb.public.account.b", "public.account"),
        )
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(return_value=["col-a", "col-b"])

        await connector._sync_table_columns(_table_record(), self._table("id"))

        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once_with(
            ["col-a", "col-b"], "conn-pg-1", table_record_id="tbl-1",
        )
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()
        assert connector.sync_outcome.removed == {"col-a", "col-b"}
        saved = connector.data_entities_processor.on_new_records.await_args.args[0]
        assert [record.external_record_id for record, _ in saved] == ["testdb.public.account.id"]

    @pytest.mark.asyncio
    async def test_nothing_is_removed_when_every_column_is_still_there(self) -> None:
        connector = self._connector()
        _record_store(connector, _stored("col-id", "testdb.public.account.id", "public.account"))

        await connector._sync_table_columns(_table_record(), self._table("id", "added"))

        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()
        saved = connector.data_entities_processor.on_new_records.await_args.args[0]
        assert [record.record_name for record, _ in saved] == ["id", "added"]

    @pytest.mark.asyncio
    async def test_renamed_column_keeps_its_record(self) -> None:
        # Moved before the dropped columns are worked out; otherwise the old
        # name reads as dropped and the notes on the column go with it.
        connector = self._connector()
        store = _record_store(
            connector,
            _stored("col-id", "testdb.public.account.id", "public.account"),
            _stored("col-status", "testdb.public.account.status", "public.account"),
        )

        await connector._sync_table_columns(
            _table_record(), self._table("id", "state"), {"status": "state"},
        )

        assert store.updates == [
            ([{"id": "col-status", "externalRecordId": "testdb.public.account.state"}], "records"),
        ]
        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()


class TestRenameColumnRecords:

    @staticmethod
    def _connector(*records) -> tuple:
        connector = _make_connector()
        connector.database_name = "testdb"
        return connector, _record_store(connector, *records)

    @pytest.mark.asyncio
    async def test_no_renames_reads_nothing(self) -> None:
        connector, _ = self._connector()

        await connector._rename_column_records("public.account", {})

        connector.data_store_provider.transaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_column_renamed_into_a_dropped_columns_name_takes_it(self) -> None:
        # ALTER TABLE ... DROP COLUMN taken; RENAME COLUMN b TO taken
        connector, store = self._connector(
            _stored("col-a", "testdb.public.account.a", "public.account"),
            _stored("col-b", "testdb.public.account.b", "public.account"),
            _stored("col-taken", "testdb.public.account.taken", "public.account"),
        )
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            side_effect=lambda ids, *args, **kwargs: ids,
        )

        await connector._rename_column_records(
            "public.account", {"a": "a2", "b": "taken", "never_stored": "x"}, table_record_id="tbl-1",
        )

        # The dropped column's record goes; b keeps its own.
        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once_with(
            ["col-taken"], "conn-pg-1", table_record_id="tbl-1",
        )
        assert store.updates == [([
            {"id": "col-a", "externalRecordId": "testdb.public.account.a2"},
            {"id": "col-b", "externalRecordId": "testdb.public.account.taken"},
        ], "records")]

    @pytest.mark.asyncio
    async def test_a_column_moved_by_a_run_that_failed_is_not_taken_for_a_dropped_one(self) -> None:
        # The record already sits under the new name, and none under the old one.
        connector, store = self._connector(
            _stored("col-b", "testdb.public.account.taken", "public.account"),
        )
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock()

        await connector._rename_column_records("public.account", {"b": "taken"}, table_record_id="tbl-1")

        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()
        assert store.updates == []

    @pytest.mark.asyncio
    async def test_columns_that_moved_are_saved_under_their_new_names_at_once(self) -> None:
        # Swapped columns: renamed again by a run after one cut off, they would trade records.
        connector, store = self._connector(
            _stored("col-id", "testdb.public.account.id", "public.account"),
            _stored("col-uuid", "testdb.public.account.uuid", "public.account"),
        )
        before = PostgresTableState(schema_name="public", table_name="account", oid=5, columns={"1": "id", "2": "uuid"})
        connector._checkpoint = {"public.account": before}
        connector._save_tables_sync_state = AsyncMock()

        await connector._rename_column_records("public.account", {"id": "legacy_id", "uuid": "id"})

        saved = connector._checkpoint["public.account"]
        assert saved.columns == {"1": "legacy_id", "2": "id"}
        assert saved.renamed_from == "public.account"
        connector._save_tables_sync_state.assert_awaited_once_with(connector._checkpoint, checkpoint=True)
        # The next run finds no column to rename, and syncs the table again.
        now = before.model_copy(update={"columns": {"1": "legacy_id", "2": "id"}, "column_hash": "new"})
        change = diff_catalog({"public.account": saved}, {"public.account": now}).changed["public.account"]
        assert change.column_renames == {} and change.rename_pending

    @pytest.mark.asyncio
    async def test_columns_that_swapped_names_keep_their_records(self) -> None:
        # RENAME COLUMN id TO legacy_id; RENAME COLUMN uuid TO id
        connector, store = self._connector(
            _stored("col-id", "testdb.public.account.id", "public.account"),
            _stored("col-uuid", "testdb.public.account.uuid", "public.account"),
        )

        await connector._rename_column_records("public.account", {"id": "legacy_id", "uuid": "id"})

        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()
        assert store.updates == [([
            {"id": "col-id", "externalRecordId": "testdb.public.account.legacy_id"},
            {"id": "col-uuid", "externalRecordId": "testdb.public.account.id"},
        ], "records")]

    @pytest.mark.asyncio
    async def test_nothing_to_move_writes_nothing(self) -> None:
        connector, store = self._connector(
            _stored("col-a", "testdb.public.account.a", "public.account"),
        )

        await connector._rename_column_records("public.account", {"never_stored": "x"})

        assert store.updates == []


# ===========================================================================
# PostgreSQLConnector._compute_column_hash
# ===========================================================================


class TestColumnHash:

    def test_same_columns_produce_same_hash(self) -> None:
        cols = [ColumnInfo(name="id", data_type="integer"), ColumnInfo(name="name", data_type="text")]
        assert _column_hash(cols) == _column_hash([c.model_copy() for c in cols])
        assert len(_column_hash(cols)) == 32

    def test_different_columns_produce_different_hash(self) -> None:
        assert _column_hash([ColumnInfo(name="id")]) != _column_hash(
            [ColumnInfo(name="id"), ColumnInfo(name="extra")]
        )


# ===========================================================================
# PostgreSQLConnector.run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:

    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_incremental_sync()

    @pytest.mark.asyncio
    async def test_falls_back_to_full_sync_when_no_state(self) -> None:
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
    def _incremental_connector(stored, current, unreadable=frozenset()) -> PostgreSQLConnector:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value={
            "state_version": SYNC_STATE_VERSION,
            "database": "testdb",
            "database_oid": _DB_OID,
            "table_states": json.dumps({k: v.model_dump() for k, v in stored.items()}),
        })
        connector._get_current_table_states = AsyncMock(return_value=(current, set(unreadable)))
        # Nothing is listed unless a test says otherwise: stats-missing tables are gone.
        connector._list_table_names = AsyncMock(return_value=[])
        connector._sync_new_tables = AsyncMock(side_effect=lambda fqns, states: set(fqns))
        connector._sync_changed_tables = AsyncMock(side_effect=lambda changes, states: set(changes))
        connector._handle_deleted_tables = AsyncMock(side_effect=lambda fqns: set(fqns))
        connector._relink_referencing_tables = AsyncMock()
        connector._remove_stale_schema_groups = AsyncMock()
        connector._save_tables_sync_state = AsyncMock()
        return connector

    @staticmethod
    async def _run(connector) -> None:
        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            await connector.run_incremental_sync()

    @pytest.mark.asyncio
    async def test_a_change_of_names_alone_is_not_a_change_of_structure(self) -> None:
        stored = {
            "public.people": PostgresTableState(schema_name="public", table_name="people", oid=7,
                                                columns={"1": "id"}, shape="s1"),
            "public.orders": PostgresTableState(schema_name="public", table_name="orders", oid=8,
                                                columns={"1": "id"}, shape="s2"),
        }
        current = {
            "public.users": PostgresTableState(schema_name="public", table_name="users", oid=7,
                                               columns={"1": "id"}, shape="s1"),
            "public.orders": PostgresTableState(schema_name="public", table_name="orders", oid=8,
                                                columns={"1": "id"}, column_hash="other", shape="s3"),
        }
        connector = self._incremental_connector(stored, current)
        connector._rename_tables = AsyncMock(return_value=set())

        await self._run(connector)

        assert connector._names_changed == {"public.users"}
        assert connector._structure_changed == {"public.orders"}

    @pytest.mark.asyncio
    async def test_detects_new_changed_deleted_tables(self) -> None:
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
        assert connector._sync_changed_tables.call_args[0][0] == {
            "public.existing": TableChange(structure=True),
        }
        assert connector._handle_deleted_tables.call_args[0][0] == ["public.deleted"]
        connector._save_tables_sync_state.assert_awaited_once_with(current)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("counter", ["n_tup_ins", "n_tup_upd", "n_tup_del"])
    @pytest.mark.parametrize("now", [15, 2])
    async def test_any_write_counter_that_moved_resyncs_the_table(self, counter, now) -> None:
        # Down as well as up: a counter that fell means the statistics were
        # reset, which says nothing about the rows.
        stored = {"public.t1": PostgresTableState(column_hash="a", **{counter: 10})}
        current = {"public.t1": PostgresTableState(column_hash="a", **{counter: now})}
        connector = self._incremental_connector(stored, current)

        await self._run(connector)

        assert connector._sync_changed_tables.call_args[0][0] == {"public.t1": TableChange(data=True)}

    @pytest.mark.asyncio
    async def test_unchanged_table_is_not_synced_again(self) -> None:
        state = PostgresTableState(column_hash="a", n_tup_ins=10, n_tup_upd=5, n_tup_del=2)
        connector = self._incremental_connector({"public.t1": state}, {"public.t1": state.model_copy()})

        await self._run(connector)

        assert connector._sync_changed_tables.call_args[0][0] == {}
        connector._save_tables_sync_state.assert_awaited_once_with({"public.t1": state})

    @pytest.mark.asyncio
    async def test_stats_failure_deletes_nothing(self) -> None:
        # Returning {} here used to mark every stored table as dropped.
        stored = {"public.t1": PostgresTableState(column_hash="a")}
        connector = self._incremental_connector(stored, {})
        connector._get_current_table_states = AsyncMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError):
            await self._run(connector)

        connector._handle_deleted_tables.assert_not_awaited()
        connector._save_tables_sync_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unreadable_table_is_neither_dropped_nor_new(self) -> None:
        stored = {"public.t1": PostgresTableState(column_hash="a")}
        connector = self._incremental_connector(stored, {}, unreadable={"public.t1"})

        await self._run(connector)

        assert connector._handle_deleted_tables.call_args[0][0] == []
        assert connector._sync_new_tables.call_args[0][0] == []
        connector._save_tables_sync_state.assert_awaited_once_with(stored)

    @pytest.mark.asyncio
    async def test_saved_state_retries_what_failed(self) -> None:
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

    @staticmethod
    def _renamed(extra_current=None) -> tuple:
        """public.old became public.new (same oid); returns (stored, current)."""
        columns = {"1": "id", "2": "total"}
        stored = {
            "public.old": PostgresTableState(
                column_hash="a", oid=7, schema_name="public", table_name="old", columns=columns,
            ),
        }
        current = {
            "public.new": PostgresTableState(
                column_hash="a", oid=7, schema_name="public", table_name="new", columns=columns,
            ),
            **(extra_current or {}),
        }
        return stored, current

    @pytest.mark.asyncio
    async def test_renamed_table_is_moved_before_anything_is_synced(self) -> None:
        # A new table that references the renamed one would otherwise leave a
        # stand-in under the new name, and the rename would find it taken.
        stored, current = self._renamed({
            "public.extra": PostgresTableState(column_hash="b", oid=8, schema_name="public", table_name="extra"),
        })
        connector = self._incremental_connector(stored, current)
        steps = []
        connector._rename_tables = AsyncMock(
            side_effect=lambda renamed, blocked=frozenset(): steps.append(("rename", dict(renamed))) or set()
        )
        connector._sync_new_tables = AsyncMock(
            side_effect=lambda fqns, states: steps.append(("new", list(fqns))) or set(fqns)
        )
        connector._sync_changed_tables = AsyncMock(
            side_effect=lambda changes, states: steps.append(("changed", dict(changes))) or set(changes)
        )

        await self._run(connector)

        assert steps == [
            ("rename", {"public.old": "public.new"}),
            ("new", ["public.extra"]),
            ("changed", {"public.new": TableChange(previous_fqn="public.old", structure=True)}),
        ]
        # Renamed, not dropped: its record is not deleted.
        assert connector._handle_deleted_tables.call_args[0][0] == []
        checkpoints, final = _saves(connector)
        assert final == [current]
        # Saved once the record moved, before anything else was synced.
        assert set(checkpoints[0]) == {"public.new"}
        assert checkpoints[0]["public.new"].renamed_from == "public.old"

    @pytest.mark.asyncio
    async def test_a_dropped_table_whose_name_a_renamed_one_took_is_removed_first(self) -> None:
        # DROP TABLE public.new; ALTER TABLE public.old RENAME TO new
        stored, current = self._renamed()
        stored["public.new"] = PostgresTableState(
            column_hash="z", oid=9, schema_name="public", table_name="new", columns={"1": "x"},
        )
        connector = self._incremental_connector(stored, current)
        steps = []
        connector._handle_deleted_tables = AsyncMock(
            side_effect=lambda fqns: steps.append(("delete", list(fqns))) or set(fqns)
        )
        connector._rename_tables = AsyncMock(
            side_effect=lambda renamed, blocked=frozenset(): steps.append(("rename", dict(renamed), set(blocked))) or set()
        )

        await self._run(connector)

        assert steps[:2] == [("delete", ["public.new"]), ("rename", {"public.old": "public.new"}, set())]
        checkpoints, final = _saves(connector)
        assert final == [{"public.new": current["public.new"]}]
        # The dropped table's state is gone and the renamed one's is under the name it took.
        assert checkpoints == [{"public.new": stored["public.old"].model_copy(
            update={"table_name": "new", "renamed_from": "public.old"},
        )}]

    @pytest.mark.asyncio
    async def test_a_run_cut_off_after_tables_moved_does_not_move_them_again(self) -> None:
        # Swapped names: a second move would trade the records back.
        a = PostgresTableState(column_hash="a", oid=1, schema_name="public", table_name="a", columns={"1": "x"})
        b = PostgresTableState(column_hash="b", oid=2, schema_name="public", table_name="b", columns={"1": "y"})
        stored = {"public.a": a, "public.b": b}
        current = {
            "public.a": b.model_copy(update={"table_name": "a"}),
            "public.b": a.model_copy(update={"table_name": "b"}),
        }
        connector = self._incremental_connector(stored, current)
        connector._rename_tables = AsyncMock(return_value=set())
        connector._sync_changed_tables = AsyncMock(side_effect=RuntimeError("cut off"))

        with pytest.raises(RuntimeError):
            await self._run(connector)

        [checkpoint], final = _saves(connector)
        assert final == []
        assert checkpoint["public.a"] == b.model_copy(update={"table_name": "a", "renamed_from": "public.b"})
        assert checkpoint["public.b"] == a.model_copy(update={"table_name": "b", "renamed_from": "public.a"})
        # The next run finds each table where its record is, and finishes the rename.
        again = diff_catalog(checkpoint, current)
        assert again.renamed == {}
        assert all(change.rename_pending for change in again.changed.values())

    @pytest.mark.asyncio
    async def test_a_table_whose_columns_moved_but_that_did_not_sync_keeps_their_new_names(self) -> None:
        # Saved with the old ones, the next run would rename the moved records again.
        before = PostgresTableState(column_hash="a", oid=3, schema_name="public", table_name="t", columns={"1": "id"})
        after = before.model_copy(update={"column_hash": "b", "columns": {"1": "key"}})
        connector = self._incremental_connector({"public.t": before}, {"public.t": after})

        async def columns_moved_then_failed(changes, states):
            connector._checkpoint["public.t"] = before.model_copy(
                update={"columns": {"1": "key"}, "renamed_from": "public.t"},
            )
            return set()

        connector._sync_changed_tables = AsyncMock(side_effect=columns_moved_then_failed)

        await self._run(connector)

        _, [final] = _saves(connector)
        assert final["public.t"].columns == {"1": "key"}
        assert final["public.t"].renamed_from == "public.t"

    @pytest.mark.asyncio
    async def test_a_checkpoint_that_cannot_be_saved_does_not_stop_the_run(self) -> None:
        stored, current = self._renamed()
        connector = self._incremental_connector(stored, current)
        connector._rename_tables = AsyncMock(return_value=set())
        saves = []

        async def save(states, *, checkpoint=False) -> None:
            saves.append(checkpoint)
            if checkpoint:
                raise ConnectionError("graph down")

        connector._save_tables_sync_state = AsyncMock(side_effect=save)

        with patch("app.connectors.sources.postgres.connector.asyncio.sleep", AsyncMock()):
            await self._run(connector)

        assert saves == [True, True, True, False]
        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_a_rename_waits_while_the_dropped_table_in_its_name_is_still_there(self) -> None:
        stored, current = self._renamed()
        stored["public.new"] = PostgresTableState(
            column_hash="z", oid=9, schema_name="public", table_name="new", columns={"1": "x"},
        )
        connector = self._incremental_connector(stored, current)
        connector._handle_deleted_tables = AsyncMock(return_value=set())
        connector._rename_tables = AsyncMock(
            side_effect=lambda renamed, blocked=frozenset(): set(blocked) & set(renamed.values())
        )

        await self._run(connector)

        assert connector._rename_tables.await_args.kwargs["blocked"] == {"public.new"}
        assert connector._sync_changed_tables.call_args[0][0] == {}
        # Both are found the same way next run.
        connector._save_tables_sync_state.assert_awaited_once_with(
            {"public.old": stored["public.old"], "public.new": stored["public.new"]},
        )

    @pytest.mark.asyncio
    async def test_a_new_table_does_not_take_a_name_its_old_owner_still_holds(self) -> None:
        # ALTER TABLE a RENAME TO a_old (failed to move); CREATE TABLE a
        stored = {"public.a": PostgresTableState(
            column_hash="c", oid=1, schema_name="public", table_name="a", columns={"1": "id"},
        )}
        current = {
            "public.a_old": PostgresTableState(
                column_hash="c", oid=1, schema_name="public", table_name="a_old", columns={"1": "id"},
            ),
            "public.a": PostgresTableState(column_hash="d", oid=3, schema_name="public", table_name="a"),
        }
        connector = self._incremental_connector(stored, current)
        connector._rename_tables = AsyncMock(return_value={"public.a_old"})

        await self._run(connector)

        # Its record would sit beside the old table's, which is still under "a".
        assert connector._sync_new_tables.call_args[0][0] == []
        connector._save_tables_sync_state.assert_awaited_once_with({"public.a": stored["public.a"]})

    @pytest.mark.asyncio
    async def test_tables_that_point_at_a_table_that_came_back_get_their_keys_again(self) -> None:
        stored = {"public.orders": PostgresTableState(column_hash="o", oid=6, n_tup_ins=1)}
        current = {
            "public.orders": PostgresTableState(column_hash="o", oid=6, n_tup_ins=1),
            "public.customers": PostgresTableState(column_hash="c", oid=5),
        }
        connector = self._incremental_connector(stored, current)

        await self._run(connector)

        connector._relink_referencing_tables.assert_awaited_once_with(
            {"public.customers"}, current, skip={"public.customers"},
        )

    @pytest.mark.asyncio
    async def test_failed_rename_keeps_the_stored_state_under_the_old_name(self) -> None:
        stored, current = self._renamed()
        connector = self._incremental_connector(stored, current)
        connector._rename_tables = AsyncMock(return_value={"public.new"})

        await self._run(connector)

        # Syncing it under the new name would create a second record for the table.
        assert connector._sync_changed_tables.call_args[0][0] == {}
        # The next run compares against this and finds the same rename.
        connector._save_tables_sync_state.assert_awaited_once_with({"public.old": stored["public.old"]})

    @pytest.mark.asyncio
    async def test_renamed_table_that_fails_to_sync_is_saved_under_the_old_name(self) -> None:
        stored, current = self._renamed()
        connector = self._incremental_connector(stored, current)
        connector._rename_tables = AsyncMock(return_value={"public.new"})
        connector._sync_changed_tables = AsyncMock(return_value=set())

        await self._run(connector)

        connector._save_tables_sync_state.assert_awaited_once_with({"public.old": stored["public.old"]})

    @pytest.mark.asyncio
    async def test_a_moved_table_that_fails_to_sync_is_saved_under_its_new_name(self) -> None:
        # A table created under the old name since would otherwise be taken for
        # this one next run, and its record moved.
        stored, current = self._renamed({
            "public.old": PostgresTableState(column_hash="n", oid=9, schema_name="public", table_name="old"),
        })
        connector = self._incremental_connector(stored, current)
        connector._rename_tables = AsyncMock(return_value=set())
        connector._sync_changed_tables = AsyncMock(return_value=set())

        await self._run(connector)

        [saved] = connector._save_tables_sync_state.await_args.args
        assert saved["public.old"] == current["public.old"]
        # With where it came from: the next run syncs it again and finishes the rename.
        assert saved["public.new"] == stored["public.old"].model_copy(
            update={"table_name": "new", "renamed_from": "public.old"},
        )

    @pytest.mark.asyncio
    async def test_schema_groups_are_cleaned_after_a_rename(self) -> None:
        # A table moved out of a schema can leave that schema's group empty.
        stored, current = self._renamed()
        connector = self._incremental_connector(stored, current)
        connector._rename_tables = AsyncMock(return_value=set())

        await self._run(connector)

        connector._remove_stale_schema_groups.assert_awaited_once_with(
            filters=connector._get_filter_values()
        )

    @pytest.mark.asyncio
    async def test_schema_groups_are_left_alone_when_nothing_was_removed_or_renamed(self) -> None:
        stored = {"public.t1": PostgresTableState(column_hash="a", n_tup_ins=1)}
        current = {"public.t1": PostgresTableState(column_hash="a", n_tup_ins=2)}
        connector = self._incremental_connector(stored, current)

        await self._run(connector)

        connector._remove_stale_schema_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_stats_do_not_delete_tables_that_are_still_listed(self) -> None:
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
    async def test_table_excluded_by_filters_is_dropped_without_listing(self) -> None:
        stored = {"secret.pay": PostgresTableState(column_hash="a", schema_name="secret", table_name="pay")}
        connector = self._incremental_connector(stored, {})
        connector._get_filter_values = MagicMock(return_value=(["secret"], "not_in", None, "in"))

        await self._run(connector)

        connector._list_table_names.assert_not_awaited()
        assert connector._handle_deleted_tables.call_args[0][0] == ["secret.pay"]

    @pytest.mark.asyncio
    async def test_listing_failure_while_confirming_deletes_nothing(self) -> None:
        stored = {"public.t1": PostgresTableState(column_hash="a", schema_name="public", table_name="t1")}
        connector = self._incremental_connector(stored, {})
        connector._list_table_names = AsyncMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError):
            await self._run(connector)

        connector._handle_deleted_tables.assert_not_awaited()
        connector._save_tables_sync_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_state_from_older_version_runs_full_sync(self) -> None:
        connector = self._incremental_connector({}, {})
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value={
            "table_states": "{}", "state_version": SYNC_STATE_VERSION - 1, "database": "testdb",
        })
        connector._run_full_sync_internal = AsyncMock()

        await self._run(connector)

        connector._run_full_sync_internal.assert_awaited_once()
        connector._get_current_table_states.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_state_saved_for_another_database_runs_full_sync(self) -> None:
        # An oid is only unique within one database: diffing against another
        # one's tables could take an unrelated table for a renamed one.
        stored = {"public.t1": PostgresTableState(column_hash="a", oid=7)}
        connector = self._incremental_connector(stored, {})
        connector.database_name = "otherdb"
        connector._run_full_sync_internal = AsyncMock()

        await self._run(connector)

        connector._run_full_sync_internal.assert_awaited_once()
        connector._get_current_table_states.assert_not_awaited()
        connector._handle_deleted_tables.assert_not_awaited()


# ===========================================================================
# PostgreSQLConnector._handle_deleted_tables
# ===========================================================================


class TestHandleDeletedTables:

    @pytest.mark.asyncio
    async def test_deletes_record(self) -> None:
        connector = _make_connector()
        mock_record = MagicMock()
        mock_record.id = "rec-1"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=mock_record
        )
        # The delete cascades: the processor answers with the table and its columns.
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            return_value=["rec-1", "col-1", "col-2"]
        )

        await connector._handle_deleted_tables(["public.t1"])

        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once_with(
            ["rec-1"], "conn-pg-1", table_record_id=None,
        )
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()
        assert connector.sync_outcome.removed == {"rec-1", "col-1", "col-2"}

    @pytest.mark.asyncio
    async def test_skips_when_no_record_found(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=None
        )
        handled = await connector._handle_deleted_tables(["public.t1"])
        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()
        assert handled == {"public.t1"}

    @pytest.mark.asyncio
    async def test_continues_on_error(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=[Exception("error"), MagicMock(id="r2")]
        )
        handled = await connector._handle_deleted_tables(["public.t1", "public.t2"])
        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once_with(
            ["r2"], "conn-pg-1", table_record_id=None,
        )
        assert handled == {"public.t2"}
        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_table_whose_delete_failed_is_not_handled(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=MagicMock(id="rec-1")
        )
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            side_effect=RuntimeError("graph down")
        )

        assert await connector._handle_deleted_tables(["public.t1"]) == set()
        assert connector.sync_stats.errors == 1


class TestAnnounceRenames:

    @pytest.mark.asyncio
    async def test_the_renamed_tables_still_there_are_announced(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.on_sql_records_renamed = AsyncMock()
        connector.sync_outcome.renamed = {"t2", "t1", "gone"}
        connector.sync_outcome.removed = {"gone"}

        await connector._announce_renames()

        connector.data_entities_processor.on_sql_records_renamed.assert_awaited_once_with(["t1", "t2"], "conn-pg-1")

    @pytest.mark.asyncio
    async def test_nothing_renamed_sends_nothing(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.on_sql_records_renamed = AsyncMock()

        await connector._announce_renames()

        connector.data_entities_processor.on_sql_records_renamed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_rename_that_cannot_be_reported_is_reported_as_restructured(self) -> None:
        # A rename alone would never bring these tables up again.
        connector = _make_connector()
        connector.data_entities_processor.on_sql_records_renamed = AsyncMock(side_effect=ConnectionError("down"))
        connector.sync_outcome.renamed = {"t1"}

        await connector._announce_renames()

        assert connector.sync_outcome.restructured == {"t1"}

    @pytest.mark.asyncio
    async def test_a_sync_announces_its_renames_even_when_it_fails(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value=None)

        async def fail() -> Never:
            connector.sync_outcome.renamed.add("t1")
            raise RuntimeError("connection lost")

        connector._run_full_sync_internal = AsyncMock(side_effect=fail)
        connector._resolve_restructured = AsyncMock()
        connector._announce_renames = AsyncMock()

        with patch(
            "app.connectors.sources.postgres.connector.load_connector_filters",
            new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection()),
        ), pytest.raises(RuntimeError):
            await connector.run_sync()

        connector._announce_renames.assert_awaited_once()


class TestTablesToRelateAgain:
    """The tables whose relations depended on a removed record are noted before it goes."""

    @pytest.mark.asyncio
    async def test_tables_related_to_a_removed_table_are_read_before_it_goes(self) -> None:
        connector = _make_connector()
        order: list[str] = []
        into = AsyncMock(side_effect=lambda *_a, **_kw: order.append("read") or {"audit"})
        connector.data_entities_processor.sql_tables_to_relate_again = into
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            side_effect=lambda *_a, **_kw: order.append("delete") or ["users", "users.id"],
        )

        await connector._remove_records(["users"])

        assert order == ["read", "delete"]
        assert into.await_args.args == (["users"],)
        assert into.await_args.kwargs == {"column_ids": None}
        assert connector.sync_outcome.relations_only == {"audit"}

    @pytest.mark.asyncio
    async def test_only_the_removed_columns_of_a_table(self) -> None:
        connector = _make_connector()
        into = AsyncMock(return_value={"audit"})
        connector.data_entities_processor.sql_tables_to_relate_again = into

        await connector._remove_records(["users.id"], table_record_id="users")

        assert into.await_args.args == (["users"],)
        assert into.await_args.kwargs == {"column_ids": ["users.id"]}
        assert connector.sync_outcome.relations_only == {"audit"}

    @pytest.mark.asyncio
    async def test_a_graph_that_cannot_answer_does_not_stop_the_removal(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(return_value=["users"])
        connector.data_entities_processor.sql_tables_to_relate_again = AsyncMock(
            side_effect=RuntimeError("graph down"),
        )

        await connector._remove_records(["users"])

        assert connector.sync_outcome.removed == {"users"}
        assert connector.sync_outcome.relations_only == set()


# ===========================================================================
# PostgreSQLConnector.stream_record
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
        record.external_record_id = "public.users"
        connector.data_source.get_table_info = AsyncMock(
            return_value=_pg_response(False, None, error="Table not found")
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
    async def test_constraint_failure_does_not_stream_hollow_table(self) -> None:
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
# PostgreSQLConnector.reindex_records
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
# PostgreSQLConnector.get_filter_options
# ===========================================================================


class TestGetFilterOptions:

    @pytest.mark.asyncio
    async def test_unknown_filter_key(self) -> None:
        connector = _make_connector()
        result = await connector.get_filter_options("unknown_key")
        assert result.success is False
        assert "Unknown filter key" in result.message

    @pytest.mark.asyncio
    async def test_returns_schemas(self) -> None:
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
    async def test_returns_tables(self) -> None:
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
    async def test_pagination(self) -> None:
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
    async def test_search_filter_schemas(self) -> None:
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
    async def test_search_filter_tables(self) -> None:
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
    async def test_cursor_pagination(self) -> None:
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
    async def test_handles_error(self) -> None:
        connector = _make_connector()
        connector.data_source = None

        result = await connector.get_filter_options("tables")
        assert result.success is False


# ===========================================================================
# PostgreSQLConnector._populate_filter_cache
# ===========================================================================


class TestPopulateFilterCache:

    @pytest.mark.asyncio
    async def test_populates_cache(self) -> None:
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
    async def test_raises_when_data_source_not_initialized(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(RuntimeError):
            await connector._populate_filter_cache()

    @pytest.mark.asyncio
    async def test_raises_when_list_schemas_fails(self) -> None:
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


def _stat(schema, table, **counters) -> dict:
    return {"schema_name": schema, "table_name": table, "n_live_tup": 0,
            "n_tup_ins": 0, "n_tup_upd": 0, "n_tup_del": 0, **counters}


def _states_connector(stats, columns=None, standby=False, constraints=None) -> PostgreSQLConnector:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector.data_source.get_table_stats = AsyncMock(return_value=_pg_response(True, stats))
    connector.data_source.get_columns = AsyncMock(return_value=_pg_response(True, columns or []))
    connector.data_source.get_constraint_hashes = AsyncMock(
        return_value=_pg_response(True, constraints or [])
    )
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
    async def test_returns_states(self) -> None:
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
        # The default of the "Max Rows Per Table" filter.
        assert state.row_limit == 1000
        connector.data_source.get_sample_hash.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_states_carry_the_row_limit_in_force(self) -> None:
        # The indexed text holds that many rows, so raising or lowering the
        # limit has to re-index the table like a write would.
        connector = _states_connector([_stat("public", "users")])
        connector.sync_filters = MagicMock()
        connector.sync_filters.get_value.return_value = 250

        states, _ = await connector._get_current_table_states(None)

        assert states["public.users"].row_limit == 250
        raised = states["public.users"].model_copy(update={"row_limit": 500})
        assert raised.revision() != states["public.users"].revision()
        assert raised.structure() == states["public.users"].structure()

    @pytest.mark.asyncio
    async def test_reads_all_columns_in_one_query(self) -> None:
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
        connector.data_source.get_constraint_hashes.assert_awaited_once_with(["public"])
        assert states["public.a"].column_hash != states["public.b"].column_hash

    @pytest.mark.asyncio
    async def test_states_carry_what_a_rename_leaves_alone(self) -> None:
        # The oid and the attnums are how the next run tells a renamed table or
        # column from a dropped one and a new one.
        connector = _states_connector(
            [_stat("public", "users", oid=16402, storage_id="16409")],
            [
                {"schema_name": "public", "table_name": "users", "name": "id", "attnum": 1},
                {"schema_name": "public", "table_name": "users", "name": "email", "attnum": 3},
            ],
            constraints=[{"schema_name": "public", "table_name": "users", "constraint_hash": "c" * 32}],
        )

        states, _ = await connector._get_current_table_states(None)

        state = states["public.users"]
        assert (state.oid, state.storage_id, state.constraint_hash) == (16402, "16409", "c" * 32)
        assert state.columns == {"1": "id", "3": "email"}

    @staticmethod
    async def _shape(table: str, columns: list, constraint_shape: str = "k") -> str:
        connector = _states_connector(
            [_stat("public", table)],
            [{"schema_name": "public", "table_name": table, **column} for column in columns],
            constraints=[{
                "schema_name": "public", "table_name": table,
                "constraint_hash": f"{table}-keys", "constraint_shape": constraint_shape,
            }],
        )
        states, _ = await connector._get_current_table_states(None)
        return states[f"public.{table}"].shape

    @pytest.mark.asyncio
    async def test_a_rename_leaves_the_shape_as_it_was(self) -> None:
        # Of the table, of a column, and of its keys, which constraint_shape leaves out.
        before = await self._shape("users", [
            {"name": "id", "attnum": 1, "data_type": "integer"},
            {"name": "mail", "attnum": 3, "data_type": "text", "nullable": True},
        ])
        after = await self._shape("people", [
            {"name": "email", "attnum": 3, "data_type": "text", "nullable": True},
            {"name": "id", "attnum": 1, "data_type": "integer"},
        ])

        assert before and before == after

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("columns", "constraint_shape"), [
        ([{"name": "id", "attnum": 1, "data_type": "bigint"}], "k"),
        ([{"name": "id", "attnum": 1, "data_type": "integer", "nullable": True}], "k"),
        ([{"name": "id", "attnum": 2, "data_type": "integer"}], "k"),
        ([{"name": "id", "attnum": 1, "data_type": "integer"}], "other keys"),
    ], ids=["type", "nullable", "another-column", "keys"])
    async def test_anything_else_changes_the_shape(self, columns, constraint_shape) -> None:
        before = await self._shape("users", [{"name": "id", "attnum": 1, "data_type": "integer"}])

        assert await self._shape("users", columns, constraint_shape) != before

    @pytest.mark.asyncio
    async def test_a_column_without_its_number_leaves_no_shape(self) -> None:
        # It could not be told apart from a renamed one.
        assert await self._shape("users", [{"name": "id", "data_type": "integer"}]) == ""

    @pytest.mark.asyncio
    async def test_table_without_constraints_or_ids_gets_empty_values(self) -> None:
        # get_constraint_hashes has no row for a table with no key or constraint.
        connector = _states_connector(
            [_stat("public", "log")],
            [{"schema_name": "public", "table_name": "log", "name": "line"}],
        )

        states, _ = await connector._get_current_table_states(None)

        state = states["public.log"]
        assert (state.oid, state.storage_id, state.constraint_hash, state.columns) == (0, "", "", {})

    @pytest.mark.asyncio
    async def test_attnum_is_not_part_of_the_column_hash(self) -> None:
        # A hash that moved with it would re-index every table on upgrade.
        connector = _states_connector(
            [_stat("public", "users")],
            [{"schema_name": "public", "table_name": "users", "name": "id", "attnum": 4}],
        )

        states, _ = await connector._get_current_table_states(None)

        assert states["public.users"].column_hash == _column_hash([ColumnInfo(name="id")])

    @pytest.mark.asyncio
    async def test_raises_on_stats_failure(self) -> None:
        connector = _states_connector([])
        connector.data_source.get_table_stats = AsyncMock(return_value=_pg_response(False, error="Error"))
        with pytest.raises(RuntimeError):
            await connector._get_current_table_states(None)

    @pytest.mark.asyncio
    async def test_raises_on_columns_failure(self) -> None:
        connector = _states_connector([_stat("public", "t")])
        connector.data_source.get_columns = AsyncMock(return_value=_pg_response(False, error="timeout"))
        with pytest.raises(RuntimeError, match="timeout"):
            await connector._get_current_table_states(None)

    @pytest.mark.asyncio
    async def test_raises_on_constraints_failure(self) -> None:
        # Empty hashes would read as every table's keys having been dropped.
        connector = _states_connector([_stat("public", "t")])
        connector.data_source.get_constraint_hashes = AsyncMock(
            return_value=_pg_response(False, error="permission denied")
        )
        with pytest.raises(RuntimeError, match="Failed to get constraints: permission denied"):
            await connector._get_current_table_states(None)

    @pytest.mark.asyncio
    async def test_standby_hashes_each_sample_in_key_order(self) -> None:
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
    async def test_standby_table_without_a_key_is_still_hashed_by_the_data_source(self) -> None:
        connector = _states_connector([_stat("public", "t")], standby=True)
        connector.data_source.get_primary_keys_by_table = AsyncMock(return_value=_pg_response(True, []))

        states, _ = await connector._get_current_table_states(None)

        assert states["public.t"].sample_hash == "s" * 32
        connector.data_source.get_sample_hash.assert_awaited_once_with(
            "public", "t", limit=1000, order_by=None, max_bytes=MAX_TABLE_DOCUMENT_BYTES
        )

    @pytest.mark.asyncio
    async def test_standby_table_that_cannot_be_sampled_is_unreadable(self) -> None:
        connector = _states_connector([_stat("public", "t")], standby=True)
        connector.data_source.get_sample_hash = AsyncMock(return_value=_pg_response(False, error="denied"))

        states, unreadable = await connector._get_current_table_states(None)

        assert states == {}
        assert unreadable == {"public.t"}

    def test_sample_hash_changes_revision_and_detection(self) -> None:
        a = PostgresTableState(column_hash="h", sample_hash="x")
        b = PostgresTableState(column_hash="h", sample_hash="y")
        assert a.revision() != b.revision()
        assert diff_catalog({"public.t": a}, {"public.t": b}).changed == {"public.t": TableChange(data=True)}
        # A primary's state has no sample hash; its revision is unchanged by the field.
        plain = PostgresTableState(schema_name="public", table_name="t", column_hash="h")
        assert plain.revision() == hashlib.md5(b"public.t:h::0:0:0::0").hexdigest()

    @pytest.mark.asyncio
    async def test_schema_with_a_dot_keeps_its_name(self) -> None:
        connector = _states_connector([_stat("my.schema", "t")])

        states, _ = await connector._get_current_table_states(None)

        assert states['"my.schema".t'].schema_name == "my.schema"
        assert states['"my.schema".t'].table_name == "t"

    @pytest.mark.asyncio
    async def test_respects_selected_tables_filter(self) -> None:
        connector = _states_connector([_stat("public", "users"), _stat("public", "orders")])

        states, _ = await connector._get_current_table_states(None, selected_tables=["public.users"])

        assert set(states) == {"public.users"}

    @pytest.mark.asyncio
    async def test_tables_not_in_filter_excludes_matches(self) -> None:
        connector = _states_connector([_stat("public", "users"), _stat("public", "orders")])

        states, _ = await connector._get_current_table_states(
            None, selected_tables=["public.orders"], tables_op="not_in"
        )

        assert set(states) == {"public.users"}

    @pytest.mark.asyncio
    async def test_schemas_not_in_fetches_all_then_excludes_client_side(self) -> None:
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
    async def test_saves_state(self) -> None:
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
        # Which database the states describe: the next run only trusts them for the same one.
        assert payload["database"] == "testdb"
        assert payload["database_oid"] == _DB_OID
        assert json.loads(payload["table_states"]) == {"public.t1": states["public.t1"].model_dump()}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("attribute", "other"), [
        ("database_name", "otherdb"), ("server_address", "db2:5432"), ("database_oid", _DB_OID + 1),
    ])
    async def test_saved_state_is_usable_by_the_next_run_of_the_same_database(self, attribute, other) -> None:
        connector = _make_connector()
        connector.database_name = "testdb"
        connector.server_address = "db1:5432"
        connector.tables_sync_point.update_sync_point = AsyncMock()

        await connector._save_tables_sync_state({"public.t1": PostgresTableState(column_hash="h")})

        saved = connector.tables_sync_point.update_sync_point.call_args[0][1]
        assert connector._has_usable_state(saved) is True
        setattr(connector, attribute, other)
        assert connector._has_usable_state(saved) is False


# ===========================================================================
# PostgreSQLConnector._sync_new_tables
# ===========================================================================


class TestSyncNewTables:

    @pytest.mark.asyncio
    async def test_syncs_new_tables_and_creates_schema_groups(self) -> None:
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
    async def test_unreadable_new_table_is_not_created(self) -> None:
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
    async def test_syncs_changed_tables(self) -> None:
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
        connector._sync_schemas = AsyncMock()
        states = {"public.users": PostgresTableState(schema_name="public", table_name="users")}
        changes = {"public.users": TableChange(data=True)}

        synced = await connector._sync_changed_tables(changes, states)

        assert synced == {"public.users"}
        connector._sync_updated_tables.assert_awaited_once()
        schema_name, tables, passed_changes = connector._sync_updated_tables.call_args[0]
        assert schema_name == "public"
        assert tables[0].state is states["public.users"]
        # Carries each table's column renames on to its column records.
        assert passed_changes is changes
        connector._sync_schemas.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_changes_reads_nothing(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._load_table = AsyncMock()

        assert await connector._sync_changed_tables({}, {}) == set()
        connector._load_table.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_table_moved_to_another_schema_gets_that_schemas_group(self) -> None:
        # The schema may never have been synced: nothing else creates its group.
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._load_table = AsyncMock(
            side_effect=lambda schema, name, state: PostgresTable(name=name, schema_name=schema, state=state)
        )
        connector._sync_updated_tables = AsyncMock(return_value=set())
        connector._sync_schemas = AsyncMock()
        states = {
            "archive.orders": PostgresTableState(schema_name="archive", table_name="orders"),
            "public.users": PostgresTableState(schema_name="public", table_name="users"),
        }
        changes = {
            "archive.orders": TableChange(previous_fqn="public.orders", structure=True),
            "public.users": TableChange(data=True),
        }

        await connector._sync_changed_tables(changes, states)

        [schemas] = connector._sync_schemas.await_args.args
        assert [schema.name for schema in schemas] == ["archive"]

    @pytest.mark.asyncio
    async def test_unreadable_changed_table_is_not_synced(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._load_table = AsyncMock(side_effect=RuntimeError("gone"))
        connector._sync_updated_tables = AsyncMock()
        states = {"public.users": PostgresTableState(schema_name="public", table_name="users")}

        synced = await connector._sync_changed_tables({"public.users": TableChange(data=True)}, states)

        assert synced == set()
        assert connector.sync_stats.errors == 1
        connector._sync_updated_tables.assert_not_awaited()


# ===========================================================================
# PostgreSQLConnector._rename_tables
# ===========================================================================


class TestRenameTables:

    @staticmethod
    def _connector(*records) -> tuple:
        connector = _make_connector()
        connector.database_name = "testdb"
        return connector, _record_store(connector, *records)

    @pytest.mark.asyncio
    async def test_table_and_its_columns_move_to_the_new_name(self) -> None:
        # Re-keyed, not recreated: the notes and links hang off these ids.
        connector, store = self._connector(
            _stored("tbl-1", "public.orders"),
            _stored("col-1", "testdb.public.orders.id", "public.orders"),
            _stored("col-2", "testdb.public.orders.status", "public.orders"),
            _stored("col-other", "testdb.public.users.id", "public.users"),
        )

        unmoved = await connector._rename_tables({"public.orders": "archive.purchases"})

        assert unmoved == set()
        assert store.updates == [(
            [
                {"id": "tbl-1", "externalRecordId": "archive.purchases"},
                {
                    "id": "col-1",
                    "externalRecordId": "testdb.archive.purchases.id",
                    "externalParentId": "archive.purchases",
                },
                {
                    "id": "col-2",
                    "externalRecordId": "testdb.archive.purchases.status",
                    "externalParentId": "archive.purchases",
                },
            ],
            "records",
        )]
        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stand_in_under_the_new_name_is_removed_first(self) -> None:
        # Left by a table that references the renamed one and was synced before it.
        connector, store = self._connector(
            _stored("tbl-1", "public.orders"),
            _stored("stand-in", "public.purchases"),
        )
        steps = []
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            side_effect=lambda ids, *args, **kwargs: steps.append("remove") or ids
        )
        update = store.batch_update_nodes

        async def batch_update_nodes(nodes, collection):
            steps.append("update")
            return await update(nodes, collection)

        store.batch_update_nodes = batch_update_nodes

        move = connector.data_entities_processor.move_incoming_relations = AsyncMock(
            side_effect=lambda *args, **kwargs: steps.append("move") or 1,
        )
        await connector._rename_tables({"public.orders": "public.purchases"})

        # The tables that reference it keep their foreign keys.
        move.assert_awaited_once_with("stand-in", "tbl-1")
        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once_with(
            ["stand-in"], "conn-pg-1", table_record_id=None,
        )
        assert steps == ["move", "remove", "update"]
        assert store.updates == [([{"id": "tbl-1", "externalRecordId": "public.purchases"}], "records")]
        assert connector.sync_outcome.removed == {"stand-in"}

    @pytest.mark.asyncio
    async def test_nothing_stored_under_the_old_name_is_a_no_op(self) -> None:
        # Also the retry after a run that moved the record and then failed:
        # the record already under the new name must be left as it is.
        connector, store = self._connector(_stored("tbl-1", "public.purchases"))

        assert await connector._rename_tables({"public.orders": "public.purchases"}) == set()

        assert store.updates == []
        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tables_that_swapped_names_move_together(self) -> None:
        # Neither name is free until both have moved, and neither record is a stand-in.
        connector, store = self._connector(_stored("tbl-a", "public.a"), _stored("tbl-b", "public.b"))

        unmoved = await connector._rename_tables({"public.a": "public.b", "public.b": "public.a"})

        assert unmoved == set()
        assert store.updates == [([
            {"id": "tbl-a", "externalRecordId": "public.b"},
            {"id": "tbl-b", "externalRecordId": "public.a"},
        ], "records")]
        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_chain_of_renames_moves_together(self) -> None:
        connector, store = self._connector(_stored("tbl-a", "public.a"), _stored("tbl-b", "public.b"))

        unmoved = await connector._rename_tables({"public.a": "public.b", "public.b": "public.c"})

        assert unmoved == set()
        assert store.updates == [([
            {"id": "tbl-a", "externalRecordId": "public.b"},
            {"id": "tbl-b", "externalRecordId": "public.c"},
        ], "records")]
        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_table_that_cannot_move_holds_back_the_one_moving_into_its_name(self) -> None:
        connector, store = self._connector(_stored("tbl-a", "public.a"), _stored("tbl-b", "public.b"))
        store.failing.add("public.b")

        unmoved = await connector._rename_tables({"public.a": "public.b", "public.b": "public.c"})

        # b stays where it is, so a cannot take its name yet; both are tried next run.
        assert unmoved == {"public.b", "public.c"}
        assert store.updates == []
        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_a_name_still_held_by_a_dropped_table_is_not_moved_into(self) -> None:
        connector, store = self._connector(_stored("tbl-a", "public.a"), _stored("tbl-old-b", "public.b"))

        unmoved = await connector._rename_tables({"public.a": "public.b"}, blocked={"public.b"})

        assert unmoved == {"public.b"}
        assert store.updates == []
        connector.data_entities_processor.on_sql_records_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_write_moves_nothing(self) -> None:
        connector, store = self._connector(_stored("tbl-a", "public.a"), _stored("tbl-b", "public.b"))
        store.batch_update_nodes = AsyncMock(side_effect=RuntimeError("graph down"))

        unmoved = await connector._rename_tables({"public.a": "public.a2", "public.b": "public.b2"})

        assert unmoved == {"public.a2", "public.b2"}
        assert connector.sync_stats.errors == 1


# ===========================================================================
# PostgreSQLConnector._remove_stale_tables
# ===========================================================================


class TestRemoveStaleTables:

    @pytest.mark.asyncio
    async def test_deletes_records_of_tables_no_longer_synced(self) -> None:
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
        # One call for them all; their columns go with them in the processor's cascade.
        deleted = [c.args for c in connector.data_entities_processor.on_sql_records_deleted.await_args_list]
        assert deleted == [(["dropped", "excluded"], "conn-pg-1")]
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continues_after_a_failed_delete(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.get_records_by_record_type = AsyncMock(return_value=[
            MagicMock(id="a", external_record_id="public.a", external_record_group_id="public"),
            MagicMock(id="b", external_record_id="public.b", external_record_group_id="public"),
        ])
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            side_effect=[Exception("db"), ["b"]]
        )

        with patch("app.connectors.sources.postgres.connector.TABLES_REMOVED_PER_BATCH", 1):
            undeleted = await connector._remove_stale_tables(set())

        assert connector.data_entities_processor.on_sql_records_deleted.await_count == 2
        assert connector.sync_stats.errors == 1
        assert undeleted == {"public.a": PostgresTableState(schema_name="public", table_name="a")}


# ===========================================================================
# PostgreSQLConnector._split_table_fqn
# ===========================================================================


class TestSplitTableFqn:

    def _record(self, fqn, group_id):
        return MagicMock(external_record_id=fqn, external_record_group_id=group_id)

    def test_plain_names(self) -> None:
        connector = _make_connector()
        connector.database_name = "db"
        assert connector._split_table_fqn(self._record("public.users", "public")) == ("public", "users")

    def test_schema_with_a_dot(self) -> None:
        connector = _make_connector()
        connector.database_name = "db"
        assert connector._split_table_fqn(self._record("my.schema.t", "my.schema")) == ("my.schema", "t")

    def test_schema_named_after_database(self) -> None:
        connector = _make_connector()
        connector.database_name = "app"
        assert connector._split_table_fqn(self._record("app.t", "app.app")) == ("app", "t")

    def test_falls_back_to_first_dot_without_group(self) -> None:
        connector = _make_connector()
        connector.database_name = "db"
        assert connector._split_table_fqn(self._record("public.users", None)) == ("public", "users")


# ===========================================================================
# PostgreSQLConnector.create_connector (factory method)
# ===========================================================================


class TestCreateConnector:

    @pytest.mark.asyncio
    async def test_creates_connector(self) -> None:
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

    def test_value(self) -> None:
        assert MAX_ROWS_PER_TABLE_LIMIT == 10000


class TestFilterCacheRefresh:

    @pytest.mark.asyncio
    async def test_later_pages_read_the_cache(self) -> None:
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
    async def test_reopening_the_list_refreshes_it(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_source.list_schemas = AsyncMock(return_value=_pg_response(True, [{"name": "a"}]))
        connector.data_source.list_tables = AsyncMock(return_value=_pg_response(True, []))

        await connector.get_filter_options("schemas")
        await connector.get_filter_options("schemas")

        assert connector.data_source.list_schemas.await_count == 2


class TestStreamRecordRowLimit:

    @pytest.mark.asyncio
    async def test_non_positive_max_rows_reads_at_least_one_row(self) -> None:
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
    async def test_rows_are_ordered_by_primary_key_and_json_safe(self) -> None:
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
            ], "primary_keys": ["id"]}),
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


class TestStreamColumn:

    @staticmethod
    def _column_record() -> MagicMock:
        return MagicMock(**{
            "record_type": RecordType.SQL_COLUMN, "record_name": "status",
            "external_record_id": "public.orders.status", "external_record_group_id": "public",
            "parent_external_record_id": "public.orders", "table_fqn": "public.orders",
            "data_type": "text", "nullable": True, "is_primary_key": False, "is_unique": False,
            "default": None, "ordinal_position": 3, "distinct_count": 2, "null_count": 1,
            "cardinality_ratio": 0.5, "min_value": None, "max_value": None, "numeric_text": False,
            "profiled_at": 1, "top_values": ["A", "B"], "top_value_frequencies": [0.75, 0.25],
        })

    @pytest.mark.asyncio
    async def test_streams_definition_profile_and_live_value_counts(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"
        connector.connector_name = Connectors.POSTGRESQL
        connector.data_source.fetch_column_value_counts = AsyncMock(return_value=[
            {"value": "A", "count": 3}, {"value": None, "count": 1},
        ])

        response = await connector.stream_record(self._column_record())
        data = json.loads(b"".join([chunk async for chunk in response.body_iterator]))

        connector.data_source.fetch_column_value_counts.assert_awaited_once_with(
            "public", "orders", "status", limit=MAX_COLUMN_VALUES_STREAMED + 1,
        )
        assert response.media_type == MimeTypes.SQL_COLUMN.value
        assert (data["schema_name"], data["table_name"], data["column_name"]) == ("public", "orders", "status")
        assert data["data_type"] == "text" and data["nullable"] is True
        assert data["profile"]["top_values"] == [
            {"value": "A", "frequency": 0.75}, {"value": "B", "frequency": 0.25},
        ]
        assert data["value_counts"] == [{"value": "A", "count": 3}, {"value": None, "count": 1}]
        assert data["value_counts_truncated"] is False
        assert data["value_counts_withheld"] is False

    @staticmethod
    async def _stream(connector, record) -> dict:
        response = await connector.stream_record(record)
        return json.loads(b"".join([chunk async for chunk in response.body_iterator]))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("rows", "truncated"), [
        (MAX_COLUMN_VALUES_STREAMED, False),
        (MAX_COLUMN_VALUES_STREAMED + 1, True),
    ])
    async def test_truncated_only_when_a_value_was_cut_off(self, rows: int, truncated: bool) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.connector_name = Connectors.POSTGRESQL
        connector.data_source.fetch_column_value_counts = AsyncMock(
            return_value=[{"value": str(i), "count": 1} for i in range(rows)],
        )

        data = await self._stream(connector, self._column_record())

        assert len(data["value_counts"]) == MAX_COLUMN_VALUES_STREAMED
        assert data["value_counts_truncated"] is truncated

    @pytest.mark.asyncio
    async def test_a_personal_column_streams_no_value_counts(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.connector_name = Connectors.POSTGRESQL
        connector.data_source.fetch_column_value_counts = AsyncMock()
        record = self._column_record()
        record.record_name = "email"

        data = await self._stream(connector, record)

        connector.data_source.fetch_column_value_counts.assert_not_awaited()
        assert data["value_counts"] == []
        assert data["value_counts_withheld"] is True
        assert data["profile"]["distinct_count"] == 2

    @pytest.mark.asyncio
    async def test_value_read_failure_is_an_error_not_an_empty_column(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.database_name = "testdb"

        class InsufficientPrivilegeError(Exception):
            pass

        connector.data_source.fetch_column_value_counts = AsyncMock(
            side_effect=InsufficientPrivilegeError("permission denied for table orders")
        )

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(self._column_record())
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_other_record_types_are_still_refused(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(MagicMock(record_type=RecordType.FILE))
        assert exc_info.value.status_code == 400


class TestJsonDefault:

    def test_range(self) -> None:
        from app.connectors.sources.postgres.connector import _json_default

        rng = MagicMock(lower=1, upper=5, lower_inc=True, upper_inc=False, isempty=False)
        assert _json_default(rng) == {
            "lower": 1, "upper": 5, "lower_inc": True, "upper_inc": False, "empty": False,
        }

    def test_composite_record(self) -> None:
        from app.connectors.sources.postgres.connector import _json_default

        class FakeRecord:
            def items(self):
                return [("street", "Main"), ("no", 5)]

        assert _json_default(FakeRecord()) == {"street": "Main", "no": 5}

    def test_other_values_fall_back_to_str(self) -> None:
        from decimal import Decimal

        from app.connectors.sources.postgres.connector import _json_default

        assert _json_default(Decimal("1.50")) == "1.50"


class TestTableIds:

    def test_plain_names_are_unchanged(self) -> None:
        # Existing ids must not move: every ordinary table keeps its id.
        assert table_fqn("public", "users") == "public.users"
        assert table_fqn("s", 'we"ird') == 's.we"ird'

    def test_names_with_dots_no_longer_collide(self) -> None:
        a = table_fqn("a.b", "c")
        b = table_fqn("a", "b.c")
        assert a == '"a.b".c'
        assert b == 'a."b.c"'
        assert a != b

    @pytest.mark.parametrize("schema,table", [
        ("public", "users"), ("a.b", "c"), ("a", "b.c"), ('"q', "t"), ("s", '"t"'), ('x"."y', "z.z"),
    ])
    def test_round_trip(self, schema, table) -> None:
        assert split_table_fqn(table_fqn(schema, table)) == (schema, table)

    def test_rejects_what_it_did_not_write(self) -> None:
        with pytest.raises(ValueError):
            split_table_fqn("my.schema.t")
        with pytest.raises(ValueError):
            split_table_fqn('"unterminated.t')

    def test_legacy_dotted_id_is_split_by_its_group(self) -> None:
        connector = _make_connector()
        connector.database_name = "db"
        record = MagicMock(external_record_id="my.schema.t", external_record_group_id="my.schema")
        assert connector._split_table_fqn(record) == ("my.schema", "t")


class _FakeTx:
    def __init__(self, groups=None, group=None) -> None:
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

    async def delete_edge(self, from_id, from_collection, to_id, to_collection, collection) -> None:
        self.deleted_edges.append((from_id, to_id, collection))


class TestSchemaGroupCleanup:

    @pytest.mark.asyncio
    async def test_groups_of_unsynced_schemas_are_deleted(self) -> None:
        connector = _make_connector()
        connector.database_name = "app"
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=True)
        tx = _FakeTx(groups=[
            {"id": "g-public", "externalGroupId": "public"},
            {"id": "g-excluded", "externalGroupId": "excluded"},
            {"id": "g-app", "externalGroupId": "app.app"},
        ])
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_stale_schema_groups(synced_schemas=["public", "app"])

        assert tx.filters == ("recordGroups", {"connectorId": "conn-pg-1", "groupType": "SQL_NAMESPACE"})
        connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            "excluded", "conn-pg-1"
        )
        connector.data_entities_processor.on_sql_record_groups_deleted.assert_awaited_once_with(
            ["g-excluded"], "conn-pg-1"
        )
        assert connector.sync_stats.errors == 0

    @pytest.mark.asyncio
    async def test_vectors_of_every_deleted_group_are_cleaned_in_one_call(self) -> None:
        # Only the indexing service reaches the vector store, so it is told
        # once, after the groups are gone. Arango documents carry _key, not id.
        connector = _make_connector()
        connector.database_name = "app"
        steps = []
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(
            side_effect=lambda external_id, connector_id: steps.append(external_id) or True
        )
        connector.data_entities_processor.on_sql_record_groups_deleted = AsyncMock(
            side_effect=lambda ids, connector_id: steps.append(ids)
        )
        tx = _FakeTx(groups=[
            {"id": "g-old", "externalGroupId": "old"},
            {"id": "g-public", "externalGroupId": "public"},
            {"_key": "g-archive", "externalGroupId": "archive"},
        ])
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_stale_schema_groups(synced_schemas=["public"])

        assert steps == ["archive", "old", ["g-archive", "g-old"]]
        connector.data_entities_processor.on_sql_record_groups_deleted.assert_awaited_once_with(
            ["g-archive", "g-old"], "conn-pg-1"
        )

    @pytest.mark.asyncio
    async def test_no_stale_group_asks_for_no_cleanup(self) -> None:
        connector = _make_connector()
        connector.database_name = "app"
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=True)
        tx = _FakeTx(groups=[{"id": "g-public", "externalGroupId": "public"}])
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_stale_schema_groups(synced_schemas=["public"])

        connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
        connector.data_entities_processor.on_sql_record_groups_deleted.assert_not_awaited()
        assert connector.sync_stats.errors == 0

    @pytest.mark.asyncio
    async def test_the_database_group_is_kept_even_when_saved_as_a_schema_group(self) -> None:
        # Before schema groups got distinct ids, a schema named after its database
        # was upserted onto the database group, leaving it typed SQL_NAMESPACE.
        connector = _make_connector()
        connector.database_name = "app"
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=True)
        tx = _FakeTx(groups=[
            {"id": "g-db", "externalGroupId": "app"},
            {"id": "g-app", "externalGroupId": "app.app"},
            {"id": "g-leftover", "externalGroupId": "leftover"},
        ])
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_stale_schema_groups(synced_schemas=["app"])

        connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
            "leftover", "conn-pg-1"
        )
        connector.data_entities_processor.on_sql_record_groups_deleted.assert_awaited_once_with(
            ["g-leftover"], "conn-pg-1"
        )

    @pytest.mark.asyncio
    async def test_groups_deleted_before_a_failure_still_have_their_search_entries_removed(self) -> None:
        # A deleted group is not listed again, so this is the only chance.
        connector = _make_connector()
        connector.database_name = "app"
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(
            side_effect=[True, RuntimeError("graph down")],
        )
        tx = _FakeTx(groups=[
            {"id": "g-a", "externalGroupId": "a"},
            {"id": "g-b", "externalGroupId": "b"},
        ])
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_stale_schema_groups(synced_schemas=[])

        connector.data_entities_processor.on_sql_record_groups_deleted.assert_awaited_once_with(
            ["g-a"], "conn-pg-1"
        )
        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_failure_is_counted_not_raised(self) -> None:
        connector = _make_connector()
        connector.data_store_provider.transaction = MagicMock(side_effect=Exception("graph down"))

        await connector._remove_stale_schema_groups(synced_schemas=["public"])

        assert connector.sync_stats.errors == 1
        connector.data_entities_processor.on_sql_record_groups_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_self_links_left_by_the_name_clash_are_removed(self) -> None:
        connector = _make_connector()
        connector.database_name = "app"
        tx = _FakeTx(group=MagicMock(id="g1"))
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_database_group_self_links()

        # The hierarchy self-loop too: a group that is its own parent is unreachable (RG-7).
        assert tx.deleted_edges == [
            ("g1", "g1", "belongsTo"), ("g1", "g1", "inheritPermissions"), ("g1", "g1", "nodeRelations"),
        ]

    @pytest.mark.asyncio
    async def test_full_sync_repairs_only_when_a_schema_is_named_after_the_database(self) -> None:
        connector = _full_sync_connector({"public": []})
        await connector._run_full_sync_internal()
        connector._remove_database_group_self_links.assert_not_awaited()

        connector = _full_sync_connector({"testdb": []})
        await connector._run_full_sync_internal()
        connector._remove_database_group_self_links.assert_awaited_once()
        connector._remove_stale_schema_groups.assert_awaited_once_with(synced_schemas=["testdb"])


class TestFailedProfilingKeepsStoredValues:
    """A failed scan left the fields None, and writing None deletes the stored values."""

    @pytest.mark.asyncio
    async def test_stored_profile_and_revision_survive_a_failed_scan(self) -> None:
        frequency = ColumnProfile(top_values=["A", "B"], top_value_frequencies=[0.6, 0.4], profiled_at=1)
        stats = (2, (2, [{"name": "frequency", "distinct_count": 2, "null_count": 0, "cardinality_ratio": 1.0}]))
        good = _profiled_connector([{"name": "frequency", "data_type": "text"}],
                                   TableColumnStatistics(reltuples=2.0), stats=stats, profiles={"frequency": frequency})
        table = await good._load_table("public", "account", PostgresTableState(n_live_tup=2))
        [stored] = good._build_column_records(_table_record(), table)

        failing = _profiled_connector([{"name": "frequency", "data_type": "text"}], TableColumnStatistics(reltuples=2.0))
        failing.data_source.profile_columns = AsyncMock(side_effect=TimeoutError("statement timeout"))
        table = await failing._load_table("public", "account", PostgresTableState(n_live_tup=2))
        assert table.profiling_failed is True

        [record] = failing._build_column_records(_table_record(), table, {stored.external_record_id: stored})

        assert record.top_values == ["A", "B"]
        assert record.distinct_count == 2
        assert record.external_revision_id == stored.external_revision_id

    @staticmethod
    async def _stored_and_failing() -> tuple:
        """A column record a good scan saved, and a connector whose scan of the table fails."""
        frequency = ColumnProfile(top_values=["A", "B"], top_value_frequencies=[0.6, 0.4], profiled_at=1)
        stats = (2, (2, [{"name": "frequency", "distinct_count": 2, "null_count": 0, "cardinality_ratio": 1.0}]))
        good = _profiled_connector([{"name": "frequency", "data_type": "text"}],
                                   TableColumnStatistics(reltuples=2.0), stats=stats, profiles={"frequency": frequency})
        table = await good._load_table("public", "account", PostgresTableState(n_live_tup=2))
        [stored] = good._build_column_records(_table_record(), table)

        failing = _profiled_connector([{"name": "frequency", "data_type": "text"}], TableColumnStatistics(reltuples=2.0))
        failing.data_source.profile_columns = AsyncMock(side_effect=TimeoutError("statement timeout"))
        table = await failing._load_table("public", "account", PostgresTableState(n_live_tup=2))
        # The parent lookup reads the records collection: plain records, without the column's fields.
        _record_store(failing, _stored(stored.id, stored.external_record_id, "public.account"))
        failing.data_entities_processor.get_typed_records_by_ids = AsyncMock(
            return_value={stored.id: stored},
        )
        return stored, failing, table

    @pytest.mark.asyncio
    async def test_a_resync_reads_the_stored_values_from_the_column_records(self) -> None:
        stored, failing, table = await self._stored_and_failing()

        await failing._sync_table_columns(_table_record(), table)

        failing.data_entities_processor.get_typed_records_by_ids.assert_awaited_once_with([stored.id], raise_on_error=True)
        [(record, _)] = failing.data_entities_processor.on_new_records.await_args.args[0]
        assert record.top_values == ["A", "B"]
        assert record.distinct_count == 2
        assert record.external_revision_id == stored.external_revision_id

    @pytest.mark.asyncio
    async def test_a_new_sync_reads_the_stored_values_from_the_column_records(self) -> None:
        stored, failing, table = await self._stored_and_failing()
        failing._build_table_record = AsyncMock(return_value=(_table_record(), False))

        yielded = [record async for record, _ in failing._process_tables_generator("public", [table])]

        column = next(r for r in yielded if r.record_type == RecordType.SQL_COLUMN)
        assert column.top_values == ["A", "B"]
        assert column.distinct_count == 2

    @pytest.mark.asyncio
    async def test_a_scan_that_worked_reads_no_stored_values(self) -> None:
        _, failing, table = await self._stored_and_failing()
        table.profiling_failed = False

        await failing._sync_table_columns(_table_record(), table)

        failing.data_entities_processor.get_typed_records_by_ids.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read", [AsyncMock(return_value={}), AsyncMock(side_effect=RuntimeError("graph down"))])
    async def test_a_stored_profile_that_cannot_be_read_is_not_overwritten(self, read) -> None:
        _, failing, table = await self._stored_and_failing()
        failing.data_entities_processor.get_typed_records_by_ids = read

        with pytest.raises((LookupError, RuntimeError)):
            await failing._sync_table_columns(_table_record(), table)

        failing.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dropped_table_takes_its_column_records(self) -> None:
        connector = _make_connector()
        stale = MagicMock(id="tbl-gone", external_record_id="public.gone")
        connector.data_entities_processor.get_records_by_record_type = AsyncMock(return_value=[stale])
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            return_value=["tbl-gone", "col-gone-1", "col-gone-2"]
        )

        await connector._remove_stale_tables({"public.kept"})

        # Deleting the table is what removes its columns: no separate column delete.
        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once_with(
            ["tbl-gone"], "conn-pg-1", table_record_id=None,
        )
        connector.data_entities_processor.on_record_deleted.assert_not_awaited()
        assert connector.sync_outcome.removed == {"tbl-gone", "col-gone-1", "col-gone-2"}


# ===========================================================================
# Round two: identity, removals, drift
# ===========================================================================


class TestRelinkReferencingTables:

    @staticmethod
    def _connector() -> PostgreSQLConnector:
        connector = _make_connector()
        connector.database_name = "testdb"
        connector.indexing_filters = MagicMock(is_enabled=MagicMock(return_value=True))
        connector.data_source = MagicMock()
        connector.data_entities_processor.on_record_metadata_update = AsyncMock()
        return connector

    @pytest.mark.asyncio
    async def test_the_foreign_keys_of_a_table_pointing_at_one_that_came_back_are_written_again(self) -> None:
        connector = self._connector()
        connector.data_source.get_referencing_tables = AsyncMock(return_value=_pg_response(data=[
            {"schema_name": "public", "table_name": "orders"},
            {"schema_name": "public", "table_name": "invoices"},
        ]))
        connector.data_source.get_foreign_keys = AsyncMock(return_value=_pg_response(data=[{
            "constraint_name": "orders_customer_fk", "column_name": "customer_id",
            "foreign_table_schema": "public", "foreign_table_name": "customers", "foreign_column_name": "id",
        }]))
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_table_record().model_copy(update={"id": "rec-orders"}),
        )
        current = {
            "public.customers": PostgresTableState(oid=5, schema_name="public", table_name="customers"),
            "public.orders": PostgresTableState(oid=6, schema_name="public", table_name="orders", n_live_tup=3),
            "public.invoices": PostgresTableState(oid=7, schema_name="public", table_name="invoices"),
        }

        await connector._relink_referencing_tables(
            {"public.customers"}, current, skip={"public.customers", "public.invoices"},
        )

        connector.data_source.get_referencing_tables.assert_awaited_once_with([5])
        # invoices was synced this run and wrote its keys itself.
        connector.data_source.get_foreign_keys.assert_awaited_once_with("public", "orders")
        [record] = [c.args[0] for c in connector.data_entities_processor.on_record_metadata_update.await_args_list]
        assert record.id == "rec-orders"
        assert [r.external_record_id for r in record.related_external_records] == ["public.customers"]

    @pytest.mark.asyncio
    async def test_a_failed_lookup_is_counted_and_nothing_written(self) -> None:
        connector = self._connector()
        connector.data_source.get_referencing_tables = AsyncMock(
            return_value=_pg_response(success=False, error="permission denied"),
        )
        current = {"public.customers": PostgresTableState(oid=5)}

        await connector._relink_referencing_tables({"public.customers"}, current, skip=set())

        assert connector.sync_stats.errors == 1
        connector.data_entities_processor.on_record_metadata_update.assert_not_awaited()


class TestDeletedTablesInBatches:

    @pytest.mark.asyncio
    async def test_dropped_tables_go_in_one_cascade(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda **kw: _table_record().model_copy(update={"id": "rec-" + kw["external_record_id"]}),
        )
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(side_effect=lambda ids, *a, **k: ids)

        handled = await connector._handle_deleted_tables(["public.a", "public.b", "public.c"])

        assert handled == {"public.a", "public.b", "public.c"}
        connector.data_entities_processor.on_sql_records_deleted.assert_awaited_once_with(
            ["rec-public.a", "rec-public.b", "rec-public.c"], "conn-pg-1", table_record_id=None,
        )

    @pytest.mark.asyncio
    async def test_a_table_whose_record_cannot_be_looked_up_stays_for_the_next_run(self) -> None:
        # Answering "nothing stored" to a graph error would drop it from the state
        # and leave its record behind for good.
        connector = _make_connector()

        async def lookup(**kw):
            if kw["external_record_id"] == "public.a":
                raise RuntimeError("graph down")
            return _table_record().model_copy(update={"id": "rec-b"})

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=lookup)
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(side_effect=lambda ids, *a, **k: ids)

        handled = await connector._handle_deleted_tables(["public.a", "public.b"])

        assert handled == {"public.b"}
        assert connector.data_entities_processor.get_record_by_external_id.await_args.kwargs["raise_on_error"] is True
        assert connector.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_a_failed_batch_leaves_its_tables_for_the_next_run(self) -> None:
        connector = _make_connector()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda **kw: _table_record().model_copy(update={"id": "rec-" + kw["external_record_id"]}),
        )
        connector.data_entities_processor.on_sql_records_deleted = AsyncMock(
            side_effect=[RuntimeError("graph down"), ["rec-public.b"]],
        )

        with patch("app.connectors.sources.postgres.connector.TABLES_REMOVED_PER_BATCH", 1):
            handled = await connector._handle_deleted_tables(["public.a", "public.b"])

        assert handled == {"public.b"}


class TestDriftSinceAnOlderVersion:
    """The first sync after the upgrade finds what changed since the old one-time sync."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("version", [2, 3])
    async def test_tables_whose_columns_changed_since_then_are_restructured(self, version) -> None:
        connector = _make_connector()
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value={
            "state_version": version,
            "table_states": json.dumps({"public.a": {"column_hash": "old"}, "public.b": {"column_hash": "same"}}),
        })
        snapshot = {
            "public.a": PostgresTableState(column_hash="new"),
            "public.b": PostgresTableState(column_hash="same"),
            "public.c": PostgresTableState(column_hash="x"),
        }

        assert await connector._columns_changed_since_older_state(snapshot) == {"public.a"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("saved", [
        None,
        {"state_version": SYNC_STATE_VERSION, "table_states": json.dumps({"public.a": {"column_hash": "old"}})},
        {"state_version": 3, "table_states": "not json"},
    ])
    async def test_nothing_to_compare_with_finds_nothing(self, saved) -> None:
        connector = _make_connector()
        connector.tables_sync_point.read_sync_point = AsyncMock(return_value=saved)

        assert await connector._columns_changed_since_older_state({"public.a": PostgresTableState(column_hash="new")}) == set()

    @pytest.mark.asyncio
    async def test_a_full_sync_reports_them_as_restructured(self) -> None:
        connector = _make_connector()
        connector._create_app_users = AsyncMock()
        connector._create_database_record_group = AsyncMock()
        connector._get_current_table_states = AsyncMock(return_value=({}, set()))
        connector._columns_changed_since_older_state = AsyncMock(return_value={"public.a"})
        connector._fetch_schemas = AsyncMock(return_value=[])
        connector._sync_schemas = AsyncMock()
        connector._remove_stale_tables = AsyncMock(return_value={})
        connector._remove_stale_schema_groups = AsyncMock()
        connector._save_tables_sync_state = AsyncMock()

        await connector._run_full_sync_internal()

        assert connector._structure_changed == {"public.a"}


class TestSavedState:

    @pytest.mark.asyncio
    async def test_a_checkpoint_leaves_the_last_sync_time_alone(self) -> None:
        connector = _make_connector()
        connector.database_name = "testdb"
        connector.tables_sync_point.update_sync_point = AsyncMock()
        state = {"public.a": PostgresTableState(column_hash="a")}

        await connector._save_tables_sync_state(state, checkpoint=True)
        await connector._save_tables_sync_state(state)

        checkpoint, final = [call.args[1] for call in connector.tables_sync_point.update_sync_point.await_args_list]
        assert "last_sync_time" not in checkpoint
        assert final["last_sync_time"] > 0
        assert checkpoint["table_states"] == final["table_states"]


class TestRestructuredAfterAnInterruptedRun:

    def test_names_alone_leave_the_structure_as_it_was(self) -> None:
        # The record's structure was written with the old names.
        before = PostgresTableState(schema_name="public", table_name="people", column_hash="old")
        after = PostgresTableState(schema_name="public", table_name="users", column_hash="new")
        existing = _table_record(_table_revision(before))

        assert PostgreSQLConnector._is_restructured(existing, after) is True
        assert PostgreSQLConnector._is_restructured(existing, after, names_only=True) is False
        assert PostgreSQLConnector._is_restructured(None, after, names_only=True) is True

    def test_the_diff_counts_even_when_the_record_already_has_the_new_structure(self) -> None:
        # Written by a run that stopped before handing the table on.
        state = PostgresTableState(schema_name="public", table_name="a", column_hash="new")
        existing = _table_record(_table_revision(state))

        assert PostgreSQLConnector._is_restructured(existing, state) is False
        assert PostgreSQLConnector._is_restructured(existing, state, structure_changed=True) is True


class TestUnreadableLeftovers:

    @pytest.mark.asyncio
    async def test_a_saved_name_no_listing_can_hold_counts_as_dropped(self) -> None:
        # Kept by a delete that failed for a record whose name could not be split.
        connector = _make_connector()
        connector._list_table_names = AsyncMock(return_value=[])

        dropped = await connector._confirm_dropped(
            ["unsplittable"], {"unsplittable": PostgresTableState()}, (None, "in", None, "in"),
        )

        assert dropped == ["unsplittable"]
        connector._list_table_names.assert_not_awaited()


class TestSchemaGroupNotDeleted:

    @pytest.mark.asyncio
    async def test_a_group_the_processor_could_not_delete_keeps_its_search_entries(self) -> None:
        connector = _make_connector()
        connector.database_name = "app"
        connector.data_entities_processor.on_record_group_deleted = AsyncMock(side_effect=[False, True])
        tx = _FakeTx(groups=[
            {"id": "g-a", "externalGroupId": "a"},
            {"id": "g-b", "externalGroupId": "b"},
        ])
        connector.data_store_provider.transaction = MagicMock(return_value=tx)

        await connector._remove_stale_schema_groups(synced_schemas=[])

        connector.data_entities_processor.on_sql_record_groups_deleted.assert_awaited_once_with(["g-b"], "conn-pg-1")
        assert connector.sync_stats.errors == 1
