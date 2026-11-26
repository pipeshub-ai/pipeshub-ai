"""The workers' load files are read as untrusted input.

Anything running as the app's user can put a file in ``<state>/load/``. A
FIFO, a link, a huge or deeply nested file, or numbers that are not finite
or not in range must each be skipped (or read as 0), never hang or break the
evaluation, and never hide the other workers' counts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import TYPE_CHECKING

import pytest

from app.services.sandbox_alerts import window
from app.services.sandbox_alerts.window import MAX_FILE_BYTES, read_window

if TYPE_CHECKING:
    from pathlib import Path

NOW = 1_800_000_000.0
MINUTE = int(NOW // 60)


def _ledger(**bucket: object) -> dict:
    return {
        "v": 1, "pid": 1, "startedAt": NOW - 100, "updatedAt": NOW, "host": "h",
        "tickSeconds": 10, "minuteSeconds": 60,
        "minutes": {str(MINUTE): {"runs": 3, "waited": 1, "refused": 0, **bucket}},
        "ticks": {},
    }


@pytest.fixture
def load(tmp_path: Path) -> Path:
    directory = tmp_path / "load"
    directory.mkdir(mode=0o700)
    (directory / "w-1-1.json").write_text(json.dumps(_ledger()))
    return directory


@pytest.fixture(autouse=True)
def _warn_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window, "_warned", {"at": float("-inf")})


def _read(directory: Path) -> window.WindowTotals:
    """``read_window`` on a thread, failing instead of hanging."""
    result: list[window.WindowTotals] = []
    worker = threading.Thread(target=lambda: result.append(read_window(directory, NOW, 600)), daemon=True)
    worker.start()
    worker.join(5.0)
    assert not worker.is_alive(), "read_window blocked"
    return result[0]


def test_a_fifo_never_blocks_the_evaluation(load: Path) -> None:
    os.mkfifo(load / "w-fifo.json")
    totals = _read(load)
    assert (totals.files, totals.runs) == (1, 3)


def test_a_deeply_nested_file_is_skipped(load: Path, caplog: pytest.LogCaptureFixture) -> None:
    (load / "w-deep.json").write_text("[" * 20_000 + "]" * 20_000)
    (load / "w-nested.json").write_text(json.dumps({"v": 1, "minutes": {"1": {"2": {"3": {"4": {"5": {"6": {}}}}}}}}))
    with caplog.at_level(logging.WARNING):
        totals = _read(load)
    assert (totals.files, totals.runs) == (1, 3)
    assert any("skipping" in r.getMessage() for r in caplog.records)


def test_a_file_over_the_size_cap_is_skipped(load: Path) -> None:
    big = _ledger()
    big["pad"] = "x" * MAX_FILE_BYTES
    (load / "w-big.json").write_text(json.dumps(big))
    assert (_read(load).files, _read(load).runs) == (1, 3)


def test_a_link_is_not_followed(load: Path, tmp_path: Path) -> None:
    target = tmp_path / "forged.json"
    target.write_text(json.dumps(_ledger(runs=500, waited=500, refused=500)))
    (load / "w-link.json").symlink_to(target)
    totals = _read(load)
    assert (totals.files, totals.runs, totals.refused) == (1, 3, 0)


@pytest.mark.parametrize("wait", ["Infinity", "-Infinity", "NaN", "1e400", "-5", "99999999", "true"])
def test_a_wait_that_is_not_a_real_number_reads_as_zero(load: Path, wait: str) -> None:
    text = json.dumps(_ledger(maxWaitS=0)).replace('"maxWaitS": 0', f'"maxWaitS": {wait}')
    (load / "w-2-2.json").write_text(text)
    totals = _read(load)
    assert totals.files == 2
    assert totals.max_wait_s == 0.0


@pytest.mark.parametrize("count", [10**30, -1, 1.5, "7", True])
def test_a_count_out_of_range_reads_as_zero(load: Path, count: object) -> None:
    (load / "w-2-2.json").write_text(json.dumps(_ledger(runs=count, refused=count)))
    totals = _read(load)
    assert (totals.files, totals.runs, totals.refused) == (2, 3, 0)


def test_a_huge_minute_key_is_ignored(load: Path) -> None:
    data = _ledger()
    data["minutes"]["9" * 4000] = {"runs": 50}
    data["ticks"] = {"9" * 4000: 1, str(int(NOW // 10)): 2**40}
    (load / "w-2-2.json").write_text(json.dumps(data))
    totals = _read(load)
    assert (totals.files, totals.runs) == (2, 6)


def test_one_bad_file_never_stops_the_others(load: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (load / "w-2-2.json").write_text(json.dumps(_ledger()))
    real = window._file_totals
    calls = {"n": 0}

    def flaky(data: dict, since: float, now: float) -> tuple:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TypeError("a shape nobody expected")
        return real(data, since, now)

    monkeypatch.setattr(window, "_file_totals", flaky)
    totals = _read(load)
    assert (totals.files, totals.runs) == (1, 3)


def test_the_skip_warning_is_rate_limited(load: Path, caplog: pytest.LogCaptureFixture) -> None:
    (load / "w-bad.json").write_text("{not json")
    with caplog.at_level(logging.WARNING, logger=window.logger.name):
        for _ in range(5):
            _read(load)
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 1
