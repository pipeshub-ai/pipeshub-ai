"""SEC-ISO-06 and SEC-ISO-07 (C2.18, C6.05): resource exhaustion inside real broker sandboxes stays inside them,
on runc and on gVisor, with no inner sandbox.

A fork bomb meets the pids limit, a memory balloon the memory limit, a disk or inode fill of a tmpfs its own size,
an fd fill its descriptor limit, a CPU spin its CPU quota, and a fill of a persistent workspace (the host's disk) the
broker's quota guard. Each attempt is bounded on its own too (a deadline, a byte ceiling), and a sibling sandbox and
the broker stay healthy throughout.

gVisor differs where its kernel is the sandbox's: it shows no cgroup files inside, so the limits are read from the
host's cgroup; the spec's process limit is the Sentry's ``RLIMIT_NPROC`` (``host_pids_limit``); the host's OOM killer
sees one process, the Sentry, so a balloon ends the whole sandbox rather than one process in it; and its tmpfs has
no inode count, so an inode fill is bounded by the sandbox's memory, which the Sentry's own objects are charged to.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import TYPE_CHECKING, Any

import docker.errors
import docker.types
import pytest

from app.harness.sandbox.docker_broker import (
    GVISOR_HOST_PIDS_BASE,
    GVISOR_HOST_PIDS_PER_TASK,
    TMPFS_INODES_PER_MB,
    WORKSPACE_QUOTA_EXCEEDED,
    DockerSandboxBroker,
)
from harness_core.ids import RandomIds
from harness_core.sandbox.errors import SandboxNotFound, SandboxRefusal
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.sandbox.contract import WORK, run

from .conftest import AGENT_LABEL, DOCKER_TEST_IMAGE, skip_unless_mode_runs

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from app.harness.sandbox.docker_broker import DockerSandboxHandle
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.testing.clock import ManualClock

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

LEASE = LeaseRef("w-sec-iso-06", 1)
PIDS, MEMORY_MB, SCRATCH_MB = 64, 256, 64
QUOTA_MB = 64
ENOSPC, EAGAIN, ENOMEM, EMFILE, ENFILE = 28, 11, 12, 24, 23
CPU_SPINNERS = 4

_FORK_BOMB = r"""
import os, sys, time
deadline = time.time() + float(sys.argv[1])
refused = 0
while time.time() < deadline:
    try:
        if os.fork() == 0:
            continue
    except OSError:
        refused += 1
        if refused == 1:
            try:
                open("/tmp/fork-refused", "w").close()
            except OSError:
                pass
        time.sleep(0.01)
os._exit(0)
"""

# Starts sleepers (exec'd, so no copy of the interpreter each) until the kernel refuses one, and reports the count.
_SPAWN = r"""
import os, signal, sys, time
kids, error = [], None
while len(kids) < int(sys.argv[1]):
    try:
        kids.append(os.posix_spawn("/bin/sleep", ["sleep", "60"], {}))
    except OSError as exc:
        error = exc.errno
        break
print(len(kids), error)
for kid in kids:
    os.kill(kid, signal.SIGKILL)
for kid in kids:
    os.waitpid(kid, 0)
"""

# Opens descriptors up to the hard limit it may raise itself to, or the ceiling.
_FDS = r"""
import os, resource, sys
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
fds, error = [], 0
while len(fds) < int(sys.argv[1]):
    try:
        fds.append(os.open("/dev/null", os.O_RDONLY))
    except OSError as exc:
        error = exc.errno
        break
print(hard, len(fds), error)
"""

_SPIN = r"""
import os, sys, time
deadline = time.time() + float(sys.argv[1])
for _ in range(int(sys.argv[2]) - 1):
    if os.fork() == 0:
        break
while time.time() < deadline:
    pass
"""

_BALLOON = r"""
held = []
while len(held) < 4096:
    held.append(bytearray(16 << 20))
print("never-capped")
"""

_FILL = r"""
import errno, sys
path, ceiling = sys.argv[1], int(sys.argv[2]) << 20
written = 0
try:
    with open(path, "wb") as f:
        while written < ceiling:
            f.write(b"\0" * (1 << 20))
            f.flush()
            written += 1 << 20
    print("ok", written)
except OSError as exc:
    print(exc.errno, written)
"""

_INODES = r"""
import os, sys
root, ceiling = sys.argv[1], int(sys.argv[2])
os.makedirs(root, exist_ok=True)
made = 0
try:
    while made < ceiling:
        os.close(os.open(f"{root}/{made}", os.O_CREAT | os.O_WRONLY, 0o600))
        made += 1
    print("ok", made)
except OSError as exc:
    print(exc.errno, made)
except MemoryError:
    # gVisor's tmpfs has no inode count: the Sentry's memory runs out first, raised here before the kernel says ENOMEM.
    print(12, made)
"""

# Writes 1 MiB files to a persistent folder, slowly enough for the guard to look, and stops on its own at the ceiling.
_WORKSPACE_FILL = r"""
import os, sys, time
root, ceiling = sys.argv[1], int(sys.argv[2])
for i in range(ceiling):
    with open(f"{root}/fill-{i}", "wb") as f:
        f.write(b"\1" * (1 << 20))
    time.sleep(0.03)
print("wrote", ceiling)
"""


def _spec(
    *, mounts: Sequence[MountSpec] = (MountSpec(WORK),), mode: RuntimeMode, image: str = DOCKER_TEST_IMAGE,
    memory_mb: int = MEMORY_MB,
) -> SandboxSpec:
    return SandboxSpec(
        image_digest=image,
        mode=mode,
        resources=Resources(cpus=0.5, memory_mb=memory_mb, pids=PIDS, scratch_mb=SCRATCH_MB),
        mounts=tuple(mounts),
        labels={**AGENT_LABEL, "scenario": "SEC-ISO-06"},
    )


async def _out(handle: DockerSandboxHandle, argv: Sequence[str], *, timeout_s: float = 60) -> tuple[str, int]:
    out, err, code = await run(await handle.exec(list(argv), env={}), timeout_s=timeout_s)
    return (out + err).decode("utf-8", "replace").strip(), code


async def _alive(handle: DockerSandboxHandle) -> bool:
    try:
        out, code = await _out(handle, ["echo", "alive"], timeout_s=30)
    except SandboxNotFound:
        return False
    return (out, code) == ("alive", 0)


def _host_cgroup(container_id: str, name: str) -> str:
    """A limit as the host enforces it (gVisor shows the sandbox no cgroup files): the systemd driver's scope, or
    the cgroupfs driver's folder."""
    for folder in (f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope", f"/sys/fs/cgroup/docker/{container_id}"):
        with contextlib.suppress(FileNotFoundError):
            with open(f"{folder}/{name}") as f:
                return f.read().strip()
    pytest.fail(f"no host cgroup for {container_id[:12]}: a cgroup v2 host is this lane's floor")


async def _limit(handle: DockerSandboxHandle, mode: RuntimeMode, name: str) -> str:
    if mode is RuntimeMode.GVISOR:
        return _host_cgroup(handle.container_id, name)
    out, code = await _out(handle, ["cat", f"/sys/fs/cgroup/{name}"])
    assert code == 0, out
    return out


@pytest.fixture
async def pair(docker_broker: DockerSandboxBroker, runtime_mode: RuntimeMode) -> AsyncIterator[tuple[Any, Any]]:
    box = await docker_broker.acquire(_spec(mode=runtime_mode), lease=LEASE, idempotency_key=f"a-{uuid.uuid4().hex}")
    sibling = await docker_broker.acquire(_spec(mode=runtime_mode), lease=LEASE, idempotency_key=f"b-{uuid.uuid4().hex}")
    yield box, sibling
    for handle in (box, sibling):
        await docker_broker.release(handle)


async def test_sec_iso_06_processes_stop_at_the_pids_limit_with_eagain_and_the_sandbox_lives(
    pair: tuple[DockerSandboxHandle, DockerSandboxHandle], runtime_mode: RuntimeMode
) -> None:
    """Under gVisor the host's cgroup at the spec's pids once let 16 of 64 start, refused with ENOMEM by a Sentry that
    could not clone (R31-GVISO): the limit is the guest's now, as under runc."""
    box, sibling = pair
    out, code = await _out(box, ["python3", "-c", _SPAWN, str(PIDS * 4)], timeout_s=120)
    started, error = out.split()
    assert code == 0 and error == str(EAGAIN), out
    assert PIDS - 8 <= int(started) < PIDS, out
    assert await _alive(box)
    assert await _alive(sibling)
    if runtime_mode is RuntimeMode.GVISOR:
        hard = GVISOR_HOST_PIDS_BASE + GVISOR_HOST_PIDS_PER_TASK * PIDS
        assert _host_cgroup(box.container_id, "pids.max") == str(hard)
        assert int(_host_cgroup(box.container_id, "pids.peak")) < hard


async def test_sec_iso_06_a_fork_bomb_meets_the_pids_limit_and_only_its_sandbox_waits(
    docker_broker: DockerSandboxBroker, pair: tuple[DockerSandboxHandle, DockerSandboxHandle],
    runtime_mode: RuntimeMode,
) -> None:
    _, sibling = pair
    # 64 copies of the interpreter outgrow 256 MB under gVisor, whose OOM ends the whole sandbox (the balloon case):
    # here the bomb is to meet the process limit, so its sandbox has the memory for it.
    gvisor = runtime_mode is RuntimeMode.GVISOR
    box = await docker_broker.acquire(
        _spec(mode=runtime_mode, memory_mb=MEMORY_MB * 4 if gvisor else MEMORY_MB),
        lease=LEASE, idempotency_key=f"bomb-{uuid.uuid4().hex}",
    )
    try:
        if gvisor:
            nproc = await _out(box, ["python3", "-c", "import resource; print(*resource.getrlimit(resource.RLIMIT_NPROC))"])
            assert nproc == (f"{PIDS} {PIDS}", 0)
        else:
            assert await _limit(box, runtime_mode, "pids.max") == str(PIDS)
        bomb = asyncio.ensure_future(_out(box, ["python3", "-c", _FORK_BOMB, "8"], timeout_s=90))
        await asyncio.sleep(3)
        assert await _alive(sibling)
        assert (await docker_broker.health()).ok
        _ = await bomb
        # Every forked copy ends at the deadline; the sandbox is usable again and saw the limit.
        for _ in range(30):
            if await _out(box, ["test", "-e", "/tmp/fork-refused"]) == ("", 0):
                break
            await asyncio.sleep(1)
        assert await _out(box, ["test", "-e", "/tmp/fork-refused"]) == ("", 0)
        assert await _alive(box)
    finally:
        await docker_broker.release(box)


async def test_sec_iso_06_a_memory_balloon_is_ended_by_the_memory_limit(
    docker_broker: DockerSandboxBroker, pair: tuple[DockerSandboxHandle, DockerSandboxHandle],
    runtime_mode: RuntimeMode,
) -> None:
    box, sibling = pair
    assert await _limit(box, runtime_mode, "memory.max") == str(MEMORY_MB << 20)
    assert await _limit(box, runtime_mode, "memory.swap.max") == "0"
    assert not await box.memory_exhausted()
    try:
        out, code = await _out(box, ["python3", "-c", _BALLOON], timeout_s=120)
    except SandboxNotFound:
        out, code = "", 137
    assert "never-capped" not in out
    assert await _alive(sibling)
    assert (await docker_broker.health()).ok
    # R31GVISO-FU-3: the broker tells an OOM end from any other on both runtimes (the container's OOMKilled), which
    # a run then fails with as ``out_of_memory``; the sibling's limit was never met.
    assert await box.memory_exhausted() is (code != 1), (code, out[-200:])
    assert not await sibling.memory_exhausted()
    if runtime_mode is RuntimeMode.GVISOR:
        # The host's OOM killer sees one process, the Sentry: the whole sandbox ends (its cgroup with it), or the
        # Sentry refuses the allocation first; nothing outside the sandbox is touched.
        assert code == 1 or not await _alive(box), (code, out[-200:])
    else:
        assert code in (137, -9, 1), (code, out[-200:])
        events, _ = await _out(box, ["cat", "/sys/fs/cgroup/memory.events"])
        assert code == 1 or "oom_kill 0" not in events, events


@pytest.mark.parametrize("folder", ["/tmp", WORK])
async def test_sec_iso_06_a_disk_fill_of_a_tmpfs_stops_at_its_size(
    pair: tuple[DockerSandboxHandle, DockerSandboxHandle], folder: str
) -> None:
    box, sibling = pair
    out, code = await _out(box, ["python3", "-c", _FILL, f"{folder}/fill", str(SCRATCH_MB * 4)])
    error, written = out.split()
    assert code == 0 and int(error) == ENOSPC, out
    assert int(written) <= SCRATCH_MB << 20
    assert await _alive(sibling)
    if folder != "/tmp":
        assert await _alive(box)


async def test_sec_iso_06_an_inode_fill_of_a_tmpfs_ends_in_enospc_not_an_oom(
    docker_broker: DockerSandboxBroker, pair: tuple[DockerSandboxHandle, DockerSandboxHandle],
    runtime_mode: RuntimeMode,
) -> None:
    box, sibling = pair
    ceiling = SCRATCH_MB * TMPFS_INODES_PER_MB * 2
    try:
        out, code = await _out(box, ["python3", "-c", _INODES, "/tmp/inodes", str(ceiling)], timeout_s=120)
    except SandboxNotFound:
        out, code = "", 137
    if runtime_mode is RuntimeMode.GVISOR:
        # gVisor's tmpfs keeps no inode count: its inodes are the Sentry's own heap, bounded only by the sandbox's
        # memory limit. The fill is refused, or the host's OOM killer ends the whole sandbox when that heap meets the
        # limit (the exec's WaitPID then ends in EOF), as the balloon case; nothing outside the sandbox is touched.
        if code == 0 and len(out.split()) == 2:
            assert out.split()[0] in ("ok", str(ENOSPC), str(ENOMEM)), out
            assert int(_host_cgroup(box.container_id, "memory.peak")) <= MEMORY_MB << 20
            assert await _alive(box)
        else:
            assert await box.memory_exhausted(), (code, out[-400:])
    else:
        assert code == 0 and len(out.split()) == 2, (code, out[-400:])
        error, made = out.split()
        assert int(error) == ENOSPC, out
        assert int(made) <= SCRATCH_MB * TMPFS_INODES_PER_MB
    assert await _alive(sibling)
    assert (await docker_broker.health()).ok


async def test_sec_iso_06_an_fd_fill_meets_its_descriptor_limit(
    docker_broker: DockerSandboxBroker, pair: tuple[DockerSandboxHandle, DockerSandboxHandle],
) -> None:
    box, sibling = pair
    ceiling = 4 << 20
    out, code = await _out(box, ["python3", "-c", _FDS, str(ceiling)], timeout_s=120)
    hard, opened, error = (int(part) for part in out.split())
    assert code == 0, out
    assert error in (EMFILE, ENFILE, ENOMEM) and opened < min(hard, ceiling), out
    assert await _alive(box)
    assert await _alive(sibling)
    assert (await docker_broker.health()).ok


async def test_sec_iso_06_a_cpu_spin_is_held_to_its_quota_and_the_sibling_answers(
    docker_broker: DockerSandboxBroker, pair: tuple[DockerSandboxHandle, DockerSandboxHandle],
    runtime_mode: RuntimeMode,
) -> None:
    box, sibling = pair
    quota, period = _host_cgroup(box.container_id, "cpu.max").split()
    cpus = int(quota) / int(period)
    assert cpus == 0.5
    before = _cpu_usage_s(box.container_id)
    started = time.monotonic()
    spin = asyncio.ensure_future(_out(box, ["python3", "-c", _SPIN, "6", str(CPU_SPINNERS)], timeout_s=60))
    await asyncio.sleep(2)
    assert await _alive(sibling)
    _ = await spin
    used, wall = _cpu_usage_s(box.container_id) - before, time.monotonic() - started
    # Four spinners would take four CPUs; the quota holds the sandbox near half of one (slack for the period edges).
    assert used <= cpus * wall * 1.25 + 0.5, (used, wall)
    assert await _alive(box)
    assert (await docker_broker.health()).ok
    del runtime_mode


def _cpu_usage_s(container_id: str) -> float:
    stat = dict(line.split() for line in _host_cgroup(container_id, "cpu.stat").splitlines())
    return int(stat["usage_usec"]) / 1e6


@pytest.fixture
def data_volume(provider: DockerClientProvider, volume_prefix: str) -> Any:  # noqa: ANN401
    name = f"{volume_prefix}-data"
    provider.client.api.create_volume(name=name, labels=dict(AGENT_LABEL))
    yield name
    # The namespace's cleanup removes it with the broker's own volumes (its prefix) when it runs first.
    with contextlib.suppress(docker.errors.NotFound):
        provider.client.api.remove_volume(name, force=True)


def _on_store(provider: DockerClientProvider, volume: str, script: str) -> tuple[int, str]:
    container = provider.client.containers.run(
        DOCKER_TEST_IMAGE, ["sh", "-c", script], detach=True, network_mode="none", labels=dict(AGENT_LABEL),
        mounts=[docker.types.Mount("/data", volume, type="volume", read_only=True)],
    )
    try:
        code = container.wait(timeout=60)["StatusCode"]
        return code, container.logs().decode().strip()
    finally:
        container.remove(force=True)


@pytest.fixture
async def quota_broker(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    data_volume: str, cleanup_namespace: None, runtime_mode: RuntimeMode,
) -> AsyncIterator[DockerSandboxBroker]:
    await skip_unless_mode_runs(provider, runtime_mode)
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
        allowed_images=frozenset({DOCKER_TEST_IMAGE}), data_volume=data_volume,
        workspace_quota_mb=QUOTA_MB, workspace_check_s=1.0,
    )
    yield broker
    await broker.aclose()


async def _refused(handle: DockerSandboxHandle) -> str:
    with pytest.raises(SandboxRefusal) as refused:
        await handle.exec(["true"], env={})
    return refused.value.reason


async def test_sec_iso_06_a_persistent_workspace_fill_is_ended_at_its_quota_and_the_disk_given_back(
    quota_broker: DockerSandboxBroker, provider: DockerClientProvider, data_volume: str, runtime_mode: RuntimeMode,
) -> None:
    source = f"org-{uuid.uuid4().hex[:6]}/ses-1/ws"
    other = f"org-{uuid.uuid4().hex[:6]}/ses-2/ws"
    box = await quota_broker.acquire(
        _spec(mounts=(MountSpec("/ws", source=source),), mode=runtime_mode), lease=LEASE, idempotency_key="fill"
    )
    sibling = await quota_broker.acquire(
        _spec(mounts=(MountSpec("/ws", source=other),), mode=runtime_mode), lease=LEASE, idempotency_key="other"
    )
    try:
        assert await _out(sibling, ["sh", "-c", "echo kept > /ws/a.txt"]) == ("", 0)
        out, code = await _out(box, ["python3", "-c", _WORKSPACE_FILL, "/ws", str(QUOTA_MB * 4)], timeout_s=120)
        assert "wrote" not in out and code != 0, (code, out)
        assert await _refused(box) == WORKSPACE_QUOTA_EXCEEDED
        for _ in range(60):
            if _on_store(provider, data_volume, f"test -e /data/{source}")[0] != 0:
                break
            await asyncio.sleep(0.5)
        assert _on_store(provider, data_volume, f"test -e /data/{source}")[0] != 0
        code, used = _on_store(provider, data_volume, "du -sk /data | cut -f1")
        assert code == 0 and int(used) < QUOTA_MB * 4 * 1024
        assert await _out(sibling, ["cat", "/ws/a.txt"]) == ("kept", 0)
        assert (await quota_broker.health()).ok
    finally:
        for handle in (box, sibling):
            await quota_broker.release(handle)
    # The next sandbox of the session starts with an empty folder, which the workspace sync fills again.
    again = await quota_broker.acquire(
        _spec(mounts=(MountSpec("/ws", source=source),), mode=runtime_mode), lease=LEASE, idempotency_key="again"
    )
    try:
        assert await _out(again, ["sh", "-c", "ls -A /ws | wc -l"]) == ("0", 0)
    finally:
        await quota_broker.release(again)


async def test_sec_iso_06_a_workspace_hidden_from_the_measure_fails_closed(
    quota_broker: DockerSandboxBroker, runtime_mode: RuntimeMode,
) -> None:
    source = f"org-{uuid.uuid4().hex[:6]}/ses-1/ws"
    box = await quota_broker.acquire(
        _spec(mounts=(MountSpec("/ws", source=source),), mode=runtime_mode), lease=LEASE, idempotency_key="hide"
    )
    try:
        assert await _out(box, ["sh", "-c", "mkdir /ws/hid && echo x > /ws/hid/f && chmod 000 /ws/hid"]) == ("", 0)
        for _ in range(20):
            await asyncio.sleep(0.5)
            try:
                _ = await box.exec(["true"], env={})
            except SandboxRefusal as refused:
                assert refused.reason == WORKSPACE_QUOTA_EXCEEDED
                break
        else:
            pytest.fail("a folder the guard could not measure was not ended")
    finally:
        await quota_broker.release(box)


# BusyBox (Alpine: opencode, Codex, Gemini) and GNU coreutils (Claude Code): the guard measures in the sandbox's own
# image, and a BusyBox du with no --inodes once failed every measure closed, ending each engine run within seconds.
@pytest.mark.parametrize("kind", ["opencode", "claude_code"])
async def test_sec_iso_06_the_guard_measures_a_workspace_in_each_engine_image(
    kind: str, provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    data_volume: str, cleanup_namespace: None, runtime_mode: RuntimeMode,
) -> None:
    from tests.unit.harness.engines.lane_releases import lane_release

    image = lane_release(kind).image
    try:
        provider.client.images.get(image)
    except docker.errors.ImageNotFound:
        pytest.skip(f"{image} is not built")
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
        allowed_images=frozenset({image}), data_volume=data_volume,
        workspace_quota_mb=QUOTA_MB, workspace_check_s=3600.0,
    )
    try:
        mounts = (MountSpec("/ws", source=f"org-{uuid.uuid4().hex[:6]}/ses-1/ws"),)
        box = await broker.acquire(_spec(mounts=mounts, mode=runtime_mode, image=image), lease=LEASE, idempotency_key=kind)
        try:
            assert await _out(box, ["sh", "-c", "mkdir -p /ws/src && echo x > /ws/src/a && echo y > /ws/b"]) == ("", 0)
            for _ in range(3):
                assert await broker.check_workspaces() == 1
            assert await _alive(box)
            assert await _out(box, ["sh", "-c", "mkdir /ws/hid && chmod 000 /ws/hid"]) == ("", 0)
            _ = await broker.check_workspaces()
            assert await _refused(box) == WORKSPACE_QUOTA_EXCEEDED
        finally:
            await broker.release(box)
    finally:
        await broker.aclose()


# SEC-A-03: writes from several processes at once, each fsynced, so the page cache cannot hide the device's rate.
_FAST_WRITERS = r"""
import os, sys, time
folder, writers, mb = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
start = time.monotonic()
kids = []
for n in range(writers):
    pid = os.fork()
    if pid == 0:
        fd = os.open(f"{folder}/fast-{n}", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        block = b"\1" * (1 << 20)
        for _ in range(mb):
            os.write(fd, block)
            os.fsync(fd)
        os.close(fd)
        os._exit(0)
    kids.append(pid)
for pid in kids:
    os.waitpid(pid, 0)
print(f"{time.monotonic() - start:.2f}")
"""
WRITE_MBPS = 8


@pytest.fixture
async def bounded_broker(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    data_volume: str, cleanup_namespace: None, runtime_mode: RuntimeMode,
) -> AsyncIterator[DockerSandboxBroker]:
    import os

    from app.harness.sandbox.docker_broker import WriteBound

    await skip_unless_mode_runs(provider, runtime_mode)
    device = (os.environ.get("SANDBOX_DATA_DEVICE") or "").strip()
    if not device:
        pytest.skip("SANDBOX_DATA_DEVICE names no disk under the daemon's volumes")
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), label_namespace=namespace, volume_prefix=volume_prefix,
        allowed_images=frozenset({DOCKER_TEST_IMAGE}), data_volume=data_volume,
        workspace_quota_mb=QUOTA_MB * 4, write_bound=WriteBound(device, WRITE_MBPS),
    )
    yield broker
    await broker.aclose()


async def test_sec_a_03_the_kernel_caps_how_fast_a_sandbox_fills_its_workspace(
    bounded_broker: DockerSandboxBroker, runtime_mode: RuntimeMode,
) -> None:
    """Parallel writers to the persistent workspace share the sandbox's cap: 48 MiB at 8 MiB/s takes about six
    seconds whatever the SSD could do, so the overshoot between two measures is the cap times the interval."""
    source = f"org-{uuid.uuid4().hex[:6]}/ses-1/ws"
    box = await bounded_broker.acquire(
        _spec(mounts=(MountSpec("/ws", source=source),), mode=runtime_mode), lease=LEASE, idempotency_key="fast"
    )
    try:
        out, code = await _out(box, ["python3", "-c", _FAST_WRITERS, "/ws", "4", "12"], timeout_s=120)
        assert code == 0, out
        floor_s = 48 / WRITE_MBPS * 0.7
        assert float(out) >= floor_s, f"48 MiB written in {out}s: faster than the {WRITE_MBPS} MiB/s cap"
    finally:
        await bounded_broker.release(box)
