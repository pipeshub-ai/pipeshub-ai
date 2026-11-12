"""tests/support/log_capture.py: personal values are found wherever a log line carries them."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from app.utils.logging_fields import log_fields
from tests.support.log_capture import assert_not_logged, assert_not_logged_mock

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
