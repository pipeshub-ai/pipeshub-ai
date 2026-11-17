"""Parse cost estimator and cost classes (pure: no I/O, no clock).

A heavy-format record gets an estimated parse time from what is known when it
is admitted: its format, its size and, once inspected, its page count. The
class (small, large, huge) comes only from that estimate. Bytes decide the
memory charge, never the class.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from app.capacity.classes import CostClass
from app.utils.env_config import env_float, env_int

__all__ = [
    "CostClass",
    "CostConfig",
    "CostEstimate",
    "CostHint",
    "CostInputs",
    "Engine",
    "FormatKind",
    "estimate",
    "cost_config_from_env",
]

_MIB = 1024 * 1024
_GIB = 1024 * _MIB


class Engine(StrEnum):
    DOCLING = "docling"  # the Docling service: needs a Docling permit, no CPU parse slot
    CPU = "cpu"  # a parse-pool worker (PdfPlumber, Excel load, in-process Office conversion)
    VLM = "vlm"  # scanned pages: render on CPU, OCR calls on the LLM gateway
    INLINE = "inline"  # runs in the record's own task


class FormatKind(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    IMAGE = "image"
    TEXT = "text"
    OTHER = "other"


@dataclass(frozen=True)
class CostHint:
    """What an earlier revision of the same record learned. Only a prior."""

    size_bytes: int | None = None
    pages: int | None = None
    cells: int | None = None
    scanned: bool | None = None


@dataclass(frozen=True)
class CostInputs:
    kind: FormatKind
    size_bytes: int | None = None  # 0 and None both mean unknown
    pages: int | None = None  # inspection: PDF page count, docx Pages, pptx Slides
    cells: int | None = None  # inspection: xlsx sum of sheet dimension areas
    scanned: bool | None = None
    hint: CostHint | None = None


@dataclass(frozen=True)
class CostConfig:
    """Rates and boundaries. The estimator reads nothing else."""

    small_max_s: float = 60.0
    unit_budget_s: float = 300.0
    unit_max_pages: int = 50
    pdfplumber_s_per_page: float = 1.5
    docling_s_per_page: float = 1.0
    # Wall time per scanned page: the render plus the OCR calls at the default OCR concurrency.
    vlm_s_per_page: float = 8.0
    office_s_per_unit: float = 1.0
    excel_s_per_million_cells: float = 10.0
    excel_load_max_s: float = 240.0
    docling_max_pages: int = 30
    docling_max_bytes: int = 100 * _MIB
    pdfplumber_forced: bool = False
    # Size priors, used only when nothing was inspected.
    pdf_bytes_per_page: int = 100 * 1024
    office_bytes_per_unit: int = 50 * 1024
    xlsx_bytes_per_cell: int = 8
    pdf_base_mem: int = int(1.25 * _GIB)
    office_base_mem: int = int(1.0 * _GIB)
    xlsx_mem: int = int(1.5 * _GIB)
    light_mem: int = 64 * _MIB
    # A hint is a prior only while the file is about the size it was.
    hint_size_tolerance: float = 0.5

    def __post_init__(self) -> None:
        if not 0 < self.small_max_s <= self.unit_budget_s:
            raise ValueError("need 0 < small_max_s <= unit_budget_s")
        if self.unit_max_pages < 1:
            raise ValueError("unit_max_pages must be at least 1")
        for name in (
            "pdfplumber_s_per_page",
            "docling_s_per_page",
            "vlm_s_per_page",
            "office_s_per_unit",
            "excel_s_per_million_cells",
        ):
            if not getattr(self, name) > 0:
                raise ValueError(f"{name} must be positive")


def cost_config_from_env() -> CostConfig:
    """The defaults, with the documented env overrides."""
    d = CostConfig()
    unit = env_float("PARSE_UNIT_BUDGET_SECONDS", d.unit_budget_s, low=1.0, high=86400.0)
    return CostConfig(
        small_max_s=min(unit, env_float("PARSE_SMALL_MAX_SECONDS", d.small_max_s, low=1.0, high=3600.0)),
        unit_budget_s=unit,
        unit_max_pages=max(1, env_int("PARSE_UNIT_MAX_PAGES", d.unit_max_pages)),
        docling_s_per_page=env_float("PARSE_DOCLING_SECONDS_PER_PAGE", d.docling_s_per_page, low=0.01, high=600.0),
        docling_max_pages=max(0, env_int("PDF_DOCLING_MAX_PAGES", d.docling_max_pages)),
    )


@dataclass(frozen=True)
class CostEstimate:
    cls: CostClass
    engine: Engine
    est_seconds: float  # worker-seconds of the parse step, no LLM time
    mem_bytes: int  # peak worker memory, admitted by the memory reservation, not by class
    split: bool
    unit_pages: int | None
    bounded: bool  # est_seconds <= unit budget, or one unit of a split document
    confidence: Literal["prior", "inspected"]
    reason: str
    needs_docling_permit: bool = field(default=False)

    @property
    def needs_cpu_slot(self) -> bool:
        """A CPU parse step is reserved for this parse (a Docling conversion is reserved by the Docling service)."""
        return self.engine in (Engine.CPU, Engine.VLM) and not self.needs_docling_permit


def _unknown(size: int | None) -> bool:
    return size is None or size <= 0


def _class_of(est: float, cfg: CostConfig) -> CostClass:
    if est <= cfg.small_max_s:
        return CostClass.SMALL
    return CostClass.LARGE if est <= cfg.unit_budget_s else CostClass.HUGE


def _prior_inputs(inp: CostInputs, cfg: CostConfig) -> tuple[CostInputs, bool]:
    """Fold a usable hint into the inputs. Returns (inputs, hint_used)."""
    h = inp.hint
    if h is None or inp.pages is not None or inp.cells is not None:
        return inp, False
    if h.pages is None and h.cells is None:
        return inp, False
    if not _unknown(inp.size_bytes) and not _unknown(h.size_bytes):
        assert inp.size_bytes is not None and h.size_bytes is not None
        if abs(inp.size_bytes - h.size_bytes) > cfg.hint_size_tolerance * h.size_bytes:
            return inp, False
    scanned = inp.scanned if inp.scanned is not None else h.scanned
    return (
        CostInputs(
            kind=inp.kind,
            size_bytes=inp.size_bytes,
            pages=h.pages,
            cells=h.cells,
            scanned=scanned,
        ),
        True,
    )


def estimate(inp: CostInputs, cfg: CostConfig | None = None) -> CostEstimate:
    cfg = cfg or CostConfig()
    inp, hinted = _prior_inputs(inp, cfg)
    size = 0 if _unknown(inp.size_bytes) else int(inp.size_bytes or 0)
    inspected = (inp.pages is not None or inp.cells is not None) and not hinted
    confidence: Literal["prior", "inspected"] = "inspected" if inspected else "prior"
    tag = "hint" if hinted else ("inspected" if inspected else "prior")
    kind = inp.kind

    if kind is FormatKind.TEXT or kind is FormatKind.IMAGE:
        return CostEstimate(
            CostClass.SMALL, Engine.INLINE, 0.0, cfg.light_mem, False, None, True, confidence, f"{kind.value}_inline"
        )
    if kind is FormatKind.PDF:
        return _pdf(inp, cfg, size, tag, hinted=hinted)
    if kind in (FormatKind.DOCX, FormatKind.PPTX):
        return _office(inp, cfg, size, tag, hinted=hinted)
    if kind is FormatKind.XLSX:
        return _xlsx(inp, cfg, size, tag, hinted=hinted)
    # Unrecognised formats are heavy by default (tiers.classify) and cheap until proven otherwise.
    return CostEstimate(
        CostClass.SMALL, Engine.CPU, 0.0, cfg.pdf_base_mem + size, False, None, True, "prior", "unrecognised_prior"
    )


def _classify(est: float, size: int, *, hinted: bool, inspected: bool, cfg: CostConfig) -> tuple[CostClass, float]:
    """Class and estimate. Before inspection the class is biased small: only a size that alone proves it
    makes it large, and then the estimate is clamped into the large band (a size prior is no measurement)."""
    if inspected or hinted:
        return _class_of(est, cfg), est
    if size > cfg.docling_max_bytes:
        return CostClass.LARGE, min(cfg.unit_budget_s, max(est, cfg.small_max_s + 1.0))
    return CostClass.SMALL, min(est, cfg.small_max_s)


_RANK = {CostClass.SMALL: 0, CostClass.LARGE: 1, CostClass.HUGE: 2}


def _never_below_the_size_prior(
    cls: CostClass, est: float, prior_est: float, size: int, *, cfg: CostConfig
) -> tuple[CostClass, float]:
    """Metadata a document writes about itself (docx Pages, pptx Slides, an xlsx dimension) is stale or missing in
    files made by common writers, so it may raise a class but never lower it below what the size alone says. Only
    measured facts (a PDF's page count and text layer) may lower one."""
    prior_cls, prior_est = _classify(prior_est, size, hinted=False, inspected=False, cfg=cfg)
    if _RANK[prior_cls] > _RANK[cls]:
        return prior_cls, max(est, prior_est)
    return cls, est


def _pdf(inp: CostInputs, cfg: CostConfig, size: int, tag: str, *, hinted: bool) -> CostEstimate:
    inspected = inp.pages is not None and not hinted
    pages_known = inp.pages is not None
    if pages_known:
        pages = max(1, int(inp.pages or 0))
    else:
        pages = max(1, math.ceil(size / cfg.pdf_bytes_per_page)) if size else 1
    scanned = bool(inp.scanned)
    too_big = size > cfg.docling_max_bytes
    by_pages = pages_known and pages > cfg.docling_max_pages
    if scanned:
        engine, rate, why = Engine.VLM, cfg.vlm_s_per_page, "scanned"
    elif cfg.pdfplumber_forced:
        engine, rate, why = Engine.CPU, cfg.pdfplumber_s_per_page, "pdfplumber_forced"
    elif too_big or by_pages:
        engine, rate, why = Engine.CPU, cfg.pdfplumber_s_per_page, "over_docling_limits"
    else:
        engine, rate, why = Engine.DOCLING, cfg.docling_s_per_page, "docling_limits"
    est = pages * rate
    cls, est = _classify(est, size, hinted=hinted, inspected=inspected, cfg=cfg)
    split = cls is CostClass.HUGE and engine in (Engine.CPU, Engine.VLM)
    unit_pages = None
    bounded = est <= cfg.unit_budget_s
    if split:
        unit_pages = max(1, min(cfg.unit_max_pages, math.floor(cfg.unit_budget_s / rate)))
        bounded = True  # every unit is
    mem = cfg.pdf_base_mem + size
    return CostEstimate(
        cls=cls,
        engine=engine,
        est_seconds=est,
        mem_bytes=mem,
        split=split,
        unit_pages=unit_pages,
        bounded=bounded,
        confidence="inspected" if inspected else "prior",
        reason=f"{tag}:{why}",
        needs_docling_permit=engine is Engine.DOCLING and cls is CostClass.SMALL,
    )


def _office(inp: CostInputs, cfg: CostConfig, size: int, tag: str, *, hinted: bool) -> CostEstimate:
    inspected = inp.pages is not None and not hinted
    size_units = max(1, math.ceil(size / cfg.office_bytes_per_unit)) if size else 1
    units = max(1, int(inp.pages)) if inp.pages is not None else size_units
    est = units * cfg.office_s_per_unit
    cls, est = _classify(est, size, hinted=hinted, inspected=inspected, cfg=cfg)
    if inp.pages is not None:  # facts the document wrote about itself, current or from an earlier revision
        cls, est = _never_below_the_size_prior(cls, est, size_units * cfg.office_s_per_unit, size, cfg=cfg)
    # An office document cannot be split: over the unit budget it stays one unbounded in-process job.
    bounded = est <= cfg.unit_budget_s
    if cls is CostClass.HUGE:
        cls = CostClass.LARGE
    return CostEstimate(
        cls=cls,
        engine=Engine.CPU,
        est_seconds=est,
        mem_bytes=cfg.office_base_mem + size,
        split=False,
        unit_pages=None,
        bounded=bounded,
        confidence="inspected" if inspected else "prior",
        reason=f"{tag}:office_in_process",
    )


def _xlsx(inp: CostInputs, cfg: CostConfig, size: int, tag: str, *, hinted: bool) -> CostEstimate:
    inspected = inp.cells is not None and not hinted
    size_cells = size // cfg.xlsx_bytes_per_cell if size else 0
    cells = max(0, int(inp.cells)) if inp.cells is not None else size_cells
    # The load is cut off at its own limit, so a workbook is never more than large.
    est = min(cfg.excel_load_max_s, cells / 1e6 * cfg.excel_s_per_million_cells)
    cls, est = _classify(est, size, hinted=hinted, inspected=inspected, cfg=cfg)
    if inp.cells is not None:
        prior_est = min(cfg.excel_load_max_s, size_cells / 1e6 * cfg.excel_s_per_million_cells)
        cls, est = _never_below_the_size_prior(cls, est, prior_est, size, cfg=cfg)
    if cls is CostClass.HUGE:
        cls = CostClass.LARGE
    return CostEstimate(
        cls=cls,
        engine=Engine.CPU,
        est_seconds=est,
        mem_bytes=cfg.xlsx_mem,
        split=False,
        unit_pages=None,
        bounded=est <= cfg.unit_budget_s,
        confidence="inspected" if inspected else "prior",
        reason=f"{tag}:excel_load",
    )
