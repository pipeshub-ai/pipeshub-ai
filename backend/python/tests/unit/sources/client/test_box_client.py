"""Unit tests for Box client module."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.box.box import (
    BoxCCGConfig,
    BoxClient,
    BoxJWTConfig,
    BoxOAuth2Config,
    BoxOAuthCodeConfig,
    BoxRESTClientViaToken,
    BoxRESTClientWithCCG,
    BoxRESTClientWithJWT,
    BoxRESTClientWithOAuth2,
    BoxRESTClientWithOAuthCode,
    BoxResponse,
    BoxTokenConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_box_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


# ---------------------------------------------------------------------------
# BoxResponse
# ---------------------------------------------------------------------------


class TestBoxResponse:
    def test_success(self):
        resp = BoxResponse(success=True, data={"key": "val"})
        assert resp.success is True
        assert resp.data == {"key": "val"}

    def test_error(self):
        resp = BoxResponse(success=False, error="oops")
        assert resp.error == "oops"

    def test_to_dict(self):
        resp = BoxResponse(success=True, data={"k": "v"})
        d = resp.to_dict()
        assert d["success"] is True

    def test_to_json(self):
        resp = BoxResponse(success=True, data={"k": "v"})
        j = resp.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_defaults(self):
        resp = BoxResponse(success=True)
        assert resp.data is None
        assert resp.error is None
        assert resp.message is None


# ---------------------------------------------------------------------------
# BoxRESTClientViaToken
# ---------------------------------------------------------------------------


class TestBoxRESTClientViaToken:
    def test_init(self):
        client = BoxRESTClientViaToken("test-token")
        assert client.access_token == "test-token"
        assert client.box_client is None

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxDeveloperTokenAuth")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_create_client(self, mock_sdk, mock_auth):
        client = BoxRESTClientViaToken("test-token")
        result = await client.create_client()
        mock_auth.assert_called_once_with(token="test-token")
        assert result is mock_sdk.return_value

    def test_get_box_client_not_initialized(self):
        client = BoxRESTClientViaToken("test-token")
        with pytest.raises(RuntimeError, match="not initialized"):
            client.get_box_client()

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxDeveloperTokenAuth")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_get_box_client_after_init(self, mock_sdk, mock_auth):
        client = BoxRESTClientViaToken("test-token")
        await client.create_client()
        result = client.get_box_client()
        assert result is mock_sdk.return_value


# ---------------------------------------------------------------------------
# BoxRESTClientWithJWT
# ---------------------------------------------------------------------------


class TestBoxRESTClientWithJWT:
    def test_init(self):
        client = BoxRESTClientWithJWT(
            client_id="cid", client_secret="csec", enterprise_id="eid",
            jwt_key_id="kid", rsa_private_key_data="key-data"
        )
        assert client.client_id == "cid"
        assert client.box_client is None

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxJWTAuth")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_create_client(self, mock_sdk, mock_auth):
        client = BoxRESTClientWithJWT(
            client_id="cid", client_secret="csec", enterprise_id="eid",
            jwt_key_id="kid", rsa_private_key_data="key-data"
        )
        result = await client.create_client()
        assert result is mock_sdk.return_value

    def test_get_box_client_not_initialized(self):
        client = BoxRESTClientWithJWT(
            client_id="cid", client_secret="csec", enterprise_id="eid",
            jwt_key_id="kid", rsa_private_key_data="key-data"
        )
        with pytest.raises(RuntimeError, match="not initialized"):
            client.get_box_client()


# ---------------------------------------------------------------------------
# BoxRESTClientWithOAuth2
# ---------------------------------------------------------------------------


class TestBoxRESTClientWithOAuth2:
    def test_init(self):
        client = BoxRESTClientWithOAuth2(
            client_id="cid", client_secret="csec", access_token="at"
        )
        assert client.access_token == "at"
        assert client.refresh_token is None

    def test_init_with_refresh(self):
        client = BoxRESTClientWithOAuth2(
            client_id="cid", client_secret="csec",
            access_token="at", refresh_token="rt"
        )
        assert client.refresh_token == "rt"

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxOAuth")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_create_client(self, mock_sdk, mock_auth):
        client = BoxRESTClientWithOAuth2(
            client_id="cid", client_secret="csec", access_token="at"
        )
        result = await client.create_client()
        assert result is mock_sdk.return_value

    def test_get_box_client_not_initialized(self):
        client = BoxRESTClientWithOAuth2(
            client_id="cid", client_secret="csec", access_token="at"
        )
        with pytest.raises(RuntimeError, match="not initialized"):
            client.get_box_client()


# ---------------------------------------------------------------------------
# BoxRESTClientWithOAuthCode
# ---------------------------------------------------------------------------


class TestBoxRESTClientWithOAuthCode:
    def test_init(self):
        client = BoxRESTClientWithOAuthCode(
            client_id="cid", client_secret="csec", code="auth-code"
        )
        assert client.code == "auth-code"
        assert client.access_token is None
        assert client.redirect_uri is None

    def test_init_with_redirect(self):
        client = BoxRESTClientWithOAuthCode(
            client_id="cid", client_secret="csec",
            code="auth-code", redirect_uri="http://localhost/callback"
        )
        assert client.redirect_uri == "http://localhost/callback"

    def test_get_box_client_not_initialized(self):
        client = BoxRESTClientWithOAuthCode(
            client_id="cid", client_secret="csec", code="auth-code"
        )
        with pytest.raises(RuntimeError, match="not initialized"):
            client.get_box_client()


# ---------------------------------------------------------------------------
# BoxRESTClientWithCCG
# ---------------------------------------------------------------------------


class TestBoxRESTClientWithCCG:
    def test_init(self):
        client = BoxRESTClientWithCCG(
            client_id="cid", client_secret="csec", enterprise_id="eid"
        )
        assert client.enterprise_id == "eid"
        assert client.user_id is None

    def test_init_with_user_id(self):
        client = BoxRESTClientWithCCG(
            client_id="cid", client_secret="csec",
            enterprise_id="eid", user_id="uid"
        )
        assert client.user_id == "uid"

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxSDKCCGAuth")
    @patch("app.sources.client.box.box.BoxSDKCCGConfig")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_create_client(self, mock_sdk, mock_ccg_config, mock_ccg_auth):
        client = BoxRESTClientWithCCG(
            client_id="cid", client_secret="csec", enterprise_id="eid"
        )
        result = await client.create_client()
        assert result is mock_sdk.return_value

    def test_get_box_client_not_initialized(self):
        client = BoxRESTClientWithCCG(
            client_id="cid", client_secret="csec", enterprise_id="eid"
        )
        with pytest.raises(RuntimeError, match="not initialized"):
            client.get_box_client()


# ---------------------------------------------------------------------------
# Config classes
# ---------------------------------------------------------------------------


class TestBoxTokenConfig:
    @pytest.mark.asyncio
    async def test_create_client(self):
        cfg = BoxTokenConfig(token="test-token")
        client = await cfg.create_client()
        assert isinstance(client, BoxRESTClientViaToken)

    def test_to_dict(self):
        cfg = BoxTokenConfig(token="test-token")
        d = cfg.to_dict()
        assert d["token"] == "test-token"

    def test_defaults(self):
        cfg = BoxTokenConfig(token="tok")
        assert cfg.base_url == "https://api.box.com"
        assert cfg.ssl is True


class TestBoxJWTConfig:
    @pytest.mark.asyncio
    async def test_create_client(self):
        cfg = BoxJWTConfig(
            client_id="cid", client_secret="csec", enterprise_id="eid",
            jwt_key_id="kid", rsa_private_key_data="key"
        )
        client = await cfg.create_client()
        assert isinstance(client, BoxRESTClientWithJWT)

    def test_to_dict(self):
        cfg = BoxJWTConfig(
            client_id="cid", client_secret="csec", enterprise_id="eid",
            jwt_key_id="kid", rsa_private_key_data="key"
        )
        d = cfg.to_dict()
        assert d["client_id"] == "cid"


class TestBoxOAuth2Config:
    @pytest.mark.asyncio
    async def test_create_client(self):
        cfg = BoxOAuth2Config(client_id="cid", client_secret="csec", access_token="at")
        client = await cfg.create_client()
        assert isinstance(client, BoxRESTClientWithOAuth2)

    def test_to_dict(self):
        cfg = BoxOAuth2Config(client_id="cid", client_secret="csec", access_token="at")
        d = cfg.to_dict()
        assert d["access_token"] == "at"


class TestBoxOAuthCodeConfig:
    @pytest.mark.asyncio
    async def test_create_client(self):
        cfg = BoxOAuthCodeConfig(client_id="cid", client_secret="csec", code="code")
        client = await cfg.create_client()
        assert isinstance(client, BoxRESTClientWithOAuthCode)

    def test_to_dict(self):
        cfg = BoxOAuthCodeConfig(client_id="cid", client_secret="csec", code="code")
        d = cfg.to_dict()
        assert d["code"] == "code"


class TestBoxCCGConfig:
    @pytest.mark.asyncio
    async def test_create_client(self):
        cfg = BoxCCGConfig(client_id="cid", client_secret="csec", enterprise_id="eid")
        client = await cfg.create_client()
        assert isinstance(client, BoxRESTClientWithCCG)

    def test_to_dict(self):
        cfg = BoxCCGConfig(client_id="cid", client_secret="csec", enterprise_id="eid")
        d = cfg.to_dict()
        assert d["enterprise_id"] == "eid"


# ---------------------------------------------------------------------------
# BoxClient
# ---------------------------------------------------------------------------


class TestBoxClient:
    def test_init(self):
        mock_rest = MagicMock()
        client = BoxClient(mock_rest)
        assert client.get_client() is mock_rest

    @pytest.mark.asyncio
    async def test_build_with_config_token(self):
        cfg = BoxTokenConfig(token="tok")
        client = await BoxClient.build_with_config(cfg)
        assert isinstance(client, BoxClient)

    @pytest.mark.asyncio
    @patch("app.sources.client.box.box.BoxSDKCCGAuth")
    @patch("app.sources.client.box.box.BoxSDKCCGConfig")
    @patch("app.sources.client.box.box.BoxSDKClient")
    async def test_build_with_config_ccg(self, mock_sdk, mock_ccg_cfg, mock_ccg_auth):
        cfg = BoxCCGConfig(client_id="cid", client_secret="csec", enterprise_id="eid")
        client = await BoxClient.build_with_config(cfg)
        assert isinstance(client, BoxClient)

    @pytest.mark.asyncio
    async def test_build_from_services_api_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_TOKEN", "access_token": "tok"}
            }
        )
        with patch("app.sources.client.box.box.BoxDeveloperTokenAuth"), \
             patch("app.sources.client.box.box.BoxSDKClient"):
            client = await BoxClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )
            assert isinstance(client, BoxClient)

    @pytest.mark.asyncio
    async def test_build_from_services_jwt(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "JWT",
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "enterpriseId": "eid",
                    "jwtKeyId": "kid",
                    "rsaPrivateKeyData": "key-data",
                }
            }
        )
        with patch("app.sources.client.box.box.BoxJWTAuth"), \
             patch("app.sources.client.box.box.BoxSDKClient"):
            client = await BoxClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )
            assert isinstance(client, BoxClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH",
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "credentials": {
                        "access_token": "at",
                        "refresh_token": "rt",
                    },
                }
            }
        )
        with patch("app.sources.client.box.box.BoxOAuth"), \
             patch("app.sources.client.box.box.BoxSDKClient"):
            client = await BoxClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )
            assert isinstance(client, BoxClient)

    @pytest.mark.asyncio
    async def test_build_from_services_unsupported_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "UNSUPPORTED"}}
        )
        with pytest.raises(ValueError, match="Unsupported auth_type"):
            await BoxClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_missing_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "API_TOKEN"}}
        )
        with pytest.raises(ValueError, match="access_token is required"):
            await BoxClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_jwt_missing_fields(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "JWT", "clientId": "cid"}}
        )
        with pytest.raises(ValueError, match="client_id, client_secret"):
            await BoxClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await BoxClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await BoxClient._get_connector_config(mock_config_service, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await BoxClient._get_connector_config(mock_config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError):
            await BoxClient._get_connector_config(mock_config_service, "inst-1")
