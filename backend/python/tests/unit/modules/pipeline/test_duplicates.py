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
async def test_copies_of_a_record_that_waited_for_first_use_are_reconciled() -> None:
    graph = _graph({"indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED", "virtualRecordId": "vr-1"}, promoted=0)
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1", "copy-1"])
    with patch("app.modules.pipeline.duplicates.DuplicateReconciler") as reconciler:
        reconciler.return_value.reconcile = AsyncMock(return_value=True)
        await QueuedCopies(graph, None, logging.getLogger("t")).release(["rec-1"])
    reconciler.return_value.reconcile.assert_awaited_once_with("rec-1", "vr-1")
    # Pending before the copies change, so the recovery sweep finishes a failed reconcile.
    key, _collection, fields = graph.update_node.await_args_list[0].args
    assert key == "rec-1" and fields["duplicateReconcilePending"] is True


@pytest.mark.asyncio
async def test_copies_that_cannot_be_listed_are_left_to_the_sweep_and_the_delivery_fails() -> None:
    graph = _graph({"indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED", "virtualRecordId": "vr-1"}, promoted=0)
    graph.get_records_by_virtual_record_id = AsyncMock(side_effect=ConnectionError("graph unavailable"))
    with pytest.raises(ConnectionError):
        await QueuedCopies(graph, None, logging.getLogger("t")).release(["rec-1"])
    key, _collection, fields = graph.update_node.await_args.args
    assert key == "rec-1" and fields["duplicateReconcilePending"] is True


@pytest.mark.asyncio
async def test_a_record_without_copies_reconciles_nothing() -> None:
    graph = _graph({"indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED", "virtualRecordId": "vr-1"}, promoted=0)
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    with patch("app.modules.pipeline.duplicates.DuplicateReconciler") as reconciler:
        await QueuedCopies(graph, None, logging.getLogger("t")).release(["rec-1"])
    reconciler.assert_not_called()


@pytest.mark.asyncio
async def test_promoted_copies_ask_for_their_code_edge_build() -> None:
    """A promoted copy can be its repo's last file; the stage path must ask, as the handler's does."""
    graph = _graph({"indexingStatus": "COMPLETED", "virtualRecordId": "vr-1"}, promoted=1)
    copies = QueuedCopies(graph, None, logging.getLogger("t"))
    on_promoted = AsyncMock()
    copies.bind_copies_promoted(on_promoted)
    with patch("app.modules.pipeline.duplicates.DuplicateReconciler") as reconciler, patch(
        "app.modules.pipeline.duplicates.notify_record_indexed", AsyncMock()
    ):
        reconciler.return_value.reconcile = AsyncMock(return_value=True)
        await copies.release(["rec-1"])
    on_promoted.assert_awaited_once_with("rec-1", "vr-1")


@pytest.mark.asyncio
async def test_nothing_promoted_asks_for_no_code_edge_build() -> None:
    graph = _graph({"indexingStatus": "COMPLETED", "virtualRecordId": "vr-1"}, promoted=0)
    copies = QueuedCopies(graph, None, logging.getLogger("t"))
    on_promoted = AsyncMock()
    copies.bind_copies_promoted(on_promoted)
    with patch("app.modules.pipeline.duplicates.notify_record_indexed", AsyncMock()):
        await copies.release(["rec-1"])
    on_promoted.assert_not_awaited()
