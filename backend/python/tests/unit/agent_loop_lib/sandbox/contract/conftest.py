"""Backend fixtures for the Liskov contract suite.

Every `CodingSandboxBackend` implementation must pass `test_backend_contract`
unchanged. A backend is exercised here in whichever forms the environment
supports:

    local        — always
    docker       — always, against a fake `DockerClientProvider`
    docker_real  — only with a reachable daemon (`-m docker`)
    e2b          — always, against a mocked `AsyncSandbox`

Skips live in the fixtures, never in the test bodies: the test bodies are
the contract, and a body that can skip itself is a body that can quietly
stop asserting anything.
"""

from __future__ import annotations

import io
import os
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.agent_loop_lib.sandbox.coding.docker_client import DockerClientProvider
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox

# Contract tests run real sandbox processes (node, npm install, containers).
# `pytest-timeout`'s default signal method fires inside asyncio's selector on
# macOS/Linux and breaks them, so the whole directory uses the thread method.
pytestmark = pytest.mark.timeout(180, method="thread")

# An image that already carries node + python3 + a baked node_modules.
# `deployment/sandbox/Dockerfile` builds `pipeshub/sandbox:latest`; a local
# build may be tagged differently, hence the override.
DOCKER_TEST_IMAGE = os.environ.get(
    "SANDBOX_TEST_IMAGE", "pipeshubai/pipeshub-sandbox:local",
)


def docker_available() -> tuple[bool, str]:
    """Whether a real daemon is reachable AND has the test image."""
    try:
        import docker
    except ImportError:
        return False, "docker SDK not installed"
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        return False, f"docker daemon unreachable: {type(exc).__name__}"
    try:
        client.images.get(DOCKER_TEST_IMAGE)
    except Exception:
        return False, f"image {DOCKER_TEST_IMAGE!r} not present"
    finally:
        client.close()
    return True, ""


def _tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf.read()


class FakeDockerProvider:
    """A `DockerClientProvider` whose containers are simulated.

    Runs the requested command's *effects* rather than the command: enough
    for the contract to be meaningful (exit codes, stdout, artifacts,
    installs) without a daemon, so the container backend's own path is
    covered on machines that have no Docker.
    """

    def __init__(self) -> None:
        self.client = MagicMock()
        self.run_calls: list[dict] = []
        self.networks_ensured: list[str] = []
        self._script_output: dict[str, bytes] = {}
        self.client.containers.create.side_effect = self._create

    def _create(self, **kwargs):
        self.run_calls.append(kwargs)
        container = MagicMock()
        container.wait.return_value = {"StatusCode": self._exit_code(kwargs)}
        container.logs.side_effect = lambda **kw: (
            self._stdout(kwargs) if kw.get("stdout") else b""
        )
        archives = {"/output": self._script_output, "/src": {}}
        container.get_archive.side_effect = lambda path: (
            iter([_tar(archives.get(path, {}))]), {},
        )
        return container

    def _command_text(self, kwargs) -> str:
        command = kwargs.get("command") or []
        return " ".join(command) if isinstance(command, list) else str(command)

    def _exit_code(self, kwargs) -> int:
        return 1 if "FAIL_MARKER" in self._command_text(kwargs) else 0

    def _stdout(self, kwargs) -> bytes:
        return b"hello\n"

    async def run_blocking(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def ensure_image(self, image: str) -> bool:
        return True

    async def pull_image(self, image: str) -> None:
        return None

    async def ensure_egress_network(self, name: str) -> str:
        self.networks_ensured.append(name)
        return name

    async def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


@pytest.fixture
def short_tmp_dir() -> Iterator[str]:
    """A working directory under the system temp root, not pytest's.

    `tsx` opens a Unix domain socket for its IPC inside the sandbox dir, and
    `sockaddr_un` caps the path at ~104 bytes. pytest's `tmp_path` is already
    ~100 chars (`/private/var/folders/../pytest-of-user/pytest-N/<test name>/`),
    so a run under it fails with `listen EINVAL` — an artefact of the harness,
    not of the sandbox, which uses a short `gettempdir()` path in production.
    """
    path = tempfile.mkdtemp(prefix="alcs-t-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
async def local_sandbox(short_tmp_dir):
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    sb = LocalCodingSandbox(working_dir=os.path.join(short_tmp_dir, "l"))
    await sb.provision()
    yield sb
    await sb.destroy()


@pytest.fixture
async def docker_real_sandbox(tmp_path):
    available, reason = docker_available()
    if not available:
        pytest.skip(reason)
    sb = DockerCodingSandbox(
        working_dir=str(tmp_path / "docker-real"),
        image=DOCKER_TEST_IMAGE,
        image_node_modules="/home/sandbox/node_modules",
        provider=DockerClientProvider(max_workers=4),
    )
    await sb.provision()
    yield sb
    await sb.destroy()
    sb._provider.close()


@pytest.fixture(params=["local", "docker_real"])
async def backend(request, short_tmp_dir, tmp_path):
    """Every backend the environment can actually run.

    `docker_real` skips without a daemon; `local` skips without node. At
    least one always runs, and CI with Docker runs both.
    """
    if request.param == "local":
        if shutil.which("node") is None:
            pytest.skip("node not on PATH")
        # Short path: see `short_tmp_dir` for why pytest's own is unusable.
        sb = LocalCodingSandbox(working_dir=os.path.join(short_tmp_dir, "l"))
    else:
        available, reason = docker_available()
        if not available:
            pytest.skip(reason)
        sb = DockerCodingSandbox(
            working_dir=str(tmp_path / "docker-real"),
            image=DOCKER_TEST_IMAGE,
            image_node_modules="/home/sandbox/node_modules",
            provider=DockerClientProvider(max_workers=4),
        )
    await sb.provision()
    yield sb
    await sb.destroy()
    provider = getattr(sb, "_provider", None)
    if provider is not None:
        provider.close()
