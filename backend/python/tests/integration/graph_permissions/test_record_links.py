"""Links between records are stored apart from the hierarchy: on
RECORD_LINK in Neo4j and in the recordLinks collection in Arango, while shared
code keeps addressing them through the logical nodeRelations collection. Every
test runs on both backends."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.config.constants.arangodb import CollectionNames

from .fixture_graph import rec
from .loaders import load_into_arango, load_into_neo4j

RECORDS = CollectionNames.RECORDS.value
RELATIONS = CollectionNames.NODE_RELATIONS.value
LINKS = CollectionNames.RECORD_LINKS.value


class _Neo4jStore:
    def __init__(self, provider, settings: dict) -> None:
        self.provider, self.settings = provider, settings

    async def load(self, nodes: list[dict]) -> None:
        await load_into_neo4j(self.settings, nodes, [])

    async def drop(self, ids: list[str]) -> None:
        await self.provider.client.execute_query(
            "MATCH (r:Record) WHERE r.id IN $ids DETACH DELETE r", parameters={"ids": ids},
        )

    async def edges(self, a: str, b: str) -> list[tuple[str, str | None]]:
        rows = await self.provider.client.execute_query(
            "MATCH (:Record {id: $a})-[r]->(:Record {id: $b}) RETURN type(r) AS t, r.relationshipType AS rt",
            parameters={"a": a, "b": b},
        )
        return sorted((r["t"], r["rt"]) for r in rows)

    async def link_props(self, a: str, prop: str) -> list:
        rows = await self.provider.client.execute_query(
            f"MATCH (:Record {{id: $a}})-[r:RECORD_LINK]->() RETURN r.{prop} AS v ORDER BY v",
            parameters={"a": a},
        )
        return [r["v"] for r in rows]

    async def seed_legacy(self, edges: list[tuple[str, str, dict]]) -> None:
        """Edges written before links had their own storage: everything on NODE_RELATION."""
        await self.provider.client.execute_query(
            "UNWIND $edges AS e MATCH (a:Record {id: e.a}), (b:Record {id: e.b}) "
            "CREATE (a)-[r:NODE_RELATION]->(b) SET r = e.props",
            parameters={"edges": [{"a": a, "b": b, "props": p} for a, b, p in edges]},
        )


class _ArangoStore:
    KIND = {RELATIONS: "NODE_RELATION", LINKS: "RECORD_LINK"}

    def __init__(self, provider, settings: dict) -> None:
        self.provider, self.settings = provider, settings

    async def _aql(self, query: str, bind: dict) -> list:
        return await self.provider.http_client.execute_aql(query, bind) or []

    async def load(self, nodes: list[dict]) -> None:
        await load_into_arango(self.settings, nodes, [])

    async def drop(self, ids: list[str]) -> None:
        handles = [f"{RECORDS}/{i}" for i in ids]
        for edges in (RELATIONS, LINKS):
            await self._aql("FOR e IN @@c FILTER e._from IN @h OR e._to IN @h REMOVE e IN @@c", {"@c": edges, "h": handles})
        await self._aql("FOR r IN records FILTER r._key IN @ids REMOVE r IN records", {"ids": ids})

    async def edges(self, a: str, b: str) -> list[tuple[str, str | None]]:
        found = []
        for edges, kind in self.KIND.items():
            rows = await self._aql(
                "FOR e IN @@c FILTER e._from == @a AND e._to == @b RETURN e.relationshipType",
                {"@c": edges, "a": f"{RECORDS}/{a}", "b": f"{RECORDS}/{b}"},
            )
            found += [(kind, rt) for rt in rows]
        return sorted(found, key=lambda x: (x[0], x[1] or ""))

    async def link_props(self, a: str, prop: str) -> list:
        rows = await self._aql(
            f"FOR e IN {LINKS} FILTER e._from == @a SORT e.{prop} RETURN e.{prop}", {"a": f"{RECORDS}/{a}"},
        )
        return list(rows)

    async def seed_legacy(self, edges: list[tuple[str, str, dict]]) -> None:
        await self._aql(
            "FOR e IN @edges INSERT MERGE(e.props, {_from: e.a, _to: e.b}) IN nodeRelations",
            {"edges": [{"a": f"{RECORDS}/{a}", "b": f"{RECORDS}/{b}", "props": p} for a, b, p in edges]},
        )


@pytest.fixture(params=["neo4j", "arango"])
def store(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings):
    if request.param == "arango":
        return _ArangoStore(arango_provider, arango_settings)
    return _Neo4jStore(neo4j_provider, neo4j_settings)


@pytest.fixture
async def records(store) -> AsyncIterator[dict[str, str]]:
    """Three fresh records a, b, c; removed afterwards."""
    ids = {name: f"link-{name}-{uuid.uuid4().hex[:8]}" for name in ("a", "b", "c")}
    await store.load([rec(i, i) for i in ids.values()])
    yield ids
    await store.drop(list(ids.values()))


def _sorted(edges: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    return sorted(edges, key=lambda x: (x[0], x[1] or ""))


async def test_links_and_hierarchy_are_stored_apart(store, records) -> None:
    a, b = records["a"], records["b"]
    for relation in ("PARENT_CHILD", "BLOCKS", "RELATED", "BLOCKS"):   # the second BLOCKS is idempotent
        await store.provider.create_record_relation(a, b, relation)

    assert _sorted(await store.edges(a, b)) == [
        ("NODE_RELATION", "PARENT_CHILD"), ("RECORD_LINK", "BLOCKS"), ("RECORD_LINK", "RELATED"),
    ]


async def test_link_readers_and_cleanup_see_links(store, records) -> None:
    provider = store.provider
    a, b, c = records["a"], records["b"], records["c"]
    await provider.create_record_relation(a, b, "BLOCKS")
    await provider.create_record_relation(a, c, "PARENT_CHILD")

    parents = await provider.get_parent_record_ids_by_relation_type(a, "BLOCKS")
    assert [p["record_id"] for p in parents] == [b]
    batch = await provider.get_node_relations_batch([a], ["BLOCKS"])
    assert [p["record_id"] for p in batch[a]["parents"]] == [b]
    outgoing = await provider.get_edges_from_node(f"{RECORDS}/{a}", RELATIONS)
    assert sorted(e["relationshipType"] for e in outgoing) == ["BLOCKS", "PARENT_CHILD"]

    # A single-pair delete is a hierarchy operation: the link survives it.
    await provider.create_record_relation(a, b, "PARENT_CHILD")
    await provider.delete_edge(a, RECORDS, b, RECORDS, RELATIONS)
    assert await store.edges(a, b) == [("RECORD_LINK", "BLOCKS")]

    assert await provider.delete_edges_by_relationship_types(a, RECORDS, RELATIONS, ["BLOCKS"]) == 1
    assert await store.edges(a, b) == []
    assert await store.edges(a, c) == [("NODE_RELATION", "PARENT_CHILD")]


async def test_a_hierarchy_write_does_not_overwrite_a_link_waiting_for_the_migration(store, records) -> None:
    """Before the split runs, a legacy link sits in the hierarchy storage; writing
    the pair's hierarchy edge adds it beside the link instead of replacing it.
    Arango only: Neo4j's hierarchy MERGE matches any NODE_RELATION of the pair,
    and its split runs at startup before any sync writes."""
    if isinstance(store, _Neo4jStore):
        pytest.skip("Neo4j splits the links at startup, before any write")
    a, b = records["a"], records["b"]
    await store.seed_legacy([(a, b, {"relationshipType": "BLOCKS"})])
    await store.provider.create_record_relation(a, b, "PARENT_CHILD")
    assert _sorted(await store.edges(a, b)) == [("NODE_RELATION", "BLOCKS"), ("NODE_RELATION", "PARENT_CHILD")]


async def test_foreign_keys_are_links(store, records) -> None:
    a, b = records["a"], records["b"]
    await store.provider.batch_upsert_node_relations([
        {"from_id": a, "from_collection": RECORDS, "to_id": b, "to_collection": RECORDS,
         "relationshipType": "FOREIGN_KEY", "constraintName": "fk_1", "sourceColumn": "b_id", "targetColumn": "id"},
    ])
    assert await store.edges(a, b) == [("RECORD_LINK", "FOREIGN_KEY")]
    children = await store.provider.get_child_record_ids_by_relation_type(b, "FOREIGN_KEY")
    assert [(c["record_id"], c["sourceColumn"]) for c in children] == [(a, "b_id")]


async def test_the_migration_moves_legacy_links_only(store, records) -> None:
    a, b, c = records["a"], records["b"], records["c"]
    await store.seed_legacy([
        (a, b, {"relationshipType": "BLOCKS", "createdAtTimestamp": 1}),
        (b, c, {"relationType": "SIBLING"}),
        (a, c, {"relationshipType": "PARENT_CHILD"}),
        (c, a, {}),
    ])

    first = await store.provider.split_link_edges()
    assert first["migrated"] >= 2

    assert await store.edges(a, b) == [("RECORD_LINK", "BLOCKS")]
    assert await store.edges(b, c) == [("RECORD_LINK", "SIBLING")]
    assert await store.edges(a, c) == [("NODE_RELATION", "PARENT_CHILD")]
    assert await store.edges(c, a) == [("NODE_RELATION", None)]
    assert await store.link_props(b, "relationType") == [None]
    assert await store.link_props(a, "createdAtTimestamp") == [1]

    assert (await store.provider.split_link_edges())["migrated"] == 0


async def test_the_migration_keeps_foreign_keys_apart(store, records) -> None:
    """orders.created_by and orders.updated_by both point at users: two edges."""
    a, b = records["a"], records["b"]
    await store.seed_legacy([
        (a, b, {"relationshipType": "FOREIGN_KEY", "constraintName": "fk_created", "sourceColumn": "created_by"}),
        (a, b, {"relationshipType": "FOREIGN_KEY", "constraintName": "fk_updated", "sourceColumn": "updated_by"}),
    ])
    await store.provider.split_link_edges(batch_size=1)
    assert await store.link_props(a, "sourceColumn") == ["created_by", "updated_by"]


async def test_a_delete_cascades_through_the_hierarchy_only(store, records) -> None:
    """Deleting a folder takes its children, never a record it merely links to or
    reaches by an untyped edge."""
    a, b, c = records["a"], records["b"], records["c"]
    await store.provider.create_record_relation(a, b, "PARENT_CHILD")
    await store.provider.create_record_relation(a, c, "BLOCKS")
    await store.seed_legacy([(a, c, {})])

    result = await store.provider.delete_records_recursive([a], "conn-1")

    assert {r["record_id"] for r in result["deleted_records"]} == {a, b}
    assert await store.provider.get_document(c, RECORDS)
