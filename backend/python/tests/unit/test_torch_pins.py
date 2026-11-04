"""The CPU torch the image installs stays on a fixed release, and integration-tests uses the same one.

The image installs the backend lock, which takes torch from the CPU index so the
resolver never pulls the CUDA wheels. Nothing else would notice it falling behind
an advisory or drifting from the build integration-tests runs.
"""

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[4]
BACKEND_LOCK = ROOT / "backend" / "python" / "uv.lock"
INTEGRATION_MANIFEST = ROOT / "integration-tests" / "pyproject.toml"

# GHSA-rrmf-rvhw-rf47
TORCH_FLOOR = Version("2.13.0")


def _image_version(package: str) -> Version:
    if not BACKEND_LOCK.exists():
        pytest.skip("backend/python/uv.lock is not part of this checkout")
    locked = [p for p in tomllib.loads(BACKEND_LOCK.read_text(encoding="utf-8"))["package"] if p["name"] == package]
    # One entry per platform, and the x86 CPU wheels carry a local version (2.14.1+cpu):
    # every platform must install the same release, which is what advisories name.
    releases = {Version(p["version"]).public for p in locked}
    assert len(releases) == 1, f"the backend lock must install one {package} release, not {sorted(releases)}"
    return Version(releases.pop())


def _integration_pin(package: str) -> Version:
    if not INTEGRATION_MANIFEST.exists():
        pytest.skip("integration-tests/ is not part of this checkout")
    dependencies = tomllib.loads(INTEGRATION_MANIFEST.read_text(encoding="utf-8"))["project"]["dependencies"]
    specifiers = [list(Requirement(d).specifier) for d in dependencies if Requirement(d).name == package]
    assert specifiers and [s.operator for s in specifiers[0]] == ["=="], f"integration-tests must pin {package} with =="
    return Version(specifiers[0][0].version)


def test_the_image_installs_a_torch_with_the_advisory_fixed() -> None:
    assert _image_version("torch") >= TORCH_FLOOR


@pytest.mark.parametrize("package", ["torch", "torchvision"])
def test_integration_tests_run_the_image_build(package: str) -> None:
    assert _integration_pin(package) == _image_version(package)
