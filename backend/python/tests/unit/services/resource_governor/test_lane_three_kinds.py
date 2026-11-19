"""Parse jobs, service-process renders and Docling conversions share one budget (T74).

The gauge is independent of the lease table: every job body records its own start and end with
its weight in a file, and the test sweeps the intervals afterwards. Pools are wider than the
limit (eight lane threads under a limit of four), so only the lease holds the load to it.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.pdf import docling_processor as dp
from app.modules.parsers.pdf import pdf_rasterizer as raster
from app.services.resource_governor.lanes import LaneBudget, plan_lanes
from tests.unit.lane_gauge import Mem
from tests.unit.modules.parsers.lane_pool_support import peak_weight, weighted_job

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

GIB = 2**30
LIMIT = 4
pytestmark = pytest.mark.timeout(240)
PDF = b"%PDF-1.7 x"
DOCX = b"PK\x03\x04 x"


@pytest.fixture
def gauge(tmp_path: Path) -> str:
    return str(tmp_path / "gauge")


class GaugedConverter:
    def __init__(self, path: str) -> None:
        self.path = path

    def convert(self, source: object, **kwargs: object) -> MagicMock:
        weighted_job(self.path, 3 if source.name.endswith(".pdf") else 1, 0.15)  # type: ignore[attr-defined]
        result = MagicMock()
        result.status.value = "success"
        result.document = "DOC"
        return result


def run_load(gauge: str, processor: dp.DoclingProcessor) -> None:
    errors: list[BaseException] = []

    def guard(call: object) -> object:
        def run() -> None:
            try:
                call()  # type: ignore[operator]
            except BaseException as exc:
                errors.append(exc)

        return threading.Thread(target=run)

    threads = [guard(lambda: parse_pool.run_blocking(weighted_job, gauge, 1, 0.15, label="layout")) for _ in range(16)]
    threads += [guard(lambda: raster._run_in_pool(weighted_job, gauge, 1, 0.15)) for _ in range(10)]

    async def docling() -> None:
        await asyncio.gather(
            *[processor.parse_document(f"{i}.pdf" if i % 2 else f"{i}.docx", PDF if i % 2 else DOCX) for i in range(10)]
        )

    threads.append(guard(lambda: asyncio.run(docling())))
    for t in threads:
        t.start()  # type: ignore[attr-defined]
    for t in threads:
        t.join(timeout=200)  # type: ignore[attr-defined]
    assert not errors, errors


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gauge: str) -> Iterator[SimpleNamespace]:
    monkeypatch.delenv("PARSE_POOL_WORKERS", raising=False)
    monkeypatch.delenv("PDF_RASTER_WORKERS", raising=False)
    monkeypatch.delenv("LOCAL_DOCLING_PARSE_WORKERS", raising=False)
    budget = LaneBudget.open(
        plan_lanes(cpus=16, heavy=16), tmp_path / "lanes", memory_reader=Mem(1000 * GIB, 0), warmup_seconds=0.0,
        view_ttl=0.0, alive=lambda pid: True,
    )
    assert budget.plan.lanes == 8
    budget.report_incident()  # 8 -> 4: the pools are twice as wide as the limit
    assert budget.limit() == LIMIT
    gov = SimpleNamespace(
        lanes=budget, ceilings=SimpleNamespace(heavy=8), fleet_ceilings=SimpleNamespace(heavy=16),
        report_memory_incident=MagicMock(),
    )
    processor = dp.DoclingProcessor.__new__(dp.DoclingProcessor)
    processor.logger = MagicMock()
    processor.config = None
    processor.converter = GaugedConverter(gauge)  # type: ignore[assignment]
    saved = (parse_pool._State.governor, dp._resource_governor, raster._resource_governor)
    parse_pool.set_resource_governor(gov)  # type: ignore[arg-type]
    dp.set_resource_governor(gov)  # type: ignore[arg-type]
    raster.set_resource_governor(gov)  # type: ignore[arg-type]
    yield SimpleNamespace(budget=budget, processor=processor)
    parse_pool.shutdown_parse_pool()
    dp.shutdown_docling_pool()
    raster.shutdown_pdf_raster_pool()
    parse_pool._State.governor = saved[0]
    dp.set_resource_governor(saved[1])  # type: ignore[arg-type]
    raster.set_resource_governor(saved[2])
    budget.close()


@pytest.mark.parametrize("path", ["thread", "executor"])
def test_parse_raster_and_docling_share_one_budget_by_gauge(
    rig: SimpleNamespace, gauge: str, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARSE_LANES", "on")
    if path == "executor":
        from concurrent.futures import ThreadPoolExecutor

        monkeypatch.setenv("LOCAL_DOCLING_PARSE_WORKERS", "auto")
        monkeypatch.setattr(dp._DoclingExecutor, "_default_factory", staticmethod(lambda w: ThreadPoolExecutor(w)))

        def child(name: str, content: bytes, page_range: object = None) -> str:
            weighted_job(gauge, 3 if content.startswith(b"%PDF-") else 1, 0.15)
            return dp.DoclingDocument(name="x").model_dump_json()

        monkeypatch.setattr(dp, "_parse_document_in_worker", child)
        assert dp._lane_mode() == "executor"
    run_load(gauge, rig.processor)
    peak = peak_weight(gauge)
    assert peak <= LIMIT, f"the weighted overlap reached {peak} against a limit of {LIMIT}"
    assert peak == LIMIT, f"the load never filled the limit (peak {peak})"
    assert rig.budget.held_count()[0] == 0


def test_negative_control_off_mode_oversubscribes_all_three(
    rig: SimpleNamespace, gauge: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same load with the budget off runs past the limit, so the gauge can see an over-admit."""
    monkeypatch.setenv("PARSE_LANES", "off")
    run_load(gauge, rig.processor)
    assert peak_weight(gauge) > LIMIT
