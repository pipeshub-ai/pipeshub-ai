"""Revision retention: which artifact sets and stage states a page retires, and which it never does."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.artifacts import retention as retention_module
from app.modules.artifacts.retention import RetentionReport, RevisionRetentionSweep
from app.modules.artifacts.store import ARTIFACT_ROUTES, ArtifactRoutesUnavailable
from app.services.graph_db.common.record_visibility import RecordVisibility

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = 10_000 * 3600 * 1000
HOUR = 3600 * 1000
OLD = NOW - 48 * HOUR
YOUNG = NOW - HOUR
ORG = "org-1"
logger = logging.getLogger("retention-test")


@dataclass
class FakeGraph:
    """Mapping rows, records (vrid, rev, trashed) and stage states (vrid, rev, updatedAtMs)."""

    mappings: dict[str, dict[str, Any]] = field(default_factory=dict)
    records: list[tuple[str, str | None, bool]] = field(default_factory=list)
    states: list[tuple[str, str, int]] = field(default_factory=list)
    fail_holders_for: set[str] = field(default_factory=set)
    fail_stage_states: bool = False
    after_page_read: Any = None

    def map(self, vrid: str, current: str | None = None, previous: str | None = None, org: str | None = ORG) -> None:
        self.mappings[vrid] = {"orgId": org, "currentRev": current, "previousRev": previous}

    async def get_revision_pointers(self, after_key: str | None, limit: int) -> list[dict[str, Any]]:
        keys = sorted(k for k in self.mappings if after_key is None or k > after_key)[:limit]
        return [{"virtualRecordId": k, **{f: self.mappings[k].get(f) for f in ("orgId", "currentRev", "previousRev")}}
                for k in keys]

    async def get_virtual_record_ids_in_use(
        self, vrids: list[str], *, visibility: RecordVisibility = RecordVisibility.LIVE
    ) -> set[str]:
        assert visibility is RecordVisibility.DELETED
        return {v for v, _, trashed in self.records if trashed and v in vrids}

    async def get_document(self, key: str, collection: str) -> dict[str, Any] | None:
        assert collection == CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
        if self.after_page_read is not None:
            hook, self.after_page_read = self.after_page_read, None
            hook()
        row = self.mappings.get(key)
        return None if row is None else {"id": key, **row}

    async def get_virtual_record_holders(self, vrids: list[str], org_id: str) -> dict[str, list[dict[str, Any]]]:
        if set(vrids) & self.fail_holders_for:
            raise RuntimeError("graph down")
        out: dict[str, list[dict[str, Any]]] = {}
        for i, (v, rev, trashed) in enumerate(self.records):
            if v in vrids:
                out.setdefault(v, []).append({"id": f"r{i}", "contentRev": rev, "isDeleted": trashed})
        return out

    def _unheld(self, vrids: set[str], before: int) -> list[tuple[str, str]]:
        held = {(v, rev) for v, rev, _ in self.records}
        newest: dict[tuple[str, str], int] = {}
        for v, rev, at in self.states:
            if v in vrids:
                newest[(v, rev)] = max(newest.get((v, rev), 0), at)
        return sorted(k for k, at in newest.items() if at < before and k not in held)

    async def stage_states_unheld_revisions(self, vrids: list[str], *, updated_before_ms: int) -> list[tuple[str, str]]:
        if self.fail_stage_states:
            raise RuntimeError("graph down")
        return self._unheld(set(vrids), updated_before_ms)

    async def stage_states_delete_unheld_revisions(
        self, revisions: list[tuple[str, str]], *, updated_before_ms: int
    ) -> int:
        doomed = set(self._unheld({v for v, _ in revisions}, updated_before_ms)) & set(revisions)
        before = len(self.states)
        self.states = [s for s in self.states if (s[0], s[1]) not in doomed]
        return before - len(self.states)


@dataclass
class FakeStore:
    revisions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    fail_list_for: set[str] = field(default_factory=set)
    routes_missing: bool = False
    list_calls: int = 0
    before_delete: Any = None

    def add(self, vrid: str, rev: str, created_at: int | None = OLD) -> None:
        entry: dict[str, Any] = {"documentId": f"{vrid}@{rev}", "rev": rev}
        if created_at is not None:
            entry["createdAt"] = created_at
        self.revisions.setdefault(vrid, []).append(entry)

    async def list_revisions(self, org_id: str, vrid: str) -> list[dict[str, Any]]:
        self.list_calls += 1
        if self.routes_missing:
            raise ArtifactRoutesUnavailable(404)
        if vrid in self.fail_list_for:
            raise RuntimeError("storage down")
        return [r for r in self.revisions.get(vrid, []) if r["documentId"] not in self.deleted]

    async def delete_revision(self, org_id: str, document_id: str) -> int:
        if self.before_delete is not None:
            await self.before_delete(document_id)
        self.deleted.append(document_id)
        return 1

    def left(self, vrid: str) -> set[str]:
        return {r["rev"] for r in self.revisions.get(vrid, []) if r["documentId"] not in self.deleted}


@pytest.fixture(autouse=True)
def _routes() -> Iterator[None]:
    ARTIFACT_ROUTES.reset()
    yield
    ARTIFACT_ROUTES.reset()


@dataclass
class FakeLeases:
    """``record:<id>`` leases as the record consumer and the sweep take them."""

    owners: dict[str, str] = field(default_factory=dict)
    refuse_renewal: bool = False
    released: list[str] = field(default_factory=list)

    async def try_acquire(self, pool: str, owner: str, limit: int, lease_seconds: float) -> bool:
        if self.owners.get(pool, owner) != owner:
            return False
        self.owners[pool] = owner
        return True

    async def renew_many(self, leases: list[tuple[str, str]], lease_seconds: float) -> dict[tuple[str, str], bool]:
        return {(p, o): not self.refuse_renewal and self.owners.get(p) == o for p, o in leases}

    async def release(self, pool: str, owner: str) -> None:
        if self.owners.get(pool) == owner:
            del self.owners[pool]
            self.released.append(pool)


def _sweep(graph: FakeGraph, store: FakeStore, **kwargs: object) -> RevisionRetentionSweep:
    return RevisionRetentionSweep(graph, store, logger, now_ms=lambda: NOW, **kwargs)  # type: ignore[arg-type]


def _revs(store: FakeStore, vrid: str, *revs: str, created_at: int | None = OLD) -> None:
    for rev in revs:
        store.add(vrid, rev, created_at)


async def test_never_deletes_the_current_or_previous_revision() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("v", "r3", "r2")
    graph.records.append(("v", "r3", False))
    _revs(store, "v", "r1", "r2", "r3", "r0")

    _, report = await _sweep(graph, store).run_page(None)

    assert store.left("v") == {"r2", "r3"}
    assert (report.revisions_deleted, report.revisions_kept) == (2, 2)


async def test_respects_the_grace() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("v", "r3", "r2")
    _revs(store, "v", "r1", created_at=YOUNG)
    _revs(store, "v", "r0", created_at=None)
    _revs(store, "v", "rx")
    store.revisions["v"].append({"documentId": "v@ry", "rev": "ry", "createdAt": OLD, "updatedAt": YOUNG})

    await _sweep(graph, store).run_page(None)

    assert store.deleted == ["v@rx"]


async def test_no_current_rev_deletes_nothing_and_lists_nothing() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("v", None, "r2")
    _revs(store, "v", "r1")

    _, report = await _sweep(graph, store).run_page(None)

    assert store.deleted == [] and store.list_calls == 0
    assert report.scanned == 1


async def test_a_revision_a_record_is_on_is_kept() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("v", "r3", "r2")
    graph.records += [("v", "r3", False), ("v", "r1", False)]
    _revs(store, "v", "r0", "r1")

    await _sweep(graph, store).run_page(None)

    assert store.deleted == ["v@r0"]


async def test_a_record_moving_onto_an_old_revision_during_the_page_keeps_it() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("v", "r3", "r2")
    _revs(store, "v", "r1")
    graph.after_page_read = lambda: graph.records.append(("v", "r1", False))

    await _sweep(graph, store).run_page(None)

    assert store.deleted == []


async def test_a_trashed_holder_leaves_the_virtual_record_whole() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("v", "r3", "r2")
    graph.records += [("v", "r3", False), ("v", "r3", True)]
    _revs(store, "v", "r1")
    graph.states.append(("v", "r1", OLD))

    _, report = await _sweep(graph, store).run_page(None)

    assert store.deleted == [] and store.list_calls == 0
    assert graph.states == [("v", "r1", OLD)]
    assert report.skipped_trashed == 1


async def test_trash_then_restore_keeps_the_content_until_it_is_retired() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("v", "r3", "r2")
    graph.records.append(("v", "r3", True))
    _revs(store, "v", "r1", "r2", "r3")
    sweep = _sweep(graph, store)

    await sweep.run_page(None)
    assert store.left("v") == {"r1", "r2", "r3"}

    graph.records[0] = ("v", "r3", False)
    await sweep.run_page(None)
    assert store.left("v") == {"r2", "r3"}


async def test_trash_appearing_after_the_page_was_read_still_keeps_the_content() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("v", "r3", "r2")
    _revs(store, "v", "r1")
    graph.after_page_read = lambda: graph.records.append(("v", "r3", True))

    await _sweep(graph, store).run_page(None)

    assert store.deleted == []


async def test_orphan_stage_states_go_only_past_the_grace_and_only_where_no_record_is() -> None:
    graph, store = FakeGraph(), FakeStore()
    graph.map("a")
    graph.map("b")
    graph.records += [("a", "r2", False), ("b", "r1", False), ("b", "r9", False)]
    graph.states += [
        ("a", "r1", OLD), ("a", "r1", OLD),
        ("a", "r2", OLD),
        ("a", "r3", OLD), ("a", "r3", YOUNG),
        ("b", "r1", OLD),
        ("b", "r0", OLD),
    ]

    _, report = await _sweep(graph, store).run_page(None)

    assert sorted(graph.states) == sorted([("a", "r2", OLD), ("a", "r3", OLD), ("a", "r3", YOUNG), ("b", "r1", OLD)])
    assert report.stage_states_deleted == 3


async def test_a_failing_virtual_record_does_not_abort_the_page(caplog: pytest.LogCaptureFixture) -> None:
    graph, store = FakeGraph(), FakeStore()
    for vrid in ("a", "b", "c"):
        graph.map(vrid, "r3", "r2")
        _revs(store, vrid, "r1")
    store.fail_list_for.add("a")
    graph.fail_holders_for.add("b")

    next_key, report = await _sweep(graph, store).run_page(None)

    assert store.deleted == ["c@r1"]
    assert report.failed == 2 and next_key is None
    assert "next pass retries" in caplog.text


async def test_a_failing_stage_state_step_does_not_stop_the_artifacts() -> None:
    graph, store = FakeGraph(fail_stage_states=True), FakeStore()
    graph.map("v", "r3", "r2")
    _revs(store, "v", "r1")

    _, report = await _sweep(graph, store).run_page(None)

    assert store.deleted == ["v@r1"] and report.failed == 1


async def test_missing_routes_stop_artifact_deletes_but_stage_states_still_go(
    caplog: pytest.LogCaptureFixture,
) -> None:
    graph, store = FakeGraph(), FakeStore(routes_missing=True)
    for vrid in ("a", "b"):
        graph.map(vrid, "r3", "r2")
        _revs(store, vrid, "r1")
        graph.states.append((vrid, "r1", OLD))
    sweep = _sweep(graph, store, concurrency=1)

    _, report = await sweep.run_page(None)
    await sweep.run_page(None)

    assert store.deleted == [] and store.list_calls == 1
    assert graph.states == [] and report.stage_states_deleted == 2
    assert caplog.text.count("no artifact routes") == 1


async def test_pages_follow_the_key_and_end_with_none() -> None:
    graph, store = FakeGraph(), FakeStore()
    for vrid in ("a", "b", "c"):
        graph.map(vrid, "r3", "r2")
        _revs(store, vrid, "r1")
    sweep = _sweep(graph, store, page_size=2)

    key, first = await sweep.run_page(None)
    assert key == "b" and first.scanned == 2
    key, second = await sweep.run_page(key)
    assert key is None and second.scanned == 1
    assert sorted(store.deleted) == ["a@r1", "b@r1", "c@r1"]
    key, again = await sweep.run_page(None)
    assert key == "b" and again.scanned == 2 and again.revisions_deleted == 0
    graph.mappings.clear()
    assert await sweep.run_page(None) == (None, RetentionReport())


async def test_losing_the_lease_stops_the_page_and_keeps_the_cursor() -> None:
    graph, store = FakeGraph(), FakeStore()
    for vrid in ("a", "b", "c"):
        graph.map(vrid, "r3", "r2")
        _revs(store, vrid, "r1")
    held = iter([True, True, False])

    key, report = await _sweep(graph, store, concurrency=1, page_size=3).run_page(
        "0", still_leader=lambda: next(held, False)
    )

    assert key == "0" and report.interrupted
    assert len(store.deleted) <= 1


async def test_a_re_index_waits_for_the_delete_of_the_set_it_would_reuse() -> None:
    """A record going back to old bytes re-uses that revision's document. Its re-index holds
    the record lease from the ``contentRev`` write to the pointer flip, so it cannot run
    between the sweep's last read and the delete, and points at a document that exists."""
    graph, store, leases = FakeGraph(), FakeStore(), FakeLeases()
    graph.map("v", "r3", "r2")
    graph.records.append(("v", "r3", False))
    _revs(store, "v", "r1")
    deferred: list[Any] = []

    async def reindex_onto_r1() -> bool:
        if not await leases.try_acquire("record:r0", "indexer", 1, 60):
            return False
        graph.records[0] = ("v", "r1", False)
        reused = next((r for r in store.revisions["v"] if r["rev"] == "r1" and r["documentId"] not in store.deleted),
                      None)
        if reused is None:
            store.add("v", "r1", NOW)
            reused = store.revisions["v"][-1] = {**store.revisions["v"][-1], "documentId": "v@r1#2"}
        graph.mappings["v"].update(currentRev="r1", previousRev="r3", manifestDocId=reused["documentId"])
        await leases.release("record:r0", "indexer")
        return True

    async def between_reads_and_delete(document_id: str) -> None:
        if not await reindex_onto_r1():
            deferred.append(reindex_onto_r1)

    store.before_delete = between_reads_and_delete
    await _sweep(graph, store, leases=leases).run_page(None)
    for run in deferred:
        assert await run()

    assert graph.mappings["v"]["manifestDocId"] not in store.deleted
    assert leases.owners == {}


async def test_a_busy_record_leaves_its_virtual_record_for_the_next_pass() -> None:
    graph, store, leases = FakeGraph(), FakeStore(), FakeLeases()
    graph.map("v", "r3", "r2")
    graph.records += [("v", "r3", False), ("v", "r3", False)]
    _revs(store, "v", "r1")
    graph.states.append(("v", "r0", OLD))
    leases.owners["record:r1"] = "indexer"
    sweep = _sweep(graph, store, leases=leases)

    _, report = await sweep.run_page(None)

    assert store.deleted == [] and graph.states == [("v", "r0", OLD)]
    assert report.skipped_busy == 1 and report.revisions_kept == 1
    assert leases.owners == {"record:r1": "indexer"}, "the lease it did take is released"

    del leases.owners["record:r1"]
    _, report = await sweep.run_page(None)
    assert store.deleted == ["v@r1"] and graph.states == []
    assert sorted(leases.released) == ["record:r0", "record:r0", "record:r1"]


async def test_a_record_joining_after_the_leases_were_taken_keeps_everything() -> None:
    graph, store, leases = FakeGraph(), FakeStore(), FakeLeases()
    graph.map("v", "r3", "r2")
    graph.records.append(("v", "r3", False))
    _revs(store, "v", "r1")
    graph.states.append(("v", "r0", OLD))
    graph.after_page_read = lambda: graph.records.append(("v", "r3", False))

    _, report = await _sweep(graph, store, leases=leases).run_page(None)

    assert store.deleted == [] and graph.states == [("v", "r0", OLD)]
    assert report.skipped_busy == 1


async def test_a_lease_that_cannot_be_renewed_stops_the_deletes() -> None:
    graph, store, leases = FakeGraph(), FakeStore(), FakeLeases(refuse_renewal=True)
    graph.map("v", "r3", "r2")
    graph.records.append(("v", "r3", False))
    _revs(store, "v", "r0", "r1")

    _, report = await _sweep(graph, store, leases=leases).run_page(None)

    assert store.deleted == [] and report.revisions_kept == 2


def test_a_zero_grace_is_refused() -> None:
    with pytest.raises(ValueError):
        RevisionRetentionSweep(FakeGraph(), FakeStore(), logger, grace_ms=0)  # type: ignore[arg-type]


def test_metrics_count_by_kind_and_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, float]] = []

    class Counter:
        def inc(self, *labels: str, value: float = 1.0) -> None:
            seen.append((*labels, value))  # type: ignore[arg-type]

    from app.telemetry.modules import record_storage_metrics

    monkeypatch.setattr(record_storage_metrics, "RETENTION", Counter())
    record_storage_metrics.record_retention("stage_state", "deleted", 3)
    record_storage_metrics.record_retention("revision", "kept", 0)
    assert seen == [("stage_state", "deleted", 3.0)]
    assert retention_module.record_retention is record_storage_metrics.record_retention
