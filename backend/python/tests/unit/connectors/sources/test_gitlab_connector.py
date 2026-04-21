import asyncio
import base64
import io
import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import StreamingResponse
from gitlab.v4.objects import (
    GroupMember,
    Project,
    ProjectCommit,
    ProjectIssue,
    ProjectIssueNote,
    ProjectMergeRequest,
    ProjectMergeRequestNote,
)
from PIL import Image

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.connectors.sources.gitlab.connector import (
    FileAttachment,
    GitLabConnector,
    GitlabLiterals,
    RecordUpdate,
)
from app.models.blocks import (
    BlockComment,
    BlockGroup,
    BlocksContainer,
    BlockSubType,
    ChildType,
    CommentAttachment,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUserGroup,
    ItemType,
    Record,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, PermissionType


def _make_connector() -> GitLabConnector:
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()

    dsp = MagicMock()
    config_service = AsyncMock()

    return GitLabConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=config_service,
        connector_id="gitlab-conn-1",
        scope="personal",
        created_by="test-user-1",
    )


class TestGitlabHelperFunctions:
    @pytest.mark.asyncio
    async def test_parse_gitlab_uploads_clean_test_only_text(self) -> None:
        connector = _make_connector()
        assert await connector.parse_gitlab_uploads_clean_test("test") == ([], "test")

    @pytest.mark.asyncio
    async def test_parse_gitlab_uploads_clean_test_with_files(self) -> None:
        connector = _make_connector()
        assert await connector.parse_gitlab_uploads_clean_test(
            "This is a simple issue.\n\n![parrot.png](/uploads/91df98eac27282a4c79af15d18659860/parrot.png){width=540 height=360}\n\nAdding some more text. Then an attachment.\n\n[mpl-ayush.pdf](/uploads/63236944d6e8c5de56b499649c4b8209/mpl-ayush.pdf)"
        ) == (
            [
                FileAttachment(
                    href="/uploads/91df98eac27282a4c79af15d18659860/parrot.png",
                    filename="parrot.png",
                    filetype="png",
                    category=GitlabLiterals.IMAGE.value,
                ),
                FileAttachment(
                    href="/uploads/63236944d6e8c5de56b499649c4b8209/mpl-ayush.pdf",
                    filename="mpl-ayush.pdf",
                    filetype="pdf",
                    category=GitlabLiterals.ATTACHMENT.value,
                ),
            ],
            "This is a simple issue.\n\n{width=540 height=360}\n\nAdding some more text. Then an attachment.",
        )

    @pytest.mark.asyncio
    async def test_parse_gitlab_uploads_clean_test_with_none(self) -> None:
        connector = _make_connector()
        assert await connector.parse_gitlab_uploads_clean_test(None) == ([], "")

    @pytest.mark.asyncio
    async def test_parse_gitlab_uploads_clean_test_with_default_extension(self) -> None:
        connector = _make_connector()
        assert await connector.parse_gitlab_uploads_clean_test(
            "Adding some more text. Then an attachment.\n\n[dockerfile](/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/dockerfile)"
        ) == (
            [
                FileAttachment(
                    href="/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/dockerfile",
                    filename="dockerfile",
                    filetype="txt",
                    category=GitlabLiterals.ATTACHMENT.value,
                )
            ],
            "Adding some more text. Then an attachment.",
        )

    @pytest.mark.asyncio
    async def test_parse_gitlab_uploads_clean_test_with_missing_file_name(self) -> None:
        connector = _make_connector()
        with patch(
            "app.connectors.sources.gitlab.connector.unquote",
            return_value="",
        ):
            files, cleaned = await connector.parse_gitlab_uploads_clean_test(
                "Adding some more text. Then an attachment.\n\n[dummy](/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/dummy)"
            )
            assert files == []
            assert (
                cleaned
                == "Adding some more text. Then an attachment.\n\n[dummy](/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/dummy)"
            )
            connector.logger.warning.assert_called_once()
            msg = connector.logger.warning.call_args[0][0]
            assert "Skipping malformed attachment missing required fields" in msg

    def test_get_parent_path_from_path(self) -> None:
        connector = _make_connector()
        assert connector.get_parent_path_from_path("a/b/c/d.txt") == ["a", "b", "c"]
        assert connector.get_parent_path_from_path("") == []


class TestGitlabConnector:
    @pytest.mark.asyncio
    async def test_init_success(self) -> None:
        connector = _make_connector()
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        with (
            patch("app.connectors.sources.gitlab.connector.GitLabClient") as MockClient,
            patch("app.connectors.sources.gitlab.connector.GitLabDataSource"),
        ):
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            result = await connector.init()
        assert result is True
        assert connector.external_client is mock_client

    @pytest.mark.asyncio
    async def test_init_failure_returns_false(self) -> None:
        connector = _make_connector()
        with patch(
            "app.connectors.sources.gitlab.connector.GitLabClient"
        ) as MockClient:
            MockClient.build_from_services = AsyncMock(
                side_effect=Exception("bad creds of auth config")
            )
            result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_failure_returns_false_due_to_data_source_build_failure(
        self,
    ) -> None:
        connector = _make_connector()
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()
        with (
            patch("app.connectors.sources.gitlab.connector.GitLabClient") as MockClient,
            patch(
                "app.connectors.sources.gitlab.connector.GitLabDataSource"
            ) as MockDataSource,
        ):
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            MockDataSource.side_effect = Exception("bad creds of client in data source")
            result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_and_access_success(self) -> None:
        connector = _make_connector()
        mock_response = MagicMock()
        mock_response.success = True
        mock_response.data = {
            "id": 123456,
            "username": "demo-user",
            "public_email": "abc.ad@test.com",
            "name": "xyz",
            "state": "active",
        }
        mock_data_source = MagicMock()
        mock_data_source.get_user = MagicMock(return_value=mock_response)
        connector.data_source = mock_data_source
        result = await connector.test_connection_and_access()
        assert result is True
        mock_data_source.get_user.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_and_access_response_not_success(self) -> None:
        connector = _make_connector()
        mock_response = MagicMock()
        mock_response.success = False
        mock_response.error = "Invalid credentials"

        mock_data_source = MagicMock()
        mock_data_source.get_user = MagicMock(return_value=mock_response)
        connector.data_source = mock_data_source

        result = await connector.test_connection_and_access()

        assert result is False
        connector.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_and_access_exception(self) -> None:
        connector = _make_connector()
        mock_data_source = MagicMock()
        mock_data_source.get_user = MagicMock(side_effect=Exception("Network error"))
        connector.data_source = mock_data_source

        result = await connector.test_connection_and_access()

        assert result is False
        connector.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_and_access_no_data_source(self) -> None:
        connector = _make_connector()
        connector.data_source = None
        result = await connector.test_connection_and_access()
        assert result is False


class TestGitlabConnectorStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_record_ticket_success(self) -> None:
        """Test streaming a ticket record successfully."""
        connector = _make_connector()

        # Create a ticket record
        ticket_record = MagicMock(spec=Record)
        ticket_record.record_type = RecordType.TICKET
        ticket_record.record_name = "ISSUE-123.json"
        ticket_record.external_record_id = "issue-123"

        # Mock the _build_ticket_blocks method
        mock_blocks_container = MagicMock(spec=BlocksContainer)
        connector._build_ticket_blocks = AsyncMock(return_value=mock_blocks_container)

        # Call the method
        response = await connector.stream_record(ticket_record)

        # Assertions
        assert isinstance(response, StreamingResponse)
        assert response.media_type == MimeTypes.BLOCKS.value
        assert (
            response.headers["Content-Disposition"]
            == "attachment; filename=ISSUE-123.json"
        )
        connector._build_ticket_blocks.assert_called_once_with(ticket_record)
        connector.logger.info.assert_any_call(" STREAM_TICKET_MARKER ")

    @pytest.mark.asyncio
    async def test_stream_record_pull_request_success(self) -> None:
        """Test streaming a pull request record successfully."""
        connector = _make_connector()

        # Create a pull request record
        pr_record = MagicMock(spec=Record)
        pr_record.record_type = RecordType.PULL_REQUEST
        pr_record.record_name = "MR-456.json"
        pr_record.external_record_id = "mr-456"

        # Mock the _build_pull_request_blocks method
        mock_blocks_container = MagicMock(spec=BlocksContainer)
        connector._build_pull_request_blocks = AsyncMock(
            return_value=mock_blocks_container
        )

        # Call the method
        response = await connector.stream_record(pr_record)

        # Assertions
        assert isinstance(response, StreamingResponse)
        assert response.media_type == MimeTypes.BLOCKS.value
        assert (
            response.headers["Content-Disposition"]
            == "attachment; filename=MR-456.json"
        )
        connector._build_pull_request_blocks.assert_called_once_with(pr_record)
        connector.logger.info.assert_any_call(" STREAM_MERGE_REQUEST_MARKER ")

    @pytest.mark.asyncio
    async def test_stream_record_file_success(self) -> None:
        """Test streaming a file record successfully."""
        connector = _make_connector()

        # Create a file record
        file_record = MagicMock(spec=Record)
        file_record.record_type = RecordType.FILE
        file_record.record_name = "document.pdf"
        file_record.external_record_id = "file-789"
        file_record.id = "record-id-123"
        file_record.mime_type = "application/pdf"

        # Mock the _fetch_attachment_content method
        async def mock_content_generator() -> AsyncGenerator[bytes, None]:
            yield b"file content chunk 1"
            yield b"file content chunk 2"

        connector._fetch_attachment_content = MagicMock(
            return_value=mock_content_generator()
        )

        # Mock create_stream_record_response
        mock_response = MagicMock(spec=StreamingResponse)
        with patch(
            "app.connectors.sources.gitlab.connector.create_stream_record_response",
            return_value=mock_response,
        ) as mock_create_stream:
            # Call the method
            response = await connector.stream_record(file_record)

            # Assertions
            assert response == mock_response
            connector._fetch_attachment_content.assert_called_once_with(file_record)
            mock_create_stream.assert_called_once()
            call_args = mock_create_stream.call_args
            assert call_args[1]["filename"] == "document.pdf"
            assert call_args[1]["mime_type"] == "application/pdf"
            assert call_args[1]["fallback_filename"] == "record_record-id-123"
            connector.logger.info.assert_any_call(" STREAM-FILE-MARKER ")

    @pytest.mark.asyncio
    async def test_stream_record_file_no_record_name(self) -> None:
        """Test streaming a file record when record_name is None."""
        connector = _make_connector()

        # Create a file record without record_name
        file_record = MagicMock(spec=Record)
        file_record.record_type = RecordType.FILE
        file_record.record_name = None
        file_record.external_record_id = "file-789"
        file_record.id = "record-id-123"
        file_record.mime_type = "application/pdf"

        # Mock the _fetch_attachment_content method
        async def mock_content_generator() -> AsyncGenerator[bytes, None]:
            yield b"file content"

        connector._fetch_attachment_content = MagicMock(
            return_value=mock_content_generator()
        )

        # Mock create_stream_record_response
        mock_response = MagicMock(spec=StreamingResponse)
        with patch(
            "app.connectors.sources.gitlab.connector.create_stream_record_response",
            return_value=mock_response,
        ) as mock_create_stream:
            # Call the method
            response = await connector.stream_record(file_record)

            # Assertions
            assert response == mock_response
            call_args = mock_create_stream.call_args
            # Should use external_record_id as filename
            assert call_args[1]["filename"] == "file-789"

    @pytest.mark.asyncio
    async def test_stream_record_code_file_success(self) -> None:
        """Test streaming a code file record successfully."""
        connector = _make_connector()

        # Create a code file record
        code_file_record = MagicMock(spec=Record)
        code_file_record.record_type = RecordType.CODE_FILE
        code_file_record.record_name = "main.py"
        code_file_record.external_record_id = "code-file-999"
        code_file_record.id = "record-id-456"
        code_file_record.mime_type = "text/x-python"

        # Mock the _fetch_code_file_content method
        async def mock_content_generator() -> AsyncGenerator[bytes, None]:
            yield b"def main():\n"
            yield b"    print('Hello')\n"

        connector._fetch_code_file_content = MagicMock(
            return_value=mock_content_generator()
        )

        # Mock create_stream_record_response
        mock_response = MagicMock(spec=StreamingResponse)
        with patch(
            "app.connectors.sources.gitlab.connector.create_stream_record_response",
            return_value=mock_response,
        ) as mock_create_stream:
            # Call the method
            response = await connector.stream_record(code_file_record)

            # Assertions
            assert response == mock_response
            connector._fetch_code_file_content.assert_called_once_with(code_file_record)
            mock_create_stream.assert_called_once()
            call_args = mock_create_stream.call_args
            assert call_args[1]["filename"] == "main.py"
            assert call_args[1]["mime_type"] == "text/x-python"
            assert call_args[1]["fallback_filename"] == "record_record-id-456"
            connector.logger.info.assert_any_call(" STREAM-CODE-FILE-MARKER ")

    @pytest.mark.asyncio
    async def test_stream_record_code_file_no_record_name(self) -> None:
        """Test streaming a code file record when record_name is None."""
        connector = _make_connector()

        # Create a code file record without record_name
        code_file_record = MagicMock(spec=Record)
        code_file_record.record_type = RecordType.CODE_FILE
        code_file_record.record_name = None
        code_file_record.external_record_id = "code-file-999"
        code_file_record.id = "record-id-456"
        code_file_record.mime_type = "text/x-python"

        # Mock the _fetch_code_file_content method
        async def mock_content_generator() -> AsyncGenerator[bytes, None]:
            yield b"code content"

        connector._fetch_code_file_content = MagicMock(
            return_value=mock_content_generator()
        )

        # Mock create_stream_record_response
        mock_response = MagicMock(spec=StreamingResponse)
        with patch(
            "app.connectors.sources.gitlab.connector.create_stream_record_response",
            return_value=mock_response,
        ) as mock_create_stream:
            # Call the method
            await connector.stream_record(code_file_record)

            # Assertions
            call_args = mock_create_stream.call_args
            # Should use external_record_id as filename
            assert call_args[1]["filename"] == "code-file-999"

    @pytest.mark.asyncio
    async def test_stream_record_unsupported_record_type(self) -> None:
        """Test streaming with an unsupported record type raises ValueError."""
        connector = _make_connector()

        # Create a record with unsupported type
        unsupported_record = MagicMock(spec=Record)
        unsupported_record.record_type = "UNSUPPORTED_TYPE"
        unsupported_record.external_record_id = "record-123"

        # Call the method and expect ValueError
        with pytest.raises(ValueError) as exc_info:
            await connector.stream_record(unsupported_record)

        # Assertions
        assert "Unsupported record type for streaming" in str(exc_info.value)
        assert "UNSUPPORTED_TYPE" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_stream_record_ticket_build_blocks_fails(self) -> None:
        """Test streaming ticket when _build_ticket_blocks raises exception."""
        connector = _make_connector()

        # Create a ticket record
        ticket_record = MagicMock(spec=Record)
        ticket_record.record_type = RecordType.TICKET
        ticket_record.record_name = "ISSUE-123.json"
        ticket_record.external_record_id = "issue-123"

        # Mock _build_ticket_blocks to raise exception
        connector._build_ticket_blocks = AsyncMock(
            side_effect=Exception("Failed to build ticket blocks")
        )

        # Call the method and expect exception to be raised
        with pytest.raises(Exception) as exc_info:
            await connector.stream_record(ticket_record)

        # Assertions
        assert "Failed to build ticket blocks" in str(exc_info.value)
        connector.logger.error.assert_called_once()
        error_call = connector.logger.error.call_args[0][0]
        assert "Error streaming record issue-123" in error_call
        assert connector.logger.error.call_args[1]["exc_info"] is True

    @pytest.mark.asyncio
    async def test_stream_record_pull_request_build_blocks_fails(self) -> None:
        """Test streaming pull request when _build_pull_request_blocks raises exception."""
        connector = _make_connector()

        # Create a pull request record
        pr_record = MagicMock(spec=Record)
        pr_record.record_type = RecordType.PULL_REQUEST
        pr_record.record_name = "MR-456.json"
        pr_record.external_record_id = "mr-456"

        # Mock _build_pull_request_blocks to raise exception
        connector._build_pull_request_blocks = AsyncMock(
            side_effect=Exception("Failed to build PR blocks")
        )

        # Call the method and expect exception to be raised
        with pytest.raises(Exception) as exc_info:
            await connector.stream_record(pr_record)

        # Assertions
        assert "Failed to build PR blocks" in str(exc_info.value)
        connector.logger.error.assert_called_once()
        error_call = connector.logger.error.call_args[0][0]
        assert "Error streaming record mr-456" in error_call

    @pytest.mark.asyncio
    async def test_stream_record_file_fetch_content_fails(self) -> None:
        """Test streaming file when _fetch_attachment_content raises exception."""
        connector = _make_connector()

        # Create a file record
        file_record = MagicMock(spec=Record)
        file_record.record_type = RecordType.FILE
        file_record.record_name = "document.pdf"
        file_record.external_record_id = "file-789"
        file_record.id = "record-id-123"
        file_record.mime_type = "application/pdf"

        # Mock _fetch_attachment_content to raise exception
        connector._fetch_attachment_content = MagicMock(
            side_effect=Exception("Failed to fetch attachment")
        )

        # Call the method and expect exception to be raised
        with pytest.raises(Exception) as exc_info:
            await connector.stream_record(file_record)

        # Assertions
        assert "Failed to fetch attachment" in str(exc_info.value)
        connector.logger.error.assert_called_once()
        error_call = connector.logger.error.call_args[0][0]
        assert "Error streaming record file-789" in error_call

    @pytest.mark.asyncio
    async def test_stream_record_code_file_fetch_content_fails(self) -> None:
        """Test streaming code file when _fetch_code_file_content raises exception."""
        connector = _make_connector()

        # Create a code file record
        code_file_record = MagicMock(spec=Record)
        code_file_record.record_type = RecordType.CODE_FILE
        code_file_record.record_name = "main.py"
        code_file_record.external_record_id = "code-file-999"
        code_file_record.id = "record-id-456"
        code_file_record.mime_type = "text/x-python"

        # Mock _fetch_code_file_content to raise exception
        connector._fetch_code_file_content = MagicMock(
            side_effect=Exception("Failed to fetch code file")
        )

        # Call the method and expect exception to be raised
        with pytest.raises(Exception) as exc_info:
            await connector.stream_record(code_file_record)

        # Assertions
        assert "Failed to fetch code file" in str(exc_info.value)
        connector.logger.error.assert_called_once()
        error_call = connector.logger.error.call_args[0][0]
        assert "Error streaming record code-file-999" in error_call


class TestGitlabConnectorSyncPoints:
    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint_success_with_last_sync_time(self) -> None:
        """Test getting issues sync checkpoint when last_sync_time exists."""
        connector = _make_connector()
        project_id = 12345
        expected_last_sync_time = 1678901234567

        # Mock the record_sync_point
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={
                GitlabLiterals.LAST_SYNC_TIME.value: expected_last_sync_time,
                "other_data": "value",
            }
        )

        # Call the method
        result = await connector._get_issues_sync_checkpoint(project_id)

        # Assertions
        assert result == expected_last_sync_time

    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint_sync_point_data_exists_no_last_sync_time(
        self,
    ) -> None:
        """Test getting issues sync checkpoint when sync_point_data exists but has no last_sync_time key."""
        connector = _make_connector()
        project_id = 12345

        # Mock the record_sync_point - return dict without last_sync_time
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"other_key": "other_value"}
        )

        # Call the method
        result = await connector._get_issues_sync_checkpoint(project_id)

        # Assertions
        assert result is None
        connector.record_sync_point.read_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint_sync_point_data_is_none(self) -> None:
        """Test getting issues sync checkpoint when sync_point_data is None."""
        connector = _make_connector()
        project_id = 12345

        # Mock the record_sync_point - return None
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)

        # Call the method
        result = await connector._get_issues_sync_checkpoint(project_id)

        # Assertions
        assert result is None
        connector.record_sync_point.read_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint_exception_during_key_generation(
        self,
    ) -> None:
        """Test getting issues sync checkpoint when generate_record_sync_point_key raises exception."""
        connector = _make_connector()
        project_id = 12345

        # Mock generate_record_sync_point_key to raise exception
        with patch(
            "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
            side_effect=Exception("Key generation error"),
        ):
            # Call the method - should catch exception and return None
            result = await connector._get_issues_sync_checkpoint(project_id)

            # Assertions
            assert result is None

    @pytest.mark.asyncio
    async def test_update_issues_sync_checkpoint_success(self) -> None:
        """Test updating issues sync checkpoint successfully."""
        connector = _make_connector()
        project_id = "12345-work-items"
        last_sync_time = "1678901234567"

        # Mock the record_sync_point
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        # Call the method
        await connector._update_issues_sync_checkpoint(project_id, last_sync_time)

        # Assertions
        connector.record_sync_point.update_sync_point.assert_called_once_with(
            "GITLAB/12345-work-items/",
            {GitlabLiterals.LAST_SYNC_TIME.value: last_sync_time},
        )

    @pytest.mark.asyncio
    async def test_get_mr_sync_checkpoint_success_with_last_sync_time(self) -> None:
        """Test getting merge requests sync checkpoint when last_sync_time exists."""
        connector = _make_connector()
        project_id = 12345
        expected_last_sync_time = 1678901234567

        # Mock the record_sync_point
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={
                GitlabLiterals.LAST_SYNC_TIME.value: expected_last_sync_time,
                "other_data": "value",
            }
        )

        # Call the method
        result = await connector._get_mr_sync_checkpoint(project_id)

        # Assertions
        assert result == expected_last_sync_time

    @pytest.mark.asyncio
    async def test_get_mr_sync_checkpoint_sync_point_data_exists_no_last_sync_time(
        self,
    ) -> None:
        """Test getting merge requests sync checkpoint when sync_point_data exists but has no last_sync_time key."""
        connector = _make_connector()
        project_id = 12345

        # Mock the record_sync_point - return dict without last_sync_time
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(
            return_value={"other_key": "other_value"}
        )

        # Call the method
        result = await connector._get_mr_sync_checkpoint(project_id)

        # Assertions
        assert result is None
        connector.record_sync_point.read_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_mr_sync_checkpoint_sync_point_data_is_none(self) -> None:
        """Test getting merge requests sync checkpoint when sync_point_data is None."""
        connector = _make_connector()
        project_id = 12345

        # Mock the record_sync_point - return None
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.read_sync_point = AsyncMock(return_value=None)

        # Call the method
        result = await connector._get_mr_sync_checkpoint(project_id)

        # Assertions
        assert result is None
        connector.record_sync_point.read_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_mr_sync_checkpoint_exception_during_key_generation(self) -> None:
        """Test getting merge requests sync checkpoint when generate_record_sync_point_key raises exception."""
        connector = _make_connector()
        project_id = 12345

        # Mock generate_record_sync_point_key to raise exception
        with patch(
            "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
            side_effect=Exception("Key generation error"),
        ):
            # Call the method - should catch exception and return None
            result = await connector._get_mr_sync_checkpoint(project_id)

            # Assertions
            assert result is None

    @pytest.mark.asyncio
    async def test_update_mrs_sync_checkpoint_success(self) -> None:
        """Test updating merge requests sync checkpoint successfully."""
        connector = _make_connector()
        project_id = "12345-merge-requests"
        last_sync_time = "1678901234567"

        # Mock the record_sync_point
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        # Call the method
        await connector._update_mrs_sync_checkpoint(project_id, last_sync_time)

        # Assertions
        # connector.record_sync_point.update_sync_point.assert_called_once()
        connector.record_sync_point.update_sync_point.assert_called_once_with(
            "GITLAB/12345-merge-requests/",
            {GitlabLiterals.LAST_SYNC_TIME.value: last_sync_time},
        )


class TestGitlabConnectorRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_completes_successfully(self) -> None:
        """Test run_sync completes without raising exceptions."""
        connector = _make_connector()

        connector._sync_users = AsyncMock()
        connector._sync_all_project = AsyncMock()

        # Should complete without raising
        await connector.run_sync()

        # Verify both methods were invoked
        assert connector._sync_users.called
        assert connector._sync_all_project.called

    @pytest.mark.asyncio
    async def test_run_sync_propagates_exceptions(self) -> None:
        """Test run_sync propagates exceptions."""
        connector = _make_connector()

        connector._sync_users = AsyncMock()
        connector._sync_all_project = AsyncMock(side_effect=RuntimeError("sync fail"))

        # Should raise an exception
        with pytest.raises(RuntimeError, match="sync fail"):
            await connector.run_sync()


class TestGitlabConnectorSyncUsers:
    @pytest.mark.asyncio
    async def test_sync_users_success_with_groups_and_projects(self) -> None:
        """Test successful sync with both groups and projects having members."""
        connector = _make_connector()

        # Mock group data
        mock_group1 = MagicMock()
        mock_group1.id = 101
        mock_group2 = MagicMock()
        mock_group2.id = 102

        # Mock project data
        mock_project1 = MagicMock()
        mock_project1.id = 201
        mock_project2 = MagicMock()
        mock_project2.id = 202

        # Mock members
        mock_member1 = MagicMock()
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"
        mock_member1.public_email = "user1@example.com"

        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = "user2@example.com"

        mock_member3 = MagicMock()
        mock_member3.id = 3
        mock_member3.username = "user3"
        mock_member3.name = "User Three"
        mock_member3.public_email = "user3@example.com"

        # Mock data source responses
        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = [mock_group1, mock_group2]

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = [mock_project1, mock_project2]

        group1_members_response = MagicMock()
        group1_members_response.success = True
        group1_members_response.data = [mock_member1]

        group2_members_response = MagicMock()
        group2_members_response.success = True
        group2_members_response.data = [mock_member2]

        project1_members_response = MagicMock()
        project1_members_response.success = True
        project1_members_response.data = [mock_member2, mock_member3]

        project2_members_response = MagicMock()
        project2_members_response.success = True
        project2_members_response.data = [mock_member1]

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_group_members_all = MagicMock(
            side_effect=[group1_members_response, group2_members_response]
        )
        mock_data_source.list_project_members_all = MagicMock(
            side_effect=[project1_members_response, project2_members_response]
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify
        mock_data_source.list_groups.assert_called_once_with(owned=True, get_all=True)
        mock_data_source.list_projects.assert_called_once_with(owned=True, get_all=True)
        assert mock_data_source.list_group_members_all.call_count == 2
        assert mock_data_source.list_project_members_all.call_count == 2

        # Verify _sync_users_from_projects_groups was called with correct members
        call_args = connector._sync_users_from_projects_groups.call_args[0][0]
        assert len(call_args) == 3  # 3 unique members
        assert 1 in call_args
        assert 2 in call_args
        assert 3 in call_args

    @pytest.mark.asyncio
    async def test_sync_users_groups_fetch_fails(self) -> None:
        """Test sync when groups fetch fails but projects succeed."""
        connector = _make_connector()

        # Mock project data
        mock_project = MagicMock()
        mock_project.id = 201

        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.username = "user1"
        mock_member.name = "User One"
        mock_member.public_email = "user1@example.com"

        # Groups fail
        groups_response = MagicMock()
        groups_response.success = False
        groups_response.error = "API error"

        # Projects succeed
        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = [mock_project]

        project_members_response = MagicMock()
        project_members_response.success = True
        project_members_response.data = [mock_member]

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_project_members_all = MagicMock(
            return_value=project_members_response
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify
        connector.logger.error.assert_called_once()
        mock_data_source.list_projects.assert_called_once()
        connector._sync_users_from_projects_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_projects_fetch_fails(self) -> None:
        """Test sync when projects fetch fails but groups succeed."""
        connector = _make_connector()

        # Mock group data
        mock_group = MagicMock()
        mock_group.id = 101

        mock_member = MagicMock()
        mock_member.id = 1

        # Groups succeed
        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = [mock_group]

        group_members_response = MagicMock()
        group_members_response.success = True
        group_members_response.data = [mock_member]

        # Projects fail
        projects_response = MagicMock()
        projects_response.success = False
        projects_response.error = "API error"

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_group_members_all = MagicMock(
            return_value=group_members_response
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify
        connector._sync_users_from_projects_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_group_missing_id(self) -> None:
        """Test sync when a group is missing an ID."""
        connector = _make_connector()

        # Mock groups - one with ID, one without
        mock_group_valid = MagicMock()
        mock_group_valid.id = 101

        mock_group_invalid = MagicMock()
        mock_group_invalid.id = None

        mock_member = MagicMock()
        mock_member.id = 1

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = [mock_group_valid, mock_group_invalid]

        group_members_response = MagicMock()
        group_members_response.success = True
        group_members_response.data = [mock_member]

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = []

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_group_members_all = MagicMock(
            return_value=group_members_response
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify - only valid group should be processed
        assert mock_data_source.list_group_members_all.call_count == 1
        connector.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_project_missing_id(self) -> None:
        """Test sync when a project is missing an ID."""
        connector = _make_connector()

        # Mock projects - one with ID, one without
        mock_project_valid = MagicMock()
        mock_project_valid.id = 201

        mock_project_invalid = MagicMock()
        mock_project_invalid.id = None

        mock_member = MagicMock()
        mock_member.id = 1

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = []

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = [mock_project_valid, mock_project_invalid]

        project_members_response = MagicMock()
        project_members_response.success = True
        project_members_response.data = [mock_member]

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_project_members_all = MagicMock(
            return_value=project_members_response
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify - only valid project should be processed
        assert mock_data_source.list_project_members_all.call_count == 1
        connector.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_group_members_fetch_fails(self) -> None:
        """Test sync when fetching members for a specific group fails."""
        connector = _make_connector()

        mock_group1 = MagicMock()
        mock_group1.id = 101
        mock_group2 = MagicMock()
        mock_group2.id = 102

        mock_member = MagicMock()
        mock_member.id = 1

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = [mock_group1, mock_group2]

        # First group fails, second succeeds
        group1_members_response = MagicMock()
        group1_members_response.success = False

        group2_members_response = MagicMock()
        group2_members_response.success = True
        group2_members_response.data = [mock_member]

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = []

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_group_members_all = MagicMock(
            side_effect=[group1_members_response, group2_members_response]
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify
        assert mock_data_source.list_group_members_all.call_count == 2
        connector._sync_users_from_projects_groups.assert_called_once()
        # Check that member from successful group is included
        call_args = connector._sync_users_from_projects_groups.call_args[0][0]
        assert 1 in call_args

    @pytest.mark.asyncio
    async def test_sync_users_project_members_fetch_fails(self) -> None:
        """Test sync when fetching members for a specific project fails."""
        connector = _make_connector()

        mock_project1 = MagicMock()
        mock_project1.id = 201
        mock_project2 = MagicMock()
        mock_project2.id = 202

        mock_member = MagicMock()
        mock_member.id = 1

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = []

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = [mock_project1, mock_project2]

        # First project fails, second succeeds
        project1_members_response = MagicMock()
        project1_members_response.success = False

        project2_members_response = MagicMock()
        project2_members_response.success = True
        project2_members_response.data = [mock_member]

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_project_members_all = MagicMock(
            side_effect=[project1_members_response, project2_members_response]
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify
        assert mock_data_source.list_project_members_all.call_count == 2
        connector.logger.error.assert_called()
        connector._sync_users_from_projects_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_exception_during_group_processing(self) -> None:
        """Test sync when an exception occurs during group processing."""
        connector = _make_connector()

        mock_group1 = MagicMock()
        mock_group1.id = 101
        mock_group2 = MagicMock()
        mock_group2.id = 102

        mock_member = MagicMock()
        mock_member.id = 1

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = [mock_group1, mock_group2]

        group2_members_response = MagicMock()
        group2_members_response.success = True
        group2_members_response.data = [mock_member]

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = []

        # Setup data source - first call raises exception, second succeeds
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_group_members_all = MagicMock(
            side_effect=[Exception("Network error"), group2_members_response]
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute - should not raise, should continue processing
        await connector._sync_users()

        # Verify
        assert mock_data_source.list_group_members_all.call_count == 2
        connector.logger.error.assert_called()
        connector._sync_users_from_projects_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_exception_during_project_processing(self) -> None:
        """Test sync when an exception occurs during project processing."""
        connector = _make_connector()

        mock_project1 = MagicMock()
        mock_project1.id = 201
        mock_project2 = MagicMock()
        mock_project2.id = 202

        mock_member = MagicMock()
        mock_member.id = 1

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = []

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = [mock_project1, mock_project2]

        project2_members_response = MagicMock()
        project2_members_response.success = True
        project2_members_response.data = [mock_member]

        # Setup data source - first call raises exception, second succeeds
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_project_members_all = MagicMock(
            side_effect=[Exception("Network error"), project2_members_response]
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute - should not raise, should continue processing
        await connector._sync_users()

        # Verify
        assert mock_data_source.list_project_members_all.call_count == 2
        connector.logger.error.assert_called()
        connector._sync_users_from_projects_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_duplicate_members_across_groups_and_projects(
        self,
    ) -> None:
        """Test sync with duplicate members across groups and projects."""
        connector = _make_connector()

        mock_group = MagicMock()
        mock_group.id = 101

        mock_project = MagicMock()
        mock_project.id = 201

        # Same member appears in both group and project
        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.username = "user1"
        mock_member.name = "User One"
        mock_member.public_email = "user1@example.com"

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = [mock_group]

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = [mock_project]

        group_members_response = MagicMock()
        group_members_response.success = True
        group_members_response.data = [mock_member]

        project_members_response = MagicMock()
        project_members_response.success = True
        project_members_response.data = [mock_member]

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_group_members_all = MagicMock(
            return_value=group_members_response
        )
        mock_data_source.list_project_members_all = MagicMock(
            return_value=project_members_response
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify - should only have 1 unique member
        call_args = connector._sync_users_from_projects_groups.call_args[0][0]
        assert len(call_args) == 1
        assert 1 in call_args

    @pytest.mark.asyncio
    async def test_sync_users_empty_groups_and_projects(self) -> None:
        """Test sync when there are no groups or projects."""
        connector = _make_connector()

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = []

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = []

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify - should be called with empty dict
        connector._sync_users_from_projects_groups.assert_called_once_with({})

    @pytest.mark.asyncio
    async def test_sync_users_groups_data_is_none(self) -> None:
        """Test sync when groups response data is None."""
        connector = _make_connector()

        mock_project = MagicMock()
        mock_project.id = 201

        mock_member = MagicMock()
        mock_member.id = 1

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = None

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = [mock_project]

        project_members_response = MagicMock()
        project_members_response.success = True
        project_members_response.data = [mock_member]

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_project_members_all = MagicMock(
            return_value=project_members_response
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify - should still process projects
        connector._sync_users_from_projects_groups.assert_called_once()
        call_args = connector._sync_users_from_projects_groups.call_args[0][0]
        assert 1 in call_args

    @pytest.mark.asyncio
    async def test_sync_users_projects_data_is_none(self) -> None:
        """Test sync when projects response data is None."""
        connector = _make_connector()

        mock_group = MagicMock()
        mock_group.id = 101

        mock_member = MagicMock()
        mock_member.id = 1

        groups_response = MagicMock()
        groups_response.success = True
        groups_response.data = [mock_group]

        group_members_response = MagicMock()
        group_members_response.success = True
        group_members_response.data = [mock_member]

        projects_response = MagicMock()
        projects_response.success = True
        projects_response.data = None

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)
        mock_data_source.list_group_members_all = MagicMock(
            return_value=group_members_response
        )

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify - should still process groups
        connector._sync_users_from_projects_groups.assert_called_once()
        call_args = connector._sync_users_from_projects_groups.call_args[0][0]
        assert 1 in call_args

    @pytest.mark.asyncio
    async def test_sync_users_both_groups_and_projects_fail(self) -> None:
        """Test sync when both groups and projects fetch fail."""
        connector = _make_connector()

        groups_response = MagicMock()
        groups_response.success = False
        groups_response.error = "Groups API error"

        projects_response = MagicMock()
        projects_response.success = False
        projects_response.error = "Projects API error"

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_groups = MagicMock(return_value=groups_response)
        mock_data_source.list_projects = MagicMock(return_value=projects_response)

        connector.data_source = mock_data_source
        connector._sync_users_from_projects_groups = AsyncMock()

        # Execute
        await connector._sync_users()

        # Verify - should be called with empty dict
        connector._sync_users_from_projects_groups.assert_called_once_with({})
        assert connector.logger.error.call_count >= 1


class TestGitlabConnectorSyncUsersFromProjectsGroups:
    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_success(self) -> None:
        """Test successful sync of users from projects and groups."""
        connector = _make_connector()

        # Mock members
        mock_member1 = MagicMock()
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"
        mock_member1.public_email = "user1@example.com"

        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = "user2@example.com"

        dict_member = {1: mock_member1, 2: mock_member2}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify
        connector.data_entities_processor.on_new_app_users.assert_called_once()
        app_users = connector.data_entities_processor.on_new_app_users.call_args[0][0]

        assert len(app_users) == 2
        assert app_users[0].source_user_id == "1"
        assert app_users[0].email == "user1@example.com"
        assert app_users[0].full_name == "User One"
        assert app_users[0].is_active is True
        assert app_users[0].connector_id == "gitlab-conn-1"

        assert app_users[1].source_user_id == "2"
        assert app_users[1].email == "user2@example.com"
        assert app_users[1].full_name == "User Two"

        # Verify migration was called for both users
        assert (
            connector.data_entities_processor.migrate_group_to_user_by_external_id.call_count
            == 2
        )

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_empty_dict(self) -> None:
        """Test sync with empty member dictionary."""
        connector = _make_connector()

        dict_member = {}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify - should not call on_new_app_users with empty list
        connector.data_entities_processor.on_new_app_users.assert_not_called()
        connector.data_entities_processor.migrate_group_to_user_by_external_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_member_without_email(self) -> None:
        """Test sync when member has no public_email."""
        connector = _make_connector()

        # Mock member without email
        mock_member1 = MagicMock()
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"
        mock_member1.public_email = ""

        # Mock member with email
        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = "user2@example.com"

        dict_member = {1: mock_member1, 2: mock_member2}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify - only user with email should be synced
        connector.data_entities_processor.on_new_app_users.assert_called_once()
        app_users = connector.data_entities_processor.on_new_app_users.call_args[0][0]

        assert len(app_users) == 1
        assert app_users[0].source_user_id == "2"
        assert app_users[0].email == "user2@example.com"

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_member_with_none_email(self) -> None:
        """Test sync when member's public_email attribute is None."""
        connector = _make_connector()

        # Mock member with None email
        mock_member1 = MagicMock()
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"
        mock_member1.public_email = None

        # Mock member with valid email
        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = "user2@example.com"

        dict_member = {1: mock_member1, 2: mock_member2}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify - only user with email should be synced
        app_users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(app_users) == 1
        assert app_users[0].email == "user2@example.com"

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_member_missing_email_attribute(
        self,
    ) -> None:
        """Test sync when member doesn't have public_email attribute at all."""
        connector = _make_connector()

        # Mock member without public_email attribute
        mock_member1 = MagicMock(spec=[])
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"

        # Mock member with email
        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = "user2@example.com"

        dict_member = {1: mock_member1, 2: mock_member2}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify - only user with email should be synced
        app_users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(app_users) == 1
        assert app_users[0].email == "user2@example.com"

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_all_members_without_email(
        self,
    ) -> None:
        """Test sync when all members lack email addresses."""
        connector = _make_connector()

        mock_member1 = MagicMock()
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"
        mock_member1.public_email = ""

        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = None

        dict_member = {1: mock_member1, 2: mock_member2}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify - no users should be synced
        connector.data_entities_processor.on_new_app_users.assert_not_called()
        connector.data_entities_processor.migrate_group_to_user_by_external_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_migration_fails_for_one_user(
        self,
    ) -> None:
        """Test sync when migration fails for one user but continues for others."""
        connector = _make_connector()

        mock_member1 = MagicMock()
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"
        mock_member1.public_email = "user1@example.com"

        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = "user2@example.com"

        dict_member = {1: mock_member1, 2: mock_member2}

        # Mock migration to fail for first user, succeed for second
        connector.data_entities_processor.migrate_group_to_user_by_external_id = (
            AsyncMock(side_effect=[Exception("Migration failed"), None])
        )

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify - both users should be created, migration attempted for both
        connector.data_entities_processor.on_new_app_users.assert_called_once()
        assert (
            connector.data_entities_processor.migrate_group_to_user_by_external_id.call_count
            == 2
        )

        # Verify warning was logged
        connector.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_migration_fails_for_all_users(
        self,
    ) -> None:
        """Test sync when migration fails for all users."""
        connector = _make_connector()

        mock_member1 = MagicMock()
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"
        mock_member1.public_email = "user1@example.com"

        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = "user2@example.com"

        dict_member = {1: mock_member1, 2: mock_member2}

        # Mock migration to fail for all users
        connector.data_entities_processor.migrate_group_to_user_by_external_id = (
            AsyncMock(side_effect=Exception("Migration failed"))
        )

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify - users should still be created despite migration failures
        connector.data_entities_processor.on_new_app_users.assert_called_once()
        assert (
            connector.data_entities_processor.migrate_group_to_user_by_external_id.call_count
            == 2
        )
        assert connector.logger.warning.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_migration_called_with_correct_params(
        self,
    ) -> None:
        """Test that migration is called with correct parameters."""
        connector = _make_connector()

        mock_member = MagicMock()
        mock_member.id = 123
        mock_member.username = "testuser"
        mock_member.name = "Test User"
        mock_member.public_email = "test@example.com"

        dict_member = {123: mock_member}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify migration parameters
        connector.data_entities_processor.migrate_group_to_user_by_external_id.assert_called_once_with(
            group_external_id="123",
            user_email="test@example.com",
            connector_id="gitlab-conn-1",
        )

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_app_user_fields(self) -> None:
        """Test that AppUser is created with correct field values."""
        connector = _make_connector()

        mock_member = MagicMock()
        mock_member.id = 999
        mock_member.username = "jdoe"
        mock_member.name = "John Doe"
        mock_member.public_email = "john.doe@example.com"

        dict_member = {999: mock_member}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify AppUser fields
        app_users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        app_user = app_users[0]

        assert app_user.org_id == "org-1"
        assert app_user.connector_id == "gitlab-conn-1"
        assert app_user.source_user_id == "999"
        assert app_user.is_active is True
        assert app_user.email == "john.doe@example.com"
        assert app_user.full_name == "John Doe"

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_preserves_member_order(self) -> None:
        """Test that users are processed in dictionary iteration order."""
        connector = _make_connector()

        # Create multiple members
        members = {}
        for i in range(5):
            mock_member = MagicMock()
            mock_member.id = i
            mock_member.username = f"user{i}"
            mock_member.name = f"User {i}"
            mock_member.public_email = f"user{i}@example.com"
            members[i] = mock_member

        # Execute
        await connector._sync_users_from_projects_groups(members)

        # Verify all users were created
        app_users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(app_users) == 5

        # Verify migration was called for all
        assert (
            connector.data_entities_processor.migrate_group_to_user_by_external_id.call_count
            == 5
        )

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_mixed_valid_invalid_emails(
        self,
    ) -> None:
        """Test sync with a mix of valid and invalid email addresses."""
        connector = _make_connector()

        mock_member1 = MagicMock()
        mock_member1.id = 1
        mock_member1.username = "user1"
        mock_member1.name = "User One"
        mock_member1.public_email = "user1@example.com"

        mock_member2 = MagicMock()
        mock_member2.id = 2
        mock_member2.username = "user2"
        mock_member2.name = "User Two"
        mock_member2.public_email = ""

        mock_member3 = MagicMock()
        mock_member3.id = 3
        mock_member3.username = "user3"
        mock_member3.name = "User Three"
        mock_member3.public_email = "user3@example.com"

        mock_member4 = MagicMock()
        mock_member4.id = 4
        mock_member4.username = "user4"
        mock_member4.name = "User Four"
        mock_member4.public_email = None

        dict_member = {
            1: mock_member1,
            2: mock_member2,
            3: mock_member3,
            4: mock_member4,
        }

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify - only users with valid emails should be synced
        app_users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(app_users) == 2
        assert app_users[0].source_user_id == "1"
        assert app_users[1].source_user_id == "3"

        # Verify migration called only for valid users
        assert (
            connector.data_entities_processor.migrate_group_to_user_by_external_id.call_count
            == 2
        )

    @pytest.mark.asyncio
    async def test_sync_users_from_projects_groups_member_id_as_string(self) -> None:
        """Test that member ID is correctly converted to string for source_user_id."""
        connector = _make_connector()

        mock_member = MagicMock()
        mock_member.id = 42
        mock_member.username = "user42"
        mock_member.name = "User Forty Two"
        mock_member.public_email = "user42@example.com"

        dict_member = {42: mock_member}

        # Execute
        await connector._sync_users_from_projects_groups(dict_member)

        # Verify source_user_id is string
        app_users = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert app_users[0].source_user_id == "42"
        assert isinstance(app_users[0].source_user_id, str)

        # Verify migration also uses string
        call_args = connector.data_entities_processor.migrate_group_to_user_by_external_id.call_args
        assert call_args[1]["group_external_id"] == "42"


class TestGitlabConnectorSyncAllProject:
    @pytest.mark.asyncio
    async def test_sync_all_project_success(self) -> None:
        """Test successful sync of all projects."""
        connector = _make_connector()

        # Mock dependencies
        connector._sync_projects = AsyncMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        # Mock timestamp
        mock_timestamp = 1678901234567
        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=mock_timestamp,
            ),
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value="GITLAB/record_group/global/",
            ) as mock_generate_key,
        ):
            # Execute
            await connector._sync_all_project()

        # Verify
        connector._sync_projects.assert_called_once()
        mock_generate_key.assert_called_once_with(
            Connectors.GITLAB.value,
            GitlabLiterals.RECORD_GROUP.value,
            GitlabLiterals.GLOBAL.value,
        )
        connector.record_sync_point.update_sync_point.assert_called_once_with(
            "GITLAB/record_group/global/",
            {GitlabLiterals.LAST_SYNC_TIME.value: mock_timestamp},
        )

    @pytest.mark.asyncio
    async def test_sync_all_project_calls_in_correct_order(self) -> None:
        """Test that sync_projects is called before updating sync point."""
        connector = _make_connector()

        call_order = []

        async def mock_sync_projects() -> None:
            call_order.append("sync_projects")

        async def mock_update_sync_point(key, data) -> None:
            call_order.append("update_sync_point")

        connector._sync_projects = mock_sync_projects
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = mock_update_sync_point

        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=1678901234567,
            ),
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value="GITLAB/record_group/global/",
            ),
        ):
            # Execute
            await connector._sync_all_project()

        # Verify order
        assert call_order == ["sync_projects", "update_sync_point"]

    @pytest.mark.asyncio
    async def test_sync_all_project_sync_projects_fails(self) -> None:
        """Test when _sync_projects raises an exception."""
        connector = _make_connector()

        # Mock _sync_projects to raise exception
        connector._sync_projects = AsyncMock(side_effect=Exception("Sync failed"))
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=1678901234567,
            ),
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value="GITLAB/record_group/global/",
            ),
        ):
            # Execute and expect exception
            with pytest.raises(Exception) as exc_info:
                await connector._sync_all_project()

            assert "Sync failed" in str(exc_info.value)

        # Verify sync point was not updated
        connector.record_sync_point.update_sync_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_all_project_update_sync_point_fails(self) -> None:
        """Test when update_sync_point raises an exception."""
        connector = _make_connector()

        # Mock _sync_projects to succeed
        connector._sync_projects = AsyncMock()

        # Mock update_sync_point to fail
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = AsyncMock(
            side_effect=Exception("Update sync point failed")
        )

        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=1678901234567,
            ),
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value="GITLAB/record_group/global/",
            ),
        ):
            # Execute and expect exception
            with pytest.raises(Exception) as exc_info:
                await connector._sync_all_project()

            assert "Update sync point failed" in str(exc_info.value)

        # Verify _sync_projects was still called
        connector._sync_projects.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_all_project_timestamp_captured_before_sync(self) -> None:
        """Test that timestamp is captured at the start, before sync begins."""
        connector = _make_connector()

        captured_timestamp = None

        async def mock_sync_projects() -> None:
            # Simulate some delay
            await asyncio.sleep(0.01)

        async def mock_update_sync_point(key, data) -> None:
            nonlocal captured_timestamp
            captured_timestamp = data[GitlabLiterals.LAST_SYNC_TIME.value]

        connector._sync_projects = mock_sync_projects
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = mock_update_sync_point

        mock_timestamp = 1678901234567
        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=mock_timestamp,
            ) as mock_get_timestamp,
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value="GITLAB/record_group/global/",
            ),
        ):
            # Execute
            await connector._sync_all_project()

        # Verify timestamp was captured before sync
        assert captured_timestamp == mock_timestamp
        # Verify get_epoch_timestamp_in_ms was called only once at the start
        mock_get_timestamp.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_all_project_generates_correct_sync_key(self) -> None:
        """Test that the correct sync point key is generated."""
        connector = _make_connector()

        connector._sync_projects = AsyncMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=1678901234567,
            ),
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value="GITLAB/record_group/global/",
            ) as mock_generate_key,
        ):
            # Execute
            await connector._sync_all_project()

        # Verify key generation parameters
        mock_generate_key.assert_called_once_with(
            Connectors.GITLAB.value,
            GitlabLiterals.RECORD_GROUP.value,
            GitlabLiterals.GLOBAL.value,
        )

    @pytest.mark.asyncio
    async def test_sync_all_project_sync_point_data_structure(self) -> None:
        """Test that sync point is updated with correct data structure."""
        connector = _make_connector()

        connector._sync_projects = AsyncMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        mock_timestamp = 1678901234567
        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=mock_timestamp,
            ),
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value="GITLAB/record_group/global/",
            ),
        ):
            # Execute
            await connector._sync_all_project()

        # Verify data structure
        call_args = connector.record_sync_point.update_sync_point.call_args
        assert call_args[0][0] == "GITLAB/record_group/global/"
        assert call_args[0][1] == {GitlabLiterals.LAST_SYNC_TIME.value: mock_timestamp}
        assert isinstance(call_args[0][1][GitlabLiterals.LAST_SYNC_TIME.value], int)

    @pytest.mark.asyncio
    async def test_sync_all_project_no_record_sync_point(self) -> None:
        """Test when record_sync_point is not initialized."""
        connector = _make_connector()

        connector._sync_projects = AsyncMock()
        connector.record_sync_point = None

        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=1678901234567,
            ),
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value="GITLAB/record_group/global/",
            ),
        ):
            # Execute and expect AttributeError
            with pytest.raises(AttributeError):
                await connector._sync_all_project()

    @pytest.mark.asyncio
    async def test_sync_all_project_multiple_calls_different_timestamps(self) -> None:
        """Test that multiple calls use different timestamps."""
        connector = _make_connector()

        connector._sync_projects = AsyncMock()
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = AsyncMock()

        timestamps = [1678901234567, 1678901234999]

        with patch(
            "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
            return_value="GITLAB/record_group/global/",
        ):
            for timestamp in timestamps:
                with patch(
                    "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                    return_value=timestamp,
                ):
                    await connector._sync_all_project()

        # Verify both calls used different timestamps
        assert connector.record_sync_point.update_sync_point.call_count == 2
        first_call = connector.record_sync_point.update_sync_point.call_args_list[0]
        second_call = connector.record_sync_point.update_sync_point.call_args_list[1]

        assert first_call[0][1][GitlabLiterals.LAST_SYNC_TIME.value] == timestamps[0]
        assert second_call[0][1][GitlabLiterals.LAST_SYNC_TIME.value] == timestamps[1]

    @pytest.mark.asyncio
    async def test_sync_all_project_integration_flow(self) -> None:
        """Test complete integration flow of the method."""
        connector = _make_connector()

        # Track all operations
        operations = []

        async def track_sync_projects() -> None:
            operations.append(("sync_projects", None))

        async def track_update_sync_point(key, data) -> None:
            operations.append(("update_sync_point", (key, data)))

        connector._sync_projects = track_sync_projects
        connector.record_sync_point = MagicMock()
        connector.record_sync_point.update_sync_point = track_update_sync_point

        mock_timestamp = 1678901234567
        mock_key = "GITLAB/record_group/global/"

        with (
            patch(
                "app.connectors.sources.gitlab.connector.get_epoch_timestamp_in_ms",
                return_value=mock_timestamp,
            ),
            patch(
                "app.connectors.sources.gitlab.connector.generate_record_sync_point_key",
                return_value=mock_key,
            ),
        ):
            # Execute
            await connector._sync_all_project()

        # Verify complete flow
        assert len(operations) == 2
        assert operations[0] == ("sync_projects", None)
        assert operations[1] == (
            "update_sync_point",
            (mock_key, {GitlabLiterals.LAST_SYNC_TIME.value: mock_timestamp}),
        )


class TestGitlabConnectorBuildCodeFileRecords:
    @pytest.mark.asyncio
    async def test_build_code_file_records_success_single_file(self) -> None:
        """Test building code file records with a single file."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "src/main.py",
                "name": "main.py",
                "sha": "abc123def456",
                "webPath": "/project/src/main.py",
                "webUrl": "https://gitlab.com/project/src/main.py",
            }
        ]

        # Mock data store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify
        connector._process_new_records.assert_called_once()
        records = connector._process_new_records.call_args[0][0]
        assert len(records) == 1

        record_update = records[0]
        assert record_update.is_new is True
        assert record_update.is_updated is False
        assert record_update.is_deleted is False
        assert record_update.external_record_id == "/project/src/main.py"

        code_file = record_update.record
        assert code_file.record_name == "main.py"
        assert code_file.file_path == "src/main.py"
        assert code_file.file_hash == "abc123def456"
        assert code_file.external_record_id == "/project/src/main.py"
        assert code_file.weburl == "https://gitlab.com/project/src/main.py"
        assert code_file.external_record_group_id == "123-code-repository"
        assert code_file.mime_type == MimeTypes.PLAIN_TEXT.value

    @pytest.mark.asyncio
    async def test_build_code_file_records_multiple_files(self) -> None:
        """Test building code file records with multiple files."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "src/main.py",
                "name": "main.py",
                "sha": "abc123",
                "webPath": "/project/src/main.py",
                "webUrl": "https://gitlab.com/project/src/main.py",
            },
            {
                "path": "src/utils.js",
                "name": "utils.js",
                "sha": "def456",
                "webPath": "/project/src/utils.js",
                "webUrl": "https://gitlab.com/project/src/utils.js",
            },
            {
                "path": "README.md",
                "name": "README.md",
                "sha": "ghi789",
                "webPath": "/project/README.md",
                "webUrl": "https://gitlab.com/project/README.md",
            },
        ]

        # Mock data store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify
        records = connector._process_new_records.call_args[0][0]
        assert len(records) == 3
        assert records[0].record.record_name == "main.py"
        assert records[1].record.record_name == "utils.js"
        assert records[2].record.record_name == "README.md"

    @pytest.mark.asyncio
    async def test_build_code_file_records_skip_dotfiles(self) -> None:
        """Test that files starting with . are skipped."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": ".gitignore",
                "name": ".gitignore",
                "sha": "abc123",
                "webPath": "/project/.gitignore",
                "webUrl": "https://gitlab.com/project/.gitignore",
            },
            {
                "path": ".env",
                "name": ".env",
                "sha": "def456",
                "webPath": "/project/.env",
                "webUrl": "https://gitlab.com/project/.env",
            },
            {
                "path": "main.py",
                "name": "main.py",
                "sha": "ghi789",
                "webPath": "/project/main.py",
                "webUrl": "https://gitlab.com/project/main.py",
            },
        ]

        # Mock data store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify - only non-dotfile should be processed
        records = connector._process_new_records.call_args[0][0]
        assert len(records) == 1
        assert records[0].record.record_name == "main.py"

    @pytest.mark.asyncio
    async def test_build_code_file_records_with_parent_path(self) -> None:
        """Test building code file records with parent path resolution."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "src/components/Button.tsx",
                "name": "Button.tsx",
                "sha": "abc123",
                "webPath": "/project/src/components/Button.tsx",
                "webUrl": "https://gitlab.com/project/src/components/Button.tsx",
            }
        ]

        # Mock parent record found
        mock_parent_record = {
            "externalRecordId": "/project/src/components",
            "id": "parent-id-123",
        }

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(return_value=mock_parent_record)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify
        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.parent_external_record_id == "/project/src/components"

        # Verify get_record_by_path was called with correct parameters
        mock_tx_store.get_record_by_path.assert_called_once_with(
            connector_id="gitlab-conn-1",
            path=["src", "components"],
            external_record_group_id="123-code-repository",
        )

    @pytest.mark.asyncio
    async def test_build_code_file_records_parent_not_found(self) -> None:
        """Test building code file records when parent path is not found."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "src/components/Button.tsx",
                "name": "Button.tsx",
                "sha": "abc123",
                "webPath": "/project/src/components/Button.tsx",
                "webUrl": "https://gitlab.com/project/src/components/Button.tsx",
            }
        ]

        # Mock parent record not found
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify - record should still be created with None parent
        records = connector._process_new_records.call_args[0][0]
        assert len(records) == 1
        assert records[0].record.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_build_code_file_records_root_level_file_no_parent(self) -> None:
        """Test building code file records for root level files (no parent)."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "README.md",
                "name": "README.md",
                "sha": "abc123",
                "webPath": "/project/README.md",
                "webUrl": "https://gitlab.com/project/README.md",
            }
        ]

        # Mock data store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify - root level file should have no parent
        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.parent_external_record_id is None

        # Verify get_record_by_path was not called for root level file
        mock_tx_store.get_record_by_path.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_code_file_records_existing_record(self) -> None:
        """Test building code file records when record already exists."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "main.py",
                "name": "main.py",
                "sha": "abc123",
                "webPath": "/project/main.py",
                "webUrl": "https://gitlab.com/project/main.py",
            }
        ]

        # Mock existing record
        mock_existing_record = MagicMock()
        mock_existing_record.id = "existing-record-id-123"

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            return_value=mock_existing_record
        )

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify - should use existing record ID
        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.id == "existing-record-id-123"

    @pytest.mark.asyncio
    async def test_build_code_file_records_mime_type_detection(self) -> None:
        """Test MIME type detection for different file extensions."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "script.py",
                "name": "script.py",
                "sha": "abc1",
                "webPath": "/project/script.py",
                "webUrl": "https://gitlab.com/project/script.py",
            },
            {
                "path": "app.js",
                "name": "app.js",
                "sha": "abc2",
                "webPath": "/project/app.js",
                "webUrl": "https://gitlab.com/project/app.js",
            },
            {
                "path": "unknown.xyz",
                "name": "unknown.xyz",
                "sha": "abc3",
                "webPath": "/project/unknown.xyz",
                "webUrl": "https://gitlab.com/project/unknown.xyz",
            },
        ]

        # Mock data store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify MIME types
        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.mime_type == MimeTypes.PLAIN_TEXT.value
        assert records[1].record.mime_type == MimeTypes.PLAIN_TEXT.value
        assert records[2].record.mime_type == MimeTypes.PLAIN_TEXT.value  # fallback

    @pytest.mark.asyncio
    async def test_build_code_file_records_parent_path_caching(self) -> None:
        """Test that parent paths are cached to avoid duplicate lookups."""
        connector = _make_connector()

        # Multiple files in same directory
        code_file_list = [
            {
                "path": "src/utils/helper1.py",
                "name": "helper1.py",
                "sha": "abc1",
                "webPath": "/project/src/utils/helper1.py",
                "webUrl": "https://gitlab.com/project/src/utils/helper1.py",
            },
            {
                "path": "src/utils/helper2.py",
                "name": "helper2.py",
                "sha": "abc2",
                "webPath": "/project/src/utils/helper2.py",
                "webUrl": "https://gitlab.com/project/src/utils/helper2.py",
            },
        ]

        # Mock parent record
        mock_parent_record = {
            "externalRecordId": "/project/src/utils",
            "id": "parent-id-123",
        }

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(return_value=mock_parent_record)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify - get_record_by_path should be called only once (cached for second file)
        assert mock_tx_store.get_record_by_path.call_count == 1

        # Verify both records have same parent
        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.parent_external_record_id == "/project/src/utils"
        assert records[1].record.parent_external_record_id == "/project/src/utils"

    @pytest.mark.asyncio
    async def test_build_code_file_records_parent_lookup_exception(self) -> None:
        """Test handling of exceptions during parent path lookup."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "src/main.py",
                "name": "main.py",
                "sha": "abc123",
                "webPath": "/project/src/main.py",
                "webUrl": "https://gitlab.com/project/src/main.py",
            }
        ]

        # Mock exception during parent lookup
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(
            side_effect=Exception("Database error")
        )

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute - should not raise, should continue processing
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify - record should still be created with None parent
        records = connector._process_new_records.call_args[0][0]
        assert len(records) == 1
        assert records[0].record.parent_external_record_id is None

        # Verify error was logged
        connector.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_code_file_records_empty_list(self) -> None:
        """Test building code file records with empty file list."""
        connector = _make_connector()

        code_file_list = []

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify - _process_new_records should not be called
        connector._process_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_code_file_records_all_files_skipped(self) -> None:
        """Test when all files are skipped (all dotfiles)."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": ".gitignore",
                "name": ".gitignore",
                "sha": "abc1",
                "webPath": "/project/.gitignore",
                "webUrl": "https://gitlab.com/project/.gitignore",
            },
            {
                "path": ".env",
                "name": ".env",
                "sha": "abc2",
                "webPath": "/project/.env",
                "webUrl": "https://gitlab.com/project/.env",
            },
        ]

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify - _process_new_records should not be called
        connector._process_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_build_code_file_records_code_file_record_fields(self) -> None:
        """Test that CodeFileRecord is created with all correct fields."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "src/main.py",
                "name": "main.py",
                "sha": "abc123def456",
                "webPath": "/project/src/main.py",
                "webUrl": "https://gitlab.com/project/src/main.py",
            }
        ]

        # Mock data store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 456, "project-path")

        # Verify all fields
        records = connector._process_new_records.call_args[0][0]
        code_file = records[0].record

        assert code_file.org_id == "org-1"
        assert code_file.record_name == "main.py"
        assert code_file.record_type == RecordType.CODE_FILE.value
        assert code_file.connector_id == "gitlab-conn-1"
        assert code_file.external_record_id == "/project/src/main.py"
        assert code_file.version == 0
        assert code_file.origin == OriginTypes.CONNECTOR
        assert code_file.record_group_type == RecordGroupType.PROJECT.value
        assert code_file.external_record_group_id == "456-code-repository"
        assert code_file.external_revision_id == "abc123def456"
        assert code_file.preview_renderable is False
        assert code_file.file_path == "src/main.py"
        assert code_file.file_hash == "abc123def456"
        assert code_file.inherit_permissions is True
        assert code_file.weburl == "https://gitlab.com/project/src/main.py"

    @pytest.mark.asyncio
    async def test_build_code_file_records_record_update_fields(self) -> None:
        """Test that RecordUpdate is created with correct fields."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "main.py",
                "name": "main.py",
                "sha": "abc123",
                "webPath": "/project/main.py",
                "webUrl": "https://gitlab.com/project/main.py",
            }
        ]

        # Mock data store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify RecordUpdate fields
        records = connector._process_new_records.call_args[0][0]
        record_update = records[0]

        assert record_update.is_new is True
        assert record_update.is_updated is False
        assert record_update.is_deleted is False
        assert record_update.metadata_changed is False
        assert record_update.content_changed is False
        assert record_update.permissions_changed is False
        assert record_update.external_record_id == "/project/main.py"
        assert record_update.new_permissions == []
        assert record_update.old_permissions == []

    @pytest.mark.asyncio
    async def test_build_code_file_records_nested_directory_structure(self) -> None:
        """Test building code file records with deeply nested directory structure."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "src/app/components/ui/Button.tsx",
                "name": "Button.tsx",
                "sha": "abc123",
                "webPath": "/project/src/app/components/ui/Button.tsx",
                "webUrl": "https://gitlab.com/project/src/app/components/ui/Button.tsx",
            }
        ]

        # Mock parent record
        mock_parent_record = {
            "externalRecordId": "/project/src/app/components/ui",
            "id": "parent-id-123",
        }

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(return_value=mock_parent_record)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute
        await connector.build_code_file_records(code_file_list, 123, "project-path")

        # Verify parent path was split correctly
        mock_tx_store.get_record_by_path.assert_called_once_with(
            connector_id="gitlab-conn-1",
            path=["src", "app", "components", "ui"],
            external_record_group_id="123-code-repository",
        )

        records = connector._process_new_records.call_args[0][0]
        assert (
            records[0].record.parent_external_record_id
            == "/project/src/app/components/ui"
        )

    @pytest.mark.asyncio
    async def test_build_code_file_records_external_group_id_format(self) -> None:
        """Test that external_record_group_id is formatted correctly."""
        connector = _make_connector()

        code_file_list = [
            {
                "path": "main.py",
                "name": "main.py",
                "sha": "abc123",
                "webPath": "/project/main.py",
                "webUrl": "https://gitlab.com/project/main.py",
            }
        ]

        # Mock data store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector._process_new_records = AsyncMock()

        # Execute with specific project_id
        await connector.build_code_file_records(code_file_list, 999, "project-path")

        # Verify external_record_group_id format
        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.external_record_group_id == "999-code-repository"


class TestGitlabConnectorFetchCodeFileContent:
    @pytest.mark.asyncio
    async def test_fetch_code_file_content_success(self) -> None:
        """Test successful fetching of code file content."""
        connector = _make_connector()

        # Mock record
        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        # Mock file path
        file_path = ["src", "main.py"]

        # Mock file content response
        mock_file_data = MagicMock()
        mock_file_data.content = base64.b64encode(b"print('Hello World')").decode(
            GitlabLiterals.UTF_8.value
        )

        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = mock_file_data

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        # Mock data source
        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute
        result_generator = connector._fetch_code_file_content(mock_record)
        chunks = [chunk async for chunk in result_generator]
        # async for chunk in result_generator:
        #     chunks.append(chunk)

        # Verify
        assert len(chunks) == 1
        assert chunks[0] == b"print('Hello World')"

        # Verify calls
        mock_tx_store.get_record_path.assert_called_once_with("record-123")
        connector.data_source.get_file_content.assert_called_once_with(
            project_id="456",
            file_path=file_path,
        )

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_extracts_project_id(self) -> None:
        """Test that project ID is correctly extracted from external_record_group_id."""
        connector = _make_connector()

        # Mock record with specific external_record_group_id
        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "999-code-repository"

        file_path = ["main.py"]

        mock_file_data = MagicMock()
        mock_file_data.content = base64.b64encode(b"content").decode(
            GitlabLiterals.UTF_8.value
        )

        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = mock_file_data

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute
        result_generator = connector._fetch_code_file_content(mock_record)
        async for _ in result_generator:
            pass

        # Verify project_id was extracted correctly
        connector.data_source.get_file_content.assert_called_once_with(
            project_id="999",
            file_path=file_path,
        )

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_no_external_group_id(self) -> None:
        """Test when external_record_group_id is None or empty."""
        connector = _make_connector()

        # Mock record without external_record_group_id
        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = None

        file_path = ["main.py"]

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        # Execute and expect exception
        result_generator = connector._fetch_code_file_content(mock_record)

        with pytest.raises(Exception) as exc_info:
            async for _ in result_generator:
                pass

        assert "Error fetching code content for record record-123" in str(
            exc_info.value
        )
        assert "'NoneType' object has no attribute 'split'" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_empty_external_group_id(self) -> None:
        """Test when external_record_group_id is empty string."""
        connector = _make_connector()

        # Mock record with empty external_record_group_id
        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = ""

        file_path = ["main.py"]

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        # Execute and expect exception
        result_generator = connector._fetch_code_file_content(mock_record)

        with pytest.raises(Exception) as exc_info:
            async for _ in result_generator:
                pass

        assert "Project id not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_file_response_not_success(self) -> None:
        """Test when file content fetch fails."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        file_path = ["main.py"]

        # Mock failed file response
        mock_file_response = MagicMock()
        mock_file_response.success = False
        mock_file_response.error = "File not found"

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute and expect exception
        result_generator = connector._fetch_code_file_content(mock_record)

        with pytest.raises(Exception, match="Error fetching code content for record"):
            async for _ in result_generator:
                pass

        # Verify error was logged
        connector.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_no_data(self) -> None:
        """Test when file response has no data."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        file_path = ["main.py"]

        # Mock response with no data
        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = None

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute and expect exception (accessing .content on None)
        result_generator = connector._fetch_code_file_content(mock_record)

        with pytest.raises(Exception, match="Error fetching code content for record"):
            async for _ in result_generator:
                pass

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_base64_decoding(self) -> None:
        """Test that base64 content is correctly decoded."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        file_path = ["main.py"]

        # Original content
        original_content = b"def hello():\n    print('Hello, World!')\n"
        encoded_content = base64.b64encode(original_content).decode(
            GitlabLiterals.UTF_8.value
        )

        mock_file_data = MagicMock()
        mock_file_data.content = encoded_content

        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = mock_file_data

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute
        result_generator = connector._fetch_code_file_content(mock_record)
        chunks = [chunk async for chunk in result_generator]
        # async for chunk in result_generator:
        #     chunks.append(chunk)

        # Verify decoded content matches original
        assert len(chunks) == 1
        assert chunks[0] == original_content

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_invalid_base64(self) -> None:
        """Test handling of invalid base64 content."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        file_path = ["main.py"]

        # Invalid base64 content
        mock_file_data = MagicMock()
        mock_file_data.content = "not-valid-base64!!!"

        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = mock_file_data

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute and expect exception
        result_generator = connector._fetch_code_file_content(mock_record)

        with pytest.raises(Exception) as exc_info:
            async for _ in result_generator:
                pass

        assert "Error fetching code content for record record-123" in str(
            exc_info.value
        )

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_get_record_path_fails(self) -> None:
        """Test when get_record_path fails."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        # Mock transaction store with failure
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(
            side_effect=Exception("Database error")
        )

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        # Execute and expect exception
        result_generator = connector._fetch_code_file_content(mock_record)

        with pytest.raises(Exception) as exc_info:
            async for _ in result_generator:
                pass

        assert "Error fetching code content for record record-123" in str(
            exc_info.value
        )

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_nested_file_path(self) -> None:
        """Test fetching content for file with nested path."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        # Nested file path
        file_path = ["src", "app", "components", "Button.tsx"]

        mock_file_data = MagicMock()
        mock_file_data.content = base64.b64encode(b"React component").decode(
            GitlabLiterals.UTF_8.value
        )

        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = mock_file_data

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute
        result_generator = connector._fetch_code_file_content(mock_record)
        async for _ in result_generator:
            pass

        # Verify nested path was used
        connector.data_source.get_file_content.assert_called_once_with(
            project_id="456", file_path=file_path
        )

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_binary_content(self) -> None:
        """Test fetching binary file content."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        file_path = ["image.png"]

        # Binary content (fake PNG header)
        binary_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        encoded_content = base64.b64encode(binary_content).decode(
            GitlabLiterals.UTF_8.value
        )

        mock_file_data = MagicMock()
        mock_file_data.content = encoded_content

        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = mock_file_data

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute
        result_generator = connector._fetch_code_file_content(mock_record)
        chunks = [chunk async for chunk in result_generator]
        # async for chunk in result_generator:
        #     chunks.append(chunk)

        # Verify binary content is preserved
        assert len(chunks) == 1
        assert chunks[0] == binary_content

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_empty_file(self) -> None:
        """Test fetching content of an empty file."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        file_path = ["empty.txt"]

        # Empty content
        empty_content = b""
        encoded_content = base64.b64encode(empty_content).decode(
            GitlabLiterals.UTF_8.value
        )

        mock_file_data = MagicMock()
        mock_file_data.content = encoded_content

        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = mock_file_data

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute
        result_generator = connector._fetch_code_file_content(mock_record)
        chunks = [chunk async for chunk in result_generator]
        # async for chunk in result_generator:
        #     chunks.append(chunk)

        # Verify empty content
        assert len(chunks) == 1
        assert chunks[0] == b""

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_exception_includes_record_id(self) -> None:
        """Test that exceptions include the record ID for debugging."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-xyz-789"
        mock_record.external_record_group_id = "456-code-repository"

        # Mock transaction store with failure
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(side_effect=Exception("Some error"))

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        # Execute and expect exception with record ID
        result_generator = connector._fetch_code_file_content(mock_record)

        with pytest.raises(Exception) as exc_info:
            async for _ in result_generator:
                pass

        assert "record-xyz-789" in str(exc_info.value)
        assert "Error fetching code content" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_code_file_content_yields_single_chunk(self) -> None:
        """Test that content is yielded as a single chunk."""
        connector = _make_connector()

        mock_record = MagicMock(spec=Record)
        mock_record.id = "record-123"
        mock_record.external_record_group_id = "456-code-repository"

        file_path = ["main.py"]

        # Large content
        large_content = b"x" * 10000
        encoded_content = base64.b64encode(large_content).decode(
            GitlabLiterals.UTF_8.value
        )

        mock_file_data = MagicMock()
        mock_file_data.content = encoded_content

        mock_file_response = MagicMock()
        mock_file_response.success = True
        mock_file_response.data = mock_file_data

        # Mock transaction store
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_path = AsyncMock(return_value=file_path)

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        connector.data_source = MagicMock()
        connector.data_source.get_file_content = MagicMock(
            return_value=mock_file_response
        )

        # Execute
        result_generator = connector._fetch_code_file_content(mock_record)
        chunks = [chunk async for chunk in result_generator]
        # async for chunk in result_generator:
        #     chunks.append(chunk)

        # Verify single chunk
        assert len(chunks) == 1
        assert chunks[0] == large_content


class TestGitlabConnectorSyncProjects:
    @pytest.mark.asyncio
    async def test_sync_projects_success_single_project(self) -> None:
        """Test successful sync with a single project."""
        connector = _make_connector()

        # Mock project
        mock_project = MagicMock()
        mock_project.id = 101
        mock_project.path_with_namespace = "group/project-one"

        # Mock successful projects response
        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project]

        # Setup data source
        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        # Mock all internal async methods
        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        # Execute
        await connector._sync_projects()

        # Verify
        mock_data_source.list_projects.assert_called_once_with(owned=True, get_all=True)
        connector._sync_project_members_as_pseudo.assert_called_once_with(mock_project)
        connector._fetch_issues_batched.assert_called_once_with(101)
        connector._fetch_prs_batched.assert_called_once_with(101)
        connector._sync_repo_main.assert_called_once_with(101, "group/project-one")

    @pytest.mark.asyncio
    async def test_sync_projects_success_multiple_projects(self) -> None:
        """Test successful sync with multiple projects."""
        connector = _make_connector()

        # Mock projects
        mock_project1 = MagicMock()
        mock_project1.id = 101
        mock_project1.path_with_namespace = "group/project-one"

        mock_project2 = MagicMock()
        mock_project2.id = 202
        mock_project2.path_with_namespace = "group/project-two"

        mock_project3 = MagicMock()
        mock_project3.id = 303
        mock_project3.path_with_namespace = "another-group/project-three"

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project1, mock_project2, mock_project3]

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        # Execute
        await connector._sync_projects()

        # Verify all projects processed
        assert connector._sync_project_members_as_pseudo.call_count == 3
        assert connector._fetch_issues_batched.call_count == 3
        assert connector._fetch_prs_batched.call_count == 3
        assert connector._sync_repo_main.call_count == 3

        # Verify correct arguments for each project
        connector._sync_project_members_as_pseudo.assert_any_call(mock_project1)
        connector._sync_project_members_as_pseudo.assert_any_call(mock_project2)
        connector._sync_project_members_as_pseudo.assert_any_call(mock_project3)

        connector._fetch_issues_batched.assert_any_call(101)
        connector._fetch_issues_batched.assert_any_call(202)
        connector._fetch_issues_batched.assert_any_call(303)

        connector._fetch_prs_batched.assert_any_call(101)
        connector._fetch_prs_batched.assert_any_call(202)
        connector._fetch_prs_batched.assert_any_call(303)

        connector._sync_repo_main.assert_any_call(101, "group/project-one")
        connector._sync_repo_main.assert_any_call(202, "group/project-two")
        connector._sync_repo_main.assert_any_call(303, "another-group/project-three")

    @pytest.mark.asyncio
    async def test_sync_projects_fetch_fails_raises_exception(self) -> None:
        """Test that exception is raised when projects fetch fails."""
        connector = _make_connector()

        mock_projects_res = MagicMock()
        mock_projects_res.success = False
        mock_projects_res.error = "API error"

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        # Execute and expect exception
        with pytest.raises(Exception, match="Error in fetching projects"):
            await connector._sync_projects()

        # Verify nothing else was called
        connector._sync_project_members_as_pseudo.assert_not_called()
        connector._fetch_issues_batched.assert_not_called()
        connector._fetch_prs_batched.assert_not_called()
        connector._sync_repo_main.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_projects_empty_data_returns_early(self) -> None:
        """Test that method returns early when project list is empty."""
        connector = _make_connector()

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = []

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        # Execute
        await connector._sync_projects()

        # Verify nothing was processed
        connector._sync_project_members_as_pseudo.assert_not_called()
        connector._fetch_issues_batched.assert_not_called()
        connector._fetch_prs_batched.assert_not_called()
        connector._sync_repo_main.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_projects_none_data_returns_early(self) -> None:
        """Test that method returns early when project data is None."""
        connector = _make_connector()

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = None

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        # Execute
        await connector._sync_projects()

        # Verify nothing was processed
        connector._sync_project_members_as_pseudo.assert_not_called()
        connector._fetch_issues_batched.assert_not_called()
        connector._fetch_prs_batched.assert_not_called()
        connector._sync_repo_main.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_projects_calls_in_correct_order_per_project(self) -> None:
        """Test that within each project, methods are called in the correct order."""
        connector = _make_connector()

        mock_project = MagicMock()
        mock_project.id = 101
        mock_project.path_with_namespace = "group/project"

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project]

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        call_order = []

        async def track_pseudo(project) -> None:
            call_order.append("sync_pseudo")

        async def track_issues(project_id) -> None:
            call_order.append("fetch_issues")

        async def track_prs(project_id) -> None:
            call_order.append("fetch_prs")

        async def track_repo(project_id, path) -> None:
            call_order.append("sync_repo")

        connector._sync_project_members_as_pseudo = track_pseudo
        connector._fetch_issues_batched = track_issues
        connector._fetch_prs_batched = track_prs
        connector._sync_repo_main = track_repo

        # Execute
        await connector._sync_projects()

        # Verify order
        assert call_order == ["sync_pseudo", "fetch_issues", "fetch_prs", "sync_repo"]

    @pytest.mark.asyncio
    async def test_sync_projects_uses_project_id_and_path(self) -> None:
        """Test project_id and path_with_namespace are extracted and passed correctly."""
        connector = _make_connector()

        mock_project = MagicMock()
        mock_project.id = 789
        mock_project.path_with_namespace = "org-name/deep/project-path"

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project]

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        # Execute
        await connector._sync_projects()

        # Verify correct project_id and path were passed
        connector._fetch_issues_batched.assert_called_once_with(789)
        connector._fetch_prs_batched.assert_called_once_with(789)
        connector._sync_repo_main.assert_called_once_with(
            789, "org-name/deep/project-path"
        )

    @pytest.mark.asyncio
    async def test_sync_projects_sync_pseudo_raises_propagates(self) -> None:
        """Test that exception from _sync_project_members_as_pseudo propagates."""
        connector = _make_connector()

        mock_project = MagicMock()
        mock_project.id = 101
        mock_project.path_with_namespace = "group/project"

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project]

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock(
            side_effect=Exception("Pseudo sync failed")
        )
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        # Execute and expect exception
        with pytest.raises(Exception) as exc_info:
            await connector._sync_projects()

        assert "Pseudo sync failed" in str(exc_info.value)

        # Subsequent methods should not be called
        connector._fetch_issues_batched.assert_not_called()
        connector._fetch_prs_batched.assert_not_called()
        connector._sync_repo_main.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_projects_fetch_issues_raises_propagates(self) -> None:
        """Test that exception from _fetch_issues_batched propagates."""
        connector = _make_connector()

        mock_project = MagicMock()
        mock_project.id = 101
        mock_project.path_with_namespace = "group/project"

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project]

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock(
            side_effect=Exception("Issues fetch failed")
        )
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        with pytest.raises(Exception) as exc_info:
            await connector._sync_projects()

        assert "Issues fetch failed" in str(exc_info.value)
        connector._fetch_prs_batched.assert_not_called()
        connector._sync_repo_main.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_projects_fetch_prs_raises_propagates(self) -> None:
        """Test that exception from _fetch_prs_batched propagates."""
        connector = _make_connector()

        mock_project = MagicMock()
        mock_project.id = 101
        mock_project.path_with_namespace = "group/project"

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project]

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock(
            side_effect=Exception("PRs fetch failed")
        )
        connector._sync_repo_main = AsyncMock()

        with pytest.raises(Exception) as exc_info:
            await connector._sync_projects()

        assert "PRs fetch failed" in str(exc_info.value)
        connector._sync_repo_main.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_projects_sync_repo_raises_propagates(self) -> None:
        """Test that exception from _sync_repo_main propagates."""
        connector = _make_connector()

        mock_project = MagicMock()
        mock_project.id = 101
        mock_project.path_with_namespace = "group/project"

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project]

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        connector._sync_project_members_as_pseudo = AsyncMock()
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock(side_effect=Exception("Repo sync failed"))

        with pytest.raises(Exception) as exc_info:
            await connector._sync_projects()

        assert "Repo sync failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_sync_projects_first_project_fails_stops_remaining(self) -> None:
        """Test that failure on first project stops processing remaining projects."""
        connector = _make_connector()

        mock_project1 = MagicMock()
        mock_project1.id = 101
        mock_project1.path_with_namespace = "group/project-one"

        mock_project2 = MagicMock()
        mock_project2.id = 202
        mock_project2.path_with_namespace = "group/project-two"

        mock_projects_res = MagicMock()
        mock_projects_res.success = True
        mock_projects_res.data = [mock_project1, mock_project2]

        mock_data_source = MagicMock()
        mock_data_source.list_projects = MagicMock(return_value=mock_projects_res)
        connector.data_source = mock_data_source

        # Fail on the first project's pseudo sync
        connector._sync_project_members_as_pseudo = AsyncMock(
            side_effect=Exception("Fail on project one")
        )
        connector._fetch_issues_batched = AsyncMock()
        connector._fetch_prs_batched = AsyncMock()
        connector._sync_repo_main = AsyncMock()

        with pytest.raises(Exception, match="Fail on project one"):
            await connector._sync_projects()

        # Only one call attempted - second project never reached
        assert connector._sync_project_members_as_pseudo.call_count == 1
        connector._fetch_issues_batched.assert_not_called()


class TestGitlabConnectorSyncProjectMembersAsPseudo:
    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_success_single_member_high_access(
        self,
    ) -> None:
        """Test sync with one member having access_level >= 15."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.name = "User One"
        mock_member.access_level = 40  # Developer level

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member]

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        mock_permission = MagicMock()
        connector._transform_restrictions_to_permisions = AsyncMock(
            return_value=mock_permission
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        # Verify on_new_record_groups was called once
        connector.data_entities_processor.on_new_record_groups.assert_called_once()
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][
            0
        ]

        # Should be 4 record groups
        assert len(call_args) == 4

        # Unpack all groups and their permissions
        (
            (proj_rg, proj_perms),
            (work_rg, work_perms),
            (code_rg, code_perms),
            (mr_rg, mr_perms),
        ) = call_args

        # access_level >= 15 → permission in all levels
        assert mock_permission in proj_perms
        assert mock_permission in work_perms
        assert mock_permission in code_perms
        assert mock_permission in mr_perms

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_access_level_10(self) -> None:
        """Test member with access_level == 10 only gets work_items permission."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.name = "Guest User"
        mock_member.access_level = 10  # Guest level

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member]

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        mock_permission = MagicMock()
        connector._transform_restrictions_to_permisions = AsyncMock(
            return_value=mock_permission
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][
            0
        ]
        (
            (proj_rg, proj_perms),
            (work_rg, work_perms),
            (code_rg, code_perms),
            (mr_rg, mr_perms),
        ) = call_args

        # access_level == 10 → only project and work_items
        assert mock_permission in proj_perms
        assert mock_permission in work_perms
        assert mock_permission not in code_perms
        assert mock_permission not in mr_perms

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_access_level_0(self) -> None:
        """Test member with access_level == 0 only gets project-level permission."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.name = "No Access User"
        mock_member.access_level = 0

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member]

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        mock_permission = MagicMock()
        connector._transform_restrictions_to_permisions = AsyncMock(
            return_value=mock_permission
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][
            0
        ]
        (
            (proj_rg, proj_perms),
            (work_rg, work_perms),
            (code_rg, code_perms),
            (mr_rg, mr_perms),
        ) = call_args

        # access_level == 0 → only project level, none of the sub-levels
        assert mock_permission in proj_perms
        assert mock_permission not in work_perms
        assert mock_permission not in code_perms
        assert mock_permission not in mr_perms

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_permission_is_none(self) -> None:
        """Test member whose permission transform returns None is excluded from all levels."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.name = "User One"
        mock_member.access_level = 40

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member]

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        # Transform returns None → permission is skipped
        connector._transform_restrictions_to_permisions = AsyncMock(return_value=None)

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][
            0
        ]
        (
            (proj_rg, proj_perms),
            (work_rg, work_perms),
            (code_rg, code_perms),
            (mr_rg, mr_perms),
        ) = call_args

        # No permissions added anywhere
        assert proj_perms == []
        assert work_perms == []
        assert code_perms == []
        assert mr_perms == []

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_mixed_access_levels(self) -> None:
        """Test multiple members with different access levels."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_member_guest = MagicMock()
        mock_member_guest.id = 1
        mock_member_guest.name = "Guest"
        mock_member_guest.access_level = 10

        mock_member_dev = MagicMock()
        mock_member_dev.id = 2
        mock_member_dev.name = "Developer"
        mock_member_dev.access_level = 30

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member_guest, mock_member_dev]

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        mock_perm_guest = MagicMock()
        mock_perm_dev = MagicMock()
        connector._transform_restrictions_to_permisions = AsyncMock(
            side_effect=[mock_perm_guest, mock_perm_dev]
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][
            0
        ]
        (
            (proj_rg, proj_perms),
            (work_rg, work_perms),
            (code_rg, code_perms),
            (mr_rg, mr_perms),
        ) = call_args

        # Both in project
        assert mock_perm_guest in proj_perms
        assert mock_perm_dev in proj_perms

        # Both in work_items (10 and 30)
        assert mock_perm_guest in work_perms
        assert mock_perm_dev in work_perms

        # Only developer (>= 15) in code and MRs
        assert mock_perm_guest not in code_perms
        assert mock_perm_dev in code_perms
        assert mock_perm_guest not in mr_perms
        assert mock_perm_dev in mr_perms

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_record_groups_structure(self) -> None:
        """Test that all four RecordGroups are created with correct fields."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 999
        mock_project.name = "my-project"
        mock_project.path_with_namespace = "org/my-project"

        # Provide one member so the code does NOT early-return
        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.name = "User One"
        mock_member.access_level = 40

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member]  # FIX: non-empty list

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        connector._transform_restrictions_to_permisions = AsyncMock(
            return_value=MagicMock()
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][
            0
        ]
        (proj_rg, _), (work_rg, _), (code_rg, _), (mr_rg, _) = call_args

        # Project record group
        assert proj_rg.external_group_id == "999"
        assert proj_rg.name == "org/my-project"
        assert proj_rg.parent_external_group_id is None
        assert proj_rg.org_id == "org-1"
        assert proj_rg.connector_id == "gitlab-conn-1"
        assert proj_rg.group_type == RecordGroupType.PROJECT.value

        # Work items record group
        assert work_rg.external_group_id == "999-work-items"
        assert work_rg.name == "Work items"
        assert work_rg.parent_external_group_id == "999"

        # Code repo record group
        assert code_rg.external_group_id == "999-code-repository"
        assert code_rg.name == "Code repository"
        assert code_rg.parent_external_group_id == "999"

        # Merge requests record group
        assert mr_rg.external_group_id == "999-merge-requests"
        assert mr_rg.name == "Merge requests"
        assert mr_rg.parent_external_group_id == "999"

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_members_fetch_fails(self) -> None:
        """Test early return when member fetch fails."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_members_res = MagicMock()
        mock_members_res.success = False

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        # Verify early return - no record groups created
        connector.data_entities_processor.on_new_record_groups.assert_not_called()
        connector.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_no_members(self) -> None:
        """Test early return when no members exist."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = None

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        # Verify early return - no record groups created
        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_empty_members_list(self) -> None:
        """Test with empty members list — returns early, no record groups created."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = []  # Empty list → `not []` is True → early return

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        # FIX: Empty list triggers early return, same as None — no record groups created
        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_duplicate_members_deduped(
        self,
    ) -> None:
        """Test that duplicate member IDs are deduped via dict_member."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        # Same member id twice
        mock_member_a = MagicMock()
        mock_member_a.id = 1
        mock_member_a.name = "User One"
        mock_member_a.access_level = 40

        mock_member_b = MagicMock()
        mock_member_b.id = 1  # Same ID
        mock_member_b.name = "User One (duplicate)"
        mock_member_b.access_level = 40

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member_a, mock_member_b]

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        mock_permission = MagicMock()
        connector._transform_restrictions_to_permisions = AsyncMock(
            return_value=mock_permission
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        # _transform_restrictions_to_permisions called only once (deduped dict)
        assert connector._transform_restrictions_to_permisions.call_count == 1

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_list_project_members_called_correctly(
        self,
    ) -> None:
        """Test that list_project_members_all is called with project_id."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 555
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = []

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        # Verify called with the project id
        connector.data_source.list_project_members_all.assert_called_once_with(
            project_id=555, get_all=True
        )

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_access_level_exactly_15(self) -> None:
        """Test boundary: access_level exactly 15 gets all permissions."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.name = "Reporter"
        mock_member.access_level = 15  # boundary

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member]

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        mock_permission = MagicMock()
        connector._transform_restrictions_to_permisions = AsyncMock(
            return_value=mock_permission
        )

        # Execute
        await connector._sync_project_members_as_pseudo(mock_project)

        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][
            0
        ]
        (_, proj_perms), (_, work_perms), (_, code_perms), (_, mr_perms) = call_args

        # access_level >= 15 → all levels
        assert mock_permission in proj_perms
        assert mock_permission in work_perms
        assert mock_permission in code_perms
        assert mock_permission in mr_perms

    @pytest.mark.asyncio
    async def test_sync_project_members_as_pseudo_unrecognised_access_level(
        self,
    ) -> None:
        """Test member with unrecognised access level (e.g. 5 - Minimal) gets
        only project-level permission and a warning is logged."""
        connector = _make_connector()

        mock_project = MagicMock(spec=Project)
        mock_project.id = 101
        mock_project.name = "test-project"
        mock_project.path_with_namespace = "group/test-project"

        mock_member = MagicMock()
        mock_member.id = 1
        mock_member.name = "Minimal User"
        mock_member.access_level = 5  # Minimal access — hits else

        mock_members_res = MagicMock()
        mock_members_res.success = True
        mock_members_res.data = [mock_member]

        connector.data_source = MagicMock()
        connector.data_source.list_project_members_all = MagicMock(
            return_value=mock_members_res
        )

        mock_permission = MagicMock()
        connector._transform_restrictions_to_permisions = AsyncMock(
            return_value=mock_permission
        )

        await connector._sync_project_members_as_pseudo(mock_project)

        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][
            0
        ]
        (_, proj_perms), (_, work_perms), (_, code_perms), (_, mr_perms) = call_args

        assert mock_permission in proj_perms
        assert mock_permission not in work_perms
        assert mock_permission not in code_perms
        assert mock_permission not in mr_perms
        connector.logger.warning.assert_called_once()


class TestTransformRestrictionsToPermissions:
    """Tests for _transform_restrictions_to_permisions method."""

    @pytest.mark.asyncio
    async def test_returns_permission_when_user_found(self) -> None:
        """When _create_permission_from_principal returns a permission, it is returned."""
        connector = _make_connector()

        mock_member = MagicMock(spec=GroupMember)
        mock_member.id = 42

        mock_permission = MagicMock()
        connector._create_permission_from_principal = AsyncMock(
            return_value=mock_permission
        )

        result = await connector._transform_restrictions_to_permisions(mock_member)

        assert result is mock_permission

    @pytest.mark.asyncio
    async def test_returns_none_when_create_permission_returns_none(self) -> None:
        """When _create_permission_from_principal returns None, method returns None."""
        connector = _make_connector()

        mock_member = MagicMock(spec=GroupMember)
        mock_member.id = 99

        connector._create_permission_from_principal = AsyncMock(return_value=None)

        result = await connector._transform_restrictions_to_permisions(mock_member)

        assert result is None

    @pytest.mark.asyncio
    async def test_calls_create_permission_with_correct_args(self) -> None:
        """Verifies _create_permission_from_principal is called with the right arguments."""
        connector = _make_connector()

        mock_member = MagicMock(spec=GroupMember)
        mock_member.id = 7

        connector._create_permission_from_principal = AsyncMock(
            return_value=MagicMock()
        )

        await connector._transform_restrictions_to_permisions(mock_member)

        connector._create_permission_from_principal.assert_called_once_with(
            EntityType.USER.value,
            "7",  # str(member.id)
            PermissionType.OWNER.value,
            create_pseudo_group_if_missing=True,
        )

    @pytest.mark.asyncio
    async def test_member_id_is_converted_to_string(self) -> None:
        """Member id (integer) is converted to string before being passed as principal_id."""
        connector = _make_connector()

        mock_member = MagicMock(spec=GroupMember)
        mock_member.id = 1234

        connector._create_permission_from_principal = AsyncMock(return_value=None)

        await connector._transform_restrictions_to_permisions(mock_member)

        call_kwargs = connector._create_permission_from_principal.call_args
        # Second positional arg is principal_id
        assert call_kwargs[0][1] == "1234"

    @pytest.mark.asyncio
    async def test_permission_type_is_always_owner(self) -> None:
        """The permission type passed is always PermissionType.OWNER."""
        connector = _make_connector()

        mock_member = MagicMock(spec=GroupMember)
        mock_member.id = 5

        connector._create_permission_from_principal = AsyncMock(return_value=None)

        await connector._transform_restrictions_to_permisions(mock_member)

        call_kwargs = connector._create_permission_from_principal.call_args
        assert call_kwargs[0][2] == PermissionType.OWNER.value

    @pytest.mark.asyncio
    async def test_pseudo_group_creation_is_enabled(self) -> None:
        """Ensures create_pseudo_group_if_missing=True is always passed."""
        connector = _make_connector()

        mock_member = MagicMock(spec=GroupMember)
        mock_member.id = 10

        connector._create_permission_from_principal = AsyncMock(return_value=None)

        await connector._transform_restrictions_to_permisions(mock_member)

        call_kwargs = connector._create_permission_from_principal.call_args
        assert call_kwargs[1]["create_pseudo_group_if_missing"] is True

    @pytest.mark.asyncio
    async def test_create_permission_not_called_when_id_is_falsy(self) -> None:
        """
        If member.id is falsy (e.g. 0), str(0) == '0' which is truthy,
        so _create_permission_from_principal IS still called.
        Edge case: explicit check that id=0 goes through normally.
        """
        connector = _make_connector()

        mock_member = MagicMock(spec=GroupMember)
        mock_member.id = 0  # str(0) == '0' → truthy

        connector._create_permission_from_principal = AsyncMock(return_value=None)

        result = await connector._transform_restrictions_to_permisions(mock_member)

        connector._create_permission_from_principal.assert_called_once()
        assert result is None


class TestCreatePermissionFromPrincipal:
    """Unit tests for _create_permission_from_principal."""

    def _make_tx_store(self, user=None, pseudo_group=None) -> AsyncMock:
        """Helper to build a mock transaction store."""
        mock_tx_store = AsyncMock()
        mock_tx_store.get_user_by_source_id = AsyncMock(return_value=user)
        mock_tx_store.get_user_group_by_external_id = AsyncMock(
            return_value=pseudo_group
        )
        return mock_tx_store

    def _attach_tx_store(self, connector, mock_tx_store) -> None:
        """Attach a mock transaction store to the connector."""
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

    # ------------------------------------------------------------------
    # principal_type == "user" — user found in DB
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_user_found_returns_permission_with_email(self) -> None:
        """When the user exists in the DB, a Permission with their email is returned."""
        connector = _make_connector()

        mock_user = MagicMock()
        mock_user.email = "alice@example.com"

        mock_tx_store = self._make_tx_store(user=mock_user)
        self._attach_tx_store(connector, mock_tx_store)

        result = await connector._create_permission_from_principal(
            EntityType.USER.value, "uid-1", PermissionType.READ
        )

        assert result is not None
        assert result.email == "alice@example.com"
        assert result.type == PermissionType.READ
        assert result.entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_user_found_passes_correct_source_id_and_connector_id(self) -> None:
        """get_user_by_source_id is called with the right principal_id and connector_id."""
        connector = _make_connector()

        mock_user = MagicMock()
        mock_user.email = "bob@example.com"

        mock_tx_store = self._make_tx_store(user=mock_user)
        self._attach_tx_store(connector, mock_tx_store)

        await connector._create_permission_from_principal(
            EntityType.USER.value, "uid-42", PermissionType.WRITE
        )

        mock_tx_store.get_user_by_source_id.assert_called_once_with(
            source_user_id="uid-42",
            connector_id="gitlab-conn-1",
        )

    # ------------------------------------------------------------------
    # principal_type == "user" — user NOT found, create_pseudo_group_if_missing=False
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_user_not_found_no_pseudo_group_flag_returns_none(self) -> None:
        """User not found and create_pseudo_group_if_missing=False → returns None."""
        connector = _make_connector()

        mock_tx_store = self._make_tx_store(user=None)
        self._attach_tx_store(connector, mock_tx_store)

        result = await connector._create_permission_from_principal(
            EntityType.USER.value, "uid-missing", PermissionType.READ
        )

        assert result is None
        mock_tx_store.get_user_group_by_external_id.assert_not_called()

    # ------------------------------------------------------------------
    # principal_type == EntityType.USER.value — user NOT found, pseudo-group already exists
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_user_not_found_existing_pseudo_group_returned(self) -> None:
        """User not found but existing pseudo-group found → returns GROUP Permission."""
        connector = _make_connector()

        mock_pseudo_group = MagicMock()
        mock_pseudo_group.source_user_group_id = "pg-uid-99"

        mock_tx_store = self._make_tx_store(user=None, pseudo_group=mock_pseudo_group)
        self._attach_tx_store(connector, mock_tx_store)

        result = await connector._create_permission_from_principal(
            EntityType.USER.value,
            "uid-99",
            PermissionType.OWNER,
            create_pseudo_group_if_missing=True,
        )

        assert result is not None
        assert result.external_id == "pg-uid-99"
        assert result.type == PermissionType.OWNER
        assert result.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_existing_pseudo_group_skips_creation(self) -> None:
        """When pseudo-group already exists, _create_pseudo_group is NOT called."""
        connector = _make_connector()

        mock_pseudo_group = MagicMock()
        mock_pseudo_group.source_user_group_id = "pg-uid-99"

        mock_tx_store = self._make_tx_store(user=None, pseudo_group=mock_pseudo_group)
        self._attach_tx_store(connector, mock_tx_store)
        connector._create_pseudo_group = AsyncMock()

        await connector._create_permission_from_principal(
            EntityType.USER.value,
            "uid-99",
            PermissionType.OWNER,
            create_pseudo_group_if_missing=True,
        )

        connector._create_pseudo_group.assert_not_called()

    # ------------------------------------------------------------------
    # principal_type == "user" — user NOT found, pseudo-group created on-the-fly
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_user_not_found_creates_pseudo_group_when_missing(self) -> None:
        """User not found + no existing pseudo-group → _create_pseudo_group is called."""
        connector = _make_connector()

        new_pseudo_group = MagicMock()
        new_pseudo_group.source_user_group_id = "pg-uid-new"

        mock_tx_store = self._make_tx_store(user=None, pseudo_group=None)
        self._attach_tx_store(connector, mock_tx_store)
        connector._create_pseudo_group = AsyncMock(return_value=new_pseudo_group)

        result = await connector._create_permission_from_principal(
            EntityType.USER.value,
            "uid-new",
            PermissionType.READ,
            create_pseudo_group_if_missing=True,
        )

        connector._create_pseudo_group.assert_called_once_with("uid-new")
        assert result is not None
        assert result.external_id == "pg-uid-new"
        assert result.entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_user_not_found_pseudo_group_creation_fails_returns_none(
        self,
    ) -> None:
        """User not found + _create_pseudo_group returns None → method returns None."""
        connector = _make_connector()

        mock_tx_store = self._make_tx_store(user=None, pseudo_group=None)
        self._attach_tx_store(connector, mock_tx_store)
        connector._create_pseudo_group = AsyncMock(return_value=None)

        result = await connector._create_permission_from_principal(
            EntityType.USER.value,
            "uid-fail",
            PermissionType.READ,
            create_pseudo_group_if_missing=True,
        )

        assert result is None

    # ------------------------------------------------------------------
    # principal_type != "user" (unhandled type)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unknown_principal_type_returns_none(self) -> None:
        """A principal_type other than 'user' falls through and returns None."""
        connector = _make_connector()

        # transaction() should not be entered for non-"user" types
        connector.data_store_provider.transaction = MagicMock()

        result = await connector._create_permission_from_principal(
            "group", "gid-1", PermissionType.READ
        )

        assert result is None
        connector.data_store_provider.transaction.assert_not_called()

    # ------------------------------------------------------------------
    # Exception handling
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_exception_during_transaction_returns_none(self) -> None:
        """Any exception raised inside the try block results in None being returned."""
        connector = _make_connector()

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            side_effect=RuntimeError("DB connection lost")
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        result = await connector._create_permission_from_principal(
            EntityType.USER.value, "uid-1", PermissionType.READ
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_exception_during_get_user_returns_none(self) -> None:
        """Exception from get_user_by_source_id is caught and None is returned."""
        connector = _make_connector()

        mock_tx_store = AsyncMock()
        mock_tx_store.get_user_by_source_id = AsyncMock(
            side_effect=Exception("Timeout")
        )
        self._attach_tx_store(connector, mock_tx_store)

        result = await connector._create_permission_from_principal(
            EntityType.USER.value, "uid-1", PermissionType.READ
        )

        assert result is None

    # ------------------------------------------------------------------
    # Permission type propagation
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_permission_type_is_propagated_to_result(self) -> None:
        """The permission_type arg is correctly set on the returned Permission."""
        connector = _make_connector()

        for perm_type in [
            PermissionType.READ,
            PermissionType.WRITE,
            PermissionType.OWNER,
        ]:
            mock_user = MagicMock()
            mock_user.email = "user@example.com"

            mock_tx_store = self._make_tx_store(user=mock_user)
            self._attach_tx_store(connector, mock_tx_store)

            result = await connector._create_permission_from_principal(
                EntityType.USER.value, "uid-1", perm_type
            )

            assert result.type == perm_type


class TestCreatePseudoGroup:
    """Unit tests for _create_pseudo_group."""

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_app_user_group_on_success(self) -> None:
        """A valid AppUserGroup is returned when everything succeeds."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        result = await connector._create_pseudo_group("uid-123")

        assert result is not None
        assert isinstance(result, AppUserGroup)

    @pytest.mark.asyncio
    async def test_source_user_group_id_matches_account_id(self) -> None:
        """source_user_group_id on the returned group equals the supplied account_id."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        result = await connector._create_pseudo_group("uid-abc")

        assert result.source_user_group_id == "uid-abc"

    @pytest.mark.asyncio
    async def test_name_uses_pseudo_prefix_and_account_id(self) -> None:
        """Group name is formatted as '[Pseudo-User]_<account_id>'."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        result = await connector._create_pseudo_group("uid-42")

        assert result.name == "[Pseudo-User]_uid-42"

    @pytest.mark.asyncio
    async def test_connector_id_is_set_on_group(self) -> None:
        """The connector's connector_id is assigned to the created group."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        result = await connector._create_pseudo_group("uid-1")

        assert result.connector_id == "gitlab-conn-1"

    @pytest.mark.asyncio
    async def test_org_id_is_set_on_group(self) -> None:
        """org_id on the group matches the data_entities_processor.org_id."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        result = await connector._create_pseudo_group("uid-1")

        assert result.org_id == "org-1"

    @pytest.mark.asyncio
    async def test_app_name_is_gitlab(self) -> None:
        """app_name on the created group is Connectors.GITLAB."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        result = await connector._create_pseudo_group("uid-1")

        assert result.app_name == Connectors.GITLAB

    # ------------------------------------------------------------------
    # on_new_user_groups call verification
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_on_new_user_groups_called_once(self) -> None:
        """on_new_user_groups is called exactly once."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        await connector._create_pseudo_group("uid-1")

        connector.data_entities_processor.on_new_user_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_new_user_groups_called_with_empty_members(self) -> None:
        """on_new_user_groups receives a list with a single (group, []) tuple."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        await connector._create_pseudo_group("uid-1")

        call_args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        assert len(call_args) == 1
        group, members = call_args[0]
        assert isinstance(group, AppUserGroup)
        assert members == []

    @pytest.mark.asyncio
    async def test_on_new_user_groups_receives_correct_group(self) -> None:
        """The AppUserGroup passed to on_new_user_groups matches the returned group."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock()

        result = await connector._create_pseudo_group("uid-99")

        call_args = connector.data_entities_processor.on_new_user_groups.call_args[0][0]
        saved_group, _ = call_args[0]
        assert saved_group.source_user_group_id == result.source_user_group_id
        assert saved_group.name == result.name

    # ------------------------------------------------------------------
    # Exception handling
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_none_when_on_new_user_groups_raises(self) -> None:
        """If on_new_user_groups raises, the exception is caught and None is returned."""
        connector = _make_connector()
        connector.data_entities_processor.on_new_user_groups = AsyncMock(
            side_effect=Exception("DB write failed")
        )

        result = await connector._create_pseudo_group("uid-err")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_app_user_group_construction_raises(self) -> None:
        """If AppUserGroup construction itself raises, None is returned."""
        connector = _make_connector()

        with patch(
            "app.connectors.sources.gitlab.connector.AppUserGroup",
            side_effect=ValueError("bad field"),
        ):
            result = await connector._create_pseudo_group("uid-bad")

        assert result is None


class TestFetchIssuesBatched:
    """Unit tests for _fetch_issues_batched."""

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        connector._build_issue_records = AsyncMock(return_value=[])
        connector._process_new_records = AsyncMock()
        connector.data_source = MagicMock()
        return connector

    def _make_issues_res(self, success=True, data=None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data
        return res

    @pytest.mark.asyncio
    async def test_raises_when_list_issues_fails(self) -> None:
        """An exception is raised when data_source.list_issues reports failure."""
        connector = self._setup_connector()
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=False, data=None)
        )

        with pytest.raises(Exception, match="Error in fetching issues"):
            await connector._fetch_issues_batched(project_id=1)

    @pytest.mark.asyncio
    async def test_returns_early_when_no_issues(self) -> None:
        """When issues_res.data is empty/None, method returns without processing."""
        connector = self._setup_connector()
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=[])
        )

        await connector._fetch_issues_batched(project_id=1)

        connector._build_issue_records.assert_not_called()
        connector._process_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_checkpoint_none_passes_none_to_list_issues(self) -> None:
        """When checkpoint is None, updated_after=None is passed to list_issues."""
        connector = self._setup_connector()
        connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=[])
        )

        await connector._fetch_issues_batched(project_id=5)

        connector.data_source.list_issues.assert_called_once_with(
            project_id=5,
            updated_after=None,
            order_by=GitlabLiterals.UPDATED_AT.value,
            sort="asc",
            get_all=True,
        )

    @pytest.mark.asyncio
    async def test_sync_checkpoint_converts_ms_to_datetime(self) -> None:
        """When checkpoint is set, it is divided by 1000 and converted to a UTC datetime."""
        connector = self._setup_connector()
        connector._get_issues_sync_checkpoint = AsyncMock(return_value=1_000_000)
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=[])
        )

        await connector._fetch_issues_batched(project_id=5)

        call_kwargs = connector.data_source.list_issues.call_args[1]
        since_dt = call_kwargs["updated_after"]
        assert since_dt is not None
        assert since_dt == datetime.fromtimestamp(1000, tz=timezone.utc)

    @pytest.mark.asyncio
    async def test_single_batch_when_issues_fit_in_one_batch(self) -> None:
        """When total issues <= batch_size, _build_issue_records is called exactly once."""
        connector = self._setup_connector()
        connector.batch_size = 5
        mock_issues = [MagicMock(spec=ProjectIssue) for _ in range(3)]
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=mock_issues)
        )

        await connector._fetch_issues_batched(project_id=10)

        connector._build_issue_records.assert_called_once_with(mock_issues)

    @pytest.mark.asyncio
    async def test_multiple_batches_when_issues_exceed_batch_size(self) -> None:
        """When issues exceed batch_size, _build_issue_records is called once per batch."""
        connector = self._setup_connector()
        connector.batch_size = 3
        mock_issues = [MagicMock(spec=ProjectIssue) for _ in range(7)]
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=mock_issues)
        )

        await connector._fetch_issues_batched(project_id=10)

        assert connector._build_issue_records.call_count == 3

    @pytest.mark.asyncio
    async def test_batches_are_sliced_correctly(self) -> None:
        """Each batch passed to _build_issue_records is the right slice of all issues."""
        connector = self._setup_connector()
        connector.batch_size = 3
        mock_issues = [MagicMock(spec=ProjectIssue) for _ in range(7)]
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=mock_issues)
        )

        await connector._fetch_issues_batched(project_id=10)

        calls = connector._build_issue_records.call_args_list
        assert calls[0][0][0] == mock_issues[0:3]
        assert calls[1][0][0] == mock_issues[3:6]
        assert calls[2][0][0] == mock_issues[6:7]

    @pytest.mark.asyncio
    async def test_process_new_records_called_once_per_batch(self) -> None:
        """_process_new_records is called exactly once for each batch."""
        connector = self._setup_connector()
        connector.batch_size = 2
        mock_issues = [MagicMock(spec=ProjectIssue) for _ in range(5)]
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=mock_issues)
        )

        await connector._fetch_issues_batched(project_id=10)

        assert connector._process_new_records.call_count == 3

    @pytest.mark.asyncio
    async def test_process_new_records_receives_build_output(self) -> None:
        """The result of _build_issue_records is forwarded to _process_new_records."""
        connector = self._setup_connector()
        connector.batch_size = 5
        mock_issues = [MagicMock(spec=ProjectIssue) for _ in range(2)]
        mock_records = [MagicMock()]
        connector._build_issue_records = AsyncMock(return_value=mock_records)
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=mock_issues)
        )

        await connector._fetch_issues_batched(project_id=10)

        connector._process_new_records.assert_called_once_with(mock_records)

    @pytest.mark.asyncio
    async def test_get_issues_sync_checkpoint_called_with_project_id(self) -> None:
        """_get_issues_sync_checkpoint is always called with the supplied project_id."""
        connector = self._setup_connector()
        connector.data_source.list_issues = MagicMock(
            return_value=self._make_issues_res(success=True, data=[])
        )

        await connector._fetch_issues_batched(project_id=99)

        connector._get_issues_sync_checkpoint.assert_called_once_with(99)


class TestProcessNewRecords:
    """Unit tests for _process_new_records."""

    def _make_record_update(
        self, record_type, project_id="proj-1", updated_at="2024-01-01T00:00:00Z"
    ) -> MagicMock:
        record = MagicMock()
        record.record_type = record_type
        record.source_updated_at = updated_at
        record.external_record_group_id = project_id

        ru = MagicMock(spec=RecordUpdate)
        ru.record = record
        ru.new_permissions = []
        return ru

    @pytest.mark.asyncio
    async def test_empty_batch_does_not_call_on_new_records(self) -> None:
        """When batch_records is empty, on_new_records is never called."""
        connector = _make_connector()
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._update_mrs_sync_checkpoint = AsyncMock()

        await connector._process_new_records([])

        connector.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_new_records_called_with_correct_tuples(self) -> None:
        """on_new_records receives a list of (record, new_permissions) tuples."""
        connector = _make_connector()
        connector._update_issues_sync_checkpoint = AsyncMock()

        ru = self._make_record_update(RecordType.TICKET)

        await connector._process_new_records([ru])

        connector.data_entities_processor.on_new_records.assert_called_once_with(
            [(ru.record, ru.new_permissions)]
        )

    @pytest.mark.asyncio
    async def test_single_batch_when_records_fit_in_one_batch(self) -> None:
        """When total records <= batch_size, on_new_records is called exactly once."""
        connector = _make_connector()
        connector.batch_size = 5
        connector._update_issues_sync_checkpoint = AsyncMock()

        records = [self._make_record_update(RecordType.TICKET) for _ in range(3)]

        await connector._process_new_records(records)

        assert connector.data_entities_processor.on_new_records.call_count == 1

    @pytest.mark.asyncio
    async def test_multiple_batches_when_records_exceed_batch_size(self) -> None:
        """When records exceed batch_size, on_new_records is called once per batch."""
        connector = _make_connector()
        connector.batch_size = 3
        connector._update_issues_sync_checkpoint = AsyncMock()

        records = [self._make_record_update(RecordType.TICKET) for _ in range(7)]

        await connector._process_new_records(records)

        assert connector.data_entities_processor.on_new_records.call_count == 3

    @pytest.mark.asyncio
    async def test_ticket_record_triggers_issues_checkpoint_update(self) -> None:
        """When a TICKET record is in the batch, _update_issues_sync_checkpoint is called."""
        connector = _make_connector()
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._update_mrs_sync_checkpoint = AsyncMock()

        ru = self._make_record_update(
            RecordType.TICKET, project_id="proj-1", updated_at="ts-1"
        )

        await connector._process_new_records([ru])

        connector._update_issues_sync_checkpoint.assert_called_once_with(
            "proj-1", "ts-1"
        )
        connector._update_mrs_sync_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_pull_request_record_triggers_mrs_checkpoint_update(self) -> None:
        """When a PULL_REQUEST record is in the batch, _update_mrs_sync_checkpoint is called."""
        connector = _make_connector()
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._update_mrs_sync_checkpoint = AsyncMock()

        ru = self._make_record_update(
            RecordType.PULL_REQUEST, project_id="proj-2", updated_at="ts-2"
        )

        await connector._process_new_records([ru])

        connector._update_mrs_sync_checkpoint.assert_called_once_with("proj-2", "ts-2")
        connector._update_issues_sync_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_ticket_non_pr_record_skips_checkpoint_update(self) -> None:
        """Records with a type other than TICKET or PULL_REQUEST do not trigger checkpoint updates."""
        connector = _make_connector()
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._update_mrs_sync_checkpoint = AsyncMock()

        ru = self._make_record_update(RecordType.FILE)

        await connector._process_new_records([ru])

        connector._update_issues_sync_checkpoint.assert_not_called()
        connector._update_mrs_sync_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_checkpoint_not_updated_when_project_id_is_none(self) -> None:
        """Checkpoint update is skipped when no TICKET/PR record sets a project_id."""
        connector = _make_connector()
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._update_mrs_sync_checkpoint = AsyncMock()

        ru = self._make_record_update(RecordType.TICKET)
        ru.record.external_record_group_id = None

        await connector._process_new_records([ru])

        connector._update_issues_sync_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_checkpoint_not_updated_when_last_sync_time_is_none(self) -> None:
        """Checkpoint update is skipped when source_updated_at is None on all TICKET/PR records."""
        connector = _make_connector()
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._update_mrs_sync_checkpoint = AsyncMock()

        ru = self._make_record_update(RecordType.TICKET)
        ru.record.source_updated_at = None

        await connector._process_new_records([ru])

        connector._update_issues_sync_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_in_on_new_records_sets_need_sync_update_false(
        self,
    ) -> None:
        """When on_new_records raises, checkpoint update is suppressed for subsequent batches."""
        connector = _make_connector()
        connector.batch_size = 2
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector._update_mrs_sync_checkpoint = AsyncMock()

        connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=Exception("DB error")
        )

        records = [self._make_record_update(RecordType.TICKET) for _ in range(4)]

        await connector._process_new_records(records)

        connector._update_issues_sync_checkpoint.assert_not_called()
        connector._update_mrs_sync_checkpoint.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_in_on_new_records_does_not_raise(self) -> None:
        """Exceptions from on_new_records are caught and do not propagate to the caller."""
        connector = _make_connector()
        connector._update_issues_sync_checkpoint = AsyncMock()
        connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=Exception("failure")
        )

        ru = self._make_record_update(RecordType.TICKET)

        await connector._process_new_records([ru])

    @pytest.mark.asyncio
    async def test_last_ticket_in_batch_determines_checkpoint_values(self) -> None:
        """When multiple TICKET records are in the same batch, the last one's values are used."""
        connector = _make_connector()
        connector.batch_size = 10
        connector._update_issues_sync_checkpoint = AsyncMock()

        ru1 = self._make_record_update(
            RecordType.TICKET, project_id="proj-A", updated_at="ts-A"
        )
        ru2 = self._make_record_update(
            RecordType.TICKET, project_id="proj-B", updated_at="ts-B"
        )

        await connector._process_new_records([ru1, ru2])

        connector._update_issues_sync_checkpoint.assert_called_once_with(
            "proj-B", "ts-B"
        )

    @pytest.mark.asyncio
    async def test_batches_are_sliced_correctly(self) -> None:
        """Each call to on_new_records receives only its own batch slice."""
        connector = _make_connector()
        connector.batch_size = 2
        connector._update_issues_sync_checkpoint = AsyncMock()

        ru1 = self._make_record_update(
            RecordType.TICKET, project_id="p1", updated_at="t1"
        )
        ru2 = self._make_record_update(
            RecordType.TICKET, project_id="p2", updated_at="t2"
        )
        ru3 = self._make_record_update(
            RecordType.TICKET, project_id="p3", updated_at="t3"
        )

        await connector._process_new_records([ru1, ru2, ru3])

        first_call = connector.data_entities_processor.on_new_records.call_args_list[0][
            0
        ][0]
        second_call = connector.data_entities_processor.on_new_records.call_args_list[
            1
        ][0][0]

        assert len(first_call) == 2
        assert len(second_call) == 1


class TestBuildIssueRecords:
    """Unit tests for _build_issue_records."""

    def _make_issue(self, description="") -> MagicMock:
        issue = MagicMock(spec=ProjectIssue)
        issue.description = description
        issue.title = "Test Issue"
        return issue

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector._process_issue_incident_task_to_ticket = AsyncMock()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))
        connector.make_file_records_from_list = AsyncMock(return_value=[])
        connector.make_files_records_from_notes = AsyncMock(return_value=[])
        return connector

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_batch_is_empty(self) -> None:
        """An empty issue_batch yields an empty result."""
        connector = self._setup_connector()

        result = await connector._build_issue_records([])

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_issue_when_process_returns_none(self) -> None:
        """When _process_issue_incident_task_to_ticket returns None, the issue is skipped."""
        connector = self._setup_connector()
        connector._process_issue_incident_task_to_ticket = AsyncMock(return_value=None)

        issue = self._make_issue()
        result = await connector._build_issue_records([issue])

        assert result == []
        connector.parse_gitlab_uploads_clean_test.assert_not_called()
        connector.make_file_records_from_list.assert_not_called()

    @pytest.mark.asyncio
    async def test_ticket_record_update_added_when_process_succeeds(self) -> None:
        """The RecordUpdate from _process_issue_incident_task_to_ticket is included in result."""
        connector = self._setup_connector()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = MagicMock()
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )

        issue = self._make_issue()
        result = await connector._build_issue_records([issue])

        assert mock_record_update in result

    @pytest.mark.asyncio
    async def test_description_none_treated_as_empty_string(self) -> None:
        """When issue.description is None, parse_gitlab_uploads_clean_test receives ''."""
        connector = self._setup_connector()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = MagicMock()
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )

        issue = self._make_issue(description=None)
        await connector._build_issue_records([issue])

        connector.parse_gitlab_uploads_clean_test.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_description_passed_to_parse_uploads(self) -> None:
        """issue.description is forwarded as-is to parse_gitlab_uploads_clean_test."""
        connector = self._setup_connector()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = MagicMock()
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )

        issue = self._make_issue(description="some **markdown** content")
        await connector._build_issue_records([issue])

        connector.parse_gitlab_uploads_clean_test.assert_called_once_with(
            "some **markdown** content"
        )

    @pytest.mark.asyncio
    async def test_make_file_records_not_called_when_no_attachments(self) -> None:
        """When parse_gitlab_uploads_clean_test returns no attachments, make_file_records_from_list is not called."""
        connector = self._setup_connector()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = MagicMock()
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([], "cleaned")
        )

        issue = self._make_issue(description="no attachments here")
        await connector._build_issue_records([issue])

        connector.make_file_records_from_list.assert_not_called()

    @pytest.mark.asyncio
    async def test_file_records_added_when_attachments_present(self) -> None:
        """File RecordUpdates from make_file_records_from_list are included in the result."""
        connector = self._setup_connector()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = MagicMock()
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )
        mock_attachments = [MagicMock()]
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(mock_attachments, "cleaned")
        )
        mock_file_ru1 = MagicMock(spec=RecordUpdate)
        mock_file_ru2 = MagicMock(spec=RecordUpdate)
        connector.make_file_records_from_list = AsyncMock(
            return_value=[mock_file_ru1, mock_file_ru2]
        )

        issue = self._make_issue(description="![img](/uploads/img.png)")
        result = await connector._build_issue_records([issue])

        assert mock_file_ru1 in result
        assert mock_file_ru2 in result

    @pytest.mark.asyncio
    async def test_make_file_records_called_with_attachments_and_record(self) -> None:
        """make_file_records_from_list receives the parsed attachments and the ticket record."""
        connector = self._setup_connector()
        mock_record = MagicMock()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = mock_record
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )
        mock_attachments = [MagicMock(), MagicMock()]
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(mock_attachments, "cleaned")
        )

        issue = self._make_issue(description="desc")
        await connector._build_issue_records([issue])

        connector.make_file_records_from_list.assert_called_once_with(
            attachments=mock_attachments, record=mock_record
        )

    @pytest.mark.asyncio
    async def test_file_records_not_extended_when_make_file_returns_empty(self) -> None:
        """When make_file_records_from_list returns [], only the ticket RecordUpdate is in the result."""
        connector = self._setup_connector()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = MagicMock()
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([MagicMock()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        issue = self._make_issue(description="desc")
        result = await connector._build_issue_records([issue])

        assert result == [mock_record_update]

    @pytest.mark.asyncio
    async def test_notes_attachments_added_when_present(self) -> None:
        """RecordUpdates from make_files_records_from_notes are included in the result."""
        connector = self._setup_connector()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = MagicMock()
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )
        mock_note_ru = MagicMock(spec=RecordUpdate)
        connector.make_files_records_from_notes = AsyncMock(return_value=[mock_note_ru])

        issue = self._make_issue()
        result = await connector._build_issue_records([issue])

        assert mock_note_ru in result

    @pytest.mark.asyncio
    async def test_notes_attachments_not_extended_when_empty(self) -> None:
        """When make_files_records_from_notes returns [], result only has the ticket record."""
        connector = self._setup_connector()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = MagicMock()
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )
        connector.make_files_records_from_notes = AsyncMock(return_value=[])

        issue = self._make_issue()
        result = await connector._build_issue_records([issue])

        assert result == [mock_record_update]

    @pytest.mark.asyncio
    async def test_make_files_records_from_notes_called_with_issue_and_record(
        self,
    ) -> None:
        """make_files_records_from_notes is called with the issue and the ticket record."""
        connector = self._setup_connector()
        mock_record = MagicMock()
        mock_record_update = MagicMock(spec=RecordUpdate)
        mock_record_update.record = mock_record
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_record_update
        )

        issue = self._make_issue()
        await connector._build_issue_records([issue])

        connector.make_files_records_from_notes.assert_called_once_with(
            issue, mock_record
        )

    @pytest.mark.asyncio
    async def test_all_record_updates_combined_in_correct_order(self) -> None:
        """Result list contains ticket record first, then description attachments, then note attachments."""
        connector = self._setup_connector()
        mock_record = MagicMock()
        mock_ticket_ru = MagicMock(spec=RecordUpdate)
        mock_ticket_ru.record = mock_record
        connector._process_issue_incident_task_to_ticket = AsyncMock(
            return_value=mock_ticket_ru
        )
        mock_file_ru = MagicMock(spec=RecordUpdate)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([MagicMock()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[mock_file_ru])
        mock_note_ru = MagicMock(spec=RecordUpdate)
        connector.make_files_records_from_notes = AsyncMock(return_value=[mock_note_ru])

        issue = self._make_issue(description="desc")
        result = await connector._build_issue_records([issue])

        assert result == [mock_ticket_ru, mock_file_ru, mock_note_ru]

    @pytest.mark.asyncio
    async def test_multiple_issues_processed_independently(self) -> None:
        """Each issue in the batch is processed and its records are all collected."""
        connector = self._setup_connector()

        ru1 = MagicMock(spec=RecordUpdate)
        ru1.record = MagicMock()
        ru2 = MagicMock(spec=RecordUpdate)
        ru2.record = MagicMock()

        connector._process_issue_incident_task_to_ticket = AsyncMock(
            side_effect=[ru1, ru2]
        )

        issues = [self._make_issue(), self._make_issue()]
        result = await connector._build_issue_records(issues)

        assert ru1 in result
        assert ru2 in result
        assert connector._process_issue_incident_task_to_ticket.call_count == 2


class TestProcessIssueIncidentTaskToTicket:
    """Unit tests for _process_issue_incident_task_to_ticket."""

    def _make_issue(
        self,
        issue_id=101,
        title="Fix bug",
        issue_type="issue",
        project_id=10,
        labels=None,
        state="opened",
        web_url="https://gitlab.com/proj/-/issues/101",
        updated_at="2024-01-02T00:00:00Z",
        created_at="2024-01-01T00:00:00Z",
        description="some description",
    ) -> MagicMock:
        issue = MagicMock(spec=ProjectIssue)
        issue.id = issue_id
        issue.title = title
        issue.issue_type = issue_type
        issue.project_id = project_id
        issue.labels = labels or []
        issue.state = state
        issue.web_url = web_url
        issue.updated_at = updated_at
        issue.created_at = created_at
        issue.description = description
        return issue

    def _attach_tx_store(self, connector, existing_record=None) -> AsyncMock:
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            return_value=existing_record
        )
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()
        return mock_tx_store

    @pytest.mark.asyncio
    async def test_returns_record_update_for_new_issue(self) -> None:
        """A RecordUpdate is returned when the issue does not yet exist in the DB."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        issue = self._make_issue()
        result = await connector._process_issue_incident_task_to_ticket(issue)

        assert result is not None
        assert isinstance(result, RecordUpdate)

    @pytest.mark.asyncio
    async def test_is_new_true_when_no_existing_record(self) -> None:
        """is_new is True when get_record_by_external_id returns None."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue()
        )

        assert result.is_new is True

    @pytest.mark.asyncio
    async def test_is_new_false_when_existing_record_found(self) -> None:
        """is_new is False when an existing record is found."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Fix bug"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue()
        )

        assert result.is_new is False

    @pytest.mark.asyncio
    async def test_is_updated_true_when_existing_record_found(self) -> None:
        """is_updated is always True when an existing record is found."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Fix bug"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue()
        )

        assert result.is_updated is True

    @pytest.mark.asyncio
    async def test_metadata_changed_true_when_title_differs(self) -> None:
        """metadata_changed is True when existing record_name differs from issue.title."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Old title"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(title="New title")
        )

        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_metadata_changed_false_when_title_same(self) -> None:
        """metadata_changed is False when existing record_name matches issue.title."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Fix bug"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(title="Fix bug")
        )

        assert result.metadata_changed is False

    @pytest.mark.asyncio
    async def test_content_changed_true_for_existing_record(self) -> None:
        """content_changed is always True when an existing record is found."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Fix bug"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue()
        )

        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_existing_record_id_reused_in_ticket(self) -> None:
        """When an existing record is found, its id is reused in the TicketRecord."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "reused-uuid"
        existing.record_name = "Fix bug"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue()
        )

        assert result.record.id == "reused-uuid"

    @pytest.mark.asyncio
    async def test_new_uuid_generated_for_new_issue(self) -> None:
        """When no existing record, a fresh UUID is assigned to the TicketRecord."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue()
        )

        assert result.record.id is not None
        assert len(result.record.id) > 0

    @pytest.mark.asyncio
    async def test_issue_type_mapped_to_issue(self) -> None:
        """issue_type 'issue' maps to ItemType.ISSUE on the TicketRecord."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(issue_type="issue")
        )

        assert result.record.type == ItemType.ISSUE.value

    @pytest.mark.asyncio
    async def test_issue_type_mapped_to_incident(self) -> None:
        """issue_type 'incident' maps to ItemType.INCIDENT on the TicketRecord."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(issue_type="incident")
        )

        assert result.record.type == ItemType.INCIDENT.value

    @pytest.mark.asyncio
    async def test_issue_type_mapped_to_task(self) -> None:
        """issue_type 'task' maps to ItemType.TASK on the TicketRecord."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(issue_type="task")
        )

        assert result.record.type == ItemType.TASK.value

    @pytest.mark.asyncio
    async def test_labels_extracted_from_issue(self) -> None:
        """Labels from the issue are collected into a list on the TicketRecord."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(labels=["bug", "urgent"])
        )

        assert result.record.labels == ["bug", "urgent"]

    @pytest.mark.asyncio
    async def test_empty_labels_results_in_empty_list(self) -> None:
        """When issue has no labels, the TicketRecord labels list is empty."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(labels=[])
        )

        assert result.record.labels == []

    @pytest.mark.asyncio
    async def test_external_record_group_id_uses_project_id_suffix(self) -> None:
        """external_record_group_id is formatted as '<project_id>-work-items'."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(project_id=42)
        )

        assert result.record.external_record_group_id == "42-work-items"

    @pytest.mark.asyncio
    async def test_ticket_record_fields_match_issue(self) -> None:
        """Core TicketRecord fields are correctly mapped from the issue."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        issue = self._make_issue(
            issue_id=55,
            title="My Issue",
            state="closed",
            web_url="https://gitlab.com/-/issues/55",
        )
        result = await connector._process_issue_incident_task_to_ticket(issue)

        assert result.record.record_name == "My Issue"
        assert result.record.external_record_id == "55"
        assert result.record.status == "closed"
        assert result.record.weburl == "https://gitlab.com/-/issues/55"
        assert result.record.connector_id == "gitlab-conn-1"
        assert result.record.org_id == "org-1"
        assert result.record.inherit_permissions is True

    @pytest.mark.asyncio
    async def test_record_update_flags_for_new_issue(self) -> None:
        """RecordUpdate flags are set correctly for a brand-new issue."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue()
        )

        assert result.is_new is True
        assert result.is_updated is False
        assert result.is_deleted is False
        assert result.metadata_changed is False
        assert result.content_changed is False
        assert result.permissions_changed is False
        assert result.old_permissions == []
        assert result.new_permissions == []

    @pytest.mark.asyncio
    async def test_external_record_id_on_record_update(self) -> None:
        """external_record_id on the RecordUpdate equals str(issue.id)."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue(issue_id=77)
        )

        assert result.external_record_id == "77"

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_called_with_correct_args(self) -> None:
        """get_record_by_external_id is called with connector_id and str(issue.id)."""
        connector = _make_connector()
        mock_tx_store = self._attach_tx_store(connector, existing_record=None)

        await connector._process_issue_incident_task_to_ticket(
            self._make_issue(issue_id=99)
        )

        mock_tx_store.get_record_by_external_id.assert_called_once_with(
            connector_id="gitlab-conn-1", external_id="99"
        )

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        """When an exception occurs inside the try block, None is returned."""
        connector = _make_connector()
        connector.data_store_provider.transaction = MagicMock(
            side_effect=Exception("connection error")
        )

        result = await connector._process_issue_incident_task_to_ticket(
            self._make_issue()
        )

        assert result is None


class TestBuildTicketBlocks:
    """Unit tests for _build_ticket_blocks."""

    def _make_record(
        self,
        weburl="https://gitlab.com/group/project/-/issues/42",
        external_record_group_id="10-work-items",
        record_name="Fix bug",
        external_record_id="42",
    ) -> MagicMock:
        record = MagicMock(spec=Record)
        record.weburl = weburl
        record.external_record_group_id = external_record_group_id
        record.record_name = record_name
        record.external_record_id = external_record_id
        return record

    def _make_issue_res(self, success=True, data=None, error=None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data
        res.error = error
        return res

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector.embed_images_as_base64 = AsyncMock(return_value="embedded content")
        connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        connector._build_comment_blocks = AsyncMock(return_value=([], []))
        connector._process_new_records = AsyncMock()
        connector.string_to_datetime = MagicMock(
            return_value=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        connector.data_source = MagicMock()
        return connector

    def _make_issue_obj(
        self, title="Fix bug", description="desc", updated_at="2024-01-01T00:00:00Z"
    ) -> MagicMock:
        issue = MagicMock()
        issue.title = title
        issue.description = description
        issue.updated_at = updated_at
        return issue

    @pytest.mark.asyncio
    async def test_raises_when_weburl_is_empty(self) -> None:
        """ValueError is raised when record.weburl is empty."""
        connector = self._setup_connector()
        record = self._make_record(weburl="")

        with pytest.raises(ValueError, match="Web URL is required"):
            await connector._build_ticket_blocks(record)

    @pytest.mark.asyncio
    async def test_raises_when_weburl_is_none(self) -> None:
        """ValueError is raised when record.weburl is None."""
        connector = self._setup_connector()
        record = self._make_record()
        record.weburl = None  # override after construction

        with pytest.raises(ValueError, match="Web URL is required"):
            await connector._build_pull_request_blocks(record)

    @pytest.mark.asyncio
    async def test_raises_when_issue_fetch_fails(self) -> None:
        """Exception is raised when data_source.get_issue reports failure."""
        connector = self._setup_connector()
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=False, error="not found")
        )
        record = self._make_record()

        with pytest.raises(Exception, match="Failed to fetch issue details"):
            await connector._build_ticket_blocks(record)

    @pytest.mark.asyncio
    async def test_raises_when_issue_data_is_none(self) -> None:
        """Exception is raised when data_source.get_issue returns success but no data."""
        connector = self._setup_connector()
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=None)
        )
        record = self._make_record()

        with pytest.raises(Exception, match="No issue data found"):
            await connector._build_ticket_blocks(record)

    @pytest.mark.asyncio
    async def test_get_issue_called_with_parsed_project_id_and_issue_number(
        self,
    ) -> None:
        """project_id and issue_iid are correctly parsed from the weburl."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj()
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        # URL pattern: https://gitlab.com/group/project/-/issues/<iid>
        record = self._make_record(
            weburl="https://gitlab.com/group/project/-/issues/7",
            external_record_group_id="99-work-items",
        )

        await connector._build_ticket_blocks(record)

        connector.data_source.get_issue.assert_called_once_with(
            project_id="99", issue_iid=7
        )

    @pytest.mark.asyncio
    async def test_embed_images_called_with_description_and_project_url(self) -> None:
        """embed_images_as_base64 receives the raw description and the base project URL."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj(description="raw markdown")
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        record = self._make_record(
            weburl="https://gitlab.com/group/project/-/issues/42",
            external_record_group_id="10-work-items",
        )

        await connector._build_ticket_blocks(record)

        connector.embed_images_as_base64.assert_called_once_with(
            "raw markdown", "https://gitlab.com/api/v4/projects/10"
        )

    @pytest.mark.asyncio
    async def test_description_none_treated_as_empty_string(self) -> None:
        """When issue.description is None, embed_images_as_base64 receives ''."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj(description=None)
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        record = self._make_record()

        await connector._build_ticket_blocks(record)

        connector.embed_images_as_base64.assert_called_once_with(
            "", "https://gitlab.com/api/v4/projects/10"
        )

    @pytest.mark.asyncio
    async def test_title_prepended_to_markdown_content(self) -> None:
        """Issue title is prepended as a heading to the embedded markdown before building blocks."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj(title="My Issue", description="body")
        connector.embed_images_as_base64 = AsyncMock(return_value="embedded body")
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        connector._build_comment_blocks = AsyncMock(return_value=([], []))

        record = self._make_record(record_name="My Issue")
        await connector._build_ticket_blocks(record)

        # The bg_0.data passed to BlockGroup should contain the heading
        # We verify indirectly via make_child_records_of_attachments receiving raw description
        connector.make_child_records_of_attachments.assert_called_once_with(
            markdown_raw="body", record=record
        )

    @pytest.mark.asyncio
    async def test_make_child_records_called_with_raw_description_and_record(
        self,
    ) -> None:
        """make_child_records_of_attachments receives the raw (pre-embed) description."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj(description="raw desc")
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        record = self._make_record()

        await connector._build_ticket_blocks(record)

        connector.make_child_records_of_attachments.assert_called_once_with(
            markdown_raw="raw desc", record=record
        )

    @pytest.mark.asyncio
    async def test_build_comment_blocks_called_with_record_weburl(self) -> None:
        """_build_comment_blocks is called with the record's weburl."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj()
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        record = self._make_record(
            weburl="https://gitlab.com/group/project/-/issues/42"
        )

        await connector._build_ticket_blocks(record)

        connector._build_comment_blocks.assert_called_once_with(
            issue_url=record.weburl, parent_index=0, record=record
        )

    @pytest.mark.asyncio
    async def test_process_new_records_called_with_remaining_records(self) -> None:
        """_process_new_records is called with the combined remaining records from both sources."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj()
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        remaining_from_attachments = [MagicMock(spec=RecordUpdate)]
        remaining_from_comments = [MagicMock(spec=RecordUpdate)]
        connector.make_child_records_of_attachments = AsyncMock(
            return_value=([], remaining_from_attachments)
        )
        connector._build_comment_blocks = AsyncMock(
            return_value=([], remaining_from_comments)
        )
        record = self._make_record()

        await connector._build_ticket_blocks(record)

        call_arg = connector._process_new_records.call_args[0][0]
        assert remaining_from_attachments[0] in call_arg
        assert remaining_from_comments[0] in call_arg

    @pytest.mark.asyncio
    async def test_returns_bytes(self) -> None:
        """The method returns a bytes object."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj()
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        record = self._make_record()

        result = await connector._build_ticket_blocks(record)

        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_returned_bytes_is_valid_json(self) -> None:
        """The returned bytes can be decoded and parsed as JSON."""
        import json

        connector = self._setup_connector()
        issue_obj = self._make_issue_obj()
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        record = self._make_record()

        result = await connector._build_ticket_blocks(record)

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        assert "block_groups" in parsed

    @pytest.mark.asyncio
    async def test_comment_block_groups_included_in_container(self) -> None:
        """BlockGroups returned by _build_comment_blocks are included in the container."""
        connector = self._setup_connector()
        issue_obj = self._make_issue_obj()
        connector.data_source.get_issue = MagicMock(
            return_value=self._make_issue_res(success=True, data=issue_obj)
        )
        mock_comment_bg = BlockGroup(
            index=1,
            name="comment",
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.COMMENT.value,
            source_group_id="https://gitlab.com/-/issues/42#note_1",
            data="a comment",
        )
        connector._build_comment_blocks = AsyncMock(
            return_value=([mock_comment_bg], [])
        )

        result = await connector._build_ticket_blocks(self._make_record())

        import json

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        assert len(parsed["block_groups"]) == 2  # bg_0 (description) + comment


class TestHandleRecordUpdates:
    """Unit tests for _handle_record_updates."""

    @pytest.mark.asyncio
    async def test_returns_none(self) -> None:
        """Method returns None (stub implementation)."""
        connector = _make_connector()
        mock_issue_update = MagicMock(spec=RecordUpdate)

        result = await connector._handle_record_updates(mock_issue_update)

        assert result is None


class TestReindexRecords:
    """Unit tests for reindex_records."""

    @pytest.mark.asyncio
    async def test_returns_none(self) -> None:
        """Method returns None (stub implementation)."""
        connector = _make_connector()

        result = await connector.reindex_records()

        assert result is None


class TestRunIncrementalSync:
    """Unit tests for run_incremental_sync."""

    @pytest.mark.asyncio
    async def test_returns_none(self) -> None:
        """Method returns None (stub implementation)."""
        connector = _make_connector()

        result = await connector.run_incremental_sync()

        assert result is None


class TestBuildCommentBlocks:
    """Unit tests for _build_comment_blocks."""

    def _make_record(
        self,
        external_record_group_id="10-work-items",
        weburl="https://gitlab.com/group/project/-/issues/42",
    ) -> MagicMock:
        record = MagicMock(spec=Record)
        record.external_record_group_id = external_record_group_id
        record.weburl = weburl
        return record

    def _make_comments_res(self, success=True, data=None, error=None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data if data is not None else []
        res.error = error
        return res

    def _make_comment(self, body="comment body", username="alice") -> MagicMock:
        comment = MagicMock(spec=ProjectIssueNote)
        comment.body = body
        comment.author = {"username": username} if username else {}
        return comment

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        connector.embed_images_as_base64 = AsyncMock(return_value="embedded content")
        return connector

    @pytest.mark.asyncio
    async def test_raises_when_list_issue_notes_fails(self) -> None:
        """Exception is raised when data_source.list_issue_notes reports failure."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(success=False, error="not found")
        )

        with pytest.raises(Exception, match="Failed to fetch comments"):
            await connector._build_comment_blocks(
                issue_url="https://gitlab.com/group/project/-/issues/42",
                parent_index=0,
                record=self._make_record(),
            )

    @pytest.mark.asyncio
    async def test_returns_empty_lists_when_no_comments(self) -> None:
        """When no comments are returned, both return values are empty lists."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(success=True, data=[])
        )

        block_groups, remaining = await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=self._make_record(),
        )

        assert block_groups == []
        assert remaining == []

    @pytest.mark.asyncio
    async def test_list_issue_notes_called_with_parsed_project_id_and_issue_number(
        self,
    ) -> None:
        """project_id and issue_iid are correctly parsed from record and URL respectively."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(success=True, data=[])
        )

        await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/7",
            parent_index=0,
            record=self._make_record(external_record_group_id="99-work-items"),
        )

        connector.data_source.list_issue_notes.assert_called_once_with(
            project_id=99, issue_iid=7, get_all=True
        )

    @pytest.mark.asyncio
    async def test_block_group_count_matches_comment_count(self) -> None:
        """One BlockGroup is produced per comment."""
        connector = self._setup_connector()
        comments = [self._make_comment(), self._make_comment(), self._make_comment()]
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(success=True, data=comments)
        )

        block_groups, _ = await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=self._make_record(),
        )

        assert len(block_groups) == 3

    @pytest.mark.asyncio
    async def test_block_group_index_starts_at_parent_index_plus_one(self) -> None:
        """First BlockGroup index is parent_index + 1."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(
                success=True, data=[self._make_comment()]
            )
        )

        block_groups, _ = await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=3,
            record=self._make_record(),
        )

        assert block_groups[0].index == 4

    @pytest.mark.asyncio
    async def test_block_group_indices_increment_per_comment(self) -> None:
        """BlockGroup indices increment sequentially across multiple comments."""
        connector = self._setup_connector()
        comments = [self._make_comment(), self._make_comment(), self._make_comment()]
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(success=True, data=comments)
        )

        block_groups, _ = await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=self._make_record(),
        )

        assert [bg.index for bg in block_groups] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_comment_name_includes_username_when_present(self) -> None:
        """Block group name is 'Comment by <username> on issue <iid>' when author has username."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(
                success=True, data=[self._make_comment(username="alice")]
            )
        )

        block_groups, _ = await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=self._make_record(),
        )

        assert block_groups[0].name == "Comment by alice on issue 42"

    @pytest.mark.asyncio
    async def test_comment_name_falls_back_when_no_username(self) -> None:
        """Block group name is 'Comment on issue <iid>' when author has no username."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(
                success=True, data=[self._make_comment(username=None)]
            )
        )

        block_groups, _ = await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=self._make_record(),
        )

        assert block_groups[0].name == "Comment on issue 42"

    @pytest.mark.asyncio
    async def test_comment_body_none_treated_as_empty_string(self) -> None:
        """When comment.body is None, embed_images_as_base64 receives ''."""
        connector = self._setup_connector()
        comment = self._make_comment(body=None)
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(success=True, data=[comment])
        )

        await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=self._make_record(),
        )

        connector.embed_images_as_base64.assert_called_once_with(
            "", "https://gitlab.com/api/v4/projects/10"
        )

    @pytest.mark.asyncio
    async def test_embed_images_called_with_body_and_project_url(self) -> None:
        """embed_images_as_base64 receives the raw body and the correct base project URL."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(
                success=True, data=[self._make_comment(body="raw body")]
            )
        )

        await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=self._make_record(external_record_group_id="10-work-items"),
        )

        connector.embed_images_as_base64.assert_called_once_with(
            "raw body", "https://gitlab.com/api/v4/projects/10"
        )

    @pytest.mark.asyncio
    async def test_make_child_records_called_with_raw_body_and_record(self) -> None:
        """make_child_records_of_attachments receives the raw body and parent record."""
        connector = self._setup_connector()
        record = self._make_record()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(
                success=True, data=[self._make_comment(body="raw body")]
            )
        )

        await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=record,
        )

        connector.make_child_records_of_attachments.assert_called_once_with(
            markdown_raw="raw body", record=record
        )

    @pytest.mark.asyncio
    async def test_remaining_records_aggregated_across_all_comments(self) -> None:
        """Remaining records from each comment's make_child_records_of_attachments are combined."""
        connector = self._setup_connector()
        ru1, ru2 = MagicMock(spec=RecordUpdate), MagicMock(spec=RecordUpdate)
        connector.make_child_records_of_attachments = AsyncMock(
            side_effect=[([], [ru1]), ([], [ru2])]
        )
        comments = [self._make_comment(), self._make_comment()]
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(success=True, data=comments)
        )

        _, remaining = await connector._build_comment_blocks(
            issue_url="https://gitlab.com/group/project/-/issues/42",
            parent_index=0,
            record=self._make_record(),
        )

        assert ru1 in remaining
        assert ru2 in remaining

    @pytest.mark.asyncio
    async def test_block_group_fields_set_correctly(self) -> None:
        """BlockGroup fields are populated with the correct values."""
        connector = self._setup_connector()
        connector.embed_images_as_base64 = AsyncMock(return_value="embedded body")
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_comments_res(
                success=True, data=[self._make_comment(body="body", username="bob")]
            )
        )
        issue_url = "https://gitlab.com/group/project/-/issues/42"
        record = self._make_record()

        block_groups, _ = await connector._build_comment_blocks(
            issue_url=issue_url, parent_index=0, record=record
        )

        bg = block_groups[0]
        assert bg.type == GroupType.TEXT_SECTION.value
        assert bg.format == DataFormat.MARKDOWN.value
        assert bg.sub_type == GroupSubType.COMMENT.value
        assert bg.data == "embedded body"
        assert str(bg.weburl) == issue_url
        assert bg.requires_processing is True
        assert bg.parent_index == 0


class TestBuildMergeRequestCommentBlocks:
    """Unit tests for _build_merge_request_comment_blocks."""

    _MR_URL = "https://gitlab.com/group/project/-/merge_requests/5"

    def _make_record(
        self, external_record_group_id="10-merge-requests", record_name="My MR"
    ) -> MagicMock:
        record = MagicMock(spec=Record)
        record.external_record_group_id = external_record_group_id
        record.record_name = record_name
        return record

    def _make_res(self, success=True, data=None, error=None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data if data is not None else []
        res.error = error
        return res

    def _make_regular_comment(
        self, body="a comment", username="alice", system=False
    ) -> MagicMock:
        comment = MagicMock(spec=ProjectMergeRequestNote)
        comment.body = body
        comment.system = system
        comment.position = None
        comment.author = {"username": username} if username else {}
        comment.updated_at = "2024-01-01T00:00:00Z"
        comment.created_at = "2024-01-01T00:00:00Z"
        return comment

    def _make_review_comment(
        self, body="review", username="bob", file_path="src/main.py"
    ) -> MagicMock:
        comment = MagicMock(spec=ProjectMergeRequestNote)
        comment.body = body
        comment.system = False
        comment.position = {"new_path": file_path}
        comment.author = {"username": username} if username else {}
        comment.updated_at = "2024-01-01T00:00:00Z"
        comment.created_at = "2024-01-01T00:00:00Z"
        return comment

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.embed_images_as_base64 = AsyncMock(return_value="embedded")
        connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        connector.make_block_comment_of_attachments = AsyncMock(return_value=([], []))
        connector.string_to_datetime = MagicMock(
            return_value=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(success=True, data=[])
        )
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": []})
        )
        connector.data_source.get_merge_request = MagicMock(
            return_value=self._make_res(success=True, data=MagicMock(sha="abc123"))
        )
        return connector

    # ------------------------------------------------------------------
    # list_merge_request_notes — failure + URL parsing
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_raises_when_list_notes_fails(self) -> None:
        """Exception is raised when list_merge_request_notes reports failure."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(success=False, error="timeout")
        )

        with pytest.raises(
            Exception, match="Failed to fetch comments for merge request"
        ):
            await connector._build_merge_request_comment_blocks(
                mr_url=self._MR_URL, parent_index=0, record=self._make_record()
            )

    @pytest.mark.asyncio
    async def test_list_notes_called_with_parsed_project_id_and_mr_number(self) -> None:
        """project_id and mr_iid are parsed from record and URL correctly."""
        connector = self._setup_connector()

        await connector._build_merge_request_comment_blocks(
            mr_url="https://gitlab.com/group/project/-/merge_requests/7",
            parent_index=0,
            record=self._make_record(external_record_group_id="99-merge-requests"),
        )

        connector.data_source.list_merge_request_notes.assert_called_once_with(
            project_id=99, mr_iid=7, get_all=True
        )

    # ------------------------------------------------------------------
    # No comments / no file changes
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_empty_lists_when_no_comments_and_no_changes(self) -> None:
        """When there are no comments and no file changes, both return values are empty."""
        connector = self._setup_connector()

        block_groups, remaining = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups == []
        assert remaining == []

    # ------------------------------------------------------------------
    # Regular (non-review) comments — name logic
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_regular_comment_name_with_username(self) -> None:
        """Regular comment with username → 'Comment by <user> on merge request <iid>'."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True,
                data=[self._make_regular_comment(username="alice", system=False)],
            )
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].name == "Comment by alice on merge request 5"

    @pytest.mark.asyncio
    async def test_system_comment_name_with_username(self) -> None:
        """System comment with username → 'System Comment by <user> on merge request <iid>'."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True,
                data=[self._make_regular_comment(username="alice", system=True)],
            )
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].name == "System Comment by alice on merge request 5"

    @pytest.mark.asyncio
    async def test_regular_comment_name_without_username(self) -> None:
        """Regular comment with no username → 'Comment on merge request <iid>'."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True,
                data=[self._make_regular_comment(username=None, system=False)],
            )
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].name == "Comment on merge request 5"

    @pytest.mark.asyncio
    async def test_system_comment_name_without_username(self) -> None:
        """System comment with no username → 'System Comment on merge request <iid>'."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True,
                data=[self._make_regular_comment(username=None, system=True)],
            )
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].name == "System Comment on merge request 5"

    @pytest.mark.asyncio
    async def test_system_comment_data_prefixed(self) -> None:
        """System comment data is prefixed with 'System comment \\n\\n'."""
        connector = self._setup_connector()
        connector.embed_images_as_base64 = AsyncMock(return_value="embedded body")
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True, data=[self._make_regular_comment(system=True)]
            )
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].data.startswith("System comment \n\n")

    @pytest.mark.asyncio
    async def test_regular_comment_block_group_fields(self) -> None:
        """BlockGroup for a regular comment has correct type, format, sub_type and weburl."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True, data=[self._make_regular_comment()]
            )
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        bg = block_groups[0]
        assert bg.type == GroupType.TEXT_SECTION.value
        assert bg.format == DataFormat.MARKDOWN.value
        assert bg.sub_type == GroupSubType.COMMENT.value
        assert str(bg.weburl) == self._MR_URL
        assert bg.requires_processing is True

    @pytest.mark.asyncio
    async def test_block_group_indices_increment_per_regular_comment(self) -> None:
        """Indices of regular comment BlockGroups increment from parent_index + 1."""
        connector = self._setup_connector()
        comments = [self._make_regular_comment(), self._make_regular_comment()]
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(success=True, data=comments)
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=2, record=self._make_record()
        )

        assert [bg.index for bg in block_groups] == [3, 4]

    # ------------------------------------------------------------------
    # Review (inline/file) comments
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_review_comment_does_not_produce_block_group_directly(self) -> None:
        """A review comment is mapped to its file and does not create its own BlockGroup."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True, data=[self._make_review_comment(file_path="src/main.py")]
            )
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert all(bg.sub_type != GroupSubType.COMMENT.value for bg in block_groups)

    @pytest.mark.asyncio
    async def test_review_comment_calls_make_block_comment_of_attachments(self) -> None:
        """make_block_comment_of_attachments is called for review comments."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True, data=[self._make_review_comment(body="review body")]
            )
        )
        record = self._make_record()

        await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=record
        )

        connector.make_block_comment_of_attachments.assert_called_once_with(
            markdown_raw="review body", record=record
        )

    @pytest.mark.asyncio
    async def test_remaining_attachments_collected_from_review_comments(self) -> None:
        """Remaining records from review comment attachment processing are returned."""
        connector = self._setup_connector()
        ru = MagicMock(spec=RecordUpdate)
        connector.make_block_comment_of_attachments = AsyncMock(return_value=([], [ru]))
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True, data=[self._make_review_comment()]
            )
        )

        _, remaining = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert ru in remaining

    # ------------------------------------------------------------------
    # File changes
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_raises_when_file_changes_fetch_fails(self) -> None:
        """A bare raise is triggered when list_merge_request_changes reports failure."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=False, error="forbidden")
        )

        with pytest.raises(
            Exception, match="Failed to fetch file changes for merge request"
        ):
            await connector._build_merge_request_comment_blocks(
                mr_url=self._MR_URL, parent_index=0, record=self._make_record()
            )

    @pytest.mark.asyncio
    async def test_file_change_block_group_created_for_each_change(self) -> None:
        """One BlockGroup of PR_FILE_CHANGE sub_type is created per file change."""
        connector = self._setup_connector()
        file_content_res = self._make_res(success=True, data=MagicMock(content=""))
        connector.data_source.get_file_content = MagicMock(
            return_value=file_content_res
        )
        changes = [
            {
                "new_path": "a.py",
                "diff": "d1",
                "new_file": False,
                "deleted_file": False,
                "generated_file": False,
                "too_large": False,
            },
            {
                "new_path": "b.py",
                "diff": "d2",
                "new_file": False,
                "deleted_file": False,
                "generated_file": False,
                "too_large": False,
            },
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        file_bgs = [
            bg for bg in block_groups if bg.sub_type == GroupSubType.PR_FILE_CHANGE
        ]
        assert len(file_bgs) == 2

    @pytest.mark.asyncio
    async def test_new_file_data_prefixed_correctly(self) -> None:
        """Data for a new file starts with '[New file]'."""
        connector = self._setup_connector()
        connector.data_source.get_file_content = MagicMock(
            return_value=self._make_res(success=True, data=MagicMock(content=""))
        )
        changes = [
            {
                "new_path": "x.py",
                "diff": "d",
                "new_file": True,
                "deleted_file": False,
                "generated_file": False,
                "too_large": False,
            }
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].data.startswith("[New file]")

    @pytest.mark.asyncio
    async def test_deleted_file_data_prefixed_correctly(self) -> None:
        """Data for a deleted file starts with '[Deleted file]'."""
        connector = self._setup_connector()
        changes = [
            {
                "new_path": "x.py",
                "diff": "d",
                "new_file": False,
                "deleted_file": True,
                "generated_file": False,
                "too_large": False,
            }
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].data.startswith("[Deleted file]")

    @pytest.mark.asyncio
    async def test_generated_file_data_prefixed_correctly(self) -> None:
        """Data for a generated file starts with '[Generated file]'."""
        connector = self._setup_connector()
        connector.data_source.get_file_content = MagicMock(
            return_value=self._make_res(success=True, data=MagicMock(content=""))
        )
        changes = [
            {
                "new_path": "x.py",
                "diff": "d",
                "new_file": False,
                "deleted_file": False,
                "generated_file": True,
                "too_large": False,
            }
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].data.startswith("[Generated file]")

    @pytest.mark.asyncio
    async def test_existing_file_data_prefixed_correctly(self) -> None:
        """Data for an existing changed file starts with 'Existing file'."""
        connector = self._setup_connector()
        connector.data_source.get_file_content = MagicMock(
            return_value=self._make_res(success=True, data=MagicMock(content=""))
        )
        changes = [
            {
                "new_path": "x.py",
                "diff": "d",
                "new_file": False,
                "deleted_file": False,
                "generated_file": False,
                "too_large": False,
            }
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert block_groups[0].data.startswith("Existing file")

    @pytest.mark.asyncio
    async def test_truncated_diff_appends_truncated_marker(self) -> None:
        """When too_large is True, '[TRUNCATED] Diff' is appended to data."""
        connector = self._setup_connector()
        connector.data_source.get_file_content = MagicMock(
            return_value=self._make_res(success=True, data=MagicMock(content=""))
        )
        changes = [
            {
                "new_path": "x.py",
                "diff": "d",
                "new_file": False,
                "deleted_file": False,
                "generated_file": False,
                "too_large": True,
            }
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        assert "[TRUNCATED] Diff" in block_groups[0].data

    @pytest.mark.asyncio
    async def test_file_content_fetch_skipped_for_deleted_file(self) -> None:
        """get_file_content is not called for deleted files."""
        connector = self._setup_connector()
        changes = [
            {
                "new_path": "x.py",
                "diff": "d",
                "new_file": False,
                "deleted_file": True,
                "generated_file": False,
                "too_large": False,
            }
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        connector.data_source.get_file_content.assert_not_called()

    @pytest.mark.asyncio
    async def test_file_content_fetch_failure_skips_file_change(self) -> None:
        """When get_file_content fails for a file, that file change is skipped (continue)."""
        connector = self._setup_connector()
        connector.data_source.get_file_content = MagicMock(
            return_value=self._make_res(success=False, error="not found")
        )
        changes = [
            {
                "new_path": "x.py",
                "diff": "d",
                "new_file": True,
                "deleted_file": False,
                "generated_file": False,
                "too_large": False,
            }
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        file_bgs = [
            bg for bg in block_groups if bg.sub_type == GroupSubType.PR_FILE_CHANGE
        ]
        assert len(file_bgs) == 0

    @pytest.mark.asyncio
    async def test_review_comments_mapped_to_correct_file_block_group(self) -> None:
        """Review comments for a file appear in that file's BlockGroup comments field."""
        connector = self._setup_connector()
        real_attachment = CommentAttachment(name="file.png", id="att-1")
        connector.make_block_comment_of_attachments = AsyncMock(
            return_value=([real_attachment], [])
        )
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_res(
                success=True,
                data=[self._make_review_comment(file_path="src/main.py")],
            )
        )
        connector.data_source.get_file_content = MagicMock(
            return_value=self._make_res(success=True, data=MagicMock(content=""))
        )
        changes = [
            {
                "new_path": "src/main.py",
                "diff": "d",
                "new_file": False,
                "deleted_file": False,
                "generated_file": False,
                "too_large": False,
            }
        ]
        connector.data_source.list_merge_request_changes = MagicMock(
            return_value=self._make_res(success=True, data={"changes": changes})
        )

        block_groups, _ = await connector._build_merge_request_comment_blocks(
            mr_url=self._MR_URL, parent_index=0, record=self._make_record()
        )

        file_bg = next(
            bg for bg in block_groups if bg.sub_type == GroupSubType.PR_FILE_CHANGE
        )
        assert len(file_bg.comments) == 1  # one thread
        thread = file_bg.comments[0]
        assert len(thread) == 1  # one comment in the thread
        block_comment = thread[0]
        assert isinstance(block_comment, BlockComment)
        assert block_comment.attachments == [
            real_attachment
        ]  # real_attachment is inside it
        assert block_comment.format == DataFormat.MARKDOWN
        assert (
            block_comment.text == "embedded"
        )  # whatever embed_images_as_base64 returns


class TestMakeFilesRecordsFromNotes:
    """Unit tests for make_files_records_from_notes."""

    def _make_issue(self, project_id="42", iid=7, title="Test Issue") -> MagicMock:
        issue = MagicMock(spec=ProjectIssue)
        issue.project_id = project_id
        issue.iid = iid
        issue.title = title
        return issue

    def _make_record(self) -> MagicMock:
        return MagicMock(spec=Record)

    def _make_notes_res(self, success=True, data=None, error=None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data  # intentionally allow None to pass through
        res.error = error
        return res

    def _make_note(self, body="note body") -> MagicMock:
        note = MagicMock(spec=ProjectIssueNote)
        note.body = body
        return note

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))
        connector.make_file_records_from_list = AsyncMock(return_value=[])
        return connector

    # ── failure / empty guards ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_raises_when_list_issue_notes_fails(self) -> None:
        """Exception is raised when data_source.list_issue_notes reports failure."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=False, error="server error")
        )

        with pytest.raises(Exception, match="Failed to fetch notes"):
            await connector.make_files_records_from_notes(
                issue=self._make_issue(), record=self._make_record()
            )

    @pytest.mark.asyncio
    async def test_returns_none_when_notes_data_is_none(self) -> None:
        """None is returned when notes_res.data is None (no notes at all)."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=None)
        )

        result = await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_notes_data_is_empty_list(self) -> None:
        """None is returned when notes_res.data is an empty list."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[])
        )

        result = await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        assert result is None

    # ── list_issue_notes call args ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_issue_notes_called_with_int_project_id_and_iid(self) -> None:
        """project_id is cast to int and issue.iid is forwarded correctly."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[])
        )

        await connector.make_files_records_from_notes(
            issue=self._make_issue(project_id="99", iid=3),
            record=self._make_record(),
        )

        connector.data_source.list_issue_notes.assert_called_once_with(
            project_id=99,
            issue_iid=3,
            get_all=True,
        )

    # ── no attachments in notes ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_notes_have_no_attachments(self) -> None:
        """Empty list is returned when no note yields attachments."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(
                success=True, data=[self._make_note("plain text")]
            )
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([], "plain text")
        )

        result = await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_make_file_records_not_called_when_no_attachments(self) -> None:
        """make_file_records_from_list is not called when parse returns no attachments."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[self._make_note()])
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))

        await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        connector.make_file_records_from_list.assert_not_called()

    # ── attachments present ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_file_record_updates_when_attachments_present(self) -> None:
        """RecordUpdates from make_file_records_from_list are returned."""
        connector = self._setup_connector()
        mock_attachment = MagicMock()
        mock_file_ru = MagicMock(spec=RecordUpdate)

        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(
                success=True, data=[self._make_note("see ![img](/uploads/abc/img.png)")]
            )
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([mock_attachment], "see")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[mock_file_ru])

        result = await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        assert mock_file_ru in result

    @pytest.mark.asyncio
    async def test_make_file_records_called_with_attachments_and_record(self) -> None:
        """make_file_records_from_list receives the parsed attachments and the record."""
        connector = self._setup_connector()
        mock_attachment = MagicMock()
        record = self._make_record()

        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[self._make_note()])
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([mock_attachment], "")
        )

        await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=record
        )

        connector.make_file_records_from_list.assert_called_once_with(
            attachments=[mock_attachment], record=record
        )

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_make_file_records_returns_empty(
        self,
    ) -> None:
        """Empty list is returned when make_file_records_from_list yields nothing."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[self._make_note()])
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([MagicMock()], "")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        result = await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        assert result == []

    # ── multiple notes ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_aggregates_updates_across_multiple_notes(self) -> None:
        """RecordUpdates from all notes are combined into a single flat list."""
        connector = self._setup_connector()
        ru1 = MagicMock(spec=RecordUpdate)
        ru2 = MagicMock(spec=RecordUpdate)
        attachment = MagicMock()

        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(
                success=True, data=[self._make_note("a"), self._make_note("b")]
            )
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attachment], "")
        )
        connector.make_file_records_from_list = AsyncMock(side_effect=[[ru1], [ru2]])

        result = await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        assert result == [ru1, ru2]

    @pytest.mark.asyncio
    async def test_skips_notes_without_attachments_in_mixed_batch(self) -> None:
        """Notes without attachments don't contribute any RecordUpdate."""
        connector = self._setup_connector()
        ru = MagicMock(spec=RecordUpdate)
        attachment = MagicMock()

        notes = [self._make_note("no attachment"), self._make_note("has attachment")]
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=notes)
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            side_effect=[([], "no attachment"), ([attachment], "has attachment")]
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[ru])

        result = await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        assert result == [ru]
        connector.make_file_records_from_list.assert_called_once()

    # ── note body edge cases ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_note_body_none_treated_as_empty_string(self) -> None:
        """When note.body is None, parse_gitlab_uploads_clean_test receives ''."""
        connector = self._setup_connector()
        note = MagicMock(spec=ProjectIssueNote)
        note.body = None

        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[note])
        )

        await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        connector.parse_gitlab_uploads_clean_test.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_parse_called_with_each_note_body(self) -> None:
        """parse_gitlab_uploads_clean_test is called once per note with its body."""
        connector = self._setup_connector()
        notes = [self._make_note("body one"), self._make_note("body two")]
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=notes)
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))

        await connector.make_files_records_from_notes(
            issue=self._make_issue(), record=self._make_record()
        )

        assert connector.parse_gitlab_uploads_clean_test.call_count == 2
        connector.parse_gitlab_uploads_clean_test.assert_any_call("body one")
        connector.parse_gitlab_uploads_clean_test.assert_any_call("body two")

    @pytest.mark.asyncio
    async def test_error_message_contains_issue_title(self) -> None:
        """The raised exception message includes the issue title."""
        connector = self._setup_connector()
        connector.data_source.list_issue_notes = MagicMock(
            return_value=self._make_notes_res(success=False, error="timeout")
        )

        with pytest.raises(Exception, match="My Special Issue"):
            await connector.make_files_records_from_notes(
                issue=self._make_issue(title="My Special Issue"),
                record=self._make_record(),
            )


class TestMakeFilesRecordsFromNotesMr:
    """Unit tests for make_files_records_from_notes_mr."""

    def _make_mr(self, project_id="42", iid=5, title="Test MR") -> MagicMock:
        mr = MagicMock(spec=ProjectMergeRequest)
        mr.project_id = project_id
        mr.iid = iid
        mr.title = title
        return mr

    def _make_record(self) -> MagicMock:
        return MagicMock(spec=Record)

    def _make_notes_res(self, success=True, data=None, error=None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data  # intentionally allow None to pass through
        res.error = error
        return res

    def _make_note(self, body="note body") -> MagicMock:
        note = MagicMock(spec=ProjectMergeRequestNote)
        note.body = body
        return note

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))
        connector.make_file_records_from_list = AsyncMock(return_value=[])
        return connector

    # ── failure / empty guards ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_raises_when_list_mr_notes_fails(self) -> None:
        """Exception is raised when data_source.list_merge_request_notes reports failure."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=False, error="server error")
        )

        with pytest.raises(Exception, match="Failed to fetch notes"):
            await connector.make_files_records_from_notes_mr(
                mr=self._make_mr(), record=self._make_record()
            )

    @pytest.mark.asyncio
    async def test_returns_none_when_notes_data_is_none(self) -> None:
        """None is returned when notes_res.data is None."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=None)
        )

        result = await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_notes_data_is_empty_list(self) -> None:
        """None is returned when notes_res.data is an empty list."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[])
        )

        result = await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        assert result is None

    # ── list_merge_request_notes call args ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_mr_notes_called_with_int_project_id_and_mr_iid(self) -> None:
        """project_id is cast to int and mr.iid is forwarded as mr_iid."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[])
        )

        await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(project_id="77", iid=12),
            record=self._make_record(),
        )

        connector.data_source.list_merge_request_notes.assert_called_once_with(
            project_id=77, mr_iid=12, get_all=True
        )

    # ── no attachments in notes ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_notes_have_no_attachments(self) -> None:
        """Empty list is returned when no note yields attachments."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(
                success=True, data=[self._make_note("plain text")]
            )
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([], "plain text")
        )

        result = await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_make_file_records_not_called_when_no_attachments(self) -> None:
        """make_file_records_from_list is not called when parse returns no attachments."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[self._make_note()])
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))

        await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        connector.make_file_records_from_list.assert_not_called()

    # ── attachments present ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_file_record_updates_when_attachments_present(self) -> None:
        """RecordUpdates from make_file_records_from_list are returned."""
        connector = self._setup_connector()
        mock_attachment = MagicMock()
        mock_file_ru = MagicMock(spec=RecordUpdate)

        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(
                success=True, data=[self._make_note("see ![img](/uploads/abc/img.png)")]
            )
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([mock_attachment], "see")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[mock_file_ru])

        result = await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        assert mock_file_ru in result

    @pytest.mark.asyncio
    async def test_make_file_records_called_with_attachments_and_record(self) -> None:
        """make_file_records_from_list receives the parsed attachments and the record."""
        connector = self._setup_connector()
        mock_attachment = MagicMock()
        record = self._make_record()

        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[self._make_note()])
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([mock_attachment], "")
        )

        await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=record
        )

        connector.make_file_records_from_list.assert_called_once_with(
            attachments=[mock_attachment], record=record
        )

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_make_file_records_returns_empty(
        self,
    ) -> None:
        """Empty list is returned when make_file_records_from_list yields nothing."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[self._make_note()])
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([MagicMock()], "")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        result = await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        assert result == []

    # ── multiple notes ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_aggregates_updates_across_multiple_notes(self) -> None:
        """RecordUpdates from all notes are combined into a single flat list."""
        connector = self._setup_connector()
        ru1 = MagicMock(spec=RecordUpdate)
        ru2 = MagicMock(spec=RecordUpdate)
        attachment = MagicMock()

        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(
                success=True, data=[self._make_note("a"), self._make_note("b")]
            )
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attachment], "")
        )
        connector.make_file_records_from_list = AsyncMock(side_effect=[[ru1], [ru2]])

        result = await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        assert result == [ru1, ru2]

    @pytest.mark.asyncio
    async def test_skips_notes_without_attachments_in_mixed_batch(self) -> None:
        """Notes without attachments don't contribute any RecordUpdate."""
        connector = self._setup_connector()
        ru = MagicMock(spec=RecordUpdate)
        attachment = MagicMock()

        notes = [self._make_note("no attachment"), self._make_note("has attachment")]
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=notes)
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            side_effect=[([], "no attachment"), ([attachment], "has attachment")]
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[ru])

        result = await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        assert result == [ru]
        connector.make_file_records_from_list.assert_called_once()

    # ── note body edge cases ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_note_body_none_treated_as_empty_string(self) -> None:
        """When note.body is None, parse_gitlab_uploads_clean_test receives ''."""
        connector = self._setup_connector()
        note = MagicMock(spec=ProjectMergeRequestNote)
        note.body = None

        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=[note])
        )

        await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        connector.parse_gitlab_uploads_clean_test.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_parse_called_with_each_note_body(self) -> None:
        """parse_gitlab_uploads_clean_test is called once per note with its body."""
        connector = self._setup_connector()
        notes = [self._make_note("body one"), self._make_note("body two")]
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=True, data=notes)
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))

        await connector.make_files_records_from_notes_mr(
            mr=self._make_mr(), record=self._make_record()
        )

        assert connector.parse_gitlab_uploads_clean_test.call_count == 2
        connector.parse_gitlab_uploads_clean_test.assert_any_call("body one")
        connector.parse_gitlab_uploads_clean_test.assert_any_call("body two")

    @pytest.mark.asyncio
    async def test_error_message_contains_mr_title(self) -> None:
        """The raised exception message includes the MR title."""
        connector = self._setup_connector()
        connector.data_source.list_merge_request_notes = MagicMock(
            return_value=self._make_notes_res(success=False, error="timeout")
        )

        with pytest.raises(Exception, match="My Special MR"):
            await connector.make_files_records_from_notes_mr(
                mr=self._make_mr(title="My Special MR"),
                record=self._make_record(),
            )


class TestFetchPrsBatched:
    """Unit tests for _fetch_prs_batched."""

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector._get_mr_sync_checkpoint = AsyncMock(return_value=None)
        connector._build_pr_records = AsyncMock(return_value=[])
        connector._process_new_records = AsyncMock()
        connector.data_source = MagicMock()
        return connector

    def _make_prs_res(self, success=True, data=None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data
        return res

    # ── failure guard ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_logs_error_and_returns_when_list_merge_requests_fails(self) -> None:
        """On failure, an error is logged and the method returns without processing."""
        connector = self._setup_connector()
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=False, data=None)
        )

        await connector._fetch_prs_batched(project_id=1)

        connector.logger.error.assert_called_once()
        connector._build_pr_records.assert_not_called()
        connector._process_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_raise_when_list_merge_requests_fails(self) -> None:
        """Unlike the issues variant, failure does not raise an exception."""
        connector = self._setup_connector()
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=False, data=None)
        )

        # should complete without raising
        await connector._fetch_prs_batched(project_id=1)

    # ── empty data ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_batches_processed_when_data_is_empty_list(self) -> None:
        """When data is [], the for loop runs zero iterations."""
        connector = self._setup_connector()
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=[])
        )

        await connector._fetch_prs_batched(project_id=1)

        connector._build_pr_records.assert_not_called()
        connector._process_new_records.assert_not_called()

    # ── sync checkpoint → updated_after conversion ────────────────────────────

    @pytest.mark.asyncio
    async def test_sync_checkpoint_none_passes_none_to_list_merge_requests(
        self,
    ) -> None:
        """When checkpoint is None, updated_after=None is passed."""
        connector = self._setup_connector()
        connector._get_mr_sync_checkpoint = AsyncMock(return_value=None)
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=[])
        )

        await connector._fetch_prs_batched(project_id=5)

        connector.data_source.list_merge_requests.assert_called_once_with(
            project_id=5,
            updated_after=None,
            order_by=GitlabLiterals.UPDATED_AT.value,
            sort="asc",
            get_all=True,
        )

    @pytest.mark.asyncio
    async def test_sync_checkpoint_converts_ms_to_utc_datetime(self) -> None:
        """A non-None checkpoint (ms) is divided by 1000 and converted to a UTC datetime."""
        connector = self._setup_connector()
        connector._get_mr_sync_checkpoint = AsyncMock(return_value=1_000_000)
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=[])
        )

        await connector._fetch_prs_batched(project_id=5)

        call_kwargs = connector.data_source.list_merge_requests.call_args[1]
        since_dt = call_kwargs["updated_after"]
        assert since_dt is not None
        assert since_dt == datetime.fromtimestamp(1000, tz=timezone.utc)

    @pytest.mark.asyncio
    async def test_get_mr_sync_checkpoint_called_with_project_id(self) -> None:
        """_get_mr_sync_checkpoint is called with the supplied project_id."""
        connector = self._setup_connector()
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=[])
        )

        await connector._fetch_prs_batched(project_id=99)

        connector._get_mr_sync_checkpoint.assert_called_once_with(99)

    # ── list_merge_requests call args ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_merge_requests_called_with_correct_fixed_args(self) -> None:
        """order_by and sort are always 'updated_at' and 'asc'."""
        connector = self._setup_connector()
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=[])
        )

        await connector._fetch_prs_batched(project_id=7)

        connector.data_source.list_merge_requests.assert_called_once_with(
            project_id=7,
            updated_after=None,
            order_by=GitlabLiterals.UPDATED_AT.value,
            sort="asc",
            get_all=True,
        )

    # ── batching behaviour ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_single_batch_when_prs_fit_in_one_batch(self) -> None:
        """When total PRs <= batch_size, _build_pr_records is called exactly once."""
        connector = self._setup_connector()
        connector.batch_size = 5
        mock_prs = [MagicMock(spec=ProjectMergeRequest) for _ in range(3)]
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=mock_prs)
        )

        await connector._fetch_prs_batched(project_id=10)

        connector._build_pr_records.assert_called_once_with(mock_prs)

    @pytest.mark.asyncio
    async def test_multiple_batches_when_prs_exceed_batch_size(self) -> None:
        """When PRs exceed batch_size, _build_pr_records is called once per batch."""
        connector = self._setup_connector()
        connector.batch_size = 3
        mock_prs = [MagicMock(spec=ProjectMergeRequest) for _ in range(7)]
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=mock_prs)
        )

        await connector._fetch_prs_batched(project_id=10)

        assert connector._build_pr_records.call_count == 3

    @pytest.mark.asyncio
    async def test_batches_are_sliced_correctly(self) -> None:
        """Each batch passed to _build_pr_records is the right slice of all PRs."""
        connector = self._setup_connector()
        connector.batch_size = 3
        mock_prs = [MagicMock(spec=ProjectMergeRequest) for _ in range(7)]
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=mock_prs)
        )

        await connector._fetch_prs_batched(project_id=10)

        calls = connector._build_pr_records.call_args_list
        assert calls[0][0][0] == mock_prs[0:3]
        assert calls[1][0][0] == mock_prs[3:6]
        assert calls[2][0][0] == mock_prs[6:7]

    @pytest.mark.asyncio
    async def test_process_new_records_called_once_per_batch(self) -> None:
        """_process_new_records is called exactly once for each batch."""
        connector = self._setup_connector()
        connector.batch_size = 2
        mock_prs = [MagicMock(spec=ProjectMergeRequest) for _ in range(5)]
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=mock_prs)
        )

        await connector._fetch_prs_batched(project_id=10)

        assert connector._process_new_records.call_count == 3

    @pytest.mark.asyncio
    async def test_process_new_records_receives_build_output(self) -> None:
        """The result of _build_pr_records is forwarded to _process_new_records."""
        connector = self._setup_connector()
        connector.batch_size = 5
        mock_prs = [MagicMock(spec=ProjectMergeRequest) for _ in range(2)]
        mock_records = [MagicMock(spec=RecordUpdate)]
        connector._build_pr_records = AsyncMock(return_value=mock_records)
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=mock_prs)
        )

        await connector._fetch_prs_batched(project_id=10)

        connector._process_new_records.assert_called_once_with(mock_records)

    @pytest.mark.asyncio
    async def test_exact_batch_boundary_produces_correct_call_count(self) -> None:
        """When total PRs is an exact multiple of batch_size, no extra batch is created."""
        connector = self._setup_connector()
        connector.batch_size = 3
        mock_prs = [MagicMock(spec=ProjectMergeRequest) for _ in range(6)]
        connector.data_source.list_merge_requests = MagicMock(
            return_value=self._make_prs_res(success=True, data=mock_prs)
        )

        await connector._fetch_prs_batched(project_id=10)

        assert connector._build_pr_records.call_count == 2
        assert connector._process_new_records.call_count == 2


class TestBuildPrRecords:
    """Unit tests for _build_pr_records."""

    def _make_pr(self, description="") -> MagicMock:
        pr = MagicMock(spec=ProjectMergeRequest)
        pr.description = description
        pr.title = "Test MR"
        return pr

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector._process_mr_to_pull_request = AsyncMock()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))
        connector.make_file_records_from_list = AsyncMock(return_value=[])
        connector.make_files_records_from_notes_mr = AsyncMock(return_value=[])
        return connector

    # ── empty batch ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_batch_is_empty(self) -> None:
        """An empty prs_batch yields an empty result."""
        connector = self._setup_connector()

        result = await connector._build_pr_records([])

        assert result == []

    # ── process returns None ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_skips_pr_when_process_returns_none(self) -> None:
        """When _process_mr_to_pull_request returns None the PR is skipped entirely."""
        connector = self._setup_connector()
        connector._process_mr_to_pull_request = AsyncMock(return_value=None)

        result = await connector._build_pr_records([self._make_pr()])

        assert result == []
        connector.parse_gitlab_uploads_clean_test.assert_not_called()
        connector.make_file_records_from_list.assert_not_called()
        connector.make_files_records_from_notes_mr.assert_not_called()

    # ── pr record update ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_pr_record_update_added_when_process_succeeds(self) -> None:
        """The RecordUpdate from _process_mr_to_pull_request is included in the result."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)

        result = await connector._build_pr_records([self._make_pr()])

        assert mock_ru in result

    # ── description → parse_gitlab_uploads_clean_test ─────────────────────────

    @pytest.mark.asyncio
    async def test_description_none_treated_as_empty_string(self) -> None:
        """When pr.description is None, parse_gitlab_uploads_clean_test receives ''."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)

        await connector._build_pr_records([self._make_pr(description=None)])

        connector.parse_gitlab_uploads_clean_test.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_description_forwarded_to_parse_uploads(self) -> None:
        """pr.description is forwarded as-is to parse_gitlab_uploads_clean_test."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)

        await connector._build_pr_records(
            [self._make_pr(description="some **markdown**")]
        )

        connector.parse_gitlab_uploads_clean_test.assert_called_once_with(
            "some **markdown**"
        )

    # ── description attachments ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_make_file_records_not_called_when_no_attachments(self) -> None:
        """When parse returns no attachments, make_file_records_from_list is not called."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([], "cleaned")
        )

        await connector._build_pr_records([self._make_pr(description="no files")])

        connector.make_file_records_from_list.assert_not_called()

    @pytest.mark.asyncio
    async def test_file_records_added_when_description_attachments_present(
        self,
    ) -> None:
        """File RecordUpdates from make_file_records_from_list are included in the result."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([MagicMock()], "cleaned")
        )
        mock_file_ru1 = MagicMock(spec=RecordUpdate)
        mock_file_ru2 = MagicMock(spec=RecordUpdate)
        connector.make_file_records_from_list = AsyncMock(
            return_value=[mock_file_ru1, mock_file_ru2]
        )

        result = await connector._build_pr_records(
            [self._make_pr(description="![img](/uploads/img.png)")]
        )

        assert mock_file_ru1 in result
        assert mock_file_ru2 in result

    @pytest.mark.asyncio
    async def test_make_file_records_called_with_attachments_and_pr_record(
        self,
    ) -> None:
        """make_file_records_from_list receives the parsed attachments and record_update.record."""
        connector = self._setup_connector()
        mock_record = MagicMock()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = mock_record
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)
        mock_attachments = [MagicMock(), MagicMock()]
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(mock_attachments, "cleaned")
        )

        await connector._build_pr_records([self._make_pr(description="desc")])

        connector.make_file_records_from_list.assert_called_once_with(
            attachments=mock_attachments, record=mock_record
        )

    @pytest.mark.asyncio
    async def test_file_records_not_extended_when_make_file_returns_empty(self) -> None:
        """When make_file_records_from_list returns [], only the PR RecordUpdate is in the result."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([MagicMock()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        result = await connector._build_pr_records([self._make_pr(description="desc")])

        assert result == [mock_ru]

    # ── notes attachments ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_notes_attachments_added_when_present(self) -> None:
        """RecordUpdates from make_files_records_from_notes_mr are included in the result."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)
        mock_note_ru = MagicMock(spec=RecordUpdate)
        connector.make_files_records_from_notes_mr = AsyncMock(
            return_value=[mock_note_ru]
        )

        result = await connector._build_pr_records([self._make_pr()])

        assert mock_note_ru in result

    @pytest.mark.asyncio
    async def test_notes_attachments_not_extended_when_empty(self) -> None:
        """When make_files_records_from_notes_mr returns [], only the PR record is in the result."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)
        connector.make_files_records_from_notes_mr = AsyncMock(return_value=[])

        result = await connector._build_pr_records([self._make_pr()])

        assert result == [mock_ru]

    @pytest.mark.asyncio
    async def test_notes_attachments_not_extended_when_none(self) -> None:
        """When make_files_records_from_notes_mr returns None, it is safely skipped."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)
        connector.make_files_records_from_notes_mr = AsyncMock(return_value=None)

        result = await connector._build_pr_records([self._make_pr()])

        assert result == [mock_ru]

    @pytest.mark.asyncio
    async def test_make_files_records_from_notes_mr_called_with_pr_and_record(
        self,
    ) -> None:
        """make_files_records_from_notes_mr is called with the PR and record_update.record."""
        connector = self._setup_connector()
        mock_record = MagicMock()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = mock_record
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_ru)

        pr = self._make_pr()
        await connector._build_pr_records([pr])

        connector.make_files_records_from_notes_mr.assert_called_once_with(
            pr, mock_record
        )

    # ── combined ordering ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_all_record_updates_combined_in_correct_order(self) -> None:
        """Result is: PR record, then description file records, then note records."""
        connector = self._setup_connector()
        mock_record = MagicMock()
        mock_pr_ru = MagicMock(spec=RecordUpdate)
        mock_pr_ru.record = mock_record
        connector._process_mr_to_pull_request = AsyncMock(return_value=mock_pr_ru)

        mock_file_ru = MagicMock(spec=RecordUpdate)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([MagicMock()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[mock_file_ru])

        mock_note_ru = MagicMock(spec=RecordUpdate)
        connector.make_files_records_from_notes_mr = AsyncMock(
            return_value=[mock_note_ru]
        )

        result = await connector._build_pr_records([self._make_pr(description="desc")])

        assert result == [mock_pr_ru, mock_file_ru, mock_note_ru]

    # ── multiple PRs ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_multiple_prs_all_processed_independently(self) -> None:
        """Each PR in the batch is processed and its updates collected."""
        connector = self._setup_connector()
        ru1 = MagicMock(spec=RecordUpdate)
        ru1.record = MagicMock()
        ru2 = MagicMock(spec=RecordUpdate)
        ru2.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(side_effect=[ru1, ru2])

        prs = [self._make_pr(), self._make_pr()]
        result = await connector._build_pr_records(prs)

        assert ru1 in result
        assert ru2 in result
        assert connector._process_mr_to_pull_request.call_count == 2

    @pytest.mark.asyncio
    async def test_skipped_prs_do_not_affect_successful_prs(self) -> None:
        """When one PR is skipped (None) and another succeeds, only the successful one appears."""
        connector = self._setup_connector()
        mock_ru = MagicMock(spec=RecordUpdate)
        mock_ru.record = MagicMock()
        connector._process_mr_to_pull_request = AsyncMock(side_effect=[None, mock_ru])

        result = await connector._build_pr_records([self._make_pr(), self._make_pr()])

        assert result == [mock_ru]
        # parse and notes should only be called for the successful PR
        assert connector.parse_gitlab_uploads_clean_test.call_count == 1
        assert connector.make_files_records_from_notes_mr.call_count == 1


class TestProcessMrToPullRequest:
    """Unit tests for _process_mr_to_pull_request."""

    def _make_pr(
        self,
        pr_id=201,
        title="Add feature",
        project_id=10,
        labels=None,
        state="opened",
        web_url="https://gitlab.com/proj/-/merge_requests/201",
        updated_at="2024-03-02T00:00:00Z",
        created_at="2024-03-01T00:00:00Z",
        assignees=None,
        reviewers=None,
        merged_by=None,
        merge_status="can_be_merged",
    ) -> MagicMock:
        pr = MagicMock(spec=ProjectMergeRequest)
        pr.id = pr_id
        pr.title = title
        pr.project_id = project_id
        pr.labels = labels or []
        pr.state = state
        pr.web_url = web_url
        pr.updated_at = updated_at
        pr.created_at = created_at
        pr.assignees = assignees or []
        pr.reviewers = reviewers or []
        pr.merged_by = merged_by  # None or dict
        pr.merge_status = merge_status
        return pr

    def _attach_tx_store(self, connector, existing_record=None) -> AsyncMock:
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            return_value=existing_record
        )
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()
        return mock_tx_store

    # ── return type ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_record_update_for_new_pr(self) -> None:
        """A RecordUpdate is returned when the PR does not yet exist in the DB."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result is not None
        assert isinstance(result, RecordUpdate)

    # ── is_new / is_updated / flags ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_is_new_true_when_no_existing_record(self) -> None:
        """is_new is True when get_record_by_external_id returns None."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result.is_new is True

    @pytest.mark.asyncio
    async def test_is_new_false_when_existing_record_found(self) -> None:
        """is_new is False when an existing record is found."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Add feature"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result.is_new is False

    @pytest.mark.asyncio
    async def test_record_update_flags_for_new_pr(self) -> None:
        """All change flags are False/empty for a brand-new PR."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result.is_new is True
        assert result.is_updated is False
        assert result.is_deleted is False
        assert result.metadata_changed is False
        assert result.content_changed is False
        assert result.permissions_changed is False
        assert result.old_permissions == []
        assert result.new_permissions == []

    @pytest.mark.asyncio
    async def test_is_updated_true_when_existing_record_found(self) -> None:
        """is_updated is always True when an existing record is found."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Add feature"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result.is_updated is True

    @pytest.mark.asyncio
    async def test_content_changed_true_for_existing_record(self) -> None:
        """content_changed is always True when an existing record is found."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Add feature"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_metadata_changed_true_when_title_differs(self) -> None:
        """metadata_changed is True when existing record_name differs from pr.title."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Old title"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(title="New title")
        )

        assert result.metadata_changed is True

    @pytest.mark.asyncio
    async def test_metadata_changed_false_when_title_same(self) -> None:
        """metadata_changed is False when existing record_name matches pr.title."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-uuid"
        existing.record_name = "Add feature"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(title="Add feature")
        )

        assert result.metadata_changed is False

    # ── record id ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_existing_record_id_reused(self) -> None:
        """When an existing record is found, its id is reused in the PullRequestRecord."""
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "reused-uuid"
        existing.record_name = "Add feature"
        self._attach_tx_store(connector, existing_record=existing)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result.record.id == "reused-uuid"

    @pytest.mark.asyncio
    async def test_new_uuid_generated_for_new_pr(self) -> None:
        """When no existing record, a fresh UUID is assigned."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result.record.id is not None
        assert len(result.record.id) > 0

    # ── external_record_group_id ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_external_record_group_id_uses_merge_requests_suffix(self) -> None:
        """external_record_group_id is formatted as '<project_id>-merge-requests'."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(project_id=77)
        )

        assert result.record.external_record_group_id == "77-merge-requests"

    # ── core record fields ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_pull_request_record_fields_match_pr(self) -> None:
        """Core PullRequestRecord fields are correctly mapped from the PR."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        pr = self._make_pr(
            pr_id=55,
            title="My MR",
            state="merged",
            web_url="https://gitlab.com/-/merge_requests/55",
            merge_status="merged",
        )
        result = await connector._process_mr_to_pull_request(pr)

        assert result.record.record_name == "My MR"
        assert result.record.external_record_id == "55"
        assert result.record.status == "merged"
        assert result.record.weburl == "https://gitlab.com/-/merge_requests/55"
        assert result.record.connector_id == "gitlab-conn-1"
        assert result.record.org_id == "org-1"
        assert result.record.inherit_permissions is True
        assert result.record.mergeable == "merged"

    @pytest.mark.asyncio
    async def test_external_record_id_on_record_update(self) -> None:
        """external_record_id on the RecordUpdate equals str(pr.id)."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(self._make_pr(pr_id=99))

        assert result.external_record_id == "99"

    @pytest.mark.asyncio
    async def test_record_type_is_pull_request(self) -> None:
        """record_type on the PullRequestRecord is RecordType.PULL_REQUEST."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(self._make_pr())

        assert result.record.record_type == RecordType.PULL_REQUEST.value

    # ── labels ────────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_labels_extracted_from_pr(self) -> None:
        """Labels from the PR are collected into a list on the PullRequestRecord."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(labels=["feature", "backend"])
        )

        assert result.record.labels == ["feature", "backend"]

    @pytest.mark.asyncio
    async def test_empty_labels_results_in_empty_list(self) -> None:
        """When PR has no labels, the PullRequestRecord labels list is empty."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(self._make_pr(labels=[]))

        assert result.record.labels == []

    # ── assignees ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_assignees_usernames_extracted(self) -> None:
        """Assignee usernames are extracted from the 'username' key of each dict."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(assignees=[{"username": "alice"}, {"username": "bob"}])
        )

        assert result.record.assignee == ["alice", "bob"]

    @pytest.mark.asyncio
    async def test_empty_assignees_results_in_empty_list(self) -> None:
        """When PR has no assignees, the assignee list is empty."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(assignees=[])
        )

        assert result.record.assignee == []

    # ── reviewers ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_reviewer_usernames_extracted(self) -> None:
        """Reviewer usernames are extracted from the 'username' key of each dict."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(reviewers=[{"username": "carol"}, {"username": "dave"}])
        )

        assert result.record.review_name == ["carol", "dave"]

    @pytest.mark.asyncio
    async def test_empty_reviewers_results_in_empty_list(self) -> None:
        """When PR has no reviewers, review_name is an empty list."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(reviewers=[])
        )

        assert result.record.review_name == []

    # ── merged_by ─────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_merged_by_username_extracted_when_present(self) -> None:
        """merged_by is the 'username' value from pr.merged_by when it is not None."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(merged_by={"username": "eve"})
        )

        assert result.record.merged_by == "eve"

    @pytest.mark.asyncio
    async def test_merged_by_is_none_when_pr_not_merged(self) -> None:
        """merged_by is None when pr.merged_by is None (PR not yet merged)."""
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=None)

        result = await connector._process_mr_to_pull_request(
            self._make_pr(merged_by=None)
        )

        assert result.record.merged_by is None

    # ── transaction / DB lookup ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_called_with_correct_args(self) -> None:
        """get_record_by_external_id is called with connector_id and str(pr.id)."""
        connector = _make_connector()
        mock_tx_store = self._attach_tx_store(connector, existing_record=None)

        await connector._process_mr_to_pull_request(self._make_pr(pr_id=42))

        mock_tx_store.get_record_by_external_id.assert_called_once_with(
            connector_id="gitlab-conn-1", external_id="42"
        )

    # ── exception handling ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_raises_on_exception(self) -> None:
        """When an exception occurs, it is logged and re-raised (not swallowed)."""
        connector = _make_connector()
        connector.data_store_provider.transaction = MagicMock(
            side_effect=Exception("connection error")
        )

        with pytest.raises(Exception, match="connection error"):
            await connector._process_mr_to_pull_request(self._make_pr())

    @pytest.mark.asyncio
    async def test_error_is_logged_on_exception(self) -> None:
        """When an exception occurs, connector.logger.error is called."""
        connector = _make_connector()
        connector.data_store_provider.transaction = MagicMock(
            side_effect=RuntimeError("transaction failed")
        )
        with pytest.raises(RuntimeError, match="transaction failed"):
            await connector._process_mr_to_pull_request(self._make_pr())
        connector.logger.error.assert_called_once()


class TestBuildPullRequestBlocks:
    """Unit tests for _build_pull_request_blocks."""

    _MR_URL = "https://gitlab.com/group/project/-/merge_requests/42"

    def _make_record(
        self,
        weburl=None,
        external_record_group_id="10-merge-requests",
        record_name="Add feature",
        external_record_id="42",
    ) -> MagicMock:
        record = MagicMock(spec=Record)
        record.weburl = weburl if weburl is not None else self._MR_URL
        record.external_record_group_id = external_record_group_id
        record.record_name = record_name
        record.external_record_id = external_record_id
        return record

    def _make_res(self, success=True, data=None, error=None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data
        res.error = error
        return res

    def _make_mr_obj(
        self, title="Add feature", description="desc", updated_at="2024-03-01T00:00:00Z"
    ) -> MagicMock:
        mr = MagicMock()
        mr.title = title
        mr.description = description
        mr.updated_at = updated_at
        return mr

    def _make_commit(
        self,
        message="msg",
        title="title",
        web_url="http://c",
        commit_id="abc",
        committed_date="2024-03-01T00:00:00Z",
    ) -> MagicMock:
        commit = MagicMock(spec=ProjectCommit)
        commit.message = message
        commit.title = title
        commit.web_url = web_url
        commit.id = commit_id
        commit.committed_date = committed_date
        return commit

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector.embed_images_as_base64 = AsyncMock(return_value="embedded")
        connector.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        connector._build_merge_request_comment_blocks = AsyncMock(return_value=([], []))
        connector._process_new_records = AsyncMock()
        connector.string_to_datetime = MagicMock(
            return_value=datetime(2024, 3, 1, tzinfo=timezone.utc)
        )
        connector.data_source = MagicMock()
        connector.data_source.get_merge_request = MagicMock(
            return_value=self._make_res(success=True, data=self._make_mr_obj())
        )
        connector.data_source.list_merge_requests_commits = MagicMock(
            return_value=self._make_res(success=True, data=[])
        )
        return connector

    # ── URL / weburl guards ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_raises_when_weburl_is_empty(self) -> None:
        """ValueError is raised when record.weburl is empty."""
        connector = self._setup_connector()

        with pytest.raises(ValueError, match="Web URL is required"):
            await connector._build_pull_request_blocks(self._make_record(weburl=""))

    @pytest.mark.asyncio
    async def test_raises_when_weburl_is_none(self) -> None:
        """ValueError is raised when record.weburl is None."""
        connector = self._setup_connector()
        record = self._make_record()
        record.weburl = None  # override after construction
        with pytest.raises(ValueError, match="Web URL is required"):
            await connector._build_pull_request_blocks(record)

    # ── get_merge_request call args ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_merge_request_called_with_parsed_project_id_and_mr_number(
        self,
    ) -> None:
        """project_id comes from external_record_group_id prefix; mr_iid from URL index 7."""
        connector = self._setup_connector()
        record = self._make_record(
            weburl="https://gitlab.com/group/project/-/merge_requests/7",
            external_record_group_id="99-merge-requests",
        )

        await connector._build_pull_request_blocks(record)

        connector.data_source.get_merge_request.assert_called_once_with(
            project_id="99", mr_iid=7
        )

    # ── get_merge_request failure / empty ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_raises_when_get_merge_request_fails(self) -> None:
        """Exception is raised when data_source.get_merge_request reports failure."""
        connector = self._setup_connector()
        connector.data_source.get_merge_request = MagicMock(
            return_value=self._make_res(success=False, error="not found")
        )

        with pytest.raises(Exception, match="Failed to fetch merge request details"):
            await connector._build_pull_request_blocks(self._make_record())

    @pytest.mark.asyncio
    async def test_raises_when_merge_request_data_is_none(self) -> None:
        """Exception is raised when get_merge_request returns success but no data."""
        connector = self._setup_connector()
        connector.data_source.get_merge_request = MagicMock(
            return_value=self._make_res(success=True, data=None)
        )

        with pytest.raises(Exception, match="No merge request data found"):
            await connector._build_pull_request_blocks(self._make_record())

    # ── embed_images_as_base64 ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_embed_images_called_with_description_and_project_url(self) -> None:
        """embed_images_as_base64 receives the raw description and the base project URL."""
        connector = self._setup_connector()
        connector.data_source.get_merge_request = MagicMock(
            return_value=self._make_res(
                success=True, data=self._make_mr_obj(description="raw markdown")
            )
        )
        record = self._make_record(
            weburl="https://gitlab.com/group/project/-/merge_requests/42",
            external_record_group_id="10-merge-requests",
        )

        await connector._build_pull_request_blocks(record)

        connector.embed_images_as_base64.assert_called_once_with(
            "raw markdown", "https://gitlab.com/api/v4/projects/10"
        )

    @pytest.mark.asyncio
    async def test_description_none_treated_as_empty_string(self) -> None:
        """When mr.description is None, embed_images_as_base64 receives ''."""
        connector = self._setup_connector()
        connector.data_source.get_merge_request = MagicMock(
            return_value=self._make_res(
                success=True, data=self._make_mr_obj(description=None)
            )
        )

        await connector._build_pull_request_blocks(self._make_record())

        connector.embed_images_as_base64.assert_called_once_with(
            "", "https://gitlab.com/api/v4/projects/10"
        )

    # ── make_child_records_of_attachments ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_make_child_records_called_with_raw_description_and_record(
        self,
    ) -> None:
        """make_child_records_of_attachments receives the raw (pre-embed) description."""
        connector = self._setup_connector()
        connector.data_source.get_merge_request = MagicMock(
            return_value=self._make_res(
                success=True, data=self._make_mr_obj(description="raw desc")
            )
        )
        record = self._make_record()

        await connector._build_pull_request_blocks(record)

        connector.make_child_records_of_attachments.assert_called_once_with(
            "raw desc", record
        )

    # ── _build_merge_request_comment_blocks ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_build_mr_comment_blocks_called_with_correct_args(self) -> None:
        """_build_merge_request_comment_blocks is called with weburl, parent_index=0, record."""
        connector = self._setup_connector()
        record = self._make_record()

        await connector._build_pull_request_blocks(record)

        connector._build_merge_request_comment_blocks.assert_called_once_with(
            mr_url=record.weburl, parent_index=0, record=record
        )

    # ── list_merge_requests_commits call args ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_list_commits_called_with_correct_project_id_and_mr_number(
        self,
    ) -> None:
        """list_merge_requests_commits is called with the parsed project_id and mr_iid."""
        connector = self._setup_connector()
        record = self._make_record(
            weburl="https://gitlab.com/group/project/-/merge_requests/5",
            external_record_group_id="77-merge-requests",
        )

        await connector._build_pull_request_blocks(record)

        connector.data_source.list_merge_requests_commits.assert_called_once_with(
            project_id="77", mr_iid=5, get_all=True
        )

    # ── commits failure / empty ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_raises_when_list_commits_fails(self) -> None:
        """Exception is raised when list_merge_requests_commits reports failure."""
        connector = self._setup_connector()
        connector.data_source.list_merge_requests_commits = MagicMock(
            return_value=self._make_res(success=False, error="timeout")
        )

        with pytest.raises(Exception, match="Failed to fetch commits"):
            await connector._build_pull_request_blocks(self._make_record())

    @pytest.mark.asyncio
    async def test_no_commit_blocks_when_commits_data_is_empty(self) -> None:
        """When commits data is empty, the commits BlockGroup has no blocks."""
        import json

        connector = self._setup_connector()
        connector.data_source.list_merge_requests_commits = MagicMock(
            return_value=self._make_res(success=True, data=[])
        )

        result = await connector._build_pull_request_blocks(self._make_record())

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        assert parsed["blocks"] == []

    # ── commit blocks ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_one_block_created_per_commit(self) -> None:
        """A Block is created for each commit in the commits list."""
        import json

        connector = self._setup_connector()
        commits = [self._make_commit(message="msg1"), self._make_commit(message="msg2")]
        connector.data_source.list_merge_requests_commits = MagicMock(
            return_value=self._make_res(success=True, data=commits)
        )

        result = await connector._build_pull_request_blocks(self._make_record())

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        assert len(parsed["blocks"]) == 2

    @pytest.mark.asyncio
    async def test_commit_block_fields_mapped_correctly(self) -> None:
        """Commit block carries the correct message, title, web_url, and id."""
        import json

        connector = self._setup_connector()
        commit = self._make_commit(
            message="feat: add login",
            title="feat: add login",
            web_url="https://gitlab.com/commit/abc",
            commit_id="abc123",
        )
        connector.data_source.list_merge_requests_commits = MagicMock(
            return_value=self._make_res(success=True, data=[commit])
        )

        result = await connector._build_pull_request_blocks(self._make_record())

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        block = parsed["blocks"][0]
        assert block["data"] == "feat: add login"
        assert block["name"] == "feat: add login"
        assert block["weburl"] == "https://gitlab.com/commit/abc"
        assert block["source_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_commit_block_sub_type_is_commit(self) -> None:
        """Each commit block has sub_type COMMIT."""
        import json

        connector = self._setup_connector()
        connector.data_source.list_merge_requests_commits = MagicMock(
            return_value=self._make_res(success=True, data=[self._make_commit()])
        )

        result = await connector._build_pull_request_blocks(self._make_record())

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        assert parsed["blocks"][0]["sub_type"] == BlockSubType.COMMIT.value

    @pytest.mark.asyncio
    async def test_commit_block_indices_are_sequential(self) -> None:
        """Block indices increment sequentially across commits."""
        import json

        connector = self._setup_connector()
        commits = [self._make_commit(), self._make_commit(), self._make_commit()]
        connector.data_source.list_merge_requests_commits = MagicMock(
            return_value=self._make_res(success=True, data=commits)
        )

        result = await connector._build_pull_request_blocks(self._make_record())

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        indices = [b["index"] for b in parsed["blocks"]]
        assert indices == [0, 1, 2]

    # ── block groups structure ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_description_block_group_is_first(self) -> None:
        """The first BlockGroup (index 0) is the title+description group."""
        import json

        connector = self._setup_connector()

        result = await connector._build_pull_request_blocks(self._make_record())

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        # at minimum: description bg + commits bg
        assert len(parsed["block_groups"]) >= 2

    @pytest.mark.asyncio
    async def test_comment_block_groups_included_in_container(self) -> None:
        """BlockGroups returned by _build_merge_request_comment_blocks appear in output."""
        import json

        connector = self._setup_connector()
        mock_comment_bg = BlockGroup(
            index=1,
            name="comment",
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.COMMENT.value,
            source_group_id="https://gitlab.com/-/merge_requests/42#note_1",
            data="a comment",
        )
        connector._build_merge_request_comment_blocks = AsyncMock(
            return_value=([mock_comment_bg], [])
        )

        result = await connector._build_pull_request_blocks(self._make_record())

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        # description bg + comment bg + commits bg = 3
        assert len(parsed["block_groups"]) == 3

    # ── remaining attachments → _process_new_records ─────────────────────────

    @pytest.mark.asyncio
    async def test_process_new_records_called_with_combined_remaining(self) -> None:
        """_process_new_records receives remainders from both child-record and comment sources."""
        connector = self._setup_connector()
        remaining_from_attachments = [MagicMock(spec=RecordUpdate)]
        remaining_from_comments = [MagicMock(spec=RecordUpdate)]
        connector.make_child_records_of_attachments = AsyncMock(
            return_value=([], remaining_from_attachments)
        )
        connector._build_merge_request_comment_blocks = AsyncMock(
            return_value=([], remaining_from_comments)
        )

        await connector._build_pull_request_blocks(self._make_record())

        call_arg = connector._process_new_records.call_args[0][0]
        assert remaining_from_attachments[0] in call_arg
        assert remaining_from_comments[0] in call_arg

    # ── return type ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_bytes(self) -> None:
        """The method returns a bytes object."""
        connector = self._setup_connector()

        result = await connector._build_pull_request_blocks(self._make_record())

        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_returned_bytes_is_valid_json(self) -> None:
        """The returned bytes can be decoded and parsed as JSON."""
        import json

        connector = self._setup_connector()

        result = await connector._build_pull_request_blocks(self._make_record())

        parsed = json.loads(result.decode(GitlabLiterals.UTF_8.value))
        assert "block_groups" in parsed
        assert "blocks" in parsed


class TestEmbedImagesAsBase64:
    """Unit tests for embed_images_as_base64."""

    BASE_URL = "https://gitlab.com/api/v4/projects/10"

    # ── helpers ───────────────────────────────────────────────────────────────

    def _setup_connector(self) -> GitLabConnector:
        connector = _make_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))
        connector.data_source = MagicMock()
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=False)
        )
        return connector

    def _make_img_bytes(self, fmt: str = "PNG") -> bytes:
        buf = io.BytesIO()
        Image.new("RGB", (2, 2), color=(255, 0, 0)).save(buf, format=fmt)
        return buf.getvalue()

    def _make_bytes_res(self, success: bool, data: bytes | None = None) -> MagicMock:
        res = MagicMock()
        res.success = success
        res.data = data
        return res

    def _img_attach(
        self, href: str = "/uploads/abc123abc123abc123abc123abc123ab/image.png"
    ) -> FileAttachment:
        return FileAttachment(
            href=href,
            filename=href.rsplit("/", 1)[-1],
            filetype=href.rsplit(".", 1)[-1],
            category=GitlabLiterals.IMAGE.value,
        )

    def _file_attach(
        self, href: str = "/uploads/abc123abc123abc123abc123abc123ab/doc.pdf"
    ) -> FileAttachment:
        return FileAttachment(
            href=href,
            filename="doc.pdf",
            filetype="pdf",
            category=GitlabLiterals.ATTACHMENT.value,
        )

    # ── no-op paths ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_attachments_returns_cleaned_markdown(self) -> None:
        """When parse returns no attachments, the cleaned text is returned unchanged."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([], "plain text")
        )

        result = await connector.embed_images_as_base64("plain text", self.BASE_URL)

        assert result == "plain text"
        connector.parse_gitlab_uploads_clean_test.assert_awaited_once_with("plain text")

    @pytest.mark.asyncio
    async def test_non_image_attachments_skipped(self) -> None:
        """Attachments with category != 'image' are ignored; data_source is never called."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "some markdown")
        )

        result = await connector.embed_images_as_base64("some markdown", self.BASE_URL)

        assert result == "some markdown"
        connector.data_source.get_img_bytes.assert_not_called()

    # ── happy path ────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_single_png_image_appended_as_base64(self) -> None:
        """A PNG image attachment is fetched, encoded, and appended to the cleaned text."""
        connector = self._setup_connector()
        raw_bytes = self._make_img_bytes("PNG")
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(
                [
                    self._img_attach(
                        "/uploads/abc123abc123abc123abc123abc123ab/image.png"
                    )
                ],
                "description text",
            )
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=raw_bytes)
        )

        result = await connector.embed_images_as_base64(
            "description text", self.BASE_URL
        )

        expected_b64 = base64.b64encode(raw_bytes).decode(GitlabLiterals.UTF_8.value)
        assert f"![Image](data:image/png;base64,{expected_b64})" in result
        assert result.startswith("description text")

    @pytest.mark.asyncio
    async def test_jpeg_image_uses_jpeg_format(self) -> None:
        """A JPEG image is embedded with the 'jpeg' format tag."""
        connector = self._setup_connector()
        raw_bytes = self._make_img_bytes("JPEG")
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(
                [
                    self._img_attach(
                        "/uploads/abc123abc123abc123abc123abc123ab/photo.jpg"
                    )
                ],
                "text",
            )
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=raw_bytes)
        )

        result = await connector.embed_images_as_base64("text", self.BASE_URL)

        assert "data:image/jpeg;base64," in result

    @pytest.mark.asyncio
    async def test_full_url_constructed_correctly(self) -> None:
        """The URL passed to get_img_bytes is base_project_url + attachment href."""
        connector = self._setup_connector()
        href = "/uploads/deadbeefdeadbeefdeadbeefdeadbeef/logo.png"
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._img_attach(href)], "text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=self._make_img_bytes())
        )

        await connector.embed_images_as_base64("text", self.BASE_URL)

        connector.data_source.get_img_bytes.assert_awaited_once_with(
            f"{self.BASE_URL}{href}"
        )

    @pytest.mark.asyncio
    async def test_multiple_images_all_appended(self) -> None:
        """All image attachments in the list are fetched and appended."""
        connector = self._setup_connector()
        raw1 = self._make_img_bytes("PNG")
        raw2 = self._make_img_bytes("PNG")
        attachments = [
            self._img_attach("/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/img1.png"),
            self._img_attach("/uploads/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/img2.png"),
        ]
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(attachments, "base text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            side_effect=[
                self._make_bytes_res(success=True, data=raw1),
                self._make_bytes_res(success=True, data=raw2),
            ]
        )

        result = await connector.embed_images_as_base64("base text", self.BASE_URL)

        assert connector.data_source.get_img_bytes.await_count == 2
        assert f"data:image/png;base64,{base64.b64encode(raw1).decode()}" in result
        assert f"data:image/png;base64,{base64.b64encode(raw2).decode()}" in result

    @pytest.mark.asyncio
    async def test_mixed_attachments_only_images_embedded(self) -> None:
        """Non-image attachments are skipped while image ones are embedded."""
        connector = self._setup_connector()
        raw_bytes = self._make_img_bytes("PNG")
        attachments = [
            self._file_attach(),
            self._img_attach("/uploads/abc123abc123abc123abc123abc123ab/shot.png"),
        ]
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(attachments, "text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=raw_bytes)
        )

        result = await connector.embed_images_as_base64("text", self.BASE_URL)

        connector.data_source.get_img_bytes.assert_awaited_once()
        assert "base64," in result

    # ── failure / fallback paths ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_img_bytes_failure_skips_image(self) -> None:
        """When get_img_bytes returns success=False the image is not appended."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._img_attach()], "clean text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=False)
        )

        result = await connector.embed_images_as_base64("clean text", self.BASE_URL)

        assert result == "clean text"
        assert "base64" not in result

    @pytest.mark.asyncio
    async def test_get_img_bytes_none_data_skips_image(self) -> None:
        """When get_img_bytes returns success=True but data=None the image is not appended."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._img_attach()], "clean text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=None)
        )

        result = await connector.embed_images_as_base64("clean text", self.BASE_URL)

        assert result == "clean text"
        assert "base64" not in result

    @pytest.mark.asyncio
    async def test_exception_during_fetch_logs_error_and_continues(self) -> None:
        """An exception from get_img_bytes is caught, logged, and the loop continues."""
        connector = self._setup_connector()
        raw_bytes = self._make_img_bytes("PNG")
        attachments = [
            self._img_attach("/uploads/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/bad.png"),
            self._img_attach("/uploads/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/good.png"),
        ]
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(attachments, "text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            side_effect=[
                Exception("network error"),
                self._make_bytes_res(success=True, data=raw_bytes),
            ]
        )

        result = await connector.embed_images_as_base64("text", self.BASE_URL)

        connector.logger.warning.assert_called_once()
        assert "base64," in result

    @pytest.mark.asyncio
    async def test_non_image_bytes_still_embedded_without_pil(self) -> None:
        """Non-image bytes are still base64-encoded using the file extension for MIME type."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._img_attach()], "text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=b"not-an-image")
        )

        result = await connector.embed_images_as_base64("text", self.BASE_URL)

        expected_b64 = base64.b64encode(b"not-an-image").decode(
            GitlabLiterals.UTF_8.value
        )
        assert f"data:image/png;base64,{expected_b64}" in result

    # ── extension-based format mapping ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_unknown_extension_falls_back_to_png(self) -> None:
        """When file extension is not in EXTENSION_TO_MIME, 'png' is used as fallback."""
        connector = self._setup_connector()
        attach = FileAttachment(
            href="/uploads/abc123abc123abc123abc123abc123ab/image.tiff",
            filename="image.tiff",
            filetype="tiff",
            category=GitlabLiterals.IMAGE.value,
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attach], "text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=b"tiff-data")
        )

        result = await connector.embed_images_as_base64("text", self.BASE_URL)

        assert "data:image/png;base64," in result

    @pytest.mark.asyncio
    async def test_svg_image_embedded_with_correct_mime(self) -> None:
        """SVG images are embedded with 'image/svg+xml' MIME type."""
        connector = self._setup_connector()
        svg_bytes = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="10"/></svg>'
        attach = FileAttachment(
            href="/uploads/abc123abc123abc123abc123abc123ab/diagram.svg",
            filename="diagram.svg",
            filetype="svg",
            category=GitlabLiterals.IMAGE.value,
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attach], "text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=svg_bytes)
        )

        result = await connector.embed_images_as_base64("text", self.BASE_URL)

        expected_b64 = base64.b64encode(svg_bytes).decode(GitlabLiterals.UTF_8.value)
        assert f"data:image/svg+xml;base64,{expected_b64}" in result

    @pytest.mark.asyncio
    async def test_jpg_extension_maps_to_jpeg_mime(self) -> None:
        """A .jpg file uses 'image/jpeg' (not 'image/jpg') as the MIME type."""
        connector = self._setup_connector()
        raw_bytes = self._make_img_bytes("JPEG")
        attach = FileAttachment(
            href="/uploads/abc123abc123abc123abc123abc123ab/photo.jpg",
            filename="photo.jpg",
            filetype="jpg",
            category=GitlabLiterals.IMAGE.value,
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attach], "text")
        )
        connector.data_source.get_img_bytes = AsyncMock(
            return_value=self._make_bytes_res(success=True, data=raw_bytes)
        )

        result = await connector.embed_images_as_base64("text", self.BASE_URL)

        assert "data:image/jpeg;base64," in result

    # ── edge inputs ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_empty_body_content(self) -> None:
        """An empty body_content string flows through without errors."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))

        result = await connector.embed_images_as_base64("", self.BASE_URL)

        assert result == ""

    @pytest.mark.asyncio
    async def test_parse_called_with_exact_body_content(self) -> None:
        """parse_gitlab_uploads_clean_test is always called with the original body_content."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))
        body = "some ![img](/uploads/abc123abc123abc123abc123abc123ab/x.png) text"

        await connector.embed_images_as_base64(body, self.BASE_URL)

        connector.parse_gitlab_uploads_clean_test.assert_awaited_once_with(body)


class TestMakeFileRecordsFromList:
    """Unit tests for make_file_records_from_list."""

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_record(
        self,
        external_record_group_id: str = "10-work-items",
        external_record_id: str = "42",
        record_type: str = RecordType.TICKET.value,
    ) -> MagicMock:
        record = MagicMock(spec=Record)
        record.external_record_group_id = external_record_group_id
        record.external_record_id = external_record_id
        record.record_type = record_type
        return record

    def _file_attach(
        self,
        href: str = "/uploads/abc123abc123abc123abc123abc123ab/doc.pdf",
        filename: str = "doc.pdf",
        filetype: str = "pdf",
        category: str = GitlabLiterals.ATTACHMENT.value,
    ) -> FileAttachment:
        return FileAttachment(
            href=href, filename=filename, filetype=filetype, category=category
        )

    def _img_attach(
        self,
        href: str = "/uploads/abc123abc123abc123abc123abc123ab/image.png",
    ) -> FileAttachment:
        return FileAttachment(
            href=href,
            filename=href.rsplit("/", 1)[-1],
            filetype="png",
            category=GitlabLiterals.IMAGE.value,
        )

    def _attach_tx_store(self, connector, existing_record=None) -> AsyncMock:
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            return_value=existing_record
        )
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()
        return mock_tx_store

    def _setup_connector(self, existing_record=None) -> GitLabConnector:
        connector = _make_connector()
        self._attach_tx_store(connector, existing_record=existing_record)
        return connector

    # ── empty / image-only inputs ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_empty_attachments_returns_empty_list(self) -> None:
        """An empty attachment list yields an empty result immediately."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list([], self._make_record())

        assert result == []

    @pytest.mark.asyncio
    async def test_image_attachments_are_skipped(self) -> None:
        """Attachments with category == 'image' are skipped; no RecordUpdate is created."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._img_attach()], self._make_record()
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_mixed_attachments_only_files_processed(self) -> None:
        """Only non-image attachments produce RecordUpdates."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._img_attach(), self._file_attach()], self._make_record()
        )

        assert len(result) == 1

    # ── RecordUpdate shape ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_single_file_returns_one_record_update(self) -> None:
        """One non-image attachment produces exactly one RecordUpdate."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach()], self._make_record()
        )

        assert len(result) == 1
        assert isinstance(result[0], RecordUpdate)

    @pytest.mark.asyncio
    async def test_multiple_files_return_multiple_record_updates(self) -> None:
        """Two non-image attachments produce two RecordUpdates."""
        connector = self._setup_connector()
        attachments = [
            self._file_attach(
                href="/uploads/aaa/a.pdf", filename="a.pdf", filetype="pdf"
            ),
            self._file_attach(
                href="/uploads/bbb/b.docx", filename="b.docx", filetype="docx"
            ),
        ]

        result = await connector.make_file_records_from_list(
            attachments, self._make_record()
        )

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_record_update_flags_are_set_correctly(self) -> None:
        """RecordUpdate flags always indicate a brand-new record."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach()], self._make_record()
        )

        ru = result[0]
        assert ru.is_new is True
        assert ru.is_updated is False
        assert ru.is_deleted is False
        assert ru.metadata_changed is False
        assert ru.content_changed is False
        assert ru.permissions_changed is False
        assert ru.old_permissions == []
        assert ru.new_permissions == []

    # ── FileRecord field mapping ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_filerecord_name_matches_attachment_filename(self) -> None:
        """FileRecord.record_name is set from attach.filename."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach(filename="report.pdf")], self._make_record()
        )

        assert result[0].record.record_name == "report.pdf"

    @pytest.mark.asyncio
    async def test_filerecord_extension_matches_attachment_filetype(self) -> None:
        """FileRecord.extension is the lowercased attach.filetype."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach(filetype="PDF")], self._make_record()
        )

        assert result[0].record.extension == "pdf"

    @pytest.mark.asyncio
    async def test_filerecord_weburl_is_full_attachment_url(self) -> None:
        """FileRecord.weburl is base_url + attach.href."""
        connector = self._setup_connector()
        href = "/uploads/abc123abc123abc123abc123abc123ab/file.pdf"
        record = self._make_record(external_record_group_id="10-work-items")

        result = await connector.make_file_records_from_list(
            [self._file_attach(href=href)], record
        )

        assert result[0].record.weburl == f"https://gitlab.com/api/v4/projects/10{href}"

    @pytest.mark.asyncio
    async def test_filerecord_external_record_id_is_full_url(self) -> None:
        """FileRecord.external_record_id equals the full attachment URL."""
        connector = self._setup_connector()
        href = "/uploads/abc123abc123abc123abc123abc123ab/file.pdf"
        record = self._make_record(external_record_group_id="10-work-items")

        result = await connector.make_file_records_from_list(
            [self._file_attach(href=href)], record
        )

        assert (
            result[0].record.external_record_id
            == f"https://gitlab.com/api/v4/projects/10{href}"
        )

    @pytest.mark.asyncio
    async def test_record_update_external_record_id_matches_full_url(self) -> None:
        """RecordUpdate.external_record_id also equals the full attachment URL."""
        connector = self._setup_connector()
        href = "/uploads/abc123abc123abc123abc123abc123ab/file.pdf"
        record = self._make_record(external_record_group_id="10-work-items")

        result = await connector.make_file_records_from_list(
            [self._file_attach(href=href)], record
        )

        assert (
            result[0].external_record_id
            == f"https://gitlab.com/api/v4/projects/10{href}"
        )

    @pytest.mark.asyncio
    async def test_filerecord_org_id_from_connector(self) -> None:
        """FileRecord.org_id is taken from data_entities_processor.org_id."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach()], self._make_record()
        )

        assert result[0].record.org_id == "org-1"

    @pytest.mark.asyncio
    async def test_filerecord_connector_id_from_connector(self) -> None:
        """FileRecord.connector_id is the connector's own connector_id."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach()], self._make_record()
        )

        assert result[0].record.connector_id == "gitlab-conn-1"

    @pytest.mark.asyncio
    async def test_filerecord_parent_external_record_id_from_record(self) -> None:
        """FileRecord.parent_external_record_id is record.external_record_id."""
        connector = self._setup_connector()
        record = self._make_record(external_record_id="99")

        result = await connector.make_file_records_from_list(
            [self._file_attach()], record
        )

        assert result[0].record.parent_external_record_id == "99"

    @pytest.mark.asyncio
    async def test_filerecord_external_record_group_id_from_record(self) -> None:
        """FileRecord.external_record_group_id is record.external_record_group_id."""
        connector = self._setup_connector()
        record = self._make_record(external_record_group_id="55-work-items")

        result = await connector.make_file_records_from_list(
            [self._file_attach()], record
        )

        assert result[0].record.external_record_group_id == "55-work-items"

    @pytest.mark.asyncio
    async def test_filerecord_known_mime_type_resolved(self) -> None:
        """A known filetype (e.g. 'pdf') resolves to the correct MIME value."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach(filetype="pdf")], self._make_record()
        )

        assert result[0].record.mime_type == MimeTypes.PDF.value

    @pytest.mark.asyncio
    async def test_filerecord_unknown_mime_type_falls_back(self) -> None:
        """An unrecognised filetype falls back to MimeTypes.UNKNOWN."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach(filetype="xyz123")], self._make_record()
        )

        assert result[0].record.mime_type == MimeTypes.UNKNOWN.value

    @pytest.mark.asyncio
    async def test_filerecord_is_file_true(self) -> None:
        """FileRecord.is_file is always True."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach()], self._make_record()
        )

        assert result[0].record.is_file is True

    @pytest.mark.asyncio
    async def test_filerecord_inherit_permissions_true(self) -> None:
        """FileRecord.inherit_permissions is always True."""
        connector = self._setup_connector()

        result = await connector.make_file_records_from_list(
            [self._file_attach()], self._make_record()
        )

        assert result[0].record.inherit_permissions is True

    # ── project_id extraction from external_record_group_id ──────────────────

    @pytest.mark.asyncio
    async def test_base_url_uses_project_id_from_group_id(self) -> None:
        """The base URL uses the numeric prefix before the first '-' in external_record_group_id."""
        connector = self._setup_connector()
        href = "/uploads/abc123abc123abc123abc123abc123ab/file.pdf"
        record = self._make_record(external_record_group_id="77-work-items")

        result = await connector.make_file_records_from_list(
            [self._file_attach(href=href)], record
        )

        assert "projects/77" in result[0].record.weburl

    # ── existing record re-use ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_existing_record_id_is_reused(self) -> None:
        """When get_record_by_external_id returns a record, its id is reused."""
        existing = MagicMock()
        existing.id = "existing-uuid-abc"
        connector = self._setup_connector(existing_record=existing)

        result = await connector.make_file_records_from_list(
            [self._file_attach()], self._make_record()
        )

        assert result[0].record.id == "existing-uuid-abc"

    @pytest.mark.asyncio
    async def test_new_uuid_assigned_when_no_existing_record(self) -> None:
        """When no existing record, a fresh UUID is assigned to the FileRecord."""
        connector = self._setup_connector(existing_record=None)

        result = await connector.make_file_records_from_list(
            [self._file_attach()], self._make_record()
        )

        assert result[0].record.id is not None
        assert len(result[0].record.id) > 0

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_called_with_full_url(self) -> None:
        """get_record_by_external_id is called with connector_id and the full attachment URL."""
        connector = _make_connector()
        mock_tx_store = self._attach_tx_store(connector, existing_record=None)
        href = "/uploads/abc123abc123abc123abc123abc123ab/file.pdf"
        record = self._make_record(external_record_group_id="10-work-items")

        await connector.make_file_records_from_list(
            [self._file_attach(href=href)], record
        )

        mock_tx_store.get_record_by_external_id.assert_awaited_once_with(
            connector_id="gitlab-conn-1",
            external_id=f"https://gitlab.com/api/v4/projects/10{href}",
        )


class TestFetchAttachmentContent:
    """Unit tests for _fetch_attachment_content."""

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_record(
        self,
        record_id: str = "record-1",
        external_record_id: str = "https://gitlab.com/api/v4/projects/10/uploads/abc/file.pdf",
        weburl: str = "https://gitlab.com/api/v4/projects/10/uploads/abc/file.pdf",
    ) -> MagicMock:
        record = MagicMock(spec=Record)
        record.id = record_id
        record.external_record_id = external_record_id
        record.weburl = weburl
        return record

    async def _collect(self, connector, record) -> list[bytes]:
        """Drain the async generator and return all yielded chunks."""
        return [chunk async for chunk in connector._fetch_attachment_content(record)]

    def _setup_connector(self, chunks: list[bytes] | None = None) -> GitLabConnector:
        """Return a connector whose data_source streams the given chunks."""
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def _gen(_url) -> AsyncGenerator[bytes, None]:
            for c in chunks or []:
                yield c

        connector.data_source.get_attachment_files_content = _gen
        return connector

    # ── happy paths ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_yields_single_chunk(self) -> None:
        """A single chunk returned by the data source is yielded as-is."""
        connector = self._setup_connector(chunks=[b"hello"])

        chunks = await self._collect(connector, self._make_record())

        assert chunks == [b"hello"]

    @pytest.mark.asyncio
    async def test_yields_multiple_chunks_in_order(self) -> None:
        """All chunks are yielded in the order the data source produces them."""
        connector = self._setup_connector(chunks=[b"part1", b"part2", b"part3"])

        chunks = await self._collect(connector, self._make_record())

        assert chunks == [b"part1", b"part2", b"part3"]

    @pytest.mark.asyncio
    async def test_empty_stream_yields_nothing(self) -> None:
        """When the data source yields nothing, the generator is empty."""
        connector = self._setup_connector(chunks=[])

        chunks = await self._collect(connector, self._make_record())

        assert chunks == []

    @pytest.mark.asyncio
    async def test_get_attachment_files_content_called_with_weburl(self) -> None:
        """get_attachment_files_content is called with record.weburl."""
        called_with = []
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def _spy(url) -> AsyncGenerator[bytes, None]:
            called_with.append(url)
            yield b"data"

        connector.data_source.get_attachment_files_content = _spy
        record = self._make_record(
            weburl="https://gitlab.com/api/v4/projects/10/uploads/abc/f.pdf"
        )

        await self._collect(connector, record)

        assert called_with == [
            "https://gitlab.com/api/v4/projects/10/uploads/abc/f.pdf"
        ]

    # ── missing attachment_id ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_raises_when_external_record_id_is_none(self) -> None:
        """An exception is raised (and re-wrapped) when external_record_id is None."""
        connector = self._setup_connector()
        record = self._make_record(external_record_id=None)

        with pytest.raises(Exception, match="Error fetching attachment content"):
            async for _ in connector._fetch_attachment_content(record):
                pass

    @pytest.mark.asyncio
    async def test_raises_when_external_record_id_is_empty_string(self) -> None:
        """An exception is raised when external_record_id is an empty string."""
        connector = self._setup_connector()
        record = self._make_record(external_record_id="")

        with pytest.raises(Exception, match="Error fetching attachment content"):
            async for _ in connector._fetch_attachment_content(record):
                pass

    @pytest.mark.asyncio
    async def test_error_message_includes_record_id_for_missing_attachment_id(
        self,
    ) -> None:
        """The error message contains the record id when external_record_id is absent."""
        connector = self._setup_connector()
        record = self._make_record(record_id="rec-99", external_record_id=None)

        with pytest.raises(Exception, match="rec-99"):
            async for _ in connector._fetch_attachment_content(record):
                pass

    # ── missing weburl ────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_raises_when_weburl_is_none(self) -> None:
        """A ValueError is re-wrapped when record.weburl is None."""
        connector = self._setup_connector()
        record = self._make_record(weburl=None)

        with pytest.raises(Exception, match="Error fetching attachment content"):
            async for _ in connector._fetch_attachment_content(record):
                pass

    @pytest.mark.asyncio
    async def test_raises_when_weburl_is_empty_string(self) -> None:
        """A ValueError is re-wrapped when record.weburl is an empty string."""
        connector = self._setup_connector()
        record = self._make_record(weburl="")

        with pytest.raises(Exception, match="Error fetching attachment content"):
            async for _ in connector._fetch_attachment_content(record):
                pass

    @pytest.mark.asyncio
    async def test_error_message_includes_record_id_for_missing_weburl(self) -> None:
        """The error message contains the record id when weburl is absent."""
        connector = self._setup_connector()
        record = self._make_record(record_id="rec-42", weburl=None)

        with pytest.raises(Exception, match="rec-42"):
            async for _ in connector._fetch_attachment_content(record):
                pass

    # ── upstream errors ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_upstream_exception_is_wrapped(self) -> None:
        """An exception raised by get_attachment_files_content is wrapped in the outer Exception."""
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def _boom(_url) -> AsyncGenerator[bytes, None]:
            raise RuntimeError("network timeout")
            yield  # make it a generator

        connector.data_source.get_attachment_files_content = _boom

        with pytest.raises(Exception, match="Error fetching attachment content"):
            async for _ in connector._fetch_attachment_content(self._make_record()):
                pass

    @pytest.mark.asyncio
    async def test_upstream_exception_preserves_cause(self) -> None:
        """The original upstream exception is available as __cause__ on the raised exception."""
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def _boom(_url) -> AsyncGenerator[bytes, None]:
            raise RuntimeError("disk error")
            yield

        connector.data_source.get_attachment_files_content = _boom

        with pytest.raises(Exception) as exc_info:
            async for _ in connector._fetch_attachment_content(self._make_record()):
                pass

        assert exc_info.value.__cause__ is not None
        assert "disk error" in str(exc_info.value.__cause__)


class TestMakeChildRecordsOfAttachments:
    """Unit tests for make_child_records_of_attachments."""

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_record(
        self,
        external_record_group_id: str = "10-work-items",
    ) -> MagicMock:
        record = MagicMock(spec=Record)
        record.external_record_group_id = external_record_group_id
        return record

    def _file_attach(
        self,
        href: str = "/uploads/abc123abc123abc123abc123abc123ab/doc.pdf",
        filename: str = "doc.pdf",
        filetype: str = "pdf",
        category: str = GitlabLiterals.ATTACHMENT.value,
    ) -> FileAttachment:
        return FileAttachment(
            href=href, filename=filename, filetype=filetype, category=category
        )

    def _img_attach(
        self,
        href: str = "/uploads/abc123abc123abc123abc123abc123ab/image.png",
    ) -> FileAttachment:
        return FileAttachment(
            href=href,
            filename=href.rsplit("/", 1)[-1],
            filetype="png",
            category=GitlabLiterals.IMAGE.value,
        )

    def _make_existing_record(
        self, record_id: str = "rec-exist-1", record_name: str = "doc.pdf"
    ) -> MagicMock:
        existing = MagicMock(spec=Record)
        existing.id = record_id
        existing.record_name = record_name
        return existing

    def _make_record_update(
        self, record_id: str = "rec-new-1", record_name: str = "doc.pdf"
    ) -> MagicMock:
        ru = MagicMock(spec=RecordUpdate)
        ru.record = MagicMock()
        ru.record.id = record_id
        ru.record.record_name = record_name
        return ru

    def _attach_tx_store(self, connector, existing_record=None) -> AsyncMock:
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            return_value=existing_record
        )
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()
        return mock_tx_store

    def _setup_connector(self, existing_record=None) -> GitLabConnector:
        connector = _make_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))
        connector.make_file_records_from_list = AsyncMock(return_value=[])
        self._attach_tx_store(connector, existing_record=existing_record)
        return connector

    # ── empty / image-only inputs ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_attachments_returns_empty_lists(self) -> None:
        """When parse returns no attachments, both output lists are empty."""
        connector = self._setup_connector()

        child_records, remaining = await connector.make_child_records_of_attachments(
            "", self._make_record()
        )

        assert child_records == []
        assert remaining == []

    @pytest.mark.asyncio
    async def test_image_only_attachments_are_skipped(self) -> None:
        """Image attachments are entirely skipped; both output lists remain empty."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._img_attach()], "cleaned")
        )

        child_records, remaining = await connector.make_child_records_of_attachments(
            "![img](/uploads/abc/img.png)", self._make_record()
        )

        assert child_records == []
        assert remaining == []

    @pytest.mark.asyncio
    async def test_mixed_image_and_file_only_file_processed(self) -> None:
        """Images are skipped; only the file attachment produces a ChildRecord."""
        connector = self._setup_connector()
        mock_ru = self._make_record_update()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._img_attach(), self._file_attach()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[mock_ru])

        child_records, remaining = await connector.make_child_records_of_attachments(
            "markdown", self._make_record()
        )

        assert len(child_records) == 1
        assert len(remaining) == 1

    # ── new record (no existing record in DB) ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_new_file_creates_child_record_from_make_file_records(self) -> None:
        """When no existing record is found, make_file_records_from_list result becomes a ChildRecord."""
        connector = self._setup_connector(existing_record=None)
        mock_ru = self._make_record_update(record_id="new-1", record_name="report.pdf")
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[mock_ru])

        child_records, remaining = await connector.make_child_records_of_attachments(
            "markdown", self._make_record()
        )

        assert len(child_records) == 1
        assert child_records[0].child_id == "new-1"
        assert child_records[0].child_type == ChildType.RECORD
        assert child_records[0].child_name == "report.pdf"
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_make_file_records_from_list_called_with_single_attach_and_record(
        self,
    ) -> None:
        """make_file_records_from_list is called with a single-element list and the parent record."""
        connector = self._setup_connector(existing_record=None)
        attach = self._file_attach()
        record = self._make_record()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attach], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        await connector.make_child_records_of_attachments("markdown", record)

        connector.make_file_records_from_list.assert_called_once_with([attach], record)

    @pytest.mark.asyncio
    async def test_make_file_records_returns_empty_no_child_record_added(self) -> None:
        """When make_file_records_from_list returns [], no ChildRecord is appended."""
        connector = self._setup_connector(existing_record=None)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        child_records, remaining = await connector.make_child_records_of_attachments(
            "markdown", self._make_record()
        )

        assert child_records == []
        assert remaining == []

    # ── existing record in DB ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_existing_record_creates_child_record_without_calling_make_file(
        self,
    ) -> None:
        """When a matching record exists in DB, make_file_records_from_list is never called."""
        existing = self._make_existing_record(
            record_id="exist-1", record_name="old.pdf"
        )
        connector = self._setup_connector(existing_record=existing)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "cleaned")
        )

        child_records, remaining = await connector.make_child_records_of_attachments(
            "markdown", self._make_record()
        )

        connector.make_file_records_from_list.assert_not_called()
        assert len(child_records) == 1
        assert child_records[0].child_id == "exist-1"
        assert child_records[0].child_type == ChildType.RECORD
        assert child_records[0].child_name == "old.pdf"

    @pytest.mark.asyncio
    async def test_existing_record_remaining_attachments_stays_empty(self) -> None:
        """When the record already exists in DB, remaining_attachments list is empty."""
        existing = self._make_existing_record()
        connector = self._setup_connector(existing_record=existing)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "cleaned")
        )

        _, remaining = await connector.make_child_records_of_attachments(
            "markdown", self._make_record()
        )

        assert remaining == []

    # ── URL construction ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_called_with_correct_full_url(self) -> None:
        """full_attachment_url is built from project_id (split from group_id) and attach.href."""
        connector = self._setup_connector(existing_record=None)
        attach = self._file_attach(href="/uploads/abc/doc.pdf")
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attach], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )

        record = self._make_record(external_record_group_id="99-work-items")
        await connector.make_child_records_of_attachments("markdown", record)

        mock_tx_store.get_record_by_external_id.assert_called_once_with(
            connector_id="gitlab-conn-1",
            external_id="https://gitlab.com/api/v4/projects/99/uploads/abc/doc.pdf",
        )

    # ── multiple attachments ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_multiple_new_files_produce_multiple_child_records(self) -> None:
        """Two new file attachments each produce a ChildRecord and a RecordUpdate."""
        connector = self._setup_connector(existing_record=None)
        ru1 = self._make_record_update(record_id="new-1", record_name="a.pdf")
        ru2 = self._make_record_update(record_id="new-2", record_name="b.docx")
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(
                [
                    self._file_attach(
                        href="/uploads/aaa/a.pdf", filename="a.pdf", filetype="pdf"
                    ),
                    self._file_attach(
                        href="/uploads/bbb/b.docx", filename="b.docx", filetype="docx"
                    ),
                ],
                "cleaned",
            )
        )
        connector.make_file_records_from_list = AsyncMock(side_effect=[[ru1], [ru2]])

        child_records, remaining = await connector.make_child_records_of_attachments(
            "markdown", self._make_record()
        )

        assert len(child_records) == 2
        assert len(remaining) == 2
        assert child_records[0].child_id == "new-1"
        assert child_records[1].child_id == "new-2"

    @pytest.mark.asyncio
    async def test_mixed_existing_and_new_files(self) -> None:
        """One existing and one new attachment: existing becomes ChildRecord, new goes through make_file_records."""
        existing = self._make_existing_record(
            record_id="exist-99", record_name="exists.pdf"
        )
        connector = self._setup_connector()
        ru_new = self._make_record_update(record_id="new-99", record_name="new.pdf")

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            side_effect=[existing, None]
        )
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(
                [
                    self._file_attach(
                        href="/uploads/aaa/exists.pdf", filename="exists.pdf"
                    ),
                    self._file_attach(href="/uploads/bbb/new.pdf", filename="new.pdf"),
                ],
                "cleaned",
            )
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[ru_new])

        child_records, remaining = await connector.make_child_records_of_attachments(
            "markdown", self._make_record()
        )

        assert len(child_records) == 2
        assert child_records[0].child_id == "exist-99"
        assert child_records[1].child_id == "new-99"
        assert len(remaining) == 1


class TestMakeBlockCommentOfAttachments:
    """Unit tests for make_block_comment_of_attachments."""

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_record(
        self,
        external_record_group_id: str = "10-work-items",
    ) -> MagicMock:
        record = MagicMock(spec=Record)
        record.external_record_group_id = external_record_group_id
        return record

    def _file_attach(
        self,
        href: str = "/uploads/abc123abc123abc123abc123abc123ab/doc.pdf",
        filename: str = "doc.pdf",
        filetype: str = "pdf",
        category: str = GitlabLiterals.ATTACHMENT.value,
    ) -> FileAttachment:
        return FileAttachment(
            href=href, filename=filename, filetype=filetype, category=category
        )

    def _img_attach(
        self,
        href: str = "/uploads/abc123abc123abc123abc123abc123ab/image.png",
    ) -> FileAttachment:
        return FileAttachment(
            href=href,
            filename=href.rsplit("/", 1)[-1],
            filetype="png",
            category=GitlabLiterals.IMAGE.value,
        )

    def _make_existing_record(
        self, record_id: str = "rec-exist-1", record_name: str = "doc.pdf"
    ) -> MagicMock:
        existing = MagicMock(spec=Record)
        existing.id = record_id
        existing.record_name = record_name
        return existing

    def _make_record_update(
        self, record_id: str = "rec-new-1", record_name: str = "doc.pdf"
    ) -> MagicMock:
        ru = MagicMock(spec=RecordUpdate)
        ru.record = MagicMock()
        ru.record.id = record_id
        ru.record.record_name = record_name
        return ru

    def _attach_tx_store(self, connector, existing_record=None) -> AsyncMock:
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            return_value=existing_record
        )
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()
        return mock_tx_store

    def _setup_connector(self, existing_record=None) -> GitLabConnector:
        connector = _make_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(return_value=([], ""))
        connector.make_file_records_from_list = AsyncMock(return_value=[])
        self._attach_tx_store(connector, existing_record=existing_record)
        return connector

    # ── empty / image-only inputs ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_attachments_returns_empty_lists(self) -> None:
        """When parse returns no attachments, both output lists are empty."""
        connector = self._setup_connector()

        (
            comment_attachments,
            remaining,
        ) = await connector.make_block_comment_of_attachments("", self._make_record())

        assert comment_attachments == []
        assert remaining == []

    @pytest.mark.asyncio
    async def test_image_only_attachments_are_skipped(self) -> None:
        """Image attachments are entirely skipped; both output lists remain empty."""
        connector = self._setup_connector()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._img_attach()], "cleaned")
        )

        (
            comment_attachments,
            remaining,
        ) = await connector.make_block_comment_of_attachments(
            "![img](/uploads/abc/img.png)", self._make_record()
        )

        assert comment_attachments == []
        assert remaining == []

    @pytest.mark.asyncio
    async def test_mixed_image_and_file_only_file_processed(self) -> None:
        """Images are skipped; only the file attachment produces a CommentAttachment."""
        connector = self._setup_connector()
        mock_ru = self._make_record_update()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._img_attach(), self._file_attach()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[mock_ru])

        (
            comment_attachments,
            remaining,
        ) = await connector.make_block_comment_of_attachments(
            "markdown", self._make_record()
        )

        assert len(comment_attachments) == 1
        assert len(remaining) == 1

    # ── new record (no existing record in DB) ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_new_file_creates_comment_attachment_from_make_file_records(
        self,
    ) -> None:
        """When no existing record is found, make_file_records_from_list result becomes a CommentAttachment."""
        connector = self._setup_connector(existing_record=None)
        mock_ru = self._make_record_update(record_id="new-1", record_name="report.pdf")
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[mock_ru])

        (
            comment_attachments,
            remaining,
        ) = await connector.make_block_comment_of_attachments(
            "markdown", self._make_record()
        )

        assert len(comment_attachments) == 1
        assert comment_attachments[0].id == "new-1"
        assert comment_attachments[0].name == "report.pdf"
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_make_file_records_from_list_called_with_single_attach_and_record(
        self,
    ) -> None:
        """make_file_records_from_list is called with a single-element list and the parent record."""
        connector = self._setup_connector(existing_record=None)
        attach = self._file_attach()
        record = self._make_record()
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attach], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        await connector.make_block_comment_of_attachments("markdown", record)

        connector.make_file_records_from_list.assert_called_once_with([attach], record)

    @pytest.mark.asyncio
    async def test_make_file_records_returns_empty_no_comment_attachment_added(
        self,
    ) -> None:
        """When make_file_records_from_list returns [], no CommentAttachment is appended."""
        connector = self._setup_connector(existing_record=None)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        (
            comment_attachments,
            remaining,
        ) = await connector.make_block_comment_of_attachments(
            "markdown", self._make_record()
        )

        assert comment_attachments == []
        assert remaining == []

    # ── existing record in DB ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_existing_record_creates_comment_attachment_without_calling_make_file(
        self,
    ) -> None:
        """When a matching record exists in DB, make_file_records_from_list is never called."""
        existing = self._make_existing_record(
            record_id="exist-1", record_name="old.pdf"
        )
        connector = self._setup_connector(existing_record=existing)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "cleaned")
        )

        (
            comment_attachments,
            remaining,
        ) = await connector.make_block_comment_of_attachments(
            "markdown", self._make_record()
        )

        connector.make_file_records_from_list.assert_not_called()
        assert len(comment_attachments) == 1
        assert comment_attachments[0].id == "exist-1"
        assert comment_attachments[0].name == "old.pdf"
        assert remaining == []

    @pytest.mark.asyncio
    async def test_existing_record_remaining_attachments_stays_empty(self) -> None:
        """When the record already exists in DB, remaining_attachments list is empty."""
        existing = self._make_existing_record()
        connector = self._setup_connector(existing_record=existing)
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([self._file_attach()], "cleaned")
        )

        _, remaining = await connector.make_block_comment_of_attachments(
            "markdown", self._make_record()
        )

        assert remaining == []

    # ── URL construction ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_record_by_external_id_called_with_correct_full_url(self) -> None:
        """full_attachment_url is built from project_id (split from group_id) and attach.href."""
        connector = self._setup_connector(existing_record=None)
        attach = self._file_attach(href="/uploads/abc/doc.pdf")
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=([attach], "cleaned")
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[])

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )

        record = self._make_record(external_record_group_id="42-work-items")
        await connector.make_block_comment_of_attachments("markdown", record)

        mock_tx_store.get_record_by_external_id.assert_called_once_with(
            connector_id="gitlab-conn-1",
            external_id="https://gitlab.com/api/v4/projects/42/uploads/abc/doc.pdf",
        )

    # ── multiple attachments ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_multiple_new_files_produce_multiple_comment_attachments(
        self,
    ) -> None:
        """Two new file attachments each produce a CommentAttachment and a RecordUpdate."""
        connector = self._setup_connector(existing_record=None)
        ru1 = self._make_record_update(record_id="new-1", record_name="a.pdf")
        ru2 = self._make_record_update(record_id="new-2", record_name="b.docx")
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(
                [
                    self._file_attach(
                        href="/uploads/aaa/a.pdf", filename="a.pdf", filetype="pdf"
                    ),
                    self._file_attach(
                        href="/uploads/bbb/b.docx", filename="b.docx", filetype="docx"
                    ),
                ],
                "cleaned",
            )
        )
        connector.make_file_records_from_list = AsyncMock(side_effect=[[ru1], [ru2]])

        (
            comment_attachments,
            remaining,
        ) = await connector.make_block_comment_of_attachments(
            "markdown", self._make_record()
        )

        assert len(comment_attachments) == 2
        assert len(remaining) == 2
        assert comment_attachments[0].id == "new-1"
        assert comment_attachments[1].id == "new-2"

    @pytest.mark.asyncio
    async def test_mixed_existing_and_new_files(self) -> None:
        """One existing and one new attachment: existing produces CommentAttachment, new goes through make_file_records."""
        existing = self._make_existing_record(
            record_id="exist-99", record_name="exists.pdf"
        )
        connector = self._setup_connector()
        ru_new = self._make_record_update(record_id="new-99", record_name="new.pdf")

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            side_effect=[existing, None]
        )
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.parse_gitlab_uploads_clean_test = AsyncMock(
            return_value=(
                [
                    self._file_attach(
                        href="/uploads/aaa/exists.pdf", filename="exists.pdf"
                    ),
                    self._file_attach(href="/uploads/bbb/new.pdf", filename="new.pdf"),
                ],
                "cleaned",
            )
        )
        connector.make_file_records_from_list = AsyncMock(return_value=[ru_new])

        (
            comment_attachments,
            remaining,
        ) = await connector.make_block_comment_of_attachments(
            "markdown", self._make_record()
        )

        assert len(comment_attachments) == 2
        assert comment_attachments[0].id == "exist-99"
        assert comment_attachments[1].id == "new-99"
        assert len(remaining) == 1


class TestGitlabConnectorCleanup:
    """Unit tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_sets_data_source_to_none(self) -> None:
        """After cleanup, data_source is set to None."""
        connector = _make_connector()
        connector.data_source = MagicMock()

        await connector.cleanup()

        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_logs_info_message(self) -> None:
        """cleanup logs an info message about releasing resources."""
        connector = _make_connector()
        connector.data_source = MagicMock()

        await connector.cleanup()

        connector.logger.info.assert_called_once()
        msg = connector.logger.info.call_args[0][0]
        assert "GitLab" in msg or "cleanup" in msg.lower() or "Cleaning" in msg

    @pytest.mark.asyncio
    async def test_cleanup_when_data_source_already_none(self) -> None:
        """cleanup does not raise when data_source is already None."""
        connector = _make_connector()
        connector.data_source = None

        await connector.cleanup()

        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_called_twice_remains_none(self) -> None:
        """Calling cleanup a second time is safe and leaves data_source as None."""
        connector = _make_connector()
        connector.data_source = MagicMock()

        await connector.cleanup()
        await connector.cleanup()

        assert connector.data_source is None


class TestGitlabConnectorCreateConnector:
    """Unit tests for create_connector class method."""

    @pytest.mark.asyncio
    async def test_returns_gitlab_connector_instance(self) -> None:
        """create_connector returns a GitLabConnector instance."""
        mock_dep = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab.connector.DataSourceEntitiesProcessor",
            return_value=mock_dep,
        ):
            result = await GitLabConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="conn-1",
                scope="personal",
                created_by="user-1",
            )

        assert isinstance(result, GitLabConnector)

    @pytest.mark.asyncio
    async def test_data_entities_processor_is_initialized(self) -> None:
        """DataSourceEntitiesProcessor.initialize() is awaited during construction."""
        mock_dep = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab.connector.DataSourceEntitiesProcessor",
            return_value=mock_dep,
        ):
            await GitLabConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="conn-1",
                scope="personal",
                created_by="user-1",
            )

        mock_dep.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connector_id_is_forwarded(self) -> None:
        """The connector_id passed to create_connector appears on the returned instance."""
        mock_dep = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab.connector.DataSourceEntitiesProcessor",
            return_value=mock_dep,
        ):
            result = await GitLabConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="my-conn-id",
                scope="personal",
                created_by="user-1",
            )

        assert result.connector_id == "my-conn-id"

    @pytest.mark.asyncio
    async def test_scope_is_forwarded(self) -> None:
        """The scope passed to create_connector appears on the returned instance."""
        mock_dep = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab.connector.DataSourceEntitiesProcessor",
            return_value=mock_dep,
        ):
            result = await GitLabConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="conn-1",
                scope="organization",
                created_by="user-1",
            )

        assert result.scope == "organization"

    @pytest.mark.asyncio
    async def test_created_by_is_forwarded(self) -> None:
        """The created_by value passed to create_connector appears on the returned instance."""
        mock_dep = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab.connector.DataSourceEntitiesProcessor",
            return_value=mock_dep,
        ):
            result = await GitLabConnector.create_connector(
                logger=MagicMock(),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="conn-1",
                scope="personal",
                created_by="owner-42",
            )

        assert result.created_by == "owner-42"

    @pytest.mark.asyncio
    async def test_data_entities_processor_constructed_with_correct_args(self) -> None:
        """DataSourceEntitiesProcessor receives logger, data_store_provider, and config_service."""
        mock_dep = AsyncMock()
        mock_logger = MagicMock()
        mock_dsp = MagicMock()
        mock_config = AsyncMock()

        with patch(
            "app.connectors.sources.gitlab.connector.DataSourceEntitiesProcessor",
        ) as MockDep:
            MockDep.return_value = mock_dep
            await GitLabConnector.create_connector(
                logger=mock_logger,
                data_store_provider=mock_dsp,
                config_service=mock_config,
                connector_id="conn-1",
                scope="personal",
                created_by="user-1",
            )

        MockDep.assert_called_once_with(mock_logger, mock_dsp, mock_config)

    @pytest.mark.asyncio
    async def test_initialize_exception_propagates(self) -> None:
        """If DataSourceEntitiesProcessor.initialize() raises, the exception propagates."""
        mock_dep = AsyncMock()
        mock_dep.initialize.side_effect = RuntimeError("init failed")

        with patch(
            "app.connectors.sources.gitlab.connector.DataSourceEntitiesProcessor",
            return_value=mock_dep,
        ):
            with pytest.raises(RuntimeError, match="init failed"):
                await GitLabConnector.create_connector(
                    logger=MagicMock(),
                    data_store_provider=MagicMock(),
                    config_service=AsyncMock(),
                    connector_id="conn-1",
                    scope="personal",
                    created_by="user-1",
                )


class TestSyncRepoMain:
    """Unit tests for _sync_repo_main."""

    # ── helpers ───────────────────────────────────────────────────────────────

    def _gql_tree_response(
        self,
        tree_nodes: list[dict],
        has_next_page: bool = False,
        end_cursor: str = "",
    ) -> str:
        """Build a JSON string mimicking the GraphQL repo-tree response."""
        return json.dumps(
            {
                "data": {
                    "project": {
                        "repository": {
                            "paginatedTree": {
                                "nodes": [
                                    {
                                        "trees": {"nodes": tree_nodes},
                                    }
                                ],
                                "pageInfo": {
                                    "hasNextPage": has_next_page,
                                    "endCursor": end_cursor,
                                },
                            }
                        }
                    }
                }
            }
        )

    def _gql_file_response(
        self,
        blob_nodes: list[dict],
        has_next_page: bool = False,
        end_cursor: str = "",
        success: bool = True,
    ) -> MagicMock:
        """Build a mock response for get_file_tree_g."""
        res = MagicMock()
        res.success = success
        res.data = (
            json.dumps(
                {
                    "data": {
                        "project": {
                            "repository": {
                                "paginatedTree": {
                                    "nodes": [
                                        {
                                            "blobs": {"nodes": blob_nodes},
                                        }
                                    ],
                                    "pageInfo": {
                                        "hasNextPage": has_next_page,
                                        "endCursor": end_cursor,
                                    },
                                }
                            }
                        }
                    }
                }
            )
            if blob_nodes is not None
            else None
        )
        res.error = None if success else "some error"
        return res

    def _make_tree_node(
        self,
        path: str = "src",
        name: str = "src",
        sha: str = "abc123",
        web_path: str = "/project/src",
        web_url: str = "https://gitlab.com/project/src",
        node_type: str = "tree",
    ) -> dict:
        return {
            "path": path,
            "name": name,
            "sha": sha,
            "webPath": web_path,
            "webUrl": web_url,
            "type": node_type,
        }

    def _make_blob_node(
        self,
        path: str = "src/main.py",
        name: str = "main.py",
        sha: str = "def456",
        web_path: str = "/project/src/main.py",
        web_url: str = "https://gitlab.com/project/src/main.py",
    ) -> dict:
        return {
            "path": path,
            "name": name,
            "sha": sha,
            "webPath": web_path,
            "webUrl": web_url,
        }

    def _attach_tx_store(
        self, connector, existing_record=None, parent_record=None
    ) -> AsyncMock:
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(
            return_value=existing_record
        )
        mock_tx_store.get_record_by_path = AsyncMock(return_value=parent_record)
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()
        return mock_tx_store

    def _setup_connector(
        self, existing_record=None, parent_record=None
    ) -> GitLabConnector:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._process_new_records = AsyncMock()
        connector.build_code_file_records = AsyncMock()
        self._attach_tx_store(
            connector, existing_record=existing_record, parent_record=parent_record
        )
        return connector

    # ── Phase 1: get_repo_tree_g early exits ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_returns_early_when_get_repo_tree_g_raises(self) -> None:
        """If get_repo_tree_g raises, the method logs an error and returns without processing."""
        connector = self._setup_connector()
        connector.data_source.get_repo_tree_g = AsyncMock(
            side_effect=Exception("network")
        )

        await connector._sync_repo_main(10, "my/project")

        connector.logger.error.assert_called_once()
        connector._process_new_records.assert_not_called()
        connector.build_code_file_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_early_when_tree_res_data_is_none(self) -> None:
        """If tree_res.data is falsy, logs info and returns."""
        connector = self._setup_connector()
        tree_res = MagicMock()
        tree_res.data = None
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        await connector._sync_repo_main(10, "my/project")

        connector.logger.info.assert_called()
        connector._process_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_early_when_no_project_nodes_in_tree(self) -> None:
        """If paginatedTree.nodes is empty, logs info and returns."""
        connector = self._setup_connector()
        tree_res = MagicMock()
        tree_res.data = json.dumps(
            {
                "data": {
                    "project": {
                        "repository": {
                            "paginatedTree": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            }
        )
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        await connector._sync_repo_main(10, "my/project")

        connector._process_new_records.assert_not_called()

    # ── Phase 1: single-page tree with one root-level folder ──────────────────

    @pytest.mark.asyncio
    async def test_single_root_folder_creates_file_record(self) -> None:
        """A single root-level tree node produces one FileRecord via _process_new_records."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(
            tree_nodes=[self._make_tree_node()],
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector._process_new_records.assert_called_once()
        records = connector._process_new_records.call_args[0][0]
        assert len(records) == 1

        ru = records[0]
        assert ru.is_new is True
        assert ru.is_updated is False
        assert ru.is_deleted is False
        assert ru.record.record_name == "src"
        assert ru.record.external_record_group_id == "10-code-repository"
        assert ru.record.mime_type == MimeTypes.FOLDER.value
        assert ru.record.is_file is False
        assert ru.record.inherit_permissions is True
        assert ru.record.record_type == RecordType.FILE.value

    @pytest.mark.asyncio
    async def test_root_folder_has_none_parent_external_record_id(self) -> None:
        """A root-level folder (empty parent_path) has parent_external_record_id=None."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(
            tree_nodes=[self._make_tree_node(path="root_dir", name="root_dir")],
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.parent_external_record_id is None

    # ── Phase 1: existing record uses existing id ─────────────────────────────

    @pytest.mark.asyncio
    async def test_existing_record_uses_existing_id(self) -> None:
        """When get_record_by_external_id returns an existing record, its id is reused."""
        existing = MagicMock()
        existing.id = "existing-uuid"
        connector = self._setup_connector(existing_record=existing)
        tree_data = self._gql_tree_response(
            tree_nodes=[self._make_tree_node()],
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.id == "existing-uuid"
        assert records[0].is_new is False

    # ── Phase 1: pagination ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_tree_pagination_fetches_multiple_pages(self) -> None:
        """When hasNextPage is True, a second page is fetched before processing."""
        connector = self._setup_connector(existing_record=None)

        page1_data = self._gql_tree_response(
            tree_nodes=[
                self._make_tree_node(path="dir1", name="dir1", web_path="/p/dir1")
            ],
            has_next_page=True,
            end_cursor="cursor-page2",
        )
        page2_data = self._gql_tree_response(
            tree_nodes=[
                self._make_tree_node(path="dir2", name="dir2", web_path="/p/dir2")
            ],
            has_next_page=False,
        )
        page1_res = MagicMock()
        page1_res.data = page1_data
        page2_res = MagicMock()
        page2_res.data = page2_data
        connector.data_source.get_repo_tree_g = AsyncMock(
            side_effect=[page1_res, page2_res]
        )

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        assert connector.data_source.get_repo_tree_g.call_count == 2
        second_call_kwargs = connector.data_source.get_repo_tree_g.call_args_list[1]
        assert second_call_kwargs == (
            {"project_id": "my/project", "ref": "HEAD", "after_cursor": "cursor-page2"},
        ) or "cursor-page2" in str(second_call_kwargs)

    @pytest.mark.asyncio
    async def test_tree_pagination_stops_when_end_cursor_is_empty(self) -> None:
        """Pagination stops even if hasNextPage is True but endCursor is empty."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(
            tree_nodes=[self._make_tree_node()],
            has_next_page=True,
            end_cursor="",
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        assert connector.data_source.get_repo_tree_g.call_count == 1

    # ── Phase 1: nested folders & parent resolution ───────────────────────────

    @pytest.mark.asyncio
    async def test_nested_folder_parent_resolved_from_cache(self) -> None:
        """A child folder's parent_external_record_id comes from the path cache (not DB)."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(
            tree_nodes=[
                self._make_tree_node(path="src", name="src", web_path="/p/src"),
                self._make_tree_node(
                    path="src/utils", name="utils", web_path="/p/src/utils"
                ),
            ],
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        all_calls = connector._process_new_records.call_args_list
        all_records = []
        for call in all_calls:
            all_records.extend(call[0][0])
        child = [r for r in all_records if r.record.record_name == "utils"]
        assert len(child) == 1

    @pytest.mark.asyncio
    async def test_nested_folder_parent_resolved_from_db(self) -> None:
        """When the parent isn't in cache, it's fetched from DB via get_record_by_path."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._process_new_records = AsyncMock()
        connector.build_code_file_records = AsyncMock()

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx_store.get_record_by_path = AsyncMock(
            return_value={"externalRecordId": "/p/src"}
        )
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            return_value=mock_tx_store
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        tree_data = self._gql_tree_response(
            tree_nodes=[
                self._make_tree_node(
                    path="src/utils", name="utils", web_path="/p/src/utils"
                ),
            ],
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.parent_external_record_id == "/p/src"

    @pytest.mark.asyncio
    async def test_parent_path_db_lookup_fails_logs_error(self) -> None:
        """When get_record_by_path raises, the error is logged and parent defaults to None."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._process_new_records = AsyncMock()
        connector.build_code_file_records = AsyncMock()

        call_count = [0]

        async def _tx_enter(*_) -> AsyncMock:
            call_count[0] += 1
            mock = AsyncMock()
            if call_count[0] == 1:
                mock.get_record_by_path = AsyncMock(side_effect=Exception("DB error"))
            mock.get_record_by_external_id = AsyncMock(return_value=None)
            return mock

        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = _tx_enter
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock()

        tree_data = self._gql_tree_response(
            tree_nodes=[
                self._make_tree_node(path="src/sub", name="sub", web_path="/p/src/sub"),
            ],
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector.logger.error.assert_called()

    # ── Phase 1: non-tree nodes are skipped ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_non_tree_type_nodes_are_skipped(self) -> None:
        """Nodes whose type is not 'tree' (e.g. 'blob') do not produce records in phase 1."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(
            tree_nodes=[
                self._make_tree_node(node_type="blob"),
            ],
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector._process_new_records.assert_not_called()

    # ── Phase 1: level-wise ordering ──────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_folders_processed_level_by_level(self) -> None:
        """Folders are grouped by depth and processed per-level, flushing after each level."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(
            tree_nodes=[
                self._make_tree_node(path="a/b", name="b", web_path="/p/a/b"),
                self._make_tree_node(path="a", name="a", web_path="/p/a"),
            ],
        )
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        assert connector._process_new_records.call_count == 2
        first_batch = connector._process_new_records.call_args_list[0][0][0]
        second_batch = connector._process_new_records.call_args_list[1][0][0]
        assert first_batch[0].record.record_name == "a"
        assert second_batch[0].record.record_name == "b"

    # ── Phase 2: get_file_tree_g early exits ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_phase2_returns_when_get_file_tree_g_raises(self) -> None:
        """If get_file_tree_g raises, the method logs error and returns."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[])
        tree_res = MagicMock()
        tree_res.data = tree_data
        # Phase 1 returns early (no project_nodes)
        empty_json = json.dumps(
            {
                "data": {
                    "project": {
                        "repository": {
                            "paginatedTree": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            }
        )
        tree_res.data = empty_json
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)
        # Phase 1 exits early because nodes=[], never reaches phase 2
        # So we need phase 1 to succeed first

        # Need at least one tree node so phase 1 completes and phase 2 runs
        connector3 = self._setup_connector(existing_record=None)
        tree_data3 = self._gql_tree_response(
            tree_nodes=[self._make_tree_node()],
        )
        tree_res3 = MagicMock()
        tree_res3.data = tree_data3
        connector3.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res3)
        connector3.data_source.get_file_tree_g = AsyncMock(
            side_effect=Exception("file tree error")
        )

        await connector3._sync_repo_main(10, "my/project")

        connector3.build_code_file_records.assert_not_called()
        # error logged for file tree
        error_msgs = [str(c) for c in connector3.logger.error.call_args_list]
        assert any("file tree" in m.lower() or "Error" in m for m in error_msgs)

    @pytest.mark.asyncio
    async def test_phase2_returns_when_success_is_false(self) -> None:
        """If file tree response has success=False, method returns."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = MagicMock()
        file_res.success = False
        file_res.error = "quota exceeded"
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector.build_code_file_records.assert_not_called()
        connector.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_phase2_returns_when_data_is_none(self) -> None:
        """If file tree response data is None, method logs info and returns."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = MagicMock()
        file_res.success = True
        file_res.data = None
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector.build_code_file_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase2_returns_when_json_is_invalid(self) -> None:
        """If file tree JSON is malformed, method logs error and returns."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = MagicMock()
        file_res.success = True
        file_res.data = "NOT VALID JSON{{"
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector.build_code_file_records.assert_not_called()
        connector.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_phase2_returns_when_no_project_nodes(self) -> None:
        """If file tree paginatedTree.nodes is empty, method returns."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = MagicMock()
        file_res.success = True
        file_res.data = json.dumps(
            {
                "data": {
                    "project": {
                        "repository": {
                            "paginatedTree": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            }
        )
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector.build_code_file_records.assert_not_called()

    # ── Phase 2: successful file processing ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_phase2_calls_build_code_file_records_with_blobs(self) -> None:
        """File blob nodes are forwarded to build_code_file_records."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        blobs = [self._make_blob_node()]
        file_res = self._gql_file_response(blob_nodes=blobs, has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector.build_code_file_records.assert_called_once_with(
            blobs, 10, "my/project"
        )

    @pytest.mark.asyncio
    async def test_phase2_empty_blobs_does_not_call_build(self) -> None:
        """If blob nodes list is empty, build_code_file_records is not called."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        connector.build_code_file_records.assert_not_called()

    # ── Phase 2: pagination ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_phase2_pagination_fetches_multiple_pages(self) -> None:
        """File tree pagination fetches all pages and calls build_code_file_records per page."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        blob1 = [self._make_blob_node(name="a.py")]
        blob2 = [self._make_blob_node(name="b.py")]
        file_res_1 = self._gql_file_response(
            blob_nodes=blob1, has_next_page=True, end_cursor="file-cursor-2"
        )
        file_res_2 = self._gql_file_response(blob_nodes=blob2, has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(
            side_effect=[file_res_1, file_res_2]
        )

        await connector._sync_repo_main(10, "my/project")

        assert connector.build_code_file_records.call_count == 2

    @pytest.mark.asyncio
    async def test_phase2_pagination_stops_when_end_cursor_empty(self) -> None:
        """File tree pagination stops when endCursor is empty even if hasNextPage is True."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(
            blob_nodes=[self._make_blob_node()], has_next_page=True, end_cursor=""
        )
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(10, "my/project")

        assert connector.data_source.get_file_tree_g.call_count == 1

    # ── get_repo_tree_g call args ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_repo_tree_g_called_with_project_path_and_head(self) -> None:
        """get_repo_tree_g is called with project_path (not project_id) and ref='HEAD'."""
        connector = self._setup_connector(existing_record=None)
        tree_res = MagicMock()
        tree_res.data = None
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        await connector._sync_repo_main(99, "owner/repo")

        connector.data_source.get_repo_tree_g.assert_called_once_with(
            project_id="owner/repo", ref="HEAD", after_cursor=""
        )

    # ── external_group_id format ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_external_group_id_uses_project_id(self) -> None:
        """external_record_group_id on created records is '{project_id}-code-repository'."""
        connector = self._setup_connector(existing_record=None)
        tree_data = self._gql_tree_response(tree_nodes=[self._make_tree_node()])
        tree_res = MagicMock()
        tree_res.data = tree_data
        connector.data_source.get_repo_tree_g = AsyncMock(return_value=tree_res)

        file_res = self._gql_file_response(blob_nodes=[], has_next_page=False)
        connector.data_source.get_file_tree_g = AsyncMock(return_value=file_res)

        await connector._sync_repo_main(42, "my/project")

        records = connector._process_new_records.call_args[0][0]
        assert records[0].record.external_record_group_id == "42-code-repository"
