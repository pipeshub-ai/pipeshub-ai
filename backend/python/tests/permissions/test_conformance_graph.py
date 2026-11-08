"""The graph check against the product oracle, on Neo4j and ArangoDB.

Every scenario's final world is loaded into each store and `check_access` is
asked about every viewer x node pair. The layer must equal the oracle: no
over-admit (a leak) and no under-admit (a gap). Needs the throwaway stores of
`tests/integration/graph_permissions/docker-compose.yml`.
"""

from __future__ import annotations

import pytest

from .runner import ConformanceRunner, Report
from .scenario import Scenario, load_all

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


def _describe(report: Report) -> str:
    lines = [report.summary()]
    lines += [f"{f.kind} {f.scenario} {f.viewer} -> {f.node}: {f.reason}" for f in report.findings[:40]]
    return "\n".join(lines)


@pytest.mark.parametrize("tier", ["rules", "golden"])
async def test_layer_equals_oracle(graph_layers, tier: str) -> None:
    scenarios: list[Scenario] = load_all()
    if tier == "rules":
        scenarios = [s for s in scenarios if "rules" in s.tags]
    elif tier == "golden":
        scenarios = [s for s in scenarios if "rules" not in s.tags]
    for layer in graph_layers:
        await layer.preload(s.final_world() for s in scenarios)
        runner = ConformanceRunner(layer)
        total = Report()
        for s in scenarios:
            total.merge(await runner.run(s))
        assert not total.over_admits, f"{layer.name} over-admits\n{_describe(total)}"
        assert not total.findings, f"{layer.name}\n{_describe(total)}"
