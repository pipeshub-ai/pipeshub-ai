"""The stamp graph of Neo4j and ArangoDB (Cypher / AQL) gives the projector what the in-memory graph of the same world does.

Needs the throwaway stores of the graph-permissions harness (`KH_PERM_*`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.permissions.projector import ScopeProjector

from .graph import compile_world
from .scenario import load_all
from .stamp_world import stamp_graph

if TYPE_CHECKING:
    from .layers import GraphCheckLayer

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]
CHUNK = 25


async def test_projections_over_the_real_graphs_equal_those_over_the_in_memory_graph(graph_layers: list[GraphCheckLayer]) -> None:
    worlds = list(dict.fromkeys(s.final_world() for s in load_all()))
    for layer in graph_layers:
        compared = skipped = 0
        view = layer._provider.permission_stamp_graph()
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await layer.preload(chunk)
            for world in chunk:
                ns = await layer._ns(world)
                if len(layer.shape(compile_world(world, ns.prefix))["nodes"]) != len(compile_world(world, ns.prefix)["nodes"]):
                    skipped += 1  # the store cannot hold a node of this world (an unknown access rule on Arango)
                    continue
                local = stamp_graph(world, "", org=ns.org)
                for app in (n for n in world.nodes if n.kind == "app" and n.org is None):
                    records = [n.id for n in world.nodes if n.kind == "record" and n.connector == app.id and n.org is None]
                    want = await ScopeProjector(local).project(ns.org, app.id, records)
                    stored_app = await ScopeProjector(view).project(ns.org, ns.id(app.id), [ns.id(r) for r in records])
                    got = {ns.local(rid): p for rid, p in stored_app.items()}
                    assert set(got) == set(want)
                    for rid, w in want.items():
                        g = got[rid]
                        assert (g.fields.stamp, g.refused, g.skipped) == (w.fields.stamp, w.refused, w.skipped), (layer.name, rid)
                        assert tuple(sorted(ns.local(i) for i in g.fields.path_ids)) == w.fields.path_ids, (layer.name, rid)
                        assert tuple(sorted(ns.local(i) for i in g.fields.acl_scope)) == w.fields.acl_scope, (layer.name, rid)
                        compared += 1
            await layer.release(chunk)
        assert compared > 300, (layer.name, compared, skipped)
