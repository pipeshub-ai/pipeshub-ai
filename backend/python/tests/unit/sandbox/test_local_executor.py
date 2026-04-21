"""Tests for app.sandbox.local_executor."""

import asyncio
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox.local_executor import LocalExecutor, _SANDBOX_ROOT
from app.sandbox.models import ExecutionResult, SandboxLanguage


class TestLocalExecutorInit:
    def test_creates_sandbox_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", str(tmp_path / "sandbox"))
        executor = LocalExecutor()
        assert os.path.isdir(str(tmp_path / "sandbox"))


class TestLocalExecutorExecute:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_unsupported_language(self, executor):
        result = await executor.execute("code", "ruby")
        assert result.success is False
        assert "Unsupported language" in (result.error or "")

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, executor):
        with patch.object(executor, "_subprocess", new_callable=AsyncMock) as mock_sub:
            mock_sub.side_effect = asyncio.TimeoutError()
            result = await executor.execute(
                "import time; time.sleep(100)",
                SandboxLanguage.PYTHON,
                timeout_seconds=1,
            )
            assert result.success is False
            assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_generic_exception(self, executor):
        with patch.object(executor, "_subprocess", new_callable=AsyncMock) as mock_sub:
            mock_sub.side_effect = RuntimeError("boom")
            result = await executor.execute("code", SandboxLanguage.PYTHON)
            assert result.success is False
            assert "boom" in (result.error or "")


class TestLocalExecutorPython:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_run_python_success(self, executor):
        mock_result = ExecutionResult(success=True, stdout="hello\n", exit_code=0)
        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute("print('hello')", SandboxLanguage.PYTHON)
            assert result.success is True
            assert result.stdout == "hello\n"

    @pytest.mark.asyncio
    async def test_run_python_with_packages(self, executor):
        pip_result = ExecutionResult(success=True, exit_code=0)
        run_result = ExecutionResult(success=True, stdout="ok", exit_code=0)
        call_count = 0

        async def _mock_sub(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pip_result
            return run_result

        with patch.object(executor, "_subprocess", side_effect=_mock_sub):
            result = await executor.execute(
                "import pandas",
                SandboxLanguage.PYTHON,
                packages=["pandas"],
            )
            assert result.success is True
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_run_python_pip_fail(self, executor):
        pip_result = ExecutionResult(success=False, stderr="ERROR", exit_code=1)

        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=pip_result):
            result = await executor.execute(
                "import missing",
                SandboxLanguage.PYTHON,
                packages=["nonexistent_pkg"],
            )
            assert result.success is False
            assert "pip install failed" in (result.error or "")


class TestLocalExecutorTypescript:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_run_typescript_success(self, executor):
        mock_result = ExecutionResult(success=True, stdout="world\n", exit_code=0)
        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute("console.log('world')", SandboxLanguage.TYPESCRIPT)
            assert result.success is True


class TestLocalExecutorSQLite:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_run_sqlite(self, executor):
        mock_result = ExecutionResult(success=True, stdout="id,name\n1,alice\n", exit_code=0)
        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute("SELECT 1;", SandboxLanguage.SQLITE)
            assert result.success is True


class TestLocalExecutorPostgreSQL:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_run_postgresql_no_url(self, executor):
        result = await executor.execute("SELECT 1;", SandboxLanguage.POSTGRESQL)
        assert result.success is False
        assert "DATABASE_URL" in (result.error or "")

    @pytest.mark.asyncio
    async def test_run_postgresql_with_url(self, executor):
        mock_result = ExecutionResult(success=True, stdout="count\n42\n", exit_code=0)
        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute(
                "SELECT 1;",
                SandboxLanguage.POSTGRESQL,
                env={"DATABASE_URL": "postgresql://localhost/test"},
            )
            assert result.success is True


class TestLocalExecutorSubprocess:
    @pytest.mark.asyncio
    async def test_subprocess_success(self, tmp_path):
        result = await LocalExecutor._subprocess(
            ["echo", "hello"],
            str(tmp_path),
            timeout=5,
            env=None,
        )
        assert result.success is True
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_subprocess_failure(self, tmp_path):
        result = await LocalExecutor._subprocess(
            ["false"],
            str(tmp_path),
            timeout=5,
            env=None,
        )
        assert result.success is False
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_subprocess_timeout(self, tmp_path):
        with pytest.raises(asyncio.TimeoutError):
            await LocalExecutor._subprocess(
                ["sleep", "10"],
                str(tmp_path),
                timeout=1,
                env=None,
            )

    @pytest.mark.asyncio
    async def test_subprocess_with_stdin(self, tmp_path):
        result = await LocalExecutor._subprocess(
            ["cat"],
            str(tmp_path),
            timeout=5,
            env=None,
            stdin_data="test input",
        )
        assert result.success is True
        assert "test input" in result.stdout


class TestCollectArtifacts:
    def test_empty_dir(self, tmp_path):
        outdir = tmp_path / "output"
        outdir.mkdir()
        artifacts = LocalExecutor.collect_artifacts(str(outdir))
        assert artifacts == []

    def test_nonexistent_dir(self):
        artifacts = LocalExecutor.collect_artifacts("/nonexistent/path")
        assert artifacts == []

    def test_collects_files(self, tmp_path):
        outdir = tmp_path / "output"
        outdir.mkdir()
        (outdir / "chart.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        (outdir / "data.csv").write_text("a,b\n1,2\n")

        artifacts = LocalExecutor.collect_artifacts(str(outdir))
        assert len(artifacts) == 2
        names = {a.file_name for a in artifacts}
        assert names == {"chart.png", "data.csv"}

        for a in artifacts:
            assert a.size_bytes > 0
            assert a.mime_type != ""

    def test_nested_files(self, tmp_path):
        outdir = tmp_path / "output"
        sub = outdir / "sub"
        sub.mkdir(parents=True)
        (sub / "report.pdf").write_bytes(b"%PDF" + b"\x00" * 50)

        artifacts = LocalExecutor.collect_artifacts(str(outdir))
        assert len(artifacts) == 1
        assert artifacts[0].file_name == "report.pdf"
        assert artifacts[0].mime_type == "application/pdf"


class TestCleanup:
    def test_cleanup_execution(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", str(tmp_path))
        exec_dir = tmp_path / "test-exec-id"
        exec_dir.mkdir()
        (exec_dir / "file.txt").write_text("data")

        LocalExecutor.cleanup_execution("test-exec-id")
        assert not exec_dir.exists()

    def test_cleanup_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", str(tmp_path))
        LocalExecutor.cleanup_execution("nonexistent")  # should not raise
