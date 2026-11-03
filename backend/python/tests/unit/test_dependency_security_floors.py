"""Dependencies with a fixed advisory must not slide back below the fixed release.

The image installs the manifest on top of a published base image, and uv keeps a
package that still satisfies the manifest, so a transitive dependency only moves
when the manifest names it.
"""

import importlib.metadata
import site
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

BACKEND_MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"

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
    # GHSA-h35f-9h28-mq5c, GHSA-xmj3-9gf7-h52w
    "setuptools": Version("84.1.0"),
}

# package -> malicious releases that must never resolve, whatever the floor.
COMPROMISED_RELEASES = {
    # 1.82.7 and 1.82.8 (March 2026) and 1.107.0 (October 2026) shipped credential stealers; 1.82.8 and 1.107.0
    # also dropped a .pth that runs at every interpreter start.
    "litellm": (Version("1.82.7"), Version("1.82.8"), Version("1.107.0")),
}

# Files those releases drop into site-packages; a .pth runs before any test does.
COMPROMISED_FILES = ("cook.pth", "cook.py", "litellm_init.pth")
# 1.107.0 also dropped a sitecustomize.py (it runs at every interpreter start too). Legitimate environments can hold
# a file of that name, so it counts only when it carries the payload's markers: the lookalike exfiltration host and
# the lock file its daemon holds.
PAYLOAD_SITECUSTOMIZE_MARKERS = ("litellm.dev", "remote_code_loader")


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


@pytest.mark.parametrize("package", sorted(COMPROMISED_RELEASES))
def test_installed_release_is_not_a_compromised_one(package: str) -> None:
    assert Version(importlib.metadata.version(package)) not in COMPROMISED_RELEASES[package]


@pytest.mark.parametrize("package", sorted(COMPROMISED_RELEASES))
def test_manifest_excludes_compromised_releases(package: str) -> None:
    specifier = Requirement(f"{package}{_specifiers(BACKEND_MANIFEST)[package]}").specifier
    for release in COMPROMISED_RELEASES[package]:
        assert not specifier.contains(release, prereleases=True), f"{BACKEND_MANIFEST} admits {package} {release}"


def _compromised_files(directories: set[Path]) -> list[str]:
    found = [str(d / name) for d in directories for name in COMPROMISED_FILES if (d / name).exists()]
    for d in directories:
        custom = d / "sitecustomize.py"
        if custom.is_file() and any(m in custom.read_text(encoding="utf-8", errors="replace") for m in PAYLOAD_SITECUSTOMIZE_MARKERS):
            found.append(str(custom))
    return found


def test_no_compromised_file_in_site_packages() -> None:
    directories = {Path(p) for p in [*site.getsitepackages(), site.getusersitepackages(), *sys.path] if p}
    found = _compromised_files(directories)
    # The payload sends credentials out and its files run at every Python start, this test run included.
    assert not found, (
        f"found {found}: rotate the credentials reachable from this environment, remove these files from a shell "
        "before running Python again, then rebuild the environment"
    )


def test_the_check_finds_each_payload_file_and_leaves_a_legitimate_sitecustomize_alone(tmp_path: Path) -> None:
    clean, dropped = tmp_path / "clean", tmp_path / "dropped"
    clean.mkdir()
    dropped.mkdir()
    (clean / "sitecustomize.py").write_text("import os\nos.environ.setdefault('TZ', 'UTC')\n")
    (dropped / "cook.pth").write_text("import cook\n")
    (dropped / "sitecustomize.py").write_text("LOCK = '/tmp/remote_code_loader.lock'\n")
    assert _compromised_files({clean}) == []
    assert sorted(_compromised_files({dropped})) == sorted([str(dropped / "cook.pth"), str(dropped / "sitecustomize.py")])
