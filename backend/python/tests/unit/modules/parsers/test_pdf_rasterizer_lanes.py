"""The PDF rasterizer on the lane budget (T55 to T59, and the round-5 raster items)."""
from __future__ import annotations

import ast
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.parse_pool import ParseAbandonedError, ParseWorkerError
from app.modules.parsers.pdf import pdf_rasterizer as raster
from app.services.resource_governor.lanes import LaneBudget, lane_scope, plan_lanes
from tests.unit.lane_gauge import Mem
from tests.unit.modules.parsers.lane_pool_support import (
    double,
    pid_after,
    resident_workers,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.timeout(180)
GIB = 2**30


def wait_for(predicate: Callable[[], bool], seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class FakeExecutor:
    created = 0

    def __init__(self, **kwargs: object) -> None:
        FakeExecutor.created += 1
        self.submitted: list[object] = []

    def submit(self, fn: Callable[..., object], *args: object) -> object:
        from concurrent.futures import Future

        self.submitted.append(args[0])
        future: Future = Future()
        future.set_result(args[0](*args[1:]))
        return future


@pytest.fixture
def fake_executor(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeExecutor]]:
    FakeExecutor.created = 0
    raster._get_pdf_raster_pool.cache_clear()
    monkeypatch.setattr(raster, "ProcessPoolExecutor", FakeExecutor)
    yield FakeExecutor
    raster._get_pdf_raster_pool.cache_clear()


class Budget:
    """A budget that fails the test if anything asks it for a lane."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def lanes(self) -> Budget:
        return self

    def __getattr__(self, name: str) -> object:
        if name.startswith(("try_acquire", "acquire")):
            def spy(*a: object, **k: object) -> None:
                self.calls.append(name)
                raise AssertionError(f"{name} called inside a lane")
            return spy
        raise AttributeError(name)


def governor(budget: object, heavy: int = 4) -> SimpleNamespace:
    return SimpleNamespace(
        lanes=budget, ceilings=SimpleNamespace(heavy=heavy), fleet_ceilings=SimpleNamespace(heavy=heavy),
        report_memory_incident=MagicMock(),
    )


@pytest.fixture
def lanes_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[set[int]]:
    """Lanes on; yields the pids of the children that already exist.

    An earlier test in the same process may have left the legacy raster executor (and its
    children) behind. Shut it down first, so the cached-pool assertions start from zero, and
    let a test judge only the children that appear after this point.
    """
    raster.shutdown_pdf_raster_pool()
    baseline = set(_child_pids())
    monkeypatch.setenv("PARSE_LANES", "on")
    monkeypatch.delenv("PARSE_POOL_WORKERS", raising=False)
    monkeypatch.delenv("PDF_RASTER_WORKERS", raising=False)
    monkeypatch.delenv("PIPESHUB_LANE_WORKER", raising=False)
    monkeypatch.delenv("PIPESHUB_LANE_RASTER_INPROC", raising=False)
    saved = parse_pool._State.governor
    yield baseline
    parse_pool.shutdown_parse_pool()
    parse_pool._State.governor = saved
    raster.set_resource_governor(None)


@pytest.fixture
def make_budget(tmp_path: Path) -> Iterator[Callable[..., LaneBudget]]:
    made: list[LaneBudget] = []

    def factory(lanes: int = 2) -> LaneBudget:
        budget = LaneBudget.open(
            plan_lanes(cpus=lanes * 2, heavy=lanes * 2), tmp_path / "lanes",
            memory_reader=Mem(1000 * GIB, 0), warmup_seconds=0.0, view_ttl=0.0, alive=lambda pid: True,
        )
        made.append(budget)
        return budget

    yield factory
    for budget in made:
        budget.close()


def wire(budget: object, heavy: int = 4) -> SimpleNamespace:
    gov = governor(budget, heavy)
    parse_pool.set_resource_governor(gov)  # type: ignore[arg-type]
    raster.set_resource_governor(gov)  # type: ignore[arg-type]
    return gov


# -- T55, T56: no second lease inside a lane ------------------------------------


def test_raster_in_lane_worker_takes_no_lease_and_never_routes_to_the_pool(
    lanes_on: None, monkeypatch: pytest.MonkeyPatch, fake_executor: type[FakeExecutor]
) -> None:
    """T55: the marker env means the process is a lane worker; the nested executor is used as before."""
    monkeypatch.setenv("PIPESHUB_LANE_WORKER", "1")
    budget = Budget()
    wire(budget)

    def forbidden(*a: object, **k: object) -> None:
        raise AssertionError("a render inside a lane entered the parse pool")

    monkeypatch.setattr(parse_pool, "run_blocking", forbidden)
    jobs = raster.raster_jobs()
    assert raster._run_in_pool(double, 21) == 42
    assert fake_executor.created == 1
    assert not budget.calls and raster.raster_jobs() == jobs


def test_lane_raster_inproc_knob_renders_in_the_worker_process(
    lanes_on: None, monkeypatch: pytest.MonkeyPatch, fake_executor: type[FakeExecutor]
) -> None:
    monkeypatch.setenv("PIPESHUB_LANE_WORKER", "1")
    monkeypatch.setenv("PIPESHUB_LANE_RASTER_INPROC", "1")
    budget = Budget()
    wire(budget)
    assert raster._run_in_pool(double, 4) == 8
    assert fake_executor.created == 0  # no nested executor
    assert not budget.calls  # and no lease


def test_lane_raster_inproc_knob_is_ignored_outside_a_lane_worker_and_in_off(
    lanes_on: None, monkeypatch: pytest.MonkeyPatch, fake_executor: type[FakeExecutor]
) -> None:
    monkeypatch.setenv("PIPESHUB_LANE_RASTER_INPROC", "1")
    assert raster._lane_route() is None  # not a worker, no governor
    monkeypatch.setenv("PIPESHUB_LANE_WORKER", "1")
    monkeypatch.setenv("PARSE_LANES", "off")
    assert raster._lane_route() is None
    assert raster._run_in_pool(double, 1) == 2
    assert fake_executor.created == 1


def test_raster_in_lane_scope_does_not_deadlock_at_budget_1(
    lanes_on: None, make_budget: Callable[..., LaneBudget], fake_executor: type[FakeExecutor]
) -> None:
    """T56: with the only lane held by the caller's own lease, a render in its scope completes."""
    budget = make_budget(1)
    wire(budget)
    lease = budget.acquire(timeout=2)
    out: list[int] = []

    def run() -> None:
        with lane_scope():
            out.append(raster._run_in_pool(double, 5))

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "the render waited for the lane its caller holds"
    assert out == [10]
    assert budget.held_count()[0] == 1  # still only the caller's lease
    lease.release()


def test_in_scope_raster_does_not_create_the_service_executor(
    lanes_on: None, make_budget: Callable[..., LaneBudget], fake_executor: type[FakeExecutor]
) -> None:
    wire(make_budget(2))
    with lane_scope():
        assert raster._run_in_pool(double, 2) == 4
    assert fake_executor.created == 0
    assert raster._get_pdf_raster_pool.cache_info().currsize == 0


def test_in_scope_renders_take_turns_in_process(
    lanes_on: None, make_budget: Callable[..., LaneBudget], fake_executor: type[FakeExecutor]
) -> None:
    """pdfium is not thread-safe, so lane threads rendering in this process never overlap."""
    wire(make_budget(4))
    active = 0
    peak = 0
    lock = threading.Lock()

    def render(_: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return 1

    def run() -> None:
        with lane_scope():
            raster._run_in_pool(render, 0)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1


def test_no_thread_hop_reachable_from_lane_fallback() -> None:
    """A thread hop on the layout path would lose the lane scope and then wait for the lane its parent holds."""
    root = Path(raster.__file__).parent
    banned = {"to_thread", "run_in_executor", "ThreadPoolExecutor", "Thread", "create_task", "ensure_future"}
    for name in ("opencv_layout_analyzer.py", "layout_pages.py", "table_detector.py", "pdf_rasterizer.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if called not in banned:
                    continue
                # The async entry point hops once, before any lane scope exists, and its legacy
                # branch uses the old executor; neither is reachable from a lane thread.
                assert name == "pdf_rasterizer.py" and called in {"to_thread", "run_in_executor"}, f"{name}:{node.lineno} {called}"


# -- T57: service-process render is a lane job ---------------------------------


def test_service_raster_runs_on_a_lane_worker(lanes_on: None, make_budget: Callable[..., LaneBudget]) -> None:
    budget = make_budget(2)
    wire(budget)
    before = raster.raster_jobs()
    seen: list[int] = []
    sampler_stop = threading.Event()

    def sample() -> None:
        while not sampler_stop.is_set():
            seen.append(budget.held_count()[0])
            time.sleep(0.01)

    thread = threading.Thread(target=sample)
    thread.start()
    try:
        pid = raster._run_in_pool(pid_after, 0.6)
    finally:
        sampler_stop.set()
        thread.join()
    assert pid != os.getpid()
    assert pid in resident_workers()
    assert max(seen) >= 1  # the render held a run lease: the gauge counts it
    assert raster.raster_jobs() == before + 1
    assert _cmdline(pid).count("parse_worker") == 1


def _cmdline(pid: int) -> str:
    return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()


def test_raster_job_crash_fails_only_that_job(lanes_on: None, make_budget: Callable[..., LaneBudget]) -> None:
    budget = make_budget(2)
    gov = wire(budget)
    first = raster._run_in_pool(pid_after, 0.0)
    with pytest.raises(ParseWorkerError) as raised:
        raster._run_in_pool(os._exit, 3)
    assert raised.value.cause == "crashed"
    gov.report_memory_incident.assert_called_once()
    assert wait_for(lambda: budget.held_count() == (0, 0))
    second = raster._run_in_pool(pid_after, 0.0)
    assert second != first and second in resident_workers()


# -- T58: legacy paths ---------------------------------------------------------


@pytest.mark.parametrize("case", ["off", "no_pool", "explicit", "no_governor"])
def test_raster_legacy_paths_unchanged(
    lanes_on: None, monkeypatch: pytest.MonkeyPatch, make_budget: Callable[..., LaneBudget],
    fake_executor: type[FakeExecutor], case: str,
) -> None:
    budget = Budget()
    if case == "off":
        monkeypatch.setenv("PARSE_LANES", "off")
    if case == "no_pool":
        monkeypatch.setenv("PARSE_POOL_WORKERS", "0")
    if case == "explicit":
        monkeypatch.setenv("PDF_RASTER_WORKERS", "3")
    if case != "no_governor":
        wire(budget)
    assert raster._lane_route() is None
    assert raster._run_in_pool(double, 3) == 6
    assert fake_executor.created == 1
    assert not budget.calls


def test_explicit_raster_pool_is_private(lanes_on: None, monkeypatch: pytest.MonkeyPatch, make_budget: Callable[..., LaneBudget], fake_executor: type[FakeExecutor]) -> None:
    monkeypatch.setenv("PDF_RASTER_WORKERS", "2")
    budget = make_budget(2)
    wire(budget)
    assert raster._run_in_pool(double, 3) == 6
    assert budget.held_count() == (0, 0)  # no lease, no token


# -- T59: nothing persistent in the service process ----------------------------


def _pdf(path: Path, pages: int) -> Path:
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path))
    for number in range(pages):
        pdf.drawString(72, 720, f"page {number}")
        pdf.showPage()
    pdf.save()
    return path


def test_no_raster_executor_in_the_service_process_under_on(
    lanes_on: set[int], make_budget: Callable[..., LaneBudget], tmp_path: Path
) -> None:
    budget = make_budget(2)
    wire(budget)
    pdf = _pdf(tmp_path / "t.pdf", 4)
    errors: list[BaseException] = []

    def one() -> None:
        try:
            pages = raster.render_batch_from_path_sync(str(pdf), [1, 2], 36)
            assert sorted(pages) == [1, 2]
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=one) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=150)
    assert not errors, errors
    assert raster._get_pdf_raster_pool.cache_info().currsize == 0
    # The multiprocessing resource tracker belongs to the test session, not to a raster pool.
    children = [c for pid, c in _children().items() if pid not in lanes_on and "resource_tracker" not in c]
    assert children and all("parse_worker" in c for c in children), children
    assert len(children) <= 2  # lanes + P - 1


def _children() -> dict[int, str]:
    """Live (not zombie) children of this process: pid to command line."""
    out = {}
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                fields = Path(f"/proc/{name}/stat").read_bytes().rsplit(b")", 1)[-1].split()
                if int(fields[1]) == os.getpid() and fields[0] != b"Z":
                    out[int(name)] = _cmdline(int(name))
            except (OSError, ValueError):
                continue
    return out


def _child_pids() -> list[int]:
    return list(_children())


def test_async_render_all_pages_goes_to_a_lane(lanes_on: set[int], make_budget: Callable[..., LaneBudget], tmp_path: Path) -> None:
    import asyncio

    wire(make_budget(2))
    pdf = _pdf(tmp_path / "a.pdf", 2)
    pages = asyncio.run(raster.render_all_pages_from_bytes(pdf.read_bytes(), 36))
    assert sorted(pages) == [1, 2]
    assert raster._get_pdf_raster_pool.cache_info().currsize == 0
    new = [c for pid, c in _children().items() if pid not in lanes_on and "resource_tracker" not in c]
    assert all("parse_worker" in c for c in new), new


# -- round 5: cancel hook and heap ----------------------------------------------


def test_cancelled_render_leaves_no_thread_or_lease(lanes_on: None, make_budget: Callable[..., LaneBudget]) -> None:
    budget = make_budget(1)
    wire(budget)
    blocker = budget.acquire(timeout=2)
    stop = threading.Event()
    result: list[BaseException] = []

    def run() -> None:
        with raster.cancel_on(stop):
            try:
                raster._run_in_pool(pid_after, 0.0)
            except BaseException as exc:
                result.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert wait_for(lambda: budget.stats()["waiting"] == 1, 30)
    stop.set()
    thread.join(timeout=15)
    assert not thread.is_alive(), "the waiting render thread outlived its cancel"
    assert result and isinstance(result[0], ParseAbandonedError)
    assert budget.held_count()[0] == 1  # only the blocker
    blocker.release()
    assert wait_for(lambda: budget.held_count()[0] == 0)


def _rss_mib(pid: int) -> float:
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    raise AssertionError


def test_render_job_trims_the_worker_heap(lanes_on: None, make_budget: Callable[..., LaneBudget], tmp_path: Path) -> None:
    """A warm worker that rendered 32 pages is back near its size before, not holding the peak."""
    wire(make_budget(2))
    pdf = _pdf(tmp_path / "big.pdf", 32)
    pid = raster._run_in_pool(pid_after, 0.0)
    before = _rss_mib(pid)
    pages = raster.render_all_pages_from_path_sync(str(pdf), 150)
    assert len(pages) == 32
    del pages
    assert wait_for(lambda: _rss_mib(pid) <= before + 100, 15), (before, _rss_mib(pid))
