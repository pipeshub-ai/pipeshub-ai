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
    await _run(broker, _handler(ConnectionError("down")), retry_manager=_Tracker())
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.attempt, end.error_class) == (
        logging.WARNING, "failure", "retry", 1, "unavailable")
    assert _others_at(caplog, logging.INFO) == []


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
    def setup(c) -> None:
        c._requeue_message = AsyncMock(side_effect=ConnectionError(f"broker down {NAME}"))
        if broker == "kafka":
            c._scheduler = object()  # fair scheduling: re-buffered, not raised
            c._IndexingKafkaConsumer__rebuffer_after_failed_handback = AsyncMock()

    with contextlib.suppress(ConnectionError):
        await _run(broker, _handler(failure()), retry_manager=tracker() if tracker else None, setup=setup)
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


async def test_redis_ack_failure_after_success_is_a_success_warning(caplog) -> None:
    def setup(c) -> None:
        c._ack_message = AsyncMock(side_effect=RedisAcknowledgementError("xack failed"))

    assert await _run("redis", _handler(), setup=setup) is False
    end = _end(caplog)
    assert (end.levelno, end.outcome, end.disposition, end.reason) == (logging.WARNING, "success", "retry", "ack_failed")


async def test_redis_cleanup_failure_after_the_ack_is_a_second_line(caplog) -> None:
    def setup(c) -> None:
        c._clear_retry_tracking = AsyncMock(side_effect=[RuntimeError("tracker down"), None])

    assert await _run("redis", _handler(), setup=setup) is True
    assert (_end(caplog).outcome, _end(caplog).levelno) == ("success", logging.INFO)
    (failed,) = [r for r in _ours(caplog) if r.getMessage() == "index.post_ack_failed"]
    assert failed.levelno == logging.WARNING


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
    broker_error = RuntimeError("commit failed")  # internal: ERROR by class
    log_failure(logging.getLogger(LOGGER), "index.commit_failed", broker_error, level=logging.WARNING)
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
