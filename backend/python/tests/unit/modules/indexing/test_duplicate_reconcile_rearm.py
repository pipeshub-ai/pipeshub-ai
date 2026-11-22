"""A reconcile whose every attempt fails keeps a retry trigger, even when a
sweep already cleared the pending flag before a slow promotion finished."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.indexing.duplicate_reconcile import (
    MAX_RECONCILE_ATTEMPTS,
    DuplicateReconciler,
    rearm_failed_reconcile,
    retry_pending_duplicate_reconciles,
)
from app.modules.pipeline.duplicates import QueuedCopies
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_ATTEMPTS_FIELD as ATTEMPTS,
)
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_DUE_AT_FIELD as DUE_AT,
)
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_GRACE_MS,
)
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_PENDING_FIELD as PENDING,
)

NOW = 1_800_000_000_000
LOGGER = logging.getLogger("duplicate-reconcile-rearm-test")


class FakeGraph:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc = doc
        self.fenced_writes = 0

    async def get_document(self, key: str, collection: str, **_kw: object) -> dict | None:
        return dict(self.doc)

    async def update_node_fields_if_match(
        self, key: str, collection: str, updates: dict, expected: dict, transaction: str | None = None,
    ) -> bool:
        if any(self.doc.get(f) != v for f, v in expected.items()):
            return False
        self.fenced_writes += 1
        self.doc.update(updates)
        return True

    async def update_node(self, key: str, collection: str, updates: dict) -> bool:
        self.doc.update(updates)
        return True

    async def get_records_pending_duplicate_reconcile(self, due_before_ms: int, limit: int, transaction: str | None = None) -> list[dict]:
        if self.doc.get(PENDING) is True and (self.doc.get(DUE_AT) or 0) < due_before_ms:
            return [{"_key": "p1", "orgId": "org-1", ATTEMPTS: self.doc.get(ATTEMPTS), DUE_AT: self.doc.get(DUE_AT)}]
        return []


def _swept_clear_doc(**extra: object) -> dict[str, Any]:
    """The record after a sweep found no promoted copies and cleared the flag."""
    return {"_key": "p1", "orgId": "org-1", "indexingStatus": "COMPLETED", "virtualRecordId": "vr1",
            PENDING: False, ATTEMPTS: 0, DUE_AT: None, **extra}


def _failing_copies(graph: FakeGraph, monkeypatch: pytest.MonkeyPatch) -> QueuedCopies:
    monkeypatch.setattr("app.modules.pipeline.duplicates.RECONCILE_RETRY_DELAY_S", 0)
    monkeypatch.setattr("app.modules.indexing.duplicate_reconcile.get_epoch_timestamp_in_ms", lambda: NOW)
    monkeypatch.setattr(DuplicateReconciler, "reconcile", AsyncMock(return_value=False))
    return QueuedCopies(graph, None, LOGGER)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_late_promotion_with_every_attempt_failing_rearms_the_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = FakeGraph(_swept_clear_doc())
    copies = _failing_copies(graph, monkeypatch)
    await copies._reconcile("p1", "vr1")
    assert graph.doc[PENDING] is True
    assert graph.doc[ATTEMPTS] == 1
    assert graph.doc[DUE_AT] == NOW + DUPLICATE_RECONCILE_GRACE_MS * 2


@pytest.mark.asyncio
async def test_a_later_success_clears_the_rearmed_marker() -> None:
    graph = FakeGraph(_swept_clear_doc())
    assert await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW)  # type: ignore[arg-type]
    reconciler = DuplicateReconciler(graph_provider=graph, sink=None, sync_vector_membership=AsyncMock(), logger=LOGGER)  # type: ignore[arg-type]
    reconciler.reconcile = AsyncMock(return_value=True)  # type: ignore[method-assign]
    # not due yet: the sweep leaves it alone
    assert await retry_pending_duplicate_reconciles(graph_provider=graph, reconciler=reconciler, logger=LOGGER, page_size=10, now_ms=lambda: NOW) == 0  # type: ignore[arg-type]
    later = NOW + DUPLICATE_RECONCILE_GRACE_MS * 2 + 1
    assert await retry_pending_duplicate_reconciles(graph_provider=graph, reconciler=reconciler, logger=LOGGER, page_size=10, now_ms=lambda: later) == 1  # type: ignore[arg-type]
    assert graph.doc[PENDING] is False
    assert graph.doc[DUE_AT] is None


@pytest.mark.asyncio
async def test_retries_are_bounded_by_the_attempt_limit() -> None:
    graph = FakeGraph(_swept_clear_doc())
    reconciler = DuplicateReconciler(graph_provider=graph, sink=None, sync_vector_membership=AsyncMock(), logger=LOGGER)  # type: ignore[arg-type]
    reconciler.reconcile = AsyncMock(return_value=False)  # type: ignore[method-assign]
    clock = NOW
    assert await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: clock)  # type: ignore[arg-type]
    sweeps = 0
    while graph.doc[PENDING] is True:
        sweeps += 1
        assert sweeps <= MAX_RECONCILE_ATTEMPTS, "the sweep never stopped"
        clock = graph.doc[DUE_AT] + 1
        await retry_pending_duplicate_reconciles(graph_provider=graph, reconciler=reconciler, logger=LOGGER, page_size=10, now_ms=lambda c=clock: c)  # type: ignore[arg-type]
    assert graph.doc[ATTEMPTS] == MAX_RECONCILE_ATTEMPTS
    # a handler that fails after the sweep gave up does not start the cycle again
    assert not await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: clock)  # type: ignore[arg-type]
    assert graph.doc[PENDING] is False


@pytest.mark.asyncio
async def test_a_flag_already_set_is_left_to_the_sweep() -> None:
    graph = FakeGraph(_swept_clear_doc(**{PENDING: True, ATTEMPTS: 2, DUE_AT: NOW + 5}))
    assert not await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW)  # type: ignore[arg-type]
    assert (graph.doc[ATTEMPTS], graph.doc[DUE_AT]) == (2, NOW + 5)
    assert graph.fenced_writes == 0


@pytest.mark.asyncio
async def test_a_promotion_made_meanwhile_keeps_its_own_flag() -> None:
    graph = FakeGraph(_swept_clear_doc())
    real_get = graph.get_document

    async def get_then_promote(key: str, collection: str, **kw: object) -> dict | None:
        seen = await real_get(key, collection, **kw)
        graph.doc.update({PENDING: True, ATTEMPTS: 0, DUE_AT: NOW + DUPLICATE_RECONCILE_GRACE_MS})
        return seen

    graph.get_document = get_then_promote  # type: ignore[method-assign]
    assert not await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW)  # type: ignore[arg-type]
    assert graph.doc[ATTEMPTS] == 0


@pytest.mark.asyncio
async def test_a_reindexed_record_is_not_rearmed() -> None:
    graph = FakeGraph(_swept_clear_doc(indexingStatus="IN_PROGRESS"))
    assert not await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW)  # type: ignore[arg-type]
    assert graph.doc[PENDING] is False


@pytest.mark.asyncio
async def test_an_unreadable_record_does_not_raise() -> None:
    graph = FakeGraph(_swept_clear_doc())
    graph.get_document = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
    assert not await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW)  # type: ignore[arg-type]


def _change_after_read(graph: FakeGraph, **changes: object) -> None:
    real_get = graph.get_document

    async def get_then_change(key: str, collection: str, **kw: object) -> dict | None:
        seen = await real_get(key, collection, **kw)
        graph.doc.update(changes)
        return seen

    graph.get_document = get_then_change  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_a_record_reindexed_between_the_read_and_the_write_is_not_rearmed() -> None:
    graph = FakeGraph(_swept_clear_doc())
    _change_after_read(graph, indexingStatus="IN_PROGRESS")
    await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW)  # type: ignore[arg-type]
    assert graph.doc[PENDING] is False
    assert graph.fenced_writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("moved", [{DUE_AT: NOW + 5}, {PENDING: True}], ids=["due_time_moved", "flag_set"])
async def test_the_write_is_fenced_on_both_the_flag_and_the_due_time(moved: dict[str, Any]) -> None:
    graph = FakeGraph(_swept_clear_doc())
    _change_after_read(graph, **moved)
    assert not await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW)  # type: ignore[arg-type]
    assert graph.fenced_writes == 0
    assert graph.doc[ATTEMPTS] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "rearmed"),
    [("COMPLETED", True), ("EMPTY", True), ("FAILED", False), ("QUEUED", False), ("IN_PROGRESS", False), (None, False)],
)
async def test_only_a_record_that_finished_indexing_is_rearmed(status: str | None, rearmed: bool) -> None:
    graph = FakeGraph(_swept_clear_doc(indexingStatus=status))
    assert await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW) is rearmed  # type: ignore[arg-type]
    assert graph.doc[PENDING] is rearmed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("previous", "rearmed"),
    [(0, True), (2, True), (MAX_RECONCILE_ATTEMPTS - 2, True), (MAX_RECONCILE_ATTEMPTS - 1, False), (MAX_RECONCILE_ATTEMPTS, False)],
)
async def test_the_attempt_limit_is_exact_and_the_count_continues(previous: int, rearmed: bool) -> None:
    graph = FakeGraph(_swept_clear_doc(**{ATTEMPTS: previous}))
    assert await rearm_failed_reconcile(graph, LOGGER, "p1", lambda: NOW) is rearmed  # type: ignore[arg-type]
    if rearmed:
        assert graph.doc[ATTEMPTS] == previous + 1
        assert graph.doc[DUE_AT] == NOW + DUPLICATE_RECONCILE_GRACE_MS * 2 ** (previous + 1)
    else:
        assert graph.doc[PENDING] is False
        assert graph.doc[ATTEMPTS] == previous


@pytest.mark.asyncio
async def test_a_successful_reconcile_leaves_the_marker_cleared_and_writes_nothing_more(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = FakeGraph(_swept_clear_doc())
    monkeypatch.setattr(DuplicateReconciler, "reconcile", AsyncMock(return_value=True))
    await QueuedCopies(graph, None, LOGGER)._reconcile("p1", "vr1")  # type: ignore[arg-type]
    assert graph.doc[PENDING] is False
    assert graph.fenced_writes == 0
    assert graph.doc[ATTEMPTS] == 0
