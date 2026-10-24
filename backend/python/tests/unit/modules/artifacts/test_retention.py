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
        self.deleted.append(document_id)
        return 1

    def left(self, vrid: str) -> set[str]:
        return {r["rev"] for r in self.revisions.get(vrid, []) if r["documentId"] not in self.deleted}


@pytest.fixture(autouse=True)
def _routes() -> Iterator[None]:
    ARTIFACT_ROUTES.reset()
    yield
    ARTIFACT_ROUTES.reset()


def _sweep(graph: FakeGraph, store: FakeStore, **kwargs: int) -> RevisionRetentionSweep:
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
