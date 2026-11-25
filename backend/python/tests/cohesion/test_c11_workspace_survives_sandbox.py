"""C-11 (first half of gate G1): a session's files survive the sandbox that held them.

P3.2 store (sealed objects) → P5.5 sync → P5.1 sandbox → engine-like writes →
autosave (collect + commit) → more writes the engine never saved → the sandbox
is killed → a fresh sandbox materializes the head. The fresh tree equals the
autosaved revision: same paths, bytes and modes; the unsaved writes are gone;
engine configuration stayed in the store and out of both sandboxes.

Lanes: the fake broker with both mount strategies over the in-memory store,
the fake over Node's workspace routes (modes on the wire, the session key from
Node), and the Docker broker (runc; gVisor where ``runsc`` exists) with both
strategies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxHandle
from harness_core.sandbox.types import LeaseRef, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace_sync import FakeEngineHands, tree
from harness_core.testing.sandbox import FakeSandboxBroker
from harness_core.workspace import (
    EXECUTABLE_MODE,
    SYMLINK_MODE,
    ChangeSet,
    ObjectScope,
    WorkspaceFile,
)
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryKeyProvider,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.sync import (
    TAR,
    VOLUME,
    SyncStrategies,
    TarWorkspaceSync,
    WorkspaceSaver,
)
from tests.unit.harness.sandbox_broker.conftest import (
    cleanup_namespace,
    clock,
    docker_broker,
    namespace,
    provider,
    runtime_mode,
    sandbox_env,
    volume_prefix,
)
from tests.unit.harness.sandbox_broker.test_workspace_sync_docker import (
    DockerEngineHands,
    data_volume,
    volume_broker,
)
from tests.unit.harness.test_node_journal_contract import node_server
from tests.unit.harness.test_node_workspace_contract import (  # pyright: ignore[reportPrivateUsage]
    ORG,
    SESSION,
    _World,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle
    from harness_core.testing.contracts.workspace_sync import EngineHands
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.sync import SyncStrategy
    from tests.unit.harness.sandbox_broker.conftest import DockerContractEnv
    from tests.unit.harness.test_node_journal_contract import (
        _NodeServer,  # pyright: ignore[reportPrivateUsage]
    )

__all__ = [
    "cleanup_namespace",
    "clock",
    "data_volume",
    "docker_broker",
    "namespace",
    "node_server",
    "provider",
    "runtime_mode",
    "sandbox_env",
    "volume_broker",
    "volume_prefix",
]

ROOT = "/workspace"
SEED = {
    "README.md": b"# project\n",
    "src/main.py": b"print('main')\n",
    "src/util.py": b"def util(): ...\n",
    "data/table.csv": b"a,b\n1,2\n",
    "scripts/build.sh": b"#!/bin/sh\necho build\n",
    ".opencode/plugin/evil.ts": b"export default () => { throw new Error('ran') }",
    ".claude/settings.json": b'{"hooks": {"PreToolUse": []}}',
}


@dataclass
class Lane:
    store: WorkspaceStore
    scope: ObjectScope
    objects: BinaryObjectStore
    object_scope: ObjectScope
    workspace_id: str
    broker: SandboxBroker
    spec: Callable[[], SandboxSpec]
    hands: EngineHands
    kill: Callable[[SandboxHandle], Awaitable[None]]


async def _seed(lane: Lane) -> None:
    upserts: dict[str, WorkspaceFile] = {}
    for path, data in SEED.items():
        ref = await lane.objects.put(lane.object_scope, data)
        mode = EXECUTABLE_MODE if path.endswith(".sh") else 0o100644
        upserts[path] = WorkspaceFile(path, ref, "application/octet-stream", mode)
    link = await lane.objects.put(lane.object_scope, b"src/main.py")
    upserts["main"] = WorkspaceFile("main", link, "inode/symlink", SYMLINK_MODE)
    head = await lane.store.head(lane.scope, lane.workspace_id)
    _ = await lane.store.commit(
        lane.scope, lane.workspace_id, base_revision=head.revision, changes=ChangeSet(upserts), reason="seed"
    )


async def _acquire(lane: Lane) -> SandboxHandle:
    return await lane.broker.acquire(lane.spec(), lease=LeaseRef("c11", 1), idempotency_key=f"c11-{uuid.uuid4().hex}")


async def scenario(lane: Lane) -> None:
    sync = TarWorkspaceSync(objects=lane.objects, scope=lane.object_scope)
    saver = WorkspaceSaver(sync=sync, store=lane.store, scope=lane.scope)
    await _seed(lane)
    first = await _acquire(lane)
    state = await sync.materialize(first, await lane.store.head(lane.scope, lane.workspace_id), root=ROOT)
    hands = lane.hands
    await hands.write(first, f"{ROOT}/src/main.py", b"print('edited')\n")
    await hands.write(first, f"{ROOT}/src/new_module.py", b"NEW = True\n")
    await hands.write(first, f"{ROOT}/out/chart.svg", b"<svg/>", 0o640)
    await hands.write(first, f"{ROOT}/data/table.csv", SEED["data/table.csv"], 0o755)
    await hands.remove(first, f"{ROOT}/src/util.py")
    await hands.symlink(first, f"{ROOT}/out/latest", "chart.svg")
    await hands.write(first, f"{ROOT}/.opencode/plugin/written.ts", b"engine-made")
    saved = await saver.save(first, state, reason="autosave")
    assert saved.manifest is not None
    autosaved = await tree(first, ROOT)
    autosaved.pop(".opencode/plugin/written.ts")
    await hands.write(first, f"{ROOT}/unsaved.txt", b"lost on purpose")
    await hands.write(first, f"{ROOT}/README.md", b"# unsaved edit\n")
    await lane.kill(first)

    second = await _acquire(lane)
    try:
        head = await lane.store.head(lane.scope, lane.workspace_id)
        assert head.revision == saved.manifest.revision
        fresh = await sync.materialize(second, head, root=ROOT)
        restored = await tree(second, ROOT)
        assert restored == autosaved
        assert restored["data/table.csv"][0] == EXECUTABLE_MODE
        assert restored["out/chart.svg"] == (0o100640, b"<svg/>")
        assert restored["out/latest"] == (SYMLINK_MODE, b"chart.svg")
        assert "src/util.py" not in restored
        assert "unsaved.txt" not in restored
        assert {".opencode/plugin/evil.ts", ".claude/settings.json"} <= set(head.files)
        assert not any(path.startswith((".opencode", ".claude")) for path in restored)
        assert set(fresh.hidden) == {".opencode/plugin/evil.ts", ".claude/settings.json"}
        assert (await sync.collect(second, fresh)).changes.is_empty
    finally:
        await lane.broker.release(second)


def _memory_stores() -> tuple[InMemoryWorkspaceStore, SealedObjectStore]:
    clock = ManualClock(datetime(2026, 10, 3, tzinfo=UTC))
    return InMemoryWorkspaceStore(clock=clock), SealedObjectStore(InMemoryBinaryObjectStore(), InMemoryKeyProvider())


def _fake_lane(strategy: SyncStrategy, store: WorkspaceStore, objects: BinaryObjectStore, org: str, session: str) -> Lane:
    broker = FakeSandboxBroker(clock=ManualClock(), persistent_mounts=strategy is VOLUME)

    def spec() -> SandboxSpec:
        mount = strategy.mount(ROOT, source=f"{org}/{session}/workspace")
        return SandboxSpec("fake@sha256:" + "0" * 64, RuntimeMode.DOCKER_HARDENED, mounts=(mount,))

    async def kill(sandbox: SandboxHandle) -> None:
        _ = await broker.reap(labels={}, older_than_s=0)
        assert sandbox.id not in broker.sandboxes

    return Lane(
        store, ObjectScope(org), objects, ObjectScope(org, f"ws:{session}"), f"ws:{session}",
        broker, spec, FakeEngineHands(), kill,
    )


@pytest.mark.parametrize("strategy", [TAR, VOLUME], ids=["tar", "volume"])
async def test_c11_on_the_fake_broker(strategy: SyncStrategy) -> None:
    store, objects = _memory_stores()
    lane = _fake_lane(strategy, store, objects, "org-c11", "session-c11")
    assert SyncStrategies().for_broker(await lane.broker.health()) is strategy
    await scenario(lane)


async def test_c11_through_nodes_workspace_routes(node_server: _NodeServer) -> None:
    attempt = await _World(node_server).attempt("run-c11", ORG)
    objects = SealedObjectStore(InMemoryBinaryObjectStore(), attempt.keys)
    await scenario(_fake_lane(VOLUME, attempt.store, objects, ORG, SESSION))


def _docker_lane(
    broker: DockerSandboxBroker, env: DockerContractEnv, provider: DockerClientProvider, strategy: SyncStrategy
) -> Lane:
    store, objects = _memory_stores()
    source = f"c11-{uuid.uuid4().hex[:8]}/session/workspace"

    def spec() -> SandboxSpec:
        base = env.spec()
        return SandboxSpec(
            base.image_digest, base.mode, base.resources, base.egress, (strategy.mount(ROOT, source=source),), base.labels
        )

    async def kill(sandbox: SandboxHandle) -> None:
        # No release: the container goes as in a crash, so no copy-back and no final collect.
        assert isinstance(sandbox, DockerSandboxHandle)
        _ = await provider.run_blocking(provider.client.api.remove_container, sandbox.container_id, force=True)

    return Lane(
        store, ObjectScope("org-c11"), objects, ObjectScope("org-c11", "ws:session-c11"), "ws:session-c11",
        broker, spec, DockerEngineHands(), kill,
    )


@pytest.mark.docker
@pytest.mark.timeout(600, method="thread")
async def test_c11_on_docker_with_a_scratch_mount(
    docker_broker: DockerSandboxBroker, sandbox_env: DockerContractEnv, provider: DockerClientProvider
) -> None:
    assert SyncStrategies().for_broker(await docker_broker.health()) is TAR
    await scenario(_docker_lane(docker_broker, sandbox_env, provider, TAR))


@pytest.mark.docker
@pytest.mark.timeout(600, method="thread")
async def test_c11_on_docker_with_the_sessions_volume(
    volume_broker: DockerSandboxBroker, sandbox_env: DockerContractEnv, provider: DockerClientProvider
) -> None:
    assert SyncStrategies().for_broker(await volume_broker.health()) is VOLUME
    await scenario(_docker_lane(volume_broker, sandbox_env, provider, VOLUME))
