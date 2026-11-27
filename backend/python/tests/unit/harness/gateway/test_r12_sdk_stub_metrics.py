"""Round 12 (WP-57 x round 11's Tool Gateway metrics): a stub call is observed exactly as a ``ph`` call is.

WP-57's stubs call ``ph`` (in process for Python, a child process for TypeScript), so they reach the Tool Gateway
through the same egress proxy and the same route. Round 11 counts each gateway request by route and outcome and each
tool call by family and disposition (C9.04, OB-08). A stub must neither add requests the operator's dashboard would
read as load nor hide a call from it.

R12-SDK-1 for allowed reads and writes, a policy denial and an ungranted tool, ``ph``, the Python stub and the
          TypeScript stub leave the same request and tool-call metrics, one tool call per call made.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.telemetry.metrics import RecordingGatewayMetrics
from harness_core.testing.contracts.tool_endpoint import build_endpoint
from tests.unit.harness.gateway.support import GatewayServer
from tests.unit.harness.gateway.test_ws14_sdk_parity import (  # pyright: ignore[reportPrivateUsage]
    _session,
    _through,
    _ways,
)

if TYPE_CHECKING:
    from pathlib import Path

CASES: list[tuple[str, dict[str, Any], list[tuple[str, dict[str, Any]]]]] = [
    ("allowed", {}, [("lookup", {"q": "revenue"}), ("create", {"q": "acme"})]),
    ("policy-deny", {"approvals": {"create": "deny"}}, [("create", {"q": "x"})]),
    ("ungranted", {}, [("delete", {"q": "x"})]),
]


async def _observed(tmp_path: Path, way: str, options: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]) -> tuple[list[tuple[str, str, str | None]], list[tuple[str, str]]]:
    metrics = RecordingGatewayMetrics()
    world = build_endpoint(metrics=metrics, **options)
    async with GatewayServer(metrics=metrics).running() as server, _session(server, world, tmp_path / way) as folder:
        for name, arguments in calls:
            _ = await _through(way, folder, world, name, arguments, {})
    requests = sorted((r.route, r.outcome, r.reason) for r in metrics.requests)
    return requests, [(c.family, c.disposition) for c in metrics.calls]


@pytest.mark.parametrize(("case", "options", "calls"), CASES, ids=[c[0] for c in CASES])
async def test_r12_sdk1_a_stub_call_leaves_the_metrics_a_ph_call_leaves(
    tmp_path: Path, case: str, options: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]
) -> None:
    seen = {way: await _observed(tmp_path, way, options, calls) for way in _ways()}
    requests, tool_calls = seen["ph"]
    assert requests, "the gateway observed no request"
    for way, observed in seen.items():
        assert observed == (requests, tool_calls), way
    if case == "allowed":
        assert tool_calls == [("read", "executed"), ("write", "executed")]
