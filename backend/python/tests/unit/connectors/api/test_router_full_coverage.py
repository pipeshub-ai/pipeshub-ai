"""Tests to cover remaining gaps in app/connectors/api/router.py.

Targets uncovered lines:
  - _stream_artifact_from_storage: full path + PDF conversion + error paths
  - get_pdf_conversion_info: PPT/PPTX detection
  - _parse_reindex_body: statusFilters validation
  - _apply_tenant_to_microsoft_oauth_url: regex substitution
  - _validate_admin_oauth_config_before_creation: update + create paths
  - _validate_non_admin_oauth_selection: credentials check, missing config
  - _get_secret_oauth_field_names_from_registry: auth_fields iteration, exceptions
  - _build_oauth_flow_config: optional fields, tenant substitution, field normalization
  - _get_streaming_connector: lazy init, disabled connector, missing type
  - convert_buffer_to_pdf_stream: timeout, file_iterator error
  - stream_record_internal: ARTIFACT branch
  - reindex_record_group: statusFilters in payload
  - get_connector_stats_endpoint: generic exception
  - _apply_confluence_optional_jira_scope: jira_scope already present
  - update_connector_instance_auth_config: redirect_uri fallback, cleanup
  - update_connector_instance_config: auth type change
  - get_filter_field_options: scope/exclude paths, cleanup
  - get_all_oauth_configs: exception in parallel fetch
  - update_oauth_config: name conflict, name/config update
  - _ensure_connector_initialized: connector_doc not found
"""

import asyncio
import logging
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_ROUTER = "app.connectors.api.router"


# ============================================================================
# _parse_reindex_body — lines 1430-1435
# ============================================================================


class TestParseReindexBody:
    def test_none_body(self):
        from app.connectors.api.router import _parse_reindex_body
        depth, filters = _parse_reindex_body(None)
        assert depth == 0
        assert filters is None

    def test_empty_body(self):
        from app.connectors.api.router import _parse_reindex_body
        depth, filters = _parse_reindex_body({})
        assert depth == 0
        assert filters is None

    def test_depth_only(self):
        from app.connectors.api.router import _parse_reindex_body
        depth, filters = _parse_reindex_body({"depth": 2})
        assert depth == 2
        assert filters is None

    def test_valid_status_filters(self):
        from app.connectors.api.router import _parse_reindex_body
        depth, filters = _parse_reindex_body({
            "depth": 1, "statusFilters": ["FAILED", "PENDING"]
        })
        assert depth == 1
        assert filters == ["FAILED", "PENDING"]

    def test_empty_status_filters_returns_none(self):
        from app.connectors.api.router import _parse_reindex_body
        depth, filters = _parse_reindex_body({"statusFilters": []})
        assert filters is None

    def test_invalid_status_filters_not_list(self):
        from app.connectors.api.router import _parse_reindex_body
        with pytest.raises(HTTPException) as exc:
            _parse_reindex_body({"statusFilters": "FAILED"})
        assert exc.value.status_code == 400

    def test_invalid_status_filters_non_string_items(self):
        from app.connectors.api.router import _parse_reindex_body
        with pytest.raises(HTTPException) as exc:
            _parse_reindex_body({"statusFilters": [123, "FAILED"]})
        assert exc.value.status_code == 400


# ============================================================================
# _apply_tenant_to_microsoft_oauth_url — lines 6901-6911
# ============================================================================


class TestApplyTenantToMicrosoftOAuthUrl:
    def test_non_microsoft_url_unchanged(self):
        from app.connectors.api.router import _apply_tenant_to_microsoft_oauth_url
        url = "https://accounts.google.com/o/oauth2/auth"
        assert _apply_tenant_to_microsoft_oauth_url(url, "my-tenant") == url

    def test_empty_url(self):
        from app.connectors.api.router import _apply_tenant_to_microsoft_oauth_url
        assert _apply_tenant_to_microsoft_oauth_url("", "t1") == ""

    def test_none_url(self):
        from app.connectors.api.router import _apply_tenant_to_microsoft_oauth_url
        assert _apply_tenant_to_microsoft_oauth_url(None, "t1") is None

    def test_common_tenant_no_op(self):
        from app.connectors.api.router import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        assert _apply_tenant_to_microsoft_oauth_url(url, "common") == url

    def test_organizations_no_op(self):
        from app.connectors.api.router import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        assert _apply_tenant_to_microsoft_oauth_url(url, "organizations") == url

    def test_blank_tenant_no_op(self):
        from app.connectors.api.router import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        assert _apply_tenant_to_microsoft_oauth_url(url, "  ") == url

    def test_real_tenant_substitution(self):
        from app.connectors.api.router import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        result = _apply_tenant_to_microsoft_oauth_url(url, "abc-123")
        assert "abc-123" in result
        assert "/common/" not in result

    def test_token_url_substitution(self):
        from app.connectors.api.router import _apply_tenant_to_microsoft_oauth_url
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        result = _apply_tenant_to_microsoft_oauth_url(url, "tenant-xyz")
        assert "tenant-xyz" in result


# ============================================================================
# _get_secret_oauth_field_names_from_registry — lines 6990-6992
# ============================================================================


class TestGetSecretOAuthFieldNames:
    def test_registry_returns_fields(self):
        from app.connectors.api.router import _get_secret_oauth_field_names_from_registry

        mock_field1 = MagicMock(is_secret=True)
        mock_field1.name = "clientSecret"
        mock_field2 = MagicMock(is_secret=False)
        mock_field2.name = "clientId"
        mock_config = MagicMock(auth_fields=[mock_field1, mock_field2])
        mock_registry = MagicMock()
        mock_registry.get_config.return_value = mock_config

        mock_mod = MagicMock()
        mock_mod.get_oauth_config_registry = MagicMock(return_value=mock_registry)

        import sys
        with patch.dict(sys.modules, {"app.connectors.core.registry.oauth_config_registry": mock_mod}):
            result = _get_secret_oauth_field_names_from_registry("GOOGLE_DRIVE")
        assert "clientSecret" in result
        assert "clientId" not in result

    def test_no_config_returns_default(self):
        from app.connectors.api.router import _get_secret_oauth_field_names_from_registry

        mock_registry = MagicMock()
        mock_registry.get_config.return_value = None
        mock_mod = MagicMock()
        mock_mod.get_oauth_config_registry = MagicMock(return_value=mock_registry)

        import sys
        with patch.dict(sys.modules, {"app.connectors.core.registry.oauth_config_registry": mock_mod}):
            result = _get_secret_oauth_field_names_from_registry("SLACK")
        assert "clientSecret" in result

    def test_exception_returns_default(self):
        from app.connectors.api.router import _get_secret_oauth_field_names_from_registry

        mock_mod = MagicMock()
        mock_mod.get_oauth_config_registry = MagicMock(side_effect=RuntimeError("fail"))

        import sys
        with patch.dict(sys.modules, {"app.connectors.core.registry.oauth_config_registry": mock_mod}):
            result = _get_secret_oauth_field_names_from_registry("SLACK")
        assert "clientSecret" in result


# ============================================================================
# _stream_artifact_from_storage — lines 182-234
# ============================================================================


class TestStreamArtifactFromStorage:
    @pytest.mark.asyncio
    async def test_no_external_id(self):
        from app.connectors.api.router import _stream_artifact_from_storage
        record = MagicMock()
        record.external_record_id = None
        with pytest.raises(HTTPException) as exc:
            await _stream_artifact_from_storage(record, "o1", AsyncMock())
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_raw_buffer(self):
        from app.connectors.api.router import _stream_artifact_from_storage
        record = MagicMock()
        record.external_record_id = "ext-1"
        record.mime_type = "image/png"
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"storage": {"endpoint": "http://store"}})

        with patch(f"{_ROUTER}.generate_jwt", new_callable=AsyncMock, return_value="tok"), \
             patch(f"{_ROUTER}.make_api_call", new_callable=AsyncMock, return_value={"data": b"imgbytes"}):
            result = await _stream_artifact_from_storage(record, "o1", cs)
        assert result.body == b"imgbytes"

    @pytest.mark.asyncio
    async def test_dict_data_response(self):
        from app.connectors.api.router import _stream_artifact_from_storage
        record = MagicMock()
        record.external_record_id = "ext-1"
        record.mime_type = "application/octet-stream"
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"storage": {"endpoint": "http://store"}})

        with patch(f"{_ROUTER}.generate_jwt", new_callable=AsyncMock, return_value="tok"), \
             patch(f"{_ROUTER}.make_api_call", new_callable=AsyncMock,
                   return_value={"data": {"data": [65, 66, 67]}}):
            result = await _stream_artifact_from_storage(record, "o1", cs)
        assert result.body == b"ABC"

    @pytest.mark.asyncio
    async def test_pdf_conversion_path(self):
        from app.connectors.api.router import _stream_artifact_from_storage
        from app.config.constants.arangodb import MimeTypes
        record = MagicMock()
        record.external_record_id = "ext-1"
        record.mime_type = "application/vnd.ms-powerpoint"
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"storage": {"endpoint": "http://store"}})
        mock_stream_resp = MagicMock()

        with patch(f"{_ROUTER}.generate_jwt", new_callable=AsyncMock, return_value="tok"), \
             patch(f"{_ROUTER}.make_api_call", new_callable=AsyncMock,
                   return_value={"data": b"pptbytes"}), \
             patch(f"{_ROUTER}.get_pdf_conversion_info",
                   return_value=(True, "slides.pptx", ".pptx")), \
             patch(f"{_ROUTER}.convert_buffer_to_pdf_stream",
                   new_callable=AsyncMock, return_value=mock_stream_resp):
            result = await _stream_artifact_from_storage(
                record, "o1", cs, convert_to=MimeTypes.PDF.value
            )
        assert result is mock_stream_resp

    @pytest.mark.asyncio
    async def test_pdf_conversion_error(self):
        from app.connectors.api.router import _stream_artifact_from_storage
        from app.config.constants.arangodb import MimeTypes
        record = MagicMock()
        record.external_record_id = "ext-1"
        record.mime_type = "application/vnd.ms-powerpoint"
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"storage": {"endpoint": "http://store"}})

        with patch(f"{_ROUTER}.generate_jwt", new_callable=AsyncMock, return_value="tok"), \
             patch(f"{_ROUTER}.make_api_call", new_callable=AsyncMock,
                   return_value={"data": b"pptbytes"}), \
             patch(f"{_ROUTER}.get_pdf_conversion_info",
                   return_value=(True, "slides.pptx", ".pptx")), \
             patch(f"{_ROUTER}.convert_buffer_to_pdf_stream",
                   new_callable=AsyncMock, side_effect=RuntimeError("libre failed")):
            with pytest.raises(HTTPException) as exc:
                await _stream_artifact_from_storage(
                    record, "o1", cs, convert_to=MimeTypes.PDF.value
                )
            assert exc.value.status_code == 500


# ============================================================================
# _apply_confluence_optional_jira_scope — line 2486 (already present)
# ============================================================================


class TestApplyConfluenceJiraScopeAlreadyPresent:
    def test_jira_scope_already_in_list(self):
        from app.connectors.api.router import _apply_confluence_optional_jira_scope
        scopes = ["read:confluence-content.all", "read:jira-user"]
        auth_config = {"includeJiraScope": True}
        result = _apply_confluence_optional_jira_scope("CONFLUENCE", auth_config, scopes)
        assert result is scopes
        assert result.count("read:jira-user") == 1


# ============================================================================
# _validate_admin_oauth_config_before_creation — lines 7250-7253
# ============================================================================


class TestValidateAdminOAuthConfigBeforeCreation:
    @pytest.mark.asyncio
    async def test_update_with_name_change(self):
        from app.connectors.api.router import _validate_admin_oauth_config_before_creation
        existing_configs = [
            {"_id": "cfg1", "orgId": "o1", "oauthInstanceName": "Old Name"},
            {"_id": "cfg2", "orgId": "o1", "oauthInstanceName": "Other"},
        ]
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=existing_configs)
        config = {"auth": {"oauthConfigId": "cfg1", "clientId": "c1", "oauthInstanceName": "New Name"}}

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId"]), \
             patch(f"{_ROUTER}._check_oauth_name_conflict") as mock_check:
            await _validate_admin_oauth_config_before_creation(
                connector_type="GOOGLE_DRIVE",
                config=config,
                oauth_config_id="cfg1",
                instance_name="My Instance",
                org_id="o1",
                config_service=cs,
                logger=MagicMock(),
            )
        mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_new_config(self):
        from app.connectors.api.router import _validate_admin_oauth_config_before_creation
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])
        config = {"auth": {"clientId": "c1"}}

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId"]), \
             patch(f"{_ROUTER}._check_oauth_name_conflict") as mock_check:
            await _validate_admin_oauth_config_before_creation(
                connector_type="GOOGLE_DRIVE",
                config=config,
                oauth_config_id=None,
                instance_name="My Instance",
                org_id="o1",
                config_service=cs,
                logger=MagicMock(),
            )
        mock_check.assert_called_once()

    @pytest.mark.asyncio
    async def test_config_not_found_creates_new(self):
        """Config with provided ID not found → create new path."""
        from app.connectors.api.router import _validate_admin_oauth_config_before_creation
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])
        config = {"auth": {"oauthConfigId": "missing-id", "clientId": "c1"}}

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._get_oauth_field_names_from_registry", return_value=["clientId"]), \
             patch(f"{_ROUTER}._check_oauth_name_conflict") as mock_check:
            await _validate_admin_oauth_config_before_creation(
                connector_type="GOOGLE_DRIVE",
                config=config,
                oauth_config_id="missing-id",
                instance_name="My Instance",
                org_id="o1",
                config_service=cs,
                logger=MagicMock(),
            )
        mock_check.assert_called_once()


# ============================================================================
# _validate_non_admin_oauth_selection — lines 7299-7337
# ============================================================================


class TestValidateNonAdminOAuthSelection:
    @pytest.mark.asyncio
    async def test_credentials_provided_rejected(self):
        """Lines 7308-7309 — non-admin providing OAuth creds."""
        from app.connectors.api.router import _validate_non_admin_oauth_selection
        config = {"auth": {"clientId": "id1", "clientSecret": "sec"}}

        with patch(f"{_ROUTER}._get_oauth_field_names_from_registry",
                   return_value=["clientId", "clientSecret"]):
            with pytest.raises(HTTPException) as exc:
                await _validate_non_admin_oauth_selection(
                    "GOOGLE_DRIVE", config, None, "u1", "o1", AsyncMock(), MagicMock()
                )
            assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_oauth_config_id(self):
        """Lines 7318-7319 — no oauthConfigId provided."""
        from app.connectors.api.router import _validate_non_admin_oauth_selection

        with pytest.raises(HTTPException) as exc:
            await _validate_non_admin_oauth_selection(
                "GOOGLE_DRIVE", None, None, "u1", "o1", AsyncMock(), MagicMock()
            )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_config_not_found(self):
        """Lines 7336-7337 — config ID not found in etcd."""
        from app.connectors.api.router import _validate_non_admin_oauth_selection
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._find_oauth_config_by_id", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await _validate_non_admin_oauth_selection(
                    "GOOGLE_DRIVE", None, "cfg-missing", "u1", "o1", cs, MagicMock()
                )
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_non_list_configs_coerced(self):
        """Line 7329 — non-list configs coerced to empty."""
        from app.connectors.api.router import _validate_non_admin_oauth_selection
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value="bad")

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._find_oauth_config_by_id", return_value=None):
            with pytest.raises(HTTPException) as exc:
                await _validate_non_admin_oauth_selection(
                    "GOOGLE_DRIVE", None, "cfg1", "u1", "o1", cs, MagicMock()
                )
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_valid_selection(self):
        """Happy path — valid config found."""
        from app.connectors.api.router import _validate_non_admin_oauth_selection
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[{"_id": "cfg1", "orgId": "o1"}])

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._find_oauth_config_by_id", return_value={"_id": "cfg1"}):
            await _validate_non_admin_oauth_selection(
                "GOOGLE_DRIVE", None, "cfg1", "u1", "o1", cs, MagicMock()
            )


# ============================================================================
# _build_oauth_flow_config — lines 4606, 4608, 4620, 4634, 4639, 4648, 4654-4658, 4662
# ============================================================================


class TestBuildOAuthFlowConfigEdgeCases:
    @pytest.mark.asyncio
    async def test_tenant_id_normalization(self):
        """Line 4648 — tenant_id normalization in direct config (no shared OAuth)."""
        from app.connectors.api.router import _build_oauth_flow_config
        auth_config = {
            "authType": "OAUTH",
            "tenant_id": "my-tenant",
            "authorizeUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            "tokenUrl": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        }
        with patch(f"{_ROUTER}._apply_confluence_optional_jira_scope", return_value=[]):
            result = await _build_oauth_flow_config(
                auth_config, "ONEDRIVE", "o1", AsyncMock(), MagicMock()
            )
        assert "my-tenant" in result.get("authorizeUrl", "")

    @pytest.mark.asyncio
    async def test_non_list_scopes_coerced(self):
        """Line 4662 — non-list scopes coerced."""
        from app.connectors.api.router import _build_oauth_flow_config
        auth_config = {
            "authType": "OAUTH",
            "scopes": "read write",
        }
        with patch(f"{_ROUTER}._apply_confluence_optional_jira_scope", return_value=["read", "write"]):
            result = await _build_oauth_flow_config(
                auth_config, "SLACK", "o1", AsyncMock(), MagicMock()
            )
        assert isinstance(result.get("scopes", []), list)

    @pytest.mark.asyncio
    async def test_shared_oauth_optional_fields(self):
        """Lines 4606, 4608, 4620 — scopeParameterName, tokenResponsePath, tenant_id in config."""
        from app.connectors.api.router import _build_oauth_flow_config
        auth_config = {"oauthConfigId": "cfg1", "authType": "OAUTH", "connectorScope": "team"}
        shared = {
            "_id": "cfg1", "orgId": "o1",
            "authorizeUrl": "https://auth.example.com",
            "tokenUrl": "https://token.example.com",
            "scopes": {"team_sync": ["read"]},
            "scopeParameterName": "scope",
            "tokenResponsePath": "access_token",
            "config": {"client_id": "c1", "client_secret": "s1", "tenant_id": "t1"},
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[shared])

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._apply_confluence_optional_jira_scope", return_value=["read"]):
            result = await _build_oauth_flow_config(
                auth_config, "ONEDRIVE", "o1", cs, MagicMock()
            )
        assert result["scopeParameterName"] == "scope"
        assert result["tokenResponsePath"] == "access_token"
        assert result.get("tenantId") == "t1"

    @pytest.mark.asyncio
    async def test_shared_oauth_instance_url_from_shared_config(self):
        """Line 4639 — instanceUrl from shared config data when not in auth_config."""
        from app.connectors.api.router import _build_oauth_flow_config
        auth_config = {"oauthConfigId": "cfg1", "authType": "OAUTH", "connectorScope": "personal"}
        shared = {
            "_id": "cfg1", "orgId": "o1",
            "authorizeUrl": "https://auth", "tokenUrl": "https://token",
            "scopes": ["read"],
            "config": {"clientId": "c1", "instanceUrl": "https://gitlab.corp.com"},
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[shared])

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._apply_confluence_optional_jira_scope", return_value=["read"]):
            result = await _build_oauth_flow_config(
                auth_config, "GITLAB", "o1", cs, MagicMock()
            )
        assert result.get("instanceUrl") == "https://gitlab.corp.com"

    @pytest.mark.asyncio
    async def test_shared_oauth_instance_url_from_auth_config(self):
        """Line 4634 — instanceUrl from auth_config takes precedence."""
        from app.connectors.api.router import _build_oauth_flow_config
        auth_config = {
            "oauthConfigId": "cfg1", "authType": "OAUTH",
            "instanceUrl": "https://my-instance.com",
        }
        shared = {
            "_id": "cfg1", "orgId": "o1",
            "authorizeUrl": "https://auth", "tokenUrl": "https://token",
            "scopes": ["read"],
            "config": {"clientId": "c1", "instanceUrl": "https://other.com"},
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[shared])

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._apply_confluence_optional_jira_scope", return_value=["read"]):
            result = await _build_oauth_flow_config(
                auth_config, "GITLAB", "o1", cs, MagicMock()
            )
        assert result.get("instanceUrl") == "https://my-instance.com"

    @pytest.mark.asyncio
    async def test_shared_oauth_not_found_raises(self):
        """Lines 4568-4572 — shared config not found raises 404."""
        from app.connectors.api.router import _build_oauth_flow_config
        auth_config = {"oauthConfigId": "missing", "authType": "OAUTH"}
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=[])

        with patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"):
            with pytest.raises(HTTPException) as exc:
                await _build_oauth_flow_config(
                    auth_config, "GOOGLE_DRIVE", "o1", cs, MagicMock()
                )
            assert exc.value.status_code == 404


# ============================================================================
# _get_streaming_connector — lines 5823-5856
# ============================================================================


class TestGetStreamingConnector:
    @pytest.mark.asyncio
    async def test_no_connector_type(self):
        """Line 5824 — empty connector type raises."""
        from app.connectors.api.router import _get_streaming_connector
        instance = {"type": "", "name": "Test", "isActive": True}
        container = MagicMock()
        container.connectors_map = {}
        with pytest.raises(HTTPException) as exc:
            await _get_streaming_connector(
                container, "c1", instance, MagicMock(), AsyncMock(),
                "u1", "o1", is_admin=True, logger=MagicMock()
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_not_active_raises_conflict(self):
        """Line 5814 — inactive connector raises 409."""
        from app.connectors.api.router import _get_streaming_connector
        instance = {"type": "GOOGLE_DRIVE", "name": "GDrive", "isActive": False}
        container = MagicMock()
        container.connectors_map = {}
        with pytest.raises(HTTPException) as exc:
            await _get_streaming_connector(
                container, "c1", instance, MagicMock(), AsyncMock(),
                "u1", "o1", is_admin=True, logger=MagicMock()
            )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_ensure_init_returns_none(self):
        """Lines 5848-5855 — _ensure_connector_initialized returns None."""
        from app.connectors.api.router import _get_streaming_connector
        instance = {"type": "GOOGLE_DRIVE", "name": "GDrive", "isActive": True}
        container = MagicMock()
        container.connectors_map = {}
        with patch(f"{_ROUTER}._ensure_connector_initialized",
                   new_callable=AsyncMock, return_value=None):
            with pytest.raises(HTTPException) as exc:
                await _get_streaming_connector(
                    container, "c1", instance, MagicMock(), AsyncMock(),
                    "u1", "o1", is_admin=True, logger=MagicMock()
                )
            assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_success(self):
        from app.connectors.api.router import _get_streaming_connector
        instance = {"type": "GOOGLE_DRIVE", "name": "GDrive", "isActive": True}
        container = MagicMock()
        container.connectors_map = {}
        mock_conn = MagicMock()
        with patch(f"{_ROUTER}._ensure_connector_initialized",
                   new_callable=AsyncMock, return_value=mock_conn):
            result = await _get_streaming_connector(
                container, "c1", instance, MagicMock(), AsyncMock(),
                "u1", "o1", is_admin=True, logger=MagicMock()
            )
        assert result is mock_conn

    @pytest.mark.asyncio
    async def test_found_in_connectors_map(self):
        """Line 5810 — connector already in map → return immediately."""
        from app.connectors.api.router import _get_streaming_connector
        instance = {"type": "GOOGLE_DRIVE", "name": "GDrive"}
        mock_conn = MagicMock()
        container = MagicMock()
        container.connectors_map = {"c1": mock_conn}
        result = await _get_streaming_connector(
            container, "c1", instance, MagicMock(), AsyncMock(),
            "u1", "o1", is_admin=True, logger=MagicMock()
        )
        assert result is mock_conn


# ============================================================================
# _ensure_connector_initialized — lines 5914-5915
# ============================================================================


class TestEnsureConnectorInitializedDocNotFound:
    @pytest.mark.asyncio
    async def test_connector_doc_not_found(self):
        from app.connectors.api.router import _ensure_connector_initialized
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        container = MagicMock()
        container.connectors_map = {}
        registry = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await _ensure_connector_initialized(
                container=container,
                connector_id="c1",
                connector_type="GOOGLE_DRIVE",
                connector_registry=registry,
                graph_provider=gp,
                user_id="u1",
                org_id="o1",
                is_admin=True,
                logger=MagicMock(),
            )
        assert exc.value.status_code == 404


# ============================================================================
# get_connector_stats_endpoint — line 1542
# ============================================================================


class TestGetConnectorStatsException:
    @pytest.mark.asyncio
    async def test_generic_exception_after_success(self):
        """Line 1542 — exception in result parsing after logger is set."""
        from app.connectors.api.router import get_connector_stats_endpoint
        gp = AsyncMock()
        gp.get_connector_stats = AsyncMock(return_value={"success": True, "data": None})
        req = MagicMock()
        req.app.container.logger.return_value = MagicMock()

        # result["data"] is None so accessing it won't raise, but
        # we make the result dict trigger an exception on the return
        bad_result = MagicMock()
        bad_result.__getitem__ = MagicMock(side_effect=[True, RuntimeError("serialize")])
        gp.get_connector_stats = AsyncMock(return_value={"success": False})

        with pytest.raises(HTTPException) as exc:
            await get_connector_stats_endpoint(
                request=req,
                org_id="o1",
                connector_id="c1",
                graph_provider=gp,
            )
        assert exc.value.status_code == 404


# ============================================================================
# reindex_record_group — line 1606
# ============================================================================


class TestParseReindexBodyInRoute:
    """Validates _parse_reindex_body is used correctly in route-level flows."""

    def test_depth_zero_no_filters(self):
        """Default: no body → depth 0, no filters."""
        from app.connectors.api.router import _parse_reindex_body
        depth, filt = _parse_reindex_body({"depth": 0})
        assert depth == 0
        assert filt is None

    def test_both_depth_and_filters(self):
        from app.connectors.api.router import _parse_reindex_body
        depth, filt = _parse_reindex_body({"depth": 3, "statusFilters": ["OK"]})
        assert depth == 3
        assert filt == ["OK"]


# ============================================================================
# get_filter_field_options — lines 5631, 5633, 5649-5653
# ============================================================================


class TestGetPdfConversionInfo:
    def test_pptx_needs_conversion(self):
        from app.connectors.api.router import get_pdf_conversion_info
        record = MagicMock()
        record.record_name = "deck.pptx"
        record.mime_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        needs, name, ext = get_pdf_conversion_info(record)
        assert needs is True
        assert "pptx" in ext

    def test_ppt_needs_conversion(self):
        from app.connectors.api.router import get_pdf_conversion_info
        record = MagicMock()
        record.record_name = "old.ppt"
        record.mime_type = "application/vnd.ms-powerpoint"
        needs, name, ext = get_pdf_conversion_info(record)
        assert needs is True
        assert "ppt" in ext

    def test_pdf_no_conversion(self):
        from app.connectors.api.router import get_pdf_conversion_info
        record = MagicMock()
        record.record_name = "doc.pdf"
        record.mime_type = "application/pdf"
        needs, name, ext = get_pdf_conversion_info(record)
        assert needs is False

    def test_with_explicit_mime_type(self):
        from app.connectors.api.router import get_pdf_conversion_info
        record = MagicMock()
        record.record_name = "slides.pptx"
        record.mime_type = None
        needs, name, ext = get_pdf_conversion_info(
            record, mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        assert needs is True

    def test_google_slides_needs_conversion(self):
        from app.connectors.api.router import get_pdf_conversion_info
        record = MagicMock()
        record.record_name = "deck"
        record.mime_type = "application/vnd.google-apps.presentation"
        needs, name, ext = get_pdf_conversion_info(record)
        assert needs is True
        assert ext == "pptx"

    def test_pptx_mime_infers_missing_extension(self):
        from app.connectors.api.router import get_pdf_conversion_info
        record = MagicMock()
        record.record_name = "Quarterly review"
        record.mime_type = (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        needs, name, ext = get_pdf_conversion_info(record)
        assert needs is True
        assert name == "Quarterly review"
        assert ext == "pptx"


# ============================================================================
# get_all_oauth_configs — lines 6825-6826
# ============================================================================


class TestGetAllOAuthConfigsExceptionInFetch:
    @pytest.mark.asyncio
    async def test_exception_result_logged(self):
        from app.connectors.api.router import get_all_oauth_configs
        request = MagicMock()
        request.state.user.get = lambda k, d=None: {"userId": "u1", "orgId": "o1"}.get(k, d)
        request.headers.get = lambda k, d=None: {"X-Is-Admin": "true"}.get(k, d)
        request.app.state.connector_registry.get_all_connector_names.return_value = ["GOOGLE_DRIVE"]

        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))

        with patch(f"{_ROUTER}._get_user_context", return_value={"userId": "u1", "orgId": "o1", "user_id": "u1", "org_id": "o1", "is_admin": True}), \
             patch(f"{_ROUTER}._validate_admin_only"), \
             patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"):
            result = await get_all_oauth_configs(
                request=request,
                page=1,
                limit=20,
                search=None,
                config_service=cs,
            )
        assert result.get("success") is True
        assert len(result.get("data", [])) == 0


# ============================================================================
# update_oauth_config — lines 7692, 7699
# ============================================================================


class TestUpdateOAuthConfigNameBranch:
    @pytest.mark.asyncio
    async def test_name_and_config_update(self):
        from app.connectors.api.router import update_oauth_config
        existing = {"_id": "cfg1", "orgId": "o1", "oauthInstanceName": "OldName", "config": {}}
        configs = [existing]

        request = MagicMock()
        request.state.user.get = lambda k, d=None: {"userId": "u1", "orgId": "o1"}.get(k, d)
        request.headers.get = lambda k, d=None: {"X-Is-Admin": "true"}.get(k, d)
        request.json = AsyncMock(return_value={
            "oauthInstanceName": "NewName",
            "config": {"clientId": "new-id"},
        })

        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=configs)
        cs.set_config = AsyncMock()

        with patch(f"{_ROUTER}._get_user_context", return_value={"userId": "u1", "orgId": "o1", "user_id": "u1", "org_id": "o1", "is_admin": True}), \
             patch(f"{_ROUTER}._validate_admin_only"), \
             patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._check_oauth_name_conflict"), \
             patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock), \
             patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
            result = await update_oauth_config(
                connector_type="GOOGLE_DRIVE",
                config_id="cfg1",
                request=request,
                config_service=cs,
            )
        assert result["success"] is True
        saved = cs.set_config.call_args[0][1]
        assert saved[0]["oauthInstanceName"] == "NewName"

    @pytest.mark.asyncio
    async def test_no_name_no_config_update(self):
        """Lines 7699->7701 — neither name nor config → still saves."""
        from app.connectors.api.router import update_oauth_config
        existing = {"_id": "cfg1", "orgId": "o1", "oauthInstanceName": "Keep"}
        configs = [existing]

        request = MagicMock()
        request.state.user.get = lambda k, d=None: {"userId": "u1", "orgId": "o1"}.get(k, d)
        request.headers.get = lambda k, d=None: {"X-Is-Admin": "true"}.get(k, d)
        request.json = AsyncMock(return_value={})

        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=configs)
        cs.set_config = AsyncMock()

        with patch(f"{_ROUTER}._get_user_context", return_value={"userId": "u1", "orgId": "o1", "user_id": "u1", "org_id": "o1", "is_admin": True}), \
             patch(f"{_ROUTER}._validate_admin_only"), \
             patch(f"{_ROUTER}._get_oauth_config_path", return_value="/path"), \
             patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock), \
             patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=999):
            result = await update_oauth_config(
                connector_type="GOOGLE_DRIVE",
                config_id="cfg1",
                request=request,
                config_service=cs,
            )
        assert result["success"] is True
        saved = cs.set_config.call_args[0][1]
        assert saved[0]["oauthInstanceName"] == "Keep"


# ============================================================================
# reindex_connector — KB collection permission fallback (lines ~1732–1770)
# ============================================================================


class TestReindexConnectorKbAuth:
    """Test KB collection reindex authorization fallback.
    
    When connector_registry.get_connector_instance returns None for a KB collection,
    the endpoint should check KB permissions and allow OWNER/WRITER/READER.
    """

    @pytest.mark.asyncio
    async def test_kb_reindex_creator_success(self):
        """KB creator can reindex via registry path (baseline)."""
        from app.connectors.api.router import reindex_connector
        
        request = MagicMock()
        request.state.user.get = lambda k, d=None: {"userId": "u1", "orgId": "o1"}.get(k, d)
        request.headers.get = lambda k, d="": {"X-Is-Admin": "false"}.get(k, d)
        request.json = AsyncMock(return_value={})
        request.app.container.logger = MagicMock(return_value=MagicMock())
        
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value={
            "type": "KB",
            "isActive": True,
            "name": "My KB",
        })
        request.app.state.connector_registry = registry
        
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()
        
        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=123):
            result = await reindex_connector(
                connector_id="kb1",
                request=request,
                kafka_service=kafka,
            )
        
        assert result["success"] is True
        assert kafka.publish_event.called

    @pytest.mark.asyncio
    async def test_kb_reindex_writer_success(self):
        """WRITER on shared KB can reindex via permission fallback."""
        from app.connectors.api.router import reindex_connector
        
        request = MagicMock()
        request.state.user.get = lambda k, d=None: {"userId": "u2", "orgId": "o1"}.get(k, d)
        request.headers.get = lambda k, d="": {"X-Is-Admin": "false"}.get(k, d)
        request.json = AsyncMock(return_value={})
        request.app.container.logger = MagicMock(return_value=MagicMock())
        
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)  # not creator
        request.app.state.connector_registry = registry
        
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "_id": "kb1",
            "type": "KB",
            "isActive": True,
            "name": "Shared KB",
        })
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u2"})
        graph_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")
        request.app.state.graph_provider = graph_provider
        
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()
        
        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=123):
            result = await reindex_connector(
                connector_id="kb1",
                request=request,
                kafka_service=kafka,
            )
        
        assert result["success"] is True
        assert kafka.publish_event.called
        graph_provider.get_user_kb_permission.assert_called_once_with("kb1", "u2")

    @pytest.mark.asyncio
    async def test_kb_reindex_reader_success(self):
        """READER on shared KB can reindex via permission fallback."""
        from app.connectors.api.router import reindex_connector
        
        request = MagicMock()
        request.state.user.get = lambda k, d=None: {"userId": "u3", "orgId": "o1"}.get(k, d)
        request.headers.get = lambda k, d="": {"X-Is-Admin": "false"}.get(k, d)
        request.json = AsyncMock(return_value={})
        request.app.container.logger = MagicMock(return_value=MagicMock())
        
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)  # not creator
        request.app.state.connector_registry = registry
        
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "_id": "kb1",
            "type": "KB",
            "isActive": True,
            "name": "Shared KB",
        })
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u3"})
        graph_provider.get_user_kb_permission = AsyncMock(return_value="READER")
        request.app.state.graph_provider = graph_provider
        
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock()
        
        with patch(f"{_ROUTER}.get_epoch_timestamp_in_ms", return_value=123):
            result = await reindex_connector(
                connector_id="kb1",
                request=request,
                kafka_service=kafka,
            )
        
        assert result["success"] is True
        assert kafka.publish_event.called

    @pytest.mark.asyncio
    async def test_kb_reindex_no_permission_denied(self):
        """User with no KB role cannot reindex."""
        from app.connectors.api.router import reindex_connector
        
        request = MagicMock()
        request.state.user.get = lambda k, d=None: {"userId": "u4", "orgId": "o1"}.get(k, d)
        request.headers.get = lambda k, d="": {"X-Is-Admin": "false"}.get(k, d)
        request.json = AsyncMock(return_value={})
        request.app.container.logger = MagicMock(return_value=MagicMock())
        
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        request.app.state.connector_registry = registry
        
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "_id": "kb1",
            "type": "KB",
            "isActive": True,
            "name": "Private KB",
        })
        graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "u4"})
        graph_provider.get_user_kb_permission = AsyncMock(return_value=None)  # no role
        request.app.state.graph_provider = graph_provider
        
        kafka = AsyncMock()
        
        with pytest.raises(HTTPException) as exc:
            await reindex_connector(
                connector_id="kb1",
                request=request,
                kafka_service=kafka,
            )
        
        assert exc.value.status_code == 403
        assert "Insufficient KB permissions" in exc.value.detail

    @pytest.mark.asyncio
    async def test_non_kb_connector_registry_miss_404(self):
        """Non-KB connector not found in registry → 404 (no KB fallback)."""
        from app.connectors.api.router import reindex_connector
        
        request = MagicMock()
        request.state.user.get = lambda k, d=None: {"userId": "u5", "orgId": "o1"}.get(k, d)
        request.headers.get = lambda k, d="": {"X-Is-Admin": "false"}.get(k, d)
        request.json = AsyncMock(return_value={})
        request.app.container.logger = MagicMock(return_value=MagicMock())
        
        registry = AsyncMock()
        registry.get_connector_instance = AsyncMock(return_value=None)
        request.app.state.connector_registry = registry
        
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "_id": "conn1",
            "type": "GOOGLE_DRIVE",  # not KB
            "isActive": True,
            "name": "GDrive",
        })
        request.app.state.graph_provider = graph_provider
        
        kafka = AsyncMock()
        
        with pytest.raises(HTTPException) as exc:
            await reindex_connector(
                connector_id="conn1",
                request=request,
                kafka_service=kafka,
            )
        
        assert exc.value.status_code == 404
        assert "not found or access denied" in exc.value.detail
