"""Deep-sync-loop tests for LinearConnector.

Covers: run_sync, _sync_issues_for_teams, _fetch_issues_for_team_batch,
_sync_attachments, _sync_documents, _sync_projects_for_teams,
_fetch_projects_for_team_batch, _sync_deleted_issues, _sync_deleted_projects,
_fetch_users, _fetch_teams, sync checkpoints, _apply_date_filters_to_linear_filter,
_process_issue_attachments, _process_issue_documents.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.connectors.sources.linear.connector import (
    LINEAR_CONFIG_PATH,
    LinearConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    LinkRecord,
    ProjectRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_connector():
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_all_active_users = AsyncMock(return_value=[MagicMock(email="u@x.com")])
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_record_deleted = AsyncMock()

    dsp = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

    class FakeTx:
        async def __aenter__(self):
            return mock_tx_store
        async def __aexit__(self, *args):
            pass

    dsp.transaction = MagicMock(return_value=FakeTx())

    cs = AsyncMock()
    connector = LinearConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="linear-1",
    )
    return connector


def _mock_gql_response(data=None, success=True, message=""):
    resp = MagicMock()
    resp.success = success
    resp.data = data or {}
    resp.message = message
    return resp


def _team_rg(team_id="t1", key="ENG"):
    return RecordGroup(
        id=str(uuid4()),
        org_id="org-1",
        external_group_id=team_id,
        connector_id="linear-1",
        connector_name=Connectors.LINEAR,
        name=f"Team {key}",
        short_name=key,
        group_type=RecordGroupType.PROJECT,
    )


def _make_issue_data(issue_id="iss-1", identifier="ENG-1", updated="2024-06-01T00:00:00.000Z"):
    return {
        "id": issue_id,
        "identifier": identifier,
        "title": f"Issue {identifier}",
        "description": "Some description",
        "state": {"name": "In Progress", "type": "started"},
        "priority": 2,
        "priorityLabel": "High",
        "creator": {"email": "u@x.com", "name": "User"},
        "assignee": {"email": "u@x.com", "name": "User"},
        "createdAt": "2024-01-01T00:00:00.000Z",
        "updatedAt": updated,
        "url": f"https://linear.app/org/issue/{identifier}",
        "team": {"id": "t1"},
        "comments": {"nodes": []},
    }


# ===========================================================================
# run_sync orchestration
# ===========================================================================


class TestLinearRunSync:

    @pytest.mark.asyncio
    async def test_initializes_if_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None
        connector.init = AsyncMock(return_value=True)

        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            connector._fetch_users = AsyncMock(return_value=[])
            connector._fetch_teams = AsyncMock(return_value=([], []))
            connector._sync_issues_for_teams = AsyncMock()
            connector._sync_attachments = AsyncMock()
            connector._sync_documents = AsyncMock()
            connector._sync_projects_for_teams = AsyncMock()
            connector._sync_deleted_issues = AsyncMock()
            connector._sync_deleted_projects = AsyncMock()

            await connector.run_sync()
            connector.init.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_active_users_returns_early(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            await connector.run_sync()
            connector.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_full_12_step_flow(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        user = MagicMock()
        user.email = "u@x.com"
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[user])

        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())

            rg = _team_rg()
            connector._fetch_users = AsyncMock(return_value=[_make_app_user()])
            connector._fetch_teams = AsyncMock(return_value=(
                [(MagicMock(), [])],  # user groups
                [(rg, [])],  # record groups
            ))
            connector._sync_issues_for_teams = AsyncMock()
            connector._sync_attachments = AsyncMock()
            connector._sync_documents = AsyncMock()
            connector._sync_projects_for_teams = AsyncMock()
            connector._sync_deleted_issues = AsyncMock()
            connector._sync_deleted_projects = AsyncMock()

            await connector.run_sync()

            connector._fetch_users.assert_awaited_once()
            connector._fetch_teams.assert_awaited_once()
            connector._sync_issues_for_teams.assert_awaited_once()
            connector._sync_attachments.assert_awaited_once()
            connector._sync_documents.assert_awaited_once()
            connector._sync_projects_for_teams.assert_awaited_once()
            connector._sync_deleted_issues.assert_awaited_once()
            connector._sync_deleted_projects.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propagates_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            side_effect=RuntimeError("DB crash")
        )
        with patch(
            "app.connectors.sources.linear.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            with pytest.raises(RuntimeError, match="DB crash"):
                await connector.run_sync()


def _make_app_user(email="u@x.com"):
    return AppUser(
        app_name=Connectors.LINEAR,
        connector_id="linear-1",
        source_user_id="u-1",
        org_id="org-1",
        email=email,
        full_name="User",
        is_active=True,
    )


# ===========================================================================
# _fetch_users (cursor pagination)
# ===========================================================================


class TestFetchUsers:

    @pytest.mark.asyncio
    async def test_single_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.users = AsyncMock(return_value=_mock_gql_response({
            "users": {
                "nodes": [
                    {"id": "u1", "email": "a@x.com", "name": "Alice", "active": True},
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users = await connector._fetch_users()
        assert len(users) == 1
        assert users[0].email == "a@x.com"

    @pytest.mark.asyncio
    async def test_multi_page_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        page1 = _mock_gql_response({
            "users": {
                "nodes": [{"id": "u1", "email": "a@x.com", "name": "A", "active": True}],
                "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
            }
        })
        page2 = _mock_gql_response({
            "users": {
                "nodes": [{"id": "u2", "email": "b@x.com", "name": "B", "active": True}],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        })
        ds.users = AsyncMock(side_effect=[page1, page2])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users = await connector._fetch_users()
        assert len(users) == 2

    @pytest.mark.asyncio
    async def test_skips_inactive_and_no_email(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.users = AsyncMock(return_value=_mock_gql_response({
            "users": {
                "nodes": [
                    {"id": "u1", "email": "a@x.com", "name": "A", "active": True},
                    {"id": "u2", "email": None, "name": "NoEmail", "active": True},
                    {"id": "u3", "email": "c@x.com", "name": "C", "active": False},
                ],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users = await connector._fetch_users()
        assert len(users) == 1

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.users = AsyncMock(return_value=_mock_gql_response(success=False, message="Unauthorized"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        with pytest.raises(RuntimeError, match="Failed to fetch users"):
            await connector._fetch_users()


# ===========================================================================
# _fetch_teams
# ===========================================================================


class TestFetchTeams:

    @pytest.mark.asyncio
    async def test_public_team_gets_org_permission(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"
        ds = MagicMock()
        ds.teams = AsyncMock(return_value=_mock_gql_response({
            "teams": {
                "nodes": [{
                    "id": "t1", "name": "Engineering", "key": "ENG",
                    "description": "Eng", "private": False,
                    "parent": None, "members": {"nodes": []},
                }],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        ugs, rgs = await connector._fetch_teams()
        assert len(rgs) == 1
        _, perms = rgs[0]
        assert perms[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_private_team_gets_group_permission(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"
        ds = MagicMock()
        ds.teams = AsyncMock(return_value=_mock_gql_response({
            "teams": {
                "nodes": [{
                    "id": "t-sec", "name": "Secret", "key": "SEC",
                    "description": "Secret", "private": True,
                    "parent": None, "members": {"nodes": [{"email": "a@x.com"}]},
                }],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        _, rgs = await connector._fetch_teams(user_email_map={"a@x.com": _make_app_user("a@x.com")})
        _, perms = rgs[0]
        assert perms[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_team_with_parent(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"
        ds = MagicMock()
        ds.teams = AsyncMock(return_value=_mock_gql_response({
            "teams": {
                "nodes": [{
                    "id": "t-child", "name": "Frontend", "key": "FE",
                    "description": "FE", "private": False,
                    "parent": {"id": "t-parent", "name": "Engineering"},
                    "members": {"nodes": []},
                }],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        _, rgs = await connector._fetch_teams()
        rg, _ = rgs[0]
        assert rg.parent_external_group_id == "t-parent"

    @pytest.mark.asyncio
    async def test_filter_in_teams(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "org"
        ds = MagicMock()
        ds.teams = AsyncMock(return_value=_mock_gql_response({
            "teams": {
                "nodes": [{
                    "id": "t1", "name": "ENG", "key": "ENG",
                    "description": "", "private": False,
                    "parent": None, "members": {"nodes": []},
                }],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        _, rgs = await connector._fetch_teams(team_ids=["t1"])
        assert len(rgs) == 1
        # Verify filter was passed
        call_args = ds.teams.call_args
        assert call_args.kwargs.get("filter") == {"id": {"in": ["t1"]}}

    @pytest.mark.asyncio
    async def test_multi_page_teams(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "org"
        ds = MagicMock()
        page1 = _mock_gql_response({
            "teams": {
                "nodes": [{
                    "id": "t1", "name": "T1", "key": "T1",
                    "description": "", "private": False,
                    "parent": None, "members": {"nodes": []},
                }],
                "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
            }
        })
        page2 = _mock_gql_response({
            "teams": {
                "nodes": [{
                    "id": "t2", "name": "T2", "key": "T2",
                    "description": "", "private": False,
                    "parent": None, "members": {"nodes": []},
                }],
                "pageInfo": {"hasNextPage": False},
            }
        })
        ds.teams = AsyncMock(side_effect=[page1, page2])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        _, rgs = await connector._fetch_teams()
        assert len(rgs) == 2


# ===========================================================================
# _sync_issues_for_teams
# ===========================================================================


class TestSyncIssuesForTeams:

    @pytest.mark.asyncio
    async def test_empty_teams_noop(self):
        connector = _make_connector()
        await connector._sync_issues_for_teams([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processes_batches(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_team_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_team_sync_checkpoint = AsyncMock()

        mock_ticket = MagicMock(spec=TicketRecord)
        mock_ticket.source_updated_at = 1700000001000

        async def gen(**kw):
            yield [(mock_ticket, [])]

        connector._fetch_issues_for_team_batch = gen

        await connector._sync_issues_for_teams([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector._update_team_sync_checkpoint.assert_awaited()

    @pytest.mark.asyncio
    async def test_continues_on_team_error(self):
        connector = _make_connector()
        rg1 = _team_rg("t1", "T1")
        rg2 = _team_rg("t2", "T2")
        connector._get_team_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_team_sync_checkpoint = AsyncMock()

        call_count = 0

        async def gen(**kw):
            nonlocal call_count
            call_count += 1
            if kw.get("team_key") == "T1":
                raise RuntimeError("API error")
            yield []

        connector._fetch_issues_for_team_batch = gen

        await connector._sync_issues_for_teams([(rg1, []), (rg2, [])])
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_skips_team_without_external_id(self):
        connector = _make_connector()
        rg = _team_rg()
        rg.external_group_id = None

        await connector._sync_issues_for_teams([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# _fetch_issues_for_team_batch (GraphQL pagination)
# ===========================================================================


class TestFetchIssuesForTeamBatch:

    @pytest.mark.asyncio
    async def test_single_page(self):
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        ds = MagicMock()
        ds.issues = AsyncMock(return_value=_mock_gql_response({
            "issues": {
                "nodes": [_make_issue_data()],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_issue_to_ticket_record = MagicMock(return_value=MagicMock(
            spec=TicketRecord, id="rec-1", weburl="https://linear.app/issue/ENG-1",
            indexing_status=None, source_updated_at=1700000000000,
        ))
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))

        batches = []
        async for batch in connector._fetch_issues_for_team_batch(
            team_id="t1", team_key="ENG"
        ):
            batches.append(batch)

        assert len(batches) == 1
        assert len(batches[0]) >= 1

    @pytest.mark.asyncio
    async def test_multi_page_cursor(self):
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None

        call_count = 0

        async def mock_issues(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_gql_response({
                    "issues": {
                        "nodes": [_make_issue_data("i1", "ENG-1")],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
                    }
                })
            return _mock_gql_response({
                "issues": {
                    "nodes": [_make_issue_data("i2", "ENG-2")],
                    "pageInfo": {"hasNextPage": False},
                }
            })

        ds = MagicMock()
        ds.issues = AsyncMock(side_effect=mock_issues)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_issue_to_ticket_record = MagicMock(return_value=MagicMock(
            spec=TicketRecord, id="rec-1", weburl="url", indexing_status=None,
            source_updated_at=1700000000000,
        ))
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))

        batches = []
        async for batch in connector._fetch_issues_for_team_batch(
            team_id="t1", team_key="ENG"
        ):
            batches.append(batch)

        assert len(batches) == 2

    @pytest.mark.asyncio
    async def test_api_failure_breaks(self):
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        ds = MagicMock()
        ds.issues = AsyncMock(return_value=_mock_gql_response(success=False, message="Error"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        batches = []
        async for batch in connector._fetch_issues_for_team_batch(
            team_id="t1", team_key="ENG"
        ):
            batches.append(batch)

        assert len(batches) == 0


# ===========================================================================
# _sync_attachments
# ===========================================================================


class TestSyncAttachments:

    @pytest.mark.asyncio
    async def test_empty_teams_noop(self):
        connector = _make_connector()
        await connector._sync_attachments([])

    @pytest.mark.asyncio
    async def test_processes_attachment_batches(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_attachments_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_attachments_sync_checkpoint = AsyncMock()

        attachment_data = {
            "id": "att-1",
            "title": "Spec Doc",
            "url": "https://example.com/spec",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
            "issue": {"id": "iss-1", "team": {"id": "t1"}},
        }

        ds = MagicMock()
        ds.attachments = AsyncMock(return_value=_mock_gql_response({
            "attachments": {
                "nodes": [attachment_data],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.sync_filters = None
        connector.indexing_filters = None

        # Setup tx_store to find parent issue
        mock_parent = MagicMock()
        mock_parent.id = "parent-rec-id"
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(side_effect=lambda **kw: mock_parent if kw.get("external_id") == "iss-1" else None)
        mock_tx_store.get_record_by_weburl = AsyncMock(return_value=None)

        class FakeTx:
            async def __aenter__(self):
                return mock_tx_store
            async def __aexit__(self, *args):
                pass

        connector.data_store_provider.transaction = MagicMock(return_value=FakeTx())
        connector._transform_attachment_to_link_record = MagicMock(return_value=MagicMock(
            spec=LinkRecord, source_updated_at=1700000001000, weburl="https://example.com",
            indexing_status=None,
        ))

        await connector._sync_attachments([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_attachments_from_unknown_teams(self):
        connector = _make_connector()
        rg = _team_rg("t1", "ENG")
        connector._get_attachments_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_attachments_sync_checkpoint = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        # Attachment belongs to team t-unknown
        ds = MagicMock()
        ds.attachments = AsyncMock(return_value=_mock_gql_response({
            "attachments": {
                "nodes": [{
                    "id": "att-1",
                    "issue": {"id": "iss-1", "team": {"id": "t-unknown"}},
                }],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_tx_store = AsyncMock()

        class FakeTx:
            async def __aenter__(self):
                return mock_tx_store
            async def __aexit__(self, *args):
                pass

        connector.data_store_provider.transaction = MagicMock(return_value=FakeTx())

        await connector._sync_attachments([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# _sync_documents
# ===========================================================================


class TestSyncDocuments:

    @pytest.mark.asyncio
    async def test_empty_teams_noop(self):
        connector = _make_connector()
        await connector._sync_documents([])

    @pytest.mark.asyncio
    async def test_processes_document_batches(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_documents_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_documents_sync_checkpoint = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        doc_data = {
            "id": "doc-1",
            "title": "Design Doc",
            "content": "# Design\nSome content",
            "createdAt": "2024-01-01T00:00:00.000Z",
            "updatedAt": "2024-01-02T00:00:00.000Z",
            "issue": {"id": "iss-1", "identifier": "ENG-1", "team": {"id": "t1"}},
        }

        ds = MagicMock()
        ds.documents = AsyncMock(return_value=_mock_gql_response({
            "documents": {
                "nodes": [doc_data],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_parent = MagicMock()
        mock_parent.id = "parent-rec-id"
        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(side_effect=lambda **kw: mock_parent if kw.get("external_id") == "iss-1" else None)

        class FakeTx:
            async def __aenter__(self):
                return mock_tx_store
            async def __aexit__(self, *args):
                pass

        connector.data_store_provider.transaction = MagicMock(return_value=FakeTx())
        connector._transform_document_to_webpage_record = MagicMock(return_value=MagicMock(
            spec=WebpageRecord, source_updated_at=1700000001000, indexing_status=None,
        ))

        await connector._sync_documents([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_standalone_documents(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_documents_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_documents_sync_checkpoint = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        # Document without parent issue
        ds = MagicMock()
        ds.documents = AsyncMock(return_value=_mock_gql_response({
            "documents": {
                "nodes": [{
                    "id": "doc-1", "title": "Orphan",
                    "updatedAt": "2024-01-01T00:00:00.000Z",
                    "issue": None, "project": None,
                }],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_tx_store = AsyncMock()

        class FakeTx:
            async def __aenter__(self):
                return mock_tx_store
            async def __aexit__(self, *args):
                pass

        connector.data_store_provider.transaction = MagicMock(return_value=FakeTx())

        await connector._sync_documents([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# _sync_projects_for_teams
# ===========================================================================


class TestSyncProjectsForTeams:

    @pytest.mark.asyncio
    async def test_empty_teams_noop(self):
        connector = _make_connector()
        await connector._sync_projects_for_teams([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processes_project_batches(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_team_project_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_team_project_sync_checkpoint = AsyncMock()

        mock_proj = MagicMock(spec=ProjectRecord)
        mock_proj.source_updated_at = 1700000001000

        async def gen(**kw):
            yield [(mock_proj, [])]

        connector._fetch_projects_for_team_batch = gen

        await connector._sync_projects_for_teams([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_continues_on_team_error(self):
        connector = _make_connector()
        rg1 = _team_rg("t1", "T1")
        rg2 = _team_rg("t2", "T2")
        connector._get_team_project_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_team_project_sync_checkpoint = AsyncMock()

        call_count = 0

        async def gen(**kw):
            nonlocal call_count
            call_count += 1
            if kw.get("team_key") == "T1":
                raise RuntimeError("fail")
            yield []

        connector._fetch_projects_for_team_batch = gen

        await connector._sync_projects_for_teams([(rg1, []), (rg2, [])])
        assert call_count == 2


# ===========================================================================
# _sync_deleted_issues
# ===========================================================================


class TestSyncDeletedIssues:

    @pytest.mark.asyncio
    async def test_empty_teams_noop(self):
        connector = _make_connector()
        await connector._sync_deleted_issues([])

    @pytest.mark.asyncio
    async def test_initial_sync_creates_checkpoint(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_deletion_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_deletion_sync_checkpoint = AsyncMock()

        await connector._sync_deleted_issues([(rg, [])])
        connector._update_deletion_sync_checkpoint.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_detects_trashed_issues(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_deletion_sync_checkpoint = AsyncMock(return_value=1700000000000)
        connector._update_deletion_sync_checkpoint = AsyncMock()
        connector._mark_record_and_children_deleted = AsyncMock()

        ds = MagicMock()
        ds.issues = AsyncMock(return_value=_mock_gql_response({
            "issues": {
                "nodes": [
                    {"id": "i1", "identifier": "ENG-1", "trashed": True, "archivedAt": "2024-06-01T00:00:00.000Z"},
                    {"id": "i2", "identifier": "ENG-2", "trashed": False, "archivedAt": "2024-06-01T00:00:00.000Z"},
                ],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_deleted_issues([(rg, [])])
        # Only trashed issue should be marked deleted
        connector._mark_record_and_children_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pagination_continues(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_deletion_sync_checkpoint = AsyncMock(return_value=1700000000000)
        connector._update_deletion_sync_checkpoint = AsyncMock()
        connector._mark_record_and_children_deleted = AsyncMock()

        call_count = 0

        async def mock_issues(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_gql_response({
                    "issues": {
                        "nodes": [{"id": "i1", "identifier": "E-1", "trashed": True, "archivedAt": "2024-06-01T00:00:00.000Z"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
                    }
                })
            return _mock_gql_response({
                "issues": {
                    "nodes": [{"id": "i2", "identifier": "E-2", "trashed": True, "archivedAt": "2024-06-02T00:00:00.000Z"}],
                    "pageInfo": {"hasNextPage": False},
                }
            })

        ds = MagicMock()
        ds.issues = AsyncMock(side_effect=mock_issues)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_deleted_issues([(rg, [])])
        assert connector._mark_record_and_children_deleted.await_count == 2


# ===========================================================================
# _sync_deleted_projects
# ===========================================================================


class TestSyncDeletedProjects:

    @pytest.mark.asyncio
    async def test_empty_teams_noop(self):
        connector = _make_connector()
        await connector._sync_deleted_projects([])

    @pytest.mark.asyncio
    async def test_initial_sync_creates_checkpoint(self):
        connector = _make_connector()
        rg = _team_rg()
        connector._get_deletion_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_deletion_sync_checkpoint = AsyncMock()

        await connector._sync_deleted_projects([(rg, [])])
        connector._update_deletion_sync_checkpoint.assert_awaited_once()


# ===========================================================================
# Sync checkpoints
# ===========================================================================


class TestSyncCheckpoints:

    @pytest.mark.asyncio
    async def test_get_team_sync_checkpoint_returns_value(self):
        connector = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1700000000000}
        )

        result = await connector._get_team_sync_checkpoint("ENG")
        assert result == 1700000000000

    @pytest.mark.asyncio
    async def test_get_team_sync_checkpoint_returns_none(self):
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

        await connector._update_team_sync_checkpoint("ENG", 1700000000000)
        connector.issues_sync_point.update_sync_point.assert_awaited_once_with(
            "team_ENG", {"last_sync_time": 1700000000000}
        )

    @pytest.mark.asyncio
    async def test_get_attachments_sync_checkpoint(self):
        connector = _make_connector()
        connector.attachments_sync_point = MagicMock()
        connector.attachments_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1700000000}
        )

        result = await connector._get_attachments_sync_checkpoint()
        assert result == 1700000000

    @pytest.mark.asyncio
    async def test_update_attachments_sync_checkpoint(self):
        connector = _make_connector()
        connector.attachments_sync_point = MagicMock()
        connector.attachments_sync_point.update_sync_point = AsyncMock()

        await connector._update_attachments_sync_checkpoint(1700000000)
        connector.attachments_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_documents_sync_checkpoint(self):
        connector = _make_connector()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.read_sync_point = AsyncMock(return_value=None)

        result = await connector._get_documents_sync_checkpoint()
        assert result is None

    @pytest.mark.asyncio
    async def test_update_documents_sync_checkpoint(self):
        connector = _make_connector()
        connector.documents_sync_point = MagicMock()
        connector.documents_sync_point.update_sync_point = AsyncMock()

        await connector._update_documents_sync_checkpoint(1700000000)
        connector.documents_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_team_project_sync_checkpoint(self):
        connector = _make_connector()
        connector.projects_sync_point = MagicMock()
        connector.projects_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 999}
        )

        result = await connector._get_team_project_sync_checkpoint("ENG")
        assert result == 999

    @pytest.mark.asyncio
    async def test_update_team_project_sync_checkpoint(self):
        connector = _make_connector()
        connector.projects_sync_point = MagicMock()
        connector.projects_sync_point.update_sync_point = AsyncMock()

        await connector._update_team_project_sync_checkpoint("ENG", 999)
        connector.projects_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_deletion_sync_checkpoint(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 888}
        )

        result = await connector._get_deletion_sync_checkpoint("issues")
        assert result == 888

    @pytest.mark.asyncio
    async def test_update_deletion_sync_checkpoint(self):
        connector = _make_connector()
        connector.deletion_sync_point = MagicMock()
        connector.deletion_sync_point.update_sync_point = AsyncMock()

        await connector._update_deletion_sync_checkpoint("issues", 888)
        connector.deletion_sync_point.update_sync_point.assert_awaited_once()


# ===========================================================================
# _linear_datetime_from_timestamp / _parse_linear_datetime
# ===========================================================================


class TestDatetimeConversion:

    def test_linear_datetime_from_timestamp(self):
        connector = _make_connector()
        result = connector._linear_datetime_from_timestamp(1704067200000)
        assert "2024-01-01" in result
        assert result.endswith("Z")

    def test_linear_datetime_from_invalid_timestamp(self):
        connector = _make_connector()
        result = connector._linear_datetime_from_timestamp(-99999999999999999)
        assert result == ""

    def test_parse_linear_datetime_valid(self):
        connector = _make_connector()
        result = connector._parse_linear_datetime("2024-01-01T00:00:00.000Z")
        assert result is not None
        assert result > 0

    def test_parse_linear_datetime_none(self):
        connector = _make_connector()
        assert connector._parse_linear_datetime(None) is None
        assert connector._parse_linear_datetime("") is None

    def test_parse_linear_datetime_invalid(self):
        connector = _make_connector()
        assert connector._parse_linear_datetime("not-a-date") is None


# ===========================================================================
# _apply_date_filters_to_linear_filter
# ===========================================================================


class TestApplyDateFilters:

    def test_no_filters_no_changes(self):
        connector = _make_connector()
        connector.sync_filters = None
        f = {}
        connector._apply_date_filters_to_linear_filter(f)
        # Should not add updatedAt filter
        assert "updatedAt" not in f

    def test_with_last_sync_time(self):
        connector = _make_connector()
        connector.sync_filters = None
        f = {}
        connector._apply_date_filters_to_linear_filter(f, last_sync_time=1700000000000)
        assert "updatedAt" in f

    def test_with_modified_filter(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import SyncFilterKey
        # Use MagicMock for sync_filters since FilterCollection is a Pydantic model
        mock_filter = MagicMock()
        mock_filter.get_value = MagicMock(return_value=(1700000000000, None))
        mock_sync_filters = MagicMock()
        mock_sync_filters.get = MagicMock(side_effect=lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None)
        connector.sync_filters = mock_sync_filters

        f = {}
        connector._apply_date_filters_to_linear_filter(f)
        assert "updatedAt" in f


# ===========================================================================
# _process_issue_attachments
# ===========================================================================


class TestProcessIssueAttachments:

    @pytest.mark.asyncio
    async def test_creates_link_records_for_new_attachments(self):
        connector = _make_connector()
        connector.indexing_filters = None
        attachment_data = [{
            "id": "att-1",
            "title": "Spec Doc",
            "url": "https://example.com/spec",
        }]

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._transform_attachment_to_link_record = MagicMock(return_value=MagicMock(
            id="link-rec-1", record_name="Spec Doc", indexing_status=None
        ))

        children = await connector._process_issue_attachments(
            attachment_data, "iss-1", "node-1", "t1", mock_tx_store
        )
        assert len(children) == 1
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reuses_existing_attachments(self):
        connector = _make_connector()
        connector.indexing_filters = None
        attachment_data = [{"id": "att-1"}]

        existing = MagicMock()
        existing.id = "existing-rec"
        existing.record_name = "Existing"

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        children = await connector._process_issue_attachments(
            attachment_data, "iss-1", "node-1", "t1", mock_tx_store
        )
        assert len(children) == 1
        # Should NOT create new records
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_attachments(self):
        connector = _make_connector()
        children = await connector._process_issue_attachments(
            [], "iss-1", "node-1", "t1", AsyncMock()
        )
        assert children == []


# ===========================================================================
# _process_issue_documents
# ===========================================================================


class TestProcessIssueDocuments:

    @pytest.mark.asyncio
    async def test_creates_records_for_new_documents(self):
        connector = _make_connector()
        connector.indexing_filters = None
        doc_data = [{"id": "doc-1", "title": "Design", "content": "# Design"}]

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

        connector._transform_document_to_webpage_record = MagicMock(return_value=MagicMock(
            id="web-rec-1", record_name="Design", indexing_status=None
        ))

        children = await connector._process_issue_documents(
            doc_data, "iss-1", "node-1", "t1", mock_tx_store
        )
        assert len(children) == 1
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reuses_existing_documents(self):
        connector = _make_connector()
        connector.indexing_filters = None
        doc_data = [{"id": "doc-1"}]

        existing = MagicMock()
        existing.id = "existing-doc"
        existing.record_name = "Existing Doc"

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing)

        children = await connector._process_issue_documents(
            doc_data, "iss-1", "node-1", "t1", mock_tx_store
        )
        assert len(children) == 1
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_documents(self):
        connector = _make_connector()
        children = await connector._process_issue_documents(
            [], "iss-1", "node-1", "t1", AsyncMock()
        )
        assert children == []

    @pytest.mark.asyncio
    async def test_skips_doc_without_id(self):
        connector = _make_connector()
        connector.indexing_filters = None
        doc_data = [{"title": "No ID"}]

        mock_tx_store = AsyncMock()

        children = await connector._process_issue_documents(
            doc_data, "iss-1", "node-1", "t1", mock_tx_store
        )
        assert children == []
