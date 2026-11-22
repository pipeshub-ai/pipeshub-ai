"""db.query.failed / db.query.slow: one record per client call, no driver text, caller and location from the provider frame."""
from __future__ import annotations

import asyncio
import logging
import types
from typing import TYPE_CHECKING

import pytest

from app.utils import db_query_log as dql
from app.utils import error_class as EC
from app.utils.logger import TextFormatter, log_record_fields

from . import _db_query_log_client as fake

if TYPE_CHECKING:
    from collections.abc import Iterator

SECRET = "alice@example.com"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, list[logging.LogRecord]]]:
    logger = logging.getLogger("test.db_query_log")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, handler.records
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


class DriverError(Exception):
    def __init__(self, cls: str, code: object = 1210, hide: bool = True) -> None:
        super().__init__(f"duplicate value {SECRET}")
        self.cls, self.code, self.hide = cls, code, hide


def _behaviour(exc: BaseException | None = None, result: str = "ok"):  # noqa: ANN202
    async def go() -> str:
        if exc is not None:
            raise exc
        return result
    return go


async def provider_method(client: fake.FakeClient) -> str:
    return await client.run()  # the line the record points at


PROVIDER_LINE = provider_method.__code__.co_firstlineno + 1


async def provider_with_a_timeout(client: fake.FakeClient) -> str:
    return await asyncio.wait_for(client.run(), 5)  # asyncio.tasks.wait_for's frame sits between this line and the client


TIMEOUT_LINE = provider_with_a_timeout.__code__.co_firstlineno + 1


def _clock(monkeypatch: pytest.MonkeyPatch, *values: float) -> None:
    ticks = iter(values)
    monkeypatch.setattr(dql, "time", types.SimpleNamespace(perf_counter=lambda: next(ticks)))


class TestFailed:
    @pytest.mark.parametrize(("cls", "level"), [("conflict", logging.WARNING), ("internal", logging.ERROR),
                                                 ("misconfigured", logging.ERROR), ("bad_request", logging.WARNING)])
    def test_one_record_at_its_class_level(self, capture, cls: str, level: int) -> None:
        logger, records = capture
        exc = DriverError(cls)
        with pytest.raises(DriverError) as raised:
            asyncio.run(provider_method(fake.FakeClient(logger, _behaviour(exc))))
        assert raised.value is exc and str(raised.value) == f"duplicate value {SECRET}"
        (record,) = records
        assert record.levelno == level and record.getMessage() == "db.query.failed"
        assert record.exc_info is None and record.exc_text is None
        fields = log_record_fields(record)
        assert fields["backend"] == "fakedb" and fields["op"] == "run" and fields["code"] == 1210
        assert fields["error_class"] == cls and fields["error_type"] == "DriverError" and fields["outcome"] == "failure"
        assert fields["caller"] == "test_db_query_log.provider_method" and isinstance(fields["duration_ms"], int)
        assert "error_message" not in fields
        assert SECRET not in TextFormatter().format(record)

    def test_location_is_the_provider_line(self, capture) -> None:
        logger, records = capture
        with pytest.raises(DriverError):
            asyncio.run(provider_method(fake.FakeClient(logger, _behaviour(DriverError("conflict")))))
        (record,) = records
        assert record.filename == "test_db_query_log.py" and record.lineno == PROVIDER_LINE
        assert record.funcName == "provider_method"

    def test_stamps_the_exception_for_the_handler(self, capture) -> None:
        logger, records = capture
        exc = DriverError("conflict")
        with pytest.raises(DriverError):
            asyncio.run(provider_method(fake.FakeClient(logger, _behaviour(exc))))
        assert EC.classify_error(exc) == "conflict"
        records.clear()
        EC.log_failure(logger, "sync_run.end", exc)  # the handler line: right class, no driver text
        (handler_line,) = records
        assert handler_line.levelno == logging.WARNING and getattr(handler_line, "error_message", None) is None

    def test_own_text_can_stay_visible(self, capture) -> None:
        logger, records = capture
        exc = DriverError("misconfigured", hide=False)
        with pytest.raises(DriverError):
            asyncio.run(provider_method(fake.FakeClient(logger, _behaviour(exc))))
        records.clear()
        EC.log_failure(logger, "sync_run.end", exc)
        assert "duplicate value" in records[0].error_message

    def test_stamps_even_when_the_level_is_off(self, capture) -> None:
        logger, records = capture
        logger.setLevel(logging.CRITICAL)
        exc = DriverError("conflict")
        with pytest.raises(DriverError):
            asyncio.run(provider_method(fake.FakeClient(logger, _behaviour(exc))))
        assert not records and EC.classify_error(exc) == "conflict"

    def test_nested_calls_write_one_record_and_keep_the_inner_class(self, capture) -> None:
        logger, records = capture
        exc = DriverError("conflict")
        with pytest.raises(DriverError):
            asyncio.run(fake.FakeClient(logger, _behaviour(exc)).outer())
        (record,) = records
        assert log_record_fields(record)["op"] == "run" and EC.classify_error(exc) == "conflict"

    def test_describe_that_raises_still_writes_internal(self, capture) -> None:
        logger, records = capture

        def broken(exc: Exception) -> tuple:
            raise TypeError("bug in describe")

        with pytest.raises(KeyError):
            asyncio.run(_raise_through(logger, broken))
        (record,) = records
        assert log_record_fields(record)["error_class"] == "internal"

    def test_cancellation_writes_nothing(self, capture) -> None:
        logger, records = capture
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(provider_method(fake.FakeClient(logger, _behaviour(asyncio.CancelledError()))))
        assert records == []

    def test_no_caller_outside_the_client(self, capture) -> None:
        logger, records = capture
        exc = DriverError("conflict")
        dql.query_ended(logger, "fakedb", "run", 0.0, exc, describe=fake.describe, skip=("",))  # every frame is "client"
        (record,) = records
        assert log_record_fields(record)["caller"] == "unknown" and record.filename == "test_db_query_log.py"

    def test_caller_from_the_context_when_the_stack_has_none(self, capture) -> None:
        logger, records = capture
        import sys

        def origin() -> object:
            return sys._getframe()

        token = dql.CALLER_FRAME.set(origin())
        try:
            dql.query_ended(logger, "fakedb", "run", 0.0, DriverError("conflict"), describe=fake.describe, skip=("",))  # every frame is "client"
        finally:
            dql.CALLER_FRAME.reset(token)
        (record,) = records
        assert log_record_fields(record)["caller"].endswith(".origin") and record.funcName == "origin"


async def _raise_through(logger: logging.Logger, describe) -> None:
    @dql.query_logged("fakedb", "run", describe=describe, skip=())
    async def run(self: object) -> None:
        raise KeyError("k")

    await run(types.SimpleNamespace(logger=logger))


class TestSlow:
    def test_threshold_boundary(self, capture, monkeypatch: pytest.MonkeyPatch) -> None:
        logger, records = capture
        monkeypatch.setenv("DB_SLOW_QUERY_MS", "500")
        _clock(monkeypatch, 0.0, 0.499, 0.499)
        asyncio.run(provider_method(fake.FakeClient(logger, _behaviour())))
        assert records == []
        _clock(monkeypatch, 0.0, 0.5, 0.5)
        asyncio.run(provider_method(fake.FakeClient(logger, _behaviour())))
        (record,) = records
        fields = log_record_fields(record)
        assert record.levelno == logging.WARNING and record.getMessage() == "db.query.slow"
        assert fields["threshold_ms"] == 500 and fields["duration_ms"] == 500 and "error_class" not in fields
        assert fields["caller"] == "test_db_query_log.provider_method" and record.lineno == PROVIDER_LINE

    def test_off(self, capture, monkeypatch: pytest.MonkeyPatch) -> None:
        logger, records = capture
        monkeypatch.setenv("DB_SLOW_QUERY_MS", "0")
        _clock(monkeypatch, 0.0, 60.0)
        asyncio.run(provider_method(fake.FakeClient(logger, _behaviour())))
        assert records == []

    def test_fast_success_does_not_call_query_ended(self, capture, monkeypatch: pytest.MonkeyPatch) -> None:
        logger, _ = capture
        calls: list[object] = []
        monkeypatch.setattr(dql, "query_ended", lambda *a, **k: calls.append(a))
        assert asyncio.run(provider_method(fake.FakeClient(logger, _behaviour(result="rows")))) == "rows"
        assert calls == []


@pytest.mark.parametrize(("environ", "expected"), [
    ({}, 500), ({"DB_SLOW_QUERY_MS": "250"}, 250), ({"DB_SLOW_QUERY_MS": "0"}, 0),
    ({"DB_SLOW_QUERY_MS": "abc"}, 500), ({"DB_SLOW_QUERY_MS": "-5"}, 500), ({"DB_SLOW_QUERY_MS": ""}, 500),
])
def test_threshold(environ: dict[str, str], expected: int) -> None:
    assert dql._threshold(environ) == expected


class TestCallerThroughAsyncio:
    def test_wait_for_is_walked_through(self, capture) -> None:
        logger, records = capture
        with pytest.raises(DriverError):
            asyncio.run(provider_with_a_timeout(fake.FakeClient(logger, _behaviour(DriverError("conflict")))))
        (record,) = records
        assert log_record_fields(record)["caller"] == "test_db_query_log.provider_with_a_timeout"
        assert record.filename == "test_db_query_log.py" and record.lineno == TIMEOUT_LINE

    def test_the_loop_is_the_end_of_a_tasks_stack(self, capture) -> None:
        logger, records = capture

        async def main() -> None:
            # Run in a task of its own: past the task's coroutine is the loop (asyncio.events), not a caller.
            await asyncio.get_running_loop().create_task(fake.FakeClient(logger, _behaviour(DriverError("conflict"))).run())

        with pytest.raises(DriverError):
            asyncio.run(main())
        (record,) = records
        assert log_record_fields(record)["caller"] == "unknown"


def _origin_of_my_caller(skip: tuple[str, ...]) -> object:
    return dql.caller_origin(skip)


def test_caller_origin_is_the_callers_caller() -> None:
    def provider() -> object:
        return _origin_of_my_caller(("tests.unit.utils.no_such_module",))

    assert provider().f_code.co_name == "provider"


def test_caller_origin_falls_back_to_the_inherited_one() -> None:
    import sys

    inherited = sys._getframe()
    token = dql.CALLER_FRAME.set(inherited)
    try:
        assert _origin_of_my_caller(("",)) is inherited  # every frame skipped: the inherited origin
    finally:
        dql.CALLER_FRAME.reset(token)
