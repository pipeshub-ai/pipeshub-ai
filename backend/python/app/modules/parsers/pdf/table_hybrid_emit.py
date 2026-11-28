"""Hybrid table extraction, emit phase (``PDF_TABLE_EXTRACTION=hybrid``).

For each eligible layout-model table box of a page, the parse worker asks the table-structure service for the model's
grid (synchronously), builds the cell text from the page's own words, runs the fallback ledger and, for a box that
passes, replaces the evidence table in that box by a table region that carries the model's grid. The rest of the page
pass then runs once with that region claimed. Every other box, and every failure of any kind, keeps the evidence result;
nothing here raises into the parse.

No word text is ever logged.
"""
from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.modules.parsers.pdf import table_hybrid as th
from app.modules.parsers.pdf import table_hybrid_text as tt
from app.modules.parsers.pdf.drawn_rules import crossings, drawn_rules
from app.modules.parsers.pdf.table_hybrid_client import (
    BatchOutcome,
)
from app.modules.parsers.pdf.table_hybrid_flow import (
    MAX_META_BYTES,
    MAX_REQUEST_IMAGE_BYTES,
    MAX_WORD_CHARS,
    QUEUE_WAIT_CAP_S,
    RENDER_SCALE,
    _default_renderer,
    _table_timeout,
    batch_jobs,
    build_request,
    max_tables,
    model_timeout_s,
    remaining_inline,
)
from app.modules.parsers.pdf.table_hybrid_sync_client import SyncTableClient
from app.services.docling.table_structure_schema import (
    MAX_MODEL_THREADS,
    TableSpec,
    config_hash,
)
from app.utils.env_config import env_seconds

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

ENV_DOC_SECONDS = "PDF_TABLE_MODEL_DOC_SECONDS"
EMIT_DOC_SECONDS_DEFAULT = 600.0  # model-time bar of one parse call in hybrid mode
PER_STEP_S = 0.06  # the supervisor's own per-step bound
PAGE_FAIL_LATCH = 3  # consecutive pages whose tables all failed before the rest of the document is service_down
CONTENT_EXTRA_MIN = 2  # letters and digits the evidence grid holds and the box's own words do not, at which the box falls back
REPAIR_AT_RULES = True  # on a ruled box a model cell with words on both sides of a horizontal drawn rule is cut along the rule
LOG_TABLE = "table hybrid emit table: "
LOG_DOC = "table hybrid emit document: "

EVIDENCE_REASONS = (
    "prefilter", "budget", "deadline", "service_busy", "service_down", "unsupported", "version_mismatch", "mode_fast", "timeout",
    "error", "malformed", "invalid_input", "collect_error", "emit_error", "cap_hit", "box_no_table", "box_multi", "box_table_extra", "small_grid",
    "word_loss", "tail_conflict",
)
LOAD_REASONS = ("service_busy", "deadline", "timeout", "service_down", "error")


def lane_threads() -> int | None:
    """This worker's lane thread budget (``PIPESHUB_LANE_THREADS``), lent to the model for the tables it waits on."""
    raw = os.environ.get("PIPESHUB_LANE_THREADS", "").strip()
    return min(int(raw), MAX_MODEL_THREADS) if raw.isdigit() and int(raw) > 0 else None

class TableClient(Protocol):
    def post_batch(self, meta: dict[str, Any], files: dict[str, bytes], *, deadline_ms: int, timeout_s: float) -> BatchOutcome: ...


@dataclass(frozen=True)
class ModelCfg:
    """What the worker needs to call the service; plain values only, it crosses the process boundary. No credentials."""

    base_url: str
    record_id: str | None
    request_id: str | None
    cutoff_epoch: float
    max_tables: int
    doc_seconds: float
    timeout_s: float


def emit_doc_seconds() -> float:
    value = env_seconds(ENV_DOC_SECONDS, EMIT_DOC_SECONDS_DEFAULT)
    return value if value > 0 else EMIT_DOC_SECONDS_DEFAULT


def hybrid_active() -> bool:
    from app.modules.parsers.pdf import table_reconcile

    return table_reconcile.table_extraction_mode() == "hybrid"


def make_cfg(record_id: str | None, request_id: str | None, base_url: str, *, record_timeout_s: float, now: float | None = None) -> ModelCfg:
    """The worker's configuration for one parse call. The record cutoff is ``0.8 * record timeout - 600 s`` from now."""
    now = time.time() if now is None else now
    return ModelCfg(
        base_url=base_url, record_id=record_id, request_id=request_id, cutoff_epoch=now + remaining_inline(record_timeout_s, 0.0),
        max_tables=max_tables(), doc_seconds=emit_doc_seconds(), timeout_s=model_timeout_s(),
    )


# --- pure helpers ------------------------------------------------------------------------------------------------


def version_mismatch(outcome: BatchOutcome) -> list[str]:
    """What differs between the service's model block and the measured configuration; empty when equal."""
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
    if model.token_definition != th.TOKEN_DEFINITION:
        moved.append("token_definition")
    if model.config_sha256 != config_hash(th.MODEL_LIBRARY_VERSION, th.MODEL_MODE, th.MODEL_WEIGHTS_SHA256, th.MODEL_REVISION, th.TOKEN_DEFINITION):
        moved.append("config")
    return moved


def page_estimate_s(caps: list[int]) -> float:
    return PER_STEP_S * sum(caps)


def bar_check(
    *, model_used_s: float, est_s: float, doc_s: float, now: float, cutoff_epoch: float, pages_left: int, avg_plain_page_s: float,
) -> str | None:
    """``"deadline"`` when the next page's request would pass the model-time bar or the record-budget cutoff, else None."""
    if model_used_s + est_s > doc_s:
        return "deadline"
    if now + est_s + pages_left * avg_plain_page_s > cutoff_epoch:
        return "deadline"
    return None


def table_extra_content(ev_bbox: tuple[float, ...], box: tuple[float, ...], words: list[dict[str, Any]]) -> bool:
    """The evidence table holds a content word (letter or digit, not made up) outside the model box: a swap would push it
    out of the table."""
    from app.modules.parsers.pdf import opencv_layout_analyzer as ola

    return any(
        ola._word_center_in_bbox(w, ev_bbox) and not ola._word_center_in_bbox(w, box) and tt.has_content(str(w["text"])) for w in words
    )


def evidence_content_excess(grid: list[list[str]] | None, words: list[dict[str, Any]]) -> int:
    """Letters and digits in the evidence table's grid that the box's own words do not hold (multiset difference of
    characters, so differences in word splitting do not count). Text the swap would push out of the table."""
    import collections

    have = collections.Counter(ch for row in (grid or []) for cell in row for ch in str(cell or "") if ch.isalnum())
    box = collections.Counter(ch for w in words for ch in str(w["text"]) if ch.isalnum())
    return sum((have - box).values())


def duplicate_token_boxes(tokens: list[dict[str, Any]]) -> bool:
    keys = {tuple(round(v, 2) for v in t["bbox"]) for t in tokens}
    return len(keys) != len(tokens)


def grid_violations(final: list[Any], swaps: list[Swap], all_words: list[dict[str, Any]]) -> list[str]:
    """After the page pass: every model grid is still there, unchanged, in its own bbox, and none of its words sits in
    another region. Empty list: holds."""
    from app.modules.parsers.pdf import opencv_layout_analyzer as ola

    out: list[str] = []
    words = [w for w in all_words if str(w.get("text", "")).strip() and not th.is_synthetic(w)]
    for s in swaps:
        mine = [r for r in final if r is s.region]
        if len(mine) != 1:
            out.append("region_lost")
            continue
        r = mine[0]
        if r.table_grid != s.grid or tuple(r.bbox) != s.bbox:
            out.append("grid_changed")
            continue
        inside = [w for w in words if ola._word_center_in_bbox(w, s.bbox)]
        for o in final:
            if o is r:
                continue
            if any(ola._word_center_in_bbox(w, o.bbox) for w in inside):
                out.append("word_in_other_region")
                break
    return out


@dataclass
class Swap:
    region: Any
    grid: list[list[str]]
    bbox: tuple[float, float, float, float]
    row: dict[str, Any]


@dataclass
class DocTotals:
    seen: int = 0
    sent: int = 0
    routed_tf: int = 0
    fallback: dict[str, int] = field(default_factory=dict)
    model_used_s: float = 0.0
    worker_wait_s: float = 0.0
    bytes_sent: int = 0
    deadline_hit_page: int | None = None
    cutoff_hit_page: int | None = None
    tail_conflicts: int = 0

    def note(self, reason: str) -> None:
        self.fallback[reason] = self.fallback.get(reason, 0) + 1


class TableEmitter:
    """One per parse call, in the worker. ``swap_page`` per page, ``finalize_page`` after that page's tail, ``finish`` once."""

    def __init__(
        self,
        cfg: ModelCfg,
        pdf_path: str,
        *,
        client: TableClient | None = None,
        renderer: Callable[[int], tuple[Any, float, float]] | None = None,
        clock: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self._pdf_path = pdf_path
        self._client = client if client is not None else SyncTableClient(cfg.base_url)
        self._renderer = renderer
        self._clock = clock
        self._mono = mono
        self.totals = DocTotals()
        self.swaps: list[Swap] = []
        self.rows: list[dict[str, Any]] = []
        self._page_rows: list[dict[str, Any]] = []
        self._n_pages = 0
        self._stop: str | None = None
        self._fail_pages = 0
        self._plain_s = 0.0
        self._plain_pages = 0
        self._page_wait_s = 0.0
        self._warned = False
        self._mismatch_warned = False
        self._evidence_copy: list[Any] | None = None

    # -- lifecycle ------------------------------------------------------------------------------------------------

    def begin(self, n_pages: int) -> None:
        self._n_pages = n_pages

    def note_page(self, wall_s: float) -> None:
        """Wall time of a page's whole pass; the worker's mean time per page without model waits feeds the cutoff."""
        self._plain_s += max(0.0, wall_s - self._page_wait_s)
        self._plain_pages += 1
        self._page_wait_s = 0.0

    def finish(self) -> None:
        close = getattr(self._renderer, "close", None)
        if close is not None:
            with contextlib.suppress(Exception):
                close()
        t = self.totals
        reasons = t.fallback
        doc = {
            "record_id": self.cfg.record_id, "request_id": self.cfg.request_id, "mode": "hybrid", "tables_seen": t.seen, "sent": t.sent,
            "routed_tf": t.routed_tf, "fallback_by_reason": dict(sorted(reasons.items())), "model_used_s": round(t.model_used_s, 3),
            "deadline_hit_page": t.deadline_hit_page, "cutoff_hit_page": t.cutoff_hit_page, "tail_conflicts": t.tail_conflicts,
            "docs_with_load_fallback": any(r in LOAD_REASONS for r in reasons), "bytes_sent": t.bytes_sent,
            "worker_wait_s": round(t.worker_wait_s, 3), "token_definition": th.TOKEN_DEFINITION,
        }
        logger.info("%s%s", LOG_DOC, json.dumps(doc, sort_keys=True, separators=(",", ":")))

    # -- the page -------------------------------------------------------------------------------------------------

    def swap_page(
        self, page_index: int, page: object, page_w: float, page_h: float, decisions: list[Any], evidence_regions: list[Any],
        all_words: list[dict[str, Any]],
    ) -> list[Any]:
        """The page's regions with the model grids swapped in. Never raises: on any exception the evidence regions."""
        self._page_rows = []
        n_swaps = len(self.swaps)
        try:
            return self._swap_page(page_index, page, page_w, page_h, decisions, evidence_regions, all_words)
        except Exception as exc:
            if not self._warned:
                self._warned = True
                logger.warning("table hybrid emit: page %d fell back to the evidence tables (%s)", page_index + 1, type(exc).__name__)
            del self.swaps[n_swaps:]
            for row in self._page_rows:
                if row.get("route") == "tf":
                    self.totals.routed_tf -= 1
                if row.get("route") != "evidence":
                    self._demote_row(row, "emit_error")
                elif row.get("reason") == "tail_conflict":
                    continue
            self._evidence_copy = None
            return evidence_regions

    def _add_row(self, row: dict[str, Any], reason: str | None) -> dict[str, Any]:
        row.setdefault("page", None)
        if reason is not None:
            row["route"], row["reason"] = "evidence", reason
            self.totals.note(reason)
            row["counted"] = True
        self._page_rows.append(row)
        self.rows.append(row)
        self.totals.seen += 1
        return row

    def _demote_row(self, row: dict[str, Any], reason: str, *, count: bool = True) -> None:
        row["route"], row["reason"] = "evidence", reason
        if count:
            self.totals.note(reason)
        row["counted"] = True

    def _swap_page(
        self, page_index: int, page: object, page_w: float, page_h: float, decisions: list[Any], evidence_regions: list[Any],
        all_words: list[dict[str, Any]],
    ) -> list[Any]:
        from app.modules.parsers.pdf import opencv_layout_analyzer as ola

        eligible = sorted((d for d in decisions if d.kind != "not_table"), key=lambda d: (d.box[1], d.box[0]))
        if not eligible:
            return evidence_regions
        words_all = [w for w in all_words if str(w.get("text", "")).strip() and not th.is_synthetic(w)]
        ev_tables = [r for r in evidence_regions if r.type == ola.LayoutRegionType.TABLE]
        candidates: list[dict[str, Any]] = []
        for bi, d in enumerate(eligible):
            box = tuple(float(v) for v in d.box)
            words = [w for w in words_all if ola._word_center_in_bbox(w, box)]
            row = self._add_row({"key": f"p{page_index + 1}:b{bi}", "page": page_index + 1, "kind": d.kind, "ntok": len(words)}, None)
            plan = th.plan_box(words)
            row.update(lines=plan.lines, cols_est=plan.cols_est, cap=plan.cap)
            overlapping = [r for r in ev_tables if ola._boxes_overlap(tuple(r.bbox), box)]
            ids = {i for i, w in enumerate(words_all) if ola._word_center_in_bbox(w, box)}
            row["box_class"] = th.box_class(ids, [{i for i, w in enumerate(words_all) if ola._word_center_in_bbox(w, tuple(r.bbox))} for r in overlapping])
            reason = None
            if plan.reason != "none":
                reason = plan.reason
            elif not overlapping:
                reason = "box_no_table"
            elif len(overlapping) > 1:
                reason = "box_multi"
            elif table_extra_content(tuple(overlapping[0].bbox), box, words_all) or (
                evidence_content_excess(overlapping[0].table_grid, words) >= CONTENT_EXTRA_MIN
            ):
                reason = "box_table_extra"
            if reason is not None:
                self._demote_row(row, reason)
                continue
            row.update(_box=box, _words=words, _ev=overlapping[0], _cap=plan.cap)
            candidates.append(row)
        if not candidates:
            return evidence_regions

        # budget, then the bars, then what an earlier page latched
        allowed: list[dict[str, Any]] = []
        for row in candidates:
            if self.totals.sent + len(allowed) >= self.cfg.max_tables:
                self._demote_row(row, "budget")
            else:
                allowed.append(row)
        if not allowed:
            return evidence_regions
        est = page_estimate_s([r["_cap"] for r in allowed])
        stop = self._stop
        if stop is None:
            avg = self._plain_s / self._plain_pages if self._plain_pages else 0.0
            bar = bar_check(
                model_used_s=self.totals.model_used_s, est_s=est, doc_s=self.cfg.doc_seconds, now=self._clock(),
                cutoff_epoch=self.cfg.cutoff_epoch, pages_left=max(0, self._n_pages - page_index - 1), avg_plain_page_s=avg,
            )
            if bar is not None:
                self._stop = stop = bar
                if self.totals.model_used_s + est > self.cfg.doc_seconds:
                    self.totals.deadline_hit_page = page_index + 1
                else:
                    self.totals.cutoff_hit_page = page_index + 1
        if stop is not None:
            for row in allowed:
                self._demote_row(row, stop)
            return evidence_regions

        # render once, build each request (a failing box is collect_error)
        try:
            img, w, h, dx, dy, pw, ph = self._render(page_index)
            if abs(pw - page_w) > 1.0 or abs(ph - page_h) > 1.0:
                raise ValueError("page size differs between renderers")
        except Exception:
            for row in allowed:
                self._demote_row(row, "collect_error")
            return evidence_regions
        jobs: list[dict[str, Any]] = []
        for row in allowed:
            try:
                b = row["_box"]
                png, sf, tb, _ = build_request(img, (b[0] - dx, b[1] - dy, b[2] - dx, b[3] - dy))
                tokens = [
                    {
                        "id": i, "text": wd["text"][:MAX_WORD_CHARS],
                        "bbox": [(wd["x0"] - dx) * RENDER_SCALE, (wd["top"] - dy) * RENDER_SCALE, (wd["x1"] - dx) * RENDER_SCALE, (wd["bottom"] - dy) * RENDER_SCALE],
                    }
                    for i, wd in enumerate(row["_words"])
                ]
                if duplicate_token_boxes(tokens):
                    self._demote_row(row, "malformed")
                    continue
                spec = {"id": row["key"], "image": "c0", "scale_factor": sf, "page_w": w * RENDER_SCALE, "page_h": h * RENDER_SCALE, "table_bbox": tb, "cap": row["_cap"], "tokens": tokens}
                if (lane := lane_threads()) is not None:
                    spec["threads"] = lane
                try:
                    TableSpec.model_validate(spec)
                except ValueError:
                    self._demote_row(row, "invalid_input")
                    continue
                meta_bytes = len(json.dumps(spec, separators=(",", ":")))
                if meta_bytes > MAX_META_BYTES or len(png) > MAX_REQUEST_IMAGE_BYTES:
                    self._demote_row(row, "invalid_input")
                    continue
                row["_job"] = {"key": row["key"], "cap": row["_cap"], "png": png, "spec": spec, "png_bytes": len(png), "meta_bytes": meta_bytes}
                jobs.append(row["_job"])
            except Exception:
                self._demote_row(row, "collect_error")
        by_key = {r["key"]: r for r in allowed if "_job" in r and r.get("route") != "evidence"}
        strokes = drawn_rules(page) if page is not None else None
        regions = list(evidence_regions)
        page_ok = page_fail = 0
        for batch in batch_jobs(jobs):
            if self._stop is not None:
                for j in batch:
                    self._demote_row(by_key[j["key"]], self._stop)
                continue
            self._send(batch, by_key, page_index)
            for j in batch:
                row = by_key[j["key"]]
                if row.get("_result") is not None:
                    self._finish_table(row, strokes, regions, page_index)
                if row.get("route") == "tf":
                    page_ok += 1
                elif row.get("reason") in ("timeout", "error"):
                    page_fail += 1
        if page_ok:
            self._fail_pages = 0
        elif page_fail:
            self._fail_pages += 1
            if self._fail_pages >= PAGE_FAIL_LATCH and self._stop is None:
                self._stop = "service_down"
        if any(r.get("route") == "tf" for r in allowed):
            self._evidence_copy = copy.deepcopy(evidence_regions)
        return regions

    def _render(self, page_index: int) -> tuple[Any, ...]:
        if self._renderer is None:
            self._renderer = _default_renderer(self._pdf_path)
        out = self._renderer(page_index)
        if len(out) == 3:  # a renderer that knows no crop offset: the page is what it renders
            out = (*out, 0.0, 0.0, out[1], out[2])
        return out

    def _send(self, batch: list[dict[str, Any]], by_key: dict[str, dict[str, Any]], page_index: int) -> None:
        """One request. Sets ``_result`` on each answered row, or demotes it with the reason."""
        now = self._clock()
        bar = min(self.cfg.doc_seconds - self.totals.model_used_s, self.cfg.cutoff_epoch - now)
        if bar <= 0:
            self._stop = "deadline"
            self.totals.deadline_hit_page = self.totals.deadline_hit_page or page_index + 1
            for j in batch:
                self._demote_row(by_key[j["key"]], "deadline")
            return
        timeout = min(bar, sum(_table_timeout(j["cap"]) for j in batch) + 10.0 + QUEUE_WAIT_CAP_S)
        specs = [{**j["spec"], "image": f"c{n}"} for n, j in enumerate(batch)]
        files = {f"c{n}": j["png"] for n, j in enumerate(batch)}
        self.totals.sent += len(batch)
        self.totals.bytes_sent += sum(len(j["png"]) for j in batch)
        t0 = self._mono()
        try:
            outcome = self._client.post_batch({"v": 1, "tables": specs}, files, deadline_ms=int(bar * 1000), timeout_s=timeout)
        except Exception:
            outcome = BatchOutcome("error")
        wall = self._mono() - t0
        self.totals.model_used_s += wall
        self.totals.worker_wait_s += wall
        self._page_wait_s += wall
        if outcome.kind != "ok":
            for j in batch:
                self._demote_row(by_key[j["key"]], outcome.kind)
            if outcome.kind in ("service_busy", "service_down", "unsupported", "mode_fast", "deadline"):
                self._stop = outcome.kind
                if outcome.kind == "deadline":
                    self.totals.deadline_hit_page = self.totals.deadline_hit_page or page_index + 1
            return
        mismatch = version_mismatch(outcome)
        if mismatch and not self._mismatch_warned:
            self._mismatch_warned = True
            logger.warning("table hybrid emit: model identity differs from the measured one (%s)", ",".join(mismatch))
        answers = {t.id: t for t in outcome.response.tables}  # type: ignore[union-attr]
        for j in batch:
            row = by_key[j["key"]]
            res = answers.get(j["key"])
            if mismatch and res is not None and res.status == "ok":
                self._demote_row(row, "version_mismatch")
                self._stop = "version_mismatch"
                continue
            row["_result"] = res if res is not None else "missing"

    def _finish_table(self, row: dict[str, Any], strokes: tuple[list[th.Stroke], list[th.Stroke]] | None, regions: list[Any], page_index: int) -> None:
        from app.modules.parsers.pdf import opencv_layout_analyzer as ola

        res = row.pop("_result")
        if res == "missing":
            self._demote_row(row, "error")
            return
        row.update(steps=res.steps, model_cpu_ms=res.cpu_ms, model_wall_ms=res.wall_ms)
        if res.status == "cap_hit":
            self._demote_row(row, "cap_hit")
            return
        if res.status != "ok":
            reason = {"timeout": "timeout", "service_busy": "service_busy", "deadline": "deadline", "weights": "unsupported"}.get(res.err or "", "error")
            if reason in ("service_busy", "unsupported", "deadline"):
                self._stop = reason
            self._demote_row(row, reason)
            return
        words = row["_words"]
        cells = [c.model_dump() for c in (res.cells or [])]
        check = th.validate_cells(cells, res.n_rows or 0, res.n_cols or 0, set(range(len(words))))
        if not check.ok:
            row["malformed_why"] = check.reason
            self._demote_row(row, "malformed")
            return
        n_rows = res.n_rows or 0
        ids_by = dict(enumerate(words))
        if strokes is not None:
            h_rules = [tuple(x) for x in strokes[0]]
            v_rules = [tuple(x) for x in strokes[1]]
            row["rule_crossings_h"], row["rule_crossings_v"], row["rule_crossings"] = crossings(ids_by, cells, h_rules, v_rules)
            if REPAIR_AT_RULES and row["kind"] == "ruled":
                # a model cell that holds words on both sides of a horizontal drawn rule is cut along the rule
                cells, n_rows, row["rows_split"] = tt.split_rows_at_rules(cells, n_rows, words, h_rules)
                if row["rows_split"] and not th.validate_cells(cells, n_rows, res.n_cols or 0, set(range(len(words)))).ok:
                    self._demote_row(row, "malformed")
                    return
        em = tt.build_grid(cells, n_rows, res.n_cols or 0, words)
        lost = tt.unassigned_content(words, em.emitted_ids, em.dropped_ids)
        row.update(
            tf_shape=list(em.shape), overlaps=em.overlaps, leaders_stripped=em.leaders_stripped, caption_row_dropped=em.caption_row_dropped,
            empty_rows_cols_dropped=em.empty_rows_cols_dropped, word_loss_content=len(lost), dollar_cross_candidates=tt.dollar_cross_candidates(em.grid),
            region_bbox_shrunk=em.bbox_top is not None,
        )
        row["glyph_candidates"] = th.glyph_candidates({i: str(w["text"]) for i, w in ids_by.items()}, cells)
        if em.shape[0] < 2 or em.shape[1] < 2:
            self._demote_row(row, "small_grid")
            return
        if lost:
            self._demote_row(row, "word_loss")
            return
        box = row["_box"]
        bbox = (box[0], max(box[1], em.bbox_top) if em.bbox_top is not None else box[1], box[2], box[3])
        region = ola.LayoutRegion(
            type=ola.LayoutRegionType.TABLE, bbox=bbox, text=ola._grid_to_text(em.grid), table_grid=em.grid, from_evidence=True, from_model_grid=True,
        )
        for i, r in enumerate(regions):
            if r is row["_ev"]:
                regions[i] = region
                break
        else:
            self._demote_row(row, "emit_error")
            return
        row["route"], row["reason"], row["counted"] = "tf", "none", False
        self.totals.routed_tf += 1
        self.swaps.append(Swap(region, copy.deepcopy(em.grid), bbox, row))

    # -- after the page's tail -------------------------------------------------------------------------------------

    def evidence_copy(self) -> list[Any]:
        """A copy of the page's evidence regions taken before the tail ran (the rerun's input)."""
        return self._evidence_copy or []

    def page_swaps(self) -> list[Swap]:
        return [s for s in self.swaps if any(s.row is r for r in self._page_rows)]

    def violations(self, final: list[Any], all_words: list[dict[str, Any]]) -> list[str]:
        return grid_violations(final, self.page_swaps(), all_words)

    def tail_conflict(self) -> None:
        """The page's tail left a model grid changed or a word in two regions: every grid of the page goes back to evidence."""
        self.totals.tail_conflicts += 1
        for s in self.page_swaps():
            self.totals.routed_tf -= 1
            self._demote_row(s.row, "tail_conflict")
        self.swaps = [s for s in self.swaps if not any(s.row is r for r in self._page_rows)]

    def finalize_page(self) -> None:
        for row in self._page_rows:
            line = {k: v for k, v in row.items() if not k.startswith("_") and k != "counted"}
            line.update(record_id=self.cfg.record_id, request_id=self.cfg.request_id, mode="hybrid")
            logger.info("%s%s", LOG_TABLE, json.dumps(line, sort_keys=True, separators=(",", ":")))
            for key in [k for k in row if k.startswith("_")]:
                del row[key]
        self._page_rows = []
