"""
Unit tests for Jira Data Center connector: factory wiring, app/constants, init paths,
datasource access, filter options, connection test, cleanup, create_connector, helpers,
and lightweight run_sync orchestration.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import AppGroups, Connectors, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.connectors.core.registry.filters import IndexingFilterKey
from app.connectors.sources.atlassian.core.apps import JiraDataCenterApp
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
    _normalize_jira_dc_group_row,
    adf_to_text,
    extract_media_from_adf,
)


def _make_logger() -> logging.Logger:
    log = logging.getLogger("test.jira.dc")
    log.setLevel(logging.CRITICAL)
    return log


def _make_deps():
    """Minimal processor + data store + config service for connector ctor."""
    logger = _make_logger()
    dep = MagicMock()
    dep.org_id = "org-dc-test"
    dep.initialize = AsyncMock()
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_new_app_roles = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.get_all_app_users = AsyncMock(return_value=[])

    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logger, dep, dsp, cs


def _make_connector(connector_id: str = "jira-dc-conn-1") -> JiraDataCenterConnector:
    logger, dep, dsp, cs = _make_deps()
    return JiraDataCenterConnector(logger, dep, dsp, cs, connector_id, "team", "creator-1")


def _auth_config_api_token_pat() -> dict:
    return {
        "authType": "API_TOKEN",
        "baseUrl": "https://jira.company.com/",
        "apiToken": "pat-secret",
    }


def _auth_config_basic() -> dict:
    return {
        "authType": "BASIC_AUTH",
        "baseUrl": "https://jira.company.com/",
        "username": "svc",
        "password": "pass",
    }


def _wrap_config(auth: dict) -> dict:
    return {"auth": auth}


# -----------------------------------------------------------------------------
# Factory + app + constants
# -----------------------------------------------------------------------------


class TestJiraDcConnectorFactoryRegistry:
    def test_factory_resolves_jiradatacenter(self) -> None:
        cls = ConnectorFactory.get_connector_class("jiradatacenter")
        assert cls is JiraDataCenterConnector

    def test_factory_resolves_jiradatacenterpersonal(self) -> None:
        from app.connectors.sources.atlassian.jira_data_center_personal.connector import (
            JiraDataCenterPersonalConnector,
        )

        cls = ConnectorFactory.get_connector_class("jiradatacenterpersonal")
        assert cls is JiraDataCenterPersonalConnector

    def test_factory_case_insensitive(self) -> None:
        assert ConnectorFactory.get_connector_class("JiraDataCenter") is JiraDataCenterConnector

    def test_list_connectors_contains_key(self) -> None:
        names = ConnectorFactory.list_connectors()
        assert "jiradatacenter" in names
        assert names["jiradatacenter"] is JiraDataCenterConnector
        assert "jiradatacenterpersonal" in names


class TestJiraDataCenterApp:
    def test_app_identity(self) -> None:
        app = JiraDataCenterApp("cid-99")
        assert app.get_app_name() == Connectors.JIRA_DATA_CENTER
        assert app.get_app_group_name() == AppGroups.ATLASSIAN
        assert app.get_connector_id() == "cid-99"


class TestJiraDataCenterConnectorMetadata:
    def test_registers_indexing_filters_like_cloud(self) -> None:
        fields = JiraDataCenterConnector._connector_metadata["config"]["filters"]["indexing"]["schema"]["fields"]
        names = {f["name"] for f in fields}
        assert IndexingFilterKey.ISSUES.value in names
        assert IndexingFilterKey.ISSUE_ATTACHMENTS.value in names


class TestJiraDataCenterAttachmentIndexingFilter:
    def test_create_attachment_file_record_respects_issue_attachments_filter(self) -> None:
        conn = _make_connector()
        conn.indexing_filters = MagicMock()
        conn.indexing_filters.is_enabled = MagicMock(return_value=False)
        record = conn._create_attachment_file_record(
            attachment_id="99",
            filename="doc.pdf",
            mime_type="application/pdf",
            file_size=100,
            created_at=1_700_000_000_000,
            parent_issue_id="10001",
            parent_node_id="node-1",
            project_id="PROJ",
            weburl="https://jira.company.com/browse/K-1",
        )
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        conn.indexing_filters.is_enabled.assert_called_with(IndexingFilterKey.ISSUE_ATTACHMENTS)


# -----------------------------------------------------------------------------
# init()
# -----------------------------------------------------------------------------


class TestJiraDataCenterConnectorInit:
    @pytest.mark.asyncio
    async def test_init_api_token_pat_success(self) -> None:
        conn = _make_connector()
        cfg = _wrap_config(_auth_config_api_token_pat())
        conn.config_service.get_config = AsyncMock(return_value=cfg)

        from app.sources.client.jira.jira import JiraClient, JiraRESTClientViaToken

        inner = JiraRESTClientViaToken("https://jira.company.com", "pat-secret", "Bearer")
        jc = JiraClient(inner)
        with patch.object(JiraClient, "build_from_services", new_callable=AsyncMock, return_value=jc):
            ok = await conn.init()

        assert ok is True
        assert conn.site_url == "https://jira.company.com"
        assert conn.external_client is jc
        assert conn.data_source is not None

    @pytest.mark.asyncio
    async def test_init_basic_auth_success(self) -> None:
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value=_wrap_config(_auth_config_basic()))

        from app.sources.client.jira.jira import JiraClient, JiraRESTClientViaUsernamePassword

        inner = JiraRESTClientViaUsernamePassword(
            "https://jira.company.com", "svc", "pass", "Basic"
        )
        jc = JiraClient(inner)
        with patch.object(JiraClient, "build_from_services", new_callable=AsyncMock, return_value=jc):
            ok = await conn.init()

        assert ok is True
        assert conn.external_client is jc

    @pytest.mark.asyncio
    async def test_init_rejects_unsupported_auth(self) -> None:
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(
            return_value=_wrap_config(
                {"authType": "OAUTH", "baseUrl": "https://jira.company.com"}
            )
        )
        ok = await conn.init()
        assert ok is False
        assert conn.external_client is None

    @pytest.mark.asyncio
    async def test_init_missing_base_url(self) -> None:
        conn = _make_connector()
        bad = {"authType": "API_TOKEN", "baseUrl": "", "apiToken": "x"}
        conn.config_service.get_config = AsyncMock(return_value=_wrap_config(bad))
        ok = await conn.init()
        assert ok is False

    @pytest.mark.asyncio
    async def test_init_build_from_services_failure(self) -> None:
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value=_wrap_config(_auth_config_api_token_pat()))

        from app.sources.client.jira.jira import JiraClient

        with patch.object(
            JiraClient,
            "build_from_services",
            new_callable=AsyncMock,
            side_effect=RuntimeError("etcd"),
        ):
            ok = await conn.init()
        assert ok is False


# -----------------------------------------------------------------------------
# datasource
# -----------------------------------------------------------------------------


class TestJiraDataCenterFreshDatasource:
    @pytest.mark.asyncio
    async def test_raises_when_not_initialized(self) -> None:
        conn = _make_connector()
        conn.external_client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            await conn._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_returns_jira_data_source(self) -> None:
        conn = _make_connector()
        from app.sources.client.jira.jira import JiraClient, JiraRESTClientViaToken

        conn.external_client = JiraClient(
            JiraRESTClientViaToken("https://jira.company.com", "t", "Bearer")
        )
        ds = await conn._get_fresh_datasource()
        assert ds.base_url == "https://jira.company.com"


# -----------------------------------------------------------------------------
# Filter options / projects
# -----------------------------------------------------------------------------


class TestJiraDataCenterFilterOptions:
    @pytest.mark.asyncio
    async def test_unsupported_filter_key_raises(self) -> None:
        conn = _make_connector()
        with pytest.raises(ValueError, match="Unsupported filter key"):
            await conn.get_filter_options("unknown_key")

    def _projects_response(self):
        resp = MagicMock()
        resp.status = HttpStatusCode.OK.value
        resp.json.return_value = [
            {"key": "DEMO", "name": "Demo"},
            {"key": "ALPHA", "name": "Alpha Project"},
            {"invalid": True},
            {"key": "", "name": "No Key"},
            {"key": "ONLYKEY"},
        ]
        return resp

    @pytest.mark.asyncio
    async def test_project_keys_first_page_no_search(self) -> None:
        conn = _make_connector()
        mock_ds = MagicMock()
        mock_ds.list_projects_get_v2 = AsyncMock(return_value=self._projects_response())

        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            out = await conn.get_filter_options("project_keys", page=1, limit=10)

        assert out.success is True
        ids = [o.id for o in out.options]
        assert ids == ["DEMO", "ALPHA"]
        mock_ds.list_projects_get_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_project_keys_search_filters(self) -> None:
        conn = _make_connector()
        mock_ds = MagicMock()
        mock_ds.list_projects_get_v2 = AsyncMock(return_value=self._projects_response())

        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            out = await conn.get_filter_options("project_keys", page=1, limit=10, search="alpha")

        assert len(out.options) == 1
        assert out.options[0].id == "ALPHA"


# -----------------------------------------------------------------------------
# Connection test + cleanup
# -----------------------------------------------------------------------------


class TestJiraDataCenterConnectionAndCleanup:
    @pytest.mark.asyncio
    async def test_connection_and_access_success(self) -> None:
        conn = _make_connector()
        cfg = _wrap_config(_auth_config_api_token_pat())
        conn.config_service.get_config = AsyncMock(return_value=cfg)

        from app.sources.client.jira.jira import JiraClient, JiraRESTClientViaToken

        jc = JiraClient(JiraRESTClientViaToken("https://jira.company.com", "tok", "Bearer"))

        resp = MagicMock()
        resp.status = HttpStatusCode.OK.value

        mock_ds = MagicMock()
        mock_ds.get_current_user_v2 = AsyncMock(return_value=resp)

        with patch.object(JiraClient, "build_from_services", new_callable=AsyncMock, return_value=jc):
            with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
                ok = await conn.test_connection_and_access()
        assert ok is True

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        conn = _make_connector()
        inner = MagicMock()
        inner.close = AsyncMock()
        mock_jira_client_wrapper = MagicMock()
        mock_jira_client_wrapper.get_client.return_value = inner
        conn.external_client = mock_jira_client_wrapper

        await conn.cleanup()

        inner.close.assert_awaited_once()
        assert conn.external_client is None
        assert conn.data_source is None


# -----------------------------------------------------------------------------
# create_connector factory method
# -----------------------------------------------------------------------------


class TestJiraDataCenterCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector_builds_instance(self) -> None:
        logger = _make_logger()
        dsp = MagicMock()
        cs = MagicMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.DataSourceEntitiesProcessor"
        ) as DepCls:
            dep_instance = MagicMock()
            dep_instance.initialize = AsyncMock()
            DepCls.return_value = dep_instance

            conn = await JiraDataCenterConnector.create_connector(
                logger, dsp, cs, "new-dc-id", "team", "u1"
            )

        DepCls.assert_called_once()
        assert isinstance(conn, JiraDataCenterConnector)
        assert conn.connector_id == "new-dc-id"
        dep_instance.initialize.assert_awaited_once()


# -----------------------------------------------------------------------------
# run_sync (orchestration smoke)
# -----------------------------------------------------------------------------


class TestJiraDataCenterRunSyncSmoke:
    @pytest.mark.asyncio
    async def test_run_sync_exits_when_no_active_users(self) -> None:
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()


# -----------------------------------------------------------------------------
# Module helpers
# -----------------------------------------------------------------------------


class TestJiraDataCenterModuleHelpers:
    def test_normalize_group_row(self) -> None:
        assert _normalize_jira_dc_group_row({}) is None
        # On DC, name is the canonical group identifier; when neither
        # groupId nor id is present, fall back to name as groupId.
        assert _normalize_jira_dc_group_row({"name": "g"}) == {
            "name": "g",
            "groupId": "g",
        }
        assert _normalize_jira_dc_group_row({"name": "g", "groupId": "1"}) == {
            "name": "g",
            "groupId": "1",
        }
        assert _normalize_jira_dc_group_row({"name": "g", "id": "2"}) == {
            "name": "g",
            "groupId": "2",
        }

    def test_extract_media_from_adf_empty(self) -> None:
        assert extract_media_from_adf({}) == []
        # id must be non-empty to be collected
        assert extract_media_from_adf({"type": "media", "attrs": {"id": ""}}) == []

    def test_extract_media_from_adf_with_id(self) -> None:
        nodes = extract_media_from_adf(
            {"content": [{"type": "media", "attrs": {"id": "f1", "alt": "a.png"}}]}
        )
        assert len(nodes) == 1
        assert nodes[0]["id"] == "f1"

    def test_adf_to_text_minimal(self) -> None:
        body = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hi"}]}]}
        assert "Hi" in adf_to_text(body)
