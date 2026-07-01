"""
Targeted coverage tests for remaining gaps in Jira Cloud, Cloud Personal, and
Data Center connector modules — ADF edge paths, OAuth init fallbacks, and
module-level DC wiki/attachment helpers.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.constants import OAuthConfigKeys
from app.connectors.sources.atlassian.jira_cloud.connector import (
    JiraConnector,
    adf_to_text,
    adf_to_text_with_images,
)
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
    _application_role_groups_from_dc_role,
)

def _make_cloud_connector() -> JiraConnector:
    logger = logging.getLogger("test.jira.gaps.cloud")
    logger.setLevel(logging.CRITICAL)
    dep = MagicMock()
    dep.org_id = "org-gap"
    dep.initialize = AsyncMock()
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.get_user_by_user_id = AsyncMock(return_value=None)
    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return JiraConnector(logger, dep, dsp, cs, "conn-gap", "team", "creator-1")


def _make_dc_connector() -> JiraDataCenterConnector:
    logger = logging.getLogger("test.jira.gaps.dc")
    logger.setLevel(logging.CRITICAL)
    dep = MagicMock()
    dep.org_id = "org-dc-gap"
    dep.initialize = AsyncMock()
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_all_app_users = AsyncMock(return_value=[])
    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return JiraDataCenterConnector(logger, dep, dsp, cs, "conn-dc-gap", "team", "u1")


class TestAdfCoverageGaps:
    def test_paragraph_containing_list_avoids_extra_spacing(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Before"},
                    {
                        "type": "bulletList",
                        "content": [{
                            "type": "listItem",
                            "content": [{
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "nested"}],
                            }],
                        }],
                    },
                ],
            }],
        }
        result = adf_to_text(adf)
        assert "Before" in result
        assert "nested" in result

    def test_heading_outside_table_uses_markdown_prefix(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "heading",
                "attrs": {"level": 3},
                "content": [{"type": "text", "text": "Section"}],
            }],
        }
        result = adf_to_text(adf)
        assert "### Section" in result

    def test_numbered_list_with_nested_content(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "orderedList",
                "content": [{
                    "type": "listItem",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "Parent"}]},
                        {
                            "type": "bulletList",
                            "content": [{
                                "type": "listItem",
                                "content": [{
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Child"}],
                                }],
                            }],
                        },
                    ],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "1. Parent" in result
        assert "Child" in result

    def test_inline_code_and_hard_break(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "paragraph",
                "content": [
                    {"type": "inlineCode", "text": "code"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "after"},
                ],
            }],
        }
        result = adf_to_text(adf)
        assert "`code`" in result
        assert "after" in result

    def test_single_node_without_doc_content_wrapper(self):
        adf = {
            "type": "paragraph",
            "content": [{"type": "text", "text": "standalone paragraph"}],
        }
        result = adf_to_text(adf)
        assert "standalone paragraph" in result

    @pytest.mark.asyncio
    async def test_adf_to_text_with_images_fetch_failure_uses_alt(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "media",
                "attrs": {"id": "m1", "alt": "fallback.png"},
            }],
        }

        async def failing_fetcher(_media_id: str, _alt: str) -> None:
            raise RuntimeError("fetch failed")

        result = await adf_to_text_with_images(adf, failing_fetcher)
        assert "fallback.png" in result

    def test_media_in_list_without_cache(self):
        adf = {
            "type": "doc",
            "content": [{
                "type": "bulletList",
                "content": [{
                    "type": "listItem",
                    "content": [{
                        "type": "media",
                        "attrs": {"id": "missing", "alt": "in-list.png"},
                    }],
                }],
            }],
        }
        result = adf_to_text(adf)
        assert "in-list.png" in result


class TestCloudInitCoverageGaps:
    @pytest.mark.asyncio
    async def test_oauth_resolves_base_url_from_shared_config(self):
        connector = _make_cloud_connector()
        mock_resource = MagicMock()
        mock_resource.id = "cloud-shared"
        mock_resource.url = "https://shared.atlassian.net"

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)
            MockJiraClient.get_accessible_resources = AsyncMock(return_value=[mock_resource])

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {
                    "authType": "OAUTH",
                    OAuthConfigKeys.OAUTH_CONFIG_ID: "oauth-config-1",
                },
                "credentials": {"access_token": "test-token"},
            })

            with patch(
                "app.connectors.sources.atlassian.jira_cloud.connector.fetch_oauth_config_by_id",
                new=AsyncMock(return_value={
                    OAuthConfigKeys.CONFIG: {"baseUrl": "https://shared.atlassian.net"},
                }),
            ):
                result = await connector.init()

        assert result is True
        assert connector.site_url == "https://shared.atlassian.net"

    @pytest.mark.asyncio
    async def test_oauth_missing_base_url_falls_back_to_first_resource(self):
        connector = _make_cloud_connector()
        mock_resource = MagicMock()
        mock_resource.id = "cloud-first"
        mock_resource.url = "https://first.atlassian.net"

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)
            MockJiraClient.get_accessible_resources = AsyncMock(return_value=[mock_resource])

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "test-token"},
            })

            with patch(
                "app.connectors.sources.atlassian.jira_cloud.connector.fetch_oauth_config_by_id",
                new=AsyncMock(return_value=None),
            ):
                result = await connector.init()

        assert result is True
        assert connector.cloud_id == "cloud-first"
        assert connector.site_url == "https://first.atlassian.net"

    @pytest.mark.asyncio
    async def test_oauth_no_base_url_and_no_resources_fails(self):
        connector = _make_cloud_connector()

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)
            MockJiraClient.get_accessible_resources = AsyncMock(return_value=[])

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {"access_token": "test-token"},
            })

            result = await connector.init()

        assert result is False

    @pytest.mark.asyncio
    async def test_init_resolves_creator_email_and_caches_myself(self):
        connector = _make_cloud_connector()
        creator = MagicMock()
        creator.email = "creator@example.com"
        connector.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=creator)

        with patch("app.connectors.sources.atlassian.jira_cloud.connector.JiraClient") as MockJiraClient:
            mock_client = MagicMock()
            mock_ds = MagicMock()
            mock_ds.get_current_user = AsyncMock(return_value=MagicMock(
                status=200,
                json=MagicMock(return_value={"emailAddress": "creator@example.com"}),
            ))
            mock_client.get_client = MagicMock(return_value=MagicMock())
            MockJiraClient.build_from_services = AsyncMock(return_value=mock_client)

            connector.config_service.get_config = AsyncMock(return_value={
                "auth": {"authType": "API_TOKEN", "baseUrl": "https://x.atlassian.net"},
            })

            with patch(
                "app.connectors.sources.atlassian.jira_cloud.connector.JiraDataSource",
                return_value=mock_ds,
            ):
                result = await connector.init()

        assert result is True
        assert connector.creator_email == "creator@example.com"


class TestCloudHandleDeletedIssueGaps:
    @pytest.mark.asyncio
    async def test_handle_deleted_issue_logs_exception(self):
        connector = _make_cloud_connector()
        connector.data_store_provider.transaction = MagicMock()
        connector.data_store_provider.transaction.return_value.__aenter__ = AsyncMock(
            side_effect=RuntimeError("tx failed"),
        )
        connector.data_store_provider.transaction.return_value.__aexit__ = AsyncMock(return_value=None)

        await connector._handle_deleted_issue("PROJ-1")

    @pytest.mark.asyncio
    async def test_sync_project_lead_roles_skips_lead_processing_errors(self):
        connector = _make_cloud_connector()
        connector.data_entities_processor.on_new_app_roles = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_cloud.connector.AppRole",
            side_effect=RuntimeError("lead lookup failed"),
        ):
            await connector._sync_project_lead_roles(
                [{"key": "PROJ", "lead": {"accountId": "bad"}}],
                [],
            )

        connector.data_entities_processor.on_new_app_roles.assert_not_awaited()


class TestDcModuleHelperGaps:
    def test_application_role_groups_skips_blank_names(self):
        role = {"groups": [" valid ", "", "  ", 123, None]}
        assert _application_role_groups_from_dc_role(role) == [
            {"groupId": "valid", "name": "valid"},
        ]


class TestDcInitAndAuditGaps:
    @pytest.mark.asyncio
    async def test_init_missing_auth_type_returns_false(self):
        conn = _make_dc_connector()
        conn.config_service.get_config = AsyncMock(
            return_value={"auth": {"baseUrl": "https://jira.example", "apiToken": "x"}},
        )
        assert await conn.init() is False

    def test_issue_key_from_auditing_event_skips_non_issue_objects(self):
        entity = {
            "affectedObjects": [
                {"type": "PROJECT", "name": "PROJ"},
                "bad",
                {"type": "ISSUE", "name": "PROJ-42"},
            ],
        }
        assert JiraDataCenterConnector._issue_key_from_auditing_event(entity) == "PROJ-42"

    @pytest.mark.asyncio
    async def test_detect_and_handle_deletions_no_audit_rows(self):
        conn = _make_dc_connector()
        conn._fetch_deleted_issues_from_audit = AsyncMock(return_value=[])
        assert await conn._detect_and_handle_deletions(1_700_000_000_000) == 0

    @pytest.mark.asyncio
    async def test_detect_and_handle_deletions_swallows_per_issue_errors(self):
        conn = _make_dc_connector()
        conn._fetch_deleted_issues_from_audit = AsyncMock(return_value=["PROJ-1", "PROJ-2"])
        conn._handle_deleted_issue = AsyncMock(side_effect=[None, RuntimeError("boom")])

        assert await conn._detect_and_handle_deletions(1_700_000_000_000) == 1

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_from_audit_paginates(self):
        conn = _make_dc_connector()
        page1 = MagicMock()
        page1.status = 200
        page1.json.return_value = {
            "entities": [{
                "affectedObjects": [{"type": "ISSUE", "name": "PROJ-1"}],
            }],
            "pagingInfo": {"lastPage": False, "nextPageOffset": 50},
        }
        page2 = MagicMock()
        page2.status = 200
        page2.json.return_value = {
            "entities": [{
                "affectedObjects": [{"type": "ISSUE", "name": "PROJ-2"}],
            }],
            "pagingInfo": {"lastPage": True},
        }
        ds = MagicMock()
        ds.get_auditing_events_v1 = AsyncMock(side_effect=[page1, page2])

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            keys = await conn._fetch_deleted_issues_from_audit(1_700_000_000_000)

        assert keys == ["PROJ-1", "PROJ-2"]
        assert ds.get_auditing_events_v1.await_count == 2

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_from_audit_unauthorized_returns_empty(self):
        conn = _make_dc_connector()
        resp = MagicMock()
        resp.status = 403
        resp.text.return_value = "forbidden"
        ds = MagicMock()
        ds.get_auditing_events_v1 = AsyncMock(return_value=resp)

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            assert await conn._fetch_deleted_issues_from_audit(1_700_000_000_000) == []

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_from_audit_exception_returns_partial(self):
        conn = _make_dc_connector()
        ok = MagicMock()
        ok.status = 200
        ok.json.return_value = {
            "entities": [{
                "affectedObjects": [{"type": "ISSUE", "name": "PROJ-9"}],
            }],
            "pagingInfo": {"lastPage": False, "nextPageOffset": 50},
        }
        ds = MagicMock()
        ds.get_auditing_events_v1 = AsyncMock(side_effect=[ok, RuntimeError("page 2 failed")])

        with patch.object(conn, "_get_fresh_datasource", new=AsyncMock(return_value=ds)):
            keys = await conn._fetch_deleted_issues_from_audit(1_700_000_000_000)

        assert keys == ["PROJ-9"]
