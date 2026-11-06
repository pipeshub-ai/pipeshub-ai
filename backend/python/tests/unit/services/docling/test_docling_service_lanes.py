"""The Docling service on the lane budget: permit, lease and the 30 s answer (T68, T69, T70)."""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

import app.services.docling.docling_service as mod
from app.modules.parsers.pdf import docling_processor as dp
from app.services.resource_governor.lanes import LaneBudget, plan_lanes
from tests.unit.lane_gauge import Mem

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

GIB = 2**30
PDF = b"%PDF-1.7 data"
pytestmark = pytest.mark.timeout(60)


class Permits:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.budget: LaneBudget | None = None

    async def acquire(self, *_a: object) -> tuple[bool, int]:
        self.events.append("permit")
        return True, 1

    def release(self, cost: int) -> None:
        if cost:
            held = self.budget.held_count()[0] if self.budget else -1
            self.events.append(f"release(lease_held={held})")


class Converter:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.go = threading.Event()
        self.go.set()
        self.calls = 0

    def convert(self, source: object, **kwargs: object) -> MagicMock:
        self.calls += 1
        self.started.set()
        self.go.wait(20)
        result = MagicMock()
        result.status.value = "success"
        result.document = MagicMock(model_dump_json=lambda: "{}")
        return result


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("PARSE_LANES", "on")
    monkeypatch.delenv("LOCAL_DOCLING_PARSE_WORKERS", raising=False)
    budget = LaneBudget.open(
        plan_lanes(cpus=2, heavy=2), tmp_path / "lanes", memory_reader=Mem(1000 * GIB, 0), view_ttl=0.0,
        alive=lambda pid: True,
    )
    permits = Permits()
    permits.budget = budget
    converter = Converter()
    processor = dp.DoclingProcessor.__new__(dp.DoclingProcessor)
    processor.logger = MagicMock()
    processor.config = None
    processor.converter = converter  # type: ignore[assignment]
    service = mod.DoclingService()
    service.processor = processor  # type: ignore[assignment]
    saved = (mod.docling_service, dp._resource_governor)
    mod.docling_service = service
    dp.set_resource_governor(SimpleNamespace(lanes=budget, report_memory_incident=MagicMock()))  # type: ignore[arg-type]
    mod._parse_tasks.clear()
    mod._abandoned.clear()
    with (
        patch.object(mod, "_acquire_docling_gate", permits.acquire),
        patch.object(mod, "_release_docling_gate", permits.release),
    ):
        yield SimpleNamespace(budget=budget, permits=permits, converter=converter)
    mod.docling_service, governor = saved
    dp.set_resource_governor(governor)  # type: ignore[arg-type]
    budget.close()


async def call() -> object:
    upload = MagicMock(read=AsyncMock(return_value=PDF))
    return await mod.parse_pdf_endpoint(
        background_tasks=BackgroundTasks(), file=upload, record_name="a.pdf", start_page=None, end_page=None
    )


async def until(predicate: Callable[[], bool], seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


@pytest.mark.asyncio
async def test_timed_out_conversion_keeps_permit_and_lease_until_it_ends(rig: SimpleNamespace) -> None:
    rig.converter.go.clear()
    with patch.object(mod, "PDF_PARSING_TIMEOUT_SECONDS", 0.3):
        response = await call()
    assert response.status_code == 422 and b"PARSE_TIMEOUT" in response.body  # type: ignore[attr-defined]
    # The caller is gone; the conversion still runs, so both are still held.
    assert rig.budget.held_count()[0] == 1  # a PDF on this 1-lane plan holds the lane
    assert rig.permits.events == ["permit"]
    rig.converter.go.set()
    assert await until(lambda: len(rig.permits.events) == 2)
    assert rig.budget.held_count()[0] == 0
    # The lease went back with the conversion, no later than the permit that follows serialization.
    assert rig.permits.events[1] == "release(lease_held=0)"


@pytest.mark.asyncio
async def test_abandoned_task_waiting_for_a_lease_never_starts(rig: SimpleNamespace) -> None:
    blocker = rig.budget.acquire(timeout=2)
    with patch.object(mod, "PDF_PARSING_TIMEOUT_SECONDS", 0.3):
        response = await call()
    assert response.status_code == 422  # type: ignore[attr-defined]
    assert await until(lambda: len(rig.permits.events) == 2)
    assert rig.permits.events[1].startswith("release")
    assert rig.converter.calls == 0
    assert rig.budget.held_count()[0] == 1  # only the blocker
    blocker.release()
    await asyncio.sleep(0.3)
    assert rig.converter.calls == 0  # it does not start once the lane frees up
    assert not mod._parse_tasks


@pytest.mark.asyncio
async def test_abandon_after_the_lease_is_held_is_ignored(rig: SimpleNamespace) -> None:
    rig.converter.go.clear()
    with patch.object(mod, "PDF_PARSING_TIMEOUT_SECONDS", 0.3):
        await call()
    assert rig.converter.started.is_set()
    rig.converter.go.set()
    assert await until(lambda: len(rig.permits.events) == 2)
    assert rig.converter.calls == 1
    assert rig.permits.events == ["permit", "release(lease_held=0)"]


@pytest.mark.asyncio
async def test_service_lease_wait_beyond_30s_is_backpressure(rig: SimpleNamespace) -> None:
    assert mod.DOCLING_GATE_TIMEOUT_SECONDS == 30.0
    blocker = rig.budget.acquire(timeout=2)
    with patch.object(mod, "DOCLING_GATE_TIMEOUT_SECONDS", 0.3):
        started = time.monotonic()
        response = await call()
    assert response.status_code == 429  # type: ignore[attr-defined]
    assert response.headers["retry-after"] == "5"  # type: ignore[attr-defined]
    assert 0.25 <= time.monotonic() - started < 3
    assert rig.converter.calls == 0
    blocker.release()


@pytest.mark.asyncio
async def test_lane_wait_timeout_releases_permit_once_and_answers_429(rig: SimpleNamespace) -> None:
    blocker = rig.budget.acquire(timeout=2)
    with patch.object(mod, "DOCLING_GATE_TIMEOUT_SECONDS", 0.2):
        response = await call()
    await asyncio.sleep(0.3)  # a second release would land here
    assert response.status_code == 429  # type: ignore[attr-defined]
    assert [e for e in rig.permits.events if e.startswith("release")] == ["release(lease_held=1)"]
    assert not mod._parse_tasks
    blocker.release()


@pytest.mark.asyncio
async def test_off_mode_passes_no_lease_arguments(rig: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_LANES", "off")
    seen: list[tuple[tuple, dict]] = []

    async def parse(*a: object, **k: object) -> MagicMock:
        seen.append((a, k))
        return MagicMock(model_dump_json=lambda: "{}")

    mod.docling_service.parse_pdf_only = parse  # type: ignore[method-assign,union-attr]
    assert (await call()).success  # type: ignore[attr-defined]
    assert seen == [(("a.pdf", PDF), {"page_range": None})]
