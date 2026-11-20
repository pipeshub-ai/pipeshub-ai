"""Random interleavings of two runs of one record, a twin, edits and sweeps, on the real begin-swap, commit and holder code.

The vector store and the graph are in-memory. The sweeper deletes the points of any virtual record no record holds, as the orphan
sweep does. The invariant after every step: a searchable record's current vrid has a complete point set (SERVE), and when nothing
runs and the sweep has run, every point set is held by someone (LEAK)."""
import asyncio
import os
import random
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.events.events import EventProcessor
from app.exceptions.indexing_exceptions import IndexingError, SupersededRun
from app.modules.indexing import keep_old_copy
from app.services.graph_db.common.record_visibility import holds_vrid
from tests.support.swap_world import FakeGraph, committer, ctx_for

SEEDS = 6000
CONTROL_SEEDS = 1500
SEED_BASE = int(os.environ.get("KEEP_OLD_COPY_SEED_BASE", "0"))
#: seeds that failed at fresh bases before the commit was confirmed after its write: kept as regressions
REGRESSION_SEEDS = (890100023, 891600104)
_RETENTION_RULE = {0: None}  # the rule the model's retention uses (None: the real one)


def _revision_held(record: dict, vrid: str, rev: str) -> bool:
    """The retention rule: a record holds a revision of the virtual record it names when it is on that revision, or when it is not
    COMPLETED (a replacement in flight or failed: its revision is the attempt's, not the served copy's)."""
    if record.get("virtualRecordId") != vrid:
        return False
    return record.get("contentRev") == rev or record.get("indexingStatus") not in (None, "COMPLETED")


def _revision_held_by_content_rev_only(record: dict, vrid: str, rev: str) -> bool:
    return record.get("virtualRecordId") == vrid and record.get("contentRev") == rev


def _held(graph: FakeGraph, vrid: str) -> bool:
    return any(holds_vrid(d, vrid) for d in graph.records.values())


@pytest.fixture(autouse=True)
def _no_undo_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.modules.transformers.swap_commit.UNDO_BACKOFF_SECONDS", 0)


class World:
    def __init__(self, rng: random.Random, twin: bool) -> None:
        self.rng = rng
        base = {"orgId": "o", "md5Checksum": "m", "connectorId": "k1"}
        self.graph = FakeGraph({"A": {**base, "virtualRecordId": "X", "indexingStatus": "COMPLETED", "hasIndexedCopy": True, "contentRev": "r0"}})
        self.states: set[tuple[str, str]] = {("X", "r0")}  # the stage states of the served copy
        self.served_rev = "r0"
        if twin:
            self.graph.records["B"] = {**base, "virtualRecordId": "X", "indexingStatus": "COMPLETED", "hasIndexedCopy": True}
        self.points: dict[str, str] = {"X": "complete"}  # vrid -> "complete" | "partial"
        # the vector store as the commit reads it: a complete copy has both blocks, a partial one the first
        def view(vrid: str) -> list[str]:
            if rng.random() < 0.03:
                raise TimeoutError("the vector store did not answer")  # a read that fails, before or after the commit write
            return {"complete": ["b1", "b2"], "partial": ["b1"]}.get(self.points.get(vrid, ""), [])

        self.swap, _ = committer(self.graph, view=view)
        self.swap._release_unheld = AsyncMock(side_effect=self._release)
        real_update = self.graph.update_node_fields_if
        self.window_swept: set[str] = set()  # the copies a sweep took between the read before the commit and the write that commits
        self.unverified = False  # the record names a copy that was not verified and could not be undone: its attempt is in flight
        self.faults = True

        async def flaky(key: str, collection: str, updates: dict, conditions: list, *a: object, **k: object) -> bool:
            # the write that undoes a move: it fails now and then, which with a failed read is the double failure
            if self.faults and updates.get("pendingVirtualRecordId") and rng.random() < 0.5:
                raise TimeoutError("the graph store did not answer")
            return await real_update(key, collection, updates, conditions, *a, **k)

        self.graph.update_node_fields_if = flaky  # type: ignore[method-assign]
        real_require = self.swap._require_points

        async def require_then_race(ctx: object, swap: object) -> None:
            """The window between the read of the set and the write that commits: the record's status leaves the running set, the
            sweep takes the copy that nothing holds, and the status comes back."""
            await real_require(ctx, swap)
            if rng.random() < 0.4:
                doc = self.graph.records["A"]
                back = doc["indexingStatus"]
                doc["indexingStatus"] = "COMPLETED"
                before = set(self.points)
                await self.sweep()
                self.window_swept |= before - set(self.points)
                doc["indexingStatus"] = back

        self.swap._require_points = require_then_race  # type: ignore[method-assign]
        self.ep = EventProcessor(MagicMock(), MagicMock(), self.graph, MagicMock())
        self.ep._spawn_abandoned_vrid_cleanup = MagicMock()
        self.ep._rewrite_or_delete_vrid_vectors = AsyncMock(side_effect=lambda v: self._release("o", v))
        self.runs: dict[str, dict] = {}
        self.n = 0

    async def _release(self, org: str, vrid: str) -> None:
        if not _held(self.graph, vrid):  # the holder-checked deleters: a held copy is kept
            self.points.pop(vrid, None)

    def retention(self, held=None) -> None:
        """Delete the stage states of a revision no record holds."""
        held = held or _revision_held
        for state in list(self.states):
            if not any(held(d, *state) for d in self.graph.records.values()):
                self.states.discard(state)

    async def sweep(self) -> None:
        for vrid in list(self.points):
            await self._release("o", vrid)

    async def step(self, action: str, run: str) -> None:
        g = self.graph
        st = self.runs.get(run)
        if action == "start" and st is None:
            self.n += 1
            g.records["A"]["indexingStatus"] = "IN_PROGRESS"  # the attempt start, as update_record_fields writes it
            g.records["A"]["contentRev"] = f"r{self.n}"
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
            if st["y"] not in self.points:
                self.points[st["y"]] = "partial"  # the first half of the set; a sweep may come before the rest
            elif self.points[st["y"]] == "partial":
                self.points[st["y"]] = "complete"
                st["written"] = True
        elif action == "commit" and st["written"]:
            try:
                await self.swap.commit(ctx_for("A", st["y"], pointed=["b1", "b2"]), keep_old_copy.SwapRun("A", "o", st["x"], st["y"]), {"indexingStatus": "COMPLETED", "hasIndexedCopy": True})
                if g.records["A"]["virtualRecordId"] == st["y"]:
                    self.served_rev = g.records["A"]["contentRev"]
                    self.states.add((st["y"], self.served_rev))  # the stages of the new copy run after its commit
                await self.swap._release_unheld("o", st["x"]) if not _held(g, st["x"]) else None
            except (SupersededRun, IndexingError, TimeoutError):
                # superseded, or the set is not whole (or was taken, or could not be read): the record keeps the copy it serves; when the undo
                # failed as well the record names the new copy with its attempt still in flight (until the rerun)
                if g.records["A"]["virtualRecordId"] == st["y"]:
                    self.unverified = True
            self.runs.pop(run)
            if not self.runs and g.records["A"]["indexingStatus"] == "COMPLETED":
                pass
        elif action == "fail":
            g.records["A"]["indexingStatus"] = "FAILED" if self.rng.random() < 0.5 else g.records["A"]["indexingStatus"]
            if g.records["A"]["indexingStatus"] == "FAILED":
                await self.swap.release_failed("A", "o")
            self.runs.pop(run)  # the run is over; its points (if any) stay until a sweep

    async def attach(self) -> None:
        """The record ends a run as a copy of another record's (or is finished by another path that takes its pending field with it)."""
        doc = self.graph.records["A"]
        await self.ep._clear_pending({**doc, "_key": "A"})
        doc["indexingStatus"] = "COMPLETED"
        self.served_rev = doc["contentRev"]
        self.states.add((doc["virtualRecordId"], self.served_rev))

    async def edit(self) -> None:
        """A connector save mid-run stores NOT_STARTED, and a later run requeues the record."""
        doc = self.graph.records["A"]
        # a save mid-run stores NOT_STARTED; another path (a twin's attach, a status repair) can finish the record without touching
        # the pending field, which releases the copy for every lookup
        doc["indexingStatus"] = self.rng.choice(["NOT_STARTED", "QUEUED", "IN_PROGRESS", "COMPLETED"])
        if doc["indexingStatus"] == "COMPLETED":
            self.served_rev = doc["contentRev"]  # the path that finished it serves what the record now names
            self.states.add((doc["virtualRecordId"], self.served_rev))

    async def recover(self) -> None:
        """The rerun of an attempt that was left in flight (the handler's requeue, or the stale-record sweep)."""
        self.faults = False
        for _ in range(12):
            if not self.unverified:
                return
            self.runs.pop("RECOVER", None)
            await self.step("start", "RECOVER")
            for _ in range(3):
                await self.step("write", "RECOVER")
            await self.step("commit", "RECOVER")
            if self.graph.records["A"]["indexingStatus"] == "COMPLETED" and self.graph.records["A"].get("pendingVirtualRecordId") is None \
                    and self.points.get(self.graph.records["A"]["virtualRecordId"]) == "complete":
                self.unverified = False

    def serve(self) -> None:
        if self.unverified:
            # The copy a record keeps serving after the double failure is the one the read before the commit confirmed: it is whole, unless
            # a sweep took it in the narrow window between that read and the write (a status that left the running set and came back).
            doc = self.graph.records["A"]
            assert self.points.get(doc["virtualRecordId"]) == "complete" or doc["virtualRecordId"] in self.window_swept, (
                "an unverified copy is incomplete", self.points)
        for key, doc in self.graph.records.items():
            if key == "A" and self.unverified:
                continue  # the record whose attempt is in flight: checked above, and finished by the rerun
            if doc.get("hasIndexedCopy") and doc["indexingStatus"] not in ("EMPTY",):
                assert self.points.get(doc["virtualRecordId"]) == "complete", (key, doc, self.points)

    def served_states(self) -> None:
        """The stage states of the copy a searchable record serves are never retained away."""
        doc = self.graph.records["A"]
        if self.unverified:
            return  # an attempt in flight on a copy that was never finished: it has no stage states until the rerun commits (A only)
        if doc.get("hasIndexedCopy"):
            assert (doc["virtualRecordId"], self.served_rev) in self.states, ("a served copy lost its stage states", doc, self.states)

    def leak(self) -> None:
        for vrid in self.points:
            assert _held(self.graph, vrid), ("leak", vrid, self.graph.records)


async def _one(seed: int) -> None:
    rng = random.Random(seed)
    w = World(rng, twin=rng.random() < 0.5)
    for _ in range(rng.randint(4, 14)):
        action = rng.choice(["start", "start", "write", "write", "write", "commit", "commit", "fail", "edit", "attach", "sweep", "sweep"])
        run = rng.choice(["R1", "R2"])
        if action == "edit":
            await w.edit()
        elif action == "attach":
            await w.attach()
        elif action == "sweep":
            await w.sweep()
        else:
            await w.step(action, run)
        w.serve()
        if rng.random() < 0.3:
            w.retention(_RETENTION_RULE[0])
        w.served_states()
    # nothing is left running: the records end in a settled state and the sweep has run
    w.runs.clear()
    await w.recover()
    assert not w.unverified, "the rerun did not finish the attempt that was left in flight"
    for doc in w.graph.records.values():
        if doc["indexingStatus"] in ("IN_PROGRESS", "QUEUED", "NOT_STARTED"):
            doc["indexingStatus"] = "FAILED"
    await w.sweep()
    w.serve()
    w.leak()


def test_no_interleaving_loses_a_served_copy_or_leaks_one() -> None:
    for seed in range(SEED_BASE, SEED_BASE + SEEDS):
        try:
            asyncio.run(_one(seed))
        except AssertionError as exc:
            pytest.fail(f"seed {seed}: {exc}")


@pytest.mark.parametrize("seed", REGRESSION_SEEDS)
def test_the_seeds_that_failed_at_fresh_bases_stay_fixed(seed: int) -> None:
    """A status that left the running set and came back while the sweep took the copy committed on an empty one."""
    asyncio.run(_one(seed))


def _without_the_checks_of_the_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """The holder rule and the status match are the first line; the reads of the set before and after the commit are the second. A control of
    the first line switches the second off, or the second would hide the loss."""
    from app.modules.transformers.swap_commit import SwapCommitter

    async def nothing(self: object, *a: object, **k: object) -> None:
        return None

    monkeypatch.setattr(SwapCommitter, "_confirm_after_commit", nothing)
    monkeypatch.setattr(SwapCommitter, "_require_points", nothing)


def _names_only(record: dict, vrid: str) -> bool:
    return record.get("virtualRecordId") == vrid


def test_negative_control_a_lookup_that_ignores_the_pending_vrid_loses_a_copy(monkeypatch) -> None:
    """The sweep deletes a running copy when the holder lookup cannot see it, and the commit then lands on nothing: the property fails."""
    _without_the_checks_of_the_set(monkeypatch)
    monkeypatch.setattr("tests.support.swap_world.holds_vrid", _names_only)
    monkeypatch.setitem(globals(), "holds_vrid", _names_only)
    failures = 0
    for seed in range(CONTROL_SEEDS):
        try:
            asyncio.run(_one(seed))
        except AssertionError:
            failures += 1
    assert failures > 0


def test_negative_control_a_commit_that_does_not_match_the_status_loses_a_copy(monkeypatch) -> None:
    _without_the_checks_of_the_set(monkeypatch)
    """Without the status condition a commit lands on a record that was meanwhile failed and whose copy the sweep took."""
    from app.modules.transformers import swap_commit

    real = swap_commit.COMMITTABLE if hasattr(swap_commit, "COMMITTABLE") else None
    monkeypatch.setattr(keep_old_copy, "COMMITTABLE", ("IN_PROGRESS", "QUEUED", "NOT_STARTED", "COMPLETED", "FAILED", "EMPTY"))
    failures = 0
    for seed in range(CONTROL_SEEDS):
        try:
            asyncio.run(_one(seed))
        except AssertionError:
            failures += 1
    assert real is None
    assert failures > 0


def test_negative_control_retention_by_content_revision_alone_loses_the_served_states(monkeypatch) -> None:
    """The rule the retention queries had: the states of the served revision are deleted while a replacement of the record is in flight."""
    monkeypatch.setitem(_RETENTION_RULE, 0, _revision_held_by_content_rev_only)
    failures = 0
    for seed in range(CONTROL_SEEDS):
        try:
            asyncio.run(_one(seed))
        except AssertionError:
            failures += 1
    assert failures > 0


def test_negative_control_without_the_confirmation_after_the_commit_a_copy_taken_in_the_window_is_committed(monkeypatch) -> None:
    """The window between the read of the set and the commit write: a sweep takes the copy while the record is in a status that does not hold it,
    and the status comes back. Only the read after the write undoes that commit."""
    from app.modules.transformers.swap_commit import SwapCommitter

    async def nothing(self: object, *a: object, **k: object) -> None:
        return None

    monkeypatch.setattr(SwapCommitter, "_confirm_after_commit", nothing)
    failures = 0
    for seed in range(CONTROL_SEEDS):
        try:
            asyncio.run(_one(seed))
        except AssertionError:
            failures += 1
    assert failures > 0
