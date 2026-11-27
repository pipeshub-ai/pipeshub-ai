"""A membership whose group no longer exists grants nothing, on a real Neo4j and a real ArangoDB.

The user u is a member of a group, role or team that exists and of one whose node is gone; so is the source
account u signed in to the connector as. Each of them grants a record, a record group and (a team) a collection.
Only what an existing group grants counts, on every read of a viewer's grants: ``get_knowledge_hub_access_v3``,
``check_access``, the role of a node (``get_knowledge_hub_context_permissions``) and the entity search's scope
(``get_entity_access_context``). On Neo4j a relationship never outlives its node; ArangoDB keeps an edge whose
vertex is removed, so the gone group's node is removed there alone. Same cases as
``postgres/test_access_live_group.py``.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tests.integration.graph_permissions.fixture_graph import (
    DRIVE,
    KB,
    ORG,
    TS,
    _node,
    app,
    authenticated_as,
    perm,
    rec,
    rg,
    user_app,
)
from tests.integration.graph_permissions.loaders import EDGE_TARGETS, NODE_TARGETS
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "access_live_group_it"
logger = logging.getLogger("access-live-group-it")

APP = "lg-app"
HOLDERS = ("own-live", "own-gone", "src-live", "src-gone")
LIVE = ("own-live", "src-live")
GRANT_TYPE = {"Group": "GROUP", "Role": "ROLE", "Teams": "TEAM"}


def _graph(kind: str) -> tuple[list, list]:
    nodes = [
        _node("User", "u", email="u@example.com", fullName="U", userId="u-uid"),
        _node("User", "src", email="src@example.com", fullName="Src"),
        app(APP, "Drive", connector=DRIVE, app_group="G"),
    ]
    edges = [user_app("u", APP), authenticated_as("u", "src", APP)]
    extra = {"Role": {"externalRoleId": "ext", "connectorName": DRIVE, "connectorId": APP, "createdAtTimestamp": TS}}
    for h in HOLDERS:
        nodes += [_node(kind, h, name=h, **extra.get(kind, {})),
                  rec(f"r-{h}", f"r {h}", connector_id=APP),
                  rg(f"g-{h}", f"g {h}", group_type="DRIVE", connector=DRIVE, connectorId=APP)]
        edges += [perm("u" if h.startswith("own") else "src", h, role="OWNER"),
                  perm(h, f"r-{h}", grant_type=GRANT_TYPE[kind], role="WRITER"),
                  perm(h, f"g-{h}", grant_type=GRANT_TYPE[kind], role="WRITER")]
        if kind == "Teams" and h.startswith("own"):
            nodes.append(app(f"k-{h}", f"kb {h}", connector=KB, app_group="Local"))
            edges.append(perm(h, f"k-{h}", grant_type="TEAM", role="WRITER"))
    return nodes, edges


class _World:
    """The seeded graph; every id and the org carry a per-run suffix, so runs and other tests do not meet."""

    def __init__(self, graph: Any, run: str, kind: str) -> None:  # noqa: ANN401
        self.graph, self.run, self.kind, self.org = graph, run, kind, f"{ORG}-{run}"
        self.nodes, self.edges = _graph(kind)

    def sid(self, node_id: str) -> str:
        return f"{node_id}-{self.run}"

    def plain(self, node_id: str) -> str:
        return node_id.removesuffix(f"-{self.run}")

    async def seed(self) -> None:
        kind_of = {n["id"]: n["kind"] for n in self.nodes}
        for kind, collection in NODE_TARGETS.items():
            rows = [{**n["props"], "id": self.sid(n["id"]), **({"orgId": self.org} if "orgId" in n["props"] else {}),
                     **({"connectorId": self.sid(n["props"]["connectorId"])} if n["props"].get("connectorId") else {})}
                    for n in self.nodes if n["kind"] == kind]
            if rows:
                assert await self.graph.batch_upsert_nodes(rows, collection=collection)
        for edge_type, collection in EDGE_TARGETS.items():
            rows = [{"from_id": self.sid(e["from"]), "from_collection": NODE_TARGETS[kind_of[e["from"]]],
                     "to_id": self.sid(e["to"]), "to_collection": NODE_TARGETS[kind_of[e["to"]]],
                     **e["props"], **({"connectorId": self.sid(e["props"]["connectorId"])}
                                      if e["props"].get("connectorId") else {})}
                    for e in self.edges if e["type"] == edge_type]
            if rows:
                assert await self.graph.batch_create_edges(rows, collection=collection)
        # The gone groups' nodes are removed and their edges stay where the store keeps them.
        gone = [self.sid(h) for h in HOLDERS if h not in LIVE]
        if hasattr(self.graph, "client"):
            await self.graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": gone})
        else:
            coll = NODE_TARGETS[self.kind]
            await self.graph.http_client.execute_aql(f"FOR d IN {coll} FILTER d._key IN @ids REMOVE d IN {coll}", {"ids": gone})

    async def remove(self) -> None:
        ids = [self.sid(n["id"]) for n in self.nodes]
        if hasattr(self.graph, "client"):
            await self.graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})
            return
        for collection in NODE_TARGETS.values():
            await self.graph.http_client.execute_aql(
                f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids})
        for collection in EDGE_TARGETS.values():
            await self.graph.http_client.execute_aql(
                f"FOR e IN {collection} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
                f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {collection}", {"ids": ids})


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    try:
        provider = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                          else connect_arango(logger, ARANGO_DB))
    except Exception as exc:
        backend_unavailable(request.param, exc)
    yield provider
    disconnect = getattr(provider, "disconnect", None)
    if disconnect is not None:
        await disconnect()


@pytest.fixture(params=["Group", "Role", "Teams"])
async def world(request: pytest.FixtureRequest, graph: Any) -> AsyncIterator[_World]:  # noqa: ANN401
    async with contextlib.AsyncExitStack() as cleanup:
        w = _World(graph, uuid.uuid4().hex[:10], request.param)
        cleanup.push_async_callback(w.remove)
        await w.seed()
        yield w


async def test_a_membership_whose_group_no_longer_exists_grants_nothing(world: _World) -> None:
    w, g = world, world.graph
    granted = {f"{p}-{h}" for p in ("r", "g") for h in LIVE}
    access = await g.get_knowledge_hub_access_v3(w.sid("u"), w.org)
    assert {w.plain(i) for i in access["by_connector"][w.sid(APP)]} == granted
    checked = await g.check_access(w.sid("u"), w.org, node_ids=[w.sid(f"{p}-{h}") for p in ("r", "g") for h in HOLDERS])
    assert {w.plain(i) for i in checked.node_ids} == granted

    nodes = [(f"{p}-{h}", t) for p, t in (("r", "record"), ("g", "recordGroup")) for h in HOLDERS]
    if w.kind == "Teams":
        nodes += [(f"k-{h}", "app") for h in ("own-live", "own-gone")]
    roles = {n: (await g.get_knowledge_hub_context_permissions(w.sid("u"), w.org, w.sid(n), parent_type=t))["role"]
             for n, t in nodes}
    # No path at all reads as READER; the grants are WRITER.
    assert roles == {n: "WRITER" if n.endswith(LIVE) else "READER" for n, _ in nodes}

    scope = await g.get_entity_access_context("u-uid", w.org)
    assert {w.plain(i) for i in scope["record_group_ids"]} == {f"g-{h}" for h in LIVE}
