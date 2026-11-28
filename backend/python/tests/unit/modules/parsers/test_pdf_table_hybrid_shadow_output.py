"""The shadow run emits only telemetry: what a parse returns is byte-identical with it on and off, whatever fails."""
from __future__ import annotations

import json
import logging
import os
import pickle
from pathlib import Path
from typing import Never

import httpx
import pytest

from app.modules.parsers import parse_pool
from app.modules.parsers.pdf import opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_hybrid_flow as flow
from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    PDFPlumberOpenCVProcessor,
)
from app.modules.parsers.pdf.table_hybrid_client import TableStructureClient
from tests.support.table_shadow import parse_logs

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "table_hybrid"
PDF = (FIXTURES / "tables.pdf").read_bytes()
BOXES = json.loads((FIXTURES / "tables.boxes.json").read_text())
W, H = 612.0, 792.0


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIPESHUB_TABLE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "shadow")
    monkeypatch.delenv("PDF_TABLE_HYBRID_SHADOW", raising=False)
    counter = {"page": 0}

    def fake_boxes(page, pdf_path=None, raster_cache=None) -> dict | None:
        boxes = BOXES.get(str(page.page_number), [])
        return {"table": [((b[0] / W, b[1] / H, b[2] / W, b[3] / H), 0.9) for b in boxes], "picture": []} if boxes else None

    monkeypatch.setattr(ola, "_detect_model_boxes", fake_boxes)
    counter["page"] += 1


def parse(spool_dir=None):  # noqa: ANN201
    return pickle.dumps(parse_pdf_layout(PDF, spool_dir), protocol=4)


def test_shadow_with_the_flag_returns_the_same_bytes_as_without(tmp_path, monkeypatch) -> None:
    baseline = parse()
    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "true")
    spool = str(tmp_path / "jobs")
    os.mkdir(spool)
    with_run = parse(spool)
    assert with_run == baseline
    recs = [json.loads(line) for line in open(os.path.join(spool, flow.INDEX_FILE))]
    assert len(recs) == 8 and all(r["reason"] == "none" for r in recs)  # the run did collect: all eight fixture boxes
    assert all("evidence_shape" in r and "box_class" in r and "legacy_shape" in r for r in recs)


def test_collection_failures_leave_the_bytes_unchanged(tmp_path, monkeypatch) -> None:
    baseline = parse()
    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "true")

    def broken(pdf_path) -> object:
        def render(index) -> Never:
            raise RuntimeError("pdfium failed")

        return render

    monkeypatch.setattr(flow, "_default_renderer", broken)
    spool = str(tmp_path / "jobs")
    os.mkdir(spool)
    assert parse(spool) == baseline
    assert {json.loads(line)["reason"] for line in open(os.path.join(spool, flow.INDEX_FILE))} == {"collect_error"}
    assert parse(str(tmp_path / "missing" / "dir")) == baseline  # an unwritable spool


def test_without_the_flag_no_collector_is_made_even_with_a_spool_directory(tmp_path) -> None:
    spool = str(tmp_path / "jobs")
    os.mkdir(spool)
    parse(spool)
    assert os.listdir(spool) == []


async def test_parse_document_returns_the_same_pages_service_up_down_and_slow(tmp_path, monkeypatch, caplog) -> None:
    """Through the processor: the detached run starts after the parse and cannot change what it returned."""
    caplog.set_level(logging.INFO, logger="app.modules.parsers.pdf.table_hybrid_flow")

    async def inline_submit(fn, *args, **kw) -> object:
        return fn(*args)

    monkeypatch.setattr(parse_pool, "submit", inline_submit)
    proc = PDFPlumberOpenCVProcessor(logging.getLogger("t"), None)  # type: ignore[arg-type]
    base = pickle.dumps(await proc.parse_document("a.pdf", PDF, table_model=True, record_id="r0"), protocol=4)

    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "true")
    import asyncio

    def down(request) -> Never:
        raise httpx.ConnectError("refused", request=request)

    async def slow(request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return httpx.Response(500, json={})

    for handler in (down, slow, lambda r: httpx.Response(429, json={})):
        coord = flow.ShadowCoordinator(TableStructureClient("http://svc", httpx.MockTransport(handler)))
        flow.set_coordinator(coord)
        try:
            out = pickle.dumps(await proc.parse_document("a.pdf", PDF, table_model=True, record_id="r1"), protocol=4)
            assert out == base
            await asyncio.gather(*list(coord._tasks))
        finally:
            flow.set_coordinator(None)
    _, docs = parse_logs(caplog)
    assert len(docs) == 3 and all(d["record_id"] == "r1" and d["tables_seen"] == 8 for d in docs)
    assert all(d["request_id"] is None for d in docs)
    # per document: the layout parse wall and the number of pages with table boxes (the denominator for a cost per page)
    assert all(d["table_pages"] == 5 and d["layout_parse_wall_s"] is not None and d["layout_parse_wall_s"] > 0 for d in docs)  # no request context: unknown is null, not a dash


async def test_callers_that_do_not_ask_for_the_table_model_start_no_run(monkeypatch) -> None:
    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "true")

    async def inline_submit(fn, *args, **kw) -> object:
        assert args == (PDF,)  # no spool directory passed
        return fn(*args)

    monkeypatch.setattr(parse_pool, "submit", inline_submit)
    proc = PDFPlumberOpenCVProcessor(logging.getLogger("t"), None)  # type: ignore[arg-type]
    await proc.parse_document("a.pdf", PDF)


async def test_a_failed_parse_removes_the_spool_and_frees_the_slot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "true")

    async def failing_submit(fn, *args, **kw) -> Never:
        assert os.path.isdir(args[1])
        raise RuntimeError("worker died")

    monkeypatch.setattr(parse_pool, "submit", failing_submit)
    coord = flow.ShadowCoordinator(TableStructureClient("http://svc", httpx.MockTransport(lambda r: httpx.Response(500))))
    flow.set_coordinator(coord)
    try:
        proc = PDFPlumberOpenCVProcessor(logging.getLogger("t"), None)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError, match="worker died"):
            await proc.parse_document("a.pdf", PDF, table_model=True, record_id="r")
        root = flow.spool_root()
        assert not os.path.exists(root) or os.listdir(root) == []
        assert coord.admit("r2", "q") is not None
    finally:
        flow.set_coordinator(None)


def test_a_parse_that_raises_after_a_table_was_collected_closes_the_collector(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "true")
    spool = str(tmp_path / "jobs")
    os.mkdir(spool)
    closed: list[bool] = []
    real_close = flow.TableJobCollector.close
    monkeypatch.setattr(flow.TableJobCollector, "close", lambda self: (closed.append(True), real_close(self))[1])
    real_extract = ola.extract_layout_regions
    seen = {"n": 0}

    def fail_on_the_second_page(page, **kwargs) -> list:
        seen["n"] += 1
        if seen["n"] == 2:
            raise RuntimeError("layout failed")
        return real_extract(page, **kwargs)

    from app.modules.parsers.pdf import layout_pages

    monkeypatch.setattr(layout_pages, "extract_layout_regions", fail_on_the_second_page)
    with pytest.raises(RuntimeError):
        parse_pdf_layout(PDF, spool)
    assert closed == [True]
