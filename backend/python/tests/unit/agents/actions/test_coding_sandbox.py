"""Tests for app.agents.actions.coding_sandbox.coding_sandbox."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox.models import ArtifactOutput, ExecutionResult


def _make_state(**overrides):
    """Build a minimal ChatState-like dict for toolset tests."""
    state = {
        "conversation_id": "conv-123",
        "org_id": "org-456",
        "blob_store": None,
        "config_service": MagicMock(),
        "graph_provider": MagicMock(),
    }
    state.update(overrides)
    return state


class TestCodingSandboxInit:
    def test_imports(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox
        assert CodingSandbox is not None


class TestExecutePython:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        mock_result = ExecutionResult(
            success=True,
            stdout="Hello World\n",
            exit_code=0,
            execution_time_ms=150,
            artifacts=[],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_python("print('Hello World')")
            assert success is True
            data = json.loads(result_json)
            assert data["message"] == "Code executed successfully"
            assert data["stdout"] == "Hello World\n"
            assert data["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        mock_result = ExecutionResult(
            success=False,
            stderr="NameError: name 'x' is not defined",
            exit_code=1,
            execution_time_ms=50,
            error="Script failed",
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_python("print(x)")
            assert success is False
            data = json.loads(result_json)
            assert data["message"] == "Code execution failed"
            assert "NameError" in data["stderr"]

    @pytest.mark.asyncio
    async def test_with_artifacts(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="chart.png",
            file_path="/tmp/chart.png",
            mime_type="image/png",
            size_bytes=4096,
        )
        mock_result = ExecutionResult(
            success=True,
            stdout="",
            exit_code=0,
            execution_time_ms=500,
            artifacts=[artifact],
        )

        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch("app.agents.actions.coding_sandbox.coding_sandbox.register_task"):
                success, result_json = await sandbox.execute_python(
                    "import matplotlib; ...",
                    requirements=["matplotlib"],
                )
                assert success is True
                data = json.loads(result_json)
                assert len(data["artifacts"]) == 1
                assert data["artifacts"][0]["fileName"] == "chart.png"
                assert data["artifacts"][0]["mimeType"] == "image/png"

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_get.side_effect = RuntimeError("executor unavailable")

            success, result_json = await sandbox.execute_python("print(1)")
            assert success is False
            data = json.loads(result_json)
            assert "executor unavailable" in data["error"]


class TestExecuteTypescript:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        mock_result = ExecutionResult(
            success=True,
            stdout="hello ts\n",
            exit_code=0,
            execution_time_ms=200,
            artifacts=[],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_typescript("console.log('hello ts')")
            assert success is True
            data = json.loads(result_json)
            assert data["stdout"] == "hello ts\n"

    @pytest.mark.asyncio
    async def test_failure(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        mock_result = ExecutionResult(
            success=False,
            stderr="TypeError: x is not defined",
            exit_code=1,
            execution_time_ms=50,
            error="Script failed",
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            success, result_json = await sandbox.execute_typescript("console.log(x)")
            assert success is False
            data = json.loads(result_json)
            assert data["message"] == "Code execution failed"
            assert "TypeError" in data["stderr"]

    @pytest.mark.asyncio
    async def test_exception_handling(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_get.side_effect = RuntimeError("executor unavailable")

            success, result_json = await sandbox.execute_typescript("console.log(1)")
            assert success is False
            data = json.loads(result_json)
            assert "executor unavailable" in data["error"]

    @pytest.mark.asyncio
    async def test_with_artifacts(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="report.html",
            file_path="/tmp/report.html",
            mime_type="text/html",
            size_bytes=2048,
        )
        mock_result = ExecutionResult(
            success=True,
            stdout="",
            exit_code=0,
            execution_time_ms=300,
            artifacts=[artifact],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch("app.agents.actions.coding_sandbox.coding_sandbox.register_task"):
                success, result_json = await sandbox.execute_typescript(
                    "import fs from 'fs'; ...",
                    packages=["fs-extra"],
                )
                assert success is True
                data = json.loads(result_json)
                assert len(data["artifacts"]) == 1
                assert data["artifacts"][0]["fileName"] == "report.html"


class TestSourceToolTracking:
    """Verify that _schedule_artifact_upload passes the correct source_tool."""

    @pytest.mark.asyncio
    async def test_python_uses_python_source_tool(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="out.png", file_path="/tmp/out.png",
            mime_type="image/png", size_bytes=100,
        )
        mock_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=100, artifacts=[artifact],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch.object(sandbox, "_schedule_artifact_upload") as mock_schedule:
                await sandbox.execute_python("print(1)")
                mock_schedule.assert_called_once()
                call_kwargs = mock_schedule.call_args
                # Default source_tool for python should not pass explicit kwarg
                # (defaults to "coding_sandbox.execute_python")
                assert call_kwargs.kwargs.get("source_tool", "coding_sandbox.execute_python") == "coding_sandbox.execute_python"

    @pytest.mark.asyncio
    async def test_typescript_uses_typescript_source_tool(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="out.html", file_path="/tmp/out.html",
            mime_type="text/html", size_bytes=100,
        )
        mock_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=100, artifacts=[artifact],
        )
        with patch("app.agents.actions.coding_sandbox.coding_sandbox.get_executor") as mock_get:
            mock_executor = AsyncMock()
            mock_executor.execute = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_executor

            with patch.object(sandbox, "_schedule_artifact_upload") as mock_schedule:
                await sandbox.execute_typescript("console.log(1)")
                mock_schedule.assert_called_once()
                call_kwargs = mock_schedule.call_args
                assert call_kwargs.kwargs.get("source_tool") == "coding_sandbox.execute_typescript"


class TestUploadArtifactsMethod:
    @pytest.mark.asyncio
    async def test_no_artifacts(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state()
        sandbox = CodingSandbox(state)

        result = ExecutionResult(success=True, artifacts=[])
        uploaded = await sandbox._upload_artifacts(result)
        assert uploaded == []

    @pytest.mark.asyncio
    async def test_no_conversation_id(self):
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state(conversation_id=None)
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="test.csv",
            file_path="/tmp/test.csv",
            mime_type="text/csv",
            size_bytes=10,
        )
        result = ExecutionResult(success=True, artifacts=[artifact])
        uploaded = await sandbox._upload_artifacts(result)
        assert uploaded == []
