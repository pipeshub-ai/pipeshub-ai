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
