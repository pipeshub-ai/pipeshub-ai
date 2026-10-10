"""Journey J-04: concurrency.

Given A is streaming in a shared chat
When B sends
Then B gets 409 CONVERSATION_BUSY naming A, nothing is stored or sent to the AI backend, and once A's run ends B can send

Owning phase: PH-05 (80-implementation-plan section 5); LS-01..05, LS-07, M-04, M-10, M-11, PH05-06, F-10, F-11.
Real HTTP; a gate on the fake AI backend keeps A's stream open while the test acts.
Also: duplicate `clientMessageId`s, a second Node instance on the same infrastructure, and a crashed instance's lease.
"""

from __future__ import annotations

import json
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
import requests
from bson import ObjectId

from helper.collab_stack import chats
from helper.collab_stack.client import Api
from helper.collab_stack.fake_backend import Drop, Frame, Reply, Sse, ai_answer, held_stream, run_error, stream_answer, text_frame
from helper.collab_stack.infra import wait_until
from helper.collab_stack.node_api import NodeApi
from helper.collab_stack.seeds import insert_session, messages_of, session_doc

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_on_for_module")]

AI_ROUTES = ("chat", "chat_stream", "agent_chat", "agent_chat_stream")
DUPLICATE = "DUPLICATE_MESSAGE"
BUSY = "CONVERSATION_BUSY"


def shared_chat(stack, api, owner, writer) -> str:  # noqa: ANN001
    chat = chats.create_chat(api, owner, "first question")
    chats.share_as_writer(api, owner, chat, writer)
    return chat


def active_run(stack, chat: str) -> dict | None:  # noqa: ANN001
    return session_doc(stack.db, chat).get("activeRun")


def user_rows(stack, chat: str) -> list[dict]:  # noqa: ANN001
    return [m for m in messages_of(stack.db, chat) if m["messageType"] == "user_query"]


def start_held_run(api, fake, who, chat: str, gate_name: str = "A-run", **body):  # noqa: ANN001, ANN201
    gate = fake.gate(gate_name)
    fake.on("chat_stream", held_stream(gate, "A is answering"))
    call = chats.stream_message(api, who, chat, "A is thinking", **body)
    gate.wait_reached()
    return call, gate


# ---- J-04: busy, then free -------------------------------------------------------------------


def test_j04_b_is_refused_while_a_streams_and_can_send_once_a_leaves(stack, api, fake, roster) -> None:  # noqa: ANN001
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, owner, writer)
    call, gate = start_held_run(api, fake, owner, chat)

    running = active_run(stack, chat)
    assert running is not None and str(running["userId"]) == owner.user_id
    assert running["runId"] == call.response_headers["X-Run-Id"]
    assert session_doc(stack.db, chat)["status"] == "Inprogress"
    rows_before, mark = len(messages_of(stack.db, chat)), fake.mark()

    # LS-01 / M-04: the plain and the streamed send are both refused with the holder named.
    plain = chats.send_message(api, writer, chat, "B jumps in")
    streamed = chats.stream_message(api, writer, chat, "B streams in").finish()
    assert "text/event-stream" not in streamed.response_headers.get("Content-Type", ""), "the refusal must be JSON before any SSE byte"
    for status, error in ((plain.status_code, plain.json()["error"]), (streamed.status, json.loads(streamed.text)["error"])):
        assert (status, error["code"]) == (409, BUSY), error
        holder = error["details"]["activeRun"]
        assert holder["userId"] == owner.user_id
        assert holder["displayName"]
        started = datetime.fromisoformat(holder["startedAt"].replace("Z", "+00:00"))
        assert abs(started - running["startedAt"].replace(tzinfo=timezone.utc)) < timedelta(seconds=1)
    assert fake.since(mark, *AI_ROUTES) == [], "a refused send reached the AI backend"
    assert len(messages_of(stack.db, chat)) == rows_before, "a refused send stored a row"
    assert str(active_run(stack, chat)["userId"]) == owner.user_id, "a refused send disturbed the holder's lease"

    # LS-03: A closes the tab; the lease is released and the session leaves Inprogress.
    call.abort()
    wait_until(lambda: active_run(stack, chat) is None, 15, message="A's lease to be released after the client closed")
    gate.open()
    assert session_doc(stack.db, chat)["status"] != "Inprogress"

    # B retries and gets through, as B.
    fake.on("chat", Reply(ai_answer("B answer")))
    retry = chats.send_message(api, writer, chat, "B jumps in")
    assert retry.status_code == 200, retry.text[:300]
    assert [str(m["authorUserId"]) for m in user_rows(stack, chat)[-1:]] == [writer.user_id]
    assert active_run(stack, chat) is None


def test_j04_the_lease_is_released_when_a_run_ends_in_each_way(stack, api, fake, roster) -> None:  # noqa: ANN001
    """LS-03 over HTTP: normal end, upstream RUN_ERROR and an upstream failure all leave the chat free for the next sender."""
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, owner, writer)
    dropped = Sse([Frame("RUN_STARTED", {"threadId": "t", "runId": "r"}), text_frame("partial"), Drop()])
    for reply in (stream_answer("done"), run_error("model overloaded"), dropped):
        fake.on("chat_stream", reply)
        chats.stream_message(api, owner, chat, "go").finish()
        wait_until(lambda: active_run(stack, chat) is None, 15, message="the lease to be released")
        fake.on("chat", Reply(ai_answer("fine")))
        assert chats.send_message(api, writer, chat, "my turn").status_code == 200


def test_j04_a_non_stream_send_holds_the_lease_too(stack, api, fake, roster) -> None:  # noqa: ANN001
    """M-11: a plain (non-stream) send by A takes the lease for its whole duration; B is refused meanwhile, then free."""
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, owner, writer)
    fake.on("chat", Reply(ai_answer("slow answer"), delay=2.0))
    with ThreadPoolExecutor(1) as pool:
        slow = pool.submit(chats.send_message, api, owner, chat, "A slow question")
        wait_until(lambda: active_run(stack, chat) is not None, 10, message="the plain send to take the lease")
        refused = chats.send_message(api, writer, chat, "B in a hurry")
        assert chats.error_of(refused) == (409, BUSY), refused.text[:300]
        assert slow.result(30).status_code == 200
    assert active_run(stack, chat) is None
    fake.on("chat", Reply(ai_answer("B answer")))
    assert chats.send_message(api, writer, chat, "B now").status_code == 200


def test_j04_two_racing_sends_on_an_idle_chat_one_wins(stack, api, fake, roster) -> None:  # noqa: ANN001
    """LS-02: A and B send at the same moment; exactly one runs and the other is told the chat is busy."""
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, owner, writer)
    fake.default("chat", Reply(ai_answer("answer"), delay=1.0))
    rows_before, mark = len(messages_of(stack.db, chat)), fake.mark()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda who: chats.send_message(api, who, chat, f"from {who.name}"), (owner, writer)))
    assert sorted(r.status_code for r in results) == [200, 409], [r.text[:200] for r in results]
    assert [chats.error_of(r) for r in results if r.status_code == 409] == [(409, BUSY)]
    assert len(messages_of(stack.db, chat)) == rows_before + 2, "only the winner's question and answer are stored"
    assert len(fake.since(mark, "chat")) == 1


# ---- duplicate clientMessageId ---------------------------------------------------------------


@pytest.mark.parametrize("path,body", [("/stream", {"chatMode": "internal_search"}), ("/create", {"chatMode": "quick"})], ids=["stream", "create"])
def test_j04_concurrent_first_sends_with_one_client_message_id_make_one_conversation(stack, api, fake, roster, path, body) -> None:  # noqa: ANN001
    """PH05-06: two first sends racing on the same clientMessageId create one conversation; the other is a 409 naming it."""
    owner = roster.owner
    fake.default("chat", Reply(ai_answer("first answer"), delay=0.8))
    fake.default("chat_stream", stream_answer("first answer"))
    payload = {"query": "my first question", "clientMessageId": "first-send-1", **body}

    def send(_: int):  # noqa: ANN202
        if path == "/create":
            return api.post(f"{chats.CONVERSATIONS}{path}", owner, json_body=payload)
        return api.stream(f"{chats.CONVERSATIONS}{path}", owner, json_body=payload).finish()

    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(send, range(2)))

    statuses = [getattr(r, "status_code", None) or r.status for r in (first, second)]
    assert sorted(statuses) == [200 if path == "/stream" else 201, 409], statuses
    sessions = list(stack.db["chatSessions"].find({"initiator": ObjectId(owner.user_id)}))
    assert len(sessions) == 1, f"{len(sessions)} conversations for one clientMessageId"
    loser = first if statuses[0] == 409 else second
    text = loser.text
    error = json.loads(text)["error"]
    assert error["code"] == DUPLICATE
    assert error["details"]["conversationId"] == str(sessions[0]["_id"])
    assert len(user_rows(stack, str(sessions[0]["_id"]))) == 1
    assert len(fake.requests_for("chat", "chat_stream")) == 1, "the loser reached the AI backend"

    # A later retry gets the same answer: same conversation id, and the original row's id.
    retry = api.post(f"{chats.CONVERSATIONS}/create", owner, json_body={"query": "my first question", "clientMessageId": "first-send-1", "chatMode": "quick"})
    assert chats.error_of(retry) == (409, DUPLICATE)
    assert chats.details_of(retry)["conversationId"] == str(sessions[0]["_id"])
    assert chats.details_of(retry)["messageId"] == str(user_rows(stack, str(sessions[0]["_id"]))[0]["_id"])


def test_j04_a_repeated_follow_up_client_message_id_is_refused_and_named(stack, api, fake, roster) -> None:  # noqa: ANN001
    """M-05 / AU-06: a retry of an answered follow-up is a 409 with the original row's id; another user may reuse the key (F-17)."""
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, owner, writer)
    fake.on("chat", Reply(ai_answer("B answer")))
    assert chats.send_message(api, writer, chat, "B asks", clientMessageId="k-1").status_code == 200
    original = user_rows(stack, chat)[-1]
    stack.db["chatSessions"].update_one({"_id": ObjectId(chat)}, {"$set": {"status": "Failed", "failReason": "earlier failure"}})
    mark, rows = fake.mark(), len(messages_of(stack.db, chat))

    again = chats.send_message(api, writer, chat, "B asks", clientMessageId="k-1")

    assert chats.error_of(again) == (409, DUPLICATE)
    assert chats.details_of(again) == {"messageId": str(original["_id"]), "answered": True}
    assert session_doc(stack.db, chat)["status"] == "Failed", "a refused duplicate must leave the status it found"
    assert fake.since(mark, *AI_ROUTES) == [] and len(messages_of(stack.db, chat)) == rows
    assert active_run(stack, chat) is None, "a duplicate must not leave a lease behind"

    fake.on("chat", Reply(ai_answer("A answer")))
    assert chats.send_message(api, owner, chat, "A reuses the key", clientMessageId="k-1").status_code == 200


def test_j04_concurrent_follow_ups_with_one_client_message_id_store_one_row(stack, api, fake, roster) -> None:  # noqa: ANN001
    """DB-09: the same author sends the same clientMessageId twice at once; one runs, the other is refused, and one user row exists."""
    writer = roster.write_recipient
    chat = shared_chat(stack, api, roster.owner, writer)
    fake.default("chat", Reply(ai_answer("answer"), delay=1.0))
    mark = fake.mark()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: chats.send_message(api, writer, chat, "twice", clientMessageId="k-race"), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409], [r.text[:200] for r in results]
    assert chats.error_of(next(r for r in results if r.status_code == 409))[1] in (BUSY, DUPLICATE)
    assert [m["content"] for m in user_rows(stack, chat)].count("twice") == 1
    assert len(fake.since(mark, "chat")) == 1
    assert active_run(stack, chat) is None


def test_j04_a_turn_that_never_started_puts_back_the_status_it_found(stack, api, fake, roster) -> None:  # noqa: ANN001
    """F4: the client leaves while the pre-lease checks run (here a slow readiness answer on an agent chat), so the turn never runs.

    The lease is taken and released at once; the chat keeps the `Failed` status and reason it had instead of becoming `Complete`.
    """
    owner = roster.owner
    chat = insert_session(stack.db, "never-started", owner, kind="agent", status="Failed", failReason="the earlier run failed").sid
    fake.default("agent_readiness", Reply({"canSend": True, "missingToolsets": [], "unauthenticatedToolsets": []}, delay=2.0))
    rows, mark = len(messages_of(stack.db, chat)), fake.mark()

    with pytest.raises(requests.exceptions.ReadTimeout):  # the client gives up and closes before any byte comes back
        requests.post(
            f"{api.base_url}{chats.stream_path(chat, chats.AGENT_KEY)}",
            headers=api.headers(owner),
            json={"query": "go", "chatMode": "quick"},
            timeout=0.5,
        )
    fake.wait_for_request("agent_readiness")
    rev_before = session_doc(stack.db, chat)["rev"]
    # `rev` moves once when the lease is taken and once when it is released; a lease that was never taken would leave it alone.
    wait_until(lambda: session_doc(stack.db, chat)["rev"] >= rev_before + 2, 15, message="the unstarted turn to take and release its lease")
    after = session_doc(stack.db, chat)
    assert after["activeRun"] is None
    assert (after["status"], after.get("failReason")) == ("Failed", "the earlier run failed")
    assert len(messages_of(stack.db, chat)) == rows and fake.since(mark, *AI_ROUTES) == []


# ---- cancel (LS-07 / F-10) -------------------------------------------------------------------


def test_j04_cancel_with_a_run_id_that_is_not_the_active_run_cancels_nothing(stack, api, fake, roster) -> None:  # noqa: ANN001
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, owner, writer)

    idle = chats.cancel(api, writer, chat, "3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c")
    assert idle.status_code == 200 and idle.json() == {"cancelled": False}, "no active run -> nothing to cancel"

    call, gate = start_held_run(api, fake, owner, chat)
    run_id = call.response_headers["X-Run-Id"]
    mark = fake.mark()

    wrong = chats.cancel(api, writer, chat, "3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c")
    assert wrong.status_code == 200 and wrong.json() == {"cancelled": False}
    wrong_by_starter = chats.cancel(api, owner, chat, "00000000-0000-4000-8000-000000000000")
    assert wrong_by_starter.json() == {"cancelled": False}
    assert fake.since(mark, "chat_cancel", "chat_cancel_participant") == [], "a mismatched runId reached the AI backend"
    assert active_run(stack, chat)["runId"] == run_id, "the run is untouched"

    # The matching id is forwarded, with the conversation, under the caller's own identity. A participant who did not
    # start the run goes through the participant route with a conversation-and-run-bound service token (PR-08e).
    right = chats.cancel(api, writer, chat, run_id)
    assert right.status_code == 200
    assert fake.since(mark, "chat_cancel") == []
    sent = fake.since(mark, "chat_cancel_participant")
    assert len(sent) == 1 and sent[0].body == {"runId": run_id, "conversationId": chat}
    assert sent[0].user_id == writer.user_id
    gate.open()
    call.finish()


# ---- two Node instances ----------------------------------------------------------------------


@pytest.fixture
def second_node(stack):  # noqa: ANN001, ANN201
    """A second API process on the same Mongo, Redis and fake backend (a second replica of the deployment)."""
    node = NodeApi(stack.infra.endpoints, stack.fake.url, stack.run_dir, node_root=stack.node_root)
    node.extra_env = dict(stack.node.extra_env)  # type: ignore[union-attr]
    node.start()
    try:
        yield node
    finally:
        node.stop()


def test_j04_a_lease_taken_on_one_instance_refuses_a_send_on_another(stack, api, fake, roster, second_node) -> None:  # noqa: ANN001
    """LS-01 across replicas: the lease lives in Mongo, so B on instance 2 meets A's run on instance 1."""
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, owner, writer)
    other = Api(second_node.base_url)
    call, gate = start_held_run(api, fake, owner, chat)
    assert active_run(stack, chat)["instanceId"], "the lease names its instance"
    mark = fake.mark()

    refused = chats.send_message(other, writer, chat, "B on the other instance")

    assert chats.error_of(refused) == (409, BUSY), refused.text[:300]
    assert chats.details_of(refused)["activeRun"]["userId"] == owner.user_id
    assert fake.since(mark, *AI_ROUTES) == []

    call.abort()
    wait_until(lambda: active_run(stack, chat) is None, 15, message="A's lease to be released")
    gate.open()
    fake.on("chat", Reply(ai_answer("B answer")))
    assert chats.send_message(other, writer, chat, "B on the other instance").status_code == 200


def test_j04_a_crashed_instance_does_not_hold_the_chat_past_its_lease(stack, api, fake, roster, second_node) -> None:  # noqa: ANN001
    """LS-04 / M-10: instance 2 dies mid-stream (SIGKILL: no release). The lease stays until its TTL passes, then B takes over.

    The TTL is 120 s. By default the test checks the refusal while the lease is live, then moves its expiry into the
    past in Mongo rather than waiting; `PCC_E2E_REAL_LEASE_TTL=1` waits the real time instead.
    """
    owner, writer = roster.owner, roster.write_recipient
    chat = shared_chat(stack, api, owner, writer)
    doomed = Api(second_node.base_url)
    gate = fake.gate("doomed")
    fake.on("chat_stream", held_stream(gate, "never finishes"))
    call = chats.stream_message(doomed, owner, chat, "A on the doomed instance")
    gate.wait_reached()
    lease = active_run(stack, chat)
    assert lease is not None and lease["leaseExpiresAt"].replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) + timedelta(seconds=60)

    os_kill(second_node)
    call.abort()
    assert active_run(stack, chat) is not None, "nobody released the dead instance's lease"
    assert session_doc(stack.db, chat)["status"] == "Inprogress"
    assert chats.error_of(chats.send_message(api, writer, chat, "B right after the crash")) == (409, BUSY)

    if os.environ.get("PCC_E2E_REAL_LEASE_TTL") == "1":
        wait_until(lambda: lease["leaseExpiresAt"].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc), 150, interval=2, message="the real lease TTL")
    else:
        stack.db["chatSessions"].update_one({"_id": ObjectId(chat)}, {"$set": {"activeRun.leaseExpiresAt": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    gate.open()

    fake.on("chat", Reply(ai_answer("B answer")))
    taken = chats.send_message(api, writer, chat, "B after the lease expired")
    assert taken.status_code == 200, taken.text[:300]
    assert active_run(stack, chat) is None
    assert str(user_rows(stack, chat)[-1]["authorUserId"]) == writer.user_id


def os_kill(node: NodeApi) -> None:
    """SIGKILL the whole process group of a running API."""
    proc, node.proc = node.proc, None
    assert proc is not None
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)


def test_j04_turn_writes_use_transactions_exactly_when_the_replica_set_mode_says_so(stack, api, fake, roster) -> None:  # noqa: ANN001
    """The lane runs in both REPLICA_SET_AVAILABLE modes; this proves the mode reaches the fenced turn writes."""
    chat_id = chats.create_chat(api, roster.owner)
    admin = stack.db.client.admin
    before = admin.command("serverStatus")["transactions"]["totalCommitted"]

    assert chats.send_message(api, roster.owner, chat_id).status_code == 200

    committed = admin.command("serverStatus")["transactions"]["totalCommitted"] - before
    if os.environ.get("PCC_E2E_REPLICA_SET_AVAILABLE", "false") == "true":
        assert committed > 0, "replica-set mode, but the turn committed no transaction"
    else:
        assert committed == 0, f"standalone mode, but the turn committed {committed} transaction(s)"
