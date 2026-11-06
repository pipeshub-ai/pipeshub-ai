"""Knowledge bases on PostgreSQL: the roles on one, the lists of them, the folder checks and the upload validation, against a real database.

The answers are those of ``Neo4jProvider`` (the same shapes, errors and empty cases). Access is asked in both directions: nobody gets a collection
they hold no role on (another user, another org, a revoked grant, a hidden collection), and everybody gets the ones they do (directly or through a team).

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes
from app.core import org_context
from app.core.org_context import MissingOrgContext, OrgContext, bind
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.postgres.kb import KbMixin

from .test_access import APP, World
from .test_identity import _org
from .test_identity import w as _identity_world  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration
FOLDER = MimeTypes.FOLDER.value
UPLOAD = "UPLOAD"


@contextlib.contextmanager
def other_org(org: str | None = None) -> Iterator[None]:
    """Calls inside run as a request of another org would (a bound org cannot be nested in another, so the bound one is set aside)."""
    token = org_context._ctx.set(None)
    try:
        with bind(OrgContext(org or _org(), None, "job", "job:test", "t")):
            yield
    finally:
        org_context._ctx.reset(token)


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_identity_world")


async def kb(w: World, key: str = "kb1", **doc: object) -> None:
    await w.nodes("apps", {"_key": key, "type": "KB", "name": key.upper(), "createdBy": "me", "createdAtTimestamp": 1, "updatedAtTimestamp": 2, **doc})


async def users(w: World, *keys: str, **doc: object) -> None:
    await w.nodes("users", *({"_key": k, "userId": f"uid-{k}", "isActive": True, **doc} for k in keys))


async def grant(w: World, who: str, role: str, key: str = "kb1") -> None:
    await w.edge("permission", ("users", who), ("apps", key), type="USER", role=role)


async def folder(w: World, key: str, kb_key: str = "kb1", parent: str | None = None, **doc: object) -> None:
    await w.nodes("records", {"_key": key, "recordName": key, "mimeType": FOLDER, "connectorId": kb_key, "origin": UPLOAD, "createdAtTimestamp": 5, "webUrl": f"/{key}", **doc})
    await w.nodes("files", {"_key": key, "isFile": False})
    await w.edge("isOfType", ("records", key), ("files", key))
    await w.edge("belongsTo", ("records", key), ("apps", kb_key))
    if parent:
        await w.edge("nodeRelations", ("records", parent), ("records", key), relationshipType="PARENT_CHILD")


async def team(w: World, who: str, tm: str, *, grant_role: str | None, member: str | None, key: str = "kb1") -> None:
    await w.nodes("teams", {"_key": tm})
    await w.edge("permission", ("users", who), ("teams", tm), type="USER", **({"role": member} if member else {}))
    await w.edge("permission", ("teams", tm), ("apps", key), type="TEAM", **({"role": grant_role} if grant_role is not None else {}))


async def listed(w: World, user: str, **kw: object) -> dict[str, str]:
    kbs, _, _ = await w.p.list_user_knowledge_bases(user, w.org, 0, 100, **kw)
    return {k["id"]: k["userRole"] for k in kbs}


# ---- existence and the grants ----------------------------------------------------------------------------------------------------------

async def test_kb_exists_is_true_only_for_a_collection_of_this_org(w: World) -> None:
    await kb(w)
    assert await w.p.kb_exists("kb1") is True
    assert await w.p.kb_exists(APP) is False                 # an app that is not a collection
    assert await w.p.kb_exists("nope") is False
    with other_org():
        assert await w.p.kb_exists("kb1") is False           # another org's view of the id


async def test_grants_are_created_read_updated_counted_listed_and_removed(w: World) -> None:
    await kb(w)
    await users(w, "me", "a", "b", "gone", isActive=True)
    await w.nodes("users", {"_key": "dead", "userId": "uid-dead", "isActive": False})
    await w.nodes("teams", {"_key": "tm", "name": "Team"})
    r = await w.p.create_kb_permissions("kb1", "me", ["me", "a", "dead"], ["tm"], "OWNER")
    assert r == {"success": True, "grantedCount": 4, "grantedUsers": ["me", "a", "dead"], "grantedTeams": ["tm"], "role": "OWNER", "kbId": "kb1", "details": {}}
    assert await w.p.get_kb_permissions("kb1", ["me", "a", "b"], ["tm", "nope"]) == {"users": {"me": "OWNER", "a": "OWNER"}, "teams": {"tm": None}}
    assert await w.p.get_kb_permissions("kb1") == {"users": {}, "teams": {}}
    assert await w.p.count_kb_owners("kb1") == 3                       # a deleted user's grant is still an owner grant; teams never count
    ls = {p["id"]: p for p in await w.p.list_kb_permissions("kb1")}
    assert set(ls) == {"me", "a", "tm"}                                 # the inactive user is not shown as having access
    assert ls["a"]["role"] == "OWNER" and ls["a"]["userId"] == "uid-a" and ls["a"]["type"] == "USER"
    assert ls["tm"] == {"id": "tm", "type": "TEAM", "createdAtTimestamp": ls["tm"]["createdAtTimestamp"], "updatedAtTimestamp": ls["tm"]["updatedAtTimestamp"],
                        "userId": None, "email": None, "name": "Team", "role": None}

    up = await w.p.update_kb_permission("kb1", "me", ["a", "b", "tm"], ["tm"], "READER")       # b holds no grant: skipped; teams hold no role
    assert up["success"] and up["updated_users"] == 1 and up["updated_teams"] == 0 and up["updated_permissions"] == 1
    assert up["updates_detail"] == {"users": {"a": {"old_role": "OWNER", "new_role": "READER"}}, "teams": {}}
    assert (await w.p.get_kb_permissions("kb1", ["a"]))["users"] == {"a": "READER"}
    assert await w.p.update_kb_permission("kb1", "me", [], [], "READER") == {"success": False, "reason": "No users or teams provided", "code": "400"}
    bad = await w.p.update_kb_permission("kb1", "me", ["a"], [], "ADMIN")
    assert bad["code"] == "400" and "OWNER, WRITER, READER" in bad["reason"]
    assert (await w.p.get_kb_permissions("kb1", ["a"]))["users"] == {"a": "READER"}

    assert await w.p.remove_kb_permission("kb1", ["a", "b"], ["tm"]) == {"success": True}
    assert await w.p.get_kb_permissions("kb1", ["me", "a"], ["tm"]) == {"users": {"me": "OWNER"}, "teams": {}}
    assert await w.p.count_kb_owners("kb1") == 2


async def test_grants_never_reach_a_record_that_is_not_a_collection_or_another_org(w: World) -> None:
    await kb(w)
    await users(w, "me", "a")
    await grant(w, "a", "OWNER")
    await w.edge("permission", ("users", "a"), ("apps", APP), type="USER", role="OWNER")      # a grant on a connector is not a collection grant
    assert await w.p.count_kb_owners(APP) == 0
    assert await w.p.get_kb_permissions(APP, ["a"]) == {"users": {}, "teams": {}}
    assert await w.p.update_kb_permission(APP, "me", ["a"], [], "READER") | {"updates_detail": 0} == {"success": True, "kb_id": APP, "new_role": "READER", "updated_permissions": 0,
                                                                                                    "updated_users": 0, "updated_teams": 0, "updates_detail": 0}
    await w.p.remove_kb_permission(APP, ["a"], [])
    assert (await w.p.get_edge("a", "users", APP, "apps", "permission"))["role"] == "OWNER"
    with other_org():                                                                   # another org changes and sees nothing
        assert await w.p.count_kb_owners("kb1") == 0
        assert (await w.p.update_kb_permission("kb1", "me", ["a"], [], "READER"))["updated_users"] == 0
        await w.p.remove_kb_permission("kb1", ["a"], [])
        assert await w.p.list_kb_permissions("kb1") == []
    assert await w.p.count_kb_owners("kb1") == 1
    assert (await w.p.get_kb_permissions("kb1", ["a"]))["users"] == {"a": "OWNER"}


# ---- the collections a user may see ----------------------------------------------------------------------------------------------------

async def test_a_user_lists_the_collections_they_hold_a_role_on_and_no_others(w: World) -> None:
    await users(w, "me", "other")
    for k in ("direct", "viaTeam", "mixed", "nogrant", "hidden", "retired", "weird", "wrongorg"):
        await kb(w, k, **({"isHidden": True} if k == "hidden" else {}), **({"orgId": "someone-else"} if k == "wrongorg" else {"orgId": w.org}))
    await grant(w, "me", "WRITER", "direct")
    await grant(w, "me", "COMMENTER", "retired")                                     # a retired role reads as READER
    await grant(w, "me", "ADMIN", "weird")                                           # not a collection role: no access
    await grant(w, "me", "OWNER", "hidden")
    await grant(w, "me", "OWNER", "wrongorg")
    await team(w, "me", "t1", grant_role="OWNER", member="READER", key="viaTeam")    # a team never makes an owner
    await team(w, "me", "t2", grant_role=None, member="READER", key="mixed")
    await grant(w, "me", "WRITER", "mixed")                                          # the higher of the direct and the team grant
    await grant(w, "other", "OWNER", "nogrant")
    assert await listed(w, "me") == {"direct": "WRITER", "viaTeam": "WRITER", "mixed": "WRITER", "retired": "READER"}
    assert await listed(w, "other") == {"nogrant": "OWNER"}
    assert await listed(w, "nobody") == {}
    with other_org():
        assert await listed(w, "me") == {}
    assert (await w.p.list_user_knowledge_bases("me", _org(), 0, 10))[:2] == ([], 0)      # another org's id is nothing of this org's
    # revoked: the grant goes, the collection goes; a team removed from the collection goes with it
    await w.p.remove_kb_permission("direct", ["me"], [])
    await w.p.remove_kb_permission("viaTeam", [], ["t1"])
    assert await listed(w, "me") == {"mixed": "WRITER", "retired": "READER"}
    # ... and a grant made again is seen again
    await w.p.create_kb_permissions("direct", "other", ["me"], [], "READER")
    assert (await listed(w, "me"))["direct"] == "READER"


async def test_the_list_pages_searches_sorts_filters_and_names_the_folders(w: World) -> None:
    await users(w, "me")
    for i, (name, role) in enumerate([("beta", "OWNER"), ("Alpha", "READER"), ("alphabet", "WRITER"), (None, "READER")]):
        await kb(w, f"k{i}", orgId=w.org, createdAtTimestamp=10 - i, updatedAtTimestamp=i, name=name)
        await grant(w, "me", role, f"k{i}")
    await w.nodes("apps", {"_key": "k1", "type": "KB", "name": "Alpha"})
    await folder(w, "f1", "k0")
    await folder(w, "f2", "k0", "f1")
    await w.nodes("records", {"_key": "plain", "recordName": "plain", "mimeType": "text/plain"})
    await w.edge("belongsTo", ("records", "plain"), ("apps", "k0"))

    async def page(**kw: object) -> tuple[list[str], int, dict]:
        kbs, total, filters = await w.p.list_user_knowledge_bases("me", w.org, kw.pop("skip", 0), kw.pop("limit", 10), **kw)
        return [k["id"] for k in kbs], total, filters

    ids, total, filters = await page()
    assert ids == ["k1", "k2", "k0", "k3"] and total == 4                               # by name, codepoint order ("Alpha" < "alphabet" < "beta"; no name last)
    assert filters == {"permissions": ["OWNER", "READER", "WRITER"], "sortFields": ["name", "createdAtTimestamp", "updatedAtTimestamp", "userRole"], "sortOrders": ["asc", "desc"]}
    assert (await page(sort_order="desc"))[0] == ["k3", "k0", "k2", "k1"]
    assert (await page(sort_by="createdAtTimestamp"))[0] == ["k3", "k2", "k1", "k0"]
    assert (await page(sort_by="updatedAtTimestamp", sort_order="desc"))[0] == ["k3", "k2", "k1", "k0"]
    assert (await page(sort_by="userRole"))[0] == ["k0", "k1", "k3", "k2"]
    assert (await page(sort_by="bogus"))[0] == ["k1", "k2", "k0", "k3"]                 # an unknown field sorts by name
    assert await page(skip=1, limit=2) == (["k2", "k0"], 4, filters)                    # the total ignores the page
    assert await page(skip=9) == ([], 4, filters)                                       # past the end: no rows, the same total
    assert (await page(search="ALPHA"))[:2] == (["k1", "k2"], 2)                        # case-insensitive substring; a collection without a name never matches
    assert (await page(permissions=["READER"]))[:2] == (["k1", "k3"], 2)
    assert (await page(permissions=["READER"], search="alpha"))[:2] == (["k1"], 1)
    assert (await page(search="none"))[:2] == ([], 0) and (await page(search="none"))[2]["permissions"] == ["OWNER", "READER", "WRITER"]
    kbs, _, _ = await w.p.list_user_knowledge_bases("me", w.org, 0, 10)
    by = {k["id"]: k for k in kbs}
    assert by["k3"]["name"] == "Untitled" and by["k0"]["userRole"] == "OWNER"
    assert by["k0"]["folders"] == [{"id": "f1", "name": "f1", "createdAtTimestamp": 5, "webUrl": "/f1"}, {"id": "f2", "name": "f2", "createdAtTimestamp": 5, "webUrl": "/f2"}]
    assert by["k1"]["folders"] == []
    assert set(by["k0"]) == {"id", "name", "description", "createdAtTimestamp", "updatedAtTimestamp", "createdBy", "userRole", "status", "folders"}


async def test_a_failed_list_raises_and_the_next_call_answers(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await users(w, "me")
    await kb(w, orgId=w.org)
    await grant(w, "me", "READER")
    real = w.p._q

    async def boom(*a: object, **k: object) -> None:
        raise ConnectionError("down")
    monkeypatch.setattr(w.p, "_q", boom)
    with pytest.raises(ConnectionError):                          # an empty list would read as "you have no collections"
        await w.p.list_user_knowledge_bases("me", w.org, 0, 10)
    monkeypatch.setattr(w.p, "_q", real)
    assert await listed(w, "me") == {"kb1": "READER"}


async def test_a_grant_to_a_collection_user_or_team_that_does_not_exist_is_not_written_and_gives_nothing_later(w: World) -> None:
    await users(w, "real")
    await kb(w, orgId=w.org)
    await w.nodes("teams", {"_key": "tm"})
    r = await w.p.create_kb_permissions("ghostkb", "real", ["real", "ghostuser"], ["tm"], "OWNER")
    assert (r["success"], r["grantedCount"], r["grantedUsers"], r["grantedTeams"]) == (True, 0, [], [])
    r = await w.p.create_kb_permissions("kb1", "real", ["real", "ghostuser"], ["tm", "ghostteam"], "OWNER")
    assert (r["grantedCount"], r["grantedUsers"], r["grantedTeams"]) == (2, ["real"], ["tm"])
    assert await w.p.get_edge("ghostuser", "users", "kb1", "apps", "permission") is None and await w.p.get_edge("real", "users", "ghostkb", "apps", "permission") is None
    r = await w.p.create_kb_permissions(APP, "real", ["real"], [], "OWNER")                    # an app that is not a collection
    assert r["grantedCount"] == 0
    await kb(w, "ghostkb", orgId=w.org)                                                         # the ids appear later: no access comes with them
    await users(w, "ghostuser")
    assert await w.p.get_user_kb_permission("ghostkb", "real") is None and await w.p.get_user_kb_permission("kb1", "ghostuser") is None
    assert await listed(w, "ghostuser") == {} and await w.p.count_kb_owners("ghostkb") == 0
    with other_org():
        assert (await w.p.create_kb_permissions("kb1", "real", ["real"], [], "OWNER"))["grantedCount"] == 0      # the ids of another org are not these


async def test_a_collection_with_a_non_boolean_hidden_flag_still_lists_and_only_a_true_one_hides(w: World) -> None:
    await users(w, "me")
    for key, flag in (("odd", "maybe"), ("no", False), ("yes", True), ("str", "true"), ("none", None)):
        await kb(w, key, orgId=w.org, isHidden=flag)
        await grant(w, "me", "READER", key)
    got = set(await listed(w, "me"))
    assert {"odd", "no", "none"} <= got and "yes" not in got


async def test_a_folder_stored_with_two_edges_to_its_collection_is_listed_once(w: World, pg_db) -> None:
    await users(w, "me")
    await kb(w, orgId=w.org)
    await grant(w, "me", "READER")
    await folder(w, "f1")

    async def dup(tx) -> None:
        await tx.execute("INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc) VALUES ($1, 'belongsTo', 'records', 'f1', 'apps', 'kb1', 'dup', '{}'::jsonb)", w.org)
    await pg_db.run(dup, write=True)
    kbs, _, _ = await w.p.list_user_knowledge_bases("me", w.org, 0, 10)
    assert [f["id"] for f in kbs[0]["folders"]] == ["f1"]


async def test_the_role_through_teams_is_the_highest_and_a_bad_stored_role_never_falls_back_to_the_members(w: World) -> None:
    await users(w, "me")
    for key in ("two", "bad", "retired", "capped", "blank", "badmember"):
        await kb(w, key, orgId=w.org)
    await team(w, "me", "ta", grant_role="READER", member="OWNER", key="two")
    await w.nodes("teams", {"_key": "tb"})
    await w.edge("permission", ("users", "me"), ("teams", "tb"), type="USER", role="READER")
    await w.edge("permission", ("teams", "tb"), ("apps", "two"), type="TEAM", role="WRITER")
    await team(w, "me", "tc", grant_role="EDITOR", member="OWNER", key="bad")                  # an unknown grant role is no grant: not the member's OWNER
    await team(w, "me", "td", grant_role=None, member="COMMENTER", key="retired")                # a roleless grant, a retired member role: READER
    await team(w, "me", "te", grant_role=None, member="OWNER", key="capped")                    # ... an OWNER member: capped at WRITER
    await team(w, "me", "tf", grant_role="", member="WRITER", key="blank")
    await team(w, "me", "tg", grant_role=None, member="ADMIN", key="badmember")                  # a roleless grant, an unknown member role: nothing
    assert await listed(w, "me") == {"two": "WRITER", "retired": "READER", "capped": "WRITER", "blank": "WRITER"}


async def test_the_same_ids_in_two_orgs_never_answer_for_each_other(w: World, pg_db) -> None:
    other = _org()
    await users(w, "me", "x")
    await kb(w, orgId=w.org)
    await grant(w, "me", "READER")
    await grant(w, "x", "OWNER")
    await w.nodes("teams", {"_key": "tm"})
    try:
        with other_org(other):
            await w.nodes("users", {"_key": "me", "userId": "uid-me", "isActive": True}, {"_key": "x", "userId": "uid-x", "isActive": True})
            await w.nodes("apps", {"_key": "kb1", "type": "KB", "name": "THEIRS", "orgId": other})
            await w.nodes("teams", {"_key": "tm"})
            await w.edge("permission", ("users", "me"), ("apps", "kb1"), type="USER", role="OWNER")
            await w.edge("permission", ("users", "x"), ("teams", "tm"), type="USER", role="OWNER")
            await w.edge("permission", ("teams", "tm"), ("apps", "kb1"), type="TEAM", role="OWNER")
            theirs = await w.p.list_user_knowledge_bases("me", other, 0, 10)
            assert [(k["name"], k["userRole"]) for k in theirs[0]] == [("THEIRS", "OWNER")]
            assert await w.p.get_kb_permissions("kb1", ["me", "x"], ["tm"]) == {"users": {"me": "OWNER"}, "teams": {"tm": None}}
            assert await w.p.count_kb_owners("kb1") == 1 and await w.p.get_user_kb_permission("kb1", "x") == "WRITER"
            await w.p.remove_kb_permission("kb1", ["me"], ["tm"])
        assert await listed(w, "me") == {"kb1": "READER"} and await w.p.get_user_kb_permission("kb1", "x") == "OWNER"
        assert await w.p.get_kb_permissions("kb1", ["me", "x"], ["tm"]) == {"users": {"me": "READER", "x": "OWNER"}, "teams": {}}
        assert await w.p.count_kb_owners("kb1") == 1
        assert [k["name"] for k in (await w.p.list_user_knowledge_bases("me", w.org, 0, 10))[0]] == ["KB1"]
        with other_org(other):
            assert await listed(w, "me") == {}                                                  # removed there, and nothing of the other org came from here
    finally:
        with other_org(other):
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", other), write=True)
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", other), write=True)


async def test_removing_a_grant_leaves_the_grants_of_others_alone(w: World) -> None:
    await users(w, "a", "b", "c")
    await kb(w, orgId=w.org)
    await kb(w, "kb2", orgId=w.org)
    await w.nodes("teams", {"_key": "t1"}, {"_key": "t2"})
    await w.p.create_kb_permissions("kb1", "a", ["a", "b"], ["t1", "t2"], "WRITER")
    await w.p.create_kb_permissions("kb2", "a", ["a"], ["t1"], "WRITER")
    await w.p.remove_kb_permission("kb1", ["a", "nobody"], ["t1"])
    assert await w.p.get_kb_permissions("kb1", ["a", "b", "c"], ["t1", "t2"]) == {"users": {"b": "WRITER"}, "teams": {"t2": None}}
    assert await w.p.get_kb_permissions("kb2", ["a"], ["t1"]) == {"users": {"a": "WRITER"}, "teams": {"t1": None}}      # the same ids on another collection
    assert await w.p.remove_kb_permission("kb1", [], []) == {"success": True}
    assert (await w.p.get_kb_permissions("kb1", ["b"]))["users"] == {"b": "WRITER"}


# ---- folders ------------------------------------------------------------------------------------------------------------------------------

async def test_folder_checks_know_membership_trash_names_depth_and_height(w: World) -> None:
    await kb(w)
    await kb(w, "kb2")
    await folder(w, "top")
    await folder(w, "mid", parent="top")
    await folder(w, "leaf", parent="mid")
    await folder(w, "gone", isDeleted=True)
    await folder(w, "elsewhere", "kb2")
    await w.nodes("records", {"_key": "file", "recordName": "file", "mimeType": "text/plain"})
    await w.edge("belongsTo", ("records", "file"), ("apps", "kb1"))
    await w.edge("nodeRelations", ("records", "mid"), ("records", "file"), relationshipType="PARENT_CHILD")
    await w.nodes("files", {"_key": "file", "isFile": True})
    await w.edge("isOfType", ("records", "file"), ("files", "file"))

    v = w.p.validate_folder_in_kb
    assert [await v("kb1", f) for f in ("top", "gone", "elsewhere", "file", "nope")] == [True, False, False, False, False]
    assert await v("kb1", "gone", visibility=RecordVisibility.ALL) and await v("kb1", "gone", visibility=RecordVisibility.DELETED)
    assert not await v("kb1", "top", visibility=RecordVisibility.DELETED)
    assert await w.p.validate_folder_exists_in_kb("kb1", "gone") and not await w.p.validate_folder_exists_in_kb("kb2", "gone")
    assert await v("kb1", "top")                                    # the anchor of the other-org check below
    with other_org():
        assert not await v("kb1", "top")

    assert (await w.p.find_folder_by_name_in_parent("kb1", "TOP"))["_key"] == "top"             # at the root, case-insensitive
    assert await w.p.find_folder_by_name_in_parent("kb1", "mid") is None                         # not at the root
    assert await w.p.find_folder_by_name_in_parent("kb1", "top", exclude_folder_id="top") is None
    assert await w.p.find_folder_by_name_in_parent("kb1", "gone") is None                        # a trashed folder does not hold its name
    assert await w.p.find_folder_by_name_in_parent("kb2", "top") is None
    assert (await w.p.find_folder_by_name_in_parent("kb1", "Mid", "top"))["_key"] == "mid"
    assert await w.p.find_folder_by_name_in_parent("kb1", "Mid", "leaf") is None
    assert await w.p.find_folder_by_name_in_parent("kb1", "file", "mid") is None                 # a file is not a folder

    assert [await w.p.get_folder_depth(f) for f in ("top", "mid", "leaf", "nope")] == [1, 2, 3, 1]
    mimes = [FOLDER]
    assert [await w.p.get_folder_subtree_height(f, mimes) for f in ("top", "mid", "leaf", "nope")] == [2, 1, 0, 0]
    assert await w.p.get_folder_subtree_height("top", ["text/plain"]) == 2                       # the endpoint's type decides
    assert [await w.p.is_record_folder(r) for r in ("top", "file", "gone", "nope")] == [True, False, True, False]
    with other_org():
        assert await w.p.get_folder_depth("leaf") == 1 and await w.p.get_folder_subtree_height("top", mimes) == 0 and not await w.p.is_record_folder("top")
    got = await w.p.get_and_validate_folder_in_kb("kb1", "gone")
    assert got["name"] == "gone" and got["recordGroupId"] == "kb1" and got["isDeleted"] is True
    assert await w.p.get_and_validate_folder_in_kb("kb2", "top") is None


async def test_a_cycle_of_parents_ends_the_walks(w: World) -> None:
    await kb(w)
    await folder(w, "a")
    await folder(w, "b", parent="a")
    await w.edge("nodeRelations", ("records", "b"), ("records", "a"), relationshipType="PARENT_CHILD")
    assert await w.p.get_folder_depth("a") == 2 and await w.p.get_folder_subtree_height("a", [FOLDER]) == 1


async def test_a_failed_folder_check_answers_false_and_the_next_answers_true(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await kb(w)
    await folder(w, "top")
    real = w.p._q

    async def boom(*a: object, **k: object) -> None:
        raise ConnectionError("down")
    monkeypatch.setattr(w.p, "_q", boom)
    assert await w.p.validate_folder_in_kb("kb1", "top") is False and await w.p.is_record_folder("top") is False
    assert await w.p.find_folder_by_name_in_parent("kb1", "top") is None
    with pytest.raises(ConnectionError):
        await w.p.find_folder_by_name_in_parent("kb1", "top", raise_on_error=True)
    monkeypatch.setattr(w.p, "_q", real)
    assert await w.p.validate_folder_in_kb("kb1", "top") is True and await w.p.is_record_folder("top") is True


# ---- uploads ------------------------------------------------------------------------------------------------------------------------------

async def test_the_upload_validation_lets_in_writers_only_and_hides_a_collection_from_those_with_no_role(w: World) -> None:
    await users(w, "me", "own", "wr", "rd", "tw", "tn", "none", "ghost")
    await kb(w, name="Docs")
    await folder(w, "top")
    await folder(w, "trashed", isDeleted=True)
    await folder(w, "other", "kb2")
    await kb(w, "kb2")
    await grant(w, "own", "OWNER")
    await grant(w, "wr", "WRITER")
    await grant(w, "rd", "READER")
    await team(w, "tw", "tm", grant_role="WRITER", member="READER")
    await team(w, "tn", "tm2", grant_role="OWNER", member="OWNER", key="kb2")
    v = w.p._validate_upload_context

    for who, role in (("own", "OWNER"), ("wr", "WRITER"), ("tw", "WRITER")):
        r = await v("kb1", f"uid-{who}", w.org)
        assert r["valid"] and r["upload_target"] == "kb_root" and r["user_role"] == role and r["user_key"] == who
        r = await v("kb1", f"uid-{who}", w.org, "top")
        assert r["valid"] and r["upload_target"] == "folder" and r["parent_folder"]["_key"] == "top"
        assert (await w.p.validate_folder_for_upload("kb1", "top", f"uid-{who}", w.org))["valid"]
        assert (await w.p._validate_folder_creation("kb1", f"uid-{who}"))["valid"]
    r = await v("kb1", "uid-rd", w.org)
    assert (r["valid"], r["code"]) == (False, 403) and "'Docs' (kb1)" in r["reason"] and "your role is: READER" in r["reason"]
    assert (await w.p._validate_folder_creation("kb1", "uid-rd"))["code"] == 403
    for who in ("none", "tn"):                                                         # no role: the collection's existence is not told
        r = await v("kb1", f"uid-{who}", w.org)
        assert (r["valid"], r["code"], r["reason"]) == (False, 404, "Knowledge base kb1 not found")
        assert (await w.p._validate_folder_creation("kb1", f"uid-{who}"))["code"] == 404
    assert (await v("kb1", "uid-nobody", w.org))["reason"] == "User not found: uid-nobody"
    assert (await v("missing", "uid-own", w.org))["code"] == 404
    r = await v("kb1", "uid-wr", w.org, "nope")
    assert r["code"] == 404 and "was not found in knowledge base 'Docs' (kb1)" in r["reason"]
    assert (await v("kb1", "uid-wr", w.org, "other"))["code"] == 404                  # a folder of another collection
    r = await v("kb1", "uid-wr", w.org, "trashed")
    assert (r["valid"], r["code"]) == (False, 409) and "Recently deleted" in r["reason"]
    with other_org():                                                               # the same names in another org: nothing
        assert (await v("kb1", "uid-own", w.org))["code"] == 404
    # a revoked writer is refused, and is let in again when the grant returns
    await w.p.remove_kb_permission("kb1", ["wr"], [])
    assert (await v("kb1", "uid-wr", w.org))["code"] == 404
    await w.p.create_kb_permissions("kb1", "own", ["wr"], [], "WRITER")
    assert (await v("kb1", "uid-wr", w.org))["valid"]


async def test_a_failed_validation_is_a_500_and_the_next_one_answers(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await users(w, "wr")
    await kb(w)
    await grant(w, "wr", "WRITER")
    real = w.p.get_user_kb_permission

    async def boom(*a: object, **k: object) -> None:
        raise ConnectionError("down")
    monkeypatch.setattr(w.p, "get_user_kb_permission", boom)
    r = await w.p._validate_upload_context("kb1", "uid-wr", w.org)
    assert (r["valid"], r["code"]) == (False, 500)
    assert (await w.p._validate_folder_creation("kb1", "uid-wr"))["code"] == 500
    monkeypatch.setattr(w.p, "get_user_kb_permission", real)
    assert (await w.p._validate_upload_context("kb1", "uid-wr", w.org))["valid"]


async def test_uploaded_document_ids_are_the_files_of_the_connector_under_the_roots(w: World) -> None:
    d1, d2, d3 = ("a" * 24, "b" * 24, "c" * 24)
    await kb(w)
    await folder(w, "top")
    for key, ext, parent, extra in (("f1", d1, "top", {}), ("f2", d2, "f1", {}), ("f3", d3, None, {}), ("ext", d1.upper(), "top", {"origin": "CONNECTOR"}), ("other", "d" * 24, "top", {"connectorId": "kb2"})):
        await w.nodes("records", {"_key": key, "recordName": key, "connectorId": "kb1", "origin": UPLOAD, "externalRecordId": ext, **extra})
        if parent:
            await w.edge("nodeRelations", ("records", parent), ("records", key), relationshipType="ATTACHMENT" if key == "f2" else "PARENT_CHILD")
    await w.nodes("files", {"_key": "f1", "isFile": True})
    await w.edge("isOfType", ("records", "f1"), ("files", "f1"))
    g = w.p.get_uploaded_document_ids
    assert sorted(await g("kb1")) == [d1, d2, d3]                         # folders hold no file; another origin's id is not a storage document
    assert sorted(await g("kb1", under_record_ids=["top"])) == [d1, d2]   # below the root, through a child and an attachment, within the connector
    assert await g("kb1", under_record_ids=["f2"]) == [d2]
    assert await g("kb1", under_record_ids=[]) == []                      # no roots is no records, not all of them
    assert await g("kb1", among=[]) == []
    assert await g("kb1", among=[d3, "x"]) == [d3] and await g("kb1", under_record_ids=["top"], among=[d3]) == []
    assert await g("kb1", under_record_ids=["other"]) == [] and await g("kb2") == ["d" * 24]
    with other_org():
        assert await g("kb1") == []


# ---- no org ---------------------------------------------------------------------------------------------------------------------------------

NAMES = sorted(n for n, f in vars(KbMixin).items() if not n.startswith("__") and callable(f) and n not in ("_fetch_kb_name", "_fetch_record_name", "_kb_writer"))
CALLS = {
    "kb_exists": ("k",), "list_user_knowledge_bases": ("u", "o", 0, 1), "create_kb_permissions": ("k", "r", [], [], "READER"), "update_kb_permission": ("k", "r", ["u"], [], "READER"),
    "remove_kb_permission": ("k", [], []), "list_kb_permissions": ("k",), "get_kb_permissions": ("k", ["u"]), "count_kb_owners": ("k",), "validate_folder_exists_in_kb": ("k", "f"),
    "validate_folder_in_kb": ("k", "f"), "get_and_validate_folder_in_kb": ("k", "f"), "find_folder_by_name_in_parent": ("k", "n"), "get_folder_depth": ("f",),
    "get_folder_subtree_height": ("f", []), "is_record_folder": ("f",), "get_uploaded_document_ids": ("c",), "_validate_folder_creation": ("k", "u"),
    "_validate_upload_context": ("k", "u", "o"), "validate_folder_for_upload": ("k", "f", "u", "o"),
}


def test_every_method_of_the_module_is_in_the_no_org_cases() -> None:
    assert set(NAMES) == set(CALLS)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*CALLS[name])
    with org_context.system("test.no-org"), pytest.raises(Exception, match="(?i)system|org"):
        await getattr(pg_provider, name)(*CALLS[name])


def test_the_connector_constant_is_the_collection_type() -> None:
    assert Connectors.KNOWLEDGE_BASE.value == "KB"
