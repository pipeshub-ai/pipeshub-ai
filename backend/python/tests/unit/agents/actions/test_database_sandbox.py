"""Tests for app.agents.actions.database_sandbox.database_sandbox."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox.models import ExecutionResult


def _make_state(**overrides):
    """Build a minimal ChatState-like dict for toolset tests."""
    state = {
        "conversation_id": "conv-db-001",
        "org_id": "org-db-001",
        "blob_store": None,
        "config_service": MagicMock(),
        "graph_provider": MagicMock(),
    }
    state.update(overrides)
    return state


class TestDatabaseSandboxInit:
    def test_imports(self):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox
        assert DatabaseSandbox is not None


class TestExecuteSQLite:
    @pytest.mark.asyncio
    async def test_success_with_results(self):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        state = _make_state()
        sandbox = DatabaseSandbox(state)

        mock_result = ExecutionResult(
            success=True,
            stdout="id,name\n1,alice\n2,bob\n",
            exit_code=0,
            execution_time_ms=30,
        )
        with patch("app.agents.actions.database_sandbox.database_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch("app.agents.actions.database_sandbox.database_sandbox.register_task"):
                success, result_json = await sandbox.execute_sqlite("SELECT * FROM users;")
                assert success is True
                data = json.loads(result_json)
                assert data["row_count"] == 2
                assert len(data["data"]) == 2
                assert data["data"][0]["name"] == "alice"

    @pytest.mark.asyncio
    async def test_with_setup_sql(self):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        state = _make_state()
        sandbox = DatabaseSandbox(state)

        mock_result = ExecutionResult(
            success=True,
            stdout="count\n3\n",
            exit_code=0,
            execution_time_ms=20,
        )
        with patch("app.agents.actions.database_sandbox.database_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch("app.agents.actions.database_sandbox.database_sandbox.register_task"):
                success, result_json = await sandbox.execute_sqlite(
                    "SELECT count(*) as count FROM t;",
                    setup_sql="CREATE TABLE t (id INT); INSERT INTO t VALUES(1),(2),(3);",
                )
                assert success is True
                call_args = mock_executor.execute.call_args
                code_arg = call_args.kwargs.get("code", call_args.args[0] if call_args.args else "")
                assert "CREATE TABLE" in code_arg
                assert "SELECT count" in code_arg

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        state = _make_state()
        sandbox = DatabaseSandbox(state)

        mock_result = ExecutionResult(
            success=False,
            stderr="Error: near 'SELEC': syntax error",
            exit_code=1,
            execution_time_ms=5,
        )
        with patch("app.agents.actions.database_sandbox.database_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_sqlite("SELEC * FROM t;")
            assert success is False
            data = json.loads(result_json)
            assert "syntax error" in data.get("stderr", "")

    @pytest.mark.asyncio
    async def test_empty_result(self):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        state = _make_state()
        sandbox = DatabaseSandbox(state)

        mock_result = ExecutionResult(
            success=True,
            stdout="",
            exit_code=0,
            execution_time_ms=10,
        )
        with patch("app.agents.actions.database_sandbox.database_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_sqlite("DELETE FROM t;")
            assert success is True
            data = json.loads(result_json)
            assert data["row_count"] == 0

    @pytest.mark.asyncio
    async def test_exception(self):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        state = _make_state()
        sandbox = DatabaseSandbox(state)

        with patch("app.agents.actions.database_sandbox.database_sandbox.get_executor") as mock_get:
            mock_get.side_effect = RuntimeError("executor crashed")

            success, result_json = await sandbox.execute_sqlite("SELECT 1;")
            assert success is False
            data = json.loads(result_json)
            assert "executor crashed" in data["error"]


class TestExecutePostgreSQL:
    @pytest.mark.asyncio
    async def test_no_url_configured(self, monkeypatch):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        monkeypatch.delenv("SANDBOX_PG_URL", raising=False)
        state = _make_state()
        sandbox = DatabaseSandbox(state)

        success, result_json = await sandbox.execute_postgresql("SELECT 1;")
        assert success is False
        data = json.loads(result_json)
        assert "not configured" in data["error"]

    @pytest.mark.asyncio
    async def test_success(self, monkeypatch):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        monkeypatch.setenv("SANDBOX_PG_URL", "postgresql://localhost/test")
        state = _make_state()
        sandbox = DatabaseSandbox(state)

        mock_result = ExecutionResult(
            success=True,
            stdout="count\n42\n",
            exit_code=0,
            execution_time_ms=50,
        )
        with patch("app.agents.actions.database_sandbox.database_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch("app.agents.actions.database_sandbox.database_sandbox.register_task"):
                success, result_json = await sandbox.execute_postgresql("SELECT count(*) as count FROM t;")
                assert success is True
                data = json.loads(result_json)
                assert data["row_count"] == 1
                assert data["data"][0]["count"] == "42"


class TestExecutePostgreSQLFailure:
    @pytest.mark.asyncio
    async def test_executor_failure(self, monkeypatch):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        monkeypatch.setenv("SANDBOX_PG_URL", "postgresql://localhost/test")
        state = _make_state()
        sandbox = DatabaseSandbox(state)

        mock_result = ExecutionResult(
            success=False,
            stderr="ERROR: relation \"nonexistent\" does not exist",
            exit_code=2,
            execution_time_ms=30,
            error="Query failed",
        )
        with patch("app.agents.actions.database_sandbox.database_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_postgresql("SELECT * FROM nonexistent;")
            assert success is False
            data = json.loads(result_json)
            assert "relation" in data.get("stderr", "")

    @pytest.mark.asyncio
    async def test_executor_exception(self, monkeypatch):
        from app.agents.actions.database_sandbox.database_sandbox import DatabaseSandbox

        monkeypatch.setenv("SANDBOX_PG_URL", "postgresql://localhost/test")
        state = _make_state()
        sandbox = DatabaseSandbox(state)

        with patch("app.agents.actions.database_sandbox.database_sandbox.get_executor") as mock_get:
            mock_get.side_effect = RuntimeError("executor crashed")

            success, result_json = await sandbox.execute_postgresql("SELECT 1;")
            assert success is False
            data = json.loads(result_json)
            assert "executor crashed" in data["error"]


class TestParseCSVOutput:
    def test_parse_csv(self):
        from app.agents.actions.database_sandbox.database_sandbox import _parse_csv_output

        csv_str = "id,name,age\n1,alice,30\n2,bob,25\n"
        rows = _parse_csv_output(csv_str)
        assert len(rows) == 2
        assert rows[0]["id"] == "1"
        assert rows[0]["name"] == "alice"
        assert rows[1]["age"] == "25"

    def test_empty_input(self):
        from app.agents.actions.database_sandbox.database_sandbox import _parse_csv_output
        assert _parse_csv_output("") == []
        assert _parse_csv_output("   ") == []

    def test_header_only(self):
        from app.agents.actions.database_sandbox.database_sandbox import _parse_csv_output
        rows = _parse_csv_output("id,name\n")
        assert rows == []

    def test_quoted_fields_with_commas(self):
        from app.agents.actions.database_sandbox.database_sandbox import _parse_csv_output
        csv_str = 'name,description\nalice,"likes cats, dogs"\nbob,"no hobbies"\n'
        rows = _parse_csv_output(csv_str)
        assert len(rows) == 2
        assert rows[0]["description"] == "likes cats, dogs"

    def test_malformed_returns_empty(self):
        from app.agents.actions.database_sandbox.database_sandbox import _parse_csv_output
        # No header line at all, just raw data — DictReader can still handle this
        # but if truly malformed (e.g. binary), it should not crash
        result = _parse_csv_output("id,name\n")
        assert result == []


class TestRowsToCsvBytes:
    def test_basic(self):
        from app.agents.actions.database_sandbox.database_sandbox import _rows_to_csv_bytes
        result = _rows_to_csv_bytes(["a", "b"], [(1, 2), (3, 4)])
        text = result.decode("utf-8")
        assert "a,b" in text
        assert "1,2" in text
        assert "3,4" in text

    def test_empty(self):
        from app.agents.actions.database_sandbox.database_sandbox import _rows_to_csv_bytes
        result = _rows_to_csv_bytes(["x"], [])
        assert b"x" in result


class TestScheduleCSVExport:
    """Cover the ``_schedule_csv_export`` background-task branches."""

    @pytest.mark.asyncio
    async def test_success_uploads_and_creates_record(self):
        import asyncio

        from app.agents.actions.database_sandbox import database_sandbox as mod

        mock_blob = MagicMock()
        mock_blob.save_conversation_file_to_storage = AsyncMock(return_value={
            "documentId": "doc-db-1",
            "fileName": "sqlite_result_xxx.csv",
            "signedUrl": "https://blob.example/x",
        })
        state = _make_state(
            blob_store=mock_blob,
            user_id="user-1",
        )
        sandbox = mod.DatabaseSandbox(state)

        captured_tasks: list[asyncio.Task] = []

        with patch.object(
            mod, "create_artifact_record",
            AsyncMock(return_value="record-123"),
        ), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            sandbox._schedule_csv_export(
                [{"id": "1", "name": "alice"}, {"id": "2", "name": "bob"}],
                "sqlite_result",
            )
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is not None
        assert result["type"] == "artifacts"
        assert len(result["artifacts"]) == 1
        entry = result["artifacts"][0]
        assert entry["mimeType"] == "text/csv"
        assert entry["recordId"] == "record-123"
        assert entry["sizeBytes"] > 0

        mock_blob.save_conversation_file_to_storage.assert_awaited_once()
        call = mock_blob.save_conversation_file_to_storage.await_args
        assert call.kwargs["org_id"] == "org-db-001"
        assert call.kwargs["conversation_id"] == "conv-db-001"
        assert call.kwargs["file_name"].startswith("sqlite_result_")
        assert call.kwargs["file_name"].endswith(".csv")

    @pytest.mark.asyncio
    async def test_record_creation_failure_still_returns_upload(self):
        import asyncio

        from app.agents.actions.database_sandbox import database_sandbox as mod

        mock_blob = MagicMock()
        mock_blob.save_conversation_file_to_storage = AsyncMock(return_value={
            "documentId": "doc-db-2",
            "fileName": "pg_result_yyy.csv",
            "signedUrl": "https://blob.example/y",
        })
        state = _make_state(
            blob_store=mock_blob,
            user_id="user-1",
        )
        sandbox = mod.DatabaseSandbox(state)

        captured_tasks: list[asyncio.Task] = []

        with patch.object(
            mod, "create_artifact_record",
            AsyncMock(side_effect=RuntimeError("graph down")),
        ), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            sandbox._schedule_csv_export(
                [{"id": "1"}], "pg_result",
            )
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is not None
        assert len(result["artifacts"]) == 1
        entry = result["artifacts"][0]
        assert "recordId" not in entry
        assert entry["mimeType"] == "text/csv"

    @pytest.mark.asyncio
    async def test_blob_save_raises_resolves_to_none(self):
        import asyncio

        from app.agents.actions.database_sandbox import database_sandbox as mod

        mock_blob = MagicMock()
        mock_blob.save_conversation_file_to_storage = AsyncMock(
            side_effect=RuntimeError("blob down"),
        )
        state = _make_state(blob_store=mock_blob, user_id="user-1")
        sandbox = mod.DatabaseSandbox(state)

        captured_tasks: list[asyncio.Task] = []

        with patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            sandbox._schedule_csv_export([{"id": "1"}], "sqlite_result")
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is None

    def test_no_rows_is_noop(self):
        from app.agents.actions.database_sandbox import database_sandbox as mod

        sandbox = mod.DatabaseSandbox(_make_state())

        with patch.object(mod, "register_task") as mock_register:
            sandbox._schedule_csv_export([], "sqlite_result")

        mock_register.assert_not_called()

    def test_no_conversation_is_noop(self):
        from app.agents.actions.database_sandbox import database_sandbox as mod

        sandbox = mod.DatabaseSandbox(_make_state(conversation_id=None))

        with patch.object(mod, "register_task") as mock_register:
            sandbox._schedule_csv_export([{"id": "1"}], "sqlite_result")

        mock_register.assert_not_called()


class TestSqliteDisplayLimit:
    """Ensure the 100-row display cap is applied and signalled to the LLM."""

    @pytest.mark.asyncio
    async def test_sqlite_truncates_over_100_rows(self):
        from app.agents.actions.database_sandbox import database_sandbox as mod

        state = _make_state()
        sandbox = mod.DatabaseSandbox(state)

        rows = "id\n" + "\n".join(str(i) for i in range(150)) + "\n"
        mock_result = ExecutionResult(
            success=True, stdout=rows, exit_code=0, execution_time_ms=9,
        )
        with patch.object(mod, "get_executor") as mock_get, patch.object(
            sandbox, "_schedule_csv_export",
        ):
            executor = AsyncMock()
            executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = executor

            success, result_json = await sandbox.execute_sqlite("SELECT id FROM t;")

        assert success is True
        data = json.loads(result_json)
        assert data["row_count"] == 150
        assert data["truncated"] is True
        assert data["displayed_row_count"] == 100
        assert len(data["data"]) == 100


class TestPostgresqlDisplayLimit:
    @pytest.mark.asyncio
    async def test_postgresql_truncates_over_100_rows(self, monkeypatch):
        from app.agents.actions.database_sandbox import database_sandbox as mod

        monkeypatch.setenv("SANDBOX_PG_URL", "postgresql://localhost/test")
        state = _make_state()
        sandbox = mod.DatabaseSandbox(state)

        rows = "id\n" + "\n".join(str(i) for i in range(150)) + "\n"
        mock_result = ExecutionResult(
            success=True, stdout=rows, exit_code=0, execution_time_ms=11,
        )
        with patch.object(mod, "get_executor") as mock_get, patch.object(
            sandbox, "_schedule_csv_export",
        ):
            executor = AsyncMock()
            executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = executor

            success, result_json = await sandbox.execute_postgresql(
                "SELECT id FROM t;",
            )

        assert success is True
        data = json.loads(result_json)
        assert data["row_count"] == 150
        assert data["truncated"] is True
        assert data["displayed_row_count"] == 100
        assert len(data["data"]) == 100


class TestParseCSVOutputHandlesError:
    def test_csv_error_returns_empty(self):
        import csv as _csv

        from app.agents.actions.database_sandbox import database_sandbox as mod

        class _BadReader:
            def __init__(self, *args, **kwargs) -> None:
                raise _csv.Error("invalid csv")

        with patch.object(mod.csv, "DictReader", _BadReader):
            result = mod._parse_csv_output("id,name\n1,alice\n")

        assert result == []


class TestTruncateHelper:
    def test_truncate_helper_overflow(self):
        from app.agents.actions.database_sandbox.database_sandbox import _truncate

        text = "x" * 50
        out = _truncate(text, 10)
        assert out.startswith("x" * 10)
        assert "truncated" in out
        assert "50 total chars" in out

    def test_truncate_helper_under_limit(self):
        from app.agents.actions.database_sandbox.database_sandbox import _truncate

        assert _truncate("short", 50) == "short"
