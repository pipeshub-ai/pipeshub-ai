"""A browse's arrows agree with its expansions on generated worlds, on a real Neo4j and a real ArangoDB.

Each seeded world is one app with one to three groups (declared or not, hiding their children or not, granted
or not), holding members that belong to them, children that only hang under them, hidden children, stubs with
and without something readable below, and nested groups. For every row of the app's browse and of every
expansion below it, the arrow is on exactly when opening the row lists something.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import random
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
    from collections.abc import AsyncIterator, Callable

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

ARANGO_DB = "kh_browse_arrow_property_it"
logger = logging.getLogger("kh-browse-arrow-property-it")
RGL = "RECORD_GROUP_LEVEL"
WORLDS = 30
SEED = 20261102


def _world(i: int, rnd: random.Random) -> tuple[list, list]:
    a = f"pa{i}"
    g = {"group_type": "DRIVE", "connector": DRIVE}
    opens = rnd.random() < 0.35
    nodes = [app(a, f"App {i}", connector=DRIVE, app_group="G", **({"permissionModel": "APP_LEVEL"} if opens else {}))]
    edges = [user_app(USER_U, a)]

    def record(name: str, **props: Any) -> str:  # noqa: ANN401
        node_id = f"{a}-{name}"
        nodes.append(rec(node_id, name, connector_id=a, **props))
        return node_id

    for k in range(rnd.randint(1, 3)):
        gid = f"{a}-g{k}"
        declared = rnd.random() < 0.6
        nodes.append(rg(gid, f"g{k}", connectorId=a, hideChildren=rnd.random() < 0.25,
                        **({"permissionModel": RGL} if declared else {}), **g))
        edges += [nr(a, gid), bt(gid, a)]
        if rnd.random() < 0.7:
            edges.append(perm(GROUP_G, gid, grant_type="GROUP"))
        for c in range(rnd.randint(0, 4)):
            kind = rnd.choice(("member", "inherit", "strict", "stub", "stub-leaf", "subgroup", "chain-top", "via-mid"))
            cid = f"g{k}c{c}"
            if kind == "member":
                m = record(cid)
                edges.append(bt(m, gid))
                edges += [nr(gid, m)] if rnd.random() < 0.5 else []
                edges += [ip(m, gid)] if rnd.random() < 0.5 else []
            elif kind == "inherit":
                m = record(cid)
                edges += [nr(gid, m), ip(m, gid)]
            elif kind == "strict":
                m = record(cid, rule="STRICT")
                edges.append(nr(gid, m))
            elif kind in ("stub", "stub-leaf"):
                m = record(cid, isPlaceholder=True)
                edges += [nr(gid, m), ip(m, gid)]
                edges += [bt(m, gid)] if rnd.random() < 0.3 else []
                if kind == "stub-leaf":
                    leaf = record(cid + "leaf")
                    edges += [nr(m, leaf), ip(leaf, m)]
                    edges += [bt(leaf, gid)] if rnd.random() < 0.5 else []
            elif kind == "via-mid":
                # A declared group nobody granted, below a readable group that is not declared.
                mid = f"{a}-{cid}"
                nodes.append(rg(mid, cid, connectorId=a, **g))
                edges += [nr(gid, mid), ip(mid, gid)]
                dd = f"{a}-{cid}d"
                nodes.append(rg(dd, cid + "d", connectorId=a, permissionModel=RGL, **g))
                edges += [nr(mid, dd), ip(dd, mid), bt(dd, mid)]
                inner = record(cid + "m")
                edges += [bt(inner, dd), nr(dd, inner)]
            elif kind == "chain-top":
                # A declared group granted to the viewer below a parent the viewer cannot read.
                sg = f"{a}-{cid}"
                nodes.append(rg(sg, cid, connectorId=a, permissionModel=RGL, **g))
                edges += [nr(gid, sg), bt(sg, gid), perm(GROUP_G, sg, grant_type="GROUP")]
                inner = record(cid + "m")
                edges += [bt(inner, sg), nr(sg, inner)]
            else:
                sg = f"{a}-{cid}"
                nodes.append(rg(sg, cid, connectorId=a, hideChildren=rnd.random() < 0.2, **g))
                edges += [nr(gid, sg), ip(sg, gid)]
                edges += [bt(sg, gid)] if rnd.random() < 0.5 else []
                inner = record(cid + "m")
                edges.append(bt(inner, sg))
    principal_nodes, principal_edges = _principals()
    return [*principal_nodes, *nodes], [*principal_edges, *edges]


class _World:
    """The seeded graph; every id and the org carry a per-run suffix, so runs and other tests do not meet."""

    def __init__(self, graph: Any, run: str, build: Callable[[], tuple[list, list]]) -> None:  # noqa: ANN401
        self.graph, self.run, self.org, self.build = graph, run, f"{ORG}-{run}", build

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
        nodes, edges = self.build()
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
        nodes, _ = self.build()
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


async def _admitted(w: _World, ids: list[str]) -> set[str]:
    result = await w.graph.check_access(w.sid(USER_U), w.org, node_ids=[w.sid(i) for i in ids])
    return {w.plain(i) for i in result.node_ids}


@pytest.fixture(params=["neo4j", "arango"])
async def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
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
        yield graph


async def _arrows_vs_expansions(w: _World, app_id: str) -> list[tuple[str, bool, list[str]]]:
    """The rows of the app's browse and of every expansion below it whose arrow is not what opening them lists."""
    wrong: list[tuple[str, bool, list[str]]] = []
    queue: list[tuple[str | None, str | None]] = [(None, None)]
    seen: set[str] = set()
    while queue:
        start, kind = queue.pop(0)
        kw = {"start_id": start, "start_type": kind} if start else {}
        page_rows = (await _page(w, app_id, flatten=False, **kw))["rows"]
        listed_ids = [r["id"] for r in page_rows]
        denied_here = set(listed_ids) - await _admitted(w, listed_ids)
        if denied_here:
            wrong.append((start or app_id, False, ["denied", *sorted(denied_here)]))
        for row in page_rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            node_type = "recordGroup" if row["nodeType"] == "recordGroup" else "record"
            opened = [r["id"] for r in (await _page(
                w, app_id, flatten=False, start_id=row["id"], start_type=node_type))["rows"]]
            if row["hasChildren"] != bool(opened):
                wrong.append((row["id"], row["hasChildren"], opened))
            denied = set(opened) - await _admitted(w, opened)
            if denied:
                wrong.append((row["id"], row["hasChildren"], ["denied", *sorted(denied)]))
            if opened:
                queue.append((row["id"], node_type))
    return wrong


async def test_an_arrow_is_on_exactly_when_opening_the_row_lists_something(backend: Any) -> None:  # noqa: ANN401
    rnd = random.Random(SEED)
    failures: list[tuple[int, list]] = []
    listed = 0
    for i in range(WORLDS):
        w = _World(backend, uuid.uuid4().hex[:10], lambda i=i, seed=rnd.random(): _world(i, random.Random(seed)))
        await w.seed()
        try:
            wrong = await _arrows_vs_expansions(w, f"pa{i}")
            listed += 1
        finally:
            await w.remove()
        if wrong:
            failures.append((i, [(w.plain(n), arrow, [w.plain(o) for o in opened]) for n, arrow, opened in wrong]))

    assert listed == WORLDS
    assert failures == []
