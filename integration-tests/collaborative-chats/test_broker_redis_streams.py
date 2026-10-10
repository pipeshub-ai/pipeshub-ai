"""PH12-05: share and mention notifications travel the Redis Streams broker and are delivered once.

Given ``MESSAGE_BROKER=redis`` (the lane's broker: outbox -> Redis stream ``notification`` -> consumer group -> in-app bell)
When a chat is shared and a person is mentioned, and then the same stream message is delivered again and Node restarts
Then each event is one outbox row (published once), one stream entry and one bell item; a redelivered entry adds no second bell item
     (the ``{assignedTo, dedupeKey}`` unique index); every consumer group has nothing pending; a restart replays nothing.

Holds with the fake Python services and with the real ones (``PCC_E2E_REAL_PYTHON=1``, where the connectors service reads the
``entity-events`` stream of the same Redis).
"""

from __future__ import annotations

import json
import time

import pytest

from helper.collab_stack import chats, collab
from helper.collab_stack.seeds import insert_session, user_row

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_both_modes,
    pytest.mark.usefixtures("flag_on_for_module"),
]

MENTIONS_FLAG = "ENABLE_CHAT_MENTIONS"
FLAG_CACHE_S = 12
STREAM = "notification"
STACK_KEEP_STATE = True


@pytest.fixture(scope="module")
def mentions_on(stack, flags, flag_on_for_module):  # noqa: ANN001, ANN201
    flags.set(True, key=MENTIONS_FLAG)
    time.sleep(FLAG_CACHE_S)
    yield
    flags.set(False, key=MENTIONS_FLAG)


def entries_about(stack, chat: str, kind: str) -> list[tuple[str, dict[str, str]]]:  # noqa: ANN001
    """Stream entries whose payload names the chat and the notification type."""
    found = []
    for entry_id, fields in stack.infra.redis.xrange(STREAM, "-", "+"):
        text = json.dumps(fields)
        if chat in text and kind in text:
            found.append((entry_id, fields))
    return found


def outbox_rows(stack, chat: str) -> list[dict]:  # noqa: ANN001
    return list(stack.db["outbox_events"].find({"topic": STREAM, "value": {"$regex": chat}}))


def bell_docs(stack, who, chat: str, kind: str) -> list[dict]:  # noqa: ANN001
    return list(stack.db["notifications"].find({"assignedTo": who.oid, "type": kind, "redirectLink": {"$regex": chat}}))


def wait_for(predicate, what: str, timeout: float = 30):  # noqa: ANN001, ANN201
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.4)
    raise AssertionError(f"timed out waiting for {what}")


def pending_in_groups(stack) -> int:  # noqa: ANN001
    return sum(int(g.get("pending", 0)) for g in stack.infra.redis.xinfo_groups(STREAM))


def settled(stack) -> bool:  # noqa: ANN001
    return pending_in_groups(stack) == 0 and not stack.db["outbox_events"].count_documents({"status": {"$in": ["pending", "publishing"]}})


@pytest.fixture
def world(stack, api, mentions_on):  # noqa: ANN001, ANN201
    owner, writer = collab.fresh_actor(stack, "BRowner"), collab.fresh_actor(stack, "BRwriter")
    return owner, writer


def test_ph12_05_the_lane_runs_on_redis_streams(stack) -> None:  # noqa: ANN001
    assert stack.node is not None and stack.node.env()["MESSAGE_BROKER"] == "redis"
    assert stack.infra.redis.type(STREAM) in ("stream", "none")


def test_ph12_05_a_share_is_one_outbox_row_one_stream_entry_and_one_bell_item(stack, api, world) -> None:  # noqa: ANN001
    owner, writer = world
    chat = chats.create_chat(api, owner, "A question")

    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write"), note="have a look"), "share")

    wait_for(lambda: bell_docs(stack, writer, chat, "chat.shared"), "the share to reach the bell")
    wait_for(lambda: settled(stack), "the pipeline to settle")
    assert len(bell_docs(stack, writer, chat, "chat.shared")) == 1
    assert bell_docs(stack, owner, chat, "chat.shared") == [], "the actor is not notified of their own share"
    assert len(entries_about(stack, chat, "chat.shared")) == 1, "one stream entry per share event"
    rows = [r for r in outbox_rows(stack, chat) if "chat.shared" in r["value"]]
    assert len(rows) == 1 and rows[0]["status"] == "published" and rows[0].get("publishedAt")
    assert pending_in_groups(stack) == 0


def test_ph12_05_a_mention_is_delivered_once_to_the_person_mentioned(stack, api, world) -> None:  # noqa: ANN001
    owner, writer = world
    seeded = insert_session(stack.db, f"br-{owner.name}", owner, shared_with=[user_row(writer, "write", principal_type=True)])
    chat = seeded.sid

    resp = collab.note(api, writer, chat, {"type": "user", "id": owner.user_id}, query="look at this", client_id="br-1")
    assert resp.status_code == 201, resp.text[:300]

    wait_for(lambda: bell_docs(stack, owner, chat, "chat.mentioned"), "the mention to reach the bell")
    wait_for(lambda: settled(stack), "the pipeline to settle")
    assert len(bell_docs(stack, owner, chat, "chat.mentioned")) == 1
    assert bell_docs(stack, writer, chat, "chat.mentioned") == []
    assert len(entries_about(stack, chat, "chat.mentioned")) == 1
    # The same note again is a duplicate request. The broker is at-least-once, so an event may be published again; it carries the
    # same dedupe key, which is what keeps the bell at one item.
    again = collab.note(api, writer, chat, {"type": "user", "id": owner.user_id}, query="look at this", client_id="br-1")
    assert again.status_code == 200
    wait_for(lambda: settled(stack), "the pipeline to settle")
    time.sleep(2)
    keys = {json.loads(json.loads(f["value"]))["dedupeKey"] for _, f in entries_about(stack, chat, "chat.mentioned")}
    assert len(keys) == 1, f"a replayed note must reuse the dedupe key: {keys}"
    assert len(bell_docs(stack, owner, chat, "chat.mentioned")) == 1


def test_ph12_05_a_redelivered_stream_entry_does_not_ring_twice(stack, api, world) -> None:  # noqa: ANN001
    owner, writer = world
    chat = chats.create_chat(api, owner, "A question")
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write")), "share")
    wait_for(lambda: bell_docs(stack, writer, chat, "chat.shared"), "the share to reach the bell")
    wait_for(lambda: settled(stack), "the pipeline to settle")
    [(_, fields)] = entries_about(stack, chat, "chat.shared")

    # An at-least-once broker may deliver an entry again: put the very same message on the stream twice more.
    for _ in range(2):
        stack.infra.redis.xadd(STREAM, fields)
    wait_for(lambda: len(entries_about(stack, chat, "chat.shared")) == 3 and pending_in_groups(stack) == 0, "the copies to be consumed and acknowledged")
    time.sleep(2)

    assert len(bell_docs(stack, writer, chat, "chat.shared")) == 1, "the dedupe key must absorb the redelivery"


def test_ph12_05_a_restart_replays_nothing(stack, api, world) -> None:  # noqa: ANN001
    owner, writer = world
    chat = chats.create_chat(api, owner, "A question")
    collab.ok(collab.put(api, owner, chat, collab.user(writer, "write")), "share")
    wait_for(lambda: bell_docs(stack, writer, chat, "chat.shared"), "the share to reach the bell")
    wait_for(lambda: settled(stack), "the pipeline to settle")
    before = (len(entries_about(stack, chat, "chat.shared")), len(bell_docs(stack, writer, chat, "chat.shared")), [r["publishedAt"] for r in outbox_rows(stack, chat)])

    stack.node.restart()
    time.sleep(6)

    after = (len(entries_about(stack, chat, "chat.shared")), len(bell_docs(stack, writer, chat, "chat.shared")), [r["publishedAt"] for r in outbox_rows(stack, chat)])
    assert after == before
    assert pending_in_groups(stack) == 0
