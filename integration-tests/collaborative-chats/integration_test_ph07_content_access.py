"""PH-07 on the real stack: the authz routes (explain, preview, project ceiling) and Node's internal chat-content check.

Real HTTP into the real Node API. Covered here: the Node half of PH-07. Not covered here, because Python is the lane's fake:
the Python record read routes that ask the PDP and the `aclVersion` cache (Python unit tests under `tests/unit/modules/authz/`
and `tests/unit/connectors/api/`), and the graph-side work (edge cleanup migration, project to KB reconcile: graph integration tests).
The journeys that drive these routes through real state are J-07 (consent) and J-08 (project inheritance).
"""

from __future__ import annotations

import copy
import re

import dataclasses

import pytest
from bson import ObjectId

from helper.collab_stack import chats, collab
from helper.collab_stack.chats import error_of
from helper.collab_stack.identity import Directory
from helper.collab_stack.pdp import check_body, explain, preview, raw_check, service_token
from helper.collab_stack.seeds import insert_project, project_member

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]


@pytest.fixture
def roster(stack, roster):  # noqa: ANN001, ANN201
    """The shared roster with a fresh owner: sharing is rate limited per sharer, so a journey does not spend the budget of the others."""
    return dataclasses.replace(roster, owner=collab.fresh_actor(stack, "P7"))

NOT_FOUND_CODES = {"NOT_FOUND", "CONVERSATION_NOT_FOUND"}


def shared_with_team(stack, api, fake, roster):  # noqa: ANN001, ANN201
    """Owner's chat shared with a team whose only member is TeamReader, and directly with Reader."""
    team = fake.add_team(roster.owner.org_id, "readers", {roster.owner.user_id: "OWNER", roster.team_reader.user_id: "READER"}).team_id
    chat = chats.create_chat(api, roster.owner)
    collab.ok(collab.put(api, roster.owner, chat, collab.user(roster.read_recipient, "read"), collab.team(team, "read")))
    return chat, team


# ---- explain ------------------------------------------------------------------------------------


def test_ph07_explain_is_visible_to_self_owner_and_admin_only(stack, api, fake, roster) -> None:  # noqa: ANN001
    chat, _ = shared_with_team(stack, api, fake, roster)
    reader, owner = roster.read_recipient, roster.owner

    own = explain(api, reader, chat)
    assert own.status_code == 200 and own.json() == {"role": "viewer", "via": [{"type": "direct", "ref": reader.user_id, "role": "viewer"}]}
    assert explain(api, reader, chat, subject=reader).json() == own.json()
    assert explain(api, owner, chat).json()["role"] == "owner"
    by_owner = explain(api, owner, chat, subject=reader)
    assert by_owner.status_code == 200 and by_owner.json() == own.json()
    assert explain(api, roster.admin, chat, subject=reader).json() == own.json()
    # A stranger asking about their own access gets "none", not an error.
    assert explain(api, roster.stranger, chat).json() == {"role": "none", "via": []}


def test_ph07_explaining_someone_else_is_403_and_gives_no_oracle(stack, api, fake, roster) -> None:  # noqa: ANN001
    chat, _ = shared_with_team(stack, api, fake, roster)
    missing = str(ObjectId())
    asks = [(roster.read_recipient, chat), (roster.stranger, chat), (roster.stranger, missing), (roster.read_recipient, missing)]

    answers = [explain(api, who, cid, subject=roster.owner) for who, cid in asks]

    assert {r.status_code for r in answers} == {403}
    assert len({re.sub(r'"requestId":"[^"]*"', "", r.text).replace(cid, "<id>") for r, (_, cid) in zip(answers, asks, strict=True)}) == 1, (
        "403 differs between a real and a missing chat"
    )
    # A user from another org is refused the same way and never reaches a chat of this org.
    assert explain(api, roster.other_org, chat).json() == {"role": "none", "via": []}
    assert explain(api, roster.other_org, chat, subject=roster.owner).status_code == 403


def test_ph07_team_references_are_redacted_for_non_members(stack, api, fake, roster) -> None:  # noqa: ANN001
    chat, team = shared_with_team(stack, api, fake, roster)
    member, owner = roster.team_reader, roster.owner

    assert explain(api, member, chat).json()["via"] == [{"type": "team", "ref": team, "role": "viewer"}]
    # The owner is a member of the team (needed to share with it), so sees the reference; an admin outside the team does not.
    assert explain(api, owner, chat, subject=member).json()["via"] == [{"type": "team", "ref": team, "role": "viewer"}]
    assert explain(api, roster.admin, chat, subject=member).json()["via"] == [{"type": "team", "ref": None, "role": "viewer"}]
    # Once the owner has left the team the reference is hidden from the owner too.
    del fake.teams[team].members[owner.user_id]
    stack.flush_caches()
    assert explain(api, owner, chat, subject=member).json()["via"] == [{"type": "team", "ref": None, "role": "viewer"}]


@pytest.mark.parametrize("extra", [{"teamIds": "t1"}, {"teamIds[]": "t1"}, {"unknown": "x"}])
def test_ph07_explain_refuses_client_supplied_team_ids(stack, api, fake, roster, extra) -> None:  # noqa: ANN001
    chat, team = shared_with_team(stack, api, fake, roster)
    resp = api.get("/api/v1/authz/explain", roster.stranger, params={"resource": f"chat:{chat}", **extra})
    assert resp.status_code == 400, resp.text[:300]
    resp = api.get("/api/v1/authz/explain", roster.stranger, params={"resource": f"chat:{chat}", "teamIds": team})
    assert resp.status_code == 400


def test_ph07_explain_and_preview_validate_their_references(stack, api, fake, roster) -> None:  # noqa: ANN001
    chat, _ = shared_with_team(stack, api, fake, roster)
    owner = roster.owner
    assert api.get("/api/v1/authz/explain", owner, params={"resource": chat}).status_code == 400
    assert api.get("/api/v1/authz/explain", owner, params={"resource": f"chat:{chat}", "subject": roster.read_recipient.user_id}).status_code == 400
    assert api.get("/api/v1/authz/explain", owner).status_code == 400
    body = {"resource": f"chat:{chat}", "change": {"type": "unlink"}}
    assert api.post("/api/v1/authz/explain/preview", owner, json_body={**body, "teamIds": ["t"]}).status_code == 400
    assert api.post("/api/v1/authz/explain/preview", owner, json_body={**body, "change": {"type": "unlink", "teamIds": ["t"]}}).status_code == 400
    assert api.post("/api/v1/authz/explain/preview", owner, json_body={**body, "change": {"type": "nope"}}).status_code == 400
    assert api.get("/api/v1/authz/explain", params={"resource": f"chat:{chat}"}).status_code == 401


# ---- preview ------------------------------------------------------------------------------------


def test_ph07_preview_writes_nothing(stack, api, fake, roster) -> None:  # noqa: ANN001
    owner = roster.owner
    project = chats.create_project(api, owner, "p")
    assert chats.add_project_member(api, owner, project, roster.project_viewer.user_id).status_code == 200
    chat = chats.create_chat(api, owner)
    assert api.put(f"{chats.CONVERSATIONS}/{chat}/project", owner, json_body={"projectId": project}).status_code == 200

    def snapshot() -> dict:
        return {
            "chats": list(stack.db["chatSessions"].find({})),
            "projects": list(stack.db["projects"].find({})),
            "messages": stack.db["chatSessionMessages"].count_documents({}),
            "audit": stack.db["auditEvents"].count_documents({}),
        }

    before = snapshot()
    for change in ({"type": "visibility", "visibility": "project"}, {"type": "unlink"}, {"type": "link", "projectId": project}):
        resp = preview(api, owner, chat, **change)
        assert resp.status_code == 200, (change, resp.text[:300])
        assert set(resp.json()) == {"gains", "loses", "becomesReadOnly", "truncated"}
    assert snapshot() == before
    # Previews are for the owner of the chat; a project the owner cannot open is a 404, not a member list.
    other_project = insert_project(stack.db, "foreign", roster.stranger, [project_member(roster.project_editor, "editor", roster.stranger)])
    hidden = preview(api, owner, chat, type="link", projectId=str(other_project))
    assert hidden.status_code == 404 and "ProjectEditor" not in hidden.text and roster.project_editor.user_id not in hidden.text
    assert preview(api, owner, str(ObjectId()), type="unlink").status_code == 404


# ---- project ceiling ----------------------------------------------------------------------------


def project_doc(stack, pid: str) -> dict:  # noqa: ANN001
    return stack.db["projects"].find_one({"_id": ObjectId(pid)})


def test_ph07_project_ceiling_is_owner_only_bumps_acl_version_and_is_audited(stack, api, roster) -> None:  # noqa: ANN001
    owner, editor = roster.owner, roster.project_editor
    pid = chats.create_project(api, owner, "ceiling")
    assert chats.add_project_member(api, owner, pid, editor.user_id, "editor").status_code == 200
    start = project_doc(stack, pid)

    refused = api.patch(f"/api/v1/projects/{pid}", editor, json_body={"projectChatAccess": "editor"})
    assert refused.status_code == 403
    assert api.patch(f"/api/v1/projects/{pid}", owner, json_body={"projectChatAccess": "admin"}).status_code == 400
    after_refusals = project_doc(stack, pid)
    assert after_refusals["projectChatAccess"] == start["projectChatAccess"] == "viewer" and after_refusals["aclVersion"] == start["aclVersion"]

    stack.db["auditEvents"].delete_many({})
    version = project_doc(stack, pid)["aclVersion"]
    changed = api.patch(f"/api/v1/projects/{pid}", owner, json_body={"projectChatAccess": "editor"})
    assert changed.status_code == 200, changed.text[:300]
    now = project_doc(stack, pid)
    assert now["projectChatAccess"] == "editor" and now["aclVersion"] == version + 1
    (event,) = list(stack.db["auditEvents"].find({"action": "project.chatAccessChanged"}))
    assert event["targetId"] == pid and str(event["actorUserId"]) == owner.user_id
    assert event["before"]["projectChatAccess"] == "viewer" and event["after"]["projectChatAccess"] == "editor"
    assert event["after"]["aclVersion"] == version + 1

    # Setting the value it already has is not a change.
    same = api.patch(f"/api/v1/projects/{pid}", owner, json_body={"projectChatAccess": "editor"})
    assert same.status_code == 200
    assert project_doc(stack, pid)["aclVersion"] == version + 1
    assert stack.db["auditEvents"].count_documents({"action": "project.chatAccessChanged"}) == 1
    # Other settings stay open to a project editor and do not touch the ceiling.
    assert api.patch(f"/api/v1/projects/{pid}", editor, json_body={"name": "renamed"}).status_code == 200
    assert project_doc(stack, pid)["projectChatAccess"] == "editor"


# ---- internal check -----------------------------------------------------------------------------


def _consented_chat(stack, api, roster):  # noqa: ANN001, ANN201
    owner, b, c = roster.owner, roster.write_recipient, roster.read_recipient
    chat = chats.create_chat(api, owner)
    chats.share_as_writer(api, owner, chat, b)
    assert chats.share(api, owner, chat, c).status_code == 200
    sent = chats.send_message(api, b, chat, "x", attachments=[{"recordId": "rec-1", "recordName": "r"}], filesShared=True)
    assert sent.status_code == 200, sent.text[:300]
    return chat


def test_ph07_internal_check_needs_the_authz_check_scope(stack, api, roster) -> None:  # noqa: ANN001
    chat = _consented_chat(stack, api, roster)
    body = check_body(roster.read_recipient, "chatAttachment", "rec-1", roster.write_recipient.user_id, chat)
    org = roster.owner.org_id

    assert raw_check(api, body).json()["allow"] is True
    assert raw_check(api, body, token=Directory.session_token(roster.owner)).status_code == 401
    assert raw_check(api, body, token=Directory.session_token(roster.admin)).status_code == 401
    assert raw_check(api, body, token=Directory.scoped_token(roster.owner)).status_code == 401  # conversation:create
    assert raw_check(api, body, token=Directory.conversation_permissions_token(roster.owner)).status_code == 401
    assert raw_check(api, body, token=service_token(org, ("authz:other",))).status_code == 401
    assert raw_check(api, body, token=service_token(org, expires_in=-5)).status_code == 401
    assert api.post("/api/v1/authz/internal/check", json_body=body).status_code == 401
    assert raw_check(api, body, token="garbage").status_code == 401


def test_ph07_internal_check_refuses_a_token_for_another_org(stack, api, roster) -> None:  # noqa: ANN001
    chat = _consented_chat(stack, api, roster)
    body = check_body(roster.read_recipient, "chatAttachment", "rec-1", roster.write_recipient.user_id, chat)

    foreign = raw_check(api, body, token=service_token(roster.other_org.org_id))
    assert foreign.status_code == 403, foreign.text[:300]
    assert "allow" not in foreign.text
    # And the body org decides the lookup: the right token for globex, asking about acme's chat and user, is a plain deny.
    other_body = check_body(roster.other_org, "chatAttachment", "rec-1", roster.write_recipient.user_id, chat)
    assert raw_check(api, other_body, token=service_token(roster.owner.org_id)).status_code == 403
    assert raw_check(api, other_body).json() == {"allow": False, "aclVersion": None}
    stranger_in_other_org = check_body(roster.read_recipient, "chatAttachment", "rec-1", roster.write_recipient.user_id, chat, org_id=roster.other_org.org_id)
    assert raw_check(api, stranger_in_other_org).json() == {"allow": False, "aclVersion": None}


def test_ph07_unknown_and_denied_records_look_the_same(stack, api, roster) -> None:  # noqa: ANN001
    chat = _consented_chat(stack, api, roster)
    outsider_chat = chats.create_chat(api, roster.other_org)
    b, c, owner = roster.write_recipient, roster.read_recipient, roster.owner
    denied_shapes = [
        check_body(roster.stranger, "chatAttachment", "rec-1", b.user_id, chat),  # real record, no access
        check_body(c, "chatAttachment", "rec-unknown", b.user_id, chat),  # unknown record
        check_body(c, "chatAttachment", "rec-1", b.user_id, str(ObjectId())),  # unknown conversation
        check_body(c, "chatAttachment", "rec-1", b.user_id, outsider_chat),  # another org's conversation
        check_body(c, "chatAttachment", "rec-1", owner.user_id, chat),  # not the uploader's record
        check_body(roster.disabled, "chatAttachment", "rec-1", b.user_id, chat),  # disabled user
        check_body(c, "chatArtifact", "art", b.user_id, chat, run_id="nope"),  # unknown run
        check_body(c, "chatArtifact", "art", b.user_id, None),
    ]
    unknown_user = copy.deepcopy(denied_shapes[1])
    unknown_user["userId"] = str(ObjectId())
    denied_shapes.append(unknown_user)

    answers = [raw_check(api, body) for body in denied_shapes]

    assert [(r.status_code, r.json()) for r in answers] == [(200, {"allow": False, "aclVersion": None})] * len(denied_shapes)
    assert {r.text for r in answers} == {answers[0].text}
    ok = raw_check(api, check_body(c, "chatAttachment", "rec-1", b.user_id, chat))
    assert ok.status_code == 200 and ok.json()["allow"] is True and isinstance(ok.json()["aclVersion"], int)


def test_ph07_internal_check_rejects_a_malformed_body(stack, api, roster) -> None:  # noqa: ANN001
    body = check_body(roster.read_recipient, "chatAttachment", "rec-1", roster.owner.user_id)
    assert raw_check(api, {**body, "action": "write"}).status_code == 400
    assert raw_check(api, {**body, "resource": {**body["resource"], "type": "record"}}).status_code == 400
    assert raw_check(api, {**body, "orgId": "x"}).status_code == 400
    assert raw_check(api, {k: v for k, v in body.items() if k != "userId"}).status_code == 400


# ---- flag off -----------------------------------------------------------------------------------


def test_ph07_flag_off_authz_user_routes_are_404_and_ceiling_is_ignored(stack, api, fake, roster, flag_off) -> None:  # noqa: ANN001
    owner = roster.owner
    pid = chats.create_project(api, owner, "off")
    chat = chats.create_chat(api, owner)
    seen = {"projectChatAccess": project_doc(stack, pid).get("projectChatAccess"), "aclVersion": project_doc(stack, pid)["aclVersion"]}

    for who in (owner, roster.admin, roster.stranger):
        assert error_of(explain(api, who, chat))[0] == 404, who.name
        assert error_of(explain(api, who, chat, subject=roster.read_recipient))[0] == 404
        assert error_of(preview(api, who, chat, type="unlink"))[0] == 404
    assert api.get("/api/v1/authz/explain", params={"resource": f"chat:{chat}"}).status_code in (401, 404)

    patched = api.patch(f"/api/v1/projects/{pid}", owner, json_body={"projectChatAccess": "editor", "name": "still renamed"})
    assert patched.status_code == 200, patched.text[:300]
    doc = project_doc(stack, pid)
    assert doc["name"] == "still renamed"
    assert {"projectChatAccess": doc.get("projectChatAccess"), "aclVersion": doc["aclVersion"]} == seen
    assert stack.db["auditEvents"].count_documents({"action": "project.chatAccessChanged", "targetId": pid}) == 0


def test_ph07_flag_off_internal_check_keeps_the_pre_collaboration_rule(stack, api, roster, flag_off) -> None:  # noqa: ANN001
    """Flag off: a read recipient opens what the session owner attached, needs no consent, and loses it on unshare; others' uploads stay closed."""
    owner, b, c = roster.owner, roster.write_recipient, roster.read_recipient
    chat = chats.create_chat(api, owner, "q", attachments=[{"recordId": "rec-own", "recordName": "r"}])
    assert chats.share(api, owner, chat, c).status_code == 200

    def allowed(subject, record, uploader, **kw):  # noqa: ANN001, ANN202
        return raw_check(api, check_body(subject, "chatAttachment", record, uploader.user_id, chat, **kw)).json()["allow"]

    assert allowed(c, "rec-own", owner) is True
    assert allowed(roster.stranger, "rec-own", owner) is False
    assert allowed(c, "rec-own", b) is False
    art = raw_check(api, check_body(c, "chatArtifact", "art", owner.user_id, chat))
    assert art.json()["allow"] is True  # no turn consent flag off
    assert raw_check(api, check_body(c, "chatArtifact", "art", owner.user_id, chat, kind={"visibility": "STAGING"})).json()["allow"] is False
    assert chats.unshare(api, owner, chat, c).status_code == 200
    assert allowed(c, "rec-own", owner) is False
