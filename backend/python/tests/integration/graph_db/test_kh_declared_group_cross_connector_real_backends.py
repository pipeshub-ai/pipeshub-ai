"""A connector's Knowledge Hub listing holds only records its own connector reads, on a real Neo4j and a real ArangoDB.

A record of another connector that belongs to a group declared in this connector's scope, or that is
still filed (by a stale ``BELONGS_TO``) in an app that opens everything to its users, was listed and
counted although the access check denies it. Seeded: an app with a declared group holding its own and
a foreign record (the flatten, a scoped browse from a nested group and from a record, the expand
arrows), and an app-level app and a collection each holding a stale-filed record of another connector
and one of an unknown connector. Every surface is checked against the access check.

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
    USER_V,
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

ARANGO_DB = "kh_declared_cross_connector_it"
logger = logging.getLogger("kh-declared-cross-connector-it")

APP_A, APP_B, GROUP = "xc-app-a", "xc-app-b", "xc-group"
RGL = "RECORD_GROUP_LEVEL"


def _collections() -> tuple[list, list]:
    """la (an app that opens everything to its users) and lk (a collection): a record of another connector,
    or of none known, filed in them by a stale BELONGS_TO (a record moved out of a collection, say) is read by
    its own connector's rules, not by the app it was filed in."""
    nodes = [
        app("la", "LA", connector=DRIVE, app_group="G", permissionModel="APP_LEVEL"),
        app("lk", "LK", connector="KB", app_group="Local"),
        rec("la-own", "zz own", connector_id="la"), rec("la-stale", "zz stale", connector_id="lb"),
        rec("la-unknown", "zz unknown", connector_id="legacy-x"),
        rec("lk-own", "zz own", connector_id="lk"), rec("lk-stale", "zz stale", connector_id="lb"),
    ]
    edges = [
        user_app(USER_U, "la"), user_app(USER_U, "lk"),
        bt("la-own", "la"), bt("la-stale", "la"), bt("la-unknown", "la"),
        bt("lk-own", "lk"), bt("lk-stale", "lk"),
    ]
    return nodes, edges


def _cross() -> tuple[list, list]:
    """c2: the other ways a record of another connector reaches a listing. M (declared group mb), D (an app
    that opens everything; group dg), KB (a collection; group kg). Viewer u reads mb, dg and kg, viewer w
    only kg. The y records belong to, or hang under, a group or app of a connector that is not their own."""
    g = {"group_type": "DRIVE", "connector": DRIVE}
    nodes = [
        app("c2-M", "M", connector=DRIVE, app_group="G"),
        app("c2-D", "D", connector=DRIVE, app_group="G", permissionModel="APP_LEVEL"),
        app("c2-KB", "KB", connector="KB", app_group="Local"),
        rg("c2-mb", "mb", permissionModel=RGL, connectorId="c2-M", **g),
        rg("c2-dg", "dg", connectorId="c2-D", **g),
        rg("c2-kg", "kg", connectorId="c2-KB", **g),
        rec("c2-m1", "zz m1", connector_id="c2-M"),
        rec("c2-y1", "zz y1", connector_id="c2-D", indexingStatus="FAILED"),
        rec("c2-y2", "zz y2", connector_id="c2-D", rule="STRICT", indexingStatus="FAILED"),
        rec("c2-y3", "zz y3", connector_id="c2-M", rule="STRICT", indexingStatus="FAILED"),
        rec("c2-y4", "zz y4", connector_id="c2-M", rule="STRICT", indexingStatus="FAILED"),
        rec("c2-y5", "zz y5", connector_id="c2-D"),
        rec("c2-y6", "zz y6", connector_id="c2-KB", rule="STRICT", indexingStatus="FAILED"),
        rec("c2-y7", "zz y7", connector_id="c2-D", rule="STRICT", indexingStatus="FAILED"),
    ]
    edges = [
        nr("c2-M", "c2-mb"), bt("c2-mb", "c2-M"), nr("c2-D", "c2-dg"), bt("c2-dg", "c2-D"),
        nr("c2-KB", "c2-kg"), bt("c2-kg", "c2-KB"),
        nr("c2-mb", "c2-m1"), ip("c2-m1", "c2-mb"), bt("c2-m1", "c2-mb"),
        nr("c2-mb", "c2-y1"), bt("c2-y1", "c2-M"), nr("c2-mb", "c2-y2"),
        nr("c2-kg", "c2-y3"), bt("c2-y3", "c2-kg"), nr("c2-dg", "c2-y4"), bt("c2-y4", "c2-dg"),
        nr("c2-dg", "c2-y5"), ip("c2-y5", "c2-dg"), bt("c2-y5", "c2-dg"),
        bt("c2-y6", "c2-mb"), bt("c2-y7", "c2-KB"),
        perm(USER_U, "c2-mb"), perm(USER_U, "c2-dg"), perm(USER_U, "c2-kg"), perm(USER_V, "c2-kg"),
        user_app(USER_U, "c2-M"), user_app(USER_U, "c2-D"), user_app(USER_U, "c2-KB"), user_app(USER_V, "c2-KB"),
    ]
    return nodes, edges


def _nested() -> tuple[list, list]:
    """na: nested groups under a declared group; a record of nb belongs to na-N and is also its hierarchy child.
    pa: the same overlap under a record; pa-g's only child is foreign, so its arrow must stay off."""
    g = {"group_type": "DRIVE", "connector": DRIVE}
    nodes = [
        app("na", "NA", connector=DRIVE, app_group="G"), app("nb", "NB", connector=DRIVE, app_group="G"),
        rg("na-D", "D", permissionModel=RGL, connectorId="na", **g), rg("na-N", "N", connectorId="na", **g),
        rec("na-r1", "zz r1", connector_id="na"), rec("na-r6", "zz r6", connector_id="na"), rec("nb-r5", "zz r5", connector_id="nb"),
        app("pa", "PA", connector=DRIVE, app_group="G"), app("pb", "PB", connector=DRIVE, app_group="G"),
        rg("pa-D", "PD", permissionModel=RGL, connectorId="pa", **g),
        rec("pa-f", "zz f", connector_id="pa"), rec("pa-y", "zz y", connector_id="pa"), rec("pb-x", "zz x", connector_id="pb"),
        rec("pa-g", "zz g", connector_id="pa"), rec("pb-y", "zz yy", connector_id="pb"),
    ]
    edges = [
        user_app(USER_U, "na"), nr("na", "na-D"), perm(GROUP_G, "na-D", grant_type="GROUP"), nr("na-D", "na-N"),
        bt("na-r1", "na-N"), bt("na-r6", "na-N"), nr("na-N", "na-r6"), bt("nb-r5", "na-N"), nr("na-N", "nb-r5"),
        user_app(USER_U, "pa"), nr("pa", "pa-D"), perm(GROUP_G, "pa-D", grant_type="GROUP"),
        bt("pa-f", "pa-D"), bt("pa-y", "pa-D"), bt("pb-x", "pa-D"), nr("pa-f", "pa-y"), nr("pa-f", "pb-x"),
        nr("pa-D", "pa-f"), nr("pa-D", "pa-g"), bt("pa-g", "pa-D"), bt("pb-y", "pa-D"), nr("pa-g", "pb-y"),
    ]
    return nodes, edges


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    nodes = [
        *principal_nodes,
        app(APP_A, "Mail", connector="GMAIL", app_group="Google Workspace"),
        app(APP_B, "Drive", connector=DRIVE, app_group="Google Workspace"),
        rg(GROUP, "Mailbox", group_type="MAILBOX", connector="GMAIL", connectorId=APP_A,
           permissionModel="RECORD_GROUP_LEVEL"),
        rec("xc-ax", "zz Own", connector_id=APP_A, recordGroupId=GROUP),
        rec("xc-bx", "zz Foreign", connector_id=APP_B, recordGroupId=GROUP),
    ]
    edges = [
        *principal_edges,
        user_app(USER_U, APP_A), nr(APP_A, GROUP), ip(GROUP, APP_A), perm(GROUP_G, GROUP, grant_type="GROUP"),
        bt("xc-ax", GROUP), bt("xc-bx", GROUP),
    ]
    n2, e2 = _nested()
    n3, e3 = _collections()
    n4, e4 = _cross()
    return nodes + n2 + n3 + n4, edges + e2 + e3 + e4


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
        # xc-nc is upserted by one test, not seeded, so _graph() does not name it.
        ids = [self.sid(n["id"]) for n in [*nodes, {"id": "xc-nc"}]]
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

async def test_the_listing_holds_only_records_of_its_own_connector(world: _World) -> None:
    assert await _admitted(world, ["xc-ax", "xc-bx"]) == {"xc-ax"}

    page = await _page(world, APP_A, flatten=True)

    assert "xc-bx" not in {row["id"] for row in page["rows"]}
    assert "xc-ax" in {row["id"] for row in page["rows"]}
    assert page["total"] == len(page["rows"])


@pytest.mark.parametrize(("app_id", "start", "kind", "flatten", "expected"), [
    ("na", "na-N", "recordGroup", False, {"na-r6"}),
    ("na", "na-N", "recordGroup", True, {"na-r6"}),
    ("na", "na-D", "recordGroup", True, {"na-N", "na-r6"}),
    ("pa", "pa-f", "record", False, {"pa-y"}),
    ("pa", "pa-f", "record", True, {"pa-y"}),
])
async def test_a_scoped_listing_holds_only_its_own_connectors_records(
    world: _World, app_id: str, start: str, kind: str, flatten: bool, expected: set,
) -> None:
    page = await _page(world, app_id, flatten=flatten, start_id=start, start_type=kind)

    assert {row["id"] for row in page["rows"]} == expected
    assert page["total"] == len(expected)


async def test_the_app_listing_counts_only_its_own_connectors_records(world: _World) -> None:
    for app_id, foreign in (("na", {"nb-r5"}), ("pa", {"pb-x", "pb-y"})):
        page = await _page(world, app_id, flatten=True)
        assert not foreign & {row["id"] for row in page["rows"]}
        assert page["total"] == len(page["rows"])


async def test_an_arrow_is_off_when_the_only_child_is_another_connectors(world: _World) -> None:
    page = await _page(world, "pa", flatten=False, start_id="pa-D", start_type="recordGroup")
    arrows = {row["id"]: row["hasChildren"] for row in page["rows"]}

    assert arrows["pa-f"] is True
    assert arrows["pa-g"] is False


_FOREIGN = {"xc-bx", "nb-r5", "pb-x", "pb-y", "la-stale", "la-unknown", "lk-stale"}
_SURFACES = {
    "flatten": {"flatten": True},
    "search": {"flatten": True, "filters": {"search_query": "zz"}},
    "sidebar": {"flatten": True, "filters": {"only_containers": True}},
    "depth1": {"flatten": True, "max_depth": 1},
    "records": {"flatten": True, "filters": {"node_types": ["record"]}},
}


@pytest.mark.parametrize("surface", list(_SURFACES))
@pytest.mark.parametrize("app_id", [APP_A, "na", "pa", "la", "lk"])
async def test_no_surface_lists_or_counts_a_record_the_access_check_denies(
    world: _World, app_id: str, surface: str,
) -> None:
    page = await _page(world, app_id, **_SURFACES[surface])
    ids = {row["id"] for row in page["rows"]}
    records = sorted(ids - {"na-N", "na-D", GROUP, "pa-D"})

    assert not _FOREIGN & ids
    assert set(records) <= (await _admitted(world, records) if records else set())
    assert page["total"] == len(page["rows"])


@pytest.mark.parametrize("app_id", ["la", "lk"])
async def test_a_collection_lists_its_own_records_and_not_a_stale_filed_one(world: _World, app_id: str) -> None:
    page = await _page(world, app_id, flatten=True)

    assert {row["id"] for row in page["rows"]} == {f"{app_id}-own"}
    assert page["total"] == 1


_C2_GROUPS = {"c2-mb", "c2-dg", "c2-kg"}
_C2_SURFACES = {**_SURFACES, "failed": {"flatten": True, "filters": {"indexing_status": ["FAILED"]}}}


@pytest.mark.parametrize("surface", list(_C2_SURFACES))
@pytest.mark.parametrize(("viewer", "app_id"), [
    (USER_U, "c2-M"), (USER_U, "c2-D"), (USER_U, "c2-KB"), (USER_V, "c2-KB"),
])
async def test_a_record_reached_by_another_connectors_group_or_app_is_not_listed_or_counted(
    world: _World, viewer: str, app_id: str, surface: str,
) -> None:
    """Rows, totals and the indexing-status facet (a filtered total) name only what the access check admits."""
    page = await _page(world, app_id, viewer=viewer, **_C2_SURFACES[surface])
    records = sorted({row["id"] for row in page["rows"]} - _C2_GROUPS)

    assert set(records) <= (await _admitted(world, records, viewer) if records else set())
    assert page["total"] == len(page["rows"])


async def test_the_own_record_of_an_app_is_still_listed_beside_the_foreign_ones(world: _World) -> None:
    page = await _page(world, "c2-D", flatten=True)

    assert "c2-y5" in {row["id"] for row in page["rows"]}


@pytest.mark.neo4j_only  # ArangoDB's schema rejects a record with no connectorId field, so this shape exists only on Neo4j
async def test_a_record_without_a_connector_id_is_listed_as_the_access_check_says(world: _World) -> None:
    """The access check denies a record with no connector, so the declared group does not list it."""
    props = {k: v for k, v in rec("xc-nc", "zz NoConnector", connector_id=APP_A)["props"].items() if k != "connectorId"}
    await world.graph.batch_upsert_nodes([{**props, "id": world.sid("xc-nc"), "orgId": world.org}], collection="records")
    await world.graph.batch_create_edges(
        [{"from_id": world.sid("xc-nc"), "from_collection": "records", "to_id": world.sid(GROUP),
          "to_collection": "recordGroups", "createdAtTimestamp": 1}],
        collection="belongsTo",
    )

    admitted = "xc-nc" in await _admitted(world, ["xc-nc"])
    listed = "xc-nc" in {row["id"] for row in (await _page(world, APP_A, flatten=True))["rows"]}

    assert listed == admitted
