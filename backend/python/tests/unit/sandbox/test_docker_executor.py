"""Tests for app.sandbox.docker_executor."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox.docker_executor import DockerExecutor
from app.sandbox.models import ExecutionResult, SandboxLanguage


class TestDockerExecutorInit:
    def test_creates_sandbox_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", str(tmp_path / "docker_sandbox"))
        executor = DockerExecutor()
        assert os.path.isdir(str(tmp_path / "docker_sandbox"))

    def test_default_resource_limits(self):
        executor = DockerExecutor()
        assert executor.memory_limit_mb == 512
        assert executor.cpu_limit == 0.5
        assert executor.network_disabled is True

    def test_custom_resource_limits(self):
        executor = DockerExecutor(memory_limit_mb=1024, cpu_limit=1.0, network_disabled=False)
        assert executor.memory_limit_mb == 1024
        assert executor.cpu_limit == 1.0
        assert executor.network_disabled is False


class TestDockerExecutorExecute:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    @pytest.mark.asyncio
    async def test_unsupported_language(self, executor):
        result = await executor.execute("code", "ruby")
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, executor):
        with patch.object(executor, "_run_container", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = asyncio.TimeoutError()
            result = await executor.execute(
                "import time; time.sleep(100)",
                SandboxLanguage.PYTHON,
                timeout_seconds=1,
            )
            assert result.success is False
            assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_generic_exception(self, executor):
        with patch.object(executor, "_run_container", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = RuntimeError("container error")
            result = await executor.execute("code", SandboxLanguage.PYTHON)
            assert result.success is False
            assert "container error" in (result.error or "")

    @pytest.mark.asyncio
    async def test_successful_execution(self, executor):
        mock_result = ExecutionResult(success=True, stdout="hello\n", exit_code=0)
        with patch.object(executor, "_run_container", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute("print('hello')", SandboxLanguage.PYTHON)
            assert result.success is True
            assert result.stdout == "hello\n"


class TestBuildCommand:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    def test_python_no_packages(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("print(1)", SandboxLanguage.PYTHON, src_dir, None)
        assert cmd[0] == "sh"
        assert "python3 /src/main.py" in cmd[2]
        assert os.path.isfile(os.path.join(src_dir, "main.py"))

    def test_python_with_packages(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("import pandas", SandboxLanguage.PYTHON, src_dir, ["pandas"])
        assert "pip install" in cmd[2]
        assert "pandas" in cmd[2]

    def test_typescript_no_packages(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("console.log(1)", SandboxLanguage.TYPESCRIPT, src_dir, None)
        assert "npx tsx /src/main.ts" in cmd[2]
        assert os.path.isfile(os.path.join(src_dir, "main.ts"))

    def test_sqlite(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("SELECT 1;", SandboxLanguage.SQLITE, src_dir, None)
        assert "sqlite3" in cmd[2]
        assert os.path.isfile(os.path.join(src_dir, "query.sql"))

    def test_postgresql(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("SELECT 1;", SandboxLanguage.POSTGRESQL, src_dir, None)
        assert "psql" in cmd[2]


class TestRunContainerDockerMissing:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    @pytest.mark.asyncio
    async def test_docker_not_installed(self, executor, tmp_path):
        with patch.dict("sys.modules", {"docker": None}):
            with patch("builtins.__import__", side_effect=ImportError("no docker")):
                result = await executor._run_container(
                    image="test",
                    command=["echo", "hi"],
                    work_dir=str(tmp_path),
                    src_dir=str(tmp_path),
                    output_dir=str(tmp_path),
                    env={},
                    timeout=10,
                )
                assert result.success is False
                assert "docker" in (result.error or "").lower()


class TestBuildCommandPackageValidation:
    """Tests that _build_command rejects malicious package names."""

    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    def test_rejects_semicolon_in_package(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        with pytest.raises(ValueError, match="Invalid package name"):
            executor._build_command("print(1)", SandboxLanguage.PYTHON, src_dir, ["pandas; rm -rf /"])

    def test_rejects_backtick_in_package(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        with pytest.raises(ValueError, match="Invalid package name"):
            executor._build_command("code", SandboxLanguage.TYPESCRIPT, src_dir, ["`whoami`"])

    def test_rejects_dollar_in_package(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        with pytest.raises(ValueError, match="Invalid package name"):
            executor._build_command("code", SandboxLanguage.PYTHON, src_dir, ["$(evil)"])

    def test_accepts_valid_scoped_npm(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("code", SandboxLanguage.TYPESCRIPT, src_dir, ["@types/node"])
        assert "@types/node" in cmd[2]


class TestExtractContainerDir:
    """Tests for tar extraction path traversal protection."""

    def test_safe_extraction(self, tmp_path):
        import io
        import tarfile
        from app.sandbox.docker_executor import _extract_container_dir

        # Build a mock container that returns a tar with a safe file
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            data = b"hello world"
            info = tarfile.TarInfo(name="output/safe.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        tar_buf.seek(0)
        tar_bytes = tar_buf.read()

        mock_container = MagicMock()
        mock_container.get_archive.return_value = (iter([tar_bytes]), {})

        output_dir = str(tmp_path / "output")
        os.makedirs(output_dir, exist_ok=True)
        _extract_container_dir(mock_container, "/output", output_dir)

        assert os.path.isfile(os.path.join(output_dir, "safe.txt"))
        with open(os.path.join(output_dir, "safe.txt")) as f:
            assert f.read() == "hello world"

    def test_blocks_path_traversal(self, tmp_path):
        import io
        import tarfile
        from app.sandbox.docker_executor import _extract_container_dir

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            data = b"evil content"
            info = tarfile.TarInfo(name="output/../../etc/evil.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        tar_buf.seek(0)
        tar_bytes = tar_buf.read()

        mock_container = MagicMock()
        mock_container.get_archive.return_value = (iter([tar_bytes]), {})

        output_dir = str(tmp_path / "output")
        os.makedirs(output_dir, exist_ok=True)
        _extract_container_dir(mock_container, "/output", output_dir)

        # The evil file should NOT have been written anywhere outside output_dir
        assert not os.path.exists(os.path.join(str(tmp_path), "etc", "evil.txt"))
        # The evil file should also not be in the output dir
        evil_in_output = os.path.join(output_dir, "etc", "evil.txt")
        assert not os.path.exists(evil_in_output)


class TestDockerCleanup:
    def test_cleanup_execution(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", str(tmp_path))
        exec_dir = tmp_path / "exec-id"
        exec_dir.mkdir()
        (exec_dir / "file.txt").write_text("data")
        DockerExecutor.cleanup_execution("exec-id")
        assert not exec_dir.exists()
