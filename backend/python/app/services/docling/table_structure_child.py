"""Child process of the table-structure endpoint: loads the structure model once and answers one table per frame.

Run as ``python -m app.services.docling.table_structure_child``. It imports only the model library, numpy and
OpenCV (inside ``main``), never the service, so it starts without the service's container or telemetry.

Wire format, both directions: ``>II`` (header length, blob length), the JSON header, the blob. Frames go over a
private copy of stdout; the process's own stdout is pointed at stderr so a library that prints cannot corrupt them.
The child exits when stdin closes, and after ``--idle-seconds`` without a request.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import select
import struct
import sys
import time
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

_FRAME = struct.Struct(">II")
MAX_HEADER_BYTES = 8 * 1024 * 1024
MAX_BLOB_BYTES = 16 * 1024 * 1024
MAX_IMAGE_SIDE = 16384
MAX_IMAGE_PIXELS = 64 * 1024 * 1024  # a crop is resized to the model's page height first, so real ones are far smaller
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
WEIGHT_SUFFIXES = (".safetensors", ".pt", ".pth", ".bin")
DEFAULT_FACTORY = "app.services.docling.table_structure_child:build_runner"


def write_frame(out: IO[bytes], header: dict[str, Any], blob: bytes = b"") -> None:
    raw = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    out.write(_FRAME.pack(len(raw), len(blob)) + raw + blob)
    out.flush()


def _read_exact(src: IO[bytes], n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = src.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def read_frame(src: IO[bytes]) -> tuple[dict[str, Any], bytes] | None:
    """The next frame, or ``None`` at end of input. A frame over the size limits is an error."""
    head = _read_exact(src, _FRAME.size)
    if head is None:
        return None
    hlen, blen = _FRAME.unpack(head)
    if hlen > MAX_HEADER_BYTES or blen > MAX_BLOB_BYTES:
        raise ValueError("frame over the size limit")
    raw = _read_exact(src, hlen)
    blob = _read_exact(src, blen) if blen else b""
    if raw is None or blob is None:
        return None
    return json.loads(raw), blob


def weights_sha256(directory: str) -> str:
    """sha256 over the sorted (name, sha256) pairs of the weight files in *directory*."""
    outer = hashlib.sha256()
    for name in sorted(os.listdir(directory)):
        if not name.endswith(WEIGHT_SUFFIXES):
            continue
        h = hashlib.sha256()
        with open(os.path.join(directory, name), "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        outer.update(name.encode() + b"\0" + h.hexdigest().encode() + b"\0")
    return outer.hexdigest()


class StructureModelRunner:
    """The library call, exactly as the evaluation harness makes it: a page dict with no image, the resized
    page's table box and crop, the scale factor, then the row and column compression."""

    def __init__(self, weights_dir: str, mode: str, threads: int) -> None:
        import docling_ibm_models.tableformer.common as common
        from docling_ibm_models.tableformer.data_management.tf_predictor import (
            TFPredictor,
        )

        art = os.path.join(weights_dir, mode)
        cfg = common.read_config(os.path.join(art, "tm_config.json"))
        cfg["model"]["save_dir"] = art
        self._mode = mode
        self._weights_sha256 = weights_sha256(art)
        self._pred = TFPredictor(cfg, "cpu", threads)
        self._model = self._pred._model
        self._end_id = self._model._init_data["word_map"]["word_map_tag"]["<end>"]
        self._last: dict[str, Any] = {}
        original = self._model.predict

        def recording(*call_args: object, **call_kwargs: object) -> tuple:
            out = original(*call_args, **call_kwargs)
            seq = out[0]
            self._last["steps"] = len(seq) if isinstance(seq, list) else None
            self._last["ended"] = isinstance(seq, list) and self._end_id in seq
            return out

        self._model.predict = recording

    def info(self) -> dict[str, str]:
        from importlib.metadata import version

        return {
            "docling_ibm_models": version("docling-ibm-models"),
            "mode": self._mode,
            "weights_sha256": self._weights_sha256,
        }

    def run(self, spec: dict[str, Any], image: np.ndarray) -> dict[str, Any]:
        tokens = [
            {"id": t["id"], "text": t["text"], "bbox": {"l": t["bbox"][0], "t": t["bbox"][1], "r": t["bbox"][2], "b": t["bbox"][3]}}
            for t in spec["tokens"]
        ]
        page = {"width": spec["page_w"], "height": spec["page_h"], "tokens": tokens}
        self._model._max_pred_len = int(spec["cap"])  # the bound the decode loop reads
        self._last.clear()
        cpu0, wall0 = time.process_time(), time.time()
        tfr, det = self._pred.predict(page, list(spec["table_bbox"]), image, float(spec["scale_factor"]), None, False)
        steps, ended = self._last.get("steps"), bool(self._last.get("ended"))
        out: dict[str, Any] = {"steps": steps, "cpu_ms": round((time.process_time() - cpu0) * 1000, 2)}
        if not ended:
            out["status"] = "cap_hit"
            out["wall_ms"] = round((time.time() - wall0) * 1000, 2)
            return out
        ncol, nrow = self._pred._compress_row_col_indexes(tfr)
        by_box = {tuple(round(v, 2) for v in t["bbox"]): t["id"] for t in spec["tokens"]}
        cells = []
        for el in tfr:
            members = [
                by_box.get((round(x["l"], 2), round(x["t"], 2), round(x["r"], 2), round(x["b"], 2)), -1)
                for x in el.get("text_cell_bboxes", [])
            ]
            bb = el.get("bbox")
            cells.append({
                "r0": el["start_row_offset_idx"], "r1": el["end_row_offset_idx"],
                "c0": el["start_col_offset_idx"], "c1": el["end_col_offset_idx"],
                "bbox": [bb["l"], bb["t"], bb["r"], bb["b"]] if isinstance(bb, dict) else None,
                "tokens": members,
                "column_header": _flag(el.get("column_header")),
                "row_header": _flag(el.get("row_header")),
            })
        out.update(status="ok", n_rows=int(det.get("num_rows", nrow)), n_cols=int(det.get("num_cols", ncol)), cells=cells)
        out["wall_ms"] = round((time.time() - wall0) * 1000, 2)
        return out


def _flag(value: object) -> bool | None:
    """A header tag as the model gives it, or ``None`` when it gives none."""
    return value if isinstance(value, bool) else None


def build_runner(weights_dir: str, mode: str, threads: int) -> StructureModelRunner:
    return StructureModelRunner(weights_dir, mode, threads)


def _decode_png(blob: bytes) -> np.ndarray:
    import cv2
    import numpy as np

    # The size is read from the PNG header first: a small file can decode to gigabytes.
    if len(blob) < 24 or not blob.startswith(_PNG_SIGNATURE) or blob[12:16] != b"IHDR":
        raise ValueError("bad image")
    w, h = struct.unpack(">II", blob[16:24])
    if w > MAX_IMAGE_SIDE or h > MAX_IMAGE_SIDE or w * h > MAX_IMAGE_PIXELS:
        raise ValueError("bad image")
    img = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
    if img is None or img.shape[0] > MAX_IMAGE_SIDE or img.shape[1] > MAX_IMAGE_SIDE:
        raise ValueError("bad image")
    return img


def _mark_non_dumpable() -> None:
    from app.utils.process_hardening import mark_process_non_dumpable

    mark_process_non_dumpable()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights-dir", required=True)
    ap.add_argument("--mode", default="accurate")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--idle-seconds", type=float, default=600.0)
    ap.add_argument("--factory", default=DEFAULT_FACTORY)
    args = ap.parse_args(argv)

    # Frames go over a private copy of the pipe; anything the libraries print lands on stderr.
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    src = os.fdopen(0, "rb", buffering=0)
    _mark_non_dumpable()

    mod, _, attr = args.factory.partition(":")
    runner = getattr(importlib.import_module(mod), attr)(args.weights_dir, args.mode, args.threads)
    write_frame(out, {"op": "ready", "info": runner.info()})
    while True:
        ready, _, _ = select.select([src], [], [], args.idle_seconds)
        if not ready:
            return 0  # idle: the supervisor starts a new child on the next request
        frame = read_frame(src)
        if frame is None:
            return 0
        header, blob = frame
        frame_id = header.get("id")
        if header.get("op") != "predict":
            write_frame(out, {"status": "error", "err": "bad_op", "id": frame_id})
            continue
        try:
            result = runner.run(header["spec"], _decode_png(blob))
        except Exception as exc:  # one table fails, the child lives on
            result = {"status": "error", "err": type(exc).__name__}
        write_frame(out, {**result, "id": frame_id})


if __name__ == "__main__":
    sys.exit(main())
