"""HTIMG: a rebuilt e2e/human stack runs the new commit in every service that runs this tree's code.

The human stack's broker outlived a refresh: `compose up` kept it (its configuration had not changed), so it went on
admitting the engine images of the allowlist it loaded at start, and refused the new tools images of Codex and Claude
Code (``image_not_allowed``). run.sh rebuild now recreates every service code_services.py finds in the rendered stack.
"""

from __future__ import annotations

import importlib.util
import re

import pytest

from tests.cohesion import test_c12_sandbox_plane as c12
from tests.cohesion.support import REPO_ROOT

# The same Compose render as C-12's, skipped where it is (no Compose CLI).
pytestmark = c12.pytestmark
E2E_ENV, E2E_OVERLAY, _render = c12.E2E_ENV, c12.E2E_OVERLAY, c12._render
E2E = REPO_ROOT / "deployment" / "e2e"
SPEC = importlib.util.spec_from_file_location("code_services", E2E / "code_services.py")
assert SPEC is not None and SPEC.loader is not None
code_services = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(code_services)

EXTRA_ENV = (
    "E2E_HUMAN_DIR=/human",
    "E2E_MCP_RUNNER_IMAGE=pxh-mcp-runner:x1",
    "E2E_MCP_PORT=4",
)
OVERLAYS = {
    "human": (str(E2E_OVERLAY), str(E2E / "docker-compose.human.yml")),
    "mcp": (str(E2E_OVERLAY), str(E2E / "docker-compose.mcp.yml")),
}


@pytest.mark.parametrize("name", list(OVERLAYS))
def test_htimg_the_broker_and_proxy_are_among_the_services_running_this_trees_code(
    name: str,
) -> None:
    rendered = _render(OVERLAYS[name], "graph-neo4j,sandbox,sandbox-broker", (*E2E_ENV, *EXTRA_ENV))
    found = set(code_services.code_services(rendered, "/src"))
    assert {
        "sandbox-broker",
        "egress-proxy",
        "pxh-fake-llm",
        "pxh-fake-saas",
        "pipeshub-ai",
    } <= found
    assert ("pxh-fake-mcp" in found) == (name == "mcp")
    assert not found & {"qdrant", "neo4j", "redis"}


def test_htimg_rebuild_force_recreates_what_code_services_lists() -> None:
    script = (E2E / "run.sh").read_text(encoding="utf-8")
    rebuild = script[
        script.index("\nrebuild() {") : script.index(
            "\n}\n", script.index("\nrebuild() {")
        )
    ]
    assert re.search(
        r'compose config --format json \| python3 "\$HERE/code_services\.py" "\$ROOT"',
        rebuild,
    )
    assert 'compose up -d --no-deps --force-recreate "${code[@]}"' in rebuild
