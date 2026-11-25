"""R36COVER-FU-6: the MCP-E2E K-6 case asserts the STDIO runner's intended design (OWD-1, WP-06), not the one before
it. Since WP-06 a runner holds a stand-in for each of its server's secrets, which the egress proxy swaps for the
sealed secret only on a request to a listed host; K-6 expected the secret itself in the runner's environment and its
digest in the answer, so it could not pass. This pins the case to the stand-in the runner really holds (the same
prefix the sealer makes) and to the secret staying out of every runner and of what the model reads; the case itself
runs on the MCP-E2E Docker lane.
"""

from __future__ import annotations

import ast
from pathlib import Path

from harness_core.sandbox.egress.sealed import PLACEHOLDER_PREFIX, new_placeholder

K6 = Path(__file__).resolve().parents[4] / "integration-tests/harness/e2e/test_mcp_kinds.py"
NAME = "test_k6_a_stdio_server_runs_in_a_runner_holding_a_stand_in_never_its_secret"


def _module() -> ast.Module:
    return ast.parse(K6.read_text())


def test_k6_stand_in_prefix_is_the_sealers() -> None:
    assigned = {
        target.id: node.value.value
        for node in _module().body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert assigned["STAND_IN_PREFIX"] == PLACEHOLDER_PREFIX
    assert new_placeholder().startswith(PLACEHOLDER_PREFIX)


def test_k6_asserts_the_secret_stays_out_of_the_runner_and_the_models_reading() -> None:
    (case,) = [n for n in _module().body if isinstance(n, ast.FunctionDef) and n.name == NAME]
    source = ast.get_source_segment(K6.read_text(), case) or ""
    assert "secret not in env for env in environments" in source
    assert "secret not in read" in source and "not in read" in source
    assert "STAND_IN_PREFIX" in source
    # the expectation before WP-06 is gone
    assert "in _environments(c) for c in runners" not in source
    assert "with_its_secret" not in K6.read_text()
