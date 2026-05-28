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
    async def test_run_sync_proceeds_when_pipeshub_directory_empty(self) -> None:
        """Regression: empty PipesHub directory must NOT bail out the sync.

        ``get_all_active_users`` returns the org/directory roster, not Jira
        users. On single-user or fresh deployments the directory may legitimately
        be empty at first sync, and the previous early-return surfaced as a
        silent "no records synced". The fix is to skip that gate entirely and
        let the sync proceed; permission mapping degrades to Jira-side IDs but
        records flow.
        """
        conn = _make_connector()
        conn.data_source = MagicMock()
        # Empty PipesHub directory — the bug used to short-circuit here.
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        # Stub every downstream step that run_sync orchestrates.
        conn._fetch_users = AsyncMock(return_value=[])
        conn._sync_user_groups = AsyncMock(return_value={})
        conn._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        rg = MagicMock()
        rg.short_name = "PROJ"
        rg.external_group_id = "10000"
        conn._fetch_projects = AsyncMock(
            return_value=([(rg, [])], [{"key": "PROJ", "lead": None}])
        )
        conn._sync_project_roles = AsyncMock()
        conn._sync_project_lead_roles = AsyncMock()
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 3, "new_count": 2, "updated_count": 1}
        )
        conn._update_issues_sync_checkpoint = AsyncMock()
        conn._handle_issue_deletions = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()

        # The actual sync path must run even with an empty PipesHub directory.
        conn._fetch_users.assert_awaited_once()
        conn._sync_user_groups.assert_awaited_once()
        conn._fetch_projects.assert_awaited_once()
        conn._sync_all_project_issues.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_raises_when_init_fails(self) -> None:
        """Regression: ``init()`` returning False must raise (not silently sync
        against a None datasource).
        """
        conn = _make_connector()
        conn.data_source = None
        conn.init = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="init failed"):
            await conn.run_sync()
        conn.init.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_hoists_app_roles_mapping_call(self) -> None:
        """Regression: ``_fetch_application_roles_to_groups_mapping`` must be
        called exactly once per ``run_sync`` and passed into ``_fetch_projects``
        rather than being fetched again inside it.
        """
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn._fetch_users = AsyncMock(return_value=[])
        conn._sync_user_groups = AsyncMock(return_value={})
        roles_mapping = {"jira-software": [{"name": "g1", "groupId": "g1"}]}
        conn._fetch_application_roles_to_groups_mapping = AsyncMock(
            return_value=roles_mapping
        )
        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_project_roles = AsyncMock()
        conn._sync_project_lead_roles = AsyncMock()
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=None)
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0}
        )
        conn._update_issues_sync_checkpoint = AsyncMock()
        conn._handle_issue_deletions = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()

        conn._fetch_application_roles_to_groups_mapping.assert_awaited_once()
        # And the mapping must be threaded through to _fetch_projects so we
        # don't re-fetch it inside that call.
        kwargs = conn._fetch_projects.await_args.kwargs
        assert kwargs.get("app_roles_mapping") is roles_mapping

    @pytest.mark.asyncio
    async def test_run_sync_calls_handle_issue_deletions(self) -> None:
        """Regression: DC ``run_sync`` must invoke deletion reconciliation
        after the checkpoint update (was previously missing entirely)."""
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn._fetch_users = AsyncMock(return_value=[])
        conn._sync_user_groups = AsyncMock(return_value={})
        conn._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={})
        conn._fetch_projects = AsyncMock(return_value=([], []))
        conn._sync_project_roles = AsyncMock()
        conn._sync_project_lead_roles = AsyncMock()
        conn._get_issues_sync_checkpoint = AsyncMock(return_value=1_700_000_000_000)
        conn._sync_all_project_issues = AsyncMock(
            return_value={"total_synced": 0, "new_count": 0, "updated_count": 0}
        )
        conn._update_issues_sync_checkpoint = AsyncMock()
        conn._handle_issue_deletions = AsyncMock()

        with patch(
            "app.connectors.sources.atlassian.jira_data_center.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(None, None),
        ):
            await conn.run_sync()

        conn._handle_issue_deletions.assert_awaited_once_with(1_700_000_000_000)


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


# -----------------------------------------------------------------------------
# Deletion handling (DC v2 audit endpoint)
# -----------------------------------------------------------------------------


def _audit_record(
    type_name: str = "ISSUE",
    summary: str = "Issue deleted",
    name: str = "PROJ-1",
) -> dict:
    return {
        "summary": summary,
        "category": "issue tracking",
        "objectItem": {"name": name, "typeName": type_name},
    }


def _audit_response(records: list[dict], total: int | None = None):
    resp = MagicMock()
    resp.status = HttpStatusCode.OK.value
    body = {"records": records}
    if total is not None:
        body["total"] = total
    resp.json.return_value = body
    return resp


class TestJiraDataCenterDeletionAudit:
    @pytest.mark.asyncio
    async def test_handle_issue_deletions_skips_when_no_baseline(self) -> None:
        """First sync (no audit checkpoint, no global checkpoint) is a no-op."""
        conn = _make_connector()
        conn.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn._detect_and_handle_deletions = AsyncMock()

        await conn._handle_issue_deletions(None)

        conn._detect_and_handle_deletions.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_issue_deletions_uses_audit_checkpoint(self) -> None:
        """Audit cursor advances independently of the JQL ``updated`` window."""
        conn = _make_connector()
        conn.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1_700_000_000_000}
        )
        conn.issues_sync_point.update_sync_point = AsyncMock()
        conn._detect_and_handle_deletions = AsyncMock(return_value=2)

        # global_last_sync_time is older — audit checkpoint wins.
        await conn._handle_issue_deletions(1_600_000_000_000)

        conn._detect_and_handle_deletions.assert_awaited_once_with(1_700_000_000_000)
        conn.issues_sync_point.update_sync_point.assert_awaited_once()
        args, kwargs = conn.issues_sync_point.update_sync_point.await_args
        assert args[0] == "issues_audit_deletions"
        assert "last_sync_time" in args[1]

    @pytest.mark.asyncio
    async def test_handle_issue_deletions_falls_back_to_global_checkpoint(self) -> None:
        conn = _make_connector()
        conn.issues_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.issues_sync_point.update_sync_point = AsyncMock()
        conn._detect_and_handle_deletions = AsyncMock(return_value=0)

        await conn._handle_issue_deletions(1_600_000_000_000)

        conn._detect_and_handle_deletions.assert_awaited_once_with(1_600_000_000_000)

    @pytest.mark.asyncio
    async def test_handle_issue_deletions_swallows_detect_errors(self) -> None:
        """Audit pass is a reconciliation pass — failures must NOT raise into
        the outer ``run_sync`` try/except."""
        conn = _make_connector()
        conn.issues_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": 1_700_000_000_000}
        )
        conn.issues_sync_point.update_sync_point = AsyncMock()
        conn._detect_and_handle_deletions = AsyncMock(side_effect=RuntimeError("boom"))

        # No exception should propagate.
        await conn._handle_issue_deletions(None)

        # And the checkpoint must NOT be advanced if the pass failed.
        conn.issues_sync_point.update_sync_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_from_audit_filters_non_issue_rows(self) -> None:
        """Only ``objectItem.typeName == "ISSUE"`` rows with a delete-shaped
        summary are returned; everything else (USER, ISSUE without "delete"
        summary, etc.) is ignored."""
        conn = _make_connector()
        conn.data_source = MagicMock()

        records = [
            _audit_record(name="PROJ-1"),  # match
            _audit_record(type_name="USER", name="alice"),  # wrong type
            _audit_record(summary="Issue updated", name="PROJ-2"),  # wrong summary
            _audit_record(summary="ISSUE DELETED", name="PROJ-3"),  # case-insensitive match
            {"summary": "Issue deleted", "objectItem": {}},  # no name
        ]
        mock_ds = MagicMock()
        mock_ds.get_audit_records_v2 = AsyncMock(
            return_value=_audit_response(records, total=len(records))
        )

        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            keys = await conn._fetch_deleted_issues_from_audit("from", "to")

        assert keys == ["PROJ-1", "PROJ-3"]

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_from_audit_handles_forbidden(self) -> None:
        """Non-admin (403) callers must NOT raise and must NOT loop — audit is
        admin-gated on DC."""
        conn = _make_connector()
        conn.data_source = MagicMock()
        resp = MagicMock()
        resp.status = HttpStatusCode.FORBIDDEN.value
        resp.text.return_value = "forbidden"
        mock_ds = MagicMock()
        mock_ds.get_audit_records_v2 = AsyncMock(return_value=resp)

        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            keys = await conn._fetch_deleted_issues_from_audit("from", "to")

        assert keys == []
        mock_ds.get_audit_records_v2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_deleted_issues_paginates_until_total_reached(self) -> None:
        """Pagination must stop when ``offset + len(records) >= total`` — guard
        against infinite loops when the server reports a non-decreasing total.
        """
        conn = _make_connector()
        conn.data_source = MagicMock()

        page1 = _audit_response(
            [_audit_record(name=f"PROJ-{i}") for i in range(500)], total=750
        )
        page2 = _audit_response(
            [_audit_record(name=f"PROJ-{i}") for i in range(500, 750)], total=750
        )

        mock_ds = MagicMock()
        mock_ds.get_audit_records_v2 = AsyncMock(side_effect=[page1, page2])

        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            keys = await conn._fetch_deleted_issues_from_audit("from", "to")

        assert len(keys) == 750
        assert mock_ds.get_audit_records_v2.await_count == 2

    @pytest.mark.asyncio
    async def test_handle_deleted_issue_skips_when_still_in_jira(self) -> None:
        """Audit hit + 200 OK from ``get_issue_v2`` means the issue was moved or
        renamed, not deleted. Don't cascade-delete from our side."""
        conn = _make_connector()
        conn.data_source = MagicMock()
        resp = MagicMock()
        resp.status = HttpStatusCode.OK.value
        mock_ds = MagicMock()
        mock_ds.get_issue_v2 = AsyncMock(return_value=resp)

        tx_store = MagicMock()
        tx_store.get_record_by_issue_key = AsyncMock()
        tx_store.delete_records_and_relations = AsyncMock()
        conn.data_store_provider = MagicMock()
        conn.data_store_provider.transaction = MagicMock(
            return_value=_aenter_ctx(tx_store)
        )

        with patch.object(conn, "_get_fresh_datasource", new_callable=AsyncMock, return_value=mock_ds):
            await conn._handle_deleted_issue("PROJ-1")

        # Must not have touched the DB at all.
        tx_store.get_record_by_issue_key.assert_not_called()
        tx_store.delete_records_and_relations.assert_not_called()


class _AsyncContext:
    def __init__(self, target) -> None:
        self._target = target

    async def __aenter__(self):
        return self._target

    async def __aexit__(self, *a, **kw) -> None:
        return None


def _aenter_ctx(target):
    return _AsyncContext(target)


# -----------------------------------------------------------------------------
# _fetch_users() — bulk-forbidden fallback (mirrors _app_roles_forbidden)
# -----------------------------------------------------------------------------


def _user_search_response(status: int, payload=None):
    resp = MagicMock()
    resp.status = status
    resp.json = MagicMock(return_value=payload if payload is not None else [])
    resp.text = MagicMock(return_value="")
    return resp


class TestJiraDataCenterUserBulkForbidden:
    """Regression: ``GET /rest/api/2/user/search`` requires the *Browse users
    and groups* global permission. Non-admin sync users get 401/403 there, and
    before the fallback this raised through ``_fetch_users`` and killed the
    whole ``run_sync``. The fallback now (a) returns the partial users
    collected so far, (b) sets ``_user_bulk_forbidden``, and (c) lets Phase 5
    sweep the PipesHub directory via per-email ``user/search?username=<email>``.
    """

    @pytest.mark.asyncio
    async def test_bulk_403_returns_partial_and_sets_flag(self) -> None:
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.get_user_search_v2 = AsyncMock(
            return_value=_user_search_response(HttpStatusCode.FORBIDDEN.value)
        )
        conn._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await conn._fetch_users()

        assert result == []
        assert conn._user_bulk_forbidden is True
        # No PipesHub candidates → Phase 5 short-circuits (no reverse lookup).
        # ``get_user_search_v2`` was only called once (the bulk attempt).
        assert mock_ds.get_user_search_v2.await_count == 1

    @pytest.mark.asyncio
    async def test_bulk_401_treated_as_forbidden(self) -> None:
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.get_user_search_v2 = AsyncMock(
            return_value=_user_search_response(HttpStatusCode.UNAUTHORIZED.value)
        )
        conn._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await conn._fetch_users()

        assert result == []
        assert conn._user_bulk_forbidden is True

    @pytest.mark.asyncio
    async def test_bulk_500_still_raises(self) -> None:
        """5xx is real infra failure — must continue to propagate so the
        sync surfaces it (retry / alerting) rather than silently degrading.
        """
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
        conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.get_user_search_v2 = AsyncMock(
            return_value=_user_search_response(HttpStatusCode.INTERNAL_SERVER_ERROR.value)
        )
        conn._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(Exception, match="Failed to fetch users"):
            await conn._fetch_users()
        assert conn._user_bulk_forbidden is False

    @pytest.mark.asyncio
    async def test_bulk_forbidden_triggers_directory_sweep(self) -> None:
        """With bulk forbidden, Phase 5 must still sweep every PipesHub
        directory candidate via per-email lookup, not be skipped by the
        ``unresolved_count == 0`` early exit.
        """
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        conn.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[
                MagicMock(email="alice@example.com"),
                MagicMock(email="bob@example.com"),
            ]
        )

        forbidden_resp = _user_search_response(HttpStatusCode.FORBIDDEN.value)
        alice_resp = _user_search_response(
            HttpStatusCode.OK.value,
            [{"key": "alice-key", "name": "alice", "displayName": "Alice"}],
        )
        bob_resp = _user_search_response(
            HttpStatusCode.OK.value,
            [{"key": "bob-key", "name": "bob", "displayName": "Bob"}],
        )

        # First call = bulk (`username="."`) -> 403.
        # Subsequent calls = per-email reverse lookup -> 200 with one user.
        per_email_responses = {"alice@example.com": alice_resp, "bob@example.com": bob_resp}

        async def get_user_search_v2(**kwargs):
            username = kwargs.get("username")
            if username == ".":
                return forbidden_resp
            return per_email_responses.get(username, _user_search_response(404))

        mock_ds = MagicMock()
        mock_ds.get_user_search_v2 = AsyncMock(side_effect=get_user_search_v2)
        conn._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await conn._fetch_users()

        assert conn._user_bulk_forbidden is True
        emails = {u.email for u in result}
        assert emails == {"alice@example.com", "bob@example.com"}
        # 1 bulk + 2 per-email = 3 total calls; both candidates must be swept
        # rather than the early-exit firing on unresolved_count==0.
        assert mock_ds.get_user_search_v2.await_count == 3

    @pytest.mark.asyncio
    async def test_bulk_forbidden_with_empty_directory_resolves_nothing(self) -> None:
        """Both bulk forbidden AND empty directory → return cleanly, no
        per-email lookups, no exception.
        """
        conn = _make_connector()
        conn.data_source = MagicMock()
        conn.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])
        conn.data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

        mock_ds = MagicMock()
        mock_ds.get_user_search_v2 = AsyncMock(
            return_value=_user_search_response(HttpStatusCode.FORBIDDEN.value)
        )
        conn._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await conn._fetch_users()
        assert result == []
        assert conn._user_bulk_forbidden is True
        # Only the bulk attempt; no per-email follow-ups.
        assert mock_ds.get_user_search_v2.await_count == 1
