"""The Neo4j driver stays on a release that cannot freeze the event loop.

neo4j 6.4.0 busy-loops in pool acquisition when the acquisition deadline has
passed while an idle connection sits in the pool (neo4j-python-driver#1360).
The coroutine never yields, so every request the service handles stops, health
checks included, until the process is restarted. 6.4.1 raises the acquisition
timeout instead. A range such as ``>=5.0.0`` let each image build pick up
whatever was newest, which is how 6.4.0 shipped.

neo4j-rust-ext is built for one driver release, so the two move together.
"""

import importlib.metadata
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

BACKEND_MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"

SPINS_IN_POOL_ACQUISITION = frozenset({Version("6.4.0")})


def _pinned(manifest: Path, package: str) -> Version:
    dependencies = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["dependencies"]
    requirements = {Requirement(d).name.lower().replace("_", "-"): Requirement(d) for d in dependencies}
    specifiers = list(requirements[package].specifier)
    assert [s.operator for s in specifiers] == ["=="], (
        f"{manifest.name} must pin {package} with == so a rebuild cannot pick up a new release"
    )
    return Version(specifiers[0].version)


def _same_release(driver: Version, extension: Version) -> bool:
    # The extension appends its own build number: 6.4.1.0 is built for 6.4.1.
    return extension.release[:3] == driver.release[:3]


def test_the_backend_pins_a_driver_that_does_not_spin() -> None:
    assert _pinned(BACKEND_MANIFEST, "neo4j") not in SPINS_IN_POOL_ACQUISITION


def test_the_rust_extension_is_pinned_to_the_drivers_release() -> None:
    assert _same_release(_pinned(BACKEND_MANIFEST, "neo4j"), _pinned(BACKEND_MANIFEST, "neo4j-rust-ext"))


def test_the_installed_driver_does_not_spin() -> None:
    assert Version(importlib.metadata.version("neo4j")) not in SPINS_IN_POOL_ACQUISITION


def test_the_installed_rust_extension_matches_the_installed_driver() -> None:
    try:
        extension = Version(importlib.metadata.version("neo4j-rust-ext"))
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("neo4j-rust-ext is not installed here")
    assert _same_release(Version(importlib.metadata.version("neo4j")), extension)
