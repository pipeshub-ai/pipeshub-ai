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
        c.data_entities_processor.get_all_active_users.return_value = [
            User(source_user_id="u1", id="1", org_id="o1", email="u1@x.com", full_name="U", is_active=True, title=None),
        ]
        await c._create_app_user()
        c.data_entities_processor.on_new_app_users.assert_awaited_once()
        called_with = c.data_entities_processor.on_new_app_users.await_args[0][0]
        assert len(called_with) == 1

    @pytest.mark.asyncio
    async def test_propagates_errors(self) -> None:
        c = _make_connector()
        c.data_entities_processor.get_all_active_users.side_effect = RuntimeError("boom")
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
