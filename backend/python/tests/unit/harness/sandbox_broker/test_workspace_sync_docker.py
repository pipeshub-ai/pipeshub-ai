"""DS-1..DS-3: ``TarWorkspaceSync`` on the Docker broker, both mount strategies (runc lane; gVisor where ``runsc`` exists)."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from harness_core.ids import RandomIds
from harness_core.sandbox.types import PERSISTENT_MOUNTS, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace_sync import (
    CountingObjectStore,
    PersistentWorkspaceSyncContract,
    WorkspaceSyncContract,
    acquire,
    commit,
)
from harness_core.testing.sandbox.contract import run
from harness_core.workspace import EXECUTABLE_MODE, ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryKeyProvider,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.sync import (
    HARD_LINK,
    SPECIAL_FILE,
    SYMLINK_ESCAPES,
    TAR,
    VOLUME,
    SyncStrategies,
    TarWorkspaceSync,
)
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

from .conftest import AGENT_LABEL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.sandbox.types import RuntimeMode
    from harness_core.workspace.sync import SyncStrategy

    from .conftest import DockerContractEnv

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

ROOT = "/workspace"
_WRITE = (
    "import base64, os, sys\n"
    "path, data, mode = sys.argv[1], base64.b64decode(sys.argv[2]), int(sys.argv[3], 8)\n"
    "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
    "open(path, 'wb').write(data)\n"
    "os.chmod(path, mode)\n"
)


async def _ok(sandbox: SandboxHandle, argv: Sequence[str]) -> bytes:
    out, err, code = await run(await sandbox.exec(list(argv), env={}))
    assert code == 0, err
    return out


class DockerEngineHands:
    """``EngineHands`` as processes of the sandbox's own user."""

    async def write(self, sandbox: SandboxHandle, path: str, data: bytes, mode: int = 0o644) -> None:
        _ = await _ok(sandbox, ["python3", "-c", _WRITE, path, base64.b64encode(data).decode(), oct(mode)[2:]])

    async def symlink(self, sandbox: SandboxHandle, path: str, target: str) -> None:
        _ = await _ok(sandbox, ["sh", "-c", 'mkdir -p "$(dirname "$1")" && ln -sfn "$2" "$1"', "sh", path, target])

    async def remove(self, sandbox: SandboxHandle, path: str) -> None:
        _ = await _ok(sandbox, ["rm", "-rf", "--", path])

    async def mkdir(self, sandbox: SandboxHandle, path: str) -> None:
        _ = await _ok(sandbox, ["mkdir", "-p", path])


class DockerSyncHarness:
    def __init__(self, broker: DockerSandboxBroker, env: DockerContractEnv, strategy: SyncStrategy) -> None:
        clock = ManualClock(datetime(2026, 10, 3, tzinfo=UTC))
        self.broker = broker
        self.env = env
        self.strategy = strategy
        self.scope = ObjectScope("tenant-a")
        self.object_scope = ObjectScope("tenant-a", "ws:session-sync")
        self.objects = CountingObjectStore(SealedObjectStore(InMemoryBinaryObjectStore(), InMemoryKeyProvider()))
        self.store = InMemoryWorkspaceStore(clock=clock)
        self.sync = TarWorkspaceSync(objects=self.objects, scope=self.object_scope, clock=clock)
        self.hands = DockerEngineHands()
        self.root = ROOT
        self.source = f"p55-{uuid.uuid4().hex[:8]}/session-sync/workspace"

    def spec(self) -> SandboxSpec:
        base = self.env.spec()
        mount = self.strategy.mount(ROOT, source=self.source)
        return SandboxSpec(base.image_digest, base.mode, base.resources, base.egress, (mount,), base.labels)


@pytest.fixture
def data_volume(provider: DockerClientProvider, volume_prefix: str) -> Iterator[str]:
    name = f"{volume_prefix}-data"
    provider.client.api.create_volume(name=name, labels=dict(AGENT_LABEL))
    yield name
    provider.client.api.remove_volume(name, force=True)


@pytest.fixture
async def volume_broker(
    provider: DockerClientProvider,
    clock: ManualClock,
    namespace: str,
    volume_prefix: str,
    runtime_mode: RuntimeMode,
    cleanup_namespace: None,
    data_volume: str,
    docker_broker: DockerSandboxBroker,
) -> AsyncIterator[DockerSandboxBroker]:
    del runtime_mode, cleanup_namespace, docker_broker  # the gVisor skip and teardown come with them
    yield await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=namespace,
        volume_prefix=volume_prefix,
        data_volume=data_volume,
    )


class DockerCases:
    """DS-2, DS-3: what only a real container shows, on whichever strategy the subclass runs."""

    async def test_ds2_materialized_files_belong_to_the_sandbox_user(self, harness: DockerSyncHarness) -> None:
        h = harness
        manifest = await commit(h, {"a.txt": b"a", "deep/b.sh": b"echo b"}, modes={"deep/b.sh": EXECUTABLE_MODE})
        sandbox = await acquire(h)
        try:
            state = await h.sync.materialize(sandbox, manifest, root=ROOT)
            out = await _ok(sandbox, ["stat", "-c", "%n %u:%g %a", f"{ROOT}/a.txt", f"{ROOT}/deep", f"{ROOT}/deep/b.sh"])
            assert out.decode().split("\n")[:3] == [
                f"{ROOT}/a.txt 1000:1000 644",
                f"{ROOT}/deep 1000:1000 755",
                f"{ROOT}/deep/b.sh 1000:1000 755",
            ]
            assert await _ok(sandbox, [f"{ROOT}/deep/b.sh"]) == b"b\n"
            _ = await _ok(sandbox, ["sh", "-c", f"echo more >> {ROOT}/a.txt && rm {ROOT}/deep/b.sh && rmdir {ROOT}/deep"])
            changes = (await h.sync.collect(sandbox, state)).changes
            assert set(changes.upserts) == {"a.txt"}
            assert changes.deletes == frozenset({"deep/b.sh"})
        finally:
            await h.broker.release(sandbox)

    async def test_ds3_real_links_fifos_and_hard_links(self, harness: DockerSyncHarness) -> None:
        h = harness
        manifest = await commit(h, {"a.txt": b"shared"})
        sandbox = await acquire(h)
        try:
            state = await h.sync.materialize(sandbox, manifest, root=ROOT)
            script = (
                f"cd {ROOT} && ln -s /etc/passwd passwd && ln -s ../../etc/hosts up && ln -s a.txt inside"
                " && mkfifo pipe && ln a.txt hard.txt"
            )
            _ = await _ok(sandbox, ["sh", "-c", script])
            h.objects.reset()
            collected = await h.sync.collect(sandbox, state)
            assert collected.skipped == {"passwd": SYMLINK_ESCAPES, "up": SYMLINK_ESCAPES, "pipe": SPECIAL_FILE}
            upserts = collected.changes.upserts
            assert set(upserts) == {"inside", "hard.txt"}
            assert upserts["hard.txt"].content == state.manifest.files["a.txt"].content
            assert h.objects.puts == 1
            assert HARD_LINK not in collected.skipped.values()
            await h.sync.materialize(sandbox, manifest, root=ROOT)
            listing = await _ok(sandbox, ["ls", "-A", ROOT])
            assert listing.split() == [b"a.txt"]
        finally:
            await h.broker.release(sandbox)


class TestTarStrategyOnDocker(WorkspaceSyncContract, DockerCases):
    @pytest.fixture
    async def harness(  # type: ignore[override]
        self, docker_broker: DockerSandboxBroker, sandbox_env: DockerContractEnv
    ) -> DockerSyncHarness:
        strategy = SyncStrategies().for_broker(await docker_broker.health())
        assert strategy is TAR
        return DockerSyncHarness(docker_broker, sandbox_env, strategy)


class TestVolumeStrategyOnDocker(PersistentWorkspaceSyncContract, DockerCases):
    @pytest.fixture
    async def harness(  # type: ignore[override]
        self, volume_broker: DockerSandboxBroker, sandbox_env: DockerContractEnv
    ) -> DockerSyncHarness:
        health = await volume_broker.health()
        assert health.detail[PERSISTENT_MOUNTS] == "true"
        assert SyncStrategies().for_broker(health) is VOLUME
        return DockerSyncHarness(volume_broker, sandbox_env, VOLUME)
