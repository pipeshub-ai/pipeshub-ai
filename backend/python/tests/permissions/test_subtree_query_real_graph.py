"""The real subtree query on Neo4j and ArangoDB. Needs the stores of the graph-permissions harness (`KH_PERM_*`).

* a child that a link edge (SIBLING, OTHERS, BLOCKS, ...) from its parent met before or after its hierarchy edge is in the subtree, and a record
  reached only through a link edge is not (ArangoDB's traversal filtered the last edge only: it marked the child visited through the link edge and
  never emitted it through the hierarchy edge);
* random graphs with hierarchy and link edges, parallel edges, cycles and diamonds equal a reference walk over hierarchy edges;
* every node of every corpus world loaded into the store gives the subtree the reference gives.
"""

from __future__ import annotations

import os
import random

import pytest

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org
from tests.integration.graph_permissions.processor_harness import build_processor
from tests.integration.graph_permissions.test_write_path import _group, _record
from tests.integration.graph_permissions.write_path_support import (
    ORG,
    _seed_app,
    _suffix,
)

from .scenario import load_all
from .test_generation_paths import backend  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1200)]

HIER = {"PARENT_CHILD", "ATTACHMENT"}
LINKS = ["SIBLING", "OTHERS", "BLOCKS", "DEPENDS_ON", "LINKED_TO", "DUPLICATES", "CLONES"]
STAMPS = {"createdAtTimestamp": 1, "updatedAtTimestamp": 1}


async def _records(backend, names: list[str]) -> tuple[str, dict[str, str]]:
    """Records `names` in one connector, each with the virtual record id `v-<name>-<connector>`; returns the connector and the record ids."""
    p = backend.provider
    processor, _ = build_processor(p, ORG)
    conn, space = f"conn-{_suffix()}", f"space-{_suffix()}"
    await _seed_app(backend, conn)
    await processor.on_new_record_groups([(_group(space, inherit=True, connector_id=conn), [])])
    recs = {n: _record(f"{n}-{_suffix()}", connector_id=conn, group_external=space) for n in names}
    await processor.on_new_records([(r, []) for r in recs.values()])
    ids = {}
    for n, r in recs.items():
        doc = await p.get_record_by_external_id(connector_id=conn, external_id=r.external_record_id)
        ids[n] = doc.id
        await p.update_node(doc.id, CollectionNames.RECORDS.value, {"virtualRecordId": f"v-{n}-{conn}"})
    return conn, ids


async def _subtree(backend, starts: list[str]) -> list[str]:
    with for_org(ORG, job="test"):
        return await backend.provider.permission_stamp_graph().subtree_vrids(ORG, starts)


@pytest.mark.parametrize("order", [("SIBLING", "PARENT_CHILD"), ("PARENT_CHILD", "SIBLING"), ("OTHERS", "PARENT_CHILD"), ("PARENT_CHILD", "OTHERS"),
                                   ("BLOCKS", "ATTACHMENT"), ("ATTACHMENT", "BLOCKS")])
async def test_the_subtree_follows_hierarchy_edges_whatever_edge_met_the_child_first(backend, order) -> None:
    """R -> Z (a link edge and a hierarchy edge), Z -> Y, R -> W; X hangs off R by a link edge only, V below X: the subtree of R is R, Z, Y, W."""
    conn, ids = await _records(backend, ["R", "Z", "Y", "W", "X", "V"])
    for rel in order:
        await backend.add_node_relation(ids["R"], ids["Z"], {"relationshipType": rel, **STAMPS})
    await backend.add_node_relation(ids["Z"], ids["Y"], {"relationshipType": "PARENT_CHILD", **STAMPS})
    await backend.add_node_relation(ids["R"], ids["W"], {"relationshipType": "PARENT_CHILD", **STAMPS})
    await backend.add_node_relation(ids["R"], ids["X"], {"relationshipType": "SIBLING", **STAMPS})
    await backend.add_node_relation(ids["X"], ids["V"], {"relationshipType": "PARENT_CHILD", **STAMPS})
    got = await _subtree(backend, [ids["R"]])
    want = sorted(f"v-{n}-{conn}" for n in ("R", "Z", "Y", "W"))
    assert sorted(got) == want, (order, "missing", sorted(set(want) - set(got)), "extra", sorted(set(got) - set(want)))


def reference(edges, starts):
    out, seen, todo = set(starts), set(starts), [(s, 0) for s in starts]
    while todo:
        cur, depth = todo.pop(0)
        if depth >= 50:
            continue
        for a, b, t in edges:
            if a == cur and t in HIER and b not in seen:
                seen.add(b)
                out.add(b)
                todo.append((b, depth + 1))
    return out


@pytest.mark.parametrize("case", range(int(os.environ.get("PERM_CASES", "12"))))
async def test_the_real_subtree_equals_a_hierarchy_only_reference_on_random_graphs(backend, case) -> None:
    rng = random.Random(4242 * 1000 + case)
    names = [f"n{i}" for i in range(14)]
    conn, ids = await _records(backend, names)
    seen = set()
    for _ in range(rng.randint(14, 42)):
        a, b = rng.sample(names, 2)
        t = rng.choice(["PARENT_CHILD", "PARENT_CHILD", "ATTACHMENT", *LINKS]) if rng.random() < 0.6 else rng.choice(LINKS)
        if (a, b, t) not in seen:
            seen.add((a, b, t))
            await backend.add_node_relation(ids[a], ids[b], {"relationshipType": t, **STAMPS})
    bad = []
    for k in (1, 2, 3):
        starts = rng.sample(names, k)
        want = {f"v-{n}-{conn}" for n in reference(sorted(seen), starts)}
        got = set(await _subtree(backend, [ids[s] for s in starts]))
        if got != want:
            bad.append((starts, sorted(want - got), sorted(got - want)))
    assert not bad, (case, "missing/extra vs reference", bad, "edges", sorted(seen))


def stored_reference(graph, org: str, start: str) -> set[str]:
    """The virtual records the subtree of `start` holds in the graph as the store was given it (a store may refuse a node: ArangoDB's schema does):
    a walk over hierarchy edges between nodes of the organisation."""
    nodes = {n["id"]: n for n in graph["nodes"] if n["props"].get("orgId") == org}
    below: dict[str, list[str]] = {}
    for e in graph["edges"]:
        if e["type"] == "NODE_RELATION" and e["props"].get("relationshipType") in HIER and e["from"] in nodes and e["to"] in nodes:
            below.setdefault(e["from"], []).append(e["to"])
    if nodes.get(start, {}).get("kind") not in ("Record", "RecordGroup"):
        return set()
    seen, frontier = {start}, [start]
    for _ in range(50):
        frontier = [c for i in frontier for c in below.get(i, ()) if c not in seen]
        seen.update(frontier)
    return {nodes[i]["props"]["virtualRecordId"] for i in seen if nodes[i]["kind"] == "Record" and nodes[i]["props"].get("virtualRecordId")}


async def test_every_node_of_every_corpus_world_gives_the_reference_subtree(graph_layers) -> None:
    scenarios = load_all()
    for layer in graph_layers:
        await layer.preload([w for s in scenarios for w in (s.world, s.final_world())])
        bad = []
        graph = layer._provider.permission_stamp_graph()
        for s in scenarios:
            for world in (s.world, s.final_world()):
                ns = await layer._ns(world)
                compiled = layer._compiled(world)
                for node in world.nodes:
                    if node.kind == "app" or node.org is not None:
                        continue
                    with for_org(ns.org, job="test"):
                        got = set(await graph.subtree_vrids(ns.org, [ns.id(node.id)]))
                    want = stored_reference(compiled, ns.org, ns.id(node.id))
                    if got != want:
                        bad.append((layer.name, s.id, node.id, sorted(want - got), sorted(got - want)))
        assert not bad, (len(bad), bad[:10])
