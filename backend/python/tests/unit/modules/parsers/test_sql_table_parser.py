"""Unit tests for app.modules.parsers.sql.sql_table_parser."""

import io
import json

import pytest

from app.models.blocks import BlockType, DataFormat, GroupSubType, GroupType
from app.modules.parsers.sql.sql_table_parser import (
    SQLColumnInfo,
    SQLForeignKeyInfo,
    SQLTableInput,
    SQLTableParser,
)


@pytest.fixture
def parser():
    return SQLTableParser()


def _stream(data: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(data).encode("utf-8"))


# ===========================================================================
# Pydantic models
# ===========================================================================


class TestSQLColumnInfo:

    def test_defaults(self):
        col = SQLColumnInfo()
        assert col.name == "unknown"
        assert col.data_type == "VARCHAR"
        assert col.nullable is True
        assert col.default is None
        assert col.is_unique is False
        assert col.character_maximum_length is None
        assert col.numeric_precision is None
        assert col.numeric_scale is None

    def test_custom_values(self):
        col = SQLColumnInfo(
            name="price",
            data_type="numeric",
            nullable=False,
            default="0.00",
            is_unique=True,
            numeric_precision=10,
            numeric_scale=2,
        )
        assert col.name == "price"
        assert col.data_type == "numeric"
        assert col.nullable is False
        assert col.default == "0.00"
        assert col.is_unique is True
        assert col.numeric_precision == 10
        assert col.numeric_scale == 2

    def test_extra_fields_allowed(self):
        col = SQLColumnInfo(name="id", column_type="int(11)")
        assert col.name == "id"
        assert col.column_type == "int(11)"


class TestSQLForeignKeyInfo:

    def test_defaults(self):
        fk = SQLForeignKeyInfo()
        assert fk.constraint_name == "fk"
        assert fk.column_name == ""
        assert fk.foreign_table_schema is None
        assert fk.foreign_database is None
        assert fk.foreign_table_name == ""
        assert fk.foreign_column_name == ""

    def test_reference_namespace_postgres(self):
        fk = SQLForeignKeyInfo(foreign_table_schema="public")
        assert fk.reference_namespace == "public"

    def test_reference_namespace_mariadb(self):
        fk = SQLForeignKeyInfo(foreign_database="mydb")
        assert fk.reference_namespace == "mydb"

    def test_reference_namespace_empty(self):
        fk = SQLForeignKeyInfo()
        assert fk.reference_namespace == ""

    def test_reference_namespace_prefers_postgres(self):
        fk = SQLForeignKeyInfo(foreign_table_schema="pg_schema", foreign_database="mdb")
        assert fk.reference_namespace == "pg_schema"

    def test_reference_target_with_namespace(self):
        fk = SQLForeignKeyInfo(
            foreign_table_schema="public",
            foreign_table_name="departments",
        )
        assert fk.reference_target == "public.departments"

    def test_reference_target_without_namespace(self):
        fk = SQLForeignKeyInfo(foreign_table_name="departments")
        assert fk.reference_target == "departments"

    def test_extra_fields_allowed(self):
        fk = SQLForeignKeyInfo(constraint_name="fk1", on_delete="CASCADE")
        assert fk.on_delete == "CASCADE"


class TestSQLTableInput:

    def test_defaults(self):
        t = SQLTableInput()
        assert t.table_name == "unknown_table"
        assert t.database_name == "unknown_db"
        assert t.schema_name is None
        assert t.columns == []
        assert t.rows == []
        assert t.foreign_keys == []
        assert t.primary_keys == []
        assert t.ddl is None
        assert t.connector_name == ""

    def test_from_dict(self):
        data = {
            "table_name": "users",
            "database_name": "mydb",
            "schema_name": "public",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [{"id": 1}],
            "primary_keys": ["id"],
            "connector_name": "postgresql",
        }
        t = SQLTableInput.model_validate(data)
        assert t.table_name == "users"
        assert len(t.columns) == 1
        assert isinstance(t.columns[0], SQLColumnInfo)
        assert t.columns[0].name == "id"

    def test_extra_fields_allowed(self):
        t = SQLTableInput(table_name="t", extra_field="value")
        assert t.extra_field == "value"


# ===========================================================================
# _build_fqn
# ===========================================================================


class TestBuildFqn:

    def test_with_schema(self, parser):
        assert parser._build_fqn("db", "public", "users") == "db.public.users"

    def test_without_schema(self, parser):
        assert parser._build_fqn("db", None, "users") == "db.users"

    def test_empty_schema(self, parser):
        assert parser._build_fqn("db", "", "users") == "db.users"


# ===========================================================================
# _calculate_content_hash
# ===========================================================================


class TestCalculateContentHash:

    def test_returns_sha256_colon_md5(self, parser):
        h = parser._calculate_content_hash("hello")
        parts = h.split(":")
        assert len(parts) == 2
        assert len(parts[0]) == 64  # SHA256 hex
        assert len(parts[1]) == 32  # MD5 hex

    def test_deterministic(self, parser):
        assert parser._calculate_content_hash("x") == parser._calculate_content_hash("x")

    def test_different_inputs_differ(self, parser):
        assert parser._calculate_content_hash("a") != parser._calculate_content_hash("b")


# ===========================================================================
# _build_full_type
# ===========================================================================


class TestBuildFullType:

    def test_plain_type(self, parser):
        col = SQLColumnInfo(data_type="integer")
        assert parser._build_full_type(col) == "integer"

    def test_varchar_with_length(self, parser):
        col = SQLColumnInfo(data_type="varchar", character_maximum_length=255)
        assert parser._build_full_type(col) == "varchar(255)"

    def test_character_varying_with_length(self, parser):
        col = SQLColumnInfo(data_type="character varying", character_maximum_length=100)
        assert parser._build_full_type(col) == "character varying(100)"

    def test_char_with_length(self, parser):
        col = SQLColumnInfo(data_type="char", character_maximum_length=1)
        assert parser._build_full_type(col) == "char(1)"

    def test_numeric_with_precision(self, parser):
        col = SQLColumnInfo(data_type="numeric", numeric_precision=10)
        assert parser._build_full_type(col) == "numeric(10)"

    def test_numeric_with_precision_and_scale(self, parser):
        col = SQLColumnInfo(data_type="numeric", numeric_precision=10, numeric_scale=2)
        assert parser._build_full_type(col) == "numeric(10,2)"

    def test_decimal_with_precision_and_scale(self, parser):
        col = SQLColumnInfo(data_type="decimal", numeric_precision=8, numeric_scale=3)
        assert parser._build_full_type(col) == "decimal(8,3)"

    def test_numeric_with_zero_scale(self, parser):
        col = SQLColumnInfo(data_type="numeric", numeric_precision=10, numeric_scale=0)
        assert parser._build_full_type(col) == "numeric(10)"

    def test_non_char_type_ignores_length(self, parser):
        col = SQLColumnInfo(data_type="integer", character_maximum_length=10)
        assert parser._build_full_type(col) == "integer"

    def test_non_numeric_type_ignores_precision(self, parser):
        col = SQLColumnInfo(data_type="text", numeric_precision=10)
        assert parser._build_full_type(col) == "text"


# ===========================================================================
# _generate_ddl
# ===========================================================================


class TestGenerateDDL:

    def test_basic_table(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer", nullable=False)]
        ddl = parser._generate_ddl("users", cols, [], [])
        assert "CREATE TABLE users (" in ddl
        assert "id integer NOT NULL" in ddl
        assert ddl.endswith(");")

    def test_with_primary_key(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer", nullable=False)]
        ddl = parser._generate_ddl("users", cols, [], ["id"])
        assert "PRIMARY KEY (id)" in ddl

    def test_with_default(self, parser):
        cols = [SQLColumnInfo(name="status", data_type="varchar", default="'active'")]
        ddl = parser._generate_ddl("t", cols, [], [])
        assert "DEFAULT 'active'" in ddl

    def test_with_unique(self, parser):
        cols = [SQLColumnInfo(name="email", data_type="varchar", is_unique=True)]
        ddl = parser._generate_ddl("t", cols, [], [])
        assert "UNIQUE" in ddl

    def test_unique_not_added_for_primary_key(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer", is_unique=True)]
        ddl = parser._generate_ddl("t", cols, [], ["id"])
        lines = ddl.split("\n")
        col_line = [l for l in lines if "id integer" in l][0]
        assert "UNIQUE" not in col_line

    def test_with_foreign_key(self, parser):
        cols = [SQLColumnInfo(name="dept_id", data_type="integer")]
        fks = [
            SQLForeignKeyInfo(
                constraint_name="fk_dept",
                column_name="dept_id",
                foreign_table_schema="public",
                foreign_table_name="departments",
                foreign_column_name="id",
            )
        ]
        ddl = parser._generate_ddl("employees", cols, fks, [])
        assert "CONSTRAINT fk_dept" in ddl
        assert "FOREIGN KEY (dept_id)" in ddl
        assert "REFERENCES public.departments(id)" in ddl

    def test_with_foreign_key_mariadb(self, parser):
        cols = [SQLColumnInfo(name="dept_id", data_type="int")]
        fks = [
            SQLForeignKeyInfo(
                constraint_name="fk_dept",
                column_name="dept_id",
                foreign_database="mydb",
                foreign_table_name="departments",
                foreign_column_name="id",
            )
        ]
        ddl = parser._generate_ddl("employees", cols, fks, [])
        assert "REFERENCES mydb.departments(id)" in ddl

    def test_multiple_columns(self, parser):
        cols = [
            SQLColumnInfo(name="id", data_type="integer", nullable=False),
            SQLColumnInfo(name="name", data_type="varchar", character_maximum_length=100),
            SQLColumnInfo(name="age", data_type="integer"),
        ]
        ddl = parser._generate_ddl("people", cols, [], ["id"])
        assert "id integer NOT NULL" in ddl
        assert "name varchar(100)" in ddl
        assert "age integer" in ddl
        assert "PRIMARY KEY (id)" in ddl

    def test_composite_primary_key(self, parser):
        cols = [
            SQLColumnInfo(name="a", data_type="integer"),
            SQLColumnInfo(name="b", data_type="integer"),
        ]
        ddl = parser._generate_ddl("t", cols, [], ["a", "b"])
        assert "PRIMARY KEY (a, b)" in ddl


# ===========================================================================
# _build_schema_row
# ===========================================================================


class TestBuildSchemaRow:

    def test_basic(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer")]
        result = parser._build_schema_row(cols, [])
        assert result == {"id": "integer"}

    def test_not_null(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer", nullable=False)]
        result = parser._build_schema_row(cols, [])
        assert "NOT NULL" in result["id"]

    def test_primary_key(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer")]
        result = parser._build_schema_row(cols, ["id"])
        assert "PRIMARY KEY" in result["id"]

    def test_unique(self, parser):
        cols = [SQLColumnInfo(name="email", data_type="varchar", is_unique=True)]
        result = parser._build_schema_row(cols, [])
        assert "UNIQUE" in result["email"]

    def test_unique_suppressed_for_pk(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer", is_unique=True)]
        result = parser._build_schema_row(cols, ["id"])
        assert "UNIQUE" not in result["id"]
        assert "PRIMARY KEY" in result["id"]

    def test_default_value(self, parser):
        cols = [SQLColumnInfo(name="status", data_type="varchar", default="'active'")]
        result = parser._build_schema_row(cols, [])
        assert "DEFAULT 'active'" in result["status"]

    def test_long_default_truncated(self, parser):
        long_default = "x" * 60
        cols = [SQLColumnInfo(name="col", data_type="text", default=long_default)]
        result = parser._build_schema_row(cols, [])
        assert "..." in result["col"]

    def test_with_type_length(self, parser):
        cols = [SQLColumnInfo(name="name", data_type="varchar", character_maximum_length=255)]
        result = parser._build_schema_row(cols, [])
        assert "varchar(255)" in result["name"]


# ===========================================================================
# _generate_detailed_table_summary
# ===========================================================================


class TestGenerateDetailedTableSummary:

    def test_basic_summary(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer")]
        summary = parser._generate_detailed_table_summary(
            fqn="db.public.users",
            columns=cols,
            row_count=100,
            primary_keys=[],
            foreign_keys=[],
        )
        assert "db.public.users" in summary
        assert "1 columns" in summary
        assert "100 rows" in summary
        assert "SQL table" in summary
        assert "- id: integer" in summary

    def test_with_connector_name(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer")]
        summary = parser._generate_detailed_table_summary(
            fqn="db.users",
            columns=cols,
            row_count=0,
            primary_keys=[],
            foreign_keys=[],
            connector_name="MariaDB",
        )
        assert "MariaDB SQL table" in summary

    def test_primary_key_annotation(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer")]
        summary = parser._generate_detailed_table_summary(
            fqn="db.t", columns=cols, row_count=0,
            primary_keys=["id"], foreign_keys=[],
        )
        assert "PRIMARY KEY" in summary

    def test_not_null_annotation(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer", nullable=False)]
        summary = parser._generate_detailed_table_summary(
            fqn="db.t", columns=cols, row_count=0,
            primary_keys=[], foreign_keys=[],
        )
        assert "NOT NULL" in summary

    def test_unique_annotation(self, parser):
        cols = [SQLColumnInfo(name="email", data_type="varchar", is_unique=True)]
        summary = parser._generate_detailed_table_summary(
            fqn="db.t", columns=cols, row_count=0,
            primary_keys=[], foreign_keys=[],
        )
        assert "UNIQUE" in summary

    def test_foreign_key_annotation(self, parser):
        cols = [SQLColumnInfo(name="dept_id", data_type="integer")]
        fks = [
            SQLForeignKeyInfo(
                column_name="dept_id",
                foreign_table_schema="public",
                foreign_table_name="depts",
                foreign_column_name="id",
            )
        ]
        summary = parser._generate_detailed_table_summary(
            fqn="db.t", columns=cols, row_count=0,
            primary_keys=[], foreign_keys=fks,
        )
        assert "FK->public.depts" in summary
        assert "Foreign Key Relationships:" in summary
        assert "dept_id references public.depts(id)" in summary

    def test_default_annotation(self, parser):
        cols = [SQLColumnInfo(name="status", data_type="varchar", default="'active'")]
        summary = parser._generate_detailed_table_summary(
            fqn="db.t", columns=cols, row_count=0,
            primary_keys=[], foreign_keys=[],
        )
        assert "DEFAULT 'active'" in summary

    def test_long_default_truncated(self, parser):
        long_val = "a" * 40
        cols = [SQLColumnInfo(name="col", data_type="text", default=long_val)]
        summary = parser._generate_detailed_table_summary(
            fqn="db.t", columns=cols, row_count=0,
            primary_keys=[], foreign_keys=[],
        )
        assert "..." in summary

    def test_no_fk_section_when_none(self, parser):
        cols = [SQLColumnInfo(name="id", data_type="integer")]
        summary = parser._generate_detailed_table_summary(
            fqn="db.t", columns=cols, row_count=0,
            primary_keys=[], foreign_keys=[],
        )
        assert "Foreign Key Relationships:" not in summary


# ===========================================================================
# parse_stream
# ===========================================================================


class TestParseStream:

    def test_empty_columns_returns_empty(self, parser):
        data = {"table_name": "t", "database_name": "db", "columns": []}
        result = parser.parse_stream(_stream(data))
        assert result.blocks == []
        assert result.block_groups == []

    def test_invalid_json_returns_empty(self, parser):
        stream = io.BytesIO(b"not json")
        result = parser.parse_stream(stream)
        assert result.blocks == []
        assert result.block_groups == []

    def test_basic_table_with_dict_rows(self, parser):
        data = {
            "table_name": "users",
            "database_name": "mydb",
            "columns": [
                {"name": "id", "data_type": "integer"},
                {"name": "name", "data_type": "varchar"},
            ],
            "rows": [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
            ],
            "primary_keys": ["id"],
        }
        result = parser.parse_stream(_stream(data))
        assert len(result.blocks) == 2
        assert len(result.block_groups) == 1

        bg = result.block_groups[0]
        assert bg.name == "users"
        assert bg.type == GroupType.TABLE
        assert bg.sub_type == GroupSubType.SQL_TABLE
        assert bg.table_metadata.num_of_rows == 2
        assert bg.table_metadata.num_of_cols == 2
        assert bg.table_metadata.column_names == ["id", "name"]
        assert bg.data["fqn"] == "mydb.users"
        assert bg.data["primary_keys"] == ["id"]

    def test_basic_table_with_list_rows(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [
                {"name": "a", "data_type": "integer"},
                {"name": "b", "data_type": "text"},
            ],
            "rows": [
                [1, "x"],
                [2, "y"],
            ],
        }
        result = parser.parse_stream(_stream(data))
        assert len(result.blocks) == 2

        row_data = json.loads(result.blocks[0].data["row"])
        assert row_data == {"a": 1, "b": "x"}

    def test_fqn_with_schema(self, parser):
        data = {
            "table_name": "users",
            "database_name": "mydb",
            "schema_name": "public",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [],
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["fqn"] == "mydb.public.users"

    def test_fqn_without_schema(self, parser):
        data = {
            "table_name": "users",
            "database_name": "mydb",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [],
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["fqn"] == "mydb.users"

    def test_provided_ddl_used(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [],
            "ddl": "CREATE TABLE t (id INT);",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["ddl"] == "CREATE TABLE t (id INT);"

    def test_ddl_generated_when_missing(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [],
        }
        result = parser.parse_stream(_stream(data))
        ddl = result.block_groups[0].data["ddl"]
        assert "CREATE TABLE t" in ddl

    def test_foreign_keys_in_output(self, parser):
        data = {
            "table_name": "orders",
            "database_name": "db",
            "columns": [
                {"name": "id", "data_type": "integer"},
                {"name": "user_id", "data_type": "integer"},
            ],
            "rows": [],
            "foreign_keys": [
                {
                    "constraint_name": "fk_user",
                    "column_name": "user_id",
                    "foreign_table_schema": "public",
                    "foreign_table_name": "users",
                    "foreign_column_name": "id",
                }
            ],
        }
        result = parser.parse_stream(_stream(data))
        fk_list = result.block_groups[0].data["foreign_keys"]
        assert len(fk_list) == 1
        assert fk_list[0]["constraint_name"] == "fk_user"

    def test_connector_name_in_summary(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [],
            "connector_name": "PostgreSQL",
        }
        result = parser.parse_stream(_stream(data))
        summary = result.block_groups[0].data["table_summary"]
        assert "PostgreSQL SQL table" in summary

    def test_block_has_row_data(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [{"id": 42}],
        }
        result = parser.parse_stream(_stream(data))
        block = result.blocks[0]
        assert block.type == BlockType.TABLE_ROW
        assert block.format == DataFormat.JSON
        assert block.parent_index == 0
        row = json.loads(block.data["row"])
        assert row["id"] == 42
        assert "id: 42" in block.data["row_natural_language_text"]

    def test_content_hash_set_on_blocks(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [{"id": 1}],
        }
        result = parser.parse_stream(_stream(data))
        assert result.blocks[0].content_hash is not None
        assert ":" in result.blocks[0].content_hash

    def test_schema_content_hash_set_on_block_group(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [],
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].content_hash is not None
        assert ":" in result.block_groups[0].content_hash

    def test_schema_hash_stable_across_row_count_changes(self, parser):
        base = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "primary_keys": ["id"],
        }
        r1 = parser.parse_stream(_stream({**base, "rows": [{"id": 1}]}))
        r2 = parser.parse_stream(_stream({**base, "rows": [{"id": 1}, {"id": 2}]}))
        assert r1.block_groups[0].content_hash == r2.block_groups[0].content_hash

    def test_schema_hash_changes_on_column_change(self, parser):
        base = {
            "table_name": "t",
            "database_name": "db",
            "rows": [],
        }
        r1 = parser.parse_stream(_stream({
            **base,
            "columns": [{"name": "id", "data_type": "integer"}],
        }))
        r2 = parser.parse_stream(_stream({
            **base,
            "columns": [{"name": "id", "data_type": "bigint"}],
        }))
        assert r1.block_groups[0].content_hash != r2.block_groups[0].content_hash

    def test_children_match_blocks(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [{"id": 1}, {"id": 2}, {"id": 3}],
        }
        result = parser.parse_stream(_stream(data))
        children = result.block_groups[0].children
        assert len(children.block_ranges) == 1
        assert children.block_ranges[0].start == 0
        assert children.block_ranges[0].end == 2

    def test_no_rows_produces_no_blocks(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [{"name": "id", "data_type": "integer"}],
            "rows": [],
        }
        result = parser.parse_stream(_stream(data))
        assert result.blocks == []
        children = result.block_groups[0].children
        assert children.block_ranges == []
        assert result.block_groups[0].table_metadata.num_of_rows == 0

    def test_short_row_padded_with_none(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [
                {"name": "a", "data_type": "integer"},
                {"name": "b", "data_type": "text"},
                {"name": "c", "data_type": "text"},
            ],
            "rows": [[1]],
        }
        result = parser.parse_stream(_stream(data))
        row = json.loads(result.blocks[0].data["row"])
        assert row["a"] == 1
        assert row["b"] is None
        assert row["c"] is None

    def test_schema_row_in_output(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [
                {"name": "id", "data_type": "integer", "nullable": False},
                {"name": "name", "data_type": "varchar", "character_maximum_length": 50},
            ],
            "rows": [],
            "primary_keys": ["id"],
        }
        result = parser.parse_stream(_stream(data))
        schema_row = result.block_groups[0].data["schema_row"]
        assert "NOT NULL" in schema_row["id"]
        assert "PRIMARY KEY" in schema_row["id"]
        assert "varchar(50)" in schema_row["name"]

    def test_table_metadata(self, parser):
        data = {
            "table_name": "t",
            "database_name": "db",
            "columns": [
                {"name": "a", "data_type": "integer"},
                {"name": "b", "data_type": "text"},
            ],
            "rows": [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}],
        }
        result = parser.parse_stream(_stream(data))
        meta = result.block_groups[0].table_metadata
        assert meta.num_of_rows == 2
        assert meta.num_of_cols == 2
        assert meta.has_header is True
        assert meta.column_names == ["a", "b"]
