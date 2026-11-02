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


def _more() -> tuple[list, list]:
    """kt: a thread that belongs to the channel (a member) with a reply that inherits from the thread, and a thread
    that is no member with a reply under it. ka and kb: a declared group in an app that opens everything and in a
    collection, each with a child that only hangs under the group, one that inherits, and one that belongs."""
    g = {"group_type": "DRIVE", "connector": DRIVE}
    nodes = [
        app("kt", "Chat", connector=DRIVE, app_group="G"),
        rg("kt-chan", "chan", permissionModel=RGL, connectorId="kt", **g),
        *[rec(f"kt-{n}", n, connector_id="kt") for n in ("thread", "reply", "thread-nm", "reply-nm")],
        app("ka", "Open", connector=DRIVE, app_group="G", permissionModel="APP_LEVEL"),
        app("kb", "Coll", connector="KB", app_group="Local"),
        rg("ka-g", "g", permissionModel=RGL, connectorId="ka", **g),
        rg("kb-g", "g", permissionModel=RGL, connectorId="kb", **g),
        *[rec(f"{a}-{n}", n, connector_id=a) for a in ("ka", "kb") for n in ("nr", "ip", "bt")],
        app("mk", "Mail", connector=DRIVE, app_group="G"),
        rg("mk-box", "box", permissionModel=RGL, connectorId="mk", **g),
        rg("mk-label", "label", connectorId="mk", **g),
        app("mt", "Chat2", connector=DRIVE, app_group="G"),
        rg("mt-chan", "chan", permissionModel=RGL, connectorId="mt", **g),
        rg("mt-thread", "thread", connectorId="mt", **g),
        app("st", "Stubs", connector=DRIVE, app_group="G"),
        rg("st-S", "S", connectorId="st", **g),
        rec("st-f", "f", connector_id="st", rule="STRICT", isPlaceholder=True),
        app("hc", "Hides", connector=DRIVE, app_group="G"),
        rg("hc-g", "g", permissionModel=RGL, connectorId="hc", hideChildren=True, **g),
        rec("hc-m", "m", connector_id="hc"),
        app("pp", "Way", connector=DRIVE, app_group="G"),
        rg("pp-dir", "dir", connectorId="pp", **g),
        rec("pp-stub", "stub", connector_id="pp", isPlaceholder=True),
        rec("pp-mid", "mid", connector_id="pp"),
        rec("pp-leaf", "leaf", connector_id="pp"),
        app("ch", "Hidden", connector=DRIVE, app_group="G"),
        rg("ch-chan", "chan", permissionModel=RGL, connectorId="ch", **g),
        rec("ch-msg", "msg", connector_id="ch"),
    ]
    edges = [
        user_app(USER_U, "kt"), nr("kt", "kt-chan"), bt("kt-chan", "kt"), perm(GROUP_G, "kt-chan", grant_type="GROUP"),
        bt("kt-thread", "kt-chan"), nr("kt-chan", "kt-thread"),
        nr("kt-thread", "kt-reply"), ip("kt-reply", "kt-thread"),
        nr("kt-chan", "kt-thread-nm"), ip("kt-thread-nm", "kt-chan"),
        nr("kt-thread-nm", "kt-reply-nm"), ip("kt-reply-nm", "kt-thread-nm"),
    ]
    edges += [
        user_app(USER_U, "hc"), nr("hc", "hc-g"), bt("hc-g", "hc"), perm(GROUP_G, "hc-g", grant_type="GROUP"),
        bt("hc-m", "hc-g"),
        user_app(USER_U, "pp"), nr("pp", "pp-dir"), bt("pp-dir", "pp"), perm(GROUP_G, "pp-dir", grant_type="GROUP"),
        nr("pp-dir", "pp-stub"), ip("pp-stub", "pp-dir"),
        nr("pp-mid", "pp-leaf"), ip("pp-leaf", "pp-mid"), ip("pp-mid", "pp-dir"),
        user_app(USER_U, "mk"), nr("mk", "mk-box"), bt("mk-box", "mk"), perm(GROUP_G, "mk-box", grant_type="GROUP"),
        nr("mk-box", "mk-label"), ip("mk-label", "mk-box"), bt("mk-label", "mk-box"),
        user_app(USER_U, "mt"), nr("mt", "mt-chan"), bt("mt-chan", "mt"), perm(GROUP_G, "mt-chan", grant_type="GROUP"),
        nr("mt-chan", "mt-thread"), ip("mt-thread", "mt-chan"),
        user_app(USER_U, "st"), nr("st", "st-S"), bt("st-S", "st"), perm(GROUP_G, "st-S", grant_type="GROUP"),
        nr("st-S", "st-f"), ip("st-f", "st-S"),
        user_app(USER_U, "ch"), nr("ch", "ch-chan"), bt("ch-chan", "ch"), perm(GROUP_G, "ch-chan", grant_type="GROUP"),
        nr("ch-chan", "ch-msg"), ip("ch-msg", "ch-chan"),
    ]
    for a in ("ka", "kb"):
        edges += [
            user_app(USER_U, a), nr(a, f"{a}-g"), bt(f"{a}-g", a), perm(GROUP_G, f"{a}-g", grant_type="GROUP"),
            nr(f"{a}-g", f"{a}-nr"), nr(f"{a}-g", f"{a}-ip"), ip(f"{a}-ip", f"{a}-g"),
            nr(f"{a}-g", f"{a}-bt"), bt(f"{a}-bt", f"{a}-g"),
        ]
    return nodes, edges


def _graph() -> tuple[list, list]:
    n2, e2 = _more()
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
        *e2,
    ]
    return nodes + n2, edges


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

    async def seed_legacy_link(self) -> None:
        """A non-hierarchy edge kept in the hierarchy collection, as data written before links had their own."""
        src, dst = self.sid("pp-stub"), self.sid("pp-mid")
        if hasattr(self.graph, "client"):
            await self.graph.client.execute_query(
                "MATCH (a:Record {id: $a}), (b:Record {id: $b}) "
                "CREATE (a)-[:NODE_RELATION {relationshipType: 'SIBLING'}]->(b)", parameters={"a": src, "b": dst},
            )
            return
        await self.graph.http_client.execute_aql(
            "INSERT {_from: @a, _to: @b, relationshipType: 'SIBLING'} INTO nodeRelations",
            {"a": f"records/{src}", "b": f"records/{dst}"},
        )

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
        await w.seed_legacy_link()
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


async def test_a_reply_under_a_member_of_a_declared_group_is_not_listed(world: _World) -> None:
    browse = await _page(world, "kt", flatten=False, start_id="kt-thread", start_type="record")
    flat = await _page(world, "kt", flatten=True)
    flat_ids = {row["id"] for row in flat["rows"]}

    assert browse["rows"] == []
    assert flat_ids == {"kt-chan", "kt-thread"}
    assert flat["total"] == len(flat_ids)
    assert flat_ids <= await _admitted(world, sorted(flat_ids))
    assert await _admitted(world, ["kt-reply", "kt-reply-nm"]) == set()


@pytest.mark.parametrize("app_id", ["ka", "kb"])
async def test_a_declared_group_in_an_app_that_opens_everything_lists_only_what_the_check_admits(
    world: _World, app_id: str,
) -> None:
    browse = await _page(world, app_id, flatten=False, start_id=f"{app_id}-g", start_type="recordGroup")
    flat = await _page(world, app_id, flatten=True)
    flat_ids = {row["id"] for row in flat["rows"]}

    assert {row["id"] for row in browse["rows"]} == {f"{app_id}-bt"}
    assert flat_ids == {f"{app_id}-g", f"{app_id}-bt"}
    assert flat["total"] == len(flat_ids)
    assert flat_ids <= await _admitted(world, sorted(flat_ids))


async def _rows_and_expansions(world: _World, app_id: str) -> list[tuple[str, bool, list[str]]]:
    """Each row of the app's listing and of every container's expansion, with its arrow and what opening it lists."""
    seen: list[tuple[str, bool, list[str]]] = []
    queue: list[tuple[str | None, str | None]] = [(None, None)]
    while queue:
        start, kind = queue.pop(0)
        kw = {"start_id": start, "start_type": kind} if start else {}
        for row in (await _page(world, app_id, flatten=False, **kw))["rows"]:
            node_type = "recordGroup" if row["nodeType"] == "recordGroup" else "record"
            opened = [r["id"] for r in (await _page(
                world, app_id, flatten=False, start_id=row["id"], start_type=node_type))["rows"]]
            seen.append((row["id"], row["hasChildren"], opened))
            if opened:
                queue.append((row["id"], node_type))
    return seen


@pytest.mark.parametrize("app_id", ["bc", "bp", "kt", "ch", "ka", "kb", "mk", "mt", "st", "hc"])
async def test_an_arrow_is_on_exactly_when_the_expansion_lists_something(world: _World, app_id: str) -> None:
    rows = await _rows_and_expansions(world, app_id)

    assert rows
    assert [(i, arrow, bool(opened)) for i, arrow, opened in rows if arrow != bool(opened)] == []


async def test_a_flatten_started_at_a_channel_lists_only_what_the_check_admits(world: _World) -> None:
    page = await _page(world, "kt", flatten=True, start_id="kt-chan", start_type="recordGroup")
    ids = {row["id"] for row in page["rows"]}

    assert ids == {"kt-thread"}
    assert page["total"] == len(ids)
    assert ids <= await _admitted(world, sorted(ids))


async def test_a_filtered_browse_gives_a_group_with_no_visible_child_no_arrow(world: _World) -> None:
    page = await _page(world, "ch", flatten=False, filters={"node_types": ["recordGroup"]})
    arrows = {row["id"]: row["hasChildren"] for row in page["rows"]}

    assert arrows == {"ch-chan": False}


async def test_a_stub_is_listed_only_when_a_hierarchy_path_leads_to_something_readable(world: _World) -> None:
    page = await _page(world, "pp", flatten=False, start_id="pp-dir", start_type="recordGroup")

    assert {row["id"] for row in page["rows"]} == set()
    assert page["total"] == 0
