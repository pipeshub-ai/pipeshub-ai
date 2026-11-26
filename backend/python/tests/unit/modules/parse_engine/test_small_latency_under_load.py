"""A small file is never queued behind a large one: the end-to-end property, through the real admission code.

A mixed load runs through ``acquire_index_slot`` and ``acquire_parsing_slot`` of the indexing consumers (the
functions a record passes), against a real ``ResourceGovernor`` and each member budget: none, the in-memory
fake, and the real file ledger. Ten large files are in flight while small ones (a Docling-routed PDF and a CPU
workbook) arrive at a steady rate; the small files' wait from arrival to their parse permit is measured with
and without the large ones. Durations are scaled down (a "large" parse holds its permits for a second).

``PARSE_ADMISSION=legacy`` over the same load is the negative control: the small files queue behind the large
ones, which is what the fix removes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.capacity.member_budget import MemberBudget as RealLedger
from app.modules.parse_engine import wiring
from app.services.messaging import consumer_concurrency as cc
from app.services.resource_governor.gate import StartRateLimiter
from app.services.resource_governor.models import ParseTier, Pool
from tests.support.fake_member_budget import FakeMemberBudget
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from pathlib import Path

LARGE_HOLD_S = 1.0
SMALL_HOLD_S = 0.02
SMALL_EVERY_S = 0.025
SMALLS = 160
LARGE_FILES = 10
MIB = 1 << 20
REPORT = os.environ.get("PARSE_SIM_REPORT")  # a path: the numbers are appended there as JSON lines


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


async def _record(host: object, payload: dict[str, object], hold: float) -> float:
    """One record through the admission points, as the consumer does it; returns the wait for its parse permit."""
    level = cc.dispatch_tier(host, SimpleNamespace(payload=payload))  # type: ignore[arg-type]
    ctx = cc.class_context(host, payload)  # type: ignore[arg-type]
    t0 = time.monotonic()
    index = await cc.acquire_index_slot(host, level, **cc.class_kwargs(ctx))  # type: ignore[arg-type]
    # The event may carry no size: it is known after the download, as are the inspected pages (START_PARSING).
    size = int(payload.get("sizeInBytes") or payload["actualSize"])  # type: ignore[call-overload]
    if ctx is not None and "pages" in payload:
        ctx = ctx.inspected(int(payload["pages"]), scanned=False)  # type: ignore[call-overload]
    cc.reclass_index_slot(index, ctx, size)
    try:
        parse = await cc.acquire_parsing_slot(
            host, ParseTier.HEAVY, size, timeout=60.0, **cc.class_kwargs(ctx)  # type: ignore[arg-type]
        )
        waited = time.monotonic() - t0
        await asyncio.sleep(hold)
        cc.release_admission(parse)
    finally:
        cc.release_admission(index)
    return waited


def _small(i: int) -> dict[str, object]:
    # Alternate a Docling-routed PDF and a CPU-parsed workbook: both small, one with no CPU slot.
    ext = "pdf" if i % 2 == 0 else "xlsx"
    return {"extension": ext, "mimeType": "", "orgId": f"org{i % 3}", "recordId": f"s{i}", "sizeInBytes": 1 * MIB}


def _large(i: int) -> dict[str, object]:
    return {"extension": "pdf", "mimeType": "application/pdf", "orgId": "flood", "recordId": f"l{i}", "sizeInBytes": 150 * MIB}


def _large_unsized(i: int) -> dict[str, object]:
    """A 1000-page PDF of 80 MiB whose event carries no size: its prior is small (under 100 MiB), inspection says huge."""
    return {
        "extension": "pdf", "mimeType": "application/pdf", "orgId": "flood", "recordId": f"u{i}",
        "actualSize": 80 * MIB, "pages": 1000,
    }


async def _run(host: object, *, large: int, make_large: object = _large) -> list[float]:
    big = [asyncio.create_task(_record(host, make_large(i), LARGE_HOLD_S)) for i in range(large)]  # type: ignore[operator]
    await asyncio.sleep(0.05)  # the large files are in flight before the first small one arrives
    smalls: list[asyncio.Task[float]] = []
    for i in range(SMALLS):
        smalls.append(asyncio.create_task(_record(host, _small(i), SMALL_HOLD_S)))
        await asyncio.sleep(SMALL_EVERY_S)
    waits = list(await asyncio.gather(*smalls))
    await asyncio.gather(*big)
    return waits


def _budget(kind: str, tmp_path: Path) -> object | None:
    if kind == "none":
        return None
    if kind == "fake":
        return FakeMemberBudget(8)
    d = tmp_path / "ledger"
    d.mkdir(mode=0o700)
    return RealLedger(d, cpu_tokens=8, mem_bytes=64 << 30, service="sim")


def _measure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, mode: str, budget_kind: str, large: int, make_large: object = _large
) -> list[float]:
    monkeypatch.setenv("PARSE_ADMISSION", mode)
    monkeypatch.delenv("PARSE_LANES", raising=False)
    wiring.reset_for_tests()
    budget = _budget(budget_kind, tmp_path)
    monkeypatch.setattr("app.capacity.runtime.member_budget", lambda *_a, **_k: budget)
    governor = make_test_governor(logger_name="test.sim", cpu_quota=16.0)
    # A 16-CPU container's steady-state limits (the governor ramps to them; no sampler runs here).
    governor._registry.set(Pool.HEAVY_PARSE, 8)
    governor._registry.set(Pool.INDEX_HEAVY, 16)
    if mode != "classes":
        # The legacy control spaces every parse, small ones included, at about one per two seconds in absolute time;
        # this simulation runs durations scaled down ~100x, so the control opens it (what it shows is the queueing).
        governor.gate(Pool.HEAVY_PARSE)._rate_limiter = StartRateLimiter(0.0005, 100_000)
    # With classes on the governor's own start-rate limiter stays as shipped: large parses wait for start tokens at
    # the heavy gate and the small ones must never queue behind them.
    host = SimpleNamespace(governor=governor, logger=logging.getLogger("test.sim"), priority_lane=None)
    try:
        return asyncio.run(_run(host, large=large, make_large=make_large))
    finally:
        if hasattr(budget, "close"):
            budget.close()


def _report(label: str, quiet: float, loaded: float) -> None:
    line = {"case": label, "small_wait_p95_s": {"no_large": round(quiet, 4), f"{LARGE_FILES}_large": round(loaded, 4)}}
    logging.getLogger(__name__).info("%s", json.dumps(line))
    print(json.dumps(line))
    if REPORT:
        with open(REPORT, "a") as f:
            f.write(json.dumps(line) + "\n")


@pytest.mark.parametrize("budget_kind", ["none", "fake", "real"])
def test_small_wait_is_not_affected_by_large_files_in_flight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, budget_kind: str
) -> None:
    monkeypatch.setenv("PARSE_DOCLING_WIDTH", "4")  # a narrow Docling service: a prior-small big file would fill it
    (tmp_path / "q").mkdir()
    (tmp_path / "l").mkdir()
    quiet = _p95(_measure(monkeypatch, tmp_path / "q", mode="classes", budget_kind=budget_kind, large=0))
    loaded = _p95(_measure(monkeypatch, tmp_path / "l", mode="classes", budget_kind=budget_kind, large=LARGE_FILES))
    _report(f"classes/{budget_kind}", quiet, loaded)
    # The gate: ten large files in flight add no more than a few scheduling quanta to a small file's wait.
    assert loaded <= quiet + 0.15, (quiet, loaded)
    # wall-clock-ok: a quarter of a large file's hold; the regression it guards waits the whole hold
    assert loaded < LARGE_HOLD_S / 4


def test_without_classes_small_files_queue_behind_the_large_ones(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The negative control: the same load under ``PARSE_ADMISSION=legacy`` makes the small files wait for the large ones."""
    monkeypatch.setenv("PARSE_DOCLING_WIDTH", "4")  # a narrow Docling service: a prior-small big file would fill it
    (tmp_path / "q").mkdir()
    (tmp_path / "l").mkdir()
    quiet = _p95(_measure(monkeypatch, tmp_path / "q", mode="legacy", budget_kind="none", large=0))
    loaded = _p95(_measure(monkeypatch, tmp_path / "l", mode="legacy", budget_kind="none", large=LARGE_FILES))
    _report("legacy/none", quiet, loaded)
    assert loaded > LARGE_HOLD_S / 2, (quiet, loaded)  # a small file waits a large file's hold or more


@pytest.mark.parametrize("budget_kind", ["none", "fake", "real"])
def test_small_wait_holds_when_the_large_files_arrive_without_a_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, budget_kind: str
) -> None:
    """The large files' events carry no size, so each is admitted as small on its prior; inspection (1000 pages)
    moves it to its class in place and the small files still never queue behind it."""
    monkeypatch.setenv("PARSE_DOCLING_WIDTH", "4")  # a narrow Docling service: a prior-small big file would fill it
    (tmp_path / "q").mkdir()
    (tmp_path / "l").mkdir()
    quiet = _p95(_measure(monkeypatch, tmp_path / "q", mode="classes", budget_kind=budget_kind, large=0))
    loaded = _p95(
        _measure(
            monkeypatch, tmp_path / "l", mode="classes", budget_kind=budget_kind, large=LARGE_FILES,
            make_large=_large_unsized,
        )
    )
    _report(f"classes/{budget_kind}/unsized-large", quiet, loaded)
    assert loaded <= quiet + 0.15, (quiet, loaded)
    # wall-clock-ok: a quarter of a large file's hold; the regression it guards waits the whole hold
    assert loaded < LARGE_HOLD_S / 4


def test_without_classes_unsized_large_files_also_block_the_small_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The negative control for the unsized case: classes off, the same arrivals."""
    monkeypatch.setenv("PARSE_DOCLING_WIDTH", "4")  # a narrow Docling service: a prior-small big file would fill it
    (tmp_path / "q").mkdir()
    (tmp_path / "l").mkdir()
    quiet = _p95(_measure(monkeypatch, tmp_path / "q", mode="legacy", budget_kind="none", large=0))
    loaded = _p95(
        _measure(
            monkeypatch, tmp_path / "l", mode="legacy", budget_kind="none", large=LARGE_FILES, make_large=_large_unsized
        )
    )
    _report("legacy/none/unsized-large", quiet, loaded)
    assert loaded > LARGE_HOLD_S / 2, (quiet, loaded)


def test_without_the_inspected_facts_the_unsized_large_files_do_block_the_small_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The prior alone (what the wiring did before inspection): classes on, but the pages are never read, so the
    unsized files stay small and the small ones queue behind them. This is what inspection removes."""

    def no_pages(i: int) -> dict[str, object]:
        p = _large_unsized(i)
        del p["pages"]
        return p

    monkeypatch.setenv("PARSE_DOCLING_WIDTH", "4")  # a narrow Docling service: a prior-small big file would fill it
    (tmp_path / "q").mkdir()
    (tmp_path / "l").mkdir()
    quiet = _p95(_measure(monkeypatch, tmp_path / "q", mode="classes", budget_kind="none", large=0))
    loaded = _p95(
        _measure(monkeypatch, tmp_path / "l", mode="classes", budget_kind="none", large=LARGE_FILES, make_large=no_pages)
    )
    _report("classes/none/unsized-large-uninspected", quiet, loaded)
    assert loaded > quiet + 0.15, (quiet, loaded)
