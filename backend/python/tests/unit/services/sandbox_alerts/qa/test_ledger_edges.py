"""Edge cases of the per-worker load ledger, read back through the alert window."""

from __future__ import annotations

import json
import math
import os
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.capacity import HoldReason
from app.agent_loop_lib.sandbox.load_ledger import FLAG_QUALIFYING, KEEP_S, LoadLedger
from app.services.sandbox_alerts.window import read_window

if TYPE_CHECKING:
    from pathlib import Path

T0 = 1_800_000_000.0


class Clock:
    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make(tmp_path: Path, *, mode: str = "compose", backed_off=lambda: False) -> tuple[LoadLedger, Clock]:
    clock = Clock()
    return LoadLedger(tmp_path / "state", mode=mode, clock=clock, backed_off=backed_off), clock


@pytest.mark.parametrize("waited", [math.inf, -math.inf, math.nan, -5.0, 1e12])
def test_an_impossible_wait_is_counted_without_failing_or_stalling(tmp_path: Path, waited: float) -> None:
    ledger, _ = make(tmp_path)
    ledger.outcome("refused", waited, HoldReason.SLOTS_FULL)
    data = ledger.to_json()
    assert sum(b["refused"] for b in data["minutes"].values()) == 1
    assert len(data["ticks"]) <= KEEP_S // 10 + 2
    assert all(math.isfinite(b["maxWaitS"]) for b in data["minutes"].values())


def test_a_back_off_check_that_fails_leaves_memory_waits_unqualified(tmp_path: Path) -> None:
    def broken() -> bool:
        raise OSError("state unreadable")

    ledger, _ = make(tmp_path, backed_off=broken)
    ledger.outcome("refused", 30, HoldReason.LIVE_MEMORY)
    bucket = next(iter(ledger.to_json()["minutes"].values()))
    assert (bucket["refused"], bucket["refusedUnqualified"]) == (0, 1)


def test_a_sidecar_does_not_even_ask_whether_indexing_backed_off(tmp_path: Path) -> None:
    def never() -> bool:
        raise AssertionError("not asked on a sidecar")

    ledger, _ = make(tmp_path, mode="helm_dind", backed_off=never)
    ledger.outcome("granted", 5, HoldReason.HOST_MEMORY_FLOOR)
    bucket = next(iter(ledger.to_json()["minutes"].values()))
    assert bucket["waited"] == 1


def test_two_ledgers_of_one_process_write_their_own_files_and_both_count(tmp_path: Path) -> None:
    first, clock = make(tmp_path)
    second = LoadLedger(tmp_path / "state", mode="compose", clock=clock, backed_off=lambda: False)
    assert first.path != second.path
    first.outcome("granted", 0, None)
    second.outcome("refused", 20, HoldReason.SLOTS_FULL)
    totals = read_window(tmp_path / "state" / "load", T0, 1800)
    assert (totals.files, totals.runs, totals.refused) == (2, 2, 1)
    assert totals.busy_ticks >= 1


def test_a_clock_stepped_back_does_not_bring_old_counts_into_the_window(tmp_path: Path) -> None:
    ledger, clock = make(tmp_path)
    for _ in range(20):
        ledger.outcome("refused", 120, HoldReason.SLOTS_FULL)
    clock.now = T0 - 1800  # NTP stepped the clock back half an hour
    ledger.outcome("granted", 0, None)
    totals = read_window(tmp_path / "state" / "load", clock.now, 1800)
    assert (totals.runs, totals.refused, totals.busy_ticks) == (1, 0, 0)


def test_a_failed_flush_is_retried_by_close(tmp_path: Path) -> None:
    ledger, _ = make(tmp_path)
    ledger.outcome("granted", 0, None)
    assert ledger.path is not None and ledger.path.exists()
    load = ledger.path.parent
    os.chmod(load, 0o500)
    try:
        ledger.outcome("refused", 10, HoldReason.SLOTS_FULL)
    finally:
        os.chmod(load, 0o700)
    if os.getuid() != 0:
        assert json.loads(ledger.path.read_text())["minutes"][str(int(T0 // 60))]["refused"] == 0
    ledger.close()
    assert json.loads(ledger.path.read_text())["minutes"][str(int(T0 // 60))]["refused"] == 1


def test_a_cancelled_wait_marks_the_ticks_held_but_not_busy(tmp_path: Path) -> None:
    ledger, _ = make(tmp_path)
    ledger.outcome("cancelled", 60, HoldReason.SLOTS_FULL)
    assert all(not flags & FLAG_QUALIFYING for flags in ledger.to_json()["ticks"].values())
    totals = read_window(tmp_path / "state" / "load", T0, 1800)
    assert (totals.runs, totals.cancelled_waiting, totals.busy_ticks) == (0, 1, 0)
