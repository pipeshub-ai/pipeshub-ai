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

    def test_comments_disabled_by_filter(self) -> None:
        c = make_mock_connector()
        from app.connectors.core.registry.filters import IndexingFilterKey
        filters = MagicMock()
        filters.is_enabled = MagicMock(side_effect=lambda k: k != IndexingFilterKey.COMMENTS)
        c.indexing_filters = filters
        issues_sync = IssuesSync(c)
        assert issues_sync._comments_indexing_enabled() is False


# ===========================================================================
# fetch_issues_batched — filter_after merge
# ===========================================================================


class TestFetchIssuesBatchedFilterAfter:
    async def test_filter_after_later_than_checkpoint_wins(self) -> None:
        """filter_after that is later than stored checkpoint is used."""
        from datetime import datetime, timezone
        c = make_mock_connector()
        c.data_source = MagicMock()
        issues_sync = IssuesSync(c)

        issues_sync._get_issues_sync_checkpoint = AsyncMock(return_value=1000)
        later_dt = datetime(2025, 6, 1, tzinfo=timezone.utc)
        c.datetime_range_from_sync_filter = MagicMock(return_value=(later_dt, None))

        empty_res = MagicMock(success=True, data=[], error=None)
        c.runtime.ds_call = AsyncMock(return_value=empty_res)
        issues_sync.process_new_records = AsyncMock()

        await issues_sync.fetch_issues_batched(42)
        call_kwargs = c.runtime.ds_call.call_args.kwargs
        assert call_kwargs["updated_after"] == later_dt


# ===========================================================================
# _build_issue_records — attachments and indexing paths
# ===========================================================================


class TestBuildIssueRecords:
    async def _make_tx(self, existing=None):
        tx_store = MagicMock()
        tx_store.get_record_by_external_id = AsyncMock(return_value=existing)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=tx_store)
        ctx.__aexit__ = AsyncMock(return_value=None)
        return ctx

    async def test_description_attachments_processed(self) -> None:
        """Issue with description upload triggers make_file_records_from_list."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=await self._make_tx())

        issue = _make_issue(iid=1)
        issue.description = "![img](/uploads/abc/file.png)"

        from app.connectors.sources.gitlab.models import RecordUpdate
        ru = MagicMock(spec=RecordUpdate)
        ru.record = MagicMock()
        ru.record.indexing_status = None
        ru.new_permissions = []

        attachment = MagicMock()
        file_ru = MagicMock(spec=RecordUpdate)
        file_ru.record = MagicMock()
        file_ru.record.indexing_status = None

        c.attachments = MagicMock()
        c.attachments.parse_gitlab_uploads = AsyncMock(return_value=([attachment], "cleaned"))
        c.attachments.make_file_records_from_list = AsyncMock(return_value=[file_ru])
        c.attachments.make_files_records_from_notes = AsyncMock(return_value=[])

        issues_sync = IssuesSync(c)
        issues_sync._process_issue_incident_task_to_ticket = AsyncMock(return_value=ru)
        issues_sync._issues_indexing_enabled = MagicMock(return_value=True)
        issues_sync._comments_indexing_enabled = MagicMock(return_value=True)

        result = await issues_sync._build_issue_records([issue])
        assert len(result) == 2
        c.attachments.make_file_records_from_list.assert_called_once()

    async def test_note_attachments_included(self) -> None:
        """Note attachments returned by make_files_records_from_notes are included."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=await self._make_tx())

        issue = _make_issue(iid=2)
        issue.description = ""

        from app.connectors.sources.gitlab.models import RecordUpdate
        ru = MagicMock(spec=RecordUpdate)
        ru.record = MagicMock()
        ru.record.indexing_status = None
        ru.new_permissions = []

        note_ru = MagicMock(spec=RecordUpdate)
        note_ru.record = MagicMock()
        note_ru.record.indexing_status = None

        c.attachments = MagicMock()
        c.attachments.parse_gitlab_uploads = AsyncMock(return_value=([], ""))
        c.attachments.make_files_records_from_notes = AsyncMock(return_value=[note_ru])

        issues_sync = IssuesSync(c)
        issues_sync._process_issue_incident_task_to_ticket = AsyncMock(return_value=ru)
        issues_sync._issues_indexing_enabled = MagicMock(return_value=True)
        issues_sync._comments_indexing_enabled = MagicMock(return_value=True)

        result = await issues_sync._build_issue_records([issue])
        assert len(result) == 2
        c.attachments.make_files_records_from_notes.assert_called_once()

    async def test_indexing_disabled_stamps_off(self) -> None:
        """When issues indexing is disabled, indexing_status is AUTO_INDEX_OFF."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=await self._make_tx())

        issue = _make_issue(iid=3)
        issue.description = ""

        from app.connectors.sources.gitlab.models import RecordUpdate
        from app.config.constants.arangodb import ProgressStatus
        ru = MagicMock(spec=RecordUpdate)
        ru.record = MagicMock()
        ru.record.indexing_status = None

        c.attachments = MagicMock()
        c.attachments.parse_gitlab_uploads = AsyncMock(return_value=([], ""))
        c.attachments.make_files_records_from_notes = AsyncMock(return_value=[])

        issues_sync = IssuesSync(c)
        issues_sync._process_issue_incident_task_to_ticket = AsyncMock(return_value=ru)
        issues_sync._issues_indexing_enabled = MagicMock(return_value=False)
        issues_sync._comments_indexing_enabled = MagicMock(return_value=True)

        await issues_sync._build_issue_records([issue])
        assert ru.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    async def test_existing_record_same_title_no_metadata_change(self) -> None:
        """Existing record with same title: metadata_changed is False."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        existing = MagicMock()
        existing.record_name = "Test Issue"
        existing.id = "existing-1"
        c.data_store_provider = MagicMock()
        c.data_store_provider.transaction = MagicMock(return_value=await self._make_tx(existing))

        issue = _make_issue(iid=1, title="Test Issue")
        issue.description = ""

        c.attachments = MagicMock()
        c.attachments.parse_gitlab_uploads = AsyncMock(return_value=([], ""))
        c.attachments.make_files_records_from_notes = AsyncMock(return_value=[])

        issues_sync = IssuesSync(c)
        issues_sync._issues_indexing_enabled = MagicMock(return_value=True)
        issues_sync._comments_indexing_enabled = MagicMock(return_value=True)

        result = await issues_sync._build_issue_records([issue])
        assert len(result) == 1
        assert result[0].metadata_changed is False


# ===========================================================================
# process_new_records
# ===========================================================================


class TestProcessNewRecords:
    async def test_happy_path_calls_on_new_records(self) -> None:
        """Batch of records triggers on_new_records and checkpoint update."""
        c = make_mock_connector()
        c.batch_size = 10
        c.data_entities_processor.on_new_records = AsyncMock()

        from app.connectors.sources.gitlab.models import RecordUpdate
        from app.models.entities import RecordType
        ru = MagicMock(spec=RecordUpdate)
        ru.record = MagicMock()
        ru.record.record_type = RecordType.TICKET.value
        ru.record.source_updated_at = 1000
        ru.record.external_record_group_id = "42-work-items"
        ru.new_permissions = []

        issues_sync = IssuesSync(c)
        issues_sync._update_sync_checkpoint = AsyncMock()

        await issues_sync.process_new_records([ru])

        c.data_entities_processor.on_new_records.assert_called_once()
        issues_sync._update_sync_checkpoint.assert_called_once()

    async def test_on_new_records_failure_logged(self) -> None:
        """on_new_records failure is caught and logged, does not propagate."""
        c = make_mock_connector()
        c.batch_size = 10
        c.data_entities_processor.on_new_records = AsyncMock(side_effect=Exception("DB error"))

        from app.connectors.sources.gitlab.models import RecordUpdate
        ru = MagicMock(spec=RecordUpdate)
        ru.record = MagicMock()
        ru.record.record_type = "TICKET"
        ru.record.source_updated_at = 1000
        ru.record.external_record_group_id = "42-work-items"
        ru.new_permissions = []

        issues_sync = IssuesSync(c)
        # Should not raise
        await issues_sync.process_new_records([ru])
        c.logger.error.assert_called()


# ===========================================================================
# build_ticket_blocks
# ===========================================================================


class TestBuildTicketBlocks:
    async def test_happy_path_returns_bytes(self) -> None:
        """build_ticket_blocks returns serialised BlocksContainer bytes."""
        c = make_mock_connector()
        c._gitlab_base_url = "https://gitlab.com"

        record = MagicMock()
        record.weburl = "https://gitlab.com/ns/proj/-/issues/1"
        record.external_record_group_id = "42-work-items"
        record.external_record_id = "1"
        record.record_name = "Test Issue"

        issue = _make_issue(iid=1)
        issue.description = ""
        issue.updated_at = "2024-01-01T00:00:00Z"
        issue.title = "Test Issue"
        issue_res = MagicMock(success=True, data=issue, error=None)

        c.runtime.ds_call = AsyncMock(return_value=issue_res)

        c.attachments = MagicMock()
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="")
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))

        c.comments = MagicMock()
        c.comments.build_comment_blocks = AsyncMock(return_value=([], []))

        issues_sync = IssuesSync(c)
        issues_sync._comments_indexing_enabled = MagicMock(return_value=False)
        issues_sync.process_new_records = AsyncMock()

        result = await issues_sync.build_ticket_blocks(record)
        assert isinstance(result, bytes)
        assert b"block_groups" in result

    async def test_with_comments_appended(self) -> None:
        """When comments indexing enabled, comment blocks are included."""
        c = make_mock_connector()
        c._gitlab_base_url = "https://gitlab.com"

        record = MagicMock()
        record.weburl = "https://gitlab.com/ns/proj/-/issues/2"
        record.external_record_group_id = "42-work-items"
        record.external_record_id = "2"
        record.record_name = "Test Issue 2"

        issue = _make_issue(iid=2)
        issue.description = ""
        issue.updated_at = "2024-01-01T00:00:00Z"
        issue.title = "Test Issue 2"
        issue_res = MagicMock(success=True, data=issue, error=None)

        c.runtime.ds_call = AsyncMock(return_value=issue_res)

        c.attachments = MagicMock()
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="")
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))

        from app.models.blocks import BlockGroup, GroupType
        comment_bg = BlockGroup(index=1, name="comment", type=GroupType.TEXT_SECTION.value)
        c.comments = MagicMock()
        c.comments.build_comment_blocks = AsyncMock(return_value=([comment_bg], []))

        issues_sync = IssuesSync(c)
        issues_sync._comments_indexing_enabled = MagicMock(return_value=True)
        issues_sync.process_new_records = AsyncMock()

        result = await issues_sync.build_ticket_blocks(record)
        assert isinstance(result, bytes)
        import json
        data = json.loads(result)
        assert len(data["block_groups"]) == 2  # description + comment
