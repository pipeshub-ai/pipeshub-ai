"""Users, orgs, the people behind an app, groups, roles and source-account links on PostgreSQL, against a real database.

The rules are those of ``Neo4jProvider``: the org is the ambient one, an email compares case-insensitively, an inactive user is still a user,
a user is an org member by an ``ENTITY=ORGANIZATION`` edge to the org's own node, a link to a source account is one per connector. What these
methods write feeds the access check, which is asked at the end.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import Connectors
from app.core import org_context
from app.core.org_context import MissingOrgContext, OrgMismatch, SystemContextNotAllowed
from app.models.entities import AppRole, AppUser, AppUserGroup

from .test_access import APP, World, rec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


def _org() -> str:
    return uuid.uuid4().hex[:24]


@pytest.fixture
async def w(pg_provider, pg_db, org_ctx) -> AsyncIterator[World]:
    org = _org()
    with org_ctx(org):
        world = World(pg_provider, org)
        await world.nodes("organizations", {"_key": org, "isActive": True, "accountType": "enterprise"})
        await world.nodes("apps", {"_key": APP, "type": "JIRA", "isActive": True, "createdBy": "uid-creator"})
        await world.edge("orgAppRelation", ("organizations", org), ("apps", APP))
        yield world
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def member(w: World, key: str, **doc: object) -> None:
    await w.nodes("users", {"_key": key, **doc})
    await w.edge("belongsTo", ("users", key), ("organizations", w.org), entityType="ORGANIZATION")


async def test_a_user_is_found_by_user_id_and_by_email_whatever_the_case_active_or_not(w: World) -> None:
    await w.nodes("users", {"_key": "k1", "userId": "uid-1", "email": "Ann@Example.com", "isActive": False, "fullName": "Ann"})
    p = w.p
    assert (await p.get_user_by_user_id("uid-1"))["_key"] == "k1"
    assert await p.get_user_by_user_id("nope") is None
    got = await p.get_user_by_email("aNN@example.COM")
    assert got is not None and got.id == "k1" and got.is_active is False        # an inactive user is still a user
    assert await p.get_user_by_email("someone@else.com") is None


async def test_users_listed_for_an_org_are_its_members_by_edge_and_active_unless_asked(w: World) -> None:
    await member(w, "on", userId="a", isActive=True, email="on@x.com")
    await member(w, "off", userId="b", isActive=False, email="off@x.com")
    await w.nodes("users", {"_key": "stray", "userId": "c", "isActive": True, "email": "stray@x.com"})           # no org edge
    await w.nodes("organizations", {"_key": "someone-elses-org"})
    await w.nodes("users", {"_key": "elsewhere", "isActive": True})
    await w.edge("belongsTo", ("users", "elsewhere"), ("organizations", "someone-elses-org"), entityType="ORGANIZATION")
    assert {u["_key"] for u in await w.p.get_users(w.org)} == {"on"}
    assert {u["_key"] for u in await w.p.get_users(w.org, active=False)} == {"on", "off"}
    assert await w.p.get_users(_org()) == []                                      # another org's id reads nothing


async def test_organization_users_page_search_count_and_leave_out_service_accounts(w: World) -> None:
    for i, name in enumerate(["Cara", "Abe", "Bea", "Dan"]):
        await member(w, f"u{i}", userId=f"id{i}", fullName=name, email=f"{name.lower()}@x.com", isActive=True)
    await member(w, "svc", userId="idsvc", fullName="Bot", email="bot@service.pipeshub.internal", isActive=True, kind="service")
    await member(w, "inactive", userId="idin", fullName="Zed", email="zed@x.com", isActive=False)
    rows, total = await w.p.get_organization_users(w.org, page=1, limit=2)
    assert [r["name"] for r in rows] == ["Abe", "Bea"] and total == 4
    rows, total = await w.p.get_organization_users(w.org, page=2, limit=2)
    assert [r["name"] for r in rows] == ["Cara", "Dan"] and total == 4
    rows, total = await w.p.get_organization_users(w.org, search="BEA@")
    assert [r["name"] for r in rows] == ["Bea"] and total == 1                    # case-insensitive, on name or email
    rows, total = await w.p.get_organization_users(w.org, include_service_accounts=True)
    assert total == 5 and "Bot" in {r["name"] for r in rows}
    assert await w.p.get_organization_users(_org()) == ([], 0)


async def test_find_users_matches_a_person_by_email_or_name_words(w: World) -> None:
    await member(w, "a1", fullName="Ann  Lee", email="ann@x.com", firstName="Ann", lastName="Lee")
    await member(w, "a2", fullName="Ann Lee", email="ann@x.com")                         # the same person twice (two nodes)
    await member(w, "b1", fullName="Bob Stone", email="bob@x.com")
    assert {r["id"] for r in await w.p.find_users(w.org, "ANN@x.com")} == {"a1", "a2"}
    assert {r["id"] for r in await w.p.find_users(w.org, "bob")} == {"b1"}
    assert await w.p.find_users(w.org, "ann lee", limit=1) != []
    assert await w.p.find_users(w.org, "") == [] and await w.p.find_users(_org(), "ann") == []


async def test_the_orgs_and_their_apps(w: World, org_ctx) -> None:
    await w.nodes("apps", {"_key": "off", "type": "SLACK", "isActive": False})
    await w.edge("orgAppRelation", ("organizations", w.org), ("apps", "off"))
    assert [o["_key"] for o in await w.p.get_all_orgs()] == [w.org]
    assert await w.p.get_all_orgs(is_external=True) == []
    assert {a["_key"] for a in await w.p.get_org_apps(w.org)} == {APP}
    assert {a["_key"] for a in await w.p.get_org_apps(w.org, active_only=False)} == {APP, "off"}
    assert {a["_key"] for a in await w.p.get_org_apps(w.org, active_only=False, app_type="SLACK")} == {"off"}
    assert await w.p.get_org_apps(_org()) == []
    assert await w.p.get_account_type(w.org) == "enterprise" and await w.p.get_account_type(w.org, is_external=True) is None
    assert await w.p.get_account_type(_org()) is None
    token = org_context.push(None)
    try:
        with org_context.system("test.orgs"):
            assert w.org in {o["_key"] for o in await w.p.get_all_orgs()}                # every org, through the vetted read
    finally:
        org_context.pop(token)


async def test_graph_keys_by_user_ids_name_every_missing_user(w: World) -> None:
    await w.nodes("users", {"_key": "k1", "userId": "uid-1"})
    assert await w.p.get_graph_user_keys_by_mongo_user_ids(["uid-1"], chunk_size=10) == {"uid-1": "k1"}
    with pytest.raises(ValueError, match="uid-2"):
        await w.p.get_graph_user_keys_by_mongo_user_ids(["uid-1", "uid-2"], chunk_size=10)


async def test_the_people_behind_an_app_are_the_orgs_members_linked_to_it(w: World) -> None:
    await member(w, "m", userId="um", email="m@x.com")
    await w.nodes("users", {"_key": "guest", "userId": "ug", "email": "g@x.com"})
    await w.edge("userAppRelation", ("users", "m"), ("apps", APP), sourceUserId="src-m")
    await w.edge("userAppRelation", ("users", "guest"), ("apps", APP))
    got = {u["_key"]: u for u in await w.p.get_app_users(w.org, APP)}
    assert set(got) == {"m"} and got["m"]["sourceUserId"] == "src-m" and got["m"]["appName"] == "JIRA"          # a guest has no org edge: as on Neo4j, not listed
    assert (await w.p.get_user_by_source_id("src-m", APP)).id == "m"
    assert await w.p.get_user_by_source_id("src-m", "other-app") is None
    await w.edge("userAppRelation", ("users", "m"), ("apps", "ghost-app"), sourceUserId="src-ghost")             # an edge to an app that is not there
    assert await w.p.get_user_by_source_id("src-ghost", "ghost-app") is None
    assert (await w.p.get_app_creator_user(APP)) is None                                    # created by a user that is not there
    await w.nodes("users", {"_key": "creator", "userId": "uid-creator", "email": "c@x.com"})
    assert (await w.p.get_app_creator_user(APP)).id == "creator"
    assert await w.p.get_app_users(_org(), APP) == []


def app_user(email: str, key: str, *, guest: bool = False, active: bool = False) -> AppUser:
    return AppUser(app_name=Connectors.JIRA, connector_id=APP, id=key, source_user_id=f"src-{key}", email=email, full_name=key, is_guest=guest, is_active=active)


async def test_synced_users_are_created_inactive_members_unless_guests_and_reused_by_email(w: World) -> None:
    await member(w, "existing", userId="ue", email="Old@X.com", isActive=True)
    await w.nodes("users", {"_key": "healme", "email": "healed@x.com", "isActive": False})
    await w.edge("belongsTo", ("users", "healme"), ("organizations", w.org), entityType="ORGANIZATION")        # an earlier sync made a guest a member
    await w.p.batch_upsert_app_users([app_user("old@x.com", "n1"), app_user("new@x.com", "n2"), app_user("NEW@x.com", "n3"),
                                      app_user("guest@x.com", "n4", guest=True), app_user("healed@x.com", "n5", guest=True)])
    users = {u["email"].lower(): u for u in await w.p.get_all_documents("users")}
    assert set(users) == {"old@x.com", "new@x.com", "guest@x.com", "healed@x.com"}       # case-insensitive: no second node for an address
    assert users["new@x.com"]["isActive"] is False and users["new@x.com"]["orgId"] == w.org
    assert users["old@x.com"]["_key"] == "existing"
    members = {u["_key"] for u in await w.p.get_users(w.org, active=False)}
    assert members == {"existing", "n2"}                                               # a guest is no member, and a healed one lost the edge
    linked = {u["_key"]: u["sourceUserId"] for u in await w.p.get_app_users(w.org, APP)}
    assert linked == {"existing": "src-n1", "n2": "src-n3"}                                  # the members; a guest is linked to the app but is no member
    assert {e["from_id"] for e in await w.p.get_edges_to_node(f"apps/{APP}", "userAppRelation")} == {"existing", "n2", "n4", "healme"}
    await w.p.batch_upsert_app_users([])                                                 # nothing to do


async def test_groups_and_roles_round_trip_in_the_ambient_org_whatever_the_model_says(w: World) -> None:
    g = AppUserGroup(app_name=Connectors.JIRA, connector_id=APP, source_user_group_id="ext-g", name="G", org_id="some-other-org")
    r = AppRole(app_name=Connectors.JIRA, connector_id=APP, source_role_id="ext-r", name="R", org_id="some-other-org")
    await w.p.batch_upsert_user_groups([g])
    await w.p.batch_upsert_app_roles([r])
    assert (await w.p.get_document(g.id, "groups"))["orgId"] == w.org and (await w.p.get_document(r.id, "roles"))["orgId"] == w.org
    assert (await w.p.get_user_group_by_external_id(APP, "ext-g")).id == g.id
    assert await w.p.get_user_group_by_external_id("other-app", "ext-g") is None
    assert [x.id for x in await w.p.get_user_groups(APP, w.org)] == [g.id] and await w.p.get_user_groups(APP, _org()) == []
    assert (await w.p.get_app_role_by_external_id(APP, "ext-r")).id == r.id
    await w.edge("belongsTo", ("users", "x"), ("groups", g.id))
    await w.edge("permission", ("users", "x"), ("groups", g.id), type="USER", role="READER")
    assert await w.p.delete_edges_to_groups("x", "users", "permission") == 1
    assert await w.p.get_edge("x", "users", g.id, "groups", "belongsTo") is not None             # other edge collections stay


async def test_a_users_directory_groups_are_theirs_and_those_of_the_account_they_signed_in_as(w: World) -> None:
    await w.nodes("users", {"_key": "me", "userId": "uid-me"}, {"_key": "src", "userId": "uid-src"})
    await w.nodes("groups", {"_key": "mine", "name": "Mine", "externalGroupId": "e1", "connectorId": APP, "connectorName": "JIRA"},
                  {"_key": "theirs", "name": "Theirs", "externalGroupId": "e2", "connectorId": APP, "connectorName": "JIRA"},
                  {"_key": "elsewhere", "name": "Other connector", "externalGroupId": "e3", "connectorId": "app2", "connectorName": "SLACK"})
    await w.member("me", "mine")
    await w.edge("authenticatedAs", ("users", "me"), ("users", "src"), connectorId=APP)
    await w.member("src", "theirs")
    await w.member("src", "elsewhere")
    got = await w.p.get_user_directory_groups("uid-me", w.org)
    assert [(g["externalId"], g["appName"]) for g in got] == [("e1", "JIRA"), ("e2", "JIRA")]         # not the account's groups of another connector
    assert await w.p.get_user_directory_groups("uid-me", w.org, app_names=["SLACK"]) == []
    assert await w.p.get_user_directory_groups("uid-me", _org()) == []


async def test_app_membership_is_create_only_and_the_all_team_edge_is_idempotent(w: World) -> None:
    await w.nodes("users", {"_key": "u1"})
    await w.p.ensure_app_membership("u1", "users", APP, is_external=False, source_user_id="s1")
    await w.p.ensure_app_membership("u1", "users", APP, is_external=True, source_user_id="s2")        # never downgrades a member
    edge = await w.p.get_edge("u1", "users", APP, "apps", "userAppRelation")
    assert edge["isExternalUser"] is False and edge["sourceUserId"] == "s1"
    await w.p.ensure_app_membership("ghost", "users", APP, is_external=False)                        # a missing end creates nothing
    assert await w.p.get_edge("ghost", "users", APP, "apps", "userAppRelation") is None
    await w.p.ensure_team_app_edge(APP, w.org)                                                       # no team yet: nothing
    assert await w.p.get_edge(f"all_{w.org}", "teams", APP, "apps", "userAppRelation") is None
    await w.nodes("teams", {"_key": f"all_{w.org}"})
    await w.p.ensure_team_app_edge("new-app", w.org)
    await w.p.ensure_team_app_edge("new-app", w.org)
    assert await w.p.get_document("new-app", "apps") is not None
    assert len(await w.p.get_edges_from_node(f"teams/all_{w.org}", "userAppRelation")) == 1
    await w.p.ensure_team_app_edge("other", _org())                                                  # another org's team is not this org's
    assert await w.p.get_document("other", "apps") is None


async def test_a_source_account_link_is_one_per_connector_and_refuses_another_org(w: World) -> None:
    await w.nodes("users", {"_key": "c"}, {"_key": "s1"}, {"_key": "s2"})
    await w.p.upsert_authenticated_as("c", "s1", APP, w.org)
    await w.p.upsert_authenticated_as("c", "s2", APP, w.org)                                          # repointed: the stale link goes
    await w.p.upsert_authenticated_as("c", "s2", APP, w.org)
    links = await w.p.get_edges_from_node("users/c", "authenticatedAs")
    assert [(e["to_id"], e["connectorId"], e["orgId"]) for e in links] == [("s2", APP, w.org)]
    await w.p.upsert_authenticated_as("c", "ghost", APP, w.org)                                       # a missing end changes nothing
    assert len(await w.p.get_edges_from_node("users/c", "authenticatedAs")) == 1
    with pytest.raises(OrgMismatch):
        await w.p.upsert_authenticated_as("c", "s1", APP, _org())
    assert await w.p.remove_authenticated_as(APP) is True and await w.p.remove_authenticated_as(APP) is False
    assert await w.p.get_edges_from_node("users/c", "authenticatedAs") == []


async def test_what_identity_writes_feeds_the_access_check(w: World) -> None:
    """A source account's grants open records for the user who signed in as it, until the link is removed; a synced group's members get its grants."""
    await member(w, "me", userId="uid-me", email="me@x.com")
    await w.nodes("users", {"_key": "src"})
    await w.nodes("records", rec("viaLink"), rec("viaGroup"))
    await w.edge("permission", ("users", "src"), ("records", "viaLink"), type="USER", role="READER")
    await w.p.ensure_app_membership("me", "users", APP, is_external=False)
    assert await w.allowed("me", "viaLink", "viaGroup") == set()                                      # a userAppRelation alone does not open a record
    await w.p.upsert_authenticated_as("me", "src", APP, w.org)
    assert await w.allowed("me", "viaLink", "viaGroup") == {"viaLink"}
    await w.p.remove_authenticated_as(APP)
    assert await w.allowed("me", "viaLink") == set()
    g = AppUserGroup(app_name=Connectors.JIRA, connector_id=APP, source_user_group_id="e", name="G")
    await w.p.batch_upsert_user_groups([g])
    await w.member("me", g.id)
    await w.edge("permission", ("groups", g.id), ("records", "viaGroup"), type="GROUP", role="READER")
    assert await w.allowed("me", "viaGroup") == {"viaGroup"}
    assert await w.p.get_user_app_ids("me") == [APP]
    assert [a["_key"] for a in await w.p.get_user_apps("me")] == [APP]


CALLS = [
    ("get_user_by_user_id", ("u",)), ("get_user_by_email", ("a@b.c",)), ("get_users_by_emails", (["a@b.c"],)), ("get_org_apps", ("o",)), ("get_account_type", ("o",)), ("get_users", ("o",)),
    ("get_organization_users", ("o",)), ("find_users", ("o", "x")), ("get_graph_user_keys_by_mongo_user_ids", (["u"],)), ("get_user_by_source_id", ("s", "c")),
    ("get_app_users", ("o", "c")), ("batch_upsert_app_users", ([app_user("a@b.c", "k")],)), ("get_user_group_by_external_id", ("c", "e")),
    ("get_user_groups", ("c", "o")), ("get_user_directory_groups", ("u", "o")), ("get_app_role_by_external_id", ("c", "e")),
    ("batch_upsert_user_groups", ([AppUserGroup(app_name=Connectors.JIRA, connector_id="c", source_user_group_id="e", name="G")],)),
    ("delete_edges_to_groups", ("u", "users", "permission")), ("ensure_app_membership", ("u", "users", "c")),
    ("ensure_team_app_edge", ("c", "o")), ("upsert_authenticated_as", ("a", "b", "c", "o")), ("remove_authenticated_as", ("c",)),
    ("get_user_apps", ("u",)), ("get_user_app_ids", ("u",)),
    ("add_user_to_all_team", ("o", "u")), ("ensure_all_team_with_users", ("o",)), ("check_api_toolset_in_use", ("i",)), ("check_mcp_instance_in_use", ("i",)),
    ("check_toolset_instance_in_use", ("i",)), ("check_connector_name_uniqueness", ("n", "team", "o", "u", "apps")),
    ("count_connector_instances_by_scope", ("apps", "team")),
]


@pytest.mark.parametrize(("name", "args"), CALLS, ids=[c[0] for c in CALLS])
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str, args: tuple) -> None:
    kwargs = {"is_external": False} if name == "ensure_app_membership" else {"chunk_size": 10} if name == "get_graph_user_keys_by_mongo_user_ids" else {}
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*args, **kwargs)
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*args, **kwargs)


async def test_two_connectors_of_one_creator_and_source_account_are_two_links(w: World) -> None:
    await w.nodes("users", {"_key": "c"}, {"_key": "s"})
    await w.nodes("apps", {"_key": "app2", "type": "SLACK", "isActive": True})
    await w.p.upsert_authenticated_as("c", "s", APP, w.org)
    await w.p.upsert_authenticated_as("c", "s", "app2", w.org)
    assert sorted(e["connectorId"] for e in await w.p.get_edges_from_node("users/c", "authenticatedAs")) == sorted([APP, "app2"])
    assert sorted(await w.p.get_user_app_ids("c")) == sorted([APP, "app2"])
    await w.p.upsert_authenticated_as("c", "s", APP, w.org)                                  # touching one leaves the other
    assert len(await w.p.get_edges_from_node("users/c", "authenticatedAs")) == 2
    assert await w.p.remove_authenticated_as(APP) is True
    assert [e["connectorId"] for e in await w.p.get_edges_from_node("users/c", "authenticatedAs")] == ["app2"]
    # the same through the generic writer, as the conformance loader and a connector write them
    await w.p.batch_create_edges([{"from_id": "c", "from_collection": "users", "to_id": "s", "to_collection": "users", "connectorId": "app3"},
                                  {"from_id": "c", "from_collection": "users", "to_id": "s", "to_collection": "users", "connectorId": "app4"}], "authenticatedAs")
    assert len(await w.p.get_edges_from_node("users/c", "authenticatedAs")) == 3


async def test_graph_keys_by_user_ids_only_name_users_of_the_org_asked(w: World) -> None:
    await w.nodes("users", {"_key": "k1", "userId": "uid-1"})
    await w.p.batch_upsert_nodes([{"_key": "k2", "userId": "uid-2", "orgId": "someone-else"}], "users")
    assert await w.p.get_graph_user_keys_by_mongo_user_ids(["uid-1"], w.org, chunk_size=10) == {"uid-1": "k1"}
    with pytest.raises(ValueError, match="uid-2"):
        await w.p.get_graph_user_keys_by_mongo_user_ids(["uid-1", "uid-2"], w.org, chunk_size=10)
    assert await w.p.get_graph_user_keys_by_mongo_user_ids(["uid-2"], None, chunk_size=10) == {"uid-2": "k2"}      # no org asked: any user of this graph


async def test_an_address_is_lowered_by_the_database_alone_so_the_stored_and_the_asked_side_agree(w: World) -> None:
    """Python's lower() and the database's can differ on non-ASCII letters (and the database's on its locale): one node per address, whichever agrees."""
    a, b = "\u00c9L\u00c8VE@X.com", "\u00e9l\u00e8ve@x.COM"
    same = await w.p._q(lambda tx: tx.fetchval("SELECT lower($1::text) = lower($2::text)", a, b), None)
    await w.p.batch_upsert_app_users([app_user(a, "n1"), app_user(b, "n2")])
    nodes = await w.p.get_all_documents("users")
    assert (len(nodes) == 1) is bool(same)
    assert (await w.p.get_user_by_email(b)).id == ("n1" if same else "n2")            # the lookup by address resolves as the batch did
    assert [r["id"] for r in await w.p.find_users(w.org, b)] == (["n1"] if same else ["n2"])
