"""tests/support/log_capture.py: personal values and error text are found wherever a log line carries them."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from app.utils.error_class import log_failure
from app.utils.logging_fields import log_fields
from tests.support.log_capture import (
    assert_not_logged,
    assert_not_logged_mock,
    assert_text_free,
    failure_line,
)

EMAIL = "alice.liddell@example.test"
LOGGER = "test.log_capture_support"


@pytest.fixture
def caplog_debug(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    return caplog


log = logging.getLogger(LOGGER)


class TestAssertNotLogged:
    def test_clean_lines_pass(self, caplog_debug) -> None:
        log.info("user.synced %s", "u-0001", extra={"target_user_id": "u-0001"})
        assert_not_logged(caplog_debug.records, EMAIL, "Alice", "Liddell")

    @pytest.mark.parametrize("emit", [
        lambda: log.info("sync for %s", EMAIL),
        lambda: log.info("user.synced", extra={"connector_id": EMAIL}),
        lambda: log.debug("Sync for %s", EMAIL.upper()),
        lambda: log.info("user.synced", extra={EMAIL: "done"}),
    ], ids=["message", "extra-field", "case", "extra-field-name"])
    def test_a_value_in_the_message_or_a_field_fails(self, caplog_debug, emit) -> None:
        emit()
        with pytest.raises(AssertionError, match="was logged"):
            assert_not_logged(caplog_debug.records, EMAIL)

    def test_a_value_in_a_bound_field_fails(self, caplog_debug) -> None:
        with log_fields(source_user_id=EMAIL):
            log.info("user.synced")
        with pytest.raises(AssertionError, match="was logged"):
            assert_not_logged(caplog_debug.records, EMAIL)

    def test_a_value_in_the_traceback_fails(self, caplog_debug) -> None:
        try:
            raise ValueError(f"Email already in use: {EMAIL}")
        except ValueError:
            log.exception("user.update.failed")
        with pytest.raises(AssertionError, match="was logged"):
            assert_not_logged(caplog_debug.records, EMAIL)

    def test_names_are_checked_separately(self, caplog_debug) -> None:
        log.info("owner Liddell")
        with pytest.raises(AssertionError, match="Liddell"):
            assert_not_logged(caplog_debug.records, "Alice Liddell", "Alice", "Liddell")

    def test_nothing_captured_fails(self, caplog_debug) -> None:
        with pytest.raises(AssertionError, match="no log records"):
            assert_not_logged(caplog_debug.records, EMAIL)

    def test_a_value_only_in_the_stack_text_fails(self, caplog_debug) -> None:
        _handle_with_stack_text(f"ValueError: Email already in use: {EMAIL}")
        with pytest.raises(AssertionError, match="was logged"):
            assert_not_logged(caplog_debug.records, EMAIL)


class TestAssertNotLoggedMock:
    def test_clean_calls_pass(self) -> None:
        logger = MagicMock()
        logger.info("user.synced", extra={"target_user_id": "u-0001"})
        assert_not_logged_mock(logger, EMAIL)

    def test_a_value_in_args_extra_or_an_exception_fails(self) -> None:
        for call in (lambda m: m.warning("no user for %s", EMAIL),
                     lambda m: m.info("x", extra={"k": EMAIL}),
                     lambda m: m.error("failed: %s", ValueError(EMAIL)),
                     lambda m: m.error("failed", exc_info=ValueError(EMAIL)),
                     lambda m: m.info("x", extra={EMAIL: "done"})):
            logger = MagicMock()
            call(logger)
            with pytest.raises(AssertionError, match="was logged"):
                assert_not_logged_mock(logger, EMAIL)

    def test_a_value_that_repr_would_escape_fails(self) -> None:
        # repr('Renamed "O'Connor"') escapes the apostrophe, so the raw strings are searched, nested ones included.
        for call in (lambda m: m.info('Renamed "O\'Connor"'),
                     lambda m: m.info("x", extra={"names": ['Renamed "O\'Connor"']})):
            logger = MagicMock()
            call(logger)
            with pytest.raises(AssertionError, match="was logged"):
                assert_not_logged_mock(logger, "O'Connor")

    def test_a_container_that_holds_itself_is_still_checked(self) -> None:
        # Logging formats a self-referencing dict; the search must stop at the cycle, not recurse until RecursionError.
        clean: dict[str, object] = {"id": "u-1"}
        clean["self"] = clean
        leaky: list[object] = ["Renamed \"O'Connor\""]
        leaky.append(leaky)
        logger = MagicMock()
        logger.info("x", extra={"payload": clean})
        assert_not_logged_mock(logger, "O'Connor")
        logger.info("y", extra={"names": leaky})
        with pytest.raises(AssertionError, match="was logged"):
            assert_not_logged_mock(logger, "O'Connor")

    def test_only_log_level_calls_count(self) -> None:
        logger = MagicMock()
        logger.isEnabledFor(logging.DEBUG)
        logger.getChild("x")
        with pytest.raises(AssertionError, match="no log-level calls"):
            assert_not_logged_mock(logger, EMAIL)

    def test_a_non_log_call_with_the_value_is_not_a_log_line(self) -> None:
        logger = MagicMock()
        logger.info("user.synced")
        logger.getChild(EMAIL)
        assert_not_logged_mock(logger, EMAIL)


def _handle_with_stack_text(exc_text: str) -> None:
    """A record whose stack is only ``exc_text``, with no ``exc_info``: how log_failure writes a text-free stack."""
    record = log.makeRecord(LOGGER, logging.ERROR, __file__, 1, "x.failed", (), None)
    record.exc_text = exc_text
    log.handle(record)


def _failure(
    *, error_class: str = "unavailable", text: bool = False, message: str = f"refused for {EMAIL}", warn: str | None = None,
    **fields: object,
) -> None:
    """The handler: one ``x.failed`` log_failure line for a raised error (its stack is written when the class is ERROR), after a plain
    ``warn`` line when given."""
    if warn:
        log.warning(warn)
    try:
        raise ConnectionError(message)
    except ConnectionError as exc:
        log_failure(log, "x.failed", exc, error_class=error_class, include_error_text=text, **fields)


class TestAssertTextFree:
    @pytest.mark.parametrize("error_class", ["unavailable", "internal"], ids=["warning", "error-with-stack"])
    def test_a_text_free_failure_line_passes(self, caplog_debug, error_class) -> None:
        _failure(error_class=error_class)
        assert_text_free(caplog_debug.records, EMAIL)
        assert caplog_debug.records[0].exc_info is None

    def test_the_error_text_field_fails_even_without_a_value(self, caplog_debug) -> None:
        _failure(text=True, message="driver said no")
        with pytest.raises(AssertionError, match=r"error text was logged: error_message on \['x.failed'\]"):
            assert_text_free(caplog_debug.records, EMAIL)

    @pytest.mark.parametrize("emit", [
        lambda: log.warning("x.failed for %s", EMAIL),
        lambda: log.warning("x.failed", extra={"detail": EMAIL}),
        lambda: _handle_with_stack_text(f"ConnectionError: {EMAIL}"),
        lambda: log.warning("x.failed for %s", EMAIL.upper()),
    ], ids=["message", "field", "stack-text", "case"])
    def test_a_value_anywhere_on_the_line_fails(self, caplog_debug, emit) -> None:
        emit()
        with pytest.raises(AssertionError, match="was logged"):
            assert_text_free(caplog_debug.records, EMAIL)

    def test_nothing_captured_fails(self, caplog_debug) -> None:
        with pytest.raises(AssertionError, match="no log records"):
            assert_text_free(caplog_debug.records, EMAIL)


class TestFailureLine:
    def test_returns_the_fields_of_the_one_line(self, caplog_debug) -> None:
        log.info("x.started")
        _failure(record_id="r-1")
        fields = failure_line(caplog_debug.records, "x.failed", "unavailable", logging.WARNING, EMAIL)
        assert (fields["record_id"], fields["error_type"], fields["outcome"]) == ("r-1", "ConnectionError", "failure")

    @pytest.mark.parametrize(("emit", "error_class", "level", "reason"), [
        (lambda: log.warning("x.other"), "unavailable", logging.WARNING, "expected one 'x.failed' line, got 0"),
        (lambda: (_failure(), _failure()), "unavailable", logging.WARNING, "expected one 'x.failed' line, got 2"),
        (_failure, "timeout", logging.WARNING, "is \\(outcome, class, level\\)"),
        (_failure, "unavailable", logging.ERROR, "is \\(outcome, class, level\\)"),
        (lambda: _failure(error_class="internal", text=True), "internal", logging.ERROR, "carries exc_info"),
        (lambda: _failure(text=True, message="driver said no"), "unavailable", logging.WARNING, "error text was logged"),
        (lambda: _failure(detail=EMAIL), "unavailable", logging.WARNING, "was logged"),
    ], ids=["no-line", "two-lines", "class", "level", "exc_info", "error_message", "value"])
    def test_a_line_that_is_not_one_text_free_failure_fails(self, caplog_debug, emit, error_class, level, reason) -> None:
        emit()
        with pytest.raises(AssertionError, match=reason):
            failure_line(caplog_debug.records, "x.failed", error_class, level, EMAIL)

    def test_the_handler_check_passes_when_the_handler_logged_only_the_failure(self, caplog_debug) -> None:
        log.warning("x.slow")  # another function's line
        log.debug("x.retry")  # a line that carries no error
        _failure()
        failure_line(caplog_debug.records, "x.failed", "unavailable", logging.WARNING, EMAIL, handler="_failure")

    @pytest.mark.parametrize(("emit", "reason"), [
        (lambda: (log.debug("x.retry", extra={"error_type": "ConnectionError"}), _failure()), "records carrying an error"),
        (lambda: (log.debug("x.retry", extra={"error_message": "driver said no"}), _failure()), "records carrying an error"),
        (lambda: _failure(warn="x.degraded"), "_failure wrote WARNING-or-above"),
    ], ids=["another-error-line", "another-error-text-line", "another-warning-of-the-handler"])
    def test_the_handler_check_is_opt_in(self, caplog_debug, emit, reason) -> None:
        emit()
        failure_line(caplog_debug.records, "x.failed", "unavailable", logging.WARNING, EMAIL)
        with pytest.raises(AssertionError, match=reason):
            failure_line(caplog_debug.records, "x.failed", "unavailable", logging.WARNING, EMAIL, handler="_failure")
