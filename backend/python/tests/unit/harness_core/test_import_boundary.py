"""K6: ``harness_core`` imports nothing from ``app``."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[3]
CORE = PYTHON_ROOT / "harness_core"
ENGINES = PYTHON_ROOT / "harness_engines"


def _app_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        found.extend(n for n in names if n == "app" or n.startswith("app."))
    return found


@pytest.mark.parametrize("package", [CORE, ENGINES], ids=["harness_core", "harness_engines"])
def test_ast_scan_finds_no_app_imports(package: Path) -> None:
    files = sorted(package.rglob("*.py"))
    assert files, f"{package.name} not found"
    offenders = {
        str(path.relative_to(PYTHON_ROOT)): hits
        for path in files
        if (hits := _app_imports(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}


def test_ast_scan_detects_a_violation() -> None:
    assert _app_imports("import app.config\nfrom app.harness import x\nimport apple") == [
        "app.config",
        "app.harness",
    ]


def test_import_linter_contract_is_kept() -> None:
    pytest.importorskip("importlinter")
    result = subprocess.run(
        [sys.executable, "-c", "from importlinter.cli import lint_imports_command; lint_imports_command()"],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"\b5 kept, 0 broken", result.stdout), result.stdout
