"""The /parse-pdf permit is released exactly once, and only when the work holding memory has ended."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse

import app.services.docling.docling_service as mod
from app.services.docling.docling_service import DoclingService

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator


class _Ledger:
    def __init__(self) -> None:
        self.acquired = 0
        self.released = 0

    async def acquire(self, *_args: object) -> tuple[bool, int]:
        self.acquired += 1
        return True, 1

    def release(self, cost: int) -> None:
        if cost:
            self.released += 1

    async def settle(self) -> None:
        for _ in range(100):
            if self.released >= self.acquired:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)  # a double release would land here


@pytest.fixture
def ledger() -> Iterator[_Ledger]:
    led = _Ledger()
    original = mod.docling_service
    mod._parse_tasks.clear()
    mod._abandoned.clear()
    with (
        patch.object(mod, "_acquire_docling_gate", led.acquire),
        patch.object(mod, "_release_docling_gate", led.release),
        patch.object(mod, "PDF_PARSING_TIMEOUT_SECONDS", 0.3),
    ):
        yield led
    mod.docling_service = original


def _wire(parse: Callable[..., Coroutine[object, object, object]]) -> None:
    svc = DoclingService()
    svc.parse_pdf_only = parse  # type: ignore[method-assign]
    mod.docling_service = svc


def _doc(serialize: Callable[..., str] | None = None) -> MagicMock:
    doc = MagicMock()
    doc.model_dump_json.side_effect = serialize or (lambda *_a, **_k: "{}")
    return doc


async def _call() -> mod.ParseResponse | JSONResponse:
    upload = MagicMock(read=AsyncMock(return_value=b"data"))
    return await mod.parse_pdf_endpoint(
        background_tasks=BackgroundTasks(), file=upload, record_name="a.pdf", start_page=None, end_page=None
    )


def _hanging_parse(stop: threading.Event) -> Callable[..., Coroutine[object, object, MagicMock]]:
    async def parse(*_a: object, **_k: object) -> MagicMock:
        await asyncio.to_thread(stop.wait, 10)
        return _doc()

    return parse


@pytest.mark.asyncio
async def test_success_releases_once(ledger: _Ledger) -> None:
    _wire(AsyncMock(return_value=_doc()))
    assert (await _call()).success
    await ledger.settle()
    assert (ledger.acquired, ledger.released) == (1, 1)


@pytest.mark.asyncio
async def test_parse_error_releases_once(ledger: _Ledger) -> None:
    _wire(AsyncMock(side_effect=RuntimeError("boom")))
    assert (await _call()).status_code == 422
    await ledger.settle()
    assert (ledger.acquired, ledger.released) == (1, 1)


@pytest.mark.asyncio
async def test_serialization_error_releases_once(ledger: _Ledger) -> None:
    _wire(AsyncMock(return_value=_doc(MagicMock(side_effect=ValueError("ser")))))
    assert (await _call()).status_code == 422
    await ledger.settle()
    assert (ledger.acquired, ledger.released) == (1, 1)


@pytest.mark.asyncio
async def test_missing_service_releases_once(ledger: _Ledger) -> None:
    mod.docling_service = None
    with pytest.raises(Exception, match="not available"):
        await _call()
    assert (ledger.acquired, ledger.released) == (1, 1)


@pytest.mark.asyncio
async def test_failed_spawn_releases_once(ledger: _Ledger) -> None:
    _wire(AsyncMock())

    def boom(coro: Coroutine[object, object, object], **_k: object) -> None:
        coro.close()
        raise RuntimeError("spawn")

    with patch.object(mod, "spawn_in_request", boom):
        assert (await _call()).status_code == 422
    assert (ledger.acquired, ledger.released) == (1, 1)


@pytest.mark.asyncio
async def test_failed_bookkeeping_after_hand_off_does_not_release_twice(ledger: _Ledger) -> None:
    _wire(AsyncMock(return_value=_doc()))
    real = mod.spawn_in_request

    def spawn(coro: Coroutine[object, object, object], **kwargs: str) -> asyncio.Task:
        task = real(coro, **kwargs)
        task.add_done_callback = MagicMock(side_effect=RuntimeError("cb"))  # type: ignore[method-assign]
        return task

    with patch.object(mod, "spawn_in_request", spawn):
        await _call()
        await ledger.settle()
    assert (ledger.acquired, ledger.released) == (1, 1)


@pytest.mark.asyncio
async def test_timeout_answers_at_the_deadline_and_holds_the_permit(ledger: _Ledger) -> None:
    stop = threading.Event()
    _wire(_hanging_parse(stop))
    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        resp = await _call()
        assert resp.status_code == 422
        assert b"PARSE_TIMEOUT" in resp.body
        assert loop.time() - started < 0.6
        assert ledger.released == 0
        assert len(mod._parse_tasks) == 1
    finally:
        stop.set()
    await ledger.settle()
    assert (ledger.acquired, ledger.released) == (1, 1)
    assert not mod._parse_tasks


@pytest.mark.asyncio
async def test_a_cancelled_caller_does_not_release_the_permit(ledger: _Ledger) -> None:
    stop = threading.Event()
    _wire(_hanging_parse(stop))
    with patch.object(mod, "PDF_PARSING_TIMEOUT_SECONDS", 5):
        caller = asyncio.ensure_future(_call())
        await asyncio.sleep(0.2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        try:
            assert (ledger.acquired, ledger.released) == (1, 0)
        finally:
            stop.set()
        await ledger.settle()
    assert (ledger.acquired, ledger.released) == (1, 1)


@pytest.mark.asyncio
async def test_the_permit_is_held_while_the_document_is_serialized(ledger: _Ledger) -> None:
    in_serializer = threading.Event()
    finish = threading.Event()

    def serialize(*_a: object, **_k: object) -> str:
        in_serializer.set()
        finish.wait(5)
        return "{}"

    _wire(AsyncMock(return_value=_doc(serialize)))
    with patch.object(mod, "PDF_PARSING_TIMEOUT_SECONDS", 5):
        caller = asyncio.ensure_future(_call())
        try:
            await asyncio.wait_for(asyncio.to_thread(in_serializer.wait, 5), timeout=6)
            assert ledger.released == 0
        finally:
            finish.set()
        assert (await caller).success
    await ledger.settle()
    assert (ledger.acquired, ledger.released) == (1, 1)


@pytest.mark.asyncio
async def test_many_timeouts_leave_no_tasks_behind(ledger: _Ledger) -> None:
    async def parse(*_a: object, **_k: object) -> MagicMock:
        await asyncio.sleep(0.3)
        return _doc()

    _wire(parse)
    with patch.object(mod, "PDF_PARSING_TIMEOUT_SECONDS", 0.05):
        for _ in range(20):
            await _call()
        await ledger.settle()
    assert (ledger.acquired, ledger.released) == (20, 20)
    assert not mod._parse_tasks
    assert not mod._abandoned


@pytest.mark.asyncio
async def test_a_failure_is_logged_once(ledger: _Ledger, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger=mod.logger.name)
    # The caller is still waiting: it reports the failure, the task does not.
    _wire(AsyncMock(side_effect=RuntimeError("waiting")))
    await _call()
    await ledger.settle()
    assert not [r for r in caplog.records if "Abandoned" in r.getMessage()]

    # The caller timed out: the task is the only one left to report it.
    async def late_failure(*_a: object, **_k: object) -> None:
        await asyncio.sleep(0.5)
        raise RuntimeError("abandoned")

    _wire(late_failure)
    await _call()
    await ledger.settle()
    assert len([r for r in caplog.records if "Abandoned" in r.getMessage()]) == 1


@pytest.mark.asyncio
async def test_a_parse_that_raises_timeout_itself_is_not_left_abandoned(ledger: _Ledger) -> None:
    _wire(AsyncMock(side_effect=asyncio.TimeoutError()))
    response = await _call()
    await ledger.settle()
    assert isinstance(response, JSONResponse) and response.status_code == 422
    assert (ledger.acquired, ledger.released) == (1, 1)
    assert not mod._parse_tasks
    assert not mod._abandoned
