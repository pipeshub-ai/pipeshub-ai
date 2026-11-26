"""What every matrix cell does around its run: the prompt's tool directive, the approvals, and the CF/NP checks
of the held sandbox (shared by E2E3 and TG-16)."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.support.sandbox_confinement import assert_confined
from tests.unit.harness.engines.matrix.confinement import (
    SandboxChecks,
    check_sandbox,
    status,
)
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from tests.unit.harness.engines.matrix.lane import MatrixLane, RunResult


def engine_kind(kind: str) -> str:
    available, reason = lane_available(engine_image(kind))
    if not available:
        pytest.skip(reason)
    return kind


def tool(name: str, **arguments: Any) -> str:  # noqa: ANN401
    return f"tool {name} {json.dumps(arguments)}"


def approve_all(_: str) -> str:
    return "approved"


async def checks(matrix: MatrixLane, run_id: str, *, unoffered: str | None = None) -> SandboxChecks:
    lane = matrix.lane
    return await asyncio.to_thread(
        check_sandbox, lane.stack.client, lane.namespace, lane.stack.proxy, run_id,
        forbidden=matrix.forbidden(), unoffered=unoffered,
    )


def assert_confined_cell(matrix: MatrixLane, result: RunResult, where: str) -> SandboxChecks:
    """CF-1..CF-4 and NP-1..NP-5 for one cell, from what the held sandbox showed and what the gateway received."""
    seen = result.held
    assert isinstance(seen, SandboxChecks), f"{where}: the run never reached its hold"
    assert_confined(seen.probes, seen.confinement, where)
    for path, answer in seen.escapes.items():
        assert status(answer) == 403 and "path_not_allowed" in answer, (where, path, answer[:400])
    assert status(seen.elsewhere) == 403 and "destination_not_allowed" in seen.elsewhere, (where, seen.elsewhere[:400])
    issued = [a.removeprefix("Bearer ") for a in result.authorizations if a.startswith("Bearer ")]
    assert issued and len(issued) == len(result.authorizations), (where, "a gateway call without a token")
    assert all(token.startswith("ey") and "pxh-" not in token for token in issued), where
    assert seen.leaks([*matrix.secrets(), *issued]) == [], where
    # Positive control: the dump read the engine's environment (its placeholder is there, legitimately).
    assert "HARNESS_TOOLS_TOKEN=pxh-tools-" in seen.dump, where
    return seen


__all__ = ["approve_all", "assert_confined_cell", "checks", "engine_kind", "tool"]
