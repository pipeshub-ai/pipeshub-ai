"""Grants, teams and the knowledge-hub reads on PostgreSQL, against a real database, with the rules of ``Neo4jProvider``.

Every method that grants, revokes, checks or lists access is asked both ways: nobody gets access they were not given (another org, another user, a
revoked grant, a removed team member) and nobody loses access they were given (through a group, a team, an inherited grant). A read that fails
closed is asked again once the cause is gone.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import OrgContext, bind, pop, push
from app.services.graph_db.interface.graph_db_provider import (
    PermissionVerificationUnavailableError,
)

from .test_access import APP, World, _org, rec
from .test_access import w as w

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


@contextlib.contextmanager
def in_org(org: str) -> Iterator[None]:
    token = push(None)
    try:
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            yield
    finally:
        pop(token)


async def grant(w: World, who: str, coll: str, key: str, role: str = "READER", from_coll: str = "users", type_: str = "USER") -> None:
    await w.edge("permission", (from_coll, who), (coll, key), type=type_, role=role)


def ext(key: str, ext_id: str, **kw: object) -> dict:
    """A record ``get_record_by_external_id`` can build."""
    return rec(key, externalRecordId=ext_id, recordType="FILE", origin="CONNECTOR", version=0, createdAtTimestamp=1, updatedAtTimestamp=1,
               sourceCreatedAtTimestamp=1, sourceLastModifiedTimestamp=1, connectorName="SLACK", **kw)


async def failing(*a: object, **k: object) -> None:
    raise RuntimeError("store down")


def keys(users: list) -> set[str]:
    return {u.id for u in users}


# ---- who holds a grant on a node -----------------------------------------------------------------------------------------------------------

async def test_users_with_a_grant_on_a_node_are_those_holding_it_directly_or_on_an_ancestor_it_inherits_from(w: World) -> None:
    await w.nodes("users", {"_key": "direct"}, {"_key": "viaGroup"}, {"_key": "unrelated"}, {"_key": "notInherited"})
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP}, {"_key": "loose", "connectorId": APP})
    await w.nodes("records", rec("r"), rec("other"))
    await w.child(("recordGroups", "g"), ("records", "r"))
    await w.child(("recordGroups", "loose"), ("records", "r"), inherits=False)
    await grant(w, "direct", "records", "r")
    await grant(w, "viaGroup", "recordGroups", "g")
    await grant(w, "notInherited", "recordGroups", "loose")        # r hangs under it but does not inherit from it
    await grant(w, "unrelated", "records", "other")
    assert keys(await w.p.get_users_with_permission_to_node("r", "records")) == {"direct", "viaGroup"}
    assert keys(await w.p.get_users_with_permission_to_node("records/r", "records")) == {"direct", "viaGroup"}       # the collection prefix is dropped
    assert keys(await w.p.get_users_with_permission_to_node("missing", "records")) == set()
    assert (await w.p.get_first_user_with_permission_to_node("r", "records")).id == "direct"                          # the nearest grant first
    assert await w.p.get_first_user_with_permission_to_node("missing", "records") is None


async def test_the_walk_to_an_inherited_grant_stops_after_eight_hops(w: World) -> None:
    await w.nodes("users", {"_key": "top8"}, {"_key": "top9"})
    await w.nodes("recordGroups", *({"_key": f"c{i}", "connectorId": APP} for i in range(10)))
    for i in range(9):
        await w.edge("inheritPermissions", ("recordGroups", f"c{i}"), ("recordGroups", f"c{i + 1}"))
    await grant(w, "top8", "recordGroups", "c8")
    await grant(w, "top9", "recordGroups", "c9")
    assert keys(await w.p.get_users_with_permission_to_node("c0", "records")) == {"top8"}


async def test_a_grant_that_is_taken_away_or_belongs_to_another_org_is_not_listed(w: World) -> None:
    await w.nodes("users", {"_key": "keep"}, {"_key": "gone"})
    await w.nodes("records", ext("r", "ext"))
    await grant(w, "keep", "records", "r")
    await grant(w, "gone", "records", "r")
    other = _org()
    with in_org(other):
        ow = World(w.p, other)
        await ow.nodes("users", {"_key": "foreign"})
        await ow.nodes("records", rec("r"))
        await grant(ow, "foreign", "records", "r")
    assert keys(await w.p.get_users_with_permission_to_node("r", "records")) == {"keep", "gone"}
    await w.p.remove_user_access_to_record(APP, "ext", "gone")
    assert keys(await w.p.get_users_with_permission_to_node("r", "records")) == {"keep"}
    with in_org(other):
        assert keys(await w.p.get_users_with_permission_to_node("r", "records")) == {"foreign"}


async def test_groups_with_a_grant_are_the_synced_ones_holding_it_on_the_node_itself(w: World) -> None:
    await w.nodes("groups", {"_key": "synced", "name": "G", "externalGroupId": "e1", "connectorName": "SLACK", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, "sourceCreatedAtTimestamp": 1, "sourceLastModifiedTimestamp": 1, "groupName": "G", "connectorId": APP}, {"_key": "local", "name": "L"})
    await w.nodes("roles", {"_key": "role", "externalGroupId": "e2"})
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("r"))
    await w.child(("recordGroups", "g"), ("records", "r"))
    for who, coll in (("synced", "groups"), ("local", "groups"), ("role", "roles")):
        await grant(w, who, "records", "r", from_coll=coll, type_=coll[:-1].upper())
    await grant(w, "synced", "recordGroups", "g", from_coll="groups", type_="GROUP")
    assert [g.id for g in await w.p.get_groups_with_permission_to_node("r", "records")] == ["synced"]
    assert [g.id for g in await w.p.get_groups_with_permission_to_node("g", "records")] == ["synced"]
    assert await w.p.get_groups_with_permission_to_node("missing", "records") == []
    with in_org(_org()):
        assert await w.p.get_groups_with_permission_to_node("r", "records") == []


async def test_a_failed_read_of_who_holds_a_grant_answers_nobody_unless_asked_to_raise_and_recovers(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("users", {"_key": "a"})
    await w.nodes("records", rec("r"))
    await grant(w, "a", "records", "r")
    real = w.p._q

    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_users_with_permission_to_node("r", "records") == []
    assert await w.p.get_groups_with_permission_to_node("r", "records") == []
    assert await w.p.get_first_user_with_permission_to_node("r", "records") is None
    with pytest.raises(RuntimeError):
        await w.p.get_users_with_permission_to_node("r", "records", raise_on_error=True)
    monkeypatch.setattr(w.p, "_q", real)
    assert keys(await w.p.get_users_with_permission_to_node("r", "records")) == {"a"}


# ---- writing and taking away grants --------------------------------------------------------------------------------------------------------

async def test_a_record_opens_for_the_user_it_was_granted_to_and_shuts_again_when_the_grant_is_taken_away(w: World) -> None:
    await w.nodes("records", ext("r", "ext"), ext("r2", "ext2"))
    await w.nodes("users", {"_key": "v"})
    await grant(w, "v", "apps", APP)                                  # v passes the connector gate as u does
    assert await w.allowed("u", "r") == set()
    await w.p.batch_upsert_record_permissions("r", [
        {"from_id": "u", "from_collection": "users", "to_id": "ignored", "to_collection": "ignored", "type": "USER", "role": "READER"},
        {"from_id": "v", "from_collection": "users", "type": "USER", "role": "WRITER"}])
    assert await w.allowed("u", "r", "r2") == {"r"} and await w.allowed("v", "r", "r2") == {"r"}        # each edge points at the record given
    await w.p.batch_upsert_record_permissions("r", [])                                                  # nothing to write
    await w.p.remove_user_access_to_record(APP, "ext", "u")
    assert await w.allowed("u", "r") == set()                      # no access that was taken away
    assert await w.allowed("v", "r") == {"r"}                      # and nobody else's went with it
    await w.p.remove_user_access_to_record(APP, "no-such-record", "v")
    await w.p.remove_user_access_to_record("another-app", "ext", "v")
    assert await w.allowed("v", "r") == {"r"}                  # a record that is not there, or of another connector, changes nothing


async def test_a_trashed_record_loses_the_users_grant_too(w: World) -> None:
    await w.nodes("records", ext("t", "ext", isDeleted=True))
    await grant(w, "u", "records", "t")
    await w.p.remove_user_access_to_record(APP, "ext", "u")
    assert not await w.p.get_users_with_permission_to_node("t", "records")


async def test_a_record_inherits_its_groups_grants_only_once_the_inherit_edge_is_written(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("r"))
    await w.edge("nodeRelations", ("recordGroups", "g"), ("records", "r"), relationshipType="PARENT_CHILD")
    await grant(w, "u", "recordGroups", "g")
    assert "r" not in await w.allowed("u", "r")
    await w.p.create_inherit_permissions_relation_record_group("r", "g")
    await w.p.create_inherit_permissions_relation_record_group("r", "g")                      # one edge per pair
    assert await w.allowed("u", "r") == {"r"}
    assert await w.allowed("other", "r") == set()
    assert len(await w.p.get_edges_from_node("records/r", "inheritPermissions")) == 1
    other = _org()
    with in_org(other):
        await World(w.p, other).nodes("recordGroups", {"_key": "g2"})
        await w.p.create_inherit_permissions_relation_record_group("r2", "g2")
    assert await w.p.get_edges_from_node("records/r2", "inheritPermissions") == []             # written in the ambient org only


# ---- teams ---------------------------------------------------------------------------------------------------------------------------------

async def team(w: World) -> None:
    await w.nodes("users", {"_key": "boss"}, {"_key": "m1"}, {"_key": "m2"}, {"_key": "outsider"})
    await w.nodes("teams", {"_key": "t1", "name": "T1"}, {"_key": "t2", "name": "T2"})
    await w.nodes("apps", {"_key": "kb", "type": "KB"})
    await w.edge("permission", ("teams", "t1"), ("apps", "kb"), type="TEAM")        # a grant without a role: the member's team role applies
    for u, role in (("boss", "OWNER"), ("m1", "READER"), ("m2", "READER")):
        await grant(w, u, "teams", "t1", role)
    await grant(w, "m1", "teams", "t2", "READER")


async def test_team_roles_change_for_the_listed_members_of_that_team_and_the_team_grant_follows(w: World) -> None:
    await team(w)
    assert await w.p.get_user_kb_permission("kb", "m1") == "READER"
    got = await w.p.batch_update_team_member_roles("t1", [{"userId": "m1", "role": "WRITER"}, {"userId": "nobody", "role": "OWNER"},
                                                         {"userId": "m2", "role": "OWNER"}, {"userId": "m2", "role": "WRITER"}], 99)
    assert {(e["from_id"], e["role"]) for e in got} == {("m1", "WRITER"), ("m2", "WRITER")}
    assert {(e["_from"], e["_to"], e["updatedAtTimestamp"]) for e in got} == {("users/m1", "teams/t1", 99), ("users/m2", "teams/t1", 99)}
    assert await w.p.get_user_kb_permission("kb", "m1") == "WRITER"            # access through the team follows the new role (nothing missing)
    assert await w.p.get_user_kb_permission("kb", "boss") == "WRITER"          # a member left out is as before (a team never makes an owner)
    assert (await w.p.get_team_permissions_and_owner_count("t2", ["m1"]))["permissions"] == {"m1": "READER"}      # another team's edge of the same user is untouched
    assert await w.p.batch_update_team_member_roles("t1", [], 1) == []
    assert await w.p.get_user_kb_permission("kb", "outsider") is None          # a user who is no member is not one after an update naming them
    assert await w.p.batch_update_team_member_roles("t1", [{"userId": "outsider", "role": "OWNER"}], 1) == []
    assert await w.p.get_user_kb_permission("kb", "outsider") is None
    other = _org()
    with in_org(other):
        assert await w.p.batch_update_team_member_roles("t1", [{"userId": "m1", "role": "OWNER"}], 1) == []        # the same ids in another org are nothing
    assert (await w.p.get_team_permissions_and_owner_count("t1", ["m1"]))["permissions"] == {"m1": "WRITER"}


async def test_the_team_the_roles_and_the_owner_count(w: World) -> None:
    await team(w)
    got = await w.p.get_team_permissions_and_owner_count("t1", ["boss", "m1", "outsider"])
    assert got["team"]["name"] == "T1" and got["permissions"] == {"boss": "OWNER", "m1": "READER"} and got["owner_count"] == 1
    assert await w.p.get_team_permissions_and_owner_count("t1", []) == {"team": got["team"], "permissions": {}, "owner_count": 1}
    gone = await w.p.get_team_permissions_and_owner_count("nope", ["boss"])
    assert gone == {"team": None, "permissions": {}, "owner_count": 0}
    with in_org(_org()):
        assert (await w.p.get_team_permissions_and_owner_count("t1", ["boss"]))["team"] is None


async def test_deleting_a_teams_permissions_removes_its_members_access_and_leaves_the_rest(w: World) -> None:
    await team(w)
    await w.nodes("records", rec("r", origin="UPLOAD"))
    await grant(w, "m1", "records", "r")
    assert await w.p.get_user_kb_permission("kb", "m1") == "READER"
    await w.p.delete_all_team_permissions("t1")
    assert await w.p.get_user_kb_permission("kb", "m1") is None               # no access through a team the user left
    assert await w.p.get_user_kb_permission("kb", "boss") is None
    assert (await w.p.get_team_permissions_and_owner_count("t2", ["m1"]))["permissions"] == {"m1": "READER"}      # another team keeps its members
    assert len(await w.p.get_edges_from_node("teams/t1", "permission")) == 1                                       # the team's own grant to the app stays
    assert await w.allowed("u", APP) == {APP}
    await w.p.delete_all_team_permissions("no-such-team")
    assert keys(await w.p.get_users_with_permission_to_node("r", "records")) == {"m1"}   # a user's direct grants are not a team's
    other = _org()
    with in_org(other):
        await w.p.delete_all_team_permissions("t2")
    assert (await w.p.get_team_permissions_and_owner_count("t2", ["m1"]))["permissions"] == {"m1": "READER"}      # another org's call reaches nothing here


# ---- agents --------------------------------------------------------------------------------------------------------------------------------

async def test_an_agent_is_reached_by_the_users_own_grant_else_the_organizations_and_by_nobody_else(w: World) -> None:
    await w.nodes("organizations", {"_key": w.org})
    await w.nodes("agentInstances", {"_key": "a1"}, {"_key": "gone", "isDeleted": True}, {"_key": "a2"}, {"_key": "a3"})
    await grant(w, "other", "agentInstances", "a3", "OWNER", type_="TEAM")
    await grant(w, "u", "agentInstances", "a1", "WRITER")
    await grant(w, w.org, "agentInstances", "a1", "READER", from_coll="organizations", type_="ORG")
    await grant(w, w.org, "agentInstances", "a2", "OWNER", from_coll="organizations", type_="ORG")
    await grant(w, "u", "agentInstances", "gone", "OWNER")
    await grant(w, "other", "agentInstances", "a2", "READER", type_="TEAM")           # a grant of another type is not the user's own
    p = w.p
    own = await p.check_agent_permission("a1", "u", w.org)
    assert own == {"access_type": "INDIVIDUAL", "user_role": "WRITER", "can_edit": True, "can_delete": False, "can_share": False, "can_view": True}
    assert (await p.check_agent_permission("a1", "other", w.org))["access_type"] == "ORG"       # the organization's grant: every user of the org
    org_grant = await p.check_agent_permission("a2", "u", w.org)
    assert org_grant["access_type"] == "ORG" and org_grant["can_delete"] and org_grant["can_share"]
    assert {k: (await p.check_agent_permission("a2", "other", w.org))[k] for k in ("access_type", "user_role")} == {"access_type": "ORG", "user_role": "OWNER"}       # an edge of another type is not the user's own grant
    assert await p.check_agent_permission("a3", "other", w.org) is None                          # only an edge of another type: no access
    assert await p.check_agent_permission("gone", "u", w.org) is None                            # deleted
    assert await p.check_agent_permission("missing", "u", w.org) is None
    assert await p.check_agent_permission("a1", "u", _org()) is None                             # another org asked of this one
    await w.p.delete_edge("u", "users", "a1", "agentInstances", "permission")
    assert (await p.check_agent_permission("a1", "u", w.org))["user_role"] == "READER"          # the grant taken away: the organization's remains
    await w.p.delete_edge(w.org, "organizations", "a1", "agentInstances", "permission")
    assert await p.check_agent_permission("a1", "u", w.org) is None


async def test_an_agent_check_that_fails_denies_and_answers_again_when_the_store_is_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("agentInstances", {"_key": "a1"})
    await grant(w, "u", "agentInstances", "a1", "OWNER")
    real = w.p._q

    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.check_agent_permission("a1", "u", w.org) is None
    monkeypatch.setattr(w.p, "_q", real)
    assert (await w.p.check_agent_permission("a1", "u", w.org))["user_role"] == "OWNER"


# ---- the knowledge hub ---------------------------------------------------------------------------------------------------------------------

async def test_the_apps_a_user_holds_through_a_grant_or_a_team_and_no_other(w: World) -> None:
    await team(w)
    await w.nodes("apps", {"_key": "direct", "type": "KB"}, {"_key": "hidden", "type": "KB", "isHidden": True}, {"_key": "foreign", "type": "KB"},
                  {"_key": "viaTeam", "type": "KB"}, {"_key": "teamNotMember", "type": "KB"}, {"_key": "typed", "type": "KB"})
    await w.nodes("apps", {"_key": "foreign", "type": "KB", "orgId": "elsewhere"})
    await grant(w, "m1", "apps", "direct")
    await grant(w, "m1", "apps", "hidden")
    await grant(w, "m1", "apps", "foreign")
    await grant(w, "m1", "apps", "typed", type_="GROUP")                         # a direct edge of another type is not a USER grant
    await w.edge("permission", ("teams", "t1"), ("apps", "viaTeam"), type="TEAM")
    await w.edge("permission", ("teams", "t2"), ("apps", "teamNotMember"), type="TEAM")
    await grant(w, "outsider", "teams", "t2", type_="GROUP")                     # an edge of another type is not membership
    await w.edge("permission", ("teams", "t2"), ("apps", "viaTeam"), type="GROUP")      # nor is a team edge of another type a team grant
    got = await w.p.get_user_permission_app_ids("m1", w.org)
    assert sorted(got) == ["direct", "kb", "teamNotMember", "viaTeam"]
    assert await w.p.get_user_permission_app_ids("outsider", w.org) == []
    assert await w.p.get_user_permission_app_ids("m1", _org()) == []
    await w.p.delete_team_member_edges("t1", ["m1"])
    assert sorted(await w.p.get_user_permission_app_ids("m1", w.org)) == ["direct", "teamNotMember"]        # a removed member loses the team's apps, not their own


async def test_the_gate_and_the_connector_types_come_from_the_apps_a_user_passes_and_widen_when_unreadable(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("users", {"_key": "u", "userId": "uid-u"}, {"_key": "other", "userId": "uid-other"})
    await w.nodes("apps", {"_key": "slack", "type": "SLACK", "orgId": w.org}, {"_key": "jira", "type": "JIRA", "orgId": w.org})
    await w.nodes("groups", {"_key": "grp"})
    await grant(w, "u", "apps", "slack")
    await w.member("u", "grp")
    await w.edge("permission", ("groups", "grp"), ("apps", "jira"), type="GROUP", role="READER")
    p = w.p
    ctx = await p.get_knowledge_hub_access_context_v2("u", w.org)
    assert set(ctx) == {"grantee_ids", "gated_app_ids"} and {"u", "grp"} <= set(ctx["grantee_ids"]) and {APP, "slack", "jira"} <= set(ctx["gated_app_ids"])
    assert ctx["grantee_ids"] == (await p.get_knowledge_hub_access_v3("u", w.org))["grantee_ids"]
    assert await p.get_knowledge_hub_access_context_v2("other", w.org) == {"grantee_ids": ["other"], "gated_app_ids": []}
    assert await p.get_knowledge_hub_access_context_v2("u", _org()) == {"grantee_ids": [], "gated_app_ids": []}
    assert {a["_key"] for a in await p.get_gated_apps("u", w.org)} == {APP, "slack", "jira"}
    assert await p.get_gated_apps("u", _org()) == []
    assert set(await p.get_accessible_connector_types("uid-u", w.org)) == {"CONNECTOR", "SLACK", "JIRA"}
    assert await p.get_accessible_connector_types("uid-other", w.org) == []              # a user who passes no gate
    assert await p.get_accessible_connector_types("uid-nobody", w.org) == []
    await p.delete_edge("groups/grp".split("/")[1], "groups", "jira", "apps", "permission")
    assert set(await p.get_accessible_connector_types("uid-u", w.org)) == {"CONNECTOR", "SLACK"}      # the group's grant taken away
    real = p.get_user_by_user_id

    monkeypatch.setattr(p, "get_user_by_user_id", failing)
    assert await p.get_accessible_connector_types("uid-u", w.org) == []
    monkeypatch.setattr(p, "get_user_by_user_id", real)
    assert set(await p.get_accessible_connector_types("uid-u", w.org)) == {"CONNECTOR", "SLACK"}


async def test_a_node_is_handed_out_with_its_metadata_only_to_a_user_who_may_open_it(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP, "groupName": "Group", "connectorName": "SLACK", "groupType": "SPACE", "orgId": w.org})
    await w.nodes("records", rec("r", orgId=w.org, recordType="FILE", mimeType="text/plain", connectorName="SLACK", indexingStatus="COMPLETED", webUrl="http://x"),
                  rec("dir", orgId=w.org, mimeType="folder"), rec("foreignDoc", orgId="elsewhere"), rec("shut", orgId=w.org, accessRule="RESTRICTED"))
    await w.child(("apps", APP), ("recordGroups", "g"))
    for k in ("r", "dir", "foreignDoc", "shut"):
        await w.child(("recordGroups", "g"), ("records", k))
    p, folders = w.p, ["folder"]
    r = await p.get_knowledge_hub_node_access("r", "u", w.org, folders)
    assert r == {"id": "r", "name": "r", "nodeType": "record", "subType": "FILE", "connector": "SLACK", "webUrl": "http://x", "recordType": "FILE",
                 "indexingStatus": "COMPLETED", "isPlaceholder": False, "userRole": None}
    assert (await p.get_knowledge_hub_node_access("dir", "u", w.org, folders))["nodeType"] == "folder"
    g = await p.get_knowledge_hub_node_access("g", "u", w.org, folders)
    assert (g["nodeType"], g["name"], g["subType"], g["connector"]) == ("recordGroup", "Group", "SPACE", "SLACK")
    assert (await p.get_knowledge_hub_node_access(APP, "u", w.org, folders))["nodeType"] == "app"
    for node, who, org in (("r", "other", w.org), ("r", "u", _org()), ("missing", "u", w.org), ("foreignDoc", "u", w.org), ("shut", "u", w.org)):
        assert await p.get_knowledge_hub_node_access(node, who, org, folders) is None          # missing and refused look the same


async def test_a_node_in_a_collection_carries_the_users_role_and_loses_it_with_the_grant(w: World) -> None:
    await w.nodes("apps", {"_key": "kb1", "type": "KB", "orgId": w.org, "name": "KB"})
    await w.nodes("records", {"_key": "kr", "connectorId": "kb1", "orgId": w.org, "connectorName": "KB", "recordName": "kr", "origin": "UPLOAD"})
    await w.edge("nodeRelations", ("apps", "kb1"), ("records", "kr"), relationshipType="PARENT_CHILD")
    await grant(w, "u", "apps", "kb1", "WRITER")
    got = await w.p.get_knowledge_hub_node_access("kr", "u", w.org, [])
    assert got is not None and got["userRole"] == "WRITER"
    assert await w.p.get_knowledge_hub_node_access("kr", "other", w.org, []) is None
    await w.p.delete_edge("u", "users", "kb1", "apps", "permission")
    assert await w.p.get_knowledge_hub_node_access("kr", "u", w.org, []) is None


async def test_a_node_read_fails_closed_unless_the_check_could_not_answer_and_recovers(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("records", rec("r", orgId=w.org))
    await w.child(("apps", APP), ("records", "r"))
    real = w.p._q

    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(PermissionVerificationUnavailableError):      # the check never says "nothing" for a graph that could not answer
        await w.p.get_knowledge_hub_node_access("r", "u", w.org, [])
    monkeypatch.setattr(w.p, "_q", real)
    assert (await w.p.get_knowledge_hub_node_access("r", "u", w.org, []))["id"] == "r"
