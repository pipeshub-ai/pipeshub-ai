"""A small ASGI app that serves ``/health`` the way the indexing service does across cells.

Each uvicorn worker claims a cell index, writes its stats file every second, and answers
``/health`` with the merged files of every cell, using the same ``app.capacity.cells`` code.
Configured by ``CELLS_IT_DIR`` and ``CELLS_IT_WORKERS``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.capacity.cells import claim_cell, read_cells, write_cell

DIRECTORY = Path(os.environ.get("CELLS_IT_DIR", "/nonexistent"))
WORKERS = int(os.environ.get("CELLS_IT_WORKERS", "2"))


async def _publish(claim) -> None:
    while True:
        write_cell(claim, {"pid": os.getpid()}, loop_failure=None, tick_s=1.0)
        await asyncio.sleep(1.0)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    claim = claim_cell(DIRECTORY, WORKERS)
    assert claim is not None
    app.state.claim = claim
    write_cell(claim, {"pid": os.getpid()}, loop_failure=None, tick_s=1.0)
    task = asyncio.get_running_loop().create_task(_publish(claim))
    try:
        yield
    finally:
        task.cancel()
        claim.release()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    merged = read_cells(DIRECTORY, WORKERS)
    cells = [{"cell": c["cell"], "status": c["status"], "pid": c["pid"]} for c in merged.cells]
    return JSONResponse(status_code=200 if merged.healthy and len(cells) == WORKERS else 503, content={"cells": cells})
