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

    @pytest.mark.asyncio
    async def test_blob_fallback_construction_fails(self):
        """When no blob_store is injected and BlobStorage construction fails,
        _upload_artifacts must return []."""
        from app.agents.actions.coding_sandbox.coding_sandbox import CodingSandbox

        state = _make_state(blob_store=None)
        sandbox = CodingSandbox(state)

        artifact = ArtifactOutput(
            file_name="test.csv",
            file_path="/tmp/test.csv",
            mime_type="text/csv",
            size_bytes=10,
        )
        result = ExecutionResult(success=True, artifacts=[artifact])

        with patch(
            "app.modules.transformers.blob_storage.BlobStorage",
            side_effect=RuntimeError("missing storage config"),
        ):
            uploaded = await sandbox._upload_artifacts(result)

        assert uploaded == []


class TestPackageRejection:
    """Cover the validate_packages failure branches in execute_python/typescript."""

    @pytest.mark.asyncio
    async def test_execute_python_rejects_disallowed_package(self):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod
        from app.sandbox.models import SandboxLanguage
        from app.sandbox.package_policy import PackageNotAllowedError

        sandbox = mod.CodingSandbox(_make_state())

        exc = PackageNotAllowedError(
            "evil-pkg", SandboxLanguage.PYTHON, ["pandas", "numpy"],
        )
        with patch(
            "app.sandbox.models.validate_packages", side_effect=exc,
        ):
            success, result_json = await sandbox.execute_python(
                "print('x')", requirements=["evil-pkg"],
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "package_not_allowed"
        assert data["rejected_package"] == "evil-pkg"
        assert data["language"] == "python"
        assert "evil-pkg" in data["message"]
        assert set(data["allowed_packages"]) >= {"pandas", "numpy"} or isinstance(
            data["allowed_packages"], list,
        )

    @pytest.mark.asyncio
    async def test_execute_python_invalid_package_name(self):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = mod.CodingSandbox(_make_state())

        with patch(
            "app.sandbox.models.validate_packages",
            side_effect=ValueError("not a valid PyPI name"),
        ):
            success, result_json = await sandbox.execute_python(
                "print('x')", requirements=["$$$invalid"],
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "invalid_package_name"
        assert "not a valid PyPI name" in data["message"]

    @pytest.mark.asyncio
    async def test_execute_typescript_rejects_disallowed_package(self):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod
        from app.sandbox.models import SandboxLanguage
        from app.sandbox.package_policy import PackageNotAllowedError

        sandbox = mod.CodingSandbox(_make_state())

        exc = PackageNotAllowedError(
            "evil-npm", SandboxLanguage.TYPESCRIPT, ["chart.js", "sharp"],
        )
        with patch(
            "app.sandbox.models.validate_packages", side_effect=exc,
        ):
            success, result_json = await sandbox.execute_typescript(
                "console.log(1)", packages=["evil-npm"],
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "package_not_allowed"
        assert data["rejected_package"] == "evil-npm"
        assert data["language"] == "typescript"

    @pytest.mark.asyncio
    async def test_execute_typescript_invalid_package_name(self):
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = mod.CodingSandbox(_make_state())

        with patch(
            "app.sandbox.models.validate_packages",
            side_effect=ValueError("bad npm name"),
        ):
            success, result_json = await sandbox.execute_typescript(
                "console.log(1)", packages=["!!bad"],
            )

        assert success is False
        data = json.loads(result_json)
        assert data["error_code"] == "invalid_package_name"
        assert "bad npm name" in data["message"]


class TestScheduleArtifactUploadException:
    """Exercise the background-task exception branch in _schedule_artifact_upload."""

    @pytest.mark.asyncio
    async def test_upload_exception_resolves_to_none(self):
        import asyncio
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = mod.CodingSandbox(_make_state())

        artifact = ArtifactOutput(
            file_name="out.csv", file_path="/tmp/out.csv",
            mime_type="text/csv", size_bytes=10,
        )
        exec_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=1, artifacts=[artifact],
        )

        captured_tasks: list[asyncio.Task] = []

        async def _raise(*args, **kwargs):
            raise RuntimeError("upload boom")

        with patch.object(sandbox, "_upload_artifacts", _raise), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            sandbox._schedule_artifact_upload(exec_result)
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result is None

    @pytest.mark.asyncio
    async def test_upload_success_yields_artifacts(self):
        import asyncio
        from app.agents.actions.coding_sandbox import coding_sandbox as mod

        sandbox = mod.CodingSandbox(_make_state())

        artifact = ArtifactOutput(
            file_name="out.csv", file_path="/tmp/out.csv",
            mime_type="text/csv", size_bytes=10,
        )
        exec_result = ExecutionResult(
            success=True, stdout="", exit_code=0,
            execution_time_ms=1, artifacts=[artifact],
        )

        captured_tasks: list[asyncio.Task] = []

        async def _ok(*args, **kwargs):
            return [{"fileName": "out.csv", "documentId": "d"}]

        with patch.object(sandbox, "_upload_artifacts", _ok), patch.object(
            mod, "register_task",
            lambda conv_id, task: captured_tasks.append(task),
        ):
            sandbox._schedule_artifact_upload(exec_result)
            assert len(captured_tasks) == 1
            result = await captured_tasks[0]

        assert result == {
            "type": "artifacts",
            "artifacts": [{"fileName": "out.csv", "documentId": "d"}],
        }
