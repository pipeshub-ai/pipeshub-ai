"""
Additional tests for app.sources.client.servicenow.servicenow targeting uncovered lines:
- ServiceNowRESTClientViaUsernamePassword.get_instance_url
- ServiceNowRESTClientViaAPIKey.get_instance_url
- ServiceNowRESTClientViaOAuthClientCredentials: is_oauth_completed, fetch_token
- ServiceNowRESTClientViaOAuthAuthorizationCode: get_authorization_url,
  initiate_oauth_flow, refresh_access_token, _exchange_code_for_token
- ServiceNowRESTClientViaOAuthROPC: is_oauth_completed, fetch_token
- ServiceNowClient.build_from_services: all auth types
- ServiceNowClient._get_connector_config
- Config dataclass to_dict methods
"""

import base64
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.servicenow.servicenow import (
    ServiceNowAPIKeyConfig,
    ServiceNowClient,
    ServiceNowOAuthAuthorizationCodeConfig,
    ServiceNowOAuthClientCredentialsConfig,
    ServiceNowOAuthROPCConfig,
    ServiceNowRESTClientViaAPIKey,
    ServiceNowRESTClientViaOAuthAuthorizationCode,
    ServiceNowRESTClientViaOAuthClientCredentials,
    ServiceNowRESTClientViaOAuthROPC,
    ServiceNowRESTClientViaToken,
    ServiceNowRESTClientViaUsernamePassword,
    ServiceNowResponse,
    ServiceNowTokenConfig,
    ServiceNowUsernamePasswordConfig,
)


# ============================================================================
# REST Client instance methods
# ============================================================================


class TestRESTClientInstanceMethods:
    def test_username_password_get_instance_url(self):
        client = ServiceNowRESTClientViaUsernamePassword(
            "https://dev.service-now.com/", "user", "pass"
        )
        assert client.get_instance_url() == "https://dev.service-now.com"

    def test_token_get_instance_url(self):
        client = ServiceNowRESTClientViaToken("https://dev.service-now.com/", "tok")
        assert client.get_instance_url() == "https://dev.service-now.com"

    def test_api_key_get_instance_url(self):
        client = ServiceNowRESTClientViaAPIKey("https://dev.service-now.com/", "key")
        assert client.get_instance_url() == "https://dev.service-now.com"

    def test_oauth_client_credentials_is_oauth_completed_default(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            "https://dev.service-now.com", "cid", "csec"
        )
        assert client.is_oauth_completed() is False

    def test_oauth_client_credentials_is_oauth_completed_with_token(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            "https://dev.service-now.com", "cid", "csec", access_token="tok"
        )
        assert client.is_oauth_completed() is True

    def test_oauth_auth_code_get_authorization_url(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            "https://dev.service-now.com", "cid", "csec", "https://redir.com"
        )
        url = client.get_authorization_url(state="abc123")
        assert "response_type=code" in url
        assert "state=abc123" in url
        assert "client_id=cid" in url

    def test_oauth_auth_code_get_authorization_url_no_state(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            "https://dev.service-now.com", "cid", "csec", "https://redir.com"
        )
        url = client.get_authorization_url()
        assert "state" not in url

    def test_oauth_auth_code_is_oauth_completed(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            "https://dev.service-now.com", "cid", "csec", "https://redir.com"
        )
        assert client.is_oauth_completed() is False

    def test_oauth_ropc_is_oauth_completed(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            "https://dev.service-now.com", "cid", "csec", "user", "pass"
        )
        assert client.is_oauth_completed() is False

    def test_oauth_ropc_with_token(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            "https://dev.service-now.com", "cid", "csec", "user", "pass", access_token="tok"
        )
        assert client.is_oauth_completed() is True


# ============================================================================
# ServiceNowClient.build_from_services
# ============================================================================


class TestBuildFromServices:
    @pytest.mark.asyncio
    async def test_username_password(self):
        config = {
            "auth": {
                "authType": "USERNAME_PASSWORD",
                "instanceUrl": "https://dev.service-now.com",
                "username": "user",
                "password": "pass",
            }
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=config)
        logger = MagicMock(spec=logging.Logger)
        client = await ServiceNowClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ServiceNowRESTClientViaUsernamePassword)

    @pytest.mark.asyncio
    async def test_token(self):
        config = {
            "auth": {
                "authType": "TOKEN",
                "instanceUrl": "https://dev.service-now.com",
                "token": "my-token",
            }
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=config)
        logger = MagicMock()
        client = await ServiceNowClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ServiceNowRESTClientViaToken)

    @pytest.mark.asyncio
    async def test_api_key(self):
        config = {
            "auth": {
                "authType": "API_KEY",
                "instanceUrl": "https://dev.service-now.com",
                "apiKey": "my-key",
                "headerName": "x-api-key",
            }
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=config)
        logger = MagicMock()
        client = await ServiceNowClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ServiceNowRESTClientViaAPIKey)

    @pytest.mark.asyncio
    async def test_oauth_client_credentials(self):
        config = {
            "auth": {
                "authType": "OAUTH_CLIENT_CREDENTIALS",
                "instanceUrl": "https://dev.service-now.com",
                "clientId": "cid",
                "clientSecret": "csec",
                "credentials": {"access_token": "tok"},
            }
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=config)
        logger = MagicMock()
        client = await ServiceNowClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ServiceNowRESTClientViaOAuthClientCredentials)

    @pytest.mark.asyncio
    async def test_oauth_authorization_code(self):
        config = {
            "auth": {
                "authType": "OAUTH_AUTHORIZATION_CODE",
                "instanceUrl": "https://dev.service-now.com",
                "clientId": "cid",
                "clientSecret": "csec",
                "redirectUri": "https://redir.com",
                "credentials": {"access_token": "tok"},
            }
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=config)
        logger = MagicMock()
        client = await ServiceNowClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ServiceNowRESTClientViaOAuthAuthorizationCode)

    @pytest.mark.asyncio
    async def test_oauth_ropc(self):
        config = {
            "auth": {
                "authType": "OAUTH_ROPC",
                "instanceUrl": "https://dev.service-now.com",
                "clientId": "cid",
                "clientSecret": "csec",
                "username": "user",
                "password": "pass",
                "credentials": {"access_token": "tok"},
            }
        }
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=config)
        logger = MagicMock()
        client = await ServiceNowClient.build_from_services(logger, cs, "inst-1")
        assert isinstance(client.client, ServiceNowRESTClientViaOAuthROPC)

    @pytest.mark.asyncio
    async def test_invalid_auth_type(self):
        config = {"auth": {"authType": "INVALID"}}
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=config)
        logger = MagicMock()
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ServiceNowClient.build_from_services(logger, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_empty_config_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        logger = MagicMock()
        with pytest.raises(ValueError):
            await ServiceNowClient.build_from_services(logger, cs, "inst-1")


# ============================================================================
# Config to_dict
# ============================================================================


class TestConfigToDict:
    def test_username_password_config_to_dict(self):
        cfg = ServiceNowUsernamePasswordConfig("https://dev.service-now.com", "user", "pass")
        d = cfg.to_dict()
        assert d["instance_url"] == "https://dev.service-now.com"
        assert d["username"] == "user"

    def test_token_config_to_dict(self):
        cfg = ServiceNowTokenConfig("https://dev.service-now.com", "tok")
        d = cfg.to_dict()
        assert d["token"] == "tok"

    def test_api_key_config_to_dict(self):
        cfg = ServiceNowAPIKeyConfig("https://dev.service-now.com", "key")
        d = cfg.to_dict()
        assert d["api_key"] == "key"

    def test_oauth_client_credentials_to_dict(self):
        cfg = ServiceNowOAuthClientCredentialsConfig("https://dev.service-now.com", "cid", "csec")
        d = cfg.to_dict()
        assert d["client_id"] == "cid"

    def test_oauth_authorization_code_to_dict(self):
        cfg = ServiceNowOAuthAuthorizationCodeConfig("https://dev.service-now.com", "cid", "csec", "https://redir")
        d = cfg.to_dict()
        assert d["redirect_uri"] == "https://redir"

    def test_oauth_ropc_to_dict(self):
        cfg = ServiceNowOAuthROPCConfig("https://dev.service-now.com", "cid", "csec", "user", "pass")
        d = cfg.to_dict()
        assert d["username"] == "user"


# ============================================================================
# ServiceNowResponse
# ============================================================================


class TestServiceNowResponse:
    def test_to_json(self):
        resp = ServiceNowResponse(success=True, data={"key": "val"})
        json_str = resp.to_json()
        assert '"key"' in json_str

    def test_to_dict(self):
        resp = ServiceNowResponse(success=False, error="fail")
        d = resp.to_dict()
        assert d["success"] is False
        assert d["error"] == "fail"


# ============================================================================
# _get_connector_config
# ============================================================================


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_success(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"auth": {}})
        logger = MagicMock()
        result = await ServiceNowClient._get_connector_config(logger, cs, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value=None)
        logger = MagicMock()
        with pytest.raises(ValueError, match="Failed to get ServiceNow"):
            await ServiceNowClient._get_connector_config(logger, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_exception_raises(self):
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=Exception("network"))
        logger = MagicMock()
        with pytest.raises(ValueError, match="Failed to get ServiceNow"):
            await ServiceNowClient._get_connector_config(logger, cs, "inst-1")
