"""Journey J-03: handover.

Given user A shares a chat with user B as a writer
When B sends a message
Then history is intact, retrieval runs as B, and the message is attributed to B

Owning phase: PH-05 (80-implementation-plan section 5); M-01, I-1, F-10, PH05-02, PH05-13.
Real HTTP into the Node API; the AI backend is the lane's fake, which records what Node sent it.
"""

from __future__ import annotations

import uuid

import pytest

from helper.collab_stack import chats
from helper.collab_stack.fake_backend import Reply, ai_answer, stream_answer
from helper.collab_stack.seeds import messages_of, session_doc

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

CLIENT_RUN_ID = "3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c"


def history_of(sent) -> list[str]:  # noqa: ANN001
    return [turn["content"] for turn in sent.body["previousConversations"]]


def shared_chat(stack, api, fake, owner, writer) -> str:  # noqa: ANN001
    """A's chat with two finished turns, shared with B as a writer."""
    fake.on("chat", Reply(ai_answer("A answer 1")), Reply(ai_answer("A answer 2")))
    chat = chats.create_chat(api, owner, "A question 1")
    assert chats.send_message(api, owner, chat, "A question 2").status_code == 200
    chats.share_as_writer(api, owner, chat, writer)
    return chat


def test_j03_stream_follow_up_by_a_writer_runs_as_the_writer(stack, api, fake, roster) -> None:  # noqa: ANN001
    """The streamed follow-up: B's identity reaches the AI backend, history is A's turns in order, rows are B's, runId is Node's."""
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, fake, owner, writer)
    fake.on("chat_stream", stream_answer("B answer"))
    mark = fake.mark()

    call = chats.stream_message(api, writer, chat, "B question", runId=CLIENT_RUN_ID, clientMessageId="b-msg-1")
    call.finish()
    assert call.status == 200 and call.result is not None, call.text[:500]

    # Identity: the forwarded token is B's, not the owner's, and so is the org/email it carries.
    sent = fake.since(mark, "chat_stream")
    assert len(sent) == 1
    assert sent[0].user_id == writer.user_id
    assert sent[0].token_claims["email"] == writer.email and sent[0].token_claims["orgId"] == writer.org_id
    assert sent[0].body["conversationId"] == chat and sent[0].body["query"] == "B question"
    # History: A's turns, in order, and not B's own message.
    assert history_of(sent[0]) == ["A question 1", "A answer 1", "A question 2", "A answer 2"]

    # Rows: appended to the same chat, in order, attributed to B.
    rows = messages_of(stack.db, chat)
    assert [r["seq"] for r in rows] == list(range(1, 7))
    assert [(r["messageType"], r["content"]) for r in rows] == [
        ("user_query", "A question 1"), ("bot_response", "A answer 1"),
        ("user_query", "A question 2"), ("bot_response", "A answer 2"),
        ("user_query", "B question"), ("bot_response", "B answer"),
    ]  # fmt: skip
    asked, answered = rows[4], rows[5]
    assert str(asked["authorUserId"]) == writer.user_id and asked["clientMessageId"] == "b-msg-1"
    assert str(answered["requestedBy"]) == writer.user_id and answered["inReplyTo"] == asked["_id"]
    assert "authorUserId" not in rows[0] or str(rows[0]["authorUserId"]) == owner.user_id  # A's rows are not rewritten
    assert str(rows[1].get("requestedBy", owner.user_id)) == owner.user_id

    # runId: minted by Node (not the client's), the same in the header, the first frame, the payload and the rows.
    run_id = call.response_headers["X-Run-Id"]
    first = call.events[0]
    assert first.event == "CUSTOM" and first.data["name"] == "conversation_created"
    assert first.data["value"]["runId"] == run_id
    assert sent[0].body["runId"] == run_id != CLIENT_RUN_ID
    assert uuid.UUID(run_id).version == 4
    assert asked["runId"] == answered["runId"] == run_id

    # Detail: a tab that only loaded the detail gets the same seq and named author as the feed.
    detail = api.get(f"/api/v1/conversations/{chat}", writer)
    assert detail.status_code == 200
    shown = sorted(detail.json()["conversation"]["messages"], key=lambda m: m["seq"])
    assert [m["seq"] for m in shown] == list(range(1, 7))
    assert [m["author"]["userId"] for m in shown[:4]] == [owner.user_id] * 4
    assert [m["author"]["userId"] for m in shown[4:]] == [writer.user_id] * 2
    assert shown[4]["author"]["displayName"] == f"User {writer.name}"

    # Bookkeeping: the lease is released and the session is idle again.
    after = session_doc(stack.db, chat)
    assert after["activeRun"] is None and after["status"] == "Complete"


def test_j03_non_stream_follow_up_by_a_writer_runs_as_the_writer(stack, api, fake, roster) -> None:  # noqa: ANN001
    """Same contract on the non-streaming route (C1): identity, history, attribution, and the run id in the header and the payload."""
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, fake, owner, writer)
    fake.on("chat", Reply(ai_answer("B answer")))
    mark = fake.mark()

    resp = chats.send_message(api, writer, chat, "B question", runId=CLIENT_RUN_ID)
    assert resp.status_code == 200, resp.text[:400]

    sent = fake.since(mark, "chat")[0]
    assert sent.user_id == writer.user_id
    assert history_of(sent) == ["A question 1", "A answer 1", "A question 2", "A answer 2"]
    run_id = resp.headers["X-Run-Id"]
    assert sent.body["runId"] == run_id != CLIENT_RUN_ID

    rows = messages_of(stack.db, chat)
    asked, answered = rows[-2], rows[-1]
    assert (asked["messageType"], answered["messageType"]) == ("user_query", "bot_response")
    assert str(asked["authorUserId"]) == writer.user_id
    assert str(answered["requestedBy"]) == writer.user_id and answered["inReplyTo"] == asked["_id"]
    assert asked["runId"] == answered["runId"] == run_id


def test_j03_owner_continues_after_the_writer_and_sees_both_in_order(stack, api, fake, roster) -> None:  # noqa: ANN001
    """The handover is not one way: A's next turn carries B's turn in its history, and the rows keep one ordered thread."""
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, fake, owner, writer)
    fake.on("chat", Reply(ai_answer("B answer")), Reply(ai_answer("A answer 3")))
    assert chats.send_message(api, writer, chat, "B question").status_code == 200
    mark = fake.mark()

    assert chats.send_message(api, owner, chat, "A question 3").status_code == 200

    sent = fake.since(mark, "chat")[0]
    assert sent.user_id == owner.user_id
    assert history_of(sent) == ["A question 1", "A answer 1", "A question 2", "A answer 2", "B question", "B answer"]
    rows = messages_of(stack.db, chat)
    assert [r["seq"] for r in rows] == sorted(r["seq"] for r in rows) == list(range(1, 9))
    assert str(rows[-2]["authorUserId"]) == owner.user_id and str(rows[-4]["authorUserId"]) == writer.user_id

