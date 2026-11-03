"""The redundant group edges migration on both backends: a nested record that
inherits from a parent record of its own group loses its inheritance edge to
the group and the group's hierarchy edge to it, as a sync of that record does
today. Every other shape keeps its edges, and a second run removes nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .fixture_graph import _node, app, bt, ip, nr, perm, rec, rg
from .loaders import NODE_TARGETS, load_into_arango, load_into_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

ORG = "org-rge"
USER = "rge-u"
CONN, G1, G2 = "rge-conn", "rge-g1", "rge-g2"


def _org(node: dict) -> dict:
    node["props"]["orgId"] = ORG
    return node


def _in(group: str, record: str) -> list[dict]:
    return [bt(record, group), ip(record, group), nr(group, record)]


def _graph() -> tuple[list[dict], list[dict]]:
    group = {"connector": "JIRA", "connectorId": CONN, "group_type": "PROJECT"}

    def r(rid: str, gid: str, **props: object) -> dict:
        return _org(rec(rid, rid, connector_id=CONN, recordGroupId=gid, **props))

    nodes = [
        _org(_node("User", USER, userId="rge-um", email="rge-u@example.com", fullName="U")),
        _org(app(CONN, "Jira", connector="JIRA", app_group="Atlassian")),
        _org(rg(G1, "Project one", **group)), _org(rg(G2, "Project two", **group)),
        r("rge-p", G1), r("rge-both", G1), r("rge-hier", G1), r("rge-noinh", G1),
        r("rge-px", G2), r("rge-cross", G1),
        r("rge-ph", G1, isPlaceholder=True), r("rge-under-ph", G1),
        r("rge-pdel", G1, isDeleted=True), r("rge-under-del", G1),
        r("rge-shared", G1),
        r("rge-pr", G1, rule="RESTRICTED"), r("rge-under-r", G1),
    ]
    edges = [
        perm(USER, CONN), perm(USER, G1), perm(USER, G2),
        bt(G1, CONN), nr(CONN, G1), bt(G2, CONN), nr(CONN, G2),
        *_in(G1, "rge-p"), *_in(G2, "rge-px"), *_in(G1, "rge-ph"), *_in(G1, "rge-pdel"), *_in(G1, "rge-pr"),
        # Both group edges and the parent's inheritance: the shape the backfill left.
        *_in(G1, "rge-both"), nr("rge-p", "rge-both"), ip("rge-both", "rge-p"),
        # Only the group's hierarchy edge is left over.
        bt("rge-hier", G1), nr(G1, "rge-hier"), nr("rge-p", "rge-hier"), ip("rge-hier", "rge-p"),
        # Does not inherit from its parent: the group is its only audience.
        *_in(G1, "rge-noinh"), nr("rge-p", "rge-noinh"),
        # Its parent is in another group.
        *_in(G1, "rge-cross"), nr("rge-px", "rge-cross"), ip("rge-cross", "rge-px"),
        # A placeholder parent, a deleted parent.
        *_in(G1, "rge-under-ph"), nr("rge-ph", "rge-under-ph"), ip("rge-under-ph", "rge-ph"),
        *_in(G1, "rge-under-del"), nr("rge-pdel", "rge-under-del"), ip("rge-under-del", "rge-pdel"),
        # A second group it is shared into is not its own group.
        bt("rge-shared", G1), nr("rge-p", "rge-shared"), ip("rge-shared", "rge-p"), *_in(G2, "rge-shared"),
        # Under a restricted parent.
        *_in(G1, "rge-under-r"), nr("rge-pr", "rge-under-r"), ip("rge-under-r", "rge-pr"),
    ]
    return nodes, edges


NODES, EDGES = _graph()
KIND = {n["id"]: n["kind"] for n in NODES}


class _Neo4j:
    def __init__(self, provider: object, settings: object) -> None:
        self.provider, self.settings = provider, settings

    async def load(self) -> None:
        await self.drop()
        await load_into_neo4j(self.settings, NODES, EDGES)

    async def drop(self) -> None:
        await self.provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'rge-' DETACH DELETE n")

    async def edge(self, rel: str, a: str, b: str) -> bool:
        rows = await self.provider.client.execute_query(
            f"MATCH ({{id: $a}})-[r:{rel}]->({{id: $b}}) RETURN count(r) AS n", parameters={"a": a, "b": b})
        return rows[0]["n"] > 0


class _Arango:
    EDGES = ("permission", "belongsTo", "nodeRelations", "inheritPermissions")
    COLL = {"NODE_RELATION": "nodeRelations", "INHERIT_PERMISSIONS": "inheritPermissions", "BELONGS_TO": "belongsTo"}

    def __init__(self, provider: object, settings: object) -> None:
        self.provider, self.settings = provider, settings

    async def load(self) -> None:
        await self.drop()
        await load_into_arango(self.settings, NODES, EDGES)

    async def drop(self) -> None:
        for c in self.EDGES:
            await self.provider.http_client.execute_aql(
                "FOR e IN @@c FILTER CONTAINS(e._from, '/rge-') OR CONTAINS(e._to, '/rge-') REMOVE e IN @@c",
                {"@c": c})
        for c in set(NODE_TARGETS.values()):
            await self.provider.http_client.execute_aql(
                "FOR d IN @@c FILTER STARTS_WITH(d._key, 'rge-') REMOVE d IN @@c", {"@c": c})

    async def edge(self, rel: str, a: str, b: str) -> bool:
        rows = await self.provider.http_client.execute_aql(
            f"RETURN LENGTH(FOR e IN {self.COLL[rel]} FILTER e._from == @a AND e._to == @b RETURN 1)",
            {"a": f"{NODE_TARGETS[KIND[a]]}/{a}", "b": f"{NODE_TARGETS[KIND[b]]}/{b}"})
        return rows[0] > 0


@pytest.fixture(params=["neo4j", "arango"])
async def store(
    request: pytest.FixtureRequest, neo4j_provider: object, neo4j_settings: object,
    arango_provider: object, arango_settings: object,
) -> AsyncIterator[_Arango | _Neo4j]:
    backend = (_Arango(arango_provider, arango_settings) if request.param == "arango"
               else _Neo4j(neo4j_provider, neo4j_settings))
    await backend.load()
    yield backend
    await backend.drop()


async def _admitted(provider: object, user: str, ids: list[str]) -> set[str]:
    return set((await provider.check_access(user, ORG, node_ids=ids)).node_ids)


async def test_only_the_edges_a_sync_removes_are_removed(store: _Arango | _Neo4j) -> None:
    result = await store.provider.drop_redundant_group_edges(batch_size=2)

    gone = [
        ("INHERIT_PERMISSIONS", "rge-both", G1), ("NODE_RELATION", G1, "rge-both"),
        ("NODE_RELATION", G1, "rge-hier"),
        ("INHERIT_PERMISSIONS", "rge-under-r", G1), ("NODE_RELATION", G1, "rge-under-r"),
    ]
    assert [e for e in gone if await store.edge(*e)] == []
    kept = [
        ("BELONGS_TO", "rge-both", G1), ("INHERIT_PERMISSIONS", "rge-both", "rge-p"),
        ("NODE_RELATION", "rge-p", "rge-both"), ("BELONGS_TO", "rge-hier", G1),
        ("INHERIT_PERMISSIONS", "rge-noinh", G1), ("NODE_RELATION", G1, "rge-noinh"),
        ("INHERIT_PERMISSIONS", "rge-cross", G1), ("NODE_RELATION", G1, "rge-cross"),
        ("INHERIT_PERMISSIONS", "rge-under-ph", G1), ("NODE_RELATION", G1, "rge-under-ph"),
        ("INHERIT_PERMISSIONS", "rge-under-del", G1), ("NODE_RELATION", G1, "rge-under-del"),
        ("INHERIT_PERMISSIONS", "rge-shared", G2), ("NODE_RELATION", G2, "rge-shared"),
        ("INHERIT_PERMISSIONS", "rge-p", G1), ("NODE_RELATION", G1, "rge-p"),
    ]
    assert [e for e in kept if not await store.edge(*e)] == []
    assert result["removed"] == 3

    again = await store.provider.drop_redundant_group_edges()
    assert again["removed"] == 0


async def test_access_is_what_the_parent_gives(store: _Arango | _Neo4j) -> None:
    ids = ["rge-both", "rge-hier", "rge-under-r"]
    assert await _admitted(store.provider, USER, ids) == set(ids), "the premise: the group lent its audience"

    await store.provider.drop_redundant_group_edges()

    # Reached through the parent record now, so a restricted parent stops the group's audience.
    assert await _admitted(store.provider, USER, ids) == {"rge-both", "rge-hier"}


async def test_the_tree_labels_follow_the_edges(store: _Arango | _Neo4j) -> None:
    if not isinstance(store, _Neo4j):
        pytest.skip("listing labels are Neo4j only")

    await store.provider.drop_redundant_group_edges()

    rows = await store.provider.client.execute_query(
        "MATCH (n:Record {id: 'rge-both'}) RETURN n:KhInherits AS one, n:KhMultiParent AS many")
    assert rows == [{"one": True, "many": False}]
