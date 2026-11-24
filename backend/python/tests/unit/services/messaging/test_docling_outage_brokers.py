"""A PDF that meets a Docling outage waits for Docling, bounded, on every broker.

Driven through each broker's real failure handling: the Kafka and Redis Streams indexing consumers' per-message wrappers (a
real ``RetryManager`` on fakeredis, the real ``_requeue_message`` stamping ``_retry_not_before``) and the PostgreSQL
consumer's ``_settle``. The handler stands for ``EventProcessor`` on a PDF the PdfPlumber fallback could not read: it calls
the real ``DoclingClient.parse_pdf`` over a transport that answers as Docling's port does (refused, or a connection dropped
mid-answer), so the consumer gets exactly the error production raises. Once Docling is back the record is indexed.

Time is a fake clock: Redis and Kafka read it through their module's ``time.time``; PostgreSQL gives its delay to
``queue.nack``, which the driver turns into the next claim. Each delivery costs ``ATTEMPT_S`` of it. Nothing sleeps.
"""

from __future__ import annotations

import json
import logging
import time as real_time
import types
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import fakeredis.aioredis
import httpx
import pytest

import app.services.messaging.kafka.consumer.indexing_consumer as kafka_mod
import app.services.messaging.redis_streams.indexing_consumer as redis_mod
from app.services.docling.client import DoclingClient, DoclingParseError
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
)
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.postgres.queue import Delivery
from app.services.messaging.retry_manager import RetryManager
from tests.support.broker_consumers import kafka_consumer, kafka_record, redis_consumer

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

LOGGER = logging.getLogger("docling-outage-brokers")
START = 1_800_000_000.0
ATTEMPT_S = 5.0  # one delivery's own time before it fails (download, inspection, PdfPlumber)
BROKERS = ["kafka", "redis", "postgres"]

REFUSED, DROPPED, UP, VERDICT = "refused", "dropped", "up", "verdict"


class Clock:
    def __init__(self) -> None:
        self.now = START

    def time(self) -> float:
        return self.now


def _time_module(clock: Clock) -> types.SimpleNamespace:
    attrs = {n: getattr(real_time, n) for n in dir(real_time) if not n.startswith("_")}
    return types.SimpleNamespace(**{**attrs, "time": clock.time})


class Docling:
    """What Docling's port does for each delivery, in order; the last entry repeats."""

    def __init__(self, script: list[str]) -> None:
        self.script = script
        self.state = script[0]
        self.client = DoclingClient(service_url="http://docling.test", retry_delay=0.0)
        self.client.health_check = AsyncMock(return_value=False)  # type: ignore[method-assign]  # the breaker probe resolves no host
        self.client._make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(self._answer))  # type: ignore[method-assign]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        if self.state == REFUSED:
            raise httpx.ConnectError("connection refused", request=request)
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)

    def for_delivery(self, n: int) -> str:
        self.state = self.script[min(n, len(self.script) - 1)]
        return self.state


class Scenario:
    def __init__(self, clock: Clock, script: list[str]) -> None:
        self.clock = clock
        self.docling = Docling(script)
        self.deliveries: list[float] = []
        self.indexed_at: float | None = None

    async def handler(self, _message: Any) -> AsyncGenerator[PipelineEvent, None]:  # noqa: ANN401
        state = self.docling.for_delivery(len(self.deliveries))
        self.deliveries.append(self.clock.now - START)
        self.clock.now += ATTEMPT_S
        if state == VERDICT:
            raise DoclingParseError("Docling failed with status 422", status_code=422, reason="parse_failed")
        if state != UP:
            await self.docling.client.parse_pdf("report.pdf", b"%PDF-1.7 small")
            raise AssertionError("an outage must raise")  # pragma: no cover
        self.indexed_at = self.clock.now - START
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))


def _outage_for(seconds: float) -> list[str]:
    """Refused for the deliveries that start before *seconds*, given a delivery every 65 s while it waits."""
    return [REFUSED] * (int(seconds // 65) + 1) + [UP]


def _pdf_body(payload: dict[str, Any] | None = None) -> str:
    return json.dumps({"eventType": "newRecord", "payload": payload or {
        "recordId": "r1", "extension": "pdf", "mimeType": "application/pdf",
    }})


def _wire_requeue(consumer: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Keep the real ``_requeue_message`` (it stamps ``_retry_not_before`` from the module clock); capture what it sends."""
    sent: list[dict[str, Any]] = []

    async def on_main(coro: Any) -> Any:  # noqa: ANN401
        return await coro

    async def send(_message: Any, _topic: str, _event_type: str, payload: dict[str, Any]) -> None:  # noqa: ANN401
        sent.append(payload)

    consumer.__dict__.pop("_requeue_message", None)  # the harness mocks it
    consumer.producer = object()
    consumer._run_on_main_loop = on_main  # type: ignore[method-assign]
    consumer._send_in_org = send  # type: ignore[method-assign]
    return sent


def _counting(manager: RetryManager) -> dict[str, int]:
    counts = {"attempts": 0}
    real = manager.increment_and_check

    async def increment_and_check(message_id: str, max_attempts: int) -> tuple[int, bool]:
        counts["attempts"] += 1
        return await real(message_id, max_attempts)

    manager.increment_and_check = increment_and_check  # type: ignore[method-assign]
    return counts


async def _drive_stream_broker(kind: str, script: list[str], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    clock = Clock()
    scenario = Scenario(clock, script)
    abandon = AsyncMock()
    if kind == "kafka":
        monkeypatch.setattr(kafka_mod, "time", _time_module(clock))
        consumer, _ = kafka_consumer()
        consumer.running = True
        abandon.side_effect = consumer._IndexingKafkaConsumer__abandon_message  # type: ignore[attr-defined]  # a spy on the real one
        consumer._IndexingKafkaConsumer__abandon_message = abandon  # type: ignore[attr-defined]
        consumer._IndexingKafkaConsumer__settle_done = AsyncMock(return_value=True)  # type: ignore[attr-defined]
        wrapper = consumer._IndexingKafkaConsumer__process_message_wrapper  # type: ignore[attr-defined]
    else:
        monkeypatch.setattr(redis_mod, "time", _time_module(clock))
        consumer, _ = redis_consumer()
        abandon.side_effect = consumer._abandon_message  # a spy on the real one: it notes the dead-letter disposition
        consumer._abandon_message = abandon  # type: ignore[method-assign]
    sent = _wire_requeue(consumer)
    consumer.message_handler = scenario.handler  # type: ignore[assignment]
    manager = RetryManager(LOGGER, redis_client=fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer()))
    counts = _counting(manager)
    consumer.retry_manager = manager
    body = _pdf_body()
    for i in range(1, 100):
        before = len(sent)
        if kind == "kafka":
            await wrapper(kafka_record(i, body))
        else:
            await consumer._process_message_wrapper("t", f"{i}-0", {"value": body})
        if abandon.await_count or scenario.indexed_at is not None or len(sent) == before:
            break
        clock.now = max(clock.now, float(sent[-1]["_retry_not_before"]))  # the consumer holds it until then
        body = _pdf_body(sent[-1])
    reason = abandon.await_args.kwargs.get("reason") if abandon.await_count else None
    return _outcome(scenario, reason, clock, counts["attempts"])


async def _drive_postgres(script: list[str]) -> dict[str, Any]:
    from app.services.messaging.worker_loop import WorkerLoop

    clock = Clock()
    scenario = Scenario(clock, script)
    worker = WorkerLoop(LOGGER, thread_name="docling-outage")
    try:
        consumer = PgMessagingConsumer(LOGGER, RedisStreamsConfig(topics=["t"], group_id="g"), indexing=True, worker=worker)
        consumer.message_handler = scenario.handler  # type: ignore[assignment]
        consumer.queue = MagicMock(dead=AsyncMock(return_value=True), ack=AsyncMock(return_value=True), nack=AsyncMock(return_value=True))
        value = json.dumps({**json.loads(_pdf_body()), "requestId": "req", "orgId": "org-1"})
        attempts, counted = 1, 0
        for _ in range(400):
            nacks = consumer.queue.nack.await_count
            await consumer._settle(Delivery(topic="t", message_id=1, attempts=attempts, value=value, key=None))
            if consumer.queue.dead.await_count or scenario.indexed_at is not None or consumer.queue.nack.await_count == nacks:
                break
            call = consumer.queue.nack.await_args
            clock.now += float(call.args[2])  # visible again after the delay it was nacked with
            if not call.kwargs.get("refund"):  # a claim counts an attempt; a refund gives it back
                attempts += 1
                counted += 1
        reason = consumer.queue.dead.await_args.args[2] if consumer.queue.dead.await_count else None
    finally:
        worker.stop()
    if reason is not None and not reason.startswith("Docling stayed unavailable"):
        counted += 1  # the failure that ended it was counted too
    return _outcome(scenario, reason, clock, counted)


def _outcome(scenario: Scenario, reason: str | None, clock: Clock, attempts: int) -> dict[str, Any]:
    return {
        "failed": reason is not None,
        "reason": reason,
        "indexed_at_s": scenario.indexed_at,
        "deliveries_at_s": scenario.deliveries,
        "ended_at_s": clock.now - START,
        "attempts_counted": attempts,
    }


async def _drive(kind: str, script: list[str], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    if kind == "postgres":
        return await _drive_postgres(script)
    return await _drive_stream_broker(kind, script, monkeypatch)


@pytest.mark.parametrize("kind", BROKERS)
async def test_a_150s_docling_outage_waits_for_docling_and_indexes(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed at 90 s before (3 counted deliveries at 0, 20 and 85 s): now handed back every 65 s, uncounted."""
    out = await _drive(kind, _outage_for(150), monkeypatch)

    assert not out["failed"], out
    assert out["deliveries_at_s"] == [0.0, 65.0, 130.0, 195.0]
    assert out["indexed_at_s"] == 200.0
    assert out["attempts_counted"] == 0


@pytest.mark.parametrize("kind", ["kafka", "redis"])
async def test_an_outage_that_never_ends_fails_the_record_at_the_delivery_backstop(
    kind: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        out = await _drive(kind, [REFUSED], monkeypatch)

    assert out["reason"] == "Docling stayed unavailable: handed back 10 times (backstop 10)"
    assert len(out["deliveries_at_s"]) == 10 and out["ended_at_s"] == 590.0
    assert out["attempts_counted"] == 0
    ends = [r.levelno for r in caplog.records if r.getMessage().startswith("index.end")]
    assert ends == [logging.INFO] * 9 + [logging.ERROR], "the give-up is not the hand-backs' INFO: a human reindexes it"


async def test_on_postgres_an_outage_that_never_ends_fails_at_the_201st_hand_back() -> None:
    """PostgreSQL counts hand-backs per process (``PG_QUEUE_HANDBACK_LIMIT``, 200): about 3.6 h at 65 s each."""
    out = await _drive_postgres([REFUSED])

    assert out["reason"] == "Docling stayed unavailable: handed back 201 times (backstop 200)"
    assert len(out["deliveries_at_s"]) == 201 and out["ended_at_s"] == 200 * 65.0 + ATTEMPT_S
    assert out["attempts_counted"] == 0


@pytest.mark.parametrize("kind", BROKERS)
async def test_a_pdf_that_keeps_bringing_docling_down_fails_after_three_counted_attempts(
    kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash loop: the file drops Docling mid-answer, Docling restarts (refused), and so on. Each drop is counted and the
    refusals between them neither add to nor reset the count, so Docling is brought down at most 3 times by one PDF."""
    out = await _drive(kind, [REFUSED, DROPPED, REFUSED, DROPPED, DROPPED, UP], monkeypatch)

    assert out["failed"], out
    assert len(out["deliveries_at_s"]) == 5
    assert out["attempts_counted"] == 3
    assert out["indexed_at_s"] is None


@pytest.mark.parametrize("kind", BROKERS)
async def test_a_file_verdict_is_not_retried(kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _drive(kind, [VERDICT], monkeypatch)

    assert out["failed"] and len(out["deliveries_at_s"]) == 1, out


@pytest.mark.parametrize("kind", ["kafka", "redis"])
async def test_a_hand_back_is_logged_at_info(kind: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """One waiting PDF a minute must not be a WARNING each: the outage itself is reported by the client."""
    with caplog.at_level(logging.DEBUG):
        await _drive(kind, _outage_for(150), monkeypatch)

    ends = [r for r in caplog.records if r.getMessage().startswith("index.end")]
    assert [r.levelno for r in ends] == [logging.INFO] * 4
