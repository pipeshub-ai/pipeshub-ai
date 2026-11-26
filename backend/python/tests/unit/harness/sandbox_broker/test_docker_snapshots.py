"""P5.9b on the Docker broker (runc, the release tools image): DK-1..DK-8.

The sandbox's ``HOME`` is its ``/tmp`` tmpfs, which Docker's archive API cannot see, so these run the real
capture and restore (``tar`` inside the sandbox) against real containers. Each broker has its own label
namespace and ``pxh-p59b-`` prefix; every container carries the owner label; teardown removes by namespace only.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, cast

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker, DockerSandboxHandle
from harness_core.engines.controller import MODEL_ROUTE, placeholder_for
from harness_core.engines.errors import SnapshotRefused, SnapshotUnavailable
from harness_core.engines.snapshots import (
    SNAPSHOT_MISSING,
    SNAPSHOT_SANDBOX_GONE,
    SNAPSHOT_SECRET,
    SNAPSHOT_TOO_LARGE,
    ObjectStoreSandboxSnapshots,
    SandboxSnapshots,
    SnapshotPolicy,
    capture,
)
from harness_core.ids import RandomIds
from harness_core.logs import HarnessLog
from harness_core.sandbox.pool import WarmPoolConfig, shape_of
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.sandbox_snapshots import SnapshotCaptureContract
from harness_core.workspace.memory import InMemoryBinaryObjectStore, InMemoryKeyProvider
from harness_core.workspace.sealing import SealedObjectStore
from harness_core.workspace.types import ObjectScope
from harness_engines.release import release_allowlist, release_snapshot_policy
from tests.unit.harness.sandbox_broker.conftest import cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.sandbox.ports import SandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

IMAGE = next(r for r in release_allowlist() if r.version.kind == "opencode").image
RESOURCES = Resources(cpus=0.5, memory_mb=512, pids=128, scratch_mb=64)
OWNER = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "P5.9b"}
LEASE = LeaseRef("worker-p59b", 1)
SESSION_A = ObjectScope("org-p59b", "ws:session-a")
SESSION_B = ObjectScope("org-p59b", "ws:session-b")
POLICY = release_snapshot_policy(max_bytes=1024 * 1024)
GITHUB = "ghp_" + "Zq81xY" * 6
_WRITE = 'mkdir -p "$(dirname "$0")" && cat > "$0" && chmod "$1" "$0"'


def _spec() -> SandboxSpec:
    return SandboxSpec(
        image_digest=IMAGE,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=RESOURCES,
        mounts=(MountSpec("/workspace"),),
        labels=OWNER,
    )


async def run(sandbox: SandboxHandle, argv: Sequence[str], data: bytes | None = None) -> tuple[int, bytes]:
    process = await sandbox.exec(argv, env={"MPLCONFIGDIR": "/tmp/matplotlib"}, stdin=data is not None)
    if data is not None:
        await process.write(data)
        await process.close_stdin()

    async def read(stream: AsyncIterator[bytes]) -> bytes:
        return b"".join([chunk async for chunk in stream])

    out, _ = await asyncio.gather(read(process.stdout), read(process.stderr))
    return await process.wait(timeout_s=120), out


class DockerCapture:
    """``CaptureEnv`` over one Docker broker with the release tools image."""

    def __init__(self, provider: DockerClientProvider) -> None:
        self.provider = provider
        self.namespace = f"io.pipeshub.p59b.{uuid.uuid4().hex[:8]}"
        self.prefix = "pxh-p59b-" + self.namespace.rsplit(".", 1)[1]
        self.broker: DockerSandboxBroker | None = None

    async def open(self, warm_pool: WarmPoolConfig | None = None) -> DockerSandboxBroker:
        self.broker = await DockerSandboxBroker.create(
            provider=self.provider,
            clock=ManualClock(),
            ids=RandomIds(),
            allowed_images=frozenset({IMAGE}),
            label_namespace=self.namespace,
            volume_prefix=self.prefix,
            warm_pool=warm_pool,
            memory_mb=8 * 1024 if warm_pool is not None else None,
        )
        return self.broker

    async def sandbox(self) -> SandboxHandle:
        broker = self.broker or await self.open()
        return await broker.acquire(_spec(), lease=LEASE, idempotency_key=f"p59b-{uuid.uuid4().hex}")

    async def write(self, sandbox: SandboxHandle, path: str, data: bytes, *, mode: int = 0o644) -> None:
        code, _ = await run(sandbox, ["sh", "-c", _WRITE, path, f"{mode:o}"], data)
        assert code == 0, path

    async def mkdir(self, sandbox: SandboxHandle, path: str) -> None:
        assert (await run(sandbox, ["mkdir", "-p", path]))[0] == 0

    async def read(self, sandbox: SandboxHandle, path: str) -> bytes | None:
        code, out = await run(sandbox, ["cat", path])
        return out if code == 0 else None

    async def mode(self, sandbox: SandboxHandle, path: str) -> int:
        code, out = await run(sandbox, ["stat", "-c", "%a", path])
        assert code == 0
        return int(out.strip(), 8)

    async def kill(self, sandbox: SandboxHandle) -> None:
        container = cast("DockerSandboxHandle", sandbox).container_id
        await self.provider.run_blocking(self.provider.client.api.kill, container)

    async def close(self) -> None:
        if self.broker is not None:
            await self.broker.aclose()
        await self.provider.run_blocking(cleanup, self.provider, self.namespace, self.prefix)


@pytest.fixture
async def docker_capture(provider: DockerClientProvider) -> AsyncIterator[DockerCapture]:
    if not await provider.ensure_image(IMAGE):
        pytest.skip(f"{IMAGE} is not built on this daemon")
    env = DockerCapture(provider)
    try:
        yield env
    finally:
        await env.close()


class TestDockerCapture(SnapshotCaptureContract):
    """DK-8: SC-1..SC-5 on Docker."""

    @pytest.fixture
    def capture_env(self, docker_capture: DockerCapture) -> DockerCapture:
        return docker_capture


class Sealed:
    """The session-keyed store PipesHub binds: ``SealedObjectStore`` over one object store, a key per session."""

    def __init__(self) -> None:
        self.objects = InMemoryBinaryObjectStore()
        self.keys = InMemoryKeyProvider()
        self.store = ObjectStoreSandboxSnapshots(SealedObjectStore(self.objects, self.keys))

    def snapshots(self, scope: ObjectScope, *, policy: SnapshotPolicy = POLICY, secrets: tuple[str, ...] = ()) -> SandboxSnapshots:
        return SandboxSnapshots(
            store=self.store, policy=policy, scope=scope, secrets=lambda: secrets, log=HarnessLog(logging.getLogger("p59b.docker"))
        )


async def _warm_tool_state(env: DockerCapture, sandbox: SandboxHandle) -> None:
    """What a turn leaves in ``HOME``: matplotlib's font cache (built by a first plot) and a tool's cache file."""
    code, _ = await run(sandbox, ["python3", "-c", "import matplotlib.pyplot as p; p.figure(); p.savefig('/tmp/x.png')"])
    assert code == 0
    await env.write(sandbox, "/tmp/.cache/tool/index.json", b'{"warm": true}\n')


async def test_dk1_a_snapshot_survives_a_sandbox_kill_and_dk7_the_dead_one_gives_none(
    docker_capture: DockerCapture,
) -> None:
    sealed = Sealed()
    first = await docker_capture.sandbox()
    await _warm_tool_state(docker_capture, first)
    taken = await sealed.snapshots(SESSION_A).take(first)
    assert taken is not None
    await docker_capture.kill(first)
    with pytest.raises(SnapshotRefused) as gone:
        _ = await capture(first, POLICY)
    assert gone.value.reason_code == SNAPSHOT_SANDBOX_GONE
    assert await sealed.snapshots(SESSION_A).take(first) is None
    second = await docker_capture.sandbox()
    assert await docker_capture.read(second, "/tmp/.cache/tool/index.json") is None
    assert await sealed.snapshots(SESSION_A).restore_into(second, taken)
    assert await docker_capture.read(second, "/tmp/.cache/tool/index.json") == b'{"warm": true}\n'
    font_cache = (await run(second, ["sh", "-c", "ls /tmp/matplotlib"]))[1]
    assert b"fontlist" in font_cache
    # Only sealed bytes reached the object store.
    assert all(b"tmp/.cache" not in blob and b"warm" not in blob for blob in sealed.objects.stored_bytes())


async def test_dk2_a_warm_pool_sandbox_is_bound_and_then_restored(docker_capture: DockerCapture) -> None:
    shape = shape_of(_spec())
    assert shape is not None
    broker = await docker_capture.open(
        WarmPoolConfig(ceiling=1, shapes=(shape,), fraction=1.0, reserve_fraction=0.0, labels=OWNER)
    )
    sealed = Sealed()
    cold = await docker_capture.sandbox()
    await _warm_tool_state(docker_capture, cold)
    taken = await sealed.snapshots(SESSION_A).take(cold)
    assert taken is not None
    await broker.release(cold)
    assert await broker.fill_warm() >= 0
    warm_ids = broker.warm_ids()
    assert warm_ids
    bound = await docker_capture.sandbox()
    assert bound.id in warm_ids
    assert await sealed.snapshots(SESSION_A).restore_into(bound, taken)
    assert await docker_capture.read(bound, "/tmp/.cache/tool/index.json") == b'{"warm": true}\n'


async def test_dk3_another_session_never_reads_the_snapshot(docker_capture: DockerCapture) -> None:
    sealed = Sealed()
    sandbox = await docker_capture.sandbox()
    await _warm_tool_state(docker_capture, sandbox)
    taken = await sealed.snapshots(SESSION_A).take(sandbox)
    assert taken is not None
    with pytest.raises(SnapshotUnavailable):
        _ = await sealed.store.load(SESSION_B, taken)
    other = await docker_capture.sandbox()
    assert not await sealed.snapshots(SESSION_B).restore_into(other, taken)
    assert await docker_capture.read(other, "/tmp/.cache/tool/index.json") is None


@pytest.mark.parametrize("planted", ["pattern", "placeholder"])
async def test_dk4_a_secret_planted_in_home_refuses_the_snapshot(
    planted: str, docker_capture: DockerCapture, caplog: pytest.LogCaptureFixture
) -> None:
    sealed = Sealed()
    placeholder = placeholder_for(b"p59b-placeholder-key-0123456789", "run-1.a1", MODEL_ROUTE)
    secret = GITHUB if planted == "pattern" else placeholder
    sandbox = await docker_capture.sandbox()
    await _warm_tool_state(docker_capture, sandbox)
    await docker_capture.write(sandbox, "/tmp/.config/gh/hosts.yml", f"github.com:\n  oauth_token: {secret}\n".encode())
    with caplog.at_level(logging.DEBUG):
        assert await sealed.snapshots(SESSION_A, secrets=(placeholder,)).take(sandbox) is None
    assert sealed.objects.object_count == 0
    refused = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.snapshot.refused"]
    assert [r.harness["reasonCode"] for r in refused] == [SNAPSHOT_SECRET]  # type: ignore[attr-defined]
    for record in caplog.records:
        text = record.getMessage() + repr(getattr(record, "harness", {}))
        assert secret not in text and "hosts.yml" not in text and "/tmp/.config" not in text


async def test_dk5_erasing_the_session_makes_its_snapshots_unreadable(docker_capture: DockerCapture) -> None:
    sealed = Sealed()
    sandbox = await docker_capture.sandbox()
    await _warm_tool_state(docker_capture, sandbox)
    taken = await sealed.snapshots(SESSION_A).take(sandbox)
    assert taken is not None
    assert SESSION_A.partition is not None
    await sealed.keys.destroy(SESSION_A.tenant_id, SESSION_A.partition)
    with pytest.raises(SnapshotUnavailable) as refused:
        _ = await sealed.store.load(SESSION_A, taken)
    assert refused.value.reason_code == SNAPSHOT_MISSING


async def test_dk6_a_snapshot_over_its_cap_is_refused(docker_capture: DockerCapture, caplog: pytest.LogCaptureFixture) -> None:
    sealed = Sealed()
    sandbox = await docker_capture.sandbox()
    await docker_capture.write(sandbox, "/tmp/.cache/big.bin", b"\x5a" * (2 * POLICY.max_bytes + 1))
    with caplog.at_level(logging.WARNING):
        assert await sealed.snapshots(SESSION_A).take(sandbox) is None
    refused = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.snapshot.refused"]
    assert [r.harness["reasonCode"] for r in refused] == [SNAPSHOT_TOO_LARGE]  # type: ignore[attr-defined]
    assert sealed.objects.object_count == 0
    # The capture was stopped, the sandbox was not.
    assert (await run(sandbox, ["sh", "-c", "exit 0"]))[0] == 0
