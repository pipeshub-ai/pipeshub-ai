"""PH-06 collaboration API on the real stack: the routes, their views and rules, the feed, readiness, list fields.

Given chats shared through PUT collaborators (chat and agent kinds), the flag on
When owners, editors and recipients call the collaboration routes
Then views reveal only what the role allows, rules and caps hold, leave and archive are per user, the feed is
cheap and paged, readiness says why a send would fail, and with the flag off every new route is 404

Owning phase: PH-06 (PH-06.md section 4); SEC-14, PH06-01, PH06-04..06, PH06-11, PH06-12/13, LC-07..09, LC-12, LC-15,
LC-21..23, CL-03, CL-08, SEC-17, PERF-05. Notifications and email are PR-6.3b's and are not covered here.
Every test creates its own owner: rate-limit budgets and the 60 s owner-status and agent-readiness caches are per user.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from bson import ObjectId

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_backend import Reply, held_stream
from helper.collab_stack.seeds import AGENT_KEY, insert_project, insert_session, messages_of, project_member, session_doc

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

OWNER_ONLY = (403, "CONVERSATION_OWNER_ONLY")
NOT_FOUND = (404, "CONVERSATION_NOT_FOUND")


def new_chat(stack, api, label: str):  # noqa: ANN001, ANN201
    owner = collab.fresh_actor(stack, f"P6{label}")
    return owner, chats.create_chat(api, owner, "A question")


def new_agent_chat(stack, api, label: str):  # noqa: ANN001, ANN201
    owner = collab.fresh_actor(stack, f"P6{label}")
    resp = api.post(f"/api/v1/agents/{AGENT_KEY}/conversations", owner, json_body={"query": "A question", "chatMode": "quick"})
    assert resp.status_code == 201, resp.text[:300]
    return owner, resp.json()["conversation"]["_id"]


def listed(api, who, **params) -> dict[str, dict]:  # noqa: ANN001, ANN003
    resp = api.get(chats.CONVERSATIONS, who, params=params or None)
    assert resp.status_code == 200, resp.text[:300]
    return {c["_id"]: c for c in resp.json()["conversations"]}


def archived_ids(api, who) -> set[str]:  # noqa: ANN001
    resp = api.get(f"{chats.CONVERSATIONS}/show/archives", who)
    assert resp.status_code == 200, resp.text[:300]
    body = resp.json()
    rows = body.get("conversations") or body.get("archives") or []
    return {c["_id"] for c in rows}


# ---- collaborators list: owner vs everyone else ----------------------------------------------------------------


def test_ph06_collaborators_view_owner_sees_everyone_others_see_a_summary(stack, api, fake, roster) -> None:  # noqa: ANN001
    """SEC-14, PH06-01, LC-12: names and ids for the owner (and an inviting editor); a count for the rest."""
    owner, chat = new_chat(stack, api, "view")
    writer, reader, team_reader = roster.write_recipient, roster.read_recipient, roster.team_reader
    group = fake.add_team(owner.org_id, "readers", {owner.user_id: "OWNER", team_reader.user_id: "READER"})
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write"), collab.user(reader, "read"), collab.team(group.team_id, "read")), "share")

    full = collab.ok(collab.list_(api, owner, chat), "owner list")
    assert full["owner"] == {"userId": owner.user_id, "displayName": f"User {owner.name}"}
    by_id = {c["principalId"]: c for c in full["collaborators"]}
    assert set(by_id) == {writer.user_id, reader.user_id, group.team_id} and full["collaboratorCount"] == 3
    assert (by_id[writer.user_id]["accessLevel"], by_id[writer.user_id]["displayName"], by_id[writer.user_id]["state"]) == ("write", "User Writer", "active")
    assert (by_id[group.team_id]["principalType"], by_id[group.team_id]["displayName"]) == ("team", "readers")
    assert full["settings"] == {"editorsCanInvite": False, "ownerContentShared": False}
    for leaked in ("hiddenFor", "archivedFor", "activeRun", "runId"):
        assert leaked not in str(full)
    audit = stack.db["auditEvents"].find_one({"targetId": chat, "action": "chat.share"})
    assert audit is not None and str(audit["actorUserId"]) == owner.user_id

    # LC-12: a rename shows on the next read; the row stores no name.
    fake.teams[group.team_id].name = "readers renamed"
    assert {c["displayName"] for c in collab.ok(collab.list_(api, owner, chat), "renamed")["collaborators"] if c["principalType"] == "team"} == {"readers renamed"}
    assert "name" not in str(session_doc(stack.db, chat)["sharedWith"]).replace("displayName", "")

    # SEC-14: reader and team member get the summary and nothing about other recipients.
    for who, access in ((reader, "read"), (team_reader, "read"), (writer, "write")):
        summary = collab.ok(collab.list_(api, who, chat), who.name)
        assert set(summary) == {"owner", "collaboratorCount", "myAccess"}, summary
        assert summary["myAccess"] == access and summary["collaboratorCount"] == 3
        body = str(summary)
        assert not any(other.user_id in body for other in (reader, writer, team_reader) if other is not who)
        assert group.team_id not in body
    assert chats.error_of(collab.list_(api, roster.stranger, chat)) == NOT_FOUND

    # PH06-01: an inviting editor sees the list, but a team they are not in is "A team".
    collab.ok(collab.settings(api, owner, chat, editorsCanInvite=True), "allow invites")
    editor_view = collab.ok(collab.list_(api, writer, chat), "editor list")
    editor_rows = {c["principalId"]: c for c in editor_view["collaborators"]}
    assert editor_rows[group.team_id]["displayName"] == "A team"
    assert editor_rows[reader.user_id]["displayName"] == "User Reader" and editor_view["settings"]["editorsCanInvite"] is True


# ---- editor invite rules ----------------------------------------------------------------------------------------


def test_ph06_editor_invite_rules(stack, api, roster) -> None:  # noqa: ANN001
    """LC-07, LC-08, LC-09: with the toggle an editor only adds people at write or below; nothing else changes."""
    owner, chat = new_chat(stack, api, "inv")
    editor, existing, newcomer, third = roster.write_recipient, roster.read_recipient, roster.stranger, roster.team_writer
    collab.ok(collab.put(api, owner, chat, collab.user(editor, "write"), collab.user(existing, "read")), "share")

    assert chats.error_of(collab.put(api, editor, chat, collab.user(newcomer, "read"))) == OWNER_ONLY, "LC-08: toggle off"
    assert chats.error_of(collab.put(api, existing, chat, collab.user(newcomer, "read")))[0] == 403, "a reader never invites"

    collab.ok(collab.settings(api, owner, chat, editorsCanInvite=True), "toggle on")
    assert chats.error_of(collab.settings(api, editor, chat, editorsCanInvite=False)) == OWNER_ONLY, "settings are the owner's"

    added = collab.ok(collab.put(api, editor, chat, collab.user(newcomer, "write")), "LC-07")
    assert added["collaboratorCount"] == 3
    row = next(r for r in session_doc(stack.db, chat)["sharedWith"] if str(r["userId"]) == newcomer.user_id)
    assert row["accessLevel"] == "write" and str(row["addedBy"]) == editor.user_id
    assert chats.get_chat(api, newcomer, chat).status_code == 200

    # LC-09: no change to an existing principal, no removal, no transfer, no demotion of the owner's grants.
    assert chats.error_of(collab.put(api, editor, chat, collab.user(existing, "write"))) == OWNER_ONLY
    assert chats.error_of(collab.remove(api, editor, chat, existing.user_id)) == OWNER_ONLY
    assert chats.error_of(collab.remove(api, editor, chat, newcomer.user_id)) == OWNER_ONLY, "not even a row the editor added"
    assert chats.error_of(collab.transfer(api, editor, chat, newcomer)) == OWNER_ONLY
    rows = {str(r["userId"]): r["accessLevel"] for r in session_doc(stack.db, chat)["sharedWith"]}
    assert rows == {editor.user_id: "write", existing.user_id: "read", newcomer.user_id: "write"}
    mixed = collab.put(api, editor, chat, collab.user(third, "write"), collab.user(existing, "read"))
    assert mixed.status_code == 200, "re-stating an existing grant unchanged is not a change"
    assert chats.get_chat(api, third, chat).status_code == 200


# ---- org-wide share ---------------------------------------------------------------------------------------------


def test_ph06_org_wide_share_needs_confirmation_and_is_read_only(stack, api, fake, roster) -> None:  # noqa: ANN001
    """LC-15: `all_<org>` needs confirmOrgWide; no write by default; anyone in the org reads, other orgs do not.

    Reading through the org-wide row relies on the connectors' "All" team (id `all_<org>`) listing the user in
    `/user/team-ids`: Node has no special case on the access path, so the fake carries that team."""
    owner, chat = new_chat(stack, api, "org")
    fake.add_team(owner.org_id, "All", {roster.stranger.user_id: "READER"}, team_id=f"all_{owner.org_id}")
    everyone = collab.team(f"all_{owner.org_id}", "read")

    unconfirmed = collab.put(api, owner, chat, everyone)
    assert chats.error_of(unconfirmed) == (400, "ORG_WIDE_CONFIRMATION_REQUIRED"), unconfirmed.text[:300]
    assert session_doc(stack.db, chat)["sharedWith"] == []

    write = collab.put(api, owner, chat, collab.team(f"all_{owner.org_id}", "write"), confirm_org_wide=True)
    assert write.status_code == 400, f"org-wide write is off by default: {write.status_code} {write.text[:300]}"
    assert session_doc(stack.db, chat)["sharedWith"] == []

    view = collab.ok(collab.put(api, owner, chat, everyone, confirm_org_wide=True), "confirmed")
    assert view["collaborators"][0]["displayName"] == "Everyone in your organization"
    assert chats.get_chat(api, roster.stranger, chat).status_code == 200
    assert chats.error_of(chats.send_message(api, roster.stranger, chat))[1] == "CONVERSATION_READ_ONLY"
    assert chats.error_of(chats.get_chat(api, roster.other_org, chat)) == NOT_FOUND
    foreign = collab.put(api, owner, chat, collab.team(f"all_{roster.other_org.org_id}", "read"), confirm_org_wide=True)
    assert chats.error_of(foreign) == (400, "INVALID_PRINCIPAL"), "another org's all-team is not shareable"


# ---- the 200 cap ------------------------------------------------------------------------------------------------


def test_ph06_collaborator_cap_is_200_rows(stack, api, roster) -> None:  # noqa: ANN001
    """LC-23: 199 rows + 2 users -> 409 COLLABORATOR_LIMIT {max: 200} and nothing added; +1 -> 200; one more -> 409. 51 in a PUT -> 400."""
    owner, chat = new_chat(stack, api, "cap")
    now = datetime.now(timezone.utc)
    filler = [{"principalType": "user", "userId": ObjectId(), "accessLevel": "read", "addedAt": now} for _ in range(199)]
    stack.db["chatSessions"].update_one({"_id": ObjectId(chat)}, {"$set": {"sharedWith": filler, "isShared": True}})

    two = collab.put(api, owner, chat, collab.user(roster.write_recipient, "read"), collab.user(roster.read_recipient, "read"))
    assert chats.error_of(two) == (409, "COLLABORATOR_LIMIT") and chats.details_of(two) == {"max": 200}, two.text[:300]
    assert len(session_doc(stack.db, chat)["sharedWith"]) == 199, "all or nothing"

    collab.ok(collab.put(api, owner, chat, collab.user(roster.write_recipient, "read")), "the 200th")
    assert len(session_doc(stack.db, chat)["sharedWith"]) == 200
    over = collab.put(api, owner, chat, collab.user(roster.read_recipient, "read"))
    assert chats.error_of(over) == (409, "COLLABORATOR_LIMIT")
    again = collab.put(api, owner, chat, collab.user(roster.write_recipient, "write"))
    assert again.status_code == 200, "changing an existing row at the cap is not an addition"

    fifty_one = [("user", str(ObjectId()), "read") for _ in range(51)]
    assert collab.put(api, owner, chat, *fifty_one).status_code == 400


# ---- leave and per-user archive ---------------------------------------------------------------------------------


def test_ph06_leave_and_per_user_archive(stack, api, roster) -> None:  # noqa: ANN001
    """CL-03, CL-04, LC-21, LC-22, TR-02: archive keeps access and is per user; leave removes the row and hides the chat."""
    owner, chat = new_chat(stack, api, "arc")
    writer, reader = roster.write_recipient, roster.read_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write"), collab.user(reader, "read")), "share")
    assert chat in listed(api, writer, source="shared")

    assert api.patch(f"{chats.CONVERSATIONS}/{chat}/archive", writer).status_code == 200
    assert chat not in listed(api, writer, source="shared") and chat in archived_ids(api, writer)
    assert chat in listed(api, owner) and chat in listed(api, reader, source="shared"), "others are unaffected"
    assert chats.get_chat(api, writer, chat).status_code == 200, "archiving keeps access"
    assert api.patch(f"{chats.CONVERSATIONS}/{chat}/unarchive", writer).status_code == 200
    assert chat in listed(api, writer, source="shared")

    assert chats.error_of(collab.leave(api, owner, chat)) == OWNER_ONLY
    left = collab.leave(api, writer, chat)
    assert left.status_code == 200 and left.json()["status"] == "left"
    assert chat not in listed(api, writer, source="shared")
    assert chats.error_of(chats.get_chat(api, writer, chat)) == NOT_FOUND
    assert [str(r["userId"]) for r in session_doc(stack.db, chat)["sharedWith"]] == [reader.user_id]
    assert chat in listed(api, reader, source="shared")

    # LC-22: sharing again un-hides it.
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write")), "re-share")
    assert chat in listed(api, writer, source="shared") and chats.get_chat(api, writer, chat).status_code == 200


def test_ph06_leave_keeps_access_that_comes_from_a_team(stack, api, fake, roster) -> None:  # noqa: ANN001
    """LC-21: direct row + team row; Leave drops the direct row and hides the chat, opening it by id still works through the team."""
    owner, chat = new_chat(stack, api, "lv")
    member = roster.team_reader
    group = fake.add_team(owner.org_id, "readers", {owner.user_id: "OWNER", member.user_id: "READER"})
    collab.ok(collab.put(api, owner, chat, collab.user(member, "write"), collab.team(group.team_id, "read")), "share")
    assert collab.leave(api, member, chat).status_code == 200
    stored = session_doc(stack.db, chat)
    assert [r.get("teamId") for r in stored["sharedWith"]] == [group.team_id], "the direct row is gone, the team row stays"
    assert chats.get_chat(api, member, chat).status_code == 200
    assert chats.error_of(chats.send_message(api, member, chat))[1] == "CONVERSATION_READ_ONLY", "only the team's read remains"


def test_ph06_leave_hides_a_chat_that_is_still_shared_through_a_team(stack, api, fake, roster) -> None:  # noqa: ANN001
    """LC-21, LC-22: Leave hides the chat from every list though the team still shares it; the link still opens it
    read-only through the team; a new direct share by the owner brings it back, a team re-share does not."""
    owner, chat = new_chat(stack, api, "lvh")
    member = roster.team_reader
    group = fake.add_team(owner.org_id, "readers", {owner.user_id: "OWNER", member.user_id: "READER"})
    collab.ok(collab.put(api, owner, chat, collab.user(member, "write"), collab.team(group.team_id, "read")), "share")
    assert chat in listed(api, member, source="shared")
    assert collab.leave(api, member, chat).status_code == 200
    assert chat not in listed(api, member, source="shared") and chat not in archived_ids(api, member)
    assert chats.get_chat(api, member, chat).status_code == 200
    assert chats.error_of(chats.send_message(api, member, chat))[1] == "CONVERSATION_READ_ONLY"

    collab.ok(collab.put(api, owner, chat, collab.team(group.team_id, "write")), "team re-share")
    assert chat not in listed(api, member, source="shared"), "a team re-share does not undo Leave"
    collab.ok(collab.put(api, owner, chat, collab.user(member, "read")), "direct re-share")
    assert chat in listed(api, member, source="shared")


# ---- feed -------------------------------------------------------------------------------------------------------


def test_ph06_feed_answers_304_on_an_equal_rev_and_not_before(stack, api, fake, roster) -> None:  # noqa: ANN001
    """PERF-05: an unchanged rev is a 304 with no body; a new turn gives a new rev and the new rows."""
    owner, chat = new_chat(stack, api, "304")
    reader = roster.read_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(reader, "read")), "share")
    first = collab.ok(collab.feed(api, reader, chat), "first poll")
    assert [m["seq"] for m in first["messages"]] == [1, 2] and first["nextSeq"] == 2 and first["hasMore"] is False
    assert first["messages"][0]["author"]["userId"] == owner.user_id

    same = collab.feed(api, reader, chat, afterSeq=first["nextSeq"], rev=first["rev"])
    assert same.status_code == 304 and same.content == b""
    stale = collab.feed(api, reader, chat, afterSeq=first["nextSeq"], rev=first["rev"] - 1)
    assert stale.status_code == 200 and stale.json()["messages"] == []

    assert chats.send_message(api, owner, chat, "a new turn").status_code == 200
    changed = collab.feed(api, reader, chat, afterSeq=first["nextSeq"], rev=first["rev"])
    assert changed.status_code == 200
    body = changed.json()
    assert [m["seq"] for m in body["messages"]] == [3, 4] and body["rev"] > first["rev"]
    assert collab.feed(api, reader, chat, afterSeq=body["nextSeq"], rev=body["rev"]).status_code == 304

    state = stack.db["chatSessionReadStates"].find_one({"userId": reader.oid, "sessionId": ObjectId(chat)})
    assert state is not None and state["lastReadSeq"] >= 2


def test_ph06_feed_pages_by_100_and_hides_the_run_id_from_readers(stack, api, fake, roster) -> None:  # noqa: ANN001
    """PH06-04: afterSeq paging with hasMore; activeRun carries runId for writers only."""
    owner = collab.fresh_actor(stack, "P6page")
    writer, reader = roster.write_recipient, roster.read_recipient
    turns = [("user_query" if i % 2 == 0 else "bot_response", f"message {i + 1}") for i in range(130)]
    seeded = insert_session(
        stack.db, f"p6page-{owner.name}", owner, messages=turns,
        shared_with=[{"principalType": "user", "userId": reader.oid, "accessLevel": "read"}, {"principalType": "user", "userId": writer.oid, "accessLevel": "write"}],
    )  # fmt: skip
    first = collab.ok(collab.feed(api, reader, seeded.sid, afterSeq=0), "page 1")
    assert [m["seq"] for m in first["messages"]] == list(range(1, 101)) and first["hasMore"] is True and first["nextSeq"] == 100
    second = collab.ok(collab.feed(api, reader, seeded.sid, afterSeq=first["nextSeq"]), "page 2")
    assert [m["seq"] for m in second["messages"]] == list(range(101, 131)) and second["hasMore"] is False and second["nextSeq"] == 130
    tail = collab.ok(collab.feed(api, reader, seeded.sid, afterSeq=130), "past the end")
    assert tail["messages"] == [] and tail["nextSeq"] == 130

    gate = fake.gate("p6feed")
    fake.on("chat_stream", held_stream(gate, "late answer"))
    call = chats.stream_message(api, owner, seeded.sid, "running")
    try:
        gate.wait_reached()
        as_reader = collab.ok(collab.feed(api, reader, seeded.sid, afterSeq=130), "reader mid-run")["activeRun"]
        as_writer = collab.ok(collab.feed(api, writer, seeded.sid, afterSeq=130), "writer mid-run")["activeRun"]
        assert as_reader is not None and "runId" not in as_reader and as_reader["userId"] == owner.user_id
        assert as_writer is not None and as_writer["runId"]
    finally:
        gate.open()
    call.finish()
    assert collab.ok(collab.feed(api, reader, seeded.sid, afterSeq=130), "after")["activeRun"] is None


# ---- readiness --------------------------------------------------------------------------------------------------


def test_ph06_readiness_reasons(stack, api, fake, roster) -> None:  # noqa: ANN001
    """PH06-06 and CL-08: a viewer is read-only; an agent chat reports the caller's own missing tools, an unavailable agent, a missing project."""
    owner, chat = new_chat(stack, api, "rdy")
    reader = roster.read_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(reader, "read"), collab.user(roster.write_recipient, "write")), "share")
    assert collab.ok(collab.readiness(api, reader, chat), "viewer") == {"canSend": False, "reasons": ["CONVERSATION_READ_ONLY"]}
    assert collab.ok(collab.readiness(api, roster.write_recipient, chat), "editor") == {"canSend": True, "reasons": []}
    assert collab.ok(collab.readiness(api, owner, chat), "owner")["canSend"] is True
    assert chats.error_of(collab.readiness(api, roster.stranger, chat)) == NOT_FOUND

    agent_owner, agent_chat = new_agent_chat(stack, api, "rdya")
    blocked_user = collab.fresh_actor(stack, "P6blocked")
    ready_user = collab.fresh_actor(stack, "P6ready")
    collab.ok(collab.put(api, agent_owner, agent_chat, collab.user(blocked_user, "write"), collab.user(ready_user, "write"), agent_key=AGENT_KEY), "share agent chat")
    blocked = {"canSend": False, "missingToolsets": ["jira"], "unauthenticatedToolsets": ["slack"]}
    fake.default("agent_readiness", lambda rec: Reply(blocked if rec.user_id == blocked_user.user_id else {"canSend": True, "missingToolsets": [], "unauthenticatedToolsets": []}))
    mine = collab.ok(collab.readiness(api, blocked_user, agent_chat, AGENT_KEY), "blocked editor")
    assert mine == {"canSend": False, "reasons": ["CONNECTOR_SETUP_REQUIRED"], "missingToolsets": ["jira", "slack"]}
    theirs = collab.ok(collab.readiness(api, ready_user, agent_chat, AGENT_KEY), "ready editor")
    assert theirs == {"canSend": True, "reasons": []}, "another user's missing tools are never reported"

    gone_user = collab.fresh_actor(stack, "P6gone")
    collab.ok(collab.put(api, agent_owner, agent_chat, collab.user(gone_user, "write"), agent_key=AGENT_KEY), "share again")
    fake.default("agent_readiness", Reply({"detail": "no such agent"}, status=404))
    unavailable = collab.ok(collab.readiness(api, gone_user, agent_chat, AGENT_KEY), "agent gone")
    assert unavailable == {"canSend": False, "reasons": ["AGENT_UNAVAILABLE"]}

    # D7: a writer who is not in the chat's project cannot send, and readiness says why.
    project_owner, project_chat_id = new_chat(stack, api, "rdyp")
    outsider = roster.write_recipient
    pid = insert_project(stack.db, "p6-ready", project_owner, [project_member(roster.project_viewer, "viewer", project_owner)], projectChatAccess="editor")
    assert api.put(f"{chats.CONVERSATIONS}/{project_chat_id}/project", project_owner, json_body={"projectId": str(pid)}).status_code == 200
    collab.ok(collab.put(api, project_owner, project_chat_id, collab.user(outsider, "write")), "share project chat")
    ready = collab.ok(collab.readiness(api, outsider, project_chat_id), "outside the project")
    assert ready["canSend"] is False and ready["reasons"] == ["PROJECT_ACCESS_REQUIRED"], ready


# ---- unread counts on lists --------------------------------------------------------------------------------------


def test_ph06_unread_count_is_the_number_of_rows_the_user_has_not_read(stack, api, fake, roster) -> None:  # noqa: ANN001
    """PH06-13 / PH-09 Q1. `nextSeq` is the highest seq allocated (allocateSeq returns the end of the block), so
    `nextSeq - lastReadSeq` IS the count of unread rows; the spec's `nextSeq-1-lastReadSeq` would under-count by one."""
    owner, chat = new_chat(stack, api, "unread")
    writer, other = roster.write_recipient, roster.read_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write"), collab.user(other, "read")), "share")
    assert len(messages_of(stack.db, chat)) == 2

    assert listed(api, writer, source="shared")[chat]["unreadCount"] == 2, "never read: both rows are unread"
    collab.ok(collab.feed(api, writer, chat), "B reads")
    assert listed(api, writer, source="shared")[chat]["unreadCount"] == 0

    assert chats.send_message(api, owner, chat, "a new turn").status_code == 200
    assert listed(api, writer, source="shared")[chat]["unreadCount"] == 2, "exactly the two rows added since B read"
    assert listed(api, other, source="shared")[chat]["unreadCount"] == 4, "C never read anything"
    assert "nextSeq" not in listed(api, writer, source="shared")[chat], "the internal counter is not exposed"
    assert listed(api, owner)[chat]["collaboratorCount"] == 2
    assert "collaboratorCount" not in listed(api, writer, source="shared")[chat]

    private = chats.create_chat(api, owner, "not shared")
    assert "unreadCount" not in listed(api, owner)[private], "only collaborative rows carry it"


# ---- agent kind -------------------------------------------------------------------------------------------------


def test_ph06_agent_kind_serves_the_same_routes_and_archives_per_user(stack, api, roster) -> None:  # noqa: ANN001
    """CL-03 (agent kind): the routes exist under /agents/:agentKey/conversations/:id, with the same guards."""
    owner, chat = new_agent_chat(stack, api, "agent")
    writer, reader = roster.write_recipient, roster.read_recipient
    key = AGENT_KEY
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write"), collab.user(reader, "read"), agent_key=key), "share")
    assert collab.ok(collab.list_(api, owner, chat, key), "owner")["collaboratorCount"] == 2
    assert set(collab.ok(collab.list_(api, reader, chat, key), "reader")) == {"owner", "collaboratorCount", "myAccess"}
    assert chats.error_of(collab.put(api, writer, chat, collab.user(roster.stranger, "read"), agent_key=key)) == OWNER_ONLY
    assert collab.feed(api, reader, chat, key).status_code == 200
    assert collab.readiness(api, reader, chat, key).json()["reasons"] == ["CONVERSATION_READ_ONLY"]
    # a chat id from the other kind is not found on this router
    plain_owner, plain = new_chat(stack, api, "plain")
    assert chats.error_of(collab.list_(api, plain_owner, plain, key))[0] == 404

    assert api.post(f"/api/v1/agents/{key}/conversations/{chat}/archive", owner).status_code == 200
    doc = session_doc(stack.db, chat)
    assert [str(u) for u in doc["archivedFor"]] == [owner.user_id], "per-user archive"
    assert api.get(f"/api/v1/agents/{key}/conversations/{chat}", writer).status_code == 200

    assert collab.leave(api, writer, chat, key).status_code == 200
    assert chats.error_of(api.get(f"/api/v1/agents/{key}/conversations/{chat}", writer)) == NOT_FOUND
    view = collab.ok(collab.remove(api, owner, chat, reader.user_id, agent_key=key), "remove")
    assert view["collaboratorCount"] == 0
    assert session_doc(stack.db, chat)["isShared"] is False


# ---- legacy share with the flag on ----------------------------------------------------------------------------------


def test_ph06_legacy_share_honours_the_level_and_unshare_matches_delete(stack, api, roster) -> None:  # noqa: ANN001
    """PH06-11: flag on, POST /share stores the requested level; /unshare is the same as DELETE collaborator."""
    owner, chat = new_chat(stack, api, "legacy")
    writer, reader = roster.write_recipient, roster.read_recipient
    shared = chats.share(api, owner, chat, writer, level="write")
    assert shared.status_code == 200 and "warnings" not in shared.json()
    assert chats.share(api, owner, chat, reader, level="read").status_code == 200
    assert {str(r["userId"]): r["accessLevel"] for r in session_doc(stack.db, chat)["sharedWith"]} == {writer.user_id: "write", reader.user_id: "read"}
    assert chats.unshare(api, owner, chat, writer).status_code == 200
    collab.ok(collab.remove(api, owner, chat, reader.user_id), "delete collaborator")
    doc = session_doc(stack.db, chat)
    assert doc["sharedWith"] == [] and doc["isShared"] is False
    assert stack.db["auditEvents"].count_documents({"targetId": chat, "action": "chat.unshare"}) == 2


# ---- rate limit -------------------------------------------------------------------------------------------------


def test_ph06_collaborator_mutations_are_rate_limited_per_user(stack, api, roster) -> None:  # noqa: ANN001
    """SEC-17: 20 mutations a minute; the 21st is 429 RATE_LIMITED with retryAfter; another user is not affected; reads are not counted."""
    owner, chat = new_chat(stack, api, "rate")
    other, other_chat = new_chat(stack, api, "rate2")
    reader = roster.read_recipient
    statuses = [collab.put(api, owner, chat, collab.user(reader, "read")).status_code for _ in range(20)]
    assert statuses == [200] * 20
    limited = collab.put(api, owner, chat, collab.user(reader, "read"))
    assert chats.error_of(limited) == (429, "RATE_LIMITED"), limited.text[:300]
    assert chats.details_of(limited)["retryAfter"] > 0
    assert collab.settings(api, owner, chat, editorsCanInvite=True).status_code == 429, "the budget is shared across mutation routes"
    assert collab.list_(api, owner, chat).status_code == 200
    assert collab.put(api, other, other_chat, collab.user(reader, "read")).status_code == 200


# ---- flag off ---------------------------------------------------------------------------------------------------


def test_ph06_flag_off_every_new_route_is_404_and_legacy_share_is_unchanged(stack, api, flags, roster) -> None:  # noqa: ANN001
    """PH06-12: with the flag off all eight routes, on both routers, answer 404 for everyone (owner, recipient, no token);
    the legacy share still stores `read` whatever level is asked, as before PH-06 (J-02 holds the full parity)."""
    owner, chat = new_chat(stack, api, "off")
    agent_owner, agent_chat = new_agent_chat(stack, api, "offa")
    reader = roster.read_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(reader, "read")), "share while on")
    with flags.value(False):
        for who, conversation, key in ((owner, chat, None), (reader, chat, None), (agent_owner, agent_chat, AGENT_KEY), (None, chat, None)):
            base = collab.base(conversation, key)
            calls = [
                api.get(f"{base}/collaborators", who),
                api.put(f"{base}/collaborators", who, json_body={"collaborators": [{"principalType": "user", "principalId": reader.user_id, "accessLevel": "read"}]}),
                api.delete(f"{base}/collaborators/{reader.user_id}", who, params={"principalType": "user"}),
                api.patch(f"{base}/collaboration-settings", who, json_body={"editorsCanInvite": True}),
                api.post(f"{base}/transfer-ownership", who, json_body={"newOwnerUserId": reader.user_id}),
                api.post(f"{base}/leave", who),
                api.get(f"{base}/feed", who),
                api.get(f"{base}/readiness", who),
            ]
            assert [c.status_code for c in calls] == [404] * 8, (who.name if who else "anonymous", [c.status_code for c in calls])
        shared = chats.share(api, owner, chat, roster.write_recipient, level="write")
        assert shared.status_code == 200, shared.text[:300]
        stored = {str(r["userId"]): r["accessLevel"] for r in session_doc(stack.db, chat)["sharedWith"]}
        assert stored[roster.write_recipient.user_id] == "read", "flag off: accessLevel is ignored"


# ---- notifications (PR-6.3b): outbox -> broker -> consumer -> bell ----------------------------------------------------

SECRET_TITLE = "Zebra quarterly budget"


def bell(api, who, chat: str, kind: str) -> list[dict]:  # noqa: ANN001
    """The caller's notifications of one type about one chat, through the public API."""
    resp = api.get("/api/v1/notifications", who, params={"limit": 50})
    assert resp.status_code == 200, resp.text[:300]
    return [n for n in resp.json()["notifications"] if n.get("type") == kind and chat in json.dumps(n)]


def wait_for_bell(api, who, chat: str, kind: str, count: int = 1, timeout: float = 30) -> list[dict]:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    found: list[dict] = []
    while time.monotonic() < deadline:
        found = bell(api, who, chat, kind)
        if len(found) >= count:
            return found
        time.sleep(0.5)
    raise AssertionError(f"no {count} {kind!r} notification for {who.name} within {timeout}s; got {found}")


def test_ph06_share_reaches_the_recipient_bell_without_the_chat_title(stack, api, roster) -> None:  # noqa: ANN001
    """NT-01/NT-14 on the live pipeline: the outbox row is dispatched, consumed, and shown; no title or content is stored.

    RR #22c: the list adds the title at read time (`context`), behind the same read check as the chat list; it is never stored."""
    owner = collab.fresh_actor(stack, "P6bell")
    chat = chats.create_chat(api, owner, SECRET_TITLE)
    writer = roster.write_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write"), note="please look at the numbers"), "share")

    (item,) = wait_for_bell(api, writer, chat, "chat.shared")
    stored = stack.db["notifications"].find_one({"_id": ObjectId(item["_id"])})
    assert stored is not None and "Zebra" not in json.dumps(stored, default=str), f"the chat title was stored: {stored}"
    text = json.dumps({k: v for k, v in item.items() if k != "context"})
    assert "Zebra" not in text, f"the chat title leaked into the notification text: {text}"
    assert item["context"]["chatTitle"] == SECRET_TITLE, "a reader sees the title resolved at read time"
    assert "numbers" not in item["title"] + item["message"], "the hand-over note is payload only, never the text"
    assert item["payload"]["note"] == "please look at the numbers", "the in-app item carries the note (email does not, NT-14)"
    assert item["status"] == "unread" and chat in item["redirectLink"]
    assert bell(api, owner, chat, "chat.shared") == [], "the actor is not notified of their own share"


def test_ph06_unshare_then_reshare_delivers_a_second_notification(stack, api, roster) -> None:  # noqa: ANN001
    """The first (unread) notification is archived by the unshare (NT-03); the re-share is a new aclVersion and so a new occurrence."""
    owner = collab.fresh_actor(stack, "P6reshare")
    chat = chats.create_chat(api, owner, "A question")
    writer = roster.write_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write")), "share")
    wait_for_bell(api, writer, chat, "chat.shared")

    collab.ok(collab.remove(api, owner, chat, writer.user_id), "unshare")
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write")), "re-share")
    deadline = time.monotonic() + 30
    docs: list[dict] = []
    while time.monotonic() < deadline:
        docs = list(stack.db["notifications"].find({"assignedTo": writer.oid, "type": "chat.shared", "redirectLink": {"$regex": chat}}))
        if len(docs) >= 2 and any(d["status"] == "unread" for d in docs):
            break
        time.sleep(0.5)
    assert len(docs) == 2, [(d["status"], d.get("dedupeKey")) for d in docs]
    assert len({d["dedupeKey"] for d in docs}) == 2
    assert sorted(d["status"] for d in docs) == ["archived", "unread"]
    assert len(bell(api, writer, chat, "chat.shared")) >= 1


def test_ph06_a_muted_session_gets_no_activity_notification(stack, api, roster) -> None:  # noqa: ANN001
    """NT-06/NT-11: B's finished turn notifies the owner unless the owner muted that chat."""
    owner = collab.fresh_actor(stack, "P6mute")
    writer = roster.write_recipient
    muted, loud = chats.create_chat(api, owner, "muted"), chats.create_chat(api, owner, "loud")
    collab.ok(collab.put(api, owner, muted, collab.user(writer, "write")), "share muted")
    collab.ok(collab.put(api, owner, loud, collab.user(writer, "write")), "share loud")
    mute = api.put(f"/api/v1/notifications/preferences/muted-sessions/{muted}", owner)
    assert mute.status_code == 200, mute.text[:300]

    assert chats.send_message(api, writer, muted, "turn in the muted chat").status_code == 200
    assert chats.send_message(api, writer, loud, "turn in the other chat").status_code == 200
    (activity,) = wait_for_bell(api, owner, loud, "chat.activity")
    assert chat_text_clean(activity)
    time.sleep(3)  # the muted chat's event, had there been one, was written first
    assert bell(api, owner, muted, "chat.activity") == []
    assert stack.db["notifications"].count_documents({"assignedTo": owner.oid, "type": "chat.activity", "redirectLink": {"$regex": muted}}) == 0


def chat_text_clean(item: dict) -> bool:
    return "turn in the" not in json.dumps(item)
