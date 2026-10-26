"""Learned table and picture boxes for the pdfplumber PDF path.

Docling's heron layout model (RT-DETR, Apache-2.0, ONNX export) proposes table
and picture boxes; the caller fills table cells from the PDF text layer. A small
PP-DocLayout-S model (bundled in models/) gates it so heron only runs on pages
that may hold a table or a picture. Any failure to load or run a model disables
detection, leaving the rule-based finders alone.
"""

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import cv2
import numpy as np

if TYPE_CHECKING:
    from onnxruntime import InferenceSession

logger = logging.getLogger(__name__)

_HERON_REPO = "docling-project/docling-layout-heron-onnx"
# The label ids below belong to this export; the Dockerfile downloads the same revision.
_HERON_REVISION = "40bde044036bb181c130ddf6c51792187268748f"
_HERON_SIZE = 640
_HERON_TABLE_LABEL = 8
_HERON_PICTURE_LABEL = 6
_HERON_CAPTION_LABEL = 0
_TABLE_MIN_SCORE = 0.5
_PICTURE_MIN_SCORE = 0.5

# PP-DocLayout-S ONNX: the bundled file unless PDF_TABLE_GATE_MODEL names another;
# "off" sends every page to heron.
_GATE_MODEL_ENV = "PDF_TABLE_GATE_MODEL"
_GATE_MODEL_BUNDLED = Path(__file__).with_name("models") / "pp_doclayout_s.onnx"
_GATE_SIZE = 480
_GATE_TABLE_LABEL = 8
_GATE_PICTURE_LABELS = (1, 18)  # image, chart
# A page goes to heron when the gate scores a table, or an image or chart, at or
# above these. Lower sends more pages (slower, a little more recall).
_GATE_MIN_SCORE = float(os.environ.get("PDF_TABLE_GATE_TABLE_SCORE", "0.2"))
_GATE_PICTURE_MIN_SCORE = float(os.environ.get("PDF_TABLE_GATE_PICTURE_SCORE", "0.3"))
_GATE_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_GATE_STD = np.array([0.229, 0.224, 0.225], np.float32)

# Docling runs this model with 4 threads too (LayoutPredictor's default).
_THREADS = int(os.environ.get("PDF_TABLE_MODEL_THREADS", "4"))

_lock = threading.Lock()
_sessions: dict = {}


def _session(name: str) -> Optional["InferenceSession"]:
    """Load once per process; None (and a single warning) if it can't load."""
    with _lock:
        if name not in _sessions:
            try:
                import onnxruntime as ort

                if name == "heron":
                    from huggingface_hub import hf_hub_download

                    path = hf_hub_download(_HERON_REPO, "model.onnx", revision=_HERON_REVISION)
                else:
                    path = os.environ.get(_GATE_MODEL_ENV) or str(_GATE_MODEL_BUNDLED)
                    if path == "off":
                        path = None
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = _THREADS
                opts.inter_op_num_threads = 1
                _sessions[name] = ort.InferenceSession(
                    path, opts, providers=["CPUExecutionProvider"]
                ) if path else None
            except Exception as e:
                logger.warning("Table model %s unavailable, using rule-based tables only: %s", name, e)
                _sessions[name] = None
        return _sessions[name]


def _gate_passes(sess: "InferenceSession", img_rgb: np.ndarray) -> bool:
    h, w = img_rgb.shape[:2]
    x = cv2.resize(img_rgb, (_GATE_SIZE, _GATE_SIZE), interpolation=cv2.INTER_CUBIC)
    x = ((x.astype(np.float32) / 255.0 - _GATE_MEAN) / _GATE_STD).transpose(2, 0, 1)[None]
    out = sess.run(None, {
        "image": x,
        "scale_factor": np.array([[_GATE_SIZE / h, _GATE_SIZE / w]], np.float32),
    })[0]
    return any(
        (int(c) == _GATE_TABLE_LABEL and s >= _GATE_MIN_SCORE)
        or (int(c) in _GATE_PICTURE_LABELS and s >= _GATE_PICTURE_MIN_SCORE)
        for c, s, *_ in out
    )


def detect_layout_boxes(
    img_rgb: np.ndarray,
) -> Optional[dict[str, list[tuple[tuple[float, float, float, float], float]]]]:
    """{"table": [...], "picture": [...], "caption": [...]}: boxes as fractions of the image
    (x0, y0, x1, y1) with scores, or None when no model is available. Fractions,
    because a raster's nominal scale can disagree with pdfplumber's page size
    (e.g. pages with a non-default UserUnit)."""
    heron = None if os.environ.get("PDF_TABLE_MODEL", "heron") == "off" else _session("heron")
    if heron is None:
        return None
    try:
        gate = _session("gate")
        if gate is not None and not _gate_passes(gate, img_rgb):
            return {"table": [], "picture": [], "caption": []}
        h, w = img_rgb.shape[:2]
        x = cv2.resize(img_rgb, (_HERON_SIZE, _HERON_SIZE), interpolation=cv2.INTER_CUBIC)
        labels, boxes, scores = heron.run(None, {
            "images": np.ascontiguousarray(x.transpose(2, 0, 1)[None]),
            "orig_target_sizes": np.array([[w, h]], np.int64),
        })
    except Exception as e:
        logger.warning("Table model failed on a page, using rule-based tables: %s", e)
        return None

    def pick(label: int, min_score: float) -> list[tuple[tuple[float, float, float, float], float]]:
        return [
            ((float(b[0]) / w, float(b[1]) / h, float(b[2]) / w, float(b[3]) / h), float(s))
            for lab, b, s in zip(labels[0], boxes[0], scores[0])
            if int(lab) == label and s >= min_score
        ]
    return {
        "table": pick(_HERON_TABLE_LABEL, _TABLE_MIN_SCORE),
        "picture": pick(_HERON_PICTURE_LABEL, _PICTURE_MIN_SCORE),
        "caption": pick(_HERON_CAPTION_LABEL, _PICTURE_MIN_SCORE),
    }
