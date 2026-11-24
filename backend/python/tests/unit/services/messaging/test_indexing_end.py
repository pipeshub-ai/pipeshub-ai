"""Indexing consumers: one ``index.end`` line per delivery that reaches the record's timeline, with the
broker decision, the attempt and the failure's class, and no other line about the same failure
(contract: docs/logging.md, "Indexing"). Real loggers and caplog: a mock logger would hide lines
written through ``log_failure``/``log_event``."""

import asyncio
import contextlib
import logging
import zipfile
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiokafka import TopicPartition
from aiokafka.errors import CommitFailedError

from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    RecordDeletedError,
)
from app.services.messaging import consumer_concurrency as concurrency
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.kafka.consumer.indexing_consumer import _InFlightOffset
from app.services.messaging.postgres.consumer import PgMessagingConsumer
from app.services.messaging.redis_streams.indexing_consumer import (
    RedisAcknowledgementError,
)
from app.utils.structured_logging import DefaultLoggingProvider
from tests.unit.services.messaging.test_indexing_message_binding import (
    LOGGER,
    _kafka,
    _kafka_message,
    _redis,
    _value,
)
from tests.unit.services.messaging.test_indexing_message_binding import (
    _Tracker as _Counts,
)

NAME = "Quarterly-Secret-Plan.docx"


@pytest.fixture(autouse=True)
def _capture(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    monkeypatch.setenv("MAX_DELIVERY_ATTEMPTS", "3")
    caplog.set_level(logging.DEBUG, logger=LOGGER)


def _corrupt() -> DocumentProcessingError:
    try:
        try:
            raise zipfile.BadZipFile("File is not a zip file")
        except zipfile.BadZipFile as e:
            raise DocumentProcessingError(f"Failed to process document: {NAME}") from e
    except DocumentProcessingError as e:
        return e


def _handler(exc: BaseException | None = None, *, wait: float = 0) -> object:
    async def handler(message) -> AsyncGenerator[PipelineEvent, None]:
        if wait:
            await asyncio.sleep(wait)
        if exc is not None:
            raise exc
        for event in (IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE):
            yield PipelineEvent(event=event, data=PipelineEventData(record_id="rec-1"))

    return handler


class _Tracker(_Counts):
    """The retry tracker also counts unfinished attempts (the crash-loop guard)."""

    async def unfinished_attempts(self, work_key: str) -> int:
        return 0

    async def start_attempt(self, work_key: str) -> int:
        return 0

    async def finish_attempt(self, work_key: str, *, settled: bool) -> None:
        return None


class _Exhausted(_Tracker):
    """Two failures already counted: this one is the last."""

    async def get_count(self, message_id: str) -> int:
        return 2

    async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
        return 3, True


async def _run(broker: str, handler, *, retry_manager=None, setup=None) -> object:
    if broker == "redis":
        c = _redis(retry_manager=retry_manager)
    else:
        c = _kafka()
        c.retry_manager = retry_manager
    c._requeue_message = AsyncMock()
    c.message_handler = handler
    if setup is not None:
        setup(c)
    if broker == "redis":
        return await c._process_message_wrapper("record-events", "5-0", {"value": _value()})
    return await c._IndexingKafkaConsumer__process_message_wrapper(_kafka_message(_value().encode()))


def _ours(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER]


def _end(caplog) -> logging.LogRecord:
    ends = [r for r in _ours(caplog) if r.getMessage() == "index.end"]
    assert len(ends) == 1, [r.getMessage() for r in _ours(caplog)]
    return ends[0]


def _others_at(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in _ours(caplog) if r.levelno >= level and r.getMessage() != "index.end"]


BROKERS = pytest.mark.parametrize("broker", ["redis", "kafka"])


@BROKERS
async def test_success_is_one_info_line(broker, caplog) -> None:
    assert await _run(broker, _handler()) is True
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.record_id, end.attempt) == (
        logging.INFO, "success", "ack", "rec-1", 1)
    assert _others_at(caplog, logging.INFO) == []


@BROKERS
async def test_a_damaged_file_is_one_warning_without_its_name(broker, caplog) -> None:
    await _run(broker, _handler(_corrupt()), retry_manager=_Tracker())
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.reason, end.attempt) == (
        logging.WARNING, "failure", "dead_letter", "terminal", 1)
    assert (end.error_class, end.error_type, end.cause_type) == ("bad_request", "DocumentProcessingError", "BadZipFile")
    assert not hasattr(end, "error_message")
    assert _others_at(caplog, logging.WARNING) == []
    assert all(NAME not in r.getMessage() + str(vars(r)) for r in _ours(caplog))


@BROKERS
async def test_a_transient_failure_with_attempts_left_is_one_warning(broker, caplog) -> None:
    consumers: list = []
    await _run(broker, _handler(ConnectionError("down")), retry_manager=_Tracker(), setup=consumers.append)
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.attempt, end.error_class) == (
        logging.WARNING, "failure", "retry", 1, "unavailable")
    assert _others_at(caplog, logging.INFO) == []
    consumers[0]._requeue_message.assert_awaited_once()  # settled once
    if broker == "kafka":
        consumers[0].consumer.commit.assert_awaited_once()


@BROKERS
async def test_attempts_exhausted_is_one_error_with_one_stack(broker, caplog) -> None:
    await _run(broker, _handler(ConnectionError("down")), retry_manager=_Exhausted())
    end = _end(caplog)
    assert (end.levelno, end.disposition, end.reason, end.attempt) == (
        logging.ERROR, "dead_letter", "attempts_exhausted", 3)
    assert [r.getMessage() for r in _ours(caplog) if r.exc_text] == ["index.end"]
    assert _others_at(caplog, logging.WARNING) == []


@BROKERS
async def test_a_record_deleted_mid_indexing_is_one_info_line(broker, caplog) -> None:
    await _run(broker, _handler(RecordDeletedError("rec-1")), retry_manager=_Tracker())
    end = _end(caplog)
    assert (end.levelno, end.error_type) == (logging.INFO, "RecordDeletedError")
    assert _others_at(caplog, logging.INFO) == []


@BROKERS
async def test_a_hand_back_is_one_warning_and_no_separate_line(broker, caplog) -> None:
    await _run(broker, _handler(concurrency.RecordLeaseHeld("busy", delay_s=1.0)))
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.reason, end.code) == (
        logging.WARNING, "skipped", "retry", "requeued", "RecordLeaseHeld")
    assert _others_at(caplog, logging.INFO) == []


@BROKERS
async def test_the_hand_back_backstop_is_a_dead_letter_error(broker, caplog) -> None:
    with patch.object(concurrency, "record_delivery", AsyncMock(return_value=(9, True))):
        await _run(broker, _handler(concurrency.RequeueWithoutAttempt("parse slot wait ran out")))
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.reason) == (
        logging.ERROR, "failure", "dead_letter", "backstop")
    assert _others_at(caplog, logging.WARNING) == []


@BROKERS
@pytest.mark.parametrize(("failure", "tracker"), [
    (lambda: concurrency.RecordLeaseHeld("busy"), None),  # the hand-back's re-queue
    (lambda: ConnectionError("down"), _Tracker),  # a transient failure's re-queue, with attempts left
], ids=["hand_back", "retry"])
async def test_a_failed_requeue_is_its_own_warning_without_text(broker, failure, tracker, caplog) -> None:
    consumers = []

    def setup(c) -> None:
        consumers.append(c)
        c._requeue_message = AsyncMock(side_effect=ConnectionError(f"broker down {NAME}"))
        if broker == "kafka":
            c._scheduler = object()  # fair scheduling: the message is re-buffered here, not raised
            c._IndexingKafkaConsumer__rebuffer_after_failed_handback = AsyncMock()

    assert not await _run(broker, _handler(failure()), retry_manager=tracker() if tracker else None, setup=setup)
    if broker == "kafka":
        consumers[0]._IndexingKafkaConsumer__rebuffer_after_failed_handback.assert_awaited_once()
    assert _end(caplog).reason == "requeue_failed"
    (failed,) = [r for r in _ours(caplog) if r.getMessage() == "index.requeue_failed"]
    assert (failed.levelno, failed.error_class) == (logging.WARNING, "unavailable")
    assert not hasattr(failed, "error_message")


@BROKERS
async def test_a_processing_timeout_is_one_line_with_its_budget(broker, caplog) -> None:
    with patch(
        "app.services.messaging.config.messaging_env.record_processing_timeout_for", return_value=0.01,
    ), patch.object(concurrency, "requeue_after_productive_cut", AsyncMock()):
        await _run(broker, _handler(wait=1), retry_manager=_Tracker())
    end = _end(caplog)
    assert (end.error_class, end.timeout_s, end.disposition) == ("timeout", 0.01, "retry")
    assert _others_at(caplog, logging.WARNING) == []


@BROKERS
async def test_a_cancelled_record_is_info(broker, caplog) -> None:
    with pytest.raises(asyncio.CancelledError):
        await _run(broker, _handler(asyncio.CancelledError()))
    end = _end(caplog)
    assert (end.levelno, end.reason) == (logging.INFO, "cancelled")
    assert _others_at(caplog, logging.WARNING) == []


async def test_redis_ack_failure_while_dead_lettering_is_not_a_dead_letter(caplog) -> None:
    def setup(c) -> None:
        c._ack_message = AsyncMock(side_effect=RedisAcknowledgementError("xack failed"))

    with pytest.raises(RedisAcknowledgementError):
        await _run("redis", _handler(_corrupt()), setup=setup)
    end = _end(caplog)
    assert (end.disposition, end.reason, end.error_type) == ("retry", "ack_failed", "DocumentProcessingError")
    (ack_line,) = [r for r in _ours(caplog) if r.getMessage() == "index.ack_failed"]
    from app.utils.logger import log_record_fields

    assert (ack_line.levelno, ack_line.error_type, log_record_fields(ack_line).get("record_id")) == (
        logging.WARNING, "RedisAcknowledgementError", "rec-1")  # in the delivery's context
    assert not hasattr(ack_line, "error_message")


async def test_redis_ack_failure_after_success_is_a_success_warning(caplog) -> None:
    def setup(c) -> None:
        c._ack_message = AsyncMock(side_effect=RedisAcknowledgementError("xack failed"))

    assert await _run("redis", _handler(), setup=setup) is False
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.reason) == (logging.WARNING, "success", "retry", "ack_failed")


async def test_redis_retry_tracker_failure_after_the_ack_is_a_second_line(caplog) -> None:
    tracker = _Tracker()
    tracker.clear = AsyncMock(side_effect=ConnectionError("tracker down"))

    assert await _run("redis", _handler(), retry_manager=tracker) is True
    tracker.clear.assert_awaited()
    assert (_end(caplog).outcome, _end(caplog).levelno) == ("success", logging.INFO)
    (failed,) = [r for r in _ours(caplog) if r.getMessage() == "index.retry_clear_failed"]
    assert (failed.levelno, failed.error_type) == (logging.WARNING, "ConnectionError")
    assert _others_at(caplog, logging.ERROR) == []


async def test_redis_pending_entry_no_longer_ours_writes_nothing_above_debug(caplog) -> None:
    def setup(c) -> None:
        c._pending_message_is_owned = AsyncMock(return_value=False)

    assert await _run("redis", _handler(), setup=setup) is False
    assert [r for r in _ours(caplog) if r.levelno > logging.DEBUG] == []


async def test_kafka_commit_failure_after_success_is_a_success_warning_and_one_failure_line(caplog) -> None:
    def setup(c) -> None:
        c.consumer.commit = AsyncMock(side_effect=ConnectionError(f"broker down {NAME}"))

    with pytest.raises(ConnectionError):
        await _run("kafka", _handler(), setup=setup)
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.reason) == (logging.WARNING, "success", "retry", "commit_failed")
    failed = [r for r in _ours(caplog) if r.getMessage() == "index.commit_failed" and r.levelno >= logging.WARNING]
    assert len(failed) == 1 and not hasattr(failed[0], "error_message")
    assert all(NAME not in r.getMessage() + str(vars(r)) for r in _ours(caplog))


async def test_kafka_requeue_failure_raises_after_one_warning(caplog) -> None:
    def setup(c) -> None:
        c._requeue_message = AsyncMock(side_effect=ConnectionError(f"broker down {NAME}"))

    with pytest.raises(ConnectionError):
        await _run("kafka", _handler(ConnectionError("down")), retry_manager=_Tracker(), setup=setup)
    end = _end(caplog)
    assert (end.reason, end.disposition, end.error_class) == ("requeue_failed", "retry", "unavailable")
    (failed,) = [r for r in _ours(caplog) if r.getMessage() == "index.requeue_failed"]
    assert failed.levelno == logging.WARNING and not hasattr(failed, "error_message")
    assert all(NAME not in r.getMessage() + str(vars(r)) for r in _ours(caplog))


# ---- P6a review fixes -------------------------------------------------------------------------------


def _tail_failure() -> object:
    """Indexed (INDEXING_COMPLETE out), then the handler's bookkeeping fails."""
    async def handler(message) -> AsyncGenerator[PipelineEvent, None]:
        for event in (IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE):
            yield PipelineEvent(event=event, data=PipelineEventData(record_id="rec-1"))
        raise ConnectionError("tail")

    return handler


@pytest.mark.parametrize(("broker", "tracker", "reason"), [
    ("redis", _Tracker, "requeue_failed"),
    ("kafka", _Tracker, "requeue_failed"),
    ("redis", None, "no_retry_tracker"),
])
async def test_a_failure_after_indexing_complete_that_leaves_the_entry_pending_is_a_retry(
    broker, tracker, reason, caplog,
) -> None:
    def setup(c) -> None:
        c._requeue_message = AsyncMock(side_effect=ConnectionError("broker down"))
        if broker == "kafka":
            c._scheduler = object()  # fair scheduling: no re-raise to the poll loop

    with contextlib.suppress(ConnectionError):
        await _run(broker, _tail_failure(), retry_manager=tracker() if tracker else None, setup=setup)
    end = _end(caplog)
    assert (end.outcome, end.disposition, end.reason, end.levelno) == ("failure", "retry", reason, logging.WARNING)


async def test_redis_count_failure_is_noted(caplog) -> None:
    class _Broken(_Tracker):
        async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
            raise ConnectionError("redis down")

    await _run("redis", _handler(ConnectionError("down")), retry_manager=_Broken())
    assert (_end(caplog).disposition, _end(caplog).reason) == ("retry", "count_failed")
    (failed,) = [r for r in _ours(caplog) if r.getMessage() == "index.count_failed"]
    assert (failed.levelno, failed.error_type, hasattr(failed, "error_message")) == (logging.WARNING, "ConnectionError", False)


@BROKERS
async def test_a_source_refusal_is_a_warning_with_its_own_reason(broker, caplog) -> None:
    from app.utils.api_call import ApiCallError

    refusal = ApiCallError("refused", status_code=400)
    refusal.source_reason = "too large"
    await _run(broker, _handler(refusal), retry_manager=_Tracker())
    end = _end(caplog)
    assert (end.levelno, end.reason, end.outcome) == (logging.WARNING, "source_refused", "failure")


@BROKERS
async def test_a_parse_slot_wait_names_its_pool(broker, caplog) -> None:
    timeout = concurrency.RequeueWithoutAttempt("no parsing slot")
    timeout.pool = "parsing"
    await _run(broker, _handler(timeout))
    assert (_end(caplog).reason, _end(caplog).pool) == ("requeued", "parsing")


@pytest.mark.parametrize("broker", ["redis", "kafka"])
async def test_a_broker_error_already_logged_is_not_logged_again_by_the_task_callback(broker, caplog, monkeypatch) -> None:
    from concurrent.futures import Future

    from app.utils.error_class import log_failure

    if broker == "redis":
        c = _redis()
        module = "app.services.messaging.redis_streams.indexing_consumer"
    else:
        c = _kafka()
        module = "app.services.messaging.kafka.consumer.indexing_consumer"
    c.worker_loop = MagicMock()
    c.running = True
    c.main_loop = None
    broker_error = RuntimeError("commit failed for broker-secret-host")  # internal: ERROR by class
    log_failure(logging.getLogger(LOGGER), "index.commit_failed", broker_error, level=logging.WARNING,
                include_error_text=False)  # as __commit_if_appropriate writes it
    done: Future = Future()
    done.set_exception(broker_error)

    def submit(coro, _loop) -> Future:
        coro.close()
        return done

    monkeypatch.setattr(f"{module}.asyncio.run_coroutine_threadsafe", submit)
    if broker == "redis":
        await c._start_processing_task("record-events", "5-0", {"value": _value()})
    else:
        await c._IndexingKafkaConsumer__start_processing_task(_kafka_message(_value().encode()))
    task_lines = [r for r in _ours(caplog) if r.getMessage() == "index.task_failed"]
    assert task_lines and all(r.levelno == logging.DEBUG for r in task_lines)
    # The text the first line left out stays out, at DEBUG too.
    (first,) = [r for r in _ours(caplog) if r.getMessage() == "index.commit_failed"]
    assert not hasattr(first, "error_message")
    assert all(not hasattr(r, "error_message") for r in task_lines)
    assert all("broker-secret-host" not in r.getMessage() + str(vars(r)) for r in _ours(caplog))


# ---- PostgreSQL broker ------------------------------------------------------------------------------


class _PgQueue:
    def __init__(self) -> None:
        self.ack = AsyncMock()
        self.nack = AsyncMock()
        self.dead = AsyncMock()


def _pg(handler, *, max_attempts: int = 3) -> PgMessagingConsumer:
    from app.services.messaging.config import RedisStreamsConfig

    c = PgMessagingConsumer(logging.getLogger(LOGGER), RedisStreamsConfig(topics=["record-events"], group_id="g"),
                            indexing=True, queue=_PgQueue())
    c.message_handler = handler
    return c


async def _pg_settle(handler, *, attempts: int = 1) -> PgMessagingConsumer:
    from app.services.messaging.postgres.queue import Delivery

    c = _pg(handler)
    await c._settle(Delivery(topic="record-events", message_id=7, attempts=attempts, value=_value(), key=None))
    return c


async def test_postgres_success_is_one_info_line(caplog) -> None:
    c = await _pg_settle(_handler())
    c.queue.ack.assert_awaited_once()
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.record_id, end.event_type, end.attempt) == (
        logging.INFO, "success", "ack", "rec-1", "newRecord", 1)
    assert _others_at(caplog, logging.INFO) == []


async def test_postgres_damaged_file_is_one_warning_without_its_name(caplog) -> None:
    c = await _pg_settle(_handler(_corrupt()))
    c.queue.dead.assert_awaited_once()
    end = _end(caplog)
    assert (end.levelno, end.disposition, end.reason, end.error_class, end.cause_type) == (
        logging.WARNING, "dead_letter", "terminal", "bad_request", "BadZipFile")
    assert _others_at(caplog, logging.WARNING) == []
    assert all(NAME not in r.getMessage() + str(vars(r)) for r in _ours(caplog))


async def test_postgres_transient_failure_is_one_retry_warning(caplog) -> None:
    c = await _pg_settle(_handler(ConnectionError("down")))
    c.queue.nack.assert_awaited_once()
    end = _end(caplog)
    assert (end.levelno, end.disposition, end.error_class) == (logging.WARNING, "retry", "unavailable")
    assert _others_at(caplog, logging.INFO) == []


async def test_postgres_out_of_attempts_is_one_error(caplog) -> None:
    c = await _pg_settle(_handler(ConnectionError("down")), attempts=3)
    c.queue.dead.assert_awaited_once()
    end = _end(caplog)
    assert (end.levelno, end.disposition, end.reason, end.attempt) == (logging.ERROR, "dead_letter", "attempts_exhausted", 3)


async def test_postgres_hand_back_is_requeued(caplog) -> None:
    await _pg_settle(_handler(concurrency.RecordLeaseHeld("busy", delay_s=1.0)))
    end = _end(caplog)
    assert (end.outcome, end.disposition, end.reason, end.code) == ("skipped", "retry", "requeued", "RecordLeaseHeld")


async def test_the_simple_postgres_consumer_keeps_its_own_lines(caplog) -> None:
    from app.services.messaging.config import RedisStreamsConfig
    from app.services.messaging.postgres.queue import Delivery

    async def handler(message) -> bool:
        raise RuntimeError("entity handler bug")

    c = PgMessagingConsumer(logging.getLogger(LOGGER), RedisStreamsConfig(topics=["entity-events"], group_id="g"),
                            queue=_PgQueue())
    c.message_handler = handler
    with patch("app.services.messaging.postgres.consumer.open_configured", AsyncMock(
            return_value=MagicMock(message={"eventType": "x", "payload": {}}, ctx=None))):
        await c._settle(Delivery(topic="entity-events", message_id=1, attempts=1, value=_value(), key=None))
    assert [r.getMessage() for r in _ours(caplog) if r.getMessage() == "index.end"] == []
    (line,) = [r for r in _ours(caplog) if r.getMessage() == "message.handler_failed"]
    assert line.error_type == "RuntimeError" and not hasattr(line, "error_message")


async def test_postgres_end_line_is_written_in_the_messages_root_org_and_ids(caplog) -> None:
    from app.utils.logger import log_record_fields

    await _pg_settle(_handler(_corrupt()))
    end = _end(caplog)
    fields = log_record_fields(end)
    assert "req-producer" in str(end.request_id)
    assert (fields.get("org_id"), fields.get("record_id"), fields.get("connector_id")) == ("org_a", "rec-1", "conn-1")


async def test_postgres_delivery_past_its_attempts_names_its_record(caplog) -> None:
    from app.utils.logger import log_record_fields

    c = await _pg_settle(_handler(), attempts=4)
    c.queue.dead.assert_awaited_once()
    end = _end(caplog)
    assert (end.levelno, end.disposition, end.reason, end.event_type) == (
        logging.ERROR, "dead_letter", "unfinished_attempts", "newRecord")
    fields = log_record_fields(end)
    assert (fields.get("org_id"), fields.get("record_id"), fields.get("connector_id")) == ("org_a", "rec-1", "conn-1")


async def test_a_cancelled_postgres_delivery_is_info(caplog) -> None:
    with pytest.raises(asyncio.CancelledError):
        await _pg_settle(_handler(asyncio.CancelledError()))
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.reason) == (logging.INFO, "failure", "cancelled")
    assert _others_at(caplog, logging.WARNING) == []


async def test_postgres_ack_failure_after_success_is_a_success_warning_and_no_other_line(caplog) -> None:
    from app.services.messaging.postgres.queue import Delivery

    queue = _PgQueue()
    queue.ack = AsyncMock(side_effect=ConnectionError("postgres down"))
    c = _pg(_handler())
    c.queue = queue
    await c._run_one(Delivery(topic="record-events", message_id=7, attempts=1, value=_value(), key=None))
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.reason, end.error_class) == (
        logging.WARNING, "success", "retry", "ack_failed", "unavailable")
    assert _others_at(caplog, logging.WARNING) == []


async def test_the_simple_postgres_consumer_still_raises_a_failed_ack() -> None:
    from app.services.messaging.config import RedisStreamsConfig
    from app.services.messaging.postgres.queue import Delivery

    queue = _PgQueue()
    queue.ack = AsyncMock(side_effect=ConnectionError("postgres down"))
    c = PgMessagingConsumer(logging.getLogger(LOGGER), RedisStreamsConfig(topics=["entity-events"], group_id="g"), queue=queue)
    c.message_handler = AsyncMock(return_value=True)
    with patch("app.services.messaging.postgres.consumer.open_configured", AsyncMock(
            return_value=MagicMock(message={"eventType": "x", "payload": {}}, ctx=None))), \
            pytest.raises(ConnectionError):
        await c._settle(Delivery(topic="entity-events", message_id=1, attempts=1, value=_value(), key=None))


async def test_postgres_end_line_shares_the_root_of_the_records_lines_when_the_envelope_has_none(caplog) -> None:
    import json

    from app.services.messaging.postgres.queue import Delivery
    from tests.unit.services.messaging.test_indexing_message_binding import PAYLOAD

    async def handler(message) -> AsyncGenerator[PipelineEvent, None]:
        logging.getLogger(LOGGER).info("handling")
        for event in (IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE):
            yield PipelineEvent(event=event, data=PipelineEventData(record_id="rec-1"))

    value = json.dumps({"eventType": "newRecord", "payload": PAYLOAD})  # no requestId: a legacy producer
    await _pg(handler)._settle(Delivery(topic="record-events", message_id=7, attempts=1, value=value, key=None))
    (handling,) = [r for r in _ours(caplog) if r.getMessage() == "handling"]
    assert str(_end(caplog).request_id).startswith("anon-")
    assert _end(caplog).request_id == handling.request_id


@pytest.mark.parametrize(("failure", "tracker"), [
    (lambda: concurrency.RecordLeaseHeld("busy"), None),
    (lambda: ConnectionError("down"), _Tracker),
], ids=["hand_back", "retry"])
async def test_redis_ack_failure_after_the_replacement_was_sent_is_not_a_failed_requeue(failure, tracker, caplog) -> None:
    calls: list[str] = []

    async def send(*_args, **_kwargs) -> None:
        calls.append("send")

    async def ack(*_args, **_kwargs) -> None:
        calls.append("ack")
        raise RedisAcknowledgementError("xack failed")

    def setup(c) -> None:
        c._requeue_message = send
        c._ack_message = ack

    await _run("redis", _handler(failure()), retry_manager=tracker() if tracker else None, setup=setup)
    assert calls == ["send", "ack"]  # the replacement exists before the original is acked
    assert (_end(caplog).disposition, _end(caplog).reason) == ("retry", "ack_failed")
    (failed,) = [r for r in _ours(caplog) if r.getMessage() == "index.ack_failed"]
    assert failed.levelno == logging.WARNING and not hasattr(failed, "error_message")
    assert not [r for r in _ours(caplog) if r.getMessage() == "index.requeue_failed"]


async def test_postgres_unreadable_message_keeps_the_deliverys_root(caplog) -> None:
    with patch("app.services.messaging.postgres.consumer.open_stream_message", AsyncMock(return_value=None)):
        await _pg_settle(_handler())
    end = _end(caplog)
    assert "req-producer" in str(end.request_id)


async def test_postgres_invalid_message_writes_no_payload_text(caplog) -> None:
    import json

    from app.services.messaging.postgres.queue import Delivery

    value = json.dumps({"eventType": "newRecord", "payload": {"recordId": "rec-1"}, "timestamp": NAME, "requestId": "req-producer"})
    await _pg(_handler())._settle(Delivery(topic="record-events", message_id=7, attempts=1, value=value, key=None))
    end = _end(caplog)
    assert end.error_type == "ValidationError" and not hasattr(end, "error_message")
    assert all(NAME not in r.getMessage() + str(vars(r)) for r in _ours(caplog))


@pytest.mark.parametrize("level", [logging.WARNING, logging.DEBUG])
async def test_postgres_hand_back_is_written_at_the_level_it_asks_for(level, caplog) -> None:
    await _pg_settle(_handler(concurrency.RequeueWithoutAttempt("paused again", delay_s=1.0, log_level=level)))
    end = _end(caplog)
    assert (end.levelno, end.reason, hasattr(end, "end_level")) == (level, "requeued", False)


@pytest.mark.parametrize(("value", "reason"), [
    ("{not json", "unparseable"),
    ("[1, 2]", "unparseable"),
])
async def test_postgres_keeps_the_specific_terminal_reason(caplog, value, reason) -> None:
    from app.services.messaging.postgres.queue import Delivery

    c = _pg(_handler())
    await c._settle(Delivery(topic="record-events", message_id=8, attempts=1, value=value, key=None))
    end = _end(caplog)
    assert (end.disposition, end.reason) == ("dead_letter", reason)


async def test_postgres_rejected_envelope_keeps_its_reason_and_code(caplog) -> None:
    from app.core.envelope import EnvelopeRejected
    from app.services.messaging.postgres.queue import Delivery

    c = _pg(_handler())
    with patch("app.services.messaging.postgres.consumer.open_stream_message",
               AsyncMock(side_effect=EnvelopeRejected("bad_signature"))):
        await c._settle(Delivery(topic="record-events", message_id=9, attempts=1, value=_value(), key=None))
    end = _end(caplog)
    assert (end.disposition, end.reason, end.code) == ("dead_letter", "envelope_rejected", "bad_signature")


async def test_postgres_unparseable_row_line_names_the_row_and_has_no_hold(caplog) -> None:
    from app.services.messaging.postgres.queue import Delivery
    from app.utils.logger import log_record_fields

    c = _pg(_handler())
    await c._settle(Delivery(topic="record-events", message_id=11, attempts=1, value="{not json", key=None))
    end = _end(caplog)
    fields = log_record_fields(end)
    assert (fields.get("topic"), fields.get("message_id")) == ("record-events", "11")
    assert not hasattr(end, "held_ms")  # it never reached an indexing slot


@pytest.mark.parametrize(("handler", "attempts", "method", "level", "outcome"), [
    (_handler(), 1, "ack", logging.WARNING, "success"),
    (_handler(ConnectionError("down")), 1, "nack", logging.WARNING, "failure"),
    (_handler(_corrupt()), 1, "dead", logging.WARNING, "failure"),
])
async def test_postgres_a_settlement_the_queue_refused_is_claim_lost(caplog, handler, attempts, method, level, outcome) -> None:
    from app.services.messaging.postgres.queue import Delivery

    c = _pg(handler)
    getattr(c.queue, method).return_value = False  # a newer claim of the row: this delivery's update changed nothing
    await c._settle(Delivery(topic="record-events", message_id=12, attempts=attempts, value=_value(), key=None))
    end = _end(caplog)
    assert (end.disposition, end.reason, end.outcome, end.levelno) == ("retry", "claim_lost", outcome, level)


@pytest.mark.parametrize(("handler", "method", "reason", "error_type"), [
    (_handler(ConnectionError("down")), "nack", "nack_failed", "ConnectionError"),
    (_handler(_corrupt()), "dead", "dead_letter_failed", "DocumentProcessingError"),
    (_handler(concurrency.RecordLeaseHeld("busy", delay_s=1.0)), "nack", "nack_failed", None),
], ids=["retry", "dead_letter", "hand_back"])
async def test_postgres_a_queue_write_that_raises_says_so_and_propagates(caplog, handler, method, reason, error_type) -> None:
    from app.services.messaging.postgres.queue import Delivery

    c = _pg(handler)
    getattr(c.queue, method).side_effect = RuntimeError("queue down")
    with pytest.raises(RuntimeError, match="queue down"):  # the claim expires and the row is handed out again
        await c._settle(Delivery(topic="record-events", message_id=13, attempts=1, value=_value(), key=None))
    end = _end(caplog)
    assert (end.disposition, end.reason, getattr(end, "error_type", None)) == ("retry", reason, error_type)


async def test_postgres_held_ms_ends_when_the_slot_is_released_not_after_the_ack(caplog) -> None:
    from app.services.messaging.postgres.queue import Delivery

    async def slow_ack(*_args) -> bool:
        await asyncio.sleep(0.3)
        return True

    c = _pg(_handler())
    c.queue.ack.side_effect = slow_ack
    await c._settle(Delivery(topic="record-events", message_id=14, attempts=1, value=_value(), key=None))
    end = _end(caplog)
    assert end.duration_ms >= 300 and end.held_ms < 150


async def test_postgres_an_unparseable_row_is_logged_once(caplog) -> None:
    from app.services.messaging.postgres.queue import Delivery
    from tests.unit.services.messaging.test_record_handler import _make_handler

    sink = _make_handler()
    sink.logger = logging.getLogger(LOGGER)
    c = _pg(_handler())
    c.disposition_sink = sink
    await c._settle(Delivery(topic="record-events", message_id=15, attempts=1, value="{not json", key=None))
    assert (_end(caplog).reason, _end(caplog).levelno) == ("unparseable", logging.ERROR)
    assert _others_at(caplog, logging.INFO) == []


async def test_kafka_a_hand_back_whose_commit_fails_says_commit_failed(caplog) -> None:
    def setup(c) -> None:
        c.consumer.commit = AsyncMock(side_effect=ConnectionError("broker down"))

    with pytest.raises(ConnectionError):
        await _run("kafka", _handler(concurrency.RecordLeaseHeld("busy", delay_s=1.0)), setup=setup)
    end = _end(caplog)
    assert (end.disposition, end.reason, end.levelno) == ("retry", "commit_failed", logging.WARNING)
    (failed,) = [r for r in _ours(caplog) if r.getMessage() == "index.commit_failed"]
    assert not hasattr(failed, "error_message")


@BROKERS
async def test_a_timeout_after_indexing_complete_is_not_the_record_budget(broker, caplog) -> None:
    async def slow_tail(message) -> AsyncGenerator[PipelineEvent, None]:
        for event in (IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE):
            yield PipelineEvent(event=event, data=PipelineEventData(record_id="rec-1"))
        await asyncio.sleep(1)  # bookkeeping that outlives its own short deadline

    with patch.object(concurrency, "POST_INDEXING_TAIL_TIMEOUT_S", 0.01), \
         patch.object(concurrency, "requeue_after_productive_cut", AsyncMock()):
        await _run(broker, slow_tail, retry_manager=_Tracker())
    end = _end(caplog)
    assert end.phase == "after_indexing_complete"
    assert not hasattr(end, "timeout_s")


@BROKERS
async def test_a_timeout_the_handler_raises_before_indexing_complete_names_no_phase(broker, caplog) -> None:
    await _run(broker, _handler(TimeoutError()), retry_manager=_Tracker())  # a shorter timeout inside the handler
    end = _end(caplog)
    assert (end.outcome, end.error_class) == ("failure", "timeout")
    assert not hasattr(end, "phase") and not hasattr(end, "timeout_s")


def _revoked(consumers: list) -> object:
    from aiokafka import TopicPartition

    def setup(c) -> None:
        consumers.append(c)
        c._offset_tracker = MagicMock()
        c._revoked_since_poll = {TopicPartition("record-events", 0)}

    return setup


async def test_kafka_a_delivery_of_a_revoked_partition_does_not_claim_its_settlement(caplog) -> None:
    consumers: list = []
    await _run("kafka", _handler(), setup=_revoked(consumers))
    end = _end(caplog)
    assert (end.outcome, end.disposition, end.reason, end.levelno) == ("success", "retry", "claim_lost", logging.WARNING)
    assert not any("Committed offset" in r.getMessage() for r in _ours(caplog))
    consumers[0].consumer.commit.assert_not_awaited()
    consumers[0]._offset_tracker.mark_done.assert_not_called()


async def test_kafka_a_hand_back_published_before_its_partition_was_revoked_is_claim_lost(caplog) -> None:
    consumers: list = []
    assert await _run("kafka", _handler(concurrency.RecordLeaseHeld("busy", delay_s=1.0)), setup=_revoked(consumers)) is False
    consumers[0]._requeue_message.assert_awaited_once()  # the replacement stands
    end = _end(caplog)
    assert (end.outcome, end.disposition, end.reason, end.levelno) == ("skipped", "retry", "claim_lost", logging.WARNING)


async def test_kafka_a_give_up_on_a_revoked_partition_is_not_a_dead_letter(caplog) -> None:
    with patch.object(concurrency, "record_delivery", AsyncMock(return_value=(9, True))):
        await _run("kafka", _handler(concurrency.RequeueWithoutAttempt("parse slot wait ran out")), setup=_revoked([]))
    end = _end(caplog)
    assert (end.disposition, end.reason, end.levelno) == ("retry", "claim_lost", logging.WARNING)


@pytest.mark.parametrize(("broker", "reason"), [("redis", "ack_failed"), ("kafka", "commit_failed")])
async def test_a_handler_failure_after_indexing_complete_stays_a_failure_when_settlement_fails(broker, reason, caplog) -> None:
    def setup(c) -> None:
        if broker == "redis":
            c._ack_message = AsyncMock(side_effect=RedisAcknowledgementError("xack failed"))
        else:
            c.consumer.commit = AsyncMock(side_effect=ConnectionError("broker down"))

    with contextlib.suppress(ConnectionError):
        await _run(broker, _tail_failure(), retry_manager=_Tracker(), setup=setup)
    end = _end(caplog)
    assert (end.outcome, end.disposition, end.reason, end.error_type) == ("failure", "retry", reason, "ConnectionError")


# Kafka: an error raised by the settlement is not the handler's. It propagates (FIFO seeks back, and the redelivery is
# deduped); nothing settles the delivery a second time, which would charge an attempt and publish a copy.


class _ChargeCounter(_Tracker):
    def __init__(self) -> None:
        super().__init__()
        self.charged = 0

    async def increment_and_check(self, message_id: str, max_attempts: int) -> tuple[int, bool]:
        self.charged += 1
        return await super().increment_and_check(message_id, max_attempts)


class _CrashLooping(_ChargeCounter):
    """Two earlier attempts never finished: the delivery is given up on before the handler runs."""

    async def start_attempt(self, work_key: str) -> int:
        return concurrency.MAX_UNFINISHED_ATTEMPTS


async def _incomplete(message) -> AsyncGenerator[PipelineEvent, None]:
    """Ends without INDEXING_COMPLETE: a failed attempt with no exception."""
    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))


def _settled(c, tracker: _ChargeCounter) -> tuple[int, int, int]:
    return tracker.charged, c._requeue_message.await_count, c.consumer.commit.await_count


def _text_free(caplog) -> bool:
    return not any(hasattr(r, "error_message") for r in _ours(caplog))


def _commit_fails_once(error: Exception, consumers: list, then=None) -> object:
    def setup(c) -> None:
        consumers.append(c)
        c.consumer.commit = AsyncMock(side_effect=[error, None])
        if then is not None:
            then(c)

    return setup


def _with_sink(c) -> None:
    c.disposition_sink = MagicMock()
    c.disposition_sink.on_message_abandoned = AsyncMock()


async def _run_fair(tracker: _ChargeCounter, setup=None) -> tuple[object, _InFlightOffset]:
    """A clean run under fair scheduling: the claim is marked before the commit, which fails."""
    c = _kafka()
    c.retry_manager = tracker
    c._requeue_message = AsyncMock()
    c.message_handler = _handler()
    c._offset_tracker = MagicMock()
    c._offset_tracker.mark_done.return_value = 4
    c._offset_tracker.mark_redeliver.return_value = 3
    c.consumer.commit = AsyncMock(side_effect=ConnectionError("broker down"))
    if setup is not None:
        setup(c)
    in_flight = _InFlightOffset(TopicPartition("record-events", 0), 3)
    with pytest.raises(ConnectionError):
        await c._IndexingKafkaConsumer__process_message_wrapper(
            _kafka_message(_value().encode()), None, None, in_flight
        )
    return c, in_flight


@pytest.mark.parametrize("error", [ConnectionError("broker down"), CommitFailedError("rebalanced")],
                         ids=["unavailable", "commit_failed_error"])
async def test_kafka_an_indexed_record_whose_commit_fails_is_not_charged_or_requeued(error, caplog) -> None:
    consumers: list = []
    tracker = _ChargeCounter()
    with pytest.raises(type(error)):
        await _run("kafka", _handler(), retry_manager=tracker, setup=_commit_fails_once(error, consumers))
    assert _settled(consumers[0], tracker) == (0, 0, 1)
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.reason) == (logging.WARNING, "success", "retry", "commit_failed")
    assert end.error_type == type(error).__name__  # the commit's class is kept, without its text
    assert len([r for r in _ours(caplog) if r.getMessage() == "index.commit_failed"]) == 1
    assert _text_free(caplog)


async def test_kafka_fair_an_indexed_record_whose_commit_fails_is_not_charged_or_requeued(caplog) -> None:
    tracker = _ChargeCounter()
    c, in_flight = await _run_fair(tracker)
    assert _settled(c, tracker) == (0, 0, 1)
    c._offset_tracker.mark_done.assert_called_once_with(TopicPartition("record-events", 0), 3)
    assert in_flight.resolved
    assert (_end(caplog).outcome, _end(caplog).reason, _end(caplog).error_type) == ("success", "commit_failed", "ConnectionError")


async def test_kafka_an_incomplete_run_whose_commit_fails_is_charged_once(caplog) -> None:
    consumers: list = []
    tracker = _ChargeCounter()
    with pytest.raises(ConnectionError):
        await _run("kafka", _incomplete, retry_manager=tracker,
                   setup=_commit_fails_once(ConnectionError("broker down"), consumers))
    assert _settled(consumers[0], tracker) == (1, 1, 1)
    end = _end(caplog)
    assert (end.outcome, end.disposition, end.reason, end.attempt) == ("failure", "retry", "commit_failed", 1)


async def test_kafka_a_settlement_whose_requeue_fails_is_not_settled_again(caplog) -> None:
    consumers: list = []
    tracker = _ChargeCounter()

    def setup(c) -> None:
        consumers.append(c)
        c._requeue_message = AsyncMock(side_effect=ConnectionError("broker down"))

    with pytest.raises(ConnectionError):
        await _run("kafka", _incomplete, retry_manager=tracker, setup=setup)
    assert _settled(consumers[0], tracker) == (1, 1, 0)
    assert _end(caplog).reason == "requeue_failed"


async def test_kafka_a_dead_letter_with_no_handler_whose_commit_fails_is_not_requeued(caplog) -> None:
    consumers: list = []
    tracker = _ChargeCounter()

    def no_handler(c) -> None:
        c.message_handler = None
        _with_sink(c)

    with pytest.raises(ConnectionError):
        await _run("kafka", None, retry_manager=tracker,
                   setup=_commit_fails_once(ConnectionError("broker down"), consumers, then=no_handler))
    c = consumers[0]
    assert (c.disposition_sink.on_message_abandoned.await_count, *_settled(c, tracker)) == (1, 0, 0, 1)


async def test_kafka_a_give_up_whose_commit_fails_is_not_requeued_and_says_commit_failed(caplog) -> None:
    consumers: list = []
    tracker = _CrashLooping()
    with pytest.raises(ConnectionError):
        await _run("kafka", _handler(), retry_manager=tracker,
                   setup=_commit_fails_once(ConnectionError(f"broker down {NAME}"), consumers, then=_with_sink))
    c = consumers[0]
    assert (c.disposition_sink.on_message_abandoned.await_count, *_settled(c, tracker)) == (1, 0, 0, 1)
    end = _end(caplog)
    assert (end.disposition, end.reason) == ("retry", "commit_failed")
    assert len([r for r in _ours(caplog) if r.getMessage() == "index.commit_failed"]) == 1
    assert _text_free(caplog)
    assert all(NAME not in r.getMessage() + str(vars(r)) for r in _ours(caplog))


async def _asks_to_parse(message) -> AsyncGenerator[PipelineEvent, None]:
    yield PipelineEvent(event=IndexingEvent.START_PARSING, data=PipelineEventData(record_id="rec-1"))


# The leases taken in order (index, record, parse); a False while stopping is that floor.
@pytest.mark.parametrize(("leases", "handler"), [
    ([False], _handler()),
    ([True, False], _handler()),
    ([True, True, False], _asks_to_parse),
], ids=["index_lease", "record_lease", "parse_lease"])
async def test_kafka_fair_a_shutdown_floor_whose_commit_fails_is_not_charged_or_requeued(leases, handler, caplog) -> None:
    def stopping(c) -> None:
        c.running = False
        c.message_handler = handler
        c.concurrency_manager = MagicMock()
        c._acquire_distributed_slot = AsyncMock(side_effect=leases)

    tracker = _ChargeCounter()
    c, in_flight = await _run_fair(tracker, stopping)
    assert c._acquire_distributed_slot.await_count == len(leases)  # stopped at this floor, not an earlier one
    assert _settled(c, tracker) == (0, 0, 1)
    c._offset_tracker.mark_redeliver.assert_called_once()
    assert in_flight.resolved
    assert _end(caplog).reason == "shutdown"
