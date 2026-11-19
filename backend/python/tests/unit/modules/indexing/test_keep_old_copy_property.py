"""Random interleavings of two runs of one record, a twin, edits and sweeps, on the real begin-swap, commit and holder code.

The vector store and the graph are in-memory. The sweeper deletes the points of any virtual record no record holds, as the orphan
sweep does. The invariant after every step: a searchable record's current vrid has a complete point set (SERVE), and when nothing
runs and the sweep has run, every point set is held by someone (LEAK)."""
import asyncio
import random
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.events.events import EventProcessor
from app.exceptions.indexing_exceptions import IndexingError, SupersededRun
from app.modules.indexing import keep_old_copy
from app.services.graph_db.common.record_visibility import holds_vrid
from tests.support.swap_world import FakeGraph, committer, ctx_for

SEEDS = 1500


def _held(graph: FakeGraph, vrid: str) -> bool:
    return any(holds_vrid(d, vrid) for d in graph.records.values())


class World:
    def __init__(self, rng: random.Random, twin: bool) -> None:
        self.rng = rng
        base = {"orgId": "o", "md5Checksum": "m", "connectorId": "k1"}
        self.graph = FakeGraph({"A": {**base, "virtualRecordId": "X", "indexingStatus": "COMPLETED", "hasIndexedCopy": True}})
        if twin:
            self.graph.records["B"] = {**base, "virtualRecordId": "X", "indexingStatus": "COMPLETED", "hasIndexedCopy": True}
        self.points: dict[str, str] = {"X": "complete"}  # vrid -> "complete" | "partial"
        self.swap, _ = committer(self.graph)
        self.swap._release_unheld = AsyncMock(side_effect=self._release)
        self.swap.o.vector_store.vector_db_service.count_points = AsyncMock(side_effect=lambda c, f: 1)
        self.ep = EventProcessor(MagicMock(), MagicMock(), self.graph, MagicMock())
        self.ep._spawn_abandoned_vrid_cleanup = MagicMock()
        self.ep._rewrite_or_delete_vrid_vectors = AsyncMock(side_effect=lambda v: self._release("o", v))
        self.runs: dict[str, dict] = {}
        self.n = 0

    async def _release(self, org: str, vrid: str) -> None:
        if not _held(self.graph, vrid):  # the holder-checked deleters: a held copy is kept
            self.points.pop(vrid, None)

    async def sweep(self) -> None:
        for vrid in list(self.points):
            await self._release("o", vrid)

    async def step(self, action: str, run: str) -> None:
        g = self.graph
        st = self.runs.get(run)
        if action == "start" and st is None:
            self.n += 1
            g.records["A"]["indexingStatus"] = "IN_PROGRESS"  # the attempt start, as update_record_fields writes it
            doc = {**g.records["A"], "_key": "A"}
            y = f"Y{self.n}"
            st = {"y": y, "x": doc["virtualRecordId"], "doc": doc, "written": False}
            try:
                await self.ep._begin_swap(st["doc"], st["x"], y, "o", forced=False)
            except IndexingError:
                return  # lost the compare-and-set: the event is retried
            self.runs[run] = st
        elif st is None:
            return
        elif action == "write" and not st["written"]:
            self.points[st["y"]] = "partial"
            self.points[st["y"]] = "complete"
            st["written"] = True
        elif action == "commit" and st["written"]:
            try:
                await self.swap.commit(ctx_for("A", st["y"]), keep_old_copy.SwapRun("A", "o", st["x"], st["y"]), {"indexingStatus": "COMPLETED", "hasIndexedCopy": True})
                await self.swap._release_unheld("o", st["x"]) if not _held(g, st["x"]) else None
            except SupersededRun:
                pass
            self.runs.pop(run)
            if not self.runs and g.records["A"]["indexingStatus"] == "COMPLETED":
                pass
        elif action == "fail":
            g.records["A"]["indexingStatus"] = "FAILED" if self.rng.random() < 0.5 else g.records["A"]["indexingStatus"]
            if g.records["A"]["indexingStatus"] == "FAILED":
                await self.swap.release_failed("A", "o")
            self.runs.pop(run)  # the run is over; its points (if any) stay until a sweep

    async def edit(self) -> None:
        """A connector save mid-run stores NOT_STARTED, and a later run requeues the record."""
        doc = self.graph.records["A"]
        # a save mid-run stores NOT_STARTED; another path (a twin's attach, a status repair) can finish the record without touching
        # the pending field, which releases the copy for every lookup
        doc["indexingStatus"] = self.rng.choice(["NOT_STARTED", "QUEUED", "IN_PROGRESS", "COMPLETED"])

    def serve(self) -> None:
        for key, doc in self.graph.records.items():
            if doc.get("hasIndexedCopy") and doc["indexingStatus"] not in ("EMPTY",):
                assert self.points.get(doc["virtualRecordId"]) == "complete", (key, doc, self.points)

    def leak(self) -> None:
        for vrid in self.points:
            assert _held(self.graph, vrid), ("leak", vrid, self.graph.records)


async def _one(seed: int) -> None:
    rng = random.Random(seed)
    w = World(rng, twin=rng.random() < 0.5)
    for _ in range(rng.randint(4, 14)):
        action = rng.choice(["start", "start", "write", "write", "commit", "commit", "fail", "edit", "sweep", "sweep"])
        run = rng.choice(["R1", "R2"])
        if action == "edit":
            await w.edit()
        elif action == "sweep":
            await w.sweep()
        else:
            await w.step(action, run)
        w.serve()
    # nothing is left running: the records end in a settled state and the sweep has run
    w.runs.clear()
    for doc in w.graph.records.values():
        if doc["indexingStatus"] in ("IN_PROGRESS", "QUEUED", "NOT_STARTED"):
            doc["indexingStatus"] = "FAILED"
    await w.sweep()
    w.serve()
    w.leak()


def test_no_interleaving_loses_a_served_copy_or_leaks_one() -> None:
    for seed in range(SEEDS):
        try:
            asyncio.run(_one(seed))
        except AssertionError as exc:
            pytest.fail(f"seed {seed}: {exc}")


def _names_only(record: dict, vrid: str) -> bool:
    return record.get("virtualRecordId") == vrid


def test_negative_control_a_lookup_that_ignores_the_pending_vrid_loses_a_copy(monkeypatch) -> None:
    """The sweep deletes a running copy when the holder lookup cannot see it, and the commit then lands on nothing: the property fails."""
    monkeypatch.setattr("tests.support.swap_world.holds_vrid", _names_only)
    monkeypatch.setitem(globals(), "holds_vrid", _names_only)
    failures = 0
    for seed in range(SEEDS):
        try:
            asyncio.run(_one(seed))
        except AssertionError:
            failures += 1
    assert failures > 0


def test_negative_control_a_commit_that_does_not_match_the_status_loses_a_copy(monkeypatch) -> None:
    """Without the status condition a commit lands on a record that was meanwhile failed and whose copy the sweep took."""
    from app.modules.transformers import swap_commit

    real = swap_commit.COMMITTABLE if hasattr(swap_commit, "COMMITTABLE") else None
    monkeypatch.setattr(keep_old_copy, "COMMITTABLE", ("IN_PROGRESS", "QUEUED", "NOT_STARTED", "COMPLETED", "FAILED", "EMPTY"))
    failures = 0
    for seed in range(SEEDS):
        try:
            asyncio.run(_one(seed))
        except AssertionError:
            failures += 1
    assert real is None
    assert failures > 0
