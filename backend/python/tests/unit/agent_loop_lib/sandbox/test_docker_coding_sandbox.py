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
)
from app.agent_loop_lib.sandbox.coding.docker_client import reset_default_provider
from app.agent_loop_lib.sandbox.coding.docker import (
    DockerCodingSandbox,
    _collect_working_dir_inputs,
    _extract_container_dir,
    _tar_files,
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


@pytest.fixture(autouse=True)
def _fresh_docker_provider():
    """`DockerCodingSandbox` draws its client from the process-wide
    `DockerClientProvider`, which caches it on first use. Without a reset
    the first test to touch the daemon would pin its fake client for every
    test after it, and the `patch("docker.from_env")` blocks below would
    silently have no effect."""
    reset_default_provider()
    yield
    reset_default_provider()


def _make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf.read()


_TOKEN = "0123456789abcdef0123456789abcdef"
_NO_FIREWALL = f"[sandbox-egress] firewall unavailable ({_TOKEN}): no working iptables in the sandbox image\n".encode()


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

    async def test_second_call_does_not_re_report_first_calls_output(self, tmp_path) -> None:
        """`output/` is a HOST directory that persists and merges across
        calls on the same sandbox instance (`_extract_container_dir` never
        clears it) — without the mtime-snapshot diff, a second call whose
        own container writes nothing new to `/output` would still re-list
        (and the bridge layer would re-download/re-register) the FIRST
        call's file."""
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()

        first_container = _fake_container(
            exit_code=0, output_files={"output/result1.txt": b"data1"},
        )
        fake_client.containers.create.return_value = first_container
        with patch("docker.from_env", return_value=fake_client):
            first = await sandbox.execute(CodeRequest(code="print(1)", language="python"))
        assert first.artifacts == ["output/result1.txt"]

        # Second call's own container writes nothing new to /output — the
        # host-side output/ dir still has result1.txt from the FIRST call,
        # merged in by the first call's own extraction.
        second_container = _fake_container(exit_code=0, output_files={})
        fake_client.containers.create.return_value = second_container
        with patch("docker.from_env", return_value=fake_client):
            second = await sandbox.execute(CodeRequest(code="print(2)", language="python"))

        assert second.artifacts == []
        # The file is still there on disk (so `read_sandbox_file` on it
        # still works) — it is just not RE-REPORTED as a fresh artifact.
        assert os.path.isfile(os.path.join(sandbox.working_dir, "output", "result1.txt"))

    async def test_second_call_reports_only_its_own_new_output_file(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()

        first_container = _fake_container(
            exit_code=0, output_files={"output/result1.txt": b"data1"},
        )
        fake_client.containers.create.return_value = first_container
        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        second_container = _fake_container(
            exit_code=0, output_files={"output/result1.txt": b"data1", "output/result2.txt": b"data2"},
        )
        fake_client.containers.create.return_value = second_container
        with patch("docker.from_env", return_value=fake_client):
            second = await sandbox.execute(CodeRequest(code="print(2)", language="python"))

        assert second.artifacts == ["output/result2.txt"]

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


class TestStagedInputArtifacts:
    """Regression coverage for the fix-artifact-staging bug: anything
    `upload_file()` put on the HOST working dir (staged `input_artifacts`,
    skill resources, ...) must actually reach the run container, and an
    unchanged staged file mirrored back afterward must NOT be re-reported
    as a freshly produced artifact (only a genuine modification should
    be)."""

    async def test_staged_input_is_archived_into_src_before_run(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("input/artifacts/photo.png", b"\x89PNG-fake")

        container = _fake_container(exit_code=0)
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        src_put_calls = [c for c in container.put_archive.call_args_list if c.args[0] == "/src"]
        matches = []
        for call in src_put_calls:
            with tarfile.open(fileobj=io.BytesIO(call.args[1])) as tar:
                if "input/artifacts/photo.png" in tar.getnames():
                    matches.append(tar.extractfile("input/artifacts/photo.png").read())
        assert matches == [b"\x89PNG-fake"]

    async def test_unchanged_staged_input_not_reported_as_artifact(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("input/artifacts/photo.png", b"same-bytes")

        # Container mirror-back includes the staged file byte-identical
        # (program never touched it) alongside a genuinely new output.
        container = _fake_container(
            exit_code=0,
            src_files={
                "src/input/artifacts/photo.png": b"same-bytes",
                "src/deck.pptx": b"new-output",
            },
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert result.artifacts == ["deck.pptx"]

    async def test_modified_staged_input_is_reported_as_artifact(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("input/artifacts/photo.png", b"original-bytes")

        container = _fake_container(
            exit_code=0,
            src_files={"src/input/artifacts/photo.png": b"modified-bytes"},
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = container

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert result.artifacts == ["input/artifacts/photo.png"]
        assert await sandbox.download_file("input/artifacts/photo.png") == b"modified-bytes"


def _with_egress_network(fake_client: MagicMock, name: str) -> None:
    network = MagicMock()
    network.name = name
    network.id = "b" * 64
    network.attrs = {
        "Id": "b" * 64,
        "Options": {"com.docker.network.bridge.enable_icc": "false"},
        "IPAM": {"Config": [{"Subnet": "172.31.0.0/16"}]},
        "Containers": {},
    }
    fake_client.networks.list.return_value = [network]
    fake_client.networks.get.return_value = network


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
        sandbox = DockerCodingSandbox(
            working_dir=str(tmp_path / "wd"), allow_network=True, egress_network="my-egress",
            egress_allow_cidrs=("10.20.0.0/16",),
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = _fake_container(exit_code=0)
        _with_egress_network(fake_client, "my-egress")

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python", allow_network=True))

        fake_client.containers.create.assert_called_once()
        kwargs = fake_client.containers.create.call_args.kwargs
        assert kwargs["network"] == "my-egress"
        assert kwargs["network_disabled"] is False
        assert "network_mode" not in kwargs
        # Firewall first, then the program as `sandbox` with nothing left.
        assert kwargs["command"][4:] == ["sh", "-c", "python3 main.py"]
        assert kwargs["user"] == "0"
        assert kwargs["cap_drop"] == ["ALL"]
        script = kwargs["command"][2]
        assert "-d 172.31.0.0/16 -j REJECT" in script
        assert "-d 169.254.0.0/16 -j REJECT" in script
        assert "-d 10.20.0.0/16 -j ACCEPT" in script
        assert "--reuid=sandbox" in script
        assert "Network access is unavailable" not in result.stderr

    async def test_image_without_firewall_reruns_offline(self, tmp_path) -> None:
        """An unfiltered bridge reaches the host, private ranges and cloud
        metadata, so a run whose firewall can't be installed gets no network.
        The firewall step exits before the program starts, so the retry
        can't repeat the program's side effects."""
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), allow_network=True, egress_network="my-egress")
        no_firewall = _fake_container(exit_code=222, stderr=_NO_FIREWALL)
        offline = _fake_container(exit_code=0, stdout=b"1\n")
        fake_client = MagicMock()
        fake_client.containers.create.side_effect = [no_firewall, offline]
        _with_egress_network(fake_client, "my-egress")

        with patch("docker.from_env", return_value=fake_client), patch(
            "app.agent_loop_lib.sandbox.coding.docker.new_firewall_token", return_value=_TOKEN,
        ):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python", allow_network=True))

        assert f"({_TOKEN})" in fake_client.containers.create.call_args_list[0].kwargs["command"][2]

        kwargs = fake_client.containers.create.call_args.kwargs
        assert kwargs["network_mode"] == "none"
        assert "network" not in kwargs
        assert result.exit_code == 0
        assert result.stdout == "1\n"
        assert "Network access is unavailable" in result.stderr

    async def test_program_faking_the_firewall_signal_is_not_rerun(self, tmp_path) -> None:
        """The program can print the marker and exit 222 but can't know the
        container's token, so it can't get itself run a second time."""
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), allow_network=True, egress_network="my-egress")
        forged = _fake_container(
            exit_code=222, stdout=b"ran\n",
            stderr=b"[sandbox-egress] firewall unavailable: no working iptables in the sandbox image\n",
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = forged
        _with_egress_network(fake_client, "my-egress")

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python", allow_network=True))

        fake_client.containers.create.assert_called_once()
        assert result.exit_code == 222
        assert result.stdout == "ran\n"
        assert "Network access is unavailable" not in result.stderr

    async def test_invalid_allowed_cidr_never_reaches_the_firewall_script(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(
            working_dir=str(tmp_path / "wd"), allow_network=True, egress_network="my-egress",
            egress_allow_cidrs=("1.1.1.1/32 -j ACCEPT; id >&2; true", "10.20.3.4/16"),
        )
        fake_client = MagicMock()
        fake_client.containers.create.return_value = _fake_container(exit_code=0)
        _with_egress_network(fake_client, "my-egress")

        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python", allow_network=True))

        script = fake_client.containers.create.call_args.kwargs["command"][2]
        assert "-d 10.20.0.0/16 -j ACCEPT" in script
        assert "id >&2" not in script
        assert "1.1.1.1/32" not in script

    async def test_unreadable_bridge_subnet_runs_offline(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), allow_network=True, egress_network="my-egress")
        fake_client = MagicMock()
        fake_client.containers.create.return_value = _fake_container(exit_code=0)
        _with_egress_network(fake_client, "my-egress")
        fake_client.networks.get.return_value.attrs["IPAM"]["Config"] = []

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python", allow_network=True))

        fake_client.containers.create.assert_called_once()
        assert fake_client.containers.create.call_args.kwargs["network_mode"] == "none"
        assert "Network access is unavailable" in result.stderr

    async def test_run_container_drops_capabilities(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()
        fake_client.containers.create.return_value = _fake_container(exit_code=0)

        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        kwargs = fake_client.containers.create.call_args.kwargs
        assert kwargs["cap_drop"] == ["ALL"]
        assert kwargs["security_opt"] == ["no-new-privileges:true"]
        assert kwargs["pids_limit"] > 0

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
        _with_egress_network(fake_client, "sandbox_egress")

        with patch("docker.from_env", return_value=fake_client):
            first = await sandbox.install_packages(["pandas"], "python")
            assert first.success is True
            assert fake_client.containers.create.call_count == 1
            kwargs = fake_client.containers.create.call_args.kwargs
            assert kwargs["network"] == "sandbox_egress"
            assert "pip install" in kwargs["command"][6]
            assert "-d 169.254.0.0/16 -j REJECT" in kwargs["command"][2]

            second = await sandbox.install_packages(["pandas"], "python")
            assert second.success is True
            assert second.installed == []
            assert fake_client.containers.create.call_count == 1


class TestInstallFailsClosed:
    """pip builds sdists and npm runs lifecycle scripts, so an install is
    never retried on the bridge without the firewall."""

    async def test_image_without_firewall_refuses_the_install(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()
        fake_client.containers.create.return_value = _fake_container(exit_code=222, stderr=_NO_FIREWALL)
        _with_egress_network(fake_client, "sandbox_egress")

        with patch("docker.from_env", return_value=fake_client), patch(
            "app.agent_loop_lib.sandbox.coding.docker.new_firewall_token", return_value=_TOKEN,
        ):
            result = await sandbox.install_packages(["pandas"], "python")

        assert result.success is False
        assert "Package install refused" in result.stderr
        fake_client.containers.create.assert_called_once()
        assert fake_client.containers.create.call_args.kwargs["user"] == "0"

    async def test_unreadable_bridge_subnet_refuses_the_install(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()
        _with_egress_network(fake_client, "sandbox_egress")
        fake_client.networks.get.return_value.attrs["IPAM"]["Config"] = []

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.install_packages(["pandas"], "python")

        assert result.success is False
        assert "subnet unreadable" in result.stderr
        fake_client.containers.create.assert_not_called()

    async def test_install_output_faking_the_signal_is_a_plain_failure(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()
        fake_client.containers.create.return_value = _fake_container(
            exit_code=222, stderr=b"[sandbox-egress] firewall unavailable: printed by a build script\n",
        )
        _with_egress_network(fake_client, "sandbox_egress")

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.install_packages(["pandas"], "python")

        assert result.success is False
        assert "Package install refused" not in result.stderr
        fake_client.containers.create.assert_called_once()

    async def test_real_install_failure_is_not_retried(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()
        fake_client.containers.create.return_value = _fake_container(exit_code=1, stderr=b"No matching distribution")
        _with_egress_network(fake_client, "sandbox_egress")

        with patch("docker.from_env", return_value=fake_client):
            result = await sandbox.install_packages(["pandas"], "python")

        assert result.success is False
        fake_client.containers.create.assert_called_once()


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


class TestCollectWorkingDirInputs:
    def test_collects_nested_files_excluding_reserved_dirs(self, tmp_path) -> None:
        working_dir = tmp_path / "wd"
        (working_dir / "input" / "artifacts").mkdir(parents=True)
        (working_dir / "input" / "artifacts" / "photo.png").write_bytes(b"photo-bytes")
        (working_dir / "notes.txt").write_bytes(b"note-bytes")
        for reserved in ("_src", "output", "deps_python", "node_modules"):
            reserved_dir = working_dir / reserved
            reserved_dir.mkdir()
            (reserved_dir / "ignored.txt").write_bytes(b"should not appear")

        files = _collect_working_dir_inputs(str(working_dir))

        assert files == {
            "input/artifacts/photo.png": b"photo-bytes",
            "notes.txt": b"note-bytes",
        }

    def test_empty_working_dir_yields_empty_map(self, tmp_path) -> None:
        working_dir = tmp_path / "wd"
        working_dir.mkdir()
        assert _collect_working_dir_inputs(str(working_dir)) == {}


class TestTarFiles:
    def test_round_trips_nested_paths_and_content(self) -> None:
        files = {"input/artifacts/photo.png": b"photo-bytes", "notes.txt": b"note-bytes"}
        tar_bytes = _tar_files(files)

        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            assert set(tar.getnames()) == set(files)
            for name, content in files.items():
                assert tar.extractfile(name).read() == content

    def test_empty_map_yields_empty_tar(self) -> None:
        tar_bytes = _tar_files({})
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            assert tar.getnames() == []


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
