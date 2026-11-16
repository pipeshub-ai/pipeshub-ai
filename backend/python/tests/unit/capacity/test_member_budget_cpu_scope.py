"""The member budget is sized from the container's CPUs, never from the node a cell is pinned to.

A NUMA pin narrows ``sched_getaffinity``; ``PIPESHUB_CONTAINER_CPUS`` keeps the count from before it. The
ledger's token total is shared by every cell, so a pinned cell that sized it from its own node would
shrink the whole container's budget.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from app.capacity import cgroup, numa, runtime
from app.services.resource_governor import probe

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    monkeypatch.setattr(probe, "_cgroup_v2_cpu_quota", lambda: None)
    monkeypatch.setattr(probe, "_cgroup_v1_cpu_quota", lambda: None)
    monkeypatch.setattr(cgroup, "cpu_quota", lambda: (None, "none"))  # the plan reads this reader, not the probe's: no runner quota
    for key in ("PIPESHUB_CPU_BUDGET", "PIPESHUB_MEMORY_BUDGET", "PIPESHUB_CAPACITY_FACTS"):
        monkeypatch.delenv(key, raising=False)  # an exported budget or facts would bypass the simulated affinity
    probe._resolve_effective_cpus.cache_clear()
    yield
    probe._resolve_effective_cpus.cache_clear()


def _tokens(monkeypatch: pytest.MonkeyPatch, *, affinity: int, container_cpus: str | None) -> int:
    monkeypatch.setenv("PIPESHUB_CAPACITY_PROFILE", "auto")
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(affinity)), raising=False)
    if container_cpus is None:
        monkeypatch.delenv(numa.CONTAINER_CPUS_ENV, raising=False)
    else:
        monkeypatch.setenv(numa.CONTAINER_CPUS_ENV, container_cpus)
    plan = runtime.plan_for_process()
    assert plan is not None and plan.member_budget is not None
    return plan.member_budget.cpu_tokens


def test_a_pinned_cell_sizes_the_token_total_from_the_container(monkeypatch: pytest.MonkeyPatch) -> None:
    whole = _tokens(monkeypatch, affinity=32, container_cpus=None)
    pinned = _tokens(monkeypatch, affinity=16, container_cpus="32")
    assert pinned == whole


def test_without_the_pin_record_the_node_count_would_shrink_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # The control: the same narrowed affinity with no recorded container count is a smaller total.
    assert _tokens(monkeypatch, affinity=16, container_cpus=None) < _tokens(monkeypatch, affinity=32, container_cpus=None)
