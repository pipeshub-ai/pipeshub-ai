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


def _record_filed_under_another_app():  # noqa: ANN202
    """A owns `foreign`, but its only edge to an app is to B: it is not a member of A (no container stamp from A's APP_LEVEL model)."""
    from .model import Node, WorldBuilder

    b = WorldBuilder()
    b.principal("x")
    b.node(Node("A", "app", app_type="KB", model="APP_LEVEL"))
    b.node(Node("B", "app", app_type="KB", model="APP_LEVEL"))
    b.node(Node("own", "record", connector="A", belongs=("A",)))
    b.node(Node("foreign", "record", connector="A", belongs=("B",)))
    return b.build()


async def test_projections_over_the_real_graphs_equal_those_over_the_in_memory_graph(graph_layers: list[GraphCheckLayer]) -> None:
    worlds = [*dict.fromkeys(s.final_world() for s in load_all()), _record_filed_under_another_app()]
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


async def test_the_page_lists_the_records_of_an_app_in_key_order_and_a_failed_read_raises(graph_layers: list[GraphCheckLayer]) -> None:
    from app.services.permissions.errors import ViewerScopesUnavailable
    from app.services.vector_db.scope_backfill import AppDocCheckpoints, Progress

    world = next(s.final_world() for s in load_all() if len([n for n in s.final_world().nodes if n.kind == "record"]) >= 4)
    app = next(n.id for n in world.nodes if n.kind == "app")
    want = sorted(n.id for n in world.nodes if n.kind == "record" and n.connector == app and n.org is None)
    for layer in graph_layers:
        await layer.preload([world])
        ns = await layer._ns(world)
        view = layer._provider.permission_stamp_graph()
        first = await view.record_page(ns.org, ns.id(app), None, 2)
        rest = await view.record_page(ns.org, ns.id(app), first[-1], 100)
        assert [ns.local(i) for i in (*first, *rest)] == want and len(first) == min(2, len(want)), layer.name
        assert await view.record_page("no-such-org", ns.id(app), None, 10) == []
        if layer.name == "neo4j":  # the page seeks in the (connectorId, id) index and sorts nothing
            from app.services.graph_db.neo4j.permission_stamps import PAGE

            client = layer._provider.client
            await client.execute_query("CREATE INDEX record_connector_id_key IF NOT EXISTS FOR (n:Record) ON (n.connectorId, n.id)")
            async with client.driver.session() as session:
                result = await session.run("EXPLAIN " + PAGE, app=ns.id(app), org=ns.org, after="", limit=2)
                plan = (await result.consume()).plan

            def operators(node: dict) -> list[str]:
                return [node["operatorType"], *(o for c in node.get("children", []) for o in operators(c))]

            ops = operators(plan)
            assert not any(o.startswith(("Sort", "Top")) for o in ops) and any("IndexSeek" in o or "IndexScan" in o for o in ops), ops
        store = AppDocCheckpoints(layer._provider)
        assert await store.load(ns.id(app)) is None
        await store.save(ns.id(app), Progress("running", first[-1], 5, 3, 2, 1, None))
        assert await store.load(ns.id(app)) == Progress("running", first[-1], 5, 3, 2, 1, None), layer.name
        await layer.release([world])
    broken = graph_layers[0]._provider.permission_stamp_graph()
    broken._get_client = lambda: None  # type: ignore[attr-defined]
    with pytest.raises(ViewerScopesUnavailable):
        await broken.record_page("o", "a", None, 1)
