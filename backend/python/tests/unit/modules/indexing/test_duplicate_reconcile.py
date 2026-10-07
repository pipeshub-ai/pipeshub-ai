"""Duplicate reconciliation (KG-51): the shared reconcile step, and the sweep
that retries a primary whose ``duplicateReconcilePending`` flag the record
handler could not clear, which otherwise waits for an event that may never
come."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.indexing.duplicate_reconcile import (
    MAX_RECONCILE_ATTEMPTS,
    DuplicateReconciler,
    retry_pending_duplicate_reconciles,
)
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_ATTEMPTS_FIELD as RECONCILE_ATTEMPTS_FIELD,
)
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_DUE_AT_FIELD as DUE_AT,
)
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_PENDING_FIELD,
)

RECORDS = CollectionNames.RECORDS.value
NOW = 1_800_000_000_000
LOGGER = logging.getLogger("duplicate-reconcile-test")


class FakeGraph:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.siblings: dict[str, list[str]] = {}
        self.copy_ok: dict[str, bool] = {}
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.pending_queries: list[tuple[int, int]] = []

    async def get_records_by_virtual_record_id(self, vrid: str) -> list[str]:
        return list(self.siblings.get(vrid, []))

    async def get_document(self, key: str, collection: str) -> dict | None:
        doc = self.docs.get(key)
        return dict(doc) if doc is not None else None

    async def copy_document_relationships(self, source: str, target: str) -> bool:
        return self.copy_ok.get(target, True)

    async def update_node(self, key: str, collection: str, updates: dict) -> bool:
        self.updates.append((key, dict(updates)))
        self.docs[key].update(updates)
        return True

    async def update_node_fields_if_match(
        self, key: str, collection: str, updates: dict, expected: dict,
        transaction: str | None = None,
    ) -> bool:
        doc = self.docs.get(key)
        if doc is None or any(doc.get(f) != v for f, v in expected.items()):
            return False
        self.updates.append((key, dict(updates)))
        doc.update(updates)
        return True

    async def get_records_pending_duplicate_reconcile(
        self, due_before_ms: int, limit: int, transaction: str | None = None,
    ) -> list[dict]:
        self.pending_queries.append((due_before_ms, limit))
        rows = [
            {"_key": k, RECONCILE_ATTEMPTS_FIELD: d.get(RECONCILE_ATTEMPTS_FIELD), DUE_AT: d.get(DUE_AT)}
            for k, d in sorted(self.docs.items())
            if d.get(DUPLICATE_RECONCILE_PENDING_FIELD) is True
            and (d.get(DUE_AT) or 0) < due_before_ms
        ]
        return rows[:limit]


def _reconciler(graph: FakeGraph, sink_ok: bool | Exception = True,
                membership: AsyncMock | None = None) -> tuple[DuplicateReconciler, AsyncMock]:
    sink = AsyncMock()
    if isinstance(sink_ok, Exception):
        sink.sync_entities_for_duplicate = AsyncMock(side_effect=sink_ok)
    else:
        sink.sync_entities_for_duplicate = AsyncMock(return_value=sink_ok)
    return DuplicateReconciler(
        graph_provider=graph, sink=sink,
        sync_vector_membership=membership or AsyncMock(), logger=LOGGER,
    ), sink


def _group(graph: FakeGraph, *, primary: str = "p1", siblings: tuple[str, ...] = ("s1",),
           org: str = "org-1", **primary_fields: object) -> None:
    graph.docs[primary] = {"_key": primary, "orgId": org, "virtualRecordId": "vr1", **primary_fields}
    for sibling in siblings:
        graph.docs[sibling] = {"_key": sibling, "orgId": org, "virtualRecordId": "vr1"}
    graph.siblings["vr1"] = [primary, *siblings]


class TestReconciler:
    async def test_copies_and_syncs_every_sibling(self) -> None:
        graph = FakeGraph()
        _group(graph, siblings=("s1", "s2"))
        reconciler, sink = _reconciler(graph)
        assert await reconciler.reconcile("p1", "vr1") is True
        assert [c.args[0]["_key"] for c in sink.sync_entities_for_duplicate.await_args_list] == ["s1", "s2"]

    async def test_a_failed_entity_sync_is_a_failed_reconcile(self) -> None:
        """The flag used to be cleared although the sibling had no entity points."""
        graph = FakeGraph()
        _group(graph)
        reconciler, _ = _reconciler(graph, sink_ok=False)
        assert await reconciler.reconcile("p1", "vr1") is False

    async def test_a_failed_copy_is_a_failed_reconcile(self) -> None:
        graph = FakeGraph()
        _group(graph, siblings=("s1", "s2"))
        graph.copy_ok["s1"] = False
        reconciler, sink = _reconciler(graph)
        assert await reconciler.reconcile("p1", "vr1") is False
        assert [c.args[0]["_key"] for c in sink.sync_entities_for_duplicate.await_args_list] == ["s2"]

    async def test_cross_org_sibling_is_skipped(self) -> None:
        graph = FakeGraph()
        _group(graph)
        graph.docs["x1"] = {"_key": "x1", "orgId": "org-2"}
        graph.siblings["vr1"].append("x1")
        reconciler, sink = _reconciler(graph)
        assert await reconciler.reconcile("p1", "vr1") is True
        assert [c.args[0]["_key"] for c in sink.sync_entities_for_duplicate.await_args_list] == ["s1"]

    @pytest.mark.parametrize("vrid", [None, ""])
    async def test_no_virtual_record_id_is_done(self, vrid: str | None) -> None:
        reconciler, _ = _reconciler(FakeGraph())
        assert await reconciler.reconcile("p1", vrid) is True

    async def test_errors_are_reported_not_raised(self) -> None:
        graph = FakeGraph()
        _group(graph)
        reconciler, _ = _reconciler(graph, membership=AsyncMock(side_effect=RuntimeError("down")))
        assert await reconciler.reconcile("p1", "vr1") is False


async def _sweep(graph: FakeGraph, reconciler: DuplicateReconciler) -> int:
    return await retry_pending_duplicate_reconciles(
        graph_provider=graph, reconciler=reconciler, logger=LOGGER, page_size=10,
        now_ms=lambda: NOW,
    )


def _pending(graph: FakeGraph, *, due: int | None = NOW - 1, **fields: object) -> None:
    _group(graph, **{
        DUPLICATE_RECONCILE_PENDING_FIELD: True, DUE_AT: due,
        "indexingStatus": "COMPLETED", **fields,
    })


class TestRetrySweep:
    async def test_due_primary_is_reconciled_and_cleared(self) -> None:
        graph = FakeGraph()
        _pending(graph)
        reconciler, _ = _reconciler(graph)
        assert await _sweep(graph, reconciler) == 1
        doc = graph.docs["p1"]
        assert doc[DUPLICATE_RECONCILE_PENDING_FIELD] is False
        assert doc[RECONCILE_ATTEMPTS_FIELD] == 0 and doc[DUE_AT] is None
        assert graph.pending_queries == [(NOW, 10)]

    async def test_not_yet_due_is_left_alone(self) -> None:
        graph = FakeGraph()
        _pending(graph, due=NOW + 1000)
        reconciler, sink = _reconciler(graph)
        assert await _sweep(graph, reconciler) == 0
        sink.sync_entities_for_duplicate.assert_not_awaited()

    async def test_takes_no_record_lease(self) -> None:
        """The consumer drops an event whose record lease it cannot get within
        seconds; a sweep holding it through a long reconcile would lose a real
        update or delete for the record."""
        import inspect

        from app.modules.indexing import duplicate_reconcile

        source = inspect.getsource(duplicate_reconcile)
        assert "try_acquire" not in source
        params = inspect.signature(duplicate_reconcile.retry_pending_duplicate_reconciles).parameters
        assert "concurrency_manager" not in params

    async def test_a_newer_promotion_keeps_its_flag(self) -> None:
        """A promotion during the reconcile re-arms the flag with a new due
        time; clearing it unconditionally would lose that promotion's copy."""
        graph = FakeGraph()
        _pending(graph)
        reconciler, sink = _reconciler(graph)

        async def _promoted_meanwhile(doc: dict) -> bool:
            graph.docs["p1"][DUE_AT] = NOW + 600_000
            return True

        sink.sync_entities_for_duplicate = AsyncMock(side_effect=_promoted_meanwhile)
        assert await _sweep(graph, reconciler) == 0
        assert graph.docs["p1"][DUPLICATE_RECONCILE_PENDING_FIELD] is True

    async def test_cleared_before_the_retry_is_skipped(self) -> None:
        graph = FakeGraph()
        _pending(graph)
        reconciler, sink = _reconciler(graph)
        real_get = graph.get_document

        async def _cleared(key: str, collection: str) -> dict | None:
            doc = await real_get(key, collection)
            if key == "p1" and doc is not None:
                doc[DUPLICATE_RECONCILE_PENDING_FIELD] = False
            return doc

        graph.get_document = _cleared
        assert await _sweep(graph, reconciler) == 0
        sink.sync_entities_for_duplicate.assert_not_awaited()

    async def test_primary_no_longer_indexed_is_cleared_without_copying(self) -> None:
        graph = FakeGraph()
        _pending(graph, indexingStatus="FAILED")
        reconciler, sink = _reconciler(graph)
        await _sweep(graph, reconciler)
        sink.sync_entities_for_duplicate.assert_not_awaited()
        assert graph.docs["p1"][DUPLICATE_RECONCILE_PENDING_FIELD] is False

    async def test_failures_back_off_then_give_up(self, caplog) -> None:
        graph = FakeGraph()
        _pending(graph)
        reconciler, _ = _reconciler(graph, sink_ok=False)
        dues = []
        for attempt in range(1, MAX_RECONCILE_ATTEMPTS):
            await _sweep(graph, reconciler)
            doc = graph.docs["p1"]
            assert doc[RECONCILE_ATTEMPTS_FIELD] == attempt
            assert doc[DUPLICATE_RECONCILE_PENDING_FIELD] is True
            dues.append(doc[DUE_AT])
            doc[DUE_AT] = NOW - 1  # time passes
        assert dues == sorted(dues) and dues[0] > NOW
        assert dues[1] - NOW == 2 * (dues[0] - NOW)
        with caplog.at_level(logging.ERROR, logger="duplicate-reconcile-test"):
            await _sweep(graph, reconciler)
        doc = graph.docs["p1"]
        assert doc[DUPLICATE_RECONCILE_PENDING_FIELD] is False
        assert doc[RECONCILE_ATTEMPTS_FIELD] == MAX_RECONCILE_ATTEMPTS
        assert any("p1" in r.getMessage() for r in caplog.records)

    async def test_an_error_counts_as_a_failed_attempt(self) -> None:
        """Otherwise a record the sweep cannot read is retried every tick."""
        graph = FakeGraph()
        _pending(graph)
        reconciler, _ = _reconciler(graph)
        real_get = graph.get_document

        async def _broken(key: str, collection: str) -> dict | None:
            if key == "p1":
                raise RuntimeError("unreadable")
            return await real_get(key, collection)

        graph.get_document = _broken
        await _sweep(graph, reconciler)
        assert graph.docs["p1"][RECONCILE_ATTEMPTS_FIELD] == 1
        assert graph.docs["p1"][DUE_AT] > NOW

    async def test_one_failing_record_does_not_stop_the_page(self) -> None:
        graph = FakeGraph()
        _pending(graph)
        graph.docs["p2"] = {"_key": "p2", "orgId": "org-1", "virtualRecordId": "vr2",
                            "indexingStatus": "COMPLETED",
                            DUPLICATE_RECONCILE_PENDING_FIELD: True, DUE_AT: NOW - 1}
        graph.siblings["vr2"] = ["p2"]
        reconciler, _ = _reconciler(graph)
        real_get = graph.get_document

        async def _flaky(key: str, collection: str) -> dict | None:
            if key == "p1":
                raise RuntimeError("graph hiccup")
            return await real_get(key, collection)

        graph.get_document = _flaky
        assert await _sweep(graph, reconciler) == 1
        assert graph.docs["p2"][DUPLICATE_RECONCILE_PENDING_FIELD] is False

    async def test_query_failure_raises(self) -> None:
        graph = FakeGraph()
        graph.get_records_pending_duplicate_reconcile = AsyncMock(side_effect=RuntimeError("down"))
        reconciler, _ = _reconciler(graph)
        with pytest.raises(RuntimeError):
            await _sweep(graph, reconciler)


def test_stale_recovery_runs_the_retry() -> None:
    import inspect

    from app import indexing_main

    source = inspect.getsource(indexing_main.recover_in_progress_records)
    assert "retry_pending_duplicate_reconciles(" in source
