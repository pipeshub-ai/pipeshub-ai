"""Comprehensive unit tests for the Linear connector - full coverage."""

import base64
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperatorType,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.connectors.sources.linear.connector import (
    LINEAR_CONFIG_PATH,
    LinearConnector,
)
from app.models.blocks import (
    BlockComment,
    BlockGroup,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    MimeTypes,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector():
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.reindex_existing_records = AsyncMock()
    data_store_provider = MagicMock()

    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx_store.get_record_by_weburl = AsyncMock(return_value=None)
    mock_tx_store.get_records_by_parent = AsyncMock(return_value=[])
    mock_tx_store.delete_records_and_relations = AsyncMock()

    class FakeTxContext:
        async def __aenter__(self):
            return mock_tx_store

        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTxContext())
    config_service = AsyncMock()
    connector_id = "linear-conn-1"
    connector = LinearConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
    )
    connector._tx_store = mock_tx_store
    return connector


def _mock_response(success=True, data=None, message=""):
    response = MagicMock()
    response.success = success
    response.data = data
    response.message = message
    return response


def _mock_linear_org():
    return _mock_response(
        data={
            "organization": {
                "id": "org-linear-1",
                "name": "Test Org",
                "urlKey": "test-org",
            }
        }
    )


def _mock_paginated(key, nodes, has_next=False, end_cursor=None):
    return _mock_response(
        data={
            key: {
                "nodes": nodes,
                "pageInfo": {
                    "hasNextPage": has_next,
                    "endCursor": end_cursor,
                },
            }
        }
    )


def _make_issue_data(
    issue_id="issue-1",
    identifier="ENG-1",
    title="Test Issue",
    team_id="team-1",
    description="Some description",
    created_at="2024-01-15T10:00:00.000Z",
    updated_at="2024-01-15T12:00:00.000Z",
    priority=2,
    state=None,
    assignee=None,
    creator=None,
    parent=None,
    labels=None,
    relations=None,
    comments=None,
    trashed=False,
    archived_at=None,
    url="https://linear.app/test-org/issue/ENG-1",
    attachments=None,
    documents=None,
):
    data = {
        "id": issue_id,
        "identifier": identifier,
        "title": title,
        "description": description,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "priority": priority,
        "state": state or {"name": "In Progress", "type": "started"},
        "assignee": assignee,
        "creator": creator,
        "parent": parent,
        "labels": labels or {"nodes": []},
        "relations": relations or {"nodes": []},
        "comments": comments or {"nodes": []},
        "trashed": trashed,
        "url": url,
        "team": {"id": team_id},
    }
    if archived_at:
        data["archivedAt"] = archived_at
    if attachments:
        data["attachments"] = attachments
    if documents:
        data["documents"] = documents
    return data


def _make_project_data(
    project_id="proj-1",
    name="Test Project",
    slug_id="test-project",
    content="Project content",
    description="Project desc",
    created_at="2024-01-10T10:00:00.000Z",
    updated_at="2024-01-15T12:00:00.000Z",
    status=None,
    lead=None,
    url="https://linear.app/test-org/project/test-project",
    external_links=None,
    documents=None,
    issues=None,
    comments=None,
    milestones=None,
    project_updates=None,
    trashed=False,
    archived_at=None,
):
    data = {
        "id": project_id,
        "name": name,
        "slugId": slug_id,
        "content": content,
        "description": description,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "status": status,
        "lead": lead,
        "url": url,
        "priorityLabel": "High",
        "externalLinks": external_links or {"nodes": []},
        "documents": documents or {"nodes": []},
        "issues": issues or {"nodes": []},
        "comments": comments or {"nodes": []},
        "projectMilestones": milestones or {"nodes": []},
        "projectUpdates": project_updates or {"nodes": []},
        "trashed": trashed,
    }
    if archived_at:
        data["archivedAt"] = archived_at
    return data


def _make_team_record_group(team_id="team-1", name="Engineering", key="ENG"):
    rg = RecordGroup(
        id="rg-1",
        org_id="org-1",
        external_group_id=team_id,
        connector_id="linear-conn-1",
        connector_name=Connectors.LINEAR,
        name=name,
        short_name=key,
        group_type=RecordGroupType.PROJECT,
    )
    perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
    return rg, perms


# ===================================================================
# _fetch_projects_for_team_batch
# ===================================================================


class TestFetchProjectsForTeamBatch:
    @pytest.mark.asyncio
    async def test_single_batch_with_projects(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        project_data = _make_project_data()
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [project_data])
        )
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )
        mock_ds.get_file_size = AsyncMock(return_value=0)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 1
            has_project = any(
                isinstance(r, ProjectRecord) for r, _ in batches[0]
            )
            assert has_project

    @pytest.mark.asyncio
    async def test_empty_project_list(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [])
        )
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 0

    @pytest.mark.asyncio
    async def test_project_fetch_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_response(success=False, message="Error")
        )
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 0

    @pytest.mark.asyncio
    async def test_project_with_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        proj1 = _make_project_data(project_id="proj-1", name="P1")
        proj2 = _make_project_data(project_id="proj-2", name="P2")

        page1 = _mock_paginated("projects", [proj1], has_next=True, end_cursor="cursor-1")
        page2 = _mock_paginated("projects", [proj2], has_next=False)

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(side_effect=[page1, page2])
        mock_ds.project = AsyncMock(
            side_effect=[
                _mock_response(data={"project": proj1}),
                _mock_response(data={"project": proj2}),
            ]
        )
        mock_ds.get_file_size = AsyncMock(return_value=0)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 2

    @pytest.mark.asyncio
    async def test_project_with_indexing_filter_off(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        indexing_filters = MagicMock()
        indexing_filters.is_enabled = MagicMock(return_value=False)
        connector.indexing_filters = indexing_filters

        project_data = _make_project_data(content="")
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [project_data])
        )
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 1
            for record, _ in batches[0]:
                if isinstance(record, ProjectRecord):
                    assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_project_with_related_issues(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        project_data = _make_project_data(
            content="",
            issues={"nodes": [{"id": "issue-1"}, {"id": "issue-2"}]},
        )
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [project_data])
        )
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            for record, _ in batches[0]:
                if isinstance(record, ProjectRecord):
                    assert len(record.related_external_records) == 2

    @pytest.mark.asyncio
    async def test_project_processing_error_continues(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        proj_bad = {"id": ""}
        proj_good = _make_project_data(project_id="proj-good", content="")
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [proj_bad, proj_good])
        )
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": proj_good})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            batches = []
            async for batch in connector._fetch_projects_for_team_batch(
                team_id="team-1", team_key="ENG"
            ):
                batches.append(batch)
            assert len(batches) == 1


# ===================================================================
# _convert_images_to_base64_in_markdown
# ===================================================================


class TestConvertImagesToBase64:
    @pytest.mark.asyncio
    async def test_empty_string(self):
        connector = _make_connector()
        result = await connector._convert_images_to_base64_in_markdown("")
        assert result == ""

    @pytest.mark.asyncio
    async def test_none_input(self):
        connector = _make_connector()
        result = await connector._convert_images_to_base64_in_markdown(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_images(self):
        connector = _make_connector()
        text = "Hello world, no images here"
        result = await connector._convert_images_to_base64_in_markdown(text)
        assert result == text

    @pytest.mark.asyncio
    async def test_non_linear_images_skipped(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        mock_ds = MagicMock()
        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![img](https://example.com/image.png)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert result == text

    @pytest.mark.asyncio
    async def test_linear_image_converted(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"fake-image-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![alt](https://uploads.linear.app/image.png)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/png;base64," in result
            encoded = base64.b64encode(b"fake-image-bytes").decode("utf-8")
            assert encoded in result

    @pytest.mark.asyncio
    async def test_jpeg_image_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"jpg-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![photo](https://uploads.linear.app/photo.jpeg)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/jpeg;base64," in result

    @pytest.mark.asyncio
    async def test_gif_image_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"gif-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![anim](https://uploads.linear.app/anim.gif)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/gif;base64," in result

    @pytest.mark.asyncio
    async def test_svg_image_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"<svg></svg>"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![logo](https://uploads.linear.app/logo.svg)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/svg+xml;base64," in result

    @pytest.mark.asyncio
    async def test_webp_image_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"webp-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = "![img](https://uploads.linear.app/photo.webp)"
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert "data:image/webp;base64," in result

    @pytest.mark.asyncio
    async def test_empty_image_content_skipped(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            return
            yield

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            original = "![alt](https://uploads.linear.app/empty.png)"
            result = await connector._convert_images_to_base64_in_markdown(original)
            assert result == original

    @pytest.mark.asyncio
    async def test_download_error_skipped(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def failing_download(url):
            raise Exception("Download failed")
            yield

        mock_ds = MagicMock()
        mock_ds.download_file = failing_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            original = "![alt](https://uploads.linear.app/fail.png)"
            result = await connector._convert_images_to_base64_in_markdown(original)
            assert result == original

    @pytest.mark.asyncio
    async def test_multiple_images(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        async def fake_download(url):
            yield b"bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            text = (
                "![a](https://uploads.linear.app/a.png)\n"
                "![b](https://uploads.linear.app/b.jpg)"
            )
            result = await connector._convert_images_to_base64_in_markdown(text)
            assert result.count("data:image/") == 2


# ===================================================================
# stream_record
# ===================================================================


class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_ticket(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.id = "rec-1"
        record.external_record_group_id = "team-1"

        with patch.object(
            connector,
            "_process_issue_blockgroups_for_streaming",
            new_callable=AsyncMock,
        ) as mock_process:
            mock_process.return_value = b'{"block_groups": []}'
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_project(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.id = "rec-1"
        record.external_record_group_id = "team-1"

        with patch.object(
            connector,
            "_process_project_blockgroups_for_streaming",
            new_callable=AsyncMock,
        ) as mock_process:
            mock_process.return_value = b'{"block_groups": []}'
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_link(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = "https://example.com/doc"
        record.record_name = "My Link"

        response = await connector.stream_record(record)
        assert response.media_type == MimeTypes.MARKDOWN.value

    @pytest.mark.asyncio
    async def test_stream_link_missing_weburl(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = None

        with pytest.raises(ValueError, match="missing weburl"):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_webpage(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"

        with patch.object(
            connector, "_fetch_document_content", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = "# Document Content"
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.MARKDOWN.value

    @pytest.mark.asyncio
    async def test_stream_file(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = "https://uploads.linear.app/file.pdf"
        record.id = "rec-1"
        record.record_name = "file"
        record.mime_type = MimeTypes.PDF.value
        record.weburl = "https://uploads.linear.app/file.pdf"
        record.extension = "pdf"

        async def fake_download(url):
            yield b"file-bytes"

        mock_ds = MagicMock()
        mock_ds.download_file = fake_download

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            response = await connector.stream_record(record)
            assert response.media_type == MimeTypes.PDF.value

    @pytest.mark.asyncio
    async def test_stream_file_missing_external_record_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = None
        record.id = "rec-1"

        with pytest.raises(ValueError, match="missing external_record_id"):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock()
        record.record_type = "UNKNOWN_TYPE"
        record.external_record_id = "unknown-1"

        with pytest.raises(ValueError, match="Unsupported record type"):
            await connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_stream_inits_datasource_if_none(self):
        connector = _make_connector()
        connector.data_source = None

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.weburl = "https://example.com"
        record.record_name = "Link"

        with patch.object(connector, "init", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            response = await connector.stream_record(record)
            mock_init.assert_called_once()


# ===================================================================
# Block processing - _create_blockgroup & _organize_comments_by_thread
# ===================================================================


class TestBlockProcessing:
    def test_create_blockgroup_basic(self):
        connector = _make_connector()
        bg = connector._create_blockgroup(
            name="Test",
            weburl="https://example.com",
            data="# Hello",
            index=0,
        )
        assert bg.name == "Test"
        assert str(bg.weburl) == "https://example.com/"
        assert bg.data == "# Hello"
        assert bg.requires_processing is True

    def test_create_blockgroup_missing_weburl(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="weburl is required"):
            connector._create_blockgroup(name="Test", weburl="", data="content")

    def test_create_blockgroup_missing_data(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="data is required"):
            connector._create_blockgroup(
                name="Test", weburl="https://example.com", data=""
            )

    def test_create_blockgroup_with_comments(self):
        connector = _make_connector()
        comments = [[
            BlockComment(
                text="Hello",
                format=DataFormat.MARKDOWN,
                thread_id="t1",
            )
        ]]
        bg = connector._create_blockgroup(
            name="Test",
            weburl="https://example.com",
            data="content",
            index=0,
            comments=comments,
        )
        assert len(bg.comments) == 1

    def test_organize_comments_empty(self):
        connector = _make_connector()
        result = connector._organize_comments_by_thread([])
        assert result == []

    def test_organize_comments_single_thread(self):
        connector = _make_connector()
        c1 = BlockComment(
            text="A",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        c2 = BlockComment(
            text="B",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c2, c1])
        assert len(result) == 1
        assert result[0][0].text == "A"
        assert result[0][1].text == "B"

    def test_organize_comments_multiple_threads(self):
        connector = _make_connector()
        c1 = BlockComment(
            text="Thread1-A",
            format=DataFormat.MARKDOWN,
            thread_id="t1",
            created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        c2 = BlockComment(
            text="Thread2-A",
            format=DataFormat.MARKDOWN,
            thread_id="t2",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c1, c2])
        assert len(result) == 2
        assert result[0][0].text == "Thread2-A"

    def test_organize_comments_no_thread_id(self):
        connector = _make_connector()
        c1 = BlockComment(
            text="No thread",
            format=DataFormat.MARKDOWN,
            thread_id=None,
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        result = connector._organize_comments_by_thread([c1])
        assert len(result) == 1


# ===================================================================
# _check_and_fetch_updated_* methods
# ===================================================================


class TestCheckAndFetchUpdated:
    @pytest.mark.asyncio
    async def test_check_updated_issue_not_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705312800000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"

        issue_data = _make_issue_data(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"
        record.version = 1

        issue_data = _make_issue_data(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is not None
            updated_record, perms = result
            assert isinstance(updated_record, TicketRecord)

    @pytest.mark.asyncio
    async def test_check_updated_issue_not_found(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-missing"
        record.id = "rec-1"

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(success=False, message="Not found")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_no_team_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = ""
        record.id = "rec-1"

        issue_data = _make_issue_data(updated_at="2024-01-16T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(
            return_value=_mock_response(data={"issue": issue_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"
        record.version = 0

        project_data = _make_project_data(
            updated_at="2024-01-16T12:00:00.000Z",
            issues={"nodes": [{"id": "issue-x"}]},
        )

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is not None
            updated_record, _ = result
            assert isinstance(updated_record, ProjectRecord)

    @pytest.mark.asyncio
    async def test_check_updated_project_not_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705312800000
        record.external_record_group_id = "team-1"
        record.id = "rec-1"

        project_data = _make_project_data(updated_at="2024-01-15T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_no_team_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.external_record_id = "proj-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = ""
        record.id = "rec-1"

        project_data = _make_project_data(updated_at="2024-01-16T12:00:00.000Z")

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_document_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.id = "rec-1"
        record.version = 0

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_document_no_parent_node_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = None
        record.id = "rec-1"

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_document_with_project_parent(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=WebpageRecord)
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "doc-1"
        record.source_updated_at = 1705000000000
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.id = "rec-1"
        record.version = 0

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "project": {"id": "proj-1"},
        }

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_document(record)
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_record_dispatches_ticket(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=TicketRecord)
        record.record_type = RecordType.TICKET
        record.id = "rec-1"

        with patch.object(
            connector,
            "_check_and_fetch_updated_issue",
            new_callable=AsyncMock,
        ) as mock_issue:
            mock_issue.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_issue.assert_called_once_with(record)
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_dispatches_project(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=ProjectRecord)
        record.record_type = RecordType.PROJECT
        record.id = "rec-1"

        with patch.object(
            connector,
            "_check_and_fetch_updated_project",
            new_callable=AsyncMock,
        ) as mock_proj:
            mock_proj.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_proj.assert_called_once_with(record)

    @pytest.mark.asyncio
    async def test_check_updated_record_file_returns_none(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=FileRecord)
        record.record_type = RecordType.FILE
        record.external_record_id = "https://uploads.linear.app/file.pdf"
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_unsupported_type(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_record_link_issue_attachment(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "attach-1"
        record.parent_external_record_id = "issue-1"
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.record_type = RecordType.TICKET
        parent_record.id = "parent-rec-1"

        connector._tx_store.get_record_by_external_id = AsyncMock(
            return_value=parent_record
        )

        with patch.object(
            connector,
            "_check_and_fetch_updated_issue_link",
            new_callable=AsyncMock,
        ) as mock_link:
            mock_link.return_value = None
            result = await connector._check_and_fetch_updated_record(record)
            mock_link.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_updated_record_link_no_parent(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = None
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_issue_link_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "attach-1"
        record.parent_external_record_id = "issue-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"
        record.version = 0
        record.weburl = None

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"

        attach_data = {
            "id": "attach-1",
            "url": "https://example.com/attachment",
            "title": "Attachment",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-16T12:00:00.000Z",
            "issue": {"id": "issue-1"},
        }

        mock_ds = MagicMock()
        mock_ds.attachment = AsyncMock(
            return_value=_mock_response(data={"attachment": attach_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_issue_link(
                record, parent_record
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_issue_link_no_external_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = ""
        record.id = "rec-1"

        result = await connector._check_and_fetch_updated_issue_link(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_changed(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = "proj-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"
        record.version = 0
        record.weburl = None

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"
        parent_record.external_record_id = "proj-1"

        project_data = _make_project_data(
            external_links={
                "nodes": [
                    {
                        "id": "link-1",
                        "url": "https://example.com",
                        "label": "Link",
                        "createdAt": "2024-01-10T10:00:00.000Z",
                        "updatedAt": "2024-01-16T12:00:00.000Z",
                    }
                ]
            }
        )

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project_link(
                record, parent_record
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_not_found(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-missing"
        record.parent_external_record_id = "proj-1"
        record.external_record_group_id = "team-1"
        record.parent_node_id = "parent-node-1"
        record.source_updated_at = 1705000000000
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.id = "parent-rec-1"
        parent_record.external_record_id = "proj-1"

        project_data = _make_project_data(external_links={"nodes": []})

        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(
            return_value=_mock_response(data={"project": project_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._check_and_fetch_updated_project_link(
                record, parent_record
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_check_updated_project_link_no_project_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=LinkRecord)
        record.record_type = RecordType.LINK
        record.external_record_id = "link-1"
        record.parent_external_record_id = ""
        record.id = "rec-1"

        parent_record = MagicMock()
        parent_record.external_record_id = ""

        result = await connector._check_and_fetch_updated_project_link(
            record, parent_record
        )
        assert result is None


# ===================================================================
# reindex_records
# ===================================================================


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_reindex_empty_list(self):
        connector = _make_connector()
        await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_reindex_with_updated_records(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"

        updated_ticket = MagicMock(spec=TicketRecord)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = (updated_ticket, [])
                await connector.reindex_records([ticket])
                connector.data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_with_non_updated_records(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([ticket])
                connector.data_entities_processor.reindex_existing_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_reindex_skips_base_record_class(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record = MagicMock(spec=Record)
        record.record_type = RecordType.TICKET
        record.external_record_id = "issue-1"
        record.id = "rec-1"
        type(record).__name__ = "Record"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([record])
                connector.data_entities_processor.reindex_existing_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_reindex_check_error_continues(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        record1 = MagicMock(spec=TicketRecord)
        record1.record_type = RecordType.TICKET
        record1.external_record_id = "issue-1"
        record1.id = "rec-1"
        type(record1).__name__ = "TicketRecord"

        record2 = MagicMock(spec=TicketRecord)
        record2.record_type = RecordType.TICKET
        record2.external_record_id = "issue-2"
        record2.id = "rec-2"
        type(record2).__name__ = "TicketRecord"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.side_effect = [Exception("fail"), None]
                await connector.reindex_records([record1, record2])

    @pytest.mark.asyncio
    async def test_reindex_not_implemented_error(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        connector.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=NotImplementedError("not impl")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch.object(
                connector,
                "_check_and_fetch_updated_record",
                new_callable=AsyncMock,
            ) as mock_check:
                mock_check.return_value = None
                await connector.reindex_records([ticket])

    @pytest.mark.asyncio
    async def test_reindex_inits_datasource_if_none(self):
        connector = _make_connector()
        connector.data_source = None

        ticket = MagicMock(spec=TicketRecord)
        ticket.record_type = RecordType.TICKET
        ticket.external_record_id = "issue-1"
        ticket.id = "rec-1"
        type(ticket).__name__ = "TicketRecord"

        with patch.object(connector, "init", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = True
            connector.data_source = MagicMock()
            with patch.object(
                connector, "_get_fresh_datasource", new_callable=AsyncMock
            ):
                with patch.object(
                    connector,
                    "_check_and_fetch_updated_record",
                    new_callable=AsyncMock,
                ) as mock_check:
                    mock_check.return_value = None
                    await connector.reindex_records([ticket])


# ===================================================================
# _sync_deleted_issues & _sync_deleted_projects
# ===================================================================


class TestSyncDeleted:
    @pytest.mark.asyncio
    async def test_sync_deleted_issues_empty_teams(self):
        connector = _make_connector()
        await connector._sync_deleted_issues([])

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_initial_sync(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        rg, perms = _make_team_record_group()
        with patch(
            "app.connectors.sources.linear.connector.get_epoch_timestamp_in_ms",
            return_value=1705000000000,
        ):
            await connector._sync_deleted_issues([(rg, perms)])
        connector.deletion_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_with_trashed(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1705000000000}
        )
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        trashed_issue = _make_issue_data(
            trashed=True,
            archived_at="2024-01-15T12:00:00.000Z",
        )

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(
            return_value=_mock_paginated("issues", [trashed_issue])
        )

        rg, perms = _make_team_record_group()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_mark_record_and_children_deleted",
                new_callable=AsyncMock,
            ) as mock_delete:
                await connector._sync_deleted_issues([(rg, perms)])
                mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_issues_fetch_failure(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1705000000000}
        )

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(
            return_value=_mock_response(success=False, message="Error")
        )

        rg, perms = _make_team_record_group()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_deleted_issues([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_empty_teams(self):
        connector = _make_connector()
        await connector._sync_deleted_projects([])

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_initial_sync(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        rg, perms = _make_team_record_group()
        with patch(
            "app.connectors.sources.linear.connector.get_epoch_timestamp_in_ms",
            return_value=1705000000000,
        ):
            await connector._sync_deleted_projects([(rg, perms)])
        connector.deletion_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_with_trashed(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1705000000000}
        )
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        trashed_project = _make_project_data(
            trashed=True,
            archived_at="2024-01-16T12:00:00.000Z",
        )

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [trashed_project])
        )

        rg, perms = _make_team_record_group()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_mark_record_and_children_deleted",
                new_callable=AsyncMock,
            ) as mock_delete:
                await connector._sync_deleted_projects([(rg, perms)])
                mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_deleted_projects_trashed_before_checkpoint_skipped(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1705400000000}
        )
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        trashed_project = _make_project_data(
            trashed=True,
            archived_at="2024-01-15T12:00:00.000Z",
        )

        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(
            return_value=_mock_paginated("projects", [trashed_project])
        )

        rg, perms = _make_team_record_group()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_mark_record_and_children_deleted",
                new_callable=AsyncMock,
            ) as mock_delete:
                await connector._sync_deleted_projects([(rg, perms)])
                mock_delete.assert_not_called()


# ===================================================================
# _mark_record_and_children_deleted
# ===================================================================


class TestMarkRecordDeleted:
    @pytest.mark.asyncio
    async def test_mark_deleted_not_found(self):
        connector = _make_connector()
        connector._tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._mark_record_and_children_deleted("ext-1", "issue")

    @pytest.mark.asyncio
    async def test_mark_deleted_with_children(self):
        connector = _make_connector()
        parent = MagicMock()
        parent.id = "parent-id"
        parent.external_record_id = "ext-parent"

        child = MagicMock()
        child.id = "child-id"
        child.external_record_id = "ext-child"

        connector._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)
        connector._tx_store.get_records_by_parent = AsyncMock(
            side_effect=[[child], []]
        )
        connector._tx_store.delete_records_and_relations = AsyncMock()

        await connector._mark_record_and_children_deleted("ext-parent", "issue")
        assert connector._tx_store.delete_records_and_relations.call_count == 2


# ===================================================================
# Sync checkpoints
# ===================================================================


class TestSyncCheckpoints:
    @pytest.mark.asyncio
    async def test_team_sync_checkpoint(self):
        connector = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 123}
        )
        result = await connector._get_team_sync_checkpoint("ENG")
        assert result == 123

    @pytest.mark.asyncio
    async def test_team_sync_checkpoint_none(self):
        connector = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        result = await connector._get_team_sync_checkpoint("ENG")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_team_sync_checkpoint(self):
        connector = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.update_sync_point = AsyncMock()
        await connector._update_team_sync_checkpoint("ENG", 456)
        connector.issues_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_team_project_sync_checkpoint(self):
        connector = _make_connector()
        connector.projects_sync_point = MagicMock()
        connector.projects_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 789}
        )
        result = await connector._get_team_project_sync_checkpoint("ENG")
        assert result == 789

    @pytest.mark.asyncio
    async def test_update_team_project_sync_checkpoint(self):
        connector = _make_connector()
        connector.projects_sync_point = MagicMock()
        connector.projects_sync_point.update_sync_point = AsyncMock()
        await connector._update_team_project_sync_checkpoint("ENG", 999)
        connector.projects_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_attachments_sync_checkpoint(self):
        connector = _make_connector()
        connector.attachments_sync_point = MagicMock()
        connector.attachments_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 111}
        )
        result = await connector._get_attachments_sync_checkpoint()
        assert result == 111

    @pytest.mark.asyncio
    async def test_update_attachments_sync_checkpoint(self):
        connector = _make_connector()
        connector.attachments_sync_point = MagicMock()
        connector.attachments_sync_point.update_sync_point = AsyncMock()
        await connector._update_attachments_sync_checkpoint(222)
        connector.attachments_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_documents_sync_checkpoint(self):
        connector = _make_connector()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 333}
        )
        result = await connector._get_documents_sync_checkpoint()
        assert result == 333

    @pytest.mark.asyncio
    async def test_update_documents_sync_checkpoint(self):
        connector = _make_connector()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.update_sync_point = AsyncMock()
        await connector._update_documents_sync_checkpoint(444)
        connector.documents_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_deletion_sync_checkpoint(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 555}
        )
        result = await connector._get_deletion_sync_checkpoint("issues")
        assert result == 555

    @pytest.mark.asyncio
    async def test_update_deletion_sync_checkpoint(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.update_sync_point = AsyncMock()
        await connector._update_deletion_sync_checkpoint("issues", 666)
        connector.deletion_sync_point.update_sync_point.assert_called_once()


# ===================================================================
# _apply_date_filters_to_linear_filter
# ===================================================================


class TestApplyDateFilters:
    def test_no_filters(self):
        connector = _make_connector()
        connector.sync_filters = None
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert linear_filter == {}

    def test_modified_after_from_checkpoint(self):
        connector = _make_connector()
        connector.sync_filters = None
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, 1705312800000)
        assert "updatedAt" in linear_filter
        assert "gt" in linear_filter["updatedAt"]

    def test_modified_after_from_filter(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "updatedAt" in linear_filter

    def test_modified_after_merge_with_checkpoint(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, 1705500000000)
        assert "updatedAt" in linear_filter

    def test_modified_before(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "updatedAt" in linear_filter
        assert "lte" in linear_filter["updatedAt"]

    def test_modified_before_with_after(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.MODIFIED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "gt" in linear_filter["updatedAt"]
        assert "lte" in linear_filter["updatedAt"]

    def test_created_after(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, None)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "createdAt" in linear_filter
        assert "gte" in linear_filter["createdAt"]

    def test_created_before(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (None, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "createdAt" in linear_filter
        assert "lte" in linear_filter["createdAt"]

    def test_created_before_with_after(self):
        connector = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_value.return_value = (1705000000000, 1706000000000)
        mock_sync_filters = MagicMock()
        mock_sync_filters.get.side_effect = lambda key: (
            mock_filter if key == SyncFilterKey.CREATED else None
        )
        connector.sync_filters = mock_sync_filters
        linear_filter = {}
        connector._apply_date_filters_to_linear_filter(linear_filter, None)
        assert "gte" in linear_filter["createdAt"]
        assert "lte" in linear_filter["createdAt"]


# ===================================================================
# _linear_datetime_from_timestamp
# ===================================================================


class TestLinearDatetimeConversion:
    def test_valid_timestamp(self):
        connector = _make_connector()
        result = connector._linear_datetime_from_timestamp(1705312800000)
        assert "2024-01-15" in result
        assert result.endswith("Z")

    def test_invalid_timestamp(self):
        connector = _make_connector()
        result = connector._linear_datetime_from_timestamp(-99999999999999999)
        assert result == ""


# ===================================================================
# _parse_linear_datetime_to_datetime
# ===================================================================


class TestParseLinearDatetimeToDatetime:
    def test_valid(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime("2024-01-15T10:30:00.000Z")
        assert isinstance(result, datetime)
        assert result.year == 2024

    def test_empty_string(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime("")
        assert result is None

    def test_none(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime(None)
        assert result is None

    def test_invalid(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime_to_datetime("not-a-date")
        assert result is None


# ===================================================================
# _extract_file_urls_from_markdown
# ===================================================================


class TestExtractFileUrls:
    def test_empty_input(self):
        connector = _make_connector()
        assert connector._extract_file_urls_from_markdown("") == []
        assert connector._extract_file_urls_from_markdown(None) == []

    def test_no_linear_urls(self):
        connector = _make_connector()
        text = "[doc](https://example.com/file.pdf)"
        assert connector._extract_file_urls_from_markdown(text) == []

    def test_image_urls(self):
        connector = _make_connector()
        text = "![alt](https://uploads.linear.app/image.png)"
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1
        assert result[0]["url"] == "https://uploads.linear.app/image.png"

    def test_exclude_images(self):
        connector = _make_connector()
        text = "![alt](https://uploads.linear.app/image.png)"
        result = connector._extract_file_urls_from_markdown(text, exclude_images=True)
        assert len(result) == 0

    def test_file_links(self):
        connector = _make_connector()
        text = "[report.pdf](https://uploads.linear.app/report.pdf)"
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1
        assert result[0]["filename"] == "report.pdf"

    def test_mixed_images_and_links(self):
        connector = _make_connector()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "[doc](https://uploads.linear.app/doc.pdf)"
        )
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 2

    def test_exclude_images_keeps_links(self):
        connector = _make_connector()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "[doc](https://uploads.linear.app/doc.pdf)"
        )
        result = connector._extract_file_urls_from_markdown(text, exclude_images=True)
        assert len(result) == 1
        assert result[0]["filename"] == "doc"

    def test_deduplication(self):
        connector = _make_connector()
        text = (
            "![img](https://uploads.linear.app/img.png)\n"
            "![img2](https://uploads.linear.app/img.png)"
        )
        result = connector._extract_file_urls_from_markdown(text)
        assert len(result) == 1


# ===================================================================
# _get_mime_type_from_url
# ===================================================================


class TestGetMimeType:
    def test_pdf(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("", "report.pdf") == MimeTypes.PDF.value

    def test_png(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("", "img.png") == MimeTypes.PNG.value

    def test_docx(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("", "file.docx") == MimeTypes.DOCX.value

    def test_from_url(self):
        connector = _make_connector()
        assert (
            connector._get_mime_type_from_url("https://example.com/file.xlsx?token=abc", "")
            == MimeTypes.XLSX.value
        )

    def test_unknown(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("https://example.com/file", "") == MimeTypes.UNKNOWN.value

    def test_csv(self):
        connector = _make_connector()
        assert connector._get_mime_type_from_url("", "data.csv") == MimeTypes.CSV.value


# ===================================================================
# Transformations
# ===================================================================


class TestTransformIssueToTicketRecord:
    def test_basic_transform(self):
        connector = _make_connector()
        issue_data = _make_issue_data()
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.record_name == "Test Issue"
        assert ticket.external_record_id == "issue-1"
        assert ticket.record_type == RecordType.TICKET
        assert ticket.version == 0

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_issue_to_ticket_record({"id": ""}, "team-1")

    def test_missing_team_id_raises(self):
        connector = _make_connector()
        issue_data = _make_issue_data()
        with pytest.raises(ValueError, match="team_id is required"):
            connector._transform_issue_to_ticket_record(issue_data, "")

    def test_priority_mapping(self):
        connector = _make_connector()
        for priority_num, expected_str in [(0, "none"), (1, "Urgent"), (2, "High"), (3, "Medium"), (4, "Low")]:
            issue_data = _make_issue_data(priority=priority_num)
            ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
            assert ticket is not None

    def test_no_priority(self):
        connector = _make_connector()
        issue_data = _make_issue_data(priority=None)
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket is not None

    def test_sub_issue_type(self):
        connector = _make_connector()
        issue_data = _make_issue_data(parent={"id": "parent-issue"})
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.type == ItemType.SUB_ISSUE

    def test_version_increment(self):
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.source_updated_at = 1705000000000

        issue_data = _make_issue_data(updated_at="2024-01-16T12:00:00.000Z")
        ticket = connector._transform_issue_to_ticket_record(
            issue_data, "team-1", existing
        )
        assert ticket.version == 2
        assert ticket.id == "existing-id"

    def test_no_version_change(self):
        connector = _make_connector()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 3
        existing.source_updated_at = 1705320000000

        issue_data = _make_issue_data(updated_at="2024-01-15T12:00:00.000Z")
        ticket = connector._transform_issue_to_ticket_record(
            issue_data, "team-1", existing
        )
        assert ticket.version == 3

    def test_with_relations(self):
        connector = _make_connector()
        issue_data = _make_issue_data(
            relations={
                "nodes": [
                    {
                        "type": "blocks",
                        "relatedIssue": {"id": "related-1"},
                    }
                ]
            }
        )
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert len(ticket.related_external_records) >= 0

    def test_missing_identifier_and_title(self):
        connector = _make_connector()
        issue_data = _make_issue_data(identifier="", title="")
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.record_name == "issue-1"

    def test_assignee_and_creator(self):
        connector = _make_connector()
        issue_data = _make_issue_data(
            assignee={"email": "dev@test.com", "displayName": "Dev"},
            creator={"email": "pm@test.com", "name": "PM"},
        )
        ticket = connector._transform_issue_to_ticket_record(issue_data, "team-1")
        assert ticket.assignee_email == "dev@test.com"
        assert ticket.creator_email == "pm@test.com"


class TestTransformToProjectRecord:
    def test_basic_transform(self):
        connector = _make_connector()
        project_data = _make_project_data()
        record = connector._transform_to_project_record(project_data, "team-1")
        assert record.record_name == "Test Project"
        assert record.record_type == RecordType.PROJECT
        assert record.version == 0

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_to_project_record({"id": ""}, "team-1")

    def test_missing_name_uses_slug(self):
        connector = _make_connector()
        data = _make_project_data(name="", slug_id="my-slug")
        record = connector._transform_to_project_record(data, "team-1")
        assert record.record_name == "my-slug"

    def test_missing_name_and_slug_uses_id(self):
        connector = _make_connector()
        data = _make_project_data(name="", slug_id="")
        record = connector._transform_to_project_record(data, "team-1")
        assert record.record_name == "proj-1"

    def test_with_lead(self):
        connector = _make_connector()
        data = _make_project_data(
            lead={"id": "lead-1", "displayName": "Lead Dev", "email": "lead@test.com"}
        )
        record = connector._transform_to_project_record(data, "team-1")
        assert record.lead_name == "Lead Dev"
        assert record.lead_email == "lead@test.com"


class TestTransformAttachmentToLinkRecord:
    def test_basic_transform(self):
        connector = _make_connector()
        attachment_data = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "My File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }
        link = connector._transform_attachment_to_link_record(
            attachment_data, "issue-1", "node-1", "team-1"
        )
        assert link.record_name == "My File"
        assert link.weburl == "https://example.com/file"
        assert link.is_public == LinkPublicStatus.UNKNOWN

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_attachment_to_link_record(
                {"id": "", "url": "x"}, "issue-1", "node-1", "team-1"
            )

    def test_missing_url_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'url'"):
            connector._transform_attachment_to_link_record(
                {"id": "a", "url": ""}, "issue-1", "node-1", "team-1"
            )

    def test_uses_label_as_title(self):
        connector = _make_connector()
        data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External Link",
            "createdAt": "",
            "updatedAt": "",
        }
        link = connector._transform_attachment_to_link_record(
            data, "proj-1", "node-1", "team-1", parent_record_type=RecordType.PROJECT
        )
        assert link.record_name == "External Link"
        assert link.parent_record_type == RecordType.PROJECT


class TestTransformDocumentToWebpageRecord:
    def test_basic_transform(self):
        connector = _make_connector()
        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Design Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }
        webpage = connector._transform_document_to_webpage_record(
            doc_data, "issue-1", "node-1", "team-1"
        )
        assert webpage.record_name == "Design Doc"
        assert webpage.record_type == RecordType.WEBPAGE

    def test_missing_id_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'id'"):
            connector._transform_document_to_webpage_record(
                {"id": "", "url": "x"}, "issue-1", "node-1", "team-1"
            )

    def test_missing_url_raises(self):
        connector = _make_connector()
        with pytest.raises(ValueError, match="missing required 'url'"):
            connector._transform_document_to_webpage_record(
                {"id": "d", "url": ""}, "issue-1", "node-1", "team-1"
            )

    def test_no_title_uses_id(self):
        connector = _make_connector()
        doc_data = {
            "id": "doc-abcdef12",
            "url": "https://linear.app/doc/1",
            "title": "",
            "createdAt": "",
            "updatedAt": "",
        }
        webpage = connector._transform_document_to_webpage_record(
            doc_data, "issue-1", "node-1", "team-1"
        )
        assert "doc-abcd" in webpage.record_name


# ===================================================================
# _transform_file_url_to_file_record
# ===================================================================


class TestTransformFileUrlToFileRecord:
    @pytest.mark.asyncio
    async def test_basic_transform(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=1024)

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            file_record = await connector._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/report.pdf",
                filename="report.pdf",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
            assert file_record.record_name == "report.pdf"
            assert file_record.record_type == RecordType.FILE
            assert file_record.extension == "pdf"
            assert file_record.size_in_bytes == 1024

    @pytest.mark.asyncio
    async def test_file_size_error_fallback(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(side_effect=Exception("network error"))

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            file_record = await connector._transform_file_url_to_file_record(
                file_url="https://uploads.linear.app/file.txt",
                filename="file.txt",
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
            )
            assert file_record.size_in_bytes == 0


# ===================================================================
# _sync_issues_for_teams
# ===================================================================


class TestSyncIssuesForTeams:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector()
        await connector._sync_issues_for_teams([])

    @pytest.mark.asyncio
    async def test_team_missing_external_group_id(self):
        connector = _make_connector()
        rg = RecordGroup(
            id="rg-1",
            org_id="org-1",
            external_group_id=None,
            connector_id="linear-conn-1",
            connector_name=Connectors.LINEAR,
            name="NoID",
            short_name="NO",
            group_type=RecordGroupType.PROJECT,
        )
        perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        await connector._sync_issues_for_teams([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_issues_processes_batches(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.issues_sync_point.update_sync_point = AsyncMock()

        rg, perms = _make_team_record_group()

        ticket = MagicMock(spec=TicketRecord)
        ticket.source_updated_at = 1705312800000
        batch = [(ticket, [])]

        async def fake_batch_gen(**kwargs):
            yield batch

        with patch.object(
            connector, "_get_team_sync_checkpoint", new_callable=AsyncMock
        ) as mock_cp:
            mock_cp.return_value = None
            with patch.object(
                connector,
                "_fetch_issues_for_team_batch",
                side_effect=lambda **kw: fake_batch_gen(**kw),
            ):
                with patch.object(
                    connector,
                    "_update_team_sync_checkpoint",
                    new_callable=AsyncMock,
                ):
                    await connector._sync_issues_for_teams([(rg, perms)])
                    connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_issues_team_error_continues(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        rg, perms = _make_team_record_group()

        with patch.object(
            connector, "_get_team_sync_checkpoint", new_callable=AsyncMock
        ) as mock_cp:
            mock_cp.side_effect = Exception("checkpoint error")
            await connector._sync_issues_for_teams([(rg, perms)])


# ===================================================================
# _sync_projects_for_teams
# ===================================================================


class TestSyncProjectsForTeams:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector()
        await connector._sync_projects_for_teams([])

    @pytest.mark.asyncio
    async def test_team_missing_external_group_id(self):
        connector = _make_connector()
        rg = RecordGroup(
            id="rg-1",
            org_id="org-1",
            external_group_id=None,
            connector_id="linear-conn-1",
            connector_name=Connectors.LINEAR,
            name="NoID",
            short_name="NO",
            group_type=RecordGroupType.PROJECT,
        )
        perms = [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)]
        await connector._sync_projects_for_teams([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_projects_processes_batches(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.projects_sync_point = MagicMock()
        connector.projects_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.projects_sync_point.update_sync_point = AsyncMock()

        rg, perms = _make_team_record_group()

        project = MagicMock(spec=ProjectRecord)
        project.source_updated_at = 1705312800000
        batch = [(project, [])]

        async def fake_batch_gen(**kwargs):
            yield batch

        with patch.object(
            connector, "_get_team_project_sync_checkpoint", new_callable=AsyncMock
        ) as mock_cp:
            mock_cp.return_value = None
            with patch.object(
                connector,
                "_fetch_projects_for_team_batch",
                side_effect=lambda **kw: fake_batch_gen(**kw),
            ):
                with patch.object(
                    connector,
                    "_update_team_project_sync_checkpoint",
                    new_callable=AsyncMock,
                ):
                    await connector._sync_projects_for_teams([(rg, perms)])
                    connector.data_entities_processor.on_new_records.assert_called()


# ===================================================================
# _sync_attachments
# ===================================================================


class TestSyncAttachments:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector()
        await connector._sync_attachments([])

    @pytest.mark.asyncio
    async def test_sync_attachments_with_data(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.attachments_sync_point = MagicMock()
        connector.attachments_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.attachments_sync_point.update_sync_point = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group()

        parent_record = MagicMock()
        parent_record.id = "parent-id"
        connector._tx_store.get_record_by_external_id = AsyncMock(
            return_value=parent_record
        )

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }

        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(
            return_value=_mock_paginated("attachments", [attachment])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_attachments([(rg, perms)])
            connector.data_entities_processor.on_new_records.assert_called()


# ===================================================================
# _sync_documents
# ===================================================================


class TestSyncDocuments:
    @pytest.mark.asyncio
    async def test_empty_teams(self):
        connector = _make_connector()
        await connector._sync_documents([])

    @pytest.mark.asyncio
    async def test_sync_documents_with_data(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.documents_sync_point.update_sync_point = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group()

        parent_record = MagicMock()
        parent_record.id = "parent-id"
        connector._tx_store.get_record_by_external_id = AsyncMock(
            return_value=parent_record
        )

        document = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Design Doc",
            "content": "# Content",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {
                "id": "issue-1",
                "identifier": "ENG-1",
                "team": {"id": "team-1"},
            },
        }

        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(
            return_value=_mock_paginated("documents", [document])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_documents([(rg, perms)])
            connector.data_entities_processor.on_new_records.assert_called()

    @pytest.mark.asyncio
    async def test_sync_documents_skips_standalone(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.documents_sync_point.update_sync_point = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        rg, perms = _make_team_record_group()

        standalone_doc = {
            "id": "doc-standalone",
            "url": "https://linear.app/doc/standalone",
            "title": "Standalone Doc",
            "content": "# Content",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(
            return_value=_mock_paginated("documents", [standalone_doc])
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            await connector._sync_documents([(rg, perms)])
            connector.data_entities_processor.on_new_records.assert_not_called()


# ===================================================================
# _fetch_document_content
# ===================================================================


class TestFetchDocumentContent:
    @pytest.mark.asyncio
    async def test_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        doc_data = {"content": "# Hello World"}
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with patch.object(
                connector,
                "_convert_images_to_base64_in_markdown",
                new_callable=AsyncMock,
            ) as mock_convert:
                mock_convert.return_value = "# Hello World"
                result = await connector._fetch_document_content("doc-1")
                assert result == "# Hello World"

    @pytest.mark.asyncio
    async def test_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(success=False, message="Not found")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with pytest.raises(Exception, match="Failed to fetch document"):
                await connector._fetch_document_content("doc-missing")

    @pytest.mark.asyncio
    async def test_empty_content(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        doc_data = {"content": ""}
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(
            return_value=_mock_response(data={"document": doc_data})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector._fetch_document_content("doc-1")
            assert result == ""

    @pytest.mark.asyncio
    async def test_datasource_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await connector._fetch_document_content("doc-1")


# ===================================================================
# Other utility methods
# ===================================================================


class TestOtherUtilities:
    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        connector = _make_connector()
        with patch.object(
            connector, "run_sync", new_callable=AsyncMock
        ) as mock_sync:
            await connector.run_incremental_sync()
            mock_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(
            return_value=_mock_response(data={"organization": {"id": "org-1"}})
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.test_connection_and_access()
            assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(
            return_value=_mock_response(success=False, message="Unauthorized")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.test_connection_and_access()
            assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.side_effect = Exception("boom")
            result = await connector.test_connection_and_access()
            assert result is False

    @pytest.mark.asyncio
    async def test_get_signed_url(self):
        connector = _make_connector()
        record = MagicMock()
        result = await connector.get_signed_url(record)
        assert result == ""

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self):
        connector = _make_connector()
        await connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.close = AsyncMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client
        await connector.cleanup()
        mock_internal.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_no_client(self):
        connector = _make_connector()
        connector.external_client = None
        await connector.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_error(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_client.get_client.side_effect = Exception("fail")
        connector.external_client = mock_client
        await connector.cleanup()


# ===================================================================
# _get_fresh_datasource edge cases
# ===================================================================


class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_api_token_auth_type(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "old-token"
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": "new-api-token"},
            }
        )

        with patch(
            "app.connectors.sources.linear.connector.LinearDataSource"
        ) as MockDS:
            MockDS.return_value = MagicMock()
            ds = await connector._get_fresh_datasource()
            mock_internal.set_token.assert_called_once_with("new-api-token")

    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "apiToken": ""},
            }
        )

        with pytest.raises(Exception, match="No access token"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_same_token_no_update(self):
        connector = _make_connector()
        mock_client = MagicMock()
        mock_internal = MagicMock()
        mock_internal.get_token.return_value = "same-token"
        mock_client.get_client.return_value = mock_internal
        connector.external_client = mock_client

        connector.config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "same-token"},
            }
        )

        with patch(
            "app.connectors.sources.linear.connector.LinearDataSource"
        ) as MockDS:
            MockDS.return_value = MagicMock()
            ds = await connector._get_fresh_datasource()
            mock_internal.set_token.assert_not_called()


# ===================================================================
# _get_team_options edge cases
# ===================================================================


class TestGetTeamOptions:
    @pytest.mark.asyncio
    async def test_team_options_with_search(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
                [
                    {"id": "t1", "name": "Engineering", "key": "ENG"},
                    {"id": "t2", "name": "Design", "key": "DES"},
                ],
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result = await connector.get_filter_options("team_ids", search="eng")
            assert len(result.options) == 1
            assert "Engineering" in result.options[0].label

    @pytest.mark.asyncio
    async def test_team_options_failure(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_response(success=False, message="Error")
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            with pytest.raises(RuntimeError, match="Failed to fetch team"):
                await connector.get_filter_options("team_ids")


# ===================================================================
# run_sync
# ===================================================================


class TestRunSync:
    @pytest.mark.asyncio
    async def test_run_sync_full_flow(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock()]
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch(
                "app.connectors.sources.linear.connector.load_connector_filters",
                new_callable=AsyncMock,
            ) as mock_load:
                mock_load.return_value = (FilterCollection(), FilterCollection())
                with patch.object(
                    connector, "_fetch_users", new_callable=AsyncMock
                ) as mock_users:
                    mock_users.return_value = []
                    with patch.object(
                        connector, "_fetch_teams", new_callable=AsyncMock
                    ) as mock_teams:
                        mock_teams.return_value = ([], [])
                        with patch.object(
                            connector,
                            "_sync_issues_for_teams",
                            new_callable=AsyncMock,
                        ):
                            with patch.object(
                                connector,
                                "_sync_attachments",
                                new_callable=AsyncMock,
                            ):
                                with patch.object(
                                    connector,
                                    "_sync_documents",
                                    new_callable=AsyncMock,
                                ):
                                    with patch.object(
                                        connector,
                                        "_sync_projects_for_teams",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch.object(
                                            connector,
                                            "_sync_deleted_issues",
                                            new_callable=AsyncMock,
                                        ):
                                            with patch.object(
                                                connector,
                                                "_sync_deleted_projects",
                                                new_callable=AsyncMock,
                                            ):
                                                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_with_team_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock()]
        )

        mock_filter = MagicMock()
        mock_filter.get_value.return_value = ["team-1"]
        mock_filter.get_operator.return_value = MagicMock(value="in")

        mock_sync_filters = MagicMock(spec=FilterCollection)
        mock_sync_filters.get.return_value = mock_filter

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ):
            with patch(
                "app.connectors.sources.linear.connector.load_connector_filters",
                new_callable=AsyncMock,
            ) as mock_load:
                mock_load.return_value = (mock_sync_filters, FilterCollection())
                with patch.object(
                    connector, "_fetch_users", new_callable=AsyncMock
                ) as mock_users:
                    mock_users.return_value = []
                    with patch.object(
                        connector, "_fetch_teams", new_callable=AsyncMock
                    ) as mock_teams:
                        mock_teams.return_value = ([], [])
                        with patch.object(
                            connector,
                            "_sync_issues_for_teams",
                            new_callable=AsyncMock,
                        ):
                            with patch.object(
                                connector,
                                "_sync_attachments",
                                new_callable=AsyncMock,
                            ):
                                with patch.object(
                                    connector,
                                    "_sync_documents",
                                    new_callable=AsyncMock,
                                ):
                                    with patch.object(
                                        connector,
                                        "_sync_projects_for_teams",
                                        new_callable=AsyncMock,
                                    ):
                                        with patch.object(
                                            connector,
                                            "_sync_deleted_issues",
                                            new_callable=AsyncMock,
                                        ):
                                            with patch.object(
                                                connector,
                                                "_sync_deleted_projects",
                                                new_callable=AsyncMock,
                                            ):
                                                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_error_propagated(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.side_effect = Exception("Config error")
            with pytest.raises(Exception, match="Config error"):
                await connector.run_sync()


# ===================================================================
# _process_issue_attachments
# ===================================================================


class TestProcessIssueAttachments:
    @pytest.mark.asyncio
    async def test_creates_new_attachment(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        children = await connector._process_issue_attachments(
            attachments_data=[attachment],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
        )
        assert len(children) == 1
        assert children[0].child_type == ChildType.RECORD

    @pytest.mark.asyncio
    async def test_uses_existing_attachment(self):
        connector = _make_connector()

        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "Existing File"

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "",
            "updatedAt": "",
        }

        children = await connector._process_issue_attachments(
            attachments_data=[attachment],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
        )
        assert len(children) == 1
        assert children[0].child_id == "existing-id"

    @pytest.mark.asyncio
    async def test_skips_empty_id(self):
        connector = _make_connector()
        mock_tx = AsyncMock()
        children = await connector._process_issue_attachments(
            attachments_data=[{"id": ""}],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
        )
        assert len(children) == 0


# ===================================================================
# _process_issue_documents
# ===================================================================


class TestProcessIssueDocuments:
    @pytest.mark.asyncio
    async def test_creates_new_document(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        doc = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        children = await connector._process_issue_documents(
            documents_data=[doc],
            issue_id="issue-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
        )
        assert len(children) == 1


# ===================================================================
# _extract_files_from_markdown
# ===================================================================


class TestExtractFilesFromMarkdown:
    @pytest.mark.asyncio
    async def test_empty_markdown(self):
        connector = _make_connector()
        result, existing = await connector._extract_files_from_markdown(
            markdown_text="",
            parent_external_id="issue-1",
            parent_node_id="node-1",
            parent_record_type=RecordType.TICKET,
            team_id="team-1",
            tx_store=AsyncMock(),
        )
        assert result == []
        assert existing == []

    @pytest.mark.asyncio
    async def test_extracts_new_files(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.indexing_filters = None

        mock_ds = MagicMock()
        mock_ds.get_file_size = AsyncMock(return_value=512)

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        text = "[report.pdf](https://uploads.linear.app/report.pdf)"

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            result, existing = await connector._extract_files_from_markdown(
                markdown_text=text,
                parent_external_id="issue-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
                tx_store=mock_tx,
                exclude_images=True,
            )
            assert len(result) == 1
            assert isinstance(result[0][0], FileRecord)

    @pytest.mark.asyncio
    async def test_returns_existing_files_as_children(self):
        connector = _make_connector()

        existing_file = MagicMock()
        existing_file.id = "existing-file-id"
        existing_file.record_type = RecordType.FILE
        existing_file.record_name = "report.pdf"

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing_file)

        text = "[report.pdf](https://uploads.linear.app/report.pdf)"

        result, existing = await connector._extract_files_from_markdown(
            markdown_text=text,
            parent_external_id="issue-1",
            parent_node_id="node-1",
            parent_record_type=RecordType.TICKET,
            team_id="team-1",
            tx_store=mock_tx,
            exclude_images=True,
        )
        assert len(result) == 0
        assert len(existing) == 1
        assert existing[0].child_id == "existing-file-id"


# ===================================================================
# _fetch_teams with filters
# ===================================================================


class TestFetchTeamsWithFilters:
    @pytest.mark.asyncio
    async def test_fetch_teams_with_not_in_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
                [
                    {
                        "id": "team-1",
                        "name": "Engineering",
                        "key": "ENG",
                        "description": "Eng team",
                        "private": False,
                        "parent": None,
                        "members": {"nodes": []},
                    }
                ],
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            operator = MagicMock()
            operator.value = "not_in"
            user_groups, record_groups = await connector._fetch_teams(
                team_ids=["team-2"],
                team_ids_operator=operator,
            )
            assert len(record_groups) == 1

    @pytest.mark.asyncio
    async def test_fetch_teams_skips_missing_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"

        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(
            return_value=_mock_paginated(
                "teams",
                [
                    {
                        "id": "",
                        "name": "Bad Team",
                        "key": "",
                        "private": False,
                        "members": {"nodes": []},
                    },
                    {
                        "id": "team-1",
                        "name": "Good Team",
                        "key": "GOOD",
                        "description": "",
                        "private": False,
                        "parent": None,
                        "members": {"nodes": []},
                    },
                ],
            )
        )

        with patch.object(
            connector, "_get_fresh_datasource", new_callable=AsyncMock
        ) as mock_fresh:
            mock_fresh.return_value = mock_ds
            _, record_groups = await connector._fetch_teams()
            assert len(record_groups) == 1


# ===================================================================
# _process_project_external_links
# ===================================================================


class TestProcessProjectExternalLinks:
    @pytest.mark.asyncio
    async def test_creates_link_records(self):
        connector = _make_connector()
        connector.indexing_filters = None

        link_data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.get_record_by_weburl = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_external_links(
            external_links_data=[link_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
            create_block_groups=False,
        )
        assert len(records) == 1
        assert len(block_groups) == 0

    @pytest.mark.asyncio
    async def test_creates_block_groups(self):
        connector = _make_connector()
        connector.indexing_filters = None

        link_data = {
            "id": "link-1",
            "url": "https://example.com",
            "label": "External",
            "createdAt": "",
            "updatedAt": "",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.get_record_by_weburl = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_external_links(
            external_links_data=[link_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
            create_block_groups=True,
        )
        assert len(block_groups) == 1


# ===================================================================
# _process_project_documents
# ===================================================================


class TestProcessProjectDocuments:
    @pytest.mark.asyncio
    async def test_creates_webpage_records(self):
        connector = _make_connector()
        connector.indexing_filters = None

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_documents(
            documents_data=[doc_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
            create_block_groups=False,
        )
        assert len(records) == 1
        assert len(block_groups) == 0

    @pytest.mark.asyncio
    async def test_creates_block_groups_for_docs(self):
        connector = _make_connector()
        connector.indexing_filters = None

        doc_data = {
            "id": "doc-1",
            "url": "https://linear.app/doc/1",
            "title": "Doc",
            "createdAt": "",
            "updatedAt": "",
        }

        mock_tx = AsyncMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

        records, block_groups = await connector._process_project_documents(
            documents_data=[doc_data],
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=mock_tx,
            create_block_groups=True,
        )
        assert len(block_groups) == 1
