"""The record access details, the accessible search scope and the context role on PostgreSQL, against a real database.

The first two must agree with the batch access check (``check_access``) on a varied world, both ways: nothing it refuses, nothing it admits left out.
The context role is the lowest one (no abilities) wherever it cannot be read, and rises only with a grant the user holds.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import random

import pytest

from .test_access import APP, World, _org, rec
from .test_access import w as w
from .test_grants import failing, grant, in_org

pytestmark = pytest.mark.integration

LOW = {"role": "READER", "canUpload": False, "canCreateFolders": False, "canEdit": False, "canDelete": False, "canManagePermissions": False}


def vr(key: str, **kw: object) -> dict:
    """An indexed, live record with a virtual record id of its own."""
    return rec(key, **{"virtualRecordId": f"v-{key}", "indexingStatus": "COMPLETED", "origin": "CONNECTOR", "recordType": "FILE", **kw})


async def world(w: World) -> None:
    """Users with every kind of path to records, collections and a connector."""
    await w.nodes("users", {"_key": "v", "userId": "uid-v"}, {"_key": "gm", "userId": "uid-gm", "email": "gm@x.io"}, {"_key": "tm", "userId": "uid-tm"},
                  {"_key": "kbu", "userId": "uid-kbu"}, {"_key": "nogate", "userId": "uid-nogate"}, {"_key": "u", "userId": "uid-u", "email": "u@x.io"})
    for who in ("v", "gm", "tm"):
        await w.edge("permission", ("users", who), ("apps", APP), type="USER", role="READER")        # the connector gate
    await w.nodes("groups", {"_key": "grp"})
    await w.nodes("teams", {"_key": "team"})
    await w.nodes("recordGroups", {"_key": "rg", "connectorId": APP}, {"_key": "rg2", "connectorId": APP})
    await w.child(("apps", APP), ("recordGroups", "rg"))
    await w.child(("apps", APP), ("recordGroups", "rg2"))
    await w.nodes("records", vr("open1"), vr("open2"), vr("shut", accessRule="RESTRICTED"), vr("trashed", isDeleted=True), vr("unindexed", indexingStatus="QUEUED"),
                  vr("stub", isPlaceholder=True), vr("inRg2"), vr("dup1", virtualRecordId="v-dup"), vr("dup2", virtualRecordId="v-dup"))
    for k in ("open1", "open2", "shut", "trashed", "unindexed", "stub", "dup1", "dup2"):
        await w.child(("recordGroups", "rg"), ("records", k))
    await w.child(("recordGroups", "rg2"), ("records", "inRg2"))
    await grant(w, "v", "records", "shut")
    await grant(w, "gm", "recordGroups", "rg2")
    await w.member("gm", "grp")
    await w.edge("permission", ("groups", "grp"), ("recordGroups", "rg"), type="GROUP", role="WRITER")
    # a granted record whose group does not inherit (hierarchy only): only the grant opens it, and what inherits below it
    await w.nodes("recordGroups", {"_key": "rgr", "connectorId": APP})
    await w.edge("nodeRelations", ("apps", APP), ("recordGroups", "rgr"), relationshipType="PARENT_CHILD")
    await w.nodes("records", vr("sp"), vr("sp_child"), vr("sp_loose"))
    await w.edge("nodeRelations", ("recordGroups", "rgr"), ("records", "sp"), relationshipType="PARENT_CHILD")
    await w.child(("records", "sp"), ("records", "sp_child"))
    await w.child(("records", "sp"), ("records", "sp_loose"), inherits=False)
    await grant(w, "v", "records", "sp")
    # a group that declares its own scope, granted to gm, with a record that only belongs to it
    await w.nodes("recordGroups", {"_key": "decl", "connectorId": APP, "permissionModel": "RECORD_GROUP_LEVEL"})
    await w.child(("apps", APP), ("recordGroups", "decl"), inherits=False)
    await w.nodes("records", vr("inDecl"))
    await w.edge("belongsTo", ("records", "inDecl"), ("recordGroups", "decl"))
    await grant(w, "gm", "recordGroups", "decl")
    # collections
    await w.nodes("apps", {"_key": "kb1", "type": "KB", "name": "KB1"}, {"_key": "kb2", "type": "KB", "isHidden": True}, {"_key": "kb3", "type": "KB"})
    for kb, k in (("kb1", "k1"), ("kb2", "k2"), ("kb3", "k3")):
        await w.nodes("records", vr(k, connectorId=kb, origin="UPLOAD", connectorName="KB"))
        await w.child(("apps", kb), ("records", k))
    await grant(w, "kbu", "apps", "kb1", "WRITER")
    await grant(w, "kbu", "apps", "kb2", "READER")
    await w.member("tm", "team", "teams")
    await w.edge("permission", ("teams", "team"), ("apps", "kb3"), type="TEAM")


VIEWERS = ("u", "v", "gm", "tm", "kbu", "nogate")
RECORDS = ("open1", "open2", "shut", "trashed", "unindexed", "stub", "inRg2", "dup1", "dup2", "k1", "k2", "k3", "sp", "sp_child", "sp_loose", "inDecl")


async def admitted(w: World, key: str) -> set[str]:
    """What the batch access check opens, a placeholder left out."""
    got = await w.p.check_access(key, w.org, node_ids=list(RECORDS))
    return set(got.node_ids - got.placeholder_ids)


async def test_the_record_details_open_exactly_for_the_records_the_batch_check_admits(w: World) -> None:
    await world(w)
    seen = 0
    for who in VIEWERS:
        want = await admitted(w, who)
        got = {r for r in RECORDS if await w.p.check_record_access_with_details(f"uid-{who}" if who != "u" else "uid-u", w.org, r)}
        assert got == want, (who, got ^ want)
        seen += len(want)
    assert seen > 6                                       # the world opens something to somebody


async def test_the_accessible_scope_is_exactly_the_indexed_live_records_the_batch_check_admits(w: World) -> None:
    await world(w)
    for who in VIEWERS:
        admit = await admitted(w, who)
        want = {r for r in admit if r not in ("trashed", "unindexed", "k2")}        # trashed, not indexed, and a hidden collection when nothing is asked for
        got = await w.p.get_accessible_virtual_record_ids(f"uid-{who}", w.org)
        cited: dict[str, str] = {}
        for r in sorted(want):          # a virtual record id carried by two records is cited by the smallest one the user may open
            cited.setdefault("v-dup" if r in ("dup1", "dup2") else f"v-{r}", r)
        assert got == cited, who


async def test_the_scope_filters_narrow_and_never_widen(w: World) -> None:
    await world(w)
    p = w.p
    full = await p.get_accessible_virtual_record_ids("uid-kbu", w.org)
    assert set(full.values()) == {"k1"}                                                   # kb2 is hidden when unscoped; the connector is not kbu's
    assert set((await p.get_accessible_virtual_record_ids("uid-kbu", w.org, {"kb": ["kb2"]})).values()) == {"k2"}      # asked for by name, a hidden one is served
    assert await p.get_accessible_virtual_record_ids("uid-kbu", w.org, {"kb": ["kb3"]}) == {}                        # not kbu's
    assert await p.get_accessible_virtual_record_ids("uid-kbu", w.org, {"apps": ["no-such"]}) == {}
    assert await p.get_accessible_virtual_record_ids("uid-kbu", w.org, {"kb": [""]}) == {}                           # a value that is no id narrows to nothing
    assert await p.get_accessible_virtual_record_ids("uid-kbu", w.org, {"strictScope": True}) == {}                  # an empty strict scope stays empty
    assert set((await p.get_accessible_virtual_record_ids("uid-kbu", w.org, {"strictScope": False})).values()) == {"k1"}
    assert await p.get_accessible_virtual_record_ids("uid-kbu", w.org, exclude_app_ids=frozenset({"kb1"})) == {}
    gm = await p.get_accessible_virtual_record_ids("uid-gm", w.org, {"apps": [APP]})
    assert set(gm.values()) == {"open1", "open2", "inRg2", "dup1", "inDecl"}
    assert await p.get_accessible_virtual_record_ids("uid-gm", w.org, {"apps": ["kb1"]}) == {}                       # a collection gm is not in
    assert await p.get_accessible_virtual_record_ids("uid-nogate", w.org) == {}
    assert await p.get_accessible_virtual_record_ids("uid-nobody", w.org) == {}
    assert await p.get_accessible_virtual_record_ids("uid-gm", _org()) == {}


async def test_metadata_and_time_filters_keep_only_the_records_they_name(w: World) -> None:
    await world(w)
    await w.nodes("departments", {"_key": "d1", "departmentName": "Legal"})
    await w.nodes("topics", {"_key": "t1", "name": "tax"}, {"_key": "t2", "name": "hr"})
    await w.edge("belongsToDepartment", ("records", "open1"), ("departments", "d1"))
    await w.edge("belongsToTopic", ("records", "open1"), ("topics", "t1"))
    await w.edge("belongsToTopic", ("records", "open2"), ("topics", "t2"))
    await w.p.update_node("open1", "records", {"sourceCreatedAtTimestamp": 100, "sourceLastModifiedTimestamp": 300})
    await w.p.update_node("open2", "records", {"sourceCreatedAtTimestamp": 200, "sourceLastModifiedTimestamp": 400})

    async def got(f: dict | None = None, t: dict | None = None) -> dict:
        return await w.p.get_accessible_virtual_record_ids("uid-gm", w.org, f, t)
    assert set((await got({"departments": ["Legal"]})).values()) == {"open1"}
    assert set((await got({"departments": ["Legal"], "topics": ["hr"]})).values()) == set()               # every filter must hold
    assert set((await got({"topics": ["tax", "hr"]})).values()) == {"open1", "open2"}
    assert set((await got({"topics": ["unknown"]})).values()) == set()
    assert set((await got(None, {"source_created_after_ms": 150})).values()) == {"open2"}
    assert set((await got(None, {"source_updated_before_ms": 350})).values()) == {"open1"}
    assert set((await got({"topics": ["tax"]}, {"source_created_after_ms": 150})).values()) == set()
    other = await w.p.get_accessible_virtual_record_ids("uid-nogate", w.org, {"topics": ["tax"]})
    assert other == {}                                                                                      # a filter never opens what the check refuses


async def test_the_filters_apply_to_a_collections_records_as_to_a_connectors(w: World) -> None:
    await world(w)
    await w.nodes("topics", {"_key": "tp", "name": "tax"})
    await w.edge("belongsToTopic", ("records", "k1"), ("topics", "tp"))
    await w.p.update_node("k1", "records", {"sourceCreatedAtTimestamp": 100, "sourceTimestampOrigin": "file"})

    async def got(f: dict | None = None, t: dict | None = None) -> dict:
        return await w.p.get_accessible_virtual_record_ids("uid-kbu", w.org, f, t)
    assert set((await got()).values()) == {"k1"}
    assert set((await got({"topics": ["tax"]})).values()) == {"k1"} and await got({"topics": ["hr"]}) == {}
    assert set((await got(None, {"source_created_after_ms": 50})).values()) == {"k1"} and await got(None, {"source_created_after_ms": 150}) == {}
    assert await got(None, {"source_created_before_ms": 50}) == {} and set((await got(None, {"source_created_before_ms": 150})).values()) == {"k1"}


async def test_the_scope_read_fails_closed_unless_asked_to_raise_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await world(w)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_accessible_virtual_record_ids("uid-gm", w.org) == {}
    with pytest.raises(RuntimeError):
        await w.p.get_accessible_virtual_record_ids("uid-gm", w.org, raise_on_error=True)
    monkeypatch.setattr(w.p, "_q", real)
    assert set((await w.p.get_accessible_virtual_record_ids("uid-gm", w.org)).values()) >= {"open1", "inRg2"}      # the records are back once the store answers


async def test_a_grant_taken_away_closes_the_record_in_both_reads_and_one_given_opens_it(w: World) -> None:
    await world(w)
    assert await w.p.check_record_access_with_details("uid-v", w.org, "shut")
    assert "v-shut" in await w.p.get_accessible_virtual_record_ids("uid-v", w.org)
    await w.p.delete_edge("v", "users", "shut", "records", "permission")
    assert await w.p.check_record_access_with_details("uid-v", w.org, "shut") is None
    assert "v-shut" not in await w.p.get_accessible_virtual_record_ids("uid-v", w.org)
    await grant(w, "v", "records", "shut")
    assert await w.p.check_record_access_with_details("uid-v", w.org, "shut")
    assert "v-shut" in await w.p.get_accessible_virtual_record_ids("uid-v", w.org)


async def test_the_details_name_the_record_the_best_role_and_the_collection(w: World) -> None:
    await world(w)
    await w.nodes("departments", {"_key": "d1", "departmentName": "Legal"})
    await w.edge("belongsToDepartment", ("records", "open1"), ("departments", "d1"))
    await w.nodes("files", {"_key": "open1", "webUrl": "http://x/{user.email}"})
    await w.p.update_node("open1", "records", {"recordType": "FILE", "webUrl": "http://rec"})
    got = await w.p.check_record_access_with_details("uid-gm", w.org, "open1")
    assert got["record"]["id"] == "open1" and "_key" not in got["record"] and got["record"]["fileRecord"]["_key"] == "open1"
    assert got["record"]["mailRecord"] is None and got["metadata"]["departments"][0]["id"] == "d1"
    assert got["permissions"][0]["relationship"] == "WRITER" and got["permissions"][0]["name"] == "open1"       # the group's grant on the record group
    assert got["knowledgeBase"] is None and got["folder"] is None
    assert (await w.p.check_record_access_with_details("uid-v", w.org, "open1"))["permissions"][0]["relationship"] == "READER"      # the connector item: read access
    kb = await w.p.check_record_access_with_details("uid-kbu", w.org, "k1")
    assert kb["knowledgeBase"] == {"id": "kb1", "name": "KB1", "orgId": w.org} and kb["permissions"][0]["relationship"] == "WRITER"
    assert await w.p.check_record_access_with_details("uid-gm", _org(), "open1") is None
    assert await w.p.check_record_access_with_details("uid-gm", w.org, "missing") is None
    assert await w.p.check_record_access_with_details("uid-gm", w.org, "stub") is None                        # a placeholder is not a record to open
    assert await w.p.check_record_access_with_details("uid-gm", w.org, "trashed") is None


async def test_a_details_read_that_cannot_be_answered_raises_instead_of_refusing_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await world(w)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(RuntimeError):
        await w.p.check_record_access_with_details("uid-gm", w.org, "open1")
    monkeypatch.setattr(w.p, "_q", real)
    assert await w.p.check_record_access_with_details("uid-gm", w.org, "open1")


# ---- the context role --------------------------------------------------------------------------------------------------------------------

async def ctx(w: World, who: str, node: str | None, kind: str | None = None, org: str | None = None) -> dict:
    return await w.p.get_knowledge_hub_context_permissions(who, org or w.org, node, parent_type=kind)


def abilities(role: str) -> dict:
    up = role in ("OWNER", "ADMIN", "EDITOR", "WRITER")
    return {"role": role, "canUpload": up, "canCreateFolders": up, "canEdit": up, "canDelete": role in ("OWNER", "ADMIN", "WRITER"),
            "canManagePermissions": role in ("OWNER", "ADMIN")}


async def test_at_the_root_only_an_administrator_may_do_anything(w: World) -> None:
    await w.nodes("users", {"_key": "adm", "role": "ADMIN"}, {"_key": "adm2", "orgRole": "ADMIN"}, {"_key": "member"})
    full = {"role": "ADMIN", "canUpload": True, "canCreateFolders": True, "canEdit": True, "canDelete": True, "canManagePermissions": True}
    assert await ctx(w, "adm", None) == full and await ctx(w, "adm2", None) == full
    assert await ctx(w, "member", None) == {**LOW, "role": "MEMBER"}
    assert await ctx(w, "nobody", None) == LOW
    assert await ctx(w, "adm", None, org=_org()) == LOW                                  # another org's administrator is nobody here


async def test_a_role_on_a_record_comes_from_the_best_grant_on_it_or_what_it_inherits_and_no_grant_gives_none(w: World) -> None:
    await world(w)
    await w.nodes("users", {"_key": "inh"}, {"_key": "weird"}, {"_key": "ret"}, {"_key": "orgm"})
    await w.nodes("organizations", {"_key": w.org})
    await w.edge("permission", ("users", "inh"), ("recordGroups", "rg"), type="USER", role="OWNER")
    await w.edge("permission", ("users", "weird"), ("records", "open1"), type="USER", role="SUPERUSER")           # a role nobody ranks gives nothing
    await w.edge("permission", ("users", "ret"), ("records", "open1"), type="USER", role="COMMENTER")            # a retired role reads as READER
    await w.edge("permission", ("users", "v"), ("records", "open1"), type="USER", role="READER")
    await w.edge("permission", ("users", "v"), ("recordGroups", "rg"), type="USER", role="EDITOR")                 # the higher of the two
    await w.edge("belongsTo", ("users", "orgm"), ("organizations", w.org), entityType="ORGANIZATION")
    await w.edge("permission", ("organizations", w.org), ("records", "open2"), type="ORG", role="WRITER")
    assert await ctx(w, "inh", "open1", "record") == abilities("OWNER")             # inherited from the record group
    assert await ctx(w, "inh", "open1", "folder") == abilities("OWNER")             # a folder is a record
    assert await ctx(w, "v", "open1", "record") == abilities("EDITOR")
    assert await ctx(w, "gm", "open1", "record") == abilities("WRITER")             # through a group on the record group
    assert await ctx(w, "ret", "open1", "record") == abilities("READER")
    assert await ctx(w, "weird", "open1", "record") == LOW
    assert await ctx(w, "orgm", "open2", "record") == abilities("WRITER")           # the organization's grant
    await w.edge("permission", ("organizations", w.org), ("records", "inRg2"), type="DOMAIN", role="OWNER")      # a share that is not the organization's grants nothing
    assert await ctx(w, "orgm", "inRg2", "record") == LOW
    await w.nodes("users", {"_key": "kbe"})
    await w.edge("belongsTo", ("records", "k1"), ("apps", "kb1"))
    await w.edge("permission", ("users", "kbe"), ("apps", "kb1"), type="USER", role="EDITOR")                   # a role no collection has is no grant on it
    assert await ctx(w, "kbe", "k1", "record") == LOW and await ctx(w, "kbu", "k1", "record") == abilities("WRITER")
    assert await ctx(w, "nogate", "open1", "record") == LOW and await ctx(w, "orgm", "open1", "record") == LOW
    assert await ctx(w, "inh", "open1", "record", org=_org()) == LOW
    assert await ctx(w, "inh", "missing", "record") == LOW
    await w.p.delete_edge("inh", "users", "rg", "recordGroups", "permission")
    assert await ctx(w, "inh", "open1", "record") == LOW                              # a grant taken away
    await w.p.delete_all_team_permissions("team")
    assert await ctx(w, "gm", "rg", "recordGroup") == abilities("WRITER")           # the group's grant on the record group itself


async def test_a_team_never_gives_more_than_writer_and_a_removed_member_loses_it(w: World) -> None:
    await world(w)
    await w.nodes("users", {"_key": "boss"})
    await w.edge("permission", ("users", "boss"), ("teams", "team"), type="USER", role="OWNER")
    await w.edge("permission", ("teams", "team"), ("recordGroups", "rg2"), type="TEAM")                           # no role: the member's team role applies, capped
    assert await ctx(w, "boss", "inRg2", "record") == abilities("WRITER")
    assert await ctx(w, "tm", "inRg2", "record") == abilities("READER")
    await w.p.delete_team_member_edges("team", ["boss"])
    assert await ctx(w, "boss", "inRg2", "record") == LOW


async def test_a_role_on_an_app_follows_the_relation_the_grant_and_the_team(w: World) -> None:
    await world(w)
    await w.nodes("users", {"_key": "adm", "userId": "uid-adm", "role": "ADMIN"}, {"_key": "maker", "userId": "uid-maker"}, {"_key": "rel"}, {"_key": "viaTeam"},
                  {"_key": "strange"})
    await w.nodes("apps", {"_key": "team_app", "type": "SLACK", "scope": "team", "createdBy": "uid-maker"}, {"_key": "own_app", "type": "SLACK", "scope": "personal", "createdBy": "uid-maker"})
    for u in ("adm", "maker", "rel"):
        await w.edge("userAppRelation", ("users", u), ("apps", "team_app"))
        await w.edge("userAppRelation", ("users", u), ("apps", "own_app"))
    await w.edge("userAppRelation", ("teams", "team"), ("apps", "team_app"))
    await w.member("viaTeam", "team", "teams")
    await w.edge("permission", ("users", "strange"), ("apps", "kb1"), type="USER", role="SUPERUSER")
    assert await ctx(w, "adm", "team_app", "app") == abilities("EDITOR") and await ctx(w, "adm", "own_app", "app") == abilities("OWNER")
    assert await ctx(w, "maker", "team_app", "app") == abilities("OWNER")
    assert await ctx(w, "rel", "team_app", "app") == abilities("READER")
    assert await ctx(w, "viaTeam", "team_app", "app") == abilities("READER")                  # only through the team
    assert await ctx(w, "nogate", "team_app", "app") == LOW and await ctx(w, "nobody", "team_app", "app") == LOW
    assert await ctx(w, "kbu", "kb1", "app") == abilities("WRITER") and await ctx(w, "strange", "kb1", "app") == LOW      # a role no collection has is no grant
    assert await ctx(w, "tm", "kb3", "app") == abilities("READER")                           # a team share: the team role, never owner
    await w.p.delete_edge("kbu", "users", "kb1", "apps", "permission")
    assert await ctx(w, "kbu", "kb1", "app") == LOW


async def test_an_unsupported_parent_is_refused_and_a_failed_read_gives_the_lowest_role_then_recovers(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await world(w)
    with pytest.raises(ValueError, match="parent_type"):
        await ctx(w, "gm", "open1", "banana")
    with pytest.raises(ValueError, match="parent_type"):
        await ctx(w, "gm", "open1", None)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await ctx(w, "gm", "rg", "recordGroup") == LOW
    monkeypatch.setattr(w.p, "_q", real)
    assert await ctx(w, "gm", "rg", "recordGroup") == abilities("WRITER")


async def test_other_orgs_see_none_of_this(w: World) -> None:
    await world(w)
    other = _org()
    with in_org(other):
        ow = World(w.p, other)
        await ow.nodes("users", {"_key": "gm", "userId": "uid-gm"})
        assert await w.p.get_accessible_virtual_record_ids("uid-gm", other) == {}
        assert await w.p.check_record_access_with_details("uid-gm", other, "open1") is None
        assert await w.p.get_knowledge_hub_context_permissions("gm", other, "rg", parent_type="recordGroup") == LOW


# ---- what the scope read walks, and the cases the cross-check must also hold on --------------------------------------------------------

async def test_the_scope_read_looks_only_at_what_the_user_can_reach(w: World) -> None:
    await world(w)
    await w.nodes("recordGroups", {"_key": "big", "connectorId": APP})
    await w.child(("apps", APP), ("recordGroups", "big"))
    hidden = [vr(f"hid{i}", accessRule="RESTRICTED") for i in range(1000)]
    loose = [vr(f"loose{i}") for i in range(1000)]            # no edge to anything: nothing reaches them
    flat = [vr(f"flat{i}") for i in range(1000)]              # open, in a reached group, but they do not inherit from it: nothing opens them
    below = [vr(f"below{i}") for i in range(1000)]            # open children of a granted record that do not inherit from it
    await w.nodes("records", *hidden, *loose, *flat, *below, vr("mine1"), vr("mine2"), vr("mine3"), vr("granted_top"))
    await grant(w, "gm", "records", "granted_top")
    await w.p.batch_create_edges([{"from_collection": "records", "from_id": "granted_top", "to_collection": "records", "to_id": r["_key"], "relationshipType": "PARENT_CHILD"} for r in below], "nodeRelations")
    await w.p.batch_create_edges([{"from_collection": "recordGroups", "from_id": "big", "to_collection": "records", "to_id": r["_key"], "relationshipType": "PARENT_CHILD"} for r in flat], "nodeRelations")
    await w.p.batch_create_edges([{"from_collection": "recordGroups", "from_id": "big", "to_collection": "records", "to_id": r["_key"], "relationshipType": "PARENT_CHILD"}
                                  for r in (*hidden, vr("mine1"), vr("mine2"), vr("mine3"))], "nodeRelations")
    await w.p.batch_create_edges([{"from_collection": "records", "from_id": r["_key"], "to_collection": "recordGroups", "to_id": "big"}
                                  for r in (*hidden, vr("mine1"), vr("mine2"), vr("mine3"))], "inheritPermissions")
    asked: list[str] = []
    read = {"children": 0}
    real = w.p._kh_v3_accessible_rows
    real_q = w.p._q

    async def counting(fn, transaction, **kw):
        async def inner(tx):
            class Counted:
                def __getattr__(self, name: str):
                    return getattr(tx, name)

                async def fetch(self, sql: str, *args: object) -> list:
                    rows = await tx.fetch(sql, *args)
                    read["children"] += len(rows) if "access_children" in sql else 0
                    return rows
            return await fn(Counted())
        return await real_q(inner, transaction, **kw)
    w.p._q = counting

    async def spy(user_key: str, org_id: str, node_ids: list[str], *a: object, **k: object) -> list:
        asked.extend(node_ids)
        return await real(user_key, org_id, node_ids, *a, **k)
    w.p._kh_v3_accessible_rows = spy
    try:
        got = await w.p.get_accessible_virtual_record_ids("uid-gm", w.org, {"apps": [APP]})
    finally:
        w.p._kh_v3_accessible_rows = real
        w.p._q = real_q
    assert {"mine1", "mine2", "mine3"} <= set(got.values()) and not {r for r in got.values() if r.startswith(("hid", "loose", "flat", "below"))}
    assert len(asked) == len(set(asked)) <= 12, len(asked)            # the 4,000 records nobody reaches were never handed to the check
    assert read["children"] <= 40, read["children"]                    # nor were they read by the walk (only the children it can take come back)


async def test_a_timestamp_that_is_not_a_number_drops_that_record_only(w: World) -> None:
    await world(w)
    await w.p.update_node("open1", "records", {"sourceCreatedAtTimestamp": "yesterday"})
    await w.p.update_node("open2", "records", {"sourceCreatedAtTimestamp": 200})
    got = await w.p.get_accessible_virtual_record_ids("uid-gm", w.org, None, {"source_created_after_ms": 100})
    assert set(got.values()) == {"open2"}
    assert "open1" in set((await w.p.get_accessible_virtual_record_ids("uid-gm", w.org)).values())


async def test_an_app_level_connector_opens_to_everyone_it_gates_in_and_to_no_one_else(w: World) -> None:
    await world(w)
    await w.nodes("apps", {"_key": "wide", "type": "CONNECTOR", "permissionModel": "APP_LEVEL", "isActive": True})
    await w.nodes("records", vr("wide1", connectorId="wide", accessRule="RESTRICTED"), vr("wide2", connectorId="wide"))
    for k in ("wide1", "wide2"):
        await w.child(("apps", "wide"), ("records", k))
    await grant(w, "v", "apps", "wide")
    assert set((await w.p.get_accessible_virtual_record_ids("uid-v", w.org, {"apps": ["wide"]})).values()) == {"wide1", "wide2"}
    assert await w.p.get_accessible_virtual_record_ids("uid-gm", w.org, {"apps": ["wide"]}) == {}
    for who in ("v", "gm", "nogate"):
        got = await w.p.check_access(who, w.org, node_ids=["wide1", "wide2"])
        assert set((await w.p.get_accessible_virtual_record_ids(f"uid-{who}", w.org, {"apps": ["wide"]})).values()) == set(got.node_ids)


async def test_a_virtual_record_id_held_by_two_records_is_cited_by_the_one_the_user_may_open(w: World) -> None:
    await world(w)
    await w.nodes("records", vr("a_shut", virtualRecordId="v-pair", accessRule="RESTRICTED"), vr("z_open", virtualRecordId="v-pair"))
    for k in ("a_shut", "z_open"):
        await w.child(("recordGroups", "rg"), ("records", k))
    assert (await w.p.get_accessible_virtual_record_ids("uid-gm", w.org))["v-pair"] == "z_open"          # not the smaller id: that one is shut to gm
    await grant(w, "gm", "records", "a_shut")
    assert (await w.p.get_accessible_virtual_record_ids("uid-gm", w.org))["v-pair"] == "a_shut"


async def test_an_excluded_app_stays_out_even_when_a_group_reaches_it_and_internal_and_placeholder_records_are_not_served(w: World) -> None:
    await world(w)
    await w.nodes("records", vr("inner", isInternal=True), vr("held", isPlaceholder=True))
    for k in ("inner", "held"):
        await w.child(("recordGroups", "rg"), ("records", k))
    assert await w.p.check_record_access_with_details("uid-gm", w.org, "inner")          # the batch check opens an internal record; the scope does not serve it
    got = set((await w.p.get_accessible_virtual_record_ids("uid-gm", w.org)).values())
    assert "open1" in got and not {"inner", "held"} & got
    assert await w.p.get_accessible_virtual_record_ids("uid-gm", w.org, exclude_app_ids=frozenset({APP})) == {}
    assert "k1" in (await w.p.get_accessible_virtual_record_ids("uid-kbu", w.org, exclude_app_ids=frozenset({APP}))).values()
    await w.nodes("records", vr("kbx", connectorId="kb1", connectorName="KB"))          # a collection serves what was uploaded to it, not any record that names it
    await w.child(("apps", "kb1"), ("records", "kbx"))
    assert (await w.p.check_access("kbu", w.org, node_ids=["kbx"])).node_ids == {"kbx"}
    assert "kbx" not in (await w.p.get_accessible_virtual_record_ids("uid-kbu", w.org)).values()


async def test_the_details_name_only_the_roles_the_model_has(w: World) -> None:
    await world(w)
    await w.nodes("users", {"_key": "ed", "userId": "uid-ed"})
    await w.edge("permission", ("users", "ed"), ("apps", APP), type="USER", role="READER")
    await w.edge("permission", ("users", "ed"), ("records", "open1"), type="USER", role="EDITOR")
    assert (await w.p.check_record_access_with_details("uid-ed", w.org, "open1"))["permissions"][0]["relationship"] == "READER"      # not a role the model lacks
    await w.edge("permission", ("users", "ed"), ("records", "open2"), type="USER", role="WRITER")
    assert (await w.p.check_record_access_with_details("uid-ed", w.org, "open2"))["permissions"][0]["relationship"] == "WRITER"


async def test_a_grant_from_a_user_who_is_not_there_gives_nothing(w: World) -> None:
    await world(w)
    await w.edge("permission", ("users", "ghost"), ("records", "open1"), type="USER", role="OWNER")
    assert await ctx(w, "ghost", "open1", "record") == LOW


# ---- the walk against the batch check, on random graphs --------------------------------------------------------------------------------

def fuzz_graph(seed: int) -> tuple[dict, dict, list[str]]:
    """A random graph of one connector: nested groups (some deleted, some declaring a scope), records under records and groups by PARENT_CHILD and
    ATTACHMENT edges that inherit or not, all three access rules, belongsTo, grants by a user, a group, a team and the organization on records and groups,
    and (every third graph) a 55-group chain with a shortcut into the middle of it."""
    rng = random.Random(seed)
    nodes: dict[str, list[dict]] = {}
    edges: dict[str, list[dict]] = {}

    def node(coll: str, key: str, **doc: object) -> None:
        nodes.setdefault(coll, []).append({"_key": key, **doc})

    def edge(coll: str, f: tuple[str, str], t: tuple[str, str], **doc: object) -> None:
        edges.setdefault(coll, []).append({"from_collection": f[0], "from_id": f[1], "to_collection": t[0], "to_id": t[1], **doc})
    node("apps", "A", type="CONNECTOR", isActive=True, **({"permissionModel": "APP_LEVEL"} if seed % 9 == 4 else {}))
    for u in ("u1", "u2", "u3"):
        node("users", u, userId=f"uid-{u}")
    node("groups", "pg")
    node("teams", "tm")
    node("organizations", "O")
    for u in ("u1", "u2"):
        edge("permission", ("users", u), ("apps", "A"), type="USER", role="READER")        # u3 passes no gate
        edge("belongsTo", ("users", u), ("organizations", "O"), entityType="ORGANIZATION")
    edge("permission", ("users", "u1"), ("groups", "pg"), type="USER", role="READER")
    edge("permission", ("users", "u2"), ("teams", "tm"), type="USER", role="READER")
    groups: list[str] = []
    for i in range(rng.randint(5, 9)):
        g = f"g{i}"
        node("recordGroups", g, connectorId="A", accessRule=rng.choice(["OPEN", "OPEN", "STRICT", "RESTRICTED"]), isDeleted=rng.random() < 0.1,
             **({"permissionModel": "RECORD_GROUP_LEVEL"} if rng.random() < 0.3 else {}))
        parent = rng.choice(["A", *groups])
        edge("nodeRelations", ("apps" if parent == "A" else "recordGroups", parent), ("recordGroups", g), relationshipType="PARENT_CHILD")
        if rng.random() < 0.7:
            edge("inheritPermissions", ("recordGroups", g), ("apps" if parent == "A" else "recordGroups", parent))
        groups.append(g)
    records: list[str] = []
    if seed % 3 == 0:
        for i in range(55):
            node("recordGroups", f"c{i}", connectorId="A")
            prev = ("apps", "A") if i == 0 else ("recordGroups", f"c{i - 1}")
            edge("nodeRelations", prev, ("recordGroups", f"c{i}"), relationshipType="PARENT_CHILD")
            edge("inheritPermissions", ("recordGroups", f"c{i}"), prev)
        edge("nodeRelations", ("recordGroups", rng.choice(groups)), ("recordGroups", "c30"), relationshipType="PARENT_CHILD")      # a shortcut into the chain
        edge("inheritPermissions", ("recordGroups", "c30"), ("recordGroups", rng.choice(groups)))
        groups += ["c20", "c54"]
        for k in ("deepA", "deepB"):
            node("records", k, connectorId="A", virtualRecordId=f"v-{k}", indexingStatus="COMPLETED", accessRule=rng.choice(["OPEN", "RESTRICTED"]))
            edge("nodeRelations", ("recordGroups", "c54"), ("records", k), relationshipType="PARENT_CHILD")
            edge("inheritPermissions", ("records", k), ("recordGroups", "c54"))
            records.append(k)
    for i in range(rng.randint(18, 32)):
        r = f"r{i}"
        node("records", r, connectorId="A", virtualRecordId=f"v-{r}" if not (records and rng.random() < 0.1) else f"v-{records[-1]}",
             accessRule=rng.choice(["OPEN", "OPEN", "STRICT", "RESTRICTED"]), isDeleted=rng.random() < 0.08, isPlaceholder=rng.random() < 0.05,
             indexingStatus="COMPLETED" if rng.random() < 0.9 else "QUEUED")
        parent = rng.choice([("apps", "A"), *(("recordGroups", g) for g in groups), *(("records", x) for x in records)])
        edge("nodeRelations", parent, ("records", r), relationshipType=rng.choice(["PARENT_CHILD", "ATTACHMENT"]))
        if rng.random() < 0.6:
            edge("inheritPermissions", ("records", r), parent)
        if rng.random() < 0.4:
            edge("belongsTo", ("records", r), ("recordGroups", rng.choice(groups)))
        records.append(r)
    sources = {("users", "u1"): "USER", ("users", "u2"): "USER", ("groups", "pg"): "GROUP", ("teams", "tm"): "TEAM", ("organizations", "O"): "ORG"}
    for _ in range(rng.randint(4, 10)):
        src = rng.choice(list(sources))
        tgt = rng.choice([("records", x) for x in records] + [("recordGroups", g) for g in groups])
        edge("permission", src, tgt, type=sources[src], role="READER")
    return nodes, edges, records


async def test_the_scope_equals_what_the_batch_check_admits_on_random_graphs(w: World, pg_db) -> None:
    served = refused = 0
    for seed in range(25):
        org = _org()
        nodes, edges, records = fuzz_graph(seed)
        with in_org(org):
            fw = World(w.p, org)
            for coll, docs in nodes.items():
                await fw.nodes(coll, *docs)
            for coll, docs in edges.items():
                await w.p.batch_create_edges(docs, coll)
            docs = {d["_key"]: d for d in nodes["records"]}
            for user in ("u1", "u2", "u3"):
                admitted = await w.p.check_access(user, org, node_ids=records)
                want: dict[str, str] = {}
                for r in sorted(admitted.node_ids - admitted.placeholder_ids):
                    d = docs[r]
                    if d.get("indexingStatus") == "COMPLETED" and not d.get("isDeleted"):
                        want.setdefault(d["virtualRecordId"], r)
                got = await w.p.get_accessible_virtual_record_ids(f"uid-{user}", org)
                assert got == want, (seed, user, sorted(got.items() ^ want.items()))
                served += len(want)
                refused += len(records) - len(want)
            await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", o), write=True)
            await pg_db.run(lambda tx, o=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)
    assert served > 100 and refused > 100            # the graphs open something to somebody and shut something to everybody
