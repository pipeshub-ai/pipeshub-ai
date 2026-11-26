"""The CPU torch the image installs stays on a fixed release, and integration-tests uses the same one.

The image installs the hashed runtime lock (``requirements/runtime.lock``, SUPPLY-CHAIN), which pins the CPU
build for Linux, so the resolver never pulls the CUDA wheels. Nothing else would notice it falling behind an
advisory or drifting from the build integration-tests runs.
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[4]
RUNTIME_LOCK = ROOT / "backend" / "python" / "requirements" / "runtime.lock"
INTEGRATION_MANIFEST = ROOT / "integration-tests" / "pyproject.toml"

# GHSA-rrmf-rvhw-rf47
TORCH_FLOOR = Version("2.13.0")


def _image_pin(package: str) -> Version:
    """The version the runtime lock installs on Linux (its CPU build, ``+cpu``)."""
    match = re.search(
        rf"^{package}==(\S+?)(?:\+cpu)? ;.*sys_platform == 'linux'", RUNTIME_LOCK.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"runtime.lock pins no Linux {package}"
    return Version(match.group(1))


def _integration_pin(package: str) -> Version:
    if not INTEGRATION_MANIFEST.exists():
        pytest.skip("integration-tests/ is not part of this checkout")
    dependencies = tomllib.loads(INTEGRATION_MANIFEST.read_text(encoding="utf-8"))["project"]["dependencies"]
    specifiers = [list(Requirement(d).specifier) for d in dependencies if Requirement(d).name == package]
    assert specifiers and [s.operator for s in specifiers[0]] == ["=="], f"integration-tests must pin {package} with =="
    return Version(specifiers[0][0].version)


def test_the_image_installs_a_torch_with_the_advisory_fixed() -> None:
    assert _image_pin("torch") >= TORCH_FLOOR


@pytest.mark.parametrize("package", ["torch", "torchvision"])
def test_integration_tests_run_the_image_build(package: str) -> None:
    assert _integration_pin(package) == _image_pin(package)
