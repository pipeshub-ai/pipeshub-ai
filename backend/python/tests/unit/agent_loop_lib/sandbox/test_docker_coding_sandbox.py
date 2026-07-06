"""Tests for app.agent_loop_lib.sandbox.coding.docker.DockerCodingSandbox."""

from __future__ import annotations

import io
import os
import sys
import tarfile
import types
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodingSandboxError,
    ErrorCategory,
    InstallResult,
)
from app.agent_loop_lib.sandbox.coding.docker import (
    DockerCodingSandbox,
    _extract_container_dir,
)


@pytest.fixture(autouse=True)
def _fake_docker_module():
    """Stub `docker` in `sys.modules` — the real SDK is not a unit-test
    dependency; `DockerCodingSandbox` imports it lazily inside its blocking
    (`asyncio.to_thread`-wrapped) helper methods."""
    created = False
    if "docker" not in sys.modules:
        fake = types.ModuleType("docker")

        class _ImageNotFound(Exception):
            pass

        errors_mod = types.ModuleType("docker.errors")
        errors_mod.ImageNotFound = _ImageNotFound
        fake.errors = errors_mod
        fake.from_env = MagicMock()
        sys.modules["docker"] = fake
        sys.modules["docker.errors"] = errors_mod
        created = True
    try:
        yield
    finally:
        if created:
            sys.modules.pop("docker", None)
            sys.modules.pop("docker.errors", None)


def _make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf.read()


def _fake_container(
    *,
    exit_code: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
    output_files: dict[str, bytes] | None = None,
    src_files: dict[str, bytes] | None = None,
):
    """`output_files`/`src_files` keys must carry their tar prefix
    ("output/..." / "src/...") — matching what `get_archive` on a real
    container returns for `/output` and `/src` respectively."""
    container = MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}

    def _logs(*, stdout: bool = True, stderr: bool = True):
        return stdout_bytes if stdout else stderr_bytes

    stdout_bytes, stderr_bytes = stdout, stderr
    container.logs.side_effect = lambda **kwargs: _logs(**kwargs)

    archives = {
        "/output": output_files or {},
        "/src": src_files or {},
    }

    def _get_archive(path):
        return (iter([_make_tar(archives.get(path, {}))]), {})

    container.get_archive.side_effect = _get_archive
    return container


class TestContract:
    async def test_sandbox_id_readable_before_provision(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        assert isinstance(sandbox.sandbox_id, str)
        assert sandbox.sandbox_id

    async def test_provision_creates_working_dir_and_returns_ready(self, tmp_path) -> None:
        working_dir = str(tmp_path / "wd")
        sandbox = DockerCodingSandbox(working_dir=working_dir)
        info = await sandbox.provision()
        assert info.status == "ready"
        assert info.sandbox_id == sandbox.sandbox_id
        assert os.path.isdir(working_dir)

    async def test_destroy_removes_dir_and_is_idempotent(self, tmp_path) -> None:
        working_dir = str(tmp_path / "wd")
        sandbox = DockerCodingSandbox(working_dir=working_dir)
        await sandbox.provision()
        assert os.path.isdir(working_dir)

        await sandbox.destroy()
        assert not os.path.isdir(working_dir)

        await sandbox.destroy()  # idempotent — no raise

    async def test_async_context_manager_provisions_and_destroys(self, tmp_path) -> None:
        working_dir = str(tmp_path / "wd")
        async with DockerCodingSandbox(working_dir=working_dir) as sandbox:
            assert os.path.isdir(working_dir)
            assert sandbox.sandbox_id
        assert not os.path.isdir(working_dir)


class TestExecuteHappyPath:
    async def test_successful_run_extracts_artifacts(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = _fake_container(
            exit_code=0, stdout=b"hello\n", output_files={"output/result.txt": b"artifact-data"},
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "hello\n"
        assert result.duration_ms >= 0
        assert result.artifacts == ["output/result.txt"]

        content = await sandbox.download_file(result.artifacts[0])
        assert content == b"artifact-data"

    async def test_file_written_to_container_cwd_is_reported_as_artifact(self, tmp_path) -> None:
        """Models overwhelmingly write output files to their cwd (/src in
        the container), not to $OUTPUT_DIR — those files must be captured
        as artifacts too, or every 'create a PDF' style request silently
        loses its output when the container is removed."""
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = _fake_container(
            exit_code=0,
            src_files={"src/five_jokes.pdf": b"%PDF-fake", "src/main.py": b"print(1)"},
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert result.success is True
        # The entry script itself must NOT be promoted — only real output.
        assert result.artifacts == ["five_jokes.pdf"]
        assert await sandbox.download_file("five_jokes.pdf") == b"%PDF-fake"

    async def test_cwd_and_output_dir_artifacts_are_combined(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = _fake_container(
            exit_code=0,
            output_files={"output/report.csv": b"a,b"},
            src_files={"src/chart.png": b"\x89PNG"},
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert result.artifacts == ["chart.png", "output/report.csv"]

    async def test_run_command_uses_python_entry(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = _fake_container(exit_code=0)
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        _, kwargs = fake_client.containers.create.call_args
        assert kwargs["command"] == ["sh", "-c", "python3 main.py"]
        assert kwargs["network_mode"] == "none"
        assert kwargs["network_disabled"] is True


class TestNetworkAccess:
    async def test_default_run_container_has_no_network(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = _fake_container(exit_code=0)
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        _, kwargs = fake_client.containers.create.call_args
        assert kwargs["network_mode"] == "none"
        assert kwargs["network_disabled"] is True
        assert "network" not in kwargs

    async def test_backend_and_request_both_allowing_network_joins_egress_network(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), allow_network=True, egress_network="my-egress")
        container = _fake_container(exit_code=0)
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container
        fake_client.networks.list.return_value = [MagicMock()]

        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python", allow_network=True))

        _, kwargs = fake_client.containers.create.call_args
        assert kwargs["network"] == "my-egress"
        assert kwargs["network_disabled"] is False
        assert "network_mode" not in kwargs

    async def test_backend_flag_off_vetoes_request_allow_network(self, tmp_path) -> None:
        """The backend-level ceiling (set once by the operator/adapter) must
        win even if an individual `CodeRequest` asks for network — either
        side can veto it."""
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), allow_network=False)
        container = _fake_container(exit_code=0)
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python", allow_network=True))

        _, kwargs = fake_client.containers.create.call_args
        assert kwargs["network_mode"] == "none"
        assert kwargs["network_disabled"] is True

    async def test_request_flag_off_vetoes_backend_allow_network(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), allow_network=True)
        container = _fake_container(exit_code=0)
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python", allow_network=False))

        _, kwargs = fake_client.containers.create.call_args
        assert kwargs["network_mode"] == "none"
        assert kwargs["network_disabled"] is True


class TestExecuteFailureAsData:
    async def test_nonzero_exit_populates_error_analysis(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = _fake_container(
            exit_code=1,
            stderr=b'Traceback (most recent call last):\n  File "main.py", line 1, in <module>\nValueError: boom\n',
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="raise ValueError('boom')", language="python"))

        assert result.success is False
        assert result.exit_code == 1
        assert result.error_analysis is not None
        assert result.error_analysis.category == ErrorCategory.RUNTIME

    async def test_infra_exception_never_raises(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()
        fake_client.containers.create.side_effect = RuntimeError("daemon unreachable")

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert result.success is False
        assert result.exit_code == -1
        assert "daemon unreachable" in result.stderr


class TestExecuteTimeout:
    async def test_wait_exception_yields_timeout_and_kills_container(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = _fake_container(exit_code=0)
        container.wait.side_effect = TimeoutError("read timed out")
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(
                CodeRequest(code="import time; time.sleep(100)", language="python", timeout=1)
            )

        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()
        assert result.error_analysis is not None
        assert result.error_analysis.category == ErrorCategory.TIMEOUT
        container.kill.assert_called_once()


class TestInstallPackagesIdempotency:
    async def test_second_call_skips_container_creation(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = _fake_container(exit_code=0, output_files={"deps/pandas/__init__.py": b""})
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container
        fake_client.networks.list.return_value = [MagicMock()]

        with patch("docker.from_env", return_value=fake_client):
            first = await sandbox.install_packages(["pandas"], "python")
            assert first.success is True
            assert fake_client.containers.create.call_count == 1

            second = await sandbox.install_packages(["pandas"], "python")
            assert second.success is True
            assert second.installed == []
            assert fake_client.containers.create.call_count == 1


class TestPackageInjectionGuard:
    async def test_install_packages_rejects_shell_metacharacters(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env") as mock_from_env:
            result = await sandbox.install_packages(["lodash; rm -rf /"], "typescript")

        assert result.success is False
        assert "invalid or unsafe package spec" in result.stderr
        mock_from_env.assert_not_called()

    async def test_execute_rejects_shell_metacharacters_in_packages(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch("docker.from_env") as mock_from_env:
            result = await sandbox.execute(
                CodeRequest(code="1", language="python", packages=["evil$(x)"])
            )

        assert result.success is False
        assert result.error_analysis is not None
        assert result.error_analysis.category == ErrorCategory.IMPORT
        mock_from_env.assert_not_called()


class TestAllowlistDenylist:
    async def test_denylisted_package_rejected(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), package_denylist=["left-pad"])
        with patch("docker.from_env") as mock_from_env:
            result = await sandbox.install_packages(["left-pad"], "typescript")

        assert result.success is False
        assert "denylisted" in result.stderr
        mock_from_env.assert_not_called()

    async def test_non_allowlisted_package_rejected(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), package_allowlist=["lodash"])
        with patch("docker.from_env") as mock_from_env:
            result = await sandbox.install_packages(["not-allowed-pkg"], "typescript")

        assert result.success is False
        assert "allowlist" in result.stderr
        mock_from_env.assert_not_called()


class TestPathTraversal:
    async def test_upload_file_rejects_traversal(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with pytest.raises(ValueError):
            await sandbox.upload_file("../../etc/passwd", b"x")

    async def test_download_file_rejects_traversal(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with pytest.raises(ValueError):
            await sandbox.download_file("../secret")

    async def test_upload_then_download_round_trips_within_sandbox(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("notes/todo.txt", b"buy milk")
        assert await sandbox.download_file("notes/todo.txt") == b"buy milk"


class TestTarExtractionGuard:
    def test_extract_container_dir_blocks_path_traversal(self, tmp_path) -> None:
        tar_bytes = _make_tar({"output/../../etc/evil.txt": b"evil-content"})
        container = MagicMock()
        container.get_archive.return_value = (iter([tar_bytes]), {})

        output_dir = str(tmp_path / "output")
        os.makedirs(output_dir, exist_ok=True)
        _extract_container_dir(container, "/output", output_dir)

        assert not os.path.exists(os.path.join(str(tmp_path), "etc", "evil.txt"))
        assert list(os.scandir(output_dir)) == []

    def test_extract_container_dir_extracts_safe_member(self, tmp_path) -> None:
        tar_bytes = _make_tar({"output/safe.txt": b"safe-content"})
        container = MagicMock()
        container.get_archive.return_value = (iter([tar_bytes]), {})

        output_dir = str(tmp_path / "output")
        os.makedirs(output_dir, exist_ok=True)
        _extract_container_dir(container, "/output", output_dir)

        with open(os.path.join(output_dir, "safe.txt"), "rb") as f:
            assert f.read() == b"safe-content"


class TestDockerMissing:
    async def test_execute_raises_infra_error_when_docker_package_missing(self, tmp_path) -> None:
        """Missing `docker` SDK is an infrastructure failure — per the
        `CodingSandboxBackend.execute()` contract, this is one of the few
        cases allowed to raise rather than come back as failure-as-data."""
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch.dict("sys.modules", {"docker": None}):
            with patch("builtins.__import__", side_effect=ImportError("no docker")):
                with pytest.raises(CodingSandboxError, match="docker"):
                    await sandbox.execute(CodeRequest(code="print(1)", language="python"))


class TestConfigWiring:
    async def test_control_plane_registers_docker_backend_factory(self) -> None:
        from app.agent_loop_lib.control_plane.config import (
            CodingSandboxConfig,
            ControlPlaneConfig,
        )
        from app.agent_loop_lib.control_plane.control_plane import ControlPlane
        from app.agent_loop_lib.sandbox.manager import SandboxType

        cfg = ControlPlaneConfig(
            coding_sandbox=CodingSandboxConfig(enabled=True, backend="docker"),
            hooks=[], tools=[],
        )
        control_plane = ControlPlane(cfg)
        await control_plane.start()

        assert control_plane.sandbox_manager.is_registered(SandboxType.CODING)
        _, backend = await control_plane.sandbox_manager.get_or_create(SandboxType.CODING)
        assert isinstance(backend, DockerCodingSandbox)
        await control_plane.sandbox_manager.destroy_all()
