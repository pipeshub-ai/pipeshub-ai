"""Reserve once, by the component that runs the work: lanes adopt, the Docling service reserves, indexing holds nothing."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.capacity.admission import docling_demand, llm_slot, parse_class
from app.capacity.budget_policy import Demand, Family
from app.capacity.member_budget import AdmissionTimeout
from app.modules.parsers import parse_pool
from tests.support import ledger_procs as lp
from tests.support.member_budget_env import GIB, install_budget

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import ModuleType


def _hold(seconds: float) -> int:
    time.sleep(seconds)
    return 1


@pytest.fixture
def no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parse_pool, "_pool", lambda **_kw: None)  # the unit runs in a thread: only the admission is under test


# ---- a permit passed in is adopted, never reserved twice ----------------------------------


async def test_the_lane_adopts_the_callers_permit_and_reserves_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None
) -> None:
    n = 4
    b = install_budget(monkeypatch, tmp_path / "l", c=n)  # C = N: a second permit per step could never be had
    seen: list[int] = []

    def work() -> int:
        seen.append(b.snapshot()["used"])
        return _hold(0.1)

    async def step() -> int:
        p = await b.reserve(Demand(Family.DOCLING, 1, 0, "small"), deadline_s=5)
        return await parse_pool.submit(work, timeout=2, permit=p)

    t = time.monotonic()
    assert await asyncio.gather(*(step() for _ in range(n))) == [1] * n
    assert time.monotonic() - t < 1.5, "a lane that reserves again waits for tokens its own caller holds"
    assert max(seen) == n  # exactly N tokens held, none for a second permit
    assert b.snapshot()["used"] == 0  # the lane released them at job end
    assert b.snapshot()["families"]["docling"]["used"] == 0


async def test_an_adopted_permit_keeps_its_owner_family(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None) -> None:
    b = install_budget(monkeypatch, tmp_path / "l")
    p = await b.reserve(Demand(Family.EMBED, 2, 0, "small"), deadline_s=5)
    seen = {}

    def work() -> int:
        seen.update(b.snapshot()["families"])
        return 1

    await parse_pool.submit(work, permit=p)
    assert seen["embed"]["used"] == 2 and seen["parse"]["used"] == 0


async def test_a_caller_without_a_permit_gets_one_from_the_lane(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None) -> None:
    b = install_budget(monkeypatch, tmp_path / "l")
    seen: list[int] = []

    def work() -> int:
        seen.append(b.snapshot()["families"]["parse"]["used"])
        return 1

    await parse_pool.submit(work)
    assert seen == [1] and b.snapshot()["used"] == 0


async def test_a_lane_wait_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None) -> None:
    b = install_budget(monkeypatch, tmp_path / "l", c=2)
    assert b.try_reserve(Demand(Family.PARSE, 2, 0, "small")) is not None
    from app.services.messaging.consumer_concurrency import (
        ParseAdmissionTimeout,
        RequeueWithoutAttempt,
    )

    with pytest.raises(ParseAdmissionTimeout) as err:
        await parse_pool.submit(lambda: 1, timeout=0.2)
    assert isinstance(err.value, RequeueWithoutAttempt)  # a wait for capacity goes back uncounted, it is not a failure
    assert not isinstance(err.value, AdmissionTimeout)


def test_run_blocking_adopts_too(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    b = install_budget(monkeypatch, tmp_path / "l", c=1)
    p = b.try_reserve(Demand(Family.PARSE, 1, 0, "small"))
    seen: list[int] = []
    monkeypatch.setattr(parse_pool, "_run_blocking", lambda *a: seen.append(b.snapshot()["used"]) or 1)
    assert parse_pool.run_blocking(_hold, 0, timeout=1, permit=p) == 1
    assert seen == [1] and b.snapshot()["used"] == 0  # C = 1 held by the caller's permit, nothing reserved again


async def test_with_the_flag_off_a_lane_holds_nothing_and_creates_no_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None) -> None:
    monkeypatch.delenv("PIPESHUB_CAPACITY_PROFILE", raising=False)
    monkeypatch.setenv("PIPESHUB_CAPACITY_DIR", str(tmp_path / "never"))
    assert await parse_pool.submit(_hold, 0) == 1
    assert not (tmp_path / "never").exists()


# ---- the Docling conversion is reserved once, by the Docling service ------------------------------------


@pytest.fixture
def docling_route(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    import app.services.docling.docling_service as mod

    mod._parse_tasks.clear()
    original = mod.docling_service
    monkeypatch.setattr(mod, "_resource_governor", None)
    monkeypatch.setattr(mod, "serialize_docling_doc", lambda doc: "{}")
    yield mod
    mod.docling_service = original


async def _post(mod, cls: str | None) -> object:
    upload = MagicMock(read=AsyncMock(return_value=b"%PDF-data"))
    from fastapi import BackgroundTasks

    return await mod.parse_pdf_endpoint(
        BackgroundTasks(), file=upload, record_name="a.pdf", start_page=None, end_page=None, x_parse_class=cls
    )


async def test_docling_reserved_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docling_route) -> None:
    """The service holds the Docling tokens (class from ``X-Parse-Class``) for exactly its conversion."""
    b = install_budget(monkeypatch, tmp_path / "l")
    seen: dict[str, object] = {}

    async def parse_pdf_only(name: str, pdf: bytes, page_range: object = None) -> MagicMock:
        seen["snap"] = b.snapshot()
        return MagicMock()

    docling_route.docling_service = MagicMock(parse_pdf_only=parse_pdf_only)
    resp = await _post(docling_route, "small")
    assert getattr(resp, "success", False) is True
    snap = seen["snap"]
    assert snap["by_class"] == {"docling/small": 2}  # one reservation, the class the caller sent
    assert snap["families"]["docling"]["used"] == 2
    await asyncio.sleep(0.1)
    assert b.snapshot()["used"] == 0  # released with the conversion


async def test_an_unknown_class_is_unclassified_not_trusted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docling_route) -> None:
    b = install_budget(monkeypatch, tmp_path / "l")
    seen: dict[str, object] = {}

    async def parse_pdf_only(name: str, pdf: bytes, page_range: object = None) -> MagicMock:
        seen["by_class"] = b.snapshot()["by_class"]
        return MagicMock()

    docling_route.docling_service = MagicMock(parse_pdf_only=parse_pdf_only)
    await _post(docling_route, "critical; drop table")
    assert seen["by_class"] == {"docling/none": 2}
    assert parse_class(" Small ") == "small" and parse_class(None) is None
    assert parse_class("critical; drop table") is None and parse_class(object()) is None


async def test_a_full_budget_answers_with_backpressure_and_holds_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, docling_route) -> None:
    b = install_budget(monkeypatch, tmp_path / "l", c=2)
    hold = b.try_reserve(Demand(Family.PARSE, 2, 0, "small"))
    monkeypatch.setattr(docling_route, "DOCLING_GATE_TIMEOUT_SECONDS", 0.2)
    docling_route.docling_service = MagicMock(parse_pdf_only=AsyncMock())
    resp = await _post(docling_route, "small")
    assert resp.status_code == 429
    hold.release()
    assert b.snapshot()["used"] == 0


async def test_the_indexing_client_holds_no_tokens_while_it_waits_on_docling(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services.docling.client import DoclingClient

    b = install_budget(monkeypatch, tmp_path / "l")
    during: list[int] = []
    client = DoclingClient(service_url="http://docling.invalid")

    async def post(path: str, **_kw: object) -> MagicMock:
        during.append(b.snapshot()["used"])
        return MagicMock(status_code=200, json=lambda: {"success": True, "parse_result": "{}"})

    with patch.object(client, "_post_multipart", post), patch.object(client, "_validate_pdf_binary", lambda _b: True):
        assert await client.parse_pdf("a.pdf", b"%PDF-1") == "{}"
    assert during == [0]


def test_docling_demand_is_the_service_weight() -> None:
    d = docling_demand("small")
    assert d.family is Family.DOCLING and d.cpu == 2 and d.cls == "small" and d.mem_bytes == int(1.5 * GIB)


def test_reserve_requires_deadline_or_cancel_at_the_call_sites(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    b = install_budget(monkeypatch, tmp_path / "l")
    with pytest.raises(ValueError, match="deadline or a cancel token"):
        b.reserve_blocking(Demand(Family.DOCLING, 2, 0, "small"))


# ---- the LLM member cap at the gateway ------------------------------------------------------------------


async def test_every_gateway_call_holds_a_member_slot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.services.llm_gateway.gateway import LLMGateway

    b = install_budget(monkeypatch, tmp_path / "l", llm=3)
    gw = LLMGateway(50)  # the per-process limiter is not what stops the fourth call
    inflight: list[int] = []
    gate = asyncio.Event()

    async def make_call() -> str:
        inflight.append(b.snapshot()["llm"]["used"])
        await gate.wait()
        return "ok"

    tasks = [asyncio.create_task(gw.call(make_call, provider="p")) for _ in range(8)]
    await asyncio.sleep(0.5)
    assert b.snapshot()["llm"]["used"] == 3 and len(inflight) == 3  # the other five wait for the container's cap
    gate.set()
    assert await asyncio.gather(*tasks) == ["ok"] * 8
    assert b.snapshot()["llm"]["used"] == 0


async def test_llm_slot_is_a_noop_with_the_flag_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PIPESHUB_CAPACITY_PROFILE", raising=False)
    monkeypatch.setenv("PIPESHUB_CAPACITY_DIR", str(tmp_path / "never"))
    async with llm_slot():
        pass
    assert not (tmp_path / "never").exists()
    assert not os.path.exists(tmp_path / "never")


async def test_a_cancelled_caller_does_not_free_the_tokens_of_a_thread_that_is_still_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None
) -> None:
    b = install_budget(monkeypatch, tmp_path / "l", c=2)
    task = asyncio.create_task(parse_pool.submit(_hold, 0.6))
    await asyncio.sleep(0.15)
    assert b.snapshot()["used"] == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert b.snapshot()["used"] == 1  # the thread cannot be stopped: its tokens stay with it
    await asyncio.sleep(0.8)
    assert b.snapshot()["used"] == 0  # released when the thread ended


def test_run_blocking_stops_waiting_for_capacity_when_its_caller_is_cancelled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import threading

    from app.modules.parsers.parse_pool import ParseAbandonedError

    b = install_budget(monkeypatch, tmp_path / "l", c=1)
    held = b.try_reserve(Demand(Family.PARSE, 1, 0, "small"))
    cancelled = threading.Event()
    threading.Timer(0.2, cancelled.set).start()
    started = time.monotonic()
    with pytest.raises(ParseAbandonedError):
        parse_pool.run_blocking(_hold, 0, timeout=30, cancelled=cancelled)  # the budget is full: it waits, then is cancelled
    assert time.monotonic() - started < 3.0
    held.release()


async def test_a_gateway_call_that_finds_no_llm_slot_is_an_unavailable_provider_not_a_raw_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.capacity import admission
    from app.services.llm_gateway.gateway import LLMGateway, ProviderUnavailableError

    b = install_budget(monkeypatch, tmp_path / "l", llm=1)
    held = b.try_reserve(Demand(Family.LLM, 0))
    monkeypatch.setattr(admission, "LLM_WAIT_S", 0.2)
    gw = LLMGateway(5)

    async def make_call() -> str:
        return "never"

    with pytest.raises(ProviderUnavailableError) as err:
        await gw.call(make_call, provider="p")
    assert err.value.sent is False
    held.release()


async def test_a_member_slot_timeout_on_a_later_attempt_keeps_what_earlier_attempts_sent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.capacity import admission
    from app.services.llm_gateway import gateway as gw_module
    from app.services.llm_gateway.gateway import LLMGateway, ProviderUnavailableError

    b = install_budget(monkeypatch, tmp_path / "l", llm=1)
    monkeypatch.setattr(admission, "LLM_WAIT_S", 0.2)
    real_sleep = asyncio.sleep

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(gw_module.asyncio, "sleep", fast_sleep)
    gw = LLMGateway(5)
    held: list[object] = []
    # After the first attempt's rate-limit refusal another process takes the only slot, so the second attempt waits.
    monkeypatch.setattr(gw, "note_rate_limit_retry", lambda: held.append(b.try_reserve(Demand(Family.LLM, 0))))

    class RateLimited(Exception):
        status_code = 429

    async def make_call() -> str:
        raise RateLimited("slow down")

    with pytest.raises(ProviderUnavailableError) as err:
        await gw.call(make_call, provider="p")
    assert err.value.sent is True  # the first attempt reached the wire
    assert (err.value.requests or 0) >= 1


async def test_a_workbook_load_reserves_its_memory_in_the_member_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None
) -> None:
    from app.services.resource_governor.kinds import ParseKind, excel_load_max_bytes

    # CPU tokens enough for two big units (the floors keep 14 of 32), memory strictly below two 1.5 GiB charges: only the
    # memory reservation can make the second load wait.
    b = install_budget(monkeypatch, tmp_path / "l", c=32, mem=int(2.5 * GIB))
    seen: list[int] = []

    def work() -> int:
        seen.append(b.snapshot()["mem_charged"])
        return 1

    await parse_pool.submit(work, kind=ParseKind.EXCEL_LOAD)
    assert seen == [excel_load_max_bytes()] and b.snapshot()["mem_charged"] == 0
    # two loads at once need more memory than the container has: the second waits for the first, it is not admitted over it
    started = time.monotonic()
    results = await asyncio.gather(
        parse_pool.submit(_hold, 0.4, kind=ParseKind.EXCEL_LOAD), parse_pool.submit(_hold, 0.4, kind=ParseKind.EXCEL_LOAD)
    )
    assert results == [1, 1] and time.monotonic() - started >= 0.7


async def test_a_pool_job_keeps_its_tokens_until_its_lane_has_finished_with_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.unit.services.messaging.governor_test_helpers import make_test_governor

    b = install_budget(monkeypatch, tmp_path / "l", c=2)
    monkeypatch.setenv("PARSE_POOL_WORKERS", "1")
    monkeypatch.delenv("PARSE_LANES", raising=False)
    parse_pool.shutdown_parse_pool()
    parse_pool.set_resource_governor(make_test_governor(logger_name="test.pool"))
    stop_may_finish = threading.Event()
    try:
        assert parse_pool._pool() is not None  # a real worker pool, not the thread fallback
        real_stop = parse_pool._Worker.stop

        def held_stop(self: object) -> None:
            stop_may_finish.wait(10)  # the lane's cleanup (stop the worker, then complete the job) is held by the test
            real_stop(self)

        monkeypatch.setattr(parse_pool._Worker, "stop", held_stop)
        ready, release = tmp_path / "ready", tmp_path / "release"
        task = asyncio.create_task(parse_pool.submit(lp.signal_ready_then_wait, str(ready), str(release), label="t", size=1))
        for _ in range(300):  # the worker says it is running the job (bounded wait)
            if ready.exists():
                break
            await asyncio.sleep(0.05)
        assert ready.exists(), "the worker never started the job"
        assert b.snapshot()["used"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.6)  # long enough for the lane to notice the cancel and begin stopping the worker
        assert b.snapshot()["used"] == 1  # but not finished: the tokens still cover the parse
        stop_may_finish.set()
        for _ in range(100):
            if b.snapshot()["used"] == 0:
                break
            await asyncio.sleep(0.1)
        assert b.snapshot()["used"] == 0  # released when the lane finished with the job
    finally:
        stop_may_finish.set()  # before shutdown, so it does not wait on a held stop
        (tmp_path / "release").write_text("go")
        parse_pool.shutdown_parse_pool()
        parse_pool.set_resource_governor(None)


def test_an_inspections_lane_demand_keeps_its_designed_memory_and_other_kinds_keep_their_lane_charge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.parse_engine import wiring
    from app.services.resource_governor.kinds import ParseKind, lane_charge_bytes

    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    wiring.reset_for_tests()
    inspect = parse_pool._lane_demand(ParseKind.INSPECT, 1 << 20)
    assert inspect.family is Family.INSPECT and inspect.mem_bytes == wiring.INSPECT_MEMORY_BYTES  # 0.1 GiB, not 1.25
    for kind in (ParseKind.EXCEL_LOAD, ParseKind.PDF_LAYOUT, ParseKind.TEXT):
        assert parse_pool._lane_demand(kind, 1 << 20).mem_bytes == lane_charge_bytes(kind, 1 << 20)  # unchanged
    monkeypatch.setenv("PARSE_ADMISSION", "legacy")
    assert parse_pool._lane_demand(ParseKind.INSPECT, 1 << 20).mem_bytes == lane_charge_bytes(ParseKind.INSPECT, 1 << 20)


async def test_twelve_inspections_on_an_eight_gib_ledger_run_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_pool: None
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from app.modules.parse_engine import wiring
    from app.services.resource_governor.kinds import ParseKind

    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    wiring.reset_for_tests()
    install_budget(monkeypatch, tmp_path / "l", c=32, mem=8 * GIB)
    # The thread fallback runs on the loop's default executor, whose size depends on the host's CPU count (8 on a
    # 4-CPU runner): pin it, so only the ledger can limit how many inspections run at once.
    executor = ThreadPoolExecutor(max_workers=16)
    asyncio.get_running_loop().set_default_executor(executor)
    running = 0
    peak = 0
    lock = threading.Lock()

    all_up = threading.Event()

    def inspect() -> int:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
            if running == 12:
                all_up.set()
        all_up.wait(10)  # held until all twelve have started (bounded: insufficient admission fails, it does not hang)
        with lock:
            running -= 1
        return 1

    try:
        await asyncio.gather(*(parse_pool.submit(inspect, kind=ParseKind.INSPECT, size=1 << 20) for _ in range(12)))
    finally:
        all_up.set()
        executor.shutdown(wait=True)
    assert peak == 12  # at 1.25 GiB each only 6 fit an 8 GiB ledger
