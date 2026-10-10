"""The hierarchy backfill on a graph in the shape main wrote before the permission
rework: record -> record hierarchy only, a collection's items and a connector's
groups hanging off their App by membership alone, nested records inheriting from
their group. Run on both backends; a second run adds nothing."""

from __future__ import annotations

import logging
from collections import Counter
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.hierarchy_backfill_migration import (
    HierarchyBackfillMigrationService,
    HierarchyNestedGroupRootsMigrationService,
    run_hierarchy_backfill_migration,
    run_hierarchy_nested_group_roots_migration,
)

from .fixture_graph import _node, app, bt, ip, nr, perm, rec, rg
from .loaders import NODE_TARGETS, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

ORG = "org-hb"
USER = "hb-u"
GRANTEE = "hb-v"
OWNER = "hb-o"
EDITORS = "hb-editors"
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
        # Below granted records (third test): an edit restriction, own ACLs, a deeper chain.
        _org(_node("User", OWNER, userId="hb-om", email="hbo@example.com", fullName="O")),
        _org(_node("Group", EDITORS, name="Editors")),
        _org(rec("hb-e1", "Edit-restricted page", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-e2", "Page below it", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-e1")),
        _org(rec("hb-o1", "Folder with its own ACL", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-o2", "Item with its own ACL", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-o1")),
        _org(rec("hb-d4", "Four down", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-c3")),
        # Written the way a sync on this branch writes them, below a granted record:
        # n2 inherits from its parent, n3 (parent in another group) from its own
        # group, which lists it.
        _org(rec("hb-n1", "Synced, granted", connector_id=CONN, recordGroupId="hb-g1")),
        _org(rec("hb-n2", "Synced child", connector_id=CONN, recordGroupId="hb-g1", externalParentId="ext-hb-n1")),
        _org(rec("hb-n3", "Synced, other group", connector_id=CONN, recordGroupId="hb-g3",
                 externalParentId="ext-hb-n1")),
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
        perm(OWNER, CONN), perm(GRANTEE, EDITORS),
        bt("hb-e1", "hb-g1"), ip("hb-e1", "hb-g1"), perm(EDITORS, "hb-e1", grant_type="GROUP", role="WRITER"),
        bt("hb-e2", "hb-g1"), ip("hb-e2", "hb-g1"), nr("hb-e1", "hb-e2"),
        bt("hb-o1", "hb-g1"), ip("hb-o1", "hb-g1"), perm(GRANTEE, "hb-o1", role="OWNER"),
        bt("hb-o2", "hb-g1"), ip("hb-o2", "hb-g1"), nr("hb-o1", "hb-o2"), perm(OWNER, "hb-o2", role="OWNER"),
        bt("hb-d4", "hb-g1"), ip("hb-d4", "hb-g1"), nr("hb-c3", "hb-d4"),
        bt("hb-n1", "hb-g1"), ip("hb-n1", "hb-g1"), nr("hb-g1", "hb-n1"), perm(GRANTEE, "hb-n1"),
        bt("hb-n2", "hb-g1"), ip("hb-n2", "hb-n1"), nr("hb-n1", "hb-n2"),
        bt("hb-n3", "hb-g3"), ip("hb-n3", "hb-g3"), nr("hb-n1", "hb-n3"), nr("hb-g3", "hb-n3"),
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

    async def edges(self) -> Counter:
        rows = await self.provider.client.execute_query(
            "MATCH (a)-[r]->(b) WHERE a.id STARTS WITH 'hb-' OR b.id STARTS WITH 'hb-' "
            "RETURN type(r) AS t, a.id AS a, b.id AS b")
        return Counter((r["t"], r["a"], r["b"]) for r in rows)

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

    async def edges(self) -> Counter:
        found: Counter = Counter()
        for coll, rel in (("permission", "PERMISSION"), ("belongsTo", "BELONGS_TO"),
                          ("nodeRelations", "NODE_RELATION"), ("inheritPermissions", "INHERIT_PERMISSIONS")):
            rows = await self.provider.http_client.execute_aql(
                "FOR e IN @@c FILTER CONTAINS(e._from, '/hb-') OR CONTAINS(e._to, '/hb-')"
                " RETURN [PARSE_IDENTIFIER(e._from).key, PARSE_IDENTIFIER(e._to).key]", {"@c": coll})
            found.update((rel, a, b) for a, b in rows)
        return found

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


BELOW_GRANTED = ["hb-z2", "hb-p3", "hb-c3", "hb-d4", "hb-e2", "hb-o2"]


async def test_records_below_a_granted_record_keep_exactly_their_groups_audience(store) -> None:
    """Main read a nested record through its group alone. A grant on a record above
    it (an edit restriction, a folder's own ACL) must neither hide it from the
    group's audience nor hand it to that grant's holders."""
    await store.provider.backfill_hierarchy()

    async def admitted(user: str) -> set[str]:
        return set((await store.provider.check_access(user, ORG, node_ids=BELOW_GRANTED)).node_ids)

    assert await admitted(USER) == set(BELOW_GRANTED)
    assert await admitted(GRANTEE) == set()
    assert await admitted(OWNER) == {"hb-o2"}
    for child in BELOW_GRANTED:
        assert await store.edge("NODE_RELATION", "hb-g1", child), child
    for child, parent in [("hb-e2", "hb-e1"), ("hb-o2", "hb-o1"), ("hb-d4", "hb-c3")]:
        assert not await store.edge("INHERIT_PERMISSIONS", child, parent), child
    # The records above them keep their place.
    assert not await store.edge("NODE_RELATION", "hb-g1", "hb-x2")
    assert await store.count("NODE_RELATION", "hb-g1", "hb-e1") == 1

    again = await store.provider.backfill_hierarchy()
    assert sum(again["added"].values()) == 0


# The shapes the backfill wrote before nested_group_roots existed: what play and the
# QA stacks ran, setting hierarchy_backfill_v1.
SHAPES_BEFORE_NESTED_GROUP_ROOTS = [
    "collection_roots", "groups", "group_roots", "nested_inheritance", "cross_group_children",
]


def _config(flags: dict) -> MagicMock:
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda key, *a, **k: flags.get(key))

    async def set_config(key, value, *a, **k) -> None:
        flags[key] = value

    config.set_config = AsyncMock(side_effect=set_config)
    return config


async def test_a_stack_backfilled_before_nested_group_roots_gets_them_from_its_own_step(store) -> None:
    """MA-01 on a stack whose backfill flag is already set: only the new step's
    edges are written, records synced by this branch are left alone, and a second
    run is a no-op."""
    await store.provider.backfill_hierarchy(shapes=SHAPES_BEFORE_NESTED_GROUP_ROOTS)
    flags = {HierarchyBackfillMigrationService.MIGRATION_FLAG_KEY: {"done": True}}
    config, log = _config(flags), logging.getLogger("test")

    async def admitted(user: str) -> set[str]:
        return set((await store.provider.check_access(user, ORG, node_ids=BELOW_GRANTED)).node_ids)

    assert await admitted(USER) == set(), "the premise: MA-01, nobody reads the records below a granted one"
    assert (await run_hierarchy_backfill_migration(store.provider, config, log))["skipped"] is True
    before = await store.edges()

    result = await run_hierarchy_nested_group_roots_migration(store.provider, config, log)

    assert result == {"success": True, "added": {"nested_group_roots": len(BELOW_GRANTED)}}
    after = await store.edges()
    assert after - before == Counter(("NODE_RELATION", "hb-g1", child) for child in BELOW_GRANTED)
    assert before - after == Counter()
    assert await admitted(USER) == set(BELOW_GRANTED)
    assert await admitted(GRANTEE) == set()
    assert await admitted(OWNER) == {"hb-o2"}
    assert flags[HierarchyNestedGroupRootsMigrationService.MIGRATION_FLAG_KEY]["done"] is True

    assert (await run_hierarchy_nested_group_roots_migration(store.provider, config, log))["skipped"] is True
    again = await store.provider.backfill_hierarchy(shapes=["nested_group_roots"])
    assert again == {"added": {"nested_group_roots": 0}}
    assert await store.edges() == after


async def test_the_nested_group_roots_step_is_a_no_op_after_a_full_backfill(store) -> None:
    """A fresh upgrade runs the whole backfill first; the step then finds nothing."""
    await store.provider.backfill_hierarchy()
    before = await store.edges()

    result = await run_hierarchy_nested_group_roots_migration(store.provider, _config({}), logging.getLogger("test"))

    assert result == {"success": True, "added": {"nested_group_roots": 0}}
    assert await store.edges() == before
