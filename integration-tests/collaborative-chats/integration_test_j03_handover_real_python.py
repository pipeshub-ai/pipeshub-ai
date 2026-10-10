"""Journey J-03 on the real Python services: retrieval runs as the sender and the message is attributed to them.

Given A's chat with two finished turns, shared with B as a writer, and a file only A (and a file only B) can read
When B sends a follow-up
Then the history the model sees is A's turns in order, B's words carry B's roster ref, the knowledge tools run as B
     (A's file is invisible, B's own is not), the rows are B's, and A's next turn sees A's file again.

Node, the query service, its agent loop and the graph permission queries run for real; only the model's words are scripted.
Files are uploaded through the real knowledge-base route, so the permission edges are the connectors service's own.
"""

from __future__ import annotations

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_llm import llm_turn, messages_text, tool_call
from helper.collab_stack.real_python import LIST_FILES, create_kb, latest_tool_result, loop_calls, offers, stream_turn, upload_text
from helper.collab_stack.seeds import messages_of

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_real_python,
    pytest.mark.usefixtures("flag_on_for_module"),
]


def search_turns(name: str):  # noqa: ANN201
    """The model looks the file up by name, then answers."""
    return (
        llm_turn(tool_calls=[tool_call(LIST_FILES, {"query": name})], when=offers(LIST_FILES)),
        llm_turn("Done.", when=offers(LIST_FILES)),
    )


@pytest.fixture
def world(stack, api, fake):  # noqa: ANN001, ANN201
    """A and B, each with a knowledge base holding one file, and A's chat (two turns) shared with B as a writer."""
    owner, writer = collab.fresh_actor(stack, "J3A"), collab.fresh_actor(stack, "J3B")
    kb_a, kb_b = create_kb(api, owner, "A private"), create_kb(api, writer, "B private")
    upload_text(api, owner, kb_a, "alpha-roadmap.txt", "Roadmap: ship collaborative chats")
    upload_text(api, writer, kb_b, "bravo-budget.txt", "Budget: two engineers")
    fake.script_llm(llm_turn("A answer 1", when=offers(LIST_FILES)))
    chat = chats.create_chat(api, owner, "A question 1", filters={"kb": [kb_a], "apps": []})
    stream_turn(api, fake, owner, chat, "A question 2", llm_turn("A answer 2", when=offers(LIST_FILES)))
    chats.share_as_writer(api, owner, chat, writer)
    return owner, writer, chat, kb_a, kb_b


def test_j03_a_writers_follow_up_runs_as_the_writer(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, chat, kb_a, kb_b = world
    mark = fake.mark()

    stream_turn(api, fake, writer, chat, "B question", *search_turns("alpha"), filters={"kb": [kb_a], "apps": []})

    calls = loop_calls(fake, mark, LIST_FILES)
    assert len(calls) == 2
    # History: A's turns in order and B's words, with the sender's ref on B's line; the model never sees user ids.
    shown = messages_text(calls[0].body)
    assert shown.index("A question 1") < shown.index("A answer 1") < shown.index("A question 2") < shown.index("A answer 2") < shown.index("B question")
    assert "B question" in shown and "Current Sender" in shown
    # (The sender's own profile may be shown; nobody else's identifiers are.)
    for hidden in (owner.user_id, writer.user_id, owner.email):
        assert hidden not in shown
    # Retrieval ran as B: A's file, named in B's own filter, is not B's to see.
    assert latest_tool_result(calls[1]) == 'No items found matching "alpha".'

    rows = messages_of(stack.db, chat)
    asked, answered = rows[-2], rows[-1]
    assert (asked["messageType"], answered["messageType"]) == ("user_query", "bot_response")
    assert str(asked["authorUserId"]) == writer.user_id and str(answered["requestedBy"]) == writer.user_id
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))


def test_j03_the_writers_own_files_are_found_and_the_owners_are_not(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, chat, kb_a, kb_b = world
    mark = fake.mark()
    stream_turn(api, fake, writer, chat, "find mine", *search_turns("bravo"), filters={"kb": [kb_b], "apps": []})
    own = latest_tool_result(loop_calls(fake, mark, LIST_FILES)[1])
    assert own.startswith('Found 1 item matching "bravo"'), own

    # The owner's next turn cannot see B's file even though B filtered on it in this chat.
    mark = fake.mark()
    stream_turn(api, fake, owner, chat, "find bravo", *search_turns("bravo"), filters={"kb": [kb_b], "apps": []})
    assert latest_tool_result(loop_calls(fake, mark, LIST_FILES)[1]) == 'No items found matching "bravo".'


def test_j03_the_owner_continues_after_the_writer_and_sees_their_own_file(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, chat, kb_a, kb_b = world
    stream_turn(api, fake, writer, chat, "B question", llm_turn("B answer", when=offers(LIST_FILES)))
    mark = fake.mark()

    stream_turn(api, fake, owner, chat, "A question 3", *search_turns("alpha"), filters={"kb": [kb_a], "apps": []})

    calls = loop_calls(fake, mark, LIST_FILES)
    shown = messages_text(calls[0].body)
    assert shown.index("A answer 2") < shown.index("B question") < shown.index("B answer") < shown.index("A question 3")
    assert latest_tool_result(calls[1]).startswith('Found 1 item matching "alpha"')
    rows = messages_of(stack.db, chat)
    assert str(rows[-2]["authorUserId"]) == owner.user_id and str(rows[-4]["authorUserId"]) == writer.user_id
