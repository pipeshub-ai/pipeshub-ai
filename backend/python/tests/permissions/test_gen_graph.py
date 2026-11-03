"""T3 against the stores: generated worlds, graph check against the oracle, Neo4j and ArangoDB.

PERM_GEN_WORLDS (default 50) and PERM_GEN_SEED set the size of a run; a nightly job runs 600 worlds for seeds
7, 11 and 2026. A disagreement is shrunk and written to scenarios/regressions/ before the test fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from . import gen
from .gen.shrink import regression, shrink, write_regression
from .runner import ConformanceRunner, Finding, OracleLayer
from .scenario import SCENARIOS_ROOT

if TYPE_CHECKING:
    from .layers import GraphCheckLayer
    from .model import World

pytestmark = [pytest.mark.integration, pytest.mark.timeout(0)]  # sized by PERM_GEN_WORLDS; the CI job has its own limit


async def _file(layer: GraphCheckLayer, world: World, finding: Finding, tag: str) -> str:

    async def fails(w: World) -> bool:
        if not {finding.viewer, finding.node} <= {p.id for p in w.principals} | {n.id for n in w.nodes}:
            return False
        want = OracleLayer(layer.shape).explain(w, finding.viewer, [finding.node])[finding.node].admitted
        return (finding.node in await layer.admitted(w, finding.viewer, [finding.node])) != want

    small = await shrink(world, {finding.viewer, finding.node}, fails)
    scenario = regression(f"GEN-{tag}", small, finding.viewer, finding.node,
                          f"{finding.layer} {finding.kind} on {finding.viewer} -> {finding.node}", layer.shape)
    return str(write_regression(SCENARIOS_ROOT / "regressions", scenario))


CHUNK = 25  # worlds resident in a store at once


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_equal_the_oracle(graph_layers: list[GraphCheckLayer], kind: str) -> None:
    for seed in gen.seeds():
        worlds = gen.generate(kind, seed, gen.world_count())
        for layer in graph_layers:
            runner = ConformanceRunner(layer)
            for start in range(0, len(worlds), CHUNK):
                chunk = worlds[start:start + CHUNK]
                await layer.preload(chunk)
                for offset, world in enumerate(chunk):
                    index = start + offset
                    report = await runner.check_world(world, f"{kind}-{seed}-{index}", cites=True)
                    if report.findings:
                        path = await _file(layer, world, report.findings[0], f"{kind}-{seed}-{index}-{layer.name}")
                        pytest.fail(f"{layer.name} {kind} seed {seed} world {index}: {report.summary()}; filed {path}")
                await layer.release(chunk)
