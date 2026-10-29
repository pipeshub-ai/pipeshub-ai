"""QueuedCopies: a record's QUEUED md5 copies are released when its enrichment ends."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.pipeline.duplicates import QueuedCopies, QueuedCopiesNotReleasedError


def _graph(record: dict[str, object] | None, promoted: int) -> AsyncMock:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=record)
    graph.update_queued_duplicates_status = AsyncMock(return_value=promoted)
    graph.update_node = AsyncMock(return_value=True)
    return graph


@pytest.mark.asyncio
async def test_promoted_copies_are_reconciled_and_the_flag_cleared() -> None:
    graph = _graph({"indexingStatus": "COMPLETED", "virtualRecordId": "vr-1", "orgId": "org-1"}, promoted=2)
    copies = QueuedCopies(graph, None, logging.getLogger("t"))
    with patch("app.modules.pipeline.duplicates.DuplicateReconciler") as reconciler, patch(
        "app.modules.pipeline.duplicates.notify_record_indexed", AsyncMock()
    ) as notify:
        reconciler.return_value.reconcile = AsyncMock(return_value=True)
        await copies.release(["rec-1"])
    graph.update_queued_duplicates_status.assert_awaited_once_with("rec-1", "COMPLETED", "vr-1")
    graph.update_node.assert_awaited_once()
    notify.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_promotion_that_did_not_run_raises_for_a_retry() -> None:
    graph = _graph({"indexingStatus": "COMPLETED", "virtualRecordId": "vr-1"}, promoted=-1)
    with pytest.raises(QueuedCopiesNotReleasedError):
        await QueuedCopies(graph, None, logging.getLogger("t")).release(["rec-1"])


@pytest.mark.asyncio
async def test_a_record_not_indexed_releases_nothing() -> None:
    graph = _graph({"indexingStatus": "IN_PROGRESS"}, promoted=0)
    await QueuedCopies(graph, None, logging.getLogger("t")).release(["rec-1"])
    graph.update_queued_duplicates_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_unbound_membership_sync_leaves_the_reconcile_pending() -> None:
    copies = QueuedCopies(AsyncMock(), None, logging.getLogger("t"))
    with pytest.raises(RuntimeError):
        await copies._sync_vector_membership("vr-1")


@pytest.mark.asyncio
async def test_copies_left_waiting_for_first_use_take_the_outcome_and_the_taxonomy() -> None:
    primary = {"indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED", "virtualRecordId": "vr-1", "orgId": "org-1"}
    docs = {
        "rec-1": primary,
        "copy-waiting": {"indexingStatus": "COMPLETED", "extractionStatus": "NOT_STARTED", "orgId": "org-1"},
        "copy-done": {"indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED", "orgId": "org-1"},
        "other-org": {"indexingStatus": "COMPLETED", "extractionStatus": "NOT_STARTED", "orgId": "org-2"},
    }
    graph = _graph(primary, promoted=0)
    graph.get_document = AsyncMock(side_effect=lambda key, _collection, **_kw: docs.get(key))
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=list(docs))
    copies = QueuedCopies(graph, None, logging.getLogger("t"))
    with patch("app.modules.pipeline.duplicates.DuplicateReconciler") as reconciler:
        reconciler.return_value.reconcile = AsyncMock(return_value=True)
        await copies.release(["rec-1"])

    graph.update_node.assert_any_await("copy-waiting", "records", {"extractionStatus": "COMPLETED"})
    finalized = [c.args[0] for c in graph.update_node.await_args_list if c.args[2] == {"extractionStatus": "COMPLETED"}]
    assert finalized == ["copy-waiting"]
    reconciler.return_value.reconcile.assert_awaited_once_with("rec-1", "vr-1")


@pytest.mark.asyncio
async def test_a_primary_still_waiting_finalizes_no_copy() -> None:
    graph = _graph(
        {"indexingStatus": "COMPLETED", "extractionStatus": "NOT_STARTED", "virtualRecordId": "vr-1", "orgId": "org-1"},
        promoted=0,
    )
    await QueuedCopies(graph, None, logging.getLogger("t")).release(["rec-1"])
    graph.get_records_by_virtual_record_id.assert_not_awaited()
