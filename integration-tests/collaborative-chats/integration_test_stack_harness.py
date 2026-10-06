"""Self-tests of the real-HTTP lane: what later journeys (J-03 to J-13) rely on from it.

Not a journey. Each test shows one capability of `helper/collab_stack` working against the real API:
holding a stream open, failing a route, recording what Node sent, the flag toggle, restarting the API,
and the replica-set transaction mode.
"""

from __future__ import annotations

import time

import pytest

from helper.collab_stack import chats
from helper.collab_stack.fake_backend import Drop, Frame, Reply, Sse, held_stream, run_error, stream_answer, text_frame
from helper.collab_stack.infra import wait_until
from helper.collab_stack.seeds import messages_of, session_doc
from helper.collab_stack.stack import CollabStack

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_off_for_module")]

STREAM = "/api/v1/conversations/stream"
BODY = {"query": "hold the line", "chatMode": "internal_search"}


def test_harness_holds_a_stream_open_until_the_gate_opens(stack: CollabStack, api, fake, roster) -> None:
    """The primitive for J-04 (concurrency) and J-05 (revoke mid-stream): the run is open while the test acts, then finishes."""
    gate = fake.gate("hold")
    fake.on("chat_stream", held_stream(gate, "A complete answer"))

    call = api.stream(STREAM, roster.owner, json_body=BODY)
    gate.wait_reached()
    chat_id = call.conversation_id
    assert chat_id and call.result is None
    assert session_doc(stack.db, chat_id)["status"] == "Inprogress"
    call.wait_for("TEXT_MESSAGE_CONTENT")  # the first half of the answer is already on the wire

    gate.open()
    call.finish()
    assert call.result is not None
    assert [m["content"] for m in messages_of(stack.db, chat_id) if m["messageType"] == "bot_response"] == ["A complete answer"]
    assert session_doc(stack.db, chat_id)["status"] == "Complete"


def test_harness_records_what_node_sent_and_as_whom(stack: CollabStack, api, fake, roster) -> None:
    """The payload and the forwarded identity are assertable (PH-05: the `runId` source, the history, the sender)."""
    chat_id = chats.create_chat(api, roster.owner, "first question")
    mark = fake.mark()
    resp = chats.send_message(api, roster.owner, chat_id, "second question", runId="3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c")
    assert resp.status_code == 200

    sent = fake.since(mark, "chat")[0]
    assert sent.user_id == roster.owner.user_id
    assert sent.body["query"] == "second question" and sent.body["conversationId"] == chat_id
    assert sent.body["runId"] == "3f2b8c1e-5d4a-4b7e-9a6c-1d2e3f4a5b6c"
    history = sent.body["previousConversations"]
    assert [turn["content"] for turn in history] == ["first question", "Fake answer"], history


def test_harness_can_fail_a_route_and_delay_a_reply(stack: CollabStack, api, fake, roster) -> None:
    with fake.failing("chat", status=503):
        failed = api.post("/api/v1/conversations/create", roster.owner, json_body={"query": "q", "chatMode": "quick"})
    assert failed.status_code >= 500
    fake.on("chat", Reply(stream_answer().steps[-1].data["result"], delay=0.5))
    started = time.monotonic()
    ok = api.post("/api/v1/conversations/create", roster.owner, json_body={"query": "q", "chatMode": "quick"})
    assert ok.status_code == 201 and time.monotonic() - started >= 0.5


def test_harness_scripts_an_upstream_error_and_a_dropped_connection(stack: CollabStack, api, fake, roster) -> None:
    fake.on("chat_stream", run_error("model overloaded"))
    errored = api.stream(STREAM, roster.owner, json_body=BODY).finish()
    assert errored.event_named("RUN_ERROR")
    wait_until(lambda: session_doc(stack.db, errored.conversation_id)["status"] == "Failed", 10, message="the failed run to be recorded")

    fake.on("chat_stream", Sse([Frame("RUN_STARTED", {"threadId": "t", "runId": "r"}), text_frame("partial"), Drop()]))
    dropped = api.stream(STREAM, roster.owner, json_body=BODY).finish()
    wait_until(lambda: session_doc(stack.db, dropped.conversation_id)["status"] != "Inprogress", 10, message="the run to leave Inprogress")
    assert session_doc(stack.db, dropped.conversation_id)["status"] in ("Failed", "Stopped")


def test_harness_client_disconnect_does_not_leave_the_run_in_progress(stack: CollabStack, api, fake, roster) -> None:
    """M-09: a closed tab ends the run cleanly. J-04 builds on this to release the lease."""
    gate = fake.gate("never-opened")
    fake.on("chat_stream", held_stream(gate))
    call = api.stream(STREAM, roster.owner, json_body=BODY)
    gate.wait_reached()
    call.abort()
    wait_until(lambda: session_doc(stack.db, call.conversation_id)["status"] != "Inprogress", 15, message="the aborted run to be closed out")
    gate.open()


def test_harness_flag_toggle_is_enforced_by_the_running_api(stack: CollabStack, api, roster, flags) -> None:
    probe = flags._ensure_probe()  # noqa: SLF001 - the probe chat is the lane's own fixture
    rename = lambda: api.patch(f"/api/v1/conversations/{probe}/title", roster.read_recipient, json_body={"title": "x"})  # noqa: E731
    assert rename().status_code == 404, "flag off: a read recipient's write is just 'not found'"
    with flags.value(True):
        assert rename().status_code == 403, "flag on: the same call is refused with the owner-only error"
    assert rename().status_code == 404


def test_harness_restart_keeps_the_data_and_the_port(stack: CollabStack, api, roster) -> None:
    chat_id = chats.create_chat(api, roster.owner, "survives a restart")
    port = stack.node.port  # type: ignore[union-attr]
    stack.node.restart()  # type: ignore[union-attr]
    assert stack.node.port == port  # type: ignore[union-attr]
    assert chats.get_chat(api, roster.owner, chat_id).status_code == 200


def test_delete_conversation_with_replica_set_transactions(stack: CollabStack, api, roster) -> None:
    node = stack.node
    assert node is not None
    chat_id = chats.create_chat(api, roster.owner, "delete me")
    node.extra_env["REPLICA_SET_AVAILABLE"] = "true"
    node.restart()
    try:
        assert api.delete(f"/api/v1/conversations/{chat_id}", roster.owner).status_code == 200
    finally:
        node.extra_env.pop("REPLICA_SET_AVAILABLE", None)
        node.restart()
