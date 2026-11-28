"""A stand-in for the table-structure model, loaded by the child process through ``--factory``.

The table id says what it does: ``sleep-<seconds>`` sleeps, ``boom`` raises, ``die`` ends the process, ``print`` writes to stdout, ``cap`` answers no
``<end>``; anything else answers one cell per token with its id.
"""
from __future__ import annotations

import os
import time
from typing import Any


class FakeRunner:
    def __init__(self, weights_dir: str, mode: str, threads: int) -> None:
        self.mode = mode
        self.threads = threads
        if os.environ.get("PIPESHUB_TABLE_FAKE_IGNORE_TERM"):
            import signal

            signal.signal(signal.SIGTERM, signal.SIG_IGN)  # only kill() ends this process
        self.load_marker = os.environ.get("PIPESHUB_TABLE_FAKE_MARKER")
        if self.load_marker:
            with open(self.load_marker, "a") as f:
                f.write(f"{os.getpid()}\n")

    def info(self) -> dict[str, str]:
        return {"docling_ibm_models": "0.0-fake", "mode": self.mode, "weights_sha256": "fake"}

    def run(self, spec: dict[str, Any], image: object) -> dict[str, Any]:
        tid = spec["id"]
        if tid.startswith("sleep-"):
            time.sleep(float(tid.split("-", 1)[1]))
        if tid == "print":
            print("a library printed on stdout", flush=True)  # must not corrupt the frames
        if tid == "boom":
            raise RuntimeError("boom")
        if tid == "die":
            os._exit(3)
        if tid == "cap":
            return {"status": "cap_hit", "steps": spec["cap"], "cpu_ms": 1.0, "wall_ms": 1.0}
        cells = [{"r0": i, "r1": i + 1, "c0": 0, "c1": 1, "bbox": None, "tokens": [t["id"]]} for i, t in enumerate(spec["tokens"])]
        return {"status": "ok", "steps": 5, "cpu_ms": 1.0, "wall_ms": 1.0, "n_rows": max(1, len(cells)), "n_cols": 1, "cells": cells,
                "threads": self.threads, "pid": os.getpid(), "shape": list(image.shape)}  # type: ignore[attr-defined]


def build(weights_dir: str, mode: str, threads: int) -> FakeRunner:
    return FakeRunner(weights_dir, mode, threads)
