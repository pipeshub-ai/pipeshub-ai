from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.load_ledger import (
    FLAG_BACKED_OFF,
    FLAG_HELD,
    FLAG_QUALIFYING,
)
from app.services.sandbox_alerts.window import STALE_AFTER_S, read_window

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_800_000_000.0
W = 1800


def bucket(**counts: int) -> dict:
    base = {"runs": 0, "waited": 0, "refused": 0, "waitedUnqualified": 0, "refusedUnqualified": 0,
            "cancelledWaiting": 0, "waitedByReason": {"slots": 0, "memory": 0, "cpu": 0, "orgShare": 0},
            "maxWaitS": 0.0}
    for key, value in counts.items():
        if key in ("slots", "memory", "cpu"):
            base["waitedByReason"][key] = value
        else:
            base[key] = value
    return base


def write(directory: Path, name: str, *, minutes: dict | None = None, ticks: dict | None = None,
          updated: float = NOW, v: int = 1) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps({
        "v": v, "pid": 1, "startedAt": NOW - 100, "updatedAt": updated, "host": "h",
        "tickSeconds": 10, "minuteSeconds": 60,
        "minutes": {str(k): b for k, b in (minutes or {}).items()},
        "ticks": {str(k): f for k, f in (ticks or {}).items()},
    }))
    return path


def minute(offset_s: float) -> int:
    return int((NOW - offset_s) // 60)


def tick(offset_s: float) -> int:
    return int((NOW - offset_s) // 10)


def test_sums_across_files(tmp_path: Path) -> None:
    for i in range(3):
        write(tmp_path, f"w-{i}-1.json", minutes={minute(60): bucket(runs=10, waited=2, refused=1, memory=2)})
    totals = read_window(tmp_path, NOW, W)
    assert (totals.runs, totals.waited, totals.refused, totals.files) == (30, 6, 3, 3)
    assert totals.waited_by_reason["memory"] == 6


def test_busy_ticks_are_ored_not_summed(tmp_path: Path) -> None:
    for i in range(3):
        write(tmp_path, f"w-{i}-1.json", ticks={tick(30): FLAG_QUALIFYING | FLAG_HELD})
    write(tmp_path, "w-9-1.json", ticks={tick(50): FLAG_HELD})
    assert read_window(tmp_path, NOW, W).busy_ticks == 1


def test_only_the_window_counts(tmp_path: Path) -> None:
    write(tmp_path, "w-1-1.json", minutes={
        minute(W + 120): bucket(runs=100),
        minute(W - 120): bucket(runs=1),
    }, ticks={tick(W + 20): FLAG_QUALIFYING, tick(W - 20): FLAG_QUALIFYING})
    totals = read_window(tmp_path, NOW, W)
    assert totals.runs == 1
    assert totals.busy_ticks == 1


def test_invalid_files_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, "w-1-1.json", minutes={minute(60): bucket(runs=1)})
    write(tmp_path, "w-2-1.json", minutes={minute(60): bucket(runs=50)}, v=2)
    (tmp_path / "w-3-1.json").write_text("{not json")
    unreadable = write(tmp_path, "w-4-1.json", minutes={minute(60): bucket(runs=70)})
    os.chmod(unreadable, 0)
    try:
        totals = read_window(tmp_path, NOW, W)
    finally:
        os.chmod(unreadable, 0o600)
    expected = 1 if os.getuid() != 0 else 71
    assert totals.runs == expected


def test_stale_files_are_listed_for_deletion(tmp_path: Path) -> None:
    old = write(tmp_path, "w-1-1.json", updated=NOW - STALE_AFTER_S - 1)
    write(tmp_path, "w-2-1.json", updated=NOW - 60)
    totals = read_window(tmp_path, NOW, W)
    assert totals.stale == (old,)
    assert totals.files == 1


def test_the_back_off_start_is_the_earliest_flagged_tick(tmp_path: Path) -> None:
    write(tmp_path, "w-1-1.json", ticks={tick(600): FLAG_QUALIFYING | FLAG_BACKED_OFF, tick(300): FLAG_BACKED_OFF})
    assert read_window(tmp_path, NOW, W).backed_off_since == tick(600) * 10


def test_a_missing_directory_reads_as_nothing(tmp_path: Path) -> None:
    assert read_window(tmp_path / "absent", NOW, W).files == 0
