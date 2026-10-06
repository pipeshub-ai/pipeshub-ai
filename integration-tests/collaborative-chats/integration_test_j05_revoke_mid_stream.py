"""Journey J-05: revoke mid-stream.

Given user B is streaming in a chat owned by A and A revokes B's access
When the run finishes, then B sends again and reads the feed
Then the run completes, its rows are written (decided behaviour), the next send is 404, the feed is 404
and the collaborators list is 404

Owning phase: PH-06 (80-implementation-plan section 5); CL-01 family, LC-05.
Revoked three ways: DELETE collaborators, a project-visibility flip, and removing B from the project.
Real HTTP into the Node API; the AI backend is the lane's fake with a held stream.
"""

from __future__ import annotations

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_backend import held_stream
from helper.collab_stack.seeds import insert_project, messages_of, project_member, session_doc

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

NOT_FOUND = (404, "CONVERSATION_NOT_FOUND")


def assert_access_gone(api, who, chat) -> None:  # noqa: ANN001
    assert chats.error_of(chats.send_message(api, who, chat, "after revoke")) == NOT_FOUND
    assert chats.error_of(collab.feed(api, who, chat)) == NOT_FOUND
    assert chats.error_of(collab.list_(api, who, chat)) == NOT_FOUND
    assert chats.error_of(chats.get_chat(api, who, chat)) == NOT_FOUND


def run_revoked_mid_stream(stack, api, fake, chat, writer, owner_prior_rows: int, revoke) -> None:  # noqa: ANN001
    """B streams a held turn; ``revoke()`` runs while it is open; the run then completes and its rows are stored."""
    gate = fake.gate("j05")
    fake.on("chat_stream", held_stream(gate, "B answer"))
    call = chats.stream_message(api, writer, chat, "B question", clientMessageId="b-msg-1")
    try:
        gate.wait_reached()
        assert session_doc(stack.db, chat)["activeRun"] is not None, "B holds the lease while the stream is held"
        revoke()
    finally:
        gate.open()
    call.finish()

    assert call.status == 200 and call.result is not None, call.text[:500]
    rows = messages_of(stack.db, chat)
    assert len(rows) == owner_prior_rows + 2, "the run's rows are written even though B was revoked meanwhile"
    asked, answered = rows[-2], rows[-1]
    assert (asked["messageType"], asked["content"], str(asked["authorUserId"])) == ("user_query", "B question", writer.user_id)
    assert (answered["messageType"], answered["content"]) == ("bot_response", "B answer")
    assert str(answered["requestedBy"]) == writer.user_id
    after = session_doc(stack.db, chat)
    assert after["activeRun"] is None and after["status"] == "Complete", "the lease is released and the chat idle"
    assert_access_gone(api, writer, chat)


def owner_chat(stack, api, fake, label: str):  # noqa: ANN001, ANN201
    owner = collab.fresh_actor(stack, f"J5{label}")
    chat = chats.create_chat(api, owner, "A question")
    return owner, chat


def test_j05_removing_the_collaborator_mid_stream(stack, api, fake, roster) -> None:  # noqa: ANN001
    owner, chat = owner_chat(stack, api, fake, "del")
    writer = roster.write_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write")), "share")
    assert chats.send_message(api, writer, chat, "warm up").status_code == 200  # B has used the access, so the decision is cached
    prior = len(messages_of(stack.db, chat))

    def revoke() -> None:
        view = collab.ok(collab.remove(api, owner, chat, writer.user_id), "remove")
        assert view["collaboratorCount"] == 0

    run_revoked_mid_stream(stack, api, fake, chat, writer, prior, revoke)
    doc = session_doc(stack.db, chat)
    assert doc["sharedWith"] == [] and doc["isShared"] is False


def test_j05_removing_through_the_legacy_unshare_mid_stream(stack, api, fake, roster) -> None:  # noqa: ANN001
    owner, chat = owner_chat(stack, api, fake, "unshare")
    writer = roster.write_recipient
    chats.share_as_writer(api, owner, chat, writer)
    prior = len(messages_of(stack.db, chat))
    run_revoked_mid_stream(stack, api, fake, chat, writer, prior, lambda: chats.unshare(api, owner, chat, writer))


def project_chat(stack, api, label: str, writer):  # noqa: ANN001, ANN201
    """A chat the writer reaches only through a project (members: the writer as editor; ceiling: editor)."""
    owner = collab.fresh_actor(stack, f"J5{label}")
    pid = insert_project(stack.db, f"j5-{label}", owner, [project_member(writer, "editor", owner)], projectChatAccess="editor")
    chat = chats.create_chat(api, owner, "A question")
    assert api.put(f"{chats.CONVERSATIONS}/{chat}/project", owner, json_body={"projectId": str(pid)}).status_code == 200
    visible = api.patch(f"{chats.CONVERSATIONS}/{chat}/project-visibility", owner, json_body={"visibility": "project"})
    assert visible.status_code == 200, visible.text[:300]
    return owner, chat, pid


def test_j05_project_visibility_flip_mid_stream(stack, api, fake, roster) -> None:  # noqa: ANN001
    writer = roster.write_recipient
    owner, chat, _ = project_chat(stack, api, "flip", writer)
    assert chats.get_chat(api, writer, chat).status_code == 200
    prior = len(messages_of(stack.db, chat))

    def revoke() -> None:
        flipped = api.patch(f"{chats.CONVERSATIONS}/{chat}/project-visibility", owner, json_body={"visibility": "private"})
        assert flipped.status_code == 200, flipped.text[:300]

    run_revoked_mid_stream(stack, api, fake, chat, writer, prior, revoke)


def test_j05_removing_the_project_member_mid_stream(stack, api, fake, roster) -> None:  # noqa: ANN001
    writer = roster.write_recipient
    owner, chat, pid = project_chat(stack, api, "member", writer)
    assert chats.get_chat(api, writer, chat).status_code == 200
    prior = len(messages_of(stack.db, chat))

    def revoke() -> None:
        removed = api.delete(f"/api/v1/projects/{pid}/members/{writer.user_id}", owner)
        assert removed.status_code in (200, 204), removed.text[:300]

    run_revoked_mid_stream(stack, api, fake, chat, writer, prior, revoke)


def test_j05_a_second_collaborator_is_unaffected_by_the_revoke(stack, api, fake, roster) -> None:  # noqa: ANN001
    """Control: revoking B leaves C's access, and C's next send works."""
    owner, chat = owner_chat(stack, api, fake, "ctl")
    writer, other = roster.write_recipient, roster.read_recipient
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write"), collab.user(other, "write")), "share")
    collab.ok(collab.remove(api, owner, chat, writer.user_id), "remove")
    assert chats.send_message(api, other, chat, "still here").status_code == 200
    assert chats.error_of(chats.send_message(api, writer, chat, "gone")) == NOT_FOUND
