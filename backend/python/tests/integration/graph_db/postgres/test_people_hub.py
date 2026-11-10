"""People, organizations, mail, and the knowledge hub's root listing, parent and trail on PostgreSQL, against a real database.

Both ways: nothing reaches a user who was not given it (another org, another app, an ancestor they cannot see, a merge that conflicts) and nothing a
user was given goes missing (a merged stub's grants, a trail through an ancestor they cannot see). A read that fails answers its safe default and
answers right again once the store is back.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core import org_context
from app.core.org_context import MissingOrgContext, OrgMismatch, SystemContextNotAllowed
from app.models.entities import Person
from app.services.graph_db.user_email_identity import GraphUserEmailConflictError

from .test_access import APP, World, _org, rec
from .test_access import w as w
from .test_grants import failing, grant, in_org

pytestmark = pytest.mark.integration

LOGIN = "a" * 24      # a login user's id is a Mongo object id; a connector stub's is not


# ---- organizations, app users ----------------------------------------------------------------------------------------------------------

async def test_an_organization_is_found_by_name_in_this_org_and_by_its_external_flag(w: World) -> None:
    await w.nodes("organizations", {"_key": "o1", "name": "Acme"}, {"_key": "o2", "name": "Ext", "isExternal": True}, {"_key": "o3", "name": "Flagless", "isExternal": False})
    p = w.p
    assert await p.organization_exists("Acme") and await p.organization_exists("Flagless")
    assert not await p.organization_exists("Acme", True)
    assert await p.organization_exists("Ext", True) and not await p.organization_exists("Ext")
    assert not await p.organization_exists("Nope")
    other = _org()
    with in_org(other):
        await World(p, other).nodes("organizations", {"_key": "x", "name": "Elsewhere"})
    assert not await p.organization_exists("Elsewhere")            # another org's organization is out of reach


async def test_an_app_user_is_found_by_email_through_the_app_only(w: World) -> None:
    await w.nodes("users", {"_key": "au", "email": "Ann@Example.com", "userId": "uid-au", "fullName": "Ann", "isActive": True}, {"_key": "nolink", "email": "nolink@x.io"})
    await w.nodes("apps", {"_key": "app2", "type": "SLACK"})
    await w.edge("userAppRelation", ("users", "au"), ("apps", APP), sourceUserId="src-7")
    await w.edge("userAppRelation", ("users", "nolink"), ("apps", "app2"))
    p = w.p
    got = await p.get_app_user_by_email("aNN@example.com", APP)
    assert got is not None and got.id == "au" and got.source_user_id == "src-7"
    assert await p.get_app_user_by_email("ann@example.com", "app2") is None          # linked to another app
    assert await p.get_app_user_by_email("nolink@x.io", APP) is None
    assert await p.get_app_user_by_email("nobody@x.io", APP) is None
    other = _org()
    with in_org(other):
        assert await p.get_app_user_by_email("ann@example.com", APP) is None         # the same address in another org is nobody here


# ---- persons ---------------------------------------------------------------------------------------------------------------------------

def person(w: World, email: str, **kw: object) -> Person:
    return Person(email=email, org_id=w.org, **kw)


async def test_a_person_is_created_once_per_email_and_never_changed(w: World) -> None:
    p = w.p
    first = person(w, "Pat@Example.com", full_name="Pat Smith", phone="1")
    assert await p.upsert_person_by_email(first) == first.id
    again = person(w, "pat@example.COM", full_name="blank")
    assert await p.upsert_person_by_email(again) == first.id            # the survivor's id, whatever the case
    got = await p.get_person_by_email("PAT@example.com", w.org)
    assert got is not None and got.id == first.id and got.full_name == "Pat Smith" and got.phone == "1" and got.email == "pat@example.com"
    assert await p.get_person_by_email("other@example.com", w.org) is None
    assert await p.get_person_by_email("pat@example.com", _org()) is None


async def test_racing_creators_of_one_person_converge_on_one_node(w: World) -> None:
    ids = await asyncio.gather(*(w.p.upsert_person_by_email(person(w, "race@example.com")) for _ in range(8)))
    assert len(set(ids)) == 1 and ids[0] is not None
    assert len(await w.p.get_nodes_by_filters("person", {"email": "race@example.com"})) == 1


async def test_a_person_of_another_org_is_refused_and_each_org_has_its_own(w: World) -> None:
    with pytest.raises(OrgMismatch):
        await w.p.upsert_person_by_email(Person(email="x@example.com", org_id=_org()))
    mine = person(w, "same@example.com")
    await w.p.upsert_person_by_email(mine)
    other = _org()
    with in_org(other):
        theirs = Person(email="same@example.com", org_id=other)
        assert await w.p.upsert_person_by_email(theirs) == theirs.id and theirs.id != mine.id
    assert (await w.p.get_person_by_email("same@example.com", w.org)).id == mine.id


async def test_a_failed_person_read_answers_none_unless_asked_to_raise_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    pers = person(w, "back@example.com")
    await w.p.upsert_person_by_email(pers)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_person_by_email("back@example.com", w.org) is None
    assert await w.p.upsert_person_by_email(person(w, "new@example.com")) is None
    with pytest.raises(RuntimeError):
        await w.p.get_person_by_email("back@example.com", w.org, raise_on_error=True)
    monkeypatch.setattr(w.p, "_q", real)
    assert (await w.p.get_person_by_email("back@example.com", w.org)).id == pers.id
    assert await w.p.get_person_by_email("new@example.com", w.org) is None       # the failed create wrote nothing


# ---- record owner, mail ----------------------------------------------------------------------------------------------------------------

async def test_the_owner_of_a_record_is_a_user_with_an_owner_grant_of_type_user_on_it_or_what_it_inherits_from(w: World) -> None:
    await w.nodes("users", {"_key": "own", "email": "own@x.io"}, {"_key": "wr", "email": "wr@x.io"}, {"_key": "tm", "email": "tm@x.io"}, {"_key": "far", "email": "far@x.io"})
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("r"), rec("noowner"), rec("deep"))
    await w.child(("recordGroups", "g"), ("records", "r"))
    await w.child(("recordGroups", "g"), ("records", "noowner"))
    await grant(w, "own", "recordGroups", "g", "OWNER")
    await grant(w, "wr", "records", "noowner", "WRITER")
    await w.edge("permission", ("users", "tm"), ("records", "noowner"), type="TEAM", role="OWNER")      # an owner grant of another type is not the owner
    p = w.p
    assert await p.get_record_owner_source_user_email("r") == "own@x.io"                # inherited from the group
    assert await p.get_record_owner_source_user_email("noowner") == "own@x.io"
    await grant(w, "own", "records", "noowner", "OWNER")
    await w.p.delete_edge("own", "users", "g", "recordGroups", "permission")
    assert await p.get_record_owner_source_user_email("r") is None                       # the grant taken away
    assert await p.get_record_owner_source_user_email("noowner") == "own@x.io"          # a direct grant stays
    assert await p.get_record_owner_source_user_email("missing") is None
    ids = [f"c{i}" for i in range(10)]                                                   # the walk stops after 8 hops
    await w.nodes("recordGroups", *({"_key": i, "connectorId": APP} for i in ids))
    for a, b in zip(ids, ids[1:], strict=False):
        await w.edge("inheritPermissions", ("recordGroups", a), ("recordGroups", b))
    await w.edge("inheritPermissions", ("records", "deep"), ("recordGroups", ids[0]))
    await grant(w, "far", "recordGroups", ids[7], "OWNER")          # eight hops up from the record
    assert await p.get_record_owner_source_user_email("deep") == "far@x.io"
    await w.p.delete_edge("far", "users", ids[7], "recordGroups", "permission")
    await grant(w, "far", "recordGroups", ids[8], "OWNER")          # nine
    assert await p.get_record_owner_source_user_email("deep") is None
    with in_org(_org()):
        assert await p.get_record_owner_source_user_email("noowner") is None


async def test_mail_is_found_by_its_message_id_header_in_its_collection_and_org_only(w: World) -> None:
    await w.nodes("mails", {"_key": "m1", "messageIdHeader": "<a@x>"}, {"_key": "m2", "messageIdHeader": "<a@x>"}, {"_key": "m3", "messageIdHeader": "<b@x>"})
    await w.nodes("records", {"_key": "r1", "messageIdHeader": "<a@x>", "connectorId": APP})
    p = w.p
    assert await p.get_message_id_header_by_key("m1", "mails") == "<a@x>" and await p.get_message_id_header_by_key("m1", "records") is None
    assert await p.get_message_id_header_by_key("missing", "mails") is None
    assert await p.get_related_mails_by_message_id_header("<a@x>", "m1", "mails") == ["m2"]
    assert await p.get_related_mails_by_message_id_header("<a@x>", "none", "mails") == ["m1", "m2"]
    assert await p.get_related_mails_by_message_id_header("<a@x>", "none", "records") == ["r1"]
    assert await p.get_related_mails_by_message_id_header("<zz@x>", "none", "mails") == []
    other = _org()
    with in_org(other):
        await World(p, other).nodes("mails", {"_key": "m9", "messageIdHeader": "<a@x>"})
        assert await p.get_related_mails_by_message_id_header("<a@x>", "none", "mails") == ["m9"]
    assert await p.get_related_mails_by_message_id_header("<a@x>", "m1", "mails") == ["m2"]          # not the other org's m9


async def test_mail_reads_answer_nothing_when_they_fail_and_come_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("mails", {"_key": "m1", "messageIdHeader": "<a@x>"}, {"_key": "m2", "messageIdHeader": "<a@x>"})
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_message_id_header_by_key("m1", "mails") is None
    assert await w.p.get_related_mails_by_message_id_header("<a@x>", "m1", "mails") == []
    assert await w.p.get_record_owner_source_user_email("m1") is None
    monkeypatch.setattr(w.p, "_q", real)
    assert await w.p.get_related_mails_by_message_id_header("<a@x>", "m1", "mails") == ["m2"]


# ---- the verified email of a login user ------------------------------------------------------------------------------------------------

async def merge_world(w: World) -> None:
    """A login user, a connector stub holding the email to be verified, a second stub, and what each is granted."""
    await w.nodes("users", {"_key": "login", "userId": LOGIN, "email": "old@x.io", "isActive": True},
                  {"_key": "stub1", "userId": "src-1", "email": "new@x.io", "isActive": False}, {"_key": "stub2", "userId": "src-2", "email": "NEW@x.io", "isActive": False},
                  {"_key": "bystander", "userId": "src-3", "email": "else@x.io", "isActive": False})
    await w.nodes("records", rec("r"), rec("r2"), rec("only_stub"))
    for k in ("r", "r2", "only_stub"):
        await w.child(("apps", APP), ("records", k))
    await w.nodes("apps", {"_key": "app9", "type": "SLACK"})
    await w.edge("userAppRelation", ("users", "stub1"), ("apps", "app9"), sourceUserId="s1")
    await w.edge("userAppRelation", ("users", "stub2"), ("apps", APP), sourceUserId="s2")
    await w.edge("permission", ("users", "stub1"), ("records", "r"), type="USER", role="OWNER")
    await w.edge("permission", ("users", "stub1"), ("records", "r2"), type="USER", role="READER")
    await w.edge("permission", ("users", "stub1"), ("records", "only_stub"), type="USER", role="WRITER")
    await w.edge("permission", ("users", "stub2"), ("records", "only_stub"), type="USER", role="OWNER")
    await w.edge("permission", ("users", "login"), ("records", "r"), type="USER", role="READER")
    await w.edge("permission", ("users", "login"), ("records", "r2"), type="USER", role="WRITER")
    await w.edge("permission", ("users", "stub1"), ("users", "login"), type="USER", role="READER")            # an edge between the two goes with the stub
    await w.edge("permission", ("users", "bystander"), ("records", "r"), type="USER", role="READER")


async def role(w: World, user: str, rec_id: str) -> str | None:
    e = await w.p.get_edge(user, "users", rec_id, "records", "permission")
    return e and e.get("role")


async def test_a_verified_email_merges_the_stubs_into_the_login_user_and_keeps_every_grant(w: World) -> None:
    await merge_world(w)
    p = w.p
    assert await w.allowed("login", "only_stub") == set()                    # before: the login user is not in the connector
    got = await p.apply_verified_user_email(LOGIN, w.org, "new@x.io")
    assert got["email"] == "new@x.io" and sorted(got["mergedStubKeys"]) == ["stub1", "stub2"] and sorted(got["connectorIds"]) == [APP, "app9"]
    assert (await p.get_document("login", "users"))["email"] == "new@x.io"
    assert await p.get_document("stub1", "users") is None and await p.get_document("stub2", "users") is None
    assert await p.get_document("bystander", "users") is not None
    assert await role(w, "login", "r") == "OWNER"                             # the stronger role wins
    assert await role(w, "login", "r2") == "WRITER"                           # the login user's own, stronger, stays
    assert await role(w, "login", "only_stub") == "OWNER"                     # two stubs collapse to the stronger grant
    assert len(await p.get_edges_from_node("users/login", "userAppRelation")) == 2
    assert await w.allowed("login", "only_stub", "r") == {"only_stub", "r"}   # nothing the stubs held is missing
    assert await p.get_edges_from_node("users/login", "permission") and not [e for e in await p.get_edges_from_node("users/login", "permission") if e["to_id"] == "login"]
    assert await role(w, "bystander", "r") == "READER"                        # nobody else's edges moved


async def test_a_verified_email_with_no_org_argument_keeps_the_users_org(w: World) -> None:
    await merge_world(w)
    await w.p.apply_verified_user_email(LOGIN, None, "new@x.io")      # type: ignore[arg-type]
    assert (await w.p.get_document("login", "users")).get("orgId") == w.org      # an empty argument never overwrites the stored org


async def test_a_merge_keeps_the_stronger_grant_whatever_the_type_of_either(w: World) -> None:
    await merge_world(w)
    await w.nodes("records", rec("same"), rec("lower"), rec("up"))
    for k in ("same", "lower", "up"):
        await w.child(("apps", APP), ("records", k))
    await w.edge("permission", ("users", "login"), ("records", "same"), type="USER", role="WRITER")
    await w.edge("permission", ("users", "stub1"), ("records", "same"), type="GROUP", role="READER")          # weaker, and of another type: the login user keeps theirs
    await w.edge("permission", ("users", "login"), ("records", "up"), type="USER", role="READER")
    await w.edge("permission", ("users", "stub1"), ("records", "up"), type="GROUP", role="OWNER")             # stronger: it becomes the one grant
    await w.p.apply_verified_user_email(LOGIN, w.org, "new@x.io")
    same, up = await w.p.get_edges_from_node("users/login", "permission"), None
    by_target = {e["to_id"]: e for e in same}
    assert (by_target["same"]["role"], by_target["same"]["type"]) == ("WRITER", "USER")
    assert (by_target["up"]["role"], by_target["up"]["type"]) == ("OWNER", "GROUP") and up is None
    assert len([e for e in same if e["to_id"] in ("same", "up")]) == 2                                          # one grant per node


async def test_a_merge_moves_every_kind_of_edge_a_stub_can_hold_and_keeps_what_the_login_user_has(w: World) -> None:
    await merge_world(w)
    await w.nodes("organizations", {"_key": w.org})
    await w.nodes("users", {"_key": "src"}, {"_key": "other"})
    await w.nodes("drives", {"_key": "d1"})
    await w.nodes("records", rec("made"))
    await w.edge("belongsTo", ("users", "stub1"), ("organizations", w.org), entityType="ORGANIZATION")
    await w.edge("userDriveRelation", ("users", "stub1"), ("drives", "d1"), access="owner")
    await w.edge("authenticatedAs", ("users", "stub1"), ("users", "src"), connectorId="c1", note="stub")
    await w.edge("authenticatedAs", ("users", "stub1"), ("users", "src"), connectorId="c2")
    await w.edge("authenticatedAs", ("users", "login"), ("users", "src"), connectorId="c1", note="login")      # the login user already has the c1 link
    await w.edge("authenticatedAs", ("users", "other"), ("users", "stub1"), connectorId="c3")                  # an edge into the stub
    await w.edge("entityRelations", ("records", "made"), ("users", "stub1"), edgeType="CREATED_BY")           # so is this one
    await w.p.apply_verified_user_email(LOGIN, w.org, "new@x.io")
    p = w.p
    assert [e["to_id"] for e in await p.get_edges_from_node("users/login", "belongsTo")] == [w.org]
    assert [(e["to_id"], e["access"]) for e in await p.get_edges_from_node("users/login", "userDriveRelation")] == [("d1", "owner")]
    links = {e["connectorId"]: e.get("note") for e in await p.get_edges_from_node("users/login", "authenticatedAs")}
    assert links == {"c1": "login", "c2": None}                                                                  # the login user's own c1 link stays, c2 moves over
    assert [(e["from_id"], e["connectorId"]) for e in await p.get_edges_to_node("users/login", "authenticatedAs")] == [("other", "c3")]
    assert [(e["from_id"], e["edgeType"]) for e in await p.get_edges_to_node("users/login", "entityRelations")] == [("made", "CREATED_BY")]
    for coll in ("belongsTo", "userDriveRelation", "authenticatedAs", "entityRelations", "userAppRelation", "permission"):
        assert not (await p.get_edges_from_node("users/stub1", coll) + await p.get_edges_to_node("users/stub1", coll))      # nothing is left on the stub


async def test_two_verifications_of_one_address_cannot_both_go_through(w: World) -> None:
    await merge_world(w)
    await w.nodes("users", {"_key": "login2", "userId": "d" * 24, "email": "old2@x.io", "isActive": True})
    res = await asyncio.gather(w.p.apply_verified_user_email(LOGIN, w.org, "new@x.io"), w.p.apply_verified_user_email("d" * 24, w.org, "new@x.io"), return_exceptions=True)
    won = [r for r in res if isinstance(r, dict)]
    lost = [r for r in res if isinstance(r, GraphUserEmailConflictError)]
    assert len(won) == 1 and len(lost) == 1 and sorted(won[0]["mergedStubKeys"]) == ["stub1", "stub2"]
    winner = LOGIN if (await w.p.get_document("login", "users"))["email"] == "new@x.io" else "d" * 24
    other = "login2" if winner == LOGIN else "login"
    assert (await w.p.get_document(other, "users"))["email"].startswith("old")                                  # the loser kept its own address


async def test_the_owner_of_a_record_is_the_nearest_owner_that_has_an_email(w: World) -> None:
    await w.nodes("users", {"_key": "near"}, {"_key": "far", "email": "far@x.io"})
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("r"))
    await w.child(("recordGroups", "g"), ("records", "r"))
    await grant(w, "near", "records", "r", "OWNER")
    await grant(w, "far", "recordGroups", "g", "OWNER")
    assert await w.p.get_record_owner_source_user_email("r") == "far@x.io"                                      # the nearer owner has none to give


async def test_a_verified_email_held_by_another_login_changes_nothing(w: World) -> None:
    await merge_world(w)
    await w.nodes("users", {"_key": "rival", "userId": "b" * 24, "email": "new@x.io", "isActive": True})
    with pytest.raises(GraphUserEmailConflictError) as err:
        await w.p.apply_verified_user_email(LOGIN, w.org, "new@x.io")
    assert err.value.conflicting_user_id == "b" * 24
    assert (await w.p.get_document("login", "users"))["email"] == "old@x.io"
    assert await w.p.get_document("stub1", "users") is not None and await role(w, "login", "r") == "READER"
    assert await w.p.apply_verified_user_email("c" * 24, w.org, "x@x.io") is None          # no such login user
    with pytest.raises(OrgMismatch):
        await w.p.apply_verified_user_email(LOGIN, _org(), "new@x.io")


async def test_a_merge_that_fails_part_way_leaves_everything_as_it_was_and_goes_through_when_retried(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await merge_world(w)
    real = w.p.batch_create_edges
    calls = {"n": 0}

    async def flaky(edges: list, collection: str, transaction: str | None = None, **kw: object) -> bool:
        calls["n"] += 1
        if collection == "userAppRelation":          # after the permissions have been moved
            raise RuntimeError("store down")
        return await real(edges, collection, transaction, **kw)
    monkeypatch.setattr(w.p, "batch_create_edges", flaky)
    with pytest.raises(RuntimeError):
        await w.p.apply_verified_user_email(LOGIN, w.org, "new@x.io")
    assert calls["n"] >= 1
    assert await w.p.get_document("stub1", "users") is not None and await role(w, "login", "r") == "READER"          # the permissions moved before the failure went back with it
    assert await role(w, "login", "only_stub") is None
    assert len(await w.p.get_edges_from_node("users/login", "userAppRelation")) == 0
    assert (await w.p.get_document("login", "users"))["email"] == "old@x.io"
    monkeypatch.setattr(w.p, "batch_create_edges", real)
    assert (await w.p.apply_verified_user_email(LOGIN, w.org, "new@x.io"))["mergedStubKeys"]
    assert await role(w, "login", "r") == "OWNER"


# ---- the knowledge hub: filter options, trail, parent, root listing --------------------------------------------------------------------

async def test_filter_options_are_the_connectors_the_user_passes_by_name_and_never_a_collection(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("apps", {"_key": "zed", "type": "SLACK", "name": "zed"}, {"_key": "abe", "type": "JIRA", "name": "Abe"}, {"_key": "kb", "type": "KB", "name": "KB"},
                  {"_key": "closed", "type": "JIRA", "name": "closed"})
    for a in ("zed", "abe", "kb"):
        await grant(w, "u", "apps", a, "OWNER" if a == "kb" else "READER")
    got = (await w.p.get_knowledge_hub_filter_options("u", w.org))["apps"]
    assert [a["name"] for a in got if a["name"]] == ["Abe", "zed"] and "kb" not in {a["id"] for a in got} and "closed" not in {a["id"] for a in got}
    assert (await w.p.get_knowledge_hub_filter_options("other", w.org)) == {"apps": []}
    assert (await w.p.get_knowledge_hub_filter_options("u", _org())) == {"apps": []}
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_knowledge_hub_filter_options("u", w.org) == {"apps": []}
    monkeypatch.setattr(w.p, "_q", real)
    assert len((await w.p.get_knowledge_hub_filter_options("u", w.org))["apps"]) >= 2


def named(key: str, name: str, **kw: object) -> dict:
    return rec(key, recordName=name, recordType="FILE", **kw)


async def hub_world(w: World) -> None:
    """APP -> group -> folder -> file, with a middle ancestor the user cannot see and a granted leaf below it."""
    await w.nodes("apps", {"_key": APP, "type": "CONNECTOR", "name": "Drive", "isActive": True, "orgId": w.org})
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP, "groupName": "Space", "connectorName": "DRIVE", "orgId": w.org})
    await w.nodes("records", named("folder", "Docs", mimeType="folder"), named("file", "Spec"), named("hidden", "Secret", accessRule="RESTRICTED"),
                  named("leaf", "Leaf"))
    await w.nodes("files", {"_key": "folder", "isFile": False}, {"_key": "file", "isFile": True})
    await w.edge("isOfType", ("records", "folder"), ("files", "folder"))
    await w.edge("isOfType", ("records", "file"), ("files", "file"))
    await w.child(("apps", APP), ("recordGroups", "g"))
    await w.edge("belongsTo", ("recordGroups", "g"), ("apps", APP))
    await w.edge("belongsTo", ("records", "folder"), ("recordGroups", "g"))
    await w.child(("recordGroups", "g"), ("records", "folder"))
    await w.child(("records", "folder"), ("records", "file"))
    await w.child(("records", "folder"), ("records", "hidden"))
    await w.child(("records", "hidden"), ("records", "leaf"), inherits=False)
    await grant(w, "u", "records", "leaf")                       # the user is let in below a restricted record


async def test_the_trail_runs_root_to_leaf_skips_what_the_user_cannot_see_and_is_empty_when_the_leaf_is_hidden(w: World) -> None:
    await hub_world(w)
    p = w.p
    trail = await p.get_knowledge_hub_breadcrumbs("file", "u", w.org)
    assert [(s["id"], s["nodeType"]) for s in trail] == [(APP, "app"), ("g", "recordGroup"), ("folder", "folder"), ("file", "record")]
    assert trail[1]["name"] == "Space" and trail[1]["subType"] == "DRIVE" and trail[3]["subType"] == "FILE"
    assert [s["id"] for s in await p.get_knowledge_hub_breadcrumbs("leaf", "u", w.org)] == [APP, "g", "folder", "leaf"]      # the hidden parent is skipped, not the leaf
    assert await p.get_knowledge_hub_breadcrumbs("hidden", "u", w.org) == []                      # the leaf itself is not visible
    assert await p.get_knowledge_hub_breadcrumbs("file", "other", w.org) == []                    # a user with no gate sees no trail
    assert await p.get_knowledge_hub_breadcrumbs("file", "u", _org()) == []
    assert await p.get_knowledge_hub_breadcrumbs("missing", "u", w.org) == []
    await p.delete_edge("u", "users", "leaf", "records", "permission")
    assert await p.get_knowledge_hub_breadcrumbs("leaf", "u", w.org) == []                        # the grant taken away closes the leaf
    await w.edge("nodeRelations", ("records", "file"), ("records", "folder"), relationshipType="PARENT_CHILD")      # a cycle ends the walk
    assert [s["id"] for s in await p.get_knowledge_hub_breadcrumbs("file", "u", w.org)] == ["folder", "file"]      # each node once, then it stops


async def test_a_failed_trail_is_empty_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await hub_world(w)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_knowledge_hub_breadcrumbs("file", "u", w.org) == []
    assert await w.p.get_knowledge_hub_parent_node("file", []) is None
    monkeypatch.setattr(w.p, "_q", real)
    assert len(await w.p.get_knowledge_hub_breadcrumbs("file", "u", w.org)) == 4
    assert (await w.p.get_knowledge_hub_parent_node("file", ["folder"]))["id"] == "folder"


async def test_the_parent_of_a_node_is_its_hierarchy_parent_else_its_group_else_its_app(w: World) -> None:
    await hub_world(w)
    await w.nodes("recordGroups", {"_key": "sub", "connectorId": APP, "groupName": "Sub", "parentId": "g", "groupType": "SPACE"}, {"_key": "top", "connectorId": APP, "groupName": "Top"},
                  {"_key": "orphan", "groupName": "Orphan"}, {"_key": "dead", "connectorId": APP, "groupName": "Dead", "parentId": "gone"})
    await w.nodes("apps", {"_key": "kb", "type": "KB", "name": "KB"})
    await w.nodes("records", named("k1", "K1", connectorId="kb", connectorName="KB"), named("c1", "C1"), named("b1", "B1"))
    await w.edge("inheritPermissions", ("records", "k1"), ("apps", "kb"))
    await w.edge("inheritPermissions", ("records", "c1"), ("recordGroups", "g"))
    await w.edge("belongsTo", ("records", "b1"), ("recordGroups", "top"))
    p, mimes = w.p, ["folder"]
    assert await p.get_knowledge_hub_parent_node("file", mimes) == {"id": "folder", "name": "Docs", "nodeType": "folder", "subType": "FILE"}
    assert await p.get_knowledge_hub_parent_node("folder", mimes) == {"id": "g", "name": "Space", "nodeType": "recordGroup", "subType": "DRIVE"}      # a group beats nothing else here
    assert await p.get_knowledge_hub_parent_node("sub", mimes) == {"id": "g", "name": "Space", "nodeType": "recordGroup", "subType": "DRIVE"}
    assert await p.get_knowledge_hub_parent_node("top", mimes) == {"id": APP, "name": "Drive", "nodeType": "app", "subType": "CONNECTOR"}
    assert await p.get_knowledge_hub_parent_node("k1", mimes) == {"id": "kb", "name": "KB", "nodeType": "app", "subType": "KB"}        # a collection item inherits from its app
    assert (await p.get_knowledge_hub_parent_node("c1", mimes))["id"] == "g"                                                          # a connector record from its group
    assert (await p.get_knowledge_hub_parent_node("b1", mimes))["id"] == "top"
    for none in (APP, "orphan", "dead", "missing"):
        assert await p.get_knowledge_hub_parent_node(none, mimes) is None
    with in_org(_org()):
        assert await p.get_knowledge_hub_parent_node("file", mimes) is None


async def roots(w: World, **kw: object) -> dict:
    kw.setdefault("limit", 10)
    return (await w.p.get_knowledge_hub_root_nodes_v2("u", w.org, kw.pop("apps", ["a", "b", "c", "d", "kb", "hid"]), **kw))["partitions"][0]


async def test_the_root_listing_holds_only_the_apps_asked_for_filtered_sorted_and_paged_by_keyset(w: World) -> None:
    await w.nodes("users", {"_key": "u", "userId": "uid-u"})
    await w.nodes("apps", {"_key": "a", "type": "SLACK", "name": "Alpha", "scope": "team", "createdAtTimestamp": 30}, {"_key": "b", "type": "JIRA", "name": "beta", "createdAtTimestamp": 20},
                  {"_key": "c", "type": "JIRA", "name": "Gamma-Ray", "createdAtTimestamp": 10}, {"_key": "d", "type": "SLACK", "createdAtTimestamp": 40},
                  {"_key": "kb", "type": "KB", "name": "Mine", "createdBy": "uid-u"}, {"_key": "hid", "type": "SLACK", "name": "Hidden", "hideConnector": True},
                  {"_key": "unasked", "type": "SLACK", "name": "Zzz"})
    page = await roots(w)
    names = [r["name"] for r in page["rows"]]
    assert names == ["Alpha", "beta", "Gamma-Ray", "Mine", None] and page["total"] == 5 and not page["hasMore"]       # a nameless app sorts last; hidden and unasked are absent
    row = next(r for r in page["rows"] if r["id"] == "kb")
    assert (row["origin"], row["sharingStatus"], row["webUrl"], row["nodeType"], row["hasChildren"]) == ("COLLECTION", "personal", "/app/kb", "app", False)
    assert next(r for r in page["rows"] if r["id"] == "a")["sharingStatus"] == "team" and next(r for r in page["rows"] if r["id"] == "a")["hasChildren"] is True
    assert [r["name"] for r in (await roots(w, sort_dir="desc"))["rows"]] == ["Mine", "Gamma-Ray", "beta", "Alpha", None]
    assert [r["id"] for r in (await roots(w, sort_field="createdAt"))["rows"]][:3] == ["kb", "c", "b"]           # an app without a date reads as 0
    assert [r["id"] for r in (await roots(w, search_query="gamma ray"))["rows"]] == ["c"]                     # "-" reads as a space
    assert [r["id"] for r in (await roots(w, connector_ids=["b", "kb"]))["rows"]] == ["b", "kb"]
    assert [r["id"] for r in (await roots(w, origins=["COLLECTION"]))["rows"]] == ["kb"]
    assert [r["id"] for r in (await roots(w, created_at={"gte": 15, "lte": 35}))["rows"]] == ["a", "b"]
    assert (await roots(w, node_types=["record"]))["rows"] == [] and (await roots(w, record_types=["FILE"]))["rows"] == [] and (await roots(w, size={"gte": 0}))["rows"] == []
    assert (await roots(w, exclude_app_ids=frozenset({"a", "kb"})))["total"] == 3
    names_only = await roots(w, names_only=True)
    assert all(r["userRole"] is None and r["hasChildren"] is False for r in names_only["rows"])
    assert {i["id"] for i in (await roots(w, include_ids=True))["ids"]} == {"a", "b", "c", "kb", "d"} and (await roots(w))["ids"] == []
    # keyset: forward pages, a page back, and the total only on the first
    first = await roots(w, limit=2)
    assert [r["name"] for r in first["rows"]] == ["Alpha", "beta"] and first["hasMore"] and first["total"] == 5
    cut = first["rows"][-1]
    second = await roots(w, limit=2, after={"nullRank": cut["nullRank"], "sortKey": cut["sortKey"], "id": cut["id"]})
    assert [r["name"] for r in second["rows"]] == ["Gamma-Ray", "Mine"] and second["total"] is None and second["hasMore"]
    last = second["rows"][-1]
    third = await roots(w, limit=2, after={"nullRank": last["nullRank"], "sortKey": last["sortKey"], "id": last["id"]})
    assert [r["name"] for r in third["rows"]] == [None] and not third["hasMore"]
    first_of_second = second["rows"][0]
    back = await roots(w, limit=2, direction="prev", after={"nullRank": first_of_second["nullRank"], "sortKey": first_of_second["sortKey"], "id": first_of_second["id"]})
    assert [r["name"] for r in back["rows"]] == ["Alpha", "beta"] and not back["hasMore"]


async def test_the_root_listing_gives_the_users_role_on_each_app_and_nothing_to_another_org_or_a_missing_user(w: World) -> None:
    await w.nodes("users", {"_key": "u", "userId": "uid-u"})
    await w.nodes("apps", {"_key": "mine", "type": "SLACK", "name": "Mine", "scope": "personal", "createdBy": "uid-u"}, {"_key": "theirs", "type": "SLACK", "name": "Theirs", "createdBy": "x"},
                  {"_key": "kb", "type": "KB", "name": "KB"})
    await w.edge("userAppRelation", ("users", "u"), ("apps", "mine"))
    await w.edge("userAppRelation", ("users", "u"), ("apps", "theirs"))
    await grant(w, "u", "apps", "kb", "WRITER")
    got = {r["id"]: r["userRole"] for r in (await roots(w, apps=["mine", "theirs", "kb"]))["rows"]}
    assert got == {"mine": "OWNER", "theirs": "READER", "kb": "WRITER"}
    assert (await roots(w, apps=[]))["rows"] == []
    res = await w.p.get_knowledge_hub_root_nodes_v2("nobody", w.org, ["mine"], 10)
    assert res["partitions"][0]["rows"] == [] and res["partitions"][0]["total"] == 0
    assert (await w.p.get_knowledge_hub_root_nodes_v2("u", _org(), ["mine"], 10))["partitions"][0]["rows"] == []
    with pytest.raises(ValueError, match="sort field"):
        await roots(w, sort_field="bogus")
    with pytest.raises(ValueError, match="sort order"):
        await roots(w, sort_dir="sideways")
    with pytest.raises(ValueError, match="page direction"):
        await roots(w, direction="up")


async def test_a_failed_root_listing_raises_rather_than_listing_nothing_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("users", {"_key": "u", "userId": "uid-u"})
    await w.nodes("apps", {"_key": "x", "type": "SLACK", "name": "X"})
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(RuntimeError):
        await w.p.get_knowledge_hub_root_nodes_v2("u", w.org, ["x"], 10)
    monkeypatch.setattr(w.p, "_q", real)
    assert [r["id"] for r in (await w.p.get_knowledge_hub_root_nodes_v2("u", w.org, ["x"], 10))["partitions"][0]["rows"]] == ["x"]


NO_ORG = {
    "organization_exists": ("Acme",), "get_app_user_by_email": ("a@x.io", "app"), "get_person_by_email": ("a@x.io", "o"),
    "apply_verified_user_email": ("u", "o", "a@x.io"), "get_knowledge_hub_filter_options": ("u", "o"), "get_knowledge_hub_breadcrumbs": ("n", "u", "o"),
    "get_knowledge_hub_parent_node": ("n", []), "get_knowledge_hub_root_nodes_v2": ("u", "o", [], 10),
}


@pytest.mark.parametrize("name", sorted(NO_ORG))
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*NO_ORG[name])
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*NO_ORG[name])


async def test_no_org_is_an_error_for_a_person_upsert(pg_provider) -> None:
    with pytest.raises(MissingOrgContext):
        await pg_provider.upsert_person_by_email(Person(email="a@x.io", org_id="o"))
