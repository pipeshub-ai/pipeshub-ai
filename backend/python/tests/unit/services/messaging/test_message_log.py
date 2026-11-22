"""message_log: one broker delivery's log context and its one ``message.end`` line."""

import asyncio
import contextvars
import logging

import pytest
from pydantic import ValidationError

from app.core import org_context
from app.services.messaging import message_log as ML
from app.services.messaging.config import StreamMessage
from app.utils import error_class
from app.utils.logger import log_record_fields
from app.utils.logging_fields import current_log_fields
from app.utils.request_context import get_context
from app.utils.structured_logging import DefaultLoggingProvider

LOGGER = "test.message_log"
log = logging.getLogger(LOGGER)
SEEDED = "Ada SEEDED Lovelace"
ORG = org_context.OrgContext("org_a", "user_a", "user", "envelope", "req-producer")
MESSAGE = {"eventType": "orgUpdated", "payload": {"orgId": "org_a", "recordId": "rec-1", "connectorId": "conn-1"}}


@pytest.fixture(autouse=True)
def _raw_identity(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: DefaultLoggingProvider())
    caplog.set_level(logging.DEBUG, logger=LOGGER)


def _ends(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == ML.EVENT_END]


def _only_end(caplog: pytest.LogCaptureFixture) -> tuple[logging.LogRecord, dict[str, object]]:
    ends = _ends(caplog)
    assert len(ends) == 1, [log_record_fields(r) for r in ends]
    return ends[0], log_record_fields(ends[0])


def _state() -> tuple[object, ...]:
    return dict(current_log_fields()), org_context.peek(), get_context(), ML._current.get()


def _validation_error() -> ValidationError:
    try:
        StreamMessage(payload={"registeredName": SEEDED})  # no eventType
    except ValidationError as e:
        return e
    raise AssertionError("expected a ValidationError")


class TestBinding:
    def test_every_line_of_the_delivery_carries_the_message_and_its_verified_org(self, caplog) -> None:
        before = _state()
        with ML.delivery(log, topic="entity-events", message_id="1-0", attempt=1):
            ML.bind_envelope({"requestId": "req-producer", "orgId": "org_a"})
            ML.bind_opened(ORG, MESSAGE)
            log.info("inside")
            ML.end("ack")
        assert _state() == before
        inside = next(r for r in caplog.records if r.getMessage() == "inside")
        assert "req-producer" in inside.request_id
        fields = log_record_fields(inside)
        assert {k: fields.get(k) for k in (
            "topic", "message_id", "event_type", "record_id", "connector_id", "org_id", "user_id",
        )} == {
            "topic": "entity-events", "message_id": "1-0", "event_type": "orgUpdated", "record_id": "rec-1",
            "connector_id": "conn-1", "org_id": "org_a", "user_id": "user_a",
        }
        _, end = _only_end(caplog)
        assert end["org_id"] == "org_a" and end["event_type"] == "orgUpdated"

    def test_bindings_are_reset_when_the_block_raises(self) -> None:
        before = _state()
        with pytest.raises(ValueError), ML.delivery(log, topic="t", message_id="1-0"):
            ML.bind_envelope({"requestId": "req-producer"})
            ML.bind_opened(ORG, MESSAGE)
            raise ValueError("boom")
        assert _state() == before

    def test_non_string_payload_ids_are_not_bound(self) -> None:
        with ML.delivery(log, topic="t", message_id="1-0"):
            ML.bind_opened(None, {"eventType": 7, "payload": {"recordId": 5, "connectorId": ["x"]}})
            assert not {"event_type", "record_id", "connector_id"} & current_log_fields().keys()
            assert org_context.peek() is None
            ML.end("ack")

    def test_binding_outside_a_delivery_raises_and_note_and_end_do_nothing(self, caplog) -> None:
        with pytest.raises(RuntimeError):
            ML.bind_envelope({"requestId": "r"})
        with pytest.raises(RuntimeError):
            ML.bind_opened(ORG, MESSAGE)
        assert org_context.peek() is None
        ML.note(reason="x")
        ML.end("ack")
        assert caplog.records == []

    def test_binding_after_end_raises(self) -> None:
        with ML.delivery(log, topic="t", message_id="1-0"):
            ML.end("ack")
            with pytest.raises(RuntimeError):
                ML.bind_opened(ORG, MESSAGE)


class TestEnd:
    def test_end_is_written_once_and_says_how_long_the_delivery_took(self, caplog) -> None:
        with ML.delivery(log, topic="t", message_id="1-0", attempt=1):
            ML.end("ack")
            ML.end("retry")
        record, fields = _only_end(caplog)
        assert record.levelno == logging.INFO
        assert fields["outcome"] == "success" and fields["disposition"] == "ack" and fields["attempt"] == 1
        assert isinstance(fields["duration_ms"], int) and fields["duration_ms"] >= 0

    def test_a_noted_skip_is_the_outcome_of_an_ack(self, caplog) -> None:
        with ML.delivery(log, topic="t", message_id="1-0"):
            ML.note(outcome="skipped", reason="duplicate")
            ML.end("ack")
        record, fields = _only_end(caplog)
        assert (record.levelno, fields["outcome"], fields["reason"]) == (logging.INFO, "skipped", "duplicate")

    @pytest.mark.parametrize(("disposition", "exc", "level", "cls"), [
        ("retry", None, logging.WARNING, None),
        ("retry", ConnectionError("graph unavailable"), logging.WARNING, "unavailable"),
        ("retry", KeyError("k"), logging.ERROR, "internal"),
        ("dead_letter", None, logging.ERROR, None),
        ("dead_letter", ConnectionError("graph unavailable"), logging.ERROR, "unavailable"),
    ])
    def test_failure_level(self, caplog, disposition, exc, level, cls) -> None:
        with ML.delivery(log, topic="t", message_id="1-0"):
            ML.note(reason="exception" if exc else "handler_false", exc=exc)
            ML.end(disposition, attempt=2)
        record, fields = _only_end(caplog)
        assert record.levelno == level
        assert fields["outcome"] == "failure" and fields["disposition"] == disposition and fields["attempt"] == 2
        assert fields.get("error_class") == cls

    def test_a_rejected_envelope_is_an_error_with_its_code_and_no_stack(self, caplog) -> None:
        with ML.delivery(log, topic="t", message_id="1-0"):
            ML.note(reason="envelope_rejected", code="bad_signature")
            ML.end("dead_letter")
        record, fields = _only_end(caplog)
        assert record.levelno == logging.ERROR and record.exc_info is None and record.exc_text is None
        assert (fields["reason"], fields["code"]) == ("envelope_rejected", "bad_signature")

    def test_a_failure_the_handler_already_logged_keeps_the_end_level_and_prints_no_second_stack(self, caplog) -> None:
        exc = ConnectionError("graph unavailable")
        error_class.log_failure(log, "entity_event.failed", exc)  # the handler's line, WARNING
        with ML.delivery(log, topic="t", message_id="1-0"):
            ML.note(reason="exception", exc=exc)
            ML.end("dead_letter")
        record, fields = _only_end(caplog)
        assert record.levelno == logging.ERROR and fields["error_class"] == "unavailable"
        assert fields["error_type"] == "ConnectionError"
        exc2 = KeyError("k")
        error_class.log_failure(log, "entity_event.failed", exc2)  # ERROR, with the stack
        caplog.clear()
        with ML.delivery(log, topic="t", message_id="1-1"):
            ML.note(reason="exception", exc=exc2)
            ML.end("retry")
        record, _ = _only_end(caplog)
        assert record.levelno == logging.ERROR and record.exc_info is None and record.exc_text is None

    def test_control_plain_log_failure_would_demote_the_end_line(self, caplog) -> None:
        exc = ConnectionError("graph unavailable")
        error_class.log_failure(log, "entity_event.failed", exc)
        error_class.log_failure(log, ML.EVENT_END, exc)
        assert caplog.records[-1].levelno == logging.DEBUG

    @pytest.mark.parametrize(("reason", "wrapped"), [("unparseable", False), ("exception", False), ("exception", True)])
    def test_validation_error_text_never_reaches_a_line(self, caplog, reason, wrapped) -> None:
        exc = _validation_error()
        assert SEEDED in str(exc)  # control: pydantic quotes the input
        if wrapped:  # a handler's `raise ProcessingError(...) from e`: the chain text would quote it too
            try:
                raise RuntimeError("bad payload") from exc
            except RuntimeError as outer:
                exc = outer
        with ML.delivery(log, topic="t", message_id="1-0"):
            ML.note(reason=reason, exc=exc)
            ML.end("dead_letter")
        record, fields = _only_end(caplog)
        assert fields["error_type"] == type(exc).__name__ and "error_message" not in fields
        assert record.exc_info is None
        text = " ".join([record.getMessage(), str(fields), record.exc_text or ""])
        assert SEEDED not in text


class TestExitWithoutEnd:
    def test_a_normal_exit_is_a_retry_with_the_noted_reason(self, caplog) -> None:
        with ML.delivery(log, topic="t", message_id="1-0", attempt=1):
            ML.note(reason="handler_false")
        record, fields = _only_end(caplog)
        assert record.levelno == logging.WARNING
        assert (fields["outcome"], fields["disposition"], fields["reason"]) == ("failure", "retry", "handler_false")

    def test_an_exit_with_nothing_noted_is_an_exception_retry(self, caplog) -> None:
        with ML.delivery(log, topic="t", message_id="1-0"):
            pass
        _, fields = _only_end(caplog)
        assert (fields["disposition"], fields["reason"]) == ("retry", "exception")

    def test_an_escaping_exception_ends_it_and_propagates(self, caplog) -> None:
        with pytest.raises(ConnectionError), ML.delivery(log, topic="t", message_id="1-0"):
            ML.bind_opened(ORG, MESSAGE)
            raise ConnectionError("graph unavailable")
        record, fields = _only_end(caplog)
        assert (fields["disposition"], fields["reason"], fields["error_class"]) == ("retry", "exception", "unavailable")
        assert fields["org_id"] == "org_a"  # written before the bindings are reset

    def test_cancellation_ends_it_at_info(self, caplog) -> None:
        with pytest.raises(asyncio.CancelledError), ML.delivery(log, topic="t", message_id="1-0"):
            raise asyncio.CancelledError
        record, fields = _only_end(caplog)
        assert record.levelno == logging.INFO
        assert (fields["outcome"], fields["disposition"], fields["reason"], fields["error_class"]) == (
            "failure", "retry", "cancelled", "cancelled",
        )

    def test_an_exception_after_end_writes_no_second_line(self, caplog) -> None:
        with pytest.raises(ValueError), ML.delivery(log, topic="t", message_id="1-0"):
            ML.end("ack")
            raise ValueError("after")
        assert len(_ends(caplog)) == 1


class TestWorkStartedFromADelivery:
    def test_a_copied_context_keeps_the_ids_but_not_the_facts_or_the_end(self, caplog) -> None:
        with ML.delivery(log, topic="sync-events", message_id="1-0"):
            ML.note(reason="exception", exc=RuntimeError("held"))
            copied = contextvars.copy_context()
            ML.end("retry")
        assert copied.run(lambda: current_log_fields()["message_id"]) == "1-0"
        copied.run(ML.note, reason="late")
        copied.run(ML.end, "ack")
        assert len(_ends(caplog)) == 1
        assert copied.run(lambda: ML._current.get().facts) == {}


class TestOnlyTheDecisionSiteEnds:
    async def test_a_task_started_inside_a_delivery_that_notes_does_not_end_it(self, caplog) -> None:
        async def work() -> None:
            ML.note(size_bytes=7)

        with ML.delivery(log, topic="t", message_id="1-0", attempt=1):
            await asyncio.create_task(work())
            assert _ends(caplog) == []
            ML.end("ack")
        record, fields = _only_end(caplog)
        assert (fields["outcome"], fields["disposition"], fields["size_bytes"]) == ("success", "ack", 7)


class TestBoundMessage:
    def test_binds_root_org_and_ids_for_the_block(self) -> None:
        message = StreamMessage(
            eventType="newRecord", payload={"orgId": "org_a", "recordId": "rec-1"}, requestId="req-producer",
        )
        before = _state()
        with ML.bound_message(message):
            assert org_context.peek().org_id == "org_a"
            assert get_context().root_id == "req-producer"
            assert current_log_fields()["record_id"] == "rec-1"
            assert current_log_fields()["event_type"] == "newRecord"
        assert _state() == before

    def test_none_binds_nothing(self) -> None:
        before = _state()
        with ML.bound_message(None):
            assert _state() == before
