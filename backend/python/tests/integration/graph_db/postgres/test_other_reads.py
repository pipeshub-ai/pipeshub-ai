"""The "All" team, what an agent still uses, name and count checks, edge checks and updates, record descent, keys by external id, failed records, and the
clean-up of a connector's sync, on PostgreSQL against a real database, with the rules of ``Neo4jProvider``.

Both ways: nothing reaches a user who was not given it (another org, another app, a removed grant, a second owner of one team) and nothing a user was given
goes missing (a member of the "All" team, a role that was raised). A read that fails answers its default and answers right once the store is back.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.org_context import OrgMismatch

from .test_access import APP, World, _org, rec
from .test_access import w as w
from .test_grants import failing, grant, in_org
from .test_kg_capture import log as capture_log

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def log_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the projection on, a unit leaves the rows it logged (the capture test below reads them)."""
    monkeypatch.setenv("PG_KG_PROJECT", "1")


# ---- the "All" team --------------------------------------------------------------------------------------------------------------------

async def org_user(w: World, key: str, **doc: object) -> None:
    await w.nodes("users", {"_key": key, **doc})
    await w.edge("belongsTo", ("users", key), ("organizations", w.org), entityType="ORGANIZATION")


async def members(w: World) -> dict[str, str]:
    return {e["from_id"]: e["role"] for e in await w.p.get_edges_to_node(f"teams/all_{w.org}", "permission")}


async def test_the_first_member_of_the_all_team_owns_it_and_the_next_only_read(w: World) -> None:
    await w.nodes("users", {"_key": "a"}, {"_key": "b"})
    p = w.p
    await p.add_user_to_all_team(w.org, "a")
    await p.add_user_to_all_team(w.org, "b")
    await p.add_user_to_all_team(w.org, "a")                                    # again: nothing changes
    assert await members(w) == {"a": "OWNER", "b": "READER"}
    team = await p.get_document(f"all_{w.org}", "teams")
    assert team["name"] == "All" and team["createdBy"] == "a" and team["orgId"] == w.org
    await p.add_user_to_all_team(w.org, "ghost")                                # a user who is not in the graph is not added, and does not take ownership
    assert set(await members(w)) == {"a", "b"}
    with pytest.raises(OrgMismatch):
        await p.add_user_to_all_team(_org(), "a")


async def test_a_failed_lookup_of_the_user_raises_instead_of_leaving_them_out_and_they_join_on_retry(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("users", {"_key": "a"})
    real = w.p._id_rows

    async def broken(sql: str, *a: object, **k: object) -> list:
        if "all_team_user_exists" in sql:
            raise RuntimeError("store down")
        return await real(sql, *a, **k)
    monkeypatch.setattr(w.p, "_id_rows", broken)
    with pytest.raises(RuntimeError):
        await w.p.add_user_to_all_team(w.org, "a")
    assert await w.p.get_document(f"all_{w.org}", "teams") is None and await members(w) == {}          # nothing was written
    monkeypatch.undo()
    await w.p.add_user_to_all_team(w.org, "a")
    assert await members(w) == {"a": "OWNER"}
    await w.p.add_user_to_all_team(w.org, "ghost")                                                      # a user who is not in the graph still adds nobody
    assert await members(w) == {"a": "OWNER"}


async def test_users_who_join_together_do_not_make_two_owners(w: World) -> None:
    keys = [f"u{i}" for i in range(8)]
    await w.nodes("users", *({"_key": k} for k in keys))
    await asyncio.gather(*(w.p.add_user_to_all_team(w.org, k) for k in keys))
    roles = await members(w)
    assert sorted(roles) == sorted(keys) and list(roles.values()).count("OWNER") == 1
    assert (await w.p.get_document(f"all_{w.org}", "teams"))["createdBy"] in {k for k, r in roles.items() if r == "OWNER"}


async def test_users_who_join_an_all_team_that_exists_and_is_empty_do_not_make_two_owners(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [f"u{i}" for i in range(8)]
    real = w.p.check_collection_has_document

    async def slow(*a: object, **k: object) -> bool:        # every joiner reaches the read of the team's members together: only the lock keeps them apart
        await asyncio.sleep(0.3)
        return await real(*a, **k)
    monkeypatch.setattr(w.p, "check_collection_has_document", slow)
    real_edges = w.p.batch_create_edges

    async def slow_edges(*a: object, **k: object) -> bool:      # and none commits before all have read
        await asyncio.sleep(0.3)
        return await real_edges(*a, **k)
    monkeypatch.setattr(w.p, "batch_create_edges", slow_edges)
    await w.nodes("users", *({"_key": k} for k in keys))
    await w.p.add_user_to_all_team(w.org, "ghost")                              # makes the team and adds nobody
    assert await w.p.get_document(f"all_{w.org}", "teams") is not None and await members(w) == {}
    await asyncio.gather(*(w.p.add_user_to_all_team(w.org, k) for k in keys))
    roles = await members(w)
    assert sorted(roles) == sorted(keys) and list(roles.values()).count("OWNER") == 1


async def test_every_active_user_joins_the_all_team_the_oldest_owning_it_and_what_the_team_is_given_reaches_them(w: World) -> None:
    await w.nodes("organizations", {"_key": w.org})
    for k, ts, active in (("old", 1, True), ("new", 9, True), ("mid", 5, True), ("off", 2, False)):
        await org_user(w, k, createdAtTimestamp=ts, isActive=active)
    await w.nodes("apps", {"_key": "kb", "type": "KB"})
    await w.edge("permission", ("teams", f"all_{w.org}"), ("apps", "kb"), type="TEAM", role="WRITER")
    p = w.p
    assert await p.get_user_kb_permission("kb", "new") is None                   # before: not a member
    await p.ensure_all_team_with_users(w.org)
    await p.ensure_all_team_with_users(w.org)
    assert await members(w) == {"old": "OWNER", "mid": "READER", "new": "READER"}      # the inactive one is not in it
    assert await p.get_user_kb_permission("kb", "new") == "WRITER"               # after: the team's grant reaches them (and a team owner never owns the collection)
    assert await p.get_user_kb_permission("kb", "old") == "WRITER"
    assert await p.get_user_kb_permission("kb", "off") is None
    await org_user(w, "later", createdAtTimestamp=0, isActive=True)        # a newcomer older than everybody does not take the team over
    await p.ensure_all_team_with_users(w.org)
    assert (await members(w))["later"] == "READER" and (await members(w))["old"] == "OWNER"
    other = _org()
    with in_org(other):
        ow = World(p, other)
        await ow.nodes("organizations", {"_key": other})
        await ow.nodes("users", {"_key": "elsewhere", "isActive": True})
        await ow.edge("belongsTo", ("users", "elsewhere"), ("organizations", other), entityType="ORGANIZATION")
    assert "elsewhere" not in await members(w)
    with pytest.raises(OrgMismatch):
        await p.ensure_all_team_with_users(other)


async def test_a_failed_all_team_fill_changes_nothing_and_goes_through_when_retried(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("organizations", {"_key": w.org})
    await org_user(w, "a", createdAtTimestamp=1, isActive=True)
    await org_user(w, "b", createdAtTimestamp=2, isActive=True)
    real = w.p.batch_create_edges

    async def flaky(*a: object, **k: object) -> bool:
        raise RuntimeError("store down")
    monkeypatch.setattr(w.p, "batch_create_edges", flaky)
    with pytest.raises(RuntimeError):
        await w.p.ensure_all_team_with_users(w.org)
    assert await w.p.get_document(f"all_{w.org}", "teams") is None and await members(w) == {}          # the team made in the same unit went with it
    monkeypatch.setattr(w.p, "batch_create_edges", real)
    await w.p.ensure_all_team_with_users(w.org)
    assert await members(w) == {"a": "OWNER", "b": "READER"}


# ---- what an agent still uses ----------------------------------------------------------------------------------------------------------

async def test_an_agent_blocks_a_toolset_an_mcp_server_or_an_api_toolset_only_while_it_is_live_and_attached(w: World) -> None:
    await w.nodes("agentInstances", {"_key": "a1", "name": "Same", "apiToolsetTools": ["inst1:op1", "inst1:op2"]}, {"_key": "a2", "name": "Same"},
                  {"_key": "gone", "name": "Gone", "isDeleted": True, "apiToolsetTools": ["inst1:op1"]}, {"_key": "a3", "name": "Other", "apiToolsetTools": ["inst10:op1"]},
                  {"_key": "odd", "name": "Odd", "apiToolsetTools": "inst1:op1"})
    await w.nodes("agentToolsets", {"_key": "ts1", "instanceId": "inst1"}, {"_key": "ts2", "instanceId": "inst2"})
    await w.nodes("agentMcpServers", {"_key": "mc1", "instanceId": "mcp1"})
    for a in ("a1", "a2", "gone"):
        await w.edge("agentHasToolset", ("agentInstances", a), ("agentToolsets", "ts1"))
        await w.edge("agentHasMcpServer", ("agentInstances", a), ("agentMcpServers", "mc1"))
    await w.edge("agentHasToolset", ("agentInstances", "a3"), ("agentToolsets", "ts2"))
    p = w.p
    assert await p.check_toolset_instance_in_use("inst1") == ["Same", "Same"]          # two agents of one name count apart
    assert await p.check_mcp_instance_in_use("mcp1") == ["Same", "Same"]
    assert await p.check_toolset_instance_in_use("inst2") == ["Other"] and await p.check_toolset_instance_in_use("nope") == []
    assert await p.check_mcp_instance_in_use("nope") == []
    assert await p.check_api_toolset_in_use("inst1") == ["Same"]                       # the prefix is the whole instance id: inst10 is another
    assert await p.check_api_toolset_in_use("inst10") == ["Other"] and await p.check_api_toolset_in_use("inst") == []
    await p.delete_edge("a1", "agentInstances", "ts1", "agentToolsets", "agentHasToolset")
    assert await p.check_toolset_instance_in_use("inst1") == ["Same"]                  # detached: no longer blocking
    other = _org()
    with in_org(other):
        assert await p.check_toolset_instance_in_use("inst1") == [] and await p.check_mcp_instance_in_use("mcp1") == [] and await p.check_api_toolset_in_use("inst1") == []


# ---- names and counts of connector instances ---------------------------------------------------------------------------------------------

async def test_a_connector_name_is_unique_within_its_scope_and_a_failed_check_lets_it_through(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("organizations", {"_key": w.org})
    await w.nodes("apps", {"_key": "t1", "name": "  Sales Slack ", "scope": "team", "type": "SLACK"}, {"_key": "p1", "name": "Mine", "scope": "personal", "createdBy": "uid-me", "type": "SLACK"})
    await w.edge("orgAppRelation", ("organizations", w.org), ("apps", "t1"))
    p = w.p
    assert not await p.check_connector_name_uniqueness("sales slack", "team", w.org, "uid-me", "apps", "orgAppRelation")
    assert await p.check_connector_name_uniqueness("Sales", "team", w.org, "uid-me", "apps", "orgAppRelation")
    assert not await p.check_connector_name_uniqueness(" MINE", "personal", w.org, "uid-me", "apps")
    assert await p.check_connector_name_uniqueness("Mine", "personal", w.org, "uid-other", "apps")          # another user's personal name is theirs
    assert await p.check_connector_name_uniqueness("Mine", "team", w.org, "uid-me", "apps", "orgAppRelation")        # a personal instance is no team name
    assert await p.check_connector_name_uniqueness("Sales Slack", "team", _org(), "uid-me", "apps", "orgAppRelation")        # another org's team names are not this org's
    async def broken(*a: object, **k: object) -> bool:
        raise RuntimeError("store down")
    monkeypatch.setattr(p, "check_connector_name_exists", broken)
    assert await p.check_connector_name_uniqueness("Sales Slack", "team", w.org, "uid-me", "apps") is True
    monkeypatch.undo()
    assert not await p.check_connector_name_uniqueness("Sales Slack", "team", w.org, "uid-me", "apps")


async def test_instances_are_counted_by_scope_configured_only_and_a_failed_count_is_zero(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("apps", {"_key": "c1", "scope": "team", "isConfigured": True}, {"_key": "c2", "scope": "team", "isConfigured": True}, {"_key": "c3", "scope": "team", "isConfigured": False},
                  {"_key": "p1", "scope": "personal", "isConfigured": True, "createdBy": "me"}, {"_key": "p2", "scope": "personal", "isConfigured": True, "createdBy": "you"})
    p = w.p
    assert await p.count_connector_instances_by_scope("apps", "team") == 2 and await p.count_connector_instances_by_scope("apps", "team", "me", is_admin=True) == 2
    assert await p.count_connector_instances_by_scope("apps", "personal") == 2 and await p.count_connector_instances_by_scope("apps", "personal", "me") == 1
    assert await p.count_connector_instances_by_scope("apps", "none") == 0
    with in_org(_org()):
        assert await p.count_connector_instances_by_scope("apps", "team") == 0
    real = p._q
    monkeypatch.setattr(p, "_q", failing)
    assert await p.count_connector_instances_by_scope("apps", "team") == 0
    monkeypatch.setattr(p, "_q", real)
    assert await p.count_connector_instances_by_scope("apps", "team") == 2


# ---- documents, edges, descent -------------------------------------------------------------------------------------------------------------

async def test_a_document_and_an_edge_are_found_only_where_they_are_and_a_failed_check_says_no(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("things", {"_key": "x"}, {"_key": "y"})
    await w.edge("belongsTo", ("things", "x"), ("things", "y"))
    p = w.p
    assert await p.check_collection_has_document("things", "x") and not await p.check_collection_has_document("things", "nope") and not await p.check_collection_has_document("other", "x")
    assert await p.check_edge_exists("x", "y", "belongsTo") and not await p.check_edge_exists("y", "x", "belongsTo") and not await p.check_edge_exists("x", "y", "permission")
    with in_org(_org()):
        assert not await p.check_collection_has_document("things", "x") and not await p.check_edge_exists("x", "y", "belongsTo")
    real = p._q
    monkeypatch.setattr(p, "_q", failing)
    assert not await p.check_collection_has_document("things", "x") and not await p.check_edge_exists("x", "y", "belongsTo")
    monkeypatch.setattr(p, "_q", real)
    assert await p.check_collection_has_document("things", "x") and await p.check_edge_exists("x", "y", "belongsTo")


async def test_updating_an_edge_merges_removes_and_changes_what_the_grant_gives_both_ways(w: World) -> None:
    await w.nodes("users", {"_key": "m"}, {"_key": "by"})
    await w.nodes("teams", {"_key": "tm"})
    await w.nodes("apps", {"_key": "kb", "type": "KB"})
    await w.edge("permission", ("users", "m"), ("teams", "tm"), type="USER", role="READER", note="n")
    await w.edge("permission", ("users", "by"), ("teams", "tm"), type="USER", role="READER")
    await w.edge("permission", ("teams", "tm"), ("apps", "kb"), type="TEAM")                  # a grant without a role: the member's team role applies
    p = w.p
    assert await p.get_user_kb_permission("kb", "m") == "READER"
    assert await p.update_edge("users/m", "teams/tm", {"role": "WRITER", "note": None, "extra": 1}, "permission")
    e = await p.get_edge("m", "users", "tm", "teams", "permission")
    assert e["role"] == "WRITER" and e["extra"] == 1 and "note" not in e and e["type"] == "USER"
    assert await p.get_user_kb_permission("kb", "m") == "WRITER"                              # raised: the member gets it
    assert await p.get_user_kb_permission("kb", "by") == "READER"                              # and nobody else's edge moved
    await p.update_edge("users/m", "teams/tm", {"role": "READER"}, "permission")
    assert await p.get_user_kb_permission("kb", "m") == "READER"                              # lowered: it goes again
    await p.update_edge("users/m", "teams/tm", {"type": "GROUP"}, "permission")             # a changed type is the edge's key: it moves with it
    assert (await p.get_edge("m", "users", "tm", "teams", "permission"))["type"] == "GROUP" and await p.get_user_kb_permission("kb", "m") is None
    keys = await p._q(lambda tx: tx.fetch("SELECT ekey FROM graph.edges WHERE org_id = $1 AND coll = 'permission' AND from_id = 'm'", w.org), None)
    assert [r["ekey"] for r in keys] == ["GROUP"]                                              # the edge's key follows its type
    await w.edge("permission", ("users", "m"), ("teams", "tm"), type="USER", role="READER")        # a grant written later replaces it, one grant per pair
    assert [e["type"] for e in await p.get_edges_from_node("users/m", "permission")] == ["USER"]
    await p.delete_edge("m", "users", "tm", "teams", "permission")
    assert await p.get_user_kb_permission("kb", "m") is None
    assert not await p.update_edge("users/m", "teams/none", {"role": "OWNER"}, "permission")
    assert not await p.update_edge("users/by", "teams/tm", {"role": "OWNER"}, "belongsTo")      # another collection
    with pytest.raises(ValueError, match="collection"):
        await p.update_edge("m", "tm", {"role": "OWNER"}, "permission")
    await w.nodes("users", {"_key": "s"})
    await w.edge("authenticatedAs", ("users", "m"), ("users", "s"), connectorId="c1", note="1")
    await w.edge("authenticatedAs", ("users", "m"), ("users", "s"), connectorId="c2", note="2")
    with pytest.raises(ValueError, match="name one of them"):                                          # two parallel edges would get one key
        await p.update_edge("users/m", "users/s", {"connectorId": "c9"}, "authenticatedAs")
    assert sorted(e["connectorId"] for e in await p.get_edges_from_node("users/m", "authenticatedAs")) == ["c1", "c2"]
    assert await p.update_edge("users/m", "users/s", {"note": "x"}, "authenticatedAs")                 # a field that is not part of the key reaches both
    assert [e["note"] for e in await p.get_edges_from_node("users/m", "authenticatedAs")] == ["x", "x"]
    with in_org(_org()):
        assert not await p.update_edge("users/m", "teams/tm", {"role": "OWNER"}, "permission")
    assert not await p.get_edges_from_node("users/m", "permission")


async def test_updating_a_grant_is_logged_for_the_projection_like_any_other_grant_write_and_a_no_change_is_not(w: World) -> None:
    await w.nodes("users", {"_key": "m"})
    await w.nodes("records", rec("r"))
    await w.child(("apps", APP), ("records", "r"))
    await grant(w, "m", "records", "r", "READER")
    p = w.p

    async def wipe() -> None:
        async def go(tx) -> None:
            await tx.execute("/* pipeshub:test.clear_log */ SELECT kg.w_changes_delete(ARRAY(SELECT id FROM kg.graph_changes WHERE org_id = $1))", w.org)
        await p._q(go, None, write=True)
    await wipe()
    await p.update_edge("users/m", "records/r", {"role": "READER"}, "permission")             # nothing the projection reads changed
    assert await capture_log(p, w.org) == []
    await p.update_edge("users/m", "records/r", {"role": "WRITER"}, "permission")
    assert await capture_log(p, w.org) != []                                                   # the same capture as batch_create_edges leaves
    await wipe()
    await p.batch_create_edges([{"from_id": "m", "from_collection": "users", "to_id": "r", "to_collection": "records", "type": "USER", "role": "OWNER"}], "permission")
    assert await capture_log(p, w.org) != []


async def test_a_record_is_below_an_ancestor_only_over_parent_child_edges_from_it(w: World) -> None:
    await w.nodes("records", rec("a"), rec("b"), rec("c"), rec("att"), rec("lone"))
    await w.edge("nodeRelations", ("records", "a"), ("records", "b"), relationshipType="PARENT_CHILD")
    await w.edge("nodeRelations", ("records", "b"), ("records", "c"), relationshipType="PARENT_CHILD")
    await w.edge("nodeRelations", ("records", "c"), ("records", "att"), relationshipType="ATTACHMENT")
    p = w.p
    assert await p.is_record_descendant_of("c", "a") and await p.is_record_descendant_of("b", "a")
    assert not await p.is_record_descendant_of("a", "c")                                  # not the other way
    assert not await p.is_record_descendant_of("att", "a") and not await p.is_record_descendant_of("att", "c")      # an attachment is not a child
    assert not await p.is_record_descendant_of("a", "a") and not await p.is_record_descendant_of("lone", "a") and not await p.is_record_descendant_of("c", "missing")
    await w.nodes("records", rec("m1"), rec("m2"), rec("m3"))
    await w.edge("nodeRelations", ("records", "m1"), ("records", "m2"), relationshipType="ATTACHMENT")
    await w.edge("nodeRelations", ("records", "m2"), ("records", "m3"), relationshipType="PARENT_CHILD")
    assert await p.is_record_descendant_of("m3", "m2") and not await p.is_record_descendant_of("m3", "m1")        # every hop must be a child hop
    await w.edge("nodeRelations", ("records", "c"), ("records", "a"), relationshipType="PARENT_CHILD")      # a cycle ends the walk
    assert await p.is_record_descendant_of("c", "a") and not await p.is_record_descendant_of("c", "lone")
    with in_org(_org()):
        assert not await p.is_record_descendant_of("c", "a")


# ---- keys and lists of records ---------------------------------------------------------------------------------------------------------------

async def test_records_are_found_by_external_id_with_or_without_a_connector_and_never_across_orgs(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("records", rec("r1", externalRecordId="ext1"), {"_key": "r2", "connectorId": "other-app", "externalRecordId": "ext1"}, rec("r3", externalRecordId="ext3"))
    p = w.p
    assert await p.get_record_key_by_external_id("ext1", APP) == "r1" and await p.get_record_key_by_external_id("ext1", "other-app") == "r2"
    assert await p.get_record_key_by_external_id("ext1", "no-app") is None and await p.get_record_key_by_external_id("zzz", APP) is None
    assert await p.get_key_by_external_file_id("ext3") == "r3" and await p.get_key_by_external_message_id("ext3") == "r3"
    assert await p.get_key_by_external_file_id("ext1") == "r1"                              # two connectors: the smallest key, stably
    other = _org()
    with in_org(other):
        assert await p.get_key_by_external_file_id("ext3") is None
    real = p._q
    monkeypatch.setattr(p, "_q", failing)
    assert await p.get_key_by_external_file_id("ext3") is None and await p.get_record_key_by_external_id("ext1", APP) is None
    monkeypatch.setattr(p, "_q", real)
    assert await p.get_key_by_external_message_id("ext3") == "r3"


async def test_records_with_a_group_or_a_parent_are_linked_and_the_rest_are_not(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("ingroup"), rec("child"), rec("att"), rec("lone"), rec("ghostgroup"), rec("related"))
    await w.edge("belongsTo", ("records", "ingroup"), ("recordGroups", "g"))
    await w.edge("belongsTo", ("records", "ghostgroup"), ("recordGroups", "nope"))              # a group that is not there is no group
    await w.edge("nodeRelations", ("records", "lone"), ("records", "child"), relationshipType="PARENT_CHILD")
    await w.edge("nodeRelations", ("records", "lone"), ("records", "att"), relationshipType="ATTACHMENT")
    await w.edge("nodeRelations", ("records", "lone"), ("records", "related"), relationshipType="RELATED")
    p = w.p
    got = await p.filter_linked_record_ids(["lone", "att", "ingroup", "child", "ghostgroup", "related", "missing", "child"])
    assert got == ["att", "ingroup", "child"]                                                    # in the order asked, once each
    assert await p.filter_linked_record_ids([]) == []
    with in_org(_org()):
        assert await p.filter_linked_record_ids(["child"]) == []


async def test_failed_records_are_the_live_ones_of_the_connector_in_this_org_and_documents_come_by_status(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("records", rec("f1", indexingStatus="FAILED"), rec("f2", indexingStatus="FAILED", isDeleted=True), rec("ok", indexingStatus="COMPLETED"),
                  {"_key": "f3", "connectorId": "other-app", "indexingStatus": "FAILED"})
    p = w.p
    assert [d["_key"] for d in await p.get_failed_records_by_org(w.org, APP)] == ["f1"]
    assert await p.get_failed_records_by_org(_org(), APP) == [] and await p.get_failed_records_by_org(w.org, "none") == []
    assert [d["_key"] for d in await p.get_documents_by_status("records", "FAILED")] == ["f1", "f2", "f3"]       # every one of the collection, trashed or not
    assert await p.get_documents_by_status("records", "NOPE") == [] and await p.get_documents_by_status("files", "FAILED") == []
    with in_org(_org()):
        assert await p.get_documents_by_status("records", "FAILED") == []
    real = p._q
    monkeypatch.setattr(p, "_q", failing)
    assert await p.get_failed_records_by_org(w.org, APP) == [] and await p.get_documents_by_status("records", "FAILED") == []
    monkeypatch.setattr(p, "_q", real)
    assert len(await p.get_failed_records_by_org(w.org, APP)) == 1


async def test_departments_are_those_without_an_org_or_with_this_one(w: World) -> None:
    await w.nodes("departments", {"_key": "d1", "departmentName": "Legal"}, {"_key": "d2", "departmentName": "Sales", "orgId": w.org}, {"_key": "d3"})
    # World.nodes stamps the org on every document: the shared department is one stored with no org
    async def strip(tx) -> None:
        await tx.execute("UPDATE graph.nodes SET doc = doc - 'orgId' WHERE org_id = $1 AND coll = 'departments' AND id = 'd1'", w.org)
    await w.p._q(strip, None, write=True)
    p = w.p
    assert sorted(await p.get_departments(w.org)) == ["Legal", "Sales"] and sorted(await p.get_departments()) == ["Legal", "Sales"]
    assert await p.get_departments(_org()) == []                                                   # another org's id reads as none


# ---- a connector's sync -------------------------------------------------------------------------------------------------------------------

async def test_a_connectors_sync_points_and_sync_edges_go_and_nothing_else_does(w: World) -> None:
    await w.nodes("users", {"_key": "me"})
    await w.nodes("records", rec("r1"), rec("r2"), {"_key": "x1", "connectorId": "other-app"})
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("files", {"_key": "r1"})
    await w.nodes("syncPoints", {"_key": "s1", "connectorId": APP, "syncPointKey": "k1"}, {"_key": "s2", "connectorId": "other-app", "syncPointKey": "k2"})
    await w.edge("nodeRelations", ("recordGroups", "g"), ("records", "r1"), relationshipType="PARENT_CHILD")
    await w.edge("belongsTo", ("records", "r2"), ("recordGroups", "g"))
    await w.edge("permission", ("users", "me"), ("records", "r1"), type="USER", role="READER")
    await w.edge("permission", ("users", "me"), ("records", "x1"), type="USER", role="READER")
    await w.edge("isOfType", ("records", "r1"), ("files", "r1"))
    p = w.p
    assert await p.delete_sync_points_by_connector_id(APP) == (1, True)
    assert await p.get_sync_point("k1", "syncPoints") is None and await p.get_sync_point("k2", "syncPoints") is not None
    assert await p.delete_sync_points_by_connector_id(APP) == (0, True)
    n, ok = await p.delete_connector_sync_edges(APP)
    assert ok and n == 4                                                                       # the three around its records and group, and the grant on the app itself
    assert await p.get_document("r1", "records") is not None and await p.get_document("g", "recordGroups") is not None              # the nodes stay
    assert await p.get_edges_from_node("records/r1", "isOfType") and await p.get_edges_from_node("users/me", "permission")          # type links and other connectors' edges stay
    assert [e["to_id"] for e in await p.get_edges_from_node("users/me", "permission")] == ["x1"]
    assert await p.delete_connector_sync_edges("no-such-app") == (0, True)
    with in_org(_org()):
        assert await p.delete_connector_sync_edges(APP) == (0, True) and await p.delete_sync_points_by_connector_id(APP) == (0, True)
    assert await p.get_document("r1", "records") is not None


async def test_a_failed_sync_clean_up_reports_it_and_leaves_what_it_could_not_remove(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("syncPoints", {"_key": "s1", "connectorId": APP, "syncPointKey": "k1"})
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.delete_sync_points_by_connector_id(APP) == (0, False)
    assert await w.p.delete_connector_sync_edges(APP) == (0, False)
    monkeypatch.setattr(w.p, "_q", real)
    assert await w.p.get_sync_point("k1", "syncPoints") is not None and await w.p.delete_sync_points_by_connector_id(APP) == (1, True)


async def test_an_external_collaborator_keeps_the_app_only_while_a_grant_holds_it(w: World) -> None:
    await w.nodes("users", {"_key": "ext", "userId": "uid-ext"}, {"_key": "ext2"}, {"_key": "member"}, {"_key": "idle_member"}, {"_key": "ext3"}, {"_key": "ext4"}, {"_key": "ext5"}, {"_key": "ext6"})
    await w.nodes("groups", {"_key": "g4", "connectorId": APP}, {"_key": "g5", "connectorId": APP}, {"_key": "g6", "connectorId": APP})
    await w.nodes("person", {"_key": "pers", "email": "p@x.io"}, {"_key": "pers_kept", "email": "k@x.io"}, {"_key": "pers_busy", "email": "b@x.io"})
    await w.nodes("groups", {"_key": "grp", "connectorId": APP})
    await w.nodes("records", rec("shared"), rec("trashed", isDeleted=True), rec("elsewhere", connectorId="other-app"), rec("viaGroup"))
    await w.nodes("recordGroups", {"_key": "rg", "connectorId": APP})
    flag = {"isExternalUser": True}
    for who, coll in (("ext", "users"), ("ext2", "users"), ("ext3", "users"), ("ext4", "users"), ("ext5", "users"), ("ext6", "users"), ("pers", "person"), ("pers_kept", "person"),
                      ("pers_busy", "person")):
        await w.edge("userAppRelation", (coll, who), ("apps", APP), **flag)
    await w.edge("userAppRelation", ("users", "member"), ("apps", APP), isExternalUser=False)         # a real member is never at risk
    await w.edge("userAppRelation", ("users", "idle_member"), ("apps", APP), isExternalUser=False)   # not even one who holds no grant
    await w.edge("permission", ("users", "ext"), ("records", "shared"), type="USER", role="READER")      # a direct grant on a live record of the connector
    await w.edge("permission", ("users", "ext2"), ("records", "trashed"), type="USER", role="READER")    # on a trashed one: not a grant
    await w.edge("permission", ("person", "pers_busy"), ("records", "elsewhere"), type="USER", role="READER")      # on another connector's: not a grant here
    await w.edge("permission", ("person", "pers_kept"), ("groups", "grp"), type="USER", role="READER")           # through a group that holds one
    await w.edge("permission", ("groups", "grp"), ("records", "viaGroup"), type="GROUP", role="READER")
    await w.edge("permission", ("users", "member"), ("recordGroups", "rg"), type="USER", role="READER")
    await w.edge("permission", ("users", "ext3"), ("records", "shared"), type="GROUP", role="READER")                 # a grant of another type is not the user's own
    await w.edge("permission", ("users", "ext4"), ("groups", "g4"), type="GROUP", role="READER")                      # nor is a group that is not joined as a user
    await w.edge("permission", ("groups", "g4"), ("records", "shared"), type="GROUP", role="READER")
    await w.edge("permission", ("users", "ext5"), ("groups", "g5"), type="USER", role="READER")                       # a group whose only grant is on a trashed record
    await w.edge("permission", ("groups", "g5"), ("records", "trashed"), type="GROUP", role="READER")
    await w.edge("permission", ("users", "ext6"), ("groups", "g6"), type="USER", role="READER")                       # a group whose only grant is on another connector's record
    await w.edge("permission", ("groups", "g6"), ("records", "elsewhere"), type="GROUP", role="READER")
    p = w.p
    assert await p.reap_stale_external_app_relations(APP) == 1                                      # the one person left with nothing
    assert await p.get_document("pers", "person") is None and await p.get_document("pers_kept", "person") is not None
    assert await p.get_document("pers_busy", "person") is not None                                  # its membership went, but it still holds an edge
    left = {e["from_id"] for e in await p.get_edges_to_node(f"apps/{APP}", "userAppRelation")}
    assert left == {"ext", "pers_kept", "member", "idle_member"}                                                    # ext2 and pers_busy went (a trashed grant, another connector's), the others hold
    assert await p.get_document("ext2", "users") is not None                                      # a user who lost the flagged edge stays a user
    await p.delete_edge("ext", "users", "shared", "records", "permission")                            # the grant taken away: the next pass reaps it
    assert await p.reap_stale_external_app_relations(APP) == 0
    assert {e["from_id"] for e in await p.get_edges_to_node(f"apps/{APP}", "userAppRelation")} == {"pers_kept", "member", "idle_member"}
    with in_org(_org()):
        assert await p.reap_stale_external_app_relations(APP) == 0
    assert await p.reap_stale_external_app_relations("another-app") == 0


async def test_a_failed_reap_reports_none_and_the_next_pass_does_it(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("users", {"_key": "ext"})
    await w.edge("userAppRelation", ("users", "ext"), ("apps", APP), isExternalUser=True)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.reap_stale_external_app_relations(APP) == 0
    monkeypatch.setattr(w.p, "_q", real)
    assert len(await w.p.get_edges_to_node(f"apps/{APP}", "userAppRelation")) == 1                 # the failed pass removed nothing
    await w.p.reap_stale_external_app_relations(APP)
    assert await w.p.get_edges_to_node(f"apps/{APP}", "userAppRelation") == []
