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
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.agent_loop_lib.sandbox.coding.docker_client import DockerClientProvider
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.agent_loop_lib.sandbox.coding.base import CodingSandboxBackend

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


# CI's Docker lane sets it: a missing daemon or sandbox image then fails the lane instead of skipping it, so a
# broken lane cannot read green. Unset (a developer's machine), those tests skip.
REQUIRE_DOCKER_ENV = "PXH_REQUIRE_DOCKER"


def docker_required() -> bool:
    return os.environ.get(REQUIRE_DOCKER_ENV, "").strip().lower() in {"1", "true", "yes"}


def skip_without_docker() -> None:
    """Skips the calling test or fixture without a daemon and the sandbox image, or fails it when required."""
    available, reason = docker_available()
    if available:
        return
    if docker_required():
        pytest.fail(f"{REQUIRE_DOCKER_ENV} is set, but the Docker lane cannot run: {reason}")
    pytest.skip(reason)


# The security gate's gVisor stage sets it (C6.42): a daemon without runsc then fails the gVisor lane instead of
# skipping it, so a host that lost its runtime cannot pass the stage on runc alone.
REQUIRE_GVISOR_ENV = "PXH_REQUIRE_GVISOR"
NO_RUNSC = "runsc is not a runtime of this Docker daemon"


def skip_without_runsc() -> None:
    """The gVisor lane's skip on a daemon without runsc, or its failure when the gate requires gVisor."""
    if os.environ.get(REQUIRE_GVISOR_ENV, "").strip().lower() in {"1", "true", "yes"}:
        pytest.fail(f"{REQUIRE_GVISOR_ENV} is set, but {NO_RUNSC}")
    pytest.skip(NO_RUNSC)


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

    async def image_labels(self, _: str) -> dict[str, str]:
        return {}

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
    skip_without_docker()
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


async def _broker_real_sandbox(working_dir: str) -> CodingSandboxBackend:
    """`BrokerCodingSandbox` over an in-process Docker broker in its own label namespace (LG-1)."""
    from app.agent_loop_lib.sandbox.coding.broker import BrokerCodingSandbox
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.clock import SystemClock
    from harness_core.ids import RandomIds

    suffix = uuid.uuid4().hex[:8]
    provider = DockerClientProvider(max_workers=8)
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=SystemClock(),
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=f"io.pipeshub.p51test.{suffix}",
        volume_prefix=f"p51test-{suffix}",
    )

    async def source() -> DockerSandboxBroker:
        return broker

    sandbox = BrokerCodingSandbox(
        broker=source,
        working_dir=working_dir,
        image=DOCKER_TEST_IMAGE,
        image_node_modules="/home/sandbox/node_modules",
        provider=provider,
    )
    # A run cut off by a test timeout never reaches its release; teardown reaps it.
    sandbox.test_broker = broker  # type: ignore[attr-defined]
    return sandbox


@pytest.fixture(params=["local", "docker_real", "broker_real"])
async def backend(request, short_tmp_dir, tmp_path):
    """Every backend the environment can actually run.

    `docker_real` and `broker_real` skip without a daemon; `local` skips
    without node. At least one always runs, and CI with Docker runs all.
    """
    if request.param == "local":
        if shutil.which("node") is None:
            pytest.skip("node not on PATH")
        # Short path: see `short_tmp_dir` for why pytest's own is unusable.
        sb = LocalCodingSandbox(working_dir=os.path.join(short_tmp_dir, "l"))
    elif request.param == "broker_real":
        skip_without_docker()
        sb = await _broker_real_sandbox(str(tmp_path / "broker-real"))
    else:
        skip_without_docker()
        sb = DockerCodingSandbox(
            working_dir=str(tmp_path / "docker-real"),
            image=DOCKER_TEST_IMAGE,
            image_node_modules="/home/sandbox/node_modules",
            provider=DockerClientProvider(max_workers=4),
        )
    await sb.provision()
    yield sb
    await sb.destroy()
    test_broker = getattr(sb, "test_broker", None)
    if test_broker is not None:
        await test_broker.reap(labels={}, older_than_s=0)
    provider = getattr(sb, "_provider", None)
    if provider is not None:
        provider.close()
