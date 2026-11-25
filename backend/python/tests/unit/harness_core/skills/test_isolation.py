"""The skill ports, adapters and contract suites import without PipesHub (Loom, principle 9)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[4]

_PROBE = """
import importlib.abc, sys

BLOCKED = ("app", "docker", "fastapi", "dependency_injector")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, Block())
import harness_core.skills
import harness_core.testing.contracts.skills
assert not any(m.split(".")[0] in BLOCKED for m in sys.modules)
print("isolated")
"""


def test_skill_packages_import_with_pipeshub_blocked() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=PYTHON_ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"
