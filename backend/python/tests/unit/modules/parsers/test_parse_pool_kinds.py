"""The parse pool charges a worker by the kind of job it runs (PX-2e, T78, T80, T84, T85).

Stub workers keep the interleavings deterministic; the budget, the ledger file and the pool are
the real ones. Time is a fake clock the tests advance.
"""
from __future__ import annotations

import ast
import asyncio
import functools
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.modules.parsers import parse_pool, parse_pool_lanes
from app.modules.parsers.parse_pool import (
    ParseAbandonedError,
    ParseWorkerError,
    _Job,
    _WorkerUnavailable,
)
from app.modules.parsers.parse_pool_lanes import LanePool
from app.services.resource_governor.kinds import SCALES, ParseKind
from app.services.resource_governor.lanes import LaneBudget, plan_lanes
from tests.unit.lane_gauge import FakeClock, Mem
from tests.unit.modules.parsers.lane_pool_support import StubHub, wait_for

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

GIB = 2**30
MIB = 2**20
pytestmark = pytest.mark.timeout(60)
APP = Path(__file__).resolve().parents[4] / "app"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(parse_pool_lanes, "_SETTLE_SECONDS", 0.1)
    monkeypatch.delenv("GOVERNOR_LANE_WORKING_SET_GB", raising=False)
    monkeypatch.delenv("LANE_WAIT_SECONDS", raising=False)
    SCALES.reset()
    yield
    SCALES.reset()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(1000.0)


@pytest.fixture
def hub() -> StubHub:
    return StubHub()


@pytest.fixture
def make_budget(tmp_path: Path) -> Iterator[Callable[..., LaneBudget]]:
    made: list[LaneBudget] = []

    def factory(lanes: int = 2, *, clock: FakeClock | None = None, mem: Mem | None = None, **options: object) -> LaneBudget:
        options.setdefault("memory_reader", mem or Mem(1000 * GIB, 0))
        options.setdefault("warmup_seconds", 60.0)
        options.setdefault("view_ttl", 0.0)
        options.setdefault("alive", lambda pid: True)
        if clock is not None:
            options["clock"] = clock
        budget = LaneBudget.open(plan_lanes(cpus=lanes * 2, heavy=lanes * 2), tmp_path / f"l{len(made)}", **options)
        made.append(budget)
        return budget

    yield factory
    for budget in made:
        budget.close()


@pytest.fixture
def make_pool() -> Iterator[Callable[..., LanePool]]:
    made: list[LanePool] = []

    def factory(budget: LaneBudget, *, threads: int = 3, hub: StubHub | None = None, tick: float = 3600.0, **options: object) -> LanePool:
        budget.register_presence()
        options.setdefault("clock", budget.now)
        pool = LanePool(
            kind="budgeted", threads=threads, budget=budget, admit=True,
            worker_factory=hub.factory if hub is not None else None, reap_tick=tick, **options,
        )
        made.append(pool)
        return pool

    yield factory
    for pool in made:
        pool.shutdown()


def submit(pool: LanePool, fn: Callable, *args: object, kind: ParseKind | None = None, covered: bool = False,
           size: int = 0, timeout: float | None = None) -> _Job:
    job = _Job(fn, args, "t", size, timeout, kind=kind, covered=covered)
    pool.enqueue(job)
    return job


def hold(event: threading.Event) -> str:
    event.wait(30)
    return "held"


def quick() -> int:
    return 1


def residents(budget: LaneBudget) -> int:
    return budget.held_count()[1]


def entries(pool: LanePool) -> list:
    with pool._lock:
        return [*pool._idle, *pool._out]


# -- T78: Excel ------------------------------------------------------------------------------------------


def test_excel_load_is_free_when_the_permit_covers_it(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    """Not resident worker memory (charge 0), but visible in the domain's in-flight window."""
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    release = threading.Event()
    job = submit(pool, hold, release, kind=ParseKind.EXCEL_LOAD, covered=True, size=20 * MIB)
    assert wait_for(lambda: entries(pool))
    (entry,) = entries(pool)
    assert entry.charged_bytes == 0
    assert budget.stats()["window_bytes"] == min(1536 * MIB, 3 * 480 * MIB) or budget.stats()["window_bytes"] >= 1536 * MIB
    release.set()
    assert job.future.result(timeout=10) == "held"


def test_excel_load_without_a_permit_is_charged_the_load_max(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    release = threading.Event()
    job = submit(pool, hold, release, kind=ParseKind.EXCEL_LOAD, covered=False, size=20 * MIB)
    assert wait_for(lambda: entries(pool))
    (entry,) = entries(pool)
    assert entry.charged_bytes == 1536 * MIB
    release.set()
    job.future.result(timeout=10)


def test_excel_worker_is_retired_after_its_load(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    assert submit(pool, quick, kind=ParseKind.EXCEL_LOAD, covered=True).future.result(timeout=10) == 1
    assert wait_for(lambda: residents(budget) == 0), "the token returns once the process is gone"
    assert pool.stats()["idle"] == 0, "it never joins the warm list"
    (worker,) = hub.workers
    assert worker.stopped


def test_excel_load_never_takes_a_warm_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    assert submit(pool, quick, kind=ParseKind.PDF_LAYOUT).future.result(timeout=10) == 1
    assert pool.stats()["idle"] == 1
    assert submit(pool, quick, kind=ParseKind.EXCEL_LOAD, covered=True).future.result(timeout=10) == 1
    assert len(hub.workers) == 2, "the loader got a worker of its own"
    assert pool.stats()["idle"] == 1, "and the layout worker is still the only warm one"
    assert hub.violations == []


def test_a_layout_job_after_an_excel_load_gets_a_fresh_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    submit(pool, quick, kind=ParseKind.EXCEL_LOAD).future.result(timeout=10)
    submit(pool, quick, kind=ParseKind.PDF_LAYOUT).future.result(timeout=10)
    assert len(hub.workers) == 2 and hub.workers[0].stopped and not hub.workers[1].stopped


def test_excel_batch_through_the_pool_is_not_throttled_by_spawn_allowance(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    """20 loads on 32 lanes (an allowance of 8 cold spawns per warm-up window) all run at once, none waits."""
    budget = make_budget(32, mem=Mem(100 * GIB, GIB))
    pool = make_pool(budget, hub=hub, threads=20)
    assert submit(pool, quick, kind=ParseKind.PDF_LAYOUT).future.result(timeout=10) == 1  # something is resident
    started = time.monotonic()
    jobs = [submit(pool, quick, kind=ParseKind.EXCEL_LOAD, covered=True, size=MIB) for _ in range(20)]
    assert [j.future.result(timeout=15) for j in jobs] == [1] * 20
    assert time.monotonic() - started < 10, "the loads waited for the spawn window"
    assert budget.stats()["spawns_in_window"] == 1, "only the layout worker's cold start is counted"


# -- T80: growth in the pool ------------------------------------------------------------------------------


def test_a_heavier_job_that_takes_a_worker_freed_while_its_spawn_was_refused_is_charged_growth(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    """A spawn refused for memory takes the worker another job returns (the idle take in ``admit``);
    that worker was charged for a text job, so the PDF layout job must still be charged its growth."""
    mem = Mem(4 * GIB, 0)
    budget = make_budget(2, mem=mem)
    pool = make_pool(budget, hub=hub)
    release = threading.Event()
    text = submit(pool, hold, release, kind=ParseKind.TEXT, size=100 * 1024)
    assert wait_for(lambda: pool.stats()["busy"] == 1)
    mem.at(0.9)
    time.sleep(1.2)
    pdf = submit(pool, quick, kind=ParseKind.PDF_LAYOUT, size=2 * MIB)
    time.sleep(0.4)
    assert len(hub.workers) == 1 and not pdf.future.done(), "the PDF job's spawn must be refused"

    release.set()
    text.future.result(timeout=10)
    pdf.future.result(timeout=10)

    (entry,) = entries(pool)
    assert len(hub.workers) == 1
    assert entry.charged_bytes >= int(1.25 * GIB)
    assert budget.stats()["growth_by_kind"].get(ParseKind.PDF_LAYOUT.value)


def test_heavier_job_on_a_light_worker_is_admitted_as_growth(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    submit(pool, quick, kind=ParseKind.TEXT, size=100 * 1024).future.result(timeout=10)
    (light,) = entries(pool)
    light_charge = light.charged_bytes
    assert light_charge == int(0.10 * GIB)
    before = budget.stats()["window_bytes"]
    tokens = budget.held_count()[1]
    release = threading.Event()
    job = submit(pool, hold, release, kind=ParseKind.PDF_LAYOUT, size=2 * MIB)
    assert wait_for(lambda: pool.stats()["busy"] == 1)
    (entry,) = entries(pool)
    assert entry is light, "the warm worker took the job"
    assert entry.charged_bytes == int(1.25 * GIB) + 2 * MIB
    assert budget.stats()["window_bytes"] == before + entry.charged_bytes - light_charge
    assert budget.held_count()[1] == tokens, "growth takes no token"
    assert len(hub.workers) == 1
    release.set()
    job.future.result(timeout=10)
    assert budget.stats()["growth_by_kind"]["pdf_layout"]["growth"] == 1


def test_growth_is_charged_once_per_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    submit(pool, quick, kind=ParseKind.TEXT, size=10 * 1024).future.result(timeout=10)
    for _ in range(3):
        submit(pool, quick, kind=ParseKind.PDF_LAYOUT, size=MIB).future.result(timeout=10)
    assert budget.stats()["growth_by_kind"]["pdf_layout"]["growth"] == 1
    (entry,) = entries(pool)
    window = budget.stats()["window_bytes"]
    submit(pool, quick, kind=ParseKind.TEXT, size=10 * 1024).future.result(timeout=10)
    assert entry.charged_bytes == int(1.25 * GIB) + MIB, "the mark is a high-water mark: it never goes down"
    assert budget.stats()["window_bytes"] == window


def test_render_on_warm_layout_worker_charges_no_growth_when_highwater_covers(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    submit(pool, quick, kind=ParseKind.PDF_LAYOUT, size=MIB).future.result(timeout=10)
    window = budget.stats()["window_bytes"]
    submit(pool, quick, kind=ParseKind.RENDER, size=MIB).future.result(timeout=10)
    assert budget.stats()["window_bytes"] == window
    assert "render" not in budget.stats()["growth_by_kind"]


def test_growth_denied_waits_without_a_token_and_is_cancellable(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    mem = Mem(10 * GIB, GIB)
    budget = make_budget(4, clock=clock, mem=mem)
    pool = make_pool(budget, hub=hub, clock=clock)
    first, second = threading.Event(), threading.Event()
    job_a = submit(pool, hold, first, kind=ParseKind.TEXT, size=10 * 1024)
    job_b = submit(pool, hold, second, kind=ParseKind.TEXT, size=10 * 1024)
    assert wait_for(lambda: pool.stats()["busy"] == 2)
    first.set()
    job_a.future.result(timeout=10)
    assert pool.stats()["idle"] == 1
    mem.raw = int(9 * GIB)
    clock.advance(1.1)
    tokens = budget.held_count()[1]
    window = budget.stats()["window_bytes"]
    heavy = submit(pool, quick, kind=ParseKind.PDF_LAYOUT, size=MIB)
    assert wait_for(lambda: pool._spawn_waiters == 1), "the job waits for the growth"
    time.sleep(0.3)
    assert not heavy.future.done()
    assert budget.held_count()[1] == tokens, "and holds no token while it waits"
    assert budget.stats()["window_bytes"] == window, "a denial charges nothing"
    heavy.cancelled.set()
    with pytest.raises(ParseAbandonedError):
        heavy.future.result(timeout=10)
    assert wait_for(lambda: pool._spawn_waiters == 0)
    second.set()
    job_b.future.result(timeout=10)
    assert budget.held_count()[0] == 0, "no lease is left behind"


def test_growth_denied_times_out_as_unavailable(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub,
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANE_WAIT_SECONDS", "0.3")
    mem = Mem(10 * GIB, GIB)
    budget = make_budget(4, clock=clock, mem=mem)
    pool = make_pool(budget, hub=hub, clock=clock)
    first, second = threading.Event(), threading.Event()
    a = submit(pool, hold, first, kind=ParseKind.TEXT, size=1024)
    b = submit(pool, hold, second, kind=ParseKind.TEXT, size=1024)
    assert wait_for(lambda: pool.stats()["busy"] == 2)
    first.set()
    a.future.result(timeout=10)
    mem.raw = 9 * GIB
    clock.advance(1.1)
    with pytest.raises(ParseWorkerError) as raised:
        submit(pool, quick, kind=ParseKind.PDF_LAYOUT, size=MIB).future.result(timeout=10)
    assert raised.value.cause == "unavailable"
    second.set()
    b.future.result(timeout=10)


def test_growth_exempt_when_the_worker_is_alone(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(2, clock=clock, mem=Mem(10 * GIB, int(9.5 * GIB)))
    pool = make_pool(budget, hub=hub, clock=clock)
    submit(pool, quick, kind=ParseKind.TEXT, size=1024).future.result(timeout=10)
    clock.advance(1.1)
    assert submit(pool, quick, kind=ParseKind.PDF_LAYOUT, size=MIB).future.result(timeout=10) == 1
    assert len(hub.workers) == 1


# -- the charge at the spawn --------------------------------------------------------------------------------


def test_a_spawn_is_charged_by_its_kind(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(16)  # a spawn allowance of 4 per warm-up window
    pool = make_pool(budget, hub=hub)
    gates = [threading.Event() for _ in range(3)]
    jobs = [
        submit(pool, hold, gates[0], kind=ParseKind.PDF_LAYOUT, size=MIB),
        submit(pool, hold, gates[1], kind=ParseKind.RENDER, size=MIB),
        submit(pool, hold, gates[2], kind=ParseKind.CODE, size=5 * MIB),
    ]
    assert wait_for(lambda: pool.stats()["busy"] == 3)
    charges = sorted(e.charged_bytes for e in entries(pool))
    assert charges == sorted([int(1.25 * GIB) + MIB, int(0.65 * GIB), int(0.064 * GIB + 64 * 5 * MIB)])
    stats = pool.stats()
    assert stats["charged_resident_bytes"] == sum(charges)
    assert stats["by_kind"] == {"pdf_layout": {"spawns": 1}, "render": {"spawns": 1}, "code": {"spawns": 1}}
    for g in gates:
        g.set()
    for j in jobs:
        j.future.result(timeout=10)


def test_missing_kind_is_charged_the_fallback(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    release = threading.Event()
    job = submit(pool, hold, release, size=30 * 1024)
    assert wait_for(lambda: entries(pool))
    assert entries(pool)[0].charged_bytes == int(1.25 * GIB), "never less than the fallback"
    assert pool.stats()["by_kind"] == {"unknown": {"spawns": 1}}
    release.set()
    job.future.result(timeout=10)


def test_an_explicit_lane_working_set_is_one_flat_charge(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2, working_set_bytes=700 * MIB)
    pool = make_pool(budget, hub=hub)
    for kind in (ParseKind.TEXT, ParseKind.PDF_LAYOUT, ParseKind.EXCEL_LOAD):
        release = threading.Event()
        job = submit(pool, hold, release, kind=kind, size=3 * MIB, covered=kind is ParseKind.EXCEL_LOAD)
        assert wait_for(lambda: pool.stats()["busy"] == 1)
        assert entries(pool)[0].charged_bytes == 700 * MIB, kind
        release.set()
        job.future.result(timeout=10)


# -- T85: every call site declares a kind ---------------------------------------------------------------------


_POOL_MODULE = "app.modules.parsers.parse_pool"
_POOL_FUNCTIONS = ("submit", "run_blocking")


def _pool_calls_in(tree: ast.AST) -> list[ast.Call]:
    """Calls to the pool's ``submit``/``run_blocking`` however the module imported them."""
    modules: set[str] = set()  # names bound to the parse_pool module
    functions: set[str] = set()  # names bound to submit / run_blocking themselves
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _POOL_MODULE and alias.asname:
                    modules.add(alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "app.modules.parsers":
            modules.update(a.asname or a.name for a in node.names if a.name == "parse_pool")
        elif isinstance(node, ast.ImportFrom) and node.module == _POOL_MODULE:
            functions.update(a.asname or a.name for a in node.names if a.name in _POOL_FUNCTIONS)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in functions:
            calls.append(node)
        elif isinstance(func, ast.Attribute) and func.attr in _POOL_FUNCTIONS:
            owner = ast.unparse(func.value)
            if owner in modules or owner == _POOL_MODULE:
                calls.append(node)
    return calls


@functools.cache
def _pool_calls() -> list[tuple[str, int, ast.Call]]:
    """Every pool call site under ``app``, parsed once for the tests below (about 2,000 files)."""
    found = []
    for path in APP.rglob("*.py"):
        if path.name in ("parse_pool.py", "parse_pool_lanes.py"):
            continue
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        found.extend((str(path.relative_to(APP)), call.lineno, call) for call in _pool_calls_in(tree))
    return found


@pytest.mark.parametrize(
    "source",
    [
        "from app.modules.parsers import parse_pool\nparse_pool.submit(f)",
        "from app.modules.parsers import parse_pool as pp\npp.submit(f)",
        "import app.modules.parsers.parse_pool as pp\npp.run_blocking(f)",
        "import app.modules.parsers.parse_pool\napp.modules.parsers.parse_pool.submit(f)",
        "from app.modules.parsers.parse_pool import submit as sub\nsub(f)",
        "from app.modules.parsers.parse_pool import run_blocking\nrun_blocking(f)",
    ],
)
def test_the_call_site_scan_follows_import_aliases(source: str) -> None:
    assert len(_pool_calls_in(ast.parse(source))) == 1


def test_the_call_site_scan_ignores_other_submits() -> None:
    assert _pool_calls_in(ast.parse("executor.submit(f)\nfrom concurrent import futures\nfutures.submit(f)")) == []


@pytest.mark.timeout(240)
def test_every_pool_call_site_declares_a_kind() -> None:
    calls = _pool_calls()
    assert len(calls) >= 10, "the scan found the call sites"
    missing = [f"{p}:{n}" for p, n, call in calls if "kind" not in {k.arg for k in call.keywords}]
    assert not missing, f"pool call sites without a kind: {missing}"


@pytest.mark.timeout(240)
def test_call_sites_use_a_member_of_parse_kind() -> None:
    names = {k.name for k in ParseKind}
    for path, line, call in _pool_calls():
        kind = next(k.value for k in call.keywords if k.arg == "kind")
        assert isinstance(kind, ast.Attribute) and kind.attr in names, f"{path}:{line}"


# -- never in the parent --------------------------------------------------------------------------------------


def test_pdf_layout_never_runs_in_the_parent_under_on(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    hub.fail_start = _WorkerUnavailable("no fork")
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    ran_in = []

    def where() -> str:
        ran_in.append(threading.current_thread().name)
        return "ran"

    with pytest.raises(ParseWorkerError) as raised:
        submit(pool, where, kind=ParseKind.PDF_LAYOUT).future.result(timeout=10)
    assert raised.value.cause == "unavailable"
    assert ran_in == []
    # A kind that is not worker memory keeps the thread fallback.
    assert submit(pool, where, kind=ParseKind.TEXT).future.result(timeout=10) == "ran"


def test_interactive_pool_jobs_are_not_charged_and_say_so_in_health(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(2)
    budgeted = make_pool(budget, hub=hub)
    assert budgeted.stats()["charged"] is True
    interactive = LanePool(
        kind="interactive", threads=1, budget=None, leases=parse_pool_lanes._CounterLeases(lambda: 1),
        worker_factory=hub.factory,
    )
    try:
        job = _Job(quick, (), "chat", MIB, kind=ParseKind.PDF_LAYOUT)
        interactive.enqueue(job)
        assert job.future.result(timeout=10) == 1
        stats = interactive.stats()
        assert stats["charged"] is False
        assert stats["charged_resident_bytes"] == 0 and stats["by_kind"] == {}
        assert budget.stats()["window_bytes"] == 0
    finally:
        interactive.shutdown()


# -- bursts are bounded by the layer that owns the memory -----------------------------------------------------


def test_burst_of_pdf_permits_is_bounded_by_lane_admission(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    """Eight layout jobs on a 10 GiB box with 3 GiB in use: workers are admitted by bytes, not by count."""
    budget = make_budget(32, mem=Mem(10 * GIB, 3 * GIB), warmup_seconds=2.0)  # allowance 8: the bytes are what stops the burst
    pool = make_pool(budget, hub=hub, threads=8)
    release = threading.Event()
    jobs = [submit(pool, hold, release, kind=ParseKind.PDF_LAYOUT, size=MIB) for _ in range(8)]
    assert wait_for(lambda: len(hub.workers) >= 3)
    time.sleep(0.5)
    assert len(hub.alive_workers()) == 3, "3 + 3 x 1.25 GiB is 6.75 of 10 GiB; a fourth is 8.0"
    assert budget.stats()["window_bytes"] > 0
    release.set()
    for job in jobs:
        job.future.result(timeout=30)


async def test_burst_of_docx_permits_is_bounded_by_the_excess_reservation() -> None:
    from app.services.messaging import consumer_concurrency as cc
    from app.services.resource_governor import policy
    from app.services.resource_governor.models import ParseTier
    from tests.unit.services.messaging.governor_test_helpers import make_test_governor

    governor = make_test_governor(logger_name="test.burst", env_parse=8)
    governor._heavy_ws = policy.HeavyWorkingSet(0.25, "measured")
    governor.memory_budget.limit = 4 * GIB
    host = SimpleNamespace(governor=governor, logger=None)
    held: list = []
    seen = {"peak": 0}

    async def one() -> None:
        item = await cc.reserve_record_memory(host, ParseTier.HEAVY, 10 * MIB, timeout=10, kind=ParseKind.DOCLING_DOCX)
        held.append(item)
        seen["peak"] = max(seen["peak"], len(held))
        await asyncio.sleep(0.2)
        held.remove(item)
        cc.release_admission(item)

    await asyncio.gather(*(one() for _ in range(10)))
    assert seen["peak"] == 3, "4 GiB holds three 1.25 GiB reservations, not eight permits"
    governor.close()


# -- T84: Excel admitted alone ------------------------------------------------------------------------------


def test_excel_on_one_lane_runs_serially_behind_a_layout_job(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, clock: FakeClock
) -> None:
    budget = make_budget(1, clock=clock)
    pool = make_pool(budget, hub=hub, clock=clock, threads=3)
    release = threading.Event()
    order: list[str] = []

    def layout() -> str:
        order.append("layout start")
        release.wait(30)
        order.append("layout end")
        return "layout"

    def excel() -> str:
        order.append(f"excel start (workers alive: {len(hub.alive_workers())})")
        return "excel"

    first = submit(pool, layout, kind=ParseKind.PDF_LAYOUT, size=MIB)
    assert wait_for(lambda: order == ["layout start"])
    second = submit(pool, excel, kind=ParseKind.EXCEL_LOAD, covered=True, size=20 * MIB)
    time.sleep(0.3)
    assert not second.future.done(), "the load waits for the lane"
    release.set()
    assert first.future.result(timeout=10) == "layout"
    # The layout worker is idle and resident; the load needs the only token, so idle workers are asked to leave.
    clock.advance(6.0)
    for _ in range(100):
        if second.future.done():
            break
        pool.reap_once()
        time.sleep(0.05)
    assert second.future.result(timeout=10) == "excel"
    assert order[-1] == "excel start (workers alive: 1)", order
    assert order.index("layout end") < len(order) - 1


def test_waiting_excel_loads_do_not_stall_the_event_loop(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub, threads=6)
    release = threading.Event()
    blocker = submit(pool, hold, release, kind=ParseKind.EXCEL_LOAD, covered=True, size=MIB)
    assert wait_for(lambda: pool.stats()["busy"] == 1)

    async def scenario() -> float:
        lag = 0.0
        loops = 0

        async def beat() -> None:
            nonlocal lag, loops
            while True:
                t = time.monotonic()
                await asyncio.sleep(0.01)
                lag = max(lag, time.monotonic() - t - 0.01)
                loops += 1

        heartbeat = asyncio.create_task(beat())

        def load() -> object:
            job = _Job(quick, (), "xlsx", 20 * MIB, kind=ParseKind.EXCEL_LOAD, covered=True)
            pool.enqueue(job)
            return job.future.result(timeout=30)

        waiting = [asyncio.create_task(asyncio.to_thread(load)) for _ in range(5)]
        await asyncio.sleep(0.6)
        assert not any(w.done() for w in waiting), "they are waiting"
        release.set()
        await asyncio.wait_for(asyncio.gather(*waiting), 30)
        heartbeat.cancel()
        assert loops > 20
        return lag

    lag = asyncio.run(scenario())
    assert lag <= 0.1, f"the loop lagged {lag * 1000:.0f} ms while loads waited"
    blocker.future.result(timeout=10)


def test_loop_heartbeat_during_excel_wait(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    test_waiting_excel_loads_do_not_stall_the_event_loop(make_budget, make_pool, hub)


def test_waiting_excel_loads_do_not_exhaust_the_default_executor(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    """Waiters are bounded by heavy permits, which are below the executor's size: other to_thread work still runs."""
    from tests.unit.services.messaging.governor_test_helpers import make_test_governor

    governor = make_test_governor(logger_name="test.executor", env_parse=4)
    permits = governor.ceilings.heavy
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub, threads=permits + 2)
    release = threading.Event()
    blocker = submit(pool, hold, release, kind=ParseKind.EXCEL_LOAD, covered=True, size=MIB)
    assert wait_for(lambda: pool.stats()["busy"] == 1)

    async def scenario() -> float:
        def load() -> object:
            job = _Job(quick, (), "xlsx", MIB, kind=ParseKind.EXCEL_LOAD, covered=True)
            pool.enqueue(job)
            return job.future.result(timeout=30)

        waiting = [asyncio.create_task(asyncio.to_thread(load)) for _ in range(permits)]
        await asyncio.sleep(0.3)
        started = time.monotonic()
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "other work"), 5) == "other work"
        took = time.monotonic() - started
        release.set()
        await asyncio.wait_for(asyncio.gather(*waiting), 30)
        return took

    took = asyncio.run(scenario())
    default_size = min(32, (os.cpu_count() or 1) + 4)
    assert permits < default_size, "waiters (one per heavy permit) fit in the loop's default executor"
    assert took < 1.0
    blocker.future.result(timeout=10)
    governor.close()


def test_excel_wait_is_cancellable_and_bounded_by_the_record_deadline(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub, threads=3)
    release = threading.Event()
    blocker = submit(pool, hold, release, kind=ParseKind.PDF_LAYOUT, size=MIB)
    assert wait_for(lambda: pool.stats()["busy"] == 1)
    # Cancellable: the record's own cancellation reaches the job waiting for a lane.
    waiting = submit(pool, quick, kind=ParseKind.EXCEL_LOAD, covered=True, size=MIB, timeout=240)
    time.sleep(0.2)
    waiting.cancelled.set()
    with pytest.raises(ParseAbandonedError):
        waiting.future.result(timeout=10)
    # Bounded: the wait ends at LANE_WAIT_SECONDS with the retryable error, whatever the load's own timeout.
    monkeypatch.setenv("LANE_WAIT_SECONDS", "0.4")
    late = submit(pool, quick, kind=ParseKind.EXCEL_LOAD, covered=True, size=MIB, timeout=240)
    started = time.monotonic()
    with pytest.raises(ParseWorkerError) as raised:
        late.future.result(timeout=10)
    assert raised.value.cause == "unavailable"
    assert time.monotonic() - started < 5
    release.set()
    blocker.future.result(timeout=10)


def test_the_loads_own_timeout_starts_when_it_is_sent_to_its_worker(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub, threads=3)
    release = threading.Event()
    blocker = submit(pool, hold, release, kind=ParseKind.EXCEL_LOAD, covered=True, size=MIB)
    assert wait_for(lambda: pool.stats()["busy"] == 1)
    load = submit(pool, quick, kind=ParseKind.EXCEL_LOAD, covered=True, size=MIB, timeout=0.5)
    time.sleep(1.0)  # waits longer than its own timeout for the lane
    release.set()
    blocker.future.result(timeout=10)
    assert load.future.result(timeout=10) == 1, "the wait is not charged to the run"


def test_cancelled_excel_wait_frees_thread_and_permit(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub
) -> None:
    budget = make_budget(1)
    pool = make_pool(budget, hub=hub, threads=3)
    release = threading.Event()
    blocker = submit(pool, hold, release, kind=ParseKind.PDF_LAYOUT, size=MIB)
    assert wait_for(lambda: pool.stats()["busy"] == 1)
    errors: list[BaseException] = []
    ended = threading.Event()

    def load() -> None:
        job = _Job(quick, (), "xlsx", MIB, kind=ParseKind.EXCEL_LOAD, covered=True)
        pool.enqueue(job)
        threading.Timer(0.3, job.cancelled.set).start()
        try:
            job.future.result(timeout=30)
        except BaseException as exc:
            errors.append(exc)
        finally:
            ended.set()

    thread = threading.Thread(target=load)
    thread.start()
    assert ended.wait(10), "the thread was freed by the cancellation"
    thread.join(5)
    assert isinstance(errors[0], ParseAbandonedError)
    assert budget.held_count()[0] == 1, "only the blocker still holds a lane"
    release.set()
    blocker.future.result(timeout=10)
    assert wait_for(lambda: budget.held_count()[0] == 0)
    assert pool.stats()["pending"] == 0


def test_the_wait_polls_off_the_event_loop_thread() -> None:
    """Static: admission polls with a thread's own wait, never an await."""
    source = Path(parse_pool_lanes.__file__).read_text()
    tree = ast.parse(source)
    pool_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LanePool")
    for node in pool_class.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("admit", "grow", "_lease"):
            assert not any(isinstance(n, (ast.Await, ast.AsyncFor)) for n in ast.walk(node)), node.name


def test_parse_pool_helpers_still_exist() -> None:
    assert callable(parse_pool.run_blocking) and callable(parse_pool.submit)


# -- the charge guard, end to end through the reaper --------------------------------------------------------


def test_observed_rss_above_charge_raises_the_scale(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(4)
    pool = make_pool(budget, hub=hub)
    release = threading.Event()
    job = submit(pool, hold, release, kind=ParseKind.CODE, size=5 * MIB)
    assert wait_for(lambda: pool.stats()["busy"] == 1)
    (entry,) = entries(pool)
    pid = entry.worker.process.pid
    charge = entry.charged_bytes
    assert SCALES.scale(ParseKind.CODE) == 1.0
    monkeypatch.setattr(parse_pool_lanes, "group_rss_bytes", lambda pgids: {pid: int(charge * 1.1)})
    pool.reap_once()
    assert SCALES.scale(ParseKind.CODE) == 1.0, "within the 20 % tolerance"
    monkeypatch.setattr(parse_pool_lanes, "group_rss_bytes", lambda pgids: {pid: int(charge * 1.6)})
    pool.reap_once()
    assert SCALES.scale(ParseKind.CODE) == pytest.approx(1.6)
    assert budget.stats()["charge_scales"]["code"]["scale"] == pytest.approx(1.6)
    assert budget.charge_for(ParseKind.CODE, 5 * MIB)[0] > charge, "the next admission is charged the raised figure"
    release.set()
    job.future.result(timeout=10)


def test_the_guard_looks_at_nothing_when_no_worker_is_charged(
    make_budget: Callable[..., LaneBudget], make_pool: Callable[..., LanePool], hub: StubHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(2)
    pool = make_pool(budget, hub=hub)
    calls: list[object] = []
    monkeypatch.setattr(parse_pool_lanes, "group_rss_bytes", lambda pgids: calls.append(pgids) or {})
    pool.reap_once()
    assert calls == []
