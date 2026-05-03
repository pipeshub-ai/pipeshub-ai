"""
Extended tests for app.sources.client.servicenow.servicenow covering missing lines:
- ServiceNowRESTClientViaOAuthClientCredentials.is_oauth_completed
- ServiceNowRESTClientViaOAuthClientCredentials.fetch_token
- ServiceNowRESTClientViaOAuthAuthorizationCode.get_authorization_url (with/without state)
- ServiceNowRESTClientViaOAuthAuthorizationCode.is_oauth_completed
- ServiceNowRESTClientViaOAuthAuthorizationCode._exchange_code_for_token
- ServiceNowRESTClientViaOAuthAuthorizationCode.refresh_access_token
- ServiceNowRESTClientViaOAuthROPC.is_oauth_completed
- ServiceNowRESTClientViaOAuthROPC.fetch_token
- ServiceNowClient.build_from_services with all auth types
"""

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
    ServiceNowTokenConfig,
    ServiceNowUsernamePasswordConfig,
)


# ============================================================================
# OAuth Client Credentials - fetch_token, is_oauth_completed
# ============================================================================


class TestOAuthClientCredentials:
    def test_is_oauth_completed_without_token(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
        )
        assert client.is_oauth_completed() is False

    def test_is_oauth_completed_with_token(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            access_token="token123",
        )
        assert client.is_oauth_completed() is True

    @pytest.mark.asyncio
    async def test_fetch_token_success(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
        )
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"access_token": "new_token"}

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.fetch_token()
            assert token == "new_token"
            assert client.is_oauth_completed() is True
            assert client.headers["Authorization"] == "Bearer new_token"

    @pytest.mark.asyncio
    async def test_fetch_token_failure(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
        )
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text.return_value = "Bad request"

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            from app.sources.client.http.exception.exception import BadRequestError
            with pytest.raises(BadRequestError):
                await client.fetch_token()


# ============================================================================
# OAuth Authorization Code - get_authorization_url, exchange, refresh
# ============================================================================


class TestOAuthAuthorizationCode:
    def test_get_authorization_url_without_state(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="http://localhost/callback",
        )
        url = client.get_authorization_url()
        assert "response_type=code" in url
        assert "client_id=cid" in url
        assert "state" not in url

    def test_get_authorization_url_with_state(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="http://localhost/callback",
        )
        url = client.get_authorization_url(state="random_state")
        assert "state=random_state" in url

    def test_is_oauth_completed_false(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="http://localhost/callback",
        )
        assert client.is_oauth_completed() is False

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="http://localhost/callback",
        )
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
        }

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.initiate_oauth_flow("auth_code_123")
            assert token == "at"
            assert client.refresh_token == "rt"
            assert client.is_oauth_completed() is True

    @pytest.mark.asyncio
    async def test_initiate_oauth_flow_failure(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="http://localhost/callback",
        )
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text.return_value = "Bad request"

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            from app.sources.client.http.exception.exception import BadRequestError
            with pytest.raises(BadRequestError):
                await client.initiate_oauth_flow("bad_code")

    @pytest.mark.asyncio
    async def test_refresh_access_token_success(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="http://localhost/callback",
        )
        client.refresh_token = "old_rt"
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "access_token": "new_at",
            "refresh_token": "new_rt",
        }

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.refresh_access_token()
            assert token == "new_at"
            assert client.refresh_token == "new_rt"

    @pytest.mark.asyncio
    async def test_refresh_access_token_no_refresh_token(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="http://localhost/callback",
        )
        with pytest.raises(ValueError, match="No refresh token available"):
            await client.refresh_access_token()

    @pytest.mark.asyncio
    async def test_refresh_access_token_failure(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            redirect_uri="http://localhost/callback",
        )
        client.refresh_token = "rt"
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text.return_value = "Token refresh failed"

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            from app.sources.client.http.exception.exception import BadRequestError
            with pytest.raises(BadRequestError):
                await client.refresh_access_token()


# ============================================================================
# OAuth ROPC - fetch_token, is_oauth_completed
# ============================================================================


class TestOAuthROPC:
    def test_is_oauth_completed_false(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            username="user",
            password="pass",
        )
        assert client.is_oauth_completed() is False

    @pytest.mark.asyncio
    async def test_fetch_token_success(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            username="user",
            password="pass",
        )
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"access_token": "ropc_token"}

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.fetch_token()
            assert token == "ropc_token"
            assert client.is_oauth_completed() is True

    @pytest.mark.asyncio
    async def test_fetch_token_failure(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            instance_url="https://dev.service-now.com",
            client_id="cid",
            client_secret="csecret",
            username="user",
            password="pass",
        )
        mock_response = MagicMock()
        mock_response.status = 400
        mock_response.text.return_value = "Token request failed"

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            from app.sources.client.http.exception.exception import BadRequestError
            with pytest.raises(BadRequestError):
                await client.fetch_token()


# ============================================================================
# build_from_services - more auth types
# ============================================================================


class TestBuildFromServicesExtended:
    @pytest.mark.asyncio
    async def test_build_oauth_client_credentials(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH_CLIENT_CREDENTIALS",
                "instanceUrl": "https://dev.service-now.com",
                "clientId": "cid",
                "clientSecret": "csecret",
                "credentials": {"access_token": "existing_token"},
            }
        })
        logger = logging.getLogger("test")
        client = await ServiceNowClient.build_from_services(logger, config_service, "inst1")
        assert isinstance(client.get_client(), ServiceNowRESTClientViaOAuthClientCredentials)

    @pytest.mark.asyncio
    async def test_build_oauth_authorization_code(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH_AUTHORIZATION_CODE",
                "instanceUrl": "https://dev.service-now.com",
                "clientId": "cid",
                "clientSecret": "csecret",
                "redirectUri": "http://localhost/callback",
                "credentials": {"access_token": "at"},
            }
        })
        logger = logging.getLogger("test")
        client = await ServiceNowClient.build_from_services(logger, config_service, "inst1")
        assert isinstance(client.get_client(), ServiceNowRESTClientViaOAuthAuthorizationCode)

    @pytest.mark.asyncio
    async def test_build_oauth_ropc(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "OAUTH_ROPC",
                "instanceUrl": "https://dev.service-now.com",
                "clientId": "cid",
                "clientSecret": "csecret",
                "username": "user",
                "password": "pass",
                "credentials": {"access_token": "at"},
            }
        })
        logger = logging.getLogger("test")
        client = await ServiceNowClient.build_from_services(logger, config_service, "inst1")
        assert isinstance(client.get_client(), ServiceNowRESTClientViaOAuthROPC)

    @pytest.mark.asyncio
    async def test_build_invalid_auth_type(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {
                "authType": "UNKNOWN",
                "instanceUrl": "https://dev.service-now.com",
            }
        })
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ServiceNowClient.build_from_services(logger, config_service, "inst1")

    @pytest.mark.asyncio
    async def test_build_get_instance_url(self):
        client_inner = ServiceNowRESTClientViaUsernamePassword(
            instance_url="https://dev.service-now.com/",
            username="user",
            password="pass",
        )
        sn_client = ServiceNowClient(client_inner)
        assert sn_client.get_instance_url() == "https://dev.service-now.com"
