"""The exact pins in pyproject.toml must agree with what the dependency tree and Dependabot expect.

A resolver rejects a pin that an installed package's requirement excludes, but only at install
time. These checks fail in the unit run instead, and keep the Dependabot holds tied to the pins.
"""

import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"
DEPENDABOT_CONFIG = REPO_ROOT / ".github" / "dependabot.yml"


def _manifest_requirements() -> dict[str, Requirement]:
    dependencies = tomllib.loads(BACKEND_MANIFEST.read_text(encoding="utf-8"))["project"]["dependencies"]
    return {canonicalize_name(Requirement(dependency).name): Requirement(dependency) for dependency in dependencies}


def _exact_pins() -> dict[str, Version]:
    dependencies = tomllib.loads(BACKEND_MANIFEST.read_text(encoding="utf-8"))["project"]["dependencies"]
    pins = {}
    for dependency in dependencies:
        requirement = Requirement(dependency)
        specifiers = list(requirement.specifier)
        if len(specifiers) == 1 and specifiers[0].operator == "==":
            pins[canonicalize_name(requirement.name)] = Version(specifiers[0].version)
    return pins


def _installed_requirements_on_pins(pins: dict[str, Version]) -> list[tuple[str, str, Requirement, Version]]:
    found = []
    for distribution in importlib.metadata.distributions():
        dependent = distribution.metadata["Name"]
        if not dependent:
            continue
        for line in distribution.requires or []:
            requirement = Requirement(line)
            name = canonicalize_name(requirement.name)
            # The project's own requirements are the pins themselves.
            if name not in pins or canonicalize_name(dependent) in (name, "ai-service"):
                continue
            # A requirement behind an extra or another platform does not apply to this install.
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            found.append((dependent, distribution.version, requirement, pins[name]))
    return found


def test_exact_pins_are_found() -> None:
    assert len(_exact_pins()) > 50


def test_every_installed_requirement_on_a_pinned_package_admits_the_pin() -> None:
    pins = _exact_pins()
    # Not-installed packages are skipped by construction: only installed distributions are read.
    refused = [
        f"{dependent} {version} requires {requirement.name}{requirement.specifier}, but pyproject pins {pins[canonicalize_name(requirement.name)]}"
        for dependent, version, requirement, pin in _installed_requirements_on_pins(pins)
        if not requirement.specifier.contains(pin, prereleases=True)
    ]
    assert not refused, "\n".join(refused)


def test_langchain_aws_requirement_covers_the_langchain_core_pin() -> None:
    try:
        requires = importlib.metadata.requires("langchain-aws") or []
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("langchain-aws is not installed")
    core = [Requirement(line) for line in requires if canonicalize_name(Requirement(line).name) == "langchain-core"]
    assert core, "langchain-aws no longer requires langchain-core; the Dependabot hold may be stale"
    assert core[0].specifier.contains(_exact_pins()[canonicalize_name("langchain-core")], prereleases=True)


def _python_ignores() -> list[tuple[str, SpecifierSet]]:
    config = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
    entries = [
        update for update in config["updates"]
        if update["package-ecosystem"] == "pip" and update["directory"] == "/backend/python"
    ]
    assert len(entries) == 1
    # Dependabot writes ranges as ">= 1.1.1, < 2"; a bare ">=1.13" is the same grammar.
    return [
        (ignore["dependency-name"], SpecifierSet(re.sub(r"\s+", "", versions)))
        for ignore in entries[0].get("ignore", [])
        for versions in ignore.get("versions", [])
    ]


def test_python_ignores_are_found() -> None:
    names = {name for name, _ in _python_ignores()}
    assert {"langchain-aws", "fastapi", "PyGithub"} <= names


def test_every_dependabot_ignore_starts_above_what_the_manifest_allows() -> None:
    """An ignore holds Dependabot back from a range; the manifest must not already admit that range."""
    requirements = _manifest_requirements()
    problems = []
    for name, ignored in _python_ignores():
        requirement = requirements.get(canonicalize_name(name))
        lower_bounds = [Version(spec.version) for spec in ignored if spec.operator == ">="]
        if requirement is None:
            problems.append(f"{name}: ignored in dependabot.yml but not a dependency in pyproject.toml")
        elif not lower_bounds:
            problems.append(f"{name}: ignore {ignored} is not a '>=' range, which this check cannot compare")
        elif requirement.specifier.contains(min(lower_bounds), prereleases=True):
            problems.append(f"{name}: pyproject allows {min(lower_bounds)}, inside the ignored range {ignored}")
    assert not problems, "\n".join(problems)
