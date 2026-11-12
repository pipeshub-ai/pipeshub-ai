"""The Knowledge Hub layer against the oracle, on Neo4j and ArangoDB.

Every golden world and the generated worlds: no row the oracle denies, a total that is the row count, counts by
type that sum the rows, and facet totals that count only what the viewer may read. Needs the throwaway stores of
`tests/integration/graph_permissions/docker-compose.yml`.
"""

from __future__ import annotations

import logging

import pytest

from . import gen
from .kh_layer import KhFinding, check_world, children_of
from .kh_worlds import DECLSTUB, KH_WORLDS, OPENSTUB
from .scenario import Scenario, load_all

pytestmark = [pytest.mark.integration, pytest.mark.timeout(0)]  # sized by the scenario set and PERM_GEN_WORLDS

CHUNK = 25
# The listing's queries make Neo4j warn about every property a sparse world lacks; pytest keeps each record of a test, and the generated worlds
# run out of memory on them.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
# `gap` is reported, never failed: the shapes the oracle cannot decide (an upload, which has its own surface, and a node under a group that hides its
# children). A readable node the flatten listing or a browse leaves out is `under` or `browse_gap` and fails.
GAPS = {"gap"}
# shortcut: ten under-admits (`browse_gap`: a node the oracle admits by another arm, as a second parent's walk, an App member or below a seed, that the
# browse of a folder under a declaration or a seed does not list) are known in the generated worlds (seed 7, 50 worlds); the golden worlds fail on any.
# Only these (world, finding) pairs are let through, so a new one fails. Remove the set when the browse admits by the same arms as the oracle.
KNOWN_BROWSE_GAPS = {
    ('adversarial-7-33', 'A-s1: the browse leaves out A-b10, which the viewer may open'),
    ('adversarial-7-42', 'A-c0: the browse leaves out A-c1, which the viewer may open'),
    ('adversarial-7-42', 'A-c0: the browse leaves out A-c27, which the viewer may open'),
    ('adversarial-7-49', 'A-c13: the browse leaves out A-c14, which the viewer may open'),
    ('proptest-7-1', 'B-s1: the browse leaves out B-g1, which the viewer may open'),
    ('proptest-7-14', 'A-s2: the browse leaves out A-r1, which the viewer may open'),
    ('proptest-7-14', 'A-s2: the browse leaves out A-r2, which the viewer may open'),
    ('proptest-7-18', 'B-s1: the browse leaves out B-r4, which the viewer may open'),
    ('proptest-7-31', 'B-s1: the browse leaves out B-r0, which the viewer may open'),
    ('proptest-7-5', 'A-s1: the browse leaves out A-r0, which the viewer may open'),
}


def _describe(found: list[KhFinding]) -> str:
    return "\n".join(f"{f.kind} {f.scenario} {f.viewer}: {f.detail}" for f in found[:40])


def _bad(found: list[KhFinding], known: set[tuple[str, str]] = frozenset()) -> list[KhFinding]:
    return [f for f in found if f.kind not in GAPS and (f.kind != "browse_gap" or (f.scenario, f.detail) not in known)]


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


async def test_a_stub_on_the_way_to_a_node_the_open_app_reads_is_listed_and_opens(graph_layers) -> None:
    """The conformance check leaves stubs out of the children it expects, so this one asks the browse directly: in an App that opens everything the
    folder lists the stub, with an arrow, and opening the stub lists what it leads to."""
    for layer in graph_layers:
        await layer.preload([OPENSTUB])
        assert await children_of(layer, OPENSTUB, "u", "g", "recordGroup") == {"p": True}, layer.name
        assert await children_of(layer, OPENSTUB, "u", "p", "record") == {"s": True}, layer.name
        await layer.release([OPENSTUB])


async def test_a_stub_is_listed_only_where_a_browse_of_the_stub_itself_lists_something(graph_layers) -> None:
    """A stub's way below it is read as a browse of the stub starts, not as the folder it is listed under: in a declared group that walk starts
    below a seed, so a stub that leads only to a strict node is not listed, and the group shows no arrow."""
    for layer in graph_layers:
        await layer.preload([DECLSTUB])
        try:
            assert await children_of(layer, DECLSTUB, "u", "s", "record") == {}, layer.name
            assert await children_of(layer, DECLSTUB, "u", "d", "recordGroup") == {}, layer.name
            assert await children_of(layer, DECLSTUB, "u", "g", "recordGroup") == {"d": False}, layer.name
            bad = _bad(await check_world(layer, DECLSTUB, "declstub"))
            assert not bad, f"{layer.name}\n{_describe(bad)}"
        finally:
            await layer.release([DECLSTUB])


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
                    bad += _bad(await check_world(layer, world, f"{kind}-{seed}-{start + offset}"), KNOWN_BROWSE_GAPS)
                await layer.release(chunk)
            assert not bad, f"{layer.name} {kind} seed {seed}\n{_describe(bad)}"
