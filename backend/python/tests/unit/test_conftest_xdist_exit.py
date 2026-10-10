"""The shared conftest force-exits xdist runs; the exit status and summary must survive it."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("xdist") is None, reason="pytest-xdist is not installed"
)


def _run_under_xdist(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "test_probe.py"
    probe.write_text(body)
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", "-n", "2", "-p", "no:cacheprovider",
            "--confcutdir", str(tmp_path), "-p", "conftest", "--rootdir", str(tmp_path), str(probe),
        ],
        cwd=TESTS_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_failing_parallel_run_exits_non_zero_and_keeps_its_summary(tmp_path: Path) -> None:
    run = _run_under_xdist(tmp_path, "def test_fails():\n    assert False\n\ndef test_passes():\n    assert True\n")
    assert run.returncode == 1, run.stdout + run.stderr
    assert "1 failed, 1 passed" in run.stdout


def test_a_passing_parallel_run_exits_zero(tmp_path: Path) -> None:
    run = _run_under_xdist(tmp_path, "def test_passes():\n    assert True\n")
    assert run.returncode == 0, run.stdout + run.stderr
