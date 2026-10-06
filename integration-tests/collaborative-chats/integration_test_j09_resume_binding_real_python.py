"""Journey J-09 on the real Python services: a parked question card resumes only for the person it was put to.

Given B's turn in a shared chat parked an `ask_user_question` card (the model called the real tool)
When A answers it
Then Node refuses with 403 RESUME_NOT_ALLOWED and the model is not called; B answering runs the turn with B's goal and the answer.

Python's own half is exercised by posting the payload Node would send straight to the query service's ``/api/v1/chat/stream``
(Node would have refused it first, so this is the second wall): as A, with or without a `resume` field, the answer is a plain new
message, no pending goal comes back, and the guard still blocks a write that uses B's address; as B, the goal and the
confirmed write run.
"""

from __future__ import annotations

import pytest
import requests

from helper.collab_stack import chats, collab
from helper.collab_stack.client import parse_sse
from helper.collab_stack.fake_llm import llm_turn, messages_text, tool_call
from helper.collab_stack.identity import Directory
from helper.collab_stack.real_python import SAVE_ARTIFACT, UI_HEADERS, latest_tool_result, loop_calls, offers, stream_turn
from helper.collab_stack.seeds import messages_of, session_doc

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_real_python,
    pytest.mark.usefixtures("flag_on_for_module"),
]

ASK = "internaltools__ask_user_question"
NOT_ALLOWED = "RESUME_NOT_ALLOWED"
ADDRESS = "carol@partner.example"
GOAL = f"Email the Q3 deck to {ADDRESS}, ask me to confirm first"
SELECTIONS = 'User selections:\n1. "Send the Q3 deck now?" → Yes'
CARD_ID = "65f000000000000000000001"


def ask_turn() -> object:
    question = {"question": f"Send the Q3 deck to {ADDRESS} now?", "options": [{"label": "Yes"}, {"label": "No"}]}
    return llm_turn(tool_calls=[tool_call(ASK, {"user_intent": "confirm the send", "questions": [question]})], when=offers(ASK))


def cards_of(stack, chat: str) -> list[dict]:  # noqa: ANN001
    return [m for m in messages_of(stack.db, chat) if m["messageType"] == "tool_call"]


@pytest.fixture
def parked(stack, api, fake):  # noqa: ANN001, ANN201
    """A's chat, shared with B as a writer; B's turn parks a question card."""
    owner, writer = collab.fresh_actor(stack, "J9A"), collab.fresh_actor(stack, "J9B")
    fake.script_llm(llm_turn("Hello.", when=offers(ASK)))
    chat = chats.create_chat(api, owner, "first question")
    chats.share_as_writer(api, owner, chat, writer)
    stream_turn(api, fake, writer, chat, GOAL, ask_turn())
    assert session_doc(stack.db, chat)["activeRun"] is None
    card = cards_of(stack, chat)[-1]
    assert str(card["requestedBy"]) == writer.user_id, "the card records whom the question was put to"
    return owner, writer, chat, str(card["_id"])


def test_j09_only_the_person_asked_may_answer_the_card(stack, api, fake, parked) -> None:  # noqa: ANN001
    owner, writer, chat, card_id = parked
    rows, mark = len(messages_of(stack.db, chat)), fake.mark()

    by_body = chats.send_message(api, owner, chat, SELECTIONS, resume={"toolCallMessageId": card_id})
    by_text = chats.send_message(api, owner, chat, SELECTIONS)
    for refused in (by_body, by_text):
        assert chats.error_of(refused) == (403, NOT_ALLOWED), refused.text[:300]
    assert fake.llm_calls(mark) == [], "a refused resume reached the model"
    assert session_doc(stack.db, chat)["activeRun"] is None and len(messages_of(stack.db, chat)) == rows

    fake.script_llm(llm_turn("Deployed to staging.", when=offers(SAVE_ARTIFACT)))
    answered = api.stream(chats.stream_path(chat), writer, json_body={"query": SELECTIONS, "chatMode": "internal_search", "resume": {"toolCallMessageId": card_id}}, headers=UI_HEADERS).finish(90)
    assert answered.status == 200 and answered.result, answered.text[:300]
    shown = messages_text(loop_calls(fake, mark, SAVE_ARTIFACT)[0].body)
    assert GOAL in shown and "Yes" in shown, "the real loop resumed B's goal with B's answer"
    last_user = [m for m in messages_of(stack.db, chat) if m["messageType"] == "user_query"][-1]
    assert str(last_user["authorUserId"]) == writer.user_id

    again = chats.send_message(api, writer, chat, SELECTIONS, resume={"toolCallMessageId": card_id})
    assert chats.error_of(again) == (403, NOT_ALLOWED), "an answered card cannot be answered twice"


# ---- Python's own wall: the payload Node would send, posted to the query service as each person ----------------------


def collaboration(sender: str) -> dict:
    return {
        "participants": [
            {"ref": "participant_1", "displayName": "Ann", "isCurrentSender": sender == "participant_1"},
            {"ref": "participant_2", "displayName": "Ben", "isCurrentSender": sender == "participant_2"},
        ],
        "currentSenderRef": sender,
    }


PREVIOUS = [
    {"role": "user_query", "content": "hello team", "authorRef": "participant_1"},
    {"role": "bot_response", "content": "Hi!"},
    {"role": "user_query", "content": GOAL, "authorRef": "participant_2"},
    {
        "role": "bot_response",
        "content": "",
        "tool_results": [
            {
                "tool_name": ASK,
                "tool_id": "ask-1",
                "status": "success",
                "args": {"questions": [{"question": f"Send the Q3 deck to {ADDRESS} now?", "options": ["Yes", "No"]}]},
                "result": '{"status": "waiting"}',
            }
        ],
    },
]


def post_to_python(stack, who, chat: str, sender: str, query: str, *, resume: bool) -> list:  # noqa: ANN001
    body = {"query": query, "chatMode": "quick", "conversationId": chat, "previousConversations": PREVIOUS, "collaboration": collaboration(sender)}
    if resume:
        body["resume"] = {"toolCallMessageId": CARD_ID}
    resp = requests.post(
        f"{stack.python.query_url}/api/v1/chat/stream",
        headers={"Authorization": f"Bearer {Directory.session_token(who)}", **UI_HEADERS},
        json=body,
        timeout=90,
    )
    assert resp.status_code == 200, (resp.status_code, resp.text[:400])
    return parse_sse(resp.text)


def write_then_answer():  # noqa: ANN202
    return (
        llm_turn(tool_calls=[tool_call(SAVE_ARTIFACT, {"name": "deck.md", "content": f"Send the Q3 deck to {ADDRESS}"})], when=offers(SAVE_ARTIFACT)),
        llm_turn("Done.", when=offers(SAVE_ARTIFACT)),
    )


@pytest.mark.parametrize("resume", [False, True], ids=["text-prefix-only", "resume-field"])
def test_j09_python_treats_the_other_participants_answer_as_a_new_message(stack, fake, parked, resume) -> None:  # noqa: ANN001
    owner, writer, chat, _card_id = parked
    mark = fake.mark()
    fake.script_llm(*write_then_answer())

    post_to_python(stack, owner, chat, "participant_1", SELECTIONS, resume=resume)

    calls = loop_calls(fake, mark, SAVE_ARTIFACT)
    assert len(calls) == 2
    last_human = [m for m in calls[0].body["messages"] if m["role"] == "user"][-1]["content"]
    assert SELECTIONS in last_human and GOAL not in last_human, "A's answer must not bring B's pending goal back"
    refusal = latest_tool_result(calls[1])
    assert "collaboration_write_guard" in refusal and "artifact_id" not in refusal


def test_j09_python_resumes_the_goal_and_the_confirmed_write_for_the_asker(stack, fake, parked) -> None:  # noqa: ANN001
    owner, writer, chat, _card_id = parked
    mark = fake.mark()
    fake.script_llm(*write_then_answer())

    post_to_python(stack, writer, chat, "participant_2", SELECTIONS, resume=True)

    calls = loop_calls(fake, mark, SAVE_ARTIFACT)
    assert len(calls) == 2
    shown = messages_text(calls[0].body)
    assert GOAL in shown and "Yes" in shown
    result = latest_tool_result(calls[1])
    assert "collaboration_write_guard" not in result and "artifact_id" in result, result
