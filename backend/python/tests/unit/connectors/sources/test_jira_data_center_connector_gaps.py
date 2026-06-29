"""Additional unit tests for jira_data_center.connector coverage gaps."""

import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import ListOperator, SyncFilterKey
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
    build_jira_attachment_filename_lookup,
    jira_storage_text_to_markdown_with_images,
)
from app.models.entities import AppUser, RecordGroupType, RecordType, TicketRecord
from app.models.permission import EntityType, PermissionType


def _make_logger() -> logging.Logger:
    log = logging.getLogger("test.jira.dc.gaps")
    log.setLevel(logging.CRITICAL)
    return log


def _make_connector() -> JiraDataCenterConnector:
    logger = _make_logger()
    dep = MagicMock()
    dep.org_id = "org-dc-gaps"
    dep.initialize = AsyncMock()
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[MagicMock(email="admin@example.com")])
    dep.get_all_app_users = AsyncMock(return_value=[])

    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return JiraDataCenterConnector(logger, dep, dsp, cs, "conn-dc-gaps", "team", "u1")


def _ok_resp(data: Any) -> MagicMock:
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    resp.json = MagicMock(return_value=data)
    resp.text = MagicMock(return_value="")
    return resp


def _tx_ctx(store: Any) -> Any:
    @asynccontextmanager
    async def _cm():
        yield store

    return _cm()


class TestWikiAndAttachmentHelpers:
    def test_build_lookup_skips_non_dict_attachments_and_populates_mime(self):
        mime: dict[str, str] = {}
        lookup = build_jira_attachment_filename_lookup(
            mime,
            raw_attachments=[
                "bad",
                {"id": "10", "filename": "doc.pdf", "mimeType": "application/pdf"},
                {"id": "11"},
            ],
        )
        assert lookup["doc.pdf"] == "10"
        assert mime["10"] == "application/pdf"

    @pytest.mark.asyncio
    async def test_storage_text_non_string_returns_empty(self):
        md, embedded = await jira_storage_text_to_markdown_with_images(
            None, AsyncMock(), {}, {},
        )
        assert md == ""
        assert embedded == set()

    @pytest.mark.asyncio
    async def test_storage_text_fetch_failure_keeps_wiki_marker(self):
        mime = {"5": "image/png"}
        lookup = {"shot.png": "5", "shot.png".lower(): "5"}

        async def boom(_aid: str, _name: str) -> None:
            raise RuntimeError("fetch failed")

        wiki = "!shot.png|width=100!"
        md, embedded = await jira_storage_text_to_markdown_with_images(
            wiki, boom, lookup, mime,
        )
        assert "!shot.png" in md
        assert embedded == set()

    @pytest.mark.asyncio
    async def test_storage_text_non_image_attachment_left_unchanged(self):
        mime = {"9": "application/pdf"}
        lookup = {"doc.pdf": "9"}

        async def fetcher(_aid: str, _name: str) -> str:
            return "data:application/pdf;base64,QUJD"

        wiki = "[^doc.pdf]"
        md, embedded = await jira_storage_text_to_markdown_with_images(
            wiki, fetcher, lookup, mime,
        )
        assert "[^doc.pdf]" in md
        assert embedded == set()


class TestRunSyncAndInitGaps:
    @pytest.mark.asyncio
    async def test_run_sync_raises_when_init_fails(self):
        conn = _make_connector()
        conn.data_source = None
        conn.init = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="init failed"):
            await conn.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_logs_not_in_project_filter(self, monkeypatch):
        conn = _make_connector()
        conn.data_source = MagicMock()

        pfilter = MagicMock()
        pfilter.get_value = MagicMock(return_value=["SKIP"])
        pfilter.get_operator = MagicMock(return_value=ListOperator.NOT_IN)
        sync_f = MagicMock()
        sync_f.get = MagicMock(
            side_effect=lambda k: pfilter if k == SyncFilterKey.PROJECT_KEYS else None,
        )

        async def fake_load(*_a, **_kw):
            return sync_f, None

        monkeypatch.setattr(
            "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
            fake_load,
        )

        with patch.object(conn, "_fetch_users", new=AsyncMock(return_value=[])), patch.object(
            conn, "_sync_user_groups", new=AsyncMock(return_value={}),
        ), patch.object(
            conn, "_fetch_application_roles_to_groups_mapping", new=AsyncMock(return_value={}),
        ), patch.object(
            conn, "_fetch_projects", new=AsyncMock(return_value=([], [])),
        ) as mock_fetch, patch.object(
            conn, "_sync_project_roles", new=AsyncMock(),
        ), patch.object(
            conn, "_sync_project_lead_roles", new=AsyncMock(),
        ), patch.object(
            conn, "_sync_all_project_issues",
            new=AsyncMock(return_value={"total_synced": 0, "new_count": 0, "updated_count": 0}),
        ), patch.object(
            conn, "_get_issues_sync_checkpoint", new=AsyncMock(return_value=None),
        ), patch.object(
            conn, "_update_issues_sync_checkpoint", new=AsyncMock(),
        ), patch.object(
            conn, "_handle_issue_deletions", new=AsyncMock(),
        ):
            await conn.run_sync()

        mock_fetch.assert_awaited_once_with(["SKIP"], ListOperator.NOT_IN, [], app_roles_mapping={})


class TestIssueDeletionGaps:
    @pytest.mark.asyncio
    async def test_handle_issue_deletions_read_checkpoint_exception(self):
        conn = _make_connector()
        conn.issues_sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("etcd"))

        with patch.object(conn, "_detect_and_handle_deletions", new=AsyncMock()) as mock_detect:
            await conn._handle_issue_deletions(1_700_000_000_000)

        mock_detect.assert_awaited_once_with(1_700_000_000_000)

    @pytest.mark.asyncio
    async def test_handle_issue_deletions_checkpoint_update_failure(self):
        conn = _make_connector()
        conn.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.issues_sync_point.update_sync_point = AsyncMock(side_effect=RuntimeError("write fail"))

        with patch.object(conn, "_detect_and_handle_deletions", new=AsyncMock(return_value=1)):
            await conn._handle_issue_deletions(1_700_000_000_000)

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_audit_404_and_non_ok(self):
        conn = _make_connector()
        not_found = MagicMock()
        not_found.status = HttpStatusCode.NOT_FOUND.value
        server_error = MagicMock()
        server_error.status = 500
        server_error.text.return_value = "boom"
        ds = MagicMock()
        ds.get_auditing_events_v1 = AsyncMock(side_effect=[not_found, server_error])

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await conn._fetch_deleted_issues_from_audit(1_700_000_000_000) == []
            assert await conn._fetch_deleted_issues_from_audit(1_700_000_000_000) == []

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_empty_entities_breaks(self):
        conn = _make_connector()
        resp = _ok_resp({"entities": [], "pagingInfo": {"lastPage": True}})
        ds = MagicMock()
        ds.get_auditing_events_v1 = AsyncMock(return_value=resp)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await conn._fetch_deleted_issues_from_audit(1_700_000_000_000) == []

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_increments_offset_without_next_page(self):
        conn = _make_connector()
        page1 = _ok_resp({
            "entities": [{"affectedObjects": [{"type": "ISSUE", "name": "A-1"}]}],
            "pagingInfo": {"lastPage": False},
        })
        page2 = _ok_resp({
            "entities": [{"affectedObjects": [{"type": "ISSUE", "name": "A-2"}]}],
            "pagingInfo": {"lastPage": True},
        })
        ds = MagicMock()
        ds.get_auditing_events_v1 = AsyncMock(side_effect=[page1, page2])

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            keys = await conn._fetch_deleted_issues_from_audit(1_700_000_000_000)

        assert keys == ["A-1", "A-2"]

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_not_in_database(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.get_issue_v2 = AsyncMock(return_value=MagicMock(status=404))
        tx = MagicMock()
        tx.get_record_by_issue_key = AsyncMock(return_value=None)
        conn.data_store_provider.transaction = MagicMock(return_value=_tx_ctx(tx))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            await conn._handle_deleted_issue("MISSING-1")

        tx.delete_records_and_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_get_issue_exception_is_ignored(self):
        conn = _make_connector()
        ds = MagicMock()
        ds.get_issue_v2 = AsyncMock(side_effect=RuntimeError("network"))
        tx = MagicMock()
        tx.get_record_by_issue_key = AsyncMock(return_value=None)
        conn.data_store_provider.transaction = MagicMock(return_value=_tx_ctx(tx))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            await conn._handle_deleted_issue("PROJ-9")

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_outer_exception_logged(self):
        conn = _make_connector()
        conn.data_store_provider.transaction = MagicMock(side_effect=RuntimeError("tx boom"))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=MagicMock())):
            await conn._handle_deleted_issue("PROJ-1")

    @pytest.mark.asyncio
    async def test_delete_direct_attachment_records_error_returns_zero(self):
        conn = _make_connector()
        tx = MagicMock()
        tx.get_records_by_parent = AsyncMock(side_effect=RuntimeError("db"))

        assert await conn._delete_direct_attachment_records("100", tx) == 0


class TestPermissionSchemeGaps:
    @pytest.mark.asyncio
    async def test_permission_grants_non_list_normalized(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        sch = _ok_resp({"id": 1, "permissions": {"bad": "shape"}})
        ds = MagicMock()
        ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)
        ds.get_permission_scheme_grants_v2 = AsyncMock()

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            perms = await conn._fetch_project_permission_scheme("PROJ", {})

        assert perms == []
        ds.get_permission_scheme_grants_v2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_application_role_bare_grants_all_licensed_users(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        sch = _ok_resp({
            "id": 1,
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "applicationRole"},
            }],
        })
        ds = MagicMock()
        ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            perms = await conn._fetch_project_permission_scheme("PROJ", {})

        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.ORG
        assert perms[0].external_id == "all_licensed_users"

    @pytest.mark.asyncio
    async def test_application_role_403_grants_configuring_user(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.creator_email = "owner@example.com"
        conn._app_roles_forbidden = True
        sch = _ok_resp({
            "id": 1,
            "permissions": [{
                "permission": "BROWSE_PROJECTS",
                "holder": {"type": "applicationRole", "parameter": "jira-software"},
            }],
        })
        ds = MagicMock()
        ds.get_assigned_permission_scheme_v2 = AsyncMock(return_value=sch)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            perms = await conn._fetch_project_permission_scheme("PROJ", {})

        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.USER
        assert perms[0].email == "owner@example.com"


class TestUserAndGroupGaps:
    @pytest.mark.asyncio
    async def test_fetch_users_empty_batch_breaks_pagination(self):
        conn = _make_connector()
        conn.data_source = MagicMock()

        first = _ok_resp([{"key": "u1", "active": True}])
        empty = _ok_resp([])
        ds = MagicMock()
        ds.get_user_search_v2 = AsyncMock(side_effect=[first, empty])

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            raw = await conn._fetch_all_jira_users_bulk()

        assert len(raw) == 1

    @pytest.mark.asyncio
    async def test_resolve_private_email_users_skips_empty_user_key(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        ds = MagicMock()
        ds.get_user_search_v2 = AsyncMock(return_value=_ok_resp([{"displayName": "No Key"}]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            found = await conn._resolve_private_email_users(
                {"user@example.com"},
                set(),
                {},
            )

        assert found == 0

    @pytest.mark.asyncio
    async def test_sync_user_groups_no_valid_members_logs(self):
        conn = _make_connector()
        conn.data_source = MagicMock()

        with patch.object(conn, "_fetch_groups", new=AsyncMock(return_value=[])):
            result = await conn._sync_user_groups([])

        assert result == {}
        conn.data_entities_processor.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fetch_groups_bad_payload_shapes(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        ds = MagicMock()
        ds.groups_picker_get_v2 = AsyncMock(return_value=_ok_resp([]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await conn._fetch_groups() == []

        ds.groups_picker_get_v2 = AsyncMock(return_value=_ok_resp({"groups": "bad"}))
        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await conn._fetch_groups() == []

    @pytest.mark.asyncio
    async def test_fetch_group_members_requires_name_and_datasource(self):
        conn = _make_connector()
        conn.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await conn._fetch_group_members("gid", "gname")

        conn.data_source = MagicMock()
        assert await conn._fetch_group_members("gid", "") == []


class TestIssueLinkAndEpicDiscoveryGaps:
    def test_parse_issue_links_skips_missing_linked_id(self):
        conn = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [{
                    "type": {"outward": "blocks"},
                    "outwardIssue": {"key": "B-1"},
                }],
            },
        }
        assert conn._parse_issue_links(issue) == []

    def test_parse_issue_links_outward_success(self):
        conn = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [{
                    "type": {"outward": "blocks", "name": "Blocks"},
                    "outwardIssue": {"id": "200", "key": "B-1"},
                }],
            },
        }
        rows = conn._parse_issue_links(issue)
        assert len(rows) == 1
        assert rows[0].external_record_id == "200"

    def test_parse_issue_links_logs_and_continues_on_bad_link(self):
        conn = _make_connector()
        issue = {
            "fields": {
                "issuelinks": [
                    {"type": {"outward": "x"}, "outwardIssue": "bad"},
                    {
                        "type": {"outward": "relates"},
                        "outwardIssue": {"id": "300", "key": "C-1"},
                    },
                ],
            },
        }
        rows = conn._parse_issue_links(issue)
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_discover_epic_link_field_http_error(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        ds = MagicMock()
        ds.get_fields_v2 = AsyncMock(return_value=MagicMock(status=500))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            await conn._discover_epic_link_field_id()

        assert conn._epic_link_field_id == ""

    @pytest.mark.asyncio
    async def test_discover_epic_link_field_from_schema(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        ds = MagicMock()
        ds.get_fields_v2 = AsyncMock(return_value=_ok_resp([
            {
                "id": "customfield_10014",
                "name": "Epic Link",
                "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"},
            },
        ]))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            await conn._discover_epic_link_field_id()

        assert conn._epic_link_field_id == "customfield_10014"

    @pytest.mark.asyncio
    async def test_sync_project_lead_roles_processing_error_skipped(self):
        conn = _make_connector()
        conn.data_entities_processor.on_new_app_roles = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.AppRole",
            side_effect=RuntimeError("role build failed"),
        ):
            await conn._sync_project_lead_roles([{"key": "P"}], [])

        conn.data_entities_processor.on_new_app_roles.assert_not_awaited()


class TestBuildIssueRecordsAndCommentsGaps:
    @pytest.mark.asyncio
    async def test_build_issue_records_attachment_failure_is_logged(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.site_url = "https://jira.example"
        tx = MagicMock()
        tx.get_record_by_external_id = AsyncMock(return_value=None)

        issue = {
            "id": "100",
            "key": "P-1",
            "fields": {
                "summary": "T",
                "updated": "2024-01-01T00:00:00.000+0000",
                "created": "2024-01-01T00:00:00.000+0000",
                "issuetype": {"name": "Task"},
                "attachment": [{"id": "1", "filename": "a.png", "mimeType": "image/png"}],
            },
        }

        mapper = MagicMock()
        mapper.map_type.return_value = "Task"
        mapper.map_status.return_value = "Open"
        mapper.map_priority.return_value = "Low"
        conn.value_mapper = mapper

        with patch.object(
            conn,
            "_fetch_issue_attachments",
            new=AsyncMock(side_effect=RuntimeError("attachments failed")),
        ):
            records = await conn._build_issue_records(
                [issue],
                "pid",
                [],
                tx,
                is_new_project=True,
            )

        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_fetch_all_issue_comments_paginates_remaining(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        ds = MagicMock()
        ds.get_issue_comments_v2 = AsyncMock(return_value=_ok_resp({
            "comments": [{"id": "c2", "body": "second"}],
        }))

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            comments = await conn._fetch_all_issue_comments(
                "P-1",
                already_fetched=[{"id": "c1", "body": "first"}],
                expected_total=2,
            )

        assert len(comments) == 2
        ds.get_issue_comments_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_all_issue_comments_stops_on_http_error(self):
        conn = _make_connector()
        conn.data_source = MagicMock()
        bad = MagicMock()
        bad.status = 500
        ds = MagicMock()
        ds.get_issue_comments_v2 = AsyncMock(return_value=bad)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            comments = await conn._fetch_all_issue_comments(
                "P-1",
                already_fetched=[{"id": "c1"}],
                expected_total=5,
            )

        assert len(comments) == 1

    @pytest.mark.asyncio
    async def test_process_issue_blockgroups_paginates_embedded_comments(self):
        conn = _make_connector()
        conn.site_url = "https://jira.example"
        record = TicketRecord(
            id="rec-1",
            org_id="org-dc-gaps",
            record_name="P-1",
            record_type=RecordType.TICKET,
            external_record_id="100",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.JIRA_DATA_CENTER,
            connector_id="conn-dc-gaps",
            mime_type=MimeTypes.BLOCKS.value,
            external_record_group_id="proj",
            record_group_type=RecordGroupType.PROJECT,
            weburl="https://jira.example/browse/P-1",
        )
        issue_payload = {
            "key": "P-1",
            "fields": {
                "summary": "Title",
                "description": "Body",
                "attachment": [],
                "comment": {
                    "comments": [{"id": "c1", "body": "one"}],
                    "total": 2,
                },
                "project": {"id": "proj"},
            },
        }
        ds = MagicMock()
        ds.get_issue_v2 = AsyncMock(return_value=_ok_resp(issue_payload))

        with patch.object(conn, "_get_issue_with_retry", new=AsyncMock(return_value=_ok_resp(issue_payload))), patch.object(
            conn,
            "_fetch_all_issue_comments",
            new=AsyncMock(return_value=[{"id": "c1", "body": "one"}, {"id": "c2", "body": "two"}]),
        ), patch.object(
            conn,
            "_parse_issue_to_blocks",
            new=AsyncMock(return_value=MagicMock(model_dump_json=MagicMock(return_value="{}"))),
        ):
            result = await conn._process_issue_blockgroups_for_streaming(record)

        assert isinstance(result, bytes)


class TestTimestampParsingGap:
    def test_parse_jira_timestamp_strptime_fallback_and_failure(self):
        conn = _make_connector()
        assert conn._parse_jira_timestamp("2024-01-15T10:30:45+0000") > 0
        assert conn._parse_jira_timestamp("not-a-date") == 0


class TestRemainingQuickGaps:
    @pytest.mark.asyncio
    async def test_fetch_users_requires_data_source(self):
        conn = _make_connector()
        conn.data_source = None
        with pytest.raises(ValueError, match="not initialized"):
            await conn._fetch_users()

    @pytest.mark.asyncio
    async def test_process_issue_blockgroups_accepts_list_comments(self):
        conn = _make_connector()
        conn.site_url = "https://jira.example"
        record = TicketRecord(
            id="rec-2",
            org_id="org-dc-gaps",
            record_name="P-2",
            record_type=RecordType.TICKET,
            external_record_id="101",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.JIRA_DATA_CENTER,
            connector_id="conn-dc-gaps",
            mime_type=MimeTypes.BLOCKS.value,
            external_record_group_id="proj",
            record_group_type=RecordGroupType.PROJECT,
            weburl="https://jira.example/browse/P-2",
        )
        issue_payload = {
            "key": "P-2",
            "fields": {
                "summary": "Title",
                "description": "Body",
                "attachment": [],
                "comment": [{"id": "c1", "body": "legacy list"}],
                "project": {"id": "proj"},
            },
        }

        with patch.object(conn, "_get_issue_with_retry", new=AsyncMock(return_value=_ok_resp(issue_payload))), patch.object(
            conn,
            "_parse_issue_to_blocks",
            new=AsyncMock(return_value=MagicMock(model_dump_json=MagicMock(return_value="{}"))),
        ):
            result = await conn._process_issue_blockgroups_for_streaming(record)

        assert isinstance(result, bytes)

    @pytest.mark.asyncio
    async def test_fetch_issue_attachments_skips_missing_id(self):
        conn = _make_connector()
        conn.site_url = "https://jira.example"
        tx = MagicMock()
        tx.get_record_by_external_id = AsyncMock(return_value=None)
        fields = {"attachment": [{"filename": "no-id.png", "mimeType": "image/png"}]}

        records = await conn._fetch_issue_attachments(
            "100",
            "P-1",
            fields,
            [],
            "proj",
            RecordGroupType.PROJECT,
            tx,
        )
        assert records == []
