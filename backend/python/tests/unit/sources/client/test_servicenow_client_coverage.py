"""Coverage tests for app.sources.client.servicenow.servicenow targeting uncovered lines.

Missing lines from coverage report (96.2%):
- 156: get_instance_url on OAuthClientCredentials
- 190->194: fetch_token no access_token in response (OAuthClientCredentials)
- 235: get_instance_url on OAuthAuthorizationCode
- 239: is_oauth_completed on OAuthAuthorizationCode (already covered but need branch)
- 310->313: refresh_access_token returns no new access_token
- 347->351: _exchange_code_for_token no access_token in response
- 394: get_instance_url on OAuthROPC
- 398: is_oauth_completed on OAuthROPC (already tested)
- 433->437: fetch_token ROPC no access_token in response
- 683: build_from_services - config returns None after _get_connector_config
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.servicenow.servicenow import (
    ServiceNowClient,
    ServiceNowRESTClientViaOAuthAuthorizationCode,
    ServiceNowRESTClientViaOAuthClientCredentials,
    ServiceNowRESTClientViaOAuthROPC,
    ServiceNowRESTClientViaToken,
    ServiceNowRESTClientViaUsernamePassword,
)


@pytest.fixture
def logger():
    return logging.getLogger("test_servicenow_coverage")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


INSTANCE_URL = "https://dev12345.service-now.com"


# ============================================================================
# Line 156: OAuthClientCredentials.get_instance_url
# ============================================================================

class TestOAuthClientCredentialsInstanceUrl:
    def test_get_instance_url(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            INSTANCE_URL, "cid", "csec"
        )
        assert client.get_instance_url() == INSTANCE_URL

    def test_get_instance_url_trailing_slash_stripped(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            f"{INSTANCE_URL}/", "cid", "csec"
        )
        assert client.get_instance_url() == INSTANCE_URL


# ============================================================================
# Line 190->194: OAuthClientCredentials.fetch_token - no access_token in response
# ============================================================================

class TestOAuthClientCredentialsFetchTokenNoToken:
    @pytest.mark.asyncio
    async def test_fetch_token_no_access_token_in_response(self):
        """When token response has no access_token, return None and don't update headers."""
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            INSTANCE_URL, "cid", "csec"
        )
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"error": "something", "token_type": "bearer"}

        original_auth = client.headers.get("Authorization", "")
        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.fetch_token()
            assert token is None
            assert client.is_oauth_completed() is False
            # Authorization header should not have been updated
            assert client.headers.get("Authorization", "") == original_auth


# ============================================================================
# Line 235: OAuthAuthorizationCode.get_instance_url
# ============================================================================

class TestOAuthAuthorizationCodeInstanceUrl:
    def test_get_instance_url(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        assert client.get_instance_url() == INSTANCE_URL

    def test_get_instance_url_trailing_slash(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            f"{INSTANCE_URL}/", "cid", "csec", "http://redirect"
        )
        assert client.get_instance_url() == INSTANCE_URL


# ============================================================================
# Line 239: OAuthAuthorizationCode.is_oauth_completed (with token)
# ============================================================================

class TestOAuthAuthorizationCodeIsOAuthCompleted:
    def test_is_oauth_completed_true_with_access_token(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect", access_token="at"
        )
        assert client.is_oauth_completed() is True

    def test_is_oauth_completed_false_without_access_token(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        assert client.is_oauth_completed() is False


# ============================================================================
# Lines 310->313: OAuthAuthorizationCode.refresh_access_token - no access_token
# ============================================================================

class TestOAuthAuthorizationCodeRefreshNoToken:
    @pytest.mark.asyncio
    async def test_refresh_returns_no_access_token(self):
        """When refresh response lacks access_token, return None."""
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        client.refresh_token = "old-rt"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "token_type": "bearer",
            # No access_token in response
        }

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.refresh_access_token()
            assert token is None
            assert client.access_token is None
            # refresh_token should be preserved
            assert client.refresh_token == "old-rt"

    @pytest.mark.asyncio
    async def test_refresh_preserves_existing_refresh_token_when_not_in_response(self):
        """When refresh response doesn't have new refresh_token, keep old one."""
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        client.refresh_token = "old-rt"

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "access_token": "new-at",
            # No refresh_token in response
        }

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.refresh_access_token()
            assert token == "new-at"
            # Old refresh_token preserved
            assert client.refresh_token == "old-rt"

    @pytest.mark.asyncio
    async def test_refresh_with_provided_refresh_token(self):
        """Can pass refresh_token as parameter instead of using stored one."""
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        # No stored refresh_token, but provide one

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "access_token": "new-at",
            "refresh_token": "new-rt",
        }

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.refresh_access_token(refresh_token="provided-rt")
            assert token == "new-at"
            assert client.refresh_token == "new-rt"


# ============================================================================
# Lines 347->351: OAuthAuthorizationCode._exchange_code_for_token - no access_token
# ============================================================================

class TestOAuthAuthorizationCodeExchangeNoToken:
    @pytest.mark.asyncio
    async def test_exchange_code_no_access_token_in_response(self):
        """When token exchange response lacks access_token, return None."""
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "token_type": "bearer",
            "refresh_token": "rt",
            # No access_token
        }

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client._exchange_code_for_token("auth-code")
            assert token is None
            assert client.access_token is None
            assert client.refresh_token == "rt"
            assert client.is_oauth_completed() is False

    @pytest.mark.asyncio
    async def test_exchange_code_with_access_token_sets_completed(self):
        """When exchange returns access_token, oauth is completed."""
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
        }

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client._exchange_code_for_token("auth-code")
            assert token == "at"
            assert client.is_oauth_completed() is True
            assert client.headers["Authorization"] == "Bearer at"


# ============================================================================
# Line 394: OAuthROPC.get_instance_url
# ============================================================================

class TestOAuthROPCInstanceUrl:
    def test_get_instance_url(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass"
        )
        assert client.get_instance_url() == INSTANCE_URL

    def test_get_instance_url_trailing_slash(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            f"{INSTANCE_URL}/", "cid", "csec", "user", "pass"
        )
        assert client.get_instance_url() == INSTANCE_URL


# ============================================================================
# Line 398: OAuthROPC.is_oauth_completed
# ============================================================================

class TestOAuthROPCIsOAuthCompleted:
    def test_with_token(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass", access_token="at"
        )
        assert client.is_oauth_completed() is True

    def test_without_token(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass"
        )
        assert client.is_oauth_completed() is False


# ============================================================================
# Lines 433->437: OAuthROPC.fetch_token - no access_token in response
# ============================================================================

class TestOAuthROPCFetchTokenNoToken:
    @pytest.mark.asyncio
    async def test_fetch_token_no_access_token_in_response(self):
        """When ROPC token response lacks access_token, return None."""
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass"
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"error": "invalid_grant"}

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.fetch_token()
            assert token is None
            assert client.is_oauth_completed() is False

    @pytest.mark.asyncio
    async def test_fetch_token_with_access_token_sets_completed(self):
        """When ROPC fetch_token returns access_token, oauth is completed."""
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass"
        )

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json.return_value = {"access_token": "ropc-at"}

        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            token = await client.fetch_token()
            assert token == "ropc-at"
            assert client.is_oauth_completed() is True
            assert client.headers["Authorization"] == "Bearer ropc-at"


# ============================================================================
# Line 683: build_from_services - config is None
# ============================================================================

class TestBuildFromServicesConfigNone:
    @pytest.mark.asyncio
    async def test_config_none_raises(self, logger, mock_config_service):
        """When _get_connector_config returns None, raise ValueError."""
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get ServiceNow"):
            await ServiceNowClient.build_from_services(
                logger, mock_config_service, "inst-missing"
            )

    @pytest.mark.asyncio
    async def test_config_empty_dict_raises(self, logger, mock_config_service):
        """When get_config returns empty dict, _get_connector_config raises ValueError."""
        mock_config_service.get_config = AsyncMock(return_value={})
        # Empty dict is falsy in Python, so _get_connector_config raises
        with pytest.raises(ValueError, match="Failed to get ServiceNow"):
            await ServiceNowClient.build_from_services(
                logger, mock_config_service, "inst-empty"
            )

    @pytest.mark.asyncio
    async def test_config_returns_falsy_directly(self, logger, mock_config_service):
        """When _get_connector_config is mocked to return None, line 683 is hit."""
        with patch.object(
            ServiceNowClient, "_get_connector_config",
            new_callable=AsyncMock,
            return_value=None
        ):
            with pytest.raises(ValueError, match="Failed to get ServiceNow"):
                await ServiceNowClient.build_from_services(
                    logger, mock_config_service, "inst-missing"
                )

    @pytest.mark.asyncio
    async def test_config_returns_empty_dict_directly(self, logger, mock_config_service):
        """When _get_connector_config is mocked to return empty dict, line 683 is hit."""
        with patch.object(
            ServiceNowClient, "_get_connector_config",
            new_callable=AsyncMock,
            return_value={}
        ):
            with pytest.raises(ValueError, match="Failed to get ServiceNow"):
                await ServiceNowClient.build_from_services(
                    logger, mock_config_service, "inst-empty"
                )


# ============================================================================
# OAuthClientCredentials additional edge cases
# ============================================================================

class TestOAuthClientCredentialsEdgeCases:
    def test_get_base_url(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            INSTANCE_URL, "cid", "csec"
        )
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"

    def test_oauth_token_url(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            INSTANCE_URL, "cid", "csec"
        )
        assert client.oauth_token_url == f"{INSTANCE_URL}/oauth_token.do"


# ============================================================================
# OAuthAuthorizationCode additional edge cases
# ============================================================================

class TestOAuthAuthorizationCodeEdgeCases:
    def test_get_base_url(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"

    def test_oauth_urls(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        assert client.oauth_auth_url == f"{INSTANCE_URL}/oauth_auth.do"
        assert client.oauth_token_url == f"{INSTANCE_URL}/oauth_token.do"

    def test_get_authorization_url_custom_scope(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        url = client.get_authorization_url(scope="custom_scope")
        assert "scope=custom_scope" in url


# ============================================================================
# OAuthROPC additional edge cases
# ============================================================================

class TestOAuthROPCEdgeCases:
    def test_get_base_url(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass"
        )
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"

    def test_oauth_token_url(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass"
        )
        assert client.oauth_token_url == f"{INSTANCE_URL}/oauth_token.do"


# ============================================================================
# ServiceNowClient delegate methods
# ============================================================================

class TestServiceNowClientDelegateMethods:
    def test_get_base_url_delegates_to_inner_client(self):
        inner = ServiceNowRESTClientViaToken(INSTANCE_URL, "tok")
        sn_client = ServiceNowClient(inner)
        assert sn_client.get_base_url() == f"{INSTANCE_URL}/api/now"

    def test_get_instance_url_delegates_to_inner_client(self):
        inner = ServiceNowRESTClientViaToken(INSTANCE_URL, "tok")
        sn_client = ServiceNowClient(inner)
        assert sn_client.get_instance_url() == INSTANCE_URL
