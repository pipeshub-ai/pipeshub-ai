# ruff: noqa
"""Properties the retention tick must keep against a real HTTP storage stand-in: it stays inside its
time budget, a delete is fenced on the listed stamp, and the cursor only moves forward."""

from __future__ import annotations

import asyncio
import os
import random
import time
import types
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from app import indexing_main as m
from app.modules.artifacts import retention as retention_module
from app.modules.artifacts import store as store_module
from app.modules.artifacts.retention import RevisionRetentionSweep
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.transformers import blob_storage as blob_module
from app.modules.transformers.blob_storage import close_shared_session
from tests.unit.modules.artifacts.test_retention import FakeGraph, FakeStore
from tests.unit.test_indexing_main_revision_retention_budget import _blob

pytestmark = pytest.mark.timeout(300)
ORG = "64d000000000000000000e01"
SEED = int(os.environ.get("RETENTION_TEST_SEED", str(time.time_ns() % 2**31)))


class TtlLeases:
    """Leases that expire, as Redis ones do."""

    def __init__(self) -> None:
        self.held: dict[str, tuple[str, float]] = {}

    def _live(self, pool: str) -> tuple[str, float] | None:
        entry = self.held.get(pool)
        return entry if entry and entry[1] > time.monotonic() else None

    async def try_acquire(self, pool: str, owner: str, limit: int, lease_seconds: float) -> bool:
        live = self._live(pool)
        if live and live[0] != owner:
            return False
        self.held[pool] = (owner, time.monotonic() + lease_seconds)
        return True

    async def renew_many(self, leases: list[tuple[str, str]], lease_seconds: float) -> dict[tuple[str, str], bool]:
        out = {}
        for pool, owner in leases:
            live = self._live(pool)
            ok = bool(live and live[0] == owner)
            if ok:
                self.held[pool] = (owner, time.monotonic() + lease_seconds)
            out[(pool, owner)] = ok
        return out

    async def release(self, pool: str, owner: str) -> None:
        live = self.held.get(pool)
        if live and live[0] == owner:
            del self.held[pool]


async def _server(routes: list[tuple[str, str, Any]]) -> tuple[web.AppRunner, str]:
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    runner = web.AppRunner(app, shutdown_timeout=0.5)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner, f"http://127.0.0.1:{port}"


@pytest.fixture(autouse=True)
async def _clean():
    m._retention_cursor = None
    store_module.ARTIFACT_ROUTES.reset()
    await close_shared_session()
    yield
    await close_shared_session()
    m._retention_cursor = None


LIST = "/api/v1/document/internal/artifacts"
DELETE = "/api/v1/document/internal/artifacts/{doc}"


def _old_ms() -> int:
    return int(time.time() * 1000) - 48 * 3600 * 1000


# --- A: lease expiry mid-delete ------------------------------------------------------------

async def test_a_revision_refiled_after_the_sweeps_lease_lapsed_is_not_deleted(monkeypatch) -> None:
    """Shipped ratio: lease term 120 s == sock_read 120 s, scaled to 1 s. The first DELETE attempt
    outlasts the term; a re-index takes the lease, moves the record back onto rev a and re-files it;
    the sweep's DELETE (one attempt, carrying the listed stamp; storage runs on after the client gave
    up) must not remove it."""
    monkeypatch.setenv("INDEXING_CONCURRENCY_LEASE_SECONDS", "1.0")
    monkeypatch.setattr(blob_module, "BLOB_HTTP_SOCK_READ_TIMEOUT_SECONDS", 1.0)
    files: dict[str, set[str]] = {"a": {"manifest.json", "blocks/part-0000.jsonl"}}
    attempts: list[float] = []
    stamps: dict[str, int] = {}  # storage's write stamp (updatedAt) by revision, moved by a re-file
    t0 = time.monotonic()

    async def listing(request: web.Request) -> web.Response:
        old = _old_ms()
        return web.json_response({"revisions": [
            {"documentId": f"doc-{r}", "virtualRecordId": "v1", "rev": r, "createdAt": old, "deleteFence": True} for r in ("a", "b", "c")
        ]})

    async def delete(request: web.Request) -> web.Response:
        attempts.append(time.monotonic() - t0)
        if len(attempts) == 1:
            await asyncio.sleep(1.6)  # a slow tree delete; the single client attempt times out at 1.0 s
        rev = request.match_info["doc"].removeprefix("doc-")
        since = request.query.get("ifUnchangedSince")
        if since is not None and stamps.get(rev, 0) > int(since):
            # Storage's fence: the revision was written again since the sweep listed it.
            return web.json_response({"purged": 0, "reason": "changed"}, status=409)
        purged = len(files.get(rev, set()))
        files[rev] = set()
        return web.json_response({"purged": 1 if purged else 0})

    runner, origin = await _server([("GET", LIST, listing), ("DELETE", DELETE, delete)])
    graph = FakeGraph()
    graph.map("v1", current="c", previous="b", org=ORG)
    graph.records.append(("v1", "c", False))
    leases = TtlLeases()
    sweep = RevisionRetentionSweep(graph, NodeArtifactStore(_blob(origin), MagicMock()), MagicMock(), leases=leases)
    refiled_at: list[float] = []

    async def reindex() -> None:
        # A re-index of record r0 whose source went back to rev a's bytes: lease, contentRev, re-file.
        while not attempts:
            await asyncio.sleep(0.01)
        while not await leases.try_acquire("record:r0", "reindex", 1, 10.0):
            await asyncio.sleep(0.02)
        graph.records[0] = ("v1", "a", False)
        files["a"] = {"manifest.json", "blocks/part-0000.jsonl"}
        stamps["a"] = int(time.time() * 1000)
        refiled_at.append(time.monotonic() - t0)

    try:
        writer = asyncio.create_task(reindex())
        await sweep.run_page(None)
        await asyncio.sleep(2.0)  # the first attempt's handler finishes server-side
        writer.cancel()
    finally:
        await runner.cleanup()
    print(f"delete attempts at {attempts}, re-filed at {refiled_at}, rev a files now {files['a']}")
    assert refiled_at, "the re-index never got the lease (the sweep's lease did not lapse)"
    assert files["a"], (
        f"rev a was re-filed under a new lease at {refiled_at[0]:.2f}s and then deleted by the sweep's "
        f"delete attempts at {attempts}"
    )


# --- B/C: deletes are not bounded by the tick budget -----------------------------------------

def _graph(n: int) -> FakeGraph:
    graph = FakeGraph()
    for i in range(n):
        graph.map(f"v{i:05d}", current="c", previous="b", org=ORG)
        graph.records.append((f"v{i:05d}", "c", False))
    return graph


async def _tick(origin: str, graph: FakeGraph) -> float:
    container = MagicMock()
    container.blob_storage = MagicMock(return_value=_blob(origin))
    started = time.perf_counter()
    await m._sweep_retired_revisions(app_container=container, graph_provider=graph, logger=MagicMock(),
                                     lease_held=lambda: True)
    return time.perf_counter() - started


async def test_slow_deletes_stay_inside_the_tick_budget(monkeypatch) -> None:
    """Listing is fast; each vrid has 20 old revisions and a tree delete takes 0.5 s. Budget 2 s."""
    monkeypatch.setenv("REVISION_RETENTION_TICK_BUDGET_SECONDS", "2")

    async def listing(request: web.Request) -> web.Response:
        vrid, old = request.query["virtualRecordId"], _old_ms()
        return web.json_response({"revisions": [
            {"documentId": f"{vrid}-{k}", "virtualRecordId": vrid, "rev": f"r{k}", "createdAt": old, "deleteFence": True} for k in range(20)
        ]})

    async def delete(request: web.Request) -> web.Response:
        await asyncio.sleep(0.5)
        return web.json_response({"purged": 3})

    runner, origin = await _server([("GET", LIST, listing), ("DELETE", DELETE, delete)])
    try:
        took = await _tick(origin, _graph(200))
    finally:
        await runner.cleanup()
    print(f"tick with slow deletes took {took:.1f}s against a 2 s budget")
    assert took < 2 + 2, f"tick took {took:.1f}s against a 2 s budget"


async def test_a_hung_delete_is_bounded_like_a_hung_listing(monkeypatch) -> None:
    """Listing fast, DELETE never answers. sock_read scaled 120 s -> 0.5 s; list timeout is 10 s
    shipped, so the scaled equivalent of the listing bound is min(10, 0.5) = 0.5 s per vrid."""
    monkeypatch.setenv("REVISION_RETENTION_TICK_BUDGET_SECONDS", "20")
    monkeypatch.setattr(blob_module, "BLOB_HTTP_SOCK_READ_TIMEOUT_SECONDS", 0.5)

    async def listing(request: web.Request) -> web.Response:
        vrid = request.query["virtualRecordId"]
        return web.json_response({"revisions": [
            {"documentId": f"{vrid}-a", "virtualRecordId": vrid, "rev": "a", "createdAt": _old_ms(), "deleteFence": True}
        ]})

    async def delete(request: web.Request) -> web.Response:
        await asyncio.sleep(3600)
        return web.json_response({"purged": 1})

    runner, origin = await _server([("GET", LIST, listing), ("DELETE", DELETE, delete)])
    try:
        took = await _tick(origin, _graph(200))
    finally:
        await runner.cleanup()
    print(f"tick with hung deletes took {took:.1f}s = {took / 0.5:.1f} x sock_read")
    # A hung listing ends the page after ~1 listing timeout; a hung delete should not cost 3 attempts.
    assert took < 2 * 0.5 + 0.5, f"tick took {took:.1f}s ({took / 0.5:.1f} x the read timeout)"


# --- D: the resume key never skips a vrid, and the walk always advances ---------------------

class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.mark.parametrize("world", range(150))
async def test_every_vrid_is_tried_once_per_walk(monkeypatch, world: int) -> None:
    rng = random.Random(SEED * 1000 + world)
    clock = _Clock()
    fake_time = types.SimpleNamespace(monotonic=clock.monotonic)
    monkeypatch.setattr(retention_module, "time", fake_time)
    monkeypatch.setattr(m, "time", fake_time)
    monkeypatch.setattr(m, "RETENTION_MAX_PAGES_PER_TICK", rng.choice([1, 2, 4]))
    budget = rng.choice([1.0, 3.0, 20.0])
    monkeypatch.setenv("REVISION_RETENTION_TICK_BUDGET_SECONDS", str(budget))

    class _Pacer:
        def __init__(self, **_: Any) -> None: ...
        async def pause(self) -> None: ...

    monkeypatch.setattr(m, "LoopShare", _Pacer)
    n = rng.randint(1, 450)
    page = rng.choice([7, 50, 200])
    graph = FakeGraph()
    for i in range(n):
        graph.map(f"v{i:05d}", current="c", previous="b")
        graph.records.append((f"v{i:05d}", "c", rng.random() < 0.05))
    trashed = {v for v, _, t in graph.records if t}
    fail_p = rng.choice([0.0, 0.2, 0.6, 1.0])
    tried: list[str] = []

    class _Store(FakeStore):
        async def list_revisions(self, org_id: str, vrid: str, *, timeout_seconds: float | None = None):
            tried.append(vrid)
            clock.now += rng.choice([0.0, 0.1, 0.5, 2.0])
            await asyncio.sleep(0)
            if rng.random() < fail_p:
                raise RuntimeError("storage down")
            return []

    store = _Store()
    monkeypatch.setattr(m, "NodeArtifactStore", lambda *a, **k: store)
    orig_init = RevisionRetentionSweep.__init__

    def init(self, *a, **k):  # page size and concurrency vary per world
        k["page_size"] = page
        k["concurrency"] = rng.choice([1, 3, 8])
        orig_init(self, *a, **k)

    monkeypatch.setattr(RevisionRetentionSweep, "__init__", init)
    container = MagicMock()
    expected = {f"v{i:05d}" for i in range(n)} - trashed
    walks, ticks, seen = 0, 0, set()
    while walks < 2:
        ticks += 1
        assert ticks < 20 * (n // max(1, page) + 2) + 4 * n, f"seed {SEED} world {world}: walk stalled"
        before = len(tried)
        await m._sweep_retired_revisions(app_container=container, graph_provider=graph, logger=MagicMock(),
                                         lease_held=lambda: True)
        seen.update(tried[before:])
        clock.now += 60
        if m._retention_cursor is None:
            missing = expected - seen
            assert not missing, f"seed {SEED} world {world}: walk {walks} never tried {sorted(missing)[:5]}"
            walks += 1
            seen = set()


# --- E: page reads longer than the budget ----------------------------------------------------

async def test_the_walk_advances_when_page_reads_take_longer_than_the_budget(monkeypatch) -> None:
    clock = _Clock()
    fake_time = types.SimpleNamespace(monotonic=clock.monotonic)
    monkeypatch.setattr(retention_module, "time", fake_time)
    monkeypatch.setattr(m, "time", fake_time)
    graph = FakeGraph()
    for i in range(500):
        graph.map(f"v{i:05d}", current="c", previous="b")
        graph.records.append((f"v{i:05d}", "c", False))
    slow = graph.stage_states_unheld_revisions

    async def slow_read(*a: Any, **k: Any):
        clock.now += 25.0  # the page's stage-state read takes longer than the 20 s budget
        return await slow(*a, **k)

    graph.stage_states_unheld_revisions = slow_read  # type: ignore[method-assign]
    store = FakeStore()
    monkeypatch.setattr(m, "NodeArtifactStore", lambda *a, **k: store)
    keys = []
    for _ in range(5):
        await m._sweep_retired_revisions(app_container=MagicMock(), graph_provider=graph, logger=MagicMock(),
                                         lease_held=lambda: True)
        keys.append(m._retention_cursor)
        clock.now += 60
    print(f"cursor after 5 ticks: {keys}, listings {store.list_calls}")
    assert store.list_calls > 0 and len(set(keys)) > 1, f"walk never advanced: cursor {keys}"


# --- F: measurements at shipped constants ----------------------------------------------------

async def test_measure_slow_storage_tick_at_shipped_budget() -> None:
    """Every listing answers in 1 s; budget 20 s, list timeout 10 s, 4 pages of 200, as shipped."""
    async def listing(request: web.Request) -> web.Response:
        await asyncio.sleep(1.0)
        return web.json_response({"revisions": []})

    runner, origin = await _server([("GET", LIST, listing)])
    graph = _graph(5000)
    try:
        took = await _tick(origin, graph)
    finally:
        await runner.cleanup()
    print(f"MEASURE slow-storage tick {took:.1f}s cursor={m._retention_cursor}")
    assert took < 20 + 10 + 2


async def test_measure_hung_storage_tick_at_shipped_timeouts() -> None:
    """Listings never answer; shipped 10 s one-attempt listing."""
    async def listing(request: web.Request) -> web.Response:
        await asyncio.sleep(3600)
        return web.json_response({"revisions": []})

    runner, origin = await _server([("GET", LIST, listing)])
    try:
        took = await _tick(origin, _graph(5000))
    finally:
        await runner.cleanup()
    print(f"MEASURE hung-storage tick {took:.1f}s cursor={m._retention_cursor}")
    assert took < 10 + 2
