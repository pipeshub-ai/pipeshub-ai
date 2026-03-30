"""Comprehensive tests for GitHub connector – targets uncovered lines."""

import base64
import logging
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.connectors.sources.github.connector import GithubConnector, RecordUpdate
from app.models.entities import RecordType


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.github.full")


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
    proc.on_record_deleted = AsyncMock()
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
            scope="personal",
            created_by="test-user-id",
        )
    return connector


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _make_issue(**kwargs):
    issue = MagicMock()
    issue.url = kwargs.get("url", "https://api.github.com/repos/owner/repo/issues/1")
    issue.html_url = kwargs.get("html_url", "https://github.com/owner/repo/issues/1")
    issue.title = kwargs.get("title", "Test Issue")
    issue.state = kwargs.get("state", "open")
    issue.created_at = kwargs.get("created_at", datetime(2024, 1, 1, tzinfo=timezone.utc))
    issue.updated_at = kwargs.get("updated_at", datetime(2024, 6, 1, tzinfo=timezone.utc))
    issue.repository_url = kwargs.get("repository_url", "https://api.github.com/repos/owner/repo")
    issue.labels = kwargs.get("labels", [])
    issue.assignees = kwargs.get("assignees", [])
    issue.raw_data = kwargs.get("raw_data", {})
    issue.pull_request = kwargs.get("pull_request", None)
    issue.body = kwargs.get("body", "issue body")
    issue.number = kwargs.get("number", 1)
    issue.repository = MagicMock()
    issue.repository.full_name = kwargs.get("full_name", "owner/repo")
    return issue


class TestDatetimeToEpochMs:
    def test_aware_datetime(self, github_connector):
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        result = github_connector.datetime_to_epoch_ms(dt)
        assert result == 1704067200000

    def test_naive_datetime(self, github_connector):
        dt = datetime(2024, 1, 1, 0, 0, 0)
        result = github_connector.datetime_to_epoch_ms(dt)
        assert result == 1704067200000


class TestGetIsoTime:
    def test_returns_iso_format(self, github_connector):
        result = github_connector._get_iso_time()
        assert result.endswith("Z")
        assert "T" in result


class TestGetApiToken:
    @pytest.mark.asyncio
    async def test_returns_token(self, github_connector):
        token = await github_connector._get_api_token_()
        assert token == "ghp_test_token"

    @pytest.mark.asyncio
    async def test_no_config_raises(self, github_connector):
        github_connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await github_connector._get_api_token_()

    @pytest.mark.asyncio
    async def test_no_token_raises(self, github_connector):
        github_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {}
        })
        with pytest.raises(ValueError, match="not found"):
            await github_connector._get_api_token_()


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_none(self, github_connector):
        record = MagicMock()
        result = await github_connector.get_signed_url(record)
        assert result is None


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_data_source(self, github_connector):
        github_connector.data_source = MagicMock()
        await github_connector.cleanup()
        assert github_connector.data_source is None


class TestGetFilterOptions:
    def test_returns_none(self, github_connector):
        result = github_connector.get_filter_options()
        assert result is None


class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_returns_true(self, github_connector):
        result = await github_connector.handle_webhook_notification()
        assert result is True


class TestProcessComments:
    @pytest.mark.asyncio
    async def test_returns_none(self, github_connector):
        result = await github_connector._process_comments_to_commentrecord()
        assert result is None


class TestHandlePageUpsert:
    @pytest.mark.asyncio
    async def test_returns_none(self, github_connector):
        result = await github_connector._handle_page_upsert_event_issue()
        assert result is None


class TestLogRateLimit:
    @pytest.mark.asyncio
    async def test_unavailable(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_rate_limit.return_value = None
        await github_connector._log_rate_limit("test")

    @pytest.mark.asyncio
    async def test_success_with_datetime_reset(self, github_connector):
        github_connector.data_source = MagicMock()
        rate = MagicMock()
        rate.remaining = 4999
        rate.limit = 5000
        rate.reset = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        res = MagicMock()
        res.success = True
        res.data = MagicMock(rate=rate)
        github_connector.data_source.get_rate_limit.return_value = res
        await github_connector._log_rate_limit("check")

    @pytest.mark.asyncio
    async def test_success_with_int_reset(self, github_connector):
        github_connector.data_source = MagicMock()
        rate = MagicMock()
        rate.remaining = 100
        rate.limit = 5000
        rate.reset = 1704067200
        res = MagicMock()
        res.success = True
        res.data = MagicMock(rate=rate)
        github_connector.data_source.get_rate_limit.return_value = res
        await github_connector._log_rate_limit("int-reset")

    @pytest.mark.asyncio
    async def test_exception_handled(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_rate_limit.side_effect = Exception("fail")
        await github_connector._log_rate_limit("error")


class TestCleanGithubContent:
    @pytest.mark.asyncio
    async def test_removes_github_images(self, github_connector):
        text = '![image](https://github.com/user-attachments/assets/abc123)'
        clean, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 1
        assert attachments[0]["type"] == "image"

    @pytest.mark.asyncio
    async def test_keeps_non_github_links(self, github_connector):
        text = '[docs](https://docs.example.com/guide)'
        clean, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 0
        assert "docs" in clean

    @pytest.mark.asyncio
    async def test_removes_html_img_github(self, github_connector):
        text = '<img src="https://github.com/user-attachments/assets/img123" alt="pic"/>'
        clean, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 1
        assert attachments[0]["source"] == "html_img"

    @pytest.mark.asyncio
    async def test_keeps_non_github_html_img(self, github_connector):
        text = '<img src="https://other.com/image.png" alt="pic"/>'
        clean, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 0
        assert "other.com" in clean

    @pytest.mark.asyncio
    async def test_removes_file_attachments(self, github_connector):
        text = '[report.pdf](https://github.com/user-attachments/files/12345/report.pdf)'
        clean, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 1
        assert attachments[0]["source"] == "file_attachment"

    @pytest.mark.asyncio
    async def test_empty_text(self, github_connector):
        clean, attachments = await github_connector.clean_github_content("")
        assert clean == ""
        assert attachments == []

    @pytest.mark.asyncio
    async def test_cleans_up_whitespace(self, github_connector):
        text = "Line1\n\n\n\n\nLine2"
        clean, attachments = await github_connector.clean_github_content(text)
        assert "\n\n\n" not in clean

    @pytest.mark.asyncio
    async def test_non_https_github_link_kept(self, github_connector):
        text = '![img](http://github.com/user-attachments/assets/abc)'
        clean, attachments = await github_connector.clean_github_content(text)
        assert len(attachments) == 0


class TestEmbedImagesAsBase64:
    @pytest.mark.asyncio
    async def test_empty_body(self, github_connector):
        result = await github_connector.embed_images_as_base64("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_attachments(self, github_connector):
        result = await github_connector.embed_images_as_base64("plain text")
        assert result == "plain text"

    @pytest.mark.asyncio
    async def test_embeds_png_image(self, github_connector):
        github_connector.data_source = MagicMock()
        from PIL import Image
        img = Image.new("RGB", (1, 1), "red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        github_connector.data_source.get_img_bytes = AsyncMock(
            return_value=_make_response(True, png_bytes)
        )
        text = '![image](https://github.com/user-attachments/assets/abc123)'
        result = await github_connector.embed_images_as_base64(text)
        assert "data:image/" in result
        assert "base64," in result

    @pytest.mark.asyncio
    async def test_embeds_svg_image(self, github_connector):
        github_connector.data_source = MagicMock()
        svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
        github_connector.data_source.get_img_bytes = AsyncMock(
            return_value=_make_response(True, svg_bytes)
        )
        text = '![icon](https://github.com/user-attachments/assets/svg123)'
        result = await github_connector.embed_images_as_base64(text)
        assert "svg+xml" in result

    @pytest.mark.asyncio
    async def test_fetch_failure_skipped(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_img_bytes = AsyncMock(
            return_value=_make_response(False, error="not found")
        )
        text = '![image](https://github.com/user-attachments/assets/abc123)'
        result = await github_connector.embed_images_as_base64(text)
        assert "base64" not in result

    @pytest.mark.asyncio
    async def test_exception_during_embed_skipped(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_img_bytes = AsyncMock(
            side_effect=Exception("network error")
        )
        text = '![image](https://github.com/user-attachments/assets/abc123)'
        result = await github_connector.embed_images_as_base64(text)
        assert "base64" not in result


class TestMakeFileRecordsFromList:
    @pytest.mark.asyncio
    async def test_skips_image_attachments(self, github_connector):
        attachments = [{"type": "image", "href": "https://example.com/img.png", "filename": "img.png"}]
        result = await github_connector.make_file_records_from_list(attachments, MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_missing_url_or_name(self, github_connector):
        attachments = [{"type": "pdf", "href": None, "filename": "file.pdf"}]
        result = await github_connector.make_file_records_from_list(attachments, MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_creates_file_record(self, github_connector):
        parent = MagicMock()
        parent.external_record_id = "parent-ext-id"
        parent.record_type = RecordType.TICKET
        parent.external_record_group_id = "group-ext-id"
        attachments = [{"type": "pdf", "href": "https://example.com/doc.pdf", "filename": "doc.pdf"}]
        result = await github_connector.make_file_records_from_list(attachments, parent)
        assert len(result) == 1
        assert result[0].record.record_type == RecordType.FILE

    @pytest.mark.asyncio
    async def test_reuses_existing_record_id(self, github_connector, mock_data_store_provider):
        existing = MagicMock()
        existing.id = "existing-file-id"
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        parent = MagicMock()
        parent.external_record_id = "parent-ext-id"
        parent.record_type = RecordType.TICKET
        parent.external_record_group_id = "group-ext-id"
        attachments = [{"type": "pdf", "href": "https://example.com/doc.pdf", "filename": "doc.pdf"}]
        result = await github_connector.make_file_records_from_list(attachments, parent)
        assert result[0].record.id == "existing-file-id"


class TestMakeChildRecordsOfAttachments:
    @pytest.mark.asyncio
    async def test_existing_record_found(self, github_connector, mock_data_store_provider):
        existing = MagicMock()
        existing.id = "child-id"
        existing.record_name = "existing.pdf"
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        github_connector.clean_github_content = AsyncMock(
            return_value=("clean", [{"type": "pdf", "href": "https://github.com/user-attachments/files/123/doc.pdf", "filename": "doc.pdf"}])
        )
        child_records, remaining = await github_connector.make_child_records_of_attachments("raw md", MagicMock())
        assert len(child_records) == 1
        assert child_records[0].child_id == "child-id"
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_new_record_created(self, github_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        parent = MagicMock()
        parent.external_record_id = "parent-ext"
        parent.record_type = RecordType.TICKET
        parent.external_record_group_id = "group-ext"

        github_connector.clean_github_content = AsyncMock(
            return_value=("clean", [{"type": "pdf", "href": "https://github.com/user-attachments/files/123/new.pdf", "filename": "new.pdf"}])
        )
        child_records, remaining = await github_connector.make_child_records_of_attachments("raw", parent)
        assert len(child_records) == 1
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_image_skipped(self, github_connector):
        github_connector.clean_github_content = AsyncMock(
            return_value=("clean", [{"type": "image", "href": "https://github.com/user-attachments/assets/img.png"}])
        )
        child_records, remaining = await github_connector.make_child_records_of_attachments("raw", MagicMock())
        assert len(child_records) == 0
        assert len(remaining) == 0


class TestMakeBlockCommentOfAttachments:
    @pytest.mark.asyncio
    async def test_existing_record(self, github_connector, mock_data_store_provider):
        existing = MagicMock()
        existing.id = "comment-att-id"
        existing.record_name = "att.zip"
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        github_connector.clean_github_content = AsyncMock(
            return_value=("clean", [{"type": "zip", "href": "https://github.com/user-attachments/files/999/att.zip", "filename": "att.zip"}])
        )
        comment_atts, remaining = await github_connector.make_block_comment_of_attachments("raw", MagicMock())
        assert len(comment_atts) == 1
        assert comment_atts[0].id == "comment-att-id"

    @pytest.mark.asyncio
    async def test_new_record(self, github_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        parent = MagicMock()
        parent.external_record_id = "parent-ext"
        parent.record_type = RecordType.TICKET
        parent.external_record_group_id = "group-ext"

        github_connector.clean_github_content = AsyncMock(
            return_value=("clean", [{"type": "pdf", "href": "https://github.com/user-attachments/files/111/f.pdf", "filename": "f.pdf"}])
        )
        comment_atts, remaining = await github_connector.make_block_comment_of_attachments("raw", parent)
        assert len(comment_atts) == 1
        assert len(remaining) == 1


class TestProcessPrToPullRequest:
    @pytest.mark.asyncio
    async def test_new_pr(self, github_connector):
        issue = _make_issue(
            url="https://api.github.com/repos/owner/repo/issues/5",
            pull_request=MagicMock(),
        )
        pr = MagicMock()
        pr.url = "https://api.github.com/repos/owner/repo/pulls/5"
        pr.html_url = "https://github.com/owner/repo/pull/5"
        pr.title = "My PR"
        pr.state = "open"
        pr.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        pr.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
        pr.labels = []
        pr.mergeable = True

        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(True, pr)

        result = await github_connector._process_pr_to_pull_request(issue)
        assert result is not None
        assert result.is_new is True
        assert result.record.record_type == RecordType.PULL_REQUEST.value

    @pytest.mark.asyncio
    async def test_existing_pr_with_title_change(self, github_connector, mock_data_store_provider):
        existing = MagicMock()
        existing.id = "pr-rec-1"
        existing.record_name = "Old PR Title"
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        issue = _make_issue(
            url="https://api.github.com/repos/owner/repo/issues/5",
            pull_request=MagicMock(),
        )
        pr = MagicMock()
        pr.url = "https://api.github.com/repos/owner/repo/pulls/5"
        pr.html_url = "https://github.com/owner/repo/pull/5"
        pr.title = "New PR Title"
        pr.state = "open"
        pr.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
        pr.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
        pr.labels = []
        pr.mergeable = False

        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(True, pr)

        result = await github_connector._process_pr_to_pull_request(issue)
        assert result.is_updated is True
        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_fetch_pr_fails(self, github_connector):
        issue = _make_issue(
            url="https://api.github.com/repos/owner/repo/issues/5",
            pull_request=MagicMock(),
        )
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(False, error="not found")
        result = await github_connector._process_pr_to_pull_request(issue)
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_pr_no_data(self, github_connector):
        issue = _make_issue(
            url="https://api.github.com/repos/owner/repo/issues/5",
            pull_request=MagicMock(),
        )
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(True, None)
        result = await github_connector._process_pr_to_pull_request(issue)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, github_connector, mock_data_store_provider):
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_record_by_external_id = AsyncMock(side_effect=Exception("DB error"))

        issue = _make_issue(
            url="https://api.github.com/repos/owner/repo/issues/5",
            pull_request=MagicMock(),
        )
        pr = MagicMock()
        pr.url = "https://api.github.com/repos/owner/repo/pulls/5"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(True, pr)
        result = await github_connector._process_pr_to_pull_request(issue)
        assert result is None


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_file_no_data(self, github_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.weburl = "https://github.com/owner/repo/blob/main/file.md"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_attachment_files_content = AsyncMock(
            return_value=_make_response(True, None)
        )
        with pytest.raises(Exception):
            await github_connector.stream_record(record)


class TestBuildTicketBlocks:
    @pytest.mark.asyncio
    async def test_builds_blocks(self, github_connector):
        record = MagicMock()
        record.weburl = "https://github.com/owner/repo/issues/1"
        record.record_name = "Issue #1"
        record.external_record_id = "ext-1"

        issue = MagicMock()
        issue.url = "https://api.github.com/repos/owner/repo/issues/1"
        issue.body = "Issue body"
        issue.title = "Issue #1"
        issue.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)

        github_connector.data_source = MagicMock()
        github_connector.data_source.get_issue.return_value = _make_response(True, issue)
        github_connector.embed_images_as_base64 = AsyncMock(return_value="clean body")
        github_connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        github_connector._build_comment_blocks = AsyncMock(return_value=([], []))
        github_connector._process_new_records = AsyncMock()

        result = await github_connector._build_ticket_blocks(record)
        assert len(result.block_groups) >= 1

    @pytest.mark.asyncio
    async def test_issue_not_found_raises(self, github_connector):
        record = MagicMock()
        record.weburl = "https://github.com/owner/repo/issues/1"
        record.external_record_id = "ext-1"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_issue.return_value = _make_response(False, error="not found")
        with pytest.raises(Exception):
            await github_connector._build_ticket_blocks(record)


class TestBuildPullRequestBlocks:
    @pytest.mark.asyncio
    async def test_builds_blocks_with_commits_and_files(self, github_connector):
        record = MagicMock()
        record.weburl = "https://github.com/owner/repo/pull/2"
        record.record_name = "PR #2"
        record.external_record_id = "pr-ext-2"

        pr = MagicMock()
        pr.url = "https://api.github.com/repos/owner/repo/pulls/2"
        pr.html_url = "https://github.com/owner/repo/pull/2"
        pr.title = "PR #2"
        pr.body = "PR description"
        pr.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
        pr.commits_url = "https://api.github.com/repos/owner/repo/pulls/2/commits"

        commit = MagicMock()
        commit.html_url = "https://github.com/owner/repo/commit/abc123"
        commit.sha = "abc123"
        commit.commit = MagicMock()
        commit.commit.message = "feat: add feature"
        commit.commit.committer = MagicMock()
        commit.commit.committer.date = datetime(2024, 5, 1, tzinfo=timezone.utc)

        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(True, pr)
        github_connector.data_source.get_pull_commits.return_value = _make_response(True, [commit])
        github_connector.data_source.get_pull_file_changes.return_value = _make_response(True, [])
        github_connector.data_source.get_pull_review_comments.return_value = _make_response(True, [])
        github_connector.data_source.get_pull_reviews.return_value = _make_response(True, [])

        github_connector.embed_images_as_base64 = AsyncMock(return_value="clean pr body")
        github_connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        github_connector._build_comment_blocks = AsyncMock(return_value=([], []))
        github_connector._process_new_records = AsyncMock()

        result = await github_connector._build_pull_request_blocks(record)
        assert len(result.block_groups) >= 2
        assert len(result.blocks) >= 1

    @pytest.mark.asyncio
    async def test_no_weburl_raises(self, github_connector):
        record = MagicMock()
        record.weburl = ""
        with pytest.raises(ValueError, match="Web URL is required"):
            await github_connector._build_pull_request_blocks(record)

    @pytest.mark.asyncio
    async def test_pr_not_found_raises(self, github_connector):
        record = MagicMock()
        record.weburl = "https://github.com/owner/repo/pull/2"
        record.external_record_id = "pr-ext-2"
        github_connector.data_source = MagicMock()
        github_connector.data_source.get_pull.return_value = _make_response(False, error="not found")
        with pytest.raises(Exception):
            await github_connector._build_pull_request_blocks(record)


class TestBuildCommentBlocks:
    @pytest.mark.asyncio
    async def test_builds_comment_block_groups(self, github_connector):
        comment = MagicMock()
        comment.body = "Great work!"
        comment.user = MagicMock(login="reviewer")
        comment.url = "https://api.github.com/repos/owner/repo/issues/comments/1"
        comment.html_url = "https://github.com/owner/repo/issues/1#issuecomment-1"
        comment.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)

        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(True, [comment])
        github_connector.embed_images_as_base64 = AsyncMock(return_value="Great work!")
        github_connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))

        record = MagicMock()
        block_groups, remaining = await github_connector._build_comment_blocks(
            "https://github.com/owner/repo/issues/1", 0, record
        )
        assert len(block_groups) == 1
        assert "reviewer" in block_groups[0].name

    @pytest.mark.asyncio
    async def test_no_comments(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(True, [])
        record = MagicMock()
        block_groups, remaining = await github_connector._build_comment_blocks(
            "https://github.com/owner/repo/issues/1", 0, record
        )
        assert block_groups == []
        assert remaining == []

    @pytest.mark.asyncio
    async def test_error_fetching_comments(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.data_source.list_issue_comments.return_value = _make_response(False, error="fail")
        record = MagicMock()
        block_groups, remaining = await github_connector._build_comment_blocks(
            "https://github.com/owner/repo/issues/1", 0, record
        )
        assert block_groups == []


class TestFetchIssuesBatchedSyncPoint:
    @pytest.mark.asyncio
    async def test_sync_point_large_timestamp(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1704067200000}
        )
        github_connector.data_source.list_issues.return_value = _make_response(True, [])
        result = await github_connector._fetch_issues_batched("owner/repo")
        call_args = github_connector.data_source.list_issues.call_args
        assert call_args.kwargs.get("since") is not None

    @pytest.mark.asyncio
    async def test_sync_point_small_timestamp(self, github_connector):
        github_connector.data_source = MagicMock()
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1704067200}
        )
        github_connector.data_source.list_issues.return_value = _make_response(True, [])
        result = await github_connector._fetch_issues_batched("owner/repo")
        call_args = github_connector.data_source.list_issues.call_args
        assert call_args.kwargs.get("since") is not None


class TestProcessNewRecordsSyncUpdate:
    @pytest.mark.asyncio
    async def test_pr_record_updates_sync(self, github_connector):
        rec = MagicMock()
        rec.record_type = RecordType.PULL_REQUEST
        rec.source_updated_at = 1704067200000
        rec.external_record_group_id = "https://api.github.com/repos/owner/repo"
        update = RecordUpdate(
            record=rec, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        github_connector._update_sync_point_of_issue = AsyncMock()
        await github_connector._process_new_records([update])
        github_connector._update_sync_point_of_issue.assert_awaited()

    @pytest.mark.asyncio
    async def test_multiple_batches(self, github_connector):
        github_connector.batch_size = 1
        rec1 = MagicMock()
        rec1.record_type = RecordType.TICKET
        rec1.source_updated_at = 1000
        rec1.external_record_group_id = "https://api.github.com/repos/owner/repo"
        rec2 = MagicMock()
        rec2.record_type = RecordType.TICKET
        rec2.source_updated_at = 2000
        rec2.external_record_group_id = "https://api.github.com/repos/owner/repo"
        updates = [
            RecordUpdate(record=rec1, is_new=True, is_updated=False, is_deleted=False,
                         metadata_changed=False, content_changed=False, permissions_changed=False,
                         new_permissions=[]),
            RecordUpdate(record=rec2, is_new=True, is_updated=False, is_deleted=False,
                         metadata_changed=False, content_changed=False, permissions_changed=False,
                         new_permissions=[]),
        ]
        github_connector._update_sync_point_of_issue = AsyncMock()
        await github_connector._process_new_records(updates)
        assert github_connector.data_entities_processor.on_new_records.call_count == 2


class TestCreateConnectorFactory:
    @pytest.mark.asyncio
    async def test_factory_method(self):
        with patch("app.connectors.sources.github.connector.DataSourceEntitiesProcessor") as mock_dep, \
             patch("app.connectors.sources.github.connector.GithubApp"):
            mock_dep_instance = MagicMock()
            mock_dep_instance.initialize = AsyncMock()
            mock_dep_instance.org_id = "org-1"
            mock_dep.return_value = mock_dep_instance
            logger = MagicMock()
            dsp = MagicMock()
            cs = AsyncMock()
            conn = await GithubConnector.create_connector(logger, dsp, cs, "gh-factory")
            assert isinstance(conn, GithubConnector)


class TestUpdateSyncPointException:
    @pytest.mark.asyncio
    async def test_exception_swallowed(self, github_connector):
        github_connector.record_sync_point = MagicMock()
        github_connector.record_sync_point.update_sync_point = AsyncMock(side_effect=Exception("fail"))
        await github_connector._update_sync_point_of_issue("repo", 1000)
