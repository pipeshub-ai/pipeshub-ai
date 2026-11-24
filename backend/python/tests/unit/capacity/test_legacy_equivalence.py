"""The governor's ceilings are today's over a grid of CPUs, memory, reserve, caps, workers and constants.

``tests/support/legacy_ceilings.py`` is a frozen copy of the resolution chain. A row that differs
is printed with its inputs. The fixed caps the capacity registry names are pinned to the values in
the governor, so a cap cannot change without its source being restated.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from app.capacity.knobs import CAPS
from app.services.resource_governor import policy
from app.services.resource_governor.models import ResourceSnapshot
from tests.support import legacy_ceilings as frozen

CPUS = [0.25, 0.5, 0.75, 1, 1.5, 2, 2.75, 3, 3.75, 4, 6, 8, 12, 16, 17, 24, 31, 32, 48, 64, 96, 128, 256, 512]
MEM_GB = [1, 4, 16, 64, 2048]
ENV_PARSE = [None, 1, 3, 40, 1000]
ENV_INDEX = [None, 1, 20, 200]
WORKERS = [1, 2, 8]
CONSTANTS = [
    frozen.Constants(),
    frozen.Constants(heavy_slots_per_cpu=0.5, light_slots_per_cpu=4.0),
    frozen.Constants(light_parse_max=64, index_max_per_tier=16, index_headroom=4.0),
    frozen.Constants(embedding_reservation=6.0),
]


def _snap(cpus: float, mem_gb: float) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_quota=cpus, cpu_utilisation=0.0, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
        mem_limit_bytes=int(mem_gb * 2**30), mem_working_set_bytes=0, source="grid",
    )


def _patch(monkeypatch: pytest.MonkeyPatch, c: frozen.Constants) -> None:
    for attr, value in {
        "HEAVY_PARSE_SLOTS_PER_CPU": c.heavy_slots_per_cpu,
        "LIGHT_PARSE_SLOTS_PER_CPU": c.light_slots_per_cpu,
        "LIGHT_PARSE_MAX": c.light_parse_max,
        "EMBEDDING_CPU_RESERVATION": c.embedding_reservation,
        "EMBEDDING_CPU_RESERVATION_MAX_FRACTION": c.embedding_reservation_max_fraction,
        "INDEX_HEADROOM": c.index_headroom,
        "INDEX_MIN_PER_TIER": c.index_min_per_tier,
        "INDEX_MAX_PER_TIER": c.index_max_per_tier,
        "INDEX_SLOTS_PER_CPU": c.index_slots_per_cpu,
        "INDEX_TOTAL_MIN": c.index_total_min,
        "INDEX_TOTAL_MAX": c.index_total_max,
        "INDEX_HEAVY_MAX_SHARE": c.index_heavy_max_share,
    }.items():
        monkeypatch.setattr(policy, attr, value)


@pytest.mark.parametrize("constants", CONSTANTS, ids=["today", "slots", "small_caps", "big_reserve"])
def test_grid(monkeypatch: pytest.MonkeyPatch, constants: frozen.Constants) -> None:
    _patch(monkeypatch, constants)
    rows = 0
    for cpus, mem, env_parse, env_index, workers, reserve in itertools.product(
        CPUS, MEM_GB, ENV_PARSE, ENV_INDEX, WORKERS, (False, True)
    ):
        got = policy.resolve_ceilings(
            _snap(cpus, mem), env_parse, env_index, workers, reserve_embedding_cpus=reserve
        )
        want = frozen.resolve(
            cpus, env_parse, env_index, workers, reserve_embedding_cpus=reserve, c=constants
        )
        row = (cpus, mem, env_parse, env_index, workers, reserve)
        assert (got.heavy, got.light, got.index_heavy, got.index_light) == want, (
            f"ceilings differ at cpus={row[0]} mem={row[1]} env_parse={row[2]} env_index={row[3]} "
            f"workers={row[4]} reserve={row[5]}: live {got} frozen {want}"
        )
        rows += 1
    assert rows == 14400


def test_the_grid_detects_a_changed_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: a different index total cap is seen at 17 CPUs and above."""
    _patch(monkeypatch, frozen.Constants())
    monkeypatch.setattr(policy, "INDEX_TOTAL_MAX", 97)
    got = policy.resolve_ceilings(_snap(32, 64), None, None)
    want = frozen.resolve(32, None, None)
    assert (got.heavy, got.light, got.index_heavy, got.index_light) != want


@pytest.mark.parametrize("cap", CAPS, ids=lambda c: c.name)
def test_every_fixed_cap_states_its_source(cap) -> None:
    from app.capacity.knobs import SOURCE_CLASSES

    assert cap.source_class in SOURCE_CLASSES
    assert cap.reason.strip()
    assert getattr(policy, cap.name) == pytest.approx(cap.value), (
        f"{cap.name} changed to {getattr(policy, cap.name)!r}: restate its source in app/capacity/knobs.py"
    )


def test_the_default_constants_are_todays() -> None:
    """The frozen copy's defaults are the governor's own."""
    today = frozen.Constants()
    assert replace(today) == today
    for name, attr in (
        ("heavy_slots_per_cpu", "HEAVY_PARSE_SLOTS_PER_CPU"),
        ("light_slots_per_cpu", "LIGHT_PARSE_SLOTS_PER_CPU"),
        ("light_parse_max", "LIGHT_PARSE_MAX"),
        ("embedding_reservation", "EMBEDDING_CPU_RESERVATION"),
        ("index_headroom", "INDEX_HEADROOM"),
        ("index_max_per_tier", "INDEX_MAX_PER_TIER"),
        ("index_total_max", "INDEX_TOTAL_MAX"),
    ):
        assert getattr(today, name) == getattr(policy, attr)
