"""Tests for app.utils.execute_query — SQL query execution tool."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.utils.execute_query import (
    ExecuteQueryArgs,
    _detect_source_type,
    _is_keyword_present,
    _is_query_safe,
    _results_to_markdown,
    agent_knowledge_has_sql_connector,
    has_sql_connector_configured,
)


# ===========================================================================
# ExecuteQueryArgs
# ===========================================================================


class TestExecuteQueryArgs:
    def test_valid_args(self):
        args = ExecuteQueryArgs(
            query="SELECT 1",
            source_name="PostgreSQL",
            connector_id="conn-1",
        )
        assert args.query == "SELECT 1"
        assert args.source_name == "PostgreSQL"
        assert args.connector_id == "conn-1"
        assert "Executing SQL query" in args.reason

    def test_custom_reason(self):
        args = ExecuteQueryArgs(
            query="SELECT 1",
            source_name="Snowflake",
            connector_id="conn-2",
            reason="Need row count",
        )
        assert args.reason == "Need row count"

    def test_missing_query_fails(self):
        with pytest.raises(ValidationError):
            ExecuteQueryArgs(source_name="PostgreSQL", connector_id="conn-1")

    def test_missing_source_name_fails(self):
        with pytest.raises(ValidationError):
            ExecuteQueryArgs(query="SELECT 1", connector_id="conn-1")

    def test_missing_connector_id_fails(self):
        with pytest.raises(ValidationError):
            ExecuteQueryArgs(query="SELECT 1", source_name="PostgreSQL")


# ===========================================================================
# _detect_source_type
# ===========================================================================


class TestDetectSourceType:
    def test_postgres_variations(self):
        assert _detect_source_type("PostgreSQL") == "postgres"
        assert _detect_source_type("postgres") == "postgres"
        assert _detect_source_type("POSTGRESQL") == "postgres"
        assert _detect_source_type("My Postgres DB") == "postgres"

    def test_snowflake_variations(self):
        assert _detect_source_type("Snowflake") == "snowflake"
        assert _detect_source_type("snowflake") == "snowflake"
        assert _detect_source_type("SNOWFLAKE") == "snowflake"

    def test_mariadb_variations(self):
        assert _detect_source_type("MariaDB") == "mariadb"
        assert _detect_source_type("mariadb") == "mariadb"
        assert _detect_source_type("MARIADB") == "mariadb"

    def test_unknown_source(self):
        assert _detect_source_type("MySQL") == "unknown"
        assert _detect_source_type("Oracle") == "unknown"
        assert _detect_source_type("") == "unknown"


# ===========================================================================
# _is_keyword_present
# ===========================================================================


class TestIsKeywordPresent:
    def test_keyword_found(self):
        assert _is_keyword_present("DROP", "DROP TABLE users") is True

    def test_keyword_not_found(self):
        assert _is_keyword_present("DROP", "SELECT * FROM table") is False

    def test_keyword_as_substring_not_matched(self):
        assert _is_keyword_present("DROP", "BACKDROP") is False

    def test_keyword_case_sensitive(self):
        assert _is_keyword_present("DROP", "drop table") is False
        assert _is_keyword_present("DROP", "DROP TABLE") is True

    def test_keyword_at_boundaries(self):
        assert _is_keyword_present("SELECT", "SELECT col FROM t") is True
        assert _is_keyword_present("SELECT", "col FROM t SELECT") is True

    def test_multi_word_keyword(self):
        assert _is_keyword_present("INSERT INTO", "INSERT INTO users VALUES (1)") is True
        assert _is_keyword_present("INSERT INTO", "SELECT * FROM users") is False


# ===========================================================================
# _is_query_safe
# ===========================================================================


class TestIsQuerySafe:
    # ---- allowed queries ----

    def test_simple_select(self):
        ok, msg = _is_query_safe("SELECT * FROM users")
        assert ok is True
        assert msg == ""

    def test_select_with_where(self):
        ok, _ = _is_query_safe("SELECT id, name FROM users WHERE id = 1")
        assert ok is True

    def test_show_statement(self):
        ok, _ = _is_query_safe("SHOW TABLES")
        assert ok is True

    def test_describe_statement(self):
        ok, _ = _is_query_safe("DESCRIBE users")
        assert ok is True

    def test_explain_statement(self):
        ok, _ = _is_query_safe("EXPLAIN SELECT * FROM users")
        assert ok is True

    def test_with_cte(self):
        ok, _ = _is_query_safe("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert ok is True

    def test_desc_statement(self):
        ok, _ = _is_query_safe("DESC users")
        assert ok is True

    def test_table_statement_postgres(self):
        ok, _ = _is_query_safe("TABLE users")
        assert ok is True

    def test_multiple_read_only_statements(self):
        ok, _ = _is_query_safe("SELECT 1; SELECT 2")
        assert ok is True

    # ---- blocked queries ----

    def test_insert_blocked(self):
        ok, msg = _is_query_safe("INSERT INTO users VALUES (1, 'a')")
        assert ok is False
        assert "INSERT" in msg

    def test_update_blocked(self):
        ok, msg = _is_query_safe("UPDATE users SET name = 'x' WHERE id = 1")
        assert ok is False

    def test_delete_blocked(self):
        ok, msg = _is_query_safe("DELETE FROM users WHERE id = 1")
        assert ok is False

    def test_drop_blocked(self):
        ok, msg = _is_query_safe("DROP TABLE users")
        assert ok is False
        assert "DROP" in msg

    def test_create_blocked(self):
        ok, msg = _is_query_safe("CREATE TABLE t (id INT)")
        assert ok is False
        assert "CREATE" in msg

    def test_alter_blocked(self):
        ok, msg = _is_query_safe("ALTER TABLE users ADD COLUMN age INT")
        assert ok is False

    def test_truncate_blocked(self):
        ok, msg = _is_query_safe("TRUNCATE TABLE users")
        assert ok is False

    def test_grant_blocked(self):
        ok, msg = _is_query_safe("GRANT SELECT ON users TO user1")
        assert ok is False

    def test_exec_blocked(self):
        ok, msg = _is_query_safe("EXEC sp_help")
        assert ok is False

    def test_copy_blocked(self):
        ok, msg = _is_query_safe("COPY users TO '/tmp/out.csv'")
        assert ok is False

    def test_merge_blocked(self):
        ok, msg = _is_query_safe("MERGE INTO target USING source ON ...")
        assert ok is False

    def test_lock_blocked(self):
        ok, msg = _is_query_safe("LOCK TABLE users")
        assert ok is False

    def test_vacuum_blocked(self):
        ok, msg = _is_query_safe("VACUUM users")
        assert ok is False

    def test_commit_blocked(self):
        ok, msg = _is_query_safe("COMMIT")
        assert ok is False

    def test_begin_blocked(self):
        ok, msg = _is_query_safe("BEGIN")
        assert ok is False

    def test_stacked_dangerous_query(self):
        ok, msg = _is_query_safe("SELECT 1; DROP TABLE users")
        assert ok is False
        assert "Multi-statement" in msg or "DROP" in msg

    def test_select_with_insert_inside(self):
        ok, msg = _is_query_safe("SELECT * FROM (INSERT INTO t VALUES (1))")
        assert ok is False

    def test_xp_stored_procedures_blocked(self):
        ok, msg = _is_query_safe("SELECT XP_ cmdshell")
        assert ok is False

    def test_dynamic_sql_blocked(self):
        ok, msg = _is_query_safe("SELECT EXEC( 'DROP TABLE x' )")
        assert ok is False

    def test_unknown_start_blocked(self):
        ok, msg = _is_query_safe("FOOBAR something")
        assert ok is False
        assert "read-only" in msg.lower() or "Blocked" in msg

    def test_empty_query_blocked(self):
        ok, msg = _is_query_safe("")
        assert ok is False

    def test_case_insensitive_blocking(self):
        ok, _ = _is_query_safe("insert into users values (1)")
        assert ok is False

    def test_insert_overwrite_blocked(self):
        ok, msg = _is_query_safe("SELECT * FROM (INSERT OVERWRITE TABLE t SELECT 1)")
        assert ok is False

    def test_snowflake_clone_blocked(self):
        ok, _ = _is_query_safe("CLONE DATABASE db1")
        assert ok is False

    def test_replace_blocked(self):
        ok, _ = _is_query_safe("REPLACE INTO users VALUES (1)")
        assert ok is False

    def test_sp_executesql_blocked(self):
        ok, msg = _is_query_safe("SELECT SP_EXECUTESQL FROM t")
        assert ok is False
        assert "Dynamic SQL" in msg


# ===========================================================================
# _results_to_markdown
# ===========================================================================


class TestResultsToMarkdown:
    def test_no_columns(self):
        result = _results_to_markdown([], [])
        assert "No results" in result

    def test_no_rows(self):
        result = _results_to_markdown(["id", "name"], [])
        assert "no rows" in result.lower()
        assert "id" in result
        assert "name" in result

    def test_basic_table(self):
        result = _results_to_markdown(
            ["id", "name"],
            [(1, "Alice"), (2, "Bob")],
        )
        assert "| id | name |" in result
        assert "| 1 | Alice |" in result
        assert "| 2 | Bob |" in result
        assert "Total: 2 rows" in result

    def test_none_values(self):
        result = _results_to_markdown(["col"], [(None,)])
        assert "NULL" in result

    def test_pipe_escape(self):
        result = _results_to_markdown(["val"], [("a|b",)])
        assert "a\\|b" in result

    def test_truncate_long_cell(self):
        long_val = "x" * 200
        result = _results_to_markdown(["val"], [(long_val,)])
        assert "..." in result
        assert len(long_val) > 100  # original is long

    def test_truncate_rows(self):
        rows = [(i,) for i in range(150)]
        result = _results_to_markdown(["num"], rows)
        assert "Showing 100 of 150" in result

    def test_separator_row(self):
        result = _results_to_markdown(["a", "b"], [(1, 2)])
        lines = result.strip().split("\n")
        assert "---" in lines[1]


# ===========================================================================
# _execute_postgres_query
# ===========================================================================


class TestExecutePostgresQuery:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.utils.execute_query import _execute_postgres_query

        mock_client = MagicMock()
        mock_client.execute_query_raw.return_value = (["id", "name"], [(1, "Alice")])
        mock_client.get_connection_info.return_value = {
            "host": "localhost", "port": 5432, "database": "test", "user": "admin"
        }
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        mock_builder = MagicMock()
        mock_builder.get_client.return_value = mock_client

        with patch(
            "app.sources.client.postgres.postgres.PostgreSQLClientBuilder.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_builder,
        ):
            result = await _execute_postgres_query("SELECT 1", MagicMock(), "conn-1")

        assert result["ok"] is True
        assert result["columns"] == ["id", "name"]
        assert result["rows"] == [(1, "Alice")]

    @pytest.mark.asyncio
    async def test_empty_rows(self):
        from app.utils.execute_query import _execute_postgres_query

        mock_client = MagicMock()
        mock_client.execute_query_raw.return_value = (["id"], [])
        mock_client.get_connection_info.return_value = {
            "host": "localhost", "port": 5432, "database": "test", "user": "admin"
        }
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        mock_builder = MagicMock()
        mock_builder.get_client.return_value = mock_client

        with patch(
            "app.sources.client.postgres.postgres.PostgreSQLClientBuilder.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_builder,
        ):
            result = await _execute_postgres_query("SELECT 1 WHERE FALSE", MagicMock())

        assert result["ok"] is True
        assert result["rows"] == []

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.utils.execute_query import _execute_postgres_query

        with patch(
            "app.sources.client.postgres.postgres.PostgreSQLClientBuilder.build_from_services",
            new_callable=AsyncMock,
            side_effect=ConnectionError("connection refused"),
        ):
            result = await _execute_postgres_query("SELECT 1", MagicMock())

        assert result["ok"] is False
        assert "connection refused" in result["error"]


# ===========================================================================
# _execute_snowflake_query
# ===========================================================================


class TestExecuteSnowflakeQuery:
    @pytest.mark.asyncio
    async def test_pat_auth_success(self):
        from app.utils.execute_query import _execute_snowflake_query

        mock_sdk_client = MagicMock()
        mock_sdk_client.execute_query_raw.return_value = (["col1"], [(42,)])
        mock_sdk_client.__enter__ = MagicMock(return_value=mock_sdk_client)
        mock_sdk_client.__exit__ = MagicMock(return_value=False)

        mock_auth = MagicMock()
        mock_auth.authType.value = "PAT"
        mock_auth.patToken = "token-123"

        mock_config = MagicMock()
        mock_config.accountIdentifier = "acc-1"
        mock_config.warehouse = "WH_1"
        mock_config.auth = mock_auth

        with patch(
            "app.sources.client.snowflake.snowflake.SnowflakeClient._get_connector_config",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "app.sources.client.snowflake.snowflake.SnowflakeConnectorConfig.model_validate",
            return_value=mock_config,
        ), patch(
            "app.sources.client.snowflake.snowflake.SnowflakeSDKClient",
            return_value=mock_sdk_client,
        ):
            result = await _execute_snowflake_query("SELECT 1", MagicMock(), "conn-sf")

        assert result["ok"] is True
        assert result["rows"] == [(42,)]

    @pytest.mark.asyncio
    async def test_oauth_auth_success(self):
        from app.utils.execute_query import _execute_snowflake_query

        mock_sdk_client = MagicMock()
        mock_sdk_client.execute_query_raw.return_value = (["x"], [(1,)])
        mock_sdk_client.__enter__ = MagicMock(return_value=mock_sdk_client)
        mock_sdk_client.__exit__ = MagicMock(return_value=False)

        mock_auth = MagicMock()
        mock_auth.authType.value = "OAUTH"

        mock_credentials = MagicMock()
        mock_credentials.access_token = "oauth-token"

        mock_config = MagicMock()
        mock_config.accountIdentifier = "acc-1"
        mock_config.warehouse = "WH_1"
        mock_config.auth = mock_auth
        mock_config.credentials = mock_credentials

        with patch(
            "app.sources.client.snowflake.snowflake.SnowflakeClient._get_connector_config",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "app.sources.client.snowflake.snowflake.SnowflakeConnectorConfig.model_validate",
            return_value=mock_config,
        ), patch(
            "app.sources.client.snowflake.snowflake.SnowflakeSDKClient",
            return_value=mock_sdk_client,
        ):
            result = await _execute_snowflake_query("SELECT 1", MagicMock())

        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_pat_missing_token(self):
        from app.utils.execute_query import _execute_snowflake_query

        mock_auth = MagicMock()
        mock_auth.authType.value = "PAT"
        mock_auth.patToken = None

        mock_config = MagicMock()
        mock_config.accountIdentifier = "acc-1"
        mock_config.warehouse = "WH_1"
        mock_config.auth = mock_auth

        with patch(
            "app.sources.client.snowflake.snowflake.SnowflakeClient._get_connector_config",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "app.sources.client.snowflake.snowflake.SnowflakeConnectorConfig.model_validate",
            return_value=mock_config,
        ):
            result = await _execute_snowflake_query("SELECT 1", MagicMock())

        assert result["ok"] is False
        assert "PAT token" in result["error"]

    @pytest.mark.asyncio
    async def test_oauth_missing_access_token(self):
        from app.utils.execute_query import _execute_snowflake_query

        mock_auth = MagicMock()
        mock_auth.authType.value = "OAUTH"

        mock_credentials = MagicMock()
        mock_credentials.access_token = None

        mock_config = MagicMock()
        mock_config.accountIdentifier = "acc-1"
        mock_config.warehouse = "WH_1"
        mock_config.auth = mock_auth
        mock_config.credentials = mock_credentials

        with patch(
            "app.sources.client.snowflake.snowflake.SnowflakeClient._get_connector_config",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "app.sources.client.snowflake.snowflake.SnowflakeConnectorConfig.model_validate",
            return_value=mock_config,
        ):
            result = await _execute_snowflake_query("SELECT 1", MagicMock())

        assert result["ok"] is False
        assert "OAuth access token" in result["error"]

    @pytest.mark.asyncio
    async def test_unsupported_auth_type(self):
        from app.utils.execute_query import _execute_snowflake_query

        mock_auth = MagicMock()
        mock_auth.authType.value = "BASIC"

        mock_config = MagicMock()
        mock_config.accountIdentifier = "acc-1"
        mock_config.warehouse = "WH_1"
        mock_config.auth = mock_auth

        with patch(
            "app.sources.client.snowflake.snowflake.SnowflakeClient._get_connector_config",
            new_callable=AsyncMock,
            return_value={},
        ), patch(
            "app.sources.client.snowflake.snowflake.SnowflakeConnectorConfig.model_validate",
            return_value=mock_config,
        ):
            result = await _execute_snowflake_query("SELECT 1", MagicMock())

        assert result["ok"] is False
        assert "Unsupported" in result["error"]

    @pytest.mark.asyncio
    async def test_exception(self):
        from app.utils.execute_query import _execute_snowflake_query

        with patch(
            "app.sources.client.snowflake.snowflake.SnowflakeClient._get_connector_config",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            result = await _execute_snowflake_query("SELECT 1", MagicMock())

        assert result["ok"] is False
        assert "boom" in result["error"]


# ===========================================================================
# _execute_mariadb_query
# ===========================================================================


class TestExecuteMariadbQuery:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.utils.execute_query import _execute_mariadb_query

        mock_client = MagicMock()
        mock_client.execute_query_raw.return_value = (["col"], [(1,)])
        mock_client.get_connection_info.return_value = {
            "host": "localhost", "port": 3306, "database": "mydb", "user": "root"
        }
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        mock_builder = MagicMock()
        mock_builder.get_client.return_value = mock_client

        with patch(
            "app.sources.client.mariadb.mariadb.MariaDBClientBuilder.build_from_services",
            new_callable=AsyncMock,
            return_value=mock_builder,
        ):
            result = await _execute_mariadb_query("SELECT 1", MagicMock(), "conn-m")

        assert result["ok"] is True
        assert result["columns"] == ["col"]
        assert result["rows"] == [(1,)]

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.utils.execute_query import _execute_mariadb_query

        with patch(
            "app.sources.client.mariadb.mariadb.MariaDBClientBuilder.build_from_services",
            new_callable=AsyncMock,
            side_effect=ConnectionError("refused"),
        ):
            result = await _execute_mariadb_query("SELECT 1", MagicMock())

        assert result["ok"] is False
        assert "refused" in result["error"]


# ===========================================================================
# _execute_query_impl
# ===========================================================================


class TestExecuteQueryImpl:
    @pytest.mark.asyncio
    async def test_unsafe_query_rejected(self):
        from app.utils.execute_query import _execute_query_impl

        result = await _execute_query_impl(
            "DROP TABLE users", "PostgreSQL", MagicMock()
        )
        assert result["ok"] is False
        assert "Blocked" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_source(self):
        from app.utils.execute_query import _execute_query_impl

        result = await _execute_query_impl(
            "SELECT 1", "OracleDB", MagicMock()
        )
        assert result["ok"] is False
        assert "Unknown data source" in result["error"]

    @pytest.mark.asyncio
    async def test_postgres_dispatch(self):
        from app.utils.execute_query import _execute_query_impl

        with patch(
            "app.utils.execute_query._execute_postgres_query",
            new_callable=AsyncMock,
            return_value={"ok": True, "columns": ["id"], "rows": [(1,)]},
        ) as mock_pg:
            result = await _execute_query_impl(
                "SELECT 1", "PostgreSQL", MagicMock(), "conn-1"
            )

        mock_pg.assert_awaited_once()
        assert result["ok"] is True
        assert "markdown_result" in result
        assert result["row_count"] == 1

    @pytest.mark.asyncio
    async def test_snowflake_dispatch(self):
        from app.utils.execute_query import _execute_query_impl

        with patch(
            "app.utils.execute_query._execute_snowflake_query",
            new_callable=AsyncMock,
            return_value={"ok": True, "columns": ["x"], "rows": [(1,)]},
        ) as mock_sf:
            result = await _execute_query_impl(
                "SELECT 1", "Snowflake", MagicMock(), "conn-sf"
            )

        mock_sf.assert_awaited_once()
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_mariadb_dispatch(self):
        from app.utils.execute_query import _execute_query_impl

        with patch(
            "app.utils.execute_query._execute_mariadb_query",
            new_callable=AsyncMock,
            return_value={"ok": True, "columns": ["v"], "rows": [(99,)]},
        ) as mock_maria:
            result = await _execute_query_impl(
                "SELECT 1", "MariaDB", MagicMock(), "conn-m"
            )

        mock_maria.assert_awaited_once()
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_result_contains_raw_columns_and_rows(self):
        from app.utils.execute_query import _execute_query_impl

        with patch(
            "app.utils.execute_query._execute_postgres_query",
            new_callable=AsyncMock,
            return_value={"ok": True, "columns": ["a", "b"], "rows": [(1, 2)]},
        ):
            result = await _execute_query_impl(
                "SELECT a, b FROM t", "PostgreSQL", MagicMock(), "conn-1"
            )

        assert result["raw_columns"] == ["a", "b"]
        assert result["raw_rows"] == [(1, 2)]

    @pytest.mark.asyncio
    async def test_error_forwarded(self):
        from app.utils.execute_query import _execute_query_impl

        with patch(
            "app.utils.execute_query._execute_postgres_query",
            new_callable=AsyncMock,
            return_value={"ok": False, "error": "connection refused"},
        ):
            result = await _execute_query_impl(
                "SELECT 1", "PostgreSQL", MagicMock()
            )

        assert result["ok"] is False
        assert "connection refused" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_connector_id_normalized(self):
        from app.utils.execute_query import _execute_query_impl

        with patch(
            "app.utils.execute_query._execute_postgres_query",
            new_callable=AsyncMock,
            return_value={"ok": True, "columns": [], "rows": []},
        ) as mock_pg:
            await _execute_query_impl(
                "SELECT 1", "PostgreSQL", MagicMock(), "  "
            )

        call_kwargs = mock_pg.call_args
        assert call_kwargs[1].get("connector_instance_id") is None or \
            call_kwargs.kwargs.get("connector_instance_id") is None


# ===========================================================================
# create_execute_query_tool
# ===========================================================================


class TestCreateExecuteQueryTool:
    def test_creates_tool(self):
        from app.utils.execute_query import create_execute_query_tool

        tool = create_execute_query_tool(config_service=MagicMock())
        assert tool.name == "execute_sql_query"

    @pytest.mark.asyncio
    async def test_tool_invocation_success(self):
        from app.utils.execute_query import create_execute_query_tool

        with patch(
            "app.utils.execute_query._execute_query_impl",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "markdown_result": "| x |\n|---|\n| 1 |",
                "row_count": 1,
                "column_count": 1,
                "raw_columns": ["x"],
                "raw_rows": [(1,)],
            },
        ):
            tool = create_execute_query_tool(config_service=MagicMock())
            result = await tool.ainvoke({
                "query": "SELECT 1",
                "source_name": "PostgreSQL",
                "connector_id": "conn-1",
                "reason": "test",
            })

        assert result["ok"] is True
        assert "raw_columns" not in result
        assert "raw_rows" not in result

    @pytest.mark.asyncio
    async def test_tool_invocation_error(self):
        from app.utils.execute_query import create_execute_query_tool

        with patch(
            "app.utils.execute_query._execute_query_impl",
            new_callable=AsyncMock,
            return_value={"ok": False, "error": "blocked"},
        ):
            tool = create_execute_query_tool(config_service=MagicMock())
            result = await tool.ainvoke({
                "query": "DROP TABLE x",
                "source_name": "PostgreSQL",
                "connector_id": "conn-1",
            })

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_tool_invocation_exception(self):
        from app.utils.execute_query import create_execute_query_tool

        with patch(
            "app.utils.execute_query._execute_query_impl",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected"),
        ):
            tool = create_execute_query_tool(config_service=MagicMock())
            result = await tool.ainvoke({
                "query": "SELECT 1",
                "source_name": "PostgreSQL",
                "connector_id": "conn-1",
            })

        assert result["ok"] is False
        assert "unexpected" in result["error"]

    @pytest.mark.asyncio
    async def test_csv_export_registered_when_prerequisites_met(self):
        from app.utils.execute_query import create_execute_query_tool

        mock_blob_store = AsyncMock()
        mock_blob_store.save_conversation_file_to_storage = AsyncMock(
            return_value={"url": "https://example.com/file.csv"}
        )

        with patch(
            "app.utils.execute_query._execute_query_impl",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "markdown_result": "| x |\n|---|\n| 1 |",
                "row_count": 1,
                "column_count": 1,
                "raw_columns": ["x"],
                "raw_rows": [(1,)],
            },
        ), patch(
            "app.utils.execute_query.register_task",
        ) as mock_register:
            tool = create_execute_query_tool(
                config_service=MagicMock(),
                org_id="org-1",
                conversation_id="conv-1",
                blob_store=mock_blob_store,
            )
            result = await tool.ainvoke({
                "query": "SELECT 1",
                "source_name": "PostgreSQL",
                "connector_id": "conn-1",
            })

        assert result["ok"] is True
        mock_register.assert_called_once()
        assert mock_register.call_args[0][0] == "conv-1"

    @pytest.mark.asyncio
    async def test_csv_export_not_registered_without_conversation_id(self):
        from app.utils.execute_query import create_execute_query_tool

        with patch(
            "app.utils.execute_query._execute_query_impl",
            new_callable=AsyncMock,
            return_value={
                "ok": True,
                "markdown_result": "| x |",
                "row_count": 1,
                "column_count": 1,
                "raw_columns": ["x"],
                "raw_rows": [(1,)],
            },
        ), patch(
            "app.utils.execute_query.register_task",
        ) as mock_register:
            tool = create_execute_query_tool(
                config_service=MagicMock(),
                org_id="org-1",
                conversation_id=None,
            )
            await tool.ainvoke({
                "query": "SELECT 1",
                "source_name": "PostgreSQL",
                "connector_id": "conn-1",
            })

        mock_register.assert_not_called()


# ===========================================================================
# has_sql_connector_configured
# ===========================================================================


class TestHasSqlConnectorConfigured:
    @pytest.mark.asyncio
    async def test_returns_true_when_configured_sql_connector_exists(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            return_value=[
                {"type": "POSTGRESQL", "isConfigured": True},
                {"type": "GOOGLE_DRIVE", "isConfigured": True},
            ]
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is True

    @pytest.mark.asyncio
    async def test_returns_true_for_snowflake(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            return_value=[{"type": "SNOWFLAKE", "isConfigured": True}]
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is True

    @pytest.mark.asyncio
    async def test_returns_true_for_mariadb(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            return_value=[{"type": "MARIADB", "isConfigured": True}]
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is True

    @pytest.mark.asyncio
    async def test_type_matching_is_case_insensitive(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            return_value=[{"type": "postgresql", "isConfigured": True}]
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is True

    @pytest.mark.asyncio
    async def test_returns_false_when_sql_connector_not_configured(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            return_value=[{"type": "POSTGRESQL", "isConfigured": False}]
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_sql_connector_present(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            return_value=[{"type": "SLACK", "isConfigured": True}]
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is False

    @pytest.mark.asyncio
    async def test_returns_false_on_empty_list(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(return_value=[])
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is False

    @pytest.mark.asyncio
    async def test_returns_false_on_none(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(return_value=None)
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is False

    @pytest.mark.asyncio
    async def test_swallows_exception_and_returns_false(self):
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is False

    @pytest.mark.asyncio
    async def test_forwards_user_and_org_to_graph_provider(self):
        from app.config.constants.arangodb import CollectionNames
        from app.connectors.core.registry.connector_builder import ConnectorScope

        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(return_value=[])
        await has_sql_connector_configured(graph_provider, "user-42", "org-7")

        graph_provider.get_user_connector_instances.assert_awaited_once_with(
            collection=CollectionNames.APPS.value,
            user_id="user-42",
            org_id="org-7",
            team_scope=ConnectorScope.TEAM.value,
            personal_scope=ConnectorScope.PERSONAL.value,
        )

    @pytest.mark.asyncio
    async def test_missing_is_configured_field_is_treated_as_not_configured(self):
        """A SQL type with no isConfigured field should not count (default falsy)."""
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            return_value=[{"type": "POSTGRESQL"}]
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is False

    @pytest.mark.asyncio
    async def test_is_configured_uses_truthy_check(self):
        """isConfigured uses truthy semantics for consistency with the rest of the codebase
        (e.g. connector_registry.py uses .get('isConfigured', False) in a truthy context).
        Truthy non-bool values (e.g. a string 'true' from a JSON path) should count."""
        graph_provider = MagicMock()
        graph_provider.get_user_connector_instances = AsyncMock(
            return_value=[{"type": "SNOWFLAKE", "isConfigured": "true"}]
        )
        assert await has_sql_connector_configured(graph_provider, "u1", "o1") is True

    @pytest.mark.asyncio
    async def test_falsy_is_configured_values_are_not_configured(self):
        """Empty string, 0, None, and False should all be treated as not configured."""
        for falsy in ("", 0, None, False):
            graph_provider = MagicMock()
            graph_provider.get_user_connector_instances = AsyncMock(
                return_value=[{"type": "POSTGRESQL", "isConfigured": falsy}]
            )
            assert (
                await has_sql_connector_configured(graph_provider, "u1", "o1") is False
            ), f"expected False for isConfigured={falsy!r}"


# ===========================================================================
# agent_knowledge_has_sql_connector
# ===========================================================================


class TestAgentKnowledgeHasSqlConnector:
    def test_none(self):
        assert agent_knowledge_has_sql_connector(None) is False

    def test_empty_list(self):
        assert agent_knowledge_has_sql_connector([]) is False

    def test_postgresql_type(self):
        assert agent_knowledge_has_sql_connector([{"type": "POSTGRESQL"}]) is True

    def test_snowflake_type(self):
        assert agent_knowledge_has_sql_connector([{"type": "SNOWFLAKE"}]) is True

    def test_mariadb_type(self):
        assert agent_knowledge_has_sql_connector([{"type": "MARIADB"}]) is True

    def test_case_insensitive(self):
        assert agent_knowledge_has_sql_connector([{"type": "postgresql"}]) is True
        assert agent_knowledge_has_sql_connector([{"type": "Snowflake"}]) is True

    def test_non_sql_connector(self):
        assert agent_knowledge_has_sql_connector(
            [{"type": "GOOGLE_DRIVE"}, {"type": "SLACK"}]
        ) is False

    def test_mixed_list_returns_true_when_any_sql(self):
        assert agent_knowledge_has_sql_connector(
            [{"type": "GOOGLE_DRIVE"}, {"type": "POSTGRESQL"}]
        ) is True

    def test_non_dict_items_ignored(self):
        assert agent_knowledge_has_sql_connector(
            ["POSTGRESQL", None, 42, {"type": "POSTGRESQL"}]
        ) is True

    def test_dict_without_type_key(self):
        assert agent_knowledge_has_sql_connector([{"name": "x"}]) is False
