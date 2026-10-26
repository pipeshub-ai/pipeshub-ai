# ruff: noqa
"""Ticks with slow and hung deletes at the shipped settings, and the metric outcomes over real HTTP."""
from __future__ import annotations

import asyncio
import time
from collections import Counter
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from app import indexing_main as m
from app.modules.artifacts import retention as retention_module
from app.modules.artifacts.retention import RevisionRetentionSweep
from app.modules.artifacts.store import NodeArtifactStore
from tests.unit.modules.artifacts.test_retention import FakeGraph
from tests.unit.test_indexing_main_revision_retention_probes import (
    DELETE, LIST, ORG, _clean, _graph, _old_ms, _server, _tick,
)
from tests.unit.test_indexing_main_revision_retention_budget import _blob

pytestmark = pytest.mark.timeout(600)


async def test_measure_hung_delete_tick_at_shipped_settings() -> None:
    """Listings answer at once with one old revision; every DELETE hangs. Shipped: budget 20 s,
    delete min(30, left, >=5) s, concurrency 8, 4 pages of 200."""
    deletes: list[float] = []

    async def listing(request: web.Request) -> web.Response:
        vrid = request.query["virtualRecordId"]
        return web.json_response({"revisions": [
            {"documentId": f"{vrid}-a", "virtualRecordId": vrid, "rev": "a", "createdAt": _old_ms(), "deleteFence": True}]})

    async def delete(request: web.Request) -> web.Response:
        deletes.append(time.perf_counter())
        await asyncio.sleep(3600)
        return web.json_response({"purged": 1})

    runner, origin = await _server([("GET", LIST, listing), ("DELETE", DELETE, delete)])
    try:
        took = await _tick(origin, _graph(5000))
    finally:
        await runner.cleanup()
    print(f"MEASURE hung-delete tick {took:.1f}s deletes started {len(deletes)} cursor={m._retention_cursor}")
    assert took < 20 + 5 + 2


async def test_measure_hung_listing_and_delete_mix_at_shipped_settings() -> None:
    """Half the listings hang (10 s bound), the other half list one old revision whose DELETE hangs."""
    async def listing(request: web.Request) -> web.Response:
        vrid = request.query["virtualRecordId"]
        if int(vrid[1:]) % 2:
            await asyncio.sleep(3600)
        return web.json_response({"revisions": [
            {"documentId": f"{vrid}-a", "virtualRecordId": vrid, "rev": "a", "createdAt": _old_ms(), "deleteFence": True}]})

    async def delete(request: web.Request) -> web.Response:
        await asyncio.sleep(3600)
        return web.json_response({"purged": 1})

    runner, origin = await _server([("GET", LIST, listing), ("DELETE", DELETE, delete)])
    try:
        took = await _tick(origin, _graph(5000))
    finally:
        await runner.cleanup()
    print(f"MEASURE hung-mix tick {took:.1f}s cursor={m._retention_cursor}")
    assert took < 20 + 10 + 2


async def test_measure_slow_delete_tick_at_shipped_settings() -> None:
    """Listings fast, 20 old revisions per vrid, each DELETE takes 2 s."""
    deleted = 0

    async def listing(request: web.Request) -> web.Response:
        vrid, old = request.query["virtualRecordId"], _old_ms()
        return web.json_response({"revisions": [
            {"documentId": f"{vrid}-{k}", "virtualRecordId": vrid, "rev": f"r{k}", "createdAt": old, "deleteFence": True} for k in range(20)]})

    async def delete(request: web.Request) -> web.Response:
        nonlocal deleted
        await asyncio.sleep(2.0)
        deleted += 1
        return web.json_response({"purged": 1})

    runner, origin = await _server([("GET", LIST, listing), ("DELETE", DELETE, delete)])
    try:
        took = await _tick(origin, _graph(5000))
    finally:
        await runner.cleanup()
    print(f"MEASURE slow-delete tick {took:.1f}s deleted {deleted} cursor={m._retention_cursor}")
    assert took < 20 + 2 + 2


async def test_metric_outcomes_over_http(monkeypatch) -> None:
    """deleted / gone / changed / failed, each counted once, over the real store and HTTP."""
    counts: Counter = Counter()
    monkeypatch.setattr(retention_module, "record_retention",
                        lambda kind, outcome, n=1: counts.__setitem__((kind, outcome), counts[(kind, outcome)] + n))
    stamps: dict[str, str | None] = {}

    async def listing(request: web.Request) -> web.Response:
        old = _old_ms()
        return web.json_response({"revisions": [
            {"documentId": d, "virtualRecordId": "v00000", "rev": d, "createdAt": old - 5, "updatedAt": old, "deleteFence": True}
            for d in ("d1", "d2", "d3", "d4", "c", "b")]})

    async def delete(request: web.Request) -> web.Response:
        doc = request.match_info["doc"]
        stamps[doc] = request.query.get("ifUnchangedSince")
        if doc == "d1":
            return web.json_response({"purged": 1})
        if doc == "d2":
            return web.json_response({"purged": 0})
        if doc == "d3":
            return web.json_response({"purged": 0, "reason": "changed"}, status=409)
        return web.json_response({"error": {"message": "conflict"}}, status=409)

    runner, origin = await _server([("GET", LIST, listing), ("DELETE", DELETE, delete)])
    try:
        sweep = RevisionRetentionSweep(_graph(1), NodeArtifactStore(_blob(origin)), MagicMock(), page_size=200)
        _, report = await sweep.run_page(None)
    finally:
        await runner.cleanup()
    print(f"METRICS {dict(counts)} report={report} stamps={stamps}")
    assert all(s is not None and s.isdigit() for s in stamps.values())
    assert counts[("revision", "deleted")] == 1
    assert counts[("revision", "gone")] == 1
    assert counts[("revision", "changed")] == 1
    assert counts[("revision", "failed")] == 1
    assert report.revisions_deleted == 1 and report.failed == 1
