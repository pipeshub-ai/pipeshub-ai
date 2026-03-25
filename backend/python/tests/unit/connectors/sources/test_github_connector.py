"""Tests for GitHub connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.github.connector import GithubConnector, RecordUpdate
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.github")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-gh-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_app_creator_user = AsyncMock(return_value=MagicMock(email="dev@test.com"))
    proc.on_record_metadata_update = AsyncMock()
    proc.on_record_content_update = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


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
            connector_id="gh-conn-1",
        )
    return connector


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


# ===========================================================================
# RecordUpdate
# ===========================================================================
class TestGitHubRecordUpdate:
    def test_default_values(self):
        ru = RecordUpdate(
            record=None, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        assert ru.old_permissions is None
        assert ru.new_permissions is None

    def test_with_all_fields(self):
        ru = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=True, permissions_changed=True,
            old_permissions=[MagicMock()], new_permissions=[MagicMock()],
            external_record_id="ext-gh-1",
        )
        assert ru.external_record_id == "ext-gh-1"


# ===========================================================================
# Init / Connection
# ===========================================================================
class TestGithubConnectorInit:
    def test_constructor(self, github_connector):
        assert github_connector.connector_id == "gh-conn-1"
        assert github_connector.data_source is None
        assert github_connector.batch_size == 5

    def test_sync_points_created(self, github_connector):
        assert github_connector.record_sync_point is not None

    @patch("app.connectors.sources.github.connector.GitHubClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.github.connector.GitHubDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, github_connector):
        mock_client = MagicMock()
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        result = await github_connector.init()
        assert result is True
        assert github_connector.external_client is mock_client

    @patch("app.connectors.sources.github.connector.GitHubClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_exception(self, mock_build, github_connector):
        mock_build.side_effect = Exception("Auth error")
        assert await github_connector.init() is False


class TestGithubTestConnection:
    async def test_not_initialized(self, github_connector):
        assert await github_connector.test_connection_and_access() is False

    async def test_success(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(True)
        assert await github_connector.test_connection_and_access() is True

    async def test_auth_failure(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(False, error="Bad creds")
        assert await github_connector.test_connection_and_access() is False

    async def test_exception(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.side_effect = Exception("Network error")
        assert await github_connector.test_connection_and_access() is False


# ===========================================================================
# Stream record
# ===========================================================================
class TestGithubStreamRecord:
    async def test_unsupported_record_type(self, github_connector):
        from fastapi import HTTPException
        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"
        with pytest.raises(HTTPException):
            await github_connector.stream_record(record)

    async def test_stream_ticket(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.TICKET
        record.record_name = "Issue #1"
        github_connector._build_ticket_blocks = AsyncMock(return_value=MagicMock(
            model_dump_json=MagicMock(return_value='{"blocks": []}')
        ))
        result = await github_connector.stream_record(record)
        assert result is not None

    async def test_stream_pull_request(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.PULL_REQUEST
        record.record_name = "PR #1"
        github_connector._build_pull_request_blocks = AsyncMock(return_value=MagicMock(
            model_dump_json=MagicMock(return_value='{"blocks": []}')
        ))
        result = await github_connector.stream_record(record)
        assert result is not None

    async def test_stream_file(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "file.md"
        record.weburl = "https://github.com/owner/repo/blob/main/file.md"
        record.mime_type = "text/markdown"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_attachment_files_content = AsyncMock(
            return_value=_make_response(True, "# File content")
        )
        result = await github_connector.stream_record(record)
        assert result is not None

    async def test_stream_file_fetch_fails(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.weburl = "https://github.com/owner/repo/blob/main/file.md"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_attachment_files_content = AsyncMock(
            return_value=_make_response(False, error="Not found")
        )
        with pytest.raises(Exception):
            await github_connector.stream_record(record)


# ===========================================================================
# Fetch users
# ===========================================================================
class TestGithubFetchUsers:
    async def test_returns_empty_if_not_initialized(self, github_connector):
        github_connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await github_connector._fetch_users()

    async def test_returns_empty_on_auth_failure(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(False, error="Token expired")
        assert await github_connector._fetch_users() == []

    async def test_returns_empty_if_no_creator_user(self, github_connector):
        github_connector.data_source = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock(login="testuser")
        github_connector.data_source.get_authenticated.return_value = resp
        github_connector.data_entities_processor.get_app_creator_user = AsyncMock(return_value=None)
        assert await github_connector._fetch_users() == []

    async def test_returns_user_on_success(self, github_connector):
        github_connector.data_source = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock(login="testuser")
        github_connector.data_source.get_authenticated.return_value = resp
        result = await github_connector._fetch_users()
        assert len(result) == 1
        assert result[0].full_name == "testuser"


# ===========================================================================
# Sync points
# ===========================================================================
class TestGithubSyncPoints:
    async def test_fetch_sync_point_of_issue(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1000}
        )
        result = await github_connector._fetch_sync_point_of_issue("repo-name")
        assert result == 1000

    async def test_fetch_sync_point_of_issue_none(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await github_connector._fetch_sync_point_of_issue("repo-name")
        assert result is None

    async def test_fetch_sync_point_of_issue_exception(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(side_effect=Exception("fail"))
        result = await github_connector._fetch_sync_point_of_issue("repo-name")
        assert result is None

    async def test_update_sync_point(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.update_sync_point = AsyncMock()
        await github_connector._update_sync_point_of_issue("repo-name", 2000)
        github_connector.record_sync_point.update_sync_point.assert_awaited_once()


# ===========================================================================
# Run sync
# ===========================================================================
class TestGithubRunSync:
    async def test_sync_raises_on_no_users(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_authenticated.return_value = _make_response(False, error="Auth error")
        with pytest.raises(ValueError, match="Failed to retrieve"):
            await github_connector.run_sync()

    async def test_sync_full_flow(self, github_connector):
        github_connector.data_source = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock(login="testuser")
        github_connector.data_source.get_authenticated.return_value = resp
        github_connector._sync_all_repo_issue = AsyncMock()
        await github_connector.run_sync()
        github_connector._sync_all_repo_issue.assert_awaited_once()

    async def test_sync_exception_propagated(self, github_connector):
        github_connector.data_source = MagicMock()
        resp = MagicMock()
        resp.success = True
        resp.data = MagicMock(login="testuser")
        github_connector.data_source.get_authenticated.return_value = resp
        github_connector._sync_all_repo_issue = AsyncMock(side_effect=Exception("boom"))
        with pytest.raises(Exception, match="boom"):
            await github_connector.run_sync()


# ===========================================================================
# Issue processing
# ===========================================================================
class TestGithubIssueProcessing:
    async def test_process_issue_to_ticket_new(self, github_connector):
        github_connector.bookstack_base_url = "https://bs.example.com/"
        issue = MagicMock()
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        issue.html_url = "https://github.com/owner/repo/issues/1"
        issue.title = "Bug fix"
        issue.state = "open"
        issue.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        issue.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
        issue.repository_url = "https://api.github.com/repos/owner/repo"
        issue.labels = []
        issue.assignees = []
        issue.raw_data = {}
        result = await github_connector._process_issue_to_ticket(issue)
        assert result is not None
        assert result.is_new is True
        assert result.record.record_name == "Bug fix"

    async def test_process_issue_to_ticket_existing(self, github_connector, mock_data_store_provider):
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "Old Title"
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        issue = MagicMock()
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        issue.html_url = "https://github.com/owner/repo/issues/1"
        issue.title = "New Title"
        issue.state = "closed"
        issue.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        issue.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
        issue.repository_url = "https://api.github.com/repos/owner/repo"
        issue.labels = []
        issue.assignees = [MagicMock(login="dev1")]
        issue.raw_data = {}
        result = await github_connector._process_issue_to_ticket(issue)
        assert result.is_updated is True
        assert result.metadata_changed is True

    async def test_process_issue_to_ticket_exception(self, github_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("DB error"))
        issue = MagicMock()
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        result = await github_connector._process_issue_to_ticket(issue)
        assert result is None

    async def test_process_issue_with_parent(self, github_connector):
        issue = MagicMock()
        issue.url = "https://api.github.com/repos/owner/repo/issues/2"
        issue.html_url = "https://github.com/owner/repo/issues/2"
        issue.title = "Sub issue"
        issue.state = "open"
        issue.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        issue.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
        issue.repository_url = "https://api.github.com/repos/owner/repo"
        label = MagicMock()
        label.name = "bug"
        issue.labels = [label]
        issue.assignees = []
        issue.raw_data = {"parent_issue_url": "https://api.github.com/repos/owner/repo/issues/1"}
        result = await github_connector._process_issue_to_ticket(issue)
        assert result is not None
        assert result.record.parent_external_record_id == "https://api.github.com/repos/owner/repo/issues/1"


# ===========================================================================
# Handle record updates
# ===========================================================================
class TestGithubHandleRecordUpdates:
    async def test_handle_updated_metadata(self, github_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="R"), is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=True, content_changed=False,
            permissions_changed=False,
        )
        await github_connector._handle_record_updates(update)
        github_connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()

    async def test_handle_updated_content(self, github_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="R"), is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=False, content_changed=True,
            permissions_changed=False,
        )
        await github_connector._handle_record_updates(update)
        github_connector.data_entities_processor.on_record_content_update.assert_awaited_once()

    async def test_handle_deleted(self, github_connector):
        update = RecordUpdate(
            record=MagicMock(record_name="R"), is_new=False, is_updated=False,
            is_deleted=True, metadata_changed=False, content_changed=False,
            permissions_changed=False,
        )
        await github_connector._handle_record_updates(update)

    async def test_handle_exception(self, github_connector):
        github_connector.data_entities_processor.on_record_metadata_update = AsyncMock(side_effect=Exception("fail"))
        update = RecordUpdate(
            record=MagicMock(record_name="R"), is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=True, content_changed=False,
            permissions_changed=False,
        )
        await github_connector._handle_record_updates(update)


# ===========================================================================
# Comment and attachment methods
# ===========================================================================
class TestGithubCommentsAndAttachments:
    async def test_make_issue_comment_records_no_comments(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(True, [])
        issue = MagicMock()
        issue.repository = MagicMock()
        issue.repository.full_name = "owner/repo"
        issue.number = 1
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        result = await github_connector.make_issue_comment_records(issue, MagicMock())
        assert result == []

    async def test_make_issue_comment_records_error(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(False, error="fail")
        issue = MagicMock()
        issue.repository = MagicMock()
        issue.repository.full_name = "owner/repo"
        issue.number = 1
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        result = await github_connector.make_issue_comment_records(issue, MagicMock())
        assert result == []

    async def test_make_r_comment_attachments_no_comments(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull_review_comments.return_value = _make_response(True, [])
        issue = MagicMock()
        issue.repository = MagicMock()
        issue.repository.full_name = "owner/repo"
        issue.number = 1
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        result = await github_connector.make_r_comment_attachments(issue, MagicMock())
        assert result == []

    async def test_make_reviews_attachments_no_reviews(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull_reviews.return_value = _make_response(True, [])
        issue = MagicMock()
        issue.repository = MagicMock()
        issue.repository.full_name = "owner/repo"
        issue.number = 1
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        result = await github_connector.make_reviews_attachments(issue, MagicMock())
        assert result == []

    async def test_make_reviews_attachments_error(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull_reviews.return_value = _make_response(False, error="fail")
        issue = MagicMock()
        issue.repository = MagicMock()
        issue.repository.full_name = "owner/repo"
        issue.number = 1
        result = await github_connector.make_reviews_attachments(issue, MagicMock())
        assert result == []


# ===========================================================================
# Process new records
# ===========================================================================
class TestGithubProcessNewRecords:
    async def test_process_new_records_success(self, github_connector):
        rec = MagicMock()
        rec.record_type = RecordType.TICKET
        rec.source_updated_at = 1000
        rec.external_record_group_id = "https://api.github.com/repos/owner/repo"
        update = RecordUpdate(
            record=rec, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        github_connector._update_sync_point_of_issue = AsyncMock()
        await github_connector._process_new_records([update])
        github_connector.data_entities_processor.on_new_records.assert_awaited()

    async def test_process_new_records_exception(self, github_connector):
        github_connector.data_entities_processor.on_new_records = AsyncMock(side_effect=Exception("fail"))
        rec = MagicMock()
        rec.record_type = RecordType.FILE
        update = RecordUpdate(
            record=rec, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        await github_connector._process_new_records([update])


# ===========================================================================
# Stub methods
# ===========================================================================
class TestGithubStubMethods:
    async def test_sync_records_incremental(self, github_connector):
        result = await github_connector._sync_records_incremental()
        assert result is None

    async def test_reindex_records(self, github_connector):
        result = await github_connector.reindex_records()
        assert result is None

    async def test_run_incremental_sync(self, github_connector):
        result = await github_connector.run_incremental_sync()
        assert result is None
