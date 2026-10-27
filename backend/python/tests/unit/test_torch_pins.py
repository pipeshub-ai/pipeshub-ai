"""The CPU torch the image installs stays on a fixed release, and integration-tests uses the same one.

torch is not in the backend manifest: Dockerfile.base installs the CPU build before
the manifest so the resolver never pulls the CUDA wheels. Nothing else would notice
it falling behind an advisory or drifting from the build integration-tests runs.
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[4]
DOCKERFILE_BASE = ROOT / "Dockerfile.base"
INTEGRATION_MANIFEST = ROOT / "integration-tests" / "pyproject.toml"

# GHSA-rrmf-rvhw-rf47
TORCH_FLOOR = Version("2.13.0")


def _image_arg(name: str) -> Version:
    if not DOCKERFILE_BASE.exists():
        pytest.skip("Dockerfile.base is not part of this checkout")
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE_BASE.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"Dockerfile.base has no ARG {name}"
    return Version(match.group(1))


def _integration_pin(package: str) -> Version:
    if not INTEGRATION_MANIFEST.exists():
        pytest.skip("integration-tests/ is not part of this checkout")
    dependencies = tomllib.loads(INTEGRATION_MANIFEST.read_text(encoding="utf-8"))["project"]["dependencies"]
    specifiers = [list(Requirement(d).specifier) for d in dependencies if Requirement(d).name == package]
    assert specifiers and [s.operator for s in specifiers[0]] == ["=="], f"integration-tests must pin {package} with =="
    return Version(specifiers[0][0].version)


def test_the_image_installs_a_torch_with_the_advisory_fixed() -> None:
    assert _image_arg("TORCH_VERSION") >= TORCH_FLOOR


@pytest.mark.parametrize(("package", "arg"), [("torch", "TORCH_VERSION"), ("torchvision", "TORCHVISION_VERSION")])
def test_integration_tests_run_the_image_build(package: str, arg: str) -> None:
    assert _integration_pin(package) == _image_arg(arg)
