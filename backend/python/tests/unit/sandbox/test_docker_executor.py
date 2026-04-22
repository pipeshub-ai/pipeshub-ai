"""Tests for app.sandbox.docker_executor."""

import asyncio
import io
import os
import sys
import tarfile
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox.docker_executor import DockerExecutor
from app.sandbox.models import ExecutionResult, SandboxLanguage


@pytest.fixture(autouse=True)
def _fake_docker_module():
    """Provide a fake ``docker`` top-level module for tests.

    The real docker SDK is not a dependency of this unit-test runner; the
    executor imports it lazily inside methods, so we stuff a minimal stand-in
    into ``sys.modules`` before each test and tear it down after. Tests that
    want to verify behaviour patch ``docker.from_env`` on this fake module.
    """
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
    """_build_command NO LONGER takes packages -- install runs in a separate container."""

    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    def test_python_command_is_clean(self, executor, tmp_path):
        """Python run command must NOT include any pip install anymore."""
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("print(1)", SandboxLanguage.PYTHON, src_dir)
        assert cmd[0] == "sh"
        assert "python3 /src/main.py" in cmd[2]
        assert "pip install" not in cmd[2]
        assert os.path.isfile(os.path.join(src_dir, "main.py"))

    def test_typescript_command_is_clean(self, executor, tmp_path):
        """TypeScript run command must NOT include any npm install anymore."""
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("console.log(1)", SandboxLanguage.TYPESCRIPT, src_dir)
        assert "npx tsx /src/main.ts" in cmd[2]
        assert "npm install" not in cmd[2]
        assert os.path.isfile(os.path.join(src_dir, "main.ts"))

    def test_sqlite(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("SELECT 1;", SandboxLanguage.SQLITE, src_dir)
        assert "sqlite3" in cmd[2]
        assert os.path.isfile(os.path.join(src_dir, "query.sql"))

    def test_postgresql(self, executor, tmp_path):
        src_dir = str(tmp_path / "src")
        os.makedirs(src_dir, exist_ok=True)
        cmd = executor._build_command("SELECT 1;", SandboxLanguage.POSTGRESQL, src_dir)
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


class TestExecutePackageValidation:
    """Package validation at execute() time: shell-injection AND allowlist."""

    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    @pytest.mark.asyncio
    async def test_rejects_semicolon_in_package(self, executor):
        """Shell-injection metacharacter rejected before any container spins up."""
        with patch.object(executor, "_install_dependencies") as install:
            with patch.object(executor, "_run_container", new_callable=AsyncMock) as run:
                result = await executor.execute(
                    "print(1)", SandboxLanguage.PYTHON,
                    packages=["pandas; rm -rf /"],
                )
                assert result.success is False
                assert "Invalid package name" in (result.error or "")
                install.assert_not_called()
                run.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_non_allowlisted_package(self, executor):
        """A package that is syntactically valid but not allowlisted is rejected."""
        with patch.object(executor, "_install_dependencies") as install:
            with patch.object(executor, "_run_container", new_callable=AsyncMock) as run:
                result = await executor.execute(
                    "import x", SandboxLanguage.PYTHON,
                    packages=["not-a-real-allowlisted-pkg"],
                )
                assert result.success is False
                assert "allowlist" in (result.error or "").lower()
                install.assert_not_called()
                run.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_allowlisted_python(self, executor):
        with patch.object(
            executor, "_install_dependencies",
            return_value=(b"fake-tar", "/deps"),
        ) as install:
            with patch.object(
                executor, "_run_container", new_callable=AsyncMock,
                return_value=ExecutionResult(success=True, exit_code=0),
            ) as run:
                result = await executor.execute(
                    "import pandas", SandboxLanguage.PYTHON,
                    packages=["pandas"],
                )
                assert result.success is True
                install.assert_called_once()
                run.assert_called_once()

    @pytest.mark.asyncio
    async def test_accepts_scoped_npm_package(self, executor):
        with patch.object(
            executor, "_install_dependencies",
            return_value=(b"fake-tar", "/node_modules"),
        ):
            with patch.object(
                executor, "_run_container", new_callable=AsyncMock,
                return_value=ExecutionResult(success=True, exit_code=0),
            ):
                result = await executor.execute(
                    "console.log(1)", SandboxLanguage.TYPESCRIPT,
                    packages=["@types/node"],
                )
                assert result.success is True


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


class TestDockerEnvAllowlist:
    """Security: host env must NOT leak into a Docker sandbox container."""

    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    @pytest.mark.asyncio
    async def test_container_env_filters_host_secrets(self, executor, monkeypatch):
        """The environment dict passed to `_run_container` must exclude non-allowlisted host vars."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-leak")
        monkeypatch.setenv("NEO4J_PASSWORD", "super-secret")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        captured_env: dict = {}

        async def fake_run_container(*, env, **kwargs):
            captured_env.update(env)
            return ExecutionResult(success=True, stdout="", exit_code=0)

        with patch.object(executor, "_run_container", side_effect=fake_run_container):
            await executor.execute(
                "print('hi')",
                SandboxLanguage.PYTHON,
                env={"OUTPUT_DIR_OVERRIDE": "/custom"},
            )

        # Allowlist should be present, host secrets must not be
        assert "PATH" in captured_env
        assert "OPENAI_API_KEY" not in captured_env
        assert "NEO4J_PASSWORD" not in captured_env
        # Tool-provided env + the OUTPUT_DIR override remain
        assert captured_env.get("OUTPUT_DIR") == "/output"
        assert captured_env.get("OUTPUT_DIR_OVERRIDE") == "/custom"


class TestDockerCleanup:
    def test_cleanup_execution(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", str(tmp_path))
        exec_dir = tmp_path / "exec-id"
        exec_dir.mkdir()
        (exec_dir / "file.txt").write_text("data")
        DockerExecutor.cleanup_execution("exec-id")
        assert not exec_dir.exists()


# ---------------------------------------------------------------------------
# Two-phase (install + run) flow: mocked docker SDK
# ---------------------------------------------------------------------------


def _make_mock_docker_client(*, install_exit_code: int = 0):
    """Return a (mock_client, tracker) tuple that records container kwargs."""
    tracker = {
        "created_containers": [],   # list of kwargs dicts passed to create()
        "networks_created": [],     # list of kwargs for networks.create()
        "networks_listed": 0,
    }

    install_container = MagicMock()
    install_container.wait.return_value = {"StatusCode": install_exit_code}
    install_container.logs.return_value = b""
    # Tar bytes returned when extracting /deps or /install/node_modules
    fake_tar = _make_fake_tar(["deps/greenlet.py"])
    install_container.get_archive.return_value = (iter([fake_tar]), {})

    run_container = MagicMock()
    run_container.wait.return_value = {"StatusCode": 0}
    run_container.logs.return_value = b"hello\n"
    run_container.get_archive.side_effect = Exception("no output")  # keeps collect_artifacts a no-op

    def _create(**kwargs):
        tracker["created_containers"].append(kwargs)
        # First create() = install, second = run (based on our flow in execute()).
        if len(tracker["created_containers"]) == 1:
            return install_container
        return run_container

    client = MagicMock()
    client.containers.create.side_effect = _create
    client.images.get.return_value = MagicMock()

    def _list_networks(names=None):
        tracker["networks_listed"] += 1
        return []

    client.networks.list.side_effect = _list_networks

    def _create_network(**kwargs):
        tracker["networks_created"].append(kwargs)
        return MagicMock()

    client.networks.create.side_effect = _create_network

    return client, tracker, install_container, run_container


def _make_fake_tar(paths: list[str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for p in paths:
            data = b"fake"
            info = tarfile.TarInfo(name=p)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf.read()


class TestInstallPhase:
    """The install phase must run in its own container on the egress network."""

    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    def test_install_python_uses_target_and_egress_network(self, executor):
        client, tracker, install_container, _ = _make_mock_docker_client()
        with patch("docker.from_env", return_value=client):
            deps_tar, mount = executor._install_dependencies(
                ["pandas"], SandboxLanguage.PYTHON, timeout=60,
            )
            assert mount == "/deps"
            assert deps_tar  # non-empty
        # Exactly one container was created (the install container).
        assert len(tracker["created_containers"]) == 1
        install_kwargs = tracker["created_containers"][0]
        # Uses the dedicated egress network, not the compose default.
        assert install_kwargs["network"] == "pipeshub_sandbox_egress"
        assert install_kwargs["network_disabled"] is False
        # Command uses pip --target /deps with an allowlisted package.
        cmd_str = " ".join(install_kwargs["command"])
        assert "pip install" in cmd_str
        assert "--target /deps" in cmd_str
        assert "pandas" in cmd_str

    def test_install_npm_uses_prefix_and_egress_network(self, executor):
        client, tracker, install_container, _ = _make_mock_docker_client()
        with patch("docker.from_env", return_value=client):
            _, mount = executor._install_dependencies(
                ["chart.js"], SandboxLanguage.TYPESCRIPT, timeout=60,
            )
            assert mount == "/node_modules"
        install_kwargs = tracker["created_containers"][0]
        assert install_kwargs["network"] == "pipeshub_sandbox_egress"
        cmd_str = " ".join(install_kwargs["command"])
        assert "npm install" in cmd_str
        assert "--prefix /install" in cmd_str
        assert "chart.js" in cmd_str

    def test_install_failure_raises(self, executor):
        client, _, _, _ = _make_mock_docker_client(install_exit_code=1)
        with patch("docker.from_env", return_value=client):
            with pytest.raises(RuntimeError, match="Package install failed"):
                executor._install_dependencies(
                    ["pandas"], SandboxLanguage.PYTHON, timeout=60,
                )

    def test_custom_egress_network_env(self, tmp_path, monkeypatch):
        """SANDBOX_EGRESS_NETWORK should override the default network name."""
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        monkeypatch.setenv("SANDBOX_EGRESS_NETWORK", "my-private-net")
        executor = DockerExecutor()
        client, tracker, _, _ = _make_mock_docker_client()
        with patch("docker.from_env", return_value=client):
            executor._install_dependencies(
                ["pandas"], SandboxLanguage.PYTHON, timeout=60,
            )
        assert tracker["created_containers"][0]["network"] == "my-private-net"
        # Network was created with that name, driver=bridge, not internal.
        assert tracker["networks_created"][0]["name"] == "my-private-net"
        assert tracker["networks_created"][0]["driver"] == "bridge"
        assert tracker["networks_created"][0]["internal"] is False

    def test_ensure_egress_network_reuses_existing(self, executor):
        client = MagicMock()
        client.networks.list.return_value = [MagicMock()]  # pretend it exists
        name = executor._ensure_egress_network(client)
        assert name == "pipeshub_sandbox_egress"
        client.networks.create.assert_not_called()

    def test_ensure_egress_network_creates_once(self, executor):
        client = MagicMock()
        client.networks.list.return_value = []
        executor._ensure_egress_network(client)
        client.networks.create.assert_called_once()
        kwargs = client.networks.create.call_args.kwargs
        assert kwargs["name"] == "pipeshub_sandbox_egress"
        assert kwargs["driver"] == "bridge"
        assert kwargs["internal"] is False
        assert kwargs["labels"] == {"pipeshub.sandbox": "egress"}


def _make_mock_run_client():
    """Simpler mock client for direct _run_container tests (single container)."""
    tracker = {"created_containers": []}
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b"ok\n"
    container.get_archive.side_effect = Exception("no output")

    def _create(**kwargs):
        tracker["created_containers"].append(kwargs)
        return container

    client = MagicMock()
    client.containers.create.side_effect = _create
    client.images.get.return_value = MagicMock()
    return client, tracker, container


class TestRunContainerIsolation:
    """The run container must have NO networking stack and never use egress network."""

    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    @pytest.mark.asyncio
    async def test_run_container_uses_network_mode_none(self, executor, tmp_path):
        """network_mode='none' AND network_disabled=True on the run container."""
        client, tracker, _ = _make_mock_run_client()
        work_dir = str(tmp_path / "work")
        src_dir = os.path.join(work_dir, "src")
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        with patch("docker.from_env", return_value=client):
            await executor._run_container(
                image="pipeshub/sandbox:latest",
                command=["sh", "-c", "echo hi"],
                work_dir=work_dir,
                src_dir=src_dir,
                output_dir=output_dir,
                env={"OUTPUT_DIR": "/output"},
                timeout=10,
            )

        assert len(tracker["created_containers"]) == 1
        kwargs = tracker["created_containers"][0]
        assert kwargs["network_mode"] == "none"
        assert kwargs["network_disabled"] is True
        assert "network" not in kwargs  # MUST NOT be attached to any named network

    @pytest.mark.asyncio
    async def test_run_container_receives_deps_tar_and_pythonpath(self, executor, tmp_path):
        """Python deps tar is injected at /deps and PYTHONPATH env is set."""
        client, tracker, run_container = _make_mock_run_client()
        work_dir = str(tmp_path / "work")
        src_dir = os.path.join(work_dir, "src")
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        fake_tar = _make_fake_tar(["deps/pandas/__init__.py"])

        with patch("docker.from_env", return_value=client):
            await executor._run_container(
                image="pipeshub/sandbox:latest",
                command=["sh", "-c", "python3 /src/main.py"],
                work_dir=work_dir,
                src_dir=src_dir,
                output_dir=output_dir,
                env={"OUTPUT_DIR": "/output", "PYTHONPATH": "/deps"},
                timeout=10,
                deps_tar=fake_tar,
                deps_target="/deps",
            )

        # put_archive called for /src, /output, and /deps (twice: mkdir + tar).
        paths = [call.args[0] for call in run_container.put_archive.call_args_list]
        assert "/src" in paths
        assert "/deps" in paths
        # PYTHONPATH must be present in the container env.
        assert tracker["created_containers"][0]["environment"]["PYTHONPATH"] == "/deps"

    @pytest.mark.asyncio
    async def test_run_container_node_path_for_typescript(self, executor, tmp_path):
        client, tracker, _ = _make_mock_run_client()
        work_dir = str(tmp_path / "work")
        src_dir = os.path.join(work_dir, "src")
        output_dir = os.path.join(work_dir, "output")
        os.makedirs(src_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        fake_tar = _make_fake_tar(["node_modules/chart.js/package.json"])

        with patch("docker.from_env", return_value=client):
            await executor._run_container(
                image="pipeshub/sandbox:latest",
                command=["sh", "-c", "npx tsx /src/main.ts"],
                work_dir=work_dir,
                src_dir=src_dir,
                output_dir=output_dir,
                env={"OUTPUT_DIR": "/output", "NODE_PATH": "/node_modules"},
                timeout=10,
                deps_tar=fake_tar,
                deps_target="/node_modules",
            )
        env = tracker["created_containers"][0]["environment"]
        assert env.get("NODE_PATH") == "/node_modules"


class TestEndToEndExecuteSetsPythonPath:
    """Integration-ish: execute() wires install deps_tar -> run env correctly."""

    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "docker_sandbox")
        monkeypatch.setattr("app.sandbox.docker_executor._SANDBOX_ROOT", root)
        return DockerExecutor()

    @pytest.mark.asyncio
    async def test_pythonpath_set_when_packages_installed(self, executor):
        captured_env: dict = {}

        async def fake_run_container(*, env, **kwargs):
            captured_env.update(env)
            return ExecutionResult(success=True, exit_code=0)

        with patch.object(
            executor, "_install_dependencies",
            return_value=(b"fake-tar", "/deps"),
        ):
            with patch.object(executor, "_run_container", side_effect=fake_run_container):
                result = await executor.execute(
                    "import pandas", SandboxLanguage.PYTHON,
                    packages=["pandas"],
                )
                assert result.success is True
        assert captured_env.get("PYTHONPATH") == "/deps"
        assert captured_env.get("OUTPUT_DIR") == "/output"

    @pytest.mark.asyncio
    async def test_nodepath_set_when_packages_installed(self, executor):
        captured_env: dict = {}

        async def fake_run_container(*, env, **kwargs):
            captured_env.update(env)
            return ExecutionResult(success=True, exit_code=0)

        with patch.object(
            executor, "_install_dependencies",
            return_value=(b"fake-tar", "/node_modules"),
        ):
            with patch.object(executor, "_run_container", side_effect=fake_run_container):
                result = await executor.execute(
                    "console.log(1)", SandboxLanguage.TYPESCRIPT,
                    packages=["chart.js"],
                )
                assert result.success is True
        assert captured_env.get("NODE_PATH") == "/node_modules"

    @pytest.mark.asyncio
    async def test_no_pythonpath_when_no_packages(self, executor):
        captured_env: dict = {}

        async def fake_run_container(*, env, **kwargs):
            captured_env.update(env)
            return ExecutionResult(success=True, exit_code=0)

        with patch.object(executor, "_install_dependencies") as install:
            with patch.object(executor, "_run_container", side_effect=fake_run_container):
                await executor.execute("print(1)", SandboxLanguage.PYTHON)
        # No install phase should have happened.
        install.assert_not_called()
        # PYTHONPATH should not be injected when no packages were requested.
        assert "PYTHONPATH" not in captured_env or captured_env["PYTHONPATH"] != "/deps"
