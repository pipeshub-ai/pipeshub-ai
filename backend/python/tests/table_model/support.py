"""Helpers of the real-model tests: the evaluation harness's call, copied, and the product path's call, side by side."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pdfplumber
import pypdfium2 as pdfium

from app.modules.parsers.pdf import table_hybrid as th
from app.modules.parsers.pdf.table_hybrid_flow import build_request
from app.services.docling.table_structure_child import StructureModelRunner

if TYPE_CHECKING:
    from app.services.docling.table_structure import TableSupervisor

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "table_hybrid"
SCALE = 2.0
THREADS = 4


def weights_dir() -> str:
    """The folder with the ``accurate`` weights. Missing weights fail the run in CI and skip it elsewhere."""
    import pytest

    root = os.environ.get("DOCLING_ARTIFACTS_PATH", "").strip()
    path = os.path.join(root, "model_artifacts", "tableformer") if root else ""
    if path and os.path.isfile(os.path.join(path, "accurate", "tm_config.json")):
        return path
    message = "table model weights not found: set DOCLING_ARTIFACTS_PATH to a folder with model_artifacts/tableformer/accurate"
    if os.environ.get("CI") or os.environ.get("REQUIRE_TABLE_MODEL"):
        pytest.fail(message)
    pytest.skip(message)
    return ""


def fixture_pages() -> list[dict[str, Any]]:
    """For each fixture page: its words as the product extracts them, and the table boxes."""
    boxes = json.loads((FIXTURES / "tables.boxes.json").read_text())
    out = []
    with pdfplumber.open(str(FIXTURES / "tables.pdf")) as pdf:
        for i, page in enumerate(pdf.pages):
            words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False, use_text_flow=True, x_tolerance_ratio=0.15)
            out.append({"page": i + 1, "words": words, "boxes": [tuple(b) for b in boxes.get(str(i + 1), [])]})
    return out


def box_words(words: list[dict[str, Any]], box: tuple[float, ...]) -> list[dict[str, Any]]:
    return [w for w in words if w["text"].strip() and box[0] <= (w["x0"] + w["x1"]) / 2 <= box[2] and box[1] <= (w["top"] + w["bottom"]) / 2 <= box[3]]


def signature(responses: list[dict[str, Any]], by_box: dict[tuple[float, float, float, float], int]) -> tuple[tuple[Any, ...], ...]:
    """Cell spans and the sorted token ids of each cell."""
    cells = []
    for el in responses:
        ids = sorted(by_box.get((round(x["l"], 2), round(x["t"], 2), round(x["r"], 2), round(x["b"], 2)), -1) for x in el.get("text_cell_bboxes", []))
        cells.append((el["start_row_offset_idx"], el["end_row_offset_idx"], el["start_col_offset_idx"], el["end_col_offset_idx"], tuple(ids)))
    return tuple(sorted(cells))


class Harness:
    """The evaluation harness's call: ``multi_table_predict`` with the full page image, the step cap on the model."""

    def __init__(self, directory: str) -> None:
        import docling_ibm_models.tableformer.common as common
        from docling_ibm_models.tableformer.data_management.tf_predictor import (
            TFPredictor,
        )

        art = os.path.join(directory, "accurate")
        cfg = common.read_config(os.path.join(art, "tm_config.json"))
        cfg["model"]["save_dir"] = art
        self.pred = TFPredictor(cfg, "cpu", THREADS)
        self.model = self.pred._model
        self._end = self.model._init_data["word_map"]["word_map_tag"]["<end>"]
        self._ended = {"v": False}
        orig = self.model.predict

        def wrapped(imgs, max_steps, k, return_attention=False) -> tuple:
            out = orig(imgs, max_steps, k, return_attention) if return_attention else orig(imgs, max_steps, k)
            self._ended["v"] = isinstance(out[0], list) and self._end in out[0]
            return out

        self.model.predict = wrapped

    def run(self, pdf_path: str, page_no: int, words: list[dict[str, Any]], box: tuple[float, ...], cap: int) -> tuple[Any, ...] | None:
        doc = pdfium.PdfDocument(pdf_path)
        pg = doc[page_no - 1]
        w, h = pg.get_size()
        img = np.asarray(pg.render(scale=SCALE).to_pil().convert("RGB"))
        tokens = [
            {"id": i, "text": x["text"], "bbox": {"l": x["x0"] * SCALE, "t": x["top"] * SCALE, "r": x["x1"] * SCALE, "b": x["bottom"] * SCALE}}
            for i, x in enumerate(words)
        ]
        tbl = [round(box[0]) * SCALE, round(box[1]) * SCALE, round(box[2]) * SCALE, round(box[3]) * SCALE]
        self.model._max_pred_len = cap
        out = self.pred.multi_table_predict({"width": w * SCALE, "height": h * SCALE, "image": img, "tokens": copy.deepcopy(tokens)}, [tbl], do_matching=True)[0]
        if not self._ended["v"]:
            return None
        by_box = {(round(t["bbox"]["l"], 2), round(t["bbox"]["t"], 2), round(t["bbox"]["r"], 2), round(t["bbox"]["b"], 2)): t["id"] for t in tokens}
        return signature(out["tf_responses"], by_box)


def product_request(pdf_path: str, page_no: int, words: list[dict[str, Any]], box: tuple[float, ...], cap: int, *, mutate: str | None = None) -> tuple[dict[str, Any], bytes]:
    """The worker's request for one box: PNG crop of the page resized as a whole, scale factor, page-space tokens.
    ``mutate`` makes the broken variants the negative controls use."""
    doc = pdfium.PdfDocument(pdf_path)
    pg = doc[page_no - 1]
    w, h = pg.get_size()
    img = np.asarray(pg.render(scale=SCALE).to_pil().convert("RGB"))
    png, sf, tb, _ = build_request(img, box)
    tokens = [{"id": i, "bbox": [x["x0"] * SCALE, x["top"] * SCALE, x["x1"] * SCALE, x["bottom"] * SCALE], "text": x["text"]} for i, x in enumerate(words)]
    if mutate == "scale":
        sf = sf * 1.01
    elif mutate == "shift_ids":
        for t in tokens:
            t["id"] += 1
    elif mutate == "crop_only":
        import cv2

        x0, y0, x1, y1 = (int(v) for v in (round(box[0]) * SCALE, round(box[1]) * SCALE, round(box[2]) * SCALE, round(box[3]) * SCALE))
        crop = np.ascontiguousarray(img[y0:y1, x0:x1])
        png = cv2.imencode(".png", crop)[1].tobytes()
        sf = 1.0
        tb = [0.0, 0.0, float(crop.shape[1]), float(crop.shape[0])]
        for t in tokens:
            t["bbox"] = [t["bbox"][0] - x0, t["bbox"][1] - y0, t["bbox"][2] - x0, t["bbox"][3] - y0]
    spec = {"id": "t", "image": "c0", "scale_factor": sf, "page_w": w * SCALE, "page_h": h * SCALE, "table_bbox": tb, "cap": cap, "tokens": tokens}
    return spec, png


_LOOP = asyncio.new_event_loop()  # one loop for every supervisor call: the child's pipes belong to the loop that started it


def run_async(coro):  # noqa: ANN201
    return _LOOP.run_until_complete(coro)


def product_signature(supervisor: TableSupervisor, spec: dict[str, Any], png: bytes) -> tuple[Any, ...] | None:
    """Run one request through the service's supervisor (a real child) and reduce the answer to a signature."""
    result = run_async(supervisor.run_table({k: v for k, v in spec.items() if k != "image"}, png))
    if result.get("status") != "ok":
        return None
    return tuple(sorted((c["r0"], c["r1"], c["c0"], c["c1"], tuple(sorted(c["tokens"]))) for c in result["cells"]))


def plan(words: list[dict[str, Any]]) -> th.BoxPlan:
    return th.plan_box(words)


__all__ = ["run_async", "Harness", "StructureModelRunner", "box_words", "fixture_pages", "plan", "product_request", "product_signature", "weights_dir"]
