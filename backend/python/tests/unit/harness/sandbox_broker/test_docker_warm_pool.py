"""P5.9a on the Docker broker (runc): WP-1..WP-10 with a real egress proxy, and DW-1..DW-6.

Each broker gets its own label namespace and ``pxh-`` prefix; warm sandboxes
carry the owner label; teardown removes everything by that namespace only.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import uuid
from typing import TYPE_CHECKING, Any, cast, override

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from harness_core.ids import RandomIds
from harness_core.sandbox.pool import (
    CAPACITY_SLOTS,
    HELD,
    WARM,
    WarmPoolConfig,
    capacity_slots,
    shape_of,
)
from harness_core.sandbox.streams import collect
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import WORK
from harness_core.testing.sandbox.pool_contract import (
    WarmPoolContract,
    WarmPoolContractEnv,
)
from tests.support import sandbox_reaper
from tests.unit.agent_loop_lib.sandbox.contract.conftest import DOCKER_TEST_IMAGE
from tests.unit.harness.sandbox_broker.conftest import (
    DockerContractEnv,
    cleanup,
    cleanup_networks,
    egress_wiring,
)
from tests.unit.harness.sandbox_broker.egress_stack import sandbox_curl

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from tests.unit.harness.sandbox_broker.egress_stack import (
        DockerEgressContractEnv,
        EgressStack,
    )

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

SLOT_MB = 256
RESOURCES = Resources(cpus=0.5, memory_mb=SLOT_MB, pids=64, scratch_mb=64)
POOL_LABELS = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "P5.9a"}
LEASE = LeaseRef("worker-p59a", 1)


class DockerPoolEnv(WarmPoolContractEnv):
    proxied = True

    def __init__(
        self,
        provider: DockerClientProvider,
        stack: EgressStack,
        bindings: RemoteEgressBindings,
        egress_env: DockerEgressContractEnv,
    ) -> None:
        self.provider = provider
        self.stack = stack
        self.bindings = bindings
        self.egress_env = egress_env
        self.clock = ManualClock()
        self.namespace = f"io.pipeshub.p59a.{uuid.uuid4().hex[:8]}"
        self.prefix = "pxh-p59a-" + self.namespace.rsplit(".", 1)[1]
        self._programs = DockerContractEnv(self.clock, RuntimeMode.DOCKER_HARDENED, self.namespace)

    @property
    @override
    def programs(self) -> DockerContractEnv:
        return self._programs

    @override
    def spec(self, labels: Mapping[str, str] | None = None) -> SandboxSpec:
        return SandboxSpec(
            image_digest=DOCKER_TEST_IMAGE,
            mode=RuntimeMode.DOCKER_HARDENED,
            resources=RESOURCES,
            egress=self.egress_env.policy(f"pxh-{uuid.uuid4().hex}"),
            mounts=(MountSpec(WORK),),
            labels={**POOL_LABELS, **(labels or {})},
        )

    def config(self, *, ceiling: int, fraction: float = 1.0, max_age_s: int = 3600) -> WarmPoolConfig:
        shape = shape_of(self.spec())
        assert shape is not None
        return WarmPoolConfig(
            ceiling=ceiling,
            shapes=(shape,),
            fraction=fraction,
            reserve_fraction=0.0,
            max_age_s=max_age_s,
            labels=POOL_LABELS,
        )

    async def new_broker(self, config: WarmPoolConfig, *, memory_mb: int | None) -> DockerSandboxBroker:
        return await DockerSandboxBroker.create(
            provider=self.provider,
            clock=self.clock,
            ids=RandomIds(),
            allowed_images=frozenset({DOCKER_TEST_IMAGE}),
            label_namespace=self.namespace,
            volume_prefix=self.prefix,
            egress=egress_wiring(self.provider, self.clock, self.stack, self.bindings, self.prefix),
            warm_pool=config,
            memory_mb=memory_mb,
        )

    @contextlib.asynccontextmanager
    async def broker(  # type: ignore[override]
        self, *, ceiling: int, slots: int, fraction: float = 1.0, max_age_s: int = 3600
    ) -> AsyncIterator[DockerSandboxBroker]:
        broker = await self.new_broker(self.config(ceiling=ceiling, fraction=fraction, max_age_s=max_age_s), memory_mb=slots * SLOT_MB)
        try:
            yield broker
        finally:
            await broker.aclose()

    async def teardown(self) -> None:
        await self.provider.run_blocking(cleanup, self.provider, self.namespace, self.prefix)
        await self.provider.run_blocking(cleanup_networks, self.provider, self.namespace, self.stack.proxy)

    @override
    async def egress_bound(self, sandbox_id: str) -> bool:
        return sandbox_id in await self.bindings.bound()

    @override
    def advance(self, seconds: float) -> None:
        _ = self.clock.advance(seconds)


@pytest.fixture
async def pool_env(
    provider: DockerClientProvider,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
    docker_egress_env: DockerEgressContractEnv,
) -> AsyncIterator[DockerPoolEnv]:
    env = DockerPoolEnv(provider, egress_stack, egress_bindings, docker_egress_env)
    yield env
    await env.teardown()


class TestDockerWarmPool(WarmPoolContract):
    """WP-1..WP-10 (``pool_env`` is the module fixture above)."""


def _container(env: DockerPoolEnv, sandbox_id: str) -> dict[str, Any]:
    api = env.provider.client.api
    (info,) = api.containers(all=True, filters={"label": [f"{env.namespace}.id={sandbox_id}"]})
    return api.inspect_container(info["Id"])


def _exec(env: DockerPoolEnv, container_id: str, command: list[str]) -> str:
    api = env.provider.client.api
    created = api.exec_create(container_id, command, user="1000:1000")
    return api.exec_start(created["Id"]).decode()


async def test_dw1_a_warm_sandbox_has_no_network_and_no_run_until_bound(pool_env: DockerPoolEnv) -> None:
    env = pool_env
    async with env.broker(ceiling=1, slots=8) as broker:
        _ = await broker.fill_warm()
        (warm_id,) = broker.warm_ids()
        warm = await asyncio.to_thread(_container, env, warm_id)
        host = warm["HostConfig"]
        assert host["NetworkMode"] == "none"
        assert host["CapDrop"] == ["ALL"] and host["ReadonlyRootfs"] is True and host["Privileged"] is False
        assert host["SecurityOpt"] == ["no-new-privileges:true"]
        assert warm["Config"]["User"] == "1000:1000"
        assert host["Binds"] in (None, []) and not host.get("PortBindings")
        assert list(warm["NetworkSettings"]["Networks"]) == ["none"]
        assert (await asyncio.to_thread(_exec, env, warm["Id"], ["ls", "/sys/class/net"])).split() == ["lo"]
        image_labels = set(env.provider.client.api.inspect_image(DOCKER_TEST_IMAGE)["Config"].get("Labels") or {})
        # The test run's own labels (SBXLEAK's reaper) are not a run's: what a test process made, to remove it.
        harness = set(sandbox_reaper.object_labels())
        own = {k for k in warm["Config"]["Labels"] if not k.startswith(env.namespace + ".")} - image_labels - harness
        assert own <= set(POOL_LABELS), "a warm sandbox carries a run's labels"
        assert warm["Config"]["Labels"]["pipeshub.agent"] == "P5.9a"
        assert warm_id not in await env.bindings.bound()

        spec = env.spec({"runId": "run-dw1"})
        handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"dw1-{uuid.uuid4().hex}")
        assert handle.id == warm_id
        bound = await asyncio.to_thread(_container, env, warm_id)
        (network,) = bound["NetworkSettings"]["Networks"]
        address = bound["NetworkSettings"]["Networks"][network]["IPAddress"]
        assert network == f"{env.prefix}-net-{warm_id}"
        subnet = ipaddress.ip_network(f"{address}/29", strict=False)
        assert ipaddress.ip_address(address) == subnet.network_address + 3
        hosts = await asyncio.to_thread(_exec, env, bound["Id"], ["cat", "/etc/hosts"])
        proxy_address = str(subnet.network_address + 2)
        assert f"{proxy_address}\tmodel-gateway" in hosts
        assert warm_id in await env.bindings.bound()
        placeholder = spec.egress.token_routes[0].placeholder
        argv = sandbox_curl(env.egress_env.gateway_url("/v1/echo"), {"Authorization": f"Bearer {placeholder}"})
        process = await handle.exec(argv, env={})
        out, _ = await asyncio.gather(collect(process.stdout), collect(process.stderr))
        assert await process.wait(timeout_s=60) == 0
        assert out.decode().startswith("200"), out
        api = env.provider.client.api
        (marker,) = api.volumes(filters={"label": [f"{env.namespace}.role=binding"]})["Volumes"]
        assert marker["Labels"]["runId"] == "run-dw1"
        await broker.release(handle)
        assert api.containers(all=True, filters={"label": [f"{env.namespace}.id={warm_id}"]}) == []
        left = cast("dict[str, Any]", api.volumes(filters={"label": [f"{env.namespace}.id={warm_id}"]}) or {})
        assert not left.get("Volumes")


async def test_dw2_two_brokers_racing_for_one_warm_sandbox_bind_it_once(pool_env: DockerPoolEnv) -> None:
    env = pool_env
    config = env.config(ceiling=1)
    first = await env.new_broker(config, memory_mb=8 * SLOT_MB)
    second = await env.new_broker(config, memory_mb=8 * SLOT_MB)
    try:
        _ = await first.fill_warm()
        (warm_id,) = first.warm_ids()
        _ = await second.pool.load()
        assert second.warm_ids() == {warm_id}
        a, b = await asyncio.gather(
            first.acquire(env.spec(), lease=LEASE, idempotency_key=f"dw2-a-{uuid.uuid4().hex}"),
            second.acquire(env.spec(), lease=LEASE, idempotency_key=f"dw2-b-{uuid.uuid4().hex}"),
        )
        assert [a.id, b.id].count(warm_id) == 1
        assert a.id != b.id
        # The loser never touched the winner's sandbox: both still run their own processes.
        for handle in (a, b):
            await env.write_file(handle, f"{WORK}/mine.txt", handle.id)
            assert await env.read_file(handle, f"{WORK}/mine.txt") == handle.id
    finally:
        await first.aclose()
        await second.aclose()


async def test_dw3_a_restarted_broker_loads_the_pool_and_adopts_a_bound_sandbox(pool_env: DockerPoolEnv) -> None:
    env = pool_env
    config = env.config(ceiling=2)
    first = await env.new_broker(config, memory_mb=8 * SLOT_MB)
    key = f"dw3-{uuid.uuid4().hex}"
    spec = env.spec({"runId": "run-dw3"})
    _ = await first.fill_warm()
    handle = await first.acquire(spec, lease=LEASE, idempotency_key=key)
    remaining = first.warm_ids()
    await first.aclose()
    restarted = await env.new_broker(config, memory_mb=8 * SLOT_MB)
    try:
        assert remaining <= restarted.warm_ids()
        adopted = await restarted.acquire(spec, lease=LeaseRef("worker-p59a", 2), idempotency_key=key)
        assert adopted.id == handle.id
        assert await env.egress_bound(handle.id)
        assert await restarted.reap(labels={"runId": "run-dw3"}, older_than_s=0) == 1
    finally:
        await restarted.aclose()


async def test_dw4_capacity_from_the_daemon_counts_warm_sandboxes(pool_env: DockerPoolEnv) -> None:
    env = pool_env
    broker = await env.new_broker(env.config(ceiling=2), memory_mb=None)
    try:
        info = await env.provider.run_blocking(env.provider.client.info)
        slots = capacity_slots(int(info["MemTotal"]) // (1024 * 1024), SLOT_MB, 0.0)
        _ = await broker.fill_warm()
        _ = await broker.acquire(env.spec(), lease=LEASE, idempotency_key=f"dw4-{uuid.uuid4().hex}")
        await broker.pool.aclose()
        detail = (await broker.health()).detail
        assert detail[CAPACITY_SLOTS] == str(slots)
        assert detail[HELD] == "1"
        assert detail[WARM] == "1"
    finally:
        await broker.aclose()


async def test_dw5_a_sandbox_claimed_by_a_crashed_broker_is_never_handed_out(pool_env: DockerPoolEnv) -> None:
    env = pool_env
    config = env.config(ceiling=1)
    crashed = await env.new_broker(config, memory_mb=8 * SLOT_MB)
    _ = await crashed.fill_warm()
    (warm,) = await crashed.list_warm()
    assert await crashed.claim_warm(warm)
    await crashed.aclose()
    survivor = await env.new_broker(env.config(ceiling=0), memory_mb=8 * SLOT_MB)
    try:
        assert survivor.warm_ids() == frozenset()
        handle = await survivor.acquire(env.spec(), lease=LEASE, idempotency_key=f"dw5-{uuid.uuid4().hex}")
        assert handle.id != warm.sandbox_id
        assert await survivor.reap(labels=POOL_LABELS, older_than_s=0) >= 1
        api = env.provider.client.api
        assert api.containers(all=True, filters={"label": [f"{env.namespace}.id={warm.sandbox_id}"]}) == []
    finally:
        await survivor.aclose()


async def test_dw6_dead_and_stale_warm_sandboxes_are_trimmed(pool_env: DockerPoolEnv) -> None:
    env = pool_env
    config = env.config(ceiling=2, max_age_s=3600)
    broker = await env.new_broker(config, memory_mb=8 * SLOT_MB)
    other = None
    api = env.provider.client.api

    def exists(sandbox_id: str) -> bool:
        return bool(api.containers(all=True, filters={"label": [f"{env.namespace}.id={sandbox_id}"]}))

    try:
        _ = await broker.fill_warm()
        died, foreign = sorted(await broker.list_warm(), key=lambda w: w.sandbox_id)
        api.stop(died.ref, timeout=1)
        assert await broker.trim_warm() == 1, "a warm sandbox this pool knew running has died"
        assert not exists(died.sandbox_id)
        await broker.aclose()
        # Seen by another broker, a stopped warm container may be a create still in flight: kept until the grace.
        api.stop(foreign.ref, timeout=1)
        other = await env.new_broker(config, memory_mb=8 * SLOT_MB)
        assert foreign.sandbox_id not in other.warm_ids()
        _ = await other.trim_warm()
        assert exists(foreign.sandbox_id)
        env.advance(config.start_grace_s + 1)
        _ = await other.trim_warm()
        assert not exists(foreign.sandbox_id)
        _ = await other.fill_warm()
        stale = other.warm_ids()
        assert stale
        env.advance(config.max_age_s + 1)
        assert await other.trim_warm() == len(stale)
        assert not any(exists(sandbox_id) for sandbox_id in stale)
    finally:
        await broker.aclose()
        if other is not None:
            await other.aclose()
