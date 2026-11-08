"""The start-up migrations away from legacy data models on PostgreSQL, against a real database: a knowledge base stored as a record group becomes its own
app, agent knowledge that names the shared hub app is expanded, and hierarchy edges stored under the pre-rename collection move onto ``nodeRelations``.

A PostgreSQL store never wrote these shapes itself, so every method is asked on a fresh store too (nothing to count, nothing to move) and on legacy data
as a copy from an older store would bring it. The answers are ``Neo4jProvider``'s; another org reads and changes nothing.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import MissingOrgContext, system
from app.migrations.kb_apps_migration import run_kb_apps_migration
from app.migrations.node_relation_migration import (
    LEGACY_ARANGO_COLLECTION,
    LEGACY_NEO4J_RELATIONSHIP_TYPE,
    run_node_relation_migration,
)
from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.postgres import legacy_migrations

from .kg_fresh import differences
from .test_access import World, _org
from .test_kg_derivation import held

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

pytestmark = pytest.mark.integration
LEGACY = LEGACY_ARANGO_COLLECTION


@pytest.fixture
async def orgs(pg_provider, pg_db, org_ctx) -> AsyncIterator[Callable[[], World]]:
    """``orgs()`` makes a fresh org's world; every one is removed afterwards."""
    made: list[str] = []

    def make() -> World:
        made.append(_org())
        return World(pg_provider, made[-1])
    yield make
    for org in made:
        with org_ctx(org):
            await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", o), write=True)
            await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


async def rows(w: World, sql: str, *args: object) -> list[dict]:
    return [dict(r) for r in await w.p._q(lambda tx: tx.fetch(sql, w.org, *args), None)]


async def graph(w: World) -> tuple[list, list]:
    """Every node and edge of the org, to show a call changed nothing."""
    return (await rows(w, "SELECT coll, id, doc::text FROM graph.nodes WHERE org_id = $1 ORDER BY coll, id"),
            await rows(w, "SELECT coll, from_coll, from_id, to_coll, to_id, ekey, doc::text FROM graph.edges WHERE org_id = $1 ORDER BY 1, 2, 3, 4, 5, 6"))


async def ends(w: World, coll: str, node: tuple[str, str]) -> set[tuple]:
    """(from, to) of every ``coll`` edge at the node, both ends as ``coll/id``."""
    got = await rows(w, """SELECT from_coll, from_id, to_coll, to_id FROM graph.edges WHERE org_id = $1 AND coll = $2
        AND ((from_coll = $3 AND from_id = $4) OR (to_coll = $3 AND to_id = $4))""", coll, *node)
    return {(f"{r['from_coll']}/{r['from_id']}", f"{r['to_coll']}/{r['to_id']}") for r in got}


# ---- knowledge bases stored as record groups -----------------------------------------------------------------------------------------------

def legacy_kb(key: str, org: str, **doc: object) -> dict:
    return {"_key": key, "groupName": key.upper(), "groupType": "KB", "connectorName": "KB", "connectorId": f"knowledgeBase_{org}", "externalGroupId": key,
            "createdBy": "uid-u1", "createdAtTimestamp": 10, "updatedAtTimestamp": 20, **doc}


async def legacy_world(w: World) -> None:
    """The pre-migration model: one hub app per org, every knowledge base a record group under it, its records naming the hub as their connector."""
    hub = f"knowledgeBase_{w.org}"
    await w.nodes("organizations", {"_key": w.org, "isActive": True})
    await w.nodes("users", {"_key": "u1", "userId": "uid-u1", "isActive": True}, {"_key": "u2", "userId": "uid-u2", "isActive": True})
    await w.nodes("teams", {"_key": "t1"})
    await w.nodes("apps", {"_key": hub, "type": "KB", "name": "Knowledge Base", "isActive": True})
    await w.edge("orgAppRelation", ("organizations", w.org), ("apps", hub), createdAtTimestamp=1)
    await w.edge("userAppRelation", ("users", "u1"), ("apps", hub), syncState="NOT_STARTED", lastSyncUpdate=1)
    await w.nodes("recordGroups", legacy_kb("kb1", w.org, description="Team docs", accessRule="OPEN", isInternal=False, shortName="d"),
                  legacy_kb("kb2", w.org, createdBy="uid-unknown"),
                  {"_key": "g", "groupName": "Drive", "groupType": "DRIVE", "connectorName": "DRIVE", "connectorId": "drive1"})
    for kb in ("kb1", "kb2"):
        await w.edge("belongsTo", ("recordGroups", kb), ("apps", hub), entityType="KB")
    await w.edge("permission", ("users", "u1"), ("recordGroups", "kb1"), type="USER", role="OWNER")
    await w.edge("permission", ("users", "u2"), ("recordGroups", "kb1"), type="USER", role="READER")
    await w.edge("permission", ("users", "u1"), ("teams", "t1"), type="USER", role="MEMBER")
    await w.edge("permission", ("teams", "t1"), ("recordGroups", "kb1"), type="TEAM", role="WRITER")
    await w.edge("permission", ("users", "u1"), ("recordGroups", "kb2"), type="USER", role="OWNER")
    rec = {"recordType": "FILE", "origin": "UPLOAD", "connectorId": hub, "createdAtTimestamp": 5}
    await w.nodes("records", {"_key": "fold", "recordName": "Folder", "mimeType": "text/directory", "externalGroupId": "kb1", **rec},
                  {"_key": "doc", "recordName": "Doc", "externalGroupId": "kb1", **rec}, {"_key": "other", "recordName": "Other", "externalGroupId": "kb2", **rec})
    for r in ("fold", "doc"):
        await w.edge("belongsTo", ("records", r), ("recordGroups", "kb1"), entityType="KB")
    await w.edge("belongsTo", ("records", "other"), ("recordGroups", "kb2"), entityType="KB")
    await w.edge("inheritPermissions", ("records", "fold"), ("recordGroups", "kb1"))
    await w.edge("nodeRelations", ("recordGroups", "kb1"), ("records", "fold"), relationshipType="PARENT_CHILD")
    await w.edge("nodeRelations", ("records", "fold"), ("records", "doc"), relationshipType="PARENT_CHILD")


async def test_a_fresh_store_has_no_legacy_knowledge_base_and_nothing_to_migrate(orgs, org_ctx) -> None:
    w = orgs()
    with org_ctx(w.org):
        await w.nodes("organizations", {"_key": w.org})
        assert await w.p.get_legacy_kb_record_groups(w.org) == []
        assert await w.p.count_legacy_kb_record_groups(w.org) == 0
        assert await w.p.migrate_legacy_kb_to_app({"_key": "kb1", "groupName": "KB"}, w.org, "u1") == {
            "success": False, "reason": "RecordGroup kb1 not found during migration"}
        assert await w.p.migrate_legacy_kb_to_app({"groupName": "KB"}, w.org, None) == {"success": False, "reason": "Legacy KB record group missing _key/id"}
        assert await w.p.migrate_agent_hub_knowledge(w.org) == {"agents_migrated": 0, "knowledge_nodes_created": 0}
        assert await w.p.delete_kb_hub_app(w.org) is True                 # no hub app: nothing to delete, as on Neo4j
        assert (await graph(w))[1] == []


async def test_the_methods_need_an_org(pg_provider) -> None:
    for call in (pg_provider.get_legacy_kb_record_groups("o"), pg_provider.count_legacy_kb_record_groups("o"), pg_provider.migrate_agent_hub_knowledge("o"),
                 pg_provider.migrate_legacy_kb_to_app({"_key": "kb1"}, "o", None)):
        with pytest.raises(MissingOrgContext):
            await call
    with system("test"), pytest.raises(Exception, match="system context"):
        await pg_provider.count_legacy_kb_record_groups("o")


async def test_the_legacy_knowledge_bases_are_the_org_s_kb_record_groups(orgs, org_ctx) -> None:
    w = orgs()
    with org_ctx(w.org):
        await legacy_world(w)
        got = await w.p.get_legacy_kb_record_groups(w.org)
        assert [g["_key"] for g in got] == ["kb1", "kb2"]
        assert got[0] | {"_id": "recordGroups/kb1", "id": "kb1"} == got[0] and got[0]["description"] == "Team docs" and got[0]["orgId"] == w.org
        assert await w.p.count_legacy_kb_record_groups(w.org) == 2
        assert await w.p.get_legacy_kb_record_groups(_org()) == []          # another org's id reads nothing
        assert await w.p.count_legacy_kb_record_groups(_org()) == 0


async def test_a_legacy_knowledge_base_becomes_its_own_app_with_every_edge_and_record(orgs, org_ctx, monkeypatch) -> None:
    w, other = orgs(), orgs()
    hub = f"knowledgeBase_{w.org}"
    with org_ctx(other.org):            # the same keys in another org
        await legacy_world(other)
        before_other = await graph(other)
    with org_ctx(w.org):
        await legacy_world(w)
        kb1, kb2 = await w.p.get_legacy_kb_record_groups(w.org)
        monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")          # what the migration writes is valid
        assert await w.p.migrate_legacy_kb_to_app(kb1, w.org, "u1") == {"success": True}

        assert await w.p.get_document("kb1", "recordGroups") is None
        app = await w.p.get_document("kb1", "apps")
        assert {k: v for k, v in app.items() if k not in ("_key", "_id")} == {
            "id": "kb1", "name": "KB1", "type": "KB", "appGroup": "Local Storage", "authType": "NONE", "scope": "personal", "isActive": True,
            "isAgentActive": True, "isConfigured": True, "isAuthenticated": True, "vectorMembershipBackfilled": False, "hideConnector": True,
            "createdBy": "u1", "orgId": w.org, "createdAtTimestamp": 10, "updatedAtTimestamp": 20, "description": "Team docs"}
        # every edge of the record group is on the app; its belongsTo to the hub app is gone
        assert await ends(w, "permission", ("recordGroups", "kb1")) == set()
        assert await ends(w, "permission", ("apps", "kb1")) == {("users/u1", "apps/kb1"), ("users/u2", "apps/kb1"), ("teams/t1", "apps/kb1")}
        assert {(r["from_id"], r["role"]) for r in await w.p.get_edges_to_node("apps/kb1", "permission")} == {("u1", "OWNER"), ("u2", "READER"), ("t1", "WRITER")}
        assert await ends(w, "belongsTo", ("apps", "kb1")) == {("records/fold", "apps/kb1"), ("records/doc", "apps/kb1")}
        assert await ends(w, "inheritPermissions", ("apps", "kb1")) == {("records/fold", "apps/kb1")}
        assert await ends(w, "nodeRelations", ("apps", "kb1")) == {("apps/kb1", "records/fold")}
        assert await ends(w, "belongsTo", ("recordGroups", "kb1")) == set()
        assert await ends(w, "belongsTo", ("apps", hub)) == {("recordGroups/kb2", f"apps/{hub}")}
        # the knowledge base's records name the app; another knowledge base's do not
        assert {d["_key"]: d["connectorId"] for d in await w.p.get_nodes_by_filters("records", {})} == {"fold": "kb1", "doc": "kb1", "other": hub}
        assert await ends(w, "orgAppRelation", ("apps", "kb1")) == {(f"organizations/{w.org}", "apps/kb1")}
        uar = await w.p.get_edge("u1", "users", "kb1", "apps", "userAppRelation")
        assert uar["syncState"] == "NOT_STARTED" and uar["lastSyncUpdate"] == uar["createdAtTimestamp"]
        # it is a knowledge base app now, with the roles it had
        assert await w.p.kb_exists("kb1")
        assert {k["id"]: k["userRole"] for k in (await w.p.list_user_knowledge_bases("u2", w.org, 0, 10))[0]} == {"kb1": "READER"}
        assert [g["_key"] for g in await w.p.get_legacy_kb_record_groups(w.org)] == ["kb2"]
        assert await w.p.count_legacy_kb_record_groups(w.org) == 1

        # a second run finds no record group and changes nothing
        snapshot = await graph(w)
        assert await w.p.migrate_legacy_kb_to_app(kb1, w.org, "u1") == {"success": False, "reason": "RecordGroup kb1 not found during migration"}
        assert await graph(w) == snapshot

        # a creator that did not resolve: the app keeps the stored createdBy and there is no userAppRelation for it
        assert await w.p.migrate_legacy_kb_to_app(kb2, w.org, None) == {"success": True}
        assert (await w.p.get_document("kb2", "apps"))["createdBy"] == "uid-unknown"
        assert await ends(w, "userAppRelation", ("apps", "kb2")) == set()
        assert await ends(w, "orgAppRelation", ("apps", "kb2")) == {(f"organizations/{w.org}", "apps/kb2")}
        assert await w.p.count_legacy_kb_record_groups(w.org) == 0
        assert await w.p.delete_kb_hub_app(w.org) is True                  # nothing points at the hub app any more
        assert await w.p.get_document(hub, "apps") is None
    with org_ctx(other.org):
        assert await graph(other) == before_other


async def test_a_record_group_of_another_org_or_an_existing_app_is_left_as_it_is(orgs, org_ctx) -> None:
    w = orgs()
    with org_ctx(w.org):
        await legacy_world(w)
        kb1 = (await w.p.get_legacy_kb_record_groups(w.org))[0]
        snapshot = await graph(w)
        assert (await w.p.migrate_legacy_kb_to_app(kb1, _org(), "u1"))["success"] is False      # the org argument names another org
        assert await graph(w) == snapshot
        await w.nodes("apps", {"_key": "kb1", "type": "KB", "name": "Taken"})
        snapshot = await graph(w)
        result = await w.p.migrate_legacy_kb_to_app(kb1, w.org, "u1")
        assert result["success"] is False and "already exists" in result["reason"]
        assert await graph(w) == snapshot


async def test_a_migration_that_fails_part_way_changes_nothing(orgs, org_ctx, monkeypatch) -> None:
    w = orgs()
    with org_ctx(w.org):
        await legacy_world(w)
        kb1 = (await w.p.get_legacy_kb_record_groups(w.org))[0]
        snapshot = await graph(w)

        def boom(*_a: object) -> str:
            raise RuntimeError("lost the connection")
        monkeypatch.setattr(legacy_migrations, "_ekey", boom)          # after the node, the edges and the records are written
        assert await w.p.migrate_legacy_kb_to_app(kb1, w.org, "u1") == {"success": False, "reason": "lost the connection"}
        assert await graph(w) == snapshot


async def test_kg_holds_the_app_and_its_records_after_the_migration(orgs, org_ctx, pg_db, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.setenv("PG_KG_FLUSH", "inline")
    monkeypatch.setenv("PG_KG_STRICT", "1")
    w = orgs()
    with org_ctx(w.org):
        await legacy_world(w)
        await w.p.batch_create_edges([{"from_collection": "recordGroups", "from_id": "kb2", "to_collection": "records", "to_id": "other",
                                       "relationshipType": "PARENT_CHILD"}], "nodeRelations")
    assert held_kind(await held(w.p, w.org), "kb1") == 2
    with org_ctx(w.org):
        for kb in await w.p.get_legacy_kb_record_groups(w.org):
            assert (await w.p.migrate_legacy_kb_to_app(kb, w.org, "u1"))["success"]
        assert await w.p.delete_kb_hub_app(w.org)
        log = await rows(w, "SELECT count(*) AS n FROM kg.graph_changes WHERE org_id = $1")
    assert log == [{"n": 0}]                                              # taken by the writer's own flush
    kg = await held(w.p, w.org)
    assert held_kind(kg, "kb1") == 1 and held_kind(kg, "kb2") == 1
    assert {r: kg["records"][r][1] for r in ("fold", "doc", "other")} == {"fold": "kb1", "doc": "kb1", "other": "kb2"}
    assert kg["records"]["fold"][3] == "kb1" and kg["records"]["doc"][3] == "fold"      # the primary parents
    assert {(p, n) for (p, n, _), (_, app, _) in kg["grants"].items() if app == "kb1"} >= {("u1", "kb1"), ("u2", "kb1"), ("t1", "kb1")}
    assert not await differences(w.p, w.org)


def held_kind(kg: dict, node: str) -> int:
    return kg["records"][node][0]


# ---- agent knowledge that names the hub app -----------------------------------------------------------------------------------------------

async def agent_world(w: World) -> None:
    hub = f"knowledgeBase_{w.org}"
    await w.nodes("apps", {"_key": hub, "type": "KB", "name": "Knowledge Base"}, *({"_key": k, "type": "KB", "name": k} for k in ("kb1", "kb2", "kb3")),
                  {"_key": "drive", "type": "DRIVE", "name": "Drive"})
    await w.nodes("agentInstances", {"_key": "all", "createdBy": "boss"}, {"_key": "scoped"}, {"_key": "has", "createdBy": "boss"},
                  {"_key": "twice", "createdBy": "boss"}, {"_key": "plain", "createdBy": "boss"})
    knowledge = {"k-all": (hub, "{}"), "k-scoped": (hub, json.dumps({"recordGroups": ["kb2", "gone"]})), "k-has": (hub, {"recordGroups": []}),
                 "k-has-kb1": ("kb1", "{}"), "k-twice-1": (hub, json.dumps({"recordGroups": ["kb1"]})), "k-twice-2": (hub, None),
                 "k-plain": ("drive", "{}")}
    await w.nodes("agentKnowledge", *({"_key": k, "connectorId": c, "createdBy": "boss", "createdAtTimestamp": 1, **({"filters": f} if f is not None else {})}
                                     for k, (c, f) in knowledge.items()))
    for k in knowledge:
        agent = k.split("-")[1]
        await w.edge("agentHasKnowledge", ("agentInstances", agent), ("agentKnowledge", k), createdAtTimestamp=1)


async def knowledge_of(w: World) -> dict[str, list[tuple]]:
    got = await rows(w, """SELECT e.from_id AS agent, k.id, k.doc FROM graph.edges e JOIN graph.nodes k ON k.org_id = e.org_id AND k.coll = 'agentKnowledge' AND k.id = e.to_id
        WHERE e.org_id = $1 AND e.coll = 'agentHasKnowledge' ORDER BY 1, k.doc->>'connectorId'""")
    out: dict[str, list[tuple]] = {}
    for r in got:
        d = json.loads(r["doc"]) if isinstance(r["doc"], str) else r["doc"]
        out.setdefault(r["agent"], []).append((d["connectorId"], d.get("filters"), d["createdBy"]) if not r["id"].startswith("k-") else r["id"])
    return out


async def test_agent_knowledge_is_expanded_to_one_source_per_knowledge_base(orgs, org_ctx, monkeypatch) -> None:
    w, other = orgs(), orgs()
    with org_ctx(other.org):
        await agent_world(other)
        before_other = await graph(other)
    with org_ctx(w.org):
        await w.nodes("apps", {"_key": "kb1", "type": "KB", "name": "kb1"})
        assert await w.p.migrate_agent_hub_knowledge(w.org) == {"agents_migrated": 0, "knowledge_nodes_created": 0}      # knowledge bases, no agents
        await agent_world(w)
        monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
        assert await w.p.migrate_agent_hub_knowledge(w.org) == {"agents_migrated": 5, "knowledge_nodes_created": 9}
        boss = ("{}", "boss")
        assert await knowledge_of(w) == {
            "all": [("kb1", *boss), ("kb2", *boss), ("kb3", *boss)],                    # no restriction: every current knowledge base
            "scoped": [("kb2", "{}", "system")],                                         # only those named that are apps now; no creator: "system"
            "has": ["k-has-kb1", ("kb2", *boss), ("kb3", *boss)],                        # an empty list is no restriction; one it has is not added again
            "twice": [("kb1", *boss), ("kb2", *boss), ("kb3", *boss)],                   # two hub sources add each knowledge base once
            "plain": ["k-plain"]}                                                        # a source that names another app stays
        assert await w.p.get_existing_keys("agentKnowledge", ["k-all", "k-scoped", "k-has", "k-twice-1", "k-twice-2"]) == set()
        assert await rows(w, "SELECT to_id FROM graph.edges WHERE org_id = $1 AND to_coll = 'agentKnowledge' AND to_id LIKE 'k-%' ORDER BY 1") == [
            {"to_id": "k-has-kb1"}, {"to_id": "k-plain"}]
        assert await w.p.migrate_agent_hub_knowledge(w.org) == {"agents_migrated": 0, "knowledge_nodes_created": 0}
        assert await w.p.migrate_agent_hub_knowledge(other.org) == {"agents_migrated": 0, "knowledge_nodes_created": 0}       # another org's id
    with org_ctx(other.org):
        assert await graph(other) == before_other


async def test_an_invalid_knowledge_source_is_refused_and_nothing_is_written(orgs, org_ctx, monkeypatch) -> None:
    w = orgs()
    with org_ctx(w.org):
        await agent_world(w)
        snapshot = await graph(w)
        monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "strict")
        monkeypatch.setattr(legacy_migrations, "get_epoch_timestamp_in_ms", lambda: "not a time")
        with pytest.raises(SchemaValidationError):
            await w.p.migrate_agent_hub_knowledge(w.org)
        assert await graph(w) == snapshot


async def test_a_failed_expansion_answers_zeros_with_the_error_and_changes_nothing(orgs, org_ctx, monkeypatch) -> None:
    w = orgs()
    with org_ctx(w.org):
        await agent_world(w)
        snapshot = await graph(w)

        def boom(*_a: object) -> str:
            raise RuntimeError("lost the connection")
        monkeypatch.setattr(legacy_migrations, "_j", boom)
        assert await w.p.migrate_agent_hub_knowledge(w.org) == {"agents_migrated": 0, "knowledge_nodes_created": 0, "error": "lost the connection"}
        assert await graph(w) == snapshot


# ---- the hierarchy edge under its pre-rename collection ------------------------------------------------------------------------------------

async def raw_edge(w: World, coll: str, frm: str, to: str, ekey: str, **doc: object) -> None:
    await w.p._q(lambda tx: tx.execute("""INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
        VALUES ($1, $2, 'records', $3, 'records', $4, $5, $6::jsonb)""", w.org, coll, frm, to, ekey, json.dumps(doc)), None, write=True)


async def legacy_edges(w: World) -> None:
    """Edges as a copy of an older store holds them: under the old collection, keyed as that collection keys them (one edge per pair)."""
    await raw_edge(w, LEGACY, "p", "c1", "", relationshipType="PARENT_CHILD", createdAtTimestamp=1)
    await raw_edge(w, LEGACY, "p", "c2", "", relationshipType="ATTACHMENT", createdAtTimestamp=1)
    await raw_edge(w, LEGACY, "a", "b", "", relationshipType="LINKED_TO", createdAtTimestamp=1)
    await raw_edge(w, LEGACY, "a", "b", "x", relationshipType="DEPENDS_ON", constraintName="fk1", createdAtTimestamp=1)
    await raw_edge(w, LEGACY, "p", "c3", "", relationshipType="PARENT_CHILD", createdAtTimestamp=1)
    await raw_edge(w, "nodeRelations", "p", "c3", "", relationshipType="PARENT_CHILD", createdAtTimestamp=99)      # already there under the new name


async def edges_of(w: World, coll: str) -> list[tuple]:
    return [(r["from_id"], r["to_id"], r["ekey"], json.loads(r["doc"])) for r in await rows(
        w, "SELECT from_id, to_id, ekey, doc::text FROM graph.edges WHERE org_id = $1 AND coll = $2 ORDER BY from_id, to_id, ekey", coll)]


async def rename(p) -> dict:
    return await p.migrate_legacy_relation_edge(legacy_collection=LEGACY, legacy_relationship_type=LEGACY_NEO4J_RELATIONSHIP_TYPE)


async def test_a_store_on_the_current_name_is_already_current(orgs, org_ctx) -> None:
    w = orgs()
    with org_ctx(w.org):
        await w.child(("records", "p"), ("records", "c"))
        before = await graph(w)
        assert await rename(w.p) == {"migrated": 0, "already_current": True}
        assert await graph(w) == before


async def test_the_legacy_hierarchy_edges_move_onto_the_current_name_once(orgs, org_ctx, monkeypatch) -> None:
    monkeypatch.setattr(legacy_migrations, "_MOVE_PAGE", 2)              # several pages, each its own transaction
    w, other = orgs(), orgs()
    for x in (w, other):
        with org_ctx(x.org):
            await legacy_edges(x)
    with org_ctx(w.org):
        assert await rename(w.p) == {"migrated": 5, "already_current": False}
        assert await edges_of(w, LEGACY) == []
        assert await edges_of(w, "nodeRelations") == [
            ("a", "b", "DEPENDS_ON\x1ffk1", {"relationshipType": "DEPENDS_ON", "constraintName": "fk1", "createdAtTimestamp": 1}),      # the key the collection gives a link
            ("a", "b", "LINKED_TO", {"relationshipType": "LINKED_TO", "createdAtTimestamp": 1}),
            ("p", "c1", "", {"relationshipType": "PARENT_CHILD", "createdAtTimestamp": 1}),
            ("p", "c2", "", {"relationshipType": "ATTACHMENT", "createdAtTimestamp": 1}),
            ("p", "c3", "", {"relationshipType": "PARENT_CHILD", "createdAtTimestamp": 99})]                                        # not duplicated; kept as it was
        assert (await w.p.get_edge("p", "records", "c1", "records", "nodeRelations"))["relationshipType"] == "PARENT_CHILD"
        assert {e["_to"] for e in await w.p.get_edges_from_node("records/a", "recordLinks")} == {"records/b"}
        assert await rename(w.p) == {"migrated": 0, "already_current": True}
    with org_ctx(other.org):
        assert len(await edges_of(other, LEGACY)) == 5                   # an org run moves only its own org's edges


async def test_a_system_run_moves_every_org_s_legacy_edges(orgs, org_ctx) -> None:
    a, b = orgs(), orgs()
    for x in (a, b):
        with org_ctx(x.org):
            await x.nodes("organizations", {"_key": x.org, "isActive": True})
            await legacy_edges(x)
    with system("node_relation_migration"):
        assert await rename(a.p) == {"migrated": 10, "already_current": False}
        assert await rename(a.p) == {"migrated": 0, "already_current": True}
    for x in (a, b):
        with org_ctx(x.org):
            assert await edges_of(x, LEGACY) == [] and len(await edges_of(x, "nodeRelations")) == 5


async def test_the_rename_needs_a_context_and_a_legacy_name(pg_provider, orgs, org_ctx) -> None:
    with pytest.raises(MissingOrgContext):
        await rename(pg_provider)
    w = orgs()
    with org_ctx(w.org):
        await w.child(("records", "p"), ("records", "c"))
        for name in ("nodeRelations", ""):
            with pytest.raises(ValueError, match="Not a legacy edge collection"):
                await pg_provider.migrate_legacy_relation_edge(legacy_collection=name, legacy_relationship_type=LEGACY_NEO4J_RELATIONSHIP_TYPE)
        assert len(await edges_of(w, "nodeRelations")) == 1


async def test_kg_holds_the_hierarchy_once_it_is_on_the_current_name(orgs, org_ctx, monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "1")
    monkeypatch.setenv("PG_KG_FLUSH", "inline")
    monkeypatch.setenv("PG_KG_STRICT", "1")
    w = orgs()
    with org_ctx(w.org):
        await w.nodes("apps", {"_key": "drive", "type": "DRIVE", "name": "Drive"})
        await w.nodes("records", *({"_key": k, "recordName": k, "connectorId": "drive", "recordType": "FILE", "origin": "CONNECTOR"} for k in ("p", "c1", "c2")))
        await raw_edge(w, LEGACY, "p", "c1", "", relationshipType="PARENT_CHILD")
        await raw_edge(w, LEGACY, "p", "c2", "", relationshipType="ATTACHMENT")
    assert held_kind(await held(w.p, w.org), "c1") == 3 and (await held(w.p, w.org))["records"]["c1"][3] is None
    with org_ctx(w.org):
        assert await rename(w.p) == {"migrated": 2, "already_current": False}
    kg = await held(w.p, w.org)
    assert kg["records"]["c1"][3] == "p" and kg["records"]["c2"][3] == "p"
    assert not await differences(w.p, w.org)


# ---- the start-up migrations, called as start-up calls them ------------------------------------------------------------------------------

class _Flags:
    """The configuration store's migration flags, in memory."""

    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get_config(self, key: str, default: object = None, use_cache: bool = True) -> object:
        return self.values.get(key, default)

    async def set_config(self, key: str, value: object) -> bool:
        self.values[key] = value
        return True


async def test_the_start_up_migrations_run_with_no_org_bound(orgs, org_ctx, pg_provider) -> None:
    """Start-up binds no org: each migration binds its own scope (every org for the edge rename and the org listing, one org for its knowledge
    bases), so on PostgreSQL the fatal edge rename and the knowledge base migration both complete."""
    import logging
    w = orgs()
    with org_ctx(w.org):
        await legacy_world(w)
        await legacy_edges(w)
    log, flags = logging.getLogger("startup-migrations"), _Flags()
    renamed = await run_node_relation_migration(graph_provider=pg_provider, config_service=flags, logger=log)
    assert renamed["success"] is True and renamed["migrated"] >= 5, renamed
    assert flags.values["/migrations/node_relation_v1"]["done"] is True
    migrated = await run_kb_apps_migration(graph_provider=pg_provider, config_service=flags, logger=log)
    assert migrated["kbs_migrated"] >= 2 and "error" not in migrated, migrated
    with org_ctx(w.org):
        assert await edges_of(w, LEGACY) == []
        assert await w.p.count_legacy_kb_record_groups(w.org) == 0
        assert {r["id"] for r in await rows(w, "SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'apps'")} == {"kb1", "kb2"}   # the hub app went too

