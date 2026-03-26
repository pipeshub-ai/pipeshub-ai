"""Unit tests for ClickUp client module."""

import base64
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.clickup.clickup import (
    ClickUpAuthConfig,
    ClickUpAuthType,
    ClickUpClient,
    ClickUpConnectorConfig,
    ClickUpCredentialsConfig,
    ClickUpOAuthConfig,
    ClickUpPersonalTokenConfig,
    ClickUpRESTClientViaOAuth,
    ClickUpRESTClientViaPersonalToken,
    ClickUpResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_clickup_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# ClickUpAuthType
# ---------------------------------------------------------------------------


class TestClickUpAuthType:
    def test_oauth(self):
        assert ClickUpAuthType.OAUTH == "OAUTH"

    def test_personal_token(self):
        assert ClickUpAuthType.PERSONAL_TOKEN == "PERSONAL_TOKEN"


# ---------------------------------------------------------------------------
# ClickUpResponse
# ---------------------------------------------------------------------------


class TestClickUpResponse:
    def test_success(self):
        resp = ClickUpResponse(success=True, data={"key": "val"})
        assert resp.success is True

    def test_to_dict(self):
        resp = ClickUpResponse(success=True, data={"key": "val"})
        d = resp.to_dict()
        assert d["success"] is True

    def test_to_dict_binary_data(self):
        resp = ClickUpResponse(success=True, data=b"binary")
        d = resp.to_dict()
        assert d["data"] == base64.b64encode(b"binary").decode("utf-8")

    def test_to_json(self):
        resp = ClickUpResponse(success=True, data={"key": "val"})
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_to_json_binary(self):
        resp = ClickUpResponse(success=True, data=b"binary")
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["data"] == base64.b64encode(b"binary").decode("utf-8")

    def test_error_response(self):
        resp = ClickUpResponse(success=False, error="oops")
        assert resp.error == "oops"

    def test_defaults(self):
        resp = ClickUpResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


class TestClickUpRESTClientViaPersonalToken:
    def test_init(self):
        client = ClickUpRESTClientViaPersonalToken("pk_token")
        assert client.get_base_url() == "https://api.clickup.com/api/v2"
        assert client.get_version() == "v2"
        assert client.headers["Authorization"] == "pk_token"
        assert client.headers["Content-Type"] == "application/json"

    def test_custom_version(self):
        client = ClickUpRESTClientViaPersonalToken("pk_token", version="v3")
        assert client.get_base_url() == "https://api.clickup.com/api/v3"
        assert client.get_version() == "v3"


class TestClickUpRESTClientViaOAuth:
    def test_init(self):
        client = ClickUpRESTClientViaOAuth("oauth-tok")
        assert client.get_base_url() == "https://api.clickup.com/api/v2"
        assert client.access_token == "oauth-tok"
        assert client.headers["Content-Type"] == "application/json"

    def test_init_with_client_credentials(self):
        client = ClickUpRESTClientViaOAuth("tok", client_id="cid", client_secret="csec")
        assert client.client_id == "cid"
        assert client.client_secret == "csec"

    def test_custom_version(self):
        client = ClickUpRESTClientViaOAuth("tok", version="v3")
        assert client.get_version() == "v3"


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------


class TestClickUpPersonalTokenConfig:
    def test_create_client(self):
        cfg = ClickUpPersonalTokenConfig(token="pk_tok")
        client = cfg.create_client()
        assert isinstance(client, ClickUpRESTClientViaPersonalToken)

    def test_default_version(self):
        cfg = ClickUpPersonalTokenConfig(token="pk_tok")
        assert cfg.version == "v2"


class TestClickUpOAuthConfig:
    def test_create_client(self):
        cfg = ClickUpOAuthConfig(access_token="tok")
        client = cfg.create_client()
        assert isinstance(client, ClickUpRESTClientViaOAuth)

    def test_with_client_credentials(self):
        cfg = ClickUpOAuthConfig(access_token="tok", client_id="cid", client_secret="csec")
        client = cfg.create_client()
        assert client.client_id == "cid"


class TestClickUpAuthConfig:
    def test_defaults(self):
        cfg = ClickUpAuthConfig()
        assert cfg.authType == ClickUpAuthType.PERSONAL_TOKEN
        assert cfg.apiToken is None


class TestClickUpCredentialsConfig:
    def test_defaults(self):
        cfg = ClickUpCredentialsConfig()
        assert cfg.access_token is None
        assert cfg.refresh_token is None


class TestClickUpConnectorConfig:
    def test_defaults(self):
        cfg = ClickUpConnectorConfig()
        assert cfg.version == "v2"
        assert cfg.auth.authType == ClickUpAuthType.PERSONAL_TOKEN


# ---------------------------------------------------------------------------
# ClickUpClient init / get_client
# ---------------------------------------------------------------------------


class TestClickUpClientInit:
    def test_init(self):
        client = ClickUpRESTClientViaPersonalToken("pk_tok")
        cc = ClickUpClient(client)
        assert cc.get_client() is client

    def test_get_base_url(self):
        client = ClickUpRESTClientViaPersonalToken("pk_tok")
        cc = ClickUpClient(client)
        assert cc.get_base_url() == "https://api.clickup.com/api/v2"

    def test_version_property(self):
        client = ClickUpRESTClientViaPersonalToken("pk_tok", version="v3")
        cc = ClickUpClient(client)
        assert cc.version == "v3"


# ---------------------------------------------------------------------------
# build_with_config
# ---------------------------------------------------------------------------


class TestBuildWithConfig:
    def test_personal_token(self):
        cfg = ClickUpPersonalTokenConfig(token="pk_tok")
        cc = ClickUpClient.build_with_config(cfg)
        assert isinstance(cc, ClickUpClient)
        assert isinstance(cc.get_client(), ClickUpRESTClientViaPersonalToken)

    def test_oauth(self):
        cfg = ClickUpOAuthConfig(access_token="tok")
        cc = ClickUpClient.build_with_config(cfg)
        assert isinstance(cc.get_client(), ClickUpRESTClientViaOAuth)


# ---------------------------------------------------------------------------
# _get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await ClickUpClient._get_connector_config(logger, mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get ClickUp"):
            await ClickUpClient._get_connector_config(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError, match="Failed to get ClickUp"):
            await ClickUpClient._get_connector_config(logger, mock_config_service, "inst-1")


# ---------------------------------------------------------------------------
# build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    async def test_personal_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "PERSONAL_TOKEN", "apiToken": "pk_tok"},
                "credentials": {},
            }
        )
        cc = await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(cc, ClickUpClient)

    @pytest.mark.asyncio
    async def test_personal_token_via_token_field(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "PERSONAL_TOKEN", "token": "pk_tok"},
                "credentials": {},
            }
        )
        cc = await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(cc, ClickUpClient)

    @pytest.mark.asyncio
    async def test_personal_token_missing_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "PERSONAL_TOKEN"},
                "credentials": {},
            }
        )
        with pytest.raises(ValueError, match="Personal token required"):
            await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH", "clientId": "cid", "clientSecret": "csec"},
                "credentials": {"access_token": "tok"},
            }
        )
        cc = await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(cc, ClickUpClient)

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "OAUTH"},
                "credentials": {},
            }
        )
        with pytest.raises(ValueError, match="Access token required"):
            await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_no_config_raises(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_shared_config(self, logger, mock_config_service):
        """Should fetch from shared OAuth config when oauthConfigId present."""

        async def fake_get_config(path, default=None):
            if "connectors" in path:
                return {
                    "auth": {"authType": "OAUTH", "oauthConfigId": "oauth-123"},
                    "credentials": {"access_token": "tok"},
                }
            if "oauth" in path:
                return [
                    {
                        "_id": "oauth-123",
                        "config": {"clientId": "shared-cid", "clientSecret": "shared-csec"},
                    }
                ]
            return default

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        cc = await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(cc, ClickUpClient)

    @pytest.mark.asyncio
    async def test_oauth_shared_config_error_fallback(self, logger, mock_config_service):
        """Should handle shared config fetch failure gracefully."""
        call_count = 0

        async def fake_get_config(path, default=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "auth": {"authType": "OAUTH", "oauthConfigId": "oauth-123"},
                    "credentials": {"access_token": "tok"},
                }
            raise RuntimeError("etcd down")

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        cc = await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(cc, ClickUpClient)


# ---------------------------------------------------------------------------
# build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_success(self, logger, mock_config_service):
        cc = await ClickUpClient.build_from_toolset(
            {"credentials": {"access_token": "tok"}, "auth": {}},
            logger,
            mock_config_service,
        )
        assert isinstance(cc, ClickUpClient)

    @pytest.mark.asyncio
    async def test_missing_token_raises(self, logger, mock_config_service):
        with pytest.raises(ValueError, match="Access token not found"):
            await ClickUpClient.build_from_toolset(
                {"credentials": {}, "auth": {}}, logger, mock_config_service
            )

    @pytest.mark.asyncio
    async def test_with_shared_oauth(self, logger, mock_config_service):
        async def fake_get_config(path, default=None):
            return [
                {"_id": "oauth-123", "config": {"clientId": "cid", "clientSecret": "csec"}}
            ]

        mock_config_service.get_config = AsyncMock(side_effect=fake_get_config)
        cc = await ClickUpClient.build_from_toolset(
            {
                "credentials": {"access_token": "tok"},
                "auth": {"oauthConfigId": "oauth-123"},
            },
            logger,
            mock_config_service,
        )
        assert isinstance(cc, ClickUpClient)

    @pytest.mark.asyncio
    async def test_custom_version(self, logger, mock_config_service):
        cc = await ClickUpClient.build_from_toolset(
            {"credentials": {"access_token": "tok"}, "auth": {}, "version": "v3"},
            logger,
            mock_config_service,
        )
        assert cc.version == "v3"

    @pytest.mark.asyncio
    async def test_shared_oauth_error_fallback(self, logger, mock_config_service):
        """Should handle shared config fetch failure gracefully in toolset."""
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        cc = await ClickUpClient.build_from_toolset(
            {
                "credentials": {"access_token": "tok"},
                "auth": {"oauthConfigId": "oauth-123"},
            },
            logger,
            mock_config_service,
        )
        assert isinstance(cc, ClickUpClient)

    @pytest.mark.asyncio
    async def test_no_config_service(self, logger):
        """Should work without config service."""
        cc = await ClickUpClient.build_from_toolset(
            {"credentials": {"access_token": "tok"}, "auth": {}},
            logger,
            None,
        )
        assert isinstance(cc, ClickUpClient)


# ---------------------------------------------------------------------------
# build_from_services - additional auth type branches
# ---------------------------------------------------------------------------


class TestBuildFromServicesAdditional:
    @pytest.mark.asyncio
    async def test_invalid_auth_type(self, logger, mock_config_service):
        """Invalid auth type should raise during build_from_services."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED"},
                "credentials": {},
            }
        )
        # Pydantic validation may reject the value, or it may raise a different error
        with pytest.raises((ValueError, Exception)):
            await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_oauth_with_inline_credentials(self, logger, mock_config_service):
        """OAuth with clientId/clientSecret directly in auth config."""
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH",
                    "clientId": "cid",
                    "clientSecret": "csec",
                },
                "credentials": {"access_token": "tok"},
            }
        )
        cc = await ClickUpClient.build_from_services(logger, mock_config_service, "inst-1")
        assert isinstance(cc, ClickUpClient)
        rest = cc.get_client()
        assert rest.client_id == "cid"
        assert rest.client_secret == "csec"
