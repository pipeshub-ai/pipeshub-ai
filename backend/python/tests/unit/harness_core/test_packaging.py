"""PK-1: harness_core and harness_engines build and import as standalone distributions."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[3]
DISTRIBUTIONS = ("harness_core", "harness_engines")


def _subpackages(root: Path) -> set[str]:
    return {
        ".".join(init.parent.relative_to(PYTHON_ROOT).parts)
        for init in root.rglob("__init__.py")
        if "__pycache__" not in init.parts
    }


@pytest.mark.parametrize("package", DISTRIBUTIONS)
def test_declared_packages_match_the_tree(package: str) -> None:
    root = PYTHON_ROOT / package
    declared = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["packages"]
    assert set(declared) == _subpackages(root)


def _build_wheel(package: str, work: Path) -> Path:
    source = work / f"src-{package}"
    shutil.copytree(PYTHON_ROOT / package, source, ignore=shutil.ignore_patterns("__pycache__"))
    dist = work / "dist"
    code = (
        "import sys; from setuptools import build_meta as b; "
        "print(b.build_wheel(sys.argv[1]))"
    )
    built = subprocess.run(
        [sys.executable, "-c", code, str(dist)], cwd=source, capture_output=True, text=True, check=True
    )
    return dist / built.stdout.strip().splitlines()[-1]


@pytest.mark.timeout(240)
def test_wheels_install_and_import_without_the_app(tmp_path: Path) -> None:
    site = tmp_path / "site"
    for package in DISTRIBUTIONS:
        with zipfile.ZipFile(_build_wheel(package, tmp_path)) as wheel:
            wheel.extractall(site)
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import harness_engines.opencode, harness_engines.codex, harness_core.engines.registry, harness_core.sandbox.ports;"
        "import harness_core.testing.engines.contract, harness_engines.opencode.testing.fake_server;"
        "import harness_core.invoker, harness_core.testing.contracts.tool_catalog;"
        "assert harness_engines.__file__.startswith(sys.argv[1]), harness_engines.__file__;"
        "assert harness_core.__file__.startswith(sys.argv[1]), harness_core.__file__;"
        "assert not [m for m in sys.modules if m == 'app' or m.startswith('app.')];"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(site)], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
