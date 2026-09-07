"""Deep-sync-loop tests for JiraConnector.

Covers: run_sync, _sync_all_project_issues, _sync_project_issues,
_fetch_issues_batched, _build_issue_records, _fetch_issue_attachments,
_handle_issue_deletions, _detect_and_handle_deletions,
_fetch_deleted_issues_from_audit, _handle_deleted_issue,
_sync_user_groups, _fetch_groups, _fetch_group_members,
_sync_project_roles, _fetch_projects, _process_new_records,
_extract_issue_data, _parse_issue_links.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.jira_cloud.connector import (
    AUDIT_PAGE_SIZE,
    BATCH_PROCESSING_SIZE,
    DEFAULT_MAX_RESULTS,
    JiraConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    ItemType,
    MimeTypes,
    OriginTypes,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.jira.deep")
    dep = MagicMock()
    dep.org_id = "org-jira-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="user@example.com"),
    ])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_record_by_issue_key = AsyncMock(return_value=None)
    dep.get_records_by_parent = AsyncMock(return_value=[])
    dep.on_records_deleted_cascade = AsyncMock(return_value={
        "success": True, "successfully_deleted": 0,
    })
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.get_placeholder_records = AsyncMock(return_value=[])
    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return JiraConnector(logger, dep, dsp, cs, "conn-jira-1", "team", "test-user-id")


def _resp(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    return resp


def _app_user(email="u@x.com", account_id="acc-1"):
    return AppUser(
        app_name=Connectors.JIRA,
        connector_id="conn-jira-1",
        source_user_id=account_id,
        org_id="org-jira-1",
        email=email,
        full_name="User",
        is_active=True,
    )


def _project_rg(key="PROJ", pid="p-1"):
    return RecordGroup(
        id=str(uuid4()),
        org_id="org-jira-1",
        external_group_id=pid,
        connector_id="conn-jira-1",
        connector_name=Connectors.JIRA,
        name=f"Project {key}",
        short_name=key,
        group_type=RecordGroupType.PROJECT,
    )


def _issue_dict(issue_id="1001", key="PROJ-1", updated="2024-06-15T10:00:00.000+0000"):
    return {
        "id": issue_id,
        "key": key,
        "fields": {
            "summary": f"Issue {key}",
            "description": None,
            "status": {"name": "Open"},
            "priority": {"name": "High"},
            "creator": {"accountId": "acc-1", "displayName": "Creator"},
            "reporter": {"accountId": "acc-1", "displayName": "Reporter"},
            "assignee": {"accountId": "acc-2", "displayName": "Assignee"},
            "created": "2024-01-01T00:00:00.000+0000",
            "updated": updated,
            "issuetype": {"name": "Task", "hierarchyLevel": 0},
            "project": {"id": "p-1", "key": "PROJ"},
            "parent": None,
            "attachment": [],
            "security": None,
            "issuelinks": [],
        },
    }


# ===========================================================================
# run_sync orchestration
# ===========================================================================


class TestRunSync:

    @pytest.mark.asyncio
    async def test_run_sync_raises_when_not_initialized(self):
        connector = _make_connector()
        connector.data_source = None
        connector.init = AsyncMock(return_value=True)
        connector.notify = AsyncMock()

        with pytest.raises(RuntimeError, match="not initialized"):
            await connector.run_sync()
        connector.init.assert_not_awaited()
        connector.notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_sync_no_active_users_returns_early(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())

            await connector.run_sync()
            # Should return early without fetching users
            connector.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_run_sync_full_flow(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        user = _app_user()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[user])

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            connector._fetch_users = AsyncMock(return_value=[user])
            connector._sync_user_groups = AsyncMock(return_value={"g1": [user]})
            rg = _project_rg()
            connector._fetch_projects = AsyncMock(return_value=(
                [(rg, [])],
                [{"key": "PROJ", "lead": None}],
            ))
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={
                "total_synced": 5, "new_count": 3, "updated_count": 2
            })
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()

            await connector.run_sync()

            connector._fetch_users.assert_awaited_once()
            connector.data_entities_processor.on_new_app_users.assert_awaited_once_with([user])
            connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
            connector._sync_all_project_issues.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_propagates_exception(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            side_effect=RuntimeError("DB crash")
        )
        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            with pytest.raises(RuntimeError, match="DB crash"):
                await connector.run_sync()


# ===========================================================================
# _sync_all_project_issues
# ===========================================================================


class TestSyncAllProjectIssues:

    @pytest.mark.asyncio
    async def test_aggregates_stats_across_projects(self):
        connector = _make_connector()
        rg1 = _project_rg("P1", "p1")
        rg2 = _project_rg("P2", "p2")

        async def mock_sync(proj, users, last_sync):
            if proj.short_name == "P1":
                return {"total_synced": 3, "new_count": 2, "updated_count": 1}
            return {"total_synced": 2, "new_count": 1, "updated_count": 1}

        connector._sync_project_issues = AsyncMock(side_effect=mock_sync)

        result = await connector._sync_all_project_issues(
            [(rg1, []), (rg2, [])], [], None
        )

        assert result["total_synced"] == 5
        assert result["new_count"] == 3
        assert result["updated_count"] == 2

    @pytest.mark.asyncio
    async def test_continues_on_project_error(self):
        connector = _make_connector()
        rg1 = _project_rg("P1", "p1")
        rg2 = _project_rg("P2", "p2")

        call_count = 0

        async def mock_sync(proj, users, last_sync):
            nonlocal call_count
            call_count += 1
            if proj.short_name == "P1":
                raise RuntimeError("API error")
            return {"total_synced": 1, "new_count": 1, "updated_count": 0}

        connector._sync_project_issues = AsyncMock(side_effect=mock_sync)

        result = await connector._sync_all_project_issues(
            [(rg1, []), (rg2, [])], [], None
        )

        assert call_count == 2
        assert result["total_synced"] == 1
        assert result["failed_count"] == 1
        assert result["failed_project_keys"] == ["P1"]

    @pytest.mark.asyncio
    async def test_empty_projects_returns_zeros(self):
        connector = _make_connector()
        result = await connector._sync_all_project_issues([], [], None)
        assert result == {
            "total_synced": 0,
            "new_count": 0,
            "updated_count": 0,
            "failed_count": 0,
            "failed_project_keys": [],
            "full_sync_project_ids": set(),
        }


# ===========================================================================
# _sync_project_issues
# ===========================================================================


class TestSyncProjectIssues:

    @pytest.mark.asyncio
    async def test_new_project_no_timestamp_filter(self):
        connector = _make_connector()
        rg = _project_rg()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.issues_sync_point.update_sync_point = AsyncMock()

        # Empty generator
        async def empty_gen(*a, **kw):
            return
            yield  # noqa: unreachable

        connector._fetch_issues_batched = empty_gen
        connector._process_new_records = AsyncMock()

        result = await connector._sync_project_issues(rg, [], None)
        assert result["total_synced"] == 0

    @pytest.mark.asyncio
    async def test_processes_batches_and_updates_checkpoint(self):
        connector = _make_connector()
        rg = _project_rg()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value={
            "last_sync_time": 1700000000000,
            "last_issue_updated": 1700000000000,
        })
        connector.issues_sync_point.update_sync_point = AsyncMock()

        mock_record = MagicMock()
        mock_record.version = 0
        batch = [(mock_record, [])]

        async def gen(*a, **kw):
            yield batch, True, 1700000001000
            yield batch, False, 1700000002000

        connector._fetch_issues_batched = gen
        connector._process_new_records = AsyncMock()

        result = await connector._sync_project_issues(rg, [], 1700000000000)
        assert result["total_synced"] == 2


# ===========================================================================
# _fetch_issues_batched (pagination loop)
# ===========================================================================


class TestFetchIssuesBatched:

    @pytest.mark.asyncio
    async def test_single_page_no_more(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        issue = _issue_dict()
        ds = MagicMock()
        ds.search_and_reconsile_issues_using_jql_post = AsyncMock(return_value=_resp(200, {
            "issues": [issue],
            "nextPageToken": None,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._build_issue_records = AsyncMock(return_value=([], []))
        connector._safe_json_parse = MagicMock(return_value={
            "issues": [issue],
            "nextPageToken": None,
        })

        batches = []
        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p-1", [], None, None):
            batches.append((batch, has_more, ts))

        assert len(batches) >= 1

    @pytest.mark.asyncio
    async def test_multi_page_pagination(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        issue1 = _issue_dict("1001", "PROJ-1", "2024-06-01T00:00:00.000+0000")
        issue2 = _issue_dict("1002", "PROJ-2", "2024-06-02T00:00:00.000+0000")

        call_count = 0

        async def mock_search(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "issues": [issue1],
                    "nextPageToken": "token-page2",
                })
            return _resp(200, {
                "issues": [issue2],
                "nextPageToken": None,
            })

        ds = MagicMock()
        ds.search_and_reconsile_issues_using_jql_post = AsyncMock(side_effect=mock_search)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        mock_tx_store = AsyncMock()
        mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx = AsyncMock()
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        connector.data_store_provider.transaction = MagicMock(return_value=mock_tx)
        connector._build_issue_records = AsyncMock(return_value=([], []))
        connector._safe_json_parse = MagicMock(side_effect=[
            {"issues": [issue1], "nextPageToken": "token-page2"},
            {"issues": [issue2], "nextPageToken": None},
        ])

        batches = []
        async for batch, has_more, ts in connector._fetch_issues_batched("PROJ", "p-1", [], None, None):
            batches.append(batch)

        assert len(batches) == 2

    @pytest.mark.asyncio
    async def test_api_error_raises(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.sync_filters = None
        connector.indexing_filters = None

        ds = MagicMock()
        ds.search_and_reconsile_issues_using_jql_post = AsyncMock(
            return_value=_resp(500, {})
        )
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._safe_json_parse = MagicMock(return_value=None)

        with pytest.raises(Exception):
            async for _ in connector._fetch_issues_batched("PROJ", "p-1", [], None, None):
                pass


# ===========================================================================
# _build_issue_records
# ===========================================================================


class TestBuildIssueRecords:

    @pytest.mark.asyncio
    async def test_new_issue_creates_ticket_record(self):
        connector = _make_connector()
        connector.site_url = "https://company.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock(return_value=[])
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records, _ = await connector._build_issue_records(
            [issue], "p-1", [_app_user()]
        )

        assert len(records) == 1
        rec, _perms, content_changed = records[0]
        assert isinstance(rec, TicketRecord)
        assert content_changed is True
        assert rec.version == 0
        assert rec.record_name == "[PROJ-1] Issue PROJ-1"
        assert "company.atlassian.net/browse/PROJ-1" in rec.weburl

    @pytest.mark.asyncio
    async def test_existing_unchanged_issue_skipped(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 1
        existing.is_placeholder = False
        existing.source_updated_at = connector._parse_jira_timestamp("2024-06-15T10:00:00.000+0000")

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing
        )
        connector._handle_attachment_deletions_from_changelog = AsyncMock(return_value=[])

        records, _ = await connector._build_issue_records(
            [issue], "p-1", []
        )

        assert len(records) == 0

    @pytest.mark.asyncio
    async def test_updated_issue_increments_version(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict(updated="2024-06-16T10:00:00.000+0000")
        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.is_placeholder = False
        existing.source_updated_at = connector._parse_jira_timestamp("2024-06-15T10:00:00.000+0000")

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=existing
        )
        connector._handle_attachment_deletions_from_changelog = AsyncMock(return_value=[])
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records, _ = await connector._build_issue_records(
            [issue], "p-1", []
        )

        assert len(records) == 1
        assert records[0][0].version == 3

    @pytest.mark.asyncio
    async def test_epic_issue_hierarchy(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        issue["fields"]["issuetype"] = {"name": "Epic", "hierarchyLevel": 1}
        issue["fields"]["parent"] = None

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock(return_value=[])
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records, _ = await connector._build_issue_records(
            [issue], "p-1", []
        )

        assert len(records) == 1
        rec = records[0][0]
        assert rec.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_subtask_has_parent(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        issue["fields"]["issuetype"] = {"name": "Sub-task", "hierarchyLevel": -1}
        issue["fields"]["parent"] = {"id": "parent-1001", "key": "PROJ-1"}

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock(return_value=[])
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records, _ = await connector._build_issue_records(
            [issue], "p-1", []
        )

        assert len(records) == 1
        rec = records[0][0]
        assert rec.parent_external_record_id == "parent-1001"

    @pytest.mark.asyncio
    async def test_attachments_appended_to_records(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        issue = _issue_dict()
        issue["fields"]["attachment"] = [{"id": "att-1", "filename": "f.pdf", "size": 100, "mimeType": "application/pdf"}]

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector._handle_attachment_deletions_from_changelog = AsyncMock(return_value=[])

        mock_file = MagicMock(spec=FileRecord)
        mock_file.version = 0
        connector._fetch_issue_attachments = AsyncMock(return_value=[(mock_file, [], True)])

        records, _ = await connector._build_issue_records(
            [issue], "p-1", []
        )

        # ticket + attachment
        assert len(records) == 2


# ===========================================================================
# _fetch_issue_attachments
# ===========================================================================


class TestFetchIssueAttachments:

    @pytest.mark.asyncio
    async def test_no_attachments_returns_empty(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", {}, [], "p-1", RecordGroupType.PROJECT
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_new_attachment_creates_file_record(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        fields = {
            "attachment": [
                {"id": "a1", "filename": "doc.pdf", "size": 2048, "mimeType": "application/pdf", "created": "2024-01-01T00:00:00.000+0000"}
            ]
        }
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        connector._create_attachment_file_record = MagicMock(return_value=MagicMock(spec=FileRecord))

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "p-1", RecordGroupType.PROJECT
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_attachment_without_id_skipped(self):
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None

        fields = {"attachment": [{"filename": "noid.txt"}]}
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        result = await connector._fetch_issue_attachments(
            "issue-1", "PROJ-1", fields, [], "p-1", RecordGroupType.PROJECT
        )
        assert result == []


# ===========================================================================
# _handle_issue_deletions / _detect_and_handle_deletions
# ===========================================================================


class TestHandleIssueDeletions:

    @pytest.mark.asyncio
    async def test_no_deletion_check_if_no_sync_time(self):
        connector = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector._detect_and_handle_deletions = AsyncMock()

        await connector._handle_issue_deletions(None)
        connector._detect_and_handle_deletions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_deletions_checked_when_sync_time_present(self):
        connector = _make_connector()
        connector.issues_sync_point = MagicMock()
        connector.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.issues_sync_point.update_sync_point = AsyncMock()
        connector._detect_and_handle_deletions = AsyncMock(return_value=(1700000000000, True))

        await connector._handle_issue_deletions(1700000000000)
        connector._detect_and_handle_deletions.assert_awaited_once()
        # success=True → checkpoint is advanced
        connector.issues_sync_point.update_sync_point.assert_awaited_once()


class TestDetectAndHandleDeletions:

    @pytest.mark.asyncio
    async def test_no_deleted_issues_succeeds(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=([], True))
        connector._handle_deleted_issue = AsyncMock()

        checkpoint_ms, success = await connector._detect_and_handle_deletions(1700000000000)
        assert success is True
        assert isinstance(checkpoint_ms, int)
        connector._handle_deleted_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_each_deleted_issue(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=(["PROJ-1", "PROJ-2"], True))
        connector._handle_deleted_issue = AsyncMock(return_value=(1, 0))

        _checkpoint_ms, success = await connector._detect_and_handle_deletions(1700000000000)
        assert success is True
        assert connector._handle_deleted_issue.await_count == 2

    @pytest.mark.asyncio
    async def test_partial_failure_reports_unsuccessful(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=(["PROJ-1", "PROJ-2"], True))

        call_count = 0

        async def mock_delete(key):
            nonlocal call_count
            call_count += 1
            if key == "PROJ-1":
                raise RuntimeError("delete error")
            return 1, 0

        connector._handle_deleted_issue = AsyncMock(side_effect=mock_delete)

        _checkpoint_ms, success = await connector._detect_and_handle_deletions(1700000000000)
        assert call_count == 2  # both attempted despite PROJ-1 failing
        assert success is False  # a per-issue failure holds the checkpoint for retry

    @pytest.mark.asyncio
    async def test_fetch_incomplete_reports_unsuccessful(self):
        connector = _make_connector()
        # audit fetch reported not-ok (a page failed) -> don't advance past unread deletions
        connector._fetch_deleted_issues_from_audit = AsyncMock(return_value=([], False))
        connector._handle_deleted_issue = AsyncMock()

        _checkpoint_ms, success = await connector._detect_and_handle_deletions(1700000000000)
        assert success is False
        connector._handle_deleted_issue.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_unsuccessful_on_exception(self):
        connector = _make_connector()
        connector._fetch_deleted_issues_from_audit = AsyncMock(side_effect=RuntimeError("API fail"))

        _checkpoint_ms, success = await connector._detect_and_handle_deletions(1700000000000)
        assert success is False


# ===========================================================================
# _fetch_deleted_issues_from_audit
# ===========================================================================


class TestFetchDeletedIssuesFromAudit:

    @pytest.mark.asyncio
    async def test_single_page_deletion_events(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_audit_records = AsyncMock(return_value=_resp(200, {
            "records": [
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-1"}, "created": "2024-01-01"},
                {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-2"}, "created": "2024-01-02"},
                {"objectItem": {"typeName": "FIELD_CHANGE", "name": "PROJ-3"}, "created": "2024-01-03"},
            ],
            "total": 3,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        keys, ok = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00.000Z", "2024-01-31T00:00:00.000Z")
        assert keys == ["PROJ-1", "PROJ-2"]
        assert ok is True

    @pytest.mark.asyncio
    async def test_pagination_loop(self):
        connector = _make_connector()

        call_count = 0

        async def mock_audit(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "records": [
                        {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-1"}}
                    ],
                    "total": 2,
                })
            return _resp(200, {
                "records": [
                    {"objectItem": {"typeName": "ISSUE_DELETE", "name": "PROJ-2"}}
                ],
                "total": 2,
            })

        ds = MagicMock()
        ds.get_audit_records = AsyncMock(side_effect=mock_audit)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        keys, ok = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00.000Z", "2024-01-31T00:00:00.000Z")
        assert "PROJ-1" in keys
        assert "PROJ-2" in keys
        assert ok is True

    @pytest.mark.asyncio
    async def test_api_failure_stops_paging(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_audit_records = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        # A 5xx is transient, not a permission problem: report ok=False so the caller holds
        # the deletion checkpoint, but do NOT send the missing-audit-permission notification
        # (that fires only on 403).
        connector.notify = AsyncMock()

        keys, ok = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00.000Z", "2024-01-31T00:00:00.000Z")
        assert keys == []
        assert ok is False
        connector.notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_records_returns_empty(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_audit_records = AsyncMock(return_value=_resp(200, {"records": [], "total": 0}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        keys, ok = await connector._fetch_deleted_issues_from_audit("2024-01-01T00:00:00.000Z", "2024-01-31T00:00:00.000Z")
        assert keys == []
        assert ok is True


# ===========================================================================
# _sync_user_groups
# ===========================================================================


class TestSyncUserGroups:

    @pytest.mark.asyncio
    async def test_no_groups_returns_empty_map(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=([], False))

        result = await connector._sync_user_groups([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_groups_with_members(self):
        connector = _make_connector()
        user = _app_user("dev@x.com")
        connector._fetch_groups = AsyncMock(return_value=([
            {"groupId": "g1", "name": "devs"},
        ], False))
        # _fetch_group_members now returns (account_ids, ok); members resolve by accountId.
        connector._fetch_group_members = AsyncMock(return_value=(["acc-1"], True))

        result = await connector._sync_user_groups([user])

        assert "g1" in result
        assert "devs" in result
        connector.data_entities_processor.on_new_user_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_group_without_id(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=([
            {"name": "no-id-group"},
        ], False))

        result = await connector._sync_user_groups([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_continues_on_group_error(self):
        connector = _make_connector()
        connector._fetch_groups = AsyncMock(return_value=([
            {"groupId": "g1", "name": "devs"},
            {"groupId": "g2", "name": "admins"},
        ], False))

        call_count = 0

        async def mock_members(gid, gname):
            nonlocal call_count
            call_count += 1
            if gname == "devs":
                raise RuntimeError("API error")
            return (["acc-1"], True)

        connector._fetch_group_members = AsyncMock(side_effect=mock_members)

        result = await connector._sync_user_groups([_app_user("admin@x.com")])
        assert call_count == 2
        assert "g2" in result


# ===========================================================================
# _fetch_groups (pagination loop)
# ===========================================================================


class TestFetchGroups:

    @pytest.mark.asyncio
    async def test_single_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.bulk_get_groups = AsyncMock(return_value=_resp(200, {
            "values": [{"groupId": "g1", "name": "devs"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        groups, fetch_failed = await connector._fetch_groups()
        assert len(groups) == 1
        assert fetch_failed is False

    @pytest.mark.asyncio
    async def test_multi_page(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        call_count = 0

        async def mock_groups(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "values": [{"groupId": f"g{i}", "name": f"g{i}"} for i in range(50)],
                    "isLast": False,
                })
            return _resp(200, {
                "values": [{"groupId": "g99", "name": "last"}],
                "isLast": True,
            })

        ds = MagicMock()
        ds.bulk_get_groups = AsyncMock(side_effect=mock_groups)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        groups, fetch_failed = await connector._fetch_groups()
        assert len(groups) == 51
        assert call_count == 2
        assert fetch_failed is False


# ===========================================================================
# _fetch_group_members (pagination loop)
# ===========================================================================


class TestFetchGroupMembers:

    @pytest.mark.asyncio
    async def test_single_page_members(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.get_users_from_group = AsyncMock(return_value=_resp(200, {
            "values": [{"accountId": "a1", "emailAddress": "a@x.com"}, {"accountId": "a2", "emailAddress": "b@x.com"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result, ok = await connector._fetch_group_members("g1", "devs")
        assert result == ["a1", "a2"]
        assert ok is True

    @pytest.mark.asyncio
    async def test_skips_members_without_account_id(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.get_users_from_group = AsyncMock(return_value=_resp(200, {
            "values": [{"displayName": "NoId"}, {"accountId": "a1", "emailAddress": "a@x.com"}],
            "isLast": True,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result, ok = await connector._fetch_group_members("g1", "devs")
        assert result == ["a1"]
        assert ok is True


# ===========================================================================
# _extract_issue_data
# ===========================================================================


class TestExtractIssueData:

    def test_basic_extraction(self):
        connector = _make_connector()
        user = _app_user()
        issue = _issue_dict()

        data = connector._extract_issue_data(issue, {user.source_user_id: user})

        assert data["issue_id"] == "1001"
        assert data["issue_key"] == "PROJ-1"
        assert "[PROJ-1]" in data["issue_name"]
        assert data["issue_type"] == ItemType.TASK
        assert data["parent_external_id"] is None

    def test_epic_detection(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["issuetype"] = {"name": "Epic", "hierarchyLevel": 1}

        data = connector._extract_issue_data(issue, {})
        assert data["issue_type"] == ItemType.EPIC

    def test_subtask_detection(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["issuetype"] = {"name": "Sub-task", "hierarchyLevel": -1}
        issue["fields"]["parent"] = {"id": "parent-1", "key": "PROJ-0"}

        data = connector._extract_issue_data(issue, {})
        assert data["issue_type"] == ItemType.SUBTASK
        assert data["parent_external_id"] == "parent-1"

    def test_user_email_resolution(self):
        connector = _make_connector()
        user = _app_user("dev@example.com", "acc-1")
        issue = _issue_dict()

        data = connector._extract_issue_data(issue, {"acc-1": user})
        assert data["creator_email"] == "dev@example.com"
        assert data["reporter_email"] == "dev@example.com"

    def test_no_creator(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["creator"] = None

        data = connector._extract_issue_data(issue, {})
        assert data["creator_email"] is None


# ===========================================================================
# _parse_issue_links
# ===========================================================================


class TestParseIssueLinks:

    def test_outward_link_extracted(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["issuelinks"] = [
            {
                "type": {"name": "Blocks", "outward": "blocks"},
                "outwardIssue": {"id": "2001", "key": "PROJ-2"},
            }
        ]

        related = connector._parse_issue_links(issue)
        assert len(related) == 1
        assert related[0].external_record_id == "2001"

    def test_inward_link_skipped(self):
        connector = _make_connector()
        issue = _issue_dict()
        issue["fields"]["issuelinks"] = [
            {
                "type": {"name": "Blocks", "inward": "is blocked by"},
                "inwardIssue": {"id": "2001", "key": "PROJ-2"},
            }
        ]

        related = connector._parse_issue_links(issue)
        assert len(related) == 0

    def test_none_issue_returns_empty(self):
        connector = _make_connector()
        assert connector._parse_issue_links(None) == []
        assert connector._parse_issue_links({}) == []

    def test_no_issuelinks_field(self):
        connector = _make_connector()
        issue = {"fields": {}}
        assert connector._parse_issue_links(issue) == []


# ===========================================================================
# _process_new_records
# ===========================================================================


class TestProcessNewRecords:

    @pytest.mark.asyncio
    async def test_sorts_epics_first(self):
        connector = _make_connector()

        epic = MagicMock(spec=TicketRecord)
        epic.parent_external_record_id = None
        epic.version = 0

        task = MagicMock(spec=TicketRecord)
        task.parent_external_record_id = "epic-1"
        task.version = 0

        records = [(task, [], True), (epic, [], True)]
        stats = {"new_count": 0, "updated_count": 0}

        await connector._process_new_records(records, "PROJ", stats)

        # Should have called on_new_records
        connector.data_entities_processor.on_new_records.assert_awaited()
        assert stats["new_count"] == 2

    @pytest.mark.asyncio
    async def test_counts_new_and_updated(self):
        connector = _make_connector()

        new_rec = MagicMock(spec=TicketRecord)
        new_rec.parent_external_record_id = None
        new_rec.version = 0

        updated_rec = MagicMock(spec=TicketRecord)
        updated_rec.parent_external_record_id = None
        updated_rec.version = 3

        stats = {"new_count": 0, "updated_count": 0}
        await connector._process_new_records(
            [(new_rec, [], True), (updated_rec, [], True)], "PROJ", stats
        )

        assert stats["new_count"] == 1
        assert stats["updated_count"] == 1
        connector.data_entities_processor.on_new_records.assert_awaited()
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()


# ===========================================================================
# _fetch_projects (pagination + filter)
# ===========================================================================


class TestFetchProjects:

    @pytest.mark.asyncio
    async def test_fetch_all_projects_no_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.search_projects = AsyncMock(return_value=_resp(200, {
            "values": [{"id": "p1", "key": "PROJ", "name": "Project 1"}],
            "isLast": True,
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._safe_json_parse = MagicMock(return_value={
            "values": [{"id": "p1", "key": "PROJ", "name": "Project 1"}],
            "isLast": True,
            "total": 1,
        })
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        record_groups, raw = await connector._fetch_projects(None, None)
        assert len(record_groups) == 1
        assert len(raw) == 1

    @pytest.mark.asyncio
    async def test_fetch_projects_with_keys_filter(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.search_projects = AsyncMock(return_value=_resp(200, {
            "values": [{"id": "p1", "key": "PROJ", "name": "Proj"}],
            "isLast": True,
            "total": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._safe_json_parse = MagicMock(return_value={
            "values": [{"id": "p1", "key": "PROJ", "name": "Proj"}],
            "isLast": True,
            "total": 1,
        })
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._fetch_project_permission_scheme = AsyncMock(return_value=[])

        from app.connectors.core.registry.filters import FilterOperatorType
        record_groups, _ = await connector._fetch_projects(["PROJ"], None)
        assert len(record_groups) == 1


# ===========================================================================
# _sync_project_roles
# ===========================================================================


class TestSyncProjectRoles:

    @pytest.mark.asyncio
    async def test_syncs_roles_for_project(self):
        connector = _make_connector()
        connector.data_source = MagicMock()

        ds = MagicMock()
        ds.get_project_roles = AsyncMock(return_value=_resp(200, {
            "Developers": "https://api.atlassian.net/rest/api/3/project/PROJ/role/10002"
        }))
        ds.get_project_role = AsyncMock(return_value=_resp(200, {
            "name": "Developers",
            "actors": [
                {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-1"}},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        user = _app_user("dev@x.com", "acc-1")
        await connector._sync_project_roles(["PROJ"], [user], {})

        connector.data_entities_processor.on_new_app_roles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_datasource_raises(self):
        connector = _make_connector()
        connector.data_source = None

        with pytest.raises(ValueError, match="not initialized"):
            await connector._sync_project_roles(["PROJ"], [], {})

    @pytest.mark.asyncio
    async def test_skips_addon_roles(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.get_project_roles = AsyncMock(return_value=_resp(200, {
            "atlassian-addons-project-access": "https://api.atlassian.net/rest/api/3/project/PROJ/role/99999"
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_project_roles(["PROJ"], [], {})
        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()


# ===========================================================================
# _fetch_project_permission_scheme
# ===========================================================================


class TestFetchProjectPermissionScheme:

    @pytest.mark.asyncio
    async def test_group_holder_permission(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_assigned_permission_scheme = AsyncMock(return_value=_resp(200, {"id": "10"}))
        ds.get_permission_scheme_grants = AsyncMock(return_value=_resp(200, {
            "permissions": [
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "group", "value": "g-dev"},
                }
            ]
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        perms = await connector._fetch_project_permission_scheme("PROJ")
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_anyone_holder_permission(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_assigned_permission_scheme = AsyncMock(return_value=_resp(200, {"id": "10"}))
        ds.get_permission_scheme_grants = AsyncMock(return_value=_resp(200, {
            "permissions": [
                {
                    "permission": "BROWSE_PROJECTS",
                    "holder": {"type": "anyone"},
                }
            ]
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        perms = await connector._fetch_project_permission_scheme("PROJ")
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_scheme_fetch_failure_returns_none(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_assigned_permission_scheme = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        # Transient 5xx -> None; the caller syncs the RecordGroup with an empty ACL
        # (returning [] would overwrite the ACL and hide the project from everyone).
        perms = await connector._fetch_project_permission_scheme("PROJ")
        assert perms is None


# ===========================================================================
# Placeholder sweep
# ===========================================================================


def _stub_record(external_id, project_id="p-1", parent_id=None):
    """An unreconciled placeholder as it comes back from the graph store."""
    return TicketRecord(
        id=f"rec-{external_id}",
        org_id="org-jira-1",
        record_name=external_id,
        record_type=RecordType.TICKET,
        external_record_id=external_id,
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.JIRA,
        connector_id="conn-jira-1",
        mime_type=MimeTypes.UNKNOWN.value,
        record_group_type=RecordGroupType.PROJECT,
        external_record_group_id=project_id,
        parent_external_record_id=parent_id,
        parent_record_type=RecordType.TICKET if parent_id else None,
        source_created_at=0,
        source_updated_at=0,
        is_placeholder=True,
    )


def _bulk_issue(issue_id, key, parent_id=None):
    return {
        "id": issue_id,
        "key": key,
        "fields": {
            "summary": f"Summary {key}",
            "status": {"name": "Open"},
            "priority": {"name": "High"},
            "issuetype": {"name": "Epic"},
            "project": {"id": "p-1", "key": "PROJ"},
            "created": "2024-01-01T00:00:00.000+0000",
            "updated": "2024-06-15T10:00:00.000+0000",
            "creator": {"accountId": "acc-1", "displayName": "Creator"},
            "reporter": {"accountId": "acc-1", "displayName": "Reporter"},
            "assignee": None,
            "parent": {"id": parent_id} if parent_id else None,
        },
    }


def _sweep_connector(bulk_pages):
    """Connector whose bulkfetch returns one page per call."""
    connector = _make_connector()
    connector.site_url = "https://co.atlassian.net"
    ds = MagicMock()
    ds.bulk_fetch_issues = AsyncMock(
        side_effect=[_resp(200, page) for page in bulk_pages]
    )
    connector._get_fresh_datasource = AsyncMock(return_value=ds)
    return connector, ds


class TestPlaceholderSweep:

    @pytest.mark.asyncio
    async def test_walks_ancestors_and_keeps_them_stubs(self):
        # Epic 2001 is an unreconciled stub; its parent (Initiative 3001) is only
        # discovered once the epic is fetched, so the sweep must run a second level.
        connector, ds = _sweep_connector([
            {"issues": [_bulk_issue("2001", "PROJ-2", parent_id="3001")]},
            {"issues": [_bulk_issue("3001", "PROJ-3")]},
        ])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[_stub_record("2001")]
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_stub_record("3001")
        )

        await connector._sweep_placeholder_records({"p-1"})

        assert ds.bulk_fetch_issues.await_count == 2
        submitted = [
            rec
            for call in connector.data_entities_processor.on_new_records.await_args_list
            for rec, _perms in call.args[0]
        ]
        assert [r.external_record_id for r in submitted] == ["2001", "3001"]
        assert [r.record_name for r in submitted] == [
            "[PROJ-2] Summary PROJ-2",
            "[PROJ-3] Summary PROJ-3",
        ]
        # Out-of-scope ancestors must never become indexable.
        assert all(r.is_placeholder for r in submitted)
        # A namespaced revision: _process_record persists an existing record only when the
        # revision differs, and it must never collide with the real issue's revision.
        assert all(
            r.external_revision_id.startswith("placeholder:") for r in submitted
        )
        assert submitted[0].parent_external_record_id == "3001"

    @pytest.mark.asyncio
    async def test_skips_stubs_outside_synced_projects(self):
        # A stub in a project the filter excluded is a cross-project link stub —
        # fetching it would pull an issue the user chose not to sync.
        connector, ds = _sweep_connector([])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[_stub_record("9001", project_id="p-other")]
        )

        await connector._sweep_placeholder_records({"p-1"})

        ds.bulk_fetch_issues.assert_not_awaited()
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resubmits_stub_when_source_fetch_fails(self):
        # Deleted or inaccessible ancestor: the stub is re-submitted so the structural
        # edges a full sync deleted are restored, and it stays a stub.
        stub = _stub_record("2001")
        connector, _ds = _sweep_connector([
            {"issues": [], "issueErrors": [{"issueId": "2001"}]},
        ])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[stub]
        )

        await connector._sweep_placeholder_records({"p-1"})

        submitted = connector.data_entities_processor.on_new_records.await_args.args[0]
        assert len(submitted) == 1
        assert submitted[0][0] is stub
        assert submitted[0][0].is_placeholder is True

    @pytest.mark.asyncio
    async def test_stops_at_ancestor_already_synced_in_scope(self):
        real_parent = _stub_record("3001")
        real_parent.is_placeholder = False
        connector, ds = _sweep_connector([
            {"issues": [_bulk_issue("2001", "PROJ-2", parent_id="3001")]},
        ])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[_stub_record("2001")]
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=real_parent
        )

        await connector._sweep_placeholder_records({"p-1"})

        assert ds.bulk_fetch_issues.await_count == 1

    @pytest.mark.asyncio
    async def test_batches_frontier_into_bulk_calls(self):
        connector, ds = _sweep_connector([{"issues": []}, {"issues": []}])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[_stub_record(str(4000 + i)) for i in range(150)]
        )

        await connector._sweep_placeholder_records({"p-1"})

        # 150 stubs cost 2 bulkfetch calls (100 + 50), not 150 single fetches.
        assert ds.bulk_fetch_issues.await_count == 2
        sizes = sorted(
            len(call.kwargs["issueIdsOrKeys"])
            for call in ds.bulk_fetch_issues.await_args_list
        )
        assert sizes == [50, 100]

    @pytest.mark.asyncio
    async def test_dedupes_a_parent_shared_by_two_stubs(self):
        # Two stubs under the same epic must not enqueue it twice.
        connector, ds = _sweep_connector([
            {"issues": [
                _bulk_issue("2001", "PROJ-2", parent_id="3001"),
                _bulk_issue("2002", "PROJ-3", parent_id="3001"),
            ]},
            {"issues": [_bulk_issue("3001", "PROJ-9")]},
        ])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[_stub_record("2001"), _stub_record("2002")]
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=_stub_record("3001")
        )

        await connector._sweep_placeholder_records({"p-1"})

        # Looked up once despite two children pointing at it.
        assert connector.data_entities_processor.get_record_by_external_id.await_count == 1
        assert ds.bulk_fetch_issues.await_count == 2

    @pytest.mark.asyncio
    async def test_skips_parent_missing_from_graph(self):
        # on_new_records should have materialised the parent stub; if it somehow did not,
        # skip rather than crash the sweep.
        connector, ds = _sweep_connector([
            {"issues": [_bulk_issue("2001", "PROJ-2", parent_id="3001")]},
        ])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[_stub_record("2001")]
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)

        await connector._sweep_placeholder_records({"p-1"})

        assert ds.bulk_fetch_issues.await_count == 1

    @pytest.mark.asyncio
    async def test_aborts_at_depth_cap(self):
        # A pathologically deep (or cyclic-by-id) chain must stop rather than walk forever.
        from app.connectors.sources.atlassian.jira_cloud.connector import (
            PLACEHOLDER_SWEEP_MAX_DEPTH,
        )

        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        counter = {"n": 0}

        async def endless_chain(**kwargs):
            counter["n"] += 1
            n = counter["n"]
            return _resp(200, {"issues": [
                _bulk_issue(str(2000 + n), f"PROJ-{n}", parent_id=str(2001 + n))
            ]})

        ds = MagicMock()
        ds.bulk_fetch_issues = AsyncMock(side_effect=endless_chain)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[_stub_record("2001")]
        )
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda **kw: _stub_record(kw["external_record_id"])
        )

        await connector._sweep_placeholder_records({"p-1"})

        assert ds.bulk_fetch_issues.await_count == PLACEHOLDER_SWEEP_MAX_DEPTH

    @pytest.mark.asyncio
    async def test_bulkfetch_transport_error_resubmits_stub(self):
        stub = _stub_record("2001")
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        ds = MagicMock()
        ds.bulk_fetch_issues = AsyncMock(side_effect=RuntimeError("boom"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])

        await connector._sweep_placeholder_records({"p-1"})

        submitted = connector.data_entities_processor.on_new_records.await_args.args[0]
        assert submitted[0][0] is stub
        assert submitted[0][0].is_placeholder is True

    @pytest.mark.asyncio
    async def test_bulkfetch_non_ok_status_resubmits_stub(self):
        stub = _stub_record("2001")
        connector, _ds = _sweep_connector([])
        connector._get_fresh_datasource.return_value.bulk_fetch_issues = AsyncMock(
            return_value=_resp(500, {})
        )
        connector.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])

        await connector._sweep_placeholder_records({"p-1"})

        submitted = connector.data_entities_processor.on_new_records.await_args.args[0]
        assert submitted[0][0] is stub

    @pytest.mark.asyncio
    async def test_bulkfetch_unparsable_body_resubmits_stub(self):
        stub = _stub_record("2001")
        bad = MagicMock()
        bad.status = 200
        bad.json = MagicMock(side_effect=ValueError("not json"))

        connector, _ds = _sweep_connector([])
        connector._get_fresh_datasource.return_value.bulk_fetch_issues = AsyncMock(return_value=bad)
        connector.data_entities_processor.get_placeholder_records = AsyncMock(return_value=[stub])

        await connector._sweep_placeholder_records({"p-1"})

        submitted = connector.data_entities_processor.on_new_records.await_args.args[0]
        assert submitted[0][0] is stub


    @pytest.mark.asyncio
    async def test_stub_revision_never_collides_with_the_real_issue(self):
        """The stub and the real record must differ, or _process_record skips the write
        that promotes the ancestor out of placeholder state."""
        connector, _ds = _sweep_connector([
            {"issues": [_bulk_issue("2001", "PROJ-2")]},
        ])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[_stub_record("2001")]
        )

        await connector._sweep_placeholder_records({"p-1"})

        stub = connector.data_entities_processor.on_new_records.await_args.args[0][0][0]
        real_revision = str(connector._parse_jira_timestamp("2024-06-15T10:00:00.000+0000"))
        assert stub.external_revision_id != real_revision
        assert real_revision in stub.external_revision_id

    @pytest.mark.asyncio
    async def test_skips_already_backfilled_stub_on_incremental_sync(self):
        # A stub the sweep has already filled in carries a "placeholder:" revision. Re-running
        # it every sync costs a bulkfetch plus an edge delete/recreate for no change.
        stub = _stub_record("2001")
        stub.external_revision_id = "placeholder:1718000000000"
        connector, ds = _sweep_connector([])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[stub]
        )

        await connector._sweep_placeholder_records({"p-1"})

        ds.bulk_fetch_issues.assert_not_awaited()
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resweeps_backfilled_stub_when_its_project_was_full_synced(self):
        # A full sync deletes BELONGS_TO / PARENT_CHILD, so the stub must be re-submitted
        # even though its metadata is already current.
        stub = _stub_record("2001")
        stub.external_revision_id = "placeholder:1718000000000"
        connector, ds = _sweep_connector([
            {"issues": [_bulk_issue("2001", "PROJ-2")]},
        ])
        connector.data_entities_processor.get_placeholder_records = AsyncMock(
            return_value=[stub]
        )

        await connector._sweep_placeholder_records({"p-1"}, {"p-1"})

        assert ds.bulk_fetch_issues.await_count == 1
        connector.data_entities_processor.on_new_records.assert_awaited()


class TestPlaceholderPromotion:

    @pytest.mark.asyncio
    async def test_placeholder_is_rebuilt_as_new_record(self):
        # Promoting a stub is semantically "new": version 0 so it is routed through
        # on_new_records and the indexer sees a newRecord event.
        connector = _make_connector()
        connector.site_url = "https://co.atlassian.net"
        connector.indexing_filters = None
        stub = _stub_record("1001")
        stub.version = 4

        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            return_value=stub
        )
        connector._handle_attachment_deletions_from_changelog = AsyncMock()
        connector._fetch_issue_attachments = AsyncMock(return_value=[])

        records, _ = await connector._build_issue_records(
            [_issue_dict()], "p-1", [], AsyncMock()
        )

        assert len(records) == 1
        assert records[0][0].version == 0
        assert records[0][0].is_placeholder is False

    @pytest.mark.asyncio
    async def test_stream_record_refuses_placeholder(self):
        # A client-visible "this can't be streamed" condition, so it must surface as a 400
        # rather than reaching the global handler as a 500.
        from fastapi import HTTPException

        connector = _make_connector()
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(_stub_record("2001"))
        assert exc_info.value.status_code == 400
        assert "placeholder" in exc_info.value.detail


# ===========================================================================
# _rate_limit_delay / permission-scheme guards
# ===========================================================================


class TestRateLimitDelay:

    def _resp_with_headers(self, headers):
        r = MagicMock()
        r.headers = headers
        return r

    def test_honors_retry_after_header(self):
        connector = _make_connector()
        delay = connector._rate_limit_delay(self._resp_with_headers({"Retry-After": "7"}), attempt=0)
        assert delay == 7.0

    def test_retry_after_is_case_insensitive(self):
        connector = _make_connector()
        delay = connector._rate_limit_delay(self._resp_with_headers({"retry-after": "3"}), attempt=2)
        assert delay == 3.0

    def test_retry_after_capped_so_a_huge_value_cannot_stall_the_sync(self):
        from app.connectors.sources.atlassian.jira_cloud.connector import RATE_LIMIT_MAX_DELAY_SEC

        connector = _make_connector()
        delay = connector._rate_limit_delay(
            self._resp_with_headers({"Retry-After": "99999"}), attempt=0
        )
        assert delay == RATE_LIMIT_MAX_DELAY_SEC

    def test_unparsable_retry_after_falls_back_to_backoff(self):
        connector = _make_connector()
        delay = connector._rate_limit_delay(
            self._resp_with_headers({"Retry-After": "soon"}), attempt=0
        )
        # 2s base with +/-30% jitter
        assert 1.4 <= delay <= 2.6

    def test_backoff_doubles_per_attempt_when_no_header(self):
        connector = _make_connector()
        first = connector._rate_limit_delay(self._resp_with_headers({}), attempt=0)
        third = connector._rate_limit_delay(self._resp_with_headers({}), attempt=2)
        assert 1.4 <= first <= 2.6      # 2s
        assert 5.6 <= third <= 10.4     # 8s

    def test_backoff_capped(self):
        from app.connectors.sources.atlassian.jira_cloud.connector import RATE_LIMIT_MAX_DELAY_SEC

        connector = _make_connector()
        delay = connector._rate_limit_delay(self._resp_with_headers({}), attempt=20)
        assert delay == RATE_LIMIT_MAX_DELAY_SEC

    def test_missing_headers_attribute_is_tolerated(self):
        connector = _make_connector()
        r = MagicMock()
        r.headers = None
        assert connector._rate_limit_delay(r, attempt=0) > 0


class TestPermissionSchemeIdGuard:

    @pytest.mark.asyncio
    async def test_scheme_without_id_returns_none_and_skips_grants(self):
        # A scheme payload with no id would build a malformed grants URL that can only fail;
        # bail out instead of burning the retry budget.
        connector = _make_connector()
        ds = MagicMock()
        ds.get_assigned_permission_scheme = AsyncMock(return_value=_resp(200, {}))
        ds.get_permission_scheme_grants = AsyncMock()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        result = await connector._fetch_project_permission_scheme("PROJ")

        assert result is None
        ds.get_permission_scheme_grants.assert_not_awaited()


# ===========================================================================
# Graceful-degradation paths: project record groups, skipped-project notice
# ===========================================================================


class TestBuildProjectRecordGroupDegradation:

    @pytest.mark.asyncio
    async def test_scheme_unavailable_syncs_project_with_empty_permissions(self):
        # A transient scheme failure must not drop the project — sync it with an empty ACL
        # so its issues keep flowing; the next successful fetch refreshes permissions.
        connector = _make_connector()
        connector._fetch_project_permission_scheme = AsyncMock(return_value=None)

        result = await connector._build_project_record_group(
            {"id": "p-1", "key": "PROJ", "name": "Project"}, {}, {},
        )

        assert result is not None
        record_group, permissions = result
        assert record_group.short_name == "PROJ"
        assert permissions == []

    @pytest.mark.asyncio
    async def test_returns_none_when_record_group_build_fails(self):
        # Returning None makes the caller skip (and notify about) this project rather than
        # aborting the whole sync.
        connector = _make_connector()
        connector._fetch_project_permission_scheme = AsyncMock(
            side_effect=RuntimeError("scheme blew up")
        )

        result = await connector._build_project_record_group(
            {"id": "p-1", "key": "PROJ", "name": "Project"}, {}, {},
        )

        assert result is None


class TestSkippedProjectNotification:

    @pytest.mark.asyncio
    async def test_notifies_when_projects_are_skipped(self):
        connector = _make_connector()
        connector.notify = AsyncMock()
        connector._list_projects_with_filter = AsyncMock(
            return_value=[{"id": "p-1", "key": "PROJ", "name": "Project"}]
        )
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._build_project_record_group = AsyncMock(return_value=None)

        record_groups, _raw = await connector._fetch_projects(None, None, [])

        assert record_groups == []
        connector.notify.assert_awaited_once()
        assert "PROJ" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_skipped_preview_truncates_past_ten(self):
        connector = _make_connector()
        connector.notify = AsyncMock()
        projects = [{"id": f"p-{i}", "key": f"P{i}", "name": f"Project {i}"} for i in range(13)]
        connector._list_projects_with_filter = AsyncMock(return_value=projects)
        connector._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        connector._build_project_record_group = AsyncMock(return_value=None)

        await connector._fetch_projects(None, None, [])

        message = connector.notify.await_args.kwargs["message"]
        assert "and 3 more" in message


class TestChangelogDeletionErrorIsolation:

    @pytest.mark.asyncio
    async def test_malformed_changelog_returns_ids_found_so_far(self):
        # A changelog that blows up mid-parse must not abort the issue's whole record build.
        connector = _make_connector()

        class Exploding(dict):
            def get(self, key, default=None):
                if key == "histories":
                    raise RuntimeError("bad changelog")
                return super().get(key, default)

        issue = {"id": "10001", "key": "PROJ-1", "fields": {"attachment": []},
                 "changelog": Exploding({"histories": []})}

        result = await connector._handle_attachment_deletions_from_changelog(issue)

        assert result == []


class TestInlineResolverEdgeCases:

    def test_ignores_malformed_comment_and_attachment_entries(self):
        # Jira payloads are not guaranteed well-formed; a non-dict entry must be skipped
        # rather than raising and dropping every attachment on the issue.
        connector = _make_connector()
        fields = {
            "description": None,
            "comment": {"comments": ["not-a-dict", None,
                                      {"id": "1", "body": '<p><img src="/rest/api/3/attachment/content/99"/></p>'}]},
            "attachment": [
                "not-a-dict",
                {"filename": "no-id.png", "mimeType": "image/png"},   # missing id
                {"id": "99", "filename": "x.png", "mimeType": "image/png", "size": 10},
            ],
        }
        assert connector._resolve_inline_image_attachment_ids(fields) == {"99"}

    def test_returns_empty_when_nothing_is_referenced(self):
        connector = _make_connector()
        fields = {
            "description": "<p>no images here</p>",
            "attachment": [{"id": "99", "filename": "x.png", "mimeType": "image/png", "size": 10}],
        }
        assert connector._resolve_inline_image_attachment_ids(fields) == set()

    def test_matches_by_filename_when_id_is_not_referenced(self):
        connector = _make_connector()
        fields = {
            "description": "!diagram.png!",
            "attachment": [{"id": "99", "filename": "diagram.png", "mimeType": "image/png", "size": 10}],
        }
        assert connector._resolve_inline_image_attachment_ids(fields) == {"99"}


class TestStreamAttachmentErrors:

    @pytest.mark.asyncio
    async def test_attachment_stream_error_propagates(self):
        # A mid-stream failure must surface, not silently truncate the file.
        connector = _make_connector()

        async def boom(*args, **kwargs):
            raise RuntimeError("connection reset")
            yield b""  # pragma: no cover - generator marker

        ds = MagicMock()
        ds.download_attachment_content = MagicMock(side_effect=boom)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        with pytest.raises(RuntimeError, match="connection reset"):
            async for _ in connector._stream_attachment_content("123", "attachment_123"):
                pass


# ===========================================================================
# Coverage: error / edge paths
# ===========================================================================


class TestCacheAuthenticatedProfile:

    def test_bad_response_is_ignored(self):
        connector = _make_connector()
        bad = MagicMock()
        type(bad).status = property(lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
        connector._cache_authenticated_jira_profile(bad)

    def test_non_ok_status_returns_early(self):
        connector = _make_connector()
        connector._cache_authenticated_jira_profile(_resp(500, {}))

    def test_unknown_timezone_falls_back_to_utc(self):
        connector = _make_connector()
        connector._cache_authenticated_jira_profile(
            _resp(200, {"emailAddress": "a@b.com", "timeZone": "Not/AZone"})
        )

    def test_valid_timezone_is_cached(self):
        connector = _make_connector()
        connector._cache_authenticated_jira_profile(
            _resp(200, {"emailAddress": "a@b.com", "timeZone": "UTC"})
        )
        assert connector._jql_timezone is not None


class TestMapBoundedEmpty:

    @pytest.mark.asyncio
    async def test_empty_items_returns_empty(self):
        connector = _make_connector()
        assert await connector._map_bounded([], AsyncMock()) == []


class TestParseJiraTimestamp:

    def test_parses_iso_with_offset(self):
        connector = _make_connector()
        assert connector._parse_jira_timestamp("2024-06-15T10:00:00.000+0000") > 0

    def test_none_returns_zero(self):
        connector = _make_connector()
        assert connector._parse_jira_timestamp(None) == 0

    def test_garbage_returns_zero(self):
        connector = _make_connector()
        assert connector._parse_jira_timestamp("not-a-date") == 0


class TestParseIssueLinksEdges:

    def test_skips_non_dict_links(self):
        connector = _make_connector()
        issue = {"fields": {"issuelinks": ["nope", None]}}
        assert connector._parse_issue_links(issue) == []

    def test_skips_links_without_type_or_outward(self):
        connector = _make_connector()
        issue = {"fields": {"issuelinks": [
            {"outwardIssue": {"id": "1"}},
            {"type": {"outward": "blocks"}},
            {"type": {"outward": "blocks"}, "outwardIssue": {}},
        ]}}
        assert connector._parse_issue_links(issue) == []

    def test_unknown_relation_falls_back_to_related(self):
        from app.models.entities import RecordRelations

        connector = _make_connector()
        issue = {"fields": {"issuelinks": [
            {"type": {"outward": "zzz-unmapped"}, "outwardIssue": {"id": "77"}},
        ]}}
        links = connector._parse_issue_links(issue)
        assert links[0].relation_type == RecordRelations.RELATED

    def test_no_issuelinks_returns_empty(self):
        connector = _make_connector()
        assert connector._parse_issue_links({"fields": {}}) == []
        assert connector._parse_issue_links(None) == []
        assert connector._parse_issue_links({"fields": None}) == []


class TestBuildIssueRecordsIsolation:

    @pytest.mark.asyncio
    async def test_malformed_issue_is_skipped_not_fatal(self):
        connector = _make_connector()
        connector.indexing_filters = None
        connector._build_records_for_issue = AsyncMock(side_effect=RuntimeError("bad issue"))

        records, delete_ids = await connector._build_issue_records(
            [_issue_dict()], "p-1", [], AsyncMock()
        )

        assert records == []
        assert delete_ids == []


class TestInlineResolverEdges:

    def test_skips_malformed_comment_and_attachment_entries(self):
        connector = _make_connector()
        img = "<p><img src=\"/rest/api/3/attachment/content/99\"/></p>"
        fields = {
            "description": None,
            "comment": {"comments": ["x", None, {"id": "1", "body": img}]},
            "attachment": [
                "not-a-dict",
                {"filename": "no-id.png", "mimeType": "image/png"},
                {"id": "99", "filename": "x.png", "mimeType": "image/png", "size": 10},
            ],
        }
        assert connector._resolve_inline_image_attachment_ids(fields) == {"99"}

    def test_nothing_referenced_returns_empty(self):
        connector = _make_connector()
        fields = {
            "description": "<p>plain</p>",
            "attachment": [{"id": "9", "filename": "x.png", "mimeType": "image/png", "size": 1}],
        }
        assert connector._resolve_inline_image_attachment_ids(fields) == set()


class TestFetchIssueAttachmentsEdges:

    @pytest.mark.asyncio
    async def test_exception_returns_empty_list(self):
        connector = _make_connector()
        connector._resolve_inline_image_attachment_ids = MagicMock(side_effect=RuntimeError("x"))
        result = await connector._fetch_issue_attachments(
            "1", "PROJ-1", {"attachment": [{"id": "1"}]}, [], "p-1",
            RecordGroupType.PROJECT, AsyncMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_no_attachments_returns_empty(self):
        connector = _make_connector()
        result = await connector._fetch_issue_attachments(
            "1", "PROJ-1", {"attachment": []}, [], "p-1",
            RecordGroupType.PROJECT, AsyncMock(),
        )
        assert result == []


class TestCallWithRetryRateLimit:

    @pytest.mark.asyncio
    async def test_429_sleeps_then_retries(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(return_value=MagicMock())
        responses = [_resp(429, {}), _resp(200, {"ok": True})]

        async def call(ds):
            return responses.pop(0)

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock:
            result = await connector._call_with_retry(call, ctx="test")

        assert result.status == 200
        sleep_mock.assert_awaited()

    @pytest.mark.asyncio
    async def test_429_on_last_attempt_returns_the_429(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(return_value=MagicMock())

        async def call(ds):
            return _resp(429, {})

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            result = await connector._call_with_retry(call, ctx="test", max_attempts=2)

        assert result.status == 429


class TestCleanupSwallowsErrors:

    @pytest.mark.asyncio
    async def test_cleanup_swallows_errors(self):
        connector = _make_connector()
        bad = MagicMock()
        type(bad).get_client = property(
            lambda s: (_ for _ in ()).throw(RuntimeError("nope"))
        )
        connector.external_client = bad
        await connector.cleanup()


class TestCheckAndFetchUpdatedEdges:

    @pytest.mark.asyncio
    async def test_issue_non_ok_status_returns_none(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_issue = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.indexing_filters = MagicMock()

        record = _stub_record("1001")
        assert await connector._check_and_fetch_updated_issue(record) is None

    @pytest.mark.asyncio
    async def test_unsupported_record_type_returns_none(self):
        connector = _make_connector()
        record = _stub_record("1001")
        record.record_type = RecordType.MAIL
        assert await connector._check_and_fetch_updated_record(record) is None

    @pytest.mark.asyncio
    async def test_attachment_fetch_exception_returns_none(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("boom"))

        record = _stub_record("attachment_5")
        record.record_type = RecordType.FILE
        assert await connector._check_and_fetch_updated_attachment(record) is None


# ===========================================================================
# Coverage: notifications, pagination bounds, streaming failures
# ===========================================================================


class TestNotificationPreviewTruncation:

    @pytest.mark.asyncio
    async def test_run_sync_failed_project_preview_truncates(self):
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector.notify = AsyncMock()
        user = _app_user()
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[user])

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection

            mock_filters.return_value = (FilterCollection(), FilterCollection())
            connector._fetch_users = AsyncMock(return_value=[user])
            connector._sync_user_groups = AsyncMock(return_value={})
            connector._fetch_projects = AsyncMock(return_value=([(_project_rg(), [])], []))
            connector._sync_project_roles = AsyncMock()
            connector._sync_project_lead_roles = AsyncMock()
            connector._get_issues_sync_checkpoint = AsyncMock(return_value=None)
            connector._sync_all_project_issues = AsyncMock(return_value={
                "total_synced": 0, "new_count": 0, "updated_count": 0,
                "failed_project_keys": [f"K{i}" for i in range(13)],
                "full_sync_project_ids": set(),
            })
            connector._update_issues_sync_checkpoint = AsyncMock()
            connector._handle_issue_deletions = AsyncMock()

            await connector.run_sync()

        assert "and 3 more" in connector.notify.await_args.kwargs["message"]

    @pytest.mark.asyncio
    async def test_sync_project_roles_failure_preview_truncates(self):
        connector = _make_connector()
        connector.notify = AsyncMock()
        connector.data_source = MagicMock()
        connector._fetch_project_roles = AsyncMock(
            side_effect=lambda pk, *a, **k: (pk, [], True)
        )

        await connector._sync_project_roles([f"K{i}" for i in range(13)], [], None)

        assert "and 3 more" in connector.notify.await_args.kwargs["message"]


class TestProcessGroupEdges:

    @pytest.mark.asyncio
    async def test_system_group_is_skipped(self):
        connector = _make_connector()
        result = await connector._process_group({"name": "atlassian-addons-admin", "groupId": "g1"}, {})
        assert result is None

    @pytest.mark.asyncio
    async def test_membership_failure_syncs_group_with_no_members(self):
        connector = _make_connector()
        connector._fetch_group_members = AsyncMock(return_value=([], False))

        result = await connector._process_group({"name": "devs", "groupId": "g1"}, {})

        assert result is not None
        _gid, _name, _group, members = result
        assert members == []


class TestPaginationBounds:

    @pytest.mark.asyncio
    async def test_project_search_unparsable_body_raises(self):
        connector = _make_connector()
        bad = MagicMock()
        bad.status = 200
        bad.json = MagicMock(side_effect=ValueError("bad"))
        connector._call_with_retry = AsyncMock(return_value=bad)
        connector.data_source = MagicMock()

        with pytest.raises(Exception, match="Failed to parse project search response"):
            await connector._paginate_project_search(None)

    @pytest.mark.asyncio
    async def test_issues_batched_unparsable_body_raises(self):
        connector = _make_connector()
        connector.sync_filters = None
        connector.data_source = MagicMock()
        bad = MagicMock()
        bad.status = 200
        bad.json = MagicMock(side_effect=ValueError("bad"))
        connector._search_issues_with_retry = AsyncMock(return_value=bad)

        with pytest.raises(Exception, match="Failed to parse issues response"):
            async for _ in connector._fetch_issues_batched("PROJ", "p-1", []):
                pass

    @pytest.mark.asyncio
    async def test_group_members_non_ok_stops_paging(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_users_from_group = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.data_source = MagicMock()

        members, ok = await connector._fetch_group_members("g1", "devs")
        assert members == []
        assert ok is False

    @pytest.mark.asyncio
    async def test_fetch_groups_non_ok_flags_failure(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_groups_paginated = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.data_source = MagicMock()

        groups, forbidden = await connector._fetch_groups()
        assert groups == []


class TestStreamingFailures:

    @pytest.mark.asyncio
    async def test_streaming_issue_missing_at_source_raises_404(self):
        from fastapi import HTTPException

        connector = _make_connector()
        connector._get_issue_with_retry = AsyncMock(return_value=_resp(404, {}))

        record = _stub_record("1001")
        record.is_placeholder = False
        with pytest.raises(HTTPException) as exc:
            await connector._process_issue_blockgroups_for_streaming(record)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_attachment_stream_error_propagates(self):
        connector = _make_connector()

        async def boom(*args, **kwargs):
            raise RuntimeError("connection reset")
            yield b""

        ds = MagicMock()
        ds.download_attachment_content = MagicMock(side_effect=boom)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        with pytest.raises(RuntimeError, match="connection reset"):
            async for _ in connector._stream_attachment_content("123", "attachment_123"):
                pass


class TestBase64OversizedBytes:

    @pytest.mark.asyncio
    async def test_actual_bytes_over_cap_are_not_inlined(self):
        from app.connectors.sources.atlassian.jira_cloud.connector import (
            MAX_INLINE_IMAGE_BYTES,
        )

        connector = _make_connector()
        big = MagicMock()
        big.status = 200
        big.bytes = MagicMock(return_value=b"x" * (MAX_INLINE_IMAGE_BYTES + 1))
        ds = MagicMock()
        ds.get_attachment_content = AsyncMock(return_value=big)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        cache = {}
        result = await connector._fetch_attachment_as_base64(
            {"id": "9", "mimeType": "image/png", "size": 0}, cache,
        )
        assert result is None
        assert cache["9"] is None


# ===========================================================================
# Regression: a full sync must rebuild edges without re-indexing everything
# ===========================================================================


class TestEdgeRebuildRecordsAreNotRepublished:

    @pytest.mark.asyncio
    async def test_unchanged_records_go_to_on_new_records(self):
        """During a full sync, unchanged issues are re-emitted only to recreate the edges
        a full sync deleted. Routing them to on_record_content_update would publish an
        updateRecord for every already-indexed issue, because that method publishes
        unconditionally; on_new_records skips records left COMPLETED.
        """
        connector = _make_connector()

        changed = MagicMock(spec=TicketRecord)
        changed.parent_external_record_id = None
        changed.version = 3

        unchanged = MagicMock(spec=TicketRecord)
        unchanged.parent_external_record_id = None
        unchanged.version = 7

        stats = {"new_count": 0, "updated_count": 0}
        await connector._process_new_records(
            [(changed, [], True), (unchanged, [], False)], "PROJ", stats
        )

        content_update = connector.data_entities_processor.on_record_content_update
        content_update.assert_awaited_once()
        assert content_update.await_args.args[0] is changed

        republished = connector.data_entities_processor.on_new_records.await_args.args[0]
        assert [r for r, _ in republished] == [unchanged]

        assert stats["updated_count"] == 1
        assert stats["new_count"] == 0
