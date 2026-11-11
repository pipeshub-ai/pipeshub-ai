"""The Knowledge Hub layer against the oracle, on Neo4j and ArangoDB.

Every golden world and the generated worlds: no row the oracle denies, a total that is the row count, counts by
type that sum the rows, and facet totals that count only what the viewer may read. Needs the throwaway stores of
`tests/integration/graph_permissions/docker-compose.yml`.
"""

from __future__ import annotations

import logging

import pytest

from . import gen
from .kh_layer import KhFinding, check_world
from .kh_worlds import KH_WORLDS
from .scenario import Scenario, load_all

pytestmark = [pytest.mark.integration, pytest.mark.timeout(0)]  # sized by the scenario set and PERM_GEN_WORLDS

CHUNK = 25
# The listing's queries make Neo4j warn about every property a sparse world lacks; pytest keeps each record of a test, and the generated worlds
# run out of memory on them.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
# The flatten listing stops at a declared record group, so an admitted node below it that belongs to none of the declared groups is
# not listed (the folder browse shows it): `declared_gap` names it and the other shapes the listing leaves out on purpose, and they are reported as `gap`. A gap, never a leak. Any
# other admitted node the listing leaves out is `under` and fails.
GAPS = {"gap", "browse_extra"}


def _describe(found: list[KhFinding]) -> str:
    return "\n".join(f"{f.kind} {f.scenario} {f.viewer}: {f.detail}" for f in found[:40])


def _bad(found: list[KhFinding], gaps: set[str] = GAPS) -> list[KhFinding]:
    return [f for f in found if f.kind not in gaps]


async def test_golden_worlds_count_what_the_viewer_may_read(graph_layers) -> None:
    scenarios: list[Scenario] = load_all()
    for layer in graph_layers:
        worlds = [*dict.fromkeys(s.final_world() for s in scenarios), *KH_WORLDS]      # shapes no generator builds: the connector check of every arm, arrows, a multi-page walk
        bad: list[KhFinding] = []
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for offset, world in enumerate(chunk):
                bad += _bad(await check_world(layer, world, f"golden-{start + offset}"))
            await layer.release(chunk)
        assert not bad, f"{layer.name}\n{_describe(bad)}"


async def test_the_declared_group_arm_lists_only_its_own_connectors_records(graph_layers) -> None:
    """The worlds with a record of another connector inside a declared group (node `m4`): no row the oracle denies, no count that includes one."""
    worlds = [w for w in [*dict.fromkeys(s.final_world() for s in load_all()), *KH_WORLDS] if "m4" in {n.id for n in w.nodes}]
    assert worlds
    for layer in graph_layers:
        await layer.preload(worlds)
        bad: list[KhFinding] = []
        for i, world in enumerate(worlds):
            bad += _bad(await check_world(layer, world, f"declared-{i}"))
        await layer.release(worlds)
        assert not bad, f"{layer.name}\n{_describe(bad)}"


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_count_what_the_viewer_may_read(graph_layers, kind: str) -> None:
    for seed in gen.seeds():
        worlds = gen.generate(kind, seed, gen.world_count())
        for layer in graph_layers:
            bad: list[KhFinding] = []
            for start in range(0, len(worlds), CHUNK):
                chunk = worlds[start:start + CHUNK]
                await layer.preload(chunk)
                for offset, world in enumerate(chunk):
                    bad += _bad(await check_world(layer, world, f"{kind}-{seed}-{start + offset}"), GAPS)
                await layer.release(chunk)
            assert not bad, f"{layer.name} {kind} seed {seed}\n{_describe(bad)}"
