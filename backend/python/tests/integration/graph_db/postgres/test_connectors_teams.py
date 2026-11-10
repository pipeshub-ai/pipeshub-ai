"""Connector instances and teams on PostgreSQL, against a real database, with the rules of ``Neo4jProvider``: an instance is the org's when its org
node has an edge to it, a personal one is its creator's alone, a team app reaches a user directly or through a team, names compare without case and
surrounding whitespace. Teams feed the access check (a team is a grantee), which is asked at the end.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import pytest

from app.core import org_context
from app.core.org_context import MissingOrgContext, SystemContextNotAllowed

from .test_access import APP, World, rec
from .test_identity import _org, member
from .test_identity import w as _identity_world  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_identity_world")


async def app(w: World, key: str, *, scope: str = "team", created_by: str = "uid-me", ts: int = 1, org_edge: bool = True, **doc: object) -> None:
    await w.nodes("apps", {"_key": key, "name": key, "type": "SLACK", "scope": scope, "createdBy": created_by, "createdAtTimestamp": ts, **doc})
    if org_edge:
        await w.edge("orgAppRelation", ("organizations", w.org), ("apps", key))


async def test_an_instance_is_the_orgs_only_through_its_org_node_and_a_personal_one_is_its_creators(w: World) -> None:
    await app(w, "team1")
    await app(w, "mine", scope="personal")
    await app(w, "theirs", scope="personal", created_by="uid-other")
    await app(w, "noedge", org_edge=False)
    await w.nodes("organizations", {"_key": "other-org-node"})
    await w.nodes("apps", {"_key": "foreign", "scope": "team"})
    await w.edge("orgAppRelation", ("organizations", "other-org-node"), ("apps", "foreign"))      # related to an org node that is not this org's
    got = {a["_key"] for a in await w.p.get_user_connector_instances("apps", "uid-me", w.org, "team", "personal")}
    assert got == {"team1", "mine"}
    assert await w.p.get_user_connector_instances("apps", "uid-me", _org(), "team", "personal") == []


async def test_filtered_instances_page_count_and_narrow_as_the_listing_does(w: World) -> None:
    await w.nodes("users", {"_key": "me", "userId": "uid-me"})
    await w.nodes("teams", {"_key": "tm"})
    await app(w, "direct", ts=5, isActive=True, isAuthenticated=True, isConfigured=True)
    await app(w, "viaTeam", ts=4, isActive=False)
    await app(w, "notMine", ts=3)
    await app(w, "personal", scope="personal", ts=2)
    await app(w, "kb", ts=1, type="KB")
    await w.edge("userAppRelation", ("users", "me"), ("apps", "direct"))
    await w.member("me", "tm", "teams")
    await w.edge("userAppRelation", ("teams", "tm"), ("apps", "viaTeam"))
    p = w.p

    async def keys(**kw: object) -> list[str]:
        rows, _ = await p.get_filtered_connector_instances("apps", "orgAppRelation", w.org, "uid-me", **kw)
        return [r["_key"] for r in rows]
    assert await keys(scope="team", kb_connector_type="KB") == ["direct", "viaTeam"]                    # a team app the user reaches, newest first, not the KB
    assert await keys(scope="team", is_admin=True, kb_connector_type="KB") == ["direct", "viaTeam", "notMine"]
    assert await keys(scope="personal") == ["personal"]
    assert await keys(kb_connector_type="KB", exclude_kb=False) == ["direct", "viaTeam", "notMine", "personal", "kb"]
    assert await keys(is_active=True) == ["direct"] and await keys(is_authenticated=True) == ["direct"] and await keys(is_configured=True) == ["direct"]
    assert await keys(search="VIAT") == ["viaTeam"] and await keys(search=".*") == []                    # a search is text, not a pattern
    assert await keys(connector_type_filter="KB") == ["kb"] and await keys(allowed_connector_types=["KB"]) == ["kb"]
    rows, total = await p.get_filtered_connector_instances("apps", "orgAppRelation", w.org, "uid-me", skip=1, limit=2, is_admin=True)
    assert [r["_key"] for r in rows] == ["viaTeam", "notMine"] and total == 5
    assert await p.get_filtered_connector_instances("apps", "orgAppRelation", _org(), "uid-me") == ([], 0)
    assert await p.get_filtered_connector_instances("apps", "orgAppRelation", "", "uid-me") == ([], 0)


async def test_a_connector_name_clashes_by_scope_ignoring_case_and_surrounding_whitespace(w: World) -> None:
    await app(w, "t1", name="  Sales Slack \t")
    await app(w, "p1", scope="personal", name="Mine", created_by="uid-me")
    p = w.p
    assert await p.check_connector_name_exists("apps", " sales slack", "team", org_id=w.org) is True
    assert await p.check_connector_name_exists("apps", "SALES SLACK", "team", org_id=w.org, exclude_connector_id="t1") is False
    assert await p.check_connector_name_exists("apps", "sales slack", "team") is False                       # a team name is looked up under an org
    assert await p.check_connector_name_exists("apps", "sales slack", "team", org_id=_org()) is False
    assert await p.check_connector_name_exists("apps", "mine", "personal", user_id="uid-me") is True
    assert await p.check_connector_name_exists("apps", "mine", "personal", user_id="uid-other") is False
    assert await p.check_connector_name_exists("apps", "mine", "team", org_id=w.org) is False


async def test_connector_status_and_use(w: World) -> None:
    await app(w, "c1", isActive=False, isAgentActive=True)
    await app(w, "c2")
    assert await w.p.batch_update_connector_status("apps", ["c1", "c2", "nope"], is_active=True, is_agent_active=False) == 2
    got = await w.p.get_document("c1", "apps")
    assert got["isActive"] is True and got["isAgentActive"] is False
    assert await w.p.batch_update_connector_status("apps", [], is_active=True, is_agent_active=True) == 0
    await w.nodes("agentKnowledge", {"_key": "k1", "connectorId": "c1"})
    await w.nodes("agentInstances", {"_key": "a1", "name": "Helper"}, {"_key": "a2", "name": "Helper"}, {"_key": "gone", "name": "Old", "isDeleted": True})
    for a in ("a1", "a2", "gone"):
        await w.edge("agentHasKnowledge", ("agentInstances", a), ("agentKnowledge", "k1"))
    assert sorted(await w.p.check_connector_in_use("c1")) == ["Helper", "Helper"]                           # two agents with one name count apart
    assert await w.p.check_connector_in_use("c2") == []


async def _team(w: World, key: str, *, owner: str | None = None, ts: int = 1, org: str | None = None, **doc: object) -> None:
    await w.p.batch_upsert_nodes([{"_key": key, "name": key, "description": f"about {key}", "createdBy": "me", "createdAtTimestamp": ts, "orgId": org or w.org, **doc}], "teams")
    if owner:
        await w.edge("permission", ("users", owner), ("teams", key), type="USER", role="OWNER", createdAtTimestamp=ts)


async def test_a_users_teams_carry_their_active_members_and_what_the_user_may_do(w: World) -> None:
    await member(w, "me", userId="uid-me", fullName="Me", email="me@x.com", isActive=True)
    await member(w, "ann", userId="uid-ann", fullName="Ann", email="ann@x.com", isActive=True)
    await member(w, "off", userId="uid-off", isActive=False)
    await member(w, "bot", userId="uid-bot", isActive=True, kind="service", email="bot@service.pipeshub.internal")
    await _team(w, "t1", owner="me", ts=2, createdBy="me")
    await _team(w, f"all_{w.org}", ts=1)
    await w.edge("permission", ("users", "me"), ("teams", f"all_{w.org}"), type="USER", role="READER")
    for u, role in (("ann", "READER"), ("off", "READER"), ("bot", "READER")):
        await w.edge("permission", ("users", u), ("teams", "t1"), type="USER", role=role)
    await _team(w, "t2", ts=3)
    await w.edge("permission", ("users", "ann"), ("teams", "t2"), type="USER", role="OWNER")             # not the user's
    teams, total = await w.p.get_user_teams("me")
    assert [t["id"] for t in teams] == ["t1", f"all_{w.org}"] and total == 2
    t1 = teams[0]
    assert sorted(m["userEmail"] for m in t1["members"]) == ["ann@x.com", "me@x.com"] and t1["memberCount"] == 2           # active people only, no bot
    assert (t1["canEdit"], t1["canDelete"], t1["canManageMembers"]) == (True, True, True)
    assert (teams[1]["canEdit"], teams[1]["canDelete"]) == (False, False)
    assert (await w.p.get_user_teams("me", include_service_accounts=True))[0][0]["memberCount"] == 3        # the bot, when asked for
    assert [t["id"] for t in (await w.p.get_user_teams("me", search="ABOUT t1"))[0]] == ["t1"]
    assert [t["id"] for t in (await w.p.get_user_teams("me", created_by="nobody"))[0]] == []
    assert (await w.p.get_user_teams("me", page=2, limit=1))[0][0]["id"] == f"all_{w.org}"
    everyone, n = await w.p.get_user_teams("me", org_id=w.org, is_org_admin=True)
    assert {t["id"] for t in everyone} == {"t1", "t2", f"all_{w.org}"} and n == 3 and all(t["canEdit"] for t in everyone)
    assert [t["canDelete"] for t in everyone if t["id"] == f"all_{w.org}"] == [False]               # the All team is never deleted
    assert await w.p.get_user_teams("me", org_id=_org(), is_org_admin=True) == ([], 0)


async def test_a_team_without_an_org_cannot_be_deleted_by_anyone(w: World) -> None:
    """Neo4j compares the id with ``'all_' + orgId``: null for a team with no orgId, so nothing is true."""
    await member(w, "me", userId="uid-me", isActive=True)
    await w.p.batch_upsert_nodes([{"_key": "orphan", "name": "orphan", "createdAtTimestamp": 1}], "teams")
    await w.edge("permission", ("users", "me"), ("teams", "orphan"), type="USER", role="OWNER")
    t = (await w.p.get_user_teams("me"))[0][0]
    assert (t["canEdit"], t["canDelete"]) == (True, False)


async def test_one_team_its_members_search_and_the_removal_of_members(w: World) -> None:
    for k, name in (("me", "Me"), ("ann", "Ann"), ("bea", "Bea")):
        await member(w, k, userId=f"uid-{k}", fullName=name, email=f"{k}@x.com", isActive=True)
    await _team(w, "t1", owner="me")
    for u in ("ann", "bea"):
        await w.edge("permission", ("users", u), ("teams", "t1"), type="USER", role="READER")
    one = await w.p.get_team_with_users("t1", w.org, "me")
    assert one["memberCount"] == 3 and one["canEdit"] is True
    assert (await w.p.get_team_with_users("t1", w.org, "ann"))["canEdit"] is False
    assert (await w.p.get_team_with_users("t1", w.org, "ann", is_org_admin=True))["canEdit"] is True
    assert await w.p.get_team_with_users("nope", w.org, "me") is None and await w.p.get_team_with_users("t1", _org(), "me") is None
    page = await w.p.get_team_users("t1", w.org, "me", search="BE", page=1, limit=5)
    assert [m["userEmail"] for m in page["members"]] == ["bea@x.com"] and page["memberCount"] == 1
    assert await w.p.find_team_by_name(w.org, "  T1 ") == {"id": "t1", "name": "t1"}
    assert await w.p.find_team_by_name(w.org, "t1", exclude_team_id="t1") is None and await w.p.find_team_by_name(_org(), "t1") is None
    info = await w.p.get_team_owner_removal_info("t1", ["me", "ann"])
    assert info == {"owners_being_removed": ["me"], "total_owner_count": 1}
    gone = await w.p.delete_team_member_edges("t1", ["ann", "nobody"])
    assert [(e["from_id"], e["to_id"], e["role"]) for e in gone] == [("ann", "t1", "READER")]
    assert (await w.p.get_team_with_users("t1", w.org, "me"))["memberCount"] == 2


async def test_a_team_is_a_grantee_of_the_access_check(w: World) -> None:
    """A user's team gates the apps it is linked to, and a team's grant opens records (a knowledge base opens everything it holds)."""
    await member(w, "me", userId="uid-me", isActive=True)
    await _team(w, "tm", owner="me")
    await w.nodes("apps", {"_key": "kb1", "type": "KB"}, {"_key": "app9", "type": "SLACK"})
    await w.nodes("records", {"_key": "inKb", "connectorId": "kb1"}, rec("viaTeamGrant"))
    await w.edge("belongsTo", ("records", "inKb"), ("apps", "kb1"))
    assert await w.allowed("me", "kb1", "app9", "inKb", "viaTeamGrant") == set()
    await w.edge("permission", ("teams", "tm"), ("apps", "kb1"), type="TEAM", role="READER")
    await w.edge("userAppRelation", ("teams", "tm"), ("apps", APP))                      # the connector gate of the record below
    await w.p.ensure_team_app_edge("app9", w.org)                                       # only the All team gets one; this team is not it
    await w.edge("userAppRelation", ("teams", "tm"), ("apps", "app9"))
    await w.edge("permission", ("teams", "tm"), ("records", "viaTeamGrant"), type="TEAM", role="READER")
    assert await w.allowed("me", "kb1", "app9", "inKb", "viaTeamGrant") == {"kb1", "app9", "inKb", "viaTeamGrant"}
    await w.p.delete_team_member_edges("tm", ["me"])                                    # leaving the team closes all of it
    assert await w.allowed("me", "kb1", "app9", "inKb", "viaTeamGrant") == set()


CALLS = [
    ("get_user_connector_instances", ("apps", "u", "o", "team", "personal")), ("get_filtered_connector_instances", ("apps", "orgAppRelation", "o", "u")),
    ("check_connector_name_exists", ("apps", "n", "personal")), ("batch_update_connector_status", ("apps", ["c"])), ("check_connector_in_use", ("c",)),
    ("get_user_teams", ("u",)), ("get_team_with_users", ("t", "o", "u")), ("find_team_by_name", ("o", "n")), ("get_team_users", ("t", "o", "u")),
    ("delete_team_member_edges", ("t", ["u"])), ("get_team_owner_removal_info", ("t", ["u"])),
]


@pytest.mark.parametrize(("name", "args"), CALLS, ids=[c[0] for c in CALLS])
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str, args: tuple) -> None:
    kwargs = {"is_active": True, "is_agent_active": True} if name == "batch_update_connector_status" else {}
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*args, **kwargs)
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*args, **kwargs)


async def test_names_compare_with_the_databases_lower_on_both_sides_even_beyond_ascii(w: World) -> None:
    """The stored and the asked name are lowered by the same function; what it makes equal is a clash, in whatever locale the database runs."""
    a, b = "Équipe", "éQUIPE"
    same = await w.p._q(lambda tx: tx.fetchval("SELECT lower($1::text) = lower($2::text)", a, b), None)
    await app(w, "t1", name=a)
    await _team(w, "tn", name=a)
    assert await w.p.check_connector_name_exists("apps", b, "team", org_id=w.org) is bool(same)
    assert (await w.p.find_team_by_name(w.org, b) is not None) is bool(same)
