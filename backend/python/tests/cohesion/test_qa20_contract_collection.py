"""QA20-25 (WSP-G5): a test class that only subclasses a contract suite collects the suite's cases.

Such a file has no test of its own, so a broken inheritance (a renamed fixture base, a class pytest skips at
collection, a contract whose cases moved) would leave it silently empty: a green lane that ran nothing.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

PYTHON = Path(__file__).resolve().parents[2]
TESTS = PYTHON / "tests"


def _is_contract(base: ast.expr) -> bool:
    name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
    return name.endswith("Contract")


def _defines_tests(node: ast.ClassDef | ast.Module) -> bool:
    return any(
        isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test")
        for member in node.body
    )


def subclass_only_contract_classes() -> dict[str, list[str]]:
    """Test file (relative to backend/python) -> its ``Test*`` classes that only subclass a contract."""
    found: dict[str, list[str]] = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        with warnings.catch_warnings():
            # A test file's own invalid escape is that file's finding, not this case's.
            warnings.simplefilter("ignore", SyntaxWarning)
            module = ast.parse(path.read_text(encoding="utf-8"))
        if _defines_tests(module):
            continue
        classes = [
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test") and any(map(_is_contract, node.bases))
        ]
        if classes and not any(_defines_tests(node) for node in classes):
            found[str(path.relative_to(PYTHON))] = [node.name for node in classes]
    return found


@pytest.mark.timeout(600)
def test_every_subclass_only_contract_class_collects_cases() -> None:
    classes = subclass_only_contract_classes()
    assert "tests/unit/harness/sandbox_broker/test_workspace_sync_docker.py" in classes
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider", *classes],
        capture_output=True, text=True, check=False, cwd=PYTHON, timeout=600,
    )
    collected = {line.split("::")[0] + "::" + line.split("::")[1] for line in done.stdout.splitlines() if line.count("::") >= 2}
    empty = sorted(f"{path}::{name}" for path, names in classes.items() for name in names if f"{path}::{name}" not in collected)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert empty == [], f"collect nothing: {empty}"
