"""QA26-P4 (SBX-21, SBX-G4): ``SANDBOX_MAX_PER_ORG`` and ``SANDBOX_MAX_TOTAL`` under 20 parallel creates.

Every create is held inside ``provision()`` until all of them have been admitted or refused,
so the caps are tested while every reservation is in flight at once (the TOCTOU window),
with no timing. Two orgs share one process governor through per-request managers, as
``build_coding_sandbox_manager`` and ``ControlPlane`` share ``get_default_governor``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from app.agent_loop_lib.sandbox.coding.base import SandboxContext
from app.agent_loop_lib.sandbox.governor import GovernorLimits, SandboxResourceGovernor
from app.agent_loop_lib.sandbox.manager import (
    SandboxLimitExceeded,
    SandboxLimits,
    SandboxManager,
    SandboxManagerError,
    SandboxType,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.sandbox.coding.registry import SandboxBackendFactory

ORG_A, ORG_B = "org-a-qa26", "org-b-qa26"


@dataclass
class Host:
    """What the backends really created: the oracle the caps are checked against."""

    gate: asyncio.Event = field(default_factory=asyncio.Event)
    live: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    peak: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    peak_total: int = 0
    fail_orgs: frozenset[str] = frozenset()
    expected: int = 0
    settled: int = 0
    all_in: asyncio.Event = field(default_factory=asyncio.Event)

    def total(self) -> int:
        return sum(self.live.values())

    def arrived(self) -> None:
        if self.total() + self.settled >= self.expected:
            self.all_in.set()


class _Backend:
    def __init__(self, host: Host, org: str, index: int) -> None:
        self._host = host
        self._org = org
        self.sandbox_id = f"{org}-sbx-{index}"

    async def provision(self) -> None:
        host = self._host
        host.live[self._org] = host.live.get(self._org, 0) + 1
        host.peak[self._org] = max(host.peak.get(self._org, 0), host.live[self._org])
        host.peak_total = max(host.peak_total, host.total())
        host.arrived()
        await host.gate.wait()
        if self._org in host.fail_orgs:
            host.live[self._org] -= 1
            raise RuntimeError("provision failed")

    async def destroy(self) -> None:
        self._host.live[self._org] -= 1


class _Factory:
    def __init__(self, host: Host, org: str) -> None:
        self._host = host
        self._org = org
        self._count = 0

    def create(self, ctx: SandboxContext) -> _Backend:
        assert ctx.org_id == self._org
        self._count += 1
        return _Backend(self._host, self._org, self._count)


def _manager(governor: SandboxResourceGovernor, host: Host, org: str) -> SandboxManager:
    manager = SandboxManager(governor=governor)
    manager.register_backend(
        SandboxType.CODING,
        # The manager only calls ``create(ctx)``; the provider registry's config surface is not under test.
        cast("SandboxBackendFactory", _Factory(host, org)),
        limits=SandboxLimits(provision_timeout_s=None),
        ctx=SandboxContext(org_id=org),
    )
    return manager


async def _parallel(host: Host, creates: list[tuple[SandboxManager, str]]) -> dict[str, list[object]]:
    host.expected = len(creates)
    tasks = [asyncio.ensure_future(manager.get_or_create(SandboxType.CODING)) for manager, _ in creates]

    def refused(_: object) -> None:
        host.settled += 1
        host.arrived()

    for task in tasks:
        task.add_done_callback(refused)
    # Every create either reserved its slot and is parked in provision, or was refused.
    _ = await host.all_in.wait()
    host.gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    by_org: dict[str, list[object]] = {}
    for (_, org), result in zip(creates, results, strict=True):
        by_org.setdefault(org, []).append(result)
    return by_org


def _created(results: list[object]) -> list[tuple[str, object]]:
    return [r for r in results if isinstance(r, tuple)]  # pyright: ignore[reportUnknownVariableType]


def _refused(results: list[object]) -> list[SandboxLimitExceeded]:
    return [r for r in results if isinstance(r, SandboxLimitExceeded)]


async def test_qa26_p4_one_org_at_its_cap_does_not_spend_another_orgs_quota() -> None:
    host = Host()
    governor = SandboxResourceGovernor(GovernorLimits(max_per_org=4, max_total_sandboxes=10))
    org_a, org_b = _manager(governor, host, ORG_A), _manager(governor, host, ORG_B)
    creates = [(org_a, ORG_A)] * 14 + [(org_b, ORG_B)] * 6
    by_org = await _parallel(host, creates[::2] + creates[1::2])

    assert len(_created(by_org[ORG_A])) == 4
    assert len(_refused(by_org[ORG_A])) == 10, "every create past the cap is refused, typed"
    assert len(_created(by_org[ORG_B])) == 4, "org a's refused creates spent none of org b's quota"
    assert len(_refused(by_org[ORG_B])) == 2
    assert host.peak == {ORG_A: 4, ORG_B: 4}, "no org was over-provisioned, not even in flight"
    for refusal in _refused(by_org[ORG_A]):
        assert ORG_B not in str(refusal)
    assert governor.snapshot() == {"total": 8, "per_org": {ORG_A: 4, ORG_B: 4}}


async def test_qa26_p4_the_process_total_holds_under_twenty_parallel_creates() -> None:
    host = Host()
    governor = SandboxResourceGovernor(GovernorLimits(max_per_org=4, max_total_sandboxes=6))
    org_a, org_b = _manager(governor, host, ORG_A), _manager(governor, host, ORG_B)
    by_org = await _parallel(host, [(org_a, ORG_A), (org_b, ORG_B)] * 10)

    created = _created(by_org[ORG_A]) + _created(by_org[ORG_B])
    assert len(created) == 6
    assert len(_refused(by_org[ORG_A]) + _refused(by_org[ORG_B])) == 14
    assert host.peak_total == 6
    assert max(host.peak.values()) <= 4
    for manager, results in ((org_a, by_org[ORG_A]), (org_b, by_org[ORG_B])):
        for sandbox_id, _ in _created(results):
            await manager.destroy(SandboxType.CODING, sandbox_id)
    assert governor.snapshot() == {"total": 0, "per_org": {}}
    assert host.total() == 0


async def test_qa26_p4_failed_provisions_give_their_slots_back_to_their_own_org() -> None:
    host = Host(fail_orgs=frozenset({ORG_A}))
    governor = SandboxResourceGovernor(GovernorLimits(max_per_org=4, max_total_sandboxes=10))
    org_a, org_b = _manager(governor, host, ORG_A), _manager(governor, host, ORG_B)
    by_org = await _parallel(host, [(org_a, ORG_A), (org_b, ORG_B)] * 10)

    assert [type(r) for r in by_org[ORG_A]].count(SandboxManagerError) == 4
    assert len(_created(by_org[ORG_B])) == 4
    assert governor.snapshot() == {"total": 4, "per_org": {ORG_B: 4}}
    host.gate = asyncio.Event()
    host.fail_orgs = frozenset()
    host.gate.set()
    _, backend = await org_a.get_or_create(SandboxType.CODING)
    assert backend is not None, "org a's failed creates left its quota free"


@pytest.mark.parametrize("cap", [1, 4])
async def test_qa26_p4_per_org_cap_is_exact_for_one_org_alone(cap: int) -> None:
    host = Host()
    governor = SandboxResourceGovernor(GovernorLimits(max_per_org=cap, max_total_sandboxes=None))
    org_a = _manager(governor, host, ORG_A)
    by_org = await _parallel(host, [(org_a, ORG_A)] * 20)
    assert len(_created(by_org[ORG_A])) == cap
    assert host.peak[ORG_A] == cap


async def test_qa26_p4_per_request_managers_share_one_cap() -> None:
    # Each chat builds its own manager, so only the process governor stands between 20 parallel creates.
    host = Host()
    governor = SandboxResourceGovernor(GovernorLimits(max_per_org=4, max_total_sandboxes=6))
    creates = [(_manager(governor, host, org), org) for org in [ORG_A, ORG_B] * 10]
    by_org = await _parallel(host, creates)
    assert host.peak_total == 6
    assert max(host.peak.values()) <= 4
    assert len(_created(by_org[ORG_A]) + _created(by_org[ORG_B])) == 6
