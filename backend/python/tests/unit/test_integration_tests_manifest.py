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


# Named in both on purpose: uv applies [tool.uv.sources] only to a package a manifest
# names itself, so each manifest names torch to send it to the CPU index. The
# integration pins stay inside the backend's ranges.
SHARED_FOR_THE_CPU_INDEX = {canonicalize_name("torch"), canonicalize_name("torchvision")}


def test_integration_tests_do_not_pin_a_backend_dependency_again(integration: dict) -> None:
    backend = _manifest(BACKEND_MANIFEST)
    assert _names(integration) & _names(backend) - SHARED_FOR_THE_CPU_INDEX == set()


def test_the_shared_cpu_pins_stay_inside_the_backend_ranges(integration: dict) -> None:
    ranges = {
        canonicalize_name(Requirement(d).name): Requirement(d).specifier
        for d in _manifest(BACKEND_MANIFEST)["project"]["dependencies"]
    }
    for dependency in integration["project"]["dependencies"]:
        requirement = Requirement(dependency)
        name = canonicalize_name(requirement.name)
        if name in SHARED_FOR_THE_CPU_INDEX:
            pinned = next(s.version for s in requirement.specifier if s.operator == "==")
            assert ranges[name].contains(pinned), f"{name}=={pinned} is outside the backend's {ranges[name]}"
