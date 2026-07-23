"""Memory debugging utilities for all Python microservices.

Mount the router on any FastAPI app to get live memory introspection endpoints.
Endpoints are gated behind ENABLE_MEMORY_DEBUG=1 (default: enabled).

Usage in a *_main.py:
    from app.utils.memory_debug import memory_debug_router
    app.include_router(memory_debug_router)
"""
from __future__ import annotations

import gc
import os
import sys
import tracemalloc
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

_ENABLED = os.getenv("ENABLE_MEMORY_DEBUG", "1") == "1"

memory_debug_router = APIRouter(prefix="/debug", tags=["debug-memory"])

if _ENABLED:
    tracemalloc.start(25)

_snapshots: list[Any] = []


def _get_rss_mb() -> float:
    """Get current RSS in MB. Works on Linux (Docker) and macOS."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        if sys.platform == "darwin":
            return usage.ru_maxrss / (1024 * 1024)  # macOS: bytes
        return usage.ru_maxrss / 1024  # Linux: KB → MB
    except Exception:
        return -1.0


def _get_proc_status_memory() -> dict[str, float]:
    """Read /proc/self/status for VmRSS and VmSize (Linux only)."""
    result: dict[str, float] = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(("VmRSS:", "VmSize:", "VmPeak:", "VmHWM:")):
                    parts = line.split()
                    result[parts[0].rstrip(":")] = int(parts[1]) / 1024  # KB → MB
    except (FileNotFoundError, OSError):
        pass
    return result


@memory_debug_router.get("/memory")
async def debug_memory() -> JSONResponse:
    """Return memory usage summary: RSS, tracemalloc top allocators, gc stats."""
    if not _ENABLED:
        return JSONResponse(status_code=501, content={"error": "Memory debug disabled"})

    gc.collect()
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics("lineno")

    top_allocations = []
    for stat in top_stats[:30]:
        top_allocations.append({
            "file": str(stat.traceback),
            "size_mb": round(stat.size / (1024 * 1024), 3),
            "count": stat.count,
        })

    traced = tracemalloc.get_traced_memory()
    proc_mem = _get_proc_status_memory()

    return JSONResponse(content={
        "rss_mb": round(_get_rss_mb(), 1),
        "proc_memory": {k: round(v, 1) for k, v in proc_mem.items()},
        "tracemalloc_current_mb": round(traced[0] / (1024 * 1024), 2),
        "tracemalloc_peak_mb": round(traced[1] / (1024 * 1024), 2),
        "top_allocations": top_allocations,
        "gc_stats": gc.get_stats(),
        "gc_garbage_count": len(gc.garbage),
    })


@memory_debug_router.post("/memory/snapshot")
async def debug_memory_snapshot() -> JSONResponse:
    """Take a named snapshot for comparison."""
    if not _ENABLED:
        return JSONResponse(status_code=501, content={"error": "Memory debug disabled"})

    gc.collect()
    snapshot = tracemalloc.take_snapshot()
    _snapshots.append(snapshot)
    return JSONResponse(content={
        "message": f"Snapshot #{len(_snapshots)} taken",
        "total_snapshots": len(_snapshots),
    })


@memory_debug_router.get("/memory/diff")
async def debug_memory_diff() -> JSONResponse:
    """Compare current allocation state vs first snapshot to show growth."""
    if not _ENABLED:
        return JSONResponse(status_code=501, content={"error": "Memory debug disabled"})

    if len(_snapshots) < 1:
        return JSONResponse(
            status_code=400,
            content={"error": "Need at least 1 snapshot. POST /debug/memory/snapshot first."},
        )

    gc.collect()
    current = tracemalloc.take_snapshot()
    baseline = _snapshots[0]
    diff_stats = current.compare_to(baseline, "lineno")

    growth = []
    for stat in diff_stats[:30]:
        if stat.size_diff > 0:
            growth.append({
                "file": str(stat.traceback),
                "size_diff_mb": round(stat.size_diff / (1024 * 1024), 3),
                "size_mb": round(stat.size / (1024 * 1024), 3),
                "count_diff": stat.count_diff,
            })

    return JSONResponse(content={"growth_since_first_snapshot": growth})


@memory_debug_router.post("/gc")
async def debug_force_gc() -> JSONResponse:
    """Force full garbage collection and return stats."""
    if not _ENABLED:
        return JSONResponse(status_code=501, content={"error": "Memory debug disabled"})

    before = tracemalloc.get_traced_memory()
    rss_before = _get_rss_mb()
    collected = gc.collect()
    after = tracemalloc.get_traced_memory()
    rss_after = _get_rss_mb()

    return JSONResponse(content={
        "objects_collected": collected,
        "tracemalloc_before_mb": round(before[0] / (1024 * 1024), 2),
        "tracemalloc_after_mb": round(after[0] / (1024 * 1024), 2),
        "tracemalloc_freed_mb": round((before[0] - after[0]) / (1024 * 1024), 2),
        "rss_before_mb": round(rss_before, 1),
        "rss_after_mb": round(rss_after, 1),
        "gc_garbage_count": len(gc.garbage),
    })


@memory_debug_router.post("/memory/reset")
async def debug_memory_reset() -> JSONResponse:
    """Clear all snapshots."""
    if not _ENABLED:
        return JSONResponse(status_code=501, content={"error": "Memory debug disabled"})

    _snapshots.clear()
    return JSONResponse(content={"message": "All snapshots cleared"})
