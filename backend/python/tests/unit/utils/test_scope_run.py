"""PX-3 tooling: ``scope_run`` never leaves a report that describes another run, and an OOM kill of the
scope is reported as a failed run (the evidence an M5 "no OOM kill" gate reads)."""
from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from tests.perf.parse_bench import scope_run

if TYPE_CHECKING:
    from pathlib import Path


def _launcher_exiting(code: int, write: dict | None = None):  # noqa: ANN202
    def launch(_cpus: float, _memory: str, report: Path, _command: list[str]) -> list[str]:
        body = f"import sys, pathlib; sys.exit({code})"
        if write is not None:
            body = f"import json, pathlib; pathlib.Path({str(report)!r}).write_text(json.dumps({write!r})); sys.exit({code})"
            body = "import sys; " + body
        return [sys.executable, "-c", body]

    return launch


def test_a_stale_report_is_replaced_and_an_oom_exit_is_recorded(tmp_path: Path) -> None:
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"stale": True, "oom_kill": 0, "exit_code": 0}))

    rc = scope_run.run_scope(1, "1G", report, ["x"], launcher=_launcher_exiting(scope_run.OOM_SCOPE_EXIT))

    data = json.loads(report.read_text())
    assert rc == scope_run.OOM_SCOPE_EXIT
    assert "stale" not in data
    assert data["failed_run"] is True
    assert data["oom_suspected"] is True
    assert data["killed"] is True
    assert data["oom_kill"] == 1
    assert data["exit_code"] == scope_run.OOM_SCOPE_EXIT


def test_no_report_with_exit_zero_is_a_failed_run(tmp_path: Path) -> None:
    report = tmp_path / "r.json"

    rc = scope_run.run_scope(1, "1G", report, ["x"], launcher=_launcher_exiting(0))

    data = json.loads(report.read_text())
    assert rc != 0
    assert data["failed_run"] is True
    assert data["report_missing"] is True


def test_a_report_written_by_a_clean_run_is_kept_and_not_marked_failed(tmp_path: Path) -> None:
    report = tmp_path / "r.json"

    rc = scope_run.run_scope(
        1, "1G", report, ["x"], launcher=_launcher_exiting(0, {"exit_code": 0, "oom_kill": 0, "memory_peak_bytes": 5})
    )

    data = json.loads(report.read_text())
    assert rc == 0
    assert data["failed_run"] is False
    assert data["memory_peak_bytes"] == 5


def _scope_available() -> bool:
    try:
        done = subprocess.run(
            ["systemd-run", "--user", "--scope", "--collect", "--quiet", "-p", "MemoryMax=256M", "true"],
            capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


@pytest.mark.timeout(60)
@pytest.mark.skipif(not _scope_available(), reason="no user cgroup scope on this host")
def test_a_scope_killed_by_the_memory_cap_is_reported_as_killed(tmp_path: Path) -> None:
    """Real scope: a 96 MB cap around a process that touches 400 MB. A stale 'clean' report is on disk."""
    report = tmp_path / "r.json"
    report.write_text(json.dumps({"stale": True, "oom_kill": 0, "exit_code": 0}))
    hog = "b = bytearray(400 * 1024 * 1024)\nfor i in range(0, len(b), 4096): b[i] = 1\n"

    rc = scope_run.run_scope(1, "96M", report, [sys.executable, "-I", "-c", hog])

    data = json.loads(report.read_text())
    assert rc != 0
    assert "stale" not in data
    assert data["failed_run"] is True
    # either the whole scope died (outer record: killed, oom_suspected) or only the command did (the
    # wrapper read memory.events itself)
    assert data.get("killed") or (data.get("oom_kill") or 0) >= 1
    assert data.get("oom_suspected") or (data.get("oom_kill") or 0) >= 1
