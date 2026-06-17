"""Unit tests for gitlab IssuesSync.

Covers:
- fetch_issues_batched: checkpoint + filter intersection, empty result
- process_new_records: batch persist, checkpoint advancement
- _process_issue_incident_task_to_ticket: type mapping (issue/incident/task), new vs updated
- _get_issues_sync_checkpoint / _update_sync_checkpoint: read/write/exception
- _issues_indexing_enabled / _comments_indexing_enabled: filter flags
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.gitlab.issues import IssuesSync

from .conftest import make_mock_connector, failed_res

pytestmark = pytest.mark.anyio


def _make_issue(
    iid: int = 1,
    title: str = "Test Issue",
    issue_type: str = "issue",
    state: str = "opened",
    project_id: int = 42,
    web_url: str = "https://gitlab.com/ns/proj/-/issues/1",
) -> MagicMock:
    issue = MagicMock()
    issue.id = iid
    issue.iid = iid
    issue.title = title
    issue.issue_type = issue_type
    issue.state = state
    issue.project_id = project_id
    issue.web_url = web_url
    issue.description = ""
    issue.labels = []
    issue.updated_at = "2024-01-01T00:00:00Z"
    issue.created_at = "2024-01-01T00:00:00Z"
    return issue


# ===========================================================================
# fetch_issues_batched
# ===========================================================================


class TestFetchIssuesBatched:
    async def test_success_processes_all_issues(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        issues_sync = IssuesSync(c)

        issues = [_make_issue(i) for i in range(3)]
        issues_res = MagicMock(success=True, data=issues, error=None)
        c.runtime.ds_call = AsyncMock(return_value=issues_res)

        issues_sync._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        issues_sync._build_issue_records = AsyncMock(return_value=[])
        issues_sync.process_new_records = AsyncMock()

        await issues_sync.fetch_issues_batched(42)
        assert issues_sync.process_new_records.called

    async def test_api_failure_returns_early(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        issues_sync = IssuesSync(c)

        c.runtime.ds_call = AsyncMock(return_value=failed_res("API error"))
        issues_sync._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        issues_sync.process_new_records = AsyncMock()

        await issues_sync.fetch_issues_batched(42)
        issues_sync.process_new_records.assert_not_called()

    async def test_empty_result_returns_early(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        issues_sync = IssuesSync(c)

        issues_res = MagicMock(success=True, data=[], error=None)
        c.runtime.ds_call = AsyncMock(return_value=issues_res)
        issues_sync._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        issues_sync.process_new_records = AsyncMock()

        await issues_sync.fetch_issues_batched(42)
        issues_sync.process_new_records.assert_not_called()

    async def test_checkpoint_applied_as_updated_after_filter(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        issues_sync = IssuesSync(c)

        # Checkpoint at epoch 1000000 ms = 1000 s
        issues_sync._get_issues_sync_checkpoint = AsyncMock(return_value=1000000)

        issues_res = MagicMock(success=True, data=[], error=None)
        c.runtime.ds_call = AsyncMock(return_value=issues_res)
        issues_sync.process_new_records = AsyncMock()

        await issues_sync.fetch_issues_batched(42)
        call_kwargs = c.runtime.ds_call.call_args.kwargs
        assert call_kwargs.get("updated_after") is not None

    async def test_batches_respect_batch_size(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        c.batch_size = 2
        issues_sync = IssuesSync(c)

        issues = [_make_issue(i) for i in range(5)]
        issues_res = MagicMock(success=True, data=issues, error=None)
        c.runtime.ds_call = AsyncMock(return_value=issues_res)

        issues_sync._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        issues_sync._build_issue_records = AsyncMock(return_value=[])
        issues_sync.process_new_records = AsyncMock()

        await issues_sync.fetch_issues_batched(42)
        # 5 issues with batch_size=2 → 3 batches
        assert issues_sync.process_new_records.call_count == 3


# ===========================================================================
# _process_issue_incident_task_to_ticket
# ===========================================================================


class TestProcessIssueToTicket:
    async def _make_tx_context(self, existing: MagicMock | None = None) -> tuple[MagicMock, MagicMock]:
        tx_store = MagicMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return tx_store, ctx

    async def test_new_issue_is_marked_as_new(self) -> None:
        c = make_mock_connector()
        _, ctx = await self._make_tx_context(None)
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=ctx)
        issues_sync = IssuesSync(c)

        result = await issues_sync._process_issue_incident_task_to_ticket(_make_issue())
        assert result is not None
        assert result.is_new is True

    async def test_incident_type_mapping(self) -> None:
        c = make_mock_connector()
        _, ctx = await self._make_tx_context(None)
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=ctx)
        issues_sync = IssuesSync(c)

        result = await issues_sync._process_issue_incident_task_to_ticket(
            _make_issue(issue_type="incident")
        )
        assert result is not None
        assert result.record.type == "INCIDENT"

    async def test_task_type_mapping(self) -> None:
        c = make_mock_connector()
        _, ctx = await self._make_tx_context(None)
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=ctx)
        issues_sync = IssuesSync(c)

        result = await issues_sync._process_issue_incident_task_to_ticket(
            _make_issue(issue_type="task")
        )
        assert result is not None
        assert result.record.type == "TASK"

    async def test_existing_record_marked_as_updated(self) -> None:
        c = make_mock_connector()
        existing = MagicMock()
        existing.record_name = "Old Title"
        existing.id = "existing-id-1"
        _, ctx = await self._make_tx_context(existing)
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=ctx)
        issues_sync = IssuesSync(c)

        result = await issues_sync._process_issue_incident_task_to_ticket(_make_issue(title="New Title"))
        assert result is not None
        assert result.is_new is False
        assert result.is_updated is True

    async def test_exception_returns_none(self) -> None:
        c = make_mock_connector()
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(side_effect=Exception("DB error"))
        issues_sync = IssuesSync(c)

        result = await issues_sync._process_issue_incident_task_to_ticket(_make_issue())
        assert result is None


# ===========================================================================
# Checkpoints
# ===========================================================================


class TestIssueCheckpoints:
    async def test_get_checkpoint_returns_value_when_present(self) -> None:
        c = make_mock_connector()
        issues_sync = IssuesSync(c)

        c.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 9999000}
        )
        result = await issues_sync._get_issues_sync_checkpoint(1)
        assert result == 9999000

    async def test_get_checkpoint_returns_none_on_missing(self) -> None:
        c = make_mock_connector()
        issues_sync = IssuesSync(c)

        c.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await issues_sync._get_issues_sync_checkpoint(1)
        assert result is None

    async def test_get_checkpoint_returns_none_on_exception(self) -> None:
        c = make_mock_connector()
        issues_sync = IssuesSync(c)

        c.record_sync_point.read_sync_point = AsyncMock(side_effect=Exception("error"))
        result = await issues_sync._get_issues_sync_checkpoint(1)
        assert result is None

    async def test_update_checkpoint_calls_update_sync_point(self) -> None:
        c = make_mock_connector()
        issues_sync = IssuesSync(c)

        await issues_sync._update_sync_checkpoint("42-work-items", 12345)
        c.record_sync_point.update_sync_point.assert_called_once()


# ===========================================================================
# Indexing filter flags
# ===========================================================================


class TestIssueIndexingFilters:
    def test_issues_enabled_by_default_when_no_filters(self) -> None:
        c = make_mock_connector()
        c.indexing_filters = None
        issues_sync = IssuesSync(c)
        assert issues_sync._issues_indexing_enabled() is True

    def test_comments_enabled_by_default_when_no_filters(self) -> None:
        c = make_mock_connector()
        c.indexing_filters = None
        issues_sync = IssuesSync(c)
        assert issues_sync._comments_indexing_enabled() is True

    def test_issues_disabled_by_filter(self) -> None:
        c = make_mock_connector()
        from app.connectors.core.registry.filters import IndexingFilterKey
        filters_mock = MagicMock()
        filters_mock.is_enabled = MagicMock(return_value=False)
        c.indexing_filters = filters_mock
        issues_sync = IssuesSync(c)
        assert issues_sync._issues_indexing_enabled() is False
