"""Deep-sync-loop tests for LinearConnector.

Covers: run_sync, _sync_issues_for_teams, _fetch_issues_for_team_batch,
_sync_attachments, _sync_documents, _sync_projects_for_teams,
_project_to_records, _sync_deleted_issues, _sync_deleted_projects,
_fetch_users, _fetch_teams, _apply_date_filters_to_linear_filter,
_process_issue_attachments, _process_issue_documents.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.connectors.sources.linear.api_gateway import (
    LinearAuthError,
    LinearTransientError,
)
from app.connectors.sources.linear.connector import (
    LINEAR_CONFIG_PATH,
    LINEAR_TRANSIENT_RETRIES,
    LinearConnector,
)
from app.connectors.sources.linear.team_access import WORKSPACE_MEMBERS_GROUP_ID
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
from app.sources.client.graphql.response import GraphQLResponse


# ===========================================================================
# Helpers
# ===========================================================================


def _memory_sync_point() -> MagicMock:
    """An in-memory stand-in for SyncPoint: what is written is what is read back."""
    store = {}

    async def read(key) -> dict:
        return dict(store.get(key) or {})

    async def update(key, data) -> None:
        store[key] = dict(data)

    sync_point = MagicMock()
    sync_point.store = store
    sync_point.read_sync_point = AsyncMock(side_effect=read)
    sync_point.update_sync_point = AsyncMock(side_effect=update)
    return sync_point


def _use_memory_state(connector) -> None:
    """Checkpoints live in memory, retries do not really sleep, and the processor
    calls made when records are removed or refreshed can be awaited."""
    for name in (
        "issues_sync_point", "attachments_sync_point", "documents_sync_point",
        "projects_sync_point", "deletion_sync_point", "reconcile_sync_point",
    ):
        setattr(connector, name, _memory_sync_point())
    connector._reconciler._sync_point = connector.reconcile_sync_point
    connector._sleep = AsyncMock()
    connector._api._sleep = connector._sleep
    # The cheap "did anything change" probe is tested on its own.
    connector._team_has_changes = AsyncMock(return_value=True)
    processor = connector.data_entities_processor
    for name, value in (
        ("get_records_by_parent", []),
        ("get_records_by_record_type", []),
        ("get_records_in_record_group", []),
        ("get_nodes_by_filters", []),
        ("on_records_deleted_cascade", None),
        ("on_records_detached_from_parent", None),
        ("on_record_content_update", None),
        ("on_record_group_deleted", None),
        ("on_user_group_deleted", None),
    ):
        if not isinstance(getattr(processor, name), AsyncMock):
            setattr(processor, name, AsyncMock(return_value=value))


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
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_record_by_weburl = AsyncMock(return_value=None)

    dsp = MagicMock()

    cs = AsyncMock()
    connector = LinearConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="linear-1",
        scope="team",
        created_by="test-user",
    )
    _use_memory_state(connector)
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


def _timed_out_page(query: str = "issues") -> GraphQLResponse:
    """What the data source returns for a read timeout: no status, no errors."""
    return GraphQLResponse(success=False, message=f"Failed to execute query {query}: ")


def _no_sleep() -> AbstractContextManager[AsyncMock]:
    return patch("app.connectors.sources.linear.connector.asyncio.sleep", new=AsyncMock())


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
    async def test_raises_if_no_datasource(self):
        connector = _make_connector()
        connector.data_source = None

        with pytest.raises(RuntimeError, match="not initialized"):
            await connector.run_sync()

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
            connector._sync_issues_for_teams = AsyncMock(return_value=(set(), []))
            connector._sync_attachments = AsyncMock()
            connector._sync_documents = AsyncMock()
            connector._sync_projects_for_teams = AsyncMock()
            connector._sync_deleted_issues = AsyncMock()
            connector._sync_deleted_projects = AsyncMock()
            connector._sweep_placeholder_records = AsyncMock(return_value=0)

            await connector.run_sync()

            connector._fetch_users.assert_awaited_once()
            connector._fetch_teams.assert_awaited_once()
            connector._sync_issues_for_teams.assert_awaited_once()
            connector._sync_attachments.assert_awaited_once()
            connector._sync_documents.assert_awaited_once()
            connector._sync_projects_for_teams.assert_awaited_once()
            connector._sync_deleted_issues.assert_awaited_once()
            connector._sync_deleted_projects.assert_awaited_once()
            connector._sweep_placeholder_records.assert_awaited_once()

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
    async def test_records_full_members_for_the_workspace_group(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._full_member_emails = {"stale@x.com"}

        ds = MagicMock()
        ds.users = AsyncMock(return_value=_mock_gql_response({
            "users": {
                "nodes": [
                    {"id": "u1", "email": "Member@X.com", "name": "M", "active": True, "guest": False},
                    {"id": "u2", "email": "guest@x.com", "name": "G", "active": True, "guest": True},
                    {"id": "u3", "email": "gone@x.com", "name": "S", "active": False, "guest": False},
                    {"id": "u4", "email": "plain@x.com", "name": "P"},
                ],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        users = await connector._fetch_users()

        # A guest is still a user (they can be added to teams) but not a full member.
        assert [u.email for u in users] == ["Member@X.com", "guest@x.com", "plain@x.com"]
        assert connector._full_member_emails == {"member@x.com", "plain@x.com"}
        # The workspace group holds exactly those members.
        user_map = {u.email.lower(): u for u in users}
        group, members = connector._workspace_members_group(user_map)
        assert group.source_user_group_id == "linear-workspace-members"
        assert [m.email for m in members] == ["Member@X.com", "plain@x.com"]

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
    async def test_public_team_is_granted_to_workspace_members_not_org(self) -> None:
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
        assert [(p.entity_type, p.external_id, p.type) for p in perms] == [
            (EntityType.GROUP, "t1", PermissionType.READ),
            (EntityType.GROUP, WORKSPACE_MEMBERS_GROUP_ID, PermissionType.READ),
        ]
        assert all(p.entity_type != EntityType.ORG for p in perms)

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
        assert [(p.entity_type, p.external_id, p.type) for p in perms] == [
            (EntityType.GROUP, "t-sec", PermissionType.READ),
        ]

    @pytest.mark.asyncio
    async def test_members_beyond_the_first_hundred_are_fetched(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.organization_url_key = "test-org"

        ds = MagicMock()
        ds.teams = AsyncMock(return_value=_mock_gql_response({
            "teams": {
                "nodes": [{
                    "id": "t-big", "name": "Everyone", "key": "ALL",
                    "description": "", "private": True, "parent": None,
                    "members": {
                        "nodes": [{"email": "a@x.com"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "m-100"},
                    },
                }],
                "pageInfo": {"hasNextPage": False},
            }
        }))

        async def execute_operation(operation, variables) -> GraphQLResponse:
            assert operation == "teamMembers"
            if variables["after"] == "m-100":
                nodes, info = [{"email": "b@x.com"}], {"hasNextPage": True, "endCursor": "m-200"}
            else:
                nodes, info = [{"email": "C@x.com"}], {"hasNextPage": False, "endCursor": None}
            return _mock_gql_response({"team": {"members": {"nodes": nodes, "pageInfo": info}}})

        ds.execute_operation = AsyncMock(side_effect=execute_operation)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        user_map = {e: _make_app_user(e) for e in ("a@x.com", "b@x.com", "c@x.com")}
        ugs, _ = await connector._fetch_teams(user_email_map=user_map)

        assert [c.args for c in ds.execute_operation.await_args_list] == [
            ("teamMembers", {"id": "t-big", "first": 100, "after": "m-100"}),
            ("teamMembers", {"id": "t-big", "first": 100, "after": "m-200"}),
        ]
        _, members = ugs[0]
        assert [m.email for m in members] == ["a@x.com", "b@x.com", "c@x.com"]

    @pytest.mark.asyncio
    async def test_unreadable_member_page_fails_the_team_listing(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()

        ds = MagicMock()
        ds.teams = AsyncMock(return_value=_mock_gql_response({
            "teams": {
                "nodes": [{
                    "id": "t-big", "name": "Everyone", "key": "ALL", "private": True,
                    "members": {
                        "nodes": [{"email": "a@x.com"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "m-100"},
                    },
                }],
                "pageInfo": {"hasNextPage": False},
            }
        }))
        ds.execute_operation = AsyncMock(return_value=_mock_gql_response(success=False, message="Error"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        # A team with part of its members would silently lock the others out.
        with pytest.raises(RuntimeError, match="Failed to fetch teams"):
            await connector._fetch_teams()

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
        assert [c.kwargs for c in ds.teams.await_args_list] == [
            {"first": 25, "after": None, "filter": None},
            {"first": 25, "after": "cur1", "filter": None},
        ]


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
        connector._now = MagicMock(return_value=1700000005000)
        rg = _team_rg()

        mock_ticket = MagicMock(spec=TicketRecord)
        mock_ticket.source_updated_at = 1700000001000

        async def gen(**kw):
            yield [(mock_ticket, [])]

        connector._fetch_issues_for_team_batch = gen

        await connector._sync_issues_for_teams([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        # Stored under the team's id (a key can be renamed), at the run's start time.
        assert connector.issues_sync_point.store == {"team_issues_t1": {
            "last_sync_time": 1700000005000, "resume_cursor": "", "resume_since": 0,
            "resume_started_at": 0, "held_runs": 0,
        }}

    @pytest.mark.asyncio
    async def test_continues_on_team_error(self):
        connector = _make_connector()
        rg1 = _team_rg("t1", "T1")
        rg2 = _team_rg("t2", "T2")

        call_count = 0

        async def gen(**kw):
            nonlocal call_count
            call_count += 1
            if kw.get("team_key") == "T1":
                raise RuntimeError("API error")
            yield []

        connector._fetch_issues_for_team_batch = gen

        full_sync_team_ids, failed = await connector._sync_issues_for_teams([(rg1, []), (rg2, [])])
        assert call_count == 2
        assert failed == ["Team T1"]
        assert full_sync_team_ids == {"t1", "t2"}
        assert set(connector.issues_sync_point.store) == {"team_issues_t2"}

    @staticmethod
    def _connector_with_issue_pages(*responses: GraphQLResponse) -> tuple[LinearConnector, MagicMock]:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        connector._get_team_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_team_sync_checkpoint = AsyncMock()
        ds = MagicMock()
        ds.issues = AsyncMock(side_effect=list(responses))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_issue_to_ticket_record = MagicMock(return_value=MagicMock(
            spec=TicketRecord, id="rec-1", weburl="url", indexing_status=None,
            source_updated_at=1700000000000,
        ))
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))
        return connector, ds

    @pytest.mark.asyncio
    async def test_a_team_whose_page_still_fails_after_the_retries_is_reported_failed(self) -> None:
        """#3991 retried the page; a page that never came back still left the team counted as synced."""
        connector, ds = self._connector_with_issue_pages(*[_timed_out_page()] * (LINEAR_TRANSIENT_RETRIES + 1))

        with _no_sleep():
            _, failed = await connector._sync_issues_for_teams([(_team_rg("t1", "FRO"), [])])

        assert ds.issues.await_count == LINEAR_TRANSIENT_RETRIES + 1
        assert failed == ["Team FRO"]
        connector.data_entities_processor.on_new_records.assert_not_awaited()

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

    @staticmethod
    def _connector_with_issue_pages(*responses: GraphQLResponse) -> tuple[LinearConnector, MagicMock]:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        ds = MagicMock()
        ds.issues = AsyncMock(side_effect=list(responses))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_issue_to_ticket_record = MagicMock(return_value=MagicMock(
            spec=TicketRecord, id="rec-1", weburl="url", indexing_status=None,
            source_updated_at=1700000000000,
        ))
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))
        return connector, ds

    @staticmethod
    def _issues_page() -> GraphQLResponse:
        return GraphQLResponse(success=True, data={
            "issues": {"nodes": [_make_issue_data()], "pageInfo": {"hasNextPage": False}},
        }, status_code=200)

    async def _batches(self, connector: LinearConnector) -> list:
        with patch("app.connectors.sources.linear.connector.asyncio.sleep", new=AsyncMock()):
            return [b async for b in connector._fetch_issues_for_team_batch(team_id="t1", team_key="FRO")]

    @pytest.mark.asyncio
    async def test_a_page_that_timed_out_is_fetched_again(self) -> None:
        """The 10/07 nightly lost team FRO's issues to one 30s read timeout."""
        timed_out = GraphQLResponse(success=False, message="Failed to execute query issues: ")
        connector, ds = self._connector_with_issue_pages(timed_out, self._issues_page())

        batches = await self._batches(connector)

        assert ds.issues.await_count == 2
        assert len(batches) == 1

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

        batches = [batch async for batch in connector._fetch_issues_for_team_batch(team_id="t1", team_key="ENG")]

        assert len(batches) == 1
        assert len(batches[0]) >= 1
        # Fifty issues a page, this team only, in Linear's default order: the list
        # is not sorted by update time, so pages do not shift while it is read.
        ds.issues.assert_awaited_once_with(
            first=50, after=None, filter={"team": {"id": {"eq": "t1"}}})
        assert "t1" not in connector.issues_sync_point.store
        assert "iss-1" in connector._refreshed_issue_ids

    @pytest.mark.asyncio
    async def test_incremental_filters_by_update_time(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None

        ds = MagicMock()
        ds.issues = AsyncMock(return_value=_mock_gql_response({
            "issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        batches = [b async for b in connector._fetch_issues_for_team_batch(
            team_id="t1", team_key="ENG", last_sync_time=1700000000000)]

        assert batches == []
        assert ds.issues.await_args.kwargs["filter"] == {
            "team": {"id": {"eq": "t1"}},
            "updatedAt": {"gt": connector._linear_datetime_from_timestamp(1700000000000)},
        }

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

        batches = [batch async for batch in connector._fetch_issues_for_team_batch(team_id="t1", team_key="ENG")]

        assert len(batches) == 2

    @pytest.mark.asyncio
    async def test_api_failure_raises(self):
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None

        ds = MagicMock()
        ds.issues = AsyncMock(return_value=_mock_gql_response(success=False, message="Error"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        batches = []
        with pytest.raises(LinearTransientError):
            async for batch in connector._fetch_issues_for_team_batch(
                team_id="t1", team_key="ENG"
            ):
                batches.append(batch)

        assert len(batches) == 0
        assert ds.issues.await_count == 4
        assert [c.args[0] for c in connector._sleep.await_args_list] == [1, 3, 9]

    @pytest.mark.asyncio
    async def test_failed_second_page_raises_after_first_page_was_yielded(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        connector._now = MagicMock(return_value=1700000005000)
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))

        first = _mock_gql_response({
            "issues": {
                "nodes": [_make_issue_data("i1", "ENG-1")],
                "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
            }
        })
        failure = _mock_gql_response(success=False, message="Error")
        ds = MagicMock()
        ds.issues = AsyncMock(side_effect=[first, failure, failure, failure, failure])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        checkpoint = connector._team_issue_checkpoint("t1")
        run = await checkpoint.begin()
        batches = []
        with pytest.raises(LinearTransientError):
            async for batch in connector._fetch_issues_for_team_batch(
                team_id="t1", team_key="ENG", run=run, checkpoint=checkpoint
            ):
                batches.append(batch)

        # The first page was handed over and its cursor saved; the listing did not
        # end quietly, so the caller cannot mistake it for complete.
        assert [[r.external_record_id for r, _ in b] for b in batches] == [["i1"]]
        saved = connector.issues_sync_point.store["team_issues_t1"]
        assert saved["resume_cursor"] == "cur1"
        assert saved["resume_started_at"] == 1700000005000
        assert saved["last_sync_time"] == 0

    @pytest.mark.asyncio
    async def test_resumes_after_the_saved_cursor(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        connector.issues_sync_point.store["team_issues_t1"] = {
            "last_sync_time": 0, "resume_cursor": "cur1", "resume_since": 0,
            "resume_started_at": 1700000005000,
        }
        ds = MagicMock()
        ds.issues = AsyncMock(return_value=_mock_gql_response({
            "issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        checkpoint = connector._team_issue_checkpoint("t1")
        run = await checkpoint.begin()
        batches = [b async for b in connector._fetch_issues_for_team_batch(
            team_id="t1", team_key="ENG", last_sync_time=run.since, run=run, checkpoint=checkpoint)]

        assert batches == []
        ds.issues.assert_awaited_once_with(
            first=50, after="cur1", filter={"team": {"id": {"eq": "t1"}}})

    @pytest.mark.asyncio
    async def test_rejected_saved_cursor_restarts_the_listing(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))
        connector.issues_sync_point.store["team_issues_t1"] = {
            "last_sync_time": 0, "resume_cursor": "stale", "resume_since": 0,
            "resume_started_at": 1700000005000,
        }

        async def issues(**kw) -> GraphQLResponse:
            if kw["after"] == "stale":
                return GraphQLResponse(success=False, message="Invalid cursor", status_code=400)
            return _mock_gql_response({
                "issues": {
                    "nodes": [_make_issue_data("i1", "ENG-1")],
                    "pageInfo": {"hasNextPage": False, "endCursor": "end"},
                }
            })

        ds = MagicMock()
        ds.issues = AsyncMock(side_effect=issues)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        checkpoint = connector._team_issue_checkpoint("t1")
        run = await checkpoint.begin()
        assert run.resumed is True
        batches = [b async for b in connector._fetch_issues_for_team_batch(
            team_id="t1", team_key="ENG", run=run, checkpoint=checkpoint)]

        assert [c.kwargs["after"] for c in ds.issues.await_args_list] == ["stale", None]
        assert len(batches) == 1
        assert run.resumed is False

    @pytest.mark.asyncio
    async def test_auth_error_on_resume_is_not_mistaken_for_a_bad_cursor(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        connector.issues_sync_point.store["team_issues_t1"] = {
            "last_sync_time": 0, "resume_cursor": "cur1", "resume_since": 0,
            "resume_started_at": 1700000005000,
        }
        ds = MagicMock()
        ds.issues = AsyncMock(
            return_value=GraphQLResponse(success=False, message="Unauthorized", status_code=401))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        checkpoint = connector._team_issue_checkpoint("t1")
        run = await checkpoint.begin()
        with pytest.raises(LinearAuthError):
            async for _ in connector._fetch_issues_for_team_batch(
                team_id="t1", team_key="ENG", run=run, checkpoint=checkpoint
            ):
                pass

        ds.issues.assert_awaited_once()
        assert connector.issues_sync_point.store["team_issues_t1"]["resume_cursor"] == "cur1"

    @pytest.mark.asyncio
    async def test_issue_that_cannot_be_converted_is_reported_to_the_caller(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None
        connector._extract_files_from_markdown = AsyncMock(return_value=([], []))

        ds = MagicMock()
        ds.issues = AsyncMock(return_value=_mock_gql_response({
            "issues": {
                "nodes": [_make_issue_data("i1", "ENG-1"), _make_issue_data("i2", "ENG-2")],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        good = MagicMock(spec=TicketRecord, id="rec-1", weburl="url", indexing_status=None)
        connector._transform_issue_to_ticket_record = MagicMock(
            side_effect=[ValueError("bad issue"), good])

        failures = []
        batches = [b async for b in connector._fetch_issues_for_team_batch(
            team_id="t1", team_key="ENG", failures=failures)]

        assert failures == ["ENG-1"]
        assert [[r for r, _ in b] for b in batches] == [[good]]

    @pytest.mark.asyncio
    async def test_comments_beyond_the_first_page_are_read_for_file_links(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.indexing_filters = None

        issue = _make_issue_data("i1", "ENG-1")
        issue["description"] = ""
        issue["comments"] = {
            "nodes": [{"id": "c1", "body": "nothing here"}],
            "pageInfo": {"hasNextPage": True, "endCursor": "c-20"},
        }
        upload = "https://uploads.linear.app/org/late.pdf"
        ds = MagicMock()
        ds.issues = AsyncMock(return_value=_mock_gql_response({
            "issues": {"nodes": [issue], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        }))
        ds.execute_operation = AsyncMock(return_value=_mock_gql_response({"issue": {"comments": {
            "nodes": [{"id": "c21", "body": f"see [late.pdf]({upload})"}],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }}}))
        ds.get_file_size = AsyncMock(return_value=12)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        batches = [b async for b in connector._fetch_issues_for_team_batch(team_id="t1", team_key="ENG")]

        ds.execute_operation.assert_awaited_once_with(
            "issueComments", {"id": "i1", "first": 100, "after": "c-20"})
        records = [r for r, _ in batches[0]]
        assert [type(r).__name__ for r in records] == ["TicketRecord", "FileRecord"]
        assert records[1].external_record_id == f"{upload}#parent=i1"
        assert records[1].parent_external_record_id == "i1"


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

        mock_parent = MagicMock()
        mock_parent.id = "parent-rec-id"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: mock_parent if external_record_id == "iss-1" else None
        )
        connector.data_entities_processor.get_record_by_weburl = AsyncMock(return_value=None)
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

        await connector._sync_attachments([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_that_still_fails_after_the_retries_raises_the_attachments_warning(self) -> None:
        connector = _make_connector()
        connector._get_attachments_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_attachments_sync_checkpoint = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None
        connector.notify = AsyncMock()
        ds = MagicMock()
        ds.attachments = AsyncMock(return_value=_timed_out_page("attachments"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        with _no_sleep():
            await connector._sync_attachments([(_team_rg(), [])])

        assert ds.attachments.await_count == LINEAR_TRANSIENT_RETRIES + 1
        connector.notify.assert_awaited_once()
        assert "couldn't sync attachments" in connector.notify.await_args.kwargs["title"]
        connector._update_attachments_sync_checkpoint.assert_not_awaited()

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
        mock_parent.external_record_group_id = "t1"
        mock_parent.is_placeholder = False
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda connector_id, external_record_id: mock_parent if external_record_id == "iss-1" else None
        )
        webpage = MagicMock(
            spec=WebpageRecord, source_updated_at=1700000001000, indexing_status=None,
        )
        connector._transform_document_to_webpage_record = MagicMock(return_value=webpage)

        await connector._sync_documents([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_awaited_once_with([(webpage, [])])
        connector._transform_document_to_webpage_record.assert_called_once_with(
            doc_data, "iss-1", "parent-rec-id", "t1", None, parent_record_type=RecordType.TICKET)

    @pytest.mark.asyncio
    async def test_skips_standalone_documents(self):
        connector = _make_connector()
        rg = _team_rg()
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

        await connector._sync_documents([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_page_that_still_fails_after_the_retries_raises_the_documents_warning(self) -> None:
        connector = _make_connector()
        connector._get_documents_sync_checkpoint = AsyncMock(return_value=None)
        connector._update_documents_sync_checkpoint = AsyncMock()
        connector.sync_filters = None
        connector.indexing_filters = None
        connector.notify = AsyncMock()
        ds = MagicMock()
        ds.documents = AsyncMock(return_value=_timed_out_page("documents"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        with _no_sleep():
            await connector._sync_documents([(_team_rg(), [])])

        assert ds.documents.await_count == LINEAR_TRANSIENT_RETRIES + 1
        connector.notify.assert_awaited_once()
        assert "couldn't sync documents" in connector.notify.await_args.kwargs["title"]
        connector._update_documents_sync_checkpoint.assert_not_awaited()

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
        connector.sync_filters = None
        connector._now = MagicMock(return_value=1700000005000)
        rg = _team_rg()

        mock_proj = MagicMock(spec=ProjectRecord)
        mock_proj.source_updated_at = 1700000001000

        ds = MagicMock()
        ds.projects = AsyncMock(return_value=_mock_gql_response({
            "projects": {"nodes": [{"id": "p1"}], "pageInfo": {"hasNextPage": False}}
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._project_to_records = AsyncMock(return_value=[(mock_proj, [])])

        await connector._sync_projects_for_teams([(rg, [])])
        connector.data_entities_processor.on_new_records.assert_awaited_once_with([(mock_proj, [])])
        connector._project_to_records.assert_awaited_once_with("p1", {"t1"})
        assert connector.projects_sync_point.store["projects"]["last_sync_time"] == 1700000005000

    @pytest.mark.asyncio
    async def test_project_shared_by_two_teams_is_read_once(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        rg1 = _team_rg("t1", "T1")
        rg2 = _team_rg("t2", "T2")

        ds = MagicMock()
        ds.projects = AsyncMock(return_value=_mock_gql_response({
            "projects": {"nodes": [{"id": "p-shared"}], "pageInfo": {"hasNextPage": False}}
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._project_to_records = AsyncMock(return_value=[])

        await connector._sync_projects_for_teams([(rg2, []), (rg1, [])])

        # One listing for all teams, not one per team.
        ds.projects.assert_awaited_once_with(
            first=10, after=None,
            filter={"accessibleTeams": {"some": {"id": {"in": ["t1", "t2"]}}}})
        connector._project_to_records.assert_awaited_once_with("p-shared", {"t1", "t2"})

    @pytest.mark.asyncio
    async def test_unreadable_project_keeps_the_checkpoint_and_the_rest_is_synced(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.notify = AsyncMock()
        connector.projects_sync_point.store["projects"] = {"last_sync_time": 1700000000000}
        rg = _team_rg()

        ds = MagicMock()
        ds.projects = AsyncMock(return_value=_mock_gql_response({
            "projects": {"nodes": [{"id": "p1"}, {"id": "p2"}], "pageInfo": {"hasNextPage": False}}
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        good = [(MagicMock(), [])]
        connector._project_to_records = AsyncMock(
            side_effect=[LinearTransientError("project could not be read", 502), good])

        await connector._sync_projects_for_teams([(rg, [])])

        # The second project is still stored, somebody is told, and the checkpoint
        # stays where it was so the first one is read again.
        assert connector._project_to_records.await_count == 2
        connector.data_entities_processor.on_new_records.assert_awaited_once_with(good)
        connector.notify.assert_awaited_once()
        stored = connector.projects_sync_point.store["projects"]
        assert stored["last_sync_time"] == 1700000000000
        assert stored["held_runs"] == 1

    @pytest.mark.asyncio
    async def test_a_project_that_never_converts_stops_holding_the_checkpoint(self) -> None:
        connector = _make_connector()
        connector.sync_filters = None
        connector.notify = AsyncMock()
        connector.projects_sync_point.store["projects"] = {"last_sync_time": 1700000000000, "held_runs": 3}
        rg = _team_rg()

        ds = MagicMock()
        ds.projects = AsyncMock(return_value=_mock_gql_response({
            "projects": {"nodes": [{"id": "p1"}], "pageInfo": {"hasNextPage": False}}
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._project_to_records = AsyncMock(side_effect=ValueError("bad project"))

        await connector._sync_projects_for_teams([(rg, [])])

        stored = connector.projects_sync_point.store["projects"]
        assert stored["last_sync_time"] > 1700000000000
        assert stored["held_runs"] == 0

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
        connector._now = MagicMock(return_value=1700000005000)
        rg = _team_rg()

        ds = MagicMock()
        ds.trashed_issues = AsyncMock(return_value=_mock_gql_response({
            "issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_deleted_issues([(rg, [])])

        ds.trashed_issues.assert_awaited_once_with(
            first=100, after=None,
            filter={"team": {"id": {"in": ["t1"]}}, "archivedAt": {"null": False}})
        assert connector.deletion_sync_point.store["trashed_issues"]["last_sync_time"] == 1700000005000

    @pytest.mark.asyncio
    async def test_detects_trashed_issues(self):
        connector = _make_connector()
        rg = _team_rg()
        connector.deletion_sync_point.store["trashed_issues"] = {"last_sync_time": 1700000000000}
        connector._remover.remove = AsyncMock(return_value=True)

        ds = MagicMock()
        ds.trashed_issues = AsyncMock(return_value=_mock_gql_response({
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

        # Only trashed issue should be removed
        connector._remover.remove.assert_awaited_once_with("i1")

    @pytest.mark.asyncio
    async def test_pagination_continues(self):
        connector = _make_connector()
        rg = _team_rg()
        connector.deletion_sync_point.store["trashed_issues"] = {"last_sync_time": 1700000000000}
        connector._remover.remove = AsyncMock(return_value=True)

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
        ds.trashed_issues = AsyncMock(side_effect=mock_issues)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_deleted_issues([(rg, [])])

        assert [c.args[0] for c in connector._remover.remove.await_args_list] == ["i1", "i2"]
        assert [c.kwargs["after"] for c in ds.trashed_issues.await_args_list] == [None, "cur1"]

# ===========================================================================
# _sync_deleted_projects
# ===========================================================================


class TestSyncDeletedProjects:

    @pytest.mark.asyncio
    async def test_empty_teams_noop(self):
        connector = _make_connector()
        await connector._sync_deleted_projects([])

    @pytest.mark.asyncio
    async def test_runs_on_the_first_sync_too(self) -> None:
        connector = _make_connector()
        rg = _team_rg()
        connector._reconciler.run_projects = AsyncMock(return_value=0)

        await connector._sync_deleted_projects([(rg, [])])

        # No "since" for projects: stored projects are compared on every sync.
        connector._reconciler.run_projects.assert_awaited_once_with({"t1"})
        assert connector.deletion_sync_point.store == {}


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

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector._transform_attachment_to_link_record = MagicMock(return_value=MagicMock(
            id="link-rec-1", record_name="Spec Doc", indexing_status=None
        ))

        children = await connector._process_issue_attachments(
            attachment_data, "iss-1", "node-1", "t1"
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

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        children = await connector._process_issue_attachments(
            attachment_data, "iss-1", "node-1", "t1"
        )
        assert len(children) == 1
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_attachments(self):
        connector = _make_connector()
        children = await connector._process_issue_attachments(
            [], "iss-1", "node-1", "t1"
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

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector._transform_document_to_webpage_record = MagicMock(return_value=MagicMock(
            id="web-rec-1", record_name="Design", indexing_status=None
        ))

        children = await connector._process_issue_documents(
            doc_data, "iss-1", "node-1", "t1"
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

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        children = await connector._process_issue_documents(
            doc_data, "iss-1", "node-1", "t1"
        )
        assert len(children) == 1
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_documents(self):
        connector = _make_connector()
        children = await connector._process_issue_documents(
            [], "iss-1", "node-1", "t1"
        )
        assert children == []

    @pytest.mark.asyncio
    async def test_skips_doc_without_id(self):
        connector = _make_connector()
        connector.indexing_filters = None
        doc_data = [{"title": "No ID"}]

        children = await connector._process_issue_documents(
            doc_data, "iss-1", "node-1", "t1"
        )
        assert children == []
