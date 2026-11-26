"""K4: ``kill9`` really SIGKILLs the process at the point (no ``finally`` runs)."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

from harness_core.failpoints import Arm, Kill9, Nth
from harness_core.testing import write_arms

PYTHON_ROOT = Path(__file__).resolve().parents[3]

CHILD = """
import sys
from pathlib import Path
from harness_core.failpoints import failpoint, registry

registry.install_file(Path(sys.argv[1]))
try:
    for step in range(1, 4):
        print(f"before {step}", flush=True)
        failpoint("worker.tool.after_intent")
        print(f"after {step}", flush=True)
finally:
    print("finally", flush=True)
"""

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL is POSIX-only")


def _run_child(failpoint_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(failpoint_file)],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_k4_kill9_terminates_child_with_sigkill_at_the_point(failpoint_file: Path) -> None:
    write_arms(failpoint_file, {"worker.tool.after_intent": Arm(Kill9(), Nth(2))})
    result = _run_child(failpoint_file)
    assert result.returncode == -signal.SIGKILL, result.stderr
    assert result.stdout.splitlines() == ["before 1", "after 1", "before 2"]


def test_unarmed_child_runs_to_completion(failpoint_file: Path) -> None:
    result = _run_child(failpoint_file)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == ["after 3", "finally"]
