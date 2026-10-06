"""Journey J-15: share on first send.

Given a person composing a new chat, who has picked colleagues before the chat exists
When they send the first message with those collaborators as a `share` block
Then the chat is created and shared in one step, each colleague gets one notification, and a refused share leaves nothing behind

Owning phase: M2 (draft collaborators applied on the first send). Node half over real HTTP; the AI backend is the lane's fake.
Covers: the share applied (rows, access, one `chat.shared` notification each, after the chat exists), a retried first send that
neither duplicates the chat nor the notification, a share that is refused (another org, a disabled user, org-wide write with the
policy off) creating no chat at all, the org-level picker (`GET /conversations/mentionables`) with draft collaborators marked
`inChat`, a draft collaborator mentioned in the first message being no non-participant, and the flag-off answer.
"""

from __future__ import annotations

import time
import uuid

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.fake_backend import stream_answer
from helper.collab_stack.seeds import messages_of, session_doc

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

MENTIONS_FLAG = "ENABLE_CHAT_MENTIONS"
FLAG_CACHE_S = 12
STREAM = f"{chats.CONVERSATIONS}/stream"
ASSISTANT = {"type": "assistant", "id": "self"}


@pytest.fixture(scope="module")
def mentions_on(stack, flags, flag_on_for_module):  # noqa: ANN001, ANN201
    flags.set(True, key=MENTIONS_FLAG)
    time.sleep(FLAG_CACHE_S)
    yield
    flags.set(False, key=MENTIONS_FLAG)


@pytest.fixture
def world(stack, fake, mentions_on):  # noqa: ANN001, ANN201
    owner, writer, reader = (collab.fresh_actor(stack, label) for label in ("FSowner", "FSwriter", "FSreader"))
    fake.on("chat_stream", stream_answer("An answer"))
    return owner, writer, reader


def collaborator(actor, level: str = "write") -> dict[str, str]:  # noqa: ANN001
    return {"principalType": "user", "principalId": actor.user_id, "accessLevel": level}


def send(api, who, share: dict | None = None, *, client_id: str | None = None, **body):  # noqa: ANN001, ANN003, ANN201
    payload = {"query": "A first question", "chatMode": "agent", "clientMessageId": client_id or uuid.uuid4().hex[:12], **body}
    if share is not None:
        payload["share"] = share
    return api.stream(STREAM, who, json_body=payload).finish()


def chats_of(stack, owner) -> int:  # noqa: ANN001
    return stack.db["chatSessions"].count_documents({"userId": owner.oid})


def bell(stack, who, chat: str) -> list[dict]:  # noqa: ANN001
    return list(stack.db["notifications"].find({"assignedTo": who.oid, "type": "chat.shared", "redirectLink": {"$regex": chat}}))


def wait_for(predicate, what: str, timeout: float = 30):  # noqa: ANN001, ANN201
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.4)
    raise AssertionError(f"timed out waiting for {what}")


def test_j15_the_first_send_creates_the_chat_shared_and_each_colleague_gets_one_notification(stack, api, world) -> None:  # noqa: ANN001
    owner, writer, reader = world

    call = send(api, owner, {"collaborators": [collaborator(writer), collaborator(reader, "read")], "note": "Have a look"})

    assert call.status == 200 and call.result is not None, call.text[:400]
    chat = call.conversation_id
    session = session_doc(stack.db, chat)
    levels = {str(r["userId"]): r["accessLevel"] for r in session["sharedWith"]}
    assert levels == {writer.user_id: "write", reader.user_id: "read"} and session["isShared"] is True
    assert [r["messageType"] for r in messages_of(stack.db, chat)] == ["user_query", "bot_response"]

    wait_for(lambda: bell(stack, writer, chat) and bell(stack, reader, chat), "both notifications")
    assert len(bell(stack, writer, chat)) == 1 and len(bell(stack, reader, chat)) == 1
    assert bell(stack, owner, chat) == [], "the owner is not told of their own share"

    # The colleagues see the chat with the access they were given.
    assert chats.get_chat(api, writer, chat).json()["conversation"]["access"]["accessLevel"] == "write"
    assert chats.get_chat(api, reader, chat).json()["conversation"]["access"]["accessLevel"] == "read"


def test_j15_a_retried_first_send_neither_duplicates_the_chat_nor_the_notification(stack, api, world) -> None:  # noqa: ANN001
    owner, writer, _reader = world
    share = {"collaborators": [collaborator(writer)]}

    first = send(api, owner, share, client_id="retry-1")
    chat = first.conversation_id
    wait_for(lambda: bell(stack, writer, chat), "the notification")
    retry = send(api, owner, share, client_id="retry-1")

    assert retry.status == 409, retry.text[:300]
    time.sleep(2)
    assert chats_of(stack, owner) == 1 and len(bell(stack, writer, chat)) == 1
    assert len(session_doc(stack.db, chat)["sharedWith"]) == 1


@pytest.mark.parametrize(
    "who",
    ["other_org", "disabled"],
)
def test_j15_a_refused_share_creates_no_chat(stack, api, fake, roster, world, who) -> None:  # noqa: ANN001
    owner, _writer, _reader = world
    mark = fake.mark()

    call = send(api, owner, {"collaborators": [collaborator(getattr(roster, who))]})

    assert 400 <= call.status < 500, call.text[:300]
    assert "INVALID_PRINCIPAL" in call.text
    assert chats_of(stack, owner) == 0
    assert fake.since(mark, "chat_stream") == [], "nothing was sent to the AI backend"


def test_j15_org_wide_write_is_refused_while_the_policy_is_off_and_nothing_is_created(stack, api, world) -> None:  # noqa: ANN001
    owner, _writer, _reader = world
    everyone = {"principalType": "team", "principalId": f"all_{owner.org_id}", "accessLevel": "write"}

    call = send(api, owner, {"collaborators": [everyone], "confirmOrgWide": True})

    assert 400 <= call.status < 500, call.text[:300]
    assert chats_of(stack, owner) == 0


def test_j15_the_org_level_picker_marks_draft_collaborators_as_in_the_chat(stack, api, roster, world) -> None:  # noqa: ANN001
    owner, writer, _reader = world
    stack.db["users"].update_one({"_id": writer.oid}, {"$set": {"firstName": "Wendy", "middleName": "Zephyra", "lastName": "Writer", "fullName": "Wendy Writer"}})

    plain = api.get(f"{chats.CONVERSATIONS}/mentionables", owner, params={"q": "zeph"})
    assert plain.status_code == 200, plain.text[:300]
    people = [i for i in plain.json()["items"] if i["type"] == "user"]
    assert [(i["id"], i["inChat"]) for i in people] == [(writer.user_id, False)] and people[0]["email"] == writer.email

    drafted = api.get(f"{chats.CONVERSATIONS}/mentionables", owner, params={"q": "zeph", "include": writer.user_id})
    assert [(i["id"], i["inChat"]) for i in drafted.json()["items"] if i["type"] == "user"] == [(writer.user_id, True)]
    assert api.get(f"{chats.CONVERSATIONS}/mentionables", owner, params={"include": "nope"}).status_code == 400
    outsiders = api.get(f"{chats.CONVERSATIONS}/mentionables", owner, params={"q": "user", "limit": 20}).json()["items"]
    assert roster.other_org.user_id not in {i["id"] for i in outsiders}
    assert owner.user_id not in {i["id"] for i in outsiders}


def test_j15_a_draft_collaborator_mentioned_in_the_first_message_is_a_participant_and_another_colleague_is_not(stack, api, roster, world) -> None:  # noqa: ANN001
    owner, writer, reader = world
    mentions = [ASSISTANT, {"type": "user", "id": writer.user_id}, {"type": "user", "id": reader.user_id}]

    call = send(api, owner, {"collaborators": [collaborator(writer)]}, mentions=mentions)

    assert call.status == 200, call.text[:300]
    created = next(e for e in call.events if e.event == "CUSTOM" and e.data.get("name") == "conversation_created")
    assert created.data["value"]["nonParticipants"] == [reader.user_id]


def test_j15_with_collaborative_chats_off_a_share_block_is_a_404_and_nothing_is_created(stack, api, flags, world) -> None:  # noqa: ANN001
    owner, writer, _reader = world
    with flags.value(False):
        call = send(api, owner, {"collaborators": [collaborator(writer)]})
        assert call.status == 404, call.text[:300]
        assert chats_of(stack, owner) == 0
        plain = send(api, owner, None)
        assert plain.status == 200, "a first send without a share still works with the flag off"
