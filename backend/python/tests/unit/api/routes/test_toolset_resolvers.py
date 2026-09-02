"""Tests for app.api.routes.toolset_resolvers — OSS edition resolver helpers."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# _oauth_config_path
# ---------------------------------------------------------------------------


class TestOauthConfigPath:
    def test_returns_path_for_type(self) -> None:
        from app.api.routes.toolset_resolvers import _oauth_config_path

        result = _oauth_config_path("jira")
        assert result == "/services/oauths/toolsets/jira"

    def test_lowercases_type(self) -> None:
        from app.api.routes.toolset_resolvers import _oauth_config_path

        result = _oauth_config_path("GoogleDrive")
        assert result == "/services/oauths/toolsets/googledrive"


# ---------------------------------------------------------------------------
# get_oauth_credentials_for_toolset
# ---------------------------------------------------------------------------


class TestGetOauthCredentialsForToolset:
    """Tests for async OAuth credential fetching."""

    async def test_inline_credentials_returned(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        toolset_config = {
            "auth": {"clientId": "id1", "clientSecret": "secret1", "tenantId": "t1"},
            "toolsetType": "jira",
        }
        config_service = AsyncMock()
        result = await get_oauth_credentials_for_toolset(toolset_config, config_service)
        assert result["clientId"] == "id1"
        assert result["clientSecret"] == "secret1"
        assert result["tenantId"] == "t1"
        # Should not call config_service when inline creds are present
        config_service.get_config.assert_not_called()

    async def test_fetches_by_oauth_config_id(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        toolset_config = {
            "toolsetType": "slack",
            "oauthConfigId": "cfg-123",
        }
        oauth_configs = [
            {"_id": "cfg-123", "config": {"clientId": "cid", "clientSecret": "csec", "domain": "d"}},
        ]
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=oauth_configs)

        result = await get_oauth_credentials_for_toolset(toolset_config, config_service)
        assert result["clientId"] == "cid"
        assert result["clientSecret"] == "csec"
        assert result["domain"] == "d"

    async def test_missing_toolset_type_raises(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        toolset_config = {"oauthConfigId": "cfg-1"}
        config_service = AsyncMock()
        with pytest.raises(ValueError, match="[Tt]oolset type"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    async def test_missing_oauth_config_id_raises(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        toolset_config = {"toolsetType": "jira"}
        config_service = AsyncMock()
        # get_config returns an empty list (no instances to look up)
        config_service.get_config = AsyncMock(return_value=[])
        with pytest.raises(ValueError, match="oauthConfigId"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    async def test_config_not_found_raises(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "missing-id",
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "other-id", "config": {"clientId": "x", "clientSecret": "y"}},
        ])

        with pytest.raises(ValueError, match="not found"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    async def test_invalid_config_data_raises(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "cfg-1",
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "config": None},
        ])

        with pytest.raises(ValueError, match="invalid"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    async def test_missing_client_credentials_raises(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "cfg-1",
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "config": {"domain": "example.com"}},
        ])

        with pytest.raises(ValueError, match="clientId|clientSecret"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    async def test_exception_in_fetch_raises_value_error(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        toolset_config = {
            "toolsetType": "jira",
            "oauthConfigId": "cfg-1",
        }
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))

        with pytest.raises(ValueError, match="Failed"):
            await get_oauth_credentials_for_toolset(toolset_config, config_service)

    async def test_empty_toolset_config_raises(self) -> None:
        from app.api.routes.toolset_resolvers import get_oauth_credentials_for_toolset

        config_service = AsyncMock()
        with pytest.raises(ValueError):
            await get_oauth_credentials_for_toolset({}, config_service)


# ---------------------------------------------------------------------------
# get_toolset_by_id
# ---------------------------------------------------------------------------


class TestGetToolsetById:
    """Tests for instance lookup by ID."""

    async def test_found(self) -> None:
        from app.api.routes.toolset_resolvers import get_toolset_by_id

        config_service = AsyncMock()
        instances = [
            {"_id": "inst-1", "orgId": "org-1", "name": "Jira"},
            {"_id": "inst-2", "orgId": "org-1", "name": "Slack"},
        ]
        config_service.get_config = AsyncMock(return_value=instances)

        result = await get_toolset_by_id("inst-2", config_service, "org-1")
        assert result is not None
        assert result["_id"] == "inst-2"
        assert result["name"] == "Slack"

    async def test_not_found(self) -> None:
        from app.api.routes.toolset_resolvers import get_toolset_by_id

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "inst-1", "orgId": "org-1"},
        ])

        result = await get_toolset_by_id("missing", config_service, "org-1")
        assert result is None

    async def test_org_mismatch_returns_none(self) -> None:
        from app.api.routes.toolset_resolvers import get_toolset_by_id

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "inst-1", "orgId": "org-other"},
        ])

        result = await get_toolset_by_id("inst-1", config_service, "org-1")
        assert result is None

    async def test_exception_returns_none(self) -> None:
        from app.api.routes.toolset_resolvers import get_toolset_by_id

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))

        result = await get_toolset_by_id("inst-1", config_service, "org-1")
        assert result is None


# ---------------------------------------------------------------------------
# check_user_is_admin
# ---------------------------------------------------------------------------


class TestCheckUserIsAdmin:
    """Tests for admin verification via Node.js API call."""

    async def test_admin_confirmed(self) -> None:
        from app.api.routes.toolset_resolvers import check_user_is_admin

        request = MagicMock()
        request.headers = {"authorization": "Bearer tok", "x-organization-id": "org-1"}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "nodejs": {"endpoint": "http://localhost:3000"},
        })

        with patch("app.api.routes.toolset_resolvers.httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock(status_code=200)
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await check_user_is_admin("user-1", "org-1", request, config_service)
        assert result is True

    async def test_not_admin(self) -> None:
        from app.api.routes.toolset_resolvers import check_user_is_admin

        request = MagicMock()
        request.headers = {"authorization": "Bearer tok"}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "nodejs": {"endpoint": "http://localhost:3000"},
        })

        with patch("app.api.routes.toolset_resolvers.httpx.AsyncClient") as mock_cls:
            mock_resp = MagicMock(status_code=403)
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await check_user_is_admin("user-1", "org-1", request, config_service)
        assert result is False

    async def test_no_request_returns_false(self) -> None:
        from app.api.routes.toolset_resolvers import check_user_is_admin

        config_service = AsyncMock()
        result = await check_user_is_admin("user-1", "org-1", None, config_service)
        assert result is False

    async def test_api_error_returns_false(self) -> None:
        from app.api.routes.toolset_resolvers import check_user_is_admin

        request = MagicMock()
        request.headers = {"authorization": "Bearer tok"}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=Exception("timeout"))

        with patch("app.api.routes.toolset_resolvers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("conn refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await check_user_is_admin("user-1", "org-1", request, config_service)
        assert result is False


# ---------------------------------------------------------------------------
# resolve_inherited_from_org_id (OSS no-op)
# ---------------------------------------------------------------------------


class TestResolveInheritedFromOrgId:
    async def test_returns_none(self) -> None:
        from app.api.routes.toolset_resolvers import resolve_inherited_from_org_id

        result = await resolve_inherited_from_org_id(
            toolset_type="google",
            oauth_config_id="oid",
            org_id="org-1",
            config_service=AsyncMock(),
        )
        assert result is None


# ---------------------------------------------------------------------------
# mask_oauth_secrets (OSS passthrough)
# ---------------------------------------------------------------------------


class TestMaskOauthSecrets:
    def test_returns_copy(self) -> None:
        from app.api.routes.toolset_resolvers import mask_oauth_secrets

        cfg = {"clientId": "id", "clientSecret": "secret"}
        result = mask_oauth_secrets(cfg, is_inherited=False)
        assert result == cfg
        assert result is not cfg

    def test_returns_copy_inherited(self) -> None:
        from app.api.routes.toolset_resolvers import mask_oauth_secrets

        cfg = {"clientId": "id", "clientSecret": "secret"}
        result = mask_oauth_secrets(cfg, is_inherited=True)
        assert result == cfg
        assert result is not cfg


# ---------------------------------------------------------------------------
# is_redacted_placeholder (OSS stub)
# ---------------------------------------------------------------------------


class TestIsRedactedPlaceholder:
    def test_returns_false(self) -> None:
        from app.api.routes.toolset_resolvers import is_redacted_placeholder

        assert is_redacted_placeholder("anything") is False

    def test_returns_false_for_empty(self) -> None:
        from app.api.routes.toolset_resolvers import is_redacted_placeholder

        assert is_redacted_placeholder("") is False


# ---------------------------------------------------------------------------
# load_instances_for_mutation
# ---------------------------------------------------------------------------


class TestLoadInstancesForMutation:
    async def test_success(self) -> None:
        from app.api.routes.toolset_resolvers import load_instances_for_mutation

        instances = [
            {"_id": "i1", "orgId": "org-1", "name": "A"},
            {"_id": "i2", "orgId": "org-2", "name": "B"},
        ]
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=instances)

        result = await load_instances_for_mutation("org-1", config_service)
        assert isinstance(result, list)

    async def test_invalid_data_type(self) -> None:
        from app.api.routes.toolset_resolvers import load_instances_for_mutation

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not-a-list")

        with pytest.raises(HTTPException) as exc_info:
            await load_instances_for_mutation("org-1", config_service)
        assert exc_info.value.status_code == 500

    async def test_exception_handling(self) -> None:
        from app.api.routes.toolset_resolvers import load_instances_for_mutation

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))

        with pytest.raises(Exception):
            await load_instances_for_mutation("org-1", config_service)
