"""The parse route never cuts a parse the indexing client still waits for, and answers only its own deadline as 504.

Time is scaled by 1/2400: the 2400 s stage floor becomes 1.0 s and the 5400 s PDF record budget 2.25 s (the client's
read timeout for a PDF, 0.8 of it, 1.8 s). A large filing parsed in page batches that takes 1.4 s (3360 s) must index.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.services.parsing.client import ParsingClient
from app.services.parsing.interface import ParserProvider
from app.services.parsing.registry import ParserRegistry
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result

if TYPE_CHECKING:
    import pytest

SCALE = 1 / 2400


def _client_for(parse: Any, monkeypatch: pytest.MonkeyPatch) -> ParsingClient:  # noqa: ANN401
    registry = MagicMock(spec=ParserRegistry)
    parser = MagicMock()
    parser.parse = parse
    registry.resolve = MagicMock(return_value=parser)
    app = _build_app(registry)
    client = ParsingClient(service_url="http://parsing.test", retry_delay=0.01)
    monkeypatch.setattr(
        client, "_make_client",
        lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://parsing.test", timeout=30),
    )
    monkeypatch.setattr(client, "_auth_headers", AsyncMock(return_value={}))
    return client


async def test_a_pdf_parse_inside_its_own_budget_is_not_cut_at_the_stage_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache import locks

    monkeypatch.setattr(locks, "STAGE_DEADLINE_S", 2400 * SCALE)
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", str(5400 * SCALE))
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", str(1800 * SCALE))

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await asyncio.sleep(3360 * SCALE)
        return _ok_result()

    client = _client_for(parse, monkeypatch)
    result = await client.parse(
        file_content=b"%PDF-1.7 a large filing", record_name="filing.pdf", mime_type="application/pdf",
        extension="pdf", provider=ParserProvider.DEFAULT,
    )
    assert result.block_container is not None


def test_the_route_deadline_is_never_shorter_than_the_clients_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.messaging.config import messaging_env
    from app.services.paid_cache import locks
    from app.services.parsing.client import PARSE_SHARE_OF_RECORD_BUDGET

    for pdf in ("900", "5400", "9000", "21600"):
        monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", pdf)
        assert locks.stage_deadline_s() >= messaging_env.pdf_record_processing_timeout * PARSE_SHARE_OF_RECORD_BUDGET
        assert locks.stage_deadline_s() >= messaging_env.record_processing_timeout * PARSE_SHARE_OF_RECORD_BUDGET


async def test_after_a_504_the_permit_is_held_until_the_conversion_thread_returns_and_freed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling the await does not stop a thread, so the gate permit stays held until the thread really returns."""
    import threading

    from app.api.routes import parsing
    from app.services.paid_cache import locks
    from app.services.resource_governor.tiers import gate_pool

    monkeypatch.setattr(locks, "STAGE_DEADLINE_S", 0.2)
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "0.2")
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "0.2")
    monkeypatch.setattr(parsing, "PARSE_GATE_TIMEOUT_SECONDS", 30.0)    # the second parse waits for the permit
    monkeypatch.setattr(parsing, "parse_admission_cap", lambda *_a, **_k: 1)   # one conversion's worth of memory
    release_thread = threading.Event()
    calls: list[int] = []

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        calls.append(1)
        if len(calls) == 1:
            await asyncio.to_thread(release_thread.wait, 20)             # the conversion, in a thread
        return _ok_result()

    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=MagicMock(parse=parse))
    app = _build_app(registry, heavy_limit=1, light_limit=1)
    gate = app.state.governor.gate(gate_pool(parsing.classify("pdf", "application/pdf")))
    post_args = {
        "files": {"file": ("s.pdf", b"%PDF-1.4 x", "application/pdf")},
        "data": {"record_name": "s.pdf", "mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p.test") as client:
        first = await client.post("/api/v1/parse", **post_args)
        assert first.status_code == 504
        assert gate.in_use == 1, "the permit was freed while the conversion still runs"
        second = asyncio.ensure_future(client.post("/api/v1/parse", **post_args))
        await asyncio.sleep(0.5)
        assert not second.done() and len(calls) == 1, "a second parse was admitted on the memory the conversion still uses"
        assert gate.in_use == 1
        release_thread.set()                                              # the conversion returns
        assert (await second).status_code == 200                          # now it is admitted
        for _ in range(100):
            if gate.in_use == 0:
                break
            await asyncio.sleep(0.02)
        assert gate.in_use == 0, "the permit was freed more than once or not at all"


async def test_a_timeout_raised_inside_the_parser_also_holds_the_permit_while_its_thread_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not only the deadline: any exit that leaves a conversion thread running keeps the permit until it returns."""
    import threading

    from app.api.routes import parsing
    from app.services.resource_governor.tiers import gate_pool

    release_thread = threading.Event()
    started = threading.Event()

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, lambda: (started.set(), release_thread.wait(20)))   # a conversion thread
        for _ in range(500):                                          # bounded wait until the thread really runs
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        raise TimeoutError("the provider timed out")

    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=MagicMock(parse=parse))
    app = _build_app(registry, heavy_limit=1, light_limit=1)
    gate = app.state.governor.gate(gate_pool(parsing.classify("pdf", "application/pdf")))
    post_args = {
        "files": {"file": ("s.pdf", b"%PDF-1.4 x", "application/pdf")},
        "data": {"record_name": "s.pdf", "mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p.test") as client:
        response = await client.post("/api/v1/parse", **post_args)
        assert response.status_code == 500
        assert started.is_set() and gate.in_use == 1, "the permit was freed while a conversion thread still runs"
        release_thread.set()
        for _ in range(100):
            if gate.in_use == 0:
                break
            await asyncio.sleep(0.02)
        assert gate.in_use == 0


async def test_a_conversion_that_never_returns_is_on_the_parsing_services_metrics_with_a_growing_age(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import re
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from app import parsing_main
    from app.api.routes import parsing
    from app.services.parsing.load import StuckConversions
    from app.services.parsing.thread_work import ThreadWork

    monkeypatch.setattr("app.services.parsing.load._stuck", StuckConversions())
    skew = [0.0]
    monkeypatch.setattr(
        "app.services.parsing.load.time", SimpleNamespace(monotonic=lambda: time.monotonic() + skew[0]),
    )
    monkeypatch.setattr(parsing, "STUCK_TICK_SECONDS", 0.05)
    monkeypatch.setattr(parsing, "THREAD_OVERRUN_WARN_SECONDS", 0.0)
    released: list[int] = []
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    work = ThreadWork()

    async def scrape(client: httpx.AsyncClient) -> tuple[int, int]:
        text = (await client.get("/metrics")).text
        count = int(re.search(r"^pipeshub_parse_stuck_conversions (\d+)$", text, re.M).group(1))
        oldest = int(re.search(r"^pipeshub_parse_stuck_oldest_seconds (\d+)$", text, re.M).group(1))
        assert "pipeshub_parsing_in_flight" in text                  # next to the rest of the parsing metrics
        return count, oldest

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=parsing_main.app), base_url="http://p.test") as client:
            assert await scrape(client) == (0, 0)
            work.add(pool.submit(stop.wait, 20))                     # a conversion that does not return
            with caplog.at_level("WARNING"):
                parsing._release_when_work_ends(lambda: released.append(1), work, time_now(), "big.pdf")
                for _ in range(500):                                 # bounded wait for the monitor's warning
                    if any("still runs" in r.getMessage() and "big.pdf" in r.getMessage() for r in caplog.records):
                        break
                    await asyncio.sleep(0.01)
            assert any("still runs" in r.getMessage() and "big.pdf" in r.getMessage() for r in caplog.records)
            count, first_age = await scrape(client)
            skew[0] += 5.0                                           # five seconds pass
            _, later_age = await scrape(client)
            assert released == [], "the permit was freed while the conversion still runs"
            assert count == 1 and later_age >= first_age + 5
            stop.set()
            for _ in range(500):
                if released:
                    break
                await asyncio.sleep(0.01)
            assert released == [1] and await scrape(client) == (0, 0)
    finally:
        stop.set()
        pool.shutdown(wait=True)


def time_now() -> float:
    import time

    return time.monotonic()


def test_a_conversion_that_ends_after_the_loop_closed_drops_its_release_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.api.routes import parsing
    from app.services.parsing.thread_work import ThreadWork

    monkeypatch.setattr(parsing, "STUCK_TICK_SECONDS", 3600.0)
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    released: list[int] = []

    async def hold() -> None:
        work = ThreadWork()
        work.add(pool.submit(stop.wait, 20))
        parsing._release_when_work_ends(lambda: released.append(1), work, time_now(), "big.pdf")

    asyncio.run(hold())                                              # the loop is closed; the thread still runs
    with caplog.at_level("ERROR"):
        stop.set()
        pool.shutdown(wait=True)                                     # the thread ends: its done-callback runs
    assert released == []
    assert not [r for r in caplog.records if "exception calling callback" in r.getMessage()]
