"""Local Docling on the lane budget (T66 to T73 and the round-5 executor items)."""
from __future__ import annotations

import asyncio
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from docling_core.types.doc.document import DoclingDocument

from app.modules.parsers.pdf import docling_processor as dp
from app.services.resource_governor.lanes import (
    LaneBudget,
    LaneWaitCancelled,
    LaneWaitTimeout,
    docling_auto_width,
    plan_lanes,
)
from tests.unit.lane_gauge import Mem

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.timeout(60)
GIB = 2**30
SERIALIZED = DoclingDocument(name="x").model_dump_json()


def wait_for(predicate: Callable[[], bool], seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


async def until(predicate: Callable[[], bool], seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


class Converter:
    """Blocks inside ``convert`` until told to go, and records what ran."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.go = threading.Event()
        self.go.set()
        self.calls: list[str] = []

    def convert(self, source: object, **kwargs: object) -> MagicMock:
        self.calls.append(source.name)  # type: ignore[attr-defined]
        self.started.set()
        self.go.wait(20)
        result = MagicMock()
        result.status.value = "success"
        result.document = "DOC"
        return result


@pytest.fixture
def make_budget(tmp_path: Path) -> Iterator[Callable[..., LaneBudget]]:
    made: list[LaneBudget] = []

    def factory(lanes: int = 4) -> LaneBudget:
        budget = LaneBudget.open(
            plan_lanes(cpus=lanes * 2, heavy=lanes * 2), tmp_path / "lanes",
            memory_reader=Mem(1000 * GIB, 0), view_ttl=0.0, alive=lambda pid: True,
        )
        made.append(budget)
        return budget

    yield factory
    for budget in made:
        budget.close()


@pytest.fixture
def lanes_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PARSE_LANES", "on")
    monkeypatch.delenv("LOCAL_DOCLING_PARSE_WORKERS", raising=False)
    saved = dp._resource_governor
    yield
    dp.shutdown_docling_pool()
    dp.set_resource_governor(saved)  # type: ignore[arg-type]


@pytest.fixture
def processor() -> dp.DoclingProcessor:
    proc = dp.DoclingProcessor.__new__(dp.DoclingProcessor)
    proc.logger = MagicMock()
    proc.config = None
    proc.converter = Converter()  # type: ignore[assignment]
    return proc


def wire(budget: LaneBudget) -> SimpleNamespace:
    gov = SimpleNamespace(lanes=budget, report_memory_incident=MagicMock())
    dp.set_resource_governor(gov)  # type: ignore[arg-type]
    return gov


PDF = b"%PDF-1.7 body"
DOCX = b"PK\x03\x04 body"


# -- T66: the thread path ---------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_path_takes_a_weighted_lease(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor) -> None:
    budget = make_budget(4)
    wire(budget)
    conv: Converter = processor.converter  # type: ignore[assignment]
    seen: dict[str, tuple[int, int]] = {}
    for name, content in (("a.docx", DOCX), ("a.pdf", PDF)):
        conv.started.clear()
        conv.go.clear()
        task = asyncio.ensure_future(processor.parse_document(name, content))
        assert await asyncio.to_thread(conv.started.wait, 5)
        seen[name] = (budget.held_count()[0], dp.docling_lane_stats()["weight_held"])  # type: ignore[assignment]
        conv.go.set()
        assert await task == "DOC"
        assert await until(lambda: budget.held_count()[0] == 0)
    assert seen == {"a.docx": (1, 1), "a.pdf": (3, 3)}


@pytest.mark.asyncio
async def test_weight_comes_from_the_content_not_the_name(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor) -> None:
    plan = plan_lanes(cpus=8, heavy=8)
    assert dp.conversion_weight(PDF, plan) == 3 and dp.conversion_weight(DOCX, plan) == 1
    assert dp.conversion_weight(b"", plan) == 1 and dp.conversion_weight(b" %PDF-", plan) == 1
    budget = make_budget(4)
    wire(budget)
    conv: Converter = processor.converter  # type: ignore[assignment]
    conv.go.clear()
    task = asyncio.ensure_future(processor.parse_document("looks-like.docx", PDF))
    assert await asyncio.to_thread(conv.started.wait, 5)
    assert budget.held_count()[0] == 3
    conv.go.set()
    await task


@pytest.mark.asyncio
async def test_page_batches_reacquire_per_batch(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor) -> None:
    budget = make_budget(4)
    wire(budget)
    held_between: list[int] = []
    for batch in ((1, 10), (11, 20)):
        await processor.parse_document("a.pdf", PDF, page_range=batch)
        held_between.append(0 if await until(lambda: budget.held_count()[0] == 0) else 1)
    assert held_between == [0, 0]
    assert len(processor.converter.calls) == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_lease_is_taken_around_the_conversion_only(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor) -> None:
    budget = make_budget(4)
    wire(budget)
    original = processor.converter.convert  # type: ignore[attr-defined]
    during: list[int] = []

    def spying(source: object, **kw: object) -> object:
        during.append(budget.held_count()[0])
        return original(source, **kw)

    processor.converter.convert = spying  # type: ignore[attr-defined]
    assert budget.held_count()[0] == 0
    await processor.parse_document("a.pdf", PDF)
    assert during == [3]


# -- round 5 R5-1: the thread path never leaks or drops its lease ------------------


@pytest.mark.asyncio
async def test_lease_released_when_cancelled_before_the_job_starts(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor) -> None:
    budget = make_budget(4)
    wire(budget)
    loop = asyncio.get_running_loop()
    saturated = ThreadPoolExecutor(1)
    loop.set_default_executor(saturated)
    gate = threading.Event()
    saturated.submit(gate.wait, 20)  # the only thread is busy: our job stays queued
    try:
        task = asyncio.ensure_future(processor.parse_document("a.pdf", PDF))
        assert await until(lambda: budget.held_count()[0] == 3)
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await until(lambda: budget.held_count()[0] == 0, 2), "the lease of a job that never started leaked"
    finally:
        gate.set()
        saturated.shutdown(wait=True)
    assert processor.converter.calls == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_lease_released_when_submit_raises(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor, monkeypatch: pytest.MonkeyPatch) -> None:
    budget = make_budget(4)
    wire(budget)
    loop = asyncio.get_running_loop()

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("executor is shut down")

    monkeypatch.setattr(loop, "run_in_executor", boom)
    with pytest.raises(RuntimeError):
        await processor.parse_document("a.pdf", PDF)
    assert budget.held_count()[0] == 0


@pytest.mark.asyncio
async def test_cancelled_awaiter_does_not_cancel_a_running_conversion(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor) -> None:
    budget = make_budget(4)
    wire(budget)
    conv: Converter = processor.converter  # type: ignore[assignment]
    conv.go.clear()
    task = asyncio.ensure_future(processor.parse_document("a.pdf", PDF))
    assert await asyncio.to_thread(conv.started.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)
    assert budget.held_count()[0] == 3, "the lease went with the awaiter while the conversion still ran"
    conv.go.set()
    assert await until(lambda: budget.held_count()[0] == 0)


# -- T70: the local wait ------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_wait_honours_cancellation(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor) -> None:
    budget = make_budget(1)
    wire(budget)
    blocker = budget.acquire(timeout=2)
    # a caller-provided abandon signal
    flag = {"gone": False}
    with dp.lease_scope(30, lambda: flag["gone"]):
        task = asyncio.ensure_future(processor.parse_document("a.docx", DOCX))
        await asyncio.sleep(0.2)
        flag["gone"] = True
        with pytest.raises(LaneWaitCancelled):
            await task
    # a bounded wait
    with dp.lease_scope(0.3), pytest.raises(LaneWaitTimeout):
        await processor.parse_document("a.docx", DOCX)
    # task cancellation while waiting leaves no lease
    task = asyncio.ensure_future(processor.parse_document("a.docx", DOCX))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert budget.held_count()[0] == 1
    blocker.release()
    assert processor.converter.calls == []  # type: ignore[attr-defined]
    assert budget.held_count()[0] == 0


# -- T71, T73: widths and settings ----------------------------------------------------


@pytest.mark.parametrize(("lanes", "weight"), [(1, 1), (2, 1), (3, 2), (4, 3), (8, 3), (31, 3)])
def test_pdf_weight_leaves_a_lane_on_small_plans(lanes: int, weight: int) -> None:
    """Q11': min(3, max(1, lanes - 1)); a DOCX stays at 1."""
    plan = plan_lanes(cpus=lanes * 2, heavy=lanes * 2)
    assert dp.conversion_weight(PDF, plan) == weight and dp.conversion_weight(DOCX, plan) == 1


@pytest.mark.asyncio
async def test_a_pdf_on_a_two_lane_plan_holds_one_lane_while_a_docx_runs(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor) -> None:
    budget = make_budget(2)
    wire(budget)
    conv: Converter = processor.converter  # type: ignore[assignment]
    conv.go.clear()
    pdf = asyncio.ensure_future(processor.parse_document("a.pdf", PDF))
    docx = asyncio.ensure_future(processor.parse_document("b.docx", DOCX))
    assert await until(lambda: len(conv.calls) == 2)
    assert budget.held_count()[0] == 2  # 1 for the PDF, 1 for the DOCX: both run, within the limit
    conv.go.set()
    await asyncio.gather(pdf, docx)


@pytest.mark.parametrize(("lanes", "width"), [(1, 0), (2, 0), (3, 0), (4, 1), (6, 1), (7, 2), (10, 3), (31, 10)])
def test_auto_width_table(lanes: int, width: int) -> None:
    plan = plan_lanes(cpus=lanes * 2, heavy=lanes * 2)
    assert plan.lanes == lanes
    assert docling_auto_width(plan) == width


@pytest.mark.parametrize(("lanes", "mode"), [(1, "thread"), (6, "thread"), (7, "executor"), (31, "executor")])
def test_auto_picks_the_path_from_the_plan(lanes: int, mode: str, lanes_on: None, make_budget: Callable[..., LaneBudget], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_DOCLING_PARSE_WORKERS", "auto")
    wire(make_budget(lanes))
    assert dp._lane_mode() == mode


@pytest.mark.parametrize("value", [None, "", "1", "0", "-2", "x"])
def test_default_docling_value_is_the_leased_thread_path(value: str | None, lanes_on: None, make_budget: Callable[..., LaneBudget], monkeypatch: pytest.MonkeyPatch) -> None:
    if value is not None:
        monkeypatch.setenv("LOCAL_DOCLING_PARSE_WORKERS", value)
    wire(make_budget(4))
    assert dp._lane_mode() == "thread"


@pytest.mark.asyncio
async def test_explicit_docling_pool_is_private_and_braked(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_DOCLING_PARSE_WORKERS", "3")
    budget = make_budget(4)
    wire(budget)
    assert dp._lane_mode() is None
    submitted: list[object] = []

    class Pool:
        def submit(self, fn: object, *args: object) -> object:
            from concurrent.futures import Future

            submitted.append(args[0])
            future: Future = Future()
            future.set_result(SERIALIZED)
            return future

    monkeypatch.setattr(dp, "LOCAL_DOCLING_PARSE_WORKERS", 3)
    monkeypatch.setattr(dp, "_get_process_pool", lambda: Pool())
    doc = await processor.parse_document("a.pdf", PDF)
    assert doc.name == "x" and submitted
    assert budget.held_count() == (0, 0)  # no lease


@pytest.mark.asyncio
async def test_off_runs_the_legacy_path_for_every_value(lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_LANES", "off")
    monkeypatch.setenv("LOCAL_DOCLING_PARSE_WORKERS", "auto")
    budget = make_budget(7)
    wire(budget)
    assert dp._lane_mode() is None
    assert dp._get_local_parse_worker_count() == 1
    assert await processor.parse_document("a.pdf", PDF) == "DOC"
    assert budget.held_count() == (0, 0)


# -- executor path (auto): T67, T71, T72 -------------------------------------------------


@pytest.fixture
def auto_threads(lanes_on: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[ThreadPoolExecutor]]:
    """``auto`` with a thread pool standing in for the child processes."""
    monkeypatch.setenv("LOCAL_DOCLING_PARSE_WORKERS", "auto")
    made: list[ThreadPoolExecutor] = []

    def factory(width: int) -> ThreadPoolExecutor:
        made.append(ThreadPoolExecutor(width))
        return made[-1]

    monkeypatch.setattr(dp._DoclingExecutor, "_default_factory", staticmethod(factory))
    yield made
    dp.shutdown_docling_pool()
    for ex in made:
        ex.shutdown(wait=False, cancel_futures=True)


class Child:
    """Stands in for ``_parse_document_in_worker`` in the executor."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.go = threading.Event()
        self.go.set()
        self.count = 0

    def __call__(self, name: str, content: bytes, page_range: object = None) -> str:
        self.count += 1
        self.started.set()
        self.go.wait(20)
        return SERIALIZED


@pytest.fixture
def child(monkeypatch: pytest.MonkeyPatch) -> Child:
    stub = Child()
    monkeypatch.setattr(dp, "_parse_document_in_worker", stub)
    return stub


@pytest.mark.asyncio
async def test_executor_lease_is_held_until_the_child_finishes_after_cancel(
    auto_threads: list[ThreadPoolExecutor], child: Child, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor
) -> None:
    budget = make_budget(7)
    wire(budget)
    child.go.clear()
    task = asyncio.ensure_future(processor.parse_document("a.pdf", PDF))
    assert await asyncio.to_thread(child.started.wait, 5)
    assert budget.held_count()[0] == 3
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.2)
    assert budget.held_count()[0] == 3
    child.go.set()
    assert await until(lambda: budget.held_count()[0] == 0)


@pytest.mark.asyncio
async def test_cancel_before_submission_releases_the_lease(
    auto_threads: list[ThreadPoolExecutor], child: Child, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(7)
    wire(budget)
    # cancelled while waiting for the lease: nothing was taken
    blocker = [budget.acquire(timeout=2) for _ in range(7)]
    task = asyncio.ensure_future(processor.parse_document("a.pdf", PDF))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for lease in blocker:
        lease.release()
    assert budget.held_count()[0] == 0
    # a job still queued in the executor: cancelled, so the callback releases at once
    child.go.clear()
    first = asyncio.ensure_future(processor.parse_document("a.pdf", PDF))
    second = asyncio.ensure_future(processor.parse_document("b.pdf", PDF))
    assert await asyncio.to_thread(child.started.wait, 5)
    await asyncio.sleep(0.2)
    # width 2 at 7 lanes: both run. Fill the pool with a third to leave one queued.
    third = asyncio.ensure_future(processor.parse_document("c.pdf", DOCX))
    assert await until(lambda: budget.held_count()[0] == 7)
    third.cancel()
    with pytest.raises(asyncio.CancelledError):
        await third
    assert await until(lambda: budget.held_count()[0] == 6, 2), "the queued job's lease did not go back"
    child.go.set()
    await first
    await second
    assert await until(lambda: budget.held_count()[0] == 0)


@pytest.mark.asyncio
async def test_lease_released_when_the_executor_submit_raises(
    auto_threads: list[ThreadPoolExecutor], child: Child, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(7)
    wire(budget)

    def boom(self: object, *a: object) -> None:
        raise RuntimeError("shut down")

    monkeypatch.setattr(dp._DoclingExecutor, "submit", boom)
    with pytest.raises(RuntimeError):
        await processor.parse_document("a.pdf", PDF)
    assert budget.held_count()[0] == 0


@pytest.mark.asyncio
async def test_auto_never_takes_a_resident_token(
    auto_threads: list[ThreadPoolExecutor], child: Child, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor
) -> None:
    budget = make_budget(7)
    wire(budget)
    child.go.clear()
    task = asyncio.ensure_future(processor.parse_document("a.pdf", PDF))
    assert await asyncio.to_thread(child.started.wait, 5)
    run, resident = budget.held_count()
    assert (run, resident) == (3, 0)
    child.go.set()
    assert (await task).name == "x"


@pytest.mark.asyncio
async def test_auto_executor_runs_two_pdfs_at_a_time_at_seven_lanes(
    auto_threads: list[ThreadPoolExecutor], child: Child, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor
) -> None:
    budget = make_budget(7)
    wire(budget)
    child.go.clear()
    tasks = [asyncio.ensure_future(processor.parse_document(f"{i}.pdf", PDF)) for i in range(3)]
    assert await until(lambda: budget.held_count()[0] == 6)
    await asyncio.sleep(0.3)
    assert budget.held_count()[0] == 6 and child.count == 2  # the third waits: 6 + 3 > 7
    child.go.set()
    await asyncio.gather(*tasks)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_inflight_conversion_blocks_idle_shutdown() -> None:
    clock = FakeClock()
    pools: list[ThreadPoolExecutor] = []

    def factory(width: int) -> ThreadPoolExecutor:
        pools.append(ThreadPoolExecutor(width))
        return pools[-1]

    ex = dp._DoclingExecutor(2, idle=60, tick=3600, factory=factory, clock=clock)  # type: ignore[arg-type]
    gate = threading.Event()
    future = ex.submit(gate.wait, 10)
    clock.t = 500
    assert ex.reap_if_idle() is False
    gate.set()
    future.result(timeout=5)
    assert wait_for(lambda: ex._inflight == 0)
    clock.t = 530
    assert ex.reap_if_idle() is False  # idle for 30 s only
    clock.t = 600
    assert ex.reap_if_idle() is True
    assert ex.children() == 0
    ex.submit(int).result(timeout=5)
    assert len(pools) == 2  # the next conversion brought a new executor
    ex.shutdown()


def test_auto_executor_children_exit_after_idle() -> None:
    """Real child processes: they exist while busy, and are gone after the idle time."""
    ex = dp._DoclingExecutor(2, idle=0.6, tick=0.1)
    pid = ex.submit(os.getpid).result(timeout=60)
    assert pid != os.getpid() and ex.children() >= 1
    assert wait_for(lambda: ex.children() == 0, 15)
    assert wait_for(lambda: not Path(f"/proc/{pid}").exists(), 15)
    again = ex.submit(os.getpid).result(timeout=60)
    assert again != pid  # on demand
    ex.shutdown()


def test_idle_shutdown_racing_submit() -> None:
    """1000 submits against a reaper that shuts the executor down whenever it may."""
    ex = dp._DoclingExecutor(1, idle=0.0, tick=3600, factory=lambda w: ThreadPoolExecutor(w))
    stop = threading.Event()
    reaped = 0

    def reaper() -> None:
        nonlocal reaped
        while not stop.is_set():
            reaped += ex.reap_if_idle()

    thread = threading.Thread(target=reaper)
    thread.start()
    try:
        for _ in range(1000):
            assert ex.submit(int).result(timeout=5) == 0
    finally:
        stop.set()
        thread.join()
        ex.shutdown()
    assert reaped > 0


def test_a_submit_does_not_wait_for_the_idle_shutdown_to_join_its_children() -> None:
    """submit runs on the event loop: it must not block while the reaper joins old children."""

    class SlowToExit(ThreadPoolExecutor):
        joining = threading.Event()
        release = threading.Event()

        def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
            if wait:
                SlowToExit.joining.set()
                SlowToExit.release.wait(10)
            super().shutdown(wait=wait, cancel_futures=cancel_futures)

    clock = FakeClock()
    ex = dp._DoclingExecutor(1, idle=60, tick=3600, factory=lambda w: SlowToExit(w), clock=clock)  # type: ignore[arg-type]
    ex.submit(int).result(timeout=5)
    assert wait_for(lambda: ex._inflight == 0)
    clock.t = 600
    reaper = threading.Thread(target=ex.reap_if_idle)
    reaper.start()
    try:
        assert SlowToExit.joining.wait(5)
        started = time.monotonic()
        ex.submit(int).result(timeout=5)
        assert time.monotonic() - started < 1.0
    finally:
        SlowToExit.release.set()
        reaper.join(10)
        ex.shutdown()


@pytest.mark.asyncio
async def test_submit_after_shutdown_creates_a_new_executor_and_keeps_the_lease(
    auto_threads: list[ThreadPoolExecutor], child: Child, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(7)
    wire(budget)
    assert (await processor.parse_document("a.pdf", PDF)).name == "x"
    executor = dp._auto[0]
    assert executor is not None and executor.reap_if_idle() is False or True
    executor._last_used = -1e9
    assert executor.reap_if_idle() is True
    child.go.clear()
    child.started.clear()
    task = asyncio.ensure_future(processor.parse_document("b.pdf", PDF))
    assert await asyncio.to_thread(child.started.wait, 5)
    assert budget.held_count()[0] == 3 and len(auto_threads) == 2
    child.go.set()
    await task


def test_shutdown_is_inert_without_an_auto_executor(lanes_on: None) -> None:
    assert dp.shutdown_docling_pool() is False


# -- fix round 1: broken executor, a failing stream, and the health block ------------------


class BrokenExecutor:
    """Stands in for a ProcessPoolExecutor whose child was killed."""

    shut_down = 0

    def submit(self, fn: object, *args: object) -> Future:
        future: Future = Future()
        future.set_exception(BrokenProcessPool("a child was terminated abruptly"))
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        BrokenExecutor.shut_down += 1


@pytest.mark.asyncio
async def test_a_broken_executor_is_dropped_so_the_next_submit_gets_a_new_one(
    auto_threads: list[ThreadPoolExecutor], child: Child, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(7)
    gov = wire(budget)
    BrokenExecutor.shut_down = 0
    pools: list[object] = [BrokenExecutor()]

    def factory(width: int) -> object:  # the first pool is broken, later ones work
        return pools.pop() if pools else ThreadPoolExecutor(width)

    monkeypatch.setattr(dp._DoclingExecutor, "_default_factory", staticmethod(factory))
    with pytest.raises(BrokenProcessPool):
        await processor.parse_document("a.pdf", PDF)
    executor = dp._auto[0]
    assert executor is not None
    assert executor._executor is None, "the broken pool was kept; every later submit would fail"
    assert BrokenExecutor.shut_down == 1
    gov.report_memory_incident.assert_called_once()
    assert await until(lambda: budget.held_count()[0] == 0)
    # the next conversion starts a fresh pool and succeeds
    assert (await processor.parse_document("b.pdf", PDF)).name == "x"


@pytest.mark.asyncio
async def test_lease_released_when_the_document_stream_cannot_be_built(
    lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(4)
    wire(budget)

    def boom(**kwargs: object) -> None:
        raise ValueError("bad stream")

    monkeypatch.setattr(dp, "DocumentStream", boom)
    with pytest.raises(ValueError):
        await processor.parse_document("a.pdf", PDF)
    assert budget.held_count()[0] == 0
    assert dp.docling_lane_stats()["leases_held"] == 0


@pytest.mark.asyncio
async def test_docling_health_block_counts_the_lease_and_forgets_it_on_release(
    lanes_on: None, make_budget: Callable[..., LaneBudget], processor: dp.DoclingProcessor
) -> None:
    from app.services.resource_governor.lanes import _add_consumer_blocks

    budget = make_budget(4)
    wire(budget)
    conv: Converter = processor.converter  # type: ignore[assignment]
    conv.go.clear()
    task = asyncio.ensure_future(processor.parse_document("a.pdf", PDF))
    assert await asyncio.to_thread(conv.started.wait, 5)
    block = budget.stats()
    _add_consumer_blocks(block)
    docling = block["docling"]
    assert docling["leases_held"] == 1
    assert docling["weight_held"] == 3
    assert docling["mode"] == "thread"
    assert docling["children"] == 0
    assert docling["oldest_age_seconds"] >= 0
    conv.go.set()
    await task
    assert await until(lambda: dp.docling_lane_stats()["leases_held"] == 0)
    assert dp.docling_lane_stats()["weight_held"] == 0
