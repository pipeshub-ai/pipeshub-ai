"""The subtree query of the stamp graph: the virtual records of some records or groups and of everything below them over hierarchy edges only.

Unit: the in-memory graph against a reference walk. The real query on Neo4j and ArangoDB is in `test_subtree_query_real_graph.py`."""

from __future__ import annotations

import pytest

from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.fakes import InMemoryStampGraph
from app.services.permissions.ports import StampNode

from .model import HIERARCHY_RELATIONS
from .scenario import load_all
from .stamp_world import ORG, stamp_graph

DEPTH = 200


def reference(world, starts) -> set[str] | None:
    """The virtual records of the start nodes' records and of every record below them over hierarchy edges (a breadth-first walk of the world);
    None when something lies deeper than the limit (the graph refuses such a subtree)."""
    below: dict[str, list[str]] = {}
    for n in (n for n in world.nodes if n.org is None):                          # another organisation's nodes are not in this graph
        for p in n.parents:
            if p.rel in HIERARCHY_RELATIONS:
                below.setdefault(p.id, []).append(n.id)
    seen, frontier = set(starts), list(starts)
    for depth in range(1, DEPTH + 2):
        frontier = [c for i in frontier for c in below.get(i, ()) if c not in seen]
        if frontier and depth > DEPTH:
            return None
        seen.update(frontier)
    return {n.vrid or f"v-{n.id}" for n in world.nodes if n.org is None and n.id in seen and n.kind == "record"}


@pytest.mark.parametrize("scenario", load_all(), ids=lambda s: s.id)
async def test_the_in_memory_subtree_equals_the_reference_for_every_node_of_every_corpus_world(scenario) -> None:
    for world in (scenario.world, scenario.final_world()):
        graph = stamp_graph(world, vrids={n.id: n.vrid or f"v-{n.id}" for n in world.nodes if n.kind == "record"})
        for node in world.nodes:
            if node.kind == "app" or node.org is not None:
                continue
            want = reference(world, [node.id])
            if want is None:
                with pytest.raises(ViewerScopesUnavailable):
                    await graph.subtree_vrids(ORG, [node.id])
            else:
                assert set(await graph.subtree_vrids(ORG, [node.id])) == want, (scenario.id, node.id)


def _chain(length: int) -> dict[str, StampNode]:
    def rec(i: int, *parents: int) -> StampNode:
        return StampNode(f"r{i}", "record", "app", parents=tuple(f"r{p}" for p in parents), vrid=f"v{i}")

    return {f"r{i}": rec(i, i - 1) if i else rec(0, 1) for i in range(length)}       # r0 is also the child of r1: a cycle


async def test_the_in_memory_subtree_reaches_exactly_the_depth_limit_and_refuses_anything_deeper() -> None:
    graph = InMemoryStampGraph(ORG, {}, _chain(201))                        # r0 .. r200: r200 is 200 levels below r0
    assert await graph.subtree_vrids(ORG, ["r0"]) == sorted(f"v{i}" for i in range(201))
    with pytest.raises(ViewerScopesUnavailable):                              # r201 would be at depth 201: not cut, refused
        await InMemoryStampGraph(ORG, {}, _chain(202)).subtree_vrids(ORG, ["r0"])


async def test_the_in_memory_subtree_ignores_cycles_other_organisations_and_unknown_ids() -> None:
    graph = InMemoryStampGraph(ORG, {}, _chain(5))
    assert await graph.subtree_vrids(ORG, ["r0"]) == [f"v{i}" for i in range(5)]
    assert await graph.subtree_vrids("other", ["r0"]) == []
    assert await graph.subtree_vrids(ORG, ["missing"]) == []
