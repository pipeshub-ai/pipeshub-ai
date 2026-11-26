"""Edge cases of reading the ledger files over the alert window."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.sandbox.load_ledger import FLAG_HELD, FLAG_QUALIFYING
from app.services.sandbox_alerts.config import AlertSettings
from app.services.sandbox_alerts.evaluator import evaluate
from app.services.sandbox_alerts.window import STALE_AFTER_S, WindowTotals, read_window
from tests.support.cpu_budget import thread_cpu

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_800_000_000.0
BUSY = FLAG_QUALIFYING | FLAG_HELD


def write(path: Path, data: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def ledger(*, minutes: dict | None = None, ticks: dict | None = None, updated: float = NOW) -> dict:
    return {
        "v": 1, "pid": 1, "startedAt": NOW - 100, "updatedAt": updated, "host": "h",
        "tickSeconds": 10, "minuteSeconds": 60,
        "minutes": {str(k): v for k, v in (minutes or {}).items()},
        "ticks": {str(k): v for k, v in (ticks or {}).items()},
    }


def every_tick(now: float, back_s: float) -> dict[int, int]:
    return dict.fromkeys(range(int((now - back_s) // 10) - 2, int(now // 10) + 3), BUSY)


@given(offset=st.floats(min_value=0, max_value=9.999), window_s=st.sampled_from([120, 600, 1800, 3600]))
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_a_window_busy_all_the_time_is_exactly_full(tmp_path: Path, offset: float, window_s: int) -> None:
    now = NOW + offset
    write(tmp_path / "w-1-1.json", ledger(ticks=every_tick(now, window_s + 60)))
    totals = read_window(tmp_path, now, window_s)
    assert totals.busy_ticks == window_s // 10
    assert evaluate(totals, AlertSettings(window_s=window_s)).busy_percent == 100.0


def test_a_window_that_is_not_whole_ticks_never_reads_above_100_percent() -> None:
    totals = WindowTotals(window_s=125, busy_ticks=13, waited=50)
    decision = evaluate(totals, AlertSettings(window_s=125))
    assert decision.busy_percent == 100.0


def test_minutes_after_now_are_not_counted(tmp_path: Path) -> None:
    """A clock stepped back leaves buckets "in the future"; they are not this window."""
    now_minute = int(NOW // 60)
    write(tmp_path / "w-1-1.json", ledger(minutes={
        now_minute: {"runs": 1},
        now_minute + 30: {"runs": 20, "refused": 20, "waited": 20},
    }, ticks={int(NOW // 10) + 100: BUSY}))
    totals = read_window(tmp_path, NOW, 1800)
    assert (totals.runs, totals.refused, totals.busy_ticks) == (1, 0, 0)


def test_files_of_dead_workers_count_until_stale(tmp_path: Path) -> None:
    write(tmp_path / "w-111-1.json", ledger(minutes={int(NOW // 60): {"runs": 3}}, updated=NOW - 60))
    write(tmp_path / "w-111-2.json", ledger(minutes={int(NOW // 60): {"runs": 4}}))  # pid reused
    gone = write(tmp_path / "w-222-1.json", ledger(updated=NOW - STALE_AFTER_S - 1))
    totals = read_window(tmp_path, NOW, 1800)
    assert (totals.runs, totals.files) == (7, 2)
    assert totals.stale == (gone,)


def test_a_fresh_tmp_file_is_left_alone_and_an_old_one_is_listed(tmp_path: Path) -> None:
    fresh = write(tmp_path / "w-1-1.json.tmp", {})
    old = write(tmp_path / "w-2-1.json.tmp", {})
    import os
    os.utime(old, (NOW - STALE_AFTER_S - 10, NOW - STALE_AFTER_S - 10))
    os.utime(fresh, (NOW, NOW))
    totals = read_window(tmp_path, NOW, 1800)
    assert totals.stale == (old,)
    assert totals.files == 0


def test_garbage_values_are_ignored(tmp_path: Path) -> None:
    minute = int(NOW // 60)
    write(tmp_path / "w-1-1.json", [1, 2, 3])
    write(tmp_path / "w-2-1.json", {"v": 1, "minutes": [1], "ticks": "x", "updatedAt": "soon"})
    write(tmp_path / "w-3-1.json", ledger(
        minutes={minute: {"runs": -5, "waited": True, "refused": 2.5, "waitedByReason": {"memory": "9"},
                          "maxWaitS": "long"},
                 "1.5": {"runs": 9}, "x": {"runs": 9}, minute - 1: "bucket"},
        ticks={int(NOW // 10): True, "y": BUSY, int(NOW // 10) - 1: -1},
    ))
    (tmp_path / "w-4-1.json").write_bytes(b"\xff\xfe\x00garbage")
    (tmp_path / "other.json").write_text(json.dumps(ledger(minutes={minute: {"runs": 50}})))
    totals = read_window(tmp_path, NOW, 1800)
    assert (totals.runs, totals.waited, totals.refused, totals.busy_ticks) == (0, 0, 0, 0)
    assert totals.waited_by_reason["memory"] == 0


def test_a_huge_file_is_read_in_reasonable_time(tmp_path: Path) -> None:
    write(tmp_path / "w-1-1.json", ledger(
        minutes={m: {"runs": 1} for m in range(0, 200_000)},
        ticks=dict.fromkeys(range(0, 200000), BUSY),
    ))
    with thread_cpu() as cpu:
        totals = read_window(tmp_path, NOW, 1800)
    assert totals.runs == 0
    # The parse is this thread's own work (thread_cpu: no time off the CPU, no collection of a large heap); a
    # quadratic read of 200,000 minutes would take minutes.
    assert cpu.seconds < 5


@given(data=st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=5),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4),
    max_leaves=30,
))
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_any_json_never_breaks_the_reader(tmp_path: Path, data: object) -> None:
    for blob in (data, {"v": 1, "minutes": data, "ticks": data, "updatedAt": NOW},
                 {"v": 1, "minutes": {str(int(NOW // 60)): data}, "ticks": {str(int(NOW // 10)): data}}):
        write(tmp_path / "w-1-1.json", blob)
        totals = read_window(tmp_path, NOW, 1800)
        assert totals.runs >= 0 and totals.busy_ticks <= 180
