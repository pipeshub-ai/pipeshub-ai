"""A browse lists only what the access check admits, and a placeholder only on the way to a readable node, on a real Neo4j and a real ArangoDB.

Seeded: a channel (a declared group) with a message that hangs under it by a hierarchy edge but does not belong to
it, which the access check denies; and a folder holding a placeholder with nothing readable below it, a placeholder
with a readable record below it, and a restricted placeholder with nothing below it.

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
    GROUP_G,
    ORG,
    USER_U,
    _principals,
    app,
    bt,
    ip,
    nr,
    perm,
    rec,
    rg,
    user_app,
)
from tests.integration.graph_permissions.loaders import (
    EDGE_TARGETS,
    NODE_TARGETS,
    arango_shape,
)
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kh_browse_denied_children_it"
logger = logging.getLogger("kh-browse-denied-children-it")
RGL = "RECORD_GROUP_LEVEL"


def _graph() -> tuple[list, list]:
    g = {"group_type": "DRIVE", "connector": DRIVE}
    principal_nodes, principal_edges = _principals()
    nodes = [
        *principal_nodes,
        app("bc", "Chat", connector=DRIVE, app_group="G"),
        rg("bc-chan", "chan", permissionModel=RGL, connectorId="bc", **g),
        rec("bc-msg", "msg", connector_id="bc"),
        rec("bc-own", "own", connector_id="bc"),
        app("bp", "Drive", connector=DRIVE, app_group="G"),
        rg("bp-dir", "dir", connectorId="bp", **g),
        rec("bp-empty", "empty stub", connector_id="bp", isPlaceholder=True),
        rec("bp-way", "stub on the way", connector_id="bp", isPlaceholder=True),
        rec("bp-leaf", "leaf", connector_id="bp"),
        rec("bp-strict", "strict stub", connector_id="bp", rule="STRICT", isPlaceholder=True),
    ]
    edges = [
        *principal_edges,
        user_app(USER_U, "bc"), nr("bc", "bc-chan"), bt("bc-chan", "bc"), perm(GROUP_G, "bc-chan", grant_type="GROUP"),
        nr("bc-chan", "bc-msg"), ip("bc-msg", "bc-chan"),
        nr("bc-chan", "bc-own"), ip("bc-own", "bc-chan"), bt("bc-own", "bc-chan"),
        user_app(USER_U, "bp"), nr("bp", "bp-dir"), bt("bp-dir", "bp"), perm(GROUP_G, "bp-dir", grant_type="GROUP"),
        nr("bp-dir", "bp-empty"), ip("bp-empty", "bp-dir"), bt("bp-empty", "bp-dir"),
        nr("bp-dir", "bp-way"), ip("bp-way", "bp-dir"), bt("bp-way", "bp-dir"),
        nr("bp-way", "bp-leaf"), ip("bp-leaf", "bp-way"), bt("bp-leaf", "bp-dir"),
        nr("bp-dir", "bp-strict"), bt("bp-strict", "bp-dir"),
    ]
    return nodes, edges


class _World:
    """The seeded graph; every id and the org carry a per-run suffix, so runs and other tests do not meet."""

    def __init__(self, graph: Any, run: str) -> None:  # noqa: ANN401
        self.graph, self.run, self.org = graph, run, f"{ORG}-{run}"

    def sid(self, node_id: str) -> str:
        return f"{node_id}-{self.run}"

    def plain(self, node_id: str) -> str:
        return node_id.removesuffix(f"-{self.run}")

    def _node(self, node: dict) -> dict:
        props = dict(node["props"])
        for key in ("id", "connectorId", "recordGroupId"):
            if props.get(key):
                props[key] = self.sid(props[key])
        if "orgId" in props:
            props["orgId"] = self.org
        return props

    async def seed(self) -> None:
        nodes, edges = _graph()
        nodes, edges = arango_shape(nodes, edges)
        kind_of = {n["id"]: n["kind"] for n in nodes}
        for kind, collection in NODE_TARGETS.items():
            rows = [self._node(n) for n in nodes if n["kind"] == kind]
            if rows:
                assert await self.graph.batch_upsert_nodes(rows, collection=collection)
        for edge_type, collection in EDGE_TARGETS.items():
            rows = [
                {"from_id": self.sid(e["from"]), "from_collection": NODE_TARGETS[kind_of[e["from"]]],
                 "to_id": self.sid(e["to"]), "to_collection": NODE_TARGETS[kind_of[e["to"]]], **e["props"]}
                for e in edges if e["type"] == edge_type
            ]
            if rows:
                assert await self.graph.batch_create_edges(rows, collection=collection)

    async def remove(self) -> None:
        nodes, _ = _graph()
        ids = [self.sid(n["id"]) for n in nodes]
        if hasattr(self.graph, "client"):
            await self.graph.client.execute_query(
                "MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids},
            )
            return
        for collection in NODE_TARGETS.values():
            await self.graph.http_client.execute_aql(
                f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids},
            )
        for edges in EDGE_TARGETS.values():
            await self.graph.http_client.execute_aql(
                f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
                f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
                {"ids": ids},
            )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        w = _World(graph, uuid.uuid4().hex[:10])
        cleanup.push_async_callback(w.remove)
        await w.seed()
        yield w


async def _page(w: _World, app_id: str, viewer: str = USER_U, **kw: Any) -> dict:  # noqa: ANN401
    access = await w.graph.get_knowledge_hub_access_v3(user_key=w.sid(viewer), org_id=w.org)
    if "start_id" in kw:
        kw["start_id"] = w.sid(kw["start_id"])
    page = await w.graph.get_knowledge_hub_connector_page_v3(
        app_id=w.sid(app_id), org_id=w.org,
        grantee_ids=access["grantee_ids"], gated_app_ids=access["gated_app_ids"],
        grants_by_connector=access["by_connector"],
        limit=50, sort_field="name", sort_dir="ASC", include_total=True, **kw,
    )
    return {**page, "rows": [{**r, "id": w.plain(r["id"])} for r in page["rows"]]}


async def _admitted(w: _World, ids: list[str], viewer: str = USER_U) -> set[str]:
    result = await w.graph.check_access(w.sid(viewer), w.org, node_ids=[w.sid(i) for i in ids])
    return {w.plain(i) for i in result.node_ids}

async def test_a_browse_lists_the_records_the_access_check_admits(world: _World) -> None:
    page = await _page(world, "bc", flatten=False, start_id="bc-chan", start_type="recordGroup")
    ids = {row["id"] for row in page["rows"]}

    assert ids == {"bc-own"}
    assert ids <= await _admitted(world, sorted(ids))
    assert await _admitted(world, ["bc-msg"]) == set()
    assert page["total"] == len(ids)


async def test_a_placeholder_is_listed_only_when_something_readable_is_below_it(world: _World) -> None:
    page = await _page(world, "bp", flatten=False, start_id="bp-dir", start_type="recordGroup")
    ids = {row["id"] for row in page["rows"]}

    assert ids == {"bp-way"}
    assert page["total"] == len(ids)
