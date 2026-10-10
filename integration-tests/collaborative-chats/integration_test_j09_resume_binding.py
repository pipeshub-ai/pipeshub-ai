"""Journey J-09: resume binding (Node half).

Given B's turn parked an `ask_user_question` card in a shared chat
When A answers it, by body `resume` or by the text `User selections: ...`
Then 403 RESUME_NOT_ALLOWED, no lease is taken and the AI backend is not called; when B answers it, the turn runs with `resume`

Owning phase: PH-05 (80-implementation-plan section 5); SEC-07, F-1, PH05-08, PH05-09, and regenerate (M-06, CL-19, O-1).
PH-08 adds the Python half (the AI backend ignoring the prefix) to this file.
"""

from __future__ import annotations

import json

import pytest
from bson import ObjectId

from helper.collab_stack import chats
from helper.collab_stack.fake_backend import Reply, ai_answer, ask_user_question, stream_answer
from helper.collab_stack.seeds import messages_of, session_doc

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

NOT_ALLOWED = "RESUME_NOT_ALLOWED"
AI_ROUTES = ("chat", "chat_stream", "agent_chat", "agent_chat_stream")
ANSWER = "User selections: staging"


def cards_of(stack, chat: str) -> list[dict]:  # noqa: ANN001
    return [m for m in messages_of(stack.db, chat) if m["messageType"] == "tool_call"]


def parked_card_by(api, stack, fake, who, chat: str) -> dict:  # noqa: ANN001
    """`who` sends a follow-up whose run parks on a question card; returns the persisted card row."""
    fake.on("chat_stream", ask_user_question())
    call = chats.stream_message(api, who, chat, "deploy it").finish()
    assert call.status == 200, call.text[:300]
    assert session_doc(stack.db, chat)["activeRun"] is None
    card = cards_of(stack, chat)[-1]
    assert str(card["requestedBy"]) == who.user_id, "the card records whom the question was put to"
    return card


def assert_refused_without_side_effects(stack, fake, chat: str, response, rows_before: int, mark: int) -> None:  # noqa: ANN001
    assert chats.error_of(response) == (403, NOT_ALLOWED), response.text[:300]
    assert session_doc(stack.db, chat)["activeRun"] is None, "a refused resume took a lease"
    assert fake.since(mark, *AI_ROUTES) == [], "a refused resume reached the AI backend"
    assert len(messages_of(stack.db, chat)) == rows_before, "a refused resume stored a row"


def test_j09_only_the_person_asked_may_answer_a_card(stack, api, fake, roster) -> None:  # noqa: ANN001
    owner, writer = roster.owner, roster.write_recipient
    chat = chats.create_chat(api, owner, "first question")
    chats.share_as_writer(api, owner, chat, writer)
    card = parked_card_by(api, stack, fake, writer, chat)
    card_id = str(card["_id"])
    rows, mark = len(messages_of(stack.db, chat)), fake.mark()

    # A, by naming the card (plain and streamed) and by the text prefix.
    by_body = chats.send_message(api, owner, chat, ANSWER, resume={"toolCallMessageId": card_id})
    assert_refused_without_side_effects(stack, fake, chat, by_body, rows, mark)
    by_text = chats.send_message(api, owner, chat, ANSWER)
    assert_refused_without_side_effects(stack, fake, chat, by_text, rows, mark)
    streamed = chats.stream_message(api, owner, chat, ANSWER, resume={"toolCallMessageId": card_id}).finish()
    assert streamed.status == 403 and json.loads(streamed.text)["error"]["code"] == NOT_ALLOWED
    assert "text/event-stream" not in streamed.response_headers.get("Content-Type", "")
    assert session_doc(stack.db, chat)["activeRun"] is None and fake.since(mark, *AI_ROUTES) == []

    # B answers their own card: the lease is taken, Python is told which card, and the row is B's.
    fake.on("chat_stream", stream_answer("deployed to staging"))
    answered = chats.stream_message(api, writer, chat, ANSWER, resume={"toolCallMessageId": card_id}).finish()
    assert answered.status == 200 and answered.result, answered.text[:300]
    sent = fake.since(mark, "chat_stream")
    assert len(sent) == 1 and sent[0].user_id == writer.user_id
    assert sent[0].body["resume"] == {"toolCallMessageId": card_id}
    assert sent[0].body["query"] == ANSWER
    last_user = [m for m in messages_of(stack.db, chat) if m["messageType"] == "user_query"][-1]
    assert str(last_user["authorUserId"]) == writer.user_id and last_user["content"] == ANSWER

    # PH05-09: the card is answered now, so it cannot be answered a second time, even by B.
    again = chats.send_message(api, writer, chat, ANSWER, resume={"toolCallMessageId": card_id})
    assert chats.error_of(again) == (403, NOT_ALLOWED)


def test_j09_the_text_prefix_alone_is_bound_to_the_card_asker_too(stack, api, fake, roster) -> None:  # noqa: ANN001
    """SEC-07: no `resume` field at all; B's plain `User selections:` message is allowed, A's is not."""
    owner, writer = roster.owner, roster.write_recipient
    chat = chats.create_chat(api, owner, "first question")
    chats.share_as_writer(api, owner, chat, writer)
    parked_card_by(api, stack, fake, writer, chat)
    rows, mark = len(messages_of(stack.db, chat)), fake.mark()

    assert_refused_without_side_effects(stack, fake, chat, chats.send_message(api, owner, chat, ANSWER), rows, mark)

    fake.on("chat", Reply(ai_answer("deployed")))
    ok = chats.send_message(api, writer, chat, ANSWER)
    assert ok.status_code == 200, ok.text[:300]
    assert len(fake.since(mark, "chat")) == 1


@pytest.mark.parametrize("lead", [" ", "\n", "\t \n", "\x1c", "\x85", "\u00a0"], ids=["space", "newline", "tab-space-newline", "fs-control", "nel", "nbsp"])
def test_j09_leading_whitespace_does_not_hide_a_card_answer(stack, api, fake, roster, lead) -> None:  # noqa: ANN001
    """B1: the AI backend reads `text.lstrip().startswith('User selections:')`, so the binding must see through the same whitespace."""
    owner, writer = roster.owner, roster.write_recipient
    chat = chats.create_chat(api, owner, "first question")
    chats.share_as_writer(api, owner, chat, writer)
    parked_card_by(api, stack, fake, writer, chat)
    rows, mark = len(messages_of(stack.db, chat)), fake.mark()

    refused = chats.send_message(api, owner, chat, lead + ANSWER)

    assert_refused_without_side_effects(stack, fake, chat, refused, rows, mark)
    fake.on("chat", Reply(ai_answer("deployed")))
    assert chats.send_message(api, writer, chat, lead + ANSWER).status_code == 200, "the person asked may answer, however the text starts"


def test_j09_a_card_made_by_regenerate_is_answered_by_its_asker_only(stack, api, fake, roster) -> None:  # noqa: ANN001
    """B2: regenerate keeps a new card on the replacement answer itself (no `tool_call` row); it binds to the person who asked."""
    owner, writer = roster.owner, roster.write_recipient
    chat = chats.create_chat(api, owner, "first question")
    chats.share_as_writer(api, owner, chat, writer)
    fake.on("chat", Reply(ai_answer("B answer")))
    assert chats.send_message(api, writer, chat, "B question").status_code == 200
    answer = messages_of(stack.db, chat)[-1]
    fake.on("chat_stream", ask_user_question())
    assert chats.regenerate(api, writer, chat, str(answer["_id"])).status_code == 200
    card = messages_of(stack.db, chat)[-1]
    assert card["_id"] == answer["_id"] and any("ask_user_question" in t.get("toolName", "") for t in card["tools"])
    assert not cards_of(stack, chat), "regenerate keeps the card on the answer, not on a tool_call row"
    rows, mark = len(messages_of(stack.db, chat)), fake.mark()

    named = chats.send_message(api, owner, chat, ANSWER, resume={"toolCallMessageId": str(card["_id"])})
    assert_refused_without_side_effects(stack, fake, chat, named, rows, mark)
    assert_refused_without_side_effects(stack, fake, chat, chats.send_message(api, owner, chat, ANSWER), rows, mark)

    fake.on("chat_stream", stream_answer("deployed"))
    mine = chats.stream_message(api, writer, chat, ANSWER, resume={"toolCallMessageId": str(card["_id"])}).finish()
    assert mine.status == 200 and mine.result, mine.text[:300]
    assert fake.since(mark, "chat_stream")[0].body["resume"] == {"toolCallMessageId": str(card["_id"])}


def test_j09_a_chat_nobody_else_can_write_keeps_the_prefix_a_plain_message(stack, api, fake, roster) -> None:  # noqa: ANN001
    """PH05-08: in a solo chat the text `User selections:` is an ordinary follow-up; there is no binding to apply."""
    owner = roster.owner
    chat = chats.create_chat(api, owner, "deploy it")
    card = parked_card_by(api, stack, fake, owner, chat)
    mark = fake.mark()

    fake.on("chat", Reply(ai_answer("deployed")))
    by_text = chats.send_message(api, owner, chat, ANSWER)
    assert by_text.status_code == 200, by_text.text[:300]
    sent = fake.since(mark, "chat")[0]
    assert sent.body["query"] == ANSWER and "resume" not in sent.body

    # With no card to answer, the prefix is still just text.
    fake.on("chat", Reply(ai_answer("fine")))
    assert chats.send_message(api, owner, chat, "User selections: nothing pending").status_code == 200
    assert str(card["requestedBy"]) == owner.user_id


def test_j09_a_first_send_cannot_resume_anything(stack, api, fake, roster) -> None:  # noqa: ANN001
    """A new conversation has no card: `resume` on /stream or /create is refused before any session, row or AI call."""
    owner = roster.owner
    body = {"query": "hello", "chatMode": "quick", "resume": {"toolCallMessageId": str(ObjectId())}}
    created = api.post(f"{chats.CONVERSATIONS}/create", owner, json_body=body)
    assert chats.error_of(created) == (403, NOT_ALLOWED), created.text[:300]
    streamed = api.stream(f"{chats.CONVERSATIONS}/stream", owner, json_body={**body, "chatMode": "internal_search"}).finish()
    assert streamed.status == 403 and "RESUME_NOT_ALLOWED" in streamed.text
    assert "text/event-stream" not in streamed.response_headers.get("Content-Type", "")
    assert stack.db["chatSessions"].count_documents({"initiator": ObjectId(owner.user_id)}) == 0
    assert fake.requests_for(*AI_ROUTES) == []


def test_j09_a_card_from_another_chat_is_not_a_card_of_this_one(stack, api, fake, roster) -> None:  # noqa: ANN001
    owner, writer = roster.owner, roster.write_recipient
    other = chats.create_chat(api, owner, "other chat")
    foreign = parked_card_by(api, stack, fake, owner, other)
    chat = chats.create_chat(api, owner, "this chat")
    chats.share_as_writer(api, owner, chat, writer)
    rows, mark = len(messages_of(stack.db, chat)), fake.mark()

    refused = chats.send_message(api, owner, chat, ANSWER, resume={"toolCallMessageId": str(foreign["_id"])})

    assert_refused_without_side_effects(stack, fake, chat, refused, rows, mark)


# ---- regenerate: only the asker (O-1) --------------------------------------------------------


def test_j09_only_the_asker_regenerates_an_answer(stack, api, fake, roster) -> None:  # noqa: ANN001
    """M-06 / CL-19 (superseded by O-1): the owner cannot regenerate B's answer; B can, and the replacement is B's."""
    owner, writer = roster.owner, roster.write_recipient
    fake.on("chat", Reply(ai_answer("A answer")), Reply(ai_answer("B answer")))
    chat = chats.create_chat(api, owner, "A question")
    chats.share_as_writer(api, owner, chat, writer)
    assert chats.send_message(api, writer, chat, "B question").status_code == 200
    rows = messages_of(stack.db, chat)
    question, answer = rows[-2], rows[-1]
    assert str(answer["requestedBy"]) == writer.user_id
    before, mark = session_doc(stack.db, chat), fake.mark()

    refused = chats.regenerate(api, owner, chat, str(answer["_id"]))
    assert chats.error_of(refused) == (403, "REGENERATE_NOT_ALLOWED"), refused.text[:300]
    assert fake.since(mark, *AI_ROUTES) == [] and session_doc(stack.db, chat)["activeRun"] is None
    assert session_doc(stack.db, chat)["rev"] == before["rev"], "a refused regenerate changed the conversation"

    fake.on("chat_stream", stream_answer("B answer, again"))
    done = chats.regenerate(api, writer, chat, str(answer["_id"]))
    assert done.status_code == 200, done.text[:300]

    sent = fake.since(mark, "chat_stream")[0]
    assert sent.user_id == writer.user_id
    assert [t["content"] for t in sent.body["previousConversations"]] == ["A question", "A answer"], "history stops before the regenerated question"
    after = messages_of(stack.db, chat)
    assert len(after) == len(rows), "the answer is replaced in place"
    replaced = after[-1]
    assert replaced["_id"] == answer["_id"] and replaced["content"] == "B answer, again"
    assert str(replaced["requestedBy"]) == writer.user_id and replaced["inReplyTo"] == question["_id"]
    assert replaced["runId"] == sent.body["runId"]
    assert session_doc(stack.db, chat)["rev"] > before["rev"], "a visible change bumps rev"
    assert session_doc(stack.db, chat)["activeRun"] is None
