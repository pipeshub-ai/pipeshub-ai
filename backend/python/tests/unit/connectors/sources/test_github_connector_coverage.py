"""Extended coverage tests for GitHub connector."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.github.connector import GithubConnector, RecordUpdate
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store(existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


def _make_issue(
    title="Test Issue",
    url="https://api.github.com/repos/owner/repo/issues/1",
    html_url="https://github.com/owner/repo/issues/1",
    state="open",
    number=1,
    labels=None,
    assignees=None,
    body="Issue body",
    pull_request=None,
    updated_at=None,
    created_at=None,
    repository_url="https://api.github.com/repos/owner/repo",
):
    issue = MagicMock()
    issue.title = title
    issue.url = url
    issue.html_url = html_url
    issue.state = state
    issue.number = number
    issue.labels = labels or []
    issue.assignees = assignees or []
    issue.body = body
    issue.pull_request = pull_request
    issue.updated_at = updated_at or datetime(2025, 1, 1, tzinfo=timezone.utc)
    issue.created_at = created_at or datetime(2025, 1, 1, tzinfo=timezone.utc)
    issue.repository_url = repository_url
    issue.raw_data = {}
    repo_mock = MagicMock()
    repo_mock.full_name = "owner/repo"
    issue.repository = repo_mock
    return issue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.github.coverage")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-gh-cov"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="dev@test.com"))
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {"oauthConfigId": "gh-oauth-1"},
        "credentials": {"access_token": "ghp_test_token"},
    })
    return svc


@pytest.fixture()
def github_connector(mock_logger, mock_data_entities_processor,
                     mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.github.connector.GithubApp"):
        connector = GithubConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="gh-cov-1",
            scope="personal",
            created_by="test-user-id",
        )
    return connector


# ===========================================================================
# RecordUpdate extended coverage
# ===========================================================================
class TestRecordUpdateExtended:
    def test_fields_all_none(self):
        ru = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        assert ru.is_deleted is True
        assert ru.old_permissions is None
        assert ru.new_permissions is None
        assert ru.external_record_id is None

    def test_external_record_id_set(self):
        ru = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            external_record_id="ext-123",
        )
        assert ru.external_record_id == "ext-123"


# ===========================================================================
# _process_issue_to_ticket
# ===========================================================================
class TestProcessIssueToTicket:
    @pytest.mark.asyncio
    async def test_new_issue_creates_ticket(self, github_connector):
        issue = _make_issue()
        result = await github_connector._process_issue_to_ticket(issue)
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "Test Issue"
        assert result.record.record_type == RecordType.TICKET.value

    @pytest.mark.asyncio
    async def test_existing_issue_title_changed(self, github_connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "Old Title"
        github_connector.data_store_provider = _make_mock_data_store_provider(existing)

        issue = _make_issue(title="New Title")
        result = await github_connector._process_issue_to_ticket(issue)
        assert result is not None
        assert result.is_new is False
        assert result.metadata_changed is True
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_issue_with_parent(self, github_connector):
        issue = _make_issue()
        issue.raw_data = {"parent_issue_url": "https://api.github.com/repos/owner/repo/issues/0"}
        result = await github_connector._process_issue_to_ticket(issue)
        assert result is not None
        assert result.record.parent_external_record_id == "https://api.github.com/repos/owner/repo/issues/0"

    @pytest.mark.asyncio
    async def test_issue_with_labels_and_assignees(self, github_connector):
        label = MagicMock()
        label.name = "bug"
        assignee = MagicMock()
        assignee.login = "dev1"
        issue = _make_issue(labels=[label], assignees=[assignee])
        result = await github_connector._process_issue_to_ticket(issue)
        assert result is not None
        assert "bug" in result.record.labels
        assert "dev1" in result.record.assignee_source_id

    @pytest.mark.asyncio
    async def test_issue_processing_exception(self, github_connector):
        """Exception during processing returns None."""
        issue = _make_issue()
        # Force an exception by making data_store_provider.transaction raise
        github_connector.data_store_provider = MagicMock()
        github_connector.data_store_provider.transaction.side_effect = Exception("DB error")
        result = await github_connector._process_issue_to_ticket(issue)
        assert result is None


# ===========================================================================
# _process_pr_to_pull_request
# ===========================================================================
class TestProcessPRToPullRequest:
    @pytest.mark.asyncio
    async def test_new_pr(self, github_connector):
        issue = _make_issue(
            url="https://api.github.com/repos/owner/repo/issues/5",
            html_url="https://github.com/owner/repo/pull/5",
            number=5,
        )
        pr_mock = MagicMock()
        pr_mock.url = "https://api.github.com/repos/owner/repo/pulls/5"
        pr_mock.html_url = "https://github.com/owner/repo/pull/5"
        pr_mock.title = "Fix something"
        pr_mock.state = "open"
        pr_mock.labels = []
        pr_mock.mergeable = True
        pr_mock.merged = False
        pr_mock.draft = False
        pr_mock.additions = 10
        pr_mock.deletions = 5
        pr_mock.changed_files = 3
        pr_mock.commits = 2
        pr_mock.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        pr_mock.created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        pr_mock.base = MagicMock()
        pr_mock.base.ref = "main"
        pr_mock.head = MagicMock()
        pr_mock.head.ref = "fix-branch"

        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(True, pr_mock)
        result = await github_connector._process_pr_to_pull_request(issue)
        assert result is not None
        assert result.record.record_type == RecordType.PULL_REQUEST.value
        assert result.record.record_name == "Fix something"

    @pytest.mark.asyncio
    async def test_pr_fetch_fails(self, github_connector):
        issue = _make_issue(
            url="https://api.github.com/repos/owner/repo/issues/5",
        )
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(False, error="Not found")
        result = await github_connector._process_pr_to_pull_request(issue)
        assert result is None

    @pytest.mark.asyncio
    async def test_pr_no_data(self, github_connector):
        issue = _make_issue(
            url="https://api.github.com/repos/owner/repo/issues/5",
        )
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(True, None)
        result = await github_connector._process_pr_to_pull_request(issue)
        assert result is None


# ===========================================================================
# _build_issue_records
# ===========================================================================
class TestBuildIssueRecords:
    @pytest.mark.asyncio
    async def test_processes_issue(self, github_connector):
        issue = _make_issue()
        github_connector._process_issue_to_ticket = AsyncMock(
            return_value=RecordUpdate(
                record=MagicMock(record_type=RecordType.TICKET),
                is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False,
                permissions_changed=False, new_permissions=[], old_permissions=[],
            )
        )
        github_connector.make_issue_comment_records = AsyncMock(return_value=[])
        github_connector.clean_github_content = AsyncMock(return_value=("cleaned", []))

        result = await github_connector._build_issue_records([issue])
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_processes_pr(self, github_connector):
        issue = _make_issue(pull_request=MagicMock())
        github_connector._process_pr_to_pull_request = AsyncMock(
            return_value=RecordUpdate(
                record=MagicMock(record_type=RecordType.PULL_REQUEST),
                is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False,
                permissions_changed=False, new_permissions=[], old_permissions=[],
            )
        )
        github_connector.make_issue_comment_records = AsyncMock(return_value=[])
        github_connector.make_r_comment_attachments = AsyncMock(return_value=[])
        github_connector.make_reviews_attachments = AsyncMock(return_value=[])
        github_connector.clean_github_content = AsyncMock(return_value=("cleaned", []))

        result = await github_connector._build_issue_records([issue])
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_skip_when_processing_returns_none(self, github_connector):
        issue = _make_issue()
        github_connector._process_issue_to_ticket = AsyncMock(return_value=None)
        result = await github_connector._build_issue_records([issue])
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_attachments_added(self, github_connector):
        issue = _make_issue(body="![img](https://example.com/img.png)")
        record_update = RecordUpdate(
            record=MagicMock(record_type=RecordType.TICKET),
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False,
            permissions_changed=False, new_permissions=[], old_permissions=[],
        )
        github_connector._process_issue_to_ticket = AsyncMock(return_value=record_update)
        github_connector.make_issue_comment_records = AsyncMock(return_value=[])
        attachment_update = RecordUpdate(
            record=MagicMock(record_type=RecordType.FILE),
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False,
            permissions_changed=False, new_permissions=[], old_permissions=[],
        )
        github_connector.clean_github_content = AsyncMock(
            return_value=("cleaned", [{"url": "https://example.com/file.pdf", "name": "file.pdf"}])
        )
        github_connector.make_file_records_from_list = AsyncMock(return_value=[attachment_update])

        result = await github_connector._build_issue_records([issue])
        # Should have the main ticket + the attachment
        assert len(result) >= 2


# ===========================================================================
# _handle_record_updates
# ===========================================================================
class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_deleted_record(self, github_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="Deleted"),
            is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        # Should not raise - deleted handling logs "need to implement"
        await github_connector._handle_record_updates(update)

    @pytest.mark.asyncio
    async def test_metadata_changed(self, github_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="Updated"),
            is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )
        await github_connector._handle_record_updates(update)
        github_connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_content_changed(self, github_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="Content Update"),
            is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=True, permissions_changed=False,
        )
        await github_connector._handle_record_updates(update)
        github_connector.data_entities_processor.on_record_content_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_handling(self, github_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="Error"),
            is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )
        github_connector.data_entities_processor.on_record_metadata_update = AsyncMock(
            side_effect=Exception("DB error")
        )
        # Should not raise
        await github_connector._handle_record_updates(update)


# ===========================================================================
# make_issue_comment_records, make_r_comment_attachments, make_reviews_attachments
# ===========================================================================
class TestCommentAttachments:
    @pytest.mark.asyncio
    async def test_make_issue_comment_records_no_comments(self, github_connector):
        issue = _make_issue()
        record = MagicMock()
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(True, None)
        result = await github_connector.make_issue_comment_records(issue, record)
        assert result == []

    @pytest.mark.asyncio
    async def test_make_issue_comment_records_failure(self, github_connector):
        issue = _make_issue()
        record = MagicMock()
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(False, error="err")
        result = await github_connector.make_issue_comment_records(issue, record)
        assert result == []

    @pytest.mark.asyncio
    async def test_make_issue_comment_records_with_attachments(self, github_connector):
        issue = _make_issue()
        record = MagicMock()
        comment = MagicMock()
        comment.body = "Check this [file](https://example.com/f.pdf)"
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(True, [comment])
        github_connector.clean_github_content = AsyncMock(
            return_value=("cleaned", [{"url": "https://example.com/f.pdf", "name": "f.pdf"}])
        )
        attachment_update = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        github_connector.make_file_records_from_list = AsyncMock(return_value=[attachment_update])
        result = await github_connector.make_issue_comment_records(issue, record)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_make_r_comment_attachments_no_data(self, github_connector):
        issue = _make_issue()
        record = MagicMock()
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull_review_comments.return_value = _make_response(True, None)
        result = await github_connector.make_r_comment_attachments(issue, record)
        assert result == []

    @pytest.mark.asyncio
    async def test_make_r_comment_attachments_failure(self, github_connector):
        issue = _make_issue()
        record = MagicMock()
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull_review_comments.return_value = _make_response(False, error="err")
        result = await github_connector.make_r_comment_attachments(issue, record)
        assert result == []

    @pytest.mark.asyncio
    async def test_make_reviews_attachments_no_data(self, github_connector):
        issue = _make_issue()
        record = MagicMock()
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull_reviews.return_value = _make_response(True, None)
        result = await github_connector.make_reviews_attachments(issue, record)
        assert result == []

    @pytest.mark.asyncio
    async def test_make_reviews_attachments_failure(self, github_connector):
        issue = _make_issue()
        record = MagicMock()
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull_reviews.return_value = _make_response(False, error="err")
        result = await github_connector.make_reviews_attachments(issue, record)
        assert result == []

    @pytest.mark.asyncio
    async def test_make_reviews_with_attachments(self, github_connector):
        issue = _make_issue()
        record = MagicMock()
        review = MagicMock()
        review.body = "See [link](https://example.com/doc.pdf)"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull_reviews.return_value = _make_response(True, [review])
        github_connector.clean_github_content = AsyncMock(
            return_value=("cleaned", [{"url": "https://example.com/doc.pdf", "name": "doc.pdf"}])
        )
        att = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        github_connector.make_file_records_from_list = AsyncMock(return_value=[att])
        result = await github_connector.make_reviews_attachments(issue, record)
        assert len(result) == 1


# ===========================================================================
# _fetch_issues_batched
# ===========================================================================
class TestFetchIssuesBatched:
    @pytest.mark.asyncio
    async def test_no_issues_returned(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issues.return_value = _make_response(True, None)
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await github_connector._fetch_issues_batched("owner/repo")
        # When data is None/empty, the method returns [] (empty list)
        assert result == []

    @pytest.mark.asyncio
    async def test_issues_failure(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issues.return_value = _make_response(False, error="API err")
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await github_connector._fetch_issues_batched("owner/repo")
        assert result == []

    @pytest.mark.asyncio
    async def test_issues_with_sync_point(self, github_connector):
        """Test with a previous sync point timestamp."""
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issues.return_value = _make_response(True, [])
        github_connector.record_sync_point = MagicMock()
        # Return a timestamp that is in seconds (< 10^12)
        github_connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1700000000}
        )
        result = await github_connector._fetch_issues_batched("owner/repo")
        # Empty data list is falsy, so the method returns []
        assert result == []

    @pytest.mark.asyncio
    async def test_issues_with_ms_sync_point(self, github_connector):
        """Test with a previous sync point timestamp in milliseconds (>= 10^12)."""
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issues.return_value = _make_response(True, [])
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1700000000000}
        )
        result = await github_connector._fetch_issues_batched("owner/repo")
        # Empty data list is falsy, so the method returns []
        assert result == []


# ===========================================================================
# _process_new_records
# ===========================================================================
class TestProcessNewRecords:
    @pytest.mark.asyncio
    async def test_process_batch(self, github_connector):
        ticket_record = MagicMock()
        ticket_record.record_type = RecordType.TICKET
        ticket_record.source_updated_at = 1700000000000
        ticket_record.external_record_group_id = "https://api.github.com/repos/owner/repo"

        update = RecordUpdate(
            record=ticket_record,
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.update_sync_point = AsyncMock()
        await github_connector._process_new_records([update])
        github_connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_batch_exception(self, github_connector):
        update = RecordUpdate(
            record=MagicMock(record_type=RecordType.FILE, source_updated_at=123),
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        github_connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=Exception("batch error")
        )
        # Should not raise
        await github_connector._process_new_records([update])


# ===========================================================================
# _sync_all_repo_issue
# ===========================================================================
class TestSyncAllRepoIssue:
    @pytest.mark.asyncio
    async def test_full_sync_path(self, github_connector):
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(
            return_value=MagicMock(email="dev@test.com")
        )
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(return_value={})
        github_connector.record_sync_point.update_sync_point = AsyncMock()
        github_connector._sync_issues_full = AsyncMock()
        github_connector._get_iso_time = MagicMock(return_value="2025-01-01T00:00:00Z")
        await github_connector._sync_all_repo_issue()
        github_connector._sync_issues_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_incremental_sync_path(self, github_connector):
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(
            return_value=MagicMock(email="dev@test.com")
        )
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"timestamp": "2025-01-01T00:00:00Z"}
        )
        github_connector.record_sync_point.update_sync_point = AsyncMock()
        github_connector._sync_issues_full = AsyncMock()
        github_connector._get_iso_time = MagicMock(return_value="2025-06-01T00:00:00Z")
        await github_connector._sync_all_repo_issue()
        github_connector._sync_issues_full.assert_awaited_once_with("2025-01-01T00:00:00Z")


# ===========================================================================
# _sync_issues_full
# ===========================================================================
class TestSyncIssuesFull:
    @pytest.mark.asyncio
    async def test_no_user_returns_early(self, github_connector):
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(return_value=None)
        await github_connector._sync_issues_full()
        # Should not crash

    @pytest.mark.asyncio
    async def test_no_email_returns_early(self, github_connector):
        user = MagicMock()
        user.email = None
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(return_value=user)
        await github_connector._sync_issues_full()

    @pytest.mark.asyncio
    async def test_no_repos_returns_early(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_user_repos.return_value = _make_response(False, error="err")
        await github_connector._sync_issues_full()

    @pytest.mark.asyncio
    async def test_empty_repos(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_user_repos.return_value = _make_response(True, None)
        await github_connector._sync_issues_full()


# ===========================================================================
# Misc methods
# ===========================================================================
class TestMiscMethods:
    @pytest.mark.asyncio
    async def test_reindex_records(self, github_connector):
        await github_connector.reindex_records()

    @pytest.mark.asyncio
    async def test_run_incremental_sync(self, github_connector):
        await github_connector.run_incremental_sync()

    @pytest.mark.asyncio
    async def test_sync_records_incremental(self, github_connector):
        await github_connector._sync_records_incremental()

    @pytest.mark.asyncio
    async def test_handle_page_upsert_event_issue(self, github_connector):
        await github_connector._handle_page_upsert_event_issue()

    @pytest.mark.asyncio
    async def test_build_comment_blocks_no_comments(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(True, None)
        bgs, records = await github_connector._build_comment_blocks(
            issue_url="https://github.com/owner/repo/issues/1",
            parent_index=0,
            record=MagicMock(),
        )
        assert bgs == []
        assert records == []

    @pytest.mark.asyncio
    async def test_build_comment_blocks_failure(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(False, error="err")
        bgs, records = await github_connector._build_comment_blocks(
            issue_url="https://github.com/owner/repo/issues/1",
            parent_index=0,
            record=MagicMock(),
        )
        assert bgs == []
        assert records == []

    @pytest.mark.asyncio
    async def test_build_comment_blocks_with_comments(self, github_connector):
        comment = MagicMock()
        comment.body = "A comment"
        comment.user = MagicMock()
        comment.user.login = "testuser"
        comment.url = "https://api.github.com/repos/owner/repo/issues/comments/1"
        comment.html_url = "https://github.com/owner/repo/issues/1#comment-1"
        comment.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)

        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(True, [comment])
        github_connector.embed_images_as_base64 = AsyncMock(return_value="cleaned comment")
        github_connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))

        bgs, records = await github_connector._build_comment_blocks(
            issue_url="https://github.com/owner/repo/issues/1",
            parent_index=0,
            record=MagicMock(),
        )
        assert len(bgs) == 1
        assert records == []

    def test_datetime_to_epoch_ms(self, github_connector):
        dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = github_connector.datetime_to_epoch_ms(dt)
        assert result == 1735689600000

    def test_datetime_to_epoch_ms_naive(self, github_connector):
        dt = datetime(2025, 1, 1, 0, 0, 0)
        result = github_connector.datetime_to_epoch_ms(dt)
        assert result == 1735689600000

    def test_get_iso_time(self, github_connector):
        result = github_connector._get_iso_time()
        assert result.endswith("Z")
        assert "T" in result

    @pytest.mark.asyncio
    async def test_get_filter_options(self, github_connector):
        result = github_connector.get_filter_options()
        assert result is None

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self, github_connector):
        result = await github_connector.handle_webhook_notification()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_signed_url(self, github_connector):
        record = MagicMock()
        result = await github_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_cleanup(self, github_connector):
        github_connector.data_source = MagicMock()
        await github_connector.cleanup()
        assert github_connector.data_source is None

    @pytest.mark.asyncio
    async def test_process_comments_to_commentrecord(self, github_connector):
        result = await github_connector._process_comments_to_commentrecord()
        assert result is None


# ===========================================================================
# init
# ===========================================================================
class TestGithubInit:
    @pytest.mark.asyncio
    async def test_init_success(self, github_connector):
        with patch("app.connectors.sources.github.connector.GitHubClient") as MockClient, \
             patch("app.connectors.sources.github.connector.GitHubDataSource") as MockDS:
            mock_client = MagicMock()
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            result = await github_connector.init()
            assert result is True
            assert github_connector.external_client is mock_client

    @pytest.mark.asyncio
    async def test_init_failure(self, github_connector):
        with patch("app.connectors.sources.github.connector.GitHubClient") as MockClient:
            MockClient.build_from_services = AsyncMock(side_effect=Exception("fail"))
            result = await github_connector.init()
            assert result is False


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestGithubTestConnection:
    @pytest.mark.asyncio
    async def test_no_data_source(self, github_connector):
        github_connector.data_source = None
        result = await github_connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(True, MagicMock(login="user"))
        result = await github_connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(False, error="bad creds")
        result = await github_connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.side_effect = Exception("err")
        result = await github_connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# _fetch_sync_point_of_issue / _update_sync_point_of_issue
# ===========================================================================
class TestSyncPointMethods:
    @pytest.mark.asyncio
    async def test_fetch_sync_point_returns_time(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 12345}
        )
        result = await github_connector._fetch_sync_point_of_issue("repo1")
        assert result == 12345

    @pytest.mark.asyncio
    async def test_fetch_sync_point_returns_none_on_no_data(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await github_connector._fetch_sync_point_of_issue("repo1")
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_sync_point_returns_none_on_exception(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(side_effect=Exception("db err"))
        result = await github_connector._fetch_sync_point_of_issue("repo1")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_sync_point(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.update_sync_point = AsyncMock()
        await github_connector._update_sync_point_of_issue("repo1", 12345)
        github_connector.record_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_sync_point_exception_swallowed(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.update_sync_point = AsyncMock(side_effect=Exception("err"))
        await github_connector._update_sync_point_of_issue("repo1", 12345)


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_ticket(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.weburl = "https://github.com/owner/repo/issues/1"
        record.record_name = "test_issue"
        github_connector._build_ticket_blocks = AsyncMock(return_value=MagicMock(
            model_dump_json=MagicMock(return_value='{"blocks": []}')
        ))
        response = await github_connector.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_pull_request(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.PULL_REQUEST
        record.record_name = "test_pr"
        github_connector._build_pull_request_blocks = AsyncMock(return_value=MagicMock(
            model_dump_json=MagicMock(return_value='{"blocks": []}')
        ))
        response = await github_connector.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_file(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.weburl = "https://github.com/owner/repo/blob/main/file.py"
        record.record_name = "file.py"
        record.mime_type = "text/plain"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_attachment_files_content = AsyncMock(
            return_value=_make_response(True, "file content here")
        )
        response = await github_connector.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_file_no_mime_type(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.weburl = "https://github.com/owner/repo/blob/main/file.bin"
        record.record_name = "file.bin"
        record.mime_type = None
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_attachment_files_content = AsyncMock(
            return_value=_make_response(True, "binary content")
        )
        response = await github_connector.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_file_failure(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.weburl = "https://github.com/url"
        record.record_name = "file.txt"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_attachment_files_content = AsyncMock(
            return_value=_make_response(False, error="not found")
        )
        with pytest.raises(Exception, match="Failed to fetch file"):
            await github_connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self, github_connector):
        record = MagicMock()
        record.record_type = "unsupported_type"
        with pytest.raises(Exception):
            await github_connector.stream_record(record)


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSync:
    @pytest.mark.asyncio
    async def test_successful_sync(self, github_connector):
        github_connector._fetch_users = AsyncMock(return_value=[MagicMock()])
        github_connector._sync_all_repo_issue = AsyncMock()
        await github_connector.run_sync()
        github_connector._sync_all_repo_issue.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_users_raises(self, github_connector):
        github_connector._fetch_users = AsyncMock(return_value=[])
        with pytest.raises(ValueError, match="Failed to retrieve account"):
            await github_connector.run_sync()

    @pytest.mark.asyncio
    async def test_sync_exception_propagates(self, github_connector):
        github_connector._fetch_users = AsyncMock(side_effect=Exception("sync fail"))
        with pytest.raises(Exception, match="sync fail"):
            await github_connector.run_sync()


# ===========================================================================
# _fetch_users
# ===========================================================================
class TestFetchUsers:
    @pytest.mark.asyncio
    async def test_no_data_source(self, github_connector):
        github_connector.data_source = None
        with pytest.raises(ValueError, match="Data source not initialized"):
            await github_connector._fetch_users()

    @pytest.mark.asyncio
    async def test_auth_fails(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(False, error="auth fail")
        result = await github_connector._fetch_users()
        assert result == []

    @pytest.mark.asyncio
    async def test_no_app_creator_user(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(True, MagicMock(login="user1"))
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(return_value=None)
        result = await github_connector._fetch_users()
        assert result == []

    @pytest.mark.asyncio
    async def test_success(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(True, MagicMock(login="user1"))
        user = MagicMock()
        user.email = "user1@test.com"
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(return_value=user)
        result = await github_connector._fetch_users()
        assert len(result) == 1
        assert result[0].email == "user1@test.com"


# ===========================================================================
# _sync_issues_full with repos
# ===========================================================================
class TestSyncIssuesFullWithRepos:
    @pytest.mark.asyncio
    async def test_repos_sync_complete(self, github_connector):
        repo = MagicMock()
        repo.full_name = "owner/repo"
        repo.url = "https://api.github.com/repos/owner/repo"
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_user_repos.return_value = _make_response(True, [repo])
        github_connector._fetch_issues_batched = AsyncMock()
        await github_connector._sync_issues_full()
        github_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        github_connector._fetch_issues_batched.assert_awaited_once()


# ===========================================================================
# clean_github_content
# ===========================================================================
class TestCleanGithubContent:
    @pytest.mark.asyncio
    async def test_html_img_github_assets(self, github_connector):
        text = '<img src="https://github.com/user-attachments/assets/abc123" alt="screenshot">'
        cleaned, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 1
        assert attachments[0]["source"] == "html_img"
        assert cleaned.strip() == ""

    @pytest.mark.asyncio
    async def test_html_img_non_github(self, github_connector):
        text = '<img src="https://example.com/image.png" alt="test">'
        cleaned, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 0
        assert "example.com" in cleaned

    @pytest.mark.asyncio
    async def test_markdown_image_github(self, github_connector):
        text = "![alt text](https://github.com/user-attachments/assets/abc123)"
        cleaned, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 1
        assert attachments[0]["source"] == "markdown_image"

    @pytest.mark.asyncio
    async def test_markdown_link_github_file(self, github_connector):
        text = "[file.pdf](https://github.com/user-attachments/files/abc123)"
        cleaned, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 1
        assert attachments[0]["source"] == "file_attachment"

    @pytest.mark.asyncio
    async def test_markdown_link_non_github(self, github_connector):
        text = "[link](https://example.com/page)"
        cleaned, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 0
        assert "[link](https://example.com/page)" in cleaned

    @pytest.mark.asyncio
    async def test_empty_text(self, github_connector):
        cleaned, attachments = await github_connector.clean_github_content("")
        assert cleaned == ""
        assert attachments == []

    @pytest.mark.asyncio
    async def test_multiple_blank_lines_cleaned(self, github_connector):
        text = "before\n\n\n\n\nafter"
        cleaned, attachments = await github_connector.clean_github_content(text)
        assert "\n\n\n" not in cleaned


# ===========================================================================
# embed_images_as_base64
# ===========================================================================
class TestEmbedImagesAsBase64:
    @pytest.mark.asyncio
    async def test_empty_body(self, github_connector):
        result = await github_connector.embed_images_as_base64("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_attachments(self, github_connector):
        github_connector.clean_github_content = AsyncMock(return_value=("clean text", []))
        result = await github_connector.embed_images_as_base64("some text")
        assert result == "clean text"

    @pytest.mark.asyncio
    async def test_non_image_attachment_skipped(self, github_connector):
        github_connector.clean_github_content = AsyncMock(
            return_value=("clean", [{"type": "pdf", "href": "https://url.com/file.pdf"}])
        )
        result = await github_connector.embed_images_as_base64("some text")
        assert result == "clean"

    @pytest.mark.asyncio
    async def test_image_fetch_failure(self, github_connector):
        github_connector.clean_github_content = AsyncMock(
            return_value=("clean", [{"type": "image", "href": "https://github.com/user-attachments/assets/img1"}])
        )
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_img_bytes = AsyncMock(
            return_value=_make_response(False, error="not found")
        )
        result = await github_connector.embed_images_as_base64("img text")
        assert result == "clean"


# ===========================================================================
# make_file_records_from_list
# ===========================================================================
class TestMakeFileRecordsFromList:
    @pytest.mark.asyncio
    async def test_skips_images(self, github_connector):
        attachments = [{"type": "image", "href": "https://url.com/img.png"}]
        record = MagicMock(external_record_id="ext-1", external_record_group_id="group-1")
        result = await github_connector.make_file_records_from_list(attachments, record)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_skips_missing_url(self, github_connector):
        attachments = [{"type": "pdf", "href": None, "filename": "file.pdf"}]
        record = MagicMock(external_record_id="ext-1", external_record_group_id="group-1")
        result = await github_connector.make_file_records_from_list(attachments, record)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_creates_file_record(self, github_connector):
        attachments = [{"type": "pdf", "href": "https://url.com/f.pdf", "filename": "f.pdf"}]
        record = MagicMock(
            external_record_id="ext-1",
            external_record_group_id="group-1",
            record_type=RecordType.TICKET,
        )
        result = await github_connector.make_file_records_from_list(attachments, record)
        assert len(result) == 1
        assert result[0].record.record_name == "f.pdf"


# ===========================================================================
# make_child_records_of_attachments
# ===========================================================================
class TestMakeChildRecordsOfAttachments:
    @pytest.mark.asyncio
    async def test_image_skipped(self, github_connector):
        github_connector.clean_github_content = AsyncMock(
            return_value=("cleaned", [{"type": "image", "href": "https://url.com/img.png"}])
        )
        children, remaining = await github_connector.make_child_records_of_attachments("md", MagicMock())
        assert len(children) == 0
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_existing_record_found(self, github_connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "existing.pdf"
        github_connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        github_connector.clean_github_content = AsyncMock(
            return_value=("cleaned", [{"type": "pdf", "href": "https://url.com/f.pdf", "filename": "f.pdf"}])
        )
        children, remaining = await github_connector.make_child_records_of_attachments("md", MagicMock())
        assert len(children) == 1
        assert children[0].child_id == "existing-id"
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_new_record_created(self, github_connector):
        github_connector.clean_github_content = AsyncMock(
            return_value=("cleaned", [{"type": "pdf", "href": "https://url.com/f.pdf", "filename": "f.pdf"}])
        )
        file_update = RecordUpdate(
            record=MagicMock(id="new-id", record_name="f.pdf"),
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        github_connector.make_file_records_from_list = AsyncMock(return_value=[file_update])
        children, remaining = await github_connector.make_child_records_of_attachments(
            "md", MagicMock(external_record_id="ext", external_record_group_id="grp", record_type=RecordType.TICKET)
        )
        assert len(children) == 1
        assert len(remaining) == 1


# ===========================================================================
# make_block_comment_of_attachments
# ===========================================================================
class TestMakeBlockCommentOfAttachments:
    @pytest.mark.asyncio
    async def test_existing_record(self, github_connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "existing.pdf"
        github_connector.data_store_provider = _make_mock_data_store_provider(existing_record=existing)
        github_connector.clean_github_content = AsyncMock(
            return_value=("cleaned", [{"type": "pdf", "href": "https://url.com/f.pdf", "filename": "f.pdf"}])
        )
        attachments, remaining = await github_connector.make_block_comment_of_attachments("md", MagicMock())
        assert len(attachments) == 1
        assert attachments[0].id == "existing-id"
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_new_record(self, github_connector):
        github_connector.clean_github_content = AsyncMock(
            return_value=("cleaned", [{"type": "pdf", "href": "https://url.com/f.pdf", "filename": "f.pdf"}])
        )
        file_update = RecordUpdate(
            record=MagicMock(id="new-id", record_name="f.pdf"),
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        github_connector.make_file_records_from_list = AsyncMock(return_value=[file_update])
        attachments, remaining = await github_connector.make_block_comment_of_attachments(
            "md", MagicMock(external_record_id="ext", external_record_group_id="grp", record_type=RecordType.TICKET)
        )
        assert len(attachments) == 1
        assert len(remaining) == 1


# ===========================================================================
# _get_api_token_
# ===========================================================================
class TestGetApiToken:
    @pytest.mark.asyncio
    async def test_success(self, github_connector):
        github_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "ghp_test123"}
        })
        result = await github_connector._get_api_token_()
        assert result == "ghp_test123"

    @pytest.mark.asyncio
    async def test_no_config(self, github_connector):
        github_connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="Github configuration not found"):
            await github_connector._get_api_token_()

    @pytest.mark.asyncio
    async def test_no_access_token(self, github_connector):
        github_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {}
        })
        with pytest.raises(ValueError, match="Github credentials not found"):
            await github_connector._get_api_token_()


# ===========================================================================
# _log_rate_limit
# ===========================================================================
class TestLogRateLimit:
    @pytest.mark.asyncio
    async def test_no_data_source(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_rate_limit.return_value = None
        await github_connector._log_rate_limit("test")

    @pytest.mark.asyncio
    async def test_failure_response(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_rate_limit.return_value = _make_response(False)
        await github_connector._log_rate_limit("test")

    @pytest.mark.asyncio
    async def test_success_with_datetime_reset(self, github_connector):
        rate = MagicMock()
        rate.remaining = 50
        rate.limit = 60
        rate.reset = datetime(2025, 6, 1, tzinfo=timezone.utc)
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_rate_limit.return_value = _make_response(True, MagicMock(rate=rate))
        await github_connector._log_rate_limit("test")

    @pytest.mark.asyncio
    async def test_success_with_non_datetime_reset(self, github_connector):
        rate = MagicMock()
        rate.remaining = 50
        rate.limit = 60
        rate.reset = 1700000000
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_rate_limit.return_value = _make_response(True, MagicMock(rate=rate))
        await github_connector._log_rate_limit("test")

    @pytest.mark.asyncio
    async def test_exception_swallowed(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_rate_limit.side_effect = Exception("err")
        await github_connector._log_rate_limit("test")


# ===========================================================================
# _build_ticket_blocks
# ===========================================================================
class TestBuildTicketBlocks:
    @pytest.mark.asyncio
    async def test_success(self, github_connector):
        record = MagicMock()
        record.weburl = "https://github.com/owner/repo/issues/1"
        record.record_name = "Test Issue"
        record.external_record_id = "ext-1"

        issue = MagicMock()
        issue.body = "Issue body"
        issue.title = "Test Issue"
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        issue.updated_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        issue.html_url = "https://github.com/owner/repo/issues/1"

        github_connector.data_source = MagicMock()
        github_connector.data_source.get_issue.return_value = _make_response(True, issue)
        github_connector.embed_images_as_base64 = AsyncMock(return_value="clean body")
        github_connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        github_connector._build_comment_blocks = AsyncMock(return_value=([], []))
        github_connector._process_new_records = AsyncMock()

        result = await github_connector._build_ticket_blocks(record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_issue_fetch_failure(self, github_connector):
        record = MagicMock()
        record.weburl = "https://github.com/owner/repo/issues/1"
        record.external_record_id = "ext-1"

        github_connector.data_source = MagicMock()
        github_connector.data_source.get_issue.return_value = _make_response(False, error="not found")
        with pytest.raises(Exception, match="Failed to fetch issue"):
            await github_connector._build_ticket_blocks(record)


# ===========================================================================
# _build_pull_request_blocks
# ===========================================================================
class TestBuildPullRequestBlocks:
    @pytest.mark.asyncio
    async def test_no_weburl(self, github_connector):
        record = MagicMock()
        record.weburl = ""
        with pytest.raises(ValueError, match="Web URL is required"):
            await github_connector._build_pull_request_blocks(record)

    @pytest.mark.asyncio
    async def test_pr_fetch_fails(self, github_connector):
        record = MagicMock()
        record.weburl = "https://github.com/owner/repo/pull/5"
        record.external_record_id = "ext-1"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(False, error="not found")
        with pytest.raises(Exception, match="Failed to fetch pull request"):
            await github_connector._build_pull_request_blocks(record)


# ===========================================================================
# _process_new_records with PR record type
# ===========================================================================
class TestProcessNewRecordsPR:
    @pytest.mark.asyncio
    async def test_pr_record_updates_sync_point(self, github_connector):
        pr_record = MagicMock()
        pr_record.record_type = RecordType.PULL_REQUEST
        pr_record.source_updated_at = 1700000000000
        pr_record.external_record_group_id = "https://api.github.com/repos/owner/repo"
        update = RecordUpdate(
            record=pr_record,
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.update_sync_point = AsyncMock()
        await github_connector._process_new_records([update])
        github_connector.record_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_file_record_skips_sync_update(self, github_connector):
        file_record = MagicMock()
        file_record.record_type = RecordType.FILE
        file_record.source_updated_at = 123
        update = RecordUpdate(
            record=file_record,
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.update_sync_point = AsyncMock()
        await github_connector._process_new_records([update])


# ===========================================================================
# create_connector factory
# ===========================================================================
class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_factory(self, mock_logger, mock_data_store_provider, mock_config_service):
        with patch("app.connectors.sources.github.connector.GithubApp"), \
             patch("app.connectors.sources.github.connector.DataSourceEntitiesProcessor") as MockProc:
            proc = MagicMock()
            proc.org_id = "org-1"
            proc.initialize = AsyncMock()
            MockProc.return_value = proc
            connector = await GithubConnector.create_connector(
                logger=mock_logger,
                data_store_provider=mock_data_store_provider,
                config_service=mock_config_service,
                connector_id="gh-test-1",
                scope="personal",
                created_by="test-user-id",
            )
            assert isinstance(connector, GithubConnector)
