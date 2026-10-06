"""Journey J-12: mentions.

Given a shared chat with a composer that supports mentions
When a message mentions only a human, and another uses the @assistant alias
Then the human-only mention records a note with no AI reply, and the alias triggers the AI

Owning phase: PH-10 (80-implementation-plan section 5). PR-10.4 covers the Node half over real HTTP: validation,
the note path (no run, no lease, `seq` and `rev` advance, the feed delivers it), `respondMode`, and flag-off parity.
PR-10.5 adds the notification half: the mentioned person's bell gets `chat.mentioned` (outbox, then consumer), the actor's does
not, and what Python receives names people by roster ref only. The AI backend is the lane's fake.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_backend import stream_answer
from helper.collab_stack.seeds import insert_session, messages_of, session_doc, user_row

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

MENTIONS_FLAG = "ENABLE_CHAT_MENTIONS"
AI_ROUTES = ("chat", "chat_stream", "agent_chat", "agent_chat_stream")
CACHE_WAIT_S = 40
ASSISTANT = {"type": "assistant", "id": "self"}


def user(actor) -> dict[str, str]:  # noqa: ANN001
    return {"type": "user", "id": actor.user_id}


def note(api, who, chat: str, *mentions: dict[str, str], query: str = "FYI", client_id: str = "n1"):  # noqa: ANN001, ANN201
    return api.post(f"{chats.CONVERSATIONS}/{chat}/notes", who, json_body={"query": query, "mentions": list(mentions), "clientMessageId": client_id})


def refused(resp) -> tuple[int, str]:  # noqa: ANN001
    return resp.status_code, resp.json()["error"]["code"]


def shared_chat(stack, owner, writer, reader) -> str:  # noqa: ANN001
    """A's chat with one finished turn, shared with B as a writer and C as a reader (seeded: the share route is rate limited)."""
    seeded = insert_session(
        stack.db, f"j12-{uuid.uuid4().hex[:12]}", owner, shared_with=[user_row(writer, "write", principal_type=True), user_row(reader, "read", principal_type=True)]
    )
    return seeded.sid


def wait_for_mentions_flag(api, owner, chat: str, enabled: bool) -> None:  # noqa: ANN001
    """The API caches platform flags for ~10 s: poll the notes route (404 off, anything else on) until it agrees."""
    deadline = time.monotonic() + CACHE_WAIT_S
    while time.monotonic() < deadline:
        if (api.post(f"{chats.CONVERSATIONS}/{chat}/notes", owner, json_body={}).status_code != 404) == enabled:
            return
        time.sleep(0.5)
    raise TimeoutError(f"the mentions flag did not reach {enabled}")


@pytest.fixture(scope="module")
def mentions_on(stack, flags, flag_on_for_module) -> Iterator[str]:  # noqa: ANN001
    """The mentions flag on for the module; yields a chat used to probe the flag."""
    roster = stack.roster
    probe = shared_chat(stack, roster.owner, roster.write_recipient, roster.read_recipient)
    flags.set(True, key=MENTIONS_FLAG)
    wait_for_mentions_flag(stack.api, roster.owner, probe, True)
    yield probe
    flags.set(False, key=MENTIONS_FLAG)


@pytest.fixture
def world(stack, roster, mentions_on):  # noqa: ANN001, ANN201
    owner, writer, reader = roster.owner, roster.write_recipient, roster.read_recipient
    chat = shared_chat(stack, owner, writer, reader)
    return owner, writer, reader, chat


def test_j12_a_note_mentioning_a_person_is_stored_with_no_run_and_reaches_the_feed(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    before = session_doc(stack.db, chat)
    mark = fake.mark()

    resp = note(api, writer, chat, user(owner), query="A, please look at the numbers")
    assert resp.status_code == 201, resp.text[:300]

    assert fake.since(mark, *AI_ROUTES) == [], "a note reached the AI backend"
    rows = messages_of(stack.db, chat)
    stored = rows[-1]
    assert (stored["messageType"], stored["content"], stored["seq"]) == ("note", "A, please look at the numbers", len(rows))
    assert stored["mentions"] == [{"type": "user", "id": owner.user_id}]
    assert str(stored["authorUserId"]) == writer.user_id and stored["clientMessageId"] == "n1" and "runId" not in stored
    after = session_doc(stack.db, chat)
    assert after.get("activeRun") is None and after["status"] == before["status"]
    assert after["rev"] > before["rev"]

    # The feed delivers it to the person it names, with its author.
    feed = collab.ok(collab.feed(api, owner, chat, afterSeq=len(rows) - 1), "owner feed")
    assert [m["messageType"] for m in feed["messages"]] == ["note"]
    assert feed["messages"][0]["author"]["userId"] == writer.user_id and feed["rev"] == after["rev"]


def test_j12_the_same_client_message_id_returns_the_first_note(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    first, second = note(api, writer, chat, user(owner)), note(api, writer, chat, user(owner))
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["note"]["id"] == first.json()["note"]["id"] and second.json()["duplicate"] is True
    assert [r["messageType"] for r in messages_of(stack.db, chat)].count("note") == 1


def test_j12_a_human_only_message_on_the_stream_route_is_422_and_the_alias_is_answered(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    rows_before, mark = len(messages_of(stack.db, chat)), fake.mark()

    human = chats.stream_message(api, writer, chat, "just for A", mentions=[user(owner)]).finish()
    assert human.status == 422 and '"MESSAGE_IS_NOTE"' in human.text, (human.status, human.text[:300])
    assert fake.since(mark, *AI_ROUTES) == [] and len(messages_of(stack.db, chat)) == rows_before
    assert session_doc(stack.db, chat).get("activeRun") is None

    fake.on("chat_stream", stream_answer("The assistant answers"))
    asked = chats.stream_message(api, writer, chat, "@assistant summarize this", clientMessageId="b-1").finish()
    assert asked.status == 200 and asked.result is not None, asked.text[:400]
    rows = messages_of(stack.db, chat)
    question = rows[rows_before]
    assert question["messageType"] == "user_query" and question["mentions"] == [ASSISTANT]
    sent = fake.since(mark, "chat_stream")
    assert len(sent) == 1 and sent[0].body.get("mentions") == [{"type": "agent", "ref": "agent:self"}]


def test_j12_validation_refuses_other_orgs_and_foreign_teams_and_stores_nothing(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    rows_before = len(messages_of(stack.db, chat))
    assert refused(note(api, writer, chat, user(roster.other_org))) == (400, "MENTION_NOT_ALLOWED")
    streamed = chats.stream_message(api, writer, chat, "hi", mentions=[ASSISTANT, user(roster.other_org)]).finish()
    assert streamed.status == 400 and '"MENTION_NOT_ALLOWED"' in streamed.text
    assert refused(note(api, writer, chat, {"type": "team", "id": "a-team-not-on-this-chat"})) == (400, "MENTION_NOT_ALLOWED")
    assert refused(note(api, writer, chat, user(owner), ASSISTANT)) == (422, "MESSAGE_NOT_NOTE")
    assert note(api, writer, chat, *[user(owner)] * 1, client_id="x" * 65).status_code == 400
    assert len(messages_of(stack.db, chat)) == rows_before


def test_j12_a_colleague_outside_the_chat_is_accepted_and_reported_not_notified(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    resp = note(api, writer, chat, user(owner), user(roster.stranger), client_id="mn12-1")
    assert resp.status_code == 201, resp.text
    assert resp.json()["nonParticipants"] == [roster.stranger.user_id]
    stored = messages_of(stack.db, chat)[-1]
    assert stored["messageType"] == "note" and {"type": "user", "id": roster.stranger.user_id} in stored["mentions"]
    assert note(api, writer, chat, user(owner), client_id="mn12-2").json()["nonParticipants"] == []


def test_j12_only_a_writer_can_post_a_note(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, _writer, reader, chat = world
    assert refused(note(api, reader, chat, user(owner))) == (403, "CONVERSATION_READ_ONLY")
    assert note(api, roster.stranger, chat, user(owner)).status_code == 404


def test_j12_respond_mode_is_the_owners_to_set_and_mention_only_turns_plain_messages_into_notes(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    assert refused(collab.settings(api, writer, chat, respondMode="mention_only")) == (403, "CONVERSATION_OWNER_ONLY")
    assert collab.settings(api, owner, chat, respondMode="loud").status_code == 400
    out = collab.ok(collab.settings(api, owner, chat, respondMode="mention_only"), "owner sets mention_only")
    assert out["settings"]["respondMode"] == "mention_only"

    plain = chats.stream_message(api, writer, chat, "no mention at all").finish()
    assert plain.status == 422 and '"MESSAGE_IS_NOTE"' in plain.text
    # ...and the notes route takes it, so the composer never has to drop the text.
    posted = note(api, writer, chat, query="no mention at all", client_id="plain-note")
    assert posted.status_code == 201, posted.text[:300]
    assert messages_of(stack.db, chat)[-1]["messageType"] == "note"
    fake.on("chat_stream", stream_answer("answered"))
    assert chats.stream_message(api, writer, chat, "@PipesHub are you there?").finish().status == 200

    # `always` answers everything, so a human-only note would skip an answer that is owed.
    collab.ok(collab.settings(api, owner, chat, respondMode="always"), "owner sets always")
    assert refused(note(api, writer, chat, user(owner), client_id="n2")) == (422, "MESSAGE_NOT_NOTE")


def test_j12_with_mentions_off_the_routes_are_gone_and_the_field_is_ignored(stack, api, fake, roster, flags, mentions_on) -> None:  # noqa: ANN001
    owner, writer, reader = roster.owner, roster.write_recipient, roster.read_recipient
    chat = shared_chat(stack, owner, writer, reader)
    collab.ok(collab.settings(api, owner, chat, respondMode="mention_only"), "owner sets mention_only")
    flags.set(False, key=MENTIONS_FLAG)
    try:
        wait_for_mentions_flag(api, owner, mentions_on, False)
        assert note(api, writer, chat, user(owner)).status_code == 404
        assert api.get(f"{chats.CONVERSATIONS}/{chat}/mentionables", writer).status_code == 404
        fake.on("chat_stream", stream_answer("answered anyway"))
        mark = fake.mark()
        call = chats.stream_message(api, writer, chat, "plain", mentions=[user(owner)]).finish()
        assert call.status == 200 and call.result is not None, call.text[:300]
        assert len(fake.since(mark, "chat_stream")) == 1
        asked = messages_of(stack.db, chat)[-2]
        assert asked["messageType"] == "user_query" and "mentions" not in asked
    finally:
        flags.set(True, key=MENTIONS_FLAG)
        wait_for_mentions_flag(api, owner, mentions_on, True)


def test_j12_mentionables_list_the_assistant_and_the_chat_people_only(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, reader, chat = world

    def offered(who) -> set[str]:  # noqa: ANN001
        resp = api.get(f"{chats.CONVERSATIONS}/{chat}/mentionables", who)
        assert resp.status_code == 200, resp.text[:300]
        items = resp.json()["items"]
        assert items[0] == {"type": "assistant", "id": "self", "label": "PipesHub"}
        return {i["id"] for i in items if i["type"] == "user"}

    assert offered(owner) == {writer.user_id, reader.user_id}, "the caller is not offered, nobody outside the chat is"
    assert offered(writer) == {owner.user_id}, "I-9: a non-inviting editor learns only who owns the chat"
    assert offered(reader) == {owner.user_id}
    assert api.get(f"{chats.CONVERSATIONS}/{chat}/mentionables", roster.stranger).status_code == 404


def bell(api, who, chat: str, kind: str = "chat.mentioned") -> list[dict]:  # noqa: ANN001
    resp = api.get("/api/v1/notifications", who, params={"limit": 50})
    assert resp.status_code == 200, resp.text[:300]
    return [n for n in resp.json()["notifications"] if n.get("type") == kind and chat in json.dumps(n)]


def wait_for_bell(api, who, chat: str, timeout: float = 30) -> list[dict]:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    found: list[dict] = []
    while time.monotonic() < deadline:
        found = bell(api, who, chat)
        if found:
            return found
        time.sleep(0.5)
    raise AssertionError(f"no chat.mentioned notification for {who.name} within {timeout}s")


def test_j12_a_note_rings_the_mentioned_persons_bell_and_not_the_actors(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, reader, chat = world
    resp = note(api, writer, chat, user(owner), query="Numbers are in the SECRET-BUDGET sheet")
    assert resp.status_code == 201, resp.text[:300]

    (item,) = wait_for_bell(api, owner, chat)
    assert item["status"] == "unread" and chat in item["redirectLink"]
    assert item["payload"]["messageId"] == resp.json()["note"]["id"] and item["payload"]["actorUserId"] == writer.user_id
    assert "SECRET-BUDGET" not in json.dumps(item), "the note text is not in the notification"
    assert bell(api, writer, chat) == [], "the actor is not told about their own mention"
    assert bell(api, reader, chat) == [], "someone who was not mentioned is not told"


def test_j12_a_replayed_note_does_not_ring_twice(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    assert note(api, writer, chat, user(owner)).status_code == 201
    wait_for_bell(api, owner, chat)
    assert note(api, writer, chat, user(owner)).status_code == 200
    time.sleep(3)
    assert len(bell(api, owner, chat)) == 1


def test_j12_a_question_to_the_assistant_that_also_mentions_a_person_rings_that_persons_bell(stack, api, fake, roster, world) -> None:  # noqa: ANN001
    owner, writer, reader, chat = world
    mark = fake.mark()
    fake.on("chat_stream", stream_answer("on it"))
    asked = chats.stream_message(api, writer, chat, "@assistant draft a reply for the owner", mentions=[ASSISTANT, user(owner)], clientMessageId="q-1").finish()
    assert asked.status == 200 and asked.result is not None, asked.text[:300]

    wait_for_bell(api, owner, chat)
    assert bell(api, writer, chat) == [] and bell(api, reader, chat) == []
    (sent,) = fake.since(mark, "chat_stream")
    refs = sent.body["mentions"]
    assert [m["type"] for m in refs] == ["agent", "participant"] or [m["type"] for m in refs] == ["participant", "agent"]
    assert owner.user_id not in json.dumps(sent.body), "no user id crosses to the AI backend"
