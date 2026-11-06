"""What is inside a knowledge base, the artifact gallery, agent templates and the legacy hub app on PostgreSQL, against a real database.

The answers are those of ``Neo4jProvider``. Access is asked in both directions: nobody lists what they hold no grant on (another user, another org,
a revoked grant, a team they are not in, a deleted or hidden item), and everybody lists what they do hold (directly or through a team).

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import pytest

from app.core import org_context
from app.core.org_context import MissingOrgContext
from app.services.graph_db.postgres.kb_listings import KbListingsMixin

from .test_access import World
from .test_identity import w as _identity_world  # noqa: F401
from .test_kb import FOLDER, folder, grant, kb, other_org, team, users


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_identity_world")


async def item(w: World, key: str, parent: str | None = None, kb_key: str = "kb1", **doc: object) -> None:
    await w.nodes("records", {"_key": key, "recordName": key, "mimeType": "text/plain", "connectorId": kb_key, "origin": "UPLOAD", "recordType": "FILE",
                              "externalRecordId": f"x-{key}", "createdAtTimestamp": 100, "updatedAtTimestamp": 200, **doc})
    await w.nodes("files", {"_key": key, "isFile": True, "name": key, "sizeInBytes": 10, "extension": "txt", "path": f"/{key}"})
    await w.edge("isOfType", ("records", key), ("files", key))
    await w.edge("belongsTo", ("records", key), ("apps", kb_key))
    if parent:
        await w.edge("nodeRelations", ("records", parent), ("records", key), relationshipType="PARENT_CHILD")


async def tree(w: World) -> None:
    """kb1: folders top (with sub, gone-sub, a file) and trash (deleted), root files r1 r2 and a deleted root file; kb2 has its own folder."""
    await kb(w, orgId=w.org)
    await kb(w, "kb2", orgId=w.org)
    await folder(w, "top")
    await folder(w, "zeta")
    await folder(w, "trash", isDeleted=True)
    await folder(w, "sub", parent="top")
    await folder(w, "gone-sub", parent="top", isDeleted=True)
    await folder(w, "other", "kb2")
    await item(w, "r1", recordName="Beta", createdAtTimestamp=1, updatedAtTimestamp=30, recordType="FILE", origin="UPLOAD", indexingStatus="COMPLETED", orgId=w.org)
    await item(w, "r2", recordName="alpha", createdAtTimestamp=3, updatedAtTimestamp=10, recordType="MAIL", origin="CONNECTOR", connectorName="GMAIL", orgId=w.org)
    await item(w, "dead", isDeleted=True, orgId=w.org)
    await item(w, "in-top", "top", recordName="doc", createdAtTimestamp=2, orgId=w.org)
    await item(w, "in-top-dead", "top", isDeleted=True, orgId=w.org)
    await item(w, "in-sub", "sub", orgId=w.org)
    await item(w, "elsewhere", kb_key="kb2", orgId=w.org)


# ---- the root and a folder ---------------------------------------------------------------------------------------------------------------

async def test_the_root_lists_live_root_folders_then_root_records_with_their_counts(w: World, org_ctx) -> None:
    await tree(w)
    r = await w.p.get_kb_children("kb1", 0, 10)
    assert r["success"] and r["paginationMode"] == "folders_first" and r["level"] == 1
    assert r["container"] == {"id": "kb1", "name": "KB1", "path": "/", "type": "kb", "webUrl": "/kb/kb1", "recordGroupId": "kb1"}
    assert [f["id"] for f in r["folders"]] == ["top", "zeta"]                              # not the trashed one, not a nested one, not another collection's
    top = r["folders"][0]
    assert top["counts"] == {"subfolders": 2, "records": 1, "totalItems": 3} and top["hasChildren"] and top["parent_id"] is None and top["recordGroupId"] == "kb1"
    assert r["folders"][1]["counts"] == {"subfolders": 0, "records": 0, "totalItems": 0} and not r["folders"][1]["hasChildren"]
    assert [x["id"] for x in r["records"]] == ["r1", "r2"]                                 # by name, codepoint order; not the deleted, nested or foreign ones
    assert r["records"][1]["connectorName"] == "GMAIL" and r["records"][0]["connectorName"] == "KNOWLEDGE_BASE" and r["records"][0]["isLatestVersion"] is True
    assert r["records"][0]["fileRecord"] == {"id": "r1", "name": "r1", "extension": "txt", "mimeType": None, "sizeInBytes": 10, "webUrl": None, "path": "/r1", "isFile": True}
    assert r["totalCount"] == 4 and r["counts"] == {"folders": 2, "records": 2, "totalItems": 4, "totalFolders": 2, "totalRecords": 2}
    assert r["availableFilters"] == {"recordTypes": ["FILE", "MAIL"], "origins": ["CONNECTOR", "UPLOAD"], "connectors": ["GMAIL", "KNOWLEDGE_BASE"], "indexingStatus": ["COMPLETED"]}

    assert [f["id"] for f in (await w.p.get_kb_children("kb1", 1, 2))["folders"]] == ["zeta"]          # folders first, then what is left of the page
    page = await w.p.get_kb_children("kb1", 1, 2)
    assert [x["id"] for x in page["records"]] == ["r1"] and page["counts"]["totalItems"] == 2
    page = await w.p.get_kb_children("kb1", 2, 5)
    assert page["folders"] == [] and [x["id"] for x in page["records"]] == ["r1", "r2"]
    page = await w.p.get_kb_children("kb1", 3, 5)
    assert [x["id"] for x in page["records"]] == ["r2"] and page["totalCount"] == 4
    assert (await w.p.get_kb_children("kb1", 9, 5))["records"] == []
    assert [x["id"] for x in (await w.p.get_kb_children("kb1", 0, 10, sort_by="created_at", sort_order="desc"))["records"]] == ["r2", "r1"]
    assert [x["id"] for x in (await w.p.get_kb_children("kb1", 0, 10, sort_by="updated_at"))["records"]] == ["r2", "r1"]
    assert [x["id"] for x in (await w.p.get_kb_children("kb1", 0, 10, sort_by="bogus", sort_order="sideways"))["records"]] == ["r1", "r2"]
    only = await w.p.get_kb_children("kb1", 0, 10, search="ALP", record_types=["MAIL"], origins=["CONNECTOR"], connectors=["GMAIL"])
    assert [x["id"] for x in only["records"]] == ["r2"] and only["folders"] == [] and only["totalCount"] == 1
    assert [x["id"] for x in (await w.p.get_kb_children("kb1", 0, 10, search="x-r1"))["records"]] == ["r1"]      # also by the external id
    assert [f["id"] for f in (await w.p.get_kb_children("kb1", 0, 10, search="ZET"))["folders"]] == ["zeta"]
    assert (await w.p.get_kb_children("kb1", 0, 10, indexing_status=["COMPLETED"]))["totalCount"] == 3
    assert await w.p.get_kb_children("nope", 0, 10) == {"success": False, "reason": "Knowledge base not found", "code": 404}
    with other_org():
        assert (await w.p.get_kb_children("kb1", 0, 10))["code"] == 404                    # another org sees no collection


async def test_a_folder_lists_its_subfolders_and_records_and_not_what_is_deleted_or_not_a_file(w: World) -> None:
    await tree(w)
    r = await w.p.get_folder_children("kb1", "top", 0, 10)
    assert r["success"] and [f["id"] for f in r["folders"]] == ["gone-sub", "sub"]          # a trashed subfolder is still listed (as on Neo4j)
    sub = r["folders"][1]
    assert sub["parent_id"] == "top" and sub["path"] is None and sub["counts"] == {"subfolders": 0, "records": 1, "totalItems": 1} and sub["hasChildren"]
    assert [x["id"] for x in r["records"]] == ["in-top"]
    assert r["counts"] == {"folders": 2, "records": 1, "totalItems": 3, "foldersShown": 2, "recordsShown": 1} and r["totalCount"] == 3
    assert r["availableFilters"]["recordTypes"] == ["FILE"]
    assert (await w.p.get_folder_children("kb1", "top", 1, 1))["folders"][0]["id"] == "sub"
    assert [x["id"] for x in (await w.p.get_folder_children("kb1", "top", 2, 5))["records"]] == ["in-top"]
    assert [x["id"] for x in (await w.p.get_folder_children("kb1", "top", 0, 5, search="doc"))["records"]] == ["in-top"]
    assert (await w.p.get_folder_children("kb1", "top", 0, 5, search="doc"))["folders"] == []
    assert (await w.p.get_folder_children("kb1", "nope", 0, 5))["totalCount"] == 0
    assert (await w.p.get_folder_children("kb1", "r1", 0, 5))["totalCount"] == 0           # a file has no children
    with other_org():
        assert (await w.p.get_folder_children("kb1", "top", 0, 5))["totalCount"] == 0


async def test_a_failed_listing_answers_a_failure_and_the_next_one_answers(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await tree(w)
    real = w.p._q

    async def boom(*a: object, **k: object) -> None:
        raise ConnectionError("down")
    monkeypatch.setattr(w.p, "_q", boom)
    assert (await w.p.get_kb_children("kb1", 0, 5)) == {"success": False, "reason": "down"}
    assert (await w.p.get_folder_children("kb1", "top", 0, 5))["success"] is False
    monkeypatch.setattr(w.p, "_q", real)
    assert (await w.p.get_kb_children("kb1", 0, 5))["success"] is True


# ---- the flat listing of a user -----------------------------------------------------------------------------------------------------------

async def test_the_flat_listing_shows_a_user_with_a_role_what_the_collection_holds_and_nobody_else(w: World) -> None:
    await tree(w)
    await users(w, "me", "viaTeam", "stranger", "outTeam")
    await grant(w, "me", "READER")
    await team(w, "viaTeam", "tm", grant_role="READER", member="READER")
    await w.nodes("teams", {"_key": "tm2"})
    await w.edge("permission", ("teams", "tm2"), ("apps", "kb1"), type="TEAM", role="WRITER")          # a team the user is not in
    await w.edge("permission", ("users", "outTeam"), ("teams", "tm9"), type="USER", role="OWNER")
    await item(w, "foreign", "top", orgId="another-org")                                               # a record of another org's id inside the folder
    empty = {"recordTypes": [], "origins": [], "connectors": [], "indexingStatus": [], "permissions": [], "folders": []}
    for who in ("stranger", "outTeam", "nobody"):
        assert await w.p.list_kb_records("kb1", who, w.org, 0, 10) == ([], 0, empty)
    for who, role in (("me", "READER"), ("viaTeam", "READER")):
        recs, total, filters = await w.p.list_kb_records("kb1", who, w.org, 0, 10, sort_by="createdAtTimestamp", sort_order="asc")
        assert [r["id"] for r in recs] == ["r1", "in-top", "r2", "in-sub"] and total == 4        # not the deleted, foreign-org or other collection's ones
        assert recs[0]["permission"] == {"role": role, "type": "USER"} and recs[0]["kb_id"] == "kb1" and recs[0]["folder"] is None
        assert recs[1]["folder"] == {"id": "top", "name": "top"} and recs[3]["folder"] == {"id": "sub", "name": "sub"}
        assert recs[0]["fileRecord"]["sizeInBytes"] == 10 and "path" not in recs[0]["fileRecord"] and recs[0]["connectorName"] == "KNOWLEDGE_BASE"
        assert filters["permissions"] == [role] and filters["recordTypes"] == ["FILE", "MAIL"]
        assert filters["folders"] == [{"id": f, "name": f} for f in ("gone-sub", "sub", "top", "trash", "zeta")]
    with other_org():
        assert (await w.p.list_kb_records("kb1", "me", w.org, 0, 10))[1] == 0
    # revoked: the grant goes and the listing goes with it; granted again it returns
    await w.p.remove_kb_permission("kb1", ["me"], [])
    assert (await w.p.list_kb_records("kb1", "me", w.org, 0, 10))[1] == 0
    await w.p.remove_kb_permission("kb1", [], ["tm"])
    assert (await w.p.list_kb_records("kb1", "viaTeam", w.org, 0, 10))[1] == 0
    await w.p.create_kb_permissions("kb1", "me", ["me"], [], "WRITER")
    recs, total, _ = await w.p.list_kb_records("kb1", "me", w.org, 0, 10)
    assert total == 4 and recs[0]["permission"]["role"] == "WRITER"


async def test_the_flat_listing_filters_sorts_pages_and_lists_a_record_in_each_of_its_folders(w: World) -> None:
    await tree(w)
    await users(w, "me")
    await grant(w, "me", "OWNER")
    await w.edge("nodeRelations", ("records", "zeta"), ("records", "in-top"), relationshipType="PARENT_CHILD")      # a second parent
    ids = lambda r: [x["id"] for x in r[0]]  # noqa: E731
    all_ = await w.p.list_kb_records("kb1", "me", w.org, 0, 10, sort_by="createdAtTimestamp", sort_order="asc")
    assert all_[1] == 5 and ids(all_) == ["r1", "in-top", "in-top", "r2", "in-sub"] and {x["folder"]["id"] for x in all_[0] if x["id"] == "in-top"} == {"top", "zeta"}
    assert ids(await w.p.list_kb_records("kb1", "me", w.org, 0, 10)) == ["in-sub", "r2", "in-top", "in-top", "r1"]
    assert ids(await w.p.list_kb_records("kb1", "me", w.org, 1, 2, sort_by="createdAtTimestamp", sort_order="asc")) == ["in-top", "in-top"]
    assert (await w.p.list_kb_records("kb1", "me", w.org, 1, 2))[1] == 5                               # the total ignores the page
    assert ids(await w.p.list_kb_records("kb1", "me", w.org, 0, 10, folder_id="top", sort_by="createdAtTimestamp", sort_order="asc")) == ["in-top"]   # a folder: its children only
    assert (await w.p.list_kb_records("kb1", "me", w.org, 0, 10, folder_id="nope"))[1] == 0
    assert ids(await w.p.list_kb_records("kb1", "me", w.org, 0, 10, search="ALPHA")) == ["r2"]
    assert ids(await w.p.list_kb_records("kb1", "me", w.org, 0, 10, search="x-r1")) == ["r1"]
    assert ids(await w.p.list_kb_records("kb1", "me", w.org, 0, 10, record_types=["MAIL"], origins=["CONNECTOR"], connectors=["GMAIL"], indexing_status=None)) == ["r2"]
    assert ids(await w.p.list_kb_records("kb1", "me", w.org, 0, 10, indexing_status=["COMPLETED"])) == ["r1"]
    assert sorted(ids(await w.p.list_kb_records("kb1", "me", w.org, 0, 10, date_from=2, date_to=3))) == ["in-top", "in-top", "r2"]
    assert (await w.p.list_kb_records("kb1", "me", w.org, 0, 10, date_from=500))[1] == 0
    assert (await w.p.list_kb_records("kb1", "me", w.org, -5, -1))[0] == []


async def test_a_failed_flat_listing_raises_and_the_next_call_answers(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await tree(w)
    await users(w, "me")
    await grant(w, "me", "READER")
    real = w.p._q

    async def boom(*a: object, **k: object) -> None:
        raise ConnectionError("down")
    monkeypatch.setattr(w.p, "_q", boom)
    with pytest.raises(ConnectionError):                      # an empty page would read as "nothing in it"
        await w.p.list_kb_records("kb1", "me", w.org, 0, 10)
    monkeypatch.setattr(w.p, "_q", real)
    assert (await w.p.list_kb_records("kb1", "me", w.org, 0, 10))[1] == 4


# ---- artifacts ------------------------------------------------------------------------------------------------------------------------------

async def artifact(w: World, key: str, role: str | None = "OWNER", who: str = "me", **art: object) -> None:
    rec = {k: art.pop(k) for k in list(art) if k in ("isDeleted", "orgId", "createdAtTimestamp", "updatedAtTimestamp")}
    await w.nodes("records", {"_key": key, "recordName": key, "recordType": "ARTIFACT", "orgId": w.org, "createdAtTimestamp": 10, "updatedAtTimestamp": 20, **rec})
    await w.nodes("artifacts", {"_key": key, "name": key.upper(), "artifactType": "REPORT", "conversationId": "c1", **art})
    await w.edge("isOfType", ("records", key), ("artifacts", key))
    if role:
        await w.edge("permission", ("users", who), ("records", key), type="USER", role=role)


async def test_the_gallery_lists_the_visible_artifacts_the_user_holds_a_grant_on_and_no_others(w: World) -> None:
    await users(w, "me", "you")
    await artifact(w, "mine", "OWNER", name="Zed", createdAtTimestamp=30, logicalName="plan")
    await artifact(w, "shared", "READER", name="Abe", createdAtTimestamp=20, artifactType="CHART")
    await artifact(w, "yours", "OWNER", "you")
    await artifact(w, "nogrant", None)
    await artifact(w, "tool", "OWNER", artifactType="TOOL_RESULT")
    await artifact(w, "notype", "OWNER", artifactType=None)
    await artifact(w, "temp", "OWNER", isTemporary=True)
    await artifact(w, "hidden", "OWNER", visibility="HIDDEN")
    await artifact(w, "dead", "OWNER", isDeleted=True)
    await artifact(w, "foreign", "OWNER", orgId="another-org")
    await w.nodes("teams", {"_key": "tm"})
    await w.edge("permission", ("users", "me"), ("teams", "tm"), type="USER", role="OWNER")
    await artifact(w, "viaTeam", None)
    await w.edge("permission", ("teams", "tm"), ("records", "viaTeam"), type="TEAM", role="OWNER")      # only the user's own grant counts
    ids = lambda r: [x["id"] for x in r[0]]  # noqa: E731
    r = await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, None, None, None, "createdAtTimestamp", "desc")
    assert ids(r) == ["mine", "shared"] and r[1] == {"mine": 1, "shared": 1}
    assert r[0][0]["artifactDoc"]["name"] == "Zed" and r[0][0]["permission"] == {"role": "OWNER", "type": "USER"} and r[0][1]["artifactDoc"]["artifactType"] == "CHART"
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, None, None, None, "name", "asc")) == ["shared", "mine"]
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, None, None, None, "artifactType", "asc")) == ["shared", "mine"]
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, None, None, None, "createdAtTimestamp", "asc")) == ["shared", "mine"]
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 1, 1, None, None, None, None, None, "createdAtTimestamp", "desc")) == ["shared"]
    mine = await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, None, None, None, "createdAtTimestamp", "desc", "mine")
    assert ids(mine) == ["mine"] and mine[1] == {"mine": 1, "shared": 1}                                  # the counts ignore the tab
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, None, None, None, "createdAtTimestamp", "desc", "shared")) == ["shared"]
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, "PLAN", None, None, None, None, "name", "asc")) == ["mine"]
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, ["CHART"], None, None, None, "name", "asc")) == ["shared"]
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, [], None, None, None, "name", "asc")) == []          # an empty type list matches nothing
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, "c2", None, None, "name", "asc")) == []
    assert ids(await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, None, 25, 35, "name", "asc")) == ["mine"]
    assert ids(await w.p.list_accessible_artifacts("you", w.org, 0, 10, None, None, None, None, None, "name", "asc")) == ["yours"]
    assert (await w.p.list_accessible_artifacts("nobody", w.org, 0, 10, None, None, None, None, None, "name", "asc")) == ([], {"mine": 0, "shared": 0})
    d = await w.p.get_artifact_detail("me", w.org, "mine")
    assert d["id"] == "mine" and d["artifactDoc"]["logicalName"] == "plan" and d["permission"]["role"] == "OWNER"
    for gone in ("yours", "nogrant", "tool", "temp", "hidden", "dead", "foreign", "viaTeam", "nope"):
        assert await w.p.get_artifact_detail("me", w.org, gone) is None
    with other_org():
        assert await w.p.get_artifact_detail("me", w.org, "mine") is None
        assert (await w.p.list_accessible_artifacts("me", w.org, 0, 10, None, None, None, None, None, "name", "asc"))[0] == []
    await w.p.delete_edges_from("me", "users", "permission")                                              # revoked, then granted again
    assert await w.p.get_artifact_detail("me", w.org, "mine") is None
    await w.edge("permission", ("users", "me"), ("records", "mine"), type="USER", role="READER")
    assert (await w.p.get_artifact_detail("me", w.org, "mine"))["permission"]["role"] == "READER"


# ---- agent templates --------------------------------------------------------------------------------------------------------------------------

async def tpl(w: World, key: str = "t1", **doc: object) -> None:
    await w.nodes("agentTemplates", {"_key": key, "name": key, "isActive": True, **doc})


async def test_a_template_opens_for_its_own_grant_or_a_team_the_user_is_in_and_no_one_else(w: World) -> None:
    await users(w, "own", "member", "reader", "stranger", "both")
    await tpl(w)
    await w.edge("permission", ("users", "own"), ("agentTemplates", "t1"), type="USER", role="OWNER", id="perm-1", createdAtTimestamp=5)
    await w.edge("permission", ("users", "reader"), ("agentTemplates", "t1"), type="USER", role="READER")
    await w.edge("permission", ("users", "both"), ("agentTemplates", "t1"), type="USER", role="READER")
    await w.nodes("teams", {"_key": "tm"}, {"_key": "tm2"})
    await w.edge("permission", ("users", "member"), ("teams", "tm"), type="USER", role="READER")
    await w.edge("permission", ("users", "both"), ("teams", "tm"), type="USER", role="READER")
    await w.edge("permission", ("teams", "tm"), ("agentTemplates", "t1"), type="TEAM", role="WRITER")
    await w.edge("permission", ("teams", "tm2"), ("agentTemplates", "t1"), type="TEAM", role="OWNER")          # a team the user is not in
    await w.edge("permission", ("users", "stranger"), ("teams", "tm2"), type="ORG", role="OWNER")                 # not a member (not a USER edge)
    t = await w.p.get_template("t1", "own")
    assert (t["access_type"], t["user_role"], t["permission_id"], t["permission_created_at"]) == ("INDIVIDUAL", "OWNER", "perm-1", 5)
    assert (t["can_edit"], t["can_delete"], t["can_share"], t["can_view"]) == (True, True, True, True) and t["_key"] == "t1" and t["name"] == "t1"
    t = await w.p.get_template("t1", "reader")
    assert (t["access_type"], t["can_edit"], t["can_delete"], t["can_share"]) == ("INDIVIDUAL", False, False, False)
    t = await w.p.get_template("t1", "member")
    assert (t["access_type"], t["user_role"], t["can_edit"], t["can_delete"]) == ("TEAM", "WRITER", True, False)
    assert (await w.p.get_template("t1", "both"))["access_type"] == "INDIVIDUAL"                                    # the user's own grant first
    assert await w.p.get_template("t1", "stranger") is None and await w.p.get_template("t1", "nobody") is None and await w.p.get_template("nope", "own") is None
    with other_org():
        assert await w.p.get_template("t1", "own") is None
    await w.p.update_node("t1", "agentTemplates", {"isDeleted": True})                                             # a deleted template opens for no one
    assert await w.p.get_template("t1", "own") is None and await w.p.get_template("t1", "member") is None
    await w.p.update_node("t1", "agentTemplates", {"isDeleted": False})
    assert (await w.p.get_template("t1", "member"))["access_type"] == "TEAM"
    await w.p.delete_edges_from("member", "users", "permission")                                                    # leaves the team: loses it
    assert await w.p.get_template("t1", "member") is None


async def test_a_clone_is_a_new_active_template_with_no_owner_and_no_grants(w: World) -> None:
    await users(w, "own")
    await tpl(w, isDeleted=True, deletedAtTimestamp=1, deletedByUserId="own", createdBy="own", updatedByUserId="own", description="d")
    await w.edge("permission", ("users", "own"), ("agentTemplates", "t1"), type="USER", role="OWNER")
    key = await w.p.clone_agent_template("t1")
    assert key and key != "t1"
    c = await w.p.get_document(key, "agentTemplates")
    assert c["_key"] == key and c["id"] == key and c["name"] == "t1" and c["description"] == "d" and c["isActive"] is True and c["isDeleted"] is False
    assert all(k not in c for k in ("deletedAtTimestamp", "deletedByUserId", "createdBy", "updatedByUserId")) and c["createdAtTimestamp"] > 0
    assert await w.p.get_template(key, "own") is None                                                                 # nobody holds a grant on the copy
    assert (await w.p.get_document("t1", "agentTemplates"))["isDeleted"] is True                                      # the original is untouched
    assert await w.p.clone_agent_template("nope") is None
    with other_org():
        assert await w.p.clone_agent_template("t1") is None


# ---- the legacy hub app ----------------------------------------------------------------------------------------------------------------------

async def test_the_hub_app_goes_with_its_edges_unless_something_still_points_at_it(w: World, org_ctx) -> None:
    hub = f"knowledgeBase_{w.org}"
    await users(w, "me")
    await w.nodes("apps", {"_key": hub, "type": "KB"}, {"_key": "keep", "type": "KB"})
    await w.edge("userAppRelation", ("users", "me"), ("apps", hub))
    await w.edge("orgAppRelation", ("organizations", w.org), ("apps", hub))
    await w.edge("belongsTo", ("records", "r"), ("apps", hub))
    assert await w.p.delete_kb_hub_app(w.org) is False                                   # a belongsTo edge still points at it
    assert await w.p.get_document(hub, "apps") is not None and len(await w.p.get_edges_to_node(f"apps/{hub}", "belongsTo")) == 1
    await w.p.delete_edges_to(hub, "apps", "belongsTo")
    await w.edge("permission", ("users", "me"), ("apps", hub), type="USER", role="OWNER")
    assert await w.p.delete_kb_hub_app(w.org) is False                                   # so does a permission edge
    await w.p.delete_edges_to(hub, "apps", "permission")
    with other_org():
        assert await w.p.delete_kb_hub_app(w.org) is True                                # another org has no such app: nothing to do, nothing touched
    assert await w.p.get_document(hub, "apps") is not None
    assert await w.p.delete_kb_hub_app(w.org) is True
    assert await w.p.get_document(hub, "apps") is None and await w.p.get_document("keep", "apps") is not None
    assert await w.p.get_edges_to_node(f"apps/{hub}", "userAppRelation") == [] and await w.p.get_edges_to_node(f"apps/{hub}", "orgAppRelation") == []
    assert await w.p.delete_kb_hub_app(w.org) is True                                    # again: nothing left, still true


# ---- no org ---------------------------------------------------------------------------------------------------------------------------------

NAMES = sorted(n for n, f in vars(KbListingsMixin).items() if not n.startswith("__") and callable(f) and n != "_children")
CALLS = {
    "get_kb_children": ("k", 0, 1), "get_folder_children": ("k", "f", 0, 1), "list_kb_records": ("k", "u", "o", 0, 1),
    "list_accessible_artifacts": ("u", "o", 0, 1, None, None, None, None, None, "name", "asc"), "get_artifact_detail": ("u", "o", "a"),
    "get_template": ("t", "u"), "clone_agent_template": ("t",), "delete_kb_hub_app": ("o",),
}


def test_every_method_of_the_module_is_in_the_no_org_cases() -> None:
    assert set(NAMES) == set(CALLS)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*CALLS[name])
    with org_context.system("test.no-org"), pytest.raises(Exception, match="(?i)system|org"):
        await getattr(pg_provider, name)(*CALLS[name])


def test_the_folder_mime_type_is_the_one_the_fixtures_write() -> None:
    assert FOLDER
