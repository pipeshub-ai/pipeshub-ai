"""Fixtures for the Docker broker against a real daemon (integration lane, ``-m docker``).

Skips live here, never in test bodies: no daemon or no test image skips the
Docker lane, no ``runsc`` skips the gVisor lane. Every broker gets its own
label namespace and volume prefix, so parallel runs never reap each other,
and teardown removes whatever a test left behind.
"""

from __future__ import annotations

import contextlib
import uuid
import warnings
from typing import TYPE_CHECKING, override

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker, probe
from app.harness.sandbox.docker_client import DockerClientProvider
from app.harness.sandbox.docker_egress import DockerEgressWiring
from harness_core.ids import RandomIds
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.addresses import DEFAULT_SUBNET_POOL, SubnetPool
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.types import MountSpec, Resources, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import WORK, SandboxContractEnv
from tests.support import docker_session
from tests.unit.agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    skip_without_docker,
    skip_without_runsc,
)
from tests.unit.harness.sandbox_broker.egress_stack import (
    HELPER_IMAGE,
    DockerEgressContractEnv,
    EgressStack,
    start_stack,
    stop_stack,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping

# Per-sandbox egress subnets in tests (P5.4); a clash with another network on the shared daemon moves on.
EGRESS_TEST_POOL = DEFAULT_SUBNET_POOL

AGENT_LABEL = {"pipeshub.agent": "P5.1"}

_PRINT = "import sys; sys.stdout.write(sys.argv[1]); sys.stderr.write(sys.argv[2]); sys.exit(int(sys.argv[3]))"
_ECHO_SERVER = """
import socket, sys
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", int(sys.argv[1]))); srv.listen(4)
while True:
    conn, _ = srv.accept()
    while True:
        data = conn.recv(65536)
        if not data:
            break
        conn.sendall(data)
    conn.close()
"""


class DockerContractEnv(SandboxContractEnv):
    def __init__(self, clock: ManualClock, mode: RuntimeMode, namespace: str) -> None:
        self._clock = clock
        self._mode = mode
        self.reserved_label = namespace + "."
        self.unsupported_mode = RuntimeMode.MICROVM
        self.short_wait_s = 1.0

    @override
    def spec(self, *, labels: Mapping[str, str] | None = None, mode: RuntimeMode | None = None) -> SandboxSpec:
        return SandboxSpec(
            image_digest=DOCKER_TEST_IMAGE,
            mode=mode or self._mode,
            resources=Resources(cpus=0.5, memory_mb=256, pids=64, scratch_mb=64),
            mounts=(MountSpec(WORK),),
            labels={**AGENT_LABEL, **(labels or {})},
        )

    @override
    def argv_print(self, stdout: str, stderr: str, code: int) -> list[str]:
        return ["python3", "-c", _PRINT, stdout, stderr, str(code)]

    @override
    def argv_cat(self) -> list[str]:
        return ["cat"]

    @override
    def argv_printenv(self, name: str) -> list[str]:
        return ["printenv", name]

    @override
    def argv_sleep(self) -> list[str]:
        return ["sleep", "1000"]

    @override
    def argv_echo_server(self, port: int) -> list[str]:
        return ["python3", "-c", _ECHO_SERVER, str(port)]

    @override
    def argv_read_file(self, path: str) -> list[str]:
        return ["cat", path]

    @override
    def argv_write_file(self, path: str, text: str) -> list[str]:
        return ["/bin/sh", "-c", 'printf %s "$1" > "$0"', path, text]

    @override
    def argv_remove_file(self, path: str) -> list[str]:
        return ["rm", "-f", path]

    @override
    def advance(self, seconds: float) -> None:
        _ = self._clock.advance(seconds)


def cleanup(provider: DockerClientProvider, namespace: str, volume_prefix: str) -> None:
    """The namespace's containers and volumes. A daemon still removing a container after ``REMOVE_WAIT_S`` does not
    fail the test whose teardown this is (BUGS-A, rx5): the volumes, and the networks ``cleanup_networks`` would
    remove, are retried when the process exits."""
    api = provider.client.api
    slow = [
        info["Id"]
        for info in api.containers(all=True, filters={"label": [f"{namespace}.managed=1"]})
        if not docker_session.remove_container(api, info["Id"])
    ]
    if slow:
        warnings.warn(f"{len(slow)} container(s) of {namespace} still being removed; retried at exit", stacklevel=2)
        _later[namespace] = volume_prefix
        return
    for volume in (api.volumes(filters={"label": [f"{namespace}.managed=1"]}) or {}).get("Volumes") or []:
        api.remove_volume(volume["Name"], force=True)
    for volume in (api.volumes() or {}).get("Volumes") or []:
        if volume["Name"].startswith(volume_prefix):
            api.remove_volume(volume["Name"], force=True)


# Namespaces (and their volume prefixes) a slow daemon kept ``cleanup`` from finishing.
_later: dict[str, str] = {}


def _cleanup_later() -> None:
    """What a slow daemon kept ``cleanup`` from removing, once more; nothing raises at exit."""
    if not _later:
        return
    provider = DockerClientProvider()
    try:
        for namespace, volume_prefix in sorted(_later.items()):
            with contextlib.suppress(Exception):
                cleanup(provider, namespace, volume_prefix)
            with contextlib.suppress(Exception):
                for info in provider.client.api.networks(filters={"label": [f"{namespace}.managed=1"]}) or []:
                    provider.client.api.remove_network(info["Id"])
    finally:
        provider.close()


# Not ``atexit`` alone: an xdist worker leaves through ``os._exit`` (SBXLEAK).
docker_session.at_exit(_cleanup_later)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def namespace() -> str:
    return f"io.pipeshub.p51test.{uuid.uuid4().hex[:8]}"


@pytest.fixture
def volume_prefix(namespace: str) -> str:
    return "p51test-" + namespace.rsplit(".", 1)[1]


@pytest.fixture(params=[RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR], ids=["runc", "gvisor"])
def runtime_mode(request: pytest.FixtureRequest) -> RuntimeMode:
    return request.param


@pytest.fixture
async def provider() -> AsyncIterator[DockerClientProvider]:
    skip_without_docker()
    docker_provider = DockerClientProvider(max_workers=16)
    yield docker_provider
    docker_provider.close()


@pytest.fixture
async def cleanup_namespace(
    provider: DockerClientProvider, namespace: str, volume_prefix: str
) -> AsyncIterator[None]:
    yield
    await provider.run_blocking(cleanup, provider, namespace, volume_prefix)


async def skip_unless_mode_runs(provider: DockerClientProvider, mode: RuntimeMode) -> None:
    """The gVisor lane's skip (or failure under PXH_REQUIRE_GVISOR) for any fixture that builds a broker for ``mode``."""
    if mode is RuntimeMode.GVISOR and RuntimeMode.GVISOR not in (await probe(provider)).modes():
        skip_without_runsc()


@pytest.fixture
async def docker_broker(
    provider: DockerClientProvider,
    clock: ManualClock,
    namespace: str,
    volume_prefix: str,
    runtime_mode: RuntimeMode,
    cleanup_namespace: None,
) -> AsyncIterator[DockerSandboxBroker]:
    await skip_unless_mode_runs(provider, runtime_mode)
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=namespace,
        volume_prefix=volume_prefix,
    )
    yield broker


@pytest.fixture
def sandbox_env(clock: ManualClock, runtime_mode: RuntimeMode, namespace: str) -> DockerContractEnv:
    return DockerContractEnv(clock, runtime_mode, namespace)


@pytest.fixture(scope="module")
def egress_stack() -> Iterator[EgressStack]:
    """One proxy, gateway and target per module (P5.4); skipped like the Docker lane, plus the helper image."""
    import docker

    skip_without_docker()
    client = docker.from_env()
    try:
        client.images.get(HELPER_IMAGE)
    except Exception:
        client.close()
        pytest.skip(f"image {HELPER_IMAGE!r} not present")
    stack = start_stack(client)
    yield stack
    stop_stack(stack)
    client.close()


@pytest.fixture
def egress_namespace() -> str:
    return f"io.pipeshub.p54test.{uuid.uuid4().hex[:8]}"


@pytest.fixture
def egress_prefix(egress_namespace: str) -> str:
    return "pxh-p54-" + egress_namespace.rsplit(".", 1)[1]


@pytest.fixture
async def egress_bindings(egress_stack: EgressStack) -> AsyncIterator[RemoteEgressBindings]:
    client = RemoteEgressBindings(egress_stack.control_url, StaticBearerAuth(egress_stack.control_token))
    yield client
    await client.aclose()


def egress_wiring(
    provider: DockerClientProvider, clock: ManualClock, stack: EgressStack, bindings: RemoteEgressBindings, prefix: str
) -> DockerEgressWiring:
    return DockerEgressWiring(
        provider=provider,
        clock=clock,
        proxy_container=stack.proxy,
        bindings=bindings,
        pool=SubnetPool(EGRESS_TEST_POOL),
        network_prefix=f"{prefix}-net-",
    )


@pytest.fixture
async def egress_docker_broker(
    provider: DockerClientProvider,
    clock: ManualClock,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
    egress_namespace: str,
    egress_prefix: str,
    runtime_mode: RuntimeMode,
) -> AsyncIterator[DockerSandboxBroker]:
    if runtime_mode is RuntimeMode.GVISOR and RuntimeMode.GVISOR not in (await probe(provider)).modes():
        skip_without_runsc()
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=egress_namespace,
        volume_prefix=egress_prefix,
        egress=egress_wiring(provider, clock, egress_stack, egress_bindings, egress_prefix),
    )
    yield broker
    await provider.run_blocking(cleanup, provider, egress_namespace, egress_prefix)
    await provider.run_blocking(cleanup_networks, provider, egress_namespace, egress_stack.proxy)


def cleanup_networks(provider: DockerClientProvider, namespace: str, proxy: str) -> None:
    api = provider.client.api
    for info in api.networks(filters={"label": [f"{namespace}.managed=1"]}) or []:
        with contextlib.suppress(Exception):
            api.disconnect_container_from_network(proxy, info["Id"], force=True)
        try:
            api.remove_network(info["Id"])
        except Exception:
            # A sandbox the daemon is still removing holds its network: ``_cleanup_later`` removes it at exit.
            if namespace not in _later:
                raise


@pytest.fixture
def docker_egress_env(egress_stack: EgressStack, egress_bindings: RemoteEgressBindings) -> DockerEgressContractEnv:
    return DockerEgressContractEnv(egress_stack, egress_bindings)
