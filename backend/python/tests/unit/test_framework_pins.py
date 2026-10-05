"""The web framework versions the services run on must be the ones the manifests pin.

Starlette is not a direct import of this codebase; it arrives through FastAPI,
whose requirement is a range. Without a pin of its own a fresh install can
resolve to a release with a known advisory.
"""

import importlib
import importlib.metadata
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

BACKEND_MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"
INTEGRATION_MANIFEST = Path(__file__).resolve().parents[4] / "integration-tests" / "pyproject.toml"

# Lowest release the security audit (OT-10) accepts.
STARLETTE_FLOOR = Version("1.3.1")


def _framework_pins(manifest: Path) -> dict[str, str]:
    dependencies = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["dependencies"]
    pins = {}
    for dependency in dependencies:
        requirement = Requirement(dependency)
        if requirement.name in ("fastapi", "starlette"):
            pins[requirement.name] = str(requirement.specifier)
    return pins


def _installed(package: str) -> Version:
    return Version(importlib.metadata.version(package))


def test_installed_starlette_is_at_least_the_patched_release():
    assert _installed("starlette") >= STARLETTE_FLOOR


def test_backend_manifest_pins_starlette_directly():
    assert _framework_pins(BACKEND_MANIFEST).get("starlette", "").startswith("==")


@pytest.mark.parametrize("package", ["fastapi", "starlette"])
def test_installed_framework_matches_the_backend_pin(package):
    assert _framework_pins(BACKEND_MANIFEST).get(package) == f"=={_installed(package)}"


def test_integration_tests_manifest_carries_the_same_framework_pins():
    if not INTEGRATION_MANIFEST.exists():
        pytest.skip("integration-tests/ is not part of this checkout")
    assert _framework_pins(INTEGRATION_MANIFEST) == _framework_pins(BACKEND_MANIFEST)


def test_fastapi_stays_below_the_route_tree_release():
    assert _installed("fastapi") < Version("0.137"), (
        "FastAPI 0.137 turns router.routes into a tree of nested routers. The route "
        "inventories (tests/unit/api/test_route_auth_policy.py and the other tests "
        "that iterate router.routes) walk a flat list, so they would stop seeing "
        "most routes: make them walk the tree before raising the pin."
    )


def test_mcp_client_imports_on_the_pinned_release():
    # Pulls in fastmcp, the MCP SDK and sse_starlette, which all build on Starlette.
    importlib.import_module("app.agents.mcp.client")
