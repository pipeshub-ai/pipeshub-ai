"""The hierarchy backfill on a graph in the shape main wrote before the permission
rework: record -> record hierarchy only, a collection's items and a connector's
groups hanging off their App by membership alone, nested records inheriting from
their group. Run on both backends; a second run adds nothing."""

from __future__ import annotations

import pytest

from .fixture_graph import _node, app, bt, ip, nr, perm, rec, rg
from .loaders import NODE_TARGETS, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-hb"
USER = "hb-u"
GRANTEE = "hb-v"
KB_APP, CONN = "hb-kb", "hb-conn"


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _graph() -> tuple[list[dict], list[dict]]:
    group = {"connector": "DRIVE", "connectorId": CONN, "group_type": "DRIVE"}
    nodes = [
        _org(_node("User", USER, userId="hb-m", email="hb@example.com", fullName="U")),
        _org(app(KB_APP, "Collection", connector="KB", app_group="Local Storage", scope="personal")),
        _org(app(CONN, "Drive", connector="DRIVE", app_group="Google Workspace")),
        _org(rec("hb-r1", "Folder", connector_id=KB_APP, origin="UPLOAD")),
        _org(rec("hb-r2", "Nested file", connector_id=KB_APP, origin="UPLOAD", externalParentId="ext-hb-r1")),
        _org(rg("hb-g1", "Top group", **group)),
        _org(rg("hb-g2", "Sub group", **group)),
        _org(rec("hb-x1", "Folder x1", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-x2", "Nested x2", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-x1")),
        _org(rec("hb-y1", "Item y1", connector_id=CONN, recordGroupId="hb-g2")),
        # A parent record granted to someone the group does not include.
        _org(_node("User", GRANTEE, userId="hb-vm", email="hbv@example.com", fullName="V")),
        _org(rec("hb-z1", "Granted folder", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-z2", "Below it", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-z1")),
        # In two groups, its own not named (a Shared-with-Me copy).
        _org(rec("hb-w1", "Shared copy", connector_id=CONN)),
        # Edge cases (second test).
        _org(rg("hb-g3", "Other project", **group)),
        _org(rec("hb-p2", "Epic in g1", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-c2", "Story in g3", connector_id=CONN, recordGroupId="hb-g3", externalParentId="ext-hb-p2")),
        _org(rec("hb-a3", "Granted top", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-p3", "Middle", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-a3")),
        _org(rec("hb-c3", "Bottom", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-p3")),
        _org(rec("hb-x4", "Linked, granted", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-p4", "Parent p4", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-c4", "Child c4", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-p4")),
        _org(rec("hb-p5", "Parent p5", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-c5", "Child c5", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-p5")),
        _org(rec("hb-r5", "Twice in the collection", connector_id=KB_APP, origin="UPLOAD")),
        _org(rec("hb-v6", "Own group g2", connector_id=CONN, recordGroupId="hb-g2")),
        _org(rec("hb-s7", "Stale parent id", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-gone")),
    ]
    edges = [
        bt("hb-r1", KB_APP), bt("hb-r2", KB_APP), ip("hb-r1", KB_APP), ip("hb-r2", KB_APP), nr("hb-r1", "hb-r2"),
        bt("hb-g1", CONN), bt("hb-g2", "hb-g1"),
        bt("hb-x1", "hb-g1"), ip("hb-x1", "hb-g1"),
        bt("hb-x2", "hb-g1"), ip("hb-x2", "hb-g1"), nr("hb-x1", "hb-x2"),
        bt("hb-y1", "hb-g2"), ip("hb-y1", "hb-g2"),
        perm(USER, CONN), perm(USER, "hb-g1"),
        bt("hb-z1", "hb-g1"), ip("hb-z1", "hb-g1"), bt("hb-z2", "hb-g1"), ip("hb-z2", "hb-g1"), nr("hb-z1", "hb-z2"),
        perm(GRANTEE, CONN), perm(GRANTEE, "hb-z1"),
        bt("hb-w1", "hb-g1"), bt("hb-w1", "hb-g2"),
        bt("hb-g3", CONN), perm(GRANTEE, "hb-g3"),
        bt("hb-p2", "hb-g1"), ip("hb-p2", "hb-g1"), bt("hb-c2", "hb-g3"), ip("hb-c2", "hb-g3"), nr("hb-p2", "hb-c2"),
        bt("hb-a3", "hb-g1"), ip("hb-a3", "hb-g1"), perm(GRANTEE, "hb-a3"),
        bt("hb-p3", "hb-g1"), ip("hb-p3", "hb-g1"), nr("hb-a3", "hb-p3"),
        bt("hb-c3", "hb-g1"), ip("hb-c3", "hb-g1"), nr("hb-p3", "hb-c3"),
        bt("hb-x4", "hb-g1"), ip("hb-x4", "hb-g1"), perm(GRANTEE, "hb-x4"),
        {"type": "NODE_RELATION", "from": "hb-x4", "to": "hb-p4", "props": {"createdAtTimestamp": 1}},
        bt("hb-p4", "hb-g1"), ip("hb-p4", "hb-g1"), bt("hb-c4", "hb-g1"), ip("hb-c4", "hb-g1"), nr("hb-p4", "hb-c4"),
        bt("hb-p5", "hb-g1"), ip("hb-p5", "hb-g1"), bt("hb-c5", "hb-g1"), ip("hb-c5", "hb-g1"),
        nr("hb-p5", "hb-c5"), nr("hb-p5", "hb-c5", "ATTACHMENT"),
        bt("hb-r5", KB_APP), bt("hb-r5", KB_APP), ip("hb-r5", KB_APP),
        bt("hb-v6", "hb-g1"), bt("hb-v6", "hb-g2"), ip("hb-v6", "hb-g2"),
        bt("hb-s7", "hb-g1"), ip("hb-s7", "hb-g1"),
    ]
    return nodes, edges


NODES, EDGES = _graph()
KIND = {n["id"]: n["kind"] for n in NODES}


class _Neo4j:
    def __init__(self, provider, settings) -> None:
        self.provider, self.settings = provider, settings

    async def load(self) -> None:
        await self.drop()
        await load_into_neo4j(self.settings, NODES, EDGES)

    async def drop(self) -> None:
        await self.provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'hb-' DETACH DELETE n")

    async def edge(self, rel: str, a: str, b: str) -> bool:
        return await self.count(rel, a, b) > 0

    async def count(self, rel: str, a: str, b: str) -> int:
        rows = await self.provider.client.execute_query(
            f"MATCH ({{id: $a}})-[r:{rel}]->({{id: $b}}) RETURN count(r) AS n", parameters={"a": a, "b": b})
        return rows[0]["n"]


class _Arango:
    EDGES = ("permission", "belongsTo", "nodeRelations", "inheritPermissions")

    def __init__(self, provider, settings) -> None:
        self.provider, self.settings = provider, settings

    async def load(self) -> None:
        await self.drop()
        await load_into_arango(self.settings, NODES, EDGES)

    async def drop(self) -> None:
        for c in self.EDGES:
            await self.provider.http_client.execute_aql(
                "FOR e IN @@c FILTER CONTAINS(e._from, '/hb-') OR CONTAINS(e._to, '/hb-') REMOVE e IN @@c", {"@c": c})
        for c in set(NODE_TARGETS.values()):
            await self.provider.http_client.execute_aql(
                "FOR d IN @@c FILTER STARTS_WITH(d._key, 'hb-') REMOVE d IN @@c", {"@c": c})

    async def edge(self, rel: str, a: str, b: str) -> bool:
        return await self.count(rel, a, b) > 0

    async def count(self, rel: str, a: str, b: str) -> int:
        coll = {"NODE_RELATION": "nodeRelations", "INHERIT_PERMISSIONS": "inheritPermissions"}[rel]
        rows = await self.provider.http_client.execute_aql(
            f"RETURN LENGTH(FOR e IN {coll} FILTER e._from == @a AND e._to == @b RETURN 1)",
            {"a": f"{NODE_TARGETS[KIND[a]]}/{a}", "b": f"{NODE_TARGETS[KIND[b]]}/{b}"})
        return rows[0]


@pytest.fixture(params=["neo4j", "arango"])
async def store(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    backend = (_Arango(arango_provider, arango_settings) if request.param == "arango"
               else _Neo4j(neo4j_provider, neo4j_settings))
    await backend.load()
    yield backend
    await backend.drop()


async def _admitted(provider, ids: list[str]) -> set[str]:
    return set((await provider.check_access(USER, ORG, node_ids=ids)).node_ids)


async def test_the_backfill_writes_what_a_fresh_sync_writes(store) -> None:
    before = await _admitted(store.provider, ["hb-x1", "hb-x2"])
    assert "hb-x2" not in before, "the premise: a nested record is unreachable without the backfill"

    result = await store.provider.backfill_hierarchy(batch_size=2)

    wanted = [
        ("NODE_RELATION", KB_APP, "hb-r1"), ("NODE_RELATION", CONN, "hb-g1"), ("NODE_RELATION", "hb-g1", "hb-g2"),
        ("NODE_RELATION", "hb-g1", "hb-x1"), ("NODE_RELATION", "hb-g2", "hb-y1"),
        ("INHERIT_PERMISSIONS", "hb-x2", "hb-x1"),
    ]
    missing = [w for w in wanted if not await store.edge(*w)]
    assert missing == []
    unwanted = [
        ("NODE_RELATION", KB_APP, "hb-r2"), ("NODE_RELATION", "hb-g1", "hb-x2"), ("NODE_RELATION", CONN, "hb-g2"),
        ("INHERIT_PERMISSIONS", "hb-g1", CONN), ("INHERIT_PERMISSIONS", "hb-r2", "hb-r1"),
        # hb-z1 is granted to hb-v, whom main never gave hb-z2.
        ("INHERIT_PERMISSIONS", "hb-z2", "hb-z1"),
        ("NODE_RELATION", "hb-g1", "hb-w1"), ("NODE_RELATION", "hb-g2", "hb-w1"),
    ]
    present = [u for u in unwanted if await store.edge(*u)]
    assert present == []
    assert sum(result["added"].values()) >= len(wanted)
    assert await _admitted(store.provider, ["hb-x1", "hb-x2", "hb-y1"]) == {"hb-x1", "hb-x2"}

    assert "hb-z2" not in set((await store.provider.check_access(GRANTEE, ORG, node_ids=["hb-z2"])).node_ids)

    again = await store.provider.backfill_hierarchy()
    assert sum(again["added"].values()) == 0


async def test_an_edge_from_a_removed_document_is_no_parent(store) -> None:
    """The KB apps migration removes old KB groups without their hierarchy edges;
    an item left under one is still a collection root (Arango only: Neo4j cannot
    hold an edge without its node)."""
    if not isinstance(store, _Arango):
        pytest.skip("Arango only")
    await store.provider.http_client.execute_aql(
        """INSERT {_from: "recordGroups/hb-gone", _to: "records/hb-r1", relationshipType: "PARENT_CHILD",
                   createdAtTimestamp: 1} INTO nodeRelations""", {})

    await store.provider.backfill_hierarchy()

    assert await store.edge("NODE_RELATION", KB_APP, "hb-r1")


async def test_the_backfill_edge_cases(store) -> None:
    await store.provider.backfill_hierarchy(batch_size=1000)

    # A parent in another group: no inheritance, and that group's audience does not gain the child.
    assert not await store.edge("INHERIT_PERMISSIONS", "hb-c2", "hb-p2")
    assert "hb-c2" not in await _admitted(store.provider, ["hb-c2"])
    # It hangs off its own group as well, so that group's audience keeps reaching it.
    assert await store.edge("NODE_RELATION", "hb-g3", "hb-c2")
    assert await store.edge("NODE_RELATION", "hb-p2", "hb-c2")
    assert "hb-c2" in set((await store.provider.check_access(GRANTEE, ORG, node_ids=["hb-c2"])).node_ids)
    # A grant two levels up blocks inheritance at every level below it.
    assert not await store.edge("INHERIT_PERMISSIONS", "hb-p3", "hb-a3")
    assert not await store.edge("INHERIT_PERMISSIONS", "hb-c3", "hb-p3")
    assert "hb-c3" not in set((await store.provider.check_access(GRANTEE, ORG, node_ids=["hb-c3"])).node_ids)
    # An untyped edge is no ancestor, so its grant does not block the child.
    assert await store.edge("INHERIT_PERMISSIONS", "hb-c4", "hb-p4")
    # Two hierarchy edges, or two memberships, are still one edge.
    assert await store.count("INHERIT_PERMISSIONS", "hb-c5", "hb-p5") == 1
    assert await store.count("NODE_RELATION", KB_APP, "hb-r5") == 1
    # The record's own group among several; a stale parent id gets nothing.
    assert await store.edge("NODE_RELATION", "hb-g2", "hb-v6")
    assert not await store.edge("NODE_RELATION", "hb-g1", "hb-v6")
    assert not await store.edge("NODE_RELATION", "hb-g1", "hb-s7")
