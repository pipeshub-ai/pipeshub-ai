"""run_periodic: one root, one context and one job.tick.end per tick."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
import pytest

from app.core import org_context as oc
from app.core.periodic import run_periodic
from app.utils import request_context as rc
from app.utils.logger import log_record_fields
from app.utils.logging_fields import bind_log_fields, current_log_fields

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def jobs_log() -> Iterator[list[logging.LogRecord]]:
    logger = logging.getLogger("app.jobs")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler.records
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)


def _run(fns: list, *, under_org: bool = False) -> None:
    """Run the loop until every fn ran once; the last one cancels the loop."""
    calls = iter(fns)

    async def tick() -> None:
        fn = next(calls, None)
        if fn is None:
            raise asyncio.CancelledError
        await fn()

    async def main() -> None:
        task = oc.spawn_detached(run_periodic("kh-keeper", 0, tick), name="kh-keeper")
        with pytest.raises(asyncio.CancelledError):
            await task

    if under_org:
        async def outer() -> None:
            with oc.for_org("o1", job="parent"):
                await main()
        asyncio.run(outer())
    else:
        asyncio.run(main())


def _ticks(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if r.getMessage() == "job.tick.end"]


def test_each_tick_has_its_own_root_and_job_field(jobs_log) -> None:
    seen: list[tuple[str, dict]] = []

    async def fn() -> None:
        seen.append((rc.get_context().root_id, dict(current_log_fields())))

    _run([fn, fn])
    (root_a, fields_a), (root_b, _) = seen
    assert root_a != root_b and root_a.startswith("sys-kh-keeper-")
    assert fields_a == {"job": "kh-keeper"}
    ok = _ticks(jobs_log)
    assert [r.levelno for r in ok] == [logging.DEBUG, logging.DEBUG]
    fields = log_record_fields(ok[0])
    assert fields["outcome"] == "success" and fields["job"] == "kh-keeper" and isinstance(fields["duration_ms"], int)


def test_a_tick_bind_does_not_reach_the_next(jobs_log) -> None:
    seen: list[dict] = []

    async def binds() -> None:
        bind_log_fields(run_id="r1")

    async def reads() -> None:
        seen.append(dict(current_log_fields()))

    _run([binds, reads])
    assert seen == [{"job": "kh-keeper"}]


def test_started_under_an_org_ticks_have_none(jobs_log) -> None:
    seen: list[object] = []

    async def fn() -> None:
        seen.append((oc.peek(), rc.get_context().org_id))

    _run([fn], under_org=True)
    assert seen == [(None, None)]


def test_failures_log_by_class_and_the_loop_continues(jobs_log) -> None:
    request = httpx.Request("GET", "https://u.example")

    async def upstream() -> None:
        raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(503, request=request))

    async def bug() -> None:
        raise KeyError("k")

    ran: list[int] = []

    async def fine() -> None:
        ran.append(1)

    _run([upstream, bug, fine])
    warn, error, ok = _ticks(jobs_log)
    assert warn.levelno == logging.WARNING and warn.exc_info is None
    assert log_record_fields(warn)["error_class"] == "unavailable"
    assert error.levelno == logging.ERROR and error.exc_info is not None
    assert ok.levelno == logging.DEBUG and ran == [1]


def test_cancel_propagates_without_a_failure_line(jobs_log) -> None:
    _run([])
    assert _ticks(jobs_log) == []


@pytest.mark.parametrize("fails", [False, True])
def test_a_tick_awaited_in_place_leaves_the_callers_context_as_it_was(jobs_log, fails: bool) -> None:
    from app.core.periodic import _tick

    async def fn() -> None:
        if fails:
            raise KeyError("k")

    async def main() -> tuple[object, dict]:
        token = rc.set_context("caller-root")
        try:
            with oc.for_org("o1", job="parent"):
                before = (rc.get_context(), dict(current_log_fields()))
                await _tick("kh-keeper", fn)
                return before, (rc.get_context(), dict(current_log_fields()))
        finally:
            rc.reset_context(token)

    before, after = asyncio.run(main())
    assert after == before
