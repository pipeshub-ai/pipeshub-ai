"""Journey J-08: project inheritance.

Given a chat inherits visibility from its project
When the project viewer opens it, the ceiling is raised, and the project is flipped to private
Then the viewer can view it, editors can continue after the raise, and the flip removes chat and file access

Owning phase: PH-07 (80-implementation-plan section 5); PI-01, PI-06, PI-07, PH07-20, PH07-21.

What this lane covers, and where the other half is:
- Covered here, over real HTTP into the real Node API: project membership (direct and through a team),
  link / unlink / visibility on the chat, the project ceiling (`PATCH /projects/:id {projectChatAccess}`),
  the chat routes (open, send), `GET /api/v1/authz/explain` (the `via` path), `POST /api/v1/authz/explain/preview`
  (gains and loses before a change, then the change matches), and the file decision from Node's PDP
  (`POST /api/v1/authz/internal/check` with an `authz:check` token, as Python calls it).
- Not on this lane, because Python is the lane's scriptable fake: the Python record routes that ask the PDP
  (a project viewer's 404 on an unconsented file, the `aclVersion` cache). Those are Python unit tests
  (`tests/unit/modules/authz/`, `tests/unit/connectors/api/test_router_chat_content_access.py`).
"""

from __future__ import annotations

import dataclasses

import pytest
from bson import ObjectId

from helper.collab_stack import chats, collab
from helper.collab_stack.chats import error_of
from helper.collab_stack.pdp import attachment_allowed, explain, preview, principals
from helper.collab_stack.seeds import messages_of

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]


@pytest.fixture
def roster(stack, roster):  # noqa: ANN001, ANN201
    """The shared roster with a fresh owner: sharing is rate limited per sharer, so a journey does not spend the budget of the others."""
    return dataclasses.replace(roster, owner=collab.fresh_actor(stack, "J8"))

OK_FILE, NO_FILE = "rec-consented", "rec-withheld"


class Scene:
    """A project with a viewer, an editor and a team member, and the owner's project-visible chat with two files."""

    def __init__(self, stack, api, fake, roster, *, visible: bool = True) -> None:  # noqa: ANN001
        self.api, self.owner = api, roster.owner
        self.viewer, self.editor, self.team_member = roster.project_viewer, roster.project_editor, roster.project_team_member
        self.team = fake.add_team(roster.owner.org_id, "project team", {self.team_member.user_id: "READER"}).team_id
        self.project = chats.create_project(api, self.owner, "inherit")
        for who in (self.viewer, self.editor):
            role = "viewer" if who is self.viewer else "editor"
            assert chats.add_project_member(api, self.owner, self.project, who.user_id, role).status_code == 200
        assert chats.add_project_member(api, self.owner, self.project, self.team, "viewer", principal_type="team").status_code == 200
        self.chat = chats.create_chat(api, self.owner)
        for record, shared in ((OK_FILE, True), (NO_FILE, False)):
            sent = chats.send_message(api, self.owner, self.chat, "attach", attachments=[{"recordId": record, "recordName": record}], filesShared=shared)
            assert sent.status_code == 200, sent.text[:300]
        assert api.put(f"{chats.CONVERSATIONS}/{self.chat}/project", self.owner, json_body={"projectId": self.project}).status_code == 200
        if visible:
            self.set_visibility("project")
        self.members = (self.viewer, self.editor, self.team_member)

    def set_visibility(self, value: str) -> None:
        resp = self.api.patch(f"{chats.CONVERSATIONS}/{self.chat}/project-visibility", self.owner, json_body={"visibility": value})
        assert resp.status_code == 200, resp.text[:300]

    def ceiling(self, who, value: str):  # noqa: ANN001, ANN201
        return self.api.patch(f"/api/v1/projects/{self.project}", who, json_body={"projectChatAccess": value})

    def role_of(self, who) -> str:  # noqa: ANN001
        resp = explain(self.api, who, self.chat)
        assert resp.status_code == 200, resp.text[:300]
        return resp.json()["role"]

    def can_open(self, who) -> bool:  # noqa: ANN001
        resp = chats.get_chat(self.api, who, self.chat)
        assert resp.status_code in (200, 404), resp.text[:300]
        return resp.status_code == 200

    def can_read_file(self, who, record: str = OK_FILE) -> bool:  # noqa: ANN001
        return attachment_allowed(self.api, who, record, self.owner, self.chat)


def test_j08_project_members_read_the_chat_and_only_its_consented_files(stack, api, fake, roster) -> None:  # noqa: ANN001
    s = Scene(stack, api, fake, roster)

    for who in s.members:
        assert s.can_open(who), who.name
        assert s.role_of(who) == "viewer"
        assert s.can_read_file(who) is True, who.name
        assert s.can_read_file(who, NO_FILE) is False, who.name
    assert s.can_open(roster.stranger) is False and s.can_read_file(roster.stranger) is False

    # The explain route names the path: a project path for each, with the team member's through the team.
    for who in (s.viewer, s.editor):
        body = explain(api, who, s.chat).json()
        assert [(p["type"], p["role"]) for p in body["via"]] == [("project", "viewer")]
        assert body["via"][0]["ref"] == s.project
    via = explain(api, s.team_member, s.chat).json()["via"]
    assert [(p["type"], p["role"]) for p in via] == [("project", "viewer")]
    # Reading is all they get: sending needs a higher ceiling.
    for who in s.members:
        refused = chats.send_message(api, who, s.chat)
        assert error_of(refused) == (403, "CONVERSATION_READ_ONLY"), who.name


def test_j08_unlinked_or_private_chat_is_not_inherited(stack, api, fake, roster) -> None:  # noqa: ANN001
    s = Scene(stack, api, fake, roster, visible=False)

    for who in s.members:
        assert s.can_open(who) is False and s.role_of(who) == "none" and s.can_read_file(who) is False, who.name
    assert explain(api, s.viewer, s.chat).json()["via"] == []


def test_j08_raising_the_ceiling_lets_project_editors_continue_and_lowering_takes_it_back(stack, api, fake, roster) -> None:  # noqa: ANN001
    s = Scene(stack, api, fake, roster)

    def version() -> int:
        return stack.db["projects"].find_one({"name": "inherit"})["aclVersion"]

    before = version()

    # Only the project owner changes the ceiling.
    assert s.ceiling(s.editor, "editor").status_code == 403
    assert s.ceiling(s.viewer, "editor").status_code == 403
    assert version() == before

    assert s.ceiling(s.owner, "editor").status_code == 200
    assert version() == before + 1
    assert s.role_of(s.editor) == "editor"
    assert s.role_of(s.viewer) == "viewer" and s.role_of(s.team_member) == "viewer"
    assert explain(api, s.editor, s.chat).json()["via"][0]["role"] == "editor"
    sent = chats.send_message(api, s.editor, s.chat, "editor continues")
    assert sent.status_code == 200, sent.text[:300]
    assert str(messages_of(stack.db, s.chat)[-2]["authorUserId"]) == s.editor.user_id
    for who in (s.viewer, s.team_member):
        refused = chats.send_message(api, who, s.chat)
        assert error_of(refused) == (403, "CONVERSATION_READ_ONLY"), who.name
    assert s.can_read_file(s.viewer) is True and s.can_read_file(s.viewer, NO_FILE) is False

    assert s.ceiling(s.owner, "viewer").status_code == 200
    assert version() == before + 2
    assert s.role_of(s.editor) == "viewer"
    refused = chats.send_message(api, s.editor, s.chat)
    assert error_of(refused) == (403, "CONVERSATION_READ_ONLY")
    assert s.can_open(s.editor) is True


def test_j08_flipping_the_chat_to_private_removes_chat_and_file_access(stack, api, fake, roster) -> None:  # noqa: ANN001
    s = Scene(stack, api, fake, roster)
    assert s.ceiling(s.owner, "editor").status_code == 200
    assert all(s.can_open(who) and s.can_read_file(who) for who in s.members)

    s.set_visibility("private")

    for who in s.members:
        assert s.can_open(who) is False, who.name
        assert s.role_of(who) == "none"
        assert s.can_read_file(who) is False, who.name
        assert s.can_read_file(who, NO_FILE) is False
        gone = chats.send_message(api, who, s.chat)
        assert error_of(gone)[0] == 404, who.name
    assert s.can_open(s.owner) and s.role_of(s.owner) == "owner"

    s.set_visibility("project")
    assert all(s.can_open(who) and s.can_read_file(who) for who in s.members)


def test_j08_unlinking_or_removing_the_member_removes_access_too(stack, api, fake, roster) -> None:  # noqa: ANN001
    s = Scene(stack, api, fake, roster)
    assert s.can_read_file(s.viewer)
    assert api.delete(f"/api/v1/projects/{s.project}/members/{s.viewer.user_id}", s.owner).status_code in (200, 204)
    assert s.can_open(s.viewer) is False and s.can_read_file(s.viewer) is False
    assert s.can_read_file(s.editor) is True

    assert api.put(f"{chats.CONVERSATIONS}/{s.chat}/project", s.owner, json_body={"projectId": None}).status_code == 200
    for who in (s.editor, s.team_member):
        assert s.can_open(who) is False and s.can_read_file(who) is False, who.name


def test_j08_preview_says_what_a_change_will_do_and_the_change_does_it(stack, api, fake, roster) -> None:  # noqa: ANN001
    s = Scene(stack, api, fake, roster, visible=False)
    audit_before = stack.db["auditEvents"].count_documents({})
    chat_before = stack.db["chatSessions"].find_one({"_id": ObjectId(s.chat)})

    # Visibility -> project: every project member gains viewer; nobody loses.
    gain = preview(api, s.owner, s.chat, type="visibility", visibility="project")
    assert gain.status_code == 200, gain.text[:300]
    body = gain.json()
    assert principals(body["gains"]) == {s.viewer.user_id: "viewer", s.editor.user_id: "viewer", f"team:{s.team}": "viewer"}
    assert body["loses"] == [] and body["becomesReadOnly"] == []
    # A preview writes nothing.
    assert stack.db["chatSessions"].find_one({"_id": chat_before["_id"]}) == chat_before
    assert stack.db["auditEvents"].count_documents({}) == audit_before
    assert all(s.can_open(who) is False for who in (s.viewer, s.editor))

    s.set_visibility("project")
    for who in (s.viewer, s.editor):
        assert s.role_of(who) == "viewer" and s.can_open(who) and s.can_read_file(who)
    assert s.role_of(s.team_member) == "viewer"

    # Visibility -> private and unlink: all of them lose what they just gained.
    for change in ({"type": "visibility", "visibility": "private"}, {"type": "unlink"}):
        lose = preview(api, s.owner, s.chat, **change)
        assert lose.status_code == 200, lose.text[:300]
        assert principals(lose.json()["loses"]) == {s.viewer.user_id: "viewer", s.editor.user_id: "viewer", f"team:{s.team}": "viewer"}
        assert lose.json()["gains"] == []
        assert all(s.can_open(who) for who in (s.viewer, s.editor)), "a preview must not change access"

    s.set_visibility("private")
    assert [s.can_open(who) for who in (s.viewer, s.editor)] == [False, False]
    assert [s.role_of(who) for who in (s.viewer, s.editor, s.team_member)] == ["none"] * 3


def test_j08_preview_and_explain_are_for_the_right_people(stack, api, fake, roster) -> None:  # noqa: ANN001
    s = Scene(stack, api, fake, roster)

    for who in (s.editor, s.viewer, roster.stranger):
        refused = preview(api, who, s.chat, type="unlink")
        assert error_of(refused)[0] == (404 if who is roster.stranger else 403), who.name
        if who is not roster.stranger:
            assert error_of(refused)[1] == "CONVERSATION_OWNER_ONLY"
    assert explain(api, s.viewer, s.chat, subject=s.editor).status_code == 403
    assert explain(api, s.owner, s.chat, subject=s.editor).json()["role"] == "viewer"
    assert explain(api, roster.admin, s.chat, subject=s.editor).json()["role"] == "viewer"
