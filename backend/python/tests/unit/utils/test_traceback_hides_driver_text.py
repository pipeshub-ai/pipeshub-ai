"""A ``logger.exception`` or ``exc_info=True`` record of a chain that hides its text (a stamped driver error, or a
``PermissionVerificationUnavailableError``) prints the stack ``log_failure`` writes: each exception as
``Type: <message omitted>``, frames kept (docs/logging.md, "Database queries")."""

from __future__ import annotations

import io
import json
import logging
import pickle
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.utils import error_class
from app.utils.logger import JsonFormatter, TextFormatter
from tests.support.graph_read_errors import (
    DRIVER_TEXT,
    SENTINELS,
    marked_internal,
    unmarked,
)
from tests.support.log_capture import assert_text_free

FORMATTERS = {"text": TextFormatter, "json": JsonFormatter, "foreign": logging.Formatter}
PVUE = "PermissionVerificationUnavailableError"


def _raised(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as caught:
        return caught


def _pvue_from(cause: BaseException) -> PermissionVerificationUnavailableError:
    """As the wrap sites raise it: the default message, the driver error as ``__cause__``."""
    try:
        try:
            raise cause
        except Exception as exc:
            raise PermissionVerificationUnavailableError() from exc
    except PermissionVerificationUnavailableError as pvue:
        return pvue


def _record(exc: BaseException, *, via: str = "exception") -> logging.LogRecord:
    """The record ``logger.exception`` (or ``logger.error(..., exc_info=True)``) builds inside ``except exc``."""
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(f"test.traceback_hides_driver_text.{via}")
    logger.propagate, logger.level = False, logging.DEBUG
    handler = _Keep()
    logger.addHandler(handler)
    try:
        try:
            raise exc
        except BaseException:
            if via == "exception":
                logger.exception("tool failed")
            elif via == "warning_exc_info":
                logger.warning("tool failed", exc_info=True)
            else:
                logger.error("tool failed", exc_info=True)
    finally:
        logger.removeHandler(handler)
    (record,) = records
    return record


def _rendered(record: logging.LogRecord, fmt: str) -> str:
    out = FORMATTERS[fmt]().format(record)
    return json.loads(out).get("stack", "") if fmt == "json" else out


def _assert_no_sentinel(text: str) -> None:
    for value in SENTINELS:
        assert value not in text, f"{value!r} was logged: {text!r}"


@pytest.mark.parametrize("fmt", FORMATTERS)
@pytest.mark.parametrize("via", ["exception", "exc_info", "warning_exc_info"])
@pytest.mark.parametrize("make", [marked_internal, unmarked], ids=["stamped_cause", "unstamped_cause"])
def test_a_pvue_prints_no_driver_text_but_keeps_types_and_frames(make, via: str, fmt: str) -> None:
    pvue = _pvue_from(make())
    stack = _rendered(_record(pvue, via=via), fmt)
    _assert_no_sentinel(stack)
    assert f"{PVUE}: <message omitted>" in stack
    assert f"{type(pvue.__cause__).__qualname__}: <message omitted>" in stack
    assert "in _pvue_from" in stack and "The above exception was the direct cause" in stack


@pytest.mark.parametrize("fmt", FORMATTERS)
def test_a_bare_stamped_driver_error_prints_no_driver_text(fmt: str) -> None:
    stack = _rendered(_record(_raised(marked_internal())), fmt)
    _assert_no_sentinel(stack)
    assert "RuntimeError: <message omitted>" in stack


def test_the_record_carries_the_text_free_stack_and_no_exc_info() -> None:
    pvue = _pvue_from(marked_internal())
    record = _record(pvue)
    assert record.exc_info is None
    assert record.exc_text == error_class._stack_text(pvue, with_messages=False)
    assert error_class.record_exception(record) is pvue
    # kept off the record: a filter that rewrites its attributes, or a pickling handler, never carries the exception
    assert all(value is not pvue for value in vars(record).values())
    for value in SENTINELS:
        assert value.encode() not in pickle.dumps(record)


@pytest.mark.parametrize("fmt", FORMATTERS)
def test_control_an_unstamped_error_keeps_its_text_and_exc_info(fmt: str) -> None:
    exc = _raised(ValueError("our own message"))
    record = _record(exc)
    assert record.exc_info is not None and record.exc_info[1] is exc
    assert error_class.record_exception(record) is exc
    assert "ValueError: our own message" in _rendered(record, fmt)


def test_logger_exception_outside_an_except_is_left_alone() -> None:
    logger = logging.getLogger("test.traceback_hides_driver_text.none")
    record = logger.makeRecord(logger.name, logging.ERROR, __file__, 1, "x", (), (None, None, None))
    assert record.exc_info == (None, None, None) and record.exc_text is None
    assert error_class.record_exception(record) is None


def test_a_stamped_context_is_hidden_and_from_none_keeps_the_outer_message() -> None:
    def during_handling() -> BaseException:
        try:
            raise marked_internal()
        except RuntimeError:
            try:
                raise KeyError("our bug")
            except KeyError as exc:
                return exc

    hidden = _rendered(_record(during_handling()), "text")
    _assert_no_sentinel(hidden)
    assert "KeyError: <message omitted>" in hidden and "During handling of the above exception" in hidden

    def from_none() -> BaseException:
        try:
            raise marked_internal()
        except RuntimeError:
            try:
                raise ValueError("our own hint") from None
            except ValueError as exc:
                return exc

    kept = _rendered(_record(from_none()), "text")
    _assert_no_sentinel(kept)
    assert "ValueError: our own hint" in kept


def _member_raising() -> RuntimeError:
    def boom() -> None:
        raise marked_internal()

    try:
        boom()
    except RuntimeError as exc:
        return exc
    raise AssertionError("unreachable")


def test_a_group_with_a_stamped_member_keeps_every_member_type_and_frame() -> None:
    group = _raised(ExceptionGroup("reads", [ValueError("clean member"), _member_raising()]))
    stack = _rendered(_record(group), "text")
    _assert_no_sentinel(stack)
    assert "ExceptionGroup: <message omitted>" in stack
    assert "+---------------- 1 ----------------\n| ValueError: <message omitted>" in stack
    assert "+---------------- 2 ----------------" in stack
    assert "| RuntimeError: <message omitted>" in stack and "in boom" in stack


def test_a_member_whose_cause_is_its_group_is_rendered_once() -> None:
    member = ConnectionError(DRIVER_TEXT)
    member.log_hide_text = True
    group = ExceptionGroup("batch", [member])
    member.__cause__ = group
    stack = error_class._stack_without_messages(group)
    assert stack.count("ExceptionGroup: <message omitted>") == 1
    assert stack.count("ConnectionError: <message omitted>") == 1


def test_a_wide_group_stops_at_fifteen_members_as_python_does() -> None:
    stamped = marked_internal()
    group = ExceptionGroup("many", [stamped, *(ValueError(f"v{i}") for i in range(19))])
    stack = error_class._stack_without_messages(group)
    assert "+---------------- 15 ----------------" in stack
    assert "+---------------- 16 ----------------" not in stack
    assert "| and 5 more exceptions" in stack


def test_a_deep_group_stops_at_ten_levels_as_python_does() -> None:
    group: BaseExceptionGroup = ExceptionGroup("leaf", [marked_internal()])
    for level in range(12):
        group = ExceptionGroup(f"level {level}", [group])
    stack = error_class._stack_without_messages(group)
    assert "... (max_group_depth is 10)" in stack
    _assert_no_sentinel(stack)


def test_a_failing_hide_check_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_: BaseException) -> bool:
        raise RuntimeError("stamp unreadable")

    monkeypatch.setattr(error_class, "_hides_text", broken)
    pvue = _pvue_from(marked_internal())
    record = _record(pvue)
    assert record.exc_info is None and f"{PVUE}: <message omitted>" in record.exc_text
    assert error_class.record_exception(record) is pvue
    _assert_no_sentinel(_rendered(record, "text"))


def test_a_link_whose_attributes_raise_does_not_reveal_a_stamped_link_below_it() -> None:
    """The stamp walk stops at the link that raises, before the stamped cause further down."""
    class Odd(Exception):
        def __getattr__(self, name: str) -> object:
            raise KeyError(name)

    try:
        raise Odd("outer") from _pvue_from(marked_internal())
    except Odd as odd:
        stack = _rendered(_record(odd), "text")
    _assert_no_sentinel(stack)
    assert "Odd: <message omitted>" in stack


def test_a_failing_stack_build_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_: BaseException, *, with_messages: bool) -> str:
        raise RuntimeError("odd traceback")

    monkeypatch.setattr(error_class, "_stack_text", broken)
    pvue = _pvue_from(marked_internal())
    record = _record(pvue)
    assert record.exc_info is None
    assert record.exc_text == f"{PVUE}: <stack omitted>"
    assert error_class.record_exception(record) is pvue
    _assert_no_sentinel(_rendered(record, "text"))


@pytest.mark.parametrize("fmt", FORMATTERS)
def test_a_failing_redactor_import_fails_closed(monkeypatch: pytest.MonkeyPatch, fmt: str) -> None:
    monkeypatch.delattr(error_class, "hide_record_text")  # the factory's lazy import now raises ImportError
    record = _record(_pvue_from(marked_internal()))
    assert record.exc_info is None
    assert record.exc_text == f"{PVUE}: <stack omitted>"
    _assert_no_sentinel(_rendered(record, fmt))


# --- the handler sites that log a PVUE with logger.exception ---------------------------------------------------------


async def _sql_tool(caplog: pytest.LogCaptureFixture) -> None:
    from app.utils.execute_query import create_execute_query_tool

    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"id": "conn-1", "orgId": "org-1"})
    graph.get_user_by_user_id = AsyncMock(side_effect=ConnectionError(DRIVER_TEXT))  # behind accessible_node_ids
    with patch("app.utils.execute_query._execute_query_impl", new_callable=AsyncMock) as impl:
        tool = create_execute_query_tool(
            config_service=MagicMock(), graph_provider=graph, org_id="org-1", user_id="u1",
            allowed_connector_ids=frozenset({"conn-1"}),
        )
        result = await tool.ainvoke({"query": "SELECT 1", "source_name": "PostgreSQL", "connector_id": "conn-1"})
    impl.assert_not_called()
    assert result["ok"] is False


async def _slack_thread_tool(caplog: pytest.LogCaptureFixture) -> None:
    from app.utils import fetch_slack_thread as fst

    with patch.object(fst, "_fetch_thread_records_impl", AsyncMock(side_effect=_pvue_from(unmarked()))):
        tool = fst.create_fetch_slack_thread_tool({}, org_id="org-1", graph_provider=AsyncMock(), blob_store=MagicMock())
        result = await tool.ainvoke({"record_id": "rec-1"})
    assert result["ok"] is False


async def _slack_nearby_tool(caplog: pytest.LogCaptureFixture) -> None:
    from app.utils import fetch_slack_nearby_messages as fsn

    # the anchor is a Slack message the user may read, so the call reaches the read that fails
    graph = AsyncMock()
    graph.get_record_by_id = AsyncMock(return_value={
        "id": "rec-1", "org_id": "org-1", "connector_name": "SLACK", "connector_id": "conn-1", "external_record_group_id": "C123",
    })
    gate = MagicMock()
    gate.decide = AsyncMock(return_value="own")
    with patch.object(fsn, "_fetch_nearby_messages_impl", AsyncMock(side_effect=_pvue_from(unmarked()))), \
            patch.object(fsn, "RecordReadGate", MagicMock(return_value=gate)):
        tool = fsn.create_fetch_slack_nearby_messages_tool(
            config_service=MagicMock(), graph_provider=graph, org_id="org-1", user_id="u1",
        )
        result = await tool.ainvoke({
            "record_id": "rec-1", "timestamp": "2023-11-14T22:13:20Z", "direction": "before",
        })
    assert result.ok is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("drive", "message"), [
    (_sql_tool, "execute_sql_query_tool failed"),
    (_slack_thread_tool, "fetch_slack_thread_tool failed"),
    (_slack_nearby_tool, "fetch_slack_nearby_messages_tool failed"),
])
async def test_a_tool_that_logs_a_pvue_with_logger_exception_prints_no_driver_text(
    caplog: pytest.LogCaptureFixture, drive, message: str,
) -> None:
    with caplog.at_level(logging.DEBUG):
        await drive(caplog)
    lines = [r for r in caplog.records if r.getMessage() == message]
    assert len(lines) == 1, [r.getMessage() for r in caplog.records]
    assert_text_free(lines, *SENTINELS)
    assert f"{PVUE}: <message omitted>" in lines[0].exc_text
    _assert_no_sentinel(caplog.text)


def test_log_failure_of_a_pvue_over_an_unstamped_cause_leaves_the_text_out() -> None:
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("test.traceback_hides_driver_text.log_failure")
    logger.propagate, logger.level = False, logging.DEBUG
    logger.addHandler(handler := _Keep())
    pvue = _pvue_from(unmarked())
    try:
        error_class.log_failure(logger, "tool.failed", pvue, level=logging.ERROR)
    finally:
        logger.removeHandler(handler)
    (record,) = records
    assert getattr(record, "error_message", None) is None
    assert record.exc_info is None and f"{PVUE}: <message omitted>" in record.exc_text
    assert error_class.record_exception(record) is pvue
    _assert_no_sentinel(TextFormatter().format(record))


def test_a_text_free_stack_written_to_a_stream_holds_no_driver_text() -> None:
    """End to end through a real StreamHandler, as the console handler writes."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(TextFormatter())
    logger = logging.getLogger("test.traceback_hides_driver_text.stream")
    logger.propagate = False
    logger.addHandler(handler)
    try:
        try:
            raise _pvue_from(marked_internal())
        except PermissionVerificationUnavailableError:
            logger.exception("tool failed")
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert "tool failed" in out and f"{PVUE}: <message omitted>" in out
    _assert_no_sentinel(out)
