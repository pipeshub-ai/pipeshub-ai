"""Every indexing consumer grows a record's budget to what the record handler's resolved labels get (F23g).

One no-mime event for a stored PDF runs through a real consumer (Redis Streams, Kafka, PostgreSQL inline and on its
worker loop), the real record handler (F23e's stored-mime fill) and a real EventProcessor on the service pipeline.
Only the graph, the download and the parsing-service call are stubbed. RECORD_PROCESSING_TIMEOUT=0.2 s and
PDF_RECORD_PROCESSING_TIMEOUT=30 s stand in for 1800 s / 5400 s.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.events import DedupDecision, EventProcessor
from app.services.messaging import record_budget
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    RedisStreamsConfig,
    StreamMessage,
    messaging_env,
)
from app.services.messaging.kafka.utils.utils import KafkaUtils
from app.services.messaging.postgres.consumer import _INDEX_POOL, PgMessagingConsumer
from app.services.messaging.worker_loop import WorkerLoop
from app.utils.structured_logging import DefaultLoggingProvider
from tests.unit.config.file_types import gen_pinned_decisions as gen
from tests.unit.services.messaging.test_indexing_end import _Tracker
from tests.unit.services.messaging.test_indexing_message_binding import (
    _kafka,
    _kafka_message,
    _redis,
)
from tests.unit.services.messaging.test_record_handler_stored_mime import (
    ABSENT,
    PDF,
    _downloads,
    _handler,
    _payload,
    _record,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

LOG = logging.getLogger("record-budget-consumers")
GENERAL_S, PDF_S, SERVICE_TAKES_S = 0.2, 30.0, 0.5
PG_CFG = RedisStreamsConfig(topics=["t"], group_id="g")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", str(GENERAL_S))
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", str(PDF_S))
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    caplog.set_level(logging.INFO)


@contextlib.asynccontextmanager
async def _no_scope(*_a: object, **_k: object) -> AsyncIterator[None]:
    """The model-call identity scope needs a real config service; it decides nothing here."""
    yield


async def _deliver(broker: str, *, event_mime: object, worker: WorkerLoop | None = None) -> dict[str, Any]:
    """One delivery through a real consumer and the real handler/dispatch; returns what each side decided."""
    record = _record("Report", PDF)
    handler = _handler(record)
    graph = AsyncMock()
    graph.update_node = AsyncMock(return_value=True)
    graph.get_document.return_value = dict(record)
    ep = EventProcessor(gen._Silent(), gen._processor(), graph, MagicMock())
    ep.parsing_client = MagicMock(circuit_open=False)
    handler.event_processor.on_event = MagicMock(side_effect=ep.on_event)
    out: dict[str, Any] = {"service_done": False}

    async def services(**kw: Any) -> AsyncIterator[PipelineEvent]:  # noqa: ANN401
        out["sent_budget_s"] = messaging_env.record_processing_timeout_for(kw["extension"], kw["mime_type"])
        out["armed"] = record_budget.current()
        await asyncio.sleep(SERVICE_TAKES_S)
        out["service_done"] = True
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

    message_handler = await KafkaUtils.create_record_message_handler(MagicMock(), record_event_service=handler)
    payload = _payload("Report", "unknown", event_mime)
    value = json.dumps({"eventType": "newRecord", "payload": payload, "requestId": "req-budget"})
    signed, jwt, stream = _downloads(handler)
    with gen._quiet_env(), signed, jwt, stream, \
         patch("app.services.messaging.kafka.utils.utils.call_scope", _no_scope), \
         patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         patch.object(ep, "_use_service_pipeline", return_value=True), \
         patch.object(ep, "_orchestrate_via_services", side_effect=services):
        try:
            if broker == "redis":
                c = _redis(retry_manager=_Tracker())
                c._requeue_message = AsyncMock()
                c.message_handler = message_handler
                out["ok"] = await c._process_message_wrapper("record-events", "5-0", {"value": value})
            elif broker == "kafka":
                c = _kafka()
                c.retry_manager = _Tracker()
                c._requeue_message = AsyncMock()
                c.message_handler = message_handler
                out["ok"] = await c._IndexingKafkaConsumer__process_message_wrapper(_kafka_message(value.encode()))
            else:
                c = PgMessagingConsumer(LOG, PG_CFG, indexing=True, worker=worker)
                c.message_handler = message_handler
                await c._drive_indexing(StreamMessage(eventType="newRecord", payload=payload), "message:1")
                out["ok"] = True
        except TimeoutError:
            out["ok"] = False
    # Read after the delivery: on the PostgreSQL worker loop the move is posted to the claim loop, not applied in place.
    armed = out.pop("armed", None)
    out["armed_s"] = armed.seconds if armed is not None else None
    return out


@pytest.fixture
def worker() -> Iterator[WorkerLoop]:
    loop = WorkerLoop(LOG, thread_name="test-record-budget-worker")
    try:
        yield loop
    finally:
        loop.stop()


@pytest.mark.parametrize("broker", ["redis", "kafka", "postgres"])
async def test_a_stored_pdf_sent_without_a_mime_gets_the_pdf_budget(broker: str) -> None:
    out = await _deliver(broker, event_mime=ABSENT)
    assert out["sent_budget_s"] == out["armed_s"] == PDF_S, out
    assert out["ok"] and out["service_done"], f"cut at {GENERAL_S}s while the service was told {PDF_S}s: {out}"


async def test_on_the_postgres_worker_loop_too(worker: WorkerLoop) -> None:
    out = await _deliver("postgres", event_mime=ABSENT, worker=worker)
    assert out["sent_budget_s"] == out["armed_s"] == PDF_S, out
    assert out["ok"] and out["service_done"], out


@pytest.mark.parametrize("broker", ["redis", "kafka", "postgres"])
async def test_a_pdf_sent_with_its_mime_keeps_the_pdf_budget(broker: str) -> None:
    out = await _deliver(broker, event_mime=PDF)
    assert out["sent_budget_s"] == out["armed_s"] == PDF_S and out["ok"], out


@pytest.mark.parametrize("broker", ["redis", "kafka"])
async def test_a_cut_reports_the_grown_budget(broker: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    # Wide margins: the resize happens well inside the general budget, and the service outlasts the grown one.
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "0.5")
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "1.0")
    monkeypatch.setattr(sys.modules[__name__], "SERVICE_TAKES_S", 3.0)
    out = await _deliver(broker, event_mime=ABSENT)
    assert not out["ok"]
    (end,) = [r for r in caplog.records if r.getMessage() == "index.end"]
    assert (end.error_class, end.timeout_s) == ("timeout", 1.0)


async def test_postgres_cuts_on_its_claim_loop_while_the_worker_is_blocked(
    worker: WorkerLoop, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cut, the lease renewal and the release stay on the claim loop: a worker blocked by a synchronous call does
    not hold the record's lease or slot past its budget."""
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "0.1")
    unblock = threading.Event()
    released: list[str] = []

    class Manager:
        async def try_acquire(self, pool: str, owner: str, limit: int, seconds: float) -> bool:
            return True

        async def renew_many(self, leases: list, seconds: float) -> dict:
            return dict.fromkeys(leases, True)

        async def release(self, pool: str, owner: str) -> None:
            released.append(pool)

    async def handler(_msg: object) -> AsyncIterator[SimpleNamespace]:
        unblock.wait(10)  # blocks the worker loop itself, as a synchronous parse would
        yield SimpleNamespace(event=IndexingEvent.INDEXING_COMPLETE)

    consumer = PgMessagingConsumer(LOG, PG_CFG, indexing=True, worker=worker, concurrency_manager=Manager())  # type: ignore[arg-type]
    consumer.message_handler = handler
    consumer.running = True  # the cluster slot is taken only while the consumer runs
    try:
        with pytest.raises(TimeoutError):
            await consumer._drive_indexing(SimpleNamespace(payload={"recordId": "r1"}, org_ctx=None, requestId="r"), "m:1")
        assert sorted(released) == sorted(["record:r1", _INDEX_POOL]), released
        assert not unblock.is_set(), "cut while the worker was still blocked"
    finally:
        unblock.set()
