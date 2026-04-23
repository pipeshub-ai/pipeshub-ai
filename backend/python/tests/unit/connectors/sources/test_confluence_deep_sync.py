"""Deep-sync-loop tests for ConfluenceConnector.

Covers: run_sync, _sync_users, _sync_user_groups, _sync_spaces,
_sync_content, _sync_permission_changes_from_audit_log,
_fetch_permission_audit_logs, _extract_content_title_from_audit_record,
_sync_content_permissions_by_titles, _fetch_space_permissions,
_fetch_page_permissions, _fetch_group_members, _extract_cursor_from_next_link.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.config.constants.arangodb import Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    CONTENT_EXPAND_PARAMS,
    PSEUDO_USER_GROUP_PREFIX,
    TIME_OFFSET_HOURS,
    ConfluenceConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    CommentRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.confluence.deep")
    dep = MagicMock()
    dep.org_id = "org-conf-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.migrate_group_to_user_by_external_id = AsyncMock()

    dsp = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    dsp.transaction = MagicMock(return_value=mock_tx)

    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return ConfluenceConnector(logger, dep, dsp, cs, "conn-conf-1", "team", "test-user")


def _resp(status=200, data=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=data or {})
    resp.text = MagicMock(return_value="")
    return resp


def _space_rg(key="DEV", sid="s-1"):
    return RecordGroup(
        id=str(uuid4()),
        org_id="org-conf-1",
        external_group_id=sid,
        connector_id="conn-conf-1",
        connector_name=Connectors.CONFLUENCE,
        name=f"Space {key}",
        short_name=key,
        group_type=RecordGroupType.CONFLUENCE_SPACES,
    )


# ===========================================================================
# run_sync
# ===========================================================================


class TestConfluenceRunSync:

    @pytest.mark.asyncio
    async def test_raises_if_client_not_initialized(self):
        connector = _make_connector()
        connector.external_client = None
        connector.data_source = None

        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            with pytest.raises(Exception, match="not initialized"):
                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_full_sync_flow(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()

        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            connector._sync_users = AsyncMock()
            connector._sync_user_groups = AsyncMock()
            rg = _space_rg()
            connector._sync_spaces = AsyncMock(return_value=[rg])
            connector._sync_content = AsyncMock()
            connector._sync_permission_changes_from_audit_log = AsyncMock()

            await connector.run_sync()

            connector._sync_users.assert_awaited_once()
            connector._sync_user_groups.assert_awaited_once()
            connector._sync_spaces.assert_awaited_once()
            # For each space: pages + blogposts
            assert connector._sync_content.await_count == 2
            connector._sync_permission_changes_from_audit_log.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_syncs_content_for_multiple_spaces(self):
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()

        with patch(
            "app.connectors.sources.atlassian.confluence_cloud.connector.load_connector_filters",
            new_callable=AsyncMock,
        ) as mock_filters:
            from app.connectors.core.registry.filters import FilterCollection
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            connector._sync_users = AsyncMock()
            connector._sync_user_groups = AsyncMock()
            connector._sync_spaces = AsyncMock(return_value=[_space_rg("S1"), _space_rg("S2")])
            connector._sync_content = AsyncMock()
            connector._sync_permission_changes_from_audit_log = AsyncMock()

            await connector.run_sync()
            # 2 spaces * 2 types (page + blogpost) = 4
            assert connector._sync_content.await_count == 4


# ===========================================================================
# _sync_users (pagination loop)
# ===========================================================================


class TestSyncUsers:

    @pytest.mark.asyncio
    async def test_single_page_users(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(200, {
            "results": [
                {"user": {"accountId": "u1", "displayName": "User1"}, "email": "u1@x.com"},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_to_app_user = MagicMock(return_value=MagicMock(
            email="u1@x.com", source_user_id="u1"
        ))

        await connector._sync_users()
        connector.data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_paginates_until_less_than_batch(self):
        connector = _make_connector()

        call_count = 0

        async def mock_search(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "results": [{"user": {"accountId": f"u{i}"}, "email": f"u{i}@x.com"} for i in range(100)],
                })
            return _resp(200, {
                "results": [{"user": {"accountId": "u100"}, "email": "u100@x.com"}],
            })

        ds = MagicMock()
        ds.search_users = AsyncMock(side_effect=mock_search)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_to_app_user = MagicMock(return_value=MagicMock(
            email="u@x.com", source_user_id="u1"
        ))

        await connector._sync_users()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_skips_users_without_email(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(200, {
            "results": [
                {"user": {"accountId": "u1", "displayName": "NoEmail"}, "email": ""},
                {"user": {"accountId": "u2", "displayName": "HasEmail"}, "email": "u2@x.com"},
            ],
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_to_app_user = MagicMock(return_value=MagicMock(
            email="u2@x.com", source_user_id="u2"
        ))

        await connector._sync_users()
        # Only one user should be passed
        args = connector.data_entities_processor.on_new_app_users.call_args[0][0]
        assert len(args) == 1

    @pytest.mark.asyncio
    async def test_api_failure_breaks_loop(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.search_users = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        # Should not raise but break the loop
        await connector._sync_users()


# ===========================================================================
# _sync_user_groups (pagination loop)
# ===========================================================================


class TestSyncUserGroups:

    @pytest.mark.asyncio
    async def test_single_page_groups(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_groups = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "g1", "name": "devs"}],
            "size": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._fetch_group_members = AsyncMock(return_value=["u@x.com"])
        connector._transform_to_user_group = MagicMock(return_value=MagicMock())
        connector._get_app_users_by_emails = AsyncMock(return_value=[])

        await connector._sync_user_groups()
        connector.data_entities_processor.on_new_user_groups.assert_awaited()

    @pytest.mark.asyncio
    async def test_skips_group_without_id(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_groups = AsyncMock(return_value=_resp(200, {
            "results": [{"name": "noid"}],
            "size": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_user_groups()

    @pytest.mark.asyncio
    async def test_pagination_stops_on_smaller_batch(self):
        connector = _make_connector()

        call_count = 0

        async def mock_get_groups(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "results": [{"id": f"g{i}", "name": f"g{i}"} for i in range(50)],
                    "size": 50,
                })
            return _resp(200, {
                "results": [{"id": "g99", "name": "last"}],
                "size": 1,
            })

        ds = MagicMock()
        ds.get_groups = AsyncMock(side_effect=mock_get_groups)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._fetch_group_members = AsyncMock(return_value=[])
        connector._transform_to_user_group = MagicMock(return_value=MagicMock())
        connector._get_app_users_by_emails = AsyncMock(return_value=[])

        await connector._sync_user_groups()
        assert call_count == 2


# ===========================================================================
# _sync_spaces (cursor-based pagination)
# ===========================================================================


class TestSyncSpaces:

    @pytest.mark.asyncio
    async def test_single_page_spaces(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        ds = MagicMock()
        ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "s1", "name": "Dev Space", "key": "DEV"}],
            "_links": {"base": "https://company.atlassian.net/wiki"},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._fetch_space_permissions = AsyncMock(return_value=[])
        connector._transform_to_space_record_group = MagicMock(return_value=_space_rg())

        spaces = await connector._sync_spaces()
        assert len(spaces) == 1
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cursor_pagination(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()

        call_count = 0

        async def mock_spaces(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "results": [{"id": "s1", "name": "Space1", "key": "S1"}],
                    "_links": {"base": "https://co.atlassian.net/wiki", "next": "/rest/api/space?cursor=abc123"},
                })
            return _resp(200, {
                "results": [{"id": "s2", "name": "Space2", "key": "S2"}],
                "_links": {"base": "https://co.atlassian.net/wiki"},
            })

        ds = MagicMock()
        ds.get_spaces = AsyncMock(side_effect=mock_spaces)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._fetch_space_permissions = AsyncMock(return_value=[])
        connector._transform_to_space_record_group = MagicMock(side_effect=[_space_rg("S1"), _space_rg("S2")])
        connector._extract_cursor_from_next_link = MagicMock(side_effect=["abc123", None])

        spaces = await connector._sync_spaces()
        assert len(spaces) == 2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_api_failure_breaks_loop(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        ds = MagicMock()
        ds.get_spaces = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        spaces = await connector._sync_spaces()
        assert len(spaces) == 0

    @pytest.mark.asyncio
    async def test_excluded_space_keys_filtered(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterOperator, SyncFilterKey
        # Use MagicMock for sync_filters since FilterCollection is a Pydantic model
        mock_filter = MagicMock()
        mock_filter.get_operator = MagicMock(return_value=FilterOperator.NOT_IN)
        mock_filter.get_value = MagicMock(return_value=["EXCLUDE_ME"])
        mock_sync_filters = MagicMock()
        mock_sync_filters.get = MagicMock(return_value=mock_filter)
        connector.sync_filters = mock_sync_filters

        ds = MagicMock()
        ds.get_spaces = AsyncMock(return_value=_resp(200, {
            "results": [
                {"id": "s1", "name": "Keep", "key": "KEEP"},
                {"id": "s2", "name": "Exclude", "key": "EXCLUDE_ME"},
            ],
            "_links": {"base": "https://co.atlassian.net/wiki"},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._fetch_space_permissions = AsyncMock(return_value=[])
        connector._transform_to_space_record_group = MagicMock(return_value=_space_rg("KEEP"))

        spaces = await connector._sync_spaces()
        assert len(spaces) == 1


# ===========================================================================
# _sync_content (pages/blogposts pagination loop)
# ===========================================================================


class TestSyncContent:

    @pytest.mark.asyncio
    async def test_pages_single_batch(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        page_data = {
            "id": "pg1",
            "title": "TestPage",
            "type": "page",
            "space": {"id": 123},
            "childTypes": {"comment": {"value": False}},
            "children": {"attachment": {"results": []}},
        }
        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_resp(200, {
            "results": [page_data],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._fetch_page_permissions = AsyncMock(return_value=[])

        mock_update = MagicMock()
        mock_update.record = MagicMock()
        mock_update.record.id = "rec-1"
        mock_update.record.inherit_permissions = True
        mock_update.record.indexing_status = None
        connector._process_webpage_with_update = AsyncMock(return_value=mock_update)

        await connector._sync_content("DEV", RecordType.CONFLUENCE_PAGE)

        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.pages_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blogposts_content_type(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        ds = MagicMock()
        ds.get_blogposts_v1 = AsyncMock(return_value=_resp(200, {
            "results": [],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        await connector._sync_content("DEV", RecordType.CONFLUENCE_BLOGPOST)
        # Should have called blogposts API, not pages API
        ds.get_blogposts_v1.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pagination_with_cursor(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        call_count = 0

        async def mock_pages(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "results": [{"id": "p1", "title": "P1", "space": {"id": 1}, "childTypes": {"comment": {"value": False}}, "children": {"attachment": {"results": []}}}],
                    "_links": {"next": "/rest/api?cursor=next123"},
                })
            return _resp(200, {
                "results": [{"id": "p2", "title": "P2", "space": {"id": 1}, "childTypes": {"comment": {"value": False}}, "children": {"attachment": {"results": []}}}],
                "_links": {},
            })

        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(side_effect=mock_pages)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._fetch_page_permissions = AsyncMock(return_value=[])
        connector._extract_cursor_from_next_link = MagicMock(side_effect=["next123", None])

        mock_update = MagicMock()
        mock_update.record = MagicMock()
        mock_update.record.id = "rec-1"
        mock_update.record.inherit_permissions = True
        mock_update.record.indexing_status = None
        connector._process_webpage_with_update = AsyncMock(return_value=mock_update)

        await connector._sync_content("DEV", RecordType.CONFLUENCE_PAGE)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_attachments_processed(self):
        connector = _make_connector()
        from app.connectors.core.registry.filters import FilterCollection
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.pages_sync_point = MagicMock()
        connector.pages_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.pages_sync_point.update_sync_point = AsyncMock()

        page_data = {
            "id": "pg1",
            "title": "TestPage",
            "space": {"id": 123},
            "childTypes": {"comment": {"value": False}},
            "children": {
                "attachment": {
                    "results": [
                        {"id": "att1", "title": "file.pdf", "metadata": {}, "extensions": {}}
                    ]
                }
            },
        }

        ds = MagicMock()
        ds.get_pages_v1 = AsyncMock(return_value=_resp(200, {
            "results": [page_data],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._fetch_page_permissions = AsyncMock(return_value=[])

        mock_update = MagicMock()
        mock_update.record = MagicMock()
        mock_update.record.id = "rec-1"
        mock_update.record.inherit_permissions = True
        mock_update.record.indexing_status = None
        connector._process_webpage_with_update = AsyncMock(return_value=mock_update)

        mock_att_rec = MagicMock()
        mock_att_rec.indexing_status = None
        connector._transform_to_attachment_file_record = MagicMock(return_value=mock_att_rec)

        await connector._sync_content("DEV", RecordType.CONFLUENCE_PAGE)
        connector.data_entities_processor.on_new_records.assert_awaited()


# ===========================================================================
# _sync_permission_changes_from_audit_log
# ===========================================================================


class TestSyncPermissionChangesFromAuditLog:

    @pytest.mark.asyncio
    async def test_first_run_initializes_checkpoint(self):
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.audit_log_sync_point.update_sync_point = AsyncMock()

        await connector._sync_permission_changes_from_audit_log()

        connector.audit_log_sync_point.update_sync_point.assert_awaited_once()
        # _fetch_permission_audit_logs should NOT be called on first run
        assert not hasattr(connector, '_fetch_permission_audit_logs_called')

    @pytest.mark.asyncio
    async def test_subsequent_run_fetches_audit_logs(self):
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time_ms": 1700000000000})
        connector.audit_log_sync_point.update_sync_point = AsyncMock()
        connector._fetch_permission_audit_logs = AsyncMock(return_value=["Page Title 1"])
        connector._sync_content_permissions_by_titles = AsyncMock()

        await connector._sync_permission_changes_from_audit_log()

        connector._fetch_permission_audit_logs.assert_awaited_once()
        connector._sync_content_permissions_by_titles.assert_awaited_once_with(["Page Title 1"])

    @pytest.mark.asyncio
    async def test_no_changes_still_updates_checkpoint(self):
        connector = _make_connector()
        connector.audit_log_sync_point = MagicMock()
        connector.audit_log_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time_ms": 1700000000000})
        connector.audit_log_sync_point.update_sync_point = AsyncMock()
        connector._fetch_permission_audit_logs = AsyncMock(return_value=[])

        await connector._sync_permission_changes_from_audit_log()

        connector.audit_log_sync_point.update_sync_point.assert_awaited()


# ===========================================================================
# _fetch_permission_audit_logs
# ===========================================================================


class TestFetchPermissionAuditLogs:

    @pytest.mark.asyncio
    async def test_extracts_content_titles(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_audit_logs = AsyncMock(return_value=_resp(200, {
            "results": [
                {
                    "category": "Permissions",
                    "associatedObjects": [
                        {"objectType": "Space", "name": "Dev"},
                        {"objectType": "Page", "name": "My Page"},
                    ],
                }
            ],
            "size": 1,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        titles = await connector._fetch_permission_audit_logs(1700000000000, 1700001000000)
        assert "My Page" in titles

    @pytest.mark.asyncio
    async def test_pagination_until_smaller_batch(self):
        connector = _make_connector()

        call_count = 0

        async def mock_audit(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "results": [
                        {
                            "category": "Permissions",
                            "associatedObjects": [
                                {"objectType": "Space", "name": "Dev"},
                                {"objectType": "Page", "name": "Page1"},
                            ],
                        }
                    ] * 100,
                    "size": 100,
                })
            return _resp(200, {
                "results": [
                    {
                        "category": "Permissions",
                        "associatedObjects": [
                            {"objectType": "Space", "name": "Dev"},
                            {"objectType": "Page", "name": "Page2"},
                        ],
                    }
                ],
                "size": 1,
            })

        ds = MagicMock()
        ds.get_audit_logs = AsyncMock(side_effect=mock_audit)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        titles = await connector._fetch_permission_audit_logs(1700000000000, 1700001000000)
        assert call_count == 2
        assert "Page1" in titles
        assert "Page2" in titles

    @pytest.mark.asyncio
    async def test_deduplicates_titles(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_audit_logs = AsyncMock(return_value=_resp(200, {
            "results": [
                {
                    "category": "Permissions",
                    "associatedObjects": [
                        {"objectType": "Space", "name": "S1"},
                        {"objectType": "Page", "name": "Same Page"},
                    ],
                },
                {
                    "category": "Permissions",
                    "associatedObjects": [
                        {"objectType": "Space", "name": "S1"},
                        {"objectType": "Page", "name": "Same Page"},
                    ],
                },
            ],
            "size": 2,
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        titles = await connector._fetch_permission_audit_logs(1700000000000, 1700001000000)
        assert len(titles) == 1


# ===========================================================================
# _extract_content_title_from_audit_record
# ===========================================================================


class TestExtractContentTitleFromAuditRecord:

    def test_returns_page_title(self):
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "Dev Space"},
                {"objectType": "Page", "name": "My Page"},
            ],
        }
        assert connector._extract_content_title_from_audit_record(record) == "My Page"

    def test_returns_blog_title(self):
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "Dev"},
                {"objectType": "Blog", "name": "My Blog"},
            ],
        }
        assert connector._extract_content_title_from_audit_record(record) == "My Blog"

    def test_returns_none_if_not_permissions_category(self):
        connector = _make_connector()
        record = {
            "category": "Content",
            "associatedObjects": [
                {"objectType": "Space", "name": "S1"},
                {"objectType": "Page", "name": "P1"},
            ],
        }
        assert connector._extract_content_title_from_audit_record(record) is None

    def test_returns_none_if_no_space(self):
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Page", "name": "Orphan Page"},
            ],
        }
        assert connector._extract_content_title_from_audit_record(record) is None

    def test_returns_none_if_no_content_object(self):
        connector = _make_connector()
        record = {
            "category": "Permissions",
            "associatedObjects": [
                {"objectType": "Space", "name": "S1"},
            ],
        }
        assert connector._extract_content_title_from_audit_record(record) is None


# ===========================================================================
# _fetch_space_permissions (pagination loop)
# ===========================================================================


class TestFetchSpacePermissions:

    @pytest.mark.asyncio
    async def test_single_page_permissions(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_space_permissions_assignments = AsyncMock(return_value=_resp(200, {
            "results": [{"group": {"id": "g1"}, "operations": ["read"]}],
            "_links": {},
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_space_permission = AsyncMock(return_value=Permission(
            entity_type=EntityType.GROUP, external_id="g1", type=PermissionType.READ
        ))

        perms = await connector._fetch_space_permissions("s1", "Dev Space")
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_cursor_pagination(self):
        connector = _make_connector()

        call_count = 0

        async def mock_perms(**kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _resp(200, {
                    "results": [{"group": {"id": "g1"}}],
                    "_links": {"next": "/rest?cursor=abc"},
                })
            return _resp(200, {
                "results": [{"group": {"id": "g2"}}],
                "_links": {},
            })

        ds = MagicMock()
        ds.get_space_permissions_assignments = AsyncMock(side_effect=mock_perms)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_space_permission = AsyncMock(return_value=Permission(
            entity_type=EntityType.GROUP, external_id="g1", type=PermissionType.READ
        ))
        connector._extract_cursor_from_next_link = MagicMock(side_effect=["abc", None])

        perms = await connector._fetch_space_permissions("s1", "Dev Space")
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_space_permissions_assignments = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        perms = await connector._fetch_space_permissions("s1", "Dev")
        assert perms == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        connector = _make_connector()
        connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("boom"))

        perms = await connector._fetch_space_permissions("s1", "Dev")
        assert perms == []


# ===========================================================================
# _fetch_page_permissions
# ===========================================================================


class TestFetchPagePermissions:

    @pytest.mark.asyncio
    async def test_returns_permissions(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_page_permissions_v1 = AsyncMock(return_value=_resp(200, {
            "results": [
                {"operation": "read", "restrictions": {"user": {"results": []}, "group": {"results": []}}}
            ]
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._transform_page_restriction_to_permissions = AsyncMock(return_value=[
            Permission(entity_type=EntityType.GROUP, external_id="g1", type=PermissionType.READ)
        ])

        perms = await connector._fetch_page_permissions("pg1")
        assert len(perms) == 1

    @pytest.mark.asyncio
    async def test_api_failure_returns_empty(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.get_page_permissions_v1 = AsyncMock(return_value=_resp(500, {}))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        perms = await connector._fetch_page_permissions("pg1")
        assert perms == []


# ===========================================================================
# _sync_content_permissions_by_titles
# ===========================================================================


class TestSyncContentPermissionsByTitles:

    @pytest.mark.asyncio
    async def test_empty_titles_noop(self):
        connector = _make_connector()
        await connector._sync_content_permissions_by_titles([])
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_existing_records_only(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.search_content_by_titles = AsyncMock(return_value=_resp(200, {
            "results": [
                {"id": "pg1", "title": "Page1", "type": "page"},
                {"id": "pg2", "title": "Page2", "type": "page"},
            ]
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)

        # Only pg1 exists in DB
        existing_record = MagicMock()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=lambda **kw: existing_record if kw.get("external_record_id") == "pg1" else None
        )
        connector._transform_to_webpage_record = MagicMock(return_value=MagicMock(inherit_permissions=True))
        connector._fetch_page_permissions = AsyncMock(return_value=[])

        await connector._sync_content_permissions_by_titles(["Page1", "Page2"])

        # Should update only pg1
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        args = connector.data_entities_processor.on_new_records.call_args[0][0]
        assert len(args) == 1

    @pytest.mark.asyncio
    async def test_raises_on_failures(self):
        connector = _make_connector()
        ds = MagicMock()
        ds.search_content_by_titles = AsyncMock(return_value=_resp(200, {
            "results": [{"id": "pg1", "title": "Fail", "type": "page"}]
        }))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=MagicMock())
        connector._transform_to_webpage_record = MagicMock(side_effect=RuntimeError("transform error"))

        with pytest.raises(ValueError, match="Failed to sync"):
            await connector._sync_content_permissions_by_titles(["Fail"])
