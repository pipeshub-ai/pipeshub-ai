"""Comprehensive tests for app.connectors.sources.snowflake.data_fetcher."""
from __future__ import annotations

# Patch typing.override for Python 3.10 compatibility before any app imports
import typing as _typing

if not hasattr(_typing, "override"):
    try:
        from typing_extensions import override as _override

        _typing.override = _override  # type: ignore[attr-defined]
    except ImportError:
        def _override(fn):  # type: ignore[misc]
            return fn

        _typing.override = _override  # type: ignore[attr-defined]

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from app.connectors.sources.snowflake.data_fetcher import (
    ForeignKey,
    SnowflakeDatabase,
    SnowflakeDataFetcher,
    SnowflakeFile,
    SnowflakeFolder,
    SnowflakeHierarchy,
    SnowflakeSchema,
    SnowflakeStage,
    SnowflakeTable,
    SnowflakeView,
)


def _resp(success: bool = True, data: Any = None, error: Optional[str] = None) -> SimpleNamespace:
    """Create a mock SnowflakeResponse-like object."""
    return SimpleNamespace(success=success, data=data, error=error)


# ===========================================================================
# Dataclass tests
# ===========================================================================


class TestSnowflakeDatabase:
    def test_required_field_only(self) -> None:
        db = SnowflakeDatabase(name="MYDB")
        assert db.name == "MYDB"
        assert db.owner is None
        assert db.comment is None
        assert db.created_at is None

    def test_all_fields(self) -> None:
        db = SnowflakeDatabase(
            name="MYDB", owner="admin", comment="a comment", created_at="2024-01-01"
        )
        assert db.owner == "admin"
        assert db.comment == "a comment"
        assert db.created_at == "2024-01-01"


class TestSnowflakeSchema:
    def test_required_fields(self) -> None:
        s = SnowflakeSchema(name="PUBLIC", database_name="DB1")
        assert s.name == "PUBLIC"
        assert s.database_name == "DB1"


class TestSnowflakeTable:
    def test_fqn(self) -> None:
        t = SnowflakeTable(name="T1", database_name="DB", schema_name="S")
        assert t.fqn == "DB.S.T1"

    def test_column_signature_empty(self) -> None:
        t = SnowflakeTable(name="T", database_name="DB", schema_name="S")
        assert t.column_signature == ""

    def test_column_signature_stable(self) -> None:
        t1 = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            columns=[{"name": "b", "data_type": "VARCHAR"}, {"name": "a", "data_type": "INT"}],
        )
        t2 = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            columns=[{"name": "a", "data_type": "INT"}, {"name": "b", "data_type": "VARCHAR"}],
        )
        # Order shouldn't matter because sorted in implementation
        assert t1.column_signature == t2.column_signature
        assert len(t1.column_signature) == 32  # md5 hex length

    def test_column_signature_differs_by_type(self) -> None:
        t1 = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            columns=[{"name": "a", "data_type": "INT"}],
        )
        t2 = SnowflakeTable(
            name="T",
            database_name="DB",
            schema_name="S",
            columns=[{"name": "a", "data_type": "VARCHAR"}],
        )
        assert t1.column_signature != t2.column_signature


class TestSnowflakeView:
    def test_fqn(self) -> None:
        v = SnowflakeView(name="V", database_name="DB", schema_name="S")
        assert v.fqn == "DB.S.V"
        assert v.source_tables == []
        assert v.is_secure is False


class TestSnowflakeStage:
    def test_fqn_and_default_type(self) -> None:
        s = SnowflakeStage(name="STG", database_name="DB", schema_name="SC")
        assert s.fqn == "DB.SC.STG"
        assert s.stage_type == "INTERNAL"


class TestSnowflakeFile:
    def test_is_folder_true(self) -> None:
        f = SnowflakeFile(
            relative_path="folder/",
            stage_name="STG",
            database_name="DB",
            schema_name="SC",
        )
        assert f.is_folder is True

    def test_is_folder_false(self) -> None:
        f = SnowflakeFile(
            relative_path="file.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="SC",
        )
        assert f.is_folder is False

    def test_file_name_root(self) -> None:
        f = SnowflakeFile(
            relative_path="data.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="SC",
        )
        assert f.file_name == "data.csv"

    def test_file_name_nested(self) -> None:
        f = SnowflakeFile(
            relative_path="a/b/c/data.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="SC",
        )
        assert f.file_name == "data.csv"

    def test_file_name_trailing_slash(self) -> None:
        f = SnowflakeFile(
            relative_path="a/b/",
            stage_name="STG",
            database_name="DB",
            schema_name="SC",
        )
        assert f.file_name == "b"

    def test_parent_folder_none(self) -> None:
        f = SnowflakeFile(
            relative_path="data.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="SC",
        )
        assert f.parent_folder is None

    def test_parent_folder_nested(self) -> None:
        f = SnowflakeFile(
            relative_path="a/b/data.csv",
            stage_name="STG",
            database_name="DB",
            schema_name="SC",
        )
        assert f.parent_folder == "a/b"


class TestSnowflakeFolder:
    def test_name_root(self) -> None:
        f = SnowflakeFolder(path="a", stage_name="STG", database_name="DB", schema_name="SC")
        assert f.name == "a"

    def test_name_nested(self) -> None:
        f = SnowflakeFolder(
            path="a/b/c",
            stage_name="STG",
            database_name="DB",
            schema_name="SC",
            parent_path="a/b",
        )
        assert f.name == "c"


class TestForeignKey:
    def test_source_and_target_fqn(self) -> None:
        fk = ForeignKey(
            constraint_name="fk1",
            database_name="DB",
            source_schema="S1",
            source_table="T1",
            source_column="c1",
            target_schema="S2",
            target_table="T2",
            target_column="c2",
        )
        assert fk.source_fqn == "DB.S1.T1"
        assert fk.target_fqn == "DB.S2.T2"


class TestSnowflakeHierarchy:
    def test_empty_summary(self) -> None:
        h = SnowflakeHierarchy()
        s = h.summary()
        assert s["databases"] == 0
        assert s["schemas"] == 0
        assert s["tables"] == 0
        assert s["views"] == 0
        assert s["stages"] == 0
        assert s["files"] == 0
        assert s["folders"] == 0
        assert s["foreign_keys"] == 0

    def test_populated_summary(self) -> None:
        h = SnowflakeHierarchy(
            databases=[SnowflakeDatabase(name="A"), SnowflakeDatabase(name="B")],
            schemas={"A": [SnowflakeSchema(name="s1", database_name="A")]},
            tables={"A.s1": [SnowflakeTable(name="t", database_name="A", schema_name="s1")]},
            views={"A.s1": [SnowflakeView(name="v", database_name="A", schema_name="s1")]},
            stages={"A.s1": [SnowflakeStage(name="stg", database_name="A", schema_name="s1")]},
            files={"A.s1.stg": [SnowflakeFile(relative_path="f", stage_name="stg", database_name="A", schema_name="s1")]},
            folders={"A.s1.stg": [SnowflakeFolder(path="p", stage_name="stg", database_name="A", schema_name="s1")]},
            foreign_keys=[
                ForeignKey(
                    constraint_name="fk",
                    database_name="A",
                    source_schema="s1",
                    source_table="t",
                    source_column="c",
                    target_schema="s1",
                    target_table="t2",
                    target_column="id",
                )
            ],
        )
        s = h.summary()
        assert s["databases"] == 2
        assert s["schemas"] == 1
        assert s["tables"] == 1
        assert s["views"] == 1
        assert s["stages"] == 1
        assert s["files"] == 1
        assert s["folders"] == 1
        assert s["foreign_keys"] == 1

    def test_to_dict_roundtrip(self) -> None:
        h = SnowflakeHierarchy(
            fetched_at="now",
            databases=[SnowflakeDatabase(name="A")],
        )
        d = h.to_dict()
        assert d["fetched_at"] == "now"
        assert d["databases"][0]["name"] == "A"
        assert "summary" in d
        # Ensure fully JSON-serializable
        assert json.dumps(d)


# ===========================================================================
# SnowflakeDataFetcher tests
# ===========================================================================


def _make_fetcher(warehouse: Optional[str] = "WH1") -> SnowflakeDataFetcher:
    ds = MagicMock()
    # Pre-create AsyncMock attributes for used methods
    ds.list_databases = AsyncMock()
    ds.list_schemas = AsyncMock()
    ds.list_tables = AsyncMock()
    ds.list_views = AsyncMock()
    ds.list_stages = AsyncMock()
    ds.list_stage_files = AsyncMock()
    ds.execute_sql = AsyncMock()
    return SnowflakeDataFetcher(ds, warehouse=warehouse)


class TestClearCache:
    def test_clears_both_caches(self) -> None:
        f = _make_fetcher()
        f._schema_cache["DB"] = [SnowflakeSchema(name="s", database_name="DB")]
        f._columns_cache["DB.s"] = {"t": [{"name": "c"}]}
        f._clear_cache()
        assert f._schema_cache == {}
        assert f._columns_cache == {}


class TestExtractItems:
    def test_none(self) -> None:
        assert _make_fetcher()._extract_items(None) == []

    def test_empty(self) -> None:
        assert _make_fetcher()._extract_items([]) == []

    def test_list(self) -> None:
        items = [{"a": 1}, {"b": 2}]
        assert _make_fetcher()._extract_items(items) == items

    def test_dict_with_data(self) -> None:
        items = [{"a": 1}]
        assert _make_fetcher()._extract_items({"data": items}) == items

    def test_dict_with_rowset(self) -> None:
        items = [{"x": 1}]
        assert _make_fetcher()._extract_items({"rowset": items}) == items

    def test_dict_with_items_key(self) -> None:
        items = [{"y": 2}]
        assert _make_fetcher()._extract_items({"items": items}) == items

    def test_dict_no_known_key(self) -> None:
        assert _make_fetcher()._extract_items({"other": [1]}) == []

    def test_string_returns_empty(self) -> None:
        assert _make_fetcher()._extract_items("hello") == []


class TestParseSqlResult:
    def test_none(self) -> None:
        assert _make_fetcher()._parse_sql_result(None) == []

    def test_not_dict(self) -> None:
        assert _make_fetcher()._parse_sql_result([1, 2]) == []

    def test_list_rows(self) -> None:
        data = {
            "resultSetMetaData": {"rowType": [{"name": "A"}, {"name": "B"}]},
            "data": [[1, "x"], [2, "y"]],
        }
        result = _make_fetcher()._parse_sql_result(data)
        assert result == [{"A": 1, "B": "x"}, {"A": 2, "B": "y"}]

    def test_dict_rows(self) -> None:
        data = {
            "resultSetMetaData": {"rowType": [{"name": "A"}]},
            "data": [{"A": 1}, {"A": 2}],
        }
        result = _make_fetcher()._parse_sql_result(data)
        assert result == [{"A": 1}, {"A": 2}]

    def test_missing_metadata(self) -> None:
        result = _make_fetcher()._parse_sql_result({"data": [[1, 2]]})
        # No metadata => columns is empty, but zip empty gives no keys
        assert result == [{}]


class TestFetchDatabases:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        f = _make_fetcher()
        f.data_source.list_databases.return_value = _resp(
            data=[{"name": "DB1", "owner": "a", "comment": "c", "created_on": "2024-01-01"}]
        )
        dbs = await f._fetch_databases()
        assert len(dbs) == 1
        assert dbs[0].name == "DB1"
        assert dbs[0].owner == "a"
        assert dbs[0].created_at == "2024-01-01"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.list_databases.return_value = _resp(success=False, error="bad")
        dbs = await f._fetch_databases()
        assert dbs == []


class TestFetchSchemas:
    @pytest.mark.asyncio
    async def test_caches(self) -> None:
        f = _make_fetcher()
        f.data_source.list_schemas.return_value = _resp(
            data=[{"name": "PUBLIC"}]
        )
        first = await f._fetch_schemas("DB1")
        second = await f._fetch_schemas("DB1")
        assert first == second
        # Should only call once due to cache
        assert f.data_source.list_schemas.await_count == 1

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.list_schemas.return_value = _resp(success=False, error="no")
        schemas = await f._fetch_schemas("DB1")
        assert schemas == []


class TestFetchTables:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        f = _make_fetcher()
        f.data_source.list_tables.return_value = _resp(
            data=[
                {
                    "name": "T",
                    "rows": 10,
                    "bytes": 200,
                    "kind": "TABLE",
                    "last_altered": "2024-06-01",
                }
            ]
        )
        tables = await f._fetch_tables("DB", "S")
        assert len(tables) == 1
        assert tables[0].row_count == 10
        assert tables[0].bytes == 200
        assert tables[0].table_type == "TABLE"
        assert tables[0].last_altered == "2024-06-01"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.list_tables.return_value = _resp(success=False, error="bad")
        assert await f._fetch_tables("DB", "S") == []


class TestFetchViews:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        f = _make_fetcher()
        f.data_source.list_views.return_value = _resp(
            data=[{"name": "V", "text": "SELECT 1", "is_secure": True}]
        )
        views = await f._fetch_views("DB", "S")
        assert len(views) == 1
        assert views[0].definition == "SELECT 1"
        assert views[0].is_secure is True

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.list_views.return_value = _resp(success=False, error="err")
        assert await f._fetch_views("DB", "S") == []


class TestFetchStages:
    @pytest.mark.asyncio
    async def test_internal(self) -> None:
        f = _make_fetcher()
        f.data_source.list_stages.return_value = _resp(
            data=[{"name": "STG", "type": "INTERNAL"}]
        )
        stages = await f._fetch_stages("DB", "S")
        assert stages[0].stage_type == "INTERNAL"

    @pytest.mark.asyncio
    async def test_external(self) -> None:
        f = _make_fetcher()
        f.data_source.list_stages.return_value = _resp(
            data=[{"name": "STG", "type": "AWS_S3_EXTERNAL", "url": "s3://b"}]
        )
        stages = await f._fetch_stages("DB", "S")
        assert stages[0].stage_type == "EXTERNAL"
        assert stages[0].url == "s3://b"

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.list_stages.return_value = _resp(success=False, error="boom")
        assert await f._fetch_stages("DB", "S") == []


class TestFetchStageFiles:
    @pytest.mark.asyncio
    async def test_no_warehouse(self) -> None:
        f = _make_fetcher(warehouse=None)
        files = await f._fetch_stage_files("DB", "S", "STG")
        assert files == []
        f.data_source.list_stage_files.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.list_stage_files.return_value = _resp(success=False, error="bad")
        files = await f._fetch_stage_files("DB", "S", "STG")
        assert files == []

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        f = _make_fetcher()
        f.data_source.list_stage_files.return_value = _resp(
            data={
                "data": [
                    ["file1.csv", "123", "2024-01-01", "md5hash", "extra", "http://u"],
                    ["file2.csv", "456"],
                ]
            }
        )
        files = await f._fetch_stage_files("DB", "S", "STG")
        assert len(files) == 2
        assert files[0].relative_path == "file1.csv"
        assert files[0].size == 123
        assert files[0].md5 == "md5hash"
        assert files[0].file_url == "http://u"
        assert files[1].size == 456

    @pytest.mark.asyncio
    async def test_non_dict_data(self) -> None:
        f = _make_fetcher()
        f.data_source.list_stage_files.return_value = _resp(data=[])
        files = await f._fetch_stage_files("DB", "S", "STG")
        assert files == []


class TestDeduceFolders:
    def test_flat_files(self) -> None:
        f = _make_fetcher()
        files = [
            SnowflakeFile(relative_path="a.csv", stage_name="S", database_name="D", schema_name="SC"),
        ]
        folders = f._deduce_folders(files, "D", "SC", "S")
        assert folders == []

    def test_nested(self) -> None:
        f = _make_fetcher()
        files = [
            SnowflakeFile(relative_path="a/b/data.csv", stage_name="S", database_name="D", schema_name="SC"),
            SnowflakeFile(relative_path="a/c/other.csv", stage_name="S", database_name="D", schema_name="SC"),
        ]
        folders = f._deduce_folders(files, "D", "SC", "S")
        paths = {fo.path for fo in folders}
        assert paths == {"a", "a/b", "a/c"}
        parents = {fo.path: fo.parent_path for fo in folders}
        assert parents["a"] is None
        assert parents["a/b"] == "a"


class TestFetchAllColumnsInSchema:
    @pytest.mark.asyncio
    async def test_no_warehouse(self) -> None:
        f = _make_fetcher(warehouse=None)
        assert await f._fetch_all_columns_in_schema("DB", "S") == {}

    @pytest.mark.asyncio
    async def test_caching(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {
                    "rowType": [
                        {"name": "TABLE_NAME"},
                        {"name": "COLUMN_NAME"},
                        {"name": "DATA_TYPE"},
                        {"name": "CHARACTER_MAXIMUM_LENGTH"},
                        {"name": "NUMERIC_PRECISION"},
                        {"name": "NUMERIC_SCALE"},
                        {"name": "IS_NULLABLE"},
                        {"name": "COLUMN_DEFAULT"},
                        {"name": "COMMENT"},
                    ]
                },
                "data": [
                    ["T1", "c1", "VARCHAR", 100, None, None, "YES", None, None],
                    ["T1", "c2", "NUMBER", None, 10, 2, "NO", "0", "comment"],
                    ["T2", "id", "NUMBER", None, 38, 0, "NO", None, None],
                ],
            }
        )
        cols = await f._fetch_all_columns_in_schema("DB", "S")
        assert set(cols.keys()) == {"T1", "T2"}
        assert cols["T1"][1]["nullable"] is False
        assert cols["T1"][0]["nullable"] is True
        # Second call should use cache
        cols2 = await f._fetch_all_columns_in_schema("DB", "S")
        assert cols2 is cols
        assert f.data_source.execute_sql.await_count == 1

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(success=False, error="bad")
        cols = await f._fetch_all_columns_in_schema("DB", "S")
        assert cols == {}


class TestGetTableDdl:
    @pytest.mark.asyncio
    async def test_no_warehouse(self) -> None:
        f = _make_fetcher(warehouse=None)
        assert await f.get_table_ddl("DB", "S", "T") is None

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {"rowType": [{"name": "DDL"}]},
                "data": [["CREATE TABLE ..."]],
            }
        )
        assert await f.get_table_ddl("DB", "S", "T") == "CREATE TABLE ..."

    @pytest.mark.asyncio
    async def test_empty_rows(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {"rowType": [{"name": "DDL"}]},
                "data": [],
            }
        )
        assert await f.get_table_ddl("DB", "S", "T") is None

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(success=False, error="bad")
        assert await f.get_table_ddl("DB", "S", "T") is None


class TestFetchForeignKeys:
    @pytest.mark.asyncio
    async def test_no_warehouse(self) -> None:
        f = _make_fetcher(warehouse=None)
        assert await f._fetch_foreign_keys_in_schema("DB", "S") == []

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(success=False, error="bad")
        assert await f._fetch_foreign_keys_in_schema("DB", "S") == []

    @pytest.mark.asyncio
    async def test_success_lowercase(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {
                    "rowType": [
                        {"name": "fk_schema_name"},
                        {"name": "fk_table_name"},
                        {"name": "fk_column_name"},
                        {"name": "pk_schema_name"},
                        {"name": "pk_table_name"},
                        {"name": "pk_column_name"},
                        {"name": "fk_name"},
                    ]
                },
                "data": [["S", "T1", "c1", "S", "T2", "id", "fk1"]],
            }
        )
        fks = await f._fetch_foreign_keys_in_schema("DB", "S")
        assert len(fks) == 1
        assert fks[0].constraint_name == "fk1"
        assert fks[0].source_table == "T1"
        assert fks[0].target_table == "T2"

    @pytest.mark.asyncio
    async def test_missing_target_table(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {
                    "rowType": [
                        {"name": "fk_table_name"},
                        {"name": "fk_column_name"},
                        {"name": "pk_table_name"},
                        {"name": "pk_column_name"},
                    ]
                },
                "data": [["T1", "c", "", ""]],
            }
        )
        fks = await f._fetch_foreign_keys_in_schema("DB", "S")
        assert fks == []


class TestFetchPrimaryKeys:
    @pytest.mark.asyncio
    async def test_no_warehouse(self) -> None:
        f = _make_fetcher(warehouse=None)
        assert await f._fetch_primary_keys_in_schema("DB", "S") == []

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(success=False, error="oops")
        assert await f._fetch_primary_keys_in_schema("DB", "S") == []

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        f = _make_fetcher()
        f.data_source.execute_sql.return_value = _resp(
            data={
                "resultSetMetaData": {
                    "rowType": [{"name": "table_name"}, {"name": "column_name"}]
                },
                "data": [["T1", "id"], ["T2", "key"]],
            }
        )
        pks = await f._fetch_primary_keys_in_schema("DB", "S")
        assert {"table": "T1", "column": "id"} in pks
        assert {"table": "T2", "column": "key"} in pks


class TestFetchAll:
    @pytest.mark.asyncio
    async def test_full_flow_with_filters(self) -> None:
        f = _make_fetcher()
        f.data_source.list_databases.return_value = _resp(
            data=[{"name": "DB1"}, {"name": "DB2"}]
        )

        async def schemas_side_effect(database: str):
            return _resp(data=[{"name": "PUBLIC"}])

        f.data_source.list_schemas.side_effect = schemas_side_effect

        f.data_source.list_tables.return_value = _resp(
            data=[{"name": "T1", "rows": 5, "bytes": 100}]
        )
        f.data_source.list_views.return_value = _resp(data=[])
        f.data_source.list_stages.return_value = _resp(data=[])

        # Columns, fks, pks via execute_sql - return empty for simplicity
        f.data_source.execute_sql.return_value = _resp(
            data={"resultSetMetaData": {"rowType": []}, "data": []}
        )

        h = await f.fetch_all(database_filter=["DB1"])
        assert len(h.databases) == 1
        assert h.databases[0].name == "DB1"
        assert h.schemas["DB1"][0].name == "PUBLIC"

    @pytest.mark.asyncio
    async def test_schema_filter(self) -> None:
        f = _make_fetcher()
        f.data_source.list_databases.return_value = _resp(data=[{"name": "DB"}])
        f.data_source.list_schemas.return_value = _resp(
            data=[{"name": "A"}, {"name": "B"}]
        )
        f.data_source.list_tables.return_value = _resp(data=[])
        f.data_source.list_views.return_value = _resp(data=[])
        f.data_source.list_stages.return_value = _resp(data=[])
        f.data_source.execute_sql.return_value = _resp(
            data={"resultSetMetaData": {"rowType": []}, "data": []}
        )

        h = await f.fetch_all(schema_filter=["DB.A"])
        assert len(h.schemas["DB"]) == 1
        assert h.schemas["DB"][0].name == "A"

    @pytest.mark.asyncio
    async def test_without_files_and_relationships(self) -> None:
        f = _make_fetcher()
        f.data_source.list_databases.return_value = _resp(data=[{"name": "DB"}])
        f.data_source.list_schemas.return_value = _resp(data=[{"name": "PUBLIC"}])
        f.data_source.list_tables.return_value = _resp(data=[{"name": "T"}])
        f.data_source.list_views.return_value = _resp(data=[])
        f.data_source.list_stages.return_value = _resp(
            data=[{"name": "STG", "type": "INTERNAL"}]
        )

        h = await f.fetch_all(include_files=False, include_relationships=False)
        assert h.tables["DB.PUBLIC"][0].name == "T"
        # Files should not be populated because include_files=False
        assert h.files == {}
        # Relationships not populated
        assert h.foreign_keys == []
        # execute_sql must not have been called for fks/pks/columns
        assert f.data_source.execute_sql.await_count == 0

    @pytest.mark.asyncio
    async def test_relationships_populate_foreign_keys(self) -> None:
        f = _make_fetcher()
        f.data_source.list_databases.return_value = _resp(data=[{"name": "DB"}])
        f.data_source.list_schemas.return_value = _resp(data=[{"name": "S"}])
        f.data_source.list_tables.return_value = _resp(
            data=[{"name": "T1"}, {"name": "T2"}]
        )
        f.data_source.list_views.return_value = _resp(data=[])
        f.data_source.list_stages.return_value = _resp(data=[])

        # Mock execute_sql responses for columns, fks, pks
        call_counter = {"n": 0}

        async def execute_side_effect(**kwargs):
            call_counter["n"] += 1
            stmt = kwargs.get("statement", "").upper()
            if "INFORMATION_SCHEMA.COLUMNS" in stmt:
                return _resp(
                    data={
                        "resultSetMetaData": {
                            "rowType": [
                                {"name": "TABLE_NAME"},
                                {"name": "COLUMN_NAME"},
                                {"name": "DATA_TYPE"},
                                {"name": "CHARACTER_MAXIMUM_LENGTH"},
                                {"name": "NUMERIC_PRECISION"},
                                {"name": "NUMERIC_SCALE"},
                                {"name": "IS_NULLABLE"},
                                {"name": "COLUMN_DEFAULT"},
                                {"name": "COMMENT"},
                            ]
                        },
                        "data": [
                            ["T1", "c1", "VARCHAR", None, None, None, "YES", None, None],
                        ],
                    }
                )
            if "IMPORTED KEYS" in stmt:
                return _resp(
                    data={
                        "resultSetMetaData": {
                            "rowType": [
                                {"name": "fk_table_name"},
                                {"name": "fk_column_name"},
                                {"name": "pk_table_name"},
                                {"name": "pk_column_name"},
                                {"name": "fk_name"},
                            ]
                        },
                        "data": [["T1", "c1", "T2", "id", "myfk"]],
                    }
                )
            if "PRIMARY KEYS" in stmt:
                return _resp(
                    data={
                        "resultSetMetaData": {
                            "rowType": [{"name": "table_name"}, {"name": "column_name"}]
                        },
                        "data": [["T2", "id"]],
                    }
                )
            return _resp(data={"resultSetMetaData": {"rowType": []}, "data": []})

        f.data_source.execute_sql.side_effect = execute_side_effect

        h = await f.fetch_all(include_files=False, include_relationships=True)
        assert len(h.foreign_keys) == 1
        t1 = next(t for t in h.tables["DB.S"] if t.name == "T1")
        assert t1.foreign_keys and t1.foreign_keys[0]["references_table"] == "T2"
        t2 = next(t for t in h.tables["DB.S"] if t.name == "T2")
        assert t2.primary_keys == ["id"]
        assert t1.columns and t1.columns[0]["name"] == "c1"


class TestSaveToFile:
    def test_writes_json(self, tmp_path) -> None:
        f = _make_fetcher()
        f.hierarchy = SnowflakeHierarchy(
            fetched_at="now",
            databases=[SnowflakeDatabase(name="X")],
        )
        out = tmp_path / "sub" / "out.json"
        f.save_to_file(str(out))
        assert out.exists()
        content = json.loads(out.read_text(encoding="utf-8"))
        assert content["databases"][0]["name"] == "X"


class TestPrintSummary:
    def test_prints(self, capsys) -> None:
        f = _make_fetcher()
        f.print_summary()
        out = capsys.readouterr().out
        assert "Snowflake Data Summary" in out
        assert "databases: 0" in out
