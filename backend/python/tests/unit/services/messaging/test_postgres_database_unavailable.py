"""A handler that fails only because the database cannot be reached is handed back without an attempt; any other failure is counted.

The queue is an in-memory row that keeps ``attempts`` as the SQL does: a claim adds one, a refunded nack gives it back.
"""

from __future__ import annotations

import json
import logging
import socket
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.core import envelope
from app.core.db.errors import (
    CommitOutcomeUnknown,
    ConnectionLost,
    DbTimeout,
    PoolTimeout,
    TooManyConnections,
)
from app.core.envelope import derive_key, sign
from app.core.org_context import OrgContext
from app.exceptions.graph_db_exceptions import (
    GraphQueryError,
    PermissionVerificationUnavailableError,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger("db-unavailable")
KEY = derive_key("db-unavailable-test-secret")
TOPIC = "record-events"
CTX = OrgContext("org-a", "u1", "user", "jwt", "req-1")


class _Row:
    def __init__(self, value: str) -> None:
        self.value = value
        self.attempts = 0
        self.state = "ready"
        self.nacks: list[tuple[float, bool]] = []
        self.admission: tuple[int | None, str | None] = (None, None)

    def claim(self) -> Delivery:
        self.attempts += 1
        return Delivery(TOPIC, 7, self.attempts, self.value, None, *self.admission)

    async def admit(self, group, d, ms, mac) -> bool:
        self.admission = (ms, mac)
        return True

    async def nack(self, group, d, delay, error="", *, refund=False, value=None, admission=None) -> bool:
        self.nacks.append((delay, refund))
        self.attempts -= 1 if refund else 0
        if value is not None:
            self.value = value
        if admission is not None:
            self.admission = admission
        return True

    async def ack(self, group, d) -> bool:
        self.state = "acked"
        return True

    async def dead(self, group, d, error="") -> bool:
        self.state = "dead"
        return True


@pytest.fixture(autouse=True)
def signing(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    monkeypatch.setenv("PG_QUEUE_HANDBACK_LIMIT", "5")

    async def keys() -> tuple[bytes, ...]:
        return (KEY,)

    envelope.configure(keys, KEY)
    yield
    envelope.configure(None)


def _consumer(row: _Row, failures: list[BaseException]) -> tuple[PgMessagingConsumer, list[int]]:
    """A consumer whose handler raises ``failures`` in turn, then succeeds; the list returned counts its runs."""
    runs: list[int] = []

    async def handler(*_a, **_k) -> None:
        runs.append(1)
        if len(runs) <= len(failures):
            raise failures[len(runs) - 1]

    consumer = object.__new__(PgMessagingConsumer)
    consumer.logger = log
    consumer.indexing = True
    consumer.config = SimpleNamespace(group_id="g")
    consumer.precision_filter = None
    consumer.queue = row
    consumer.inbox = None
    consumer.disposition_sink = None
    consumer.message_handler = object()
    consumer.stage_admission = None
    consumer._paused_until = 0.0
    consumer._handbacks = {}
    consumer._handback_log = {}
    consumer._opened = {}
    consumer._drive_indexing = AsyncMock(side_effect=handler)
    return consumer, runs


async def _deliver_until_settled(row: _Row, consumer: PgMessagingConsumer) -> None:
    for _ in range(50):
        if row.state != "ready":
            return
        await consumer._settle(row.claim())
    raise AssertionError("never settled")


def _row() -> _Row:
    payload = {"orgId": "org-a", "recordId": "rec-1", "virtualRecordId": "v1", "extension": "txt", "mimeType": "text/plain"}
    return _Row(json.dumps(sign(KEY, TOPIC, payload, CTX, event_type="newRecord")))


def _from(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


def _stamped(e: BaseException, cls: str) -> BaseException:
    e.log_error_class = cls  # type: ignore[attr-defined]
    return e


UNAVAILABLE = [
    lambda: ConnectionLost("connection terminated", retry_safe=False),
    lambda: TooManyConnections(),
    lambda: PoolTimeout("no free connection"),
    lambda: _from(GraphQueryError("Could not read file record"), ConnectionLost("closed", retry_safe=True)),
    lambda: _from(PermissionVerificationUnavailableError(), _from(RuntimeError("wrapped"), PoolTimeout("pool"))),
    lambda: _from(_stamped(GraphQueryError("Could not look up records"), "timeout"), PoolTimeout("pool")),
    # the resolver could not answer for now (EAI_AGAIN): an outage of the name service, not a wrong address
    lambda: _from(ConnectionLost("cannot connect", retry_safe=True), socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")),
]


@pytest.mark.parametrize("make", UNAVAILABLE)
async def test_a_database_down_for_more_than_the_attempt_limit_never_dead_letters_a_good_event(make) -> None:
    row = _row()
    consumer, runs = _consumer(row, [make() for _ in range(5)])  # more failures than MAX_DELIVERY_ATTEMPTS (3)

    await _deliver_until_settled(row, consumer)

    assert row.state == "acked"
    assert len(runs) == 6  # handled once the database came back
    assert [refund for _, refund in row.nacks] == [True] * 5
    assert [d for d, _ in row.nacks] == sorted(d for d, _ in row.nacks)  # backs off, never faster


BUGS = [
    lambda: ValueError("bad input"),
    lambda: TypeError("bad call"),
    lambda: _from(ValueError("decided by the handler"), ConnectionLost("closed", retry_safe=True)),
    lambda: DbTimeout("statement timeout"),  # the database answered: the statement was too slow
    lambda: CommitOutcomeUnknown("connection lost after COMMIT was sent"),  # may have applied
    lambda: _from(_stamped(GraphQueryError("Could not look up records"), "internal"), ConnectionLost("closed", retry_safe=True)),
    # a misconfigured address: wrong password, a database that does not exist, a host name that does not resolve. Waiting cannot fix
    # them, so they must not be taken for an outage (that would hold the message for the whole hand-back backstop)
    lambda: asyncpg.InvalidPasswordError("password authentication failed for user"),
    lambda: _from(GraphQueryError("Could not read file record"), asyncpg.InvalidCatalogNameError('database "nope" does not exist')),
    lambda: _from(ConnectionLost("cannot connect", retry_safe=True), socket.gaierror(socket.EAI_NONAME, "Name or service not known")),
    lambda: _from(GraphQueryError("Could not read file record"),
                  _from(ConnectionLost("cannot connect", retry_safe=True), socket.gaierror(socket.EAI_NONAME, "Name or service not known"))),
]


@pytest.mark.parametrize("make", BUGS)
async def test_any_other_failure_still_dead_letters_after_the_attempt_limit(make) -> None:
    row = _row()
    consumer, runs = _consumer(row, [make() for _ in range(10)])

    await _deliver_until_settled(row, consumer)

    assert row.state == "dead"
    assert len(runs) == 3
    assert not any(refund for _, refund in row.nacks)


async def test_a_database_that_never_comes_back_is_dead_lettered_by_the_backstop() -> None:
    row = _row()
    consumer, runs = _consumer(row, [ConnectionLost("refused", retry_safe=True) for _ in range(100)])

    await _deliver_until_settled(row, consumer)

    assert row.state == "dead"
    # five refunded hand-backs (PG_QUEUE_HANDBACK_LIMIT), then each one is charged until the three attempts are spent
    assert [refund for _, refund in row.nacks] == [True] * 5 + [False] * 3
    assert len(runs) == 8


async def test_hand_back_delays_grow_to_the_cap(monkeypatch) -> None:
    """The delays passed to the nack, without waiting for them: never shorter than the first retry, doubling per hand-back, then
    held at the 60 s cap."""
    from app.services.messaging.config import compute_retry_backoff_seconds
    from app.services.messaging.postgres import consumer as mod

    monkeypatch.setenv("PG_QUEUE_HANDBACK_LIMIT", "20")
    row = _row()
    consumer, _ = _consumer(row, [ConnectionLost("terminated", retry_safe=False) for _ in range(10)])

    await _deliver_until_settled(row, consumer)

    first, base = compute_retry_backoff_seconds(1), mod._handback_base_s()
    assert [d for d, _ in row.nacks] == [max(first, min(base * 2 ** n, 60.0)) for n in range(10)]
    assert row.nacks[-1][0] == 60.0


class _AdmitFails(_Row):
    """A row whose admission write fails with ``errors`` in turn, then works."""

    def __init__(self, value: str, errors: list[BaseException]) -> None:
        super().__init__(value)
        self.errors = errors

    async def admit(self, group, d, ms, mac) -> bool:
        if self.errors:
            raise self.errors.pop(0)
        return await super().admit(group, d, ms, mac)


async def test_an_admission_the_database_could_not_record_is_handed_back_not_charged() -> None:
    row = _AdmitFails(_row().value, [ConnectionLost("terminated", retry_safe=False) for _ in range(5)])
    consumer, runs = _consumer(row, [])

    await _deliver_until_settled(row, consumer)

    assert row.state == "acked" and len(runs) == 1
    assert [refund for _, refund in row.nacks] == [True] * 5


async def test_an_admission_that_failed_for_another_reason_still_dead_letters() -> None:
    row = _AdmitFails(_row().value, [ValueError("bad stamp") for _ in range(10)])
    consumer, runs = _consumer(row, [])

    await _deliver_until_settled(row, consumer)

    assert row.state == "dead" and runs == []
    assert not any(refund for _, refund in row.nacks)


class _NackFails(_Row):
    """A row whose refunding nack fails ``failures`` times (the outage outlasts the handler), then applies, or reports the claim lost."""

    def __init__(self, value: str, failures: int, *, lost: bool = False) -> None:
        super().__init__(value)
        self.failures, self.lost, self.tries = failures, lost, 0

    async def nack(self, group, d, delay, error="", *, refund=False, value=None, admission=None) -> bool:
        self.tries += 1
        if self.failures:
            self.failures -= 1
            raise ConnectionLost("terminated", retry_safe=False)
        if self.lost:
            return False
        return await super().nack(group, d, delay, error, refund=refund, value=value, admission=admission)


async def test_a_refund_survives_a_nack_the_outage_also_failed(monkeypatch) -> None:
    monkeypatch.setattr("app.services.messaging.postgres.consumer._NACK_RETRY_S", (0.0, 0.0))
    row = _NackFails(_row().value, failures=3)
    consumer, runs = _consumer(row, [ConnectionLost("terminated", retry_safe=False)])
    consumer.running = True

    await consumer._settle(row.claim())

    assert row.tries == 4 and row.nacks == [(row.nacks[0][0], True)] and row.attempts == 0     # applied once, refunded


async def test_a_refund_nack_stops_when_another_consumer_holds_the_row(monkeypatch) -> None:
    monkeypatch.setattr("app.services.messaging.postgres.consumer._NACK_RETRY_S", (0.0, 0.0))
    row = _NackFails(_row().value, failures=2, lost=True)
    consumer, _ = _consumer(row, [ConnectionLost("terminated", retry_safe=False)])
    consumer.running = True

    await consumer._settle(row.claim())

    assert row.tries == 3 and row.nacks == []
