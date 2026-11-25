"""SEC-DOS-05 (C2.22): one org cannot drain the warm pool.

While another org has asked the broker for a sandbox within ``org_window_s``, an org holds at most its share of the
pool's ceiling in warm-served sandboxes at once; past it, its acquires go cold (never refused), and a released
sandbox gives the share back. An org alone on the broker (a laptop, a one-org install) is never capped.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from harness_core.sandbox.pool import (
    CapacityProbe,
    WarmPool,
    WarmPoolConfig,
    WarmSandbox,
    WarmShape,
    org_claim_cap,
    shape_of,
)
from harness_core.sandbox.types import MountSpec, Resources, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock

SPEC = SandboxSpec(
    image_digest="pxh-opencode-tools:1.18.34",
    mode=RuntimeMode.DOCKER_HARDENED,
    resources=Resources(cpus=1.0, memory_mb=512, pids=256, scratch_mb=256),
    mounts=(MountSpec("/workspace"),),
)


def _of(org: str | None, run: str = "r") -> SandboxSpec:
    labels = {"runId": run} if org is None else {"orgId": org, "runId": run}
    return replace(SPEC, labels=labels)


class _Backend:
    def __init__(self) -> None:
        self.warm: dict[str, WarmSandbox] = {}
        self.held: set[str] = set()
        self._n = 0

    async def create_warm(self, shape: WarmShape, labels: object) -> WarmSandbox:
        self._n += 1
        sandbox = WarmSandbox(f"w{self._n}", shape, float(self._n))
        self.warm[sandbox.sandbox_id] = sandbox
        return sandbox

    async def claim_warm(self, sandbox: WarmSandbox) -> bool:
        if self.warm.pop(sandbox.sandbox_id, None) is None:
            return False
        self.held.add(sandbox.sandbox_id)
        return True

    async def discard_warm(self, sandbox: WarmSandbox, *, reason: str) -> None:
        self.held.discard(sandbox.sandbox_id)

    async def list_warm(self) -> list[WarmSandbox]:
        return list(self.warm.values())

    async def probe_capacity(self) -> CapacityProbe:
        size = SPEC.resources.memory_mb
        return CapacityProbe(
            memory_mb=64 * 1024,
            held=len(self.held),
            warm=len(self.warm),
            held_mb=len(self.held) * size,
            warm_mb=len(self.warm) * size,
        )

    def holds(self, sandbox_id: str) -> bool:
        return sandbox_id in self.held


def _pool(ceiling: int = 4, **config: object) -> tuple[WarmPool, _Backend, ManualClock]:
    shape = shape_of(SPEC)
    assert shape is not None
    backend, clock = _Backend(), ManualClock()
    pool = WarmPool(
        backend,
        WarmPoolConfig(ceiling=ceiling, shapes=(shape,), fraction=1.0, reserve_fraction=0.0, **config),  # pyright: ignore[reportArgumentType]
        clock=clock,
    )
    return pool, backend, clock


@pytest.mark.parametrize(("ceiling", "share", "cap"), [(4, 0.5, 2), (2, 0.5, 1), (1, 0.5, 1), (5, 0.5, 3), (8, 0.25, 2)])
def test_sec_dos_05_an_orgs_share_of_the_ceiling(ceiling: int, share: float, cap: int) -> None:
    assert org_claim_cap(ceiling, share) == cap


def test_sec_dos_05_a_share_outside_zero_to_one_refuses_to_build() -> None:
    for share in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="share"):
            _ = WarmPoolConfig(ceiling=2, org_share=share)


async def test_sec_dos_05_one_org_takes_its_share_then_goes_cold_and_another_org_is_still_served_warm() -> None:
    pool, _, _ = _pool(ceiling=4)
    assert await pool.refill() == 4
    assert await pool.take(_of("org-b")) is not None, "org b is served warm"
    taken = [await pool.take(_of("org-a", f"a{i}")) for i in range(4)]
    assert [t is not None for t in taken] == [True, True, False, False], "org a stops at ceil(4 x 0.5) = 2"
    assert pool.capped == 2
    assert await pool.take(_of("org-b", "b2")) is not None, "what org a could not take is still there for org b"


async def test_sec_dos_05_an_org_alone_on_the_broker_is_never_capped() -> None:
    pool, _, _ = _pool(ceiling=4)
    _ = await pool.refill()
    assert all([await pool.take(_of("org-a", f"a{i}")) is not None for i in range(4)])
    assert pool.capped == 0
    # Specs with no org label (tests, embedders) are never one org's either.
    _ = await pool.refill()
    assert all([await pool.take(_of(None, f"n{i}")) is not None for i in range(4)])


async def test_sec_dos_05_a_released_sandbox_gives_the_share_back() -> None:
    pool, backend, _ = _pool(ceiling=4)
    _ = await pool.refill()
    assert await pool.take(_of("org-b")) is not None
    first = await pool.take(_of("org-a", "a1"))
    assert first is not None and await pool.take(_of("org-a", "a2")) is not None
    assert await pool.take(_of("org-a", "a3")) is None
    backend.held.discard(first.sandbox_id)  # the run ended and the broker removed its sandbox
    assert await pool.take(_of("org-a", "a4")) is not None


async def test_sec_dos_05_the_cap_lifts_once_the_other_org_has_been_quiet_for_the_window() -> None:
    pool, _, clock = _pool(ceiling=4, org_window_s=600)
    _ = await pool.refill()
    assert await pool.take(_of("org-b")) is not None
    assert await pool.take(_of("org-a", "a1")) is not None
    assert await pool.take(_of("org-a", "a2")) is not None
    assert await pool.take(_of("org-a", "a3")) is None
    clock.advance(601)
    assert await pool.take(_of("org-a", "a4")) is not None, "org b left; org a may use the whole pool again"


async def test_sec_dos_05_a_bind_in_flight_counts_toward_the_share() -> None:
    """Taken by an acquire still binding it (the backend does not hold it yet): counted, so concurrent acquires of
    one org cannot all slip under the cap; once those acquires are over, only what the backend holds counts."""
    pool, backend, _ = _pool(ceiling=4)
    _ = await pool.refill()
    assert await pool.take(_of("org-b")) is not None
    async with pool.acquiring():
        a1 = await pool.take(_of("org-a", "a1"))
        async with pool.acquiring():
            a2 = await pool.take(_of("org-a", "a2"))
            assert a1 is not None and a2 is not None
            backend.held -= {a1.sandbox_id, a2.sandbox_id}  # claimed, bind not done
            async with pool.acquiring():
                assert await pool.take(_of("org-a", "a3")) is None
    # Both binds failed: nothing of org a's is held any more.
    assert await pool.take(_of("org-a", "a4")) is not None


async def test_sec_dos_05_a_share_of_one_turns_the_cap_off() -> None:
    pool, _, _ = _pool(ceiling=4, org_share=1.0)
    _ = await pool.refill()
    assert await pool.take(_of("org-b")) is not None
    assert all([await pool.take(_of("org-a", f"a{i}")) is not None for i in range(3)])
