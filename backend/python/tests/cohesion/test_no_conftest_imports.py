"""No module imports the root conftest by name. pytest loads ``tests/conftest.py`` as ``conftest``; ``from
tests.conftest import …`` runs it again as ``tests.conftest``, and that copy's import-time ``NetworkGuard.install``
stacked a second guard that was never registered, so it refused every dial, Docker tests' included (round 3: 450
docker-lane errors). ``NetworkGuard.install`` now refuses a second guard (NG-10); this keeps the import itself out."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from tests.cohesion.support import PYTHON_ROOT

if TYPE_CHECKING:
    from pathlib import Path

ROOT_CONFTEST = ("tests", "conftest")


def _imports_the_root_conftest(tree: ast.AST, path: Path) -> bool:
    package = path.relative_to(PYTHON_ROOT).parent.parts
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [tuple(alias.name.split(".")) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else ()
            module = (*base, *(node.module.split(".") if node.module else ()))
            targets = [module, *[(*module, alias.name) for alias in node.names]]
        else:
            continue
        if any(target in (ROOT_CONFTEST, ("conftest",)) for target in targets):
            return True
    return False


def test_no_module_imports_the_root_conftest() -> None:
    offenders = [
        str(path.relative_to(PYTHON_ROOT))
        for root in ("tests", "harness_core", "app")
        for path in (PYTHON_ROOT / root).rglob("*.py")
        if "conftest" in (text := path.read_text(encoding="utf-8", errors="replace"))
        and _imports_the_root_conftest(ast.parse(text), path)
    ]
    assert offenders == [], f"import shared helpers from tests/support instead: {offenders}"


def test_the_check_sees_each_import_form() -> None:
    at_root, nested = PYTHON_ROOT / "tests" / "x.py", PYTHON_ROOT / "tests" / "unit" / "y" / "x.py"
    for source, path in [
        ("from tests.conftest import X", nested), ("import tests.conftest", nested), ("from tests import conftest", nested),
        ("import conftest", nested), ("from .conftest import X", at_root), ("from ...conftest import X", nested),
    ]:
        assert _imports_the_root_conftest(ast.parse(source), path), source
    for source, path in [
        ("from tests.support.opt_in_lanes import OPT_IN_LANES", nested), ("from .conftest import X", nested),
        ("from tests.integration.conftest import X", nested),
    ]:
        assert not _imports_the_root_conftest(ast.parse(source), path), source
