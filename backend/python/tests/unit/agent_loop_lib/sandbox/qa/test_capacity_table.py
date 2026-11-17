"""The ceiling across machine sizes, process limits and pod counts.

The expected value is worked out here independently of ``compute_plan``:
each term is the share of the machine over one run, split between the pods,
the smallest known term wins, then the minimum and the slot files clamp it.
"""

from __future__ import annotations

import itertools
import math
from fractions import Fraction

import pytest

from app.agent_loop_lib.sandbox.capacity import (
    CapacitySettings,
    MachineSize,
    RunSize,
    compute_plan,
)

GIB = 2**30
_RUN = RunSize(memory_bytes=GIB, cpus=0.5, pids=512)
_CPUS = [1, 2, 3, 4, 6, 8, 12, 16, 32, 48, 64, 96, 128, 256]
_MEM_GIB = [1, 2, 3, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
_PIDS = [None, 4096, 32768, 4_194_304]
_DIVISORS = [1, 2, 3, 7, 64]
_FILES = [256, 1024]


def _expected(mem_gib: int, cpus: int, pids: int | None, divisor: int, files: int, min_total: int) -> int:
    # Exact rational arithmetic, so the reference cannot share a rounding slip.
    terms = [
        math.floor(Fraction(mem_gib) * Fraction(1, 4) / 1) // divisor,
        math.floor(Fraction(cpus) * Fraction(3, 4) / Fraction(1, 2)) // divisor,
    ]
    if pids is not None:
        terms.append(math.floor(Fraction(pids) * Fraction(1, 4) / 512) // divisor)
    # A minimum above the machine's terms is held to them, never below 2.
    min_total = min(min_total, max(2, min(terms)))
    return min(max(min_total, min(min(terms), files)), files)


@pytest.mark.parametrize("files", _FILES)
@pytest.mark.parametrize("divisor", _DIVISORS)
@pytest.mark.parametrize("min_total", [1, 2, 5])
def test_the_ceiling_table(files: int, divisor: int, min_total: int) -> None:
    settings = CapacitySettings(divisor=divisor, min_total=min_total)
    for mem_gib, cpus, pids in itertools.product(_MEM_GIB, _CPUS, _PIDS):
        machine = MachineSize(mem_gib * GIB, float(cpus), pids, "helm_env")
        plan = compute_plan("compose", machine, _RUN, settings, files)
        want = _expected(mem_gib, cpus, pids, divisor, files, min_total)
        assert plan.ceiling == want, (mem_gib, cpus, pids, divisor, files, plan.terms)
        assert plan.floor == min(min_total, want, max(2, min(plan.terms[k] for k in plan.terms if plan.terms[k] is not None)))
        known = {k: v for k, v in plan.terms.items() if v is not None}
        assert known[plan.binding] == min(known.values())


@pytest.mark.parametrize("mode", ["compose", "helm_dind", "local"])
def test_the_ceiling_never_falls_as_the_machine_grows(mode: str) -> None:
    settings = CapacitySettings()
    last = 0
    for mem_gib, cpus in zip(_MEM_GIB, _CPUS[: len(_MEM_GIB)], strict=True):
        plan = compute_plan(mode, MachineSize(mem_gib * GIB, float(cpus), None, "helm_env"), _RUN, settings, 1024)
        assert plan.ceiling >= last
        last = plan.ceiling


@pytest.mark.parametrize("divisor", range(1, 65))
def test_pods_together_never_exceed_the_whole_machine(divisor: int) -> None:
    machine = MachineSize(256 * GIB, 64.0, 4_194_304, "helm_env")
    whole = compute_plan("compose", machine, _RUN, CapacitySettings(min_total=1), 1024).ceiling
    each = compute_plan("compose", machine, _RUN, CapacitySettings(divisor=divisor, min_total=1), 1024).ceiling
    assert each * divisor <= whole


def test_e2b_ignores_the_machine_and_the_divisor() -> None:
    plan = compute_plan(
        "e2b", MachineSize(GIB, 1.0, 10, "helm_env"), None, CapacitySettings(divisor=8), 256,
    )
    assert plan.ceiling == 100
    assert compute_plan("e2b", plan.machine, None, CapacitySettings(max_total=500), 256).ceiling == 256
