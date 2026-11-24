"""The ``auto`` ceilings (``app.capacity.ceilings``): the legacy ceilings at 16 CPUs or fewer, growing with CPUs and cells above."""

from __future__ import annotations

import itertools

import pytest

from app.capacity import ceilings as auto
from app.services.resource_governor import policy
from tests.support import legacy_ceilings as frozen

CPUS_LOW = [0.25 * i for i in range(1, 65)]  # 0.25 .. 16
CPUS_HIGH = [16 + 0.25 * i for i in range(0, 4 * 496 + 1)]  # 16 .. 512


def _resolve(cpus, *, profile, cells=1, workers=1, parse=None, index=None, reserve=True) -> tuple[int, ...]:
    """``legacy`` is the frozen copy of today's chain; ``auto`` is ``app.capacity.ceilings`` (fleet, then split)."""
    if profile == "legacy":
        return frozen.resolve(cpus, parse, index, workers, reserve_embedding_cpus=reserve)
    fleet = auto.auto_ceilings(cpus, parse, index, cells=cells, reserve_embedding_cpus=reserve)
    per = auto.per_cell(fleet, workers)
    return per.as_tuple()


def test_auto_equals_legacy_up_to_16_cpus() -> None:
    rows = 0
    for cpus, parse, index, workers, reserve in itertools.product(
        CPUS_LOW, [None, 1, 5, 40], [None, 1, 30], [1, 2], (False, True)
    ):
        want = _resolve(cpus, profile="legacy", workers=workers, parse=parse, index=index, reserve=reserve)
        got = _resolve(cpus, profile="auto", workers=workers, parse=parse, index=index, reserve=reserve)
        assert got == want, f"cpus={cpus} parse={parse} index={index} workers={workers} reserve={reserve}: auto {got} legacy {want}"
        rows += 1
    assert rows == 64 * 4 * 3 * 2 * 2


@pytest.mark.parametrize(
    "cpus,cells,fleet",
    [(24, 1, (21, 102, 42, 102)), (32, 2, (28, 136, 56, 136)), (64, 4, (56, 272, 112, 272)), (128, 8, (112, 544, 224, 544))],
)
def test_fleet_ceilings_above_16(cpus, cells, fleet) -> None:
    assert _resolve(cpus, profile="auto", cells=cells) == fleet
    per_cell = _resolve(cpus, profile="auto", cells=cells, workers=cells)
    assert per_cell == tuple(max(1, v // cells) for v in fleet)


def test_monotone_above_16_and_legacy_dips_are_kept() -> None:
    prev = None
    for cpus in CPUS_HIGH:
        cells = max(1, int(cpus // 16))
        got = _resolve(cpus, profile="auto", cells=cells)
        if prev is not None:
            assert all(g >= p for g, p in zip(got, prev, strict=True)), f"fleet ceilings fell at {cpus} CPUs: {prev} -> {got}"
        prev = got
    for lo, hi in ((2.75, 3.0), (3.75, 4.0)):  # recorded legacy dips: auto equals legacy there
        assert _resolve(hi, profile="auto", reserve=False)[1] < _resolve(lo, profile="auto", reserve=False)[1]
        assert _resolve(hi, profile="auto", reserve=False) == _resolve(hi, profile="legacy", reserve=False)


def test_the_96_cap_does_not_come_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: a fixed total cap in ``auto`` stops the monotone growth at 17 CPUs."""
    assert _resolve(48, profile="auto", cells=3)[2] + _resolve(48, profile="auto", cells=3)[3] > 96
    real = auto.auto_ceilings

    def capped(cpu_quota, env_parse, env_index, **kw) -> auto.FleetCeilings:
        return real(cpu_quota, env_parse, min(env_index or 96, 96), **kw)

    monkeypatch.setattr(auto, "auto_ceilings", capped)
    assert sum(_resolve(48, profile="auto", cells=3)[2:]) <= 96


def test_auto_constants_default_to_the_governors() -> None:
    c = auto.Constants()
    assert (c.heavy_slots_per_cpu, c.light_slots_per_cpu, c.light_parse_max) == (
        policy.HEAVY_PARSE_SLOTS_PER_CPU, policy.LIGHT_PARSE_SLOTS_PER_CPU, policy.LIGHT_PARSE_MAX)
    assert (c.index_headroom, c.index_min_per_tier, c.index_max_per_tier, c.index_slots_per_cpu, c.index_total_min) == (
        policy.INDEX_HEADROOM, policy.INDEX_MIN_PER_TIER, policy.INDEX_MAX_PER_TIER, policy.INDEX_SLOTS_PER_CPU, policy.INDEX_TOTAL_MIN)
    assert (c.embedding_reservation, c.embedding_reservation_max_fraction, c.index_heavy_max_share) == (
        policy.EMBEDDING_CPU_RESERVATION, policy.EMBEDDING_CPU_RESERVATION_MAX_FRACTION, policy.INDEX_HEAVY_MAX_SHARE)
