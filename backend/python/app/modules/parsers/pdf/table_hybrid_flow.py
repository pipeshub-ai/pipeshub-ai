"""Shadow run of the table-structure model: collect jobs in the parse worker, send them from a detached task.

Nothing here changes what a parse emits. The worker half (``TableJobCollector``) renders and spools what the model
needs for each eligible table box; every failure of that half is recorded for the box and the parse goes on. After the
record's parse has returned, a detached task of the parent (``ShadowCoordinator``) posts the spooled jobs to the Docling
service one document at a time under a wall-clock deadline, compares each answer with what the evidence path emitted,
and logs one line per table and one per document. No word text is logged.

Active only with ``PDF_TABLE_EXTRACTION=shadow`` and ``PDF_TABLE_HYBRID_SHADOW=true``; otherwise none of this runs.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.parsers.pdf import table_hybrid as th
from app.modules.parsers.pdf.table_hybrid_client import (
    DOWN_LATCH_S,
    UNSUPPORTED_LATCH_S,
    BatchOutcome,
    TableStructureClient,
)
from app.services.docling.table_structure_schema import (
    MAX_SUM_CAPS,
    MAX_TABLES_PER_REQUEST,
    TableResult,
    config_hash,
)
from app.utils.env_config import env_choice, env_positive_int, env_seconds

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

logger = logging.getLogger(__name__)

ENV_SHADOW = "PDF_TABLE_HYBRID_SHADOW"
ENV_MAX_TABLES = "PDF_TABLE_MODEL_MAX_TABLES_PER_DOC"
ENV_TIMEOUT = "PDF_TABLE_MODEL_TIMEOUT_S"
ENV_DOC_SECONDS = "PDF_TABLE_MODEL_DOC_SECONDS"
ENV_SPOOL_DIR = "PIPESHUB_TABLE_SPOOL_DIR"
DEFAULT_MAX_TABLES = 300
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_DOC_SECONDS = 300.0
RENDER_SCALE = 2.0
RESIZE_HEIGHT = 1024
SPOOL_MAX_BYTES = 128 * 1024 * 1024  # per document; past it the boxes are ``spool_full``
QUEUE_WAIT_CAP_S = 120.0  # the service's longest wait for a permit; the request timeout covers it
RECORD_BUDGET_SHARE = 0.8  # the parsing client's read-timeout share of the record budget
CREATE_BLOCKS_RESERVE_S = 600.0
SPOOL_PREFIX = "doc-"
OWNER_FILE = "owner.json"
INDEX_FILE = "index.jsonl"
OWNERLESS_MAX_AGE_S = 24 * 3600.0


# --- settings ----------------------------------------------------------------------------------------------------


def shadow_active() -> bool:
    """True only for ``PDF_TABLE_EXTRACTION=shadow`` with ``PDF_TABLE_HYBRID_SHADOW=true``."""
    from app.modules.parsers.pdf import table_reconcile

    if table_reconcile.table_extraction_mode() != "shadow":
        return False
    return env_choice(ENV_SHADOW, "false", ("true", "false")) == "true"


def max_tables() -> int:
    return env_positive_int(ENV_MAX_TABLES, DEFAULT_MAX_TABLES)


def model_timeout_s() -> float:
    value = env_seconds(ENV_TIMEOUT, DEFAULT_TIMEOUT_S)
    return value if value > 0 else DEFAULT_TIMEOUT_S


def doc_seconds() -> float:
    value = env_seconds(ENV_DOC_SECONDS, DEFAULT_DOC_SECONDS)
    return value if value > 0 else DEFAULT_DOC_SECONDS


# --- deadline and batches (pure) ---------------------------------------------------------------------------------


def remaining_inline(record_timeout_s: float, elapsed_s: float) -> float:
    """Time an emitting mode may spend in phase 2 inside the parse call: the parsing client's share of the record
    budget, less the time already spent and the time reserved for building blocks. Never negative. The detached
    shadow run does not call it."""
    return max(0.0, RECORD_BUDGET_SHARE * record_timeout_s - elapsed_s - CREATE_BLOCKS_RESERVE_S)


def phase2_deadline_s(doc_s: float, remaining_s: float | None = None) -> float:
    """The wall-clock bound of a document's phase 2."""
    return doc_s if remaining_s is None else min(doc_s, remaining_s)


def batch_jobs(jobs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Batches in document order: one closes at 16 tables or when the next table would take the sum of caps over
    ``MAX_SUM_CAPS`` steps, whichever comes first."""
    batches: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    total = 0
    for j in jobs:
        if cur and (len(cur) >= MAX_TABLES_PER_REQUEST or total + j["cap"] > MAX_SUM_CAPS):
            batches.append(cur)
            cur, total = [], 0
        cur.append(j)
        total += j["cap"]
    if cur:
        batches.append(cur)
    return batches


# --- the request the worker builds (what the evaluation harness feeds the model) ---------------------------------


def resize_page(img: np.ndarray, height: int = RESIZE_HEIGHT) -> tuple[np.ndarray, float]:
    """A copy of the library's ``TFPredictor.resize_img`` for the height case: area interpolation, same size rule."""
    import cv2

    h, w = img.shape[:2]
    r = height / float(h)
    return cv2.resize(img, (int(w * r), height), interpolation=cv2.INTER_AREA), r


def build_request(img: np.ndarray, box: tuple[float, float, float, float]) -> tuple[bytes, float, list[float], tuple[int, int]]:
    """PNG of the table crop, the scale factor and the table box in the resized page, from the page render *img*
    (RGB, scale 2) and the box in points. The page is resized as a whole, the box scaled by the same factor, then cropped."""
    import cv2

    rz, sf = resize_page(img)
    tb = [round(box[0]) * RENDER_SCALE * sf, round(box[1]) * RENDER_SCALE * sf, round(box[2]) * RENDER_SCALE * sf, round(box[3]) * RENDER_SCALE * sf]
    import numpy as np

    crop = np.ascontiguousarray(rz[round(tb[1]) : round(tb[3]), round(tb[0]) : round(tb[2])])
    if crop.size == 0:
        raise ValueError("empty crop")
    ok, png = cv2.imencode(".png", crop)
    if not ok:
        raise ValueError("png encode failed")
    return png.tobytes(), sf, tb, (int(crop.shape[0]), int(crop.shape[1]))


def _in_box(w: dict[str, Any], b: tuple[float, float, float, float]) -> bool:
    x, y = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def _shape(region: object) -> list[int] | None:
    grid = getattr(region, "table_grid", None)
    if not grid:
        return None
    return [len(grid), max(len(r) for r in grid)]


def _overlap_area(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


# --- the spool ---------------------------------------------------------------------------------------------------


def spool_root() -> str:
    """Container-local: the temp directory, never a shared volume (another container's pid always looks dead)."""
    return os.environ.get(ENV_SPOOL_DIR) or os.path.join(tempfile.gettempdir(), "pipeshub-table-spool")


def process_start_token(pid: int) -> str | None:
    """A token that changes when a pid is reused: the process's start time. ``None`` when the process is gone."""
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/stat", "rb") as f:
                raw = f.read().decode("utf-8", "replace")
            return raw.rsplit(")", 1)[1].split()[19]  # field 22: start time in clock ticks
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False).stdout.strip()
        return out or None
    except (OSError, IndexError, subprocess.SubprocessError):
        return None


_own: dict[str, str | None] = {}


def own_token() -> str | None:
    if "token" not in _own:
        _own["token"] = process_start_token(os.getpid())
    return _own["token"]


def create_spool() -> str:
    """A new spool directory named for its owner, with the owner's pid and start token inside."""
    root = spool_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    path = os.path.join(root, f"{SPOOL_PREFIX}{os.getpid()}-{uuid.uuid4().hex[:12]}")
    os.mkdir(path, 0o700)
    with open(os.path.join(path, OWNER_FILE), "w") as f:
        json.dump({"pid": os.getpid(), "start": own_token()}, f)
    return path


def remove_spool(path: str | None) -> None:
    if path:
        shutil.rmtree(path, ignore_errors=True)


def sweep_stale(root: str | None = None, token_of: Callable[[int], str | None] = process_start_token) -> int:
    """Remove spool directories whose owner is gone: its pid is dead, or alive with another start token (a restarted
    container reuses pids). Never by age alone; a directory with no owner file goes only after a day."""
    root = root or spool_root()
    removed = 0
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(root, name)
        if not name.startswith(SPOOL_PREFIX) or not os.path.isdir(path):
            continue
        try:
            with open(os.path.join(path, OWNER_FILE)) as f:
                owner = json.load(f)
            pid, start = int(owner["pid"]), owner.get("start")
        except (OSError, ValueError, KeyError, TypeError):
            with contextlib.suppress(OSError):
                if time.time() - os.path.getmtime(path) > OWNERLESS_MAX_AGE_S:
                    remove_spool(path)
                    removed += 1
            continue
        live = token_of(pid)
        if live is not None and start is not None and live == start:
            continue
        remove_spool(path)
        removed += 1
    return removed


# --- worker half: collect jobs, never fail the parse -------------------------------------------------------------


def _default_renderer(pdf_path: str) -> Callable[[int], tuple[Any, float, float]]:
    """``render(page_index) -> (RGB array at scale 2, width pt, height pt)`` from the file."""
    import numpy as np
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_path)

    def render(index: int) -> tuple[Any, float, float]:
        page = doc[index]
        try:
            w, h = page.get_size()
            bitmap = page.render(scale=RENDER_SCALE)
            return np.asarray(bitmap.to_pil().convert("RGB")), float(w), float(h)
        finally:
            page.close()

    render.close = doc.close  # type: ignore[attr-defined]
    return render


class TableJobCollector:
    """Renders and spools one job per eligible table box and an index line per box. Per box, any exception is
    recorded as ``collect_error`` and the parse continues; nothing from here reaches the caller."""

    def __init__(
        self,
        spool_dir: str,
        pdf_path: str,
        *,
        renderer: Callable[[int], tuple[Any, float, float]] | None = None,
        table_budget: int | None = None,
        byte_cap: int = SPOOL_MAX_BYTES,
    ) -> None:
        self._dir = spool_dir
        self._pdf_path = pdf_path
        self._renderer = renderer
        self._budget = max_tables() if table_budget is None else table_budget
        self._byte_cap = byte_cap
        self.sent = 0
        self.bytes = 0
        self.errors = 0
        self._warned = False
        self._page_cache: tuple[int, Any, float, float] | None = None

    def close(self) -> None:
        close = getattr(self._renderer, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                close()

    def _warn_once(self, what: str) -> None:
        self.errors += 1
        if not self._warned:
            self._warned = True
            logger.warning("table hybrid shadow: job collection failed (%s); the parse goes on", what)

    def _render(self, page_index: int) -> tuple[Any, float, float]:
        if self._page_cache is not None and self._page_cache[0] == page_index:
            return self._page_cache[1:]  # type: ignore[return-value]
        if self._renderer is None:
            self._renderer = _default_renderer(self._pdf_path)
        img, w, h = self._renderer(page_index)
        self._page_cache = (page_index, img, w, h)
        return img, w, h

    def _append(self, rec: dict[str, Any]) -> None:
        with open(os.path.join(self._dir, INDEX_FILE), "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def collect_page(
        self,
        page_index: int,
        page_w: float,
        page_h: float,
        decisions: list[Any],
        legacy: list[Any],
        evidence: list[Any] | None,
        all_words: list[dict[str, Any]],
    ) -> None:
        """Never raises."""
        try:
            self._collect_page(page_index, page_w, page_h, decisions, legacy, evidence, all_words)
        except Exception as exc:
            self._warn_once(type(exc).__name__)

    def _collect_page(
        self, page_index: int, page_w: float, page_h: float, decisions: list[Any], legacy: list[Any],
        evidence: list[Any] | None, all_words: list[dict[str, Any]],
    ) -> None:
        eligible = [d for d in decisions if d.kind != "not_table"]
        eligible.sort(key=lambda d: (d.box[1], d.box[0]))
        words_all = [w for w in all_words if str(w.get("text", "")).strip()]
        for bi, d in enumerate(eligible):
            box = tuple(float(v) for v in d.box)
            rec: dict[str, Any] = {
                "key": f"p{page_index + 1}:b{bi}", "page": page_index + 1, "bi": bi, "reason": "none", "top": box[1], "left": box[0],
            }
            try:
                self._one_box(rec, page_index, page_w, page_h, box, legacy, evidence, words_all)
            except Exception as exc:
                self._warn_once(type(exc).__name__)
                rec.update(reason="collect_error", png=None)
            try:
                self._append(rec)
            except Exception as exc:
                self._warn_once(type(exc).__name__)

    def _one_box(
        self, rec: dict[str, Any], page_index: int, page_w: float, page_h: float, box: tuple[float, ...], legacy: list[Any],
        evidence: list[Any] | None, words_all: list[dict[str, Any]],
    ) -> None:
        words = [w for w in words_all if _in_box(w, box)]
        plan = th.plan_box(words)
        rec.update(lines=plan.lines, cols_est=plan.cols_est, cap=plan.cap, cap_reason=plan.cap_reason, ntok=len(words))
        rec["legacy_shape"] = self._best_shape(legacy, box)
        if evidence is not None:
            rec["evidence_shape"] = self._best_shape(evidence, box)
            ev_sets = [
                {i for i, w in enumerate(words_all) if _in_box(w, tuple(r.bbox))}
                for r in evidence
                if getattr(r, "type", None) is not None and getattr(r.type, "name", "") == "TABLE" and _overlap_area(tuple(r.bbox), box) > 0
            ]
            box_ids = {i for i, w in enumerate(words_all) if _in_box(w, box)}
            rec["box_class"] = th.box_class(box_ids, ev_sets)
        if plan.reason != "none":
            rec["reason"] = plan.reason
            return
        if self.sent >= self._budget:
            rec["reason"] = "budget"
            return
        if self.bytes >= self._byte_cap:
            rec["reason"] = "spool_full"
            return
        img, w, h = self._render(page_index)
        if abs(w - page_w) > 1.0 or abs(h - page_h) > 1.0:
            raise ValueError("page size differs between renderers")
        png, sf, tb, _size = build_request(img, box)
        name = f"p{page_index + 1}-b{rec['bi']}.png"
        tmp = os.path.join(self._dir, name + ".part")
        with open(tmp, "wb") as f:
            f.write(png)
        os.replace(tmp, os.path.join(self._dir, name))
        self.bytes += len(png)
        self.sent += 1
        rec.update(
            png=name, png_bytes=len(png), scale_factor=sf, page_w=w * RENDER_SCALE, page_h=h * RENDER_SCALE, table_bbox=tb,
            words=[[x["text"], x["x0"], x["top"], x["x1"], x["bottom"]] for x in words],
        )

    @staticmethod
    def _best_shape(regions: list[Any], box: tuple[float, float, float, float]) -> list[int] | None:
        best, area = None, 0.0
        for r in regions:
            if getattr(r.type, "name", "") != "TABLE":
                continue
            a = _overlap_area(tuple(r.bbox), box)
            if a > area:
                best, area = r, a
        return _shape(best) if best is not None else None


# --- parent half: detached run ----------------------------------------------------------------------------------


@dataclass
class Admission:
    spool_dir: str


@dataclass
class DocStats:
    record_id: str | None
    request_id: str | None
    tables_seen: int = 0
    prefiltered: int = 0
    sent: int = 0
    budget: int = 0
    deadline_hit: bool = False
    fallback_by_reason: dict[str, int] = field(default_factory=dict)
    phase2_wall_s: float = 0.0
    bytes_sent: int = 0
    spool_peak_bytes: int = 0
    outcome: str = "ok"
    kinds: dict[str, int] = field(default_factory=dict)

    def note(self, reason: str) -> None:
        self.fallback_by_reason[reason] = self.fallback_by_reason.get(reason, 0) + 1


def _read_index(spool_dir: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with open(os.path.join(spool_dir, INDEX_FILE)) as f:
            for line in f:
                with contextlib.suppress(ValueError):
                    out.append(json.loads(line))
    except OSError:
        return []
    out.sort(key=lambda r: (r["page"], r["bi"]))
    return out


def _read_png(spool_dir: str, name: str) -> bytes:
    with open(os.path.join(spool_dir, name), "rb") as f:
        return f.read()


def _tokens(rec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"id": i, "bbox": [w[1] * RENDER_SCALE, w[2] * RENDER_SCALE, w[3] * RENDER_SCALE, w[4] * RENDER_SCALE], "text": w[0]}
        for i, w in enumerate(rec["words"])
    ]


def _duplicate_boxes(tokens: list[dict[str, Any]]) -> bool:
    keys = {tuple(round(v, 2) for v in t["bbox"]) for t in tokens}
    return len(keys) != len(tokens)


def analyse_table(rec: dict[str, Any], result: TableResult | None, failure: str | None) -> dict[str, Any]:
    """The comparison of one answered table with the evidence path, as the fields of its log line (no word text)."""
    out: dict[str, Any] = {"route": "evidence", "reason": failure or "none"}
    if failure or result is None:
        out["would_route"], out["would_reason"] = th.would_route(failure or "error", None, None, 0.0)
        return out
    out.update(steps=result.steps, model_cpu_ms=result.cpu_ms, model_wall_ms=result.wall_ms)
    if result.status == "cap_hit":
        out["would_route"], out["would_reason"] = th.would_route("cap_hit", None, None, 0.0)
        out["reason"] = "cap_hit"
        return out
    if result.status != "ok":
        reason = {"timeout": "timeout", "service_busy": "service_busy", "weights": "unsupported"}.get(result.err or "", "error")
        out["reason"] = reason
        out["would_route"], out["would_reason"] = th.would_route(reason, None, None, 0.0)
        return out
    cells = [c.model_dump() for c in (result.cells or [])]
    words = {i: {"text": w[0], "x0": w[1], "top": w[2], "x1": w[3], "bottom": w[4]} for i, w in enumerate(rec["words"])}
    texts = {i: w["text"] for i, w in words.items()}
    check = th.validate_cells(cells, result.n_rows or 0, result.n_cols or 0, set(texts))
    if not check.ok:
        out["reason"] = "malformed"
        out["malformed_why"] = check.reason
        out["would_route"], out["would_reason"] = th.would_route(None, check, None, 0.0)
        return out
    shape = th.model_shape(cells)
    loss_all, loss_content = th.word_losses(texts, cells)
    would, why = th.would_route(None, check, shape, loss_content)
    out.update(
        tf_shape=list(shape), shape_equal=(rec.get("evidence_shape") == list(shape)) if rec.get("evidence_shape") else None,
        would_route=would, would_reason=why, reason=why, word_loss_all=round(loss_all, 4), word_loss_content=round(loss_content, 4),
        glyph_candidates=th.glyph_candidates(texts, cells), overlaps=check.overlaps, checks=th.hard_flags(words, cells, shape),
    )
    out["route"] = "model" if would == "model" else "evidence"
    return out


class ShadowCoordinator:
    """One document at a time per process: a document that arrives while another is running or waiting for its
    turn is not shadowed (``shadow_busy``). Owns the spool directories and the detached tasks."""

    def __init__(
        self,
        client: TableStructureClient | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        doc_s: Callable[[], float] = doc_seconds,
    ) -> None:
        self._client = client
        self._clock = clock
        self._doc_s = doc_s
        self._busy = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._latched_until: dict[str, float] = {}
        self._swept = False

    def _client_or_default(self) -> TableStructureClient:
        if self._client is None:
            self._client = TableStructureClient()
        return self._client

    def latch(self, reason: str, seconds: float) -> None:
        self._latched_until[reason] = self._clock() + seconds

    def _latched(self) -> str | None:
        now = self._clock()
        for reason, until in self._latched_until.items():
            if until > now:
                return reason
        return None

    def admit(self, record_id: str | None = None, request_id: str | None = None) -> Admission | None:
        """Decide before the parse whether this document is shadowed, from what is known now: the single slot, and a
        latched unsupported or service-down state. Returns the spool directory, or ``None`` (the document line says why)."""
        why: str | None = None
        if self._busy:
            why = "queue_full"
        else:
            latched = self._latched()
            if latched is not None:
                why = latched
        if why is not None:
            self._log_document(DocStats(record_id, request_id, outcome=f"shadow_busy:{why}"), None)
            return None
        if not self._swept:
            self._swept = True
            with contextlib.suppress(Exception):
                sweep_stale()
        try:
            path = create_spool()
        except OSError:
            self._log_document(DocStats(record_id, request_id, outcome="shadow_busy:spool"), None)
            return None
        self._busy = True
        return Admission(path)

    def abandon(self, admission: Admission) -> None:
        """The parse did not complete: free the slot and remove the spool."""
        remove_spool(admission.spool_dir)
        self._busy = False

    def start(self, admission: Admission, record_id: str | None, request_id: str | None) -> asyncio.Task[None]:
        """Start the detached run in a fresh context that keeps only the ids it logs."""
        task = asyncio.get_running_loop().create_task(
            self._run(admission, record_id, request_id), name="table-hybrid-shadow", context=contextvars.Context()
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def shutdown(self) -> None:
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _run(self, admission: Admission, record_id: str | None, request_id: str | None) -> None:
        stats = DocStats(record_id, request_id)
        t0 = self._clock()
        try:
            await self._phase2(admission.spool_dir, stats, t0)
        except asyncio.CancelledError:
            stats.outcome = "cancelled"
            raise
        except Exception as exc:  # shadow never raises into anything
            stats.outcome = f"error:{type(exc).__name__}"
        finally:
            stats.phase2_wall_s = round(self._clock() - t0, 3)
            self._busy = False
            self._log_document(stats, admission.spool_dir)
            await asyncio.shield(asyncio.to_thread(remove_spool, admission.spool_dir))

    async def _phase2(self, spool_dir: str, stats: DocStats, t0: float) -> None:
        records = await asyncio.to_thread(_read_index, spool_dir)
        stats.tables_seen = len(records)
        stats.spool_peak_bytes = sum(r.get("png_bytes", 0) for r in records)
        jobs: list[dict[str, Any]] = []
        for rec in records:
            reason = rec.get("reason", "error")
            if reason == "none":
                jobs.append(rec)
                continue
            if reason == "prefilter":
                stats.prefiltered += 1
            if reason == "budget":
                stats.budget += 1
            stats.note(reason)
            self._log_table(stats, rec, {"route": "evidence", "reason": reason, "would_route": "evidence", "would_reason": reason})
        deadline = t0 + self._doc_s()
        stop: str | None = None  # a reason that ends the document's sending
        warned_version = False
        for batch in batch_jobs(jobs):
            if stop is None and self._clock() >= deadline:
                stop = "deadline"
                stats.deadline_hit = True
            if stop is not None:
                for rec in batch:
                    self._fail(stats, rec, stop)
                continue
            ok_batch, tokens_by_key = [], {}
            for rec in batch:
                toks = _tokens(rec)
                if _duplicate_boxes(toks):
                    self._fail(stats, rec, "malformed")
                    continue
                ok_batch.append(rec)
                tokens_by_key[rec["key"]] = toks
            if not ok_batch:
                continue
            files, specs = {}, []
            for n, rec in enumerate(ok_batch):
                png = await asyncio.to_thread(_read_png, spool_dir, rec["png"])
                files[f"c{n}"] = png
                stats.bytes_sent += len(png)
                specs.append({
                    "id": rec["key"], "image": f"c{n}", "scale_factor": rec["scale_factor"], "page_w": rec["page_w"],
                    "page_h": rec["page_h"], "table_bbox": rec["table_bbox"], "cap": rec["cap"], "tokens": tokens_by_key[rec["key"]],
                })
            left = deadline - self._clock()
            timeout = min(left, sum(_table_timeout(r["cap"]) for r in ok_batch) + 10.0 + QUEUE_WAIT_CAP_S)
            stats.sent += len(ok_batch)
            sent_at = self._clock()
            outcome = await self._client_or_default().post_batch(
                {"v": 1, "tables": specs}, files, deadline_ms=int(left * 1000), timeout_s=timeout
            )
            wall_ms = (self._clock() - sent_at) * 1000
            if outcome.kind != "ok":
                for rec in ok_batch:
                    self._fail(stats, rec, outcome.kind)
                if outcome.kind in ("service_busy", "service_down", "unsupported", "mode_fast"):
                    stop = outcome.kind
                    if outcome.kind == "service_down":
                        self.latch("service_down", DOWN_LATCH_S)
                    elif outcome.kind in ("unsupported", "mode_fast"):
                        self.latch("unsupported", UNSUPPORTED_LATCH_S)
                continue
            mismatch = self._version_mismatch(outcome)
            if mismatch and not warned_version:
                warned_version = True
                logger.warning("table hybrid shadow: model identity differs from the measured one (%s)", ",".join(mismatch))
            by_id = {t.id: t for t in outcome.response.tables}  # type: ignore[union-attr]
            model_ms = sum((t.wall_ms or 0.0) for t in by_id.values())
            for rec in ok_batch:
                res = by_id.get(rec["key"])
                if mismatch and res is not None and res.status == "ok":
                    self._fail(stats, rec, "version_mismatch")
                    continue
                fields = analyse_table(rec, res, None if res is not None else "error")
                fields.update(batch_tables=len(ok_batch), batch_cap_sum=sum(r["cap"] for r in ok_batch), queue_wait_ms=round(max(0.0, wall_ms - model_ms), 1))
                if fields["reason"] != "none":
                    stats.note(fields["reason"])
                self._log_table(stats, rec, fields)
                if res is not None and res.status == "error" and res.err == "service_busy":
                    stop = "service_busy"
                if res is not None and res.status == "error" and res.err == "weights":
                    stop = "unsupported"  # the service has no model files: not asked again for a while
                    self.latch("unsupported", UNSUPPORTED_LATCH_S)

    def _version_mismatch(self, outcome: BatchOutcome) -> list[str]:
        model = outcome.response.model if outcome.response else None
        if model is None:
            return ["no_model_block"] if outcome.response and any(t.status == "ok" for t in outcome.response.tables) else []
        moved = []
        if model.docling_ibm_models != th.MODEL_LIBRARY_VERSION:
            moved.append("library")
        if model.mode != th.MODEL_MODE:
            moved.append("mode")
        if model.weights_sha256 != th.MODEL_WEIGHTS_SHA256:
            moved.append("weights")
        if model.model_revision != th.MODEL_REVISION:
            moved.append("revision")
        if model.config_sha256 != config_hash(th.MODEL_LIBRARY_VERSION, th.MODEL_MODE, th.MODEL_WEIGHTS_SHA256, th.MODEL_REVISION):
            moved.append("config")
        return moved

    def _fail(self, stats: DocStats, rec: dict[str, Any], reason: str) -> None:
        stats.note(reason)
        self._log_table(stats, rec, {"route": "evidence", "reason": reason, "would_route": "evidence", "would_reason": reason})

    def _log_table(self, stats: DocStats, rec: dict[str, Any], fields: dict[str, Any]) -> None:
        line = {
            "record_id": stats.record_id, "request_id": stats.request_id, "mode": "shadow", "key": rec["key"],
            "lines": rec.get("lines"), "cols_est": rec.get("cols_est"), "cap": rec.get("cap"), "ntok": rec.get("ntok"),
            "cap_reason": rec.get("cap_reason"), "box_class": rec.get("box_class"), "legacy_shape": rec.get("legacy_shape"),
            "evidence_shape": rec.get("evidence_shape"), **fields,
        }
        logger.info("table hybrid shadow table: %s", json.dumps(line, sort_keys=True, separators=(",", ":")))

    def _log_document(self, stats: DocStats, spool_dir: str | None) -> None:
        reasons = stats.fallback_by_reason
        doc = {
            "record_id": stats.record_id, "request_id": stats.request_id, "outcome": stats.outcome,
            "tables_seen": stats.tables_seen if not stats.outcome.startswith("shadow_busy") else None,
            "prefiltered": stats.prefiltered, "sent": stats.sent, "budget": stats.budget, "deadline_hit": stats.deadline_hit,
            "fallback_by_reason": dict(sorted(reasons.items())),
            "docs_with_load_fallback": any(r in th.LOAD_REASONS for r in reasons), "phase2_wall_s": stats.phase2_wall_s,
            "bytes_sent": stats.bytes_sent, "spool_peak_bytes": stats.spool_peak_bytes,
        }
        logger.info("table hybrid shadow document: %s", json.dumps(doc, sort_keys=True, separators=(",", ":")))


def _table_timeout(cap: int) -> float:
    from app.services.docling.table_structure import table_timeout_s

    return table_timeout_s(cap, model_timeout_s())


_coordinator: dict[str, ShadowCoordinator | None] = {"c": None}


def get_coordinator() -> ShadowCoordinator:
    if _coordinator["c"] is None:
        _coordinator["c"] = ShadowCoordinator()
    return _coordinator["c"]  # type: ignore[return-value]


def set_coordinator(c: ShadowCoordinator | None) -> None:
    """Replace the process's coordinator (tests)."""
    _coordinator["c"] = c


async def shutdown_shadow() -> None:
    if _coordinator["c"] is not None:
        await _coordinator["c"].shutdown()
