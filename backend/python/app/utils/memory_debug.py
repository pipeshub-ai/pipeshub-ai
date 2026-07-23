"""Memory debugging utilities for all Python microservices.

Mount the router on any FastAPI app to get live memory introspection endpoints.
Endpoints are gated behind ENABLE_MEMORY_DEBUG=1 (default: enabled).

Usage in a *_main.py:
    from app.utils.memory_debug import memory_debug_router
    app.include_router(memory_debug_router)
"""
from __future__ import annotations

import ctypes
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


def _get_smaps_rollup() -> dict[str, float]:
    """Read /proc/self/smaps_rollup for detailed memory breakdown (Linux only).

    Key fields:
    - Rss: actual physical memory used
    - Pss: proportional share (accounts for shared pages)
    - Anonymous: heap + stack (non-file-backed) — this is where C malloc lives
    - Shared_Clean/Shared_Dirty: shared libraries
    - Private_Clean/Private_Dirty: private mappings
    """
    result: dict[str, float] = {}
    try:
        with open("/proc/self/smaps_rollup") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    try:
                        result[key] = int(parts[1]) / 1024  # KB → MB
                    except ValueError:
                        pass
    except (FileNotFoundError, OSError):
        pass
    return result


def _malloc_trim() -> bool:
    """Call glibc malloc_trim(0) to release free heap pages back to the OS.

    Returns True if memory was actually released.
    This only works on Linux with glibc (which Docker containers use).
    """
    try:
        libc = ctypes.CDLL("libc.so.6")
        return libc.malloc_trim(0) != 0
    except (OSError, AttributeError):
        return False


def _get_malloc_stats() -> str | None:
    """Call malloc_stats() and capture its stderr output (Linux/glibc only).

    Returns the text output or None if unavailable.
    """
    try:
        import io
        import contextlib

        libc = ctypes.CDLL("libc.so.6")
        # malloc_stats prints to stderr; capture via pipe
        r_fd, w_fd = os.pipe()
        old_stderr = os.dup(2)
        os.dup2(w_fd, 2)
        try:
            libc.malloc_stats()
        finally:
            os.dup2(old_stderr, 2)
            os.close(old_stderr)
            os.close(w_fd)

        with os.fdopen(r_fd, "r") as f:
            return f.read()
    except (OSError, AttributeError):
        return None


def _get_child_processes_rss() -> list[dict[str, Any]]:
    """Get RSS of child processes (ProcessPoolExecutor workers) via /proc."""
    children: list[dict[str, Any]] = []
    my_pid = os.getpid()
    try:
        proc_dir = "/proc"
        for entry in os.listdir(proc_dir):
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid == my_pid:
                continue
            try:
                stat_path = f"{proc_dir}/{pid}/stat"
                with open(stat_path) as f:
                    stat_parts = f.read().split()
                ppid = int(stat_parts[3])
                if ppid != my_pid:
                    continue
                # Read RSS from /proc/pid/status
                status_path = f"{proc_dir}/{pid}/status"
                rss_kb = 0
                name = stat_parts[1].strip("()")
                with open(status_path) as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            rss_kb = int(line.split()[1])
                            break
                children.append({
                    "pid": pid,
                    "name": name,
                    "rss_mb": round(rss_kb / 1024, 1),
                })
            except (FileNotFoundError, OSError, IndexError, ValueError):
                continue
    except (FileNotFoundError, OSError):
        pass
    return children


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
    smaps = _get_smaps_rollup()
    children = _get_child_processes_rss()

    return JSONResponse(content={
        "rss_mb": round(_get_rss_mb(), 1),
        "proc_memory": {k: round(v, 1) for k, v in proc_mem.items()},
        "smaps_rollup_mb": {k: round(v, 1) for k, v in smaps.items()},
        "child_processes": children,
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


@memory_debug_router.post("/memory/malloc_trim")
async def debug_malloc_trim() -> JSONResponse:
    """Call glibc malloc_trim(0) to return freed heap pages to the OS.

    If RSS drops significantly after this, the issue is heap fragmentation
    (glibc's allocator holding freed pages in its arena). If RSS doesn't
    drop, the memory is still actively referenced by C extensions.
    """
    if not _ENABLED:
        return JSONResponse(status_code=501, content={"error": "Memory debug disabled"})

    gc.collect()
    rss_before = _get_rss_mb()
    proc_before = _get_proc_status_memory()
    trimmed = _malloc_trim()
    rss_after = _get_rss_mb()
    proc_after = _get_proc_status_memory()

    return JSONResponse(content={
        "malloc_trim_returned_memory": trimmed,
        "rss_before_mb": round(rss_before, 1),
        "rss_after_mb": round(rss_after, 1),
        "rss_freed_mb": round(rss_before - rss_after, 1),
        "VmRSS_before_mb": round(proc_before.get("VmRSS", 0), 1),
        "VmRSS_after_mb": round(proc_after.get("VmRSS", 0), 1),
    })


@memory_debug_router.post("/memory/reset")
async def debug_memory_reset() -> JSONResponse:
    """Clear all snapshots."""
    if not _ENABLED:
        return JSONResponse(status_code=501, content={"error": "Memory debug disabled"})

    _snapshots.clear()
    return JSONResponse(content={"message": "All snapshots cleared"})
