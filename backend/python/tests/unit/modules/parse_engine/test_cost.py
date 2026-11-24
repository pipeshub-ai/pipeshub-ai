"""Cost estimator: the class comes from the estimated parse time, never from bytes."""
from __future__ import annotations

import random

import pytest

from app.modules.parse_engine.cost import (
    CostClass,
    CostConfig,
    CostHint,
    CostInputs,
    Engine,
    FormatKind,
    cost_config_from_env,
    estimate,
)

MIB = 1024 * 1024
CFG = CostConfig()
S, L, H = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE


@pytest.mark.parametrize(
    ("kind", "size", "cls", "engine"),
    [
        (FormatKind.PDF, None, S, Engine.DOCLING),
        (FormatKind.PDF, 0, S, Engine.DOCLING),
        (FormatKind.PDF, 19 * MIB, S, Engine.DOCLING),  # biased small: size alone does not prove large
        (FormatKind.PDF, 150 * MIB, L, Engine.CPU),  # over the Docling size limit: size alone proves large
        (FormatKind.DOCX, None, S, Engine.CPU),
        (FormatKind.DOCX, 19 * MIB, S, Engine.CPU),
        (FormatKind.DOCX, 150 * MIB, L, Engine.CPU),
        (FormatKind.PPTX, 19 * MIB, S, Engine.CPU),
        (FormatKind.XLSX, None, S, Engine.CPU),
        (FormatKind.XLSX, 19 * MIB, S, Engine.CPU),
        (FormatKind.XLSX, 150 * MIB, L, Engine.CPU),
        (FormatKind.TEXT, 150 * MIB, S, Engine.INLINE),
        (FormatKind.IMAGE, 19 * MIB, S, Engine.INLINE),
        (FormatKind.OTHER, 19 * MIB, S, Engine.CPU),
    ],
)
def test_prior_table(kind: FormatKind, size: int | None, cls: CostClass, engine: Engine) -> None:
    e = estimate(CostInputs(kind, size), CFG)
    assert (e.cls, e.engine) == (cls, engine)
    assert e.confidence == "prior"
    assert e.est_seconds <= (CFG.small_max_s if cls is S else CFG.unit_budget_s)
    assert e.reason


@pytest.mark.parametrize(
    ("pages", "size", "cls", "engine", "docling_permit"),
    [
        (1, 100_000, S, Engine.DOCLING, True),
        (30, 5 * MIB, S, Engine.DOCLING, True),
        (31, 5 * MIB, S, Engine.CPU, False),  # over the Docling page limit: PdfPlumber at 1.5 s/page = 46.5 s
        (40, 5 * MIB, S, Engine.CPU, False),  # 60 s exactly is small
        (41, 5 * MIB, L, Engine.CPU, False),  # 61.5 s
        (200, 5 * MIB, L, Engine.CPU, False),  # 300 s exactly fits one unit
        (201, 5 * MIB, H, Engine.CPU, False),
        (20, 30 * MIB, S, Engine.DOCLING, True),  # many bytes, few pages: still small
        (2000, 2 * MIB, H, Engine.CPU, False),  # few bytes, many pages: huge
    ],
)
def test_class_by_estimate(pages: int, size: int, cls: CostClass, engine: Engine, docling_permit: bool) -> None:
    e = estimate(CostInputs(FormatKind.PDF, size, pages=pages), CFG)
    assert (e.cls, e.engine, e.needs_docling_permit) == (cls, engine, docling_permit)
    assert e.confidence == "inspected"
    assert e.needs_cpu_slot is (not docling_permit)
    if cls is H:
        assert e.split and e.bounded
        assert e.unit_pages == 50  # 75 s per unit at 1.5 s/page
    else:
        assert not e.split


def test_bytes_set_memory_not_class() -> None:
    small_file = estimate(CostInputs(FormatKind.PDF, 100_000, pages=20), CFG)
    big_file = estimate(CostInputs(FormatKind.PDF, 60 * MIB, pages=20), CFG)
    assert small_file.cls is big_file.cls is S
    assert big_file.mem_bytes > small_file.mem_bytes + 50 * MIB


def test_class_independent_of_size_when_inspected() -> None:
    rng = random.Random(20260515)
    for _ in range(300):
        kind = rng.choice([FormatKind.PDF, FormatKind.DOCX, FormatKind.PPTX])
        pages = rng.randint(1, 400)
        ref = estimate(CostInputs(kind, 1 * MIB, pages=pages), CFG)
        # below the Docling size limit, which is the one place bytes pick an engine (and so a rate)
        for size in (None, 0, rng.randint(1, 100 * MIB)):
            got = estimate(CostInputs(kind, size, pages=pages), CFG)
            assert (got.cls, got.engine, got.est_seconds) == (ref.cls, ref.engine, ref.est_seconds)


def test_scanned() -> None:
    text = estimate(CostInputs(FormatKind.PDF, 5 * MIB, pages=10, scanned=False), CFG)
    scan = estimate(CostInputs(FormatKind.PDF, 5 * MIB, pages=10, scanned=True), CFG)
    assert text.engine is Engine.DOCLING and scan.engine is Engine.VLM
    assert scan.est_seconds > text.est_seconds
    assert scan.cls is L and not scan.needs_docling_permit and scan.needs_cpu_slot
    # more expensive per page, so it turns huge earlier and splits into smaller units
    plain = estimate(CostInputs(FormatKind.PDF, 5 * MIB, pages=100, scanned=False), CFG)
    scanned = estimate(CostInputs(FormatKind.PDF, 5 * MIB, pages=100, scanned=True), CFG)
    assert plain.cls is L and scanned.cls is H
    assert scanned.unit_pages is not None and scanned.unit_pages < 50
    assert scanned.unit_pages * CFG.vlm_s_per_page <= CFG.unit_budget_s


def test_hint_is_prior_only() -> None:
    hint = CostHint(size_bytes=10 * MIB, pages=150, scanned=False)
    # used as a prior when the file is about the size it was: 150 pages of PdfPlumber is large
    used = estimate(CostInputs(FormatKind.PDF, 11 * MIB, hint=hint), CFG)
    assert used.cls is L and used.confidence == "prior" and used.reason.startswith("hint")
    # a file twice the size is a different file: no hint, the biased-small prior
    off = estimate(CostInputs(FormatKind.PDF, 20 * MIB, hint=hint), CFG)
    assert off.cls is S and off.reason.startswith("prior")
    # inspection always decides
    inspected = estimate(CostInputs(FormatKind.PDF, 11 * MIB, pages=3, hint=hint), CFG)
    assert inspected.cls is S and inspected.confidence == "inspected" and inspected.reason.startswith("inspected")
    # a missing hint is just the prior
    assert estimate(CostInputs(FormatKind.PDF, 11 * MIB, hint=None), CFG).reason.startswith("prior")
    # a hint that carries nothing is ignored
    assert estimate(CostInputs(FormatKind.PDF, 11 * MIB, hint=CostHint(size_bytes=11 * MIB)), CFG).cls is S


@pytest.mark.parametrize(
    ("kind", "pages", "cells", "cls", "bounded"),
    [
        (FormatKind.DOCX, 5, None, S, True),
        (FormatKind.DOCX, 100, None, L, True),
        (FormatKind.DOCX, 1000, None, L, False),  # cannot be split: one unbounded in-process job
        (FormatKind.PPTX, 400, None, L, False),
        (FormatKind.XLSX, None, 100_000, S, True),
        (FormatKind.XLSX, None, 50_000_000, L, True),  # load is cut off at its own limit: never above large
        (FormatKind.XLSX, None, 5_000_000_000, L, True),
    ],
)
def test_office_refine(kind: FormatKind, pages: int | None, cells: int | None, cls: CostClass, bounded: bool) -> None:
    e = estimate(CostInputs(kind, 2 * MIB, pages=pages, cells=cells), CFG)
    assert (e.cls, e.bounded, e.split) == (cls, bounded, False)
    assert e.engine is Engine.CPU and e.confidence == "inspected"
    assert not e.needs_docling_permit


def test_office_malformed_metadata_keeps_the_prior() -> None:
    for kind in (FormatKind.DOCX, FormatKind.PPTX, FormatKind.XLSX):
        e = estimate(CostInputs(kind, 2 * MIB, pages=None, cells=None), CFG)
        assert e.confidence == "prior" and e.cls is S


def test_class_change_on_update() -> None:
    before = estimate(CostInputs(FormatKind.PDF, 5 * MIB, pages=20), CFG)
    after = estimate(CostInputs(FormatKind.PDF, 5 * MIB, pages=400), CFG)
    assert before.cls is S and after.cls is H


def test_pdfplumber_forced_routes_small_to_cpu() -> None:
    e = estimate(CostInputs(FormatKind.PDF, MIB, pages=10), CostConfig(pdfplumber_forced=True))
    assert e.engine is Engine.CPU and e.cls is S and e.needs_cpu_slot


def test_config_rejects_nonsense() -> None:
    with pytest.raises(ValueError, match="small_max_s"):
        CostConfig(small_max_s=500.0)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_UNIT_BUDGET_SECONDS", "100")
    monkeypatch.setenv("PARSE_SMALL_MAX_SECONDS", "400")  # above the unit budget: clamped, never invalid
    monkeypatch.setenv("PDF_DOCLING_MAX_PAGES", "10")
    cfg = cost_config_from_env()
    assert cfg.unit_budget_s == 100.0 and cfg.small_max_s == 100.0 and cfg.docling_max_pages == 10
    monkeypatch.setenv("PARSE_UNIT_BUDGET_SECONDS", "")
    assert cost_config_from_env().unit_budget_s == 300.0
