"""Unit tests for gitlab MergeRequestsSync.

Covers:
- fetch_prs_batched: checkpoint + filter, empty result, batch processing
- gitlab_project_id_and_iid_from_record: URL parsing edge cases
- check_and_fetch_updated_record_for_reindex: revision unchanged skip, revision changed fetch
- _merge_requests_indexing_enabled / _comments_indexing_enabled: filter flags
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.gitlab.merge_requests import MergeRequestsSync

from .conftest import make_mock_connector, failed_res

pytestmark = pytest.mark.anyio


def _make_mr(
    iid: int = 1,
    title: str = "My MR",
    state: str = "opened",
    project_id: int = 42,
    web_url: str = "https://gitlab.com/ns/proj/-/merge_requests/1",
) -> MagicMock:
    mr = MagicMock()
    mr.id = iid
    mr.iid = iid
    mr.title = title
    mr.state = state
    mr.project_id = project_id
    mr.web_url = web_url
    mr.description = ""
    mr.labels = []
    mr.updated_at = "2024-01-01T00:00:00Z"
    mr.created_at = "2024-01-01T00:00:00Z"
    mr.merge_commit_sha = None
    mr.source_branch = "feature"
    mr.target_branch = "main"
    return mr


def _make_record(
    record_type: str = "PULL_REQUEST",
    weburl: str = "https://gitlab.com/ns/proj/-/merge_requests/1",
    external_group_id: str = "42-merge-requests",
    external_revision_id: str = "1000",
) -> MagicMock:
    r = MagicMock()
    r.id = "rec-1"
    r.record_type = record_type
    r.weburl = weburl
    r.external_record_group_id = external_group_id
    r.external_revision_id = external_revision_id
    r.record_name = "My MR"
    return r


# ===========================================================================
# gitlab_project_id_and_iid_from_record
# ===========================================================================


class TestGitlabProjectIdAndIidFromRecord:
    def test_valid_mr_url_parses_correctly(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)

        record = _make_record(weburl="https://gitlab.com/ns/proj/-/merge_requests/7")
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is not None
        project_id, iid = result
        assert iid == 7

    def test_valid_issue_url_parses_correctly(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)

        record = _make_record(weburl="https://gitlab.com/ns/proj/-/issues/12")
        record.external_record_group_id = "42-work-items"
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is not None
        _, iid = result
        assert iid == 12

    def test_missing_weburl_returns_none(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)

        record = _make_record(weburl="")
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is None

    def test_missing_external_group_id_returns_none(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)

        record = _make_record(external_group_id="")
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is None

    def test_url_without_dash_segment_returns_none(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)

        record = _make_record(weburl="https://gitlab.com/ns/proj/merge_requests/1")
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is None

    def test_work_items_url_parses_correctly(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)

        record = _make_record(weburl="https://gitlab.com/ns/proj/-/work_items/99")
        record.external_record_group_id = "42-work-items"
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is not None
        _, iid = result
        assert iid == 99

    def test_unknown_resource_type_returns_none(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)

        record = _make_record(weburl="https://gitlab.com/ns/proj/-/commits/abc123")
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is None

    def test_non_numeric_iid_returns_none(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)

        record = _make_record(weburl="https://gitlab.com/ns/proj/-/merge_requests/abc")
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is None


# ===========================================================================
# fetch_prs_batched
# ===========================================================================


class TestFetchPrsBatched:
    async def test_api_failure_returns_early(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        mrs = MergeRequestsSync(c)
        mrs._get_mr_sync_checkpoint = AsyncMock(return_value=None)

        c.runtime.ds_call = AsyncMock(return_value=failed_res("API error"))
        c.issues = MagicMock()
        c.issues.process_new_records = AsyncMock()

        await mrs.fetch_prs_batched(42)
        c.issues.process_new_records.assert_not_called()

    async def test_empty_result_returns_early(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        mrs = MergeRequestsSync(c)
        mrs._get_mr_sync_checkpoint = AsyncMock(return_value=None)

        empty_res = MagicMock(success=True, data=[], error=None)
        c.runtime.ds_call = AsyncMock(return_value=empty_res)
        c.issues = MagicMock()
        c.issues.process_new_records = AsyncMock()

        await mrs.fetch_prs_batched(42)
        c.issues.process_new_records.assert_not_called()

    async def test_processes_mrs_in_batches(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        c.batch_size = 2
        mrs = MergeRequestsSync(c)
        mrs._get_mr_sync_checkpoint = AsyncMock(return_value=None)

        mr_list = [_make_mr(i) for i in range(5)]
        mr_res = MagicMock(success=True, data=mr_list, error=None)
        c.runtime.ds_call = AsyncMock(return_value=mr_res)

        mrs._build_pr_records = AsyncMock(return_value=[])
        c.issues = MagicMock()
        c.issues.process_new_records = AsyncMock()

        await mrs.fetch_prs_batched(42)
        # 5 MRs / batch_size=2 → 3 calls
        assert c.issues.process_new_records.call_count == 3


# ===========================================================================
# check_and_fetch_updated_record_for_reindex
# ===========================================================================


class TestCheckAndFetchUpdatedRecord:
    async def test_revision_unchanged_returns_none(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        mrs = MergeRequestsSync(c)
        mrs.gitlab_project_id_and_iid_from_record = MagicMock(return_value=("42", 1))

        mr = _make_mr()
        mr_res = MagicMock(success=True, data=mr, error=None)
        c.runtime.ds_call = AsyncMock(return_value=mr_res)

        # Same revision
        record = _make_record(record_type="PULL_REQUEST")
        # patch parse_timestamp to return same value as stored
        with patch("app.connectors.sources.gitlab.merge_requests.parse_timestamp", return_value=1000):
            record.external_revision_id = "1000"
            result = await mrs.check_and_fetch_updated_record_for_reindex(record)

        assert result is None

    async def test_revision_changed_returns_updated_record(self) -> None:
        c = make_mock_connector()
        c.data_source = MagicMock()
        mrs = MergeRequestsSync(c)
        mrs.gitlab_project_id_and_iid_from_record = MagicMock(return_value=("42", 1))

        mr = _make_mr()
        mr_res = MagicMock(success=True, data=mr, error=None)
        c.runtime.ds_call = AsyncMock(return_value=mr_res)

        ru = MagicMock()
        ru.record = MagicMock()
        ru.new_permissions = []
        mrs._process_mr_to_pull_request = AsyncMock(return_value=ru)

        record = _make_record(record_type="PULL_REQUEST")
        with patch("app.connectors.sources.gitlab.merge_requests.parse_timestamp", return_value=9999):
            record.external_revision_id = "0"
            result = await mrs.check_and_fetch_updated_record_for_reindex(record)

        assert result is not None

    async def test_missing_parsed_fields_returns_none(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)
        mrs.gitlab_project_id_and_iid_from_record = MagicMock(return_value=None)

        record = _make_record()
        result = await mrs.check_and_fetch_updated_record_for_reindex(record)
        assert result is None

    async def test_unsupported_record_type_returns_none(self) -> None:
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)
        mrs.gitlab_project_id_and_iid_from_record = MagicMock(return_value=("42", 1))

        record = _make_record(record_type="CODE_FILE")
        result = await mrs.check_and_fetch_updated_record_for_reindex(record)
        assert result is None


# ===========================================================================
# Indexing filter flags
# ===========================================================================


class TestMRIndexingFilters:
    def test_merge_requests_enabled_by_default(self) -> None:
        c = make_mock_connector()
        c.indexing_filters = None
        mrs = MergeRequestsSync(c)
        assert mrs._merge_requests_indexing_enabled() is True

    def test_comments_enabled_by_default(self) -> None:
        c = make_mock_connector()
        c.indexing_filters = None
        mrs = MergeRequestsSync(c)
        assert mrs._comments_indexing_enabled() is True

    def test_merge_requests_disabled_by_filter(self) -> None:
        c = make_mock_connector()
        from app.connectors.core.registry.filters import IndexingFilterKey
        filters = MagicMock()
        filters.is_enabled = MagicMock(side_effect=lambda k: k != IndexingFilterKey.MERGE_REQUESTS)
        c.indexing_filters = filters
        mrs = MergeRequestsSync(c)
        assert mrs._merge_requests_indexing_enabled() is False

    def test_comments_disabled_by_filter(self) -> None:
        c = make_mock_connector()
        from app.connectors.core.registry.filters import IndexingFilterKey
        filters = MagicMock()
        filters.is_enabled = MagicMock(side_effect=lambda k: k != IndexingFilterKey.COMMENTS)
        c.indexing_filters = filters
        mrs = MergeRequestsSync(c)
        assert mrs._comments_indexing_enabled() is False


# ===========================================================================
# fetch_prs_batched — filter_after merge
# ===========================================================================


class TestFetchPrsBatchedFilterAfter:
    async def test_filter_after_overrides_earlier_checkpoint(self) -> None:
        """filter_after that is later than checkpoint wins."""
        from datetime import datetime, timezone
        c = make_mock_connector()
        c.data_source = MagicMock()
        mrs = MergeRequestsSync(c)

        # Checkpoint 1000ms epoch
        mrs._get_mr_sync_checkpoint = AsyncMock(return_value=1000)
        # filter_after is a later datetime
        later_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
        c.datetime_range_from_sync_filter = MagicMock(return_value=(later_dt, None))

        empty_res = MagicMock(success=True, data=[], error=None)
        c.runtime.ds_call = AsyncMock(return_value=empty_res)
        c.issues = MagicMock()
        c.issues.process_new_records = AsyncMock()

        await mrs.fetch_prs_batched(42)

        # Verify ds_call was called with a since_dt equal to later_dt
        call_kwargs = c.runtime.ds_call.call_args[1]
        assert call_kwargs["updated_after"] == later_dt


# ===========================================================================
# _build_pr_records — attachment and indexing paths
# ===========================================================================


class TestBuildPrRecords:
    async def test_description_attachments_processed(self) -> None:
        """MR with upload in description triggers make_file_records_from_list."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        c.data_store_provider = MagicMock()

        mr = _make_mr(iid=1)
        mr.description = "See ![img](/uploads/abc/file.png)"
        mr.assignees = []
        mr.reviewers = []
        mr.merged_by = None
        mr.labels = []

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
        c.attachments.make_files_records_from_notes_mr = AsyncMock(return_value=[])

        mrs = MergeRequestsSync(c)
        mrs._process_mr_to_pull_request = AsyncMock(return_value=ru)
        mrs._merge_requests_indexing_enabled = MagicMock(return_value=True)
        mrs._comments_indexing_enabled = MagicMock(return_value=True)

        result = await mrs._build_pr_records([mr])
        assert len(result) == 2  # original + attachment
        c.attachments.make_file_records_from_list.assert_called_once()

    async def test_indexing_disabled_stamps_off(self) -> None:
        """When MR indexing is disabled, indexing_status is set to AUTO_INDEX_OFF."""
        c = make_mock_connector()
        c.data_source = MagicMock()

        mr = _make_mr(iid=2)
        mr.description = ""
        mr.assignees = []
        mr.reviewers = []
        mr.merged_by = None
        mr.labels = []

        from app.connectors.sources.gitlab.models import RecordUpdate
        from app.config.constants.arangodb import ProgressStatus
        ru = MagicMock(spec=RecordUpdate)
        ru.record = MagicMock()
        ru.record.indexing_status = None
        ru.new_permissions = []

        c.attachments = MagicMock()
        c.attachments.parse_gitlab_uploads = AsyncMock(return_value=([], mr.description))
        c.attachments.make_files_records_from_notes_mr = AsyncMock(return_value=[])

        mrs = MergeRequestsSync(c)
        mrs._process_mr_to_pull_request = AsyncMock(return_value=ru)
        mrs._merge_requests_indexing_enabled = MagicMock(return_value=False)
        mrs._comments_indexing_enabled = MagicMock(return_value=True)

        await mrs._build_pr_records([mr])
        assert ru.record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


# ===========================================================================
# build_pull_request_blocks
# ===========================================================================


class TestBuildPullRequestBlocks:
    async def test_happy_path_returns_bytes(self) -> None:
        """build_pull_request_blocks returns serialised BlocksContainer bytes."""
        c = make_mock_connector()
        c._gitlab_base_url = "https://gitlab.com"

        record = _make_record(
            record_type="PULL_REQUEST",
            weburl="https://gitlab.com/ns/proj/-/merge_requests/1",
            external_group_id="42-merge-requests",
        )
        record.record_name = "Test MR"

        mr = _make_mr(iid=1)
        mr.description = ""
        mr.updated_at = "2024-01-01T00:00:00Z"
        mr.title = "Test MR"
        mr_res = MagicMock(success=True, data=mr, error=None)

        commits_res = MagicMock(success=True, data=[], error=None)

        async def ds_call_side(fn, **kwargs):
            if "get_merge_request" in str(fn):
                return mr_res
            if "list_merge_requests_commits" in str(fn):
                return commits_res
            return MagicMock(success=True, data=[], error=None)

        c.runtime.ds_call = AsyncMock(side_effect=ds_call_side)

        c.attachments = MagicMock()
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="")
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))

        c.comments = MagicMock()
        c.comments.build_merge_request_comment_blocks = AsyncMock(return_value=([], []))

        c.issues = MagicMock()
        c.issues.process_new_records = AsyncMock()

        mrs = MergeRequestsSync(c)
        mrs._comments_indexing_enabled = MagicMock(return_value=True)

        result = await mrs.build_pull_request_blocks(record)
        assert isinstance(result, bytes)
        assert b"block_groups" in result

    async def test_commits_appended_as_blocks(self) -> None:
        """Commits in MR are included as COMMIT blocks."""
        c = make_mock_connector()
        c._gitlab_base_url = "https://gitlab.com"

        record = _make_record(
            record_type="PULL_REQUEST",
            weburl="https://gitlab.com/ns/proj/-/merge_requests/1",
            external_group_id="42-merge-requests",
        )
        record.record_name = "Test MR"

        mr = _make_mr(iid=1)
        mr.description = ""
        mr.updated_at = "2024-01-01T00:00:00Z"
        mr.title = "Test MR"
        mr_res = MagicMock(success=True, data=mr, error=None)

        commit = MagicMock()
        commit.id = "abc123"
        commit.message = "Fix bug"
        commit.title = "Fix bug"
        commit.web_url = "https://gitlab.com/ns/proj/-/commit/abc123"
        commit.committed_date = "2024-01-01T00:00:00Z"
        commits_res = MagicMock(success=True, data=[commit], error=None)

        async def ds_call_side(fn, **kwargs):
            if "get_merge_request" in str(fn):
                return mr_res
            if "list_merge_requests_commits" in str(fn):
                return commits_res
            return MagicMock(success=True, data=[], error=None)

        c.runtime.ds_call = AsyncMock(side_effect=ds_call_side)

        c.attachments = MagicMock()
        c.attachments.embed_images_as_base64 = AsyncMock(return_value="")
        c.attachments.make_child_records_of_attachments = AsyncMock(return_value=([], []))

        c.comments = MagicMock()
        c.comments.build_merge_request_comment_blocks = AsyncMock(return_value=([], []))

        c.issues = MagicMock()
        c.issues.process_new_records = AsyncMock()

        mrs = MergeRequestsSync(c)
        mrs._comments_indexing_enabled = MagicMock(return_value=False)

        result = await mrs.build_pull_request_blocks(record)
        assert isinstance(result, bytes)
        # Block group for commits should be present
        import json
        data = json.loads(result)
        blocks = data.get("blocks", [])
        assert any(b.get("sub_type") == "commit" for b in blocks)


# ===========================================================================
# gitlab_project_id_and_iid_from_record — additional parse failure paths
# ===========================================================================


class TestGitlabProjectIdAndIidFromRecordEdgeCases:
    def test_empty_project_part_returns_none(self) -> None:
        """external_group_id that produces empty project part returns None."""
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)
        record = _make_record(external_group_id="-merge-requests")
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is None

    def test_url_with_too_few_segments_returns_none(self) -> None:
        """URL with dash but not enough segments after it returns None."""
        c = make_mock_connector()
        mrs = MergeRequestsSync(c)
        record = _make_record(weburl="https://gitlab.com/ns/-/")
        result = mrs.gitlab_project_id_and_iid_from_record(record)
        assert result is None


# ===========================================================================
# check_and_fetch_updated_record_for_reindex — additional paths
# ===========================================================================


class TestCheckAndFetchUpdatedRecordEdgeCases:
    async def test_ticket_fetch_failure_returns_none(self) -> None:
        """TICKET with ds_call failure returns None."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        mrs = MergeRequestsSync(c)
        mrs.gitlab_project_id_and_iid_from_record = MagicMock(return_value=("42", 1))

        fail_res = MagicMock(success=False, data=None, error="not found")
        c.runtime.ds_call = AsyncMock(return_value=fail_res)

        from app.models.entities import RecordType
        record = _make_record(record_type=RecordType.TICKET.value)
        result = await mrs.check_and_fetch_updated_record_for_reindex(record)
        assert result is None

    async def test_pr_revision_unchanged_returns_none(self) -> None:
        """PULL_REQUEST with same revision as fetched returns None."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        mrs = MergeRequestsSync(c)
        mrs.gitlab_project_id_and_iid_from_record = MagicMock(return_value=("42", 1))

        mr = _make_mr()
        mr_res = MagicMock(success=True, data=mr, error=None)
        c.runtime.ds_call = AsyncMock(return_value=mr_res)

        from app.models.entities import RecordType
        record = _make_record(record_type=RecordType.PULL_REQUEST.value)

        with patch("app.connectors.sources.gitlab.merge_requests.parse_timestamp", return_value=1000):
            record.external_revision_id = "1000"
            result = await mrs.check_and_fetch_updated_record_for_reindex(record)

        assert result is None

    async def test_pr_ru_none_returns_none(self) -> None:
        """PULL_REQUEST where _process_mr_to_pull_request returns None → returns None."""
        c = make_mock_connector()
        c.data_source = MagicMock()
        mrs = MergeRequestsSync(c)
        mrs.gitlab_project_id_and_iid_from_record = MagicMock(return_value=("42", 1))
        mrs._process_mr_to_pull_request = AsyncMock(return_value=None)

        mr = _make_mr()
        mr_res = MagicMock(success=True, data=mr, error=None)
        c.runtime.ds_call = AsyncMock(return_value=mr_res)

        from app.models.entities import RecordType
        record = _make_record(record_type=RecordType.PULL_REQUEST.value)

        with patch("app.connectors.sources.gitlab.merge_requests.parse_timestamp", return_value=9999):
            record.external_revision_id = "0"
            result = await mrs.check_and_fetch_updated_record_for_reindex(record)

        assert result is None


# ===========================================================================
# _get_mr_sync_checkpoint — exception path
# ===========================================================================


class TestGetMrSyncCheckpointException:
    async def test_exception_returns_none(self) -> None:
        """Store read failure returns None instead of propagating."""
        c = make_mock_connector()
        c.record_sync_point.read_sync_point = AsyncMock(side_effect=Exception("DB error"))

        mrs = MergeRequestsSync(c)
        result = await mrs._get_mr_sync_checkpoint(42)
        assert result is None
