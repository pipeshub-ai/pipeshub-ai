"""Unit tests for DeletedWorkItems: removal only on GitLab's own 404."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from app.connectors.sources.gitlab.deleted_work_items import ISSUES, DeletedWorkItems
from app.sources.client.gitlab.gitlab import GitLabResponse

from .conftest import make_mock_connector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PROJECT = 42


def _stored(*iids: int) -> list[dict]:
    return [{"id": f"rec-{iid}", "externalRecordId": str(1000 + iid), "recordType": "TICKET",
             "webUrl": f"https://gitlab.com/acme/web/-/issues/{iid}"} for iid in iids]


def _setup(stored: list[dict], total: int | None, present: list[int], lookups: dict[int, GitLabResponse],
           state: dict | None = None) -> tuple[MagicMock, DeletedWorkItems]:
    c = make_mock_connector()
    tx = MagicMock()
    tx.get_nodes_by_filters = AsyncMock(side_effect=lambda collection, filters, **_: (
        stored if filters["externalGroupId"].endswith("-work-items") and "confidential" not in filters["externalGroupId"]
        else []))

    @asynccontextmanager
    async def transaction() -> AsyncIterator[MagicMock]:
        yield tx

    c.data_store_provider = MagicMock(transaction=transaction)
    c.record_sync_point.read_sync_point = AsyncMock(return_value=state)

    async def ds_call(method, **kw) -> GitLabResponse:
        if kw.get("iterator"):
            return GitLabResponse(success=True, data=SimpleNamespace(total=total))
        if kw.get("get_all"):
            return GitLabResponse(success=True, data=[SimpleNamespace(iid=i) for i in present])
        return lookups[kw["issue_iid"]]

    c.runtime.ds_call = AsyncMock(side_effect=ds_call)
    return c, DeletedWorkItems(c, ISSUES)


async def _reconcile(items: DeletedWorkItems, listed: list | None = None, everything: bool = False) -> None:
    await items.reconcile(PROJECT, listed=listed or [], listed_everything=everything,
                          created_after=None, created_before=None)


async def test_only_an_item_gitlab_answers_404_for_is_removed() -> None:
    gone = GitLabResponse(success=False, status_code=404, error="404")
    flaky = GitLabResponse(success=False, status_code=500, error="500")
    c, items = _setup(_stored(1, 2, 3), total=1, present=[1], lookups={2: gone, 3: flaky})
    await _reconcile(items)
    c.data_entities_processor.on_record_deleted.assert_awaited_once_with("rec-2")
    c.note_failed_read.assert_called_once()  # issue 3 could not be confirmed
    _, saved = c.record_sync_point.update_sync_point.await_args.args
    assert saved["sweep_pending"] is True  # so the next sync looks again


async def test_a_sweep_that_failed_runs_again_even_when_the_count_adds_up() -> None:
    """One issue deleted and one created while a sweep failed: the next sync lists only
    newer items, so the count matches the baseline and only the pending flag finds it."""
    gone = GitLabResponse(success=False, status_code=404, error="404")
    c, items = _setup(_stored(1, 2), total=2, present=[1], lookups={2: gone},
                      state={"total": 2, "checked_at": 0, "sweep_pending": True})
    await _reconcile(items)
    c.data_entities_processor.on_record_deleted.assert_awaited_once_with("rec-2")
    _, saved = c.record_sync_point.update_sync_point.await_args.args
    assert saved["sweep_pending"] is False


async def test_a_count_that_adds_up_sweeps_nothing() -> None:
    c, items = _setup(_stored(1, 2), total=2, present=[], lookups={},
                      state={"total": 2, "checked_at": 0})
    await _reconcile(items)
    assert c.runtime.ds_call.await_count == 1  # the count alone
    c.data_entities_processor.on_record_deleted.assert_not_called()


async def test_a_failed_count_removes_nothing() -> None:
    c, items = _setup(_stored(1), total=0, present=[], lookups={})
    c.runtime.ds_call = AsyncMock(return_value=GitLabResponse(success=False, error="503", status_code=503))
    await _reconcile(items)
    c.data_entities_processor.on_record_deleted.assert_not_called()
    c.note_failed_read.assert_called_once()


async def test_no_count_above_ten_thousand_removes_nothing() -> None:
    c, items = _setup(_stored(1), total=None, present=[], lookups={})
    await _reconcile(items)
    c.data_entities_processor.on_record_deleted.assert_not_called()


async def test_a_complete_listing_is_used_instead_of_a_second_sweep() -> None:
    gone = GitLabResponse(success=False, status_code=404, error="404")
    c, items = _setup(_stored(1, 2), total=1, present=[], lookups={2: gone})
    await _reconcile(items, listed=[SimpleNamespace(iid=1, created_at="2026-01-01T00:00:00Z")], everything=True)
    assert not any(call.kwargs.get("get_all") for call in c.runtime.ds_call.await_args_list)
    c.data_entities_processor.on_record_deleted.assert_awaited_once_with("rec-2")


async def test_attachments_filed_under_a_removed_issue_go_with_it() -> None:
    gone = GitLabResponse(success=False, status_code=404, error="404")
    c, items = _setup(_stored(2), total=0, present=[], lookups={2: gone})
    attachment = SimpleNamespace(id="file-1", record_type="FILE")
    c.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[attachment])
    await _reconcile(items)
    deleted = [call.args[0] for call in c.data_entities_processor.on_record_deleted.await_args_list]
    assert deleted == ["file-1", "rec-2"]
