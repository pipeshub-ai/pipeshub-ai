"""PY-4, PY-5: ``harness_core.workspace`` stands alone; the app path re-exports it."""

from __future__ import annotations

import pkgutil
import subprocess
import sys
from pathlib import Path

import harness_core.workspace

PYTHON_ROOT = Path(__file__).resolve().parents[4]

# Fails any import of ``app`` so a hidden dependency cannot load it on the side.
_BLOCK_APP = """
import importlib.abc, sys

class _BlockApp(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "app" or name.startswith("app."):
            raise ImportError(f"harness_core imported {name}")
        return None

sys.meta_path.insert(0, _BlockApp())
import importlib
for module in sys.argv[1:]:
    importlib.import_module(module)
assert not any(m == "app" or m.startswith("app.") for m in sys.modules)
print("ok")
"""


def _workspace_modules() -> list[str]:
    package = harness_core.workspace
    return [package.__name__] + [
        f"{package.__name__}.{info.name}" for info in pkgutil.iter_modules(package.__path__)
    ]


def test_workspace_package_imports_without_the_app() -> None:
    modules = _workspace_modules()
    assert len(modules) > 1
    result = subprocess.run(
        [sys.executable, "-c", _BLOCK_APP, *modules],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_the_blocker_catches_an_app_import() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _BLOCK_APP, "app.harness.contracts.workspace"],
        cwd=PYTHON_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "harness_core imported app" in result.stderr


def test_app_shim_re_exports_the_core_objects() -> None:
    from app.harness.contracts import workspace as shim

    for name in shim.__all__:
        assert getattr(shim, name) is getattr(harness_core.workspace, name), name
