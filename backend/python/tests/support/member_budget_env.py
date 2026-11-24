"""Put a member budget in place of this process's, as ``PIPESHUB_CAPACITY_PROFILE=auto`` would build it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.capacity import runtime
from app.capacity.budget_policy import AdmissionPolicy, plan_floors
from app.capacity.member_budget import MemberBudget

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

GIB = 1 << 30


def install_budget(
    monkeypatch: pytest.MonkeyPatch, directory: Path, *, c: int = 18, mem: int = 64 * GIB, llm: int = 24, cpus: int = 16
) -> MemberBudget:
    directory.mkdir(mode=0o700, exist_ok=True)
    monkeypatch.setenv("PIPESHUB_CAPACITY_PROFILE", "auto")
    budget = MemberBudget(
        directory, cpu_tokens=c, mem_bytes=mem, llm_cap=llm,
        floors=plan_floors(cpus, c, None, 1.0, AdmissionPolicy()), service="test",
    )
    monkeypatch.setitem(runtime._STATE, "budget", budget)
    monkeypatch.setitem(runtime._STATE, "plan", None)
    return budget
