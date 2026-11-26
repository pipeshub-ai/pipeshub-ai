"""TESTINFRA: a ``pytestmark`` in a conftest.py is silently ignored; pytest reads it from test modules only.

The sandbox contract suite's conftest set ``timeout(180, method="thread")`` that way, so its tests ran on the suite's
30 s signal timeout instead, and a slow ``npm install`` under a loaded gate crashed the xdist worker.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]


def test_testinfra_no_conftest_sets_a_pytestmark_pytest_would_ignore() -> None:
    found = []
    for path in sorted(TESTS.rglob("conftest.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
                found.append(f"{path.relative_to(TESTS)}:{node.lineno}")
    assert not found, f"pytestmark in a conftest.py marks nothing; set it in the test modules: {found}"
