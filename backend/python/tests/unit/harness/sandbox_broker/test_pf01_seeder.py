"""PF-01 (C7.01): seeding a read-only mount costs no container create on a run's start.

Every engine turn seeds the ``/pipeshub`` tool folder, a run that pins skills seeds those too, and an over-cap
knowledge result is shelved into the folder mid-turn. Each seed used to create, use and remove a helper container
(1.9 s p50 on a loaded host, ~170 ms quiet, P5.9a). Now a sandbox with seedable mounts is made with one seeder: a
container never started that mounts those scratch volumes read-write, made beside the sandbox (in the background
refill for a warm one). A seed is one archive write into it; release removes it before the volumes.

S1-S2 the seeder is made with the sandbox, cold and warm, and a seed creates nothing. S3 a sandbox without one
(made by a broker before this change) is still seeded. S4 a seeder whose sandbox is gone is reaped before the
volumes it holds.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from harness_core.ids import RandomIds
from harness_core.sandbox.pool import WarmPoolConfig
from harness_core.sandbox.streams import iter_bytes
from harness_core.sandbox.types import LeaseRef, MountSpec, RuntimeMode
from harness_core.testing.sandbox.contract import run, tar_of, with_mounts
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.sandbox.types import SandboxSpec
    from harness_core.testing.clock import ManualClock
    from tests.unit.harness.sandbox_broker.conftest import DockerContractEnv

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

LEASE = LeaseRef("w-pf01", 1)
SKILLS = "/skills"
FOLDER = "/pipeshub"
POOL_LABELS = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "PF-01"}


@pytest.fixture
def runtime_mode() -> RuntimeMode:
    return RuntimeMode.DOCKER_HARDENED


@pytest.fixture
def volume_prefix(namespace: str) -> str:
    return "pxh-pf01-" + namespace.rsplit(".", 1)[1]


def _seeded_spec(env: DockerContractEnv) -> SandboxSpec:
    return with_mounts(env.spec(), MountSpec(SKILLS, read_only=True), MountSpec(FOLDER, read_only=True))


def _count_calls(provider: DockerClientProvider) -> Counter[str]:
    """Docker calls the broker makes from here on, by name; a container create counts by the name it gives the
    container (``create_container:<name>``, a helper has none), since a warm refill may create in the background."""
    calls: Counter[str] = Counter()
    run_blocking = provider.run_blocking

    async def counted(fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        name = getattr(fn, "__name__", "?")
        calls[f"{name}:{kwargs.get('name')}" if name == "create_container" else name] += 1
        return await run_blocking(fn, *args, **kwargs)

    provider.run_blocking = counted  # type: ignore[method-assign]
    return calls


def _created_for(calls: Counter[str], sandbox_id: str) -> list[str]:
    """Containers created for ``sandbox_id``'s seeds: a helper (no name) or a seeder named for it."""
    return [k for k in calls if k == "create_container:None" or (k.startswith("create_container:") and k.endswith(sandbox_id))]


def _containers(provider: DockerClientProvider, namespace: str) -> list[dict[str, Any]]:
    return provider.client.api.containers(all=True, filters={"label": [f"{namespace}.managed=1"]})


def _roles(provider: DockerClientProvider, namespace: str) -> list[tuple[str, str]]:
    return sorted((c["Labels"][f"{namespace}.role"], c["State"]) for c in _containers(provider, namespace))


def _volumes(provider: DockerClientProvider, namespace: str) -> list[str]:
    listed = provider.client.api.volumes(filters={"label": [f"{namespace}.managed=1"]}) or {}
    return [v["Name"] for v in listed.get("Volumes") or []]


async def _seed_both(handle: Any) -> None:  # noqa: ANN401
    await handle.seed_tar(SKILLS, iter_bytes(tar_of({"report/SKILL.md": b"# report"})))
    await handle.seed_tar(FOLDER, iter_bytes(tar_of({"INDEX.md": b"# tools"})))
    await handle.seed_tar(FOLDER, iter_bytes(tar_of({"knowledge/r1.md": b"shelved"})))


async def _assert_seeded(handle: Any) -> None:  # noqa: ANN401
    for path, data in ((f"{SKILLS}/report/SKILL.md", b"# report"), (f"{FOLDER}/INDEX.md", b"# tools"),
                       (f"{FOLDER}/knowledge/r1.md", b"shelved")):
        out, _, code = await run(await handle.exec(["cat", path], env={}))
        assert (out, code) == (data, 0), path
    _, _, code = await run(await handle.exec(["sh", "-c", f"echo x > {FOLDER}/INDEX.md"], env={}))
    assert code != 0, "the sandbox changed a seeded mount"


@pytest.fixture
async def broker(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str, cleanup_namespace: None
) -> AsyncIterator[DockerSandboxBroker]:
    created = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=namespace, volume_prefix=volume_prefix,
    )
    yield created
    await created.aclose()


async def test_pf01_s1_a_cold_sandbox_is_made_with_one_seeder_and_a_seed_creates_nothing(
    broker: DockerSandboxBroker, provider: DockerClientProvider, sandbox_env: DockerContractEnv, namespace: str,
) -> None:
    handle = await broker.acquire(_seeded_spec(sandbox_env), lease=LEASE, idempotency_key="k-pf01-cold")
    released = False
    try:
        assert _roles(provider, namespace) == [("sandbox", "running"), ("seeder", "created")]
        calls = _count_calls(provider)
        await _seed_both(handle)
        assert _created_for(calls, handle.id) == [], calls
        assert calls["remove_container"] == 0, calls
        await _assert_seeded(handle)
        await broker.release(handle)
        released = True
        assert _containers(provider, namespace) == []
        assert _volumes(provider, namespace) == []
    finally:
        if not released:
            await broker.release(handle)


async def test_pf01_s2_a_warm_sandbox_is_made_with_its_seeder_before_any_run_takes_it(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    sandbox_env: DockerContractEnv, cleanup_namespace: None,
) -> None:
    pooled = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=namespace, volume_prefix=volume_prefix,
        warm_pool=WarmPoolConfig(ceiling=1, learn_images=frozenset({DOCKER_TEST_IMAGE}), labels=POOL_LABELS),
    )
    try:
        pooled.pool.learn(_seeded_spec(sandbox_env))
        assert await pooled.fill_warm() == 1
        assert _roles(provider, namespace) == [("seeder", "created"), ("warm", "running")]
        warm = pooled.warm_ids()
        calls = _count_calls(provider)
        handle = await pooled.acquire(_seeded_spec(sandbox_env), lease=LEASE, idempotency_key="k-pf01-warm")
        assert handle.id in warm
        await _seed_both(handle)
        assert _created_for(calls, handle.id) == [], calls
        await _assert_seeded(handle)
        await pooled.release(handle)
    finally:
        await pooled.aclose()
    roles = Counter(role for role, _ in _roles(provider, namespace))
    assert roles["sandbox"] == 0
    assert roles["seeder"] == roles["warm"], f"a seeder outlived its sandbox, or a refilled one has none: {roles}"


async def test_pf01_s3_a_sandbox_made_without_a_seeder_is_still_seeded(
    broker: DockerSandboxBroker, provider: DockerClientProvider, sandbox_env: DockerContractEnv, namespace: str,
) -> None:
    handle = await broker.acquire(_seeded_spec(sandbox_env), lease=LEASE, idempotency_key="k-pf01-legacy")
    try:
        for info in _containers(provider, namespace):
            if info["Labels"][f"{namespace}.role"] == "seeder":
                provider.client.api.remove_container(info["Id"], force=True)
        assert _roles(provider, namespace) == [("sandbox", "running")]
        await _seed_both(handle)
        await _assert_seeded(handle)
    finally:
        await broker.release(handle)
    assert _containers(provider, namespace) == []
    assert _volumes(provider, namespace) == []


@pytest.mark.parametrize("sweep", ["reap", "reconcile"])
async def test_pf01_s4_a_seeder_whose_sandbox_is_gone_is_swept_before_its_volumes(
    provider: DockerClientProvider, clock: ManualClock, namespace: str, volume_prefix: str,
    sandbox_env: DockerContractEnv, cleanup_namespace: None, sweep: str,
) -> None:
    """A broker died holding the sandbox, whose container then went (a host reboot): the next broker finds the
    seeder and the volumes it holds, and removes both; a seeder younger than the reap's age is left."""
    install = f"pf01-{uuid.uuid4().hex[:6]}"

    async def broker() -> DockerSandboxBroker:
        return await DockerSandboxBroker.create(
            provider=provider, clock=clock, ids=RandomIds(), allowed_images=frozenset({DOCKER_TEST_IMAGE}),
            label_namespace=namespace, volume_prefix=volume_prefix, install_id=install,
        )

    run_label = {"runId": f"run-{uuid.uuid4().hex[:8]}"}
    spec = with_mounts(sandbox_env.spec(labels=run_label), MountSpec(SKILLS, read_only=True))
    handle = await (await broker()).acquire(spec, lease=LEASE, idempotency_key="k-pf01-reap")
    provider.client.api.remove_container(handle.container_id, force=True)
    assert _roles(provider, namespace) == [("seeder", "created")]
    restarted = await broker()
    if sweep == "reap":
        _ = await restarted.reap(labels=run_label, older_than_s=60)
        assert _roles(provider, namespace) == [("seeder", "created")], "a seeder younger than the reap's age was taken"
        _ = clock.advance(3600)
        _ = await restarted.reap(labels=run_label, older_than_s=60)
    else:
        assert await restarted.reconcile() >= 1
    assert _containers(provider, namespace) == []
    assert _volumes(provider, namespace) == []
