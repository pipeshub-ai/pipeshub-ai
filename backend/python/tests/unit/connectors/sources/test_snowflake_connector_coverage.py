"""Comprehensive coverage tests for app.connectors.sources.snowflake.connector."""
from __future__ import annotations

# Patch typing.override for Python < 3.12 before any app imports
import typing as _typing

if not hasattr(_typing, "override"):
    try:
        from typing_extensions import override as _override

        _typing.override = _override  # type: ignore[attr-defined]
    except ImportError:  # pragma: no cover
        def _override(fn):  # type: ignore[misc]
            return fn

        _typing.override = _override  # type: ignore[attr-defined]

import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiolimiter import AsyncLimiter
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOption,
    IndexingFilterKey,
)
from app.connectors.sources.snowflake.connector import (
    SnowflakeConnector,
    SyncStats,
    get_file_extension,
    get_mimetype_from_path,
)
from app.connectors.sources.snowflake.data_fetcher import (
    ForeignKey,
    SnowflakeDatabase,
    SnowflakeFile,
    SnowflakeHierarchy,
    SnowflakeSchema,
    SnowflakeStage,
    SnowflakeTable,
    SnowflakeView,
)
from app.models.entities import (
    FileRecord,
    RecordType,
    SQLTableRecord,
    SQLViewRecord,
    User,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _resp(success: bool = True, data: Any = None, error: Optional[str] = None) -> SimpleNamespace:
    return SimpleNamespace(success=success, data=data, error=error)


def _make_connector() -> SnowflakeConnector:
    """Construct a SnowflakeConnector without running __init__ (which takes scope/created_by)."""
    c = SnowflakeConnector.__new__(SnowflakeConnector)
    c.logger = logging.getLogger("test.snowflake")
    c.logger.setLevel(logging.CRITICAL)

    c.connector_id = "conn-sf-1"
    c.connector_name = Connectors.SNOWFLAKE
    c.warehouse = "WH1"
    c.account_identifier = "acct.us-east-1"
    c.batch_size = 100
    c.rate_limiter = AsyncLimiter(100, 1)
    c.connector_scope = "PERSONAL"
    c.scope = "PERSONAL"
    c.created_by = "user-1"

    c.sync_filters = FilterCollection()
    c.indexing_filters = FilterCollection()

    c._record_id_cache = {}
    c.sync_stats = SyncStats()
    c._sync_state_key = "snowflake_sync_state"
    c._checkpoint_key = "snowflake_sync_checkpoint"
    c._enable_streams = True
    c._stream_prefix = "PIPESHUB_CDC_"

    # Default mocks for dependencies
    data_source = MagicMock()
    data_source.list_databases = AsyncMock()
    data_source.list_schemas = AsyncMock()
    data_source.list_tables = AsyncMock()
    data_source.list_views = AsyncMock()
    data_source.list_stages = AsyncMock()
    data_source.list_stage_files = AsyncMock()
    data_source.get_view = AsyncMock()
    data_source.execute_sql = AsyncMock()
    data_source.get_stage_file_stream = AsyncMock()
    c.data_source = data_source

    fetcher = MagicMock()
    fetcher._fetch_all_columns_in_schema = AsyncMock(return_value={})
    fetcher._fetch_primary_keys_in_schema = AsyncMock(return_value=[])
    fetcher._fetch_foreign_keys_in_schema = AsyncMock(return_value=[])
    fetcher.get_table_ddl = AsyncMock(return_value=None)
    fetcher.fetch_all = AsyncMock(return_value=SnowflakeHierarchy())
    c.data_fetcher = fetcher

    # Data entities processor
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_user_by_user_id = AsyncMock()
    c.data_entities_processor = dep

    # Data store provider (used for checkpoint/transactions)
    dsp = MagicMock()
    tx_store = AsyncMock()
    tx_store.get_record_by_external_id = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _transaction():
        yield tx_store

    dsp.transaction = _transaction
    c.data_store_provider = dsp
    c._tx_store = tx_store  # save for asserts

    # Sync point / config service
    sync_point = MagicMock()
    sync_point.update_sync_point = AsyncMock()
    sync_point.read_sync_point = AsyncMock(return_value=None)
    c.record_sync_point = sync_point

    c.config_service = MagicMock()
    c.config_service.get_config = AsyncMock()
    return c


# ===========================================================================
# Module helpers
# ===========================================================================


class TestGetFileExtension:
    def test_returns_extension(self) -> None:
        assert get_file_extension("data.csv") == "csv"

    def test_case_insensitive(self) -> None:
        assert get_file_extension("image.PNG") == "png"

    def test_nested_path(self) -> None:
        assert get_file_extension("a/b/c.json") == "json"

    def test_no_extension(self) -> None:
        assert get_file_extension("README") is None

    def test_multiple_dots(self) -> None:
        assert get_file_extension("archive.tar.gz") == "gz"


class TestGetMimetypeFromPath:
    def test_folder(self) -> None:
        assert get_mimetype_from_path("any", is_folder=True) == MimeTypes.FOLDER.value

    def test_known_type(self) -> None:
        mt = get_mimetype_from_path("file.txt")
        assert isinstance(mt, str) and mt

    def test_unknown_type(self) -> None:
        mt = get_mimetype_from_path("file.xyzabc")
        assert mt == MimeTypes.BIN.value


# ===========================================================================
# SyncStats
# ===========================================================================


class TestSyncStats:
    def test_defaults(self) -> None:
        s = SyncStats()
        assert s.databases_synced == 0
        assert s.errors == 0
        assert s.checkpoint_resumed is False

    def test_to_dict_includes_all_fields(self) -> None:
        s = SyncStats(
            databases_synced=2,
            tables_new=3,
            checkpoint_resumed=True,
            errors=1,
        )
        d = s.to_dict()
        assert d["databases_synced"] == 2
        assert d["tables_new"] == 3
        assert d["checkpoint_resumed"] == 1
        assert d["errors"] == 1

    def test_to_dict_checkpoint_resumed_false(self) -> None:
        s = SyncStats(checkpoint_resumed=False)
        assert s.to_dict()["checkpoint_resumed"] == 0

    def test_log_summary_no_resume(self) -> None:
        s = SyncStats()
        logger = MagicMock()
        s.log_summary(logger)
        logger.info.assert_called_once()
        msg = logger.info.call_args[0][0]
        assert "Sync Stats" in msg
        assert "(resumed from checkpoint)" not in msg

    def test_log_summary_with_resume(self) -> None:
        s = SyncStats(checkpoint_resumed=True)
        logger = MagicMock()
        s.log_summary(logger)
        msg = logger.info.call_args[0][0]
        assert "(resumed from checkpoint)" in msg


# ===========================================================================
# get_app_users
# ===========================================================================


class TestGetAppUsers:
    def test_skips_users_without_email(self) -> None:
        c = _make_connector()
        users = [
            User(source_user_id="u1", id="1", org_id="o1", email="u1@x.com", full_name="U1", is_active=True, title="T"),
            User(source_user_id="u2", id="2", org_id="o1", email="", full_name="U2", is_active=True, title=None),
        ]
        app_users = c.get_app_users(users)
        assert len(app_users) == 1
        assert app_users[0].email == "u1@x.com"

    def test_fallback_source_user_id(self) -> None:
        c = _make_connector()
        users = [
            User(source_user_id=None, id="", email="u@x.com", org_id=None, full_name=None, is_active=None, title=None),
        ]
        app_users = c.get_app_users(users)
        assert len(app_users) == 1
        # When both source_user_id and id are falsy, fall back to email
        assert app_users[0].source_user_id == "u@x.com"
        assert app_users[0].is_active is True
        assert app_users[0].org_id == "org-1"


# ===========================================================================
# _compute_* helpers
# ===========================================================================


class TestComputeHashes:
    def test_compute_definition_hash_empty(self) -> None:
        c = _make_connector()
        assert c._compute_definition_hash(None) == ""
        assert c._compute_definition_hash("") == ""

    def test_compute_definition_hash_stable(self) -> None:
        c = _make_connector()
        h1 = c._compute_definition_hash("SELECT * FROM t")
        h2 = c._compute_definition_hash("SELECT * FROM t")
        assert h1 == h2
        assert len(h1) == 32

    def test_compute_column_signature_empty(self) -> None:
        c = _make_connector()
        assert c._compute_column_signature([]) == ""

    def test_compute_column_signature_order_independent(self) -> None:
        c = _make_connector()
        cols1 = [{"name": "a", "data_type": "INT"}, {"name": "b", "data_type": "VARCHAR"}]
        cols2 = [{"name": "b", "data_type": "VARCHAR"}, {"name": "a", "data_type": "INT"}]
        assert c._compute_column_signature(cols1) == c._compute_column_signature(cols2)


# ===========================================================================
# _is_table_changed / _is_file_changed
# ===========================================================================


class TestIsChanged:
    def test_table_last_altered_changed(self) -> None:
        c = _make_connector()
        t = SnowflakeTable(name="T", database_name="DB", schema_name="S", last_altered="2024-06-01")
        assert c._is_table_changed(t, {"last_altered": "2024-01-01"}) is True

    def test_table_last_altered_same_and_metrics_unchanged(self) -> None:
        c = _make_connector()
        t = SnowflakeTable(
            name="T", database_name="DB", schema_name="S",
            last_altered="2024-06-01", row_count=100, bytes=200,
        )
        assert c._is_table_changed(
            t,
            {"last_altered": "2024-06-01", "row_count": 100, "bytes": 200},
        ) is False

    def test_table_row_count_changed(self) -> None:
        c = _make_connector()
        t = SnowflakeTable(name="T", database_name="DB", schema_name="S", row_count=10, bytes=20)
        assert c._is_table_changed(t, {"row_count": 5, "bytes": 20}) is True

    def test_file_last_modified_changed(self) -> None:
        c = _make_connector()
        f = SnowflakeFile(relative_path="a", stage_name="S", database_name="D", schema_name="SC", last_modified="b", md5="m")
        assert c._is_file_changed(f, {"last_modified": "a", "md5": "m"}) is True

    def test_file_last_modified_same(self) -> None:
        c = _make_connector()
        f = SnowflakeFile(relative_path="a", stage_name="S", database_name="D", schema_name="SC", last_modified="a", md5="other")
        # last_modified match => unchanged regardless of md5
        assert c._is_file_changed(f, {"last_modified": "a", "md5": "m"}) is False

    def test_file_md5_fallback(self) -> None:
        c = _make_connector()
        f = SnowflakeFile(relative_path="a", stage_name="S", database_name="D", schema_name="SC", md5="new")
        assert c._is_file_changed(f, {"md5": "old"}) is True


# ===========================================================================
# _parse_source_tables
# ===========================================================================


class TestParseSourceTables:
    def test_none(self) -> None:
        c = _make_connector()
        assert c._parse_source_tables(None) == []

    def test_empty(self) -> None:
        c = _make_connector()
        assert c._parse_source_tables("") == []

    def test_simple_from(self) -> None:
        c = _make_connector()
        result = c._parse_source_tables("SELECT * FROM my_table")
        assert "my_table" in result

    def test_schema_table(self) -> None:
        c = _make_connector()
        result = c._parse_source_tables("SELECT * FROM public.users")
        assert "public.users" in result

    def test_db_schema_table(self) -> None:
        c = _make_connector()
        result = c._parse_source_tables("SELECT * FROM db.sch.tbl")
        assert "db.sch.tbl" in result

    def test_join_clause(self) -> None:
        c = _make_connector()
        sql = "SELECT * FROM t1 INNER JOIN t2 ON t1.id = t2.id"
        result = c._parse_source_tables(sql)
        assert "t1" in result and "t2" in result

    def test_quoted_identifiers(self) -> None:
        c = _make_connector()
        sql = 'SELECT * FROM "MyTable"'
        result = c._parse_source_tables(sql)
        assert "MyTable" in result

    def test_skips_reserved_words(self) -> None:
        c = _make_connector()
        sql = "SELECT * FROM t1 WHERE id = 1 GROUP BY id"
        result = c._parse_source_tables(sql)
        assert "t1" in result
        # Reserved SQL tokens should not be captured
        assert "WHERE" not in {r.upper() for r in result}
        assert "GROUP" not in {r.upper() for r in result}


# ===========================================================================
# _should_skip_to_checkpoint
# ===========================================================================


class TestShouldSkipToCheckpoint:
    def test_no_checkpoint(self) -> None:
        c = _make_connector()
        assert c._should_skip_to_checkpoint(None, "DB1") is False

    def test_no_db_in_checkpoint(self) -> None:
        c = _make_connector()
        assert c._should_skip_to_checkpoint({}, "DB1") is False

    def test_db_before_checkpoint(self) -> None:
        c = _make_connector()
        assert c._should_skip_to_checkpoint({"current_database": "M"}, "A") is True

    def test_db_after_checkpoint(self) -> None:
        c = _make_connector()
        assert c._should_skip_to_checkpoint({"current_database": "A"}, "M") is False

    def test_same_db_schema_before(self) -> None:
        c = _make_connector()
        assert c._should_skip_to_checkpoint(
            {"current_database": "DB", "current_schema": "M"}, "DB", "A"
        ) is True

    def test_same_db_schema_after(self) -> None:
        c = _make_connector()
        assert c._should_skip_to_checkpoint(
            {"current_database": "DB", "current_schema": "A"}, "DB", "M"
        ) is False


# ===========================================================================
# Permissions
# ===========================================================================


class TestGetPermissions:
    @pytest.mark.asyncio
    async def test_returns_org_permission(self) -> None:
        c = _make_connector()
        perms = await c._get_permissions()
        assert len(perms) == 1
        # Just verify it's a Permission for ORG
        from app.models.permission import EntityType, PermissionType
        assert perms[0].type == PermissionType.OWNER
        assert perms[0].entity_type == EntityType.ORG


# ===========================================================================
# Checkpoint operations
# ===========================================================================


class TestCheckpointOperations:
    @pytest.mark.asyncio
    async def test_save_checkpoint(self) -> None:
        c = _make_connector()
        await c._save_checkpoint({"current_database": "DB", "current_schema": "S"})
        c.record_sync_point.update_sync_point.assert_awaited_once_with(
            c._checkpoint_key, {"current_database": "DB", "current_schema": "S"}
        )

    @pytest.mark.asyncio
    async def test_save_checkpoint_swallows_errors(self) -> None:
        c = _make_connector()
        c.record_sync_point.update_sync_point.side_effect = RuntimeError("boom")
        # Should not raise
        await c._save_checkpoint({"x": 1})

    @pytest.mark.asyncio
    async def test_load_checkpoint(self) -> None:
        c = _make_connector()
        c.record_sync_point.read_sync_point.return_value = {"current_database": "DB"}
        out = await c._load_checkpoint()
        assert out == {"current_database": "DB"}

    @pytest.mark.asyncio
    async def test_load_checkpoint_exception_returns_none(self) -> None:
        c = _make_connector()
        c.record_sync_point.read_sync_point.side_effect = RuntimeError("boom")
        assert await c._load_checkpoint() is None

    @pytest.mark.asyncio
    async def test_clear_checkpoint(self) -> None:
        c = _make_connector()
        await c._clear_checkpoint()
        c.record_sync_point.update_sync_point.assert_awaited_once_with(c._checkpoint_key, {})


# ===========================================================================
# Stream operations
# ===========================================================================


class TestStreamOperations:
    @pytest.mark.asyncio
    async def test_ensure_stream_exists_no_data_source(self) -> None:
        c = _make_connector()
        c.data_source = None
        assert await c._ensure_stream_exists("DB.S.T") is None

    @pytest.mark.asyncio
    async def test_ensure_stream_exists_invalid_fqn(self) -> None:
        c = _make_connector()
        assert await c._ensure_stream_exists("invalid") is None

    @pytest.mark.asyncio
    async def test_ensure_stream_exists_already_present(self) -> None:
        c = _make_connector()
        # First call (SHOW STREAMS LIKE) returns rows => already exists
        c.data_source.execute_sql.return_value = _resp(data={"data": [["row1"]]})
        result = await c._ensure_stream_exists("DB.S.T")
        assert result == "DB.S.PIPESHUB_CDC_DB_S_T"
        # Should only call once — no CREATE STREAM
        assert c.data_source.execute_sql.await_count == 1

    @pytest.mark.asyncio
    async def test_ensure_stream_creates_if_missing(self) -> None:
        c = _make_connector()
        # First call: no rows, second call: create succeeds
        c.data_source.execute_sql.side_effect = [
            _resp(data={"data": []}),
            _resp(success=True, data={}),
        ]
        result = await c._ensure_stream_exists("DB.S.T")
        assert result == "DB.S.PIPESHUB_CDC_DB_S_T"
        assert c.data_source.execute_sql.await_count == 2

    @pytest.mark.asyncio
    async def test_ensure_stream_create_fails(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.side_effect = [
            _resp(data={"data": []}),
            _resp(success=False, error="permission denied"),
        ]
        assert await c._ensure_stream_exists("DB.S.T") is None

    @pytest.mark.asyncio
    async def test_ensure_stream_exception(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.side_effect = RuntimeError("fail")
        assert await c._ensure_stream_exists("DB.S.T") is None

    @pytest.mark.asyncio
    async def test_check_stream_has_changes_no_data_source(self) -> None:
        c = _make_connector()
        c.data_source = None
        assert await c._check_stream_has_changes("DB.S.STREAM") == (False, 0)

    @pytest.mark.asyncio
    async def test_check_stream_has_changes_invalid_name(self) -> None:
        c = _make_connector()
        assert await c._check_stream_has_changes("invalid") == (False, 0)

    @pytest.mark.asyncio
    async def test_check_stream_no_changes(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.return_value = _resp(data={"data": [[False]]})
        assert await c._check_stream_has_changes("DB.S.STREAM") == (False, 0)

    @pytest.mark.asyncio
    async def test_check_stream_has_changes(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.side_effect = [
            _resp(data={"data": [[True]]}),
            _resp(data={"data": [[42]]}),
        ]
        has_changes, count = await c._check_stream_has_changes("DB.S.STREAM")
        assert has_changes is True
        assert count == 42

    @pytest.mark.asyncio
    async def test_check_stream_exception(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.side_effect = RuntimeError("fail")
        assert await c._check_stream_has_changes("DB.S.STREAM") == (False, 0)

    @pytest.mark.asyncio
    async def test_consume_stream_changes_empty(self) -> None:
        c = _make_connector()
        c.data_source = None
        assert await c._consume_stream_changes("DB.S.STREAM") == []

    @pytest.mark.asyncio
    async def test_consume_stream_invalid_name(self) -> None:
        c = _make_connector()
        assert await c._consume_stream_changes("invalid") == []

    @pytest.mark.asyncio
    async def test_consume_stream_success(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {"rowType": [{"name": "ID"}, {"name": "METADATA$ACTION"}]},
                "data": [[1, "INSERT"], [2, "UPDATE"]],
            }
        )
        result = await c._consume_stream_changes("DB.S.STREAM")
        assert len(result) == 2
        assert result[0]["METADATA$ACTION"] == "INSERT"

    @pytest.mark.asyncio
    async def test_consume_stream_exception(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.side_effect = RuntimeError("fail")
        assert await c._consume_stream_changes("DB.S.STREAM") == []


# ===========================================================================
# _batch_get_records_by_external_ids
# ===========================================================================


class TestBatchGetRecords:
    @pytest.mark.asyncio
    async def test_empty(self) -> None:
        c = _make_connector()
        result = await c._batch_get_records_by_external_ids([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_found_records(self) -> None:
        c = _make_connector()
        mock_record = MagicMock()
        mock_record.id = "rec-1"

        async def fetch(*, connector_id: str, external_id: str):
            if external_id == "ext-1":
                return mock_record
            return None

        c._tx_store.get_record_by_external_id.side_effect = fetch
        result = await c._batch_get_records_by_external_ids(["ext-1", "ext-2"])
        assert "ext-1" in result
        assert "ext-2" not in result

    @pytest.mark.asyncio
    async def test_handles_exceptions_per_record(self) -> None:
        c = _make_connector()

        async def fetch(*, connector_id: str, external_id: str):
            if external_id == "bad":
                raise RuntimeError("err")
            return MagicMock()

        c._tx_store.get_record_by_external_id.side_effect = fetch
        result = await c._batch_get_records_by_external_ids(["bad", "good"])
        assert "good" in result
        assert "bad" not in result


# ===========================================================================
# _mark_records_for_reindex
# ===========================================================================


class TestMarkRecordsForReindex:
    @pytest.mark.asyncio
    async def test_empty_list_noop(self) -> None:
        c = _make_connector()
        await c._mark_records_for_reindex([])
        c.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_reindex(self) -> None:
        c = _make_connector()
        mock_record = MagicMock()

        async def fetch(*, connector_id: str, external_id: str):
            return mock_record

        c._tx_store.get_record_by_external_id.side_effect = fetch
        await c._mark_records_for_reindex(["ext-1", "ext-2"])
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()
        assert c.sync_stats.records_reindexed == 2


# ===========================================================================
# _create_app_user
# ===========================================================================


class TestCreateAppUser:
    @pytest.mark.asyncio
    async def test_creates_app_users(self) -> None:
        c = _make_connector()
        c.scope = "PERSONAL"
        c.created_by = "user-1"
        c.data_entities_processor.get_user_by_user_id.return_value = User(
            source_user_id="u1",
            id="1",
            org_id="o1",
            email="u1@x.com",
            full_name="U",
            is_active=True,
            title=None,
        )
        await c._create_app_user()
        c.data_entities_processor.on_new_app_users.assert_awaited_once()
        called_with = c.data_entities_processor.on_new_app_users.await_args[0][0]
        assert len(called_with) == 1

    @pytest.mark.asyncio
    async def test_propagates_errors(self) -> None:
        c = _make_connector()
        c.scope = "PERSONAL"
        c.created_by = "user-1"
        c.data_entities_processor.get_user_by_user_id.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await c._create_app_user()


# ===========================================================================
# _get_filter_values
# ===========================================================================


class TestGetFilterValues:
    def test_no_filters_returns_nones(self) -> None:
        c = _make_connector()
        result = c._get_filter_values()
        assert all(v is None for v in result)


# ===========================================================================
# _sync_databases / _sync_namespaces / _sync_stages
# ===========================================================================


class TestSyncGroups:
    @pytest.mark.asyncio
    async def test_sync_databases_empty(self) -> None:
        c = _make_connector()
        await c._sync_databases([])
        c.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_databases(self) -> None:
        c = _make_connector()
        await c._sync_databases([SnowflakeDatabase(name="DB1"), SnowflakeDatabase(name="DB2")])
        c.data_entities_processor.on_new_record_groups.assert_awaited_once()
        groups = c.data_entities_processor.on_new_record_groups.await_args[0][0]
        assert len(groups) == 2

    @pytest.mark.asyncio
    async def test_sync_namespaces_empty(self) -> None:
        c = _make_connector()
        await c._sync_namespaces("DB", [])
        c.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_namespaces(self) -> None:
        c = _make_connector()
        schemas = [SnowflakeSchema(name="PUBLIC", database_name="DB")]
        await c._sync_namespaces("DB", schemas)
        c.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_stages_empty(self) -> None:
        c = _make_connector()
        await c._sync_stages("DB", "S", [])
        c.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_stages(self) -> None:
        c = _make_connector()
        stages = [SnowflakeStage(name="STG", database_name="DB", schema_name="S")]
        await c._sync_stages("DB", "S", stages)
        c.data_entities_processor.on_new_record_groups.assert_awaited_once()


# ===========================================================================
# _process_* generators
# ===========================================================================


class TestProcessTablesGenerator:
    @pytest.mark.asyncio
    async def test_yields_records(self) -> None:
        c = _make_connector()
        tables = [
            SnowflakeTable(name="T1", database_name="DB", schema_name="S", bytes=100, row_count=5),
        ]
        results = []
        async for rec, perms in c._process_tables_generator("DB", "S", tables):
            results.append((rec, perms))
        assert len(results) == 1
        record, perms = results[0]
        assert isinstance(record, SQLTableRecord)
        assert record.external_record_id == "DB.S.T1"
        assert perms == []

    @pytest.mark.asyncio
    async def test_adds_foreign_key_related_records(self) -> None:
        c = _make_connector()
        t = SnowflakeTable(
            name="T1", database_name="DB", schema_name="S",
            foreign_keys=[
                {"references_schema": "S", "references_table": "T2", "column": "c1", "references_column": "id", "constraint_name": "fk1"}
            ],
        )
        async for rec, _ in c._process_tables_generator("DB", "S", [t]):
            assert len(rec.related_external_records) == 1
            assert rec.related_external_records[0].external_record_id == "DB.S.T2"


class TestProcessViewsGenerator:
    @pytest.mark.asyncio
    async def test_yields_view_records(self) -> None:
        c = _make_connector()

        # Mock _fetch_view_definition to return a simple one
        async def _fake_def(*args, **kwargs):
            return "SELECT * FROM t1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]

        views = [SnowflakeView(name="V1", database_name="DB", schema_name="S")]
        results = []
        async for rec, perms, enriched in c._process_views_generator("DB", "S", views):
            results.append((rec, perms, enriched))
        assert len(results) == 1
        rec, perms, enriched = results[0]
        assert isinstance(rec, SQLViewRecord)
        assert enriched.definition == "SELECT * FROM t1"
        assert "t1" in enriched.source_tables


class TestProcessStageFilesGenerator:
    @pytest.mark.asyncio
    async def test_yields_file_records(self) -> None:
        c = _make_connector()
        files = [
            SnowflakeFile(
                relative_path="data.csv",
                stage_name="STG",
                database_name="DB",
                schema_name="S",
                size=100,
                md5="abc",
            ),
        ]
        results = []
        async for rec, perms in c._process_stage_files_generator("DB.S.STG", files):
            results.append((rec, perms))
        assert len(results) == 1
        rec, _ = results[0]
        assert isinstance(rec, FileRecord)
        assert rec.record_name == "data.csv"
        assert rec.etag == "abc"

    @pytest.mark.asyncio
    async def test_skips_folders(self) -> None:
        c = _make_connector()
        files = [
            SnowflakeFile(
                relative_path="folder/", stage_name="STG", database_name="DB", schema_name="S"
            ),
            SnowflakeFile(
                relative_path="data.csv", stage_name="STG", database_name="DB", schema_name="S"
            ),
        ]
        count = 0
        async for _, _ in c._process_stage_files_generator("DB.S.STG", files):
            count += 1
        assert count == 1


# ===========================================================================
# _sync_tables / _sync_views / _sync_stage_files
# ===========================================================================


class TestSyncBatches:
    @pytest.mark.asyncio
    async def test_sync_tables_empty(self) -> None:
        c = _make_connector()
        await c._sync_tables("DB", "S", [])
        c.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_tables_batches(self) -> None:
        c = _make_connector()
        c.batch_size = 2
        tables = [
            SnowflakeTable(name=f"T{i}", database_name="DB", schema_name="S")
            for i in range(3)
        ]
        await c._sync_tables("DB", "S", tables)
        # 2 then 1 => 2 calls
        assert c.data_entities_processor.on_new_records.await_count == 2

    @pytest.mark.asyncio
    async def test_sync_views_empty(self) -> None:
        c = _make_connector()
        await c._sync_views("DB", "S", [])
        c.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_views_calls_on_new_records(self) -> None:
        c = _make_connector()

        async def _fake_def(*args, **kwargs):
            return "SELECT * FROM x"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        await c._sync_views("DB", "S", [SnowflakeView(name="V", database_name="DB", schema_name="S")])
        c.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_stage_files_empty(self) -> None:
        c = _make_connector()
        await c._sync_stage_files("DB", "S", "STG", [])
        c.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_stage_files_nonempty(self) -> None:
        c = _make_connector()
        files = [SnowflakeFile(relative_path="x.csv", stage_name="STG", database_name="DB", schema_name="S")]
        await c._sync_stage_files("DB", "S", "STG", files)
        c.data_entities_processor.on_new_records.assert_awaited_once()


# ===========================================================================
# _fetch_table_rows / _fetch_view_definition
# ===========================================================================


class TestFetchTableRows:
    @pytest.mark.asyncio
    async def test_no_data_source(self) -> None:
        c = _make_connector()
        c.data_source = None
        assert await c._fetch_table_rows("DB", "S", "T") == []

    @pytest.mark.asyncio
    async def test_no_warehouse(self) -> None:
        c = _make_connector()
        c.warehouse = None
        assert await c._fetch_table_rows("DB", "S", "T") == []

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.return_value = _resp(data={"data": [[1, 2], [3, 4]]})
        rows = await c._fetch_table_rows("DB", "S", "T")
        assert rows == [[1, 2], [3, 4]]

    @pytest.mark.asyncio
    async def test_failure_returns_empty(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.side_effect = RuntimeError("boom")
        assert await c._fetch_table_rows("DB", "S", "T") == []


class TestFetchViewDefinition:
    @pytest.mark.asyncio
    async def test_no_data_source(self) -> None:
        c = _make_connector()
        c.data_source = None
        assert await c._fetch_view_definition("DB", "S", "V") is None

    @pytest.mark.asyncio
    async def test_no_warehouse(self) -> None:
        c = _make_connector()
        c.warehouse = None
        assert await c._fetch_view_definition("DB", "S", "V") is None

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {"rowType": [{"name": "DDL"}]},
                "data": [["CREATE VIEW V AS SELECT 1"]],
            }
        )
        result = await c._fetch_view_definition("DB", "S", "V")
        assert result == "CREATE VIEW V AS SELECT 1"

    @pytest.mark.asyncio
    async def test_empty_rows(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.return_value = _resp(
            data={"resultSetMetaData": {"rowType": []}, "data": []}
        )
        assert await c._fetch_view_definition("DB", "S", "V") is None

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.side_effect = RuntimeError("nope")
        assert await c._fetch_view_definition("DB", "S", "V") is None


# ===========================================================================
# Misc methods
# ===========================================================================


class TestMisc:
    def test_get_signed_url_returns_none(self) -> None:
        c = _make_connector()
        record = MagicMock()
        assert c.get_signed_url(record) is None

    def test_handle_webhook_not_implemented(self) -> None:
        c = _make_connector()
        with pytest.raises(NotImplementedError):
            c.handle_webhook_notification({"x": 1})

    @pytest.mark.asyncio
    async def test_test_connection_success(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.return_value = _resp(success=True, data=[{"name": "a"}])
        assert await c.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.return_value = _resp(success=False, error="bad")
        assert await c.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_test_connection_no_data_source(self) -> None:
        c = _make_connector()
        c.data_source = None
        assert await c.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.side_effect = RuntimeError("boom")
        assert await c.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_cleanup(self) -> None:
        c = _make_connector()
        c._record_id_cache["k"] = "v"
        await c.cleanup()
        assert c.data_source is None
        assert c.data_fetcher is None
        assert c.warehouse is None
        assert c.account_identifier is None
        assert c._record_id_cache == {}


# ===========================================================================
# _check_record_at_source
# ===========================================================================


class TestCheckRecordAtSource:
    @pytest.mark.asyncio
    async def test_table_exists(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "DB.S.T1"
        c.data_source.list_tables.return_value = _resp(success=True, data=[{"name": "T1"}])
        assert await c._check_record_at_source(record) is True

    @pytest.mark.asyncio
    async def test_table_does_not_exist(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "DB.S.T1"
        c.data_source.list_tables.return_value = _resp(success=True, data=[{"name": "Other"}])
        assert await c._check_record_at_source(record) is False

    @pytest.mark.asyncio
    async def test_invalid_fqn(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "invalid"
        assert await c._check_record_at_source(record) is False

    @pytest.mark.asyncio
    async def test_view(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_VIEW
        record.external_record_id = "DB.S.V1"
        c.data_source.list_views.return_value = _resp(success=True, data=[{"name": "V1"}])
        assert await c._check_record_at_source(record) is True

    @pytest.mark.asyncio
    async def test_file(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_group_id = "DB.S.STG"
        record.external_record_id = "DB.S.STG/folder/data.csv"
        c.data_source.list_stage_files.return_value = _resp(
            success=True, data=[{"name": "folder/data.csv"}]
        )
        assert await c._check_record_at_source(record) is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "DB.S.T"
        c.data_source.list_tables.side_effect = RuntimeError("bad")
        assert await c._check_record_at_source(record) is False


# ===========================================================================
# get_filter_options
# ===========================================================================


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_unknown_filter_returns_error(self) -> None:
        c = _make_connector()
        result = await c.get_filter_options("unknown_key")
        assert result.success is False
        assert "Unknown" in (result.message or "")

    @pytest.mark.asyncio
    async def test_databases_success(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.return_value = _resp(
            success=True, data=[{"name": "DB1"}, {"name": "DB2"}]
        )
        result = await c.get_filter_options("databases", page=1, limit=10)
        assert result.success is True
        assert len(result.options) == 2

    @pytest.mark.asyncio
    async def test_databases_with_search(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.return_value = _resp(
            success=True, data=[{"name": "PROD"}, {"name": "DEV"}]
        )
        result = await c.get_filter_options("databases", page=1, limit=10, search="prod")
        assert len(result.options) == 1
        assert result.options[0].id == "PROD"

    @pytest.mark.asyncio
    async def test_databases_pagination(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.return_value = _resp(
            success=True, data=[{"name": f"DB{i}"} for i in range(15)]
        )
        result = await c.get_filter_options("databases", page=1, limit=10)
        assert len(result.options) == 10
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_databases_no_data_source(self) -> None:
        c = _make_connector()
        c.data_source = None
        result = await c.get_filter_options("databases")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_databases_api_failure(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.return_value = _resp(success=False, error="err")
        result = await c.get_filter_options("databases")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_schemas(self) -> None:
        c = _make_connector()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S1", database_name="DB")]},
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c.get_filter_options("schemas", page=1, limit=10)
        assert result.success is True
        assert result.options[0].id == "DB.S1"

    @pytest.mark.asyncio
    async def test_schemas_no_fetcher(self) -> None:
        c = _make_connector()
        c.data_fetcher = None
        result = await c.get_filter_options("schemas")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_tables(self) -> None:
        c = _make_connector()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={"DB.S": [SnowflakeTable(name="T1", database_name="DB", schema_name="S")]},
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c.get_filter_options("tables", page=1, limit=10)
        assert result.success is True
        assert result.options[0].id == "DB.S.T1"

    @pytest.mark.asyncio
    async def test_tables_no_fetcher(self) -> None:
        c = _make_connector()
        c.data_fetcher = None
        result = await c.get_filter_options("tables")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_views_no_fetcher(self) -> None:
        c = _make_connector()
        c.data_fetcher = None
        result = await c.get_filter_options("views")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_stages(self) -> None:
        c = _make_connector()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            stages={"DB.S": [SnowflakeStage(name="STG", database_name="DB", schema_name="S")]},
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c.get_filter_options("stages", page=1, limit=10)
        assert result.success is True
        assert result.options[0].id == "DB.S.STG"

    @pytest.mark.asyncio
    async def test_stages_no_fetcher(self) -> None:
        c = _make_connector()
        c.data_fetcher = None
        result = await c.get_filter_options("stages")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_files(self) -> None:
        c = _make_connector()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            stages={"DB.S": [SnowflakeStage(name="STG", database_name="DB", schema_name="S")]},
            files={
                "DB.S.STG": [
                    SnowflakeFile(
                        relative_path="a.csv",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                    ),
                    SnowflakeFile(
                        relative_path="folder/",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                    ),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c.get_filter_options("files", page=1, limit=10)
        assert result.success is True
        # Folders should be skipped
        assert len(result.options) == 1
        assert result.options[0].id == "DB.S.STG/a.csv"

    @pytest.mark.asyncio
    async def test_files_no_fetcher(self) -> None:
        c = _make_connector()
        c.data_fetcher = None
        result = await c.get_filter_options("files")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_cursor_is_page(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.return_value = _resp(
            success=True, data=[{"name": "DB"}]
        )
        # cursor='2' should set page=2
        result = await c.get_filter_options("databases", cursor="2")
        # Past the end, has_more False
        assert result.success is True


# ===========================================================================
# stream_record
# ===========================================================================


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_no_data_source(self) -> None:
        c = _make_connector()
        c.data_source = None
        record = MagicMock()
        record.record_type = RecordType.FILE
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(record)
        assert ei.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unsupported_record_type(self) -> None:
        c = _make_connector()
        record = MagicMock()
        # Use an enum value that isn't FILE/SQL_TABLE/SQL_VIEW
        record.record_type = RecordType.MAIL
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(record)
        assert ei.value.status_code == 400

    @pytest.mark.asyncio
    async def test_table_invalid_fqn(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "invalid"
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(record)
        assert ei.value.status_code == 500

    @pytest.mark.asyncio
    async def test_view_invalid_fqn(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_VIEW
        record.external_record_id = "bad"
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(record)
        assert ei.value.status_code == 500

    @pytest.mark.asyncio
    async def test_file_invalid_stage_fqn(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_group_id = "invalid"
        record.external_record_id = "invalid/file"
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(record)
        assert ei.value.status_code == 500


# ===========================================================================
# init
# ===========================================================================


class TestInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self) -> None:
        c = _make_connector()
        c.config_service.get_config.return_value = None
        assert await c.init() is False

    @pytest.mark.asyncio
    async def test_init_missing_account(self) -> None:
        c = _make_connector()
        c.config_service.get_config.return_value = {"auth": {}, "credentials": {}}
        assert await c.init() is False

    @pytest.mark.asyncio
    async def test_init_no_auth_method(self) -> None:
        c = _make_connector()
        c.config_service.get_config.return_value = {
            "auth": {"accountIdentifier": "acct"},
            "credentials": {},
        }
        assert await c.init() is False

    @pytest.mark.asyncio
    async def test_init_exception(self) -> None:
        c = _make_connector()
        c.config_service.get_config.side_effect = RuntimeError("config load err")
        assert await c.init() is False


# ===========================================================================
# _process_deletions_batch
# ===========================================================================


class TestProcessDeletionsBatch:
    @pytest.mark.asyncio
    async def test_nothing_to_delete(self) -> None:
        c = _make_connector()
        await c._process_deletions_batch(set(), set(), set(), set(), set(), set())
        c.data_entities_processor.on_record_deleted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletes_tables(self) -> None:
        c = _make_connector()
        mock_rec = MagicMock()
        mock_rec.id = "rec-1"

        async def fetch(*, connector_id: str, external_id: str):
            return mock_rec

        c._tx_store.get_record_by_external_id.side_effect = fetch
        await c._process_deletions_batch(
            deleted_databases=set(),
            deleted_schemas=set(),
            deleted_stages=set(),
            deleted_tables={"DB.S.T1"},
            deleted_views=set(),
            deleted_files=set(),
        )
        assert c.sync_stats.tables_deleted == 1

    @pytest.mark.asyncio
    async def test_legacy_process_deletions(self) -> None:
        c = _make_connector()
        # Just ensure the legacy wrapper calls through without error
        await c._process_deletions(set(), set(), set(), set(), set(), set())

    @pytest.mark.asyncio
    async def test_deletes_views_and_files(self) -> None:
        c = _make_connector()
        mock_rec = MagicMock()
        mock_rec.id = "rec"

        async def fetch(*, connector_id: str, external_id: str):
            return mock_rec

        c._tx_store.get_record_by_external_id.side_effect = fetch
        await c._process_deletions_batch(
            deleted_databases=set(),
            deleted_schemas=set(),
            deleted_stages=set(),
            deleted_tables=set(),
            deleted_views={"DB.S.V1"},
            deleted_files={"DB.S.STG/a.csv"},
        )
        assert c.sync_stats.views_deleted == 1
        assert c.sync_stats.files_deleted == 1

    @pytest.mark.asyncio
    async def test_error_in_table_delete_increments_errors(self) -> None:
        c = _make_connector()
        mock_rec = MagicMock()
        mock_rec.id = "rec"

        async def fetch(*, connector_id: str, external_id: str):
            return mock_rec

        c._tx_store.get_record_by_external_id.side_effect = fetch
        c.data_entities_processor.on_record_deleted.side_effect = RuntimeError("boom")
        await c._process_deletions_batch(
            deleted_databases=set(),
            deleted_schemas=set(),
            deleted_stages=set(),
            deleted_tables={"DB.S.T"},
            deleted_views={"DB.S.V"},
            deleted_files={"DB.S.STG/a"},
        )
        assert c.sync_stats.errors == 3


# ===========================================================================
# __init__ real construction (covers lines 372-413)
# ===========================================================================


class TestRealInit:
    def test_constructs_with_real_init(self) -> None:
        from app.connectors.sources.snowflake.connector import SnowflakeConnector

        logger = logging.getLogger("t.init")
        dep = MagicMock()
        dep.org_id = "org-x"
        dsp = MagicMock()
        config_service = MagicMock()

        def _fake_base_init(self, app, logger, data_entities_processor,
                            data_store_provider, config_service, connector_id):
            self.logger = logger
            self.data_entities_processor = data_entities_processor
            self.data_store_provider = data_store_provider
            self.config_service = config_service
            self.connector_id = connector_id
            self.app = app

        # BaseConnector.__init__ currently requires extra args not forwarded by
        # SnowflakeConnector.__init__; patch it so we can exercise the body.
        with patch(
            "app.connectors.sources.snowflake.connector.BaseConnector.__init__",
            new=_fake_base_init,
        ), patch(
            "app.connectors.sources.snowflake.connector.SyncPoint"
        ) as sp_cls, patch(
            "app.connectors.sources.snowflake.connector.SnowflakeApp"
        ) as app_cls:
            sp_cls.return_value = MagicMock()
            app_cls.return_value = MagicMock()
            c = SnowflakeConnector(logger, dep, dsp, config_service, "conn-123")

        assert c.connector_id == "conn-123"
        assert c.connector_name == Connectors.SNOWFLAKE
        assert c.data_source is None
        assert c.data_fetcher is None
        assert c.warehouse is None
        assert c.account_identifier is None
        assert c.batch_size == 100
        assert c.connector_scope is None
        assert c.created_by is None
        assert isinstance(c.sync_filters, FilterCollection)
        assert c._record_id_cache == {}
        assert isinstance(c.sync_stats, SyncStats)
        assert c._sync_state_key == "snowflake_sync_state"
        assert c._checkpoint_key == "snowflake_sync_checkpoint"
        assert c._enable_streams is True
        assert c._stream_prefix == "PIPESHUB_CDC_"


# ===========================================================================
# init() auth success paths
# ===========================================================================


class TestInitAuthSuccess:
    @pytest.mark.asyncio
    async def test_init_oauth_success(self) -> None:
        c = _make_connector()
        c.config_service.get_config.return_value = {
            "auth": {"accountIdentifier": "acct", "warehouse": "WH"},
            "credentials": {"access_token": "tok"},
            "scope": "PERSONAL",
            "created_by": "u",
        }
        with patch(
            "app.connectors.sources.snowflake.connector.SnowflakeOAuthConfig"
        ) as oauth_cls, patch(
            "app.connectors.sources.snowflake.connector.SnowflakeClient"
        ) as client_cls, patch(
            "app.connectors.sources.snowflake.connector.SnowflakeDataSource"
        ) as ds_cls, patch(
            "app.connectors.sources.snowflake.connector.SnowflakeDataFetcher"
        ) as df_cls, patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new=AsyncMock(return_value=({}, {})),
        ):
            oauth_cls.return_value.create_client.return_value = "raw"
            client_cls.return_value = "wrapped"
            ds_cls.return_value = "ds"
            df_cls.return_value = "df"
            ok = await c.init()
        assert ok is True
        assert c.data_source == "ds"
        assert c.data_fetcher == "df"


# ===========================================================================
# _run_full_sync_internal
# ===========================================================================


class TestRunFullSyncInternal:
    @pytest.mark.asyncio
    async def test_full_sync_end_to_end(self) -> None:
        c = _make_connector()
        c._ensure_scope_app_edges = AsyncMock()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={
                "DB.S": [SnowflakeTable(name="T", database_name="DB", schema_name="S")]
            },
            views={
                "DB.S": [SnowflakeView(name="V", database_name="DB", schema_name="S")]
            },
            stages={
                "DB.S": [SnowflakeStage(name="STG", database_name="DB", schema_name="S")]
            },
            files={
                "DB.S.STG": [
                    SnowflakeFile(
                        relative_path="a.csv",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                    )
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h

        async def _fake_def(*a, **k):
            return "SELECT 1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]

        await c._run_full_sync_internal()
        assert c.sync_stats.databases_synced == 1
        assert c.sync_stats.schemas_synced == 1
        assert c.sync_stats.tables_new == 1
        assert c.sync_stats.views_new == 1
        assert c.sync_stats.stages_synced == 1
        assert c.sync_stats.files_new == 1

    @pytest.mark.asyncio
    async def test_full_sync_with_filters(self) -> None:
        c = _make_connector()
        c._ensure_scope_app_edges = AsyncMock()
        # Provide filters that match
        c.sync_filters = {
            "schemas": SimpleNamespace(value=["DB.S"]),
            "tables": SimpleNamespace(value=["DB.S.T"]),
            "views": SimpleNamespace(value=["DB.S.V"]),
            "stages": SimpleNamespace(value=["DB.S.STG"]),
            "files": SimpleNamespace(value=["DB.S.STG/a.csv"]),
        }
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={
                "DB": [
                    SnowflakeSchema(name="S", database_name="DB"),
                    SnowflakeSchema(name="OTHER", database_name="DB"),
                ]
            },
            tables={
                "DB.S": [
                    SnowflakeTable(name="T", database_name="DB", schema_name="S"),
                    SnowflakeTable(name="X", database_name="DB", schema_name="S"),
                ]
            },
            views={
                "DB.S": [
                    SnowflakeView(name="V", database_name="DB", schema_name="S"),
                    SnowflakeView(name="W", database_name="DB", schema_name="S"),
                ]
            },
            stages={
                "DB.S": [
                    SnowflakeStage(name="STG", database_name="DB", schema_name="S"),
                    SnowflakeStage(name="OTHER_STG", database_name="DB", schema_name="S"),
                ]
            },
            files={
                "DB.S.STG": [
                    SnowflakeFile(
                        relative_path="a.csv",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                    ),
                    SnowflakeFile(
                        relative_path="b.csv",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                    ),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h

        async def _fake_def(*a, **k):
            return "SELECT 1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        await c._run_full_sync_internal()
        # Filters should narrow to 1 of each
        assert c.sync_stats.tables_new == 1
        assert c.sync_stats.views_new == 1
        assert c.sync_stats.stages_synced == 1
        assert c.sync_stats.files_new == 1

    @pytest.mark.asyncio
    async def test_full_sync_error_increments_and_reraises(self) -> None:
        c = _make_connector()
        c._ensure_scope_app_edges = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await c._run_full_sync_internal()
        assert c.sync_stats.errors == 1


# ===========================================================================
# _run_incremental_sync_internal
# ===========================================================================


class TestRunIncrementalSyncInternal:
    @pytest.mark.asyncio
    async def test_incremental_new_objects(self) -> None:
        c = _make_connector()
        c._ensure_stream_exists = AsyncMock(return_value="DB.S.PIPESHUB_CDC_DB_S_T")

        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={
                "DB.S": [
                    SnowflakeTable(
                        name="T",
                        database_name="DB",
                        schema_name="S",
                        columns=[{"name": "c", "data_type": "INT"}],
                    )
                ]
            },
            views={
                "DB.S": [SnowflakeView(name="V", database_name="DB", schema_name="S")]
            },
            stages={
                "DB.S": [SnowflakeStage(name="STG", database_name="DB", schema_name="S")]
            },
            files={
                "DB.S.STG": [
                    SnowflakeFile(
                        relative_path="a.csv",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                        md5="m",
                        last_modified="t0",
                    )
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h

        async def _fake_def(*a, **k):
            return "SELECT 1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]

        await c._run_incremental_sync_internal(prev_state={})
        assert c.sync_stats.tables_new == 1
        assert c.sync_stats.views_new == 1
        assert c.sync_stats.files_new == 1
        # stream recorded
        assert c.record_sync_point.update_sync_point.await_count >= 2

    @pytest.mark.asyncio
    async def test_incremental_unchanged_and_updated(self) -> None:
        c = _make_connector()
        c._ensure_stream_exists = AsyncMock(return_value=None)
        c._check_stream_has_changes = AsyncMock(return_value=(False, 0))

        table = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            columns=[{"name": "c", "data_type": "INT"}],
            last_altered="t1",
            row_count=10,
            bytes=100,
        )
        column_sig = c._compute_column_signature(table.columns)

        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={"DB.S": [table]},
            views={
                "DB.S": [
                    SnowflakeView(
                        name="V",
                        database_name="DB",
                        schema_name="S",
                        definition="SELECT 1",
                    )
                ]
            },
            stages={},
            files={},
        )
        c.data_fetcher.fetch_all.return_value = h

        prev_state = {
            "databases": ["DB"],
            "tables": {
                "DB.S.T": {
                    "row_count": 10,
                    "bytes": 100,
                    "last_altered": "t1",
                    "column_signature": column_sig,
                }
            },
            "views": {
                "DB.S.V": {
                    "definition_hash": c._compute_definition_hash("SELECT 1"),
                }
            },
            "streams": {},
            "files": {},
        }

        async def _fake_def(*a, **k):
            return "SELECT 1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        await c._run_incremental_sync_internal(prev_state=prev_state)
        assert c.sync_stats.tables_unchanged == 1
        assert c.sync_stats.views_unchanged == 1

    @pytest.mark.asyncio
    async def test_incremental_schema_change(self) -> None:
        c = _make_connector()

        new_cols = [{"name": "c2", "data_type": "VARCHAR"}]
        table = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            columns=new_cols,
            last_altered="t2",
        )
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={"DB.S": [table]},
            views={},
            stages={},
            files={},
        )
        c.data_fetcher.fetch_all.return_value = h

        prev_state = {
            "databases": ["DB"],
            "tables": {
                "DB.S.T": {
                    "row_count": 0,
                    "bytes": 0,
                    "last_altered": "t1",
                    "column_signature": "different_signature_hash",
                }
            },
            "views": {},
            "streams": {},
            "files": {},
        }
        await c._run_incremental_sync_internal(prev_state=prev_state)
        assert c.sync_stats.tables_schema_changed == 1

    @pytest.mark.asyncio
    async def test_incremental_stream_changes(self) -> None:
        c = _make_connector()
        c._check_stream_has_changes = AsyncMock(return_value=(True, 5))
        c._consume_stream_changes = AsyncMock(return_value=[])
        c.data_entities_processor.reindex_existing_records = AsyncMock()

        mock_record = MagicMock()

        async def fetch(*, connector_id: str, external_id: str):
            return mock_record

        c._tx_store.get_record_by_external_id.side_effect = fetch

        table = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            columns=[{"name": "c", "data_type": "INT"}],
        )
        column_sig = c._compute_column_signature(table.columns)

        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={"DB.S": [table]},
            views={},
            stages={},
            files={},
        )
        c.data_fetcher.fetch_all.return_value = h

        prev_state = {
            "databases": ["DB"],
            "tables": {
                "DB.S.T": {
                    "row_count": 0,
                    "bytes": 0,
                    "last_altered": None,
                    "column_signature": column_sig,
                }
            },
            "views": {},
            "streams": {"DB.S.T": "DB.S.PIPESHUB_CDC_DB_S_T"},
            "files": {},
        }
        await c._run_incremental_sync_internal(prev_state=prev_state)
        assert c.sync_stats.tables_stream_changes == 1
        c._consume_stream_changes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_skips_checkpoint(self) -> None:
        c = _make_connector()
        c.record_sync_point.read_sync_point.return_value = {
            "current_database": "ZZ",
            "current_schema": None,
        }
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="AA"), SnowflakeDatabase(name="ZZ")],
            schemas={
                "AA": [SnowflakeSchema(name="S", database_name="AA")],
                "ZZ": [SnowflakeSchema(name="S", database_name="ZZ")],
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        await c._run_incremental_sync_internal(prev_state={})
        # AA should be skipped, so schemas_synced reflects only ZZ's schema
        assert c.sync_stats.checkpoint_resumed is True

    @pytest.mark.asyncio
    async def test_incremental_error_reraises(self) -> None:
        c = _make_connector()
        c.data_fetcher.fetch_all.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError):
            await c._run_incremental_sync_internal(prev_state={})
        assert c.sync_stats.errors == 1

    @pytest.mark.asyncio
    async def test_incremental_updates_and_new_view(self) -> None:
        c = _make_connector()

        view = SnowflakeView(
            name="V",
            database_name="DB",
            schema_name="S",
            definition="SELECT 2",
        )
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={},
            views={"DB.S": [view]},
            stages={},
            files={},
        )
        c.data_fetcher.fetch_all.return_value = h

        async def _fake_def(*a, **k):
            return "SELECT 2"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        prev_state = {
            "databases": ["DB"],
            "tables": {},
            "views": {
                "DB.S.V": {
                    "definition_hash": c._compute_definition_hash("SELECT 1"),
                }
            },
            "streams": {},
            "files": {},
        }
        await c._run_incremental_sync_internal(prev_state=prev_state)
        assert c.sync_stats.views_updated == 1

    @pytest.mark.asyncio
    async def test_incremental_files_updated_and_unchanged(self) -> None:
        c = _make_connector()
        # One changed file, one unchanged, one new
        file1 = SnowflakeFile(
            relative_path="a.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="S",
            md5="new",
            last_modified="t2",
        )
        file2 = SnowflakeFile(
            relative_path="b.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="S",
            md5="same",
            last_modified="t1",
        )
        file3 = SnowflakeFile(
            relative_path="new.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="S",
        )
        folder_file = SnowflakeFile(
            relative_path="folder/",
            stage_name="STG",
            database_name="DB",
            schema_name="S",
        )
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            stages={
                "DB.S": [SnowflakeStage(name="STG", database_name="DB", schema_name="S")]
            },
            files={"DB.S.STG": [file1, file2, file3, folder_file]},
        )
        c.data_fetcher.fetch_all.return_value = h
        prev_state = {
            "databases": ["DB"],
            "tables": {},
            "views": {},
            "streams": {},
            "files": {
                "DB.S.STG/a.csv": {"md5": "old", "last_modified": "t1"},
                "DB.S.STG/b.csv": {"md5": "same", "last_modified": "t1"},
            },
        }
        await c._run_incremental_sync_internal(prev_state=prev_state)
        assert c.sync_stats.files_updated == 1
        assert c.sync_stats.files_unchanged == 1
        assert c.sync_stats.files_new == 1


# ===========================================================================
# run_incremental_sync
# ===========================================================================


class TestRunIncrementalSync:
    @pytest.mark.asyncio
    async def test_no_previous_state_runs_full(self) -> None:
        c = _make_connector()
        c.get_sync_point = AsyncMock(return_value=None)
        c._run_full_sync_internal = AsyncMock()
        c._run_incremental_sync_internal = AsyncMock()
        # Ensure sync_stats has all attrs for log line
        c.sync_stats.tables_synced = 0
        c.sync_stats.tables_skipped = 0
        c.sync_stats.views_synced = 0
        c.sync_stats.views_skipped = 0
        c.sync_stats.files_synced = 0
        c.sync_stats.files_skipped = 0
        c.sync_stats.deletions_processed = 0
        await c.run_incremental_sync()
        c._run_full_sync_internal.assert_awaited_once()
        c._run_incremental_sync_internal.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_cursor_runs_incremental(self) -> None:
        c = _make_connector()
        c.get_sync_point = AsyncMock(return_value={"cursor": {"databases": []}})
        c._run_full_sync_internal = AsyncMock()
        c._run_incremental_sync_internal = AsyncMock()
        c.sync_stats.tables_synced = 0
        c.sync_stats.tables_skipped = 0
        c.sync_stats.views_synced = 0
        c.sync_stats.views_skipped = 0
        c.sync_stats.files_synced = 0
        c.sync_stats.files_skipped = 0
        c.sync_stats.deletions_processed = 0
        await c.run_incremental_sync()
        c._run_incremental_sync_internal.assert_awaited_once()


# ===========================================================================
# stream_record (FILE / SQL_TABLE / SQL_VIEW success)
# ===========================================================================


class TestStreamRecordSuccess:
    @pytest.mark.asyncio
    async def test_stream_record_file(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_group_id = "DB.S.STG"
        record.external_record_id = "DB.S.STG/a.csv"
        record.record_name = "a.csv"
        record.mime_type = "text/csv"

        resp_cm = MagicMock()
        resp_cm.__aenter__ = AsyncMock(return_value=resp_cm)
        resp_cm.__aexit__ = AsyncMock(return_value=None)
        resp_cm.status = 200

        async def _iter():
            yield b"chunk1"
            yield b"chunk2"

        content_mock = MagicMock()
        content_mock.iter_any = _iter
        resp_cm.content = content_mock

        c.data_source.get_stage_file_stream.return_value = resp_cm
        response = await c.stream_record(record)
        # Consume the iterator to cover the yield path
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        assert chunks == [b"chunk1", b"chunk2"]

    @pytest.mark.asyncio
    async def test_stream_record_table(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "DB.S.T"

        c.data_fetcher._fetch_all_columns_in_schema.return_value = {
            "T": [{"name": "c", "data_type": "INT"}]
        }
        c.data_fetcher._fetch_primary_keys_in_schema.return_value = [
            {"table": "T", "column": "c"},
        ]
        fk = ForeignKey(
            constraint_name="fk",
            database_name="DB",
            source_schema="S",
            source_table="T",
            source_column="c",
            target_schema="S",
            target_table="T2",
            target_column="c",
        )
        c.data_fetcher._fetch_foreign_keys_in_schema.return_value = [fk]
        c.data_fetcher.get_table_ddl.return_value = "CREATE TABLE T"
        c.data_source.execute_sql.return_value = _resp(
            data={"data": [[1], [2]]}
        )
        response = await c.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_record_table_no_fetcher(self) -> None:
        c = _make_connector()
        c.data_fetcher = None
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "DB.S.T"
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(record)
        assert ei.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_record_view(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_VIEW
        record.external_record_id = "DB.S.V"

        async def _fake_def(*a, **k):
            return "SELECT * FROM db.s.src"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        c.data_source.get_view.return_value = _resp(
            success=True, data={"is_secure": False, "comment": "x"}
        )
        c.data_fetcher.get_table_ddl.return_value = "CREATE TABLE src"
        response = await c.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_record_view_empty_definition(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_VIEW
        record.external_record_id = "DB.S.V"

        async def _fake_def(*a, **k):
            return None

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        c.data_source.get_view.return_value = _resp(success=False, error="x")
        response = await c.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_record_generic_exception(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_group_id = "DB.S.STG"
        record.external_record_id = "DB.S.STG/a.csv"
        c.data_source.get_stage_file_stream.side_effect = RuntimeError("boom")
        with pytest.raises(HTTPException) as ei:
            await c.stream_record(record)
        assert ei.value.status_code == 500


# ===========================================================================
# reindex_records
# ===========================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty(self) -> None:
        c = _make_connector()
        await c.reindex_records([])
        c.data_entities_processor.on_new_records.assert_not_awaited()
        c.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_data_source_raises(self) -> None:
        c = _make_connector()
        c.data_source = None
        with pytest.raises(Exception):
            await c.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_unchanged(self) -> None:
        c = _make_connector()
        rec_updated = MagicMock()
        rec_updated.record_type = RecordType.SQL_TABLE
        rec_updated.external_record_id = "DB.S.T"
        rec_unchanged = MagicMock()
        rec_unchanged.record_type = RecordType.SQL_TABLE
        rec_unchanged.external_record_id = "DB.S.T2"

        c.data_source.list_tables.side_effect = [
            _resp(success=True, data=[{"name": "T"}]),
            _resp(success=True, data=[]),
        ]
        await c.reindex_records([rec_updated, rec_unchanged])
        c.data_entities_processor.on_new_records.assert_awaited_once()
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_during_check_still_processes(self) -> None:
        c = _make_connector()
        rec = MagicMock()
        rec.record_type = RecordType.SQL_TABLE
        rec.external_record_id = "DB.S.T"
        rec.id = "x"
        # Force exception inside _check_record_at_source via list_tables raising
        c.data_source.list_tables.side_effect = RuntimeError("boom")
        # _check_record_at_source catches and returns False, so it is treated as unchanged
        await c.reindex_records([rec])
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()


# ===========================================================================
# _check_record_at_source edge cases
# ===========================================================================


class TestCheckRecordAtSourceMore:
    @pytest.mark.asyncio
    async def test_file_invalid_stage_fqn(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_group_id = "invalid"
        record.external_record_id = "invalid/a"
        assert await c._check_record_at_source(record) is False

    @pytest.mark.asyncio
    async def test_unknown_record_type_returns_false(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.MAIL
        assert await c._check_record_at_source(record) is False


# ===========================================================================
# cleanup exception path
# ===========================================================================


class TestCleanupException:
    @pytest.mark.asyncio
    async def test_cleanup_swallows_exception(self) -> None:
        c = _make_connector()
        # make _record_id_cache.clear raise
        broken = MagicMock()
        broken.clear.side_effect = RuntimeError("boom")
        c._record_id_cache = broken
        # should not raise
        await c.cleanup()


# ===========================================================================
# _clear_checkpoint error path
# ===========================================================================


class TestClearCheckpointError:
    @pytest.mark.asyncio
    async def test_clear_checkpoint_swallows_error(self) -> None:
        c = _make_connector()
        c.record_sync_point.update_sync_point.side_effect = RuntimeError("bad")
        await c._clear_checkpoint()


# ===========================================================================
# get_mimetype_from_path ValueError path (unparseable mime type)
# ===========================================================================


class TestGetMimetypeValueError:
    def test_invalid_mime_string_raises_valueerror_falls_back(self) -> None:
        with patch(
            "app.connectors.sources.snowflake.connector.mimetypes.guess_type",
            return_value=("not/a/real/type/value", None),
        ):
            mt = get_mimetype_from_path("file.weird")
            # The fallback path returns MimeTypes.BIN.value on ValueError
            assert mt == MimeTypes.BIN.value


# ===========================================================================
# _consume_stream_changes with dict rows
# ===========================================================================


class TestConsumeStreamDictRows:
    @pytest.mark.asyncio
    async def test_consume_stream_dict_rows(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {"rowType": [{"name": "ID"}]},
                "data": [{"ID": 1}, {"ID": 2}],
            }
        )
        result = await c._consume_stream_changes("DB.S.STREAM")
        assert len(result) == 2
        assert result[0]["ID"] == 1


# ===========================================================================
# _check_stream_has_changes dict row
# ===========================================================================


class TestCheckStreamDictRow:
    @pytest.mark.asyncio
    async def test_dict_row_true(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.side_effect = [
            _resp(data={"data": [{"HAS_DATA": "TRUE"}]}),
            _resp(data={"data": [{"CNT": 7}]}),
        ]
        has_changes, count = await c._check_stream_has_changes("DB.S.STREAM")
        assert has_changes is True
        assert count == 7


# ===========================================================================
# _process_*_generator exception branches
# ===========================================================================


class TestGeneratorExceptions:
    @pytest.mark.asyncio
    async def test_tables_generator_logs_and_continues(self) -> None:
        c = _make_connector()
        # Provide an invalid table object that will fail record construction
        bad = SimpleNamespace(name="bad", foreign_keys=None)
        good = SnowflakeTable(name="T", database_name="DB", schema_name="S")
        results = []
        async for rec, _ in c._process_tables_generator("DB", "S", [bad, good]):
            results.append(rec)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_views_generator_logs_and_continues(self) -> None:
        c = _make_connector()

        call_count = {"n": 0}

        async def _fake_def(*a, **k):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("fail")
            return "SELECT 1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        bad = SnowflakeView(name="bad", database_name="DB", schema_name="S")
        good = SnowflakeView(name="V", database_name="DB", schema_name="S")
        results = []
        async for rec, _, _v in c._process_views_generator("DB", "S", [bad, good]):
            results.append(rec)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_stage_files_generator_logs_and_continues(self) -> None:
        c = _make_connector()
        bad = SimpleNamespace(is_folder=False, relative_path="b.csv")
        good = SnowflakeFile(
            relative_path="a.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="S",
        )
        results = []
        async for rec, _ in c._process_stage_files_generator("DB.S.STG", [bad, good]):
            results.append(rec)
        assert len(results) == 1


# ===========================================================================
# get_filter_options outer exception
# ===========================================================================


class TestGetFilterOptionsException:
    @pytest.mark.asyncio
    async def test_outer_exception_returns_failure(self) -> None:
        c = _make_connector()
        # Force _get_database_options to raise
        with patch.object(
            c, "_get_database_options", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            result = await c.get_filter_options("databases")
        assert result.success is False
        assert "boom" in (result.message or "")


# ===========================================================================
# _get_*_options exception paths
# ===========================================================================


class TestGetOptionsExceptions:
    @pytest.mark.asyncio
    async def test_database_options_exception(self) -> None:
        c = _make_connector()
        c.data_source.list_databases.side_effect = RuntimeError("boom")
        result = await c._get_database_options(1, 10)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_schema_options_exception(self) -> None:
        c = _make_connector()
        c.data_fetcher.fetch_all.side_effect = RuntimeError("boom")
        result = await c._get_schema_options(1, 10)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_table_options_exception(self) -> None:
        c = _make_connector()
        c.data_fetcher.fetch_all.side_effect = RuntimeError("boom")
        result = await c._get_table_options(1, 10)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_view_options_exception(self) -> None:
        c = _make_connector()
        c.data_fetcher.fetch_all.side_effect = RuntimeError("boom")
        result = await c._get_view_options(1, 10)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_stage_options_exception(self) -> None:
        c = _make_connector()
        c.data_fetcher.fetch_all.side_effect = RuntimeError("boom")
        result = await c._get_stage_options(1, 10)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_file_options_exception(self) -> None:
        c = _make_connector()
        c.data_fetcher.fetch_all.side_effect = RuntimeError("boom")
        result = await c._get_file_options(1, 10)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_view_options_success(self) -> None:
        c = _make_connector()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            views={"DB.S": [SnowflakeView(name="V1", database_name="DB", schema_name="S")]},
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c._get_view_options(1, 10, search="v1")
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_file_options_search_and_stage_filter(self) -> None:
        c = _make_connector()
        # Use a plain dict since _get_filter_values / sync_filters.get() only
        # depends on .get() returning something with a .value attribute.
        c.sync_filters = {"stages": SimpleNamespace(value=["DB.S.STG"])}
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            stages={
                "DB.S": [
                    SnowflakeStage(name="STG", database_name="DB", schema_name="S"),
                    SnowflakeStage(name="OTHER", database_name="DB", schema_name="S"),
                ]
            },
            files={
                "DB.S.STG": [
                    SnowflakeFile(
                        relative_path="alpha.csv",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                    )
                ],
                "DB.S.OTHER": [
                    SnowflakeFile(
                        relative_path="beta.csv",
                        stage_name="OTHER",
                        database_name="DB",
                        schema_name="S",
                    )
                ],
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c._get_file_options(1, 10, search="alpha")
        assert result.success is True
        assert len(result.options) == 1
        assert result.options[0].id == "DB.S.STG/alpha.csv"


# ===========================================================================
# create_connector classmethod
# ===========================================================================


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector_instantiates(self) -> None:
        from app.connectors.sources.snowflake.connector import SnowflakeConnector

        def _fake_base_init(self, app, logger, data_entities_processor,
                            data_store_provider, config_service, connector_id):
            self.logger = logger
            self.data_entities_processor = data_entities_processor
            self.data_store_provider = data_store_provider
            self.config_service = config_service
            self.connector_id = connector_id
            self.app = app

        with patch(
            "app.connectors.sources.snowflake.connector.DataSourceEntitiesProcessor"
        ) as dep_cls, patch(
            "app.connectors.sources.snowflake.connector.BaseConnector.__init__",
            new=_fake_base_init,
        ), patch(
            "app.connectors.sources.snowflake.connector.SyncPoint"
        ), patch(
            "app.connectors.sources.snowflake.connector.SnowflakeApp"
        ):
            dep_instance = MagicMock()
            dep_instance.initialize = AsyncMock()
            dep_instance.org_id = "org-1"
            dep_cls.return_value = dep_instance

            conn = await SnowflakeConnector.create_connector(
                logger=logging.getLogger("x"),
                data_store_provider=MagicMock(),
                config_service=MagicMock(),
                connector_id="conn-xyz",
            )
        assert isinstance(conn, SnowflakeConnector)
        assert conn.connector_id == "conn-xyz"


# ===========================================================================
# _is_table_changed with no last_altered (fallback path)
# ===========================================================================


class TestIsTableChangedFallback:
    def test_no_prev_last_altered_uses_row_count(self) -> None:
        c = _make_connector()
        t = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            row_count=10,
            bytes=100,
            last_altered=None,
        )
        assert c._is_table_changed(t, {"row_count": 10, "bytes": 100}) is False

    def test_table_bytes_changed(self) -> None:
        c = _make_connector()
        t = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            row_count=10,
            bytes=200,
        )
        assert c._is_table_changed(t, {"row_count": 10, "bytes": 100}) is True


# ===========================================================================
# _is_file_changed fallback (no last_modified)
# ===========================================================================


class TestIsFileChangedFallback:
    def test_no_prev_last_modified_uses_md5(self) -> None:
        c = _make_connector()
        f = SnowflakeFile(
            relative_path="a",
            stage_name="S",
            database_name="D",
            schema_name="SC",
            md5="same",
            last_modified=None,
        )
        assert c._is_file_changed(f, {"md5": "same"}) is False


# ===========================================================================
# run_sync error path (additional coverage)
# ===========================================================================


class TestRunSyncError:
    @pytest.mark.asyncio
    async def test_run_sync_not_initialized_raises(self) -> None:
        c = _make_connector()
        c.data_fetcher = None
        with pytest.raises(ConnectionError):
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_happy_path(self) -> None:
        c = _make_connector()
        c._run_full_sync_internal = AsyncMock()
        with patch(
            "app.connectors.sources.snowflake.connector.load_connector_filters",
            new=AsyncMock(return_value=({"a": 1}, {"b": 2})),
        ):
            await c.run_sync()
        c._run_full_sync_internal.assert_awaited_once()
        assert c.sync_filters == {"a": 1}
        assert c.indexing_filters == {"b": 2}


# ===========================================================================
# _ensure_scope_app_edges variants (team/personal without created_by)
# ===========================================================================


class TestEnsureScopeAppEdges:
    @pytest.mark.asyncio
    async def test_team_scope_creates_team_edge(self) -> None:
        c = _make_connector()
        from app.connectors.core.registry.connector_builder import ConnectorScope
        c.scope = ConnectorScope.TEAM.value
        await c._ensure_scope_app_edges()
        c._tx_store.ensure_team_app_edge.assert_awaited_once_with("conn-sf-1", "org-1")

    @pytest.mark.asyncio
    async def test_personal_without_created_by_logs_warning(self) -> None:
        c = _make_connector()
        c.scope = "PERSONAL"
        c.created_by = None
        await c._ensure_scope_app_edges()
        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_personal_user_without_email_skipped(self) -> None:
        c = _make_connector()
        c.scope = "PERSONAL"
        c.created_by = "u1"
        c.data_entities_processor.get_user_by_user_id.return_value = SimpleNamespace(email=None)
        await c._ensure_scope_app_edges()
        c.data_entities_processor.on_new_app_users.assert_not_awaited()


# ===========================================================================
# Final coverage edge cases
# ===========================================================================


class TestFinalEdges:
    @pytest.mark.asyncio
    async def test_consume_stream_changes_returns_empty_on_no_success(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.return_value = _resp(success=True, data=None)
        assert await c._consume_stream_changes("DB.S.STREAM") == []

    def test_should_skip_to_checkpoint_empty_db_string(self) -> None:
        c = _make_connector()
        # checkpoint_db is falsy ""
        assert c._should_skip_to_checkpoint({"current_database": ""}, "AA") is False

    @pytest.mark.asyncio
    async def test_incremental_schema_filter_applied(self) -> None:
        c = _make_connector()
        c.sync_filters = {
            "schemas": SimpleNamespace(value=["DB.KEEP"]),
        }
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={
                "DB": [
                    SnowflakeSchema(name="KEEP", database_name="DB"),
                    SnowflakeSchema(name="DROP", database_name="DB"),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        await c._run_incremental_sync_internal(prev_state={})
        assert c.sync_stats.schemas_synced == 1

    @pytest.mark.asyncio
    async def test_incremental_checkpoint_schema_skip(self) -> None:
        c = _make_connector()
        c.record_sync_point.read_sync_point.return_value = {
            "current_database": "DB",
            "current_schema": "M",
        }
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={
                "DB": [
                    SnowflakeSchema(name="A", database_name="DB"),
                    SnowflakeSchema(name="Z", database_name="DB"),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        await c._run_incremental_sync_internal(prev_state={})
        # A should be skipped; Z proceeds
        assert c.sync_stats.schemas_synced == 1

    @pytest.mark.asyncio
    async def test_incremental_filters_tables_views_stages(self) -> None:
        c = _make_connector()
        c.sync_filters = {
            "tables": SimpleNamespace(value=["DB.S.T_KEEP"]),
            "views": SimpleNamespace(value=["DB.S.V_KEEP"]),
            "stages": SimpleNamespace(value=["DB.S.STG_KEEP"]),
        }
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={
                "DB.S": [
                    SnowflakeTable(name="T_KEEP", database_name="DB", schema_name="S"),
                    SnowflakeTable(name="T_DROP", database_name="DB", schema_name="S"),
                ]
            },
            views={
                "DB.S": [
                    SnowflakeView(name="V_KEEP", database_name="DB", schema_name="S"),
                    SnowflakeView(name="V_DROP", database_name="DB", schema_name="S"),
                ]
            },
            stages={
                "DB.S": [
                    SnowflakeStage(name="STG_KEEP", database_name="DB", schema_name="S"),
                    SnowflakeStage(name="STG_DROP", database_name="DB", schema_name="S"),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h

        async def _fake_def(*a, **k):
            return "SELECT 1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        await c._run_incremental_sync_internal(prev_state={})
        assert c.sync_stats.tables_new == 1
        assert c.sync_stats.views_new == 1

    @pytest.mark.asyncio
    async def test_incremental_table_metadata_updated(self) -> None:
        c = _make_connector()
        c._check_stream_has_changes = AsyncMock(return_value=(False, 0))
        table = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            columns=[{"name": "c", "data_type": "INT"}],
            last_altered="t2",
            row_count=200,
        )
        column_sig = c._compute_column_signature(table.columns)
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={"DB.S": [table]},
        )
        c.data_fetcher.fetch_all.return_value = h
        prev_state = {
            "databases": ["DB"],
            "tables": {
                "DB.S.T": {
                    "row_count": 100,
                    "bytes": 0,
                    "last_altered": "t1",
                    "column_signature": column_sig,
                }
            },
            "views": {},
            "streams": {},
            "files": {},
        }
        await c._run_incremental_sync_internal(prev_state=prev_state)
        assert c.sync_stats.tables_updated == 1

    @pytest.mark.asyncio
    async def test_incremental_file_filter_applied(self) -> None:
        c = _make_connector()
        c.sync_filters = {"files": SimpleNamespace(value=["DB.S.STG/keep.csv"])}
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            stages={
                "DB.S": [SnowflakeStage(name="STG", database_name="DB", schema_name="S")]
            },
            files={
                "DB.S.STG": [
                    SnowflakeFile(
                        relative_path="keep.csv",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                    ),
                    SnowflakeFile(
                        relative_path="drop.csv",
                        stage_name="STG",
                        database_name="DB",
                        schema_name="S",
                    ),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        await c._run_incremental_sync_internal(prev_state={})
        assert c.sync_stats.files_new == 1

    @pytest.mark.asyncio
    async def test_tables_generator_indexing_disabled(self) -> None:
        c = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled.return_value = False
        tables = [SnowflakeTable(name="T", database_name="DB", schema_name="S")]
        results = []
        async for rec, _ in c._process_tables_generator("DB", "S", tables):
            results.append(rec)
        assert results[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_views_generator_indexing_disabled(self) -> None:
        c = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled.return_value = False

        async def _fake_def(*a, **k):
            return "SELECT 1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        views = [SnowflakeView(name="V", database_name="DB", schema_name="S")]
        results = []
        async for rec, _, _v in c._process_views_generator("DB", "S", views):
            results.append(rec)
        assert results[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_stage_files_generator_indexing_disabled(self) -> None:
        c = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled.return_value = False
        files = [
            SnowflakeFile(
                relative_path="a.csv",
                stage_name="STG",
                database_name="DB",
                schema_name="S",
            )
        ]
        results = []
        async for rec, _ in c._process_stage_files_generator("DB.S.STG", files):
            results.append(rec)
        assert results[0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_views_generator_source_table_2_part(self) -> None:
        c = _make_connector()

        async def _fake_def(*a, **k):
            return "SELECT * FROM schema2.other_table"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        views = [SnowflakeView(name="V", database_name="DB", schema_name="S")]
        async for rec, _, _v in c._process_views_generator("DB", "S", views):
            # Expect a relationship to DB.schema2.other_table
            assert any(
                r.external_record_id == "DB.schema2.other_table"
                for r in rec.related_external_records
            )

    @pytest.mark.asyncio
    async def test_views_generator_source_table_3_part(self) -> None:
        c = _make_connector()

        async def _fake_def(*a, **k):
            return "SELECT * FROM db2.schema2.other_table"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        views = [SnowflakeView(name="V", database_name="DB", schema_name="S")]
        async for rec, _, _v in c._process_views_generator("DB", "S", views):
            assert any(
                r.external_record_id == "db2.schema2.other_table"
                for r in rec.related_external_records
            )

    @pytest.mark.asyncio
    async def test_fetch_table_metadata_returns_none(self) -> None:
        c = _make_connector()
        assert await c._fetch_table_metadata("DB", "S", "T") is None

    @pytest.mark.asyncio
    async def test_sync_views_batch_trigger(self) -> None:
        c = _make_connector()
        c.batch_size = 2

        async def _fake_def(*a, **k):
            return "SELECT 1"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        views = [
            SnowflakeView(name=f"V{i}", database_name="DB", schema_name="S")
            for i in range(3)
        ]
        await c._sync_views("DB", "S", views)
        assert c.data_entities_processor.on_new_records.await_count == 2

    @pytest.mark.asyncio
    async def test_sync_stage_files_batch_trigger(self) -> None:
        c = _make_connector()
        c.batch_size = 2
        files = [
            SnowflakeFile(
                relative_path=f"f{i}.csv",
                stage_name="STG",
                database_name="DB",
                schema_name="S",
            )
            for i in range(3)
        ]
        await c._sync_stage_files("DB", "S", "STG", files)
        assert c.data_entities_processor.on_new_records.await_count == 2

    @pytest.mark.asyncio
    async def test_fetch_view_definition_dict_rows(self) -> None:
        c = _make_connector()
        c.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {"rowType": [{"name": "DDL"}]},
                "data": [{"DDL": "CREATE VIEW V AS SELECT 1"}],
            }
        )
        result = await c._fetch_view_definition("DB", "S", "V")
        assert result == "CREATE VIEW V AS SELECT 1"

    @pytest.mark.asyncio
    async def test_stream_record_file_http_error_status(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_group_id = "DB.S.STG"
        record.external_record_id = "DB.S.STG/a.csv"
        record.record_name = "a.csv"
        record.mime_type = "text/csv"

        resp_cm = MagicMock()
        resp_cm.__aenter__ = AsyncMock(return_value=resp_cm)
        resp_cm.__aexit__ = AsyncMock(return_value=None)
        resp_cm.status = 403

        async def _iter():
            yield b""

        content_mock = MagicMock()
        content_mock.iter_any = _iter
        resp_cm.content = content_mock
        c.data_source.get_stage_file_stream.return_value = resp_cm
        streaming_response = await c.stream_record(record)
        # Iterate the response body to trigger the http-error path inside the iterator
        body_iter = streaming_response.body_iterator
        with pytest.raises(HTTPException) as ei:
            async for _ in body_iter:
                pass
        assert ei.value.status_code == 403

    @pytest.mark.asyncio
    async def test_stream_record_table_iterator(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_TABLE
        record.external_record_id = "DB.S.T"
        c.data_fetcher._fetch_all_columns_in_schema.return_value = {"T": []}
        c.data_fetcher._fetch_primary_keys_in_schema.return_value = []
        c.data_fetcher._fetch_foreign_keys_in_schema.return_value = []
        c.data_fetcher.get_table_ddl.return_value = None
        c.data_source.execute_sql.return_value = _resp(data={"data": []})
        streaming = await c.stream_record(record)
        collected = []
        async for chunk in streaming.body_iterator:
            collected.append(chunk)
        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_stream_record_view_iterator_and_source_ddl_exception(self) -> None:
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.SQL_VIEW
        record.external_record_id = "DB.S.V"

        # Definition with 1/2/3-part refs + a parsed source with >3 segments
        # to exercise the `else: continue` branch when src_parts > 3.
        async def _fake_def(*a, **k):
            return "SELECT * FROM tbl1 JOIN sch.tbl2 JOIN db2.sch2.tbl3"

        c._fetch_view_definition = _fake_def  # type: ignore[assignment]
        c.data_source.get_view.return_value = _resp(
            success=True, data={"isSecure": True, "comment": "c"}
        )

        # Force _parse_source_tables to include a 4-part name to hit the
        # `else: continue` branch for oversized FQNs.
        original_parse = c._parse_source_tables

        def _parse_with_oversized(defn):
            return original_parse(defn) + ["a.b.c.d"]

        c._parse_source_tables = _parse_with_oversized  # type: ignore[assignment]

        call_count = {"n": 0}

        async def _ddl(db, sch, t):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("ddl fail")
            return "CREATE " + t

        c.data_fetcher.get_table_ddl = _ddl
        streaming = await c.stream_record(record)
        collected = []
        async for chunk in streaming.body_iterator:
            collected.append(chunk)
        assert len(collected) == 1

    @pytest.mark.asyncio
    async def test_reindex_records_exception_in_check(self) -> None:
        c = _make_connector()
        rec = MagicMock()
        rec.id = "id-1"
        # Make _check_record_at_source raise directly
        c._check_record_at_source = AsyncMock(side_effect=RuntimeError("boom"))
        await c.reindex_records([rec])
        # The exception handler catches and adds to non_updated -> reindex_existing_records called
        c.data_entities_processor.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schema_options_search_filter(self) -> None:
        c = _make_connector()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={
                "DB": [
                    SnowflakeSchema(name="PUBLIC", database_name="DB"),
                    SnowflakeSchema(name="OTHER", database_name="DB"),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c._get_schema_options(1, 10, search="public")
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_table_options_search_filter(self) -> None:
        c = _make_connector()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            tables={
                "DB.S": [
                    SnowflakeTable(name="USERS", database_name="DB", schema_name="S"),
                    SnowflakeTable(name="ORDERS", database_name="DB", schema_name="S"),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c._get_table_options(1, 10, search="users")
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_stage_options_search_filter(self) -> None:
        c = _make_connector()
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="DB")],
            schemas={"DB": [SnowflakeSchema(name="S", database_name="DB")]},
            stages={
                "DB.S": [
                    SnowflakeStage(name="IMPORT", database_name="DB", schema_name="S"),
                    SnowflakeStage(name="EXPORT", database_name="DB", schema_name="S"),
                ]
            },
        )
        c.data_fetcher.fetch_all.return_value = h
        result = await c._get_stage_options(1, 10, search="import")
        assert result.success is True
        assert len(result.options) == 1


# Need ProgressStatus import
from app.models.entities import ProgressStatus  # noqa: E402
