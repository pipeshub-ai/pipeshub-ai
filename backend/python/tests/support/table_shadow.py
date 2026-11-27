"""Fakes shared by the table-structure tests: a counting gate, a governor, a stand-in supervisor and spool builders."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import TYPE_CHECKING, Any

import numpy as np

from app.modules.parsers.pdf import table_hybrid_flow as flow
from app.services.docling.table_structure_schema import ModelInfo, config_hash
from app.services.resource_governor.gate import AdmissionGate
from app.services.resource_governor.models import Limits, Pool
from app.services.resource_governor.registry import LimitRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class CountingGate(AdmissionGate):
    """The real gate, recording the cost of each acquire."""

    def __init__(self, limit: int = 1) -> None:
        super().__init__(Pool.HEAVY_PARSE, LimitRegistry(Limits(values=dict.fromkeys(Pool, limit))))
        self.acquires: list[int] = []

    async def acquire(self, cost: int = 1, timeout: float | None = None, **kw) -> bool:
        ok = await super().acquire(cost=cost, timeout=timeout, **kw)
        if ok:
            self.acquires.append(cost)
        return ok


class FakeGovernor:
    def __init__(self, gate: CountingGate) -> None:
        self._gate = gate

    def gate(self, _pool) -> CountingGate:
        return self._gate

    def ceiling(self, _pool) -> int:
        return self._gate.limit


def measured_model_info() -> ModelInfo:
    """A model block equal to the constants the client compares with."""
    from app.modules.parsers.pdf import table_hybrid as th

    return ModelInfo(
        docling_ibm_models=th.MODEL_LIBRARY_VERSION, mode=th.MODEL_MODE, weights_sha256=th.MODEL_WEIGHTS_SHA256,
        model_revision=th.MODEL_REVISION, token_definition=th.TOKEN_DEFINITION,
        config_sha256=config_hash(th.MODEL_LIBRARY_VERSION, th.MODEL_MODE, th.MODEL_WEIGHTS_SHA256, th.MODEL_REVISION, th.TOKEN_DEFINITION),
    )


class FakeSupervisor:
    threads = 1

    def __init__(self, delay: float = 0.0, fail: set[str] | None = None) -> None:
        self.delay = delay
        self._turn = asyncio.Lock()
        self.fail = fail or set()
        self.calls: list[str] = []
        self.model_info = measured_model_info()

    @contextlib.asynccontextmanager
    async def turn(self, wait_s: float | None = None) -> AsyncIterator[None]:
        async with self._turn:
            yield

    async def prepare(self, timeout_s: float) -> str | None:
        return None

    async def run_unlocked(self, spec: dict, png: bytes) -> dict:
        return await self.run_table(spec, png)

    async def run_table(self, spec: dict, png: bytes) -> dict:
        self.calls.append(spec["id"])
        await asyncio.sleep(self.delay)
        if spec["id"] in self.fail:
            return {"status": "error", "err": "RuntimeError"}
        return {"status": "ok", "steps": 3, "cpu_ms": 1.0, "wall_ms": 1.0, "n_rows": 1, "n_cols": 1,
                "cells": [{"r0": 0, "r1": 1, "c0": 0, "c1": 1, "bbox": None, "tokens": [t["id"] for t in spec["tokens"]]}]}


class FakeRegion:
    """The few attributes the collector reads from a layout region."""

    def __init__(self, bbox: tuple[float, float, float, float], grid: list[list[str]] | None = None) -> None:
        self.bbox = bbox
        self.table_grid = grid

        class _T:
            name = "TABLE"

        self.type = _T()


class FakeDecision:
    def __init__(self, box: tuple[float, float, float, float], kind: str = "none") -> None:
        self.box = box
        self.kind = kind


PAGE_W, PAGE_H = 612.0, 792.0


def grid_words(box: tuple[float, float, float, float], rows: int = 5, cols: int = 3) -> list[dict[str, Any]]:
    """A grid of words inside *box*: ``rows`` lines of ``cols`` separated words."""
    x0, top = box[0] + 4, box[1] + 4
    return [
        {"text": f"w{r}{c}", "x0": x0 + c * 60, "x1": x0 + c * 60 + 20, "top": top + r * 18, "bottom": top + r * 18 + 10}
        for r in range(rows)
        for c in range(cols)
    ]


def blank_renderer(page_index: int) -> tuple[Any, float, float]:
    return np.full((int(PAGE_H * 2), int(PAGE_W * 2), 3), 255, np.uint8), PAGE_W, PAGE_H


def spool_tables(spool_dir: str, count: int, *, caps: int | None = None, byte_cap: int | None = None, budget: int | None = None, renderer=blank_renderer) -> flow.TableJobCollector:
    """Collect ``count`` boxes, six to a page, and return the collector."""
    kwargs: dict[str, Any] = {"renderer": renderer}
    if budget is not None:
        kwargs["table_budget"] = budget
    if byte_cap is not None:
        kwargs["byte_cap"] = byte_cap
    col = flow.TableJobCollector(spool_dir, "unused.pdf", **kwargs)
    per_page = 6
    for page in range((count + per_page - 1) // per_page):
        n = min(per_page, count - page * per_page)
        boxes = [(40.0, 20.0 + 110 * i, 300.0, 20.0 + 110 * i + 100) for i in range(n)]
        words = [w for b in boxes for w in grid_words(b)]
        col.collect_page(page, PAGE_W, PAGE_H, [FakeDecision(b) for b in boxes], [], None, words)
    return col


def read_index(spool_dir: str) -> list[dict[str, Any]]:
    with open(os.path.join(spool_dir, flow.INDEX_FILE)) as f:
        return [json.loads(line) for line in f]


TABLE_PREFIX = "table hybrid shadow table: "
DOC_PREFIX = "table hybrid shadow document: "


def parse_logs(caplog) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The per-table and per-document lines the shadow run logged."""
    tables, docs = [], []
    for rec in caplog.records:
        msg = rec.getMessage()
        if msg.startswith(TABLE_PREFIX):
            tables.append(json.loads(msg[len(TABLE_PREFIX):]))
        elif msg.startswith(DOC_PREFIX):
            docs.append(json.loads(msg[len(DOC_PREFIX):]))
    return tables, docs


class ScriptedSupervisor(FakeSupervisor):
    """A stand-in supervisor whose answer per table id is scripted: ``ok`` (default), an ``err``, ``cap_hit``,
    ``malformed`` (a cell outside the grid), ``slow`` (sleeps), or ``raise`` (an exception in the service)."""

    def __init__(self, script: dict[str, str] | None = None, slow_s: float = 0.2, model_info=None) -> None:
        super().__init__()
        self.script = script or {}
        self.slow_s = slow_s
        if model_info is not None:
            self.model_info = model_info

    async def run_table(self, spec: dict, png: bytes) -> dict:
        self.calls.append(spec["id"])
        what = self.script.get(spec["id"], "ok")
        if what == "slow":
            await asyncio.sleep(self.slow_s)
        if what == "raise":
            raise RuntimeError("service exception")
        if what == "cap_hit":
            return {"status": "cap_hit", "steps": spec["cap"], "cpu_ms": 1.0, "wall_ms": 1.0}
        if what.startswith("err:"):
            return {"status": "error", "err": what[4:]}
        ids = [t["id"] for t in spec["tokens"]]
        if what == "malformed":
            return {"status": "ok", "steps": 5, "cpu_ms": 1.0, "wall_ms": 1.0, "n_rows": 2, "n_cols": 2,
                    "cells": [{"r0": 0, "r1": 1, "c0": 0, "c1": 5, "bbox": None, "tokens": ids}]}
        # a clean 2 x 2 grid: the words split over the four cells
        quarter = [ids[i::4] for i in range(4)]
        cells = [{"r0": r, "r1": r + 1, "c0": c, "c1": c + 1, "bbox": None, "tokens": quarter[r * 2 + c]} for r in range(2) for c in range(2)]
        return {"status": "ok", "steps": 9, "cpu_ms": 2.0, "wall_ms": 3.0, "n_rows": 2, "n_cols": 2, "cells": cells}
