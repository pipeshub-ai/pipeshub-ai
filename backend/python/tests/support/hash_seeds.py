"""Run a snippet under several `PYTHONHASHSEED` values at once.

Output that iterates a set or a hashed dict can differ between processes while
looking stable inside one test run, which is the case this catches.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SEEDS = ("0", "1", "4242")


def outputs_across_hash_seeds(script: str, *, timeout: float = 150) -> set[str]:
    """The last stdout line of ``script`` under each seed, run in parallel."""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=_ROOT,
            env={
                "PYTHONHASHSEED": seed, "PYTHONPATH": str(_ROOT), "PATH": "",
                "SECRET_KEY": "test-secret-key-for-unit-tests-only-0123456789abcdef",
            },
        )
        for seed in _SEEDS
    ]
    outputs: set[str] = set()
    for proc in procs:
        stdout, stderr = proc.communicate(timeout=timeout)
        assert proc.returncode == 0, stderr[-2000:]
        outputs.add(stdout.strip().splitlines()[-1])
    return outputs
