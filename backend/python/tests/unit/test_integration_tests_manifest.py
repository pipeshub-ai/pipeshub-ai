"""integration-tests runs this package's code, so it installs this package's pins.

A second copy of the pins in integration-tests/pyproject.toml drifted from this
manifest, and the suites then ran app code against releases the services never
use. That manifest depends on this package instead and names only what the
suites add on top.
"""

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

BACKEND_MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"
INTEGRATION_MANIFEST = Path(__file__).resolve().parents[4] / "integration-tests" / "pyproject.toml"


def _manifest(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _names(manifest: dict) -> set[str]:
    return {canonicalize_name(Requirement(d).name) for d in manifest["project"]["dependencies"]}


@pytest.fixture
def integration() -> dict:
    if not INTEGRATION_MANIFEST.exists():
        pytest.skip("integration-tests/ is not part of this checkout")
    return _manifest(INTEGRATION_MANIFEST)


def test_integration_tests_install_this_package_from_the_checkout(integration: dict) -> None:
    backend_name = _manifest(BACKEND_MANIFEST)["project"]["name"]
    assert canonicalize_name(backend_name) in _names(integration)
    source = integration["tool"]["uv"]["sources"][backend_name]
    assert (INTEGRATION_MANIFEST.parent / source["path"]).resolve() == BACKEND_MANIFEST.parent


def test_integration_tests_do_not_pin_a_backend_dependency_again(integration: dict) -> None:
    backend = _manifest(BACKEND_MANIFEST)
    assert _names(integration) & _names(backend) == set()
