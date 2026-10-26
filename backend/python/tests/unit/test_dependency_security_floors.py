"""Dependencies with a fixed advisory must not slide back below the fixed release.

The image installs the manifest on top of a published base image, and uv keeps a
package that still satisfies the manifest, so a transitive dependency only moves
when the manifest names it. These checks also keep the integration-test manifest
on the same pins as the backend one.
"""

import importlib.metadata
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

BACKEND_MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"
INTEGRATION_MANIFEST = Path(__file__).resolve().parents[4] / "integration-tests" / "pyproject.toml"

# package -> lowest release that carries the fixes for the advisories listed.
SECURITY_FLOORS = {
    # GHSA-x3q2-h9hx-4r4j, GHSA-cgc7-9qp3-86m3, GHSA-3cr3-8m4c-fpxw, GHSA-p944-4xh2-x776
    "docling": Version("2.132.0"),
    # GHSA-29pf-2h5f-8g72, GHSA-fgcw-684q-jj6r, GHSA-xrqw-3rrv-vx5w, GHSA-x9r9-c232-4q39
    "transformers": Version("5.10.0"),
    # GHSA-29h4-r29x-hchv
    "redshift-connector": Version("2.1.14"),
    # GHSA-vfmq-68hx-4jfw
    "lxml": Version("6.1.0"),
}


def _specifiers(manifest: Path) -> dict[str, str]:
    dependencies = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["dependencies"]
    return {Requirement(d).name.lower().replace("_", "-"): str(Requirement(d).specifier) for d in dependencies}


@pytest.mark.parametrize("package", sorted(SECURITY_FLOORS))
def test_installed_release_is_at_least_the_fixed_one(package: str) -> None:
    assert Version(importlib.metadata.version(package)) >= SECURITY_FLOORS[package]


@pytest.mark.parametrize("package", sorted(SECURITY_FLOORS))
def test_backend_manifest_does_not_admit_a_vulnerable_release(package: str) -> None:
    lower_bounds = [
        Version(spec.version)
        for spec in Requirement(f"{package}{_specifiers(BACKEND_MANIFEST)[package]}").specifier
        if spec.operator in ("==", ">=")
    ]
    assert lower_bounds
    assert min(lower_bounds) >= SECURITY_FLOORS[package]


@pytest.mark.parametrize("package", sorted(SECURITY_FLOORS))
def test_integration_tests_manifest_carries_the_same_pin(package: str) -> None:
    if not INTEGRATION_MANIFEST.exists():
        pytest.skip("integration-tests/ is not part of this checkout")
    integration = _specifiers(INTEGRATION_MANIFEST)
    if package not in integration:
        pytest.skip(f"integration-tests/ does not name {package}")
    assert integration[package] == _specifiers(BACKEND_MANIFEST)[package]
