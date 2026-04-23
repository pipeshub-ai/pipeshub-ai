"""Unit tests for app.modules.parsers.sql.sql_view_parser."""

import io
import json

import pytest

from app.models.blocks import DataFormat, GroupSubType, GroupType
from app.modules.parsers.sql.sql_view_parser import SQLViewParser


@pytest.fixture
def parser():
    return SQLViewParser()


def _stream(data: dict) -> io.BytesIO:
    return io.BytesIO(json.dumps(data).encode("utf-8"))


# ===========================================================================
# parse_stream — invalid input
# ===========================================================================


class TestParseStreamInvalid:

    def test_invalid_json_returns_empty(self, parser):
        stream = io.BytesIO(b"not valid json")
        result = parser.parse_stream(stream)
        assert result.blocks == []
        assert result.block_groups == []

    def test_empty_object_uses_defaults(self, parser):
        result = parser.parse_stream(_stream({}))
        assert len(result.block_groups) == 1
        bg = result.block_groups[0]
        assert bg.name == "unknown_view"
        assert bg.data["fqn"] == "unknown_db.unknown_schema.unknown_view"
        assert bg.data["definition"] == ""

    def test_truncated_json_returns_empty(self, parser):
        stream = io.BytesIO(b'{"view_name": "v1"')
        result = parser.parse_stream(stream)
        assert result.blocks == []
        assert result.block_groups == []


# ===========================================================================
# parse_stream — basic output
# ===========================================================================


class TestParseStreamBasic:

    def test_block_group_metadata(self, parser):
        data = {
            "view_name": "active_users",
            "database_name": "mydb",
            "schema_name": "public",
            "definition": "SELECT * FROM users WHERE active = true",
        }
        result = parser.parse_stream(_stream(data))

        assert result.blocks == []
        assert len(result.block_groups) == 1

        bg = result.block_groups[0]
        assert bg.index == 0
        assert bg.type == GroupType.VIEW
        assert bg.sub_type == GroupSubType.SQL_VIEW
        assert bg.name == "active_users"
        assert bg.format == DataFormat.JSON
        assert bg.children.block_ranges == []
        assert bg.children.block_group_ranges == []

    def test_fqn(self, parser):
        data = {
            "view_name": "v1",
            "database_name": "db",
            "schema_name": "sch",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["fqn"] == "db.sch.v1"

    def test_definition_stored(self, parser):
        sql = "SELECT id, name FROM employees"
        data = {
            "view_name": "emp_view",
            "database_name": "hr",
            "schema_name": "public",
            "definition": sql,
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["definition"] == sql

    def test_source_tables(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
            "source_tables": ["users", "orders"],
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["source_tables"] == ["users", "orders"]

    def test_source_table_ddls(self, parser):
        ddls = {"users": "CREATE TABLE users (id INT);"}
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
            "source_table_ddls": ddls,
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["source_table_ddls"] == ddls

    def test_source_tables_summary(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
            "source_tables_summary": "Summary of tables",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["source_tables_summary"] == "Summary of tables"

    def test_is_secure_flag(self, parser):
        data = {
            "view_name": "secure_view",
            "database_name": "db",
            "schema_name": "s",
            "is_secure": True,
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["is_secure"] is True

    def test_is_secure_default_false(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["is_secure"] is False

    def test_comment(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
            "comment": "This view shows active users",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["comment"] == "This view shows active users"

    def test_comment_default_none(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["comment"] is None

    def test_type_field(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["type"] == "VIEW"

    def test_database_and_schema_in_data(self, parser):
        data = {
            "view_name": "v",
            "database_name": "mydb",
            "schema_name": "myschema",
        }
        result = parser.parse_stream(_stream(data))
        bg_data = result.block_groups[0].data
        assert bg_data["database"] == "mydb"
        assert bg_data["schema"] == "myschema"


# ===========================================================================
# parse_stream — defaults
# ===========================================================================


class TestParseStreamDefaults:

    def test_missing_source_tables_defaults_empty(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["source_tables"] == []

    def test_missing_source_table_ddls_defaults_empty(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["source_table_ddls"] == {}

    def test_missing_definition_defaults_empty(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["definition"] == ""

    def test_missing_source_tables_summary_defaults_empty(self, parser):
        data = {
            "view_name": "v",
            "database_name": "db",
            "schema_name": "s",
        }
        result = parser.parse_stream(_stream(data))
        assert result.block_groups[0].data["source_tables_summary"] == ""


# ===========================================================================
# parse_stream — full data
# ===========================================================================


class TestParseStreamFull:

    def test_full_view_data(self, parser):
        data = {
            "view_name": "order_summary",
            "database_name": "ecommerce",
            "schema_name": "analytics",
            "definition": "SELECT u.name, COUNT(o.id) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name",
            "source_tables": ["users", "orders"],
            "source_table_ddls": {
                "users": "CREATE TABLE users (id INT, name VARCHAR(100));",
                "orders": "CREATE TABLE orders (id INT, user_id INT);",
            },
            "source_tables_summary": "users: user data, orders: order records",
            "is_secure": True,
            "comment": "Aggregated order counts per user",
        }
        result = parser.parse_stream(_stream(data))

        assert len(result.block_groups) == 1
        assert result.blocks == []

        bg = result.block_groups[0]
        assert bg.name == "order_summary"
        assert bg.data["fqn"] == "ecommerce.analytics.order_summary"
        assert bg.data["is_secure"] is True
        assert bg.data["comment"] == "Aggregated order counts per user"
        assert len(bg.data["source_tables"]) == 2
        assert len(bg.data["source_table_ddls"]) == 2
        assert "JOIN orders" in bg.data["definition"]
