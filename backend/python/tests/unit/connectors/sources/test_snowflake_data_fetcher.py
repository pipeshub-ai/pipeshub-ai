"""SnowflakeDataFetcher: catalog SQL and how its rows map to dataclasses."""

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.connectors.core.sql.key_flags import KeyDefinition
from app.connectors.sources.snowflake.data_fetcher import (
    SnowflakeDataFetcher,
    SnowflakeFetchError,
    SnowflakeTable,
    schema_key,
)
from app.sources.external.snowflake.statements import SnowflakeColumn, SnowflakeResult

if TYPE_CHECKING:
    from collections.abc import Callable


def result(rows: list[dict[str, Any]]) -> SnowflakeResult:
    names = list(rows[0].keys()) if rows else []
    return SnowflakeResult(
        columns=[SnowflakeColumn(name, "text") for name in names],
        rows=[[row[name] for name in names] for row in rows],
    )


class FakeRunner:
    """Answers statements by the first registered substring they contain."""

    def __init__(self) -> None:
        self.answers: list[tuple[str, Callable[[], SnowflakeResult]]] = []
        self.statements: list[str] = []

    def on(self, fragment: str, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        def answer() -> SnowflakeResult:
            if error:
                raise error
            return result(rows or [])
        self.answers.append((fragment, answer))

    async def run(self, statement: str, **_: object) -> SnowflakeResult:
        self.statements.append(statement)
        for fragment, answer in self.answers:
            if fragment in statement:
                return answer()
        return result([])


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def fetcher(runner: FakeRunner) -> SnowflakeDataFetcher:
    return SnowflakeDataFetcher(runner)  # type: ignore[arg-type]


REFUSED = SnowflakeFetchError("Insufficient privileges", sqlstate="42501")


class TestDatabases:
    async def test_show_databases_maps_rows_and_skips_system_ones(self, runner, fetcher) -> None:
        # SHOW output columns are lowercase.
        runner.on("SHOW DATABASES", [
            {"name": "SALES", "kind": "STANDARD", "owner": "SYSADMIN", "comment": "", "created_on": "2024-01-01"},
            {"name": "SNOWFLAKE", "kind": "APPLICATION", "owner": "", "comment": "", "created_on": None},
            {"name": "APP_DB", "kind": "APPLICATION", "owner": "", "comment": "", "created_on": None},
            {"name": "USER$ME", "kind": "PERSONAL DATABASE", "owner": "", "comment": "", "created_on": None},
            {"name": "PKG", "kind": "APPLICATION PACKAGE", "owner": "", "comment": "", "created_on": None},
            {"name": "SHARED", "kind": "IMPORTED DATABASE", "owner": "", "comment": "x", "created_on": None},
        ])
        databases = await fetcher.list_databases()
        assert [d.name for d in databases] == ["SALES", "SHARED"]
        assert databases[0].owner == "SYSADMIN"
        assert databases[0].comment is None


class TestSchemaObjects:
    async def test_schemas_exclude_information_schema(self, runner, fetcher) -> None:
        runner.on("SCHEMATA", [{"SCHEMA_NAME": "PUBLIC", "SCHEMA_OWNER": "R", "CREATED": "t", "COMMENT": None}])
        schemas = await fetcher.list_schemas("My DB")
        assert [s.name for s in schemas] == ["PUBLIC"]
        assert 'FROM "My DB".INFORMATION_SCHEMA.SCHEMATA' in runner.statements[0]
        assert "<> 'INFORMATION_SCHEMA'" in runner.statements[0]

    async def test_tables_filter_the_schema_as_a_literal_and_map_metadata(self, runner, fetcher) -> None:
        runner.on("INFORMATION_SCHEMA.TABLES", [{
            "TABLE_NAME": "ORDERS", "TABLE_TYPE": "BASE TABLE", "ROW_COUNT": 10, "BYTES": 2048,
            "TABLE_OWNER": "R", "COMMENT": None, "CREATED": "2024-01-01T00:00:00+00:00",
            "LAST_ALTERED": "2024-02-01T00:00:00+00:00", "LAST_DDL": "2024-01-15T00:00:00+00:00",
        }])
        (table,) = await fetcher.list_tables("DB", "it's")
        statement = runner.statements[0]
        assert "TABLE_SCHEMA = 'it''s'" in statement
        assert "TABLE_TYPE IN ('BASE TABLE', 'EXTERNAL TABLE')" in statement
        assert (table.name, table.row_count, table.bytes) == ("ORDERS", 10, 2048)
        assert (table.created_at, table.last_altered, table.last_ddl) == (
            "2024-01-01T00:00:00+00:00", "2024-02-01T00:00:00+00:00", "2024-01-15T00:00:00+00:00",
        )
        assert table.fqn == "DB.it's.ORDERS"

    async def test_views_map_definition_and_secure_flag(self, runner, fetcher) -> None:
        runner.on("INFORMATION_SCHEMA.VIEWS", [
            {"TABLE_NAME": "V1", "VIEW_DEFINITION": "select 1", "IS_SECURE": "NO",
             "TABLE_OWNER": "R", "COMMENT": "c", "CREATED": "t", "LAST_ALTERED": "t"},
            {"TABLE_NAME": "V2", "VIEW_DEFINITION": None, "IS_SECURE": "YES",
             "TABLE_OWNER": "R", "COMMENT": None, "CREATED": "t", "LAST_ALTERED": "t"},
        ])
        v1, v2 = await fetcher.list_views("DB", "S")
        assert (v1.definition, v1.is_secure) == ("select 1", False)
        assert (v2.definition, v2.is_secure) == (None, True)

    async def test_get_view_reads_one_view(self, runner, fetcher) -> None:
        runner.on("INFORMATION_SCHEMA.VIEWS", [
            {"TABLE_NAME": "V1", "VIEW_DEFINITION": "select 1", "IS_SECURE": "NO",
             "TABLE_OWNER": "R", "COMMENT": None, "CREATED": "t", "LAST_ALTERED": "t"},
        ])
        view = await fetcher.get_view("DB", "S", "V1")
        assert view is not None and view.name == "V1"
        assert "AND TABLE_NAME = 'V1'" in runner.statements[0]

    async def test_get_view_returns_none_when_absent(self, fetcher) -> None:
        assert await fetcher.get_view("DB", "S", "V1") is None

    async def test_stages_map_type_case_insensitively(self, runner, fetcher) -> None:
        runner.on("INFORMATION_SCHEMA.STAGES", [
            {"STAGE_NAME": "EXT", "STAGE_TYPE": "External Named", "STAGE_URL": "s3://b",
             "STAGE_OWNER": "R", "COMMENT": None, "CREATED": "t"},
            {"STAGE_NAME": "INT", "STAGE_TYPE": "Internal Named", "STAGE_URL": "",
             "STAGE_OWNER": "R", "COMMENT": None, "CREATED": "t"},
        ])
        ext, internal = await fetcher.list_stages("DB", "S")
        assert (ext.stage_type, ext.url) == ("EXTERNAL", "s3://b")
        assert (internal.stage_type, internal.url) == ("INTERNAL", None)

    async def test_stage_files_read_the_quoted_directory(self, runner, fetcher) -> None:
        runner.on("DIRECTORY(", [
            {"RELATIVE_PATH": "a/b.csv", "SIZE": 12, "LAST_MODIFIED": "t", "MD5": "m", "FILE_URL": "u"},
            {"RELATIVE_PATH": None, "SIZE": 0, "LAST_MODIFIED": None, "MD5": None, "FILE_URL": None},
        ])
        (file,) = await fetcher.list_stage_files("DB", "S", "my stage")
        assert 'DIRECTORY(@"DB"."S"."my stage")' in runner.statements[0]
        assert (file.relative_path, file.size, file.md5, file.file_name) == ("a/b.csv", 12, "m", "b.csv")

    async def test_object_names_read_a_whole_database_at_once(self, runner, fetcher) -> None:
        runner.on("INFORMATION_SCHEMA.TABLES", [
            {"TABLE_SCHEMA": "S", "TABLE_NAME": "T"},
            {"TABLE_SCHEMA": "a.b", "TABLE_NAME": "T"},
        ])
        names = await fetcher.object_names("DB", "tables")
        assert names == ["DB.S.T", 'DB."a.b".T']
        assert "TABLE_SCHEMA =" not in runner.statements[0]

    async def test_stage_object_names(self, runner, fetcher) -> None:
        runner.on("INFORMATION_SCHEMA.STAGES", [{"STAGE_SCHEMA": "S", "STAGE_NAME": "ST"}])
        assert await fetcher.object_names("DB", "stages") == ["DB.S.ST"]


class TestColumnsAndKeys:
    async def test_columns_grouped_by_table_in_ordinal_order(self, runner, fetcher) -> None:
        runner.on("INFORMATION_SCHEMA.COLUMNS", [
            {"TABLE_NAME": "T", "COLUMN_NAME": "ID", "ORDINAL_POSITION": 1, "DATA_TYPE": "NUMBER",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": 38, "NUMERIC_SCALE": 0,
             "IS_NULLABLE": "NO", "COLUMN_DEFAULT": None, "COMMENT": None},
            {"TABLE_NAME": "T", "COLUMN_NAME": "NAME", "ORDINAL_POSITION": 2, "DATA_TYPE": "TEXT",
             "CHARACTER_MAXIMUM_LENGTH": 255, "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None,
             "IS_NULLABLE": "YES", "COLUMN_DEFAULT": None, "COMMENT": "who"},
        ])
        columns = await fetcher.columns_by_table("DB", "S")
        assert [(c.name, c.ordinal_position) for c in columns["T"]] == [("ID", 1), ("NAME", 2)]
        assert columns["T"][0].nullable is False and columns["T"][1].nullable is True
        assert columns["T"][0].numeric_precision == 38 and columns["T"][1].comment == "who"
        # Precision and scale tell an integer key from a measure.
        assert (columns["T"][0].data_type, columns["T"][1].data_type) == ("NUMBER(38,0)", "TEXT")
        assert "ORDER BY TABLE_NAME, ORDINAL_POSITION" in runner.statements[0]

    async def test_columns_of_one_table(self, runner, fetcher) -> None:
        await fetcher.columns_by_table("DB", "S", "T'x")
        assert "AND TABLE_NAME = 'T''x'" in runner.statements[0]

    async def test_key_definitions_group_by_constraint_in_key_order(self, runner, fetcher) -> None:
        runner.on("SHOW PRIMARY KEYS", [
            {"table_name": "T", "column_name": "B", "key_sequence": 2, "constraint_name": "PK_T"},
            {"table_name": "T", "column_name": "A", "key_sequence": 1, "constraint_name": "PK_T"},
            {"table_name": "U", "column_name": "ID", "key_sequence": 1, "constraint_name": "PK_U"},
        ])
        runner.on("SHOW UNIQUE KEYS", [
            {"table_name": "T", "column_name": "CODE", "key_sequence": 1, "constraint_name": "UQ_CODE"},
            {"table_name": "T", "column_name": "Y", "key_sequence": 2, "constraint_name": "UQ_XY"},
            {"table_name": "T", "column_name": "X", "key_sequence": 1, "constraint_name": "UQ_XY"},
        ])
        keys = await fetcher.key_definitions("DB", "my schema")
        assert keys == {
            "T": [
                KeyDefinition("primary", ("A", "B")),
                KeyDefinition("unique", ("CODE",)),
                KeyDefinition("unique", ("X", "Y")),
            ],
            "U": [KeyDefinition("primary", ("ID",))],
        }
        assert runner.statements == [
            'SHOW PRIMARY KEYS IN SCHEMA "DB"."my schema"',
            'SHOW UNIQUE KEYS IN SCHEMA "DB"."my schema"',
        ]

    async def test_a_key_column_without_its_position_is_unreadable(self, runner, fetcher) -> None:
        runner.on("SHOW PRIMARY KEYS", [{"table_name": "T", "column_name": "A", "key_sequence": None}])
        with pytest.raises(SnowflakeFetchError):
            await fetcher.key_definitions("DB", "S")

    async def test_keys_of_one_table(self, runner, fetcher) -> None:
        await fetcher.key_definitions("DB", "S", "T")
        await fetcher.foreign_keys("DB", "S", "T")
        assert runner.statements == [
            'SHOW PRIMARY KEYS IN TABLE "DB"."S"."T"',
            'SHOW UNIQUE KEYS IN TABLE "DB"."S"."T"',
            'SHOW IMPORTED KEYS IN TABLE "DB"."S"."T"',
        ]

    async def test_foreign_keys_keep_the_target_database(self, runner, fetcher) -> None:
        runner.on("SHOW IMPORTED KEYS", [{
            "fk_name": "FK1", "fk_database_name": "DB", "fk_schema_name": "S", "fk_table_name": "ORDERS",
            "fk_column_name": "CUSTOMER_ID", "pk_database_name": "OTHER", "pk_schema_name": "CRM",
            "pk_table_name": "CUSTOMERS", "pk_column_name": "ID",
        }, {
            "fk_name": "", "fk_database_name": "", "fk_schema_name": "", "fk_table_name": "",
            "fk_column_name": "", "pk_database_name": "", "pk_schema_name": "", "pk_table_name": "X",
            "pk_column_name": "",
        }])
        (fk,) = await fetcher.foreign_keys("DB", "S")
        assert fk.target_fqn == "OTHER.CRM.CUSTOMERS"
        assert (fk.source_table, fk.source_column, fk.target_column) == ("ORDERS", "CUSTOMER_ID", "ID")

    async def test_get_ddl_quotes_the_name_inside_a_literal(self, runner, fetcher) -> None:
        runner.on("GET_DDL", [{"DDL": "create table ..."}])
        assert await fetcher.get_table_ddl("DB", "S", "it's a \\ table") == "create table ..."
        assert runner.statements[0] == (
            "SELECT GET_DDL('TABLE', '\"DB\".\"S\".\"it''s a \\\\ table\"') AS DDL"
        )

    async def test_get_ddl_without_text_is_none(self, runner, fetcher) -> None:
        runner.on("GET_DDL", [{"DDL": ""}])
        assert await fetcher.get_ddl("VIEW", "DB", "S", "V") is None

    async def test_presigned_url(self, runner, fetcher) -> None:
        runner.on("GET_PRESIGNED_URL", [{"URL": "https://signed"}])
        assert await fetcher.presigned_url("DB", "S", "ST", "it's.csv") == "https://signed"
        assert runner.statements[0] == (
            "SELECT GET_PRESIGNED_URL(@\"DB\".\"S\".\"ST\", 'it''s.csv', 3600) AS URL"
        )


class TestValueCountsAndSamples:
    async def test_exact_value_counts_quote_their_aliases(self, runner, fetcher) -> None:
        runner.on("GROUP BY", [{"value": 3, "count": 10}, {"value": None, "count": 2}])
        counts = await fetcher.column_value_counts("DB", "S", "T", "STATUS", limit=5, approximate=False)
        assert counts == [{"value": "3", "count": 10}, {"value": None, "count": 2}]
        assert runner.statements[0] == (
            'SELECT "STATUS" AS "value", COUNT(*) AS "count" FROM "DB"."S"."T" '
            "GROUP BY 1 ORDER BY 2 DESC, 1 NULLS LAST LIMIT 5"
        )

    async def test_approximate_value_counts(self, runner, fetcher) -> None:
        runner.on("APPROX_TOP_K", [{"top": json.dumps([["OPEN", 9], [7, 3], ["bad"], [None, 4]])}])
        counts = await fetcher.column_value_counts("DB", "S", "T", "STATUS", limit=5, approximate=True)
        assert counts == [{"value": "OPEN", "count": 9}, {"value": "7", "count": 3}]
        assert 'APPROX_TOP_K("STATUS", 5, 50)' in runner.statements[0]

    async def test_a_sample_ordered_by_key(self, runner, fetcher) -> None:
        runner.on("SELECT *", [{"ID": 1, "NAME": "a"}])
        names, rows = await fetcher.sample_rows("DB", "S", "T", limit=10, order_by=["ID"], max_bytes=1000)
        assert (names, rows) == (["ID", "NAME"], [[1, "a"]])
        assert runner.statements[0] == 'SELECT * FROM "DB"."S"."T" ORDER BY "ID" LIMIT 10'

    async def test_a_sample_without_a_key_is_ordered_by_row_hash_and_the_hash_dropped(self, runner, fetcher) -> None:
        runner.on("SELECT *", [{"ID": 1, "_pipeshub_row_hash": 99}])
        names, rows = await fetcher.sample_rows("DB", "S", "T", limit=10, order_by=[], max_bytes=1000)
        assert (names, rows) == (["ID"], [[1]])
        assert 'ORDER BY "_pipeshub_row_hash"' in runner.statements[0]

    async def test_an_unordered_sample(self, runner, fetcher) -> None:
        runner.on("SELECT *", [{"ID": 1}])
        await fetcher.sample_rows("DB", "S", "T", limit=10, order_by=None, max_bytes=1000)
        assert runner.statements[0] == 'SELECT * FROM "DB"."S"."T" LIMIT 10'

    async def test_the_byte_cap_drops_later_rows(self, runner, fetcher) -> None:
        runner.on("SELECT *", [{"V": "x" * 40}, {"V": "y" * 40}, {"V": "z" * 40}])
        _, rows = await fetcher.sample_rows("DB", "S", "T", limit=10, order_by=None, max_bytes=100)
        assert rows == [["x" * 40], ["y" * 40]]


class TestErrorsRaise:
    @pytest.mark.parametrize(
        "read",
        [
            lambda f: f.list_databases(),
            lambda f: f.list_schemas("DB"),
            lambda f: f.list_tables("DB", "S"),
            lambda f: f.list_views("DB", "S"),
            lambda f: f.list_stages("DB", "S"),
            lambda f: f.list_stage_files("DB", "S", "ST"),
            lambda f: f.columns_by_table("DB", "S"),
            lambda f: f.key_definitions("DB", "S"),
            lambda f: f.foreign_keys("DB", "S"),
            lambda f: f.get_table_ddl("DB", "S", "T"),
        ],
    )
    async def test_every_read_raises_when_refused(self, runner, fetcher, read) -> None:
        runner.on("", error=REFUSED)
        with pytest.raises(SnowflakeFetchError):
            await read(fetcher)


class TestFetchAll:
    @staticmethod
    def catalog(runner: FakeRunner) -> None:
        runner.on("SHOW DATABASES", [
            {"name": "DB", "kind": "STANDARD", "owner": "", "comment": "", "created_on": None},
            {"name": "SKIP", "kind": "STANDARD", "owner": "", "comment": "", "created_on": None},
        ])
        runner.on("SCHEMATA", [
            {"SCHEMA_NAME": "GOOD", "SCHEMA_OWNER": "", "CREATED": None, "COMMENT": None},
            {"SCHEMA_NAME": "BAD", "SCHEMA_OWNER": "", "CREATED": None, "COMMENT": None},
        ])

    async def test_one_unreadable_schema_is_skipped_and_counted(self, runner, fetcher) -> None:
        self.catalog(runner)
        runner.on("TABLE_SCHEMA = 'BAD'", error=REFUSED)
        runner.on("INFORMATION_SCHEMA.TABLES", [{
            "TABLE_NAME": "T", "TABLE_TYPE": "BASE TABLE", "ROW_COUNT": 1, "BYTES": 1,
            "TABLE_OWNER": "", "COMMENT": None, "CREATED": None, "LAST_ALTERED": None, "LAST_DDL": None,
        }])
        hierarchy = await fetcher.fetch_all(include_database=lambda d: d == "DB", include_files=False, include_relationships=False)
        assert [d.name for d in hierarchy.databases] == ["DB"]
        assert [t.name for t in hierarchy.tables[schema_key("DB", "GOOD")]] == ["T"]
        assert schema_key("DB", "BAD") not in hierarchy.tables
        assert hierarchy.errors == 1
        assert hierarchy.unreadable_schemas == {schema_key("DB", "BAD")}

    async def test_a_stage_without_a_directory_table_leaves_the_schema_synced(self, runner, fetcher) -> None:
        self.catalog(runner)
        runner.on("INFORMATION_SCHEMA.STAGES", [
            {"STAGE_NAME": "NODIR", "STAGE_TYPE": "Internal Named", "STAGE_URL": "",
             "STAGE_OWNER": "", "COMMENT": None, "CREATED": None},
        ])
        runner.on("DIRECTORY(", error=SnowflakeFetchError(
            'DIRECTORY not enabled for the stage "DB"."GOOD"."NODIR"', sqlstate="55000", code="393901",
        ))
        runner.on("INFORMATION_SCHEMA.TABLES", [{
            "TABLE_NAME": "T", "TABLE_TYPE": "BASE TABLE", "ROW_COUNT": 1, "BYTES": 1,
            "TABLE_OWNER": "", "COMMENT": None, "CREATED": None, "LAST_ALTERED": None, "LAST_DDL": None,
        }])
        hierarchy = await fetcher.fetch_all(
            include_database=lambda d: d == "DB",
            include_schema=lambda key: key == schema_key("DB", "GOOD"),
            include_relationships=False,
        )
        assert [t.name for t in hierarchy.tables[schema_key("DB", "GOOD")]] == ["T"]
        # No files to list is not a failed listing: nothing is kept back, and a full sync is complete.
        assert hierarchy.files == {"DB.GOOD.NODIR": []}
        assert hierarchy.errors == 0
        assert hierarchy.unreadable_stages == set()

    async def test_a_stage_whose_files_could_not_be_listed_is_unreadable(self, runner, fetcher) -> None:
        self.catalog(runner)
        runner.on("INFORMATION_SCHEMA.STAGES", [
            {"STAGE_NAME": "DOCS", "STAGE_TYPE": "Internal Named", "STAGE_URL": "",
             "STAGE_OWNER": "", "COMMENT": None, "CREATED": None},
        ])
        runner.on("DIRECTORY(", error=REFUSED)
        hierarchy = await fetcher.fetch_all(
            include_database=lambda d: d == "DB",
            include_schema=lambda key: key == schema_key("DB", "GOOD"),
            include_relationships=False,
        )
        assert hierarchy.files == {}
        assert hierarchy.errors == 1
        assert hierarchy.unreadable_stages == {"DB.GOOD.DOCS"}

    async def test_schema_filter_skips_reading_other_schemas(self, runner, fetcher) -> None:
        self.catalog(runner)
        await fetcher.fetch_all(include_schema=lambda key: key.endswith(".GOOD"), include_files=False)
        object_reads = [s for s in runner.statements if "TABLE_SCHEMA = " in s or "IN SCHEMA" in s]
        assert object_reads and all("BAD" not in s for s in object_reads)

    @staticmethod
    def relationships(runner: FakeRunner) -> None:
        runner.on("INFORMATION_SCHEMA.COLUMNS", [
            {"TABLE_NAME": "ORDERS", "COLUMN_NAME": "ID", "ORDINAL_POSITION": 1, "DATA_TYPE": "NUMBER",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None,
             "IS_NULLABLE": "NO", "COLUMN_DEFAULT": None, "COMMENT": None},
            {"TABLE_NAME": "ORDERS", "COLUMN_NAME": "LINE", "ORDINAL_POSITION": 2, "DATA_TYPE": "NUMBER",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None,
             "IS_NULLABLE": "NO", "COLUMN_DEFAULT": None, "COMMENT": None},
            {"TABLE_NAME": "CUSTOMERS", "COLUMN_NAME": "ID", "ORDINAL_POSITION": 1, "DATA_TYPE": "NUMBER",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None,
             "IS_NULLABLE": "NO", "COLUMN_DEFAULT": None, "COMMENT": None},
            {"TABLE_NAME": "CUSTOMERS", "COLUMN_NAME": "CODE", "ORDINAL_POSITION": 2, "DATA_TYPE": "TEXT",
             "CHARACTER_MAXIMUM_LENGTH": None, "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None,
             "IS_NULLABLE": "NO", "COLUMN_DEFAULT": None, "COMMENT": None},
        ])
        runner.on("SHOW IMPORTED KEYS", [{
            "fk_name": "FK", "fk_database_name": "DB", "fk_schema_name": "S", "fk_table_name": "ORDERS",
            "fk_column_name": "CID", "pk_database_name": "DB", "pk_schema_name": "S",
            "pk_table_name": "CUSTOMERS", "pk_column_name": "ID",
        }])

    async def test_relationships_are_attached_per_table(self, runner, fetcher) -> None:
        tables = [SnowflakeTable("ORDERS", "DB", "S"), SnowflakeTable("CUSTOMERS", "DB", "S")]
        self.relationships(runner)
        runner.on("SHOW PRIMARY KEYS", [
            {"table_name": "CUSTOMERS", "column_name": "ID", "key_sequence": 1, "constraint_name": "PK_C"},
            {"table_name": "ORDERS", "column_name": "ID", "key_sequence": 1, "constraint_name": "PK_O"},
            {"table_name": "ORDERS", "column_name": "LINE", "key_sequence": 2, "constraint_name": "PK_O"},
        ])
        runner.on("SHOW UNIQUE KEYS", [
            {"table_name": "CUSTOMERS", "column_name": "CODE", "key_sequence": 1, "constraint_name": "UQ"},
        ])
        assert await fetcher.attach_relationships("DB", "S", tables) == 0
        orders, customers = tables
        assert (orders.columns_read, orders.keys_read, orders.foreign_keys_read) == (True, True, True)
        assert orders.fully_read
        assert [c.name for c in orders.columns] == ["ID", "LINE"]
        assert [fk.target_table for fk in orders.foreign_keys] == ["CUSTOMERS"]
        assert orders.primary_keys == ["ID", "LINE"]
        assert all(c.is_part_of_composite_primary_key and not c.is_unique for c in orders.columns)
        assert customers.primary_keys == ["ID"] and customers.foreign_keys == []
        code = customers.columns[1]
        assert (code.is_unique, code.is_primary_key) == (True, False)

    async def test_unread_keys_leave_the_columns(self, runner, fetcher) -> None:
        tables = [SnowflakeTable("ORDERS", "DB", "S")]
        self.relationships(runner)
        runner.on("SHOW UNIQUE KEYS", error=REFUSED)
        assert await fetcher.attach_relationships("DB", "S", tables) == 1
        (orders,) = tables
        assert (orders.columns_read, orders.keys_read) == (True, False)
        assert [c.name for c in orders.columns] == ["ID", "LINE"]
        assert orders.foreign_keys

    async def test_unread_foreign_keys_are_flagged(self, runner, fetcher) -> None:
        tables = [SnowflakeTable("ORDERS", "DB", "S")]
        self.relationships(runner)
        runner.on("SHOW IMPORTED KEYS", error=REFUSED)
        runner.answers.insert(0, runner.answers.pop())
        assert await fetcher.attach_relationships("DB", "S", tables) == 1
        (orders,) = tables
        assert (orders.columns_read, orders.keys_read, orders.foreign_keys_read) == (True, True, False)
        assert not orders.fully_read

    async def test_an_unreadable_database_is_reported(self, runner, fetcher) -> None:
        runner.on("SHOW DATABASES", [{"name": "DB", "kind": "STANDARD", "owner": "", "comment": "", "created_on": None}])
        runner.on("SCHEMATA", error=REFUSED)
        hierarchy = await fetcher.fetch_all()
        assert hierarchy.unreadable_databases == {"DB"} and hierarchy.schemas == {}

    async def test_unread_columns_leave_the_keys(self, runner, fetcher) -> None:
        tables = [SnowflakeTable("ORDERS", "DB", "S")]
        runner.on("INFORMATION_SCHEMA.COLUMNS", error=REFUSED)
        assert await fetcher.attach_relationships("DB", "S", tables) == 1
        assert (tables[0].columns_read, tables[0].keys_read) == (False, True)

    async def test_a_table_missing_from_the_column_listing_is_not_read(self, runner, fetcher) -> None:
        tables = [SnowflakeTable("GHOST", "DB", "S")]
        self.relationships(runner)
        await fetcher.attach_relationships("DB", "S", tables)
        assert tables[0].columns_read is False

    async def test_include_objects_false_reads_only_databases_and_schemas(self, runner, fetcher) -> None:
        self.catalog(runner)
        hierarchy = await fetcher.fetch_all(include_objects=False)
        assert set(hierarchy.schemas) == {"DB", "SKIP"}
        assert hierarchy.tables == {}
        assert all("SHOW DATABASES" in s or "SCHEMATA" in s for s in runner.statements)

    async def test_an_unreadable_database_listing_raises(self, runner, fetcher) -> None:
        runner.on("SHOW DATABASES", error=REFUSED)
        with pytest.raises(SnowflakeFetchError):
            await fetcher.fetch_all()
