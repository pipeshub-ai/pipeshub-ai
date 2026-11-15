"""A signed message that waits through repeated precision hand-backs still reaches its handler.

Signature verification is real and enforced; the queue is an in-memory stand-in that keeps the stored value, and the
clock is a variable the test advances.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import envelope
from app.core.envelope import derive_key, refresh_envelope, sign
from app.core.org_context import OrgContext
from app.services.messaging.postgres.consumer import PgMessagingConsumer

log = logging.getLogger("signed-handback")
KEY = derive_key("handback-test-secret")
TOPIC = "record-events"
CTX = OrgContext("org-a", "u1", "user", "jwt", "req-1")
LIFETIME = 900


class _Queue:
    """Keeps the stored value as the real queue does: one value per message, replaced by a nack that carries one."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.nacks: list[dict] = []
        self.ack = AsyncMock()

    async def nack(self, group, d, delay, error="", *, refund=False, value=None):  # noqa: ANN202
        self.nacks.append({"delay": delay, "refund": refund, "reissued": value is not None})
        if value is not None:
            self.value = value
        return True


class _Filter:
    def __init__(self, hand_backs: int) -> None:
        self.left = hand_backs

    def hand_back(self, messages):  # noqa: ANN202
        if self.left <= 0:
            return {}
        self.left -= 1
        return {0: 60.0}


@pytest.fixture
def clock(monkeypatch):  # noqa: ANN201
    now = [1_800_000_000.0]
    monkeypatch.setattr(envelope, "time", SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.delenv("ENVELOPE_MAX_AGE_SECONDS", raising=False)

    async def keys():  # noqa: ANN202
        return (KEY,)

    envelope.configure(keys, None, KEY)
    yield now
    envelope.configure(None, None, None)


def _signed(issued_at: float) -> str:
    payload = {"orgId": "org-a", "recordId": "rec-1", "virtualRecordId": "v1", "extension": "txt", "mimeType": "text/plain"}
    return json.dumps(sign(KEY, TOPIC, payload, CTX, event_type="newRecord", issued_at=int(issued_at)))


def _consumer(queue: _Queue, hand_backs: int) -> PgMessagingConsumer:
    consumer = object.__new__(PgMessagingConsumer)
    consumer.logger = log
    consumer.indexing = True
    consumer.config = SimpleNamespace(group_id="g")
    consumer.precision_filter = _Filter(hand_backs)
    consumer.queue = queue
    consumer.message_handler = object()
    consumer.stage_admission = None
    consumer._paused_until = 0.0
    consumer._handbacks = {}
    consumer._handback_log = {}
    consumer._drive_indexing = AsyncMock()
    consumer._abandon = AsyncMock()
    return consumer


def _claim(queue: _Queue, attempts: int = 1) -> SimpleNamespace:
    return SimpleNamespace(topic=TOPIC, message_id=7, attempts=attempts, value=queue.value)


async def test_a_message_that_waits_past_the_lifetime_through_hand_backs_still_runs(clock) -> None:
    queue = _Queue(_signed(clock[0]))
    consumer = _consumer(queue, hand_backs=3)
    for _ in range(3):  # three waits of 10 minutes: 30 minutes against a 15 minute lifetime
        await consumer._settle(_claim(queue))
        clock[0] += 600
    assert clock[0] - 1_800_000_000.0 > LIFETIME + 600

    await consumer._settle(_claim(queue))

    assert [n["refund"] and n["reissued"] for n in queue.nacks] == [True, True, True]
    consumer._drive_indexing.assert_awaited_once()
    consumer._abandon.assert_not_awaited()
    queue.ack.assert_awaited_once()


async def test_the_renewed_message_is_signed_over_the_same_payload(clock) -> None:
    queue = _Queue(_signed(clock[0]))
    before = json.loads(queue.value)
    clock[0] += 600
    await _consumer(queue, hand_backs=1)._settle(_claim(queue))
    after = json.loads(queue.value)
    assert after["payload"] == before["payload"] and after["eventType"] == before["eventType"]
    assert after["ctx"]["eventId"] == before["ctx"]["eventId"] and after["ctx"]["sig"] != before["ctx"]["sig"]
    assert after["ctx"]["issuedAt"] == int(clock[0])


async def test_a_message_that_is_already_stale_is_refused_and_not_renewed(clock) -> None:
    queue = _Queue(_signed(clock[0] - LIFETIME - 5))
    consumer = _consumer(queue, hand_backs=1)
    await consumer._settle(_claim(queue))
    assert queue.nacks == [] and json.loads(queue.value)["ctx"]["issuedAt"] == int(clock[0] - LIFETIME - 5)
    consumer._drive_indexing.assert_not_awaited()
    consumer._abandon.assert_awaited_once()
    assert "stale" in consumer._abandon.await_args.args[2]


async def test_a_tampered_message_is_not_renewed_and_is_refused(clock) -> None:
    stored = json.loads(_signed(clock[0]))
    stored["payload"] = stored["payload"].replace("rec-1", "rec-2")
    queue = _Queue(json.dumps(stored))
    consumer = _consumer(queue, hand_backs=1)
    await consumer._settle(_claim(queue))
    consumer._drive_indexing.assert_not_awaited()
    consumer._abandon.assert_awaited_once()
    assert "bad_signature" in consumer._abandon.await_args.args[2]
    assert json.loads(queue.value)["payload"] == stored["payload"]


async def test_a_hand_back_that_is_not_signed_for_this_key_cannot_be_renewed(clock) -> None:
    other = json.dumps(sign(derive_key("someone-else"), TOPIC, {"orgId": "org-a"}, CTX, event_type="newRecord", issued_at=int(clock[0])))
    with pytest.raises(envelope.EnvelopeRejected):
        await refresh_envelope(TOPIC, json.loads(other))


async def test_nothing_is_renewed_without_a_key_to_verify_with(clock) -> None:
    envelope.configure(None, None, None)
    assert await refresh_envelope(TOPIC, json.loads(_signed(clock[0]))) is None


class _Tx:
    def __init__(self, nack_result: str) -> None:
        self.statements: list[tuple] = []
        self.nack_result = nack_result

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *_a):  # noqa: ANN204
        return False

    async def execute(self, sql, *args):  # noqa: ANN202
        self.statements.append((sql, args))
        return self.nack_result if "queue.nack" in sql else "UPDATE 1"


class TestQueueNackValue:
    @staticmethod
    def _queue(tx):  # noqa: ANN205
        from app.services.messaging.postgres.queue import PgQueue

        queue = object.__new__(PgQueue)

        async def write():  # noqa: ANN202
            return tx

        queue._write = write
        return queue

    async def test_the_stored_message_is_replaced_in_the_same_transaction_as_the_nack(self) -> None:
        tx = _Tx("UPDATE 1")
        d = SimpleNamespace(topic=TOPIC, message_id=7, attempts=1)
        assert await self._queue(tx).nack("g", d, 5.0, "x", refund=True, value="NEW")
        assert [("queue.nack" in s, "queue.reissue" in s) for s, _ in tx.statements] == [(True, False), (False, True)]
        assert tx.statements[1][1] == (7, "NEW")

    async def test_a_nack_that_lost_its_claim_leaves_the_stored_message_alone(self) -> None:
        tx = _Tx("UPDATE 0")
        d = SimpleNamespace(topic=TOPIC, message_id=7, attempts=1)
        assert not await self._queue(tx).nack("g", d, 5.0, "x", refund=True, value="NEW")
        assert len(tx.statements) == 1

    async def test_without_a_value_only_the_delivery_changes(self) -> None:
        tx = _Tx("UPDATE 1")
        await self._queue(tx).nack("g", SimpleNamespace(topic=TOPIC, message_id=7, attempts=1), 5.0)
        assert len(tx.statements) == 1
