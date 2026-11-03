"""T3 against the stores: generated worlds, graph check against the oracle, Neo4j and ArangoDB.

PERM_GEN_WORLDS (default 50) and PERM_GEN_SEED set the size of a run; a nightly job runs 600 worlds for seeds
7, 11 and 2026. A disagreement is shrunk and written to scenarios/regressions/ before the test fails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from . import gen
from .gen.shrink import cite_regression, regression, shrink, write_regression
from .runner import ConformanceRunner, Finding, OracleLayer, vrids
from .scenario import SCENARIOS_ROOT

if TYPE_CHECKING:
    from pathlib import Path

    from .layers import GraphCheckLayer
    from .model import World

pytestmark = [pytest.mark.integration, pytest.mark.timeout(0)]  # sized by PERM_GEN_WORLDS; the CI job has its own limit


async def _file(
    layer: GraphCheckLayer, world: World, finding: Finding, tag: str, directory: Path = SCENARIOS_ROOT / "regressions",
) -> str:
    """Shrink the world around the first finding and file it as a scenario that reproduces it. A citation finding names a
    virtual record id (not a node), so it is shrunk on the citation check and filed as a citation expectation."""
    reference = OracleLayer(layer.shape)
    note = f"{finding.layer} {finding.kind} on {finding.viewer} -> {finding.node}"
    if finding.kind == "cite":
        indexed_only = "indexed_only=True" in finding.reason
        keep = {finding.viewer} | {n.id for n in world.nodes if (n.vrid or f"v-{n.id}") == finding.node}

        async def cite_fails(w: World) -> bool:
            if finding.viewer not in {p.id for p in w.principals} or finding.node not in vrids(w):
                return False
            try:
                want = await reference.cited(w, finding.viewer, [finding.node], indexed_only)
                got = await layer.cited(w, finding.viewer, [finding.node], indexed_only)
            finally:
                await layer.release([w])
            return got != want

        small = await shrink(world, keep, cite_fails)
        scenario = cite_regression(f"GEN-{tag}", small, finding.viewer, finding.node, indexed_only, note, layer.shape)
        return str(write_regression(directory, scenario))

    async def fails(w: World) -> bool:
        if not {finding.viewer, finding.node} <= {p.id for p in w.principals} | {n.id for n in w.nodes}:
            return False
        try:
            want = reference.explain(w, finding.viewer, [finding.node])[finding.node].admitted
            return (finding.node in await layer.admitted(w, finding.viewer, [finding.node])) != want
        finally:
            await layer.release([w])

    small = await shrink(world, {finding.viewer, finding.node}, fails)
    scenario = regression(f"GEN-{tag}", small, finding.viewer, finding.node, note, layer.shape)
    return str(write_regression(directory, scenario))


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
