"""Journey J-14: guest agent turns.

Given an agent the owner can run, and an ordinary (default-assistant) chat
When the owner mentions that agent in the chat, on the first send and later, and a second participant reads the chat
Then the agent, not the assistant, answers that turn, the answer is attributed to it for everyone who can read the agent,
     and a message that mentions two agents, an agent nobody may run, or a service-account agent in a shared chat is refused

Owning phase: M2 (50-design section 2.4). Node half over real HTTP; the AI backend is the lane's fake, which serves the agent
directory (`GET /agent/{key}`, `GET /agent/`), readiness and the agent stream route. What is proven here is everything Node
decides: which backend the turn goes to, with whose token and what history, which row is stamped `respondingAgentKey`, what the
feed and the detail show each reader, the readiness refusal, and that nothing in an answer is ever read as a mention.

The last test is the people search of the same picker: an organisation colleague found by a middle name, offered as not in the chat,
and reported as a non-participant when mentioned.
"""

from __future__ import annotations

import time
import uuid

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_backend import Reply, stream_answer
from helper.collab_stack.seeds import messages_of, session_doc

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

BUILDER_FLAG = "ENABLE_CHAT_AGENT_BUILDER"
MENTIONS_FLAG = "ENABLE_CHAT_MENTIONS"
FLAG_CACHE_S = 12  # the API caches platform flags for 10 s
ASSISTANT = {"type": "assistant", "id": "self"}
STREAM = f"{chats.CONVERSATIONS}/stream"
AI = ("chat", "chat_stream", "agent_chat", "agent_chat_stream")


def agent(key: str) -> dict[str, str]:
    return {"type": "agent", "id": key}


@pytest.fixture(scope="module")
def guest_on(stack, flags, flag_on_for_module):  # noqa: ANN001, ANN201
    flags.set(True, key=BUILDER_FLAG)
    flags.set(True, key=MENTIONS_FLAG)
    time.sleep(FLAG_CACHE_S)
    yield
    flags.set(False, key=BUILDER_FLAG)
    flags.set(False, key=MENTIONS_FLAG)


@pytest.fixture
def world(stack, fake, guest_on):  # noqa: ANN001, ANN201
    """Fresh people (every cache in the API is per user) and one agent: runnable by the owner and the writer, not by the reader."""
    owner, writer, reader = (collab.fresh_actor(stack, label) for label in ("GAowner", "GAwriter", "GAreader"))
    key = f"joke-{uuid.uuid4().hex[:8]}"
    profile = {"_key": key, "name": "Joke Buddy", "handle": "joke-buddy", "isServiceAccount": False}
    runnable = {owner.user_id, writer.user_id}

    def item(rec):  # noqa: ANN001, ANN202
        if rec.user_id not in runnable or rec.match["agent_key"] != key:
            return Reply({"detail": "not found"}, status=404)
        return Reply({"status": "success", "agent": profile})

    fake.default("agent_item", item)
    fake.default("agent_list", lambda rec: Reply({"status": "success", "agents": [profile] if rec.user_id in runnable else [], "pagination": {}}))
    yield owner, writer, reader, key
    fake.default("agent_item", None)
    fake.default("agent_list", None)


def first_send(api, who, query: str, key: str | None, **body):  # noqa: ANN001, ANN003, ANN201
    mentions = [agent(key)] if key else []
    return api.stream(STREAM, who, json_body={"query": query, "chatMode": "agent", "mentions": mentions, "clientMessageId": uuid.uuid4().hex[:12], **body}).finish()


def follow_up(api, who, chat: str, query: str, *mentions: dict[str, str]):  # noqa: ANN001, ANN201
    return chats.stream_message(api, who, chat, query, mentions=list(mentions), clientMessageId=uuid.uuid4().hex[:12]).finish()


def code_of(call) -> tuple[int, str | None]:  # noqa: ANN001
    import json

    try:
        return call.status, json.loads(call.text)["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return call.status, None


def test_j14_a_first_send_that_mentions_the_agent_is_answered_by_it_and_a_second_participant_sees_who(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, reader, key = world
    fake.on("agent_chat_stream", stream_answer("Why did the chicken cross the road?"))
    mark = fake.mark()

    call = first_send(api, owner, "Tell me a joke", key)

    assert call.status == 200 and call.result is not None, call.text[:400]
    assert fake.since(mark, "chat_stream") == [], "the assistant did not answer"
    [sent] = fake.since(mark, "agent_chat_stream")
    assert sent.match["agent_key"] == key and sent.user_id == owner.user_id, "the agent is run as the sender"
    chat = call.conversation_id
    assert chat
    session = session_doc(stack.db, chat)
    assert session["sessionType"] == "chat" and "agentKey" not in session, "the chat keeps its identity"
    rows = messages_of(stack.db, chat)
    assert [r["messageType"] for r in rows] == ["user_query", "bot_response"]
    assert rows[0]["mentions"] == [agent(key)]
    assert rows[1]["respondingAgentKey"] == key and rows[1]["content"].startswith("Why did the chicken")

    # The owner hands the chat to B (writer) and C (reader); each sees the answer attributed, as far as they may read the agent.
    chats.share_as_writer(api, owner, chat, writer)
    assert chats.share(api, owner, chat, reader, level="read").status_code == 200
    seen = {}
    for who in (owner, writer, reader):
        answer = next(m for m in collab.ok(collab.feed(api, who, chat), "feed")["messages"] if m["messageType"] == "bot_response")
        seen[who.name] = answer["respondingAgent"]
    assert seen[owner.name] == seen[writer.name] == {"key": key, "name": "Joke Buddy", "handle": "joke-buddy"}
    assert seen[reader.name] == {"key": key}, "a reader who cannot run the agent sees only its key"
    detail = chats.get_chat(api, writer, chat).json()["conversation"]["messages"]
    assert next(m for m in detail if m["messageType"] == "bot_response")["respondingAgent"]["handle"] == "joke-buddy"


def test_j14_a_follow_up_goes_to_the_agent_with_the_history_and_the_sender_s_token_and_the_next_one_back_to_the_assistant(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, _reader, key = world
    fake.on("agent_chat_stream", stream_answer("Knock knock"))
    chat = first_send(api, owner, "Tell me a joke", key).conversation_id
    chats.share_as_writer(api, owner, chat, writer)

    mark = fake.mark()
    fake.on("agent_chat_stream", stream_answer("Who is there?"))
    call = follow_up(api, writer, chat, "Who is there?", agent(key))
    assert call.status == 200, call.text[:400]
    [sent] = fake.since(mark, "agent_chat_stream")
    assert sent.user_id == writer.user_id and sent.match["agent_key"] == key
    assert [t["content"] for t in sent.body["previousConversations"]][:2] == ["Tell me a joke", "Knock knock"]
    assert sent.body["previousConversations"][1]["agentRef"] == f"agent:joke-buddy"
    assert sent.body["collaboration"]["participants"], "the shared-chat payload is the assistant turn's"
    rows = messages_of(stack.db, chat)
    assert rows[-1]["respondingAgentKey"] == key and str(rows[-1]["requestedBy"]) == writer.user_id

    mark = fake.mark()
    fake.on("chat_stream", stream_answer("The assistant answers"))
    plain = follow_up(api, writer, chat, "and now something else")
    assert plain.status == 200, plain.text[:400]
    assert fake.since(mark, "agent_chat_stream") == [] and len(fake.since(mark, "chat_stream")) == 1
    assert "respondingAgentKey" not in messages_of(stack.db, chat)[-1]
    assert session_doc(stack.db, chat).get("activeRun") is None


def test_j14_an_agent_answer_that_names_agents_is_never_read_as_a_mention(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, _writer, _reader, key = world
    fake.on("agent_chat_stream", stream_answer("Ask <@agent:other-agent> and @assistant, then <@user:{}>".format(owner.user_id)))
    chat = first_send(api, owner, "Tell me a joke", key).conversation_id
    answer = messages_of(stack.db, chat)[-1]
    assert answer["messageType"] == "bot_response" and not answer.get("mentions")

    mark = fake.mark()
    fake.on("chat_stream", stream_answer("assistant"))
    assert follow_up(api, owner, chat, "thanks").status == 200
    assert fake.since(mark, "agent_chat_stream") == [], "the next turn was not handed on to an agent named in the answer"


def test_j14_two_agents_an_unrunnable_agent_and_a_service_account_agent_are_refused_before_anything_runs(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, _reader, key = world
    fake.on("agent_chat_stream", stream_answer("hi"))
    chat = first_send(api, owner, "Tell me a joke", key).conversation_id
    chats.share_as_writer(api, owner, chat, writer)
    rows, mark = len(messages_of(stack.db, chat)), fake.mark()

    assert code_of(follow_up(api, writer, chat, "two", agent(key), agent("another-agent"))) == (422, "TOO_MANY_AGENT_MENTIONS")
    assert code_of(follow_up(api, writer, chat, "nobody may run it", agent("not-runnable"))) == (403, "MENTION_NOT_ALLOWED")
    assert fake.since(mark, *AI) == [] and len(messages_of(stack.db, chat)) == rows
    assert session_doc(stack.db, chat).get("activeRun") is None

    sa_key = f"sa-{uuid.uuid4().hex[:8]}"
    fake.default("agent_item", lambda rec: Reply({"status": "success", "agent": {"_key": sa_key, "name": "Robot", "handle": "robot", "isServiceAccount": True}}))
    shared = code_of(follow_up(api, writer, chat, "robot", agent(sa_key)))
    assert shared == (403, "MENTION_SA_AGENT_SHARED"), "a service-account agent answers from its creator's access, so not in a shared chat"
    solo = first_send(api, owner, "robot in a chat of my own", sa_key)
    assert solo.status == 200, solo.text[:300]


def test_j14_a_guest_agent_whose_tools_the_sender_has_not_connected_is_the_existing_setup_error(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, _writer, _reader, key = world
    fake.default("agent_readiness", Reply({"canSend": False, "missingToolsets": ["slack"], "unauthenticatedToolsets": []}))
    try:
        mark = fake.mark()
        refused = first_send(api, owner, "Tell me a joke", key)
        assert code_of(refused) == (412, "CONNECTOR_SETUP_REQUIRED"), refused.text[:300]
        assert fake.since(mark, *AI) == []
        assert stack.db["chatSessions"].count_documents({"userId": owner.oid}) == 0, "no chat was created"
    finally:
        fake.default("agent_readiness", None)


def test_j14_the_picker_offers_the_agents_the_caller_can_run_in_any_chat_and_the_validator_accepts_exactly_those(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, reader, key = world
    fake.on("chat_stream", stream_answer("hello"))
    chat = first_send(api, owner, "hello", None).conversation_id
    chats.share_as_writer(api, owner, chat, writer)
    assert chats.share(api, owner, chat, reader, level="read").status_code == 200

    def offered(who) -> list[str]:  # noqa: ANN001
        return [i["id"] for i in collab.mentionables(api, who, chat) if i["type"] == "agent"]

    assert offered(owner) == [key] and offered(writer) == [key] and offered(reader) == []
    item = next(i for i in collab.mentionables(api, owner, chat) if i["type"] == "agent")
    assert item == {"type": "agent", "id": key, "label": "Joke Buddy", "handle": "joke-buddy"}
    fake.on("agent_chat_stream", stream_answer("ha"))
    assert follow_up(api, writer, chat, "joke please", agent(key)).status == 200
    assert code_of(follow_up(api, writer, chat, "joke please", agent("never-offered")))[0] == 403


def test_j14_a_colleague_found_by_a_middle_name_is_offered_as_not_in_the_chat_and_reported_when_mentioned(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, _writer, _reader, _key = world
    colleague = collab.fresh_actor(stack, "GAcolleague")
    stack.db["users"].update_one({"_id": colleague.oid}, {"$set": {"firstName": "Pat", "middleName": "Quillon", "lastName": "Rivers", "fullName": "Pat Rivers"}})
    fake.on("chat_stream", stream_answer("hello"))
    chat = first_send(api, owner, "A chat of my own", None).conversation_id

    items = [i for i in collab.mentionables(api, owner, chat) if i["type"] == "user"]
    assert items == [], "a solo chat lists no one until the owner types"
    resp = api.get(f"{chats.CONVERSATIONS}/{chat}/mentionables", owner, params={"q": "quil"})
    assert resp.status_code == 200, resp.text[:300]
    found = [i for i in resp.json()["items"] if i["type"] == "user"]
    assert found == [{"type": "user", "id": colleague.user_id, "label": "Pat Rivers", "inChat": False, "email": colleague.email}]
    assert api.get(f"{chats.CONVERSATIONS}/{chat}/mentionables", owner, params={"q": "pat riv"}).json()["items"][-1]["id"] == colleague.user_id
    assert [i for i in api.get(f"{chats.CONVERSATIONS}/{chat}/mentionables", owner, params={"q": "pat zzz"}).json()["items"] if i["type"] == "user"] == []

    call = follow_up(api, owner, chat, "@assistant, Pat should see this", ASSISTANT, {"type": "user", "id": colleague.user_id})
    assert call.status == 200, call.text[:300]
    created = next(e for e in call.events if e.event == "CUSTOM" and e.data.get("name") == "conversation_created")
    assert created.data["value"]["nonParticipants"] == [colleague.user_id]
    assert messages_of(stack.db, chat)[-2]["mentions"] == [ASSISTANT, {"type": "user", "id": colleague.user_id}]
