"""Contract suite for a broker with a warm pool (P5.9a WP-1..WP-10).

Subclass :class:`WarmPoolContract` and provide ``pool_env`` (a
:class:`WarmPoolContractEnv`). The env builds brokers with a pool sized by the
test (ceiling and host slots), so the same bodies run on the in-memory fake and
on a container runtime. Skips belong in fixtures, never in these bodies.
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.failpoints import Raise, registry
from harness_core.sandbox.errors import SandboxNotFound
from harness_core.sandbox.pool import CAPACITY_SLOTS, HELD, WARM, WARM_POOL, shape_of
from harness_core.sandbox.streams import collect
from harness_core.sandbox.types import LeaseRef
from harness_core.testing.sandbox.contract import WORK

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextlib import AbstractAsyncContextManager

    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle, WarmSandboxPool
    from harness_core.sandbox.types import SandboxSpec
    from harness_core.testing.sandbox.contract import SandboxContractEnv

    class PooledBroker(SandboxBroker, WarmSandboxPool, Protocol): ...


LEASE = LeaseRef("worker-pool", 1)
AFTER_CLAIM = "broker.warm.after_claim"


class WarmPoolContractEnv(ABC):
    """What the pool cases need from one adapter."""

    @abstractmethod
    def spec(self, labels: Mapping[str, str] | None = None) -> SandboxSpec:
        """A spec a warm sandbox can serve: a writable scratch mount at ``WORK``; an egress allowlist when
        ``proxied``."""

    #: Whether ``spec`` carries an egress allowlist the adapter binds through its proxy.
    proxied: bool = False

    @abstractmethod
    def broker(
        self, *, ceiling: int, slots: int, fraction: float = 1.0, max_age_s: int = 3600
    ) -> AbstractAsyncContextManager[PooledBroker]:
        """An async context (``async with env.broker(...) as broker``) for a broker whose pool pre-creates
        ``spec()``'s shape, on a host that holds ``slots`` such sandboxes."""

    @property
    @abstractmethod
    def programs(self) -> SandboxContractEnv:
        """The adapter's program vocabulary (``SandboxBrokerContract``'s env)."""

    async def write_file(self, handle: SandboxHandle, path: str, text: str) -> None:
        process = await handle.exec(self.programs.argv_write_file(path, text), env={})
        assert await process.wait(timeout_s=60) == 0

    async def read_file(self, handle: SandboxHandle, path: str) -> str | None:
        """The file's text, ``None`` when it is not there."""
        process = await handle.exec(self.programs.argv_read_file(path), env={})
        out, _ = await asyncio.gather(collect(process.stdout), collect(process.stderr))
        return out.decode() if await process.wait(timeout_s=60) == 0 else None

    @abstractmethod
    async def egress_bound(self, sandbox_id: str) -> bool:
        """Whether the egress proxy holds a binding for this sandbox."""

    @abstractmethod
    def advance(self, seconds: float) -> None:
        """Move the broker's clock forward."""


def _key() -> str:
    return f"pool-{uuid.uuid4().hex}"


async def _detail(broker: PooledBroker) -> Mapping[str, str]:
    return (await broker.health()).detail


class WarmPoolContract:
    """The subclass's module (or the class) provides the ``pool_env`` fixture."""

    async def test_wp1_an_idle_host_fills_the_pool_from_its_capacity(self, pool_env: WarmPoolContractEnv) -> None:
        async with pool_env.broker(ceiling=2, slots=8, fraction=0.25) as broker:
            assert await broker.fill_warm() == 2
            assert len(broker.warm_ids()) == 2
            assert await broker.fill_warm() == 0
            detail = await _detail(broker)
            assert detail[WARM_POOL] == "true"
            assert (detail[CAPACITY_SLOTS], detail[HELD], detail[WARM]) == ("8", "0", "2")
        async with pool_env.broker(ceiling=5, slots=8, fraction=0.25) as broker:
            _ = await broker.fill_warm()
            assert len(broker.warm_ids()) == 2, "ceil(8 x 0.25) bounds the pool below the ceiling"

    async def test_wp2_a_matching_acquire_is_served_warm_and_bound_to_its_run(self, pool_env: WarmPoolContractEnv) -> None:
        async with pool_env.broker(ceiling=2, slots=8) as broker:
            _ = await broker.fill_warm()
            before = broker.warm_ids()
            handle = await broker.acquire(pool_env.spec({"runId": "run-wp2"}), lease=LEASE, idempotency_key=_key())
            assert handle.id in before
            assert handle.id not in broker.warm_ids()
            await pool_env.write_file(handle, f"{WORK}/a.txt", "run")
            assert await pool_env.read_file(handle, f"{WORK}/a.txt") == "run"
            _ = await broker.fill_warm()
            after = broker.warm_ids()
            assert len(after) == 2
            assert handle.id not in after
            assert await broker.reap(labels={"runId": "run-wp2"}, older_than_s=0) == 1
            with pytest.raises(SandboxNotFound):
                await pool_env.write_file(handle, f"{WORK}/b.txt", "gone")

    async def test_wp3_wp9_a_released_sandbox_is_never_handed_out_again(self, pool_env: WarmPoolContractEnv) -> None:
        async with pool_env.broker(ceiling=1, slots=8) as broker:
            seen: list[str] = []
            for round_ in range(3):
                _ = await broker.fill_warm()
                warm = broker.warm_ids()
                assert not warm & set(seen), "a released sandbox came back into the pool"
                handle = await broker.acquire(pool_env.spec({"runId": f"run-wp3-{round_}"}), lease=LEASE, idempotency_key=_key())
                assert handle.id in warm
                assert handle.id not in seen
                assert await pool_env.read_file(handle, f"{WORK}/left.txt") is None, "a previous run's file is there"
                await pool_env.write_file(handle, f"{WORK}/left.txt", f"round {round_}")
                seen.append(handle.id)
                await broker.release(handle)
            _ = await broker.fill_warm()
            assert not broker.warm_ids() & set(seen)

    async def test_wp4_the_same_key_twice_takes_one_warm_sandbox(self, pool_env: WarmPoolContractEnv) -> None:
        async with pool_env.broker(ceiling=2, slots=8) as broker:
            _ = await broker.fill_warm()
            before = broker.warm_ids()
            key = _key()
            first = await broker.acquire(pool_env.spec(), lease=LEASE, idempotency_key=key)
            again = await broker.acquire(pool_env.spec(), lease=LEASE, idempotency_key=key)
            assert again.id == first.id
            # A refill only adds new ids, so what left the pool is what the acquires took.
            assert before - broker.warm_ids() == {first.id}

    async def test_wp5_a_spec_of_another_shape_goes_cold(self, pool_env: WarmPoolContractEnv) -> None:
        async with pool_env.broker(ceiling=2, slots=8) as broker:
            _ = await broker.fill_warm()
            before = broker.warm_ids()
            spec = pool_env.spec()
            other = replace(spec, resources=replace(spec.resources, pids=spec.resources.pids + 1))
            assert shape_of(other) != shape_of(spec)
            handle = await broker.acquire(other, lease=LEASE, idempotency_key=_key())
            assert handle.id not in before
            assert broker.warm_ids() == before

    async def test_wp6_warm_sandboxes_never_starve_cold_acquires(self, pool_env: WarmPoolContractEnv) -> None:
        slots = 3
        async with pool_env.broker(ceiling=slots, slots=slots, fraction=1.0) as broker:
            assert await broker.fill_warm() == slots
            handles: list[SandboxHandle] = []
            for _ in range(slots):
                spec = pool_env.spec()
                cold = replace(spec, resources=replace(spec.resources, pids=spec.resources.pids + 1))
                handles.append(await broker.acquire(cold, lease=LEASE, idempotency_key=_key()))
                detail = await _detail(broker)
                assert int(detail[HELD]) + int(detail[WARM]) <= slots, detail
            assert broker.warm_ids() == frozenset()
            assert await broker.fill_warm() == 0, "a full host refilled its pool"
            await broker.release(handles.pop())
            assert await broker.fill_warm() == 1

    async def test_wp7_reap_by_run_labels_and_stale_warm_sandboxes(self, pool_env: WarmPoolContractEnv) -> None:
        async with pool_env.broker(ceiling=2, slots=8, max_age_s=600) as broker:
            _ = await broker.fill_warm()
            handle = await broker.acquire(pool_env.spec({"runId": "run-wp7"}), lease=LEASE, idempotency_key=_key())
            unbound = broker.warm_ids()
            assert unbound
            assert await broker.reap(labels={"runId": "run-wp7"}, older_than_s=0) == 1
            assert unbound <= broker.warm_ids(), "a run-scoped reap took an unbound warm sandbox"
            with pytest.raises(SandboxNotFound):
                await pool_env.write_file(handle, f"{WORK}/x.txt", "x")
            _ = await broker.fill_warm()
            stale = broker.warm_ids()
            pool_env.advance(601)
            assert await broker.trim_warm() == len(stale)
            _ = await broker.fill_warm()
            assert broker.warm_ids()
            assert not broker.warm_ids() & stale

    async def test_wp8_a_pool_with_ceiling_zero_is_off(self, pool_env: WarmPoolContractEnv) -> None:
        async with pool_env.broker(ceiling=0, slots=8) as broker:
            assert await broker.fill_warm() == 0
            handle = await broker.acquire(pool_env.spec(), lease=LEASE, idempotency_key=_key())
            await pool_env.write_file(handle, f"{WORK}/a.txt", "cold")
            assert broker.warm_ids() == frozenset()
            assert (await _detail(broker)).get(WARM_POOL, "false") == "false"

    async def test_wp9_no_egress_binding_until_bound(self, pool_env: WarmPoolContractEnv) -> None:
        if not pool_env.proxied:
            pytest.skip("the adapter's pool env has no egress proxy")
        async with pool_env.broker(ceiling=2, slots=8) as broker:
            _ = await broker.fill_warm()
            for sandbox_id in broker.warm_ids():
                assert not await pool_env.egress_bound(sandbox_id)
            handle = await broker.acquire(pool_env.spec({"runId": "run-wp9"}), lease=LEASE, idempotency_key=_key())
            assert await pool_env.egress_bound(handle.id)
            await broker.release(handle)
            assert not await pool_env.egress_bound(handle.id)

    async def test_wp10_a_bind_that_fails_after_the_claim_goes_cold(self, pool_env: WarmPoolContractEnv) -> None:
        async with pool_env.broker(ceiling=1, slots=8) as broker:
            _ = await broker.fill_warm()
            (claimed,) = broker.warm_ids()
            registry.arm(AFTER_CLAIM, Raise())
            try:
                handle = await broker.acquire(pool_env.spec(), lease=LEASE, idempotency_key=_key())
            finally:
                registry.disarm(AFTER_CLAIM)
            assert handle.id != claimed
            await pool_env.write_file(handle, f"{WORK}/a.txt", "cold")
            _ = await broker.fill_warm()
            assert claimed not in broker.warm_ids()

    async def test_wp11_qa26_a_warm_sandbox_handed_to_org_b_never_carried_org_a(
        self, pool_env: WarmPoolContractEnv
    ) -> None:
        org_a, org_b = {"orgId": "org-a-wp11", "runId": "run-a-wp11"}, {"orgId": "org-b-wp11", "runId": "run-b-wp11"}
        assert shape_of(pool_env.spec(org_a)) == shape_of(pool_env.spec(org_b)), "a warm shape names no org or run"
        async with pool_env.broker(ceiling=2, slots=8) as broker:
            _ = await broker.fill_warm()
            a_warm = await broker.acquire(pool_env.spec(org_a), lease=LEASE, idempotency_key=_key())
            await pool_env.write_file(a_warm, f"{WORK}/org-a.txt", "org a scratch")
            a_kept = await broker.acquire(pool_env.spec(org_a), lease=LEASE, idempotency_key=_key())
            await pool_env.write_file(a_kept, f"{WORK}/org-a.txt", "org a scratch")
            await broker.release(a_warm)
            await broker.release(a_kept, keep_warm_s=600)
            a_ids = {a_warm.id, a_kept.id}

            _ = await broker.fill_warm()
            offered = broker.warm_ids()
            assert offered and not offered & a_ids, "org a's sandbox went back into the pool"
            b = await broker.acquire(pool_env.spec(org_b), lease=LEASE, idempotency_key=_key())
            assert b.id in offered, "org b was served warm"
            assert b.id not in a_ids
            assert await pool_env.read_file(b, f"{WORK}/org-a.txt") is None, "org b found org a's scratch"

            assert await broker.reap(labels={"orgId": org_a["orgId"]}, older_than_s=0) >= 1
            await pool_env.write_file(b, f"{WORK}/still.txt", "org b is untouched by org a's reap")
            assert offered - {b.id} <= broker.warm_ids(), "an org-scoped reap took unbound warm sandboxes"
            await broker.release(b)

    async def test_wp12_sec_dos_05_one_org_cannot_drain_the_pool(self, pool_env: WarmPoolContractEnv) -> None:
        """Another org active: org a holds at most ceil(4 x 0.5) = 2 warm-served sandboxes; its third acquire goes
        cold, org b is still served warm, and a release gives org a its share back."""
        org_a, org_b = {"orgId": "org-a-wp12"}, {"orgId": "org-b-wp12"}
        async with pool_env.broker(ceiling=4, slots=8, fraction=1.0) as broker:
            assert await broker.fill_warm() == 4
            seen = set(broker.warm_ids())
            b_first = await broker.acquire(pool_env.spec({**org_b, "runId": "b1"}), lease=LEASE, idempotency_key=_key())
            assert b_first.id in seen
            a_handles: list[SandboxHandle] = []
            for n in range(3):
                seen |= broker.warm_ids()
                spec = pool_env.spec({**org_a, "runId": f"a{n}"})
                a_handles.append(await broker.acquire(spec, lease=LEASE, idempotency_key=_key()))
            assert [h.id in seen for h in a_handles] == [True, True, False], "org a's third acquire went cold"
            _ = await broker.fill_warm()
            seen |= broker.warm_ids()
            b_second = await broker.acquire(pool_env.spec({**org_b, "runId": "b2"}), lease=LEASE, idempotency_key=_key())
            assert b_second.id in seen, "org b was still served warm"
            await broker.release(a_handles[0])
            _ = await broker.fill_warm()
            seen |= broker.warm_ids()
            again = await broker.acquire(pool_env.spec({**org_a, "runId": "a9"}), lease=LEASE, idempotency_key=_key())
            assert again.id in seen, "the release gave org a its share back"
            for handle in (*a_handles[1:], again, b_first, b_second):
                await broker.release(handle)


__all__ = ["WarmPoolContract", "WarmPoolContractEnv"]
