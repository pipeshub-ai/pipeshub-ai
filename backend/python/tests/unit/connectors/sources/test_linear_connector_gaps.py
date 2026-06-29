"""Targeted coverage tests for remaining gaps in linear.connector."""

from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import IndexingFilterKey, SyncFilterKey
from app.connectors.sources.linear.connector import LinearConnector
from app.models.blocks import BlockGroup, BlocksContainer, ChildRecord, ChildType, GroupSubType, GroupType
from app.models.entities import (
    AppUser,
    FileRecord,
    LinkPublicStatus,
    LinkRecord,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


def _make_connector() -> LinearConnector:
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-linear-gaps"
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dsp = MagicMock()
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    tx.get_records_by_parent = AsyncMock(return_value=[])
    tx.get_record_by_weburl = AsyncMock(return_value=None)

    class _Tx:
        async def __aenter__(self):
            return tx
        async def __aexit__(self, *_):
            return None

    dsp.transaction = MagicMock(return_value=_Tx())
    cs = AsyncMock()
    conn = LinearConnector(logger, dep, dsp, cs, "linear-gap", "team", "u1")
    conn._tx_store = tx
    return conn


def _teams_resp(teams, *, has_next=False, end_cursor=None):
    r = MagicMock()
    r.success = True
    r.data = {
        "teams": {
            "nodes": teams,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        },
    }
    return r


def _users_resp(users, *, has_next=False, end_cursor=None):
    r = MagicMock()
    r.success = True
    r.data = {
        "users": {
            "nodes": users,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        },
    }
    return r


def _issues_resp(issues, *, has_next=False, end_cursor=None):
    r = MagicMock()
    r.success = True
    r.data = {
        "issues": {
            "nodes": issues,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        },
    }
    return r


def _attachments_resp(attachments, *, has_next=False, end_cursor=None):
    r = MagicMock()
    r.success = True
    r.data = {
        "attachments": {
            "nodes": attachments,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        },
    }
    return r


def _projects_resp(projects, *, has_next=False, end_cursor=None):
    r = MagicMock()
    r.success = True
    r.data = {
        "projects": {
            "nodes": projects,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        },
    }
    return r


def _documents_resp(documents, *, has_next=False, end_cursor=None):
    r = MagicMock()
    r.success = True
    r.data = {
        "documents": {
            "nodes": documents,
            "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        },
    }
    return r


def _project_record(**overrides) -> ProjectRecord:
    defaults = {
        "id": str(uuid4()),
        "org_id": "org-linear-gaps",
        "record_name": "Alpha",
        "record_type": RecordType.PROJECT,
        "external_record_id": "proj-1",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.LINEAR,
        "connector_id": "linear-gap",
        "external_record_group_id": "team-1",
        "record_group_type": RecordGroupType.PROJECT,
        "source_updated_at": 1700000000000,
    }
    defaults.update(overrides)
    return ProjectRecord(**defaults)


def _make_team_rg(team_id="team-1"):
    rg = RecordGroup(
        id="rg-1",
        org_id="org-linear-gaps",
        external_group_id=team_id,
        connector_id="linear-gap",
        connector_name=Connectors.LINEAR,
        name="Eng",
        short_name="ENG",
        group_type=RecordGroupType.PROJECT,
    )
    perm = Permission(entity_type=EntityType.ORG, type=PermissionType.READ)
    return rg, [perm]


def _make_issue_payload(**overrides):
    issue = {
        "id": "iss-1",
        "identifier": "ENG-1",
        "title": "Bug",
        "url": "https://linear.app/org/issue/ENG-1",
        "description": "Issue body",
        "priority": 2,
        "state": {"name": "Todo", "type": "unstarted"},
        "assignee": None,
        "creator": {"id": "u1", "email": "a@x.com", "name": "A"},
        "parent": None,
        "labels": {"nodes": []},
        "relations": {"nodes": []},
        "comments": {"nodes": []},
        "attachments": {"nodes": []},
        "documents": {"nodes": []},
        "createdAt": "2024-01-01T10:00:00.000Z",
        "updatedAt": "2024-06-01T10:00:00.000Z",
    }
    issue.update(overrides)
    return issue


def _ticket_record(**overrides) -> TicketRecord:
    defaults = {
        "id": str(uuid4()),
        "org_id": "org-linear-gaps",
        "record_name": "ENG-1",
        "record_type": RecordType.TICKET,
        "external_record_id": "iss-1",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.LINEAR,
        "connector_id": "linear-gap",
        "external_record_group_id": "team-1",
        "record_group_type": RecordGroupType.PROJECT,
        "source_updated_at": 1704067200000,
    }
    defaults.update(overrides)
    return TicketRecord(**defaults)


def _file_record(**overrides) -> FileRecord:
    defaults = {
        "id": str(uuid4()),
        "org_id": "org-linear-gaps",
        "record_name": "spec.pdf",
        "record_type": RecordType.FILE,
        "external_record_id": "file-1",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.LINEAR,
        "connector_id": "linear-gap",
        "external_record_group_id": "team-1",
        "is_file": True,
    }
    defaults.update(overrides)
    return FileRecord(**defaults)


class TestFilterOptionsAndUsers:
    @pytest.mark.asyncio
    async def test_team_options_calls_init_when_datasource_missing(self):
        conn = _make_connector()
        conn.data_source = None
        conn.init = AsyncMock(return_value=True)
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_teams_resp([
            {"id": "t1", "name": "Eng", "key": "ENG"},
        ]))
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn.get_filter_options("team_ids")
        conn.init.assert_awaited_once()
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_fetch_all_users_stops_on_empty_page(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(side_effect=[
            _users_resp([{"id": "u1", "email": "a@x.com", "active": True, "displayName": "A"}]),
            _users_resp([]),
        ])
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            users = await conn._fetch_users()
        assert len(users) == 1

    @pytest.mark.asyncio
    async def test_fetch_users_stops_when_no_end_cursor(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(return_value=_users_resp(
            [{"id": "u1", "email": "a@x.com", "active": True, "displayName": "A"}],
            has_next=True,
            end_cursor=None,
        ))
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            users = await conn._fetch_users()
        assert len(users) == 1


class TestIssueBatchCommentFiles:
    @pytest.mark.asyncio
    async def test_fetch_issues_batch_extracts_comment_attachments(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"

        issue = {
            "id": "iss-1",
            "identifier": "ENG-1",
            "title": "Bug",
            "url": "https://linear.app/org/issue/ENG-1",
            "description": "",
            "priority": 2,
            "state": {"name": "Todo", "type": "unstarted"},
            "assignee": None,
            "creator": {"id": "u1", "email": "a@x.com", "name": "A"},
            "parent": None,
            "labels": {"nodes": []},
            "relations": {"nodes": []},
            "comments": {
                "nodes": [{
                    "id": "c1",
                    "body": "See [file.pdf](https://uploads.linear.app/file.pdf)",
                    "createdAt": "2024-06-01T10:00:00.000Z",
                    "updatedAt": "2024-06-01T10:00:00.000Z",
                    "url": "https://linear.app/org/issue/ENG-1#comment-c1",
                }],
            },
            "createdAt": "2024-01-01T10:00:00.000Z",
            "updatedAt": "2024-06-01T10:00:00.000Z",
        }

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_issues_resp([issue], has_next=False))

        file_record = MagicMock()
        file_record.id = "file-node"

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_extract_files_from_markdown",
            new=AsyncMock(return_value=([(file_record, [])], [])),
        ) as mock_extract:
            batches = []
            async for batch in conn._fetch_issues_for_team_batch(
                team_id="team-1",
                team_key="ENG",
                last_sync_time=None,
            ):
                batches.append(batch)

        assert mock_extract.await_count >= 1
        assert batches

    @pytest.mark.asyncio
    async def test_fetch_issues_batch_extracts_description_files(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"

        issue = {
            "id": "iss-1",
            "identifier": "ENG-1",
            "title": "Bug",
            "url": "https://linear.app/org/issue/ENG-1",
            "description": "See [spec.pdf](https://uploads/spec.pdf)",
            "priority": 2,
            "state": {"name": "Todo", "type": "unstarted"},
            "assignee": None,
            "creator": {"id": "u1", "email": "a@x.com", "name": "A"},
            "parent": None,
            "labels": {"nodes": []},
            "relations": {"nodes": []},
            "comments": {"nodes": []},
            "createdAt": "2024-01-01T10:00:00.000Z",
            "updatedAt": "2024-06-01T10:00:00.000Z",
        }

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_issues_resp([issue], has_next=False))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_extract_files_from_markdown",
            new=AsyncMock(return_value=([], [])),
        ) as mock_extract:
            batches = []
            async for batch in conn._fetch_issues_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        mock_extract.assert_awaited_once()
        assert batches

    @pytest.mark.asyncio
    async def test_fetch_issues_batch_skips_empty_comment_body(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"

        issue = {
            "id": "iss-1",
            "identifier": "ENG-1",
            "title": "Bug",
            "url": "https://linear.app/org/issue/ENG-1",
            "description": "",
            "priority": 2,
            "state": {"name": "Todo", "type": "unstarted"},
            "assignee": None,
            "creator": {"id": "u1", "email": "a@x.com", "name": "A"},
            "parent": None,
            "labels": {"nodes": []},
            "relations": {"nodes": []},
            "comments": {"nodes": [{"id": "c1", "body": ""}]},
            "createdAt": "2024-01-01T10:00:00.000Z",
            "updatedAt": "2024-06-01T10:00:00.000Z",
        }

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_issues_resp([issue], has_next=False))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_extract_files_from_markdown",
            new=AsyncMock(return_value=([], [])),
        ) as mock_extract:
            batches = []
            async for batch in conn._fetch_issues_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        mock_extract.assert_not_awaited()
        assert batches

    @pytest.mark.asyncio
    async def test_fetch_issues_batch_api_failure_yields_nothing(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=MagicMock(success=False, message="rate limited"))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            batches = []
            async for batch in conn._fetch_issues_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        assert batches == []


class TestParseProjectUpdates:
    @pytest.mark.asyncio
    async def test_parse_project_to_blocks_includes_update_comments(self):
        conn = _make_connector()
        conn.organization_url_key = "org"

        project = {
            "id": "proj-1",
            "name": "Project",
            "url": "https://linear.app/org/project/p",
            "description": "Desc",
            "content": "Body",
            "updates": {
                "nodes": [{
                    "id": "upd-1",
                    "body": "Shipped",
                    "createdAt": "2024-06-01T10:00:00.000Z",
                    "url": "https://linear.app/org/update/1",
                    "comments": {
                        "nodes": [{
                            "id": "uc1",
                            "body": "Nice [doc.pdf](https://files/doc.pdf)",
                            "user": {"id": "u1", "displayName": "Alice"},
                            "url": "https://linear.app/org/update/1#c1",
                            "createdAt": "2024-06-01T10:00:00.000Z",
                            "updatedAt": "2024-06-01T10:00:00.000Z",
                        }],
                    },
                }],
            },
            "milestones": {"nodes": []},
            "externalLinks": {"nodes": []},
            "documents": {"nodes": []},
        }

        with patch.object(
            conn,
            "_convert_images_to_base64_in_markdown",
            new=AsyncMock(side_effect=lambda x: x),
        ):
            container = await conn._parse_project_to_blocks(project, weburl=project["url"])

        assert container.block_groups


class TestProjectStreaming:
    @pytest.mark.asyncio
    async def test_process_project_blockgroups_success(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = ProjectRecord(
            id="rec-p1",
            org_id="org-linear-gaps",
            record_name="Project",
            record_type=RecordType.PROJECT,
            external_record_id="proj-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            record_group_type=RecordGroupType.PROJECT,
        )
        project = {
            "id": "proj-1",
            "name": "Project",
            "url": "https://linear.app/org/project/p",
            "content": "Body",
            "externalLinks": {"nodes": []},
            "documents": {"nodes": []},
        }
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=MagicMock(success=True, data={"project": project}))
        container = BlocksContainer(
            block_groups=[BlockGroup(index=0, type=GroupType.TEXT_SECTION, name="Description")],
        )
        child = MagicMock()

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_parse_project_to_blocks",
            new=AsyncMock(return_value=container),
        ), patch.object(
            conn,
            "_process_content_files_for_children",
            new=AsyncMock(return_value=[child]),
        ):
            result = await conn._process_project_blockgroups_for_streaming(record)

        assert isinstance(result, bytes)
        assert container.block_groups[0].children_records == [child]

    @pytest.mark.asyncio
    async def test_process_project_blockgroups_api_failure(self):
        conn = _make_connector()
        record = ProjectRecord(
            id="rec-p1",
            org_id="org-linear-gaps",
            record_name="Project",
            record_type=RecordType.PROJECT,
            external_record_id="proj-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            record_group_type=RecordGroupType.PROJECT,
        )
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=MagicMock(success=False, message="not found"))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            with pytest.raises(Exception, match="Failed to fetch project"):
                await conn._process_project_blockgroups_for_streaming(record)

    @pytest.mark.asyncio
    async def test_process_project_blockgroups_with_links_and_documents(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = ProjectRecord(
            id="rec-p1",
            org_id="org-linear-gaps",
            record_name="Project",
            record_type=RecordType.PROJECT,
            external_record_id="proj-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            record_group_type=RecordGroupType.PROJECT,
        )
        project = {
            "id": "proj-1",
            "name": "Project",
            "url": "https://linear.app/org/project/p",
            "content": "",
            "createdAt": "2024-01-01T10:00:00.000Z",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "externalLinks": {"nodes": [{"id": "link-1", "url": "https://example.com", "label": "Docs"}]},
            "documents": {"nodes": [{"id": "doc-1", "title": "Spec", "url": "https://linear.app/doc/1"}]},
        }
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=MagicMock(success=True, data={"project": project}))
        container = BlocksContainer(
            block_groups=[BlockGroup(index=0, type=GroupType.TEXT_SECTION, name="Description")],
        )
        link_bg = BlockGroup(index=1, type=GroupType.TEXT_SECTION, name="Link")
        doc_bg = BlockGroup(index=2, type=GroupType.TEXT_SECTION, name="Doc")

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_parse_project_to_blocks",
            new=AsyncMock(return_value=container),
        ), patch.object(
            conn,
            "_process_project_external_links",
            new=AsyncMock(return_value=([], [link_bg])),
        ), patch.object(
            conn,
            "_process_project_documents",
            new=AsyncMock(return_value=([], [doc_bg])),
        ):
            result = await conn._process_project_blockgroups_for_streaming(record)

        assert isinstance(result, bytes)
        assert len(container.block_groups) == 3


class TestFetchUsersEdgeCases:
    @pytest.mark.asyncio
    async def test_fetch_users_raises_on_api_failure(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(return_value=MagicMock(success=False, message="forbidden"))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            with pytest.raises(RuntimeError, match="Failed to fetch users"):
                await conn._fetch_users()

    @pytest.mark.asyncio
    async def test_fetch_users_skips_inactive_and_no_email(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(return_value=_users_resp([
            {"id": "u1", "email": "a@x.com", "active": False, "displayName": "Inactive"},
            {"id": "u2", "active": True, "displayName": "NoEmail"},
            {"id": "u3", "email": "ok@x.com", "active": True, "displayName": "OK"},
        ]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            users = await conn._fetch_users()

        assert len(users) == 1
        assert users[0].email == "ok@x.com"


class TestIssueBatchPaginationAndErrors:
    @pytest.mark.asyncio
    async def test_fetch_issues_batch_stops_when_next_page_has_no_cursor(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"

        issue = _make_issue_payload()
        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_issues_resp(
            [issue], has_next=True, end_cursor=None,
        ))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_extract_files_from_markdown", new=AsyncMock(return_value=([], [])),
        ):
            batches = []
            async for batch in conn._fetch_issues_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        assert len(batches) == 1

    @pytest.mark.asyncio
    async def test_fetch_issues_batch_continues_after_single_issue_error(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"

        good = _make_issue_payload(id="iss-good", identifier="ENG-1")
        bad = _make_issue_payload(id="iss-bad", identifier="ENG-2")
        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_issues_resp([good, bad], has_next=False))

        good_ticket = MagicMock()
        good_ticket.id = "node-good"

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_transform_issue_to_ticket_record",
            side_effect=[good_ticket, ValueError("transform failed")],
        ), patch.object(conn, "_extract_files_from_markdown", new=AsyncMock(return_value=([], []))):
            batches = []
            async for batch in conn._fetch_issues_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        assert len(batches) == 1
        assert len(batches[0]) == 1


class TestContentAndCommentFileChildren:
    @pytest.mark.asyncio
    async def test_process_content_files_saves_new_records(self):
        conn = _make_connector()
        file_rec = MagicMock()
        file_rec.id = "file-1"
        file_rec.record_name = "spec.pdf"

        with patch.object(
            conn,
            "_extract_files_from_markdown",
            new=AsyncMock(return_value=([(file_rec, [])], [])),
        ):
            children = await conn._process_content_files_for_children(
                content="[spec.pdf](https://files/spec.pdf)",
                parent_external_id="iss-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
                tx_store=conn._tx_store,
            )

        conn.data_entities_processor.on_new_records.assert_awaited_once()
        assert len(children) == 1
        assert children[0].child_id == "file-1"

    @pytest.mark.asyncio
    async def test_process_content_files_empty_content(self):
        conn = _make_connector()
        children = await conn._process_content_files_for_children(
            content="",
            parent_external_id="iss-1",
            parent_node_id="node-1",
            parent_record_type=RecordType.TICKET,
            team_id="team-1",
            tx_store=conn._tx_store,
        )
        assert children == []

    @pytest.mark.asyncio
    async def test_process_comment_files_maps_by_comment_id(self):
        conn = _make_connector()
        file_rec = MagicMock()
        file_rec.id = "file-2"
        file_rec.record_name = "note.pdf"
        existing_child = ChildRecord(
            child_type=ChildType.RECORD,
            child_id="existing-file",
            child_name="existing.pdf",
        )

        with patch.object(
            conn,
            "_extract_files_from_markdown",
            new=AsyncMock(return_value=([(file_rec, [])], [existing_child])),
        ):
            result = await conn._process_comment_files_for_children(
                comments_data=[{
                    "id": "c1",
                    "body": "[note.pdf](https://files/note.pdf)",
                    "createdAt": "2024-06-01T10:00:00.000Z",
                    "updatedAt": "2024-06-01T10:00:00.000Z",
                }],
                issue_id="iss-1",
                issue_node_id="node-1",
                team_id="team-1",
                issue_weburl="https://linear.app/issue/ENG-1",
                tx_store=conn._tx_store,
            )

        assert "c1" in result
        assert len(result["c1"]) == 2


class TestIssueStreaming:
    @pytest.mark.asyncio
    async def test_process_issue_blockgroups_success(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = TicketRecord(
            id="rec-i1",
            org_id="org-linear-gaps",
            record_name="Bug",
            record_type=RecordType.TICKET,
            external_record_id="iss-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            record_group_type=RecordGroupType.PROJECT,
        )
        issue = _make_issue_payload(
            description="Desc with [file.pdf](https://files/file.pdf)",
            comments={"nodes": [{
                "id": "c1",
                "body": "Comment",
                "createdAt": "2024-06-01T10:00:00.000Z",
                "updatedAt": "2024-06-01T10:00:00.000Z",
            }]},
            attachments={"nodes": [{"id": "att-1", "url": "https://x.com/a", "title": "A"}]},
            documents={"nodes": [{"id": "doc-1", "title": "Doc", "url": "https://x.com/d"}]},
        )
        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(return_value=MagicMock(success=True, data={"issue": issue}))
        container = BlocksContainer(
            block_groups=[BlockGroup(index=0, type=GroupType.TEXT_SECTION, name="Description")],
        )
        att_child = ChildRecord(child_type=ChildType.RECORD, child_id="att-node", child_name="A")
        doc_child = ChildRecord(child_type=ChildType.RECORD, child_id="doc-node", child_name="Doc")
        file_child = ChildRecord(child_type=ChildType.RECORD, child_id="file-node", child_name="file.pdf")

        mock_parse = AsyncMock(return_value=container)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_process_issue_attachments",
            new=AsyncMock(return_value=[att_child]),
        ), patch.object(
            conn,
            "_process_issue_documents",
            new=AsyncMock(return_value=[doc_child]),
        ), patch.object(
            conn,
            "_process_content_files_for_children",
            new=AsyncMock(return_value=[file_child]),
        ), patch.object(
            conn,
            "_process_comment_files_for_children",
            new=AsyncMock(return_value={"c1": []}),
        ), patch.object(
            conn,
            "_parse_issue_to_blocks",
            new=mock_parse,
        ):
            result = await conn._process_issue_blockgroups_for_streaming(record)

        assert isinstance(result, bytes)
        mock_parse.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_issue_blockgroups_api_failure(self):
        conn = _make_connector()
        record = TicketRecord(
            id="rec-i1",
            org_id="org-linear-gaps",
            record_name="Bug",
            record_type=RecordType.TICKET,
            external_record_id="iss-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            record_group_type=RecordGroupType.PROJECT,
        )
        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(return_value=MagicMock(success=False, message="not found"))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            with pytest.raises(Exception, match="Failed to fetch issue"):
                await conn._process_issue_blockgroups_for_streaming(record)


class TestSyncAttachmentsEdgeCases:
    @pytest.mark.asyncio
    async def test_sync_attachments_api_failure(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()
        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(return_value=MagicMock(success=False, message="forbidden"))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ):
            await conn._sync_attachments([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_attachments_skips_unknown_team_and_missing_parent(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg("team-1")
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        attachments = [
            {"id": "a1", "issue": {"id": "i1", "team": {"id": "other-team"}}},
            {"id": "a2", "issue": {"id": "i2", "team": {"id": "team-1"}}},
        ]
        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(return_value=_attachments_resp(attachments))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ):
            await conn._sync_attachments([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_attachments_stops_when_next_page_has_no_cursor(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(return_value=_attachments_resp(
            [attachment], has_next=True, end_cursor=None,
        ))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(
            conn, "_update_attachments_sync_checkpoint", new=AsyncMock(),
        ):
            await conn._sync_attachments([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_awaited_once()


class TestFetchTeamsExtended:
    @pytest.mark.asyncio
    async def test_raises_when_datasource_not_initialized(self):
        conn = _make_connector()
        conn.data_source = None
        with pytest.raises(ValueError, match="DataSource not initialized"):
            await conn._fetch_teams()

    @pytest.mark.asyncio
    async def test_raises_on_api_failure(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=MagicMock(success=False, message="rate limited"))
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            with pytest.raises(RuntimeError, match="Failed to fetch teams"):
                await conn._fetch_teams()

    @pytest.mark.asyncio
    async def test_paginates_through_multiple_pages(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.organization_url_key = "org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(side_effect=[
            _teams_resp([{"id": "t1", "name": "Eng", "key": "ENG", "private": False, "members": {"nodes": []}}], has_next=True, end_cursor="cur1"),
            _teams_resp([{"id": "t2", "name": "Ops", "key": "OPS", "private": False, "members": {"nodes": []}}]),
        ])
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            _, record_groups = await conn._fetch_teams()
        assert len(record_groups) == 2

    @pytest.mark.asyncio
    async def test_builds_user_email_map_for_team_members(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.organization_url_key = "org"
        app_user = AppUser(
            app_name=Connectors.LINEAR,
            connector_id="linear-gap",
            source_user_id="u1",
            org_id="org-linear-gaps",
            email="member@example.com",
            full_name="Member",
            is_active=True,
        )
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_teams_resp([{
            "id": "t1",
            "name": "Eng",
            "key": "ENG",
            "private": False,
            "members": {"nodes": [{"email": "member@example.com", "id": "u1"}]},
        }]))
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            user_groups, _ = await conn._fetch_teams(user_email_map={"member@example.com": app_user})
        assert user_groups
        assert user_groups[0][1] == [app_user]


class TestSyncIssuesForTeamsExtended:
    @pytest.mark.asyncio
    async def test_incremental_sync_updates_checkpoint_for_tickets_and_files(self):
        conn = _make_connector()
        rg, perms = _make_team_rg()
        conn._get_team_sync_checkpoint = AsyncMock(return_value=1700000000000)
        conn._update_team_sync_checkpoint = AsyncMock()

        ticket = _ticket_record(source_updated_at=1704067200000)
        file_rec = _file_record()

        async def batch_gen(**_kwargs):
            yield [(ticket, []), (file_rec, [])]

        conn._fetch_issues_for_team_batch = batch_gen
        await conn._sync_issues_for_teams([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_awaited_once()
        conn._update_team_sync_checkpoint.assert_awaited_with("ENG", 1704067200000)

    @pytest.mark.asyncio
    async def test_skips_empty_batches_from_fetch(self):
        conn = _make_connector()
        rg, _ = _make_team_rg()
        conn._get_team_sync_checkpoint = AsyncMock(return_value=None)
        conn._update_team_sync_checkpoint = AsyncMock()

        async def batch_gen(**_kwargs):
            yield []
            yield [(_ticket_record(), [])]

        conn._fetch_issues_for_team_batch = batch_gen
        await conn._sync_issues_for_teams([(rg, [])])
        assert conn.data_entities_processor.on_new_records.await_count == 1


class TestFetchIssuesIndexingFilter:
    @pytest.mark.asyncio
    async def test_disables_issue_indexing_when_filter_off(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=False)
        conn.organization_url_key = "org"

        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_issues_resp([_make_issue_payload()]))
        real_ticket = conn._transform_issue_to_ticket_record(
            _make_issue_payload(), "team-1", None,
        )

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_transform_issue_to_ticket_record", return_value=real_ticket,
        ), patch.object(conn, "_extract_files_from_markdown", new=AsyncMock(return_value=([], []))):
            batches = []
            async for batch in conn._fetch_issues_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        assert batches[0][0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


class TestSyncAttachmentsExtended:
    @pytest.mark.asyncio
    async def test_links_attachment_to_existing_record_by_weburl(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()

        parent = MagicMock()
        parent.id = "parent-node"
        related = MagicMock()
        related.id = "related-node"

        async def lookup(**kwargs):
            if kwargs.get("external_id") == "issue-1":
                return parent
            return None

        conn._tx_store.get_record_by_external_id = AsyncMock(side_effect=lookup)
        conn._tx_store.get_record_by_weburl = AsyncMock(return_value=related)

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/spec.pdf",
            "title": "Spec",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(return_value=_attachments_resp([attachment]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(conn, "_update_attachments_sync_checkpoint", new=AsyncMock()):
            await conn._sync_attachments([(rg, perms)])

        saved = conn.data_entities_processor.on_new_records.call_args[0][0]
        link_record = saved[0][0]
        assert link_record.linked_record_id == "related-node"

    @pytest.mark.asyncio
    async def test_paginates_attachments_and_updates_checkpoint(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/file",
            "title": "File",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(side_effect=[
            _attachments_resp([attachment], has_next=True, end_cursor="cur-1"),
            _attachments_resp([], has_next=False),
        ])

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(conn, "_update_attachments_sync_checkpoint", new=AsyncMock()) as mock_update:
            await conn._sync_attachments([(rg, perms)])

        assert mock_ds.attachments.await_count == 2
        mock_update.assert_awaited()


class TestSyncDocumentsExtended:
    @pytest.mark.asyncio
    async def test_skips_project_attached_documents(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()

        doc = {
            "id": "doc-1",
            "title": "Roadmap",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "issue": None,
            "project": {"id": "proj-1"},
        }
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=MagicMock(
            success=True,
            data={"documents": {"nodes": [doc], "pageInfo": {"hasNextPage": False}}},
        ))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(conn, "_update_documents_sync_checkpoint", new=AsyncMock()) as mock_update:
            await conn._sync_documents([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()
        mock_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_syncs_issue_documents_and_paginates(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(side_effect=lambda **kw: (
            parent if kw.get("external_id") == "issue-1" else None
        ))

        doc = {
            "id": "doc-1",
            "title": "Design",
            "url": "https://linear.app/org/document/doc-1",
            "content": "# Design",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(side_effect=[
            MagicMock(success=True, data={"documents": {"nodes": [doc], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}),
            MagicMock(success=True, data={"documents": {"nodes": [], "pageInfo": {"hasNextPage": False}}}),
        ])

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(conn, "_update_documents_sync_checkpoint", new=AsyncMock()):
            await conn._sync_documents([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_awaited()
        assert mock_ds.documents.await_count == 2


class TestFetchProjectsBatchExtended:
    @pytest.mark.asyncio
    async def test_uses_list_payload_when_full_project_fetch_fails(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"

        project_summary = {
            "id": "proj-1",
            "name": "Alpha",
            "url": "https://linear.app/org/project/alpha",
            "content": "[brief.pdf](https://files/brief.pdf)",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-06-01T00:00:00.000Z",
            "issues": {"nodes": [{"id": "iss-linked"}]},
        }
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(return_value=MagicMock(
            success=True,
            data={"projects": {"nodes": [project_summary], "pageInfo": {"hasNextPage": False}}},
        ))
        mock_ds.project = AsyncMock(return_value=MagicMock(success=False, message="forbidden"))

        file_rec = _file_record()
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_extract_files_from_markdown", new=AsyncMock(return_value=([(file_rec, [])], [])),
        ), patch.object(conn, "_prepare_project_related_records", new=AsyncMock(return_value=[])):
            batches = []
            async for batch in conn._fetch_projects_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        assert batches
        project_records = [rec for rec, _ in batches[0] if isinstance(rec, ProjectRecord)]
        file_records = [rec for rec, _ in batches[0] if isinstance(rec, FileRecord)]
        assert project_records
        assert project_records[0].related_external_records
        assert file_records


class TestParseProjectBlocksExtended:
    @pytest.mark.asyncio
    async def test_content_only_project_with_threaded_comments(self):
        conn = _make_connector()
        conn.organization_url_key = "org"
        project = {
            "id": "proj-1",
            "name": "Alpha",
            "url": "https://linear.app/org/project/alpha",
            "description": "",
            "content": "Main body",
            "comments": {
                "nodes": [{
                    "id": "pc1",
                    "body": "LGTM [notes.pdf](https://files/notes.pdf)",
                    "user": {"id": "u1", "displayName": "Reviewer"},
                    "url": "https://linear.app/org/project/alpha#pc1",
                    "createdAt": "2024-06-01T10:00:00.000Z",
                    "updatedAt": "2024-06-01T10:00:00.000Z",
                    "quotedText": "Previous text",
                }],
            },
            "updates": {"nodes": []},
            "milestones": {"nodes": []},
            "externalLinks": {"nodes": []},
            "documents": {"nodes": []},
        }

        with patch.object(
            conn,
            "_convert_images_to_base64_in_markdown",
            new=AsyncMock(side_effect=lambda text: text),
        ), patch.object(
            conn,
            "_extract_file_urls_from_markdown",
            return_value=[{"filename": "notes.pdf", "url": "https://files/notes.pdf"}],
        ):
            container = await conn._parse_project_to_blocks(project, weburl=project["url"])

        assert container.block_groups
        description_group = container.block_groups[0]
        assert description_group.comments
        assert description_group.comments[0][0].attachments


class TestReindexExtended:
    @pytest.mark.asyncio
    async def test_issue_unchanged_at_source_is_not_reindexed(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = _ticket_record(source_updated_at=1704067200000)
        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(return_value=MagicMock(
            success=True,
            data={"issue": _make_issue_payload(updatedAt="2024-01-01T00:00:00.000Z")},
        ))
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_issue(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_file_record_reindex_returns_none(self):
        conn = _make_connector()
        record = _file_record()
        result = await conn._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_webpage_reindex_returns_updated_record(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = WebpageRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Design",
            record_type=RecordType.WEBPAGE,
            external_record_id="doc-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            source_updated_at=1700000000000,
            parent_external_record_id="iss-1",
            parent_node_id="node-1",
        )
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(return_value=MagicMock(
            success=True,
            data={"document": {
                "id": "doc-1",
                "title": "Design",
                "url": "https://linear.app/org/document/doc-1",
                "updatedAt": "2024-06-01T10:00:00.000Z",
                "createdAt": "2024-01-01T00:00:00.000Z",
                "issue": {"id": "iss-1", "team": {"id": "team-1"}},
            }},
        ))
        parent = MagicMock()
        parent.id = "node-1"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_record(record)

        assert result is not None
        updated, _ = result
        assert isinstance(updated, WebpageRecord)

    @pytest.mark.asyncio
    async def test_link_reindex_routes_to_issue_attachment_checker(self):
        conn = _make_connector()
        record = LinkRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Attachment",
            record_type=RecordType.LINK,
            external_record_id="att-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            parent_external_record_id="iss-1",
            parent_node_id="node-1",
            url="https://example.com/spec.pdf",
            is_public=LinkPublicStatus.UNKNOWN,
            source_updated_at=1700000000000,
        )
        parent = _ticket_record(id="node-1", external_record_id="iss-1")
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        with patch.object(
            conn,
            "_check_and_fetch_updated_issue_link",
            new=AsyncMock(return_value=(record, [])),
        ) as mock_issue_link:
            result = await conn._check_and_fetch_updated_record(record)

        mock_issue_link.assert_awaited_once()
        assert result == (record, [])


class TestStreamRecordExtended:
    @pytest.mark.asyncio
    async def test_streams_file_record_via_download(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.external_client = MagicMock()
        record = _file_record(
            external_record_id="https://files.example.com/report.pdf",
            mime_type="application/pdf",
        )

        async def download(_url):
            yield b"pdf-chunk"

        mock_ds = MagicMock()
        mock_ds.download_file = download
        conn._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        response = await conn.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert body == b"pdf-chunk"
        assert response.media_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_streams_webpage_markdown(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = WebpageRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Doc",
            record_type=RecordType.WEBPAGE,
            external_record_id="doc-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
        )
        with patch.object(conn, "_fetch_document_content", new=AsyncMock(return_value="# Title")):
            response = await conn.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert body == b"# Title"


class TestCleanupExtended:
    @pytest.mark.asyncio
    async def test_closes_internal_http_client(self):
        conn = _make_connector()
        internal = AsyncMock()
        internal.close = AsyncMock()
        conn.external_client = MagicMock()
        conn.external_client.get_client.return_value = internal

        await conn.cleanup()
        internal.close.assert_awaited_once()


class TestRunSyncFilterLogging:
    @pytest.mark.asyncio
    async def test_empty_team_ids_filter_still_runs_fetch(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        team_filter = MagicMock()
        team_filter.get_value.return_value = []
        team_filter.get_operator.return_value = MagicMock(value="in")
        sync_filters = MagicMock()
        sync_filters.get.side_effect = lambda key: team_filter if key == "team_ids" else None

        conn.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[MagicMock(email="u@x.com")],
        )
        conn._fetch_users = AsyncMock(return_value=[])
        conn._fetch_teams = AsyncMock(return_value=([], []))
        conn._sync_issues_for_teams = AsyncMock()
        conn._sync_attachments = AsyncMock()
        conn._sync_documents = AsyncMock()
        conn._sync_projects_for_teams = AsyncMock()
        conn._sync_deleted_issues = AsyncMock()
        conn._sync_deleted_projects = AsyncMock()

        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new=AsyncMock(return_value=(sync_filters, None)),
        ), patch.object(conn.logger, "info") as mock_info:
            await conn.run_sync()

        conn._fetch_teams.assert_awaited_once_with(
            team_ids=[],
            team_ids_operator=team_filter.get_operator.return_value,
            user_email_map={},
        )
        assert any("Team filter is empty" in str(call) for call in mock_info.call_args_list)


class TestSyncIndexingFiltersAndErrors:
    @pytest.mark.asyncio
    async def test_attachment_indexing_filter_off(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=False)
        rg, perms = _make_team_rg()
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/spec.pdf",
            "title": "Spec",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(return_value=_attachments_resp([attachment]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(conn, "_update_attachments_sync_checkpoint", new=AsyncMock()):
            await conn._sync_attachments([(rg, perms)])

        saved = conn.data_entities_processor.on_new_records.call_args[0][0]
        assert saved[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_attachment_weburl_lookup_error_is_swallowed(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)
        conn._tx_store.get_record_by_weburl = AsyncMock(side_effect=RuntimeError("db down"))

        attachment = {
            "id": "attach-1",
            "url": "https://example.com/spec.pdf",
            "title": "Spec",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(return_value=_attachments_resp([attachment]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(conn, "_update_attachments_sync_checkpoint", new=AsyncMock()):
            await conn._sync_attachments([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_attachment_processing_error_continues(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        attachment = {
            "id": "attach-bad",
            "url": "https://example.com/bad.pdf",
            "title": "Bad",
            "createdAt": "2024-01-10T10:00:00.000Z",
            "updatedAt": "2024-01-15T12:00:00.000Z",
            "issue": {"id": "issue-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.attachments = AsyncMock(return_value=_attachments_resp([attachment]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(
            conn,
            "_transform_attachment_to_link_record",
            side_effect=ValueError("transform failed"),
        ):
            await conn._sync_attachments([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_attachments_outer_exception(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(side_effect=RuntimeError("boom"))), patch.object(
            conn, "_get_attachments_sync_checkpoint", new=AsyncMock(return_value=None),
        ):
            await conn._sync_attachments([(rg, perms)])

    @pytest.mark.asyncio
    async def test_document_indexing_filter_off(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=False)
        rg, perms = _make_team_rg()
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        doc = {
            "id": "doc-1",
            "title": "Design",
            "url": "https://linear.app/org/document/doc-1",
            "content": "# Design",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=_documents_resp([doc]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(conn, "_update_documents_sync_checkpoint", new=AsyncMock()):
            await conn._sync_documents([(rg, perms)])

        saved = conn.data_entities_processor.on_new_records.call_args[0][0]
        assert saved[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_document_processing_error_continues(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        doc = {
            "id": "doc-bad",
            "title": "Bad",
            "url": "https://linear.app/org/document/doc-bad",
            "updatedAt": "2024-01-02T00:00:00.000Z",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=_documents_resp([doc]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(
            conn,
            "_transform_document_to_webpage_record",
            side_effect=ValueError("transform failed"),
        ):
            await conn._sync_documents([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_documents_fetch_failure(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=MagicMock(success=False, message="forbidden"))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ):
            await conn._sync_documents([(rg, perms)])


class TestFetchBatchEdgeCases:
    @pytest.mark.asyncio
    async def test_fetch_users_empty_first_page(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.users = AsyncMock(return_value=_users_resp([]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            users = await conn._fetch_users()

        assert users == []

    @pytest.mark.asyncio
    async def test_fetch_teams_stops_when_next_page_has_no_cursor(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.organization_url_key = "org"
        mock_ds = MagicMock()
        mock_ds.teams = AsyncMock(return_value=_teams_resp(
            [{"id": "t1", "name": "Eng", "key": "ENG", "private": False, "members": {"nodes": []}}],
            has_next=True,
            end_cursor=None,
        ))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            _, record_groups = await conn._fetch_teams()

        assert len(record_groups) == 1

    @pytest.mark.asyncio
    async def test_fetch_issues_empty_list_breaks(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"
        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_issues_resp([]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            batches = []
            async for batch in conn._fetch_issues_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        assert batches == []

    @pytest.mark.asyncio
    async def test_fetch_projects_batch_processing_error(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"

        project_summary = {
            "id": "proj-1",
            "name": "Alpha",
            "url": "https://linear.app/org/project/alpha",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-06-01T00:00:00.000Z",
        }
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(return_value=_projects_resp([project_summary]))
        mock_ds.project = AsyncMock(return_value=MagicMock(success=True, data={"project": project_summary}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_transform_to_project_record",
            side_effect=ValueError("bad project"),
        ):
            batches = []
            async for batch in conn._fetch_projects_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        assert batches == []


class TestApplyDateFiltersExtended:
    def test_modified_before_merges_with_updated_at(self):
        conn = _make_connector()
        modified_filter = MagicMock()
        modified_filter.get_value.return_value = (1704067200000, 1706745600000)
        created_filter = MagicMock()
        created_filter.get_value.return_value = (1700000000000, 1705000000000)
        sync_filters = MagicMock()
        sync_filters.get.side_effect = lambda key: {
            SyncFilterKey.MODIFIED: modified_filter,
            SyncFilterKey.CREATED: created_filter,
        }.get(key)

        conn.sync_filters = sync_filters
        linear_filter = {}
        conn._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=1705000000000)

        assert "updatedAt" in linear_filter
        assert "gt" in linear_filter["updatedAt"]
        assert "lte" in linear_filter["updatedAt"]
        assert "createdAt" in linear_filter
        assert "gte" in linear_filter["createdAt"]
        assert "lte" in linear_filter["createdAt"]

    def test_modified_after_only_uses_filter_timestamp(self):
        conn = _make_connector()
        modified_filter = MagicMock()
        modified_filter.get_value.return_value = (1704067200000, None)
        sync_filters = MagicMock()
        sync_filters.get.side_effect = lambda key: modified_filter if key == SyncFilterKey.MODIFIED else None
        conn.sync_filters = sync_filters

        linear_filter = {}
        conn._apply_date_filters_to_linear_filter(linear_filter, last_sync_time=None)
        assert "updatedAt" in linear_filter


class TestDeletionSyncExtended:
    @pytest.mark.asyncio
    async def test_deleted_issues_skips_when_no_team_ids(self):
        conn = _make_connector()
        rg = RecordGroup(
            id="rg-empty",
            org_id="org-linear-gaps",
            external_group_id=None,
            connector_id="linear-gap",
            connector_name=Connectors.LINEAR,
            name="Empty",
            short_name="EMP",
            group_type=RecordGroupType.PROJECT,
        )
        with patch.object(conn, "_get_deletion_sync_checkpoint", new=AsyncMock(return_value=1000)):
            await conn._sync_deleted_issues([(rg, [])])

    @pytest.mark.asyncio
    async def test_deleted_issues_empty_page_breaks(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        rg, perms = _make_team_rg()
        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(return_value=_issues_resp([]))

        with patch.object(conn, "_get_deletion_sync_checkpoint", new=AsyncMock(return_value=1704067200000)), patch.object(
            conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds),
        ), patch.object(conn, "_mark_record_and_children_deleted", new=AsyncMock()):
            await conn._sync_deleted_issues([(rg, perms)])

    @pytest.mark.asyncio
    async def test_deleted_issues_marks_trashed_and_paginates(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        rg, perms = _make_team_rg()
        trashed = _make_issue_payload(
            id="iss-trashed",
            trashed=True,
            archivedAt="2024-06-15T10:00:00.000Z",
        )
        mock_ds = MagicMock()
        mock_ds.issues = AsyncMock(side_effect=[
            _issues_resp([trashed], has_next=True, end_cursor=None),
        ])

        with patch.object(conn, "_get_deletion_sync_checkpoint", new=AsyncMock(return_value=1704067200000)), patch.object(
            conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds),
        ), patch.object(conn, "_mark_record_and_children_deleted", new=AsyncMock()) as mock_mark, patch.object(
            conn, "_update_deletion_sync_checkpoint", new=AsyncMock(),
        ) as mock_update:
            await conn._sync_deleted_issues([(rg, perms)])

        mock_mark.assert_awaited_once()
        mock_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deleted_issues_exception_is_logged(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        rg, perms = _make_team_rg()

        with patch.object(conn, "_get_deletion_sync_checkpoint", new=AsyncMock(return_value=1704067200000)), patch.object(
            conn, "_get_fresh_datasource", new=AsyncMock(side_effect=RuntimeError("api down")),
        ):
            await conn._sync_deleted_issues([(rg, perms)])

    @pytest.mark.asyncio
    async def test_deleted_projects_skips_when_no_team_ids(self):
        conn = _make_connector()
        rg = RecordGroup(
            id="rg-empty",
            org_id="org-linear-gaps",
            external_group_id="",
            connector_id="linear-gap",
            connector_name=Connectors.LINEAR,
            name="Empty",
            short_name="EMP",
            group_type=RecordGroupType.PROJECT,
        )
        with patch.object(conn, "_get_deletion_sync_checkpoint", new=AsyncMock(return_value=1000)):
            await conn._sync_deleted_projects([(rg, [])])

    @pytest.mark.asyncio
    async def test_deleted_projects_api_failure(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        rg, perms = _make_team_rg()
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(return_value=MagicMock(success=False, message="forbidden"))

        with patch.object(conn, "_get_deletion_sync_checkpoint", new=AsyncMock(return_value=1704067200000)), patch.object(
            conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds),
        ):
            await conn._sync_deleted_projects([(rg, perms)])

    @pytest.mark.asyncio
    async def test_deleted_projects_filters_by_checkpoint(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        rg, perms = _make_team_rg()
        old_trashed = {
            "id": "proj-old",
            "name": "Old",
            "trashed": True,
            "archivedAt": "2023-01-01T00:00:00.000Z",
        }
        new_trashed = {
            "id": "proj-new",
            "name": "New",
            "trashed": True,
            "archivedAt": "2024-06-15T10:00:00.000Z",
        }
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(return_value=_projects_resp([old_trashed, new_trashed]))

        with patch.object(conn, "_get_deletion_sync_checkpoint", new=AsyncMock(return_value=1704067200000)), patch.object(
            conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds),
        ), patch.object(conn, "_mark_record_and_children_deleted", new=AsyncMock()) as mock_mark, patch.object(
            conn, "_update_deletion_sync_checkpoint", new=AsyncMock(),
        ):
            await conn._sync_deleted_projects([(rg, perms)])

        mock_mark.assert_awaited_once_with(external_record_id="proj-new", record_type="project")


class TestMarkDeletedExtended:
    @pytest.mark.asyncio
    async def test_deletes_grandchild_records(self):
        conn = _make_connector()
        parent = MagicMock()
        parent.id = "parent-id"
        parent.external_record_id = "ext-parent"

        child = MagicMock()
        child.id = "child-id"
        child.external_record_id = "ext-child"

        grandchild = MagicMock()
        grandchild.id = "grand-id"
        grandchild.external_record_id = "ext-grand"

        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)
        conn._tx_store.get_records_by_parent = AsyncMock(side_effect=[[child], [grandchild]])
        conn._tx_store.delete_records_and_relations = AsyncMock()

        await conn._mark_record_and_children_deleted("ext-parent", "issue")
        assert conn._tx_store.delete_records_and_relations.await_count == 3


class TestParseBlocksExtended:
    @pytest.mark.asyncio
    async def test_parse_issue_creates_minimal_blockgroup(self):
        conn = _make_connector()
        issue = _make_issue_payload(
            title="Empty Issue",
            description="",
            comments={"nodes": []},
        )
        with patch.object(
            conn,
            "_convert_images_to_base64_in_markdown",
            new=AsyncMock(side_effect=lambda text: text),
        ):
            container = await conn._parse_issue_to_blocks(
                issue,
                weburl=issue["url"],
            )

        assert container.block_groups
        assert "Empty Issue" in container.block_groups[0].data

    @pytest.mark.asyncio
    async def test_parse_project_update_comment_attachments(self):
        conn = _make_connector()
        conn.organization_url_key = "org"
        project = {
            "id": "proj-1",
            "name": "Project",
            "url": "https://linear.app/org/project/p",
            "description": "",
            "content": "",
            "comments": {"nodes": []},
            "projectUpdates": {
                "nodes": [{
                    "id": "upd-1",
                    "body": "Shipped",
                    "createdAt": "2024-06-01T10:00:00.000Z",
                    "url": "https://linear.app/org/update/1",
                    "user": {"id": "u1", "displayName": "Alice"},
                    "comments": {
                        "nodes": [{
                            "id": "uc1",
                            "body": "See [spec.pdf](https://files/spec.pdf)",
                            "user": {"id": "u2", "displayName": "Bob"},
                            "url": "https://linear.app/org/update/1#c1",
                            "createdAt": "2024-06-01T11:00:00.000Z",
                            "updatedAt": "2024-06-01T11:00:00.000Z",
                            "quotedText": "Prior note",
                            "parent": {"id": "uc0"},
                        }],
                    },
                }],
            },
            "projectMilestones": {"nodes": []},
            "externalLinks": {"nodes": []},
            "documents": {"nodes": []},
        }

        with patch.object(
            conn,
            "_convert_images_to_base64_in_markdown",
            new=AsyncMock(side_effect=lambda text: text),
        ), patch.object(
            conn,
            "_extract_file_urls_from_markdown",
            return_value=[{"filename": "spec.pdf", "url": "https://files/spec.pdf"}],
        ):
            container = await conn._parse_project_to_blocks(project, weburl=project["url"])

        update_groups = [bg for bg in container.block_groups if bg.sub_type == GroupSubType.UPDATE]
        assert update_groups
        assert update_groups[0].comments
        assert update_groups[0].comments[0][0].attachments


class TestIssueStreamingMinimal:
    @pytest.mark.asyncio
    async def test_streams_issue_without_optional_sections(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = _ticket_record()
        issue = _make_issue_payload(
            description="",
            comments={"nodes": []},
            attachments={"nodes": []},
            documents={"nodes": []},
        )
        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(return_value=MagicMock(success=True, data={"issue": issue}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_convert_images_to_base64_in_markdown",
            new=AsyncMock(side_effect=lambda text: text),
        ):
            result = await conn._process_issue_blockgroups_for_streaming(record)

        assert isinstance(result, bytes)
        assert b"block_groups" in result


class TestFetchDocumentContentExtended:
    @pytest.mark.asyncio
    async def test_fetch_document_content_converts_images(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(return_value=MagicMock(
            success=True,
            data={"document": {"id": "doc-1", "content": "# Title\n![img](https://img/x.png)"}},
        ))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn,
            "_convert_images_to_base64_in_markdown",
            new=AsyncMock(return_value="# Title\n![img](data:image/png;base64,abc)"),
        ) as mock_convert:
            content = await conn._fetch_document_content("doc-1")

        mock_convert.assert_awaited_once()
        assert "base64" in content


class TestStreamRecordErrors:
    @pytest.mark.asyncio
    async def test_file_stream_download_error_raises(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.external_client = MagicMock()
        record = _file_record(
            external_record_id="https://files.example.com/report.pdf",
            mime_type="application/pdf",
        )

        async def failing_download(_url):
            raise RuntimeError("download failed")
            yield b""  # pragma: no cover

        mock_ds = MagicMock()
        mock_ds.download_file = failing_download
        conn._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        response = await conn.stream_record(record)
        with pytest.raises(RuntimeError, match="download failed"):
            async for _ in response.body_iterator:
                pass

    @pytest.mark.asyncio
    async def test_file_stream_inits_when_datasource_missing(self):
        conn = _make_connector()
        conn.external_client = MagicMock()
        record = _file_record(
            external_record_id="https://files.example.com/report.pdf",
            mime_type="application/pdf",
        )

        async def download(_url):
            yield b"data"

        mock_ds = MagicMock()
        mock_ds.download_file = download

        async def fake_init():
            conn.data_source = MagicMock()
            return True

        init_mock = AsyncMock(side_effect=fake_init)
        conn.data_source = None
        conn.init = init_mock

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            response = await conn.stream_record(record)
            body = b"".join([chunk async for chunk in response.body_iterator])

        init_mock.assert_awaited_once()
        assert body == b"data"


class TestReindexFullFlows:
    @pytest.mark.asyncio
    async def test_issue_changed_returns_updated_ticket(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = _ticket_record(source_updated_at=1700000000000)
        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(return_value=MagicMock(
            success=True,
            data={"issue": _make_issue_payload(updatedAt="2024-06-01T10:00:00.000Z")},
        ))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_issue(record)

        assert result is not None
        updated, perms = result
        assert isinstance(updated, TicketRecord)
        assert perms == []

    @pytest.mark.asyncio
    async def test_project_changed_returns_updated_project(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = _project_record(source_updated_at=1700000000000)
        project_data = {
            "id": "proj-1",
            "name": "Alpha",
            "url": "https://linear.app/org/project/alpha",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "issues": {"nodes": [{"id": "iss-linked"}]},
        }
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=MagicMock(success=True, data={"project": project_data}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_project(record)

        assert result is not None
        updated, _ = result
        assert updated.related_external_records

    @pytest.mark.asyncio
    async def test_link_routes_to_project_external_link_checker(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = LinkRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="External",
            record_type=RecordType.LINK,
            external_record_id="link-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            parent_external_record_id="proj-1",
            parent_node_id="node-1",
            url="https://example.com/docs",
            is_public=LinkPublicStatus.UNKNOWN,
            source_updated_at=1700000000000,
        )
        parent = _project_record(id="node-1", external_record_id="proj-1")
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)

        project_data = {
            "id": "proj-1",
            "externalLinks": {
                "nodes": [{
                    "id": "link-1",
                    "url": "https://example.com/docs",
                    "label": "Docs",
                    "updatedAt": "2024-06-01T10:00:00.000Z",
                    "createdAt": "2024-01-01T00:00:00.000Z",
                }],
            },
        }
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=MagicMock(success=True, data={"project": project_data}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_record(record)

        assert result is not None

    @pytest.mark.asyncio
    async def test_issue_link_reindex_links_related_record(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = LinkRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Attachment",
            record_type=RecordType.LINK,
            external_record_id="att-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            parent_external_record_id="iss-1",
            parent_node_id="node-1",
            url="https://example.com/spec.pdf",
            is_public=LinkPublicStatus.UNKNOWN,
            source_updated_at=1700000000000,
        )
        parent = _ticket_record(id="node-1", external_record_id="iss-1")
        related = MagicMock()
        related.id = "related-node"
        conn._tx_store.get_record_by_weburl = AsyncMock(return_value=related)

        attach_data = {
            "id": "att-1",
            "url": "https://example.com/spec.pdf",
            "title": "Spec",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "issue": {"id": "iss-1"},
        }
        mock_ds = MagicMock()
        mock_ds.attachment = AsyncMock(return_value=MagicMock(success=True, data={"attachment": attach_data}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_issue_link(record, parent)

        assert result is not None
        updated, _ = result
        assert updated.linked_record_id == "related-node"

    @pytest.mark.asyncio
    async def test_unsupported_record_type_for_reindex(self):
        conn = _make_connector()
        record = Record(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Unknown",
            record_type=RecordType.DRIVE,
            external_record_id="x-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
        )
        result = await conn._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_reindex_records_updates_changed_and_reindexes_unchanged(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        changed = _ticket_record(source_updated_at=1700000000000)
        unchanged = _ticket_record(
            id=str(uuid4()),
            external_record_id="iss-2",
            source_updated_at=1704067200000,
        )
        updated_ticket = _ticket_record(source_updated_at=1717200000000)

        async def check_record(record):
            if record.external_record_id == "iss-1":
                return (updated_ticket, [])
            return None

        with patch.object(conn, "_check_and_fetch_updated_record", new=AsyncMock(side_effect=check_record)), patch.object(
            conn, "_get_fresh_datasource", new=AsyncMock(return_value=MagicMock()),
        ):
            await conn.reindex_records([changed, unchanged])

        conn.data_entities_processor.on_new_records.assert_awaited_once()
        conn.data_entities_processor.reindex_existing_records.assert_awaited_once_with([unchanged])

    @pytest.mark.asyncio
    async def test_reindex_records_skips_untyped_base_record(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        base = Record(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Base",
            record_type=RecordType.TICKET,
            external_record_id="iss-base",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
        )

        with patch.object(conn, "_check_and_fetch_updated_record", new=AsyncMock(return_value=None)), patch.object(
            conn, "_get_fresh_datasource", new=AsyncMock(return_value=MagicMock()),
        ):
            await conn.reindex_records([base])

        conn.data_entities_processor.reindex_existing_records.assert_not_called()


class TestFileTransformExtended:
    @pytest.mark.asyncio
    async def test_existing_file_reuses_version_and_size(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        existing = _file_record(
            external_record_id="https://files.example.com/spec.pdf",
            version=3,
        )
        existing.size_in_bytes = 4096
        existing.created_at = 1700000000000

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock()):
            result = await conn._transform_file_url_to_file_record(
                file_url="https://files.example.com/spec.pdf",
                filename="spec.pdf",
                parent_external_id="iss-1",
                parent_node_id="node-1",
                parent_record_type=RecordType.TICKET,
                team_id="team-1",
                existing_record=existing,
                parent_created_at=1700000000000,
                parent_updated_at=1700000000000,
            )

        assert result.version == 3
        assert result.size_in_bytes == 4096


class TestProjectExternalLinksExtended:
    @pytest.mark.asyncio
    async def test_project_external_link_indexing_off_and_weburl_error(self):
        conn = _make_connector()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=False)
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        conn._tx_store.get_record_by_weburl = AsyncMock(side_effect=RuntimeError("lookup failed"))

        links = [{
            "id": "link-1",
            "url": "https://example.com/docs",
            "label": "Docs",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
        }]

        records, block_groups = await conn._process_project_external_links(
            external_links_data=links,
            project_id="proj-1",
            project_node_id="node-1",
            team_id="team-1",
            tx_store=conn._tx_store,
            create_block_groups=True,
        )

        assert records
        assert records[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert block_groups


class TestConnectionExtended:
    @pytest.mark.asyncio
    async def test_connection_success(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(return_value=MagicMock(success=True, data={"organization": {}}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            assert await conn.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_connection_failure_response(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(return_value=MagicMock(success=False, message="unauthorized"))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            assert await conn.test_connection_and_access() is False


class TestCoverageBoostFinal:
    @pytest.mark.asyncio
    async def test_sync_documents_standalone_document(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()

        doc = {
            "id": "doc-standalone",
            "title": "Notes",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "issue": None,
            "project": None,
        }
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=_documents_resp([doc]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(conn, "_update_documents_sync_checkpoint", new=AsyncMock()) as mock_update:
            await conn._sync_documents([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()
        mock_update.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_documents_skips_missing_team_and_parent(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg("team-1")

        docs = [
            {
                "id": "doc-no-team",
                "title": "A",
                "url": "https://linear.app/org/document/a",
                "updatedAt": "2024-06-01T10:00:00.000Z",
                "issue": {"id": "issue-1", "identifier": "ENG-1", "team": {"id": "other-team"}},
            },
            {
                "id": "doc-no-parent",
                "title": "B",
                "url": "https://linear.app/org/document/b",
                "updatedAt": "2024-06-01T10:00:00.000Z",
                "issue": {"id": "issue-2", "identifier": "ENG-2", "team": {"id": "team-1"}},
            },
        ]
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=_documents_resp(docs))
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ):
            await conn._sync_documents([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_documents_outer_exception(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        rg, perms = _make_team_rg()

        with patch.object(conn, "_get_documents_sync_checkpoint", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await conn._sync_documents([(rg, perms)])

    @pytest.mark.asyncio
    async def test_sync_projects_for_teams_incremental(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        rg, perms = _make_team_rg()

        async def empty_batch(*_args, **_kwargs):
            if False:
                yield []

        with patch.object(conn, "_get_team_project_sync_checkpoint", new=AsyncMock(return_value=1704067200000)), patch.object(
            conn, "_fetch_projects_for_team_batch", side_effect=empty_batch,
        ), patch.object(conn, "_update_team_project_sync_checkpoint", new=AsyncMock()):
            await conn._sync_projects_for_teams([(rg, perms)])

    @pytest.mark.asyncio
    async def test_process_issue_attachments_creates_record_with_indexing_off(self):
        conn = _make_connector()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=False)
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        attachments = [{
            "id": "att-1",
            "url": "https://example.com/a.pdf",
            "title": "A",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
        }]

        children = await conn._process_issue_attachments(
            attachments_data=attachments,
            issue_id="iss-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=conn._tx_store,
        )

        assert children
        saved = conn.data_entities_processor.on_new_records.call_args[0][0]
        assert saved[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_process_issue_attachments_error_continues(self):
        conn = _make_connector()
        conn._tx_store.get_record_by_external_id = AsyncMock(side_effect=RuntimeError("db"))

        children = await conn._process_issue_attachments(
            attachments_data=[{"id": "att-bad"}],
            issue_id="iss-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=conn._tx_store,
        )

        assert children == []

    @pytest.mark.asyncio
    async def test_process_issue_documents_creates_record(self):
        conn = _make_connector()
        conn.indexing_filters = None
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        documents = [{
            "id": "doc-1",
            "title": "Spec",
            "url": "https://linear.app/org/document/doc-1",
            "content": "# Spec",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
        }]

        children = await conn._process_issue_documents(
            documents_data=documents,
            issue_id="iss-1",
            issue_node_id="node-1",
            team_id="team-1",
            tx_store=conn._tx_store,
        )

        assert children
        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_comment_files_persists_new_records(self):
        conn = _make_connector()
        conn.indexing_filters = None
        file_rec = _file_record()
        comments = [{
            "id": "c1",
            "body": "[notes.pdf](https://files/notes.pdf)",
            "createdAt": "2024-06-01T10:00:00.000Z",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "url": "https://linear.app/org/issue/ENG-1#c1",
        }]

        with patch.object(
            conn,
            "_extract_files_from_markdown",
            new=AsyncMock(return_value=([(file_rec, [])], [])),
        ):
            result = await conn._process_comment_files_for_children(
                comments_data=comments,
                issue_id="iss-1",
                issue_node_id="node-1",
                team_id="team-1",
                issue_weburl="https://linear.app/org/issue/ENG-1",
                tx_store=conn._tx_store,
            )

        assert "c1" in result
        conn.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_issue_streaming_raises_when_issue_data_missing(self):
        conn = _make_connector()
        record = _ticket_record()
        mock_ds = MagicMock()
        mock_ds.issue = AsyncMock(return_value=MagicMock(success=True, data={"issue": {}}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            with pytest.raises(Exception, match="No issue data found"):
                await conn._process_issue_blockgroups_for_streaming(record)

    @pytest.mark.asyncio
    async def test_fetch_document_content_empty_returns_blank(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        mock_ds = MagicMock()
        mock_ds.document = AsyncMock(return_value=MagicMock(
            success=True,
            data={"document": {"id": "doc-1", "content": ""}},
        ))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            assert await conn._fetch_document_content("doc-1") == ""

    @pytest.mark.asyncio
    async def test_link_reindex_parent_not_found(self):
        conn = _make_connector()
        record = LinkRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Link",
            record_type=RecordType.LINK,
            external_record_id="link-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            parent_external_record_id="missing-parent",
            url="https://example.com",
            is_public=LinkPublicStatus.UNKNOWN,
        )
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        result = await conn._check_and_fetch_updated_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_document_reindex_missing_external_id(self):
        conn = _make_connector()
        record = WebpageRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Doc",
            record_type=RecordType.WEBPAGE,
            external_record_id="",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            parent_node_id="node-1",
        )
        result = await conn._check_and_fetch_updated_document(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_project_reindex_not_found(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = _project_record()
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=MagicMock(success=True, data={"project": {}}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_project(record)

        assert result is None

    @pytest.mark.asyncio
    async def test_project_link_reindex_with_weburl_lookup(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = LinkRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Docs",
            record_type=RecordType.LINK,
            external_record_id="link-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            parent_external_record_id="proj-1",
            parent_node_id="node-1",
            url="https://example.com/docs",
            is_public=LinkPublicStatus.UNKNOWN,
            source_updated_at=1700000000000,
        )
        parent = _project_record(id="node-1", external_record_id="proj-1")
        related = MagicMock()
        related.id = "related-node"
        conn._tx_store.get_record_by_weburl = AsyncMock(return_value=related)

        project_data = {
            "id": "proj-1",
            "externalLinks": {
                "nodes": [{
                    "id": "link-1",
                    "url": "https://example.com/docs",
                    "label": "Docs",
                    "updatedAt": "2024-06-01T10:00:00.000Z",
                    "createdAt": "2024-01-01T00:00:00.000Z",
                }],
            },
        }
        mock_ds = MagicMock()
        mock_ds.project = AsyncMock(return_value=MagicMock(success=True, data={"project": project_data}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_project_link(record, parent)

        assert result is not None
        updated, _ = result
        assert updated.linked_record_id == "related-node"

    @pytest.mark.asyncio
    async def test_cleanup_swallows_client_close_error(self):
        conn = _make_connector()
        conn.external_client = MagicMock()
        conn.external_client.get_client.side_effect = RuntimeError("already closed")

        await conn.cleanup()

    @pytest.mark.asyncio
    async def test_reindex_records_handles_not_implemented(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        unchanged = _ticket_record(source_updated_at=1704067200000)

        conn.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=NotImplementedError("kafka missing"),
        )

        with patch.object(conn, "_check_and_fetch_updated_record", new=AsyncMock(return_value=None)), patch.object(
            conn, "_get_fresh_datasource", new=AsyncMock(return_value=MagicMock()),
        ):
            await conn.reindex_records([unchanged])

    @pytest.mark.asyncio
    async def test_reindex_records_outer_exception_raises(self):
        conn = _make_connector()
        conn.data_source = MagicMock()

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(side_effect=RuntimeError("boom"))):
            with pytest.raises(RuntimeError, match="boom"):
                await conn.reindex_records([_ticket_record()])

    @pytest.mark.asyncio
    async def test_sync_documents_skips_empty_document_id(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()

        doc = {
            "id": "",
            "title": "NoId",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "issue": {"id": "issue-1", "identifier": "ENG-1", "team": {"id": "team-1"}},
        }
        parent = MagicMock()
        parent.id = "parent-node"
        conn._tx_store.get_record_by_external_id = AsyncMock(return_value=parent)
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=_documents_resp([doc]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ):
            await conn._sync_documents([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_documents_skips_missing_issue_id(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.sync_filters = None
        conn.indexing_filters = None
        rg, perms = _make_team_rg()

        doc = {
            "id": "doc-1",
            "title": "Partial",
            "url": "https://linear.app/org/document/doc-1",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "issue": {"id": "", "identifier": "ENG-1", "team": {"id": "team-1"}},
        }
        mock_ds = MagicMock()
        mock_ds.documents = AsyncMock(return_value=_documents_resp([doc]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_get_documents_sync_checkpoint", new=AsyncMock(return_value=None),
        ):
            await conn._sync_documents([(rg, perms)])

        conn.data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_projects_batch_stops_without_end_cursor(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.indexing_filters = None
        conn.organization_url_key = "org"

        project_summary = {
            "id": "proj-1",
            "name": "Alpha",
            "url": "https://linear.app/org/project/alpha",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-06-01T00:00:00.000Z",
        }
        mock_ds = MagicMock()
        mock_ds.projects = AsyncMock(return_value=_projects_resp(
            [project_summary], has_next=True, end_cursor=None,
        ))
        mock_ds.project = AsyncMock(return_value=MagicMock(success=True, data={"project": project_summary}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)), patch.object(
            conn, "_prepare_project_related_records", new=AsyncMock(return_value=[]),
        ), patch.object(conn, "_extract_files_from_markdown", new=AsyncMock(return_value=([], []))):
            batches = []
            async for batch in conn._fetch_projects_for_team_batch("team-1", "ENG"):
                batches.append(batch)

        assert batches

    @pytest.mark.asyncio
    async def test_connection_inits_when_datasource_missing(self):
        conn = _make_connector()
        mock_ds = MagicMock()
        mock_ds.organization = AsyncMock(return_value=MagicMock(success=True, data={"organization": {}}))

        async def set_ds():
            conn.data_source = MagicMock()
            return True

        conn.data_source = None
        conn.init = AsyncMock(side_effect=set_ds)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            assert await conn.test_connection_and_access() is True

        conn.init.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_issue_link_reindex_unchanged_returns_none(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        record = LinkRecord(
            id=str(uuid4()),
            org_id="org-linear-gaps",
            record_name="Attachment",
            record_type=RecordType.LINK,
            external_record_id="att-1",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id="linear-gap",
            external_record_group_id="team-1",
            parent_external_record_id="iss-1",
            parent_node_id="node-1",
            url="https://example.com/spec.pdf",
            is_public=LinkPublicStatus.UNKNOWN,
            source_updated_at=1717236000000,
        )
        attach_data = {
            "id": "att-1",
            "url": "https://example.com/spec.pdf",
            "title": "Spec",
            "updatedAt": "2024-06-01T10:00:00.000Z",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "issue": {"id": "iss-1"},
        }
        mock_ds = MagicMock()
        mock_ds.attachment = AsyncMock(return_value=MagicMock(success=True, data={"attachment": attach_data}))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=mock_ds)):
            result = await conn._check_and_fetch_updated_issue_link(record, _ticket_record(id="node-1"))

        assert result is None

