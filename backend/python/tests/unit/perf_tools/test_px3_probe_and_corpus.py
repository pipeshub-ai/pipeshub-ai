"""PX-3 measurement tooling: phase ring tokens, per-name freeze attribution, reproducible archives."""
from __future__ import annotations

import time
import zipfile
from typing import TYPE_CHECKING

import pytest

from tests.perf.parse_bench import loop_probe, px3_corpus
from tests.perf.parse_bench.loop_probe import Freeze, PhaseRing, attribute

if TYPE_CHECKING:
    from pathlib import Path


def _ring(*spans: tuple[str, float, float]) -> PhaseRing:
    ring = PhaseRing(capacity=16)
    for i, (name, a, b) in enumerate(spans):
        ring.names[i], ring.starts[i], ring.ends[i] = name, a, b
        ring.gens[i] = i + 1
    ring.n = len(spans)
    return ring


# -- ring generation token ------------------------------------------------------------------------


def test_a_stale_close_does_not_overwrite_a_newer_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(float(i) for i in range(1, 100))
    monkeypatch.setattr(loop_probe.time, "perf_counter", lambda: next(clock))
    ring = PhaseRing(capacity=2)
    outer = ring.open("outer")  # slot 0, t=1
    ring.close(ring.open("a"))  # slot 1
    ring.close(ring.open("b"))  # slot 0 again: overwrites outer
    newer_end = ring.ends[0]
    assert ring.close(outer) is False
    assert ring.ends[0] == newer_end
    assert ring.names[0] == "b"
    assert ring.stale_closes == 1
    assert ring.overwritten == 1


def test_a_live_close_still_records_the_end() -> None:
    ring = PhaseRing(capacity=4)
    token = ring.open("p")
    assert ring.close(token) is True
    assert ring.ends[0] > 0 and ring.stale_closes == 0


# -- attribution ----------------------------------------------------------------------------------


def test_one_freeze_over_two_same_name_intervals_counts_once() -> None:
    ring = _ring(("json.walk", 10.0, 10.2), ("json.walk", 10.05, 10.15))  # 200 ms and nested 100 ms
    out = attribute([Freeze(10.0, 10.2)], ring)
    assert out["json.walk"]["freezes"] == 1
    assert out["json.walk"]["overlap_ms"] == pytest.approx(200.0, abs=0.01)


def test_overlap_of_disjoint_same_name_intervals_is_summed_and_names_stay_separate() -> None:
    ring = _ring(("a", 0.0, 0.1), ("a", 0.15, 0.2), ("b", 0.0, 0.2))
    out = attribute([Freeze(0.0, 0.2)], ring)
    assert out["a"] == {"freezes": 1, "overlap_ms": pytest.approx(150.0, abs=0.01), "max_freeze_ms": 200.0}
    assert out["b"]["freezes"] == 1 and out["b"]["overlap_ms"] == pytest.approx(200.0, abs=0.01)


def test_a_freeze_outside_every_phase_is_unattributed() -> None:
    out = attribute([Freeze(5.0, 5.2)], _ring(("a", 0.0, 1.0)))
    assert list(out) == ["(no parse phase: sink, GC or other)"]


# -- reproducible archives ------------------------------------------------------------------------


def test_epub_and_xlsx_bytes_are_identical_across_generations_with_one_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks = iter(range(10_000))
    # A different "now" on every call: members stamped from the clock would differ between runs.
    monkeypatch.setattr(
        zipfile.time, "localtime", lambda *_a: time.gmtime(1_600_000_000 + next(ticks) * 40_000_000)
    )
    one, two = tmp_path / "one", tmp_path / "two"
    px3_corpus.generate(one, profile="smoke", seed=7, max_mb=64)
    px3_corpus.generate(two, profile="smoke", seed=7, max_mb=64)
    archives = [p for p in sorted(one.iterdir()) if p.suffix in {".epub", ".xlsx"}]
    assert archives
    for p in archives:
        assert p.read_bytes() == (two / p.name).read_bytes(), p.name
    with zipfile.ZipFile(archives[0]) as z:
        assert {i.date_time for i in z.infolist()} == {(2020, 1, 1, 0, 0, 0)}
