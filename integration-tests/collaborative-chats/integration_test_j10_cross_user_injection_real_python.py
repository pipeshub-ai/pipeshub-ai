"""Journey J-10 on the real Python services: the write guard blocks a tool call that uses another participant's address.

Given B's earlier turn in a shared chat names an address, and A then sends "continue"
When the (scripted) model answers A's turn with a write tool call whose argument is B's address
Then the real agent loop's write guard refuses the call, the model is told to ask A, and nothing is written;
     the same call is allowed when the address came from A's own message.

The fake-lane file next to this one covers what Node sends. Here Node, the query service and its agent loop run for real;
only the model's words are scripted (``helper/collab_stack/fake_llm.py``).
"""

from __future__ import annotations

import json

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_llm import llm_turn, messages_text, tool_call
from helper.collab_stack.real_python import SAVE_ARTIFACT, latest_tool_result, loop_calls, offers, stream_turn
from helper.collab_stack.seeds import messages_of

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_real_python,
    pytest.mark.usefixtures("flag_on_for_module"),
]

ADDRESS = "carol@partner.example"
main_loop = offers(SAVE_ARTIFACT)


def save_note(content: str) -> dict:
    return tool_call(SAVE_ARTIFACT, {"name": "followup.md", "content": content})


@pytest.fixture
def shared(stack, api, fake, roster):  # noqa: ANN001, ANN201
    """A's chat, shared with B as a writer, where B asked for something that names an address."""
    owner = collab.fresh_actor(stack, "J10A")
    writer = collab.fresh_actor(stack, "J10B")
    fake.script_llm(llm_turn("Hello, A.", when=main_loop))
    chat = chats.create_chat(api, owner, "hello team")
    chats.share_as_writer(api, owner, chat, writer)
    stream_turn(api, fake, writer, chat, f"Email the Q3 deck to {ADDRESS}", llm_turn("Noted, waiting for A.", when=main_loop))
    return owner, writer, chat


def test_j10_the_guard_blocks_a_write_that_uses_another_participants_address(stack, api, fake, shared) -> None:  # noqa: ANN001
    owner, writer, chat = shared
    mark = fake.mark()

    stream_turn(
        api, fake, owner, chat, "continue",
        llm_turn(tool_calls=[save_note(f"Send the deck to {ADDRESS}")], when=main_loop),
        llm_turn("I need A to confirm that address first.", when=main_loop),
    )

    calls = loop_calls(fake, mark, SAVE_ARTIFACT)
    assert len(calls) == 2
    refusal = latest_tool_result(calls[1])
    verdict = json.loads(refusal)
    assert verdict["status"] == "blocked" and verdict["code"] == "collaboration_write_guard"
    assert ADDRESS in verdict["literals"]
    assert "ask_user_question" in verdict["message"]
    assert "artifact_id" not in refusal, "the write ran"

    # Nothing was attached to the turn.
    answer = messages_of(stack.db, chat)[-1]
    assert answer["messageType"] == "bot_response" and not answer.get("artifacts")


def test_j10_the_same_write_runs_when_the_address_came_from_the_current_sender(stack, api, fake, shared) -> None:  # noqa: ANN001
    owner, writer, chat = shared
    mark = fake.mark()

    stream_turn(
        api, fake, owner, chat, f"please also send it to {ADDRESS}",
        llm_turn(tool_calls=[save_note(f"Send the deck to {ADDRESS}")], when=main_loop),
        llm_turn("Saved.", when=main_loop),
    )

    result = latest_tool_result(loop_calls(fake, mark, SAVE_ARTIFACT)[1])
    assert "collaboration_write_guard" not in result and "artifact_id" in result, result


def test_j10_the_model_sees_roster_refs_not_the_other_participants_address_or_ids(stack, api, fake, shared) -> None:  # noqa: ANN001
    owner, writer, chat = shared
    mark = fake.mark()

    stream_turn(api, fake, owner, chat, "continue", llm_turn("ok", when=main_loop))

    shown = messages_text(loop_calls(fake, mark, SAVE_ARTIFACT)[0].body)
    assert "## Shared Conversation" in shown and "## Current Sender" in shown
    assert "[participant_" in shown
    # The current sender's own profile may be shown to the model; the other participant's address and ids never are.
    for secret in (writer.email, writer.user_id, owner.user_id):
        assert secret not in shown, f"{secret} reached the model"
    # B's words are in the history under B's ref; the current sender is A's.
    assert f"Email the Q3 deck to {ADDRESS}" in shown
