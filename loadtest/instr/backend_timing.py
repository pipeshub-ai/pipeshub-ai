"""Caller-side backend latency instrumentation — throwaway load-test tooling.

Measures, from inside the query-service process, how long each backend call
takes and how many are made per second:

  BACKENDAGG pid=<pid> kind=neo4j     n=..  qps=..  p50=..  p95=..  max=..
  BACKENDAGG pid=<pid> kind=node_api  ...        (query svc -> Node storage API)
  BACKENDAGG pid=<pid> kind=blob_url  ...        (signed-URL downloads)
  BACKENDAGG pid=<pid> kind=http_other ...       (any other aiohttp traffic)
  LOOPLAG    pid=<pid> n=.. ms_mean=.. ms_p95=.. ms_max=..

Notes on semantics:
  - neo4j ms  = await of AsyncSession.run (round trip until result ready).
  - aiohttp ms = until response HEADERS arrive (service+queue latency);
    body download time is deliberately excluded.
  - LOOPLAG = overshoot of a 100 ms asyncio timer on this worker's event
    loop; the direct measure of how long *ready* work waits for the loop.

Wired by importing from app/utils/runtime_threads.py (same pattern as
llm_timing). Patches apply per-process, so every uvicorn worker reports
independently under its own pid.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

logger = logging.getLogger("backend_timing")

_WINDOW = 5.0
_PID = os.getpid()
_stats: dict[str, list[float]] = {}
_probed_loops: set[int] = set()


def _record(kind: str, ms: float) -> None:
    _stats.setdefault(kind, []).append(ms)
    _ensure_probes()


def _ensure_probes() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if id(loop) in _probed_loops:
        return
    _probed_loops.add(id(loop))
    loop.create_task(_lag_probe())
    loop.create_task(_flusher())


async def _lag_probe() -> None:
    lags: list[float] = []
    last = time.perf_counter()
    while True:
        t0 = time.perf_counter()
        await asyncio.sleep(0.1)
        lags.append(max((time.perf_counter() - t0 - 0.1) * 1000.0, 0.0))
        now = time.perf_counter()
        if now - last >= _WINDOW and lags:
            lags.sort()
            n = len(lags)
            logger.info(
                "LOOPLAG pid=%d n=%d ms_mean=%.1f ms_p95=%.1f ms_max=%.1f",
                _PID, n, sum(lags) / n, lags[max(int(n * 0.95) - 1, 0)], lags[-1],
            )
            lags = []
            last = now


async def _flusher() -> None:
    while True:
        await asyncio.sleep(_WINDOW)
        for kind in list(_stats.keys()):
            arr = _stats.get(kind) or []
            if not arr:
                continue
            _stats[kind] = []
            arr.sort()
            n = len(arr)
            logger.info(
                "BACKENDAGG pid=%d kind=%s n=%d qps=%.1f p50=%.0f p95=%.0f max=%.0f",
                _PID, kind, n, n / _WINDOW,
                arr[n // 2], arr[max(int(n * 0.95) - 1, 0)], arr[-1],
            )


def _classify_url(url: object) -> str:
    s = str(url)
    if ":3000" in s or "localhost" in s or "127.0.0.1" in s or "nodejs" in s:
        return "node_api"
    if "blob.core" in s or "amazonaws" in s or "sig=" in s or "X-Amz-" in s:
        return "blob_url"
    return "http_other"


def _patch_neo4j() -> None:
    from neo4j import AsyncSession
    orig = AsyncSession.run

    async def timed_run(self, *a, **k):  # noqa: ANN001
        t0 = time.perf_counter()
        try:
            return await orig(self, *a, **k)
        finally:
            _record("neo4j", (time.perf_counter() - t0) * 1000.0)

    AsyncSession.run = timed_run
    logger.info("backend_timing: neo4j AsyncSession.run patched pid=%d", _PID)


def _patch_aiohttp() -> None:
    import aiohttp
    orig = aiohttp.ClientSession._request

    async def timed_request(self, method, url, **k):  # noqa: ANN001
        t0 = time.perf_counter()
        try:
            return await orig(self, method, url, **k)
        finally:
            _record(_classify_url(url), (time.perf_counter() - t0) * 1000.0)

    aiohttp.ClientSession._request = timed_request
    logger.info("backend_timing: aiohttp ClientSession._request patched pid=%d", _PID)


for _fn in (_patch_neo4j, _patch_aiohttp):
    try:
        _fn()
    except Exception:  # instrumentation must never break the app
        logger.exception("backend_timing: patch failed: %s", _fn.__name__)
