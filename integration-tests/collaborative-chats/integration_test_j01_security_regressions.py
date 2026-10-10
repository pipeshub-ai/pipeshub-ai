"""Journey J-01: access-control regressions (PH-01), observed over HTTP.

Given the PH-01 fixes (S1 to S4)
When each is exercised through the public Node API on the real stack, with the Python services faked
Then the foreign team is refused, the share validator keeps accessLevel, a read recipient cannot reach
a write-gated operation, a bare `isShared` chat does not open regenerate to a stranger, attachment ids
are validated, no signed URL is persisted, and disabled or foreign-org internal callers are refused

Owning phase: PH-01 (80-implementation-plan section 5). Spec: PH-01 section 4, J-01.

Not observable on this lane, covered where the graph lives:
- S1 org scope of the Python team routes (`GET /entity/team/all_<otherOrg>` is a 404 only if the
  graph query filters on org): `backend/python/tests/integration/graph_db/test_team_org_scope_real_backends.py`.
  Here the fake plays that route; the test proves the Node half (a missing team is refused, nothing written).
- S2a the Python validate route (`/chat/attachments/validate`; the grant routes were removed in PH-07 PR-7.3):
  its ownership query and the service-token requirement are Python unit tests (`test_chatbot_attachment_permissions.py`).
  Here: Node asks the validate route with a `conversation:permissions` token and keeps only what it returns,
  and share/unshare/view make no record-permission call (PR-7.3).
- S3 the Python `team-ids` route itself (limit, org filter): `test_team_org_scope_real_backends.py`.
- S4c the Python half (`_append_task_markers`): `backend/python/tests/unit/utils/test_streaming.py`.
- S5 KB team role (graph providers): `tests/integration/graph_db/test_kb_team_role_real_backends.py`.
"""

from __future__ import annotations

import time

import jwt
import pytest

from helper.collab_stack import chats
from helper.collab_stack.chats import AGENT_KEY
from helper.collab_stack.fake_backend import FakeBackend, Reply, ai_answer, stream_answer
from helper.collab_stack.identity import CONVERSATION_PERMISSIONS_SCOPE, Actor, Directory
from helper.collab_stack.node_api import JWT_SECRET, SCOPED_JWT_SECRET
from helper.collab_stack.seeds import insert_project, insert_session, messages_of, session_doc, user_row
from helper.collab_stack.stack import CollabStack

AUTHZ_CHECK_SCOPE = "authz:check"

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_off")]

AWS_SIGNED = "https://bucket.s3.amazonaws.com/report.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=deadbeef0123"
AZURE_SIGNED = "https://acct.blob.core.windows.net/c/f.docx?sv=2023&sig=abc123%3D&se=2026-12-31"


# ---- S1: cross-org team (Node half) -----------------------------------------------------------


def test_j01_foreign_team_is_refused_and_no_member_is_written(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    """The team directory answers 404 for a team of another org; Node must refuse the project member and write nothing."""
    foreign = fake.add_team(stack.directory.org("globex"), "foreign")
    own = fake.add_team(roster.owner.org_id, "own", {roster.owner.user_id: "OWNER"})
    project = chats.create_project(api, roster.owner)

    denied = chats.add_project_member(api, roster.owner, project, foreign.team_id, principal_type="team")
    assert denied.status_code == 400, denied.text[:300]
    assert "Team not found" in denied.text
    members = stack.db["projects"].find_one({"_id": __import__("bson").ObjectId(project)})["members"]
    assert members == [], f"foreign team was written: {members}"

    accepted = chats.add_project_member(api, roster.owner, project, own.team_id, principal_type="team")
    assert accepted.status_code == 200, accepted.text[:300]
    asked = {r.match.get("team_id") for r in fake.requests_for("team_get")}
    assert {foreign.team_id, own.team_id} <= asked


# ---- S3: team ids resolved through the dedicated route ----------------------------------------


def test_j01_project_shared_with_team_number_101_is_visible(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    """A user in 101 teams still resolves team ids (no 422 on a page limit), so the 101st team's project lists."""
    member = roster.team_reader
    teams = [fake.add_team(member.org_id, f"t{i}", {member.user_id: "READER"}) for i in range(101)]
    insert_project(
        stack.db,
        "team-101",
        roster.owner,
        [{"principalType": "team", "teamId": teams[100].team_id, "role": "viewer", "addedBy": roster.owner.oid}],
    )

    listed = api.get("/api/v1/projects", member, params={"scope": "shared"})

    assert listed.status_code == 200, listed.text[:300]
    assert [p["name"] for p in listed.json()["projects"]] == ["team-101"]
    asked = fake.requests_for("team_ids")
    assert asked, "Node never asked the team-ids route"
    assert all(r.path == "/api/v1/entity/user/team-ids" for r in asked)
    assert not fake.requests_for("user_teams"), "Node used the paginated /user/teams route"
    assert asked[0].user_id == member.user_id


@pytest.mark.parametrize("failure", [{"status": 422}, {"status": 500}, {"status": 200, "body": {}}])
def test_j01_team_lookup_failure_fails_closed(stack: CollabStack, api, fake: FakeBackend, roster, failure) -> None:
    """A failed or malformed team lookup hides the team-only project; direct projects still list."""
    member = roster.team_reader
    team = fake.add_team(member.org_id, "t", {member.user_id: "READER"})
    insert_project(
        stack.db, "via-team", roster.owner, [{"principalType": "team", "teamId": team.team_id, "role": "viewer", "addedBy": roster.owner.oid}]
    )
    insert_project(stack.db, "direct", roster.owner, [{"principalType": "user", "principalId": member.oid, "role": "viewer", "addedBy": roster.owner.oid}])

    with fake.failing("team_ids", **failure):
        listed = api.get("/api/v1/projects", member, params={"scope": "shared"})

    assert listed.status_code == 200, listed.text[:300]
    assert [p["name"] for p in listed.json()["projects"]] == ["direct"]


# ---- S4a: share validator, per-entry matching, bare isShared ----------------------------------


def test_j01_share_validator_keeps_access_level(stack: CollabStack, api, roster) -> None:
    """`accessLevel` survives validation (it used to be stripped); with the flag off `write` is stored as `read` and says so."""
    chat = chats.create_chat(api, roster.owner)

    shared = chats.share(api, roster.owner, chat, roster.write_recipient, level="write")

    assert shared.status_code == 200, shared.text[:300]
    assert shared.json()["warnings"] == [{"code": "ACCESS_LEVEL_DOWNGRADED", "requested": "write", "applied": "read"}]
    stored = session_doc(stack.db, chat)["sharedWith"]
    assert [(str(r["userId"]), r["accessLevel"]) for r in stored] == [(roster.write_recipient.user_id, "read")]

    again = chats.share(api, roster.owner, chat, roster.write_recipient, level="write")
    assert again.status_code == 200
    assert [(str(r["userId"]), r["accessLevel"]) for r in session_doc(stack.db, chat)["sharedWith"]] == [(roster.write_recipient.user_id, "read")]


def test_j01_share_rejects_an_unknown_access_level_and_persists_nothing(stack: CollabStack, api, roster) -> None:
    chat = chats.create_chat(api, roster.owner)

    bad = chats.share(api, roster.owner, chat, roster.read_recipient, level="admin")

    assert bad.status_code == 400, bad.text[:300]
    doc = session_doc(stack.db, chat)
    assert doc["sharedWith"] == [] and doc["isShared"] is False


def test_j01_read_row_does_not_borrow_a_write_row_of_another_entry(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    """sharedWith = [U1 read, U2 write]: U1 matching 'userId=U1' and 'accessLevel=write' across entries must not pass a write gate."""
    u1, u2 = roster.read_recipient, roster.write_recipient
    rows = [user_row(u1, "read"), user_row(u2, "write")]
    agent_chat = insert_session(stack.db, "agent-mixed", roster.owner, kind="agent", shared_with=rows)
    chat = insert_session(stack.db, "chat-mixed", roster.owner, shared_with=rows)
    mark = fake.mark()

    deleted = api.delete(f"/api/v1/agents/{AGENT_KEY}/conversations/{agent_chat.sid}", u1)
    chat_deleted = api.delete(f"/api/v1/conversations/{chat.sid}", u1)
    archived = api.patch(f"/api/v1/conversations/{chat.sid}/archive", u1)
    regenerated = chats.regenerate(api, u1, chat.sid, chat.answer_id)

    for name, resp in {"agent delete": deleted, "chat delete": chat_deleted, "archive": archived, "regenerate": regenerated}.items():
        assert resp.status_code in (403, 404), f"{name}: {resp.status_code} {resp.text[:200]}"
    assert session_doc(stack.db, agent_chat.id)["isDeleted"] is False
    assert session_doc(stack.db, chat.id)["isDeleted"] is False
    assert session_doc(stack.db, chat.id)["isArchived"] is False
    assert not fake.since(mark, "chat", "agent_chat"), "a denied regenerate still reached the AI backend"


def test_j01_write_recipient_cannot_delete_or_archive(stack: CollabStack, api, roster) -> None:
    """The latent sibling of the same bug on the assistant routes (PH01-10): a `write` row is not ownership."""
    chat = insert_session(stack.db, "chat-write", roster.owner, shared_with=[user_row(roster.write_recipient, "write")])

    for resp in (
        api.delete(f"/api/v1/conversations/{chat.sid}", roster.write_recipient),
        api.patch(f"/api/v1/conversations/{chat.sid}/archive", roster.write_recipient),
        api.patch(f"/api/v1/conversations/{chat.sid}/unarchive", roster.write_recipient),
    ):
        assert resp.status_code == 404, resp.text[:200]
    doc = session_doc(stack.db, chat.id)
    assert doc["isDeleted"] is False and doc["isArchived"] is False


@pytest.mark.parametrize("kind", ["chat", "agent"])
def test_j01_bare_is_shared_flag_does_not_open_regenerate(stack: CollabStack, api, fake: FakeBackend, roster, kind: str) -> None:
    """`isShared: true` with no recipient list must not let a same-org stranger regenerate (and spend the owner's context)."""
    seeded = insert_session(stack.db, f"bare-{kind}", roster.owner, kind=kind, is_shared=True, shared_with=[])
    before = len(messages_of(stack.db, seeded.id))

    resp = chats.regenerate(api, roster.stranger, seeded.sid, seeded.answer_id, agent_key=AGENT_KEY if kind == "agent" else None)

    assert resp.status_code == 404, resp.text[:300]
    assert not fake.requests_for("chat", "agent_chat", "chat_stream", "agent_chat_stream")
    assert len(messages_of(stack.db, seeded.id)) == before


# ---- S2: attachment grants ---------------------------------------------------------------------


def test_j01_only_validated_attachments_are_stored_and_sent(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    """Node keeps the ids the AI backend confirms as the caller's chat attachments; a KB record id is dropped before it is stored."""
    fake.on("attachments_validate", Reply({"recordIds": ["upload-1"]}))
    attachments = [{"recordId": "upload-1", "recordName": "a.md"}, {"recordId": "kb-record-9", "recordName": "kb.md"}]

    chat = chats.create_chat(api, roster.owner, "summarize", attachments=attachments)

    question = next(m for m in messages_of(stack.db, chat) if m["messageType"] == "user_query")
    assert [a["recordId"] for a in question["attachments"]] == ["upload-1"]
    sent = fake.requests_for("chat")[0].body
    assert [a["recordId"] for a in sent["attachments"]] == ["upload-1"]
    validate = fake.requests_for("attachments_validate")[0]
    assert validate.body == {"recordIds": ["upload-1", "kb-record-9"]}
    claims = jwt.decode(validate.headers["authorization"][7:], SCOPED_JWT_SECRET, algorithms=["HS256"], options={"verify_exp": True})
    assert claims["scopes"] == [CONVERSATION_PERMISSIONS_SCOPE]
    assert (claims["userId"], claims["orgId"]) == (roster.owner.user_id, roster.owner.org_id)
    with pytest.raises(jwt.InvalidTokenError):
        jwt.decode(validate.headers["authorization"][7:], JWT_SECRET, algorithms=["HS256"])


@pytest.mark.parametrize(
    "reply",
    [Reply({"error": "down"}, status=503), Reply({"recordIds": "not-a-list"}), Reply({"recordIds": []}, delay=3.0)],
    ids=["5xx", "malformed", "timeout"],
)
def test_j01_attachment_validation_failure_drops_attachments_but_the_turn_succeeds(
    stack: CollabStack, api, fake: FakeBackend, roster, reply: Reply
) -> None:
    fake.on("attachments_validate", reply)

    chat = chats.create_chat(api, roster.owner, "summarize", attachments=[{"recordId": "r1", "recordName": "a.md"}])

    question = next(m for m in messages_of(stack.db, chat) if m["messageType"] == "user_query")
    assert question["attachments"] == []
    assert fake.requests_for("chat")[0].body["attachments"] == []


def test_j01_share_view_and_unshare_make_no_record_permission_call(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    """PR-7.3: READER grants are gone; the AI backend is never asked to grant or revoke chat attachments or artifacts."""
    fake.on("attachments_validate", Reply({"recordIds": ["upload-1"]}))
    mark = fake.mark()

    chat = chats.create_chat(api, roster.owner, "summarize", attachments=[{"recordId": "upload-1", "recordName": "a.md"}])
    assert chats.share(api, roster.owner, chat, roster.read_recipient).status_code == 200
    assert chats.get_chat(api, roster.read_recipient, chat).status_code == 200
    assert chats.get_chat(api, roster.read_recipient, chat, page=2).status_code == 200
    assert chats.unshare(api, roster.owner, chat, roster.read_recipient).status_code == 200

    sent = [r for r in fake.since(mark) if "/permissions" in r.path]
    assert sent == []


def test_j01_flag_off_recipient_reads_owner_attachment_through_the_pdp(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    """PR-7.3 flag-off parity: with no READER edge, Node's internal check lets a recipient read the owner's attachment and nobody else."""
    fake.on("attachments_validate", Reply({"recordIds": ["upload-1"]}))
    chat = chats.create_chat(api, roster.owner, "summarize", attachments=[{"recordId": "upload-1", "recordName": "a.md"}])
    assert chats.share(api, roster.owner, chat, roster.read_recipient).status_code == 200

    def check(actor: Actor) -> dict:
        now = int(time.time())
        token = jwt.encode(
            {"userId": "svc", "orgId": roster.owner.org_id, "scopes": [AUTHZ_CHECK_SCOPE], "iat": now, "exp": now + 60},
            SCOPED_JWT_SECRET,
            algorithm="HS256",
        )
        body = {
            "userId": actor.user_id,
            "orgId": actor.org_id,
            "action": "read",
            "resource": {"type": "chatAttachment", "recordId": "upload-1", "ownerUserId": roster.owner.user_id, "conversationId": chat},
        }
        resp = api.post("/api/v1/authz/internal/check", token=token, json_body=body)
        assert resp.status_code == 200, resp.text[:300]
        return resp.json()

    assert check(roster.read_recipient)["allow"] is True
    assert check(roster.stranger)["allow"] is False
    chats.unshare(api, roster.owner, chat, roster.read_recipient)
    assert check(roster.read_recipient)["allow"] is False


# ---- S4c: signed URLs never persisted ----------------------------------------------------------


ANSWER_WITH_SIGNED_URLS = (
    f"Report: [report.pdf]({AWS_SIGNED}) and [deck]({AZURE_SIGNED}).\n"
    f"::download_conversation_task[export.csv]({AWS_SIGNED})\n"
    f"Bare: {AWS_SIGNED}\n"
    "Docs: [guide](https://docs.example.com/guide?page=2)"
)


def assert_no_signed_url(text: str) -> None:
    for needle in ("X-Amz-Signature", "sig=abc123", "deadbeef0123"):
        assert needle not in text, f"signed URL fragment {needle!r} persisted in: {text[:300]}"
    assert "[guide](https://docs.example.com/guide?page=2)" in text, "an ordinary link was stripped"


def test_j01_signed_urls_are_not_persisted_non_streaming(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    fake.on("chat", Reply(ai_answer(ANSWER_WITH_SIGNED_URLS)))

    chat = chats.create_chat(api, roster.owner, "export it")

    stored = [m for m in messages_of(stack.db, chat) if m["messageType"] == "bot_response"][0]["content"]
    assert_no_signed_url(stored)
    shown = chats.get_chat(api, roster.owner, chat).json()["conversation"]["messages"][-1]["content"]
    assert_no_signed_url(shown)


def test_j01_signed_urls_are_not_persisted_streaming(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    fake.on("chat_stream", stream_answer(ANSWER_WITH_SIGNED_URLS))

    call = api.stream("/api/v1/conversations/stream", roster.owner, json_body={"query": "export it", "chatMode": "internal_search"}).finish()

    assert call.status == 200 and call.result, call.text[:500]
    stored = [m for m in messages_of(stack.db, call.conversation_id) if m["messageType"] == "bot_response"][0]["content"]
    assert_no_signed_url(stored)
    assert "X-Amz-Signature" in call.text, "the live stream is expected to carry the link unchanged"


# ---- S4b: disabled and foreign internal callers ------------------------------------------------


def test_j01_internal_route_rejects_a_disabled_user_and_creates_nothing(stack: CollabStack, api, fake: FakeBackend, roster) -> None:
    token = Directory.scoped_token(roster.disabled)

    resp = api.post("/api/v1/conversations/internal/create", token=token, json_body={"query": "hi"})

    assert resp.status_code == 401, resp.text[:300]
    assert stack.db["chatSessions"].count_documents({"userId": roster.disabled.oid}) == 0
    assert not fake.requests_for("chat")


def test_j01_internal_route_rejects_a_token_for_another_orgs_user(stack: CollabStack, api, roster) -> None:
    token = Directory.scoped_token(roster.other_org, org_id=roster.owner.org_id)

    resp = api.post("/api/v1/conversations/internal/create", token=token, json_body={"query": "hi"})

    assert resp.status_code == 401, resp.text[:300]
    assert stack.db["chatSessions"].count_documents({"userId": roster.other_org.oid}) == 0


def test_j01_internal_route_rejects_a_service_account(stack: CollabStack, api, roster) -> None:
    bot = stack.directory.user("Bot", kind="service")
    resp = api.post("/api/v1/conversations/internal/create", token=Directory.scoped_token(bot), json_body={"query": "hi"})
    assert resp.status_code == 401, resp.text[:300]


def test_j01_internal_route_still_serves_an_active_user(stack: CollabStack, api, roster) -> None:
    resp = api.post("/api/v1/conversations/internal/create", token=Directory.scoped_token(roster.owner), json_body={"query": "hi"})
    assert resp.status_code == 201, resp.text[:300]
    assert str(stack.db["chatSessions"].find_one({"title": "hi"})["userId"]) == roster.owner.user_id


def test_j01_disabled_user_session_token_is_refused_too(stack: CollabStack, api, roster) -> None:
    assert api.get("/api/v1/conversations", roster.disabled).status_code == 401
