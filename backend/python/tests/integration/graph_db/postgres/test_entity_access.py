"""Entity search on PostgreSQL, against a real database: the records of an entity, the ones a user holds a role on, what a user can reach and the entity index.

Both directions for each: nothing of another org, another connector, a deleted or unindexed record, a connector or group the user is not in, a record outside ``within``
(an empty ``within`` admits nothing, a record without a connector is outside any ``within`` that names connectors); and everything the user is entitled to. A seeded fuzz
test compares the records a user holds a role on with what ``check_access`` opens for the same user.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.services.graph_db.postgres import entity_access

from .test_access import APP, rec
from .test_access import w as _access_world  # noqa: F401
from .test_grants import failing, in_org

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_access_world")


def ref(typ: str, key: str, *connectors: str, **over: object) -> dict:
    return {"type": typ, "id": key, "connectorIds": list(connectors or [APP]), **over}


def done(**kw: object) -> dict:
    return {"indexingStatus": "COMPLETED", **kw}


async def entity_world(w: World, other: str) -> None:
    await w.p.create_named_entities_if_absent([{"id": "E", "orgId": w.org, "kind": "org", "name": "E", "normKey": "e", "aliases": ["E"], "normalizedAliases": ["e"],
                                                "createdAtTimestamp": 1, "updatedAtTimestamp": 1}])
    await w.nodes("records", rec("old", **done(sourceLastModifiedTimestamp=10, recordType="FILE")), rec("new", **done(sourceLastModifiedTimestamp=30, recordType="FILE")),
                  rec("mid", **done(updatedAtTimestamp=20, recordType="MAIL")), rec("kept", hasIndexedCopy=True, sourceLastModifiedTimestamp=5),
                  rec("raw", indexingStatus="QUEUED"), rec("gone", **done(isDeleted=True)), rec("elsewhere", **done(connectorId="app2")),
                  rec("foreign", **done(orgId=other)), rec("noconn", **done(connectorId=None)))
    for r in ("old", "new", "mid", "kept", "raw", "gone", "elsewhere", "foreign", "noconn"):
        await w.edge("mentionsEntity", ("records", r), ("namedEntities", "E"))


def ids(rows: list) -> list[str]:
    return [r["_key"] for r in rows]


# ---- the records of an entity ----------------------------------------------------------------------------------------------------------

async def test_candidates_are_the_live_indexed_records_of_the_org_in_the_refs_connectors_newest_first(w: World) -> None:
    await entity_world(w, "other-org")
    got = await w.p.get_entity_candidate_records([ref("named_entity", "E")], w.org)
    rows = got[("named_entity", "E")]
    assert ids(rows) == ["new", "mid", "old", "kept"] and not rows.capped                       # not unindexed, deleted, in another connector, another org, or without a connector
    assert rows[0]["recordName"] == "new" and rows[0]["sourceLastModifiedTimestamp"] == 30 and rows[3]["hasIndexedCopy"] is True and rows[3]["webUrl"] is None
    assert ids((await w.p.get_entity_candidate_records([ref("named_entity", "E")], w.org, record_types=["FILE"]))[("named_entity", "E")]) == ["new", "old"]
    assert ids((await w.p.get_entity_candidate_records([ref("named_entity", "E")], w.org, limit_per_entity=2, offset=1))[("named_entity", "E")]) == ["mid", "old"]
    assert ids((await w.p.get_entity_candidate_records([ref("named_entity", "E", "app2")], w.org))[("named_entity", "E")]) == ["elsewhere"]
    assert ids((await w.p.get_entity_candidate_records([ref("named_entity", "E", "app2", APP)], w.org, limit_per_entity=1))[("named_entity", "E")]) == ["new"]
    assert (await w.p.get_entity_candidate_records([{"type": "named_entity", "id": "E", "connectorIds": []}], w.org))[("named_entity", "E")] == []           # no connector, no record
    assert (await w.p.get_entity_candidate_records([ref("named_entity", "E")], "no-such-org"))[("named_entity", "E")] == []
    assert await w.p.get_entity_candidate_records([ref("named_entity", "E")], "") == {} and await w.p.get_entity_candidate_records([], w.org) == {}
    assert (await w.p.get_entity_candidate_records([ref("nonsense", "E"), ref("named_entity", "")], w.org)) == {}
    twice = await w.p.get_entity_candidate_records([ref("named_entity", "E"), ref("named_entity", "E", "app2")], w.org)
    assert ids(twice[("named_entity", "E")]) == ["new", "mid", "old", "kept"]                      # the first of a repeated ref


async def test_a_record_found_by_its_own_name_is_shown_unindexed_but_never_deleted_or_foreign(w: World) -> None:
    await entity_world(w, "other-org")
    for key, shown in (("raw", True), ("new", True), ("gone", False), ("foreign", False), ("elsewhere", False), ("nobody", False)):
        got = (await w.p.get_entity_candidate_records([ref("record", key)], w.org))[("record", key)]
        assert (ids(got) == [key]) is shown, key
    assert (await w.p.get_entity_candidate_records([ref("record", "new")], w.org, offset=1))[("record", "new")] == []
    assert (await w.p.get_entity_candidate_records([ref("record", "new")], w.org, record_types=["MAIL"]))[("record", "new")] == []


async def test_the_scan_cap_is_reported(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await entity_world(w, "other-org")
    monkeypatch.setattr(entity_access, "ENTITY_CANDIDATE_SCAN_CAP", 3)
    rows = (await w.p.get_entity_candidate_records([ref("named_entity", "E")], w.org, limit_per_entity=10))[("named_entity", "E")]
    assert rows.capped and len(rows) == 3
    assert (await w.p.get_entity_candidate_records([ref("named_entity", "E")], w.org, offset=50))[("named_entity", "E")].capped          # an empty page past the end still says so
    monkeypatch.setattr(entity_access, "ENTITY_CANDIDATE_SCAN_CAP", 1)
    assert not (await w.p.get_entity_candidate_records([ref("record", "new")], w.org))[("record", "new")].capped                       # a record named by itself is one row, never a scan


async def test_groups_departments_people_and_organizations_link_records_each_their_own_way(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "rgA", "connectorId": APP}, {"_key": "rgB", "connectorId": APP, "orgId": "other-org"})
    await w.nodes("departments", {"_key": "d1", "departmentName": "d"})
    await w.nodes("users", {"_key": "p1", "userId": "up1", "orgId": w.org}, {"_key": "p2", "userId": "up2", "orgId": "other-org"})
    await w.nodes("organizations", {"_key": "ext", "isExternal": True, "parentOrgId": w.org}, {"_key": "ext2", "isExternal": True, "parentOrgId": "other-org"}, {"_key": "int"},
                  {"_key": "ext3", "isExternal": False, "parentOrgId": w.org})
    await w.nodes("records", rec("a", **done()), rec("b", **done()), rec("c", **done()), rec("d", **done()), rec("e", **done()))
    await w.edge("belongsTo", ("records", "a"), ("recordGroups", "rgA"))
    await w.edge("belongsTo", ("records", "b"), ("recordGroups", "rgB"))
    await w.edge("belongsToDepartment", ("records", "c"), ("departments", "d1"))
    await w.edge("entityRelations", ("records", "a"), ("users", "p1"), edgeType="ASSIGNED_TO")
    await w.edge("entityRelations", ("users", "p1"), ("records", "d"), edgeType="MENTIONED_IN")
    await w.edge("entityRelations", ("records", "e"), ("users", "p1"), edgeType="NOT_A_PERSON_EDGE")
    await w.edge("entityRelations", ("records", "b"), ("users", "p2"), edgeType="ASSIGNED_TO")
    await w.edge("entityRelations", ("records", "a"), ("organizations", "ext"), edgeType="ACCOUNT")
    await w.edge("entityRelations", ("records", "b"), ("organizations", "ext2"), edgeType="ACCOUNT")
    await w.edge("entityRelations", ("records", "c"), ("organizations", "int"), edgeType="ACCOUNT")
    await w.edge("entityRelations", ("records", "c"), ("organizations", "ext3"), edgeType="ACCOUNT")
    refs = [ref("record_group", "rgA"), ref("record_group", "rgB"), ref("department", "d1"), ref("person", "p1"), ref("person", "p2"), ref("organization", "ext"),
            ref("organization", "ext2"), ref("organization", "int"), ref("organization", "ext3")]
    got = await w.p.get_entity_candidate_records(refs, w.org)
    assert {k: sorted(ids(v)) for k, v in got.items()} == {("record_group", "rgA"): ["a"], ("record_group", "rgB"): [], ("department", "d1"): ["c"], ("person", "p1"): ["a", "d"],
                                                           ("person", "p2"): [], ("organization", "ext"): ["a"], ("organization", "ext2"): [], ("organization", "int"): [], ("organization", "ext3"): []}
    one = await w.p.get_entity_candidate_records([ref("person", "p1", outTypes=["ASSIGNED_TO"], inTypes=[])], w.org)
    assert ids(one[("person", "p1")]) == ["a"]


# ---- the records a user holds a role on ------------------------------------------------------------------------------------------------

def permitted_ids(rows: dict, key: tuple[str, str] = ("named_entity", "E")) -> list[str]:
    return ids(rows[key])


async def user(w: World, key: str) -> str:
    await w.nodes("users", {"_key": key, "userId": f"uid-{key}"})
    return key


async def test_a_user_gets_the_records_they_hold_a_role_on_and_no_other(w: World) -> None:
    await entity_world(w, "other-org")
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP}, {"_key": "shut", "connectorId": APP})
    await w.nodes("groups", {"_key": "grp"})
    await w.nodes("teams", {"_key": "tm"})
    await w.nodes("organizations", {"_key": "o1"})
    await w.child(("recordGroups", "g"), ("records", "new"))
    await w.child(("recordGroups", "shut"), ("records", "mid"))
    await w.edge("permission", ("users", "u"), ("records", "old"), type="USER", role="READER")             # direct
    await w.edge("permission", ("users", "u"), ("recordGroups", "g"), type="USER", role="READER")          # inherited through the group the record sits in
    await w.member("u", "grp")
    await w.edge("permission", ("groups", "grp"), ("records", "kept"), type="GROUP", role="READER")        # through a group
    await w.edge("permission", ("users", "other"), ("records", "mid"), type="USER", role="READER")         # someone else's
    got = await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "u", app_level_connector_ids=[], limit_per_entity=50)
    assert permitted_ids(got) == ["new", "old", "kept"]                                                       # not mid (another user's), not the unindexed or deleted
    assert got[("named_entity", "E")].window_size == 4 and got[("named_entity", "E")].examined == 4
    got = await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "other", app_level_connector_ids=[], limit_per_entity=50)
    assert permitted_ids(got) == ["mid"]
    got = await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "nobody", app_level_connector_ids=[APP])
    assert got == {("named_entity", "E"): []}                                                                  # a user the graph does not know gets nothing, even in an app-level connector
    assert await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "", app_level_connector_ids=[]) == {}
    assert await w.p.get_permitted_entity_records([ref("named_entity", "E")], "", "u", app_level_connector_ids=[]) == {}
    assert await w.p.get_permitted_entity_records([ref("named_entity", "E")], "other-org", "u", app_level_connector_ids=[], limit_per_entity=50) == {("named_entity", "E"): []}


async def test_an_app_level_connector_admits_its_records_and_only_those(w: World) -> None:
    await entity_world(w, "other-org")
    got = await w.p.get_permitted_entity_records([ref("named_entity", "E", APP, "app2")], w.org, "u", app_level_connector_ids=["app2"], limit_per_entity=50)
    assert permitted_ids(got) == ["elsewhere"]
    got = await w.p.get_permitted_entity_records([ref("named_entity", "E", APP, "app2")], w.org, "u", app_level_connector_ids=[APP, "app2"], limit_per_entity=50)
    assert permitted_ids(got) == ["new", "mid", "old", "kept", "elsewhere"]


async def test_the_walk_stops_at_the_limit_and_the_next_window_starts_after_it(w: World) -> None:
    await entity_world(w, "other-org")
    for r in ("new", "mid", "old", "kept"):
        await w.edge("permission", ("users", "u"), ("records", r), type="USER", role="READER")
    first = (await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "u", app_level_connector_ids=[], limit_per_entity=2))[("named_entity", "E")]
    assert ids(first) == ["new", "mid"] and first.examined == 2 and first.window_size == 4
    second = (await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "u", app_level_connector_ids=[], limit_per_entity=2, offset=first.examined))[("named_entity", "E")]
    assert ids(second) == ["old", "kept"] and second.window_size == 2
    narrow = (await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "u", app_level_connector_ids=[], limit_per_entity=5, window=3))[("named_entity", "E")]
    assert ids(narrow) == ["new", "mid", "old"] and narrow.window_size == 3


async def test_the_limit_and_the_window_are_at_least_one_and_the_walk_stops_at_the_limit(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await entity_world(w, "other-org")
    for r in ("new", "mid", "old", "kept"):
        await w.edge("permission", ("users", "u"), ("records", r), type="USER", role="READER")
    one = (await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "u", app_level_connector_ids=[], limit_per_entity=0, window=0))[("named_entity", "E")]
    assert ids(one) == ["new"] and one.window_size == 1
    asked = []
    real = w.p._role_on

    async def counting(*a: object, **k: object) -> object:
        asked.append(a[-1])
        return await real(*a, **k)
    monkeypatch.setattr(w.p, "_role_on", counting)
    two = (await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "u", app_level_connector_ids=[], limit_per_entity=2))[("named_entity", "E")]
    assert ids(two) == ["new", "mid"] and asked == ["new", "mid"]                                  # no role is read past the limit


async def test_within_bounds_the_candidates_before_the_walk_and_an_empty_within_admits_nothing(w: World) -> None:
    await entity_world(w, "other-org")
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.edge("belongsTo", ("records", "old"), ("recordGroups", "g"))
    for r in ("new", "mid", "old", "kept", "noconn", "elsewhere"):
        await w.edge("permission", ("users", "u"), ("records", r), type="USER", role="READER")
    async def run(within: list | None, apps: list[str] | None = None) -> list[str]:
        refs = [ref("named_entity", "E", APP, "app2")]
        return permitted_ids(await w.p.get_permitted_entity_records(refs, w.org, "u", app_level_connector_ids=[], limit_per_entity=50, within=within))
    assert await run(None) == ["new", "mid", "old", "kept", "elsewhere"]
    assert await run([]) == [] and await run([{"apps": [], "groups": [], "records": []}]) == []                    # nothing named: nothing admitted
    assert await run([{"apps": ["app2"]}]) == ["elsewhere"]
    assert await run([{"records": ["old", "kept"]}]) == ["old", "kept"]
    assert await run([{"groups": ["g"]}]) == ["old"]                                                              # directly in the group
    assert await run([{"apps": [APP]}, {"records": ["mid"]}]) == ["mid"]                                          # every scope must hold
    assert await run([{"apps": [APP]}]) == ["new", "mid", "old", "kept"]                                          # the record with no connector is in no app scope
    assert await run([{"groups": ["nope"], "records": ["nope"], "apps": []}]) == []


async def test_a_failed_read_raises_and_the_right_answer_returns(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await entity_world(w, "other-org")
    await w.edge("permission", ("users", "u"), ("records", "old"), type="USER", role="READER")
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    for call in (w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "u", app_level_connector_ids=[]), w.p.get_entity_candidate_records([ref("named_entity", "E")], w.org),
                 w.p.get_entity_access_context("uid-u", w.org), w.p.get_entity_index_candidate("apps", "m"), w.p.page_entity_index_source("records", APP, None, 5),
                 w.p.stage_state_org_for_virtual_record("v1")):
        with pytest.raises(RuntimeError, match="store down"):
            await call
    monkeypatch.setattr(w.p, "_q", real)
    got = await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, "u", app_level_connector_ids=[], limit_per_entity=50)
    assert permitted_ids(got) == ["old"]


# ---- fuzz: the role walk against the access check --------------------------------------------------------------------------------------

async def random_world(w: World, rng: random.Random) -> list[str]:
    users = [await user(w, f"x{i}") for i in range(4)]
    for u in users:
        await w.edge("userAppRelation", ("users", u), ("apps", APP))
        await w.edge("permission", ("users", u), ("apps", APP), type="USER", role="READER")
    await w.nodes("recordGroups", *({"_key": f"rg{i}", "connectorId": APP} for i in range(4)))
    await w.nodes("groups", {"_key": "g0"}, {"_key": "g1"})
    await w.nodes("teams", {"_key": "t0"})
    await w.nodes("organizations", {"_key": "o0"})
    for i in range(4):
        if i and rng.random() < 0.6:
            await w.child(("recordGroups", f"rg{rng.randrange(i)}"), ("recordGroups", f"rg{i}"), inherits=rng.random() < 0.7)
        else:
            await w.child(("apps", APP), ("recordGroups", f"rg{i}"), inherits=rng.random() < 0.7)
    records = [f"r{i}" for i in range(14)]
    await w.nodes("records", *(rec(r, **done(isDeleted=rng.random() < 0.1)) for r in records))
    for r in records:
        await w.child(("recordGroups", f"rg{rng.randrange(4)}"), ("records", r), inherits=rng.random() < 0.7)
        await w.edge("mentionsEntity", ("records", r), ("namedEntities", "E"))
    for u in users:
        if rng.random() < 0.5:
            await w.member(u, "g0")
        if rng.random() < 0.5:
            await w.member(u, "g1")
        if rng.random() < 0.4:
            await w.member(u, "t0", "teams")
        if rng.random() < 0.4:
            await w.edge("belongsTo", ("users", u), ("organizations", "o0"), entityType="ORGANIZATION")
    targets = [("recordGroups", f"rg{i}") for i in range(4)] + [("records", r) for r in records]
    for src, kind, role in ((("groups", "g0"), "GROUP", "READER"), (("groups", "g1"), "GROUP", "READER"), (("teams", "t0"), "TEAM", "READER"), (("organizations", "o0"), "ORG", "READER")):
        for t in rng.sample(targets, 3):
            await w.edge("permission", src, t, type=kind, role=role)
    for u in users:
        for t in rng.sample(targets, 2):
            await w.edge("permission", ("users", u), t, type="USER", role="READER")
    return records


async def test_the_role_walk_gives_what_the_access_check_opens(w: World) -> None:
    """Over seeded random worlds (every user passes the connector gate, no access rule): the records a user holds a role on are the records ``check_access`` opens."""
    await w.p.create_named_entities_if_absent([{"id": "E", "orgId": w.org, "kind": "org", "name": "E", "normKey": "e", "aliases": ["E"], "normalizedAliases": ["e"],
                                                "createdAtTimestamp": 1, "updatedAtTimestamp": 1}])
    rng = random.Random(20261106)
    records = await random_world(w, rng)
    live = {r["id"] for r in await w.p.get_all_documents("records") if not r.get("isDeleted")}
    opened = 0
    for u in (f"x{i}" for i in range(4)):
        want = await w.allowed(u, *records) & live
        got = set(permitted_ids(await w.p.get_permitted_entity_records([ref("named_entity", "E")], w.org, u, app_level_connector_ids=[], limit_per_entity=100, window=100)))
        assert got == want, (u, sorted(got - want), sorted(want - got))
        opened += len(want)
    assert opened > 4                                      # the worlds open something, and not everything


# ---- what a user can reach -------------------------------------------------------------------------------------------------------------

async def context_world(w: World) -> None:
    await user(w, "c1")
    await w.nodes("apps", {"_key": "conn", "type": "JIRA", "name": "Jira", "permissionModel": "RECORD_LEVEL"}, {"_key": "appl", "type": "SLACK", "name": "Slack", "permissionModel": "APP_LEVEL"},
                  {"_key": "kb", "type": "KB", "name": "KB1"}, {"_key": "kbother", "type": "KB", "name": "KB2", "orgId": "other-org"}, {"_key": "hid", "type": "JIRA", "isHidden": True},
                  {"_key": "nolink", "type": "JIRA"}, {"_key": "src", "type": "DRIVE"})
    await w.nodes("teams", {"_key": "tm"})
    await w.nodes("users", {"_key": "acct", "userId": "uid-acct"})
    await w.edge("userAppRelation", ("users", "c1"), ("apps", "conn"))
    await w.edge("userAppRelation", ("users", "c1"), ("apps", "appl"))
    await w.edge("userAppRelation", ("users", "c1"), ("apps", "hid"))
    await w.member("c1", "tm", "teams")
    await w.edge("permission", ("users", "c1"), ("apps", "kb"), type="USER", role="OWNER")
    await w.edge("permission", ("users", "c1"), ("apps", "kbother"), type="USER", role="OWNER")
    await w.edge("authenticatedAs", ("users", "c1"), ("users", "acct"), connectorId="src")


async def test_the_apps_a_user_reaches(w: World) -> None:
    await context_world(w)
    got = await w.p.get_entity_access_context("uid-c1", w.org)
    assert got["user_key"] == "c1" and {a["id"]: a["type"] for a in got["apps"]} == {"conn": "JIRA", "appl": "SLACK", "kb": "KB", "src": "DRIVE"}      # hidden, other org's and unlinked ones out
    assert next(a for a in got["apps"] if a["id"] == "kb") == {"id": "kb", "name": "KB1", "type": "KB", "permissionModel": None}
    assert {a["id"] for a in (await w.p.get_entity_access_context("uid-c1", w.org, source_ids=["conn", "hid", "nolink"]))["apps"]} == {"conn", "hid"}      # named, but only what they reach
    assert {a["id"] for a in (await w.p.get_entity_access_context("uid-c1", w.org, exclude_app_ids=frozenset({"conn", "kb"})))["apps"]} == {"appl", "src"}
    assert {a["id"] for a in (await w.p.get_entity_access_context("uid-c1", w.org, source_ids=["conn"], exclude_app_ids=frozenset({"conn"})))["apps"]} == set()
    assert await w.p.get_entity_access_context("uid-nobody", w.org) is None
    await w.edge("permission", ("users", "c1"), ("apps", "nolink"), type="USER", role="BOGUS")           # a role no collection carries is no access
    await w.edge("permission", ("teams", "tm"), ("apps", "nolink"), type="TEAM", role="READER")
    assert "nolink" not in {a["id"] for a in (await w.p.get_entity_access_context("uid-c1", w.org))["apps"]}


async def test_a_collection_shared_with_the_users_team_is_reached(w: World) -> None:
    await context_world(w)
    await w.nodes("apps", {"_key": "kbteam", "type": "KB", "name": "KB3"}, {"_key": "kbteam_other", "type": "KB", "name": "KB4", "orgId": "other-org"})
    await w.edge("permission", ("teams", "tm"), ("apps", "kbteam"), type="TEAM", role="READER")
    await w.edge("permission", ("teams", "tm"), ("apps", "kbteam_other"), type="TEAM", role="READER")
    await w.edge("permission", ("users", "c1"), ("apps", "nolink"), type="USER", role="BOGUS")
    ids_ = {a["id"] for a in (await w.p.get_entity_access_context("uid-c1", w.org))["apps"]}
    assert "kbteam" in ids_ and "kbteam_other" not in ids_ and "nolink" not in ids_ and "kbother" not in ids_


async def test_the_record_groups_a_user_reaches_by_each_grant_and_what_inherits_from_them(w: World) -> None:
    await context_world(w)
    await w.nodes("groups", {"_key": "grp"})
    await w.nodes("roles", {"_key": "rol", "connectorId": "conn"})
    await w.nodes("organizations", {"_key": "o1"})
    names = ["direct", "viagroup", "viarole", "viateam", "viaorg", "other", "gone", "appl", "kbgroup", "foreign", "hidden_parent", "child", "grandchild", "child_of_hidden", "deleted_child",
             "wrongapp", "srcgrp", "srcother"]
    conn = {"gone": "conn", "appl": "appl", "kbgroup": "kb", "wrongapp": "nolink", "srcother": "conn", "srcgrp": "src"}
    await w.nodes("recordGroups", *({"_key": n, "connectorId": conn.get(n, "conn"), **({"isDeleted": True} if n in ("gone", "deleted_child") else {}),
                                     **({"orgId": "other-org"} if n == "foreign" else {}), **({"hideChildren": True} if n == "hidden_parent" else {})} for n in names))
    await w.edge("permission", ("users", "c1"), ("recordGroups", "direct"), type="USER", role="READER")
    await w.member("c1", "grp")
    await w.edge("permission", ("groups", "grp"), ("recordGroups", "viagroup"), type="GROUP", role="READER")
    await w.member("c1", "rol", "roles")
    await w.edge("permission", ("roles", "rol"), ("recordGroups", "viarole"), type="ROLE", role="READER")
    await w.edge("permission", ("teams", "tm"), ("recordGroups", "viateam"), type="TEAM", role="READER")
    await w.edge("belongsTo", ("users", "c1"), ("organizations", "o1"), entityType="ORGANIZATION")
    await w.edge("permission", ("organizations", "o1"), ("recordGroups", "viaorg"), type="ORG", role="READER")
    await w.edge("permission", ("users", "other"), ("recordGroups", "other"), type="USER", role="READER")
    for g in ("gone", "appl", "kbgroup", "foreign", "hidden_parent", "wrongapp"):
        await w.edge("permission", ("users", "c1"), ("recordGroups", g), type="USER", role="READER")
    await w.edge("permission", ("users", "acct"), ("recordGroups", "srcgrp"), type="USER", role="READER")          # the source account's grant, in its connector
    await w.edge("permission", ("users", "acct"), ("recordGroups", "srcother"), type="USER", role="READER")        # ... not in another connector
    for parent, child_ in (("direct", "child"), ("child", "grandchild"), ("hidden_parent", "child_of_hidden"), ("direct", "deleted_child")):
        await w.edge("inheritPermissions", ("recordGroups", child_), ("recordGroups", parent))
    await w.nodes("recordGroups", *({"_key": n, "connectorId": "conn"} for n in ("g_wrongmember", "g_wrongteam", "g_wrongorg", "g_wrongorgmember", "dup")))
    await w.nodes("groups", {"_key": "grp2"})
    await w.nodes("organizations", {"_key": "o2"})
    await w.nodes("records", {"_key": "dup", "connectorId": "conn", "recordName": "dup"})
    await w.edge("permission", ("users", "c1"), ("groups", "grp2"), type="GROUP", role="READER")           # not a membership
    await w.edge("permission", ("groups", "grp2"), ("recordGroups", "g_wrongmember"), type="GROUP", role="READER")
    await w.edge("permission", ("teams", "tm"), ("recordGroups", "g_wrongteam"), type="USER", role="READER")        # a team's grant has the team type
    await w.edge("belongsTo", ("users", "c1"), ("organizations", "o2"), entityType="KB")
    await w.edge("permission", ("organizations", "o2"), ("recordGroups", "g_wrongorgmember"), type="ORG", role="READER")   # the user belongs to o2 as something else
    await w.edge("permission", ("organizations", "o1"), ("recordGroups", "g_wrongorg"), type="USER", role="READER")        # an org's grant has the org type
    await w.edge("inheritPermissions", ("records", "dup"), ("recordGroups", "direct"))                    # a record of that key inherits; the group of that key does not
    await w.nodes("apps", {"_key": "teamapp", "type": "JIRA", "name": "T"})
    await w.edge("userAppRelation", ("teams", "tm"), ("apps", "teamapp"))
    got = await w.p.get_entity_access_context("uid-c1", w.org)
    assert "teamapp" in {a["id"] for a in got["apps"]}                                                    # an app linked to the user's team
    assert got["record_group_ids"] == sorted(["direct", "viagroup", "viarole", "viateam", "viaorg", "hidden_parent", "child", "grandchild", "srcgrp"])
    only = await w.p.get_entity_access_context("uid-c1", w.org, source_ids=["conn"])
    assert "srcgrp" not in only["record_group_ids"] and "direct" in only["record_group_ids"]                          # the account's connector is not among the apps asked for
    ex = await w.p.get_entity_access_context("uid-c1", w.org, exclude_app_ids=frozenset({"conn"}))
    assert ex["record_group_ids"] == ["srcgrp"]                                                                   # its record groups drop out with the app
    assert (await w.p.get_entity_access_context("uid-other", w.org)) is None


async def test_the_reachable_record_groups_are_ones_the_access_check_opens(w: World) -> None:
    """Over seeded random worlds: a record group listed as reached is one ``check_access`` opens for the user (no over-admit)."""
    await w.p.create_named_entities_if_absent([{"id": "E", "orgId": w.org, "kind": "org", "name": "E", "normKey": "e", "aliases": ["E"], "normalizedAliases": ["e"],
                                                "createdAtTimestamp": 1, "updatedAtTimestamp": 1}])
    await random_world(w, random.Random(77))
    reached = 0
    for i in range(4):
        got = await w.p.get_entity_access_context(f"uid-x{i}", w.org)
        groups = set(got["record_group_ids"])
        allowed = await w.allowed(f"x{i}", *(f"rg{j}" for j in range(4)))
        assert groups <= allowed, (i, sorted(groups - allowed))
        reached += len(groups)
    assert reached > 0


# ---- the entity index ------------------------------------------------------------------------------------------------------------------

async def test_the_index_candidate_is_an_app_or_organization_not_at_the_marker(w: World) -> None:
    await w.nodes("apps", {"_key": "a1", "type": "JIRA", "entityIndexState": "m1"}, {"_key": "a2", "type": "JIRA", "status": "DELETING"}, {"_key": "a3", "type": "JIRA", "entityIndexState": "old"})
    await w.nodes("organizations", {"_key": "o1", "entityIndexState": "m1", "entityIndexSweptAt": 100}, {"_key": "o2", "isExternal": True})
    assert (await w.p.get_entity_index_candidate("apps", "m1"))["_key"] == "a3"                  # a1 is at the marker, a2 is being deleted
    assert await w.p.get_entity_index_candidate("apps", "old") is not None and (await w.p.get_entity_index_candidate("apps", "old"))["_key"] == "a1"
    assert await w.p.get_entity_index_candidate("organizations", "m1") is None
    assert (await w.p.get_entity_index_candidate("organizations", "m1", sweep_before=200))["_key"] == "o1"      # swept before the cutoff
    assert await w.p.get_entity_index_candidate("organizations", "m1", sweep_before=100) is None
    assert await w.p.get_entity_index_candidate("organizations", "m2") is not None and (await w.p.get_entity_index_candidate("organizations", "m2"))["_key"] == "o1"
    await w.nodes("organizations", {"_key": "o3", "entityIndexState": "m1"})
    assert (await w.p.get_entity_index_candidate("organizations", "m1", sweep_before=200))["_key"] == "o1" and (await w.p.get_entity_index_candidate("organizations", "m1", sweep_before=50))["_key"] == "o3"
    with pytest.raises(ValueError):
        await w.p.get_entity_index_candidate("records", "m1")


async def test_the_index_candidate_is_found_in_any_org_under_a_system_scope(pg_provider, org_ctx) -> None:
    import uuid
    a, b, tag = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24], uuid.uuid4().hex[:6]
    for org in (a, b):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": "o", "isActive": True, "orgId": org}], "organizations")
    with org_ctx(b):
        await pg_provider.batch_upsert_nodes([{"_key": f"app-{tag}", "type": "JIRA", "orgId": b, "entityIndexState": "old"}], "apps")
    token = org_context.push(None)
    try:
        with org_context.system("test.index"):
            found = await pg_provider.get_entity_index_candidate("apps", f"new-{tag}")
    finally:
        org_context.pop(token)
    assert found is not None and found["orgId"]                    # whichever org holds one that is not at the marker; this database holds others
    with org_ctx(b):
        assert (await pg_provider.get_entity_index_candidate("apps", f"new-{tag}"))["_key"] == f"app-{tag}"
    with org_ctx(a):
        assert await pg_provider.get_entity_index_candidate("apps", f"new-{tag}") is None


async def test_the_index_source_pages_a_scope_in_key_order(w: World) -> None:
    await w.nodes("records", rec("r1", recordName="one", **done(isDeleted=False)), rec("r2", recordName="two", isInternal=True), rec("r3", recordName="three"), rec("r4", connectorId="app2"))
    first = await w.p.page_entity_index_source("records", APP, None, 2)
    assert [r["_key"] for r in first] == ["r1", "r2"] and first[0]["name"] == "one" and first[0]["indexingStatus"] == "COMPLETED" and first[1]["isInternal"] is True
    assert [r["_key"] for r in await w.p.page_entity_index_source("records", APP, "r2", 5)] == ["r3"]
    assert await w.p.page_entity_index_source("records", "", None, 5) == [] and await w.p.page_entity_index_source("records", "nobody", None, 5) == []
    await w.nodes("records", {"_key": "noscope", "connectorId": ""})
    assert await w.p.page_entity_index_source("records", "", None, 5) == []                          # no scope names no rows, not the rows with an empty one
    assert len(await w.p.page_entity_index_source("records", APP, None, 0)) == 1                       # a page is at least one
    with pytest.raises(ValueError):
        await w.p.page_entity_index_source("nonsense", "", None, 5)
    await w.nodes("departments", {"_key": "d1", "departmentName": "Eng", "orgId": w.org}, {"_key": "d2", "departmentName": "Global", "orgId": None}, {"_key": "d3", "departmentName": "X", "orgId": "other-org"})
    assert [r["_key"] for r in await w.p.page_entity_index_source("departments", w.org, None, 10)] == ["d1", "d2"]          # a department seeded without an org is global
    await w.nodes("categories", {"_key": "c1", "name": "A", "normalizedName": "a", "aliases": ["x"]}, {"_key": "c2", "name": "B"}, {"_key": "c3", "name": "C", "normalizedName": "c", "mergedInto": "c1"})
    for k in ("c1", "c2", "c3"):
        await w.nodes("categories", {"_key": k, "orgId": w.org})
    got = await w.p.page_entity_index_source("categories", w.org, None, 10)
    assert [r["_key"] for r in got] == ["c1"] and got[0]["aliases"] == ["x"]                                  # canonical only: named, not merged
    await w.nodes("organizations", {"_key": "e1", "name": "Ext", "isExternal": True, "parentOrgId": w.org}, {"_key": "e2", "name": "Not", "isExternal": False, "parentOrgId": w.org})
    assert [r["_key"] for r in await w.p.page_entity_index_source("organizations", w.org, None, 10)] == ["e1"]
    await w.nodes("users", {"_key": "n1", "fullName": "  ", "email": "n1@x.io", "orgId": w.org}, {"_key": "n2", "fullName": "Ann", "email": "a@x.io", "orgId": w.org})
    assert {r["_key"]: r["name"] for r in await w.p.page_entity_index_source("users", w.org, None, 10) if r["_key"] in ("n1", "n2")} == {"n1": "n1@x.io", "n2": "Ann"}
    with in_org("some-other-org"):
        assert await w.p.page_entity_index_source("records", APP, None, 5) == []


async def test_the_stage_state_names_its_org(w: World) -> None:
    await w.nodes("stageStates", {"_key": "s1", "virtualRecordId": "v1"}, {"_key": "s2", "virtualRecordId": "v2", "orgId": "other-org"}, {"_key": "s3", "virtualRecordId": "v3", "orgId": None})
    assert await w.p.stage_state_org_for_virtual_record("v1") == w.org
    assert await w.p.stage_state_org_for_virtual_record("v2") is None                    # a stage state stored here that names another org is no answer
    assert await w.p.stage_state_org_for_virtual_record("v3") is None and await w.p.stage_state_org_for_virtual_record("nope") is None
    with in_org("some-other-org"):
        assert await w.p.stage_state_org_for_virtual_record("v1") is None


async def test_no_org_is_an_error_not_an_empty_answer(pg_provider) -> None:
    token = org_context.push(None)
    try:
        for call in (pg_provider.get_entity_candidate_records([ref("named_entity", "E")], "o"), pg_provider.get_permitted_entity_records([ref("named_entity", "E")], "o", "u", app_level_connector_ids=[]),
                     pg_provider.get_entity_access_context("uid", "o"), pg_provider.page_entity_index_source("records", "a", None, 1)):
            with pytest.raises(org_context.MissingOrgContext):
                await call
    finally:
        org_context.pop(token)
