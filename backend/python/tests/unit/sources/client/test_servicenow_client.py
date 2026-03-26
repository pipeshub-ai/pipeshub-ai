"""Unit tests for ServiceNow client module."""

import base64
import logging
from unittest.mock import AsyncMock, MagicMock

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_servicenow_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


INSTANCE_URL = "https://dev12345.service-now.com"


# ---------------------------------------------------------------------------
# ServiceNowResponse
# ---------------------------------------------------------------------------


class TestServiceNowResponse:
    def test_success(self):
        resp = ServiceNowResponse(success=True, data={"key": "val"})
        assert resp.success is True

    def test_to_dict(self):
        resp = ServiceNowResponse(success=True, data={"k": "v"})
        d = resp.to_dict()
        assert d["success"] is True

    def test_to_json(self):
        resp = ServiceNowResponse(success=True)
        j = resp.to_json()
        assert '"success":true' in j or '"success": true' in j


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


class TestServiceNowRESTClientViaUsernamePassword:
    def test_init(self):
        client = ServiceNowRESTClientViaUsernamePassword(INSTANCE_URL, "user", "pass")
        assert client.instance_url == INSTANCE_URL
        assert client.base_url == INSTANCE_URL
        assert client.username == "user"
        expected_cred = base64.b64encode(b"user:pass").decode()
        assert client.headers["Authorization"] == f"Basic {expected_cred}"

    def test_trailing_slash_stripped(self):
        client = ServiceNowRESTClientViaUsernamePassword(f"{INSTANCE_URL}/", "u", "p")
        assert client.instance_url == INSTANCE_URL

    def test_get_base_url(self):
        client = ServiceNowRESTClientViaUsernamePassword(INSTANCE_URL, "u", "p")
        assert client.get_base_url() == INSTANCE_URL

    def test_get_instance_url(self):
        client = ServiceNowRESTClientViaUsernamePassword(INSTANCE_URL, "u", "p")
        assert client.get_instance_url() == INSTANCE_URL


class TestServiceNowRESTClientViaToken:
    def test_init(self):
        client = ServiceNowRESTClientViaToken(INSTANCE_URL, "my-token")
        assert client.instance_url == INSTANCE_URL
        assert f"{INSTANCE_URL}/api/now" == client.base_url

    def test_get_base_url(self):
        client = ServiceNowRESTClientViaToken(INSTANCE_URL, "tok")
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"


class TestServiceNowRESTClientViaAPIKey:
    def test_init(self):
        client = ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "api-key-123")
        assert client.api_key == "api-key-123"
        assert client.headers["x-sn-apikey"] == "api-key-123"

    def test_custom_header(self):
        client = ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "key", "X-Custom")
        assert client.headers["X-Custom"] == "key"
        assert client.header_name == "X-Custom"

    def test_get_base_url(self):
        client = ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "key")
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"


class TestServiceNowRESTClientViaOAuthClientCredentials:
    def test_init(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            INSTANCE_URL, "cid", "csec"
        )
        assert client.client_id == "cid"
        assert client.client_secret == "csec"
        assert client.access_token is None
        assert client.is_oauth_completed() is False

    def test_init_with_token(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            INSTANCE_URL, "cid", "csec", access_token="at"
        )
        assert client.access_token == "at"
        assert client.is_oauth_completed() is True

    def test_get_base_url(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(INSTANCE_URL, "c", "s")
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"


class TestServiceNowRESTClientViaOAuthAuthorizationCode:
    def test_init(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        assert client.redirect_uri == "http://redirect"
        assert client.is_oauth_completed() is False

    def test_init_with_token(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect", access_token="at"
        )
        assert client.is_oauth_completed() is True

    def test_get_authorization_url(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        url = client.get_authorization_url()
        assert "response_type=code" in url
        assert "client_id=cid" in url

    def test_get_authorization_url_with_state(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        url = client.get_authorization_url(state="random-state")
        assert "state=random-state" in url

    def test_no_refresh_token_raises(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        assert client.refresh_token is None


class TestServiceNowRESTClientViaOAuthROPC:
    def test_init(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass"
        )
        assert client.username == "user"
        assert client.password == "pass"
        assert client.is_oauth_completed() is False

    def test_init_with_token(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass", access_token="at"
        )
        assert client.is_oauth_completed() is True


# ---------------------------------------------------------------------------
# Config classes
# ---------------------------------------------------------------------------


class TestServiceNowUsernamePasswordConfig:
    def test_create_client(self):
        cfg = ServiceNowUsernamePasswordConfig(INSTANCE_URL, "user", "pass")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaUsernamePassword)

    def test_to_dict(self):
        cfg = ServiceNowUsernamePasswordConfig(INSTANCE_URL, "user", "pass")
        d = cfg.to_dict()
        assert d["instance_url"] == INSTANCE_URL
        assert d["username"] == "user"


class TestServiceNowTokenConfig:
    def test_create_client(self):
        cfg = ServiceNowTokenConfig(INSTANCE_URL, "tok")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaToken)

    def test_to_dict(self):
        cfg = ServiceNowTokenConfig(INSTANCE_URL, "tok")
        d = cfg.to_dict()
        assert d["token"] == "tok"


class TestServiceNowAPIKeyConfig:
    def test_create_client(self):
        cfg = ServiceNowAPIKeyConfig(INSTANCE_URL, "api-key")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaAPIKey)

    def test_to_dict(self):
        cfg = ServiceNowAPIKeyConfig(INSTANCE_URL, "api-key")
        d = cfg.to_dict()
        assert d["api_key"] == "api-key"


class TestServiceNowOAuthClientCredentialsConfig:
    def test_create_client(self):
        cfg = ServiceNowOAuthClientCredentialsConfig(INSTANCE_URL, "cid", "csec")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaOAuthClientCredentials)

    def test_to_dict(self):
        cfg = ServiceNowOAuthClientCredentialsConfig(INSTANCE_URL, "cid", "csec")
        d = cfg.to_dict()
        assert d["client_id"] == "cid"


class TestServiceNowOAuthAuthorizationCodeConfig:
    def test_create_client(self):
        cfg = ServiceNowOAuthAuthorizationCodeConfig(INSTANCE_URL, "cid", "csec", "http://redirect")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaOAuthAuthorizationCode)


class TestServiceNowOAuthROPCConfig:
    def test_create_client(self):
        cfg = ServiceNowOAuthROPCConfig(INSTANCE_URL, "cid", "csec", "user", "pass")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaOAuthROPC)


# ---------------------------------------------------------------------------
# ServiceNowClient
# ---------------------------------------------------------------------------


class TestServiceNowClient:
    def test_init(self):
        mock_client = MagicMock()
        mock_client.get_base_url.return_value = INSTANCE_URL
        mock_client.get_instance_url.return_value = INSTANCE_URL
        client = ServiceNowClient(mock_client)
        assert client.get_client() is mock_client
        assert client.get_base_url() == INSTANCE_URL
        assert client.get_instance_url() == INSTANCE_URL

    def test_build_with_config_username_password(self):
        cfg = ServiceNowUsernamePasswordConfig(INSTANCE_URL, "user", "pass")
        client = ServiceNowClient.build_with_config(cfg)
        assert isinstance(client, ServiceNowClient)

    def test_build_with_config_token(self):
        cfg = ServiceNowTokenConfig(INSTANCE_URL, "tok")
        client = ServiceNowClient.build_with_config(cfg)
        assert isinstance(client, ServiceNowClient)

    def test_build_with_config_api_key(self):
        cfg = ServiceNowAPIKeyConfig(INSTANCE_URL, "api-key")
        client = ServiceNowClient.build_with_config(cfg)
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_username_password(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "USERNAME_PASSWORD",
                    "instanceUrl": INSTANCE_URL,
                    "username": "user",
                    "password": "pass",
                }
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "TOKEN", "instanceUrl": INSTANCE_URL, "token": "tok"}
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_api_key(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_KEY", "instanceUrl": INSTANCE_URL, "apiKey": "key"}
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_client_credentials(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_CLIENT_CREDENTIALS",
                    "instanceUrl": INSTANCE_URL,
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "credentials": {"access_token": "at"},
                }
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_authorization_code(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_AUTHORIZATION_CODE",
                    "instanceUrl": INSTANCE_URL,
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "redirectUri": "http://redirect",
                    "credentials": {"access_token": "at"},
                }
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_ropc(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_ROPC",
                    "instanceUrl": INSTANCE_URL,
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "username": "user",
                    "password": "pass",
                    "credentials": {"access_token": "at"},
                }
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_invalid_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED", "instanceUrl": INSTANCE_URL}
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ServiceNowClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await ServiceNowClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await ServiceNowClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await ServiceNowClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError):
            await ServiceNowClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )
