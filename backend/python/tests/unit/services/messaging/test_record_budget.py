"""The record budget the consumer arms only grows, on the loop that owns it, to what the handler's resolved labels get (F23g)."""
from __future__ import annotations

import asyncio
import threading
from logging import getLogger

import pytest

from app.services.messaging import record_budget
from app.services.messaging.config import messaging_env
from app.services.messaging.worker_loop import WorkerLoop

LOG = getLogger("record-budget-test")


async def test_it_grows_by_the_difference_from_the_same_start() -> None:
    async with asyncio.timeout(100) as timeout, record_budget.armed(timeout, 100) as budget:
        before = timeout.when()
        budget.resize(3700)
        assert timeout.when() == pytest.approx(before + 3600)
        assert budget.seconds == 3700


@pytest.mark.parametrize("seconds", [100, 40, 0])
async def test_a_figure_no_larger_moves_nothing(seconds: float) -> None:
    async with asyncio.timeout(100) as timeout, record_budget.armed(timeout, 100) as budget:
        before = timeout.when()
        budget.resize(seconds)
        assert (timeout.when(), budget.seconds) == (before, 100)


async def test_a_paused_budget_is_not_moved() -> None:
    async with asyncio.timeout(100) as timeout, record_budget.armed(timeout, 100) as budget:
        timeout.reschedule(None)
        budget.resize(3700)
        assert (timeout.when(), budget.seconds) == (None, 100)


async def test_a_budget_already_left_is_not_moved_and_does_not_raise() -> None:
    async with asyncio.timeout(100) as timeout, record_budget.armed(timeout, 100) as budget:
        pass
    budget.resize(3700)
    assert budget.seconds == 100


async def test_a_budget_that_fired_is_not_moved_and_does_not_raise() -> None:
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0) as timeout, record_budget.armed(timeout, 0) as budget:
            await asyncio.sleep(1)
    budget.resize(3700)
    assert budget.seconds == 0


async def test_resize_to_uses_the_record_budget_of_the_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "100")
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "300")
    async with asyncio.timeout(100) as timeout, record_budget.armed(timeout, 100) as budget:
        record_budget.resize_to("unknown", "text/plain")
        assert budget.seconds == 100
        record_budget.resize_to("unknown", "application/pdf")
        assert budget.seconds == messaging_env.pdf_record_processing_timeout == 300


async def test_nothing_armed_is_a_no_op_and_arming_resets_on_exit() -> None:
    record_budget.resize_to("pdf", "application/pdf")
    assert record_budget.current() is None
    async with asyncio.timeout(100) as outer_timeout, record_budget.armed(outer_timeout, 100) as outer:
        async with asyncio.timeout(50) as inner_timeout, record_budget.armed(inner_timeout, 50) as inner:
            assert record_budget.current() is inner
        assert record_budget.current() is outer
    assert record_budget.current() is None


async def test_a_resize_from_another_loop_is_applied_on_the_owner() -> None:
    # Debug mode refuses a call_soon from a foreign thread, so a post that is not thread-safe fails this test.
    asyncio.get_running_loop().set_debug(True)
    worker = WorkerLoop(LOG, thread_name="test-record-budget-worker")
    try:
        async with asyncio.timeout(100) as timeout, record_budget.armed(timeout, 100) as budget:
            before = timeout.when()
            threads: list[str] = []

            async def on_worker() -> None:
                threads.append(threading.current_thread().name)
                record_budget.resize_to("pdf", None)  # the armed budget reaches the worker through the task's context

            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(on_worker(), worker.start()))
            await asyncio.sleep(0)  # the posted move runs on this (the owner's) loop
            assert threads == ["test-record-budget-worker"]
            assert budget.seconds == messaging_env.pdf_record_processing_timeout
            assert timeout.when() == pytest.approx(before + budget.seconds - 100)
    finally:
        worker.stop()


def test_a_closed_owner_loop_does_not_fail_the_caller() -> None:
    loop = asyncio.new_event_loop()

    async def arm() -> record_budget.RecordBudget:
        async with asyncio.timeout(100) as timeout, record_budget.armed(timeout, 100) as budget:
            return budget

    budget = loop.run_until_complete(arm())
    loop.close()
    budget.resize(3700)  # from no running loop, to a closed one: posted, refused, swallowed
    assert budget.seconds == 100
