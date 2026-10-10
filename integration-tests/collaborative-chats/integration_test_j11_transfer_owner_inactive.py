"""Journey J-11: ownership transfer and owner deactivation.

Given a shared chat whose owner is deactivated
When a collaborator opens it, then ownership is transferred
Then the chat is read-only with OWNER_INACTIVE until the transfer, and writable after

Owning phase: PH-06 (80-implementation-plan section 5); TR-01..04, LC-01, LC-02, LC-04, LC-10, LC-11, D11.
Every test uses its own owner: the API remembers an owner's active/inactive status for 60 s and the mutation
rate limit is per user.
"""

from __future__ import annotations

import time

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_backend import held_stream
from helper.collab_stack.node_api import DB_NAME
from helper.collab_stack.seeds import insert_project, messages_of, project_member, session_doc, user_row

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

NOT_FOUND = (404, "CONVERSATION_NOT_FOUND")
OWNER_ONLY = (403, "CONVERSATION_OWNER_ONLY")
INVALID = (400, "INVALID_PRINCIPAL")


def shared_chat(stack, api, label: str, *collaborators):  # noqa: ANN001, ANN201
    """A fresh owner's chat shared with the given (actor, level) pairs through the API."""
    owner = collab.fresh_actor(stack, f"J11{label}")
    chat = chats.create_chat(api, owner, "A question")
    if collaborators:
        collab.ok(collab.put(api, owner, chat, *(collab.user(a, level) for a, level in collaborators)), "share")
    return owner, chat


def audit_rows(stack, chat: str, action: str) -> list[dict]:  # noqa: ANN001
    return list(stack.db["auditEvents"].find({"targetId": chat, "action": action}))


# ---- (a) transfer -----------------------------------------------------------------------------------------


def test_j11_transfer_to_a_write_collaborator(stack, api, roster) -> None:  # noqa: ANN001
    writer, reader = roster.write_recipient, roster.read_recipient
    owner, chat = shared_chat(stack, api, "tr", (writer, "write"), (reader, "read"))

    # TR-02: the owner cannot leave before transferring.
    assert chats.error_of(collab.leave(api, owner, chat)) == OWNER_ONLY
    # TR-03: an editor cannot transfer.
    assert chats.error_of(collab.transfer(api, writer, chat, writer)) == OWNER_ONLY
    # LC-10: only a direct write collaborator can receive it; nothing changes on refusal.
    assert chats.error_of(collab.transfer(api, owner, chat, reader)) == INVALID
    assert chats.error_of(collab.transfer(api, owner, chat, roster.stranger)) == INVALID
    assert chats.error_of(collab.transfer(api, owner, chat, roster.other_org)) == INVALID
    unchanged = session_doc(stack.db, chat)
    assert str(unchanged["userId"]) == owner.user_id and not unchanged.get("ownershipHistory")
    before_acl = unchanged["aclVersion"]

    summary = collab.ok(collab.transfer(api, owner, chat, writer), "transfer")

    assert summary["owner"]["userId"] == writer.user_id and summary["myAccess"] == "write", "the caller is now a write collaborator"
    doc = session_doc(stack.db, chat)
    assert str(doc["userId"]) == str(doc["initiator"]) == writer.user_id
    rows = {str(r["userId"]): r for r in doc["sharedWith"]}
    assert set(rows) == {owner.user_id, reader.user_id}, "B's row is gone, A's was added, the reader's grant is untouched"
    assert rows[owner.user_id]["accessLevel"] == "write" and rows[reader.user_id]["accessLevel"] == "read"
    history = doc["ownershipHistory"]
    assert len(history) == 1 and str(history[0]["fromUserId"]) == owner.user_id and str(history[0]["toUserId"]) == writer.user_id
    assert doc["aclVersion"] > before_acl and doc["isShared"] is True
    (audit,) = audit_rows(stack, chat, "chat.ownershipTransfer")
    assert str(audit["actorUserId"]) == owner.user_id and audit["principal"]["principalId"] == writer.user_id
    assert audit["before"]["ownerId"] == owner.user_id and audit["after"]["ownerId"] == writer.user_id

    # B manages the chat now; A no longer can.
    view = collab.ok(collab.list_(api, writer, chat), "B lists")
    assert view["owner"]["userId"] == writer.user_id and view["collaboratorCount"] == 2
    assert chats.error_of(collab.put(api, owner, chat, collab.user(roster.stranger, "read"))) == OWNER_ONLY
    collab.ok(collab.put(api, writer, chat, collab.user(roster.stranger, "read")), "B shares")
    assert chats.send_message(api, owner, chat, "old owner, new editor").status_code == 200

    # TR-02: now A may leave; the chat is gone for A, B keeps it.
    assert collab.leave(api, owner, chat).status_code == 200
    assert chats.error_of(chats.get_chat(api, owner, chat)) == NOT_FOUND
    assert chats.get_chat(api, writer, chat).status_code == 200


def test_j11_transfer_to_a_disabled_user_is_refused(stack, api, roster) -> None:  # noqa: ANN001
    owner, chat = shared_chat(stack, api, "dis")
    stack.db["chatSessions"].update_one({"_id": ObjectId(chat)}, {"$set": {"sharedWith": [user_row(roster.disabled, "write", principal_type=True)], "isShared": True}})
    assert chats.error_of(collab.transfer(api, owner, chat, roster.disabled)) == INVALID
    assert str(session_doc(stack.db, chat)["userId"]) == owner.user_id


def test_j11_transfer_on_a_project_chat_needs_the_target_in_the_project(stack, api, roster) -> None:  # noqa: ANN001
    """LC-11: PROJECT_ACCESS_REQUIRED (403, as implemented; the scenario table says 409)."""
    writer = roster.write_recipient
    owner, chat = shared_chat(stack, api, "prj", (writer, "write"))
    pid = insert_project(stack.db, "j11-prj", owner, [], projectChatAccess="editor")
    assert api.put(f"{chats.CONVERSATIONS}/{chat}/project", owner, json_body={"projectId": str(pid)}).status_code == 200
    refused = collab.transfer(api, owner, chat, writer)
    assert chats.error_of(refused) == (403, "PROJECT_ACCESS_REQUIRED"), refused.text[:300]
    assert str(session_doc(stack.db, chat)["userId"]) == owner.user_id
    stack.db["projects"].update_one({"_id": pid}, {"$push": {"members": project_member(writer, "editor", owner)}, "$inc": {"aclVersion": 1}})
    collab.ok(collab.transfer(api, owner, chat, writer), "transfer once B is a project member")
    assert str(session_doc(stack.db, chat)["userId"]) == writer.user_id


def test_j11_a_run_in_flight_during_the_transfer_completes(stack, api, fake, roster) -> None:  # noqa: ANN001
    """TR-04: the old owner's held stream finishes after the transfer; no lease corruption."""
    writer = roster.write_recipient
    owner, chat = shared_chat(stack, api, "fly", (writer, "write"))
    gate = fake.gate("j11")
    fake.on("chat_stream", held_stream(gate, "A answer"))
    call = chats.stream_message(api, owner, chat, "A question 2")
    try:
        gate.wait_reached()
        collab.ok(collab.transfer(api, owner, chat, writer), "transfer mid-run")
    finally:
        gate.open()
    call.finish()
    assert call.status == 200 and call.result is not None, call.text[:400]
    doc = session_doc(stack.db, chat)
    assert doc["activeRun"] is None and doc["status"] == "Complete" and str(doc["userId"]) == writer.user_id
    rows = messages_of(stack.db, chat)
    assert [r["messageType"] for r in rows[-2:]] == ["user_query", "bot_response"] and str(rows[-2]["authorUserId"]) == owner.user_id
    assert chats.send_message(api, writer, chat, "new owner continues").status_code == 200


# ---- (b) an inactive owner --------------------------------------------------------------------------------


def test_j11_inactive_owner_is_read_only_until_the_chat_is_transferred(stack, api, roster) -> None:  # noqa: ANN001
    writer, other = roster.write_recipient, roster.read_recipient
    owner, chat = shared_chat(stack, api, "own", (writer, "write"), (other, "write"))

    # Fail closed (D11): the owner's status cannot be read -> 503, nothing stored, reads unaffected.
    rows_before = len(messages_of(stack.db, chat))
    with stack.infra.failing_finds(DB_NAME, "users", skip=1):
        unavailable = chats.send_message(api, writer, chat, "directory down")
    assert chats.error_of(unavailable) == (503, "OWNER_STATUS_UNAVAILABLE"), unavailable.text[:300]
    assert len(messages_of(stack.db, chat)) == rows_before
    assert chats.get_chat(api, writer, chat).status_code == 200

    # Disabled owner: non-owner sends are refused, reads work, readiness says why.
    stack.directory.set_disabled(owner, True)
    blocked = chats.send_message(api, writer, chat, "owner is gone")
    assert chats.error_of(blocked) == (403, "OWNER_INACTIVE"), blocked.text[:300]
    streamed = chats.stream_message(api, writer, chat, "owner is gone").finish()
    assert streamed.status == 403 and "OWNER_INACTIVE" in streamed.text
    assert chats.error_of(chats.regenerate(api, writer, chat, str(messages_of(stack.db, chat)[-1]["_id"])))[0] == 403
    assert chats.get_chat(api, writer, chat).status_code == 200
    assert collab.feed(api, writer, chat).status_code == 200
    ready = collab.ok(collab.readiness(api, writer, chat), "readiness")
    assert ready["canSend"] is False and "OWNER_INACTIVE" in ready["reasons"]
    assert len(messages_of(stack.db, chat)) == rows_before

    # The disabled owner cannot act (401), so the transfer needs the owner back; an admin cannot reassign in v1.
    assert collab.transfer(api, owner, chat, writer).status_code == 401
    stack.directory.set_disabled(owner, False)
    collab.ok(collab.transfer(api, owner, chat, writer), "transfer to an active user")
    stack.directory.set_disabled(owner, True)

    # The new owner is active: every collaborator, including the other editor, can send again.
    assert chats.send_message(api, writer, chat, "I own this now").status_code == 200
    assert chats.send_message(api, other, chat, "and I can write").status_code == 200
    ready_after = collab.ok(collab.readiness(api, other, chat), "readiness after")
    assert ready_after["canSend"] is True and ready_after["reasons"] == []
    # The disabled former owner still cannot get in.
    assert chats.get_chat(api, owner, chat).status_code == 401


# ---- offboarding (PR-6.5) ---------------------------------------------------------------------------------


def test_j11_deleting_a_collaborator_removes_them_from_every_share(stack, api, fake, roster) -> None:  # noqa: ANN001
    leaver = collab.fresh_actor(stack, "J11gone")
    keeper = roster.read_recipient
    owner = collab.fresh_actor(stack, "J11off")
    solo = chats.create_chat(api, owner, "solo")
    both = chats.create_chat(api, owner, "both")
    collab.ok(collab.put(api, owner, solo, collab.user(leaver, "write")), "share solo")
    collab.ok(collab.put(api, owner, both, collab.user(leaver, "write"), collab.user(keeper, "read")), "share both")
    assert chats.send_message(api, leaver, both, "my contribution").status_code == 200
    assert collab.feed(api, leaver, both).status_code == 200  # creates a read state
    assert stack.db["chatSessionReadStates"].count_documents({"userId": leaver.oid}) >= 1
    held = stack.db["chatSessions"].update_one({"_id": ObjectId(solo)}, {"$set": {"activeRun": {"runId": "stuck", "userId": leaver.oid, "startedAt": datetime.now(timezone.utc)}}})
    assert held.modified_count == 1

    gone = api.delete(f"/api/v1/users/{leaver.user_id}", roster.admin)
    assert gone.status_code == 200, gone.text[:400]
    assert gone.json()["ownedSharedChats"] == 0

    solo_doc, both_doc = session_doc(stack.db, solo), session_doc(stack.db, both)
    assert solo_doc["sharedWith"] == [] and solo_doc["isShared"] is False and solo_doc["activeRun"] is None
    assert [str(r["userId"]) for r in both_doc["sharedWith"]] == [keeper.user_id] and both_doc["isShared"] is True
    assert stack.db["chatSessionReadStates"].count_documents({"userId": leaver.oid}) == 0
    assert any(str(m.get("authorUserId")) == leaver.user_id for m in messages_of(stack.db, both)), "their messages stay"
    (offboard,) = list(stack.db["auditEvents"].find({"action": "chat.offboard", "targetId": leaver.user_id}))
    assert offboard["after"]["removedFrom"] == 2
    view = collab.ok(collab.list_(api, owner, both), "list")
    assert view["collaboratorCount"] == 1

    # LC-04: restoring the user does not revive the old shares.
    stack.db["users"].update_one({"_id": leaver.oid}, {"$set": {"isDeleted": False}})
    # Main ends every session issued up to the deletion's second (#3686); sign in again after it.
    time.sleep(1.1)
    assert chats.error_of(chats.get_chat(api, leaver, both)) == NOT_FOUND


def test_j11_deleting_an_owner_reports_the_shared_chats_and_leaves_them_read_only(stack, api, roster) -> None:  # noqa: ANN001
    writer, reader = roster.write_recipient, roster.read_recipient
    owner, first = shared_chat(stack, api, "del", (writer, "write"), (reader, "read"))
    second = chats.create_chat(api, owner, "second")
    collab.ok(collab.put(api, owner, second, collab.user(writer, "write")), "share second")
    chats.create_chat(api, owner, "private")

    gone = api.delete(f"/api/v1/users/{owner.user_id}", roster.admin)
    assert gone.status_code == 200, gone.text[:400]
    assert gone.json()["ownedSharedChats"] == 2

    for chat in (first, second):
        doc = session_doc(stack.db, chat)
        assert doc["isShared"] is True and str(doc["userId"]) == owner.user_id, "the chats stay with their collaborators"
        assert chats.get_chat(api, writer, chat).status_code == 200
        blocked = chats.send_message(api, writer, chat, "the owner was deleted")
        assert chats.error_of(blocked) == (403, "OWNER_INACTIVE"), blocked.text[:300]
