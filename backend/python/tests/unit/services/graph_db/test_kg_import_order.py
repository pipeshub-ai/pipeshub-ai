"""Every module of ``postgres.kg`` imports on its own, first, in a fresh interpreter: a cycle between them (a reader importing what the projection imports) only shows when
the module that closes it is the first one a process loads, as a unit test that imports one module alone does."""
from __future__ import annotations

import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.graph_db.postgres import kg

ROOT = Path(__file__).resolve().parents[4]
MODULES = sorted(m.name for m in pkgutil.iter_modules(kg.__path__))


@pytest.mark.parametrize("name", MODULES)
def test_a_kg_module_imports_first_in_a_fresh_process(name: str) -> None:
    run = subprocess.run([sys.executable, "-c", f"import app.services.graph_db.postgres.kg.{name}"], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "PYTHONPATH": str(ROOT)}, check=False)
    assert run.returncode == 0, run.stderr[-2000:]


def test_the_modules_are_found() -> None:
    assert {"grant_marks", "project", "read", "drainer"} <= set(MODULES)
