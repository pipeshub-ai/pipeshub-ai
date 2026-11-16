"""A signed message that waits through repeated precision hand-backs still reaches its handler.

Signature verification is real and enforced; the queue is an in-memory stand-in that keeps the stored value, and the
clock is a variable the test advances.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import envelope
from app.core.envelope import derive_key, refresh_envelope, sign
from app.core.org_context import OrgContext
from app.services.messaging.config import StreamMessage
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery

log = logging.getLogger("signed-handback")
KEY = derive_key("handback-test-secret")
TOPIC = "record-events"
CTX = OrgContext("org-a", "u1", "user", "jwt", "req-1")
LIFETIME = 900


class _Queue:
    """The row of one delivery, as the SQL keeps it: one stored value, the admission columns, and the guards of ``admit`` and ``nack``."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.admission: tuple[int | None, str | None] = (None, None)
        self.nacks: list[dict] = []
        self.admits: list[tuple[int, str]] = []
        self.claimed_attempts = 1
        self.fail_admit = False
        self.fail_nack = False
        self.ack = AsyncMock()

    def claim(self, attempts: int | None = None) -> Delivery:
        self.claimed_attempts = attempts or self.claimed_attempts
        return Delivery(TOPIC, 7, self.claimed_attempts, self.value, None, *self.admission)

    async def admit(self, group, d, ms, mac):  # noqa: ANN202
        if self.fail_admit:
            raise RuntimeError("database unavailable")
        if d.attempts != self.claimed_attempts:
            return False
        self.admission = (ms, mac)
        self.admits.append((ms, mac))
        return True

    async def nack(self, group, d, delay, error="", *, refund=False, value=None, admission=None):  # noqa: ANN202
        if self.fail_nack:
            raise RuntimeError("database unavailable")
        self.nacks.append({"delay": delay, "refund": refund, "reissued": value is not None, "admission": admission, "error": error})
        if value is not None:
            self.value = value
        if admission is not None:
            self.admission = admission
        return True


class _Filter:
    def __init__(self, hand_backs: int, on_check=None) -> None:
        self.left = hand_backs
        self.on_check = on_check

    def hand_back(self, messages):  # noqa: ANN202
        if self.on_check:
            self.on_check()
        if self.left <= 0:
            return {}
        self.left -= 1
        return {0: 60.0}


@pytest.fixture
def clock(monkeypatch):  # noqa: ANN201
    now = [1_800_000_000.0]
    monkeypatch.setattr(envelope, "time", SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")  # the age check rejects only in enforce mode: without it these tests would pass for nothing
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.delenv("ENVELOPE_MAX_AGE_SECONDS", raising=False)
    monkeypatch.delenv("ENVELOPE_ADMITTED_MAX_AGE_SECONDS", raising=False)

    async def keys():  # noqa: ANN202
        return (KEY,)

    envelope.configure(keys, None, KEY)
    yield now
    envelope.configure(None, None, None)


def _signed(issued_at: float, record: str = "rec-1") -> str:
    payload = {"orgId": "org-a", "recordId": record, "virtualRecordId": "v1", "extension": "txt", "mimeType": "text/plain"}
    return json.dumps(sign(KEY, TOPIC, payload, CTX, event_type="newRecord", issued_at=int(issued_at)))


def _consumer(queue: _Queue, hand_backs: int = 0, group: str = "g", on_check=None) -> PgMessagingConsumer:
    consumer = object.__new__(PgMessagingConsumer)
    consumer.logger = log
    consumer.indexing = True
    consumer.config = SimpleNamespace(group_id=group)
    consumer.precision_filter = _Filter(hand_backs, on_check)
    consumer.queue = queue
    consumer.message_handler = object()
    consumer.stage_admission = None
    consumer._paused_until = 0.0
    consumer._handbacks = {}
    consumer._handback_log = {}
    consumer._opened = {}
    consumer._drive_indexing = AsyncMock()
    consumer._abandon = AsyncMock()
    return consumer


def _claim(queue: _Queue, attempts: int | None = None) -> Delivery:
    return queue.claim(attempts)


async def _stored_admission(message_json: str, *, group: str = "g", message_id: int = 7) -> tuple[int, str]:
    stamp = await envelope.new_admission(TOPIC, group, message_id, json.loads(message_json))
    assert stamp is not None
    return stamp


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


async def test_a_claim_delayed_past_the_lifetime_by_an_outage_still_runs(clock) -> None:
    queue = _Queue(_signed(clock[0]))
    consumer = _consumer(queue, hand_backs=1)
    await consumer._settle(_claim(queue))
    clock[0] += 3 * LIFETIME  # consumers were down: the row is claimed far later than the wait asked for

    consumer._abandon.reset_mock()
    await _consumer(queue)._settle(_claim(queue))  # another process, no memory of the first claim

    assert json.loads(queue.value)["ctx"]["issuedAt"] < clock[0] - LIFETIME  # the signature really is stale now
    consumer._abandon.assert_not_awaited()
    queue.ack.assert_awaited_once()


async def test_a_message_never_admitted_and_stale_at_its_first_claim_is_refused(clock) -> None:
    queue = _Queue(_signed(clock[0] - LIFETIME - 5))
    consumer = _consumer(queue)
    await consumer._settle(_claim(queue))
    consumer._drive_indexing.assert_not_awaited()
    consumer._abandon.assert_awaited_once()
    assert "stale" in consumer._abandon.await_args.args[2]
    assert queue.admits == [] and queue.admission == (None, None)


async def test_a_handler_that_outlives_the_envelope_and_requeues_is_signed_again_and_runs_next(clock) -> None:
    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt

    queue = _Queue(_signed(clock[0] - LIFETIME + 5))  # admitted five seconds before it would expire

    async def slow_handler(*_a, **_k):  # noqa: ANN202
        clock[0] += 10
        raise RequeueWithoutAttempt("the index was reset")

    consumer = _consumer(queue)
    consumer._drive_indexing = AsyncMock(side_effect=slow_handler)
    await consumer._settle(_claim(queue))
    assert queue.nacks[-1]["refund"] and queue.nacks[-1]["reissued"]
    assert json.loads(queue.value)["ctx"]["issuedAt"] == int(clock[0])

    clock[0] += 3 * LIFETIME
    follow = _consumer(queue)
    await follow._settle(_claim(queue))
    follow._abandon.assert_not_awaited()
    follow._drive_indexing.assert_awaited_once()


async def test_a_handler_that_dies_leaves_a_row_that_the_next_claim_still_opens(clock) -> None:
    queue = _Queue(_signed(clock[0] - LIFETIME + 5))

    async def dies(*_a, **_k):  # noqa: ANN202
        clock[0] += 10
        raise asyncio.CancelledError  # a deploy stops the process mid-handler; no nack is ever written

    consumer = _consumer(queue)
    consumer._drive_indexing = AsyncMock(side_effect=dies)
    with pytest.raises(asyncio.CancelledError):
        await consumer._settle(_claim(queue))
    assert queue.nacks == [] and len(queue.admits) == 1

    clock[0] += 1800  # the lease lapses; the row is handed out again, untouched
    redelivered = _consumer(queue)
    await redelivered._settle(_claim(queue, attempts=2))
    redelivered._abandon.assert_not_awaited()
    redelivered._drive_indexing.assert_awaited_once()


async def test_a_nack_that_fails_leaves_a_row_that_the_next_claim_still_opens(clock) -> None:
    queue = _Queue(_signed(clock[0] - LIFETIME + 5))
    queue.fail_nack = True

    async def fails(*_a, **_k):  # noqa: ANN202
        clock[0] += 10
        raise ValueError("transient")

    consumer = _consumer(queue)
    consumer._drive_indexing = AsyncMock(side_effect=fails)
    with pytest.raises(RuntimeError):
        await consumer._settle(_claim(queue))

    clock[0] += 1800
    queue.fail_nack = False
    redelivered = _consumer(queue)
    await redelivered._settle(_claim(queue, attempts=2))
    redelivered._abandon.assert_not_awaited()
    redelivered._drive_indexing.assert_awaited_once()


async def test_the_precision_check_and_the_handler_open_the_same_delivery_across_the_lifetime_boundary(clock) -> None:
    queue = _Queue(_signed(clock[0] - LIFETIME + 1))  # fresh by one second when it is claimed

    consumer = _consumer(queue, hand_backs=0, on_check=lambda: clock.__setitem__(0, clock[0] + 3))  # the check takes three seconds
    await consumer._settle(_claim(queue))
    consumer._abandon.assert_not_awaited()
    consumer._drive_indexing.assert_awaited_once()


async def test_a_failed_admission_write_stops_the_handler_and_the_delivery_is_retried(clock) -> None:
    queue = _Queue(_signed(clock[0]))
    queue.fail_admit = True
    consumer = _consumer(queue)
    await consumer._settle(_claim(queue))
    consumer._drive_indexing.assert_not_awaited()
    consumer._abandon.assert_not_awaited()
    assert [(n["refund"], "admission" in n["error"]) for n in queue.nacks] == [(False, True)]


async def test_a_retry_nack_renews_the_admission_and_leaves_the_message_alone(clock) -> None:
    queue = _Queue(_signed(clock[0]))
    consumer = _consumer(queue)
    consumer._drive_indexing = AsyncMock(side_effect=ValueError("transient"))
    before = queue.value
    clock[0] += 100
    await consumer._settle(_claim(queue))
    assert queue.nacks[-1]["admission"] is not None and not queue.nacks[-1]["reissued"]
    assert queue.admission[0] == int(clock[0] * 1000) and queue.value == before


async def test_a_replay_held_by_an_admission_is_never_signed_fresh(clock) -> None:
    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt

    old = _signed(clock[0] - 3 * 86400)  # issued three days ago
    queue = _Queue(old)
    queue.admission = await _stored_admission(old)  # admitted now, so valid for the cap
    clock[0] += 3600
    consumer = _consumer(queue)
    consumer._drive_indexing = AsyncMock(side_effect=RequeueWithoutAttempt("waiting"))
    await consumer._settle(_claim(queue))

    assert queue.nacks[-1]["refund"] and not queue.nacks[-1]["reissued"]
    assert queue.value == old  # byte for byte: the admission never turns into a fresh signature
    assert queue.admission[0] == int(clock[0] * 1000)  # but the stamp moved, so a message that keeps moving is not cut off


async def test_a_resign_by_another_group_leaves_this_groups_admission_valid(clock) -> None:
    original = _signed(clock[0])
    queue_b = _Queue(original)
    consumer_b = _consumer(queue_b, group="h")
    consumer_b._drive_indexing = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):  # B opens the message and its process dies
        await consumer_b._settle(queue_b.claim())
    assert len(queue_b.admits) == 1

    clock[0] += 100
    queue_a = _Queue(original)  # the same stored message, as group A sees it
    await _consumer(queue_a, hand_backs=1)._settle(_claim(queue_a))  # A hands back and signs the message again
    assert queue_a.value != original
    queue_b.value = queue_a.value  # one stored value for every group of the topic

    clock[0] += 3 * LIFETIME
    later = _consumer(queue_b, group="h")
    await later._settle(queue_b.claim(attempts=2))
    later._abandon.assert_not_awaited()
    later._drive_indexing.assert_awaited_once()


async def test_a_consumer_that_predates_admissions_still_finds_a_fresh_signature_after_a_hand_back(clock) -> None:
    queue = _Queue(_signed(clock[0]))
    await _consumer(queue, hand_backs=1)._settle(_claim(queue))
    clock[0] += 500
    opened = await envelope.open_stream_message(TOPIC, json.loads(queue.value), StreamMessage)  # an earlier version: no admission, age judged
    assert opened.payload["recordId"] == "rec-1"


@pytest.mark.parametrize(
    "tamper",
    [
        "bad_mac", "other_group", "other_message", "other_topic", "swapped_value", "edited_stamp", "stamp_past_the_cap",
        "stamp_in_the_future", "other_key", "no_mac", "no_stamp",
    ],
)
async def test_a_forged_admission_is_judged_by_age_and_refused(clock, tamper) -> None:
    old_issued = clock[0] - 5 * LIFETIME
    old, other_old = _signed(old_issued), _signed(old_issued, record="rec-2")
    ms, mac = await _stored_admission(old)
    other_key = derive_key("another-secret")
    queue = _Queue(old)
    forged = {
        "bad_mac": (ms, mac[:-3] + ("AAA" if not mac.endswith("AAA") else "BBB")),
        "other_group": await _stored_admission(old, group="not-g"),
        "other_message": await _stored_admission(old, message_id=8),
        "other_topic": (ms, envelope.admission_mac(KEY, "another-topic", "g", 7, json.loads(old), ms)),
        "swapped_value": await _stored_admission(other_old),  # a good admission of another old message, the row now holds this one
        "edited_stamp": (ms + 5000, mac),
        "stamp_past_the_cap": (ms - 90_000_000, envelope.admission_mac(KEY, TOPIC, "g", 7, json.loads(old), ms - 90_000_000)),
        "stamp_in_the_future": (ms + 3_600_000, envelope.admission_mac(KEY, TOPIC, "g", 7, json.loads(old), ms + 3_600_000)),
        "other_key": (ms, envelope.admission_mac(other_key, TOPIC, "g", 7, json.loads(old), ms)),
        "no_mac": (ms, None),
        "no_stamp": (None, mac),
    }[tamper]
    queue.admission = forged
    consumer = _consumer(queue)
    await consumer._settle(_claim(queue))
    consumer._drive_indexing.assert_not_awaited()
    consumer._abandon.assert_awaited_once()
    assert "stale" in consumer._abandon.await_args.args[2]

    control = _Queue(old)  # the same message with its genuine admission is let through: the refusal above is the admission's, not something else
    control.admission = (ms, mac)
    ok = _consumer(control)
    await ok._settle(_claim(control))
    ok._drive_indexing.assert_awaited_once()


async def test_an_admission_made_with_the_previous_key_holds_until_that_key_is_dropped(clock) -> None:
    old = _signed(clock[0] - 5 * LIFETIME)
    new_key = derive_key("rotated-secret")
    ms, mac = await _stored_admission(old)  # made with KEY

    async def both():  # noqa: ANN202
        return (new_key, KEY)

    async def only_new():  # noqa: ANN202
        return (new_key,)

    envelope.configure(both, None, new_key)
    assert envelope.admission_valid(await both(), TOPIC, "g", 7, json.loads(old), ms, mac)
    envelope.configure(only_new, None, new_key)
    assert not envelope.admission_valid(await only_new(), TOPIC, "g", 7, json.loads(old), ms, mac)


@pytest.mark.parametrize("damage", ["payload", "org", "future"])
async def test_an_admitted_message_still_has_to_verify_in_every_other_way(clock, damage) -> None:
    message = json.loads(_signed(clock[0] - 5 * LIFETIME))
    if damage == "payload":
        message["payload"] = message["payload"].replace("rec-1", "rec-9")
    elif damage == "org":
        message = json.loads(json.dumps(sign(KEY, TOPIC, {"orgId": "org-b"}, CTX, event_type="newRecord", issued_at=int(clock[0]))))
        message["payload"] = json.dumps({"orgId": "org-b"})
    else:
        message = json.loads(_signed(clock[0] + 3600))
    with pytest.raises(envelope.EnvelopeRejected):
        await envelope.open_stream_message(TOPIC, message, StreamMessage, admitted=True)


def test_the_admission_cap_is_configurable_and_validated(monkeypatch) -> None:
    monkeypatch.delenv("ENVELOPE_ADMITTED_MAX_AGE_SECONDS", raising=False)
    assert envelope.admitted_max_age_seconds() == 86400
    monkeypatch.setenv("ENVELOPE_ADMITTED_MAX_AGE_SECONDS", "3600")
    assert envelope.admitted_max_age_seconds() == 3600
    for bad in ("0", "-1", "soon", "inf"):
        monkeypatch.setenv("ENVELOPE_ADMITTED_MAX_AGE_SECONDS", bad)
        with pytest.raises(ValueError):
            envelope.admitted_max_age_seconds()


async def test_the_wait_stays_within_a_short_configured_lifetime(clock, monkeypatch) -> None:
    monkeypatch.setenv("ENVELOPE_MAX_AGE_SECONDS", "300")
    queue = _Queue(_signed(clock[0]))
    consumer = _consumer(queue, hand_backs=2)
    for _ in range(2):  # the second hand-back waits ten times the first: 600 s against a 300 s lifetime
        await consumer._settle(_claim(queue))
        clock[0] += queue.nacks[-1]["delay"]

    assert all(n["delay"] <= 200 for n in queue.nacks)
    await consumer._settle(_claim(queue))

    consumer._abandon.assert_not_awaited()
    consumer._drive_indexing.assert_awaited_once()


async def test_a_message_requeued_without_an_attempt_by_the_handler_is_renewed_too(clock) -> None:
    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt

    queue = _Queue(_signed(clock[0]))
    consumer = _consumer(queue, hand_backs=0)
    consumer._process = AsyncMock(side_effect=RequeueWithoutAttempt("the index was reset"))
    clock[0] += 600
    await consumer._settle(_claim(queue))
    # nothing opened the message in this process (the handler is replaced), so the signature is renewed only because it still verifies strictly
    assert [n["refund"] and n["reissued"] for n in queue.nacks] == [True]
    assert json.loads(queue.value)["ctx"]["issuedAt"] == int(clock[0])


async def test_a_long_handler_requeue_stays_within_a_short_configured_lifetime(clock, monkeypatch) -> None:
    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt

    monkeypatch.setenv("ENVELOPE_MAX_AGE_SECONDS", "300")
    queue = _Queue(_signed(clock[0]))
    consumer = _consumer(queue, hand_backs=0)
    consumer._process = AsyncMock(side_effect=RequeueWithoutAttempt("the stage is paused", delay_s=600))
    await consumer._settle(_claim(queue))
    clock[0] += queue.nacks[-1]["delay"]

    assert queue.nacks[-1]["delay"] <= 200
    consumer._process = AsyncMock(return_value=(True, False, None, None))
    await consumer._settle(_claim(queue))
    consumer._abandon.assert_not_awaited()


async def test_a_paused_consumer_resumes_before_the_requeued_message_outlives_a_short_lifetime(clock, monkeypatch) -> None:
    import time

    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt

    monkeypatch.setenv("ENVELOPE_MAX_AGE_SECONDS", "300")
    queue = _Queue(_signed(clock[0]))
    consumer = _consumer(queue, hand_backs=0)
    consumer.stage_admission = object()
    consumer._process = AsyncMock(side_effect=RequeueWithoutAttempt("the stage is paused", delay_s=600))
    before = time.monotonic()
    await consumer._settle(_claim(queue))

    assert consumer._paused_until - before <= 200 + 1


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
        return self.nack_result if ("queue.nack" in sql or "queue.admit" in sql) else "UPDATE 1"


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

    async def test_the_admission_travels_in_the_nack_statement(self) -> None:
        tx = _Tx("UPDATE 1")
        d = SimpleNamespace(topic=TOPIC, message_id=7, attempts=1)
        await self._queue(tx).nack("g", d, 5.0, admission=(1234, "mac"))
        assert len(tx.statements) == 1 and tx.statements[0][1][-2:] == (1234, "mac")
        tx2 = _Tx("UPDATE 1")
        await self._queue(tx2).nack("g", d, 5.0)
        assert tx2.statements[0][1][-2:] == (None, None)

    async def test_admit_is_one_guarded_statement_and_reports_a_lost_claim(self) -> None:
        d = SimpleNamespace(topic=TOPIC, message_id=7, attempts=3)
        tx = _Tx("UPDATE 1")
        assert await self._queue(tx).admit("g", d, 99, "mac")
        assert len(tx.statements) == 1 and "queue.admit" in tx.statements[0][0]
        assert "attempts = $4 AND status = 'claimed'" in tx.statements[0][0]
        assert tx.statements[0][1] == ("g", TOPIC, 7, 3, 99, "mac")
        assert not await self._queue(_Tx("UPDATE 0")).admit("g", d, 99, "mac")
