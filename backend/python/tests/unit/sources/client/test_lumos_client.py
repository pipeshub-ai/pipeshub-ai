"""Unit tests for the Lumos client module.

Covers:
- LumosRESTClientViaToken / LumosRESTClientViaApiKey constructors and accessors
- LumosTokenConfig / LumosApiKeyConfig dataclass behaviour
- LumosClient.build_with_config
- LumosClient.build_from_services (every auth-type branch)
- LumosClient.build_from_toolset (every auth-type branch + missing-credential paths)
- LumosClient._get_connector_config error paths
"""

import logging
from unittest.mock import AsyncMock

import pytest

from app.sources.client.lumos.lumos import (
    LUMOS_BASE_URL,
    LumosApiKeyConfig,
    LumosClient,
    LumosRESTClientViaApiKey,
    LumosRESTClientViaToken,
    LumosTokenConfig,
)


@pytest.fixture
def logger():
    return logging.getLogger("test_lumos_client")


# ---------------------------------------------------------------------------
# LumosRESTClientViaToken
# ---------------------------------------------------------------------------


class TestLumosRESTClientViaToken:
    def test_init_default_base_url(self):
        client = LumosRESTClientViaToken("tok-1")
        assert client.base_url == LUMOS_BASE_URL
        assert client.headers["Authorization"] == "Bearer tok-1"

    def test_init_custom_base_url(self):
        client = LumosRESTClientViaToken("tok-1", base_url="https://custom.lumos.test")
        assert client.base_url == "https://custom.lumos.test"

    def test_init_custom_token_type(self):
        client = LumosRESTClientViaToken("tok-1", token_type="Token")
        assert client.headers["Authorization"] == "Token tok-1"

    def test_init_empty_token_raises(self):
        with pytest.raises(ValueError, match="Lumos token cannot be empty"):
            LumosRESTClientViaToken("")

    def test_get_base_url(self):
        client = LumosRESTClientViaToken("tok-1", base_url="https://x.test")
        assert client.get_base_url() == "https://x.test"

    def test_get_token_strips_bearer_prefix(self):
        client = LumosRESTClientViaToken("tok-abc")
        assert client.get_token() == "tok-abc"

    def test_set_token_replaces_header(self):
        client = LumosRESTClientViaToken("tok-1")
        client.set_token("tok-2")
        assert client.get_token() == "tok-2"
        assert client.headers["Authorization"] == "Bearer tok-2"


# ---------------------------------------------------------------------------
# LumosRESTClientViaApiKey
# ---------------------------------------------------------------------------


class TestLumosRESTClientViaApiKey:
    def test_init_default_base_url(self):
        client = LumosRESTClientViaApiKey("api-key-1")
        assert client.base_url == LUMOS_BASE_URL
        assert client.headers["Authorization"] == "Bearer api-key-1"

    def test_init_custom_base_url(self):
        client = LumosRESTClientViaApiKey("api-1", base_url="https://eu.lumos.test")
        assert client.base_url == "https://eu.lumos.test"

    def test_init_empty_api_key_raises(self):
        with pytest.raises(ValueError, match="Lumos API key cannot be empty"):
            LumosRESTClientViaApiKey("")

    def test_get_base_url(self):
        client = LumosRESTClientViaApiKey("api-1", base_url="https://x.test")
        assert client.get_base_url() == "https://x.test"


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


class TestLumosTokenConfig:
    def test_create_client_returns_token_client(self):
        cfg = LumosTokenConfig(token="t-1")
        client = cfg.create_client()
        assert isinstance(client, LumosRESTClientViaToken)
        assert client.get_token() == "t-1"
        assert client.base_url == LUMOS_BASE_URL

    def test_create_client_passes_custom_base_url(self):
        cfg = LumosTokenConfig(token="t-1", base_url="https://eu.lumos.test")
        client = cfg.create_client()
        assert client.base_url == "https://eu.lumos.test"

    def test_to_dict(self):
        cfg = LumosTokenConfig(token="t-1", base_url="https://x.test")
        d = cfg.to_dict()
        assert d == {"token": "t-1", "base_url": "https://x.test"}


class TestLumosApiKeyConfig:
    def test_create_client_returns_api_key_client(self):
        cfg = LumosApiKeyConfig(api_key="k-1")
        client = cfg.create_client()
        assert isinstance(client, LumosRESTClientViaApiKey)
        assert client.headers["Authorization"] == "Bearer k-1"

    def test_to_dict(self):
        cfg = LumosApiKeyConfig(api_key="k-1", base_url="https://x.test")
        d = cfg.to_dict()
        assert d == {"api_key": "k-1", "base_url": "https://x.test"}


# ---------------------------------------------------------------------------
# LumosClient — builder methods
# ---------------------------------------------------------------------------


class TestLumosClientBasic:
    def test_init_stores_client(self):
        inner = LumosRESTClientViaToken("t-1")
        wrapper = LumosClient(inner)
        assert wrapper.get_client() is inner

    def test_build_with_token_config(self):
        cfg = LumosTokenConfig(token="t-1")
        wrapper = LumosClient.build_with_config(cfg)
        assert isinstance(wrapper, LumosClient)
        assert isinstance(wrapper.get_client(), LumosRESTClientViaToken)

    def test_build_with_api_key_config(self):
        cfg = LumosApiKeyConfig(api_key="k-1")
        wrapper = LumosClient.build_with_config(cfg)
        assert isinstance(wrapper.get_client(), LumosRESTClientViaApiKey)


# ---------------------------------------------------------------------------
# LumosClient.build_from_services
# ---------------------------------------------------------------------------


class TestBuildFromServices:
    @pytest.mark.asyncio
    async def test_oauth_with_access_token(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {"access_token": "oauth-tok"},
        })
        wrapper = await LumosClient.build_from_services(logger, config_service, "inst-1")
        assert isinstance(wrapper.get_client(), LumosRESTClientViaToken)
        assert wrapper.get_client().get_token() == "oauth-tok"

    @pytest.mark.asyncio
    async def test_bearer_token_falls_back_to_auth_bearer_token_field(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "BEARER_TOKEN", "bearerToken": "bearer-tok"},
            "credentials": {},
        })
        wrapper = await LumosClient.build_from_services(logger, config_service, "inst-1")
        assert wrapper.get_client().get_token() == "bearer-tok"

    @pytest.mark.asyncio
    async def test_oauth_with_no_token_raises(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH"},
            "credentials": {},
        })
        with pytest.raises(ValueError, match="Token required"):
            await LumosClient.build_from_services(logger, config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_api_key_from_auth_block(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_KEY", "apiKey": "key-from-auth"},
            "credentials": {},
        })
        wrapper = await LumosClient.build_from_services(logger, config_service, "inst-1")
        assert isinstance(wrapper.get_client(), LumosRESTClientViaApiKey)
        assert wrapper.get_client().headers["Authorization"] == "Bearer key-from-auth"

    @pytest.mark.asyncio
    async def test_api_key_falls_back_to_credentials_block(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_KEY"},
            "credentials": {"api_key": "key-from-creds"},
        })
        wrapper = await LumosClient.build_from_services(logger, config_service, "inst-1")
        assert wrapper.get_client().headers["Authorization"] == "Bearer key-from-creds"

    @pytest.mark.asyncio
    async def test_api_key_default_when_no_auth_type_in_config(self, logger):
        """Auth type defaults to API_KEY if not specified."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"apiKey": "k-default"},
            "credentials": {},
        })
        wrapper = await LumosClient.build_from_services(logger, config_service, "inst-1")
        assert isinstance(wrapper.get_client(), LumosRESTClientViaApiKey)

    @pytest.mark.asyncio
    async def test_api_key_missing_raises(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "API_KEY"},
            "credentials": {},
        })
        with pytest.raises(ValueError, match="API key required"):
            await LumosClient.build_from_services(logger, config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_invalid_auth_type_raises(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "MAGIC_LINK"},
            "credentials": {},
        })
        with pytest.raises(ValueError, match="Invalid auth type: MAGIC_LINK"):
            await LumosClient.build_from_services(logger, config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_config_retrieval_failure_propagates(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with pytest.raises(ValueError, match="Failed to get Lumos connector configuration"):
            await LumosClient.build_from_services(logger, config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_config_with_none_auth_and_credentials(self, logger):
        """auth and credentials being None should be coerced to {} via the `or {}`."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "auth": None,
            "credentials": None,
        })
        # Default auth_type = API_KEY, no apiKey present → ValueError
        with pytest.raises(ValueError, match="API key required"):
            await LumosClient.build_from_services(logger, config_service, "inst-1")


# ---------------------------------------------------------------------------
# LumosClient.build_from_toolset
# ---------------------------------------------------------------------------


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_empty_toolset_raises(self, logger):
        with pytest.raises(ValueError, match="Toolset config is required"):
            await LumosClient.build_from_toolset({}, logger)

    @pytest.mark.asyncio
    async def test_oauth_happy_path(self, logger):
        wrapper = await LumosClient.build_from_toolset(
            {"authType": "OAUTH", "credentials": {"access_token": "oauth-tok"}},
            logger,
        )
        assert isinstance(wrapper.get_client(), LumosRESTClientViaToken)
        assert wrapper.get_client().get_token() == "oauth-tok"

    @pytest.mark.asyncio
    async def test_oauth_lowercase_normalized(self, logger):
        """authType is uppercased via .upper() in the implementation."""
        wrapper = await LumosClient.build_from_toolset(
            {"authType": "oauth", "credentials": {"access_token": "tok-1"}},
            logger,
        )
        assert wrapper.get_client().get_token() == "tok-1"

    @pytest.mark.asyncio
    async def test_oauth_missing_token_raises(self, logger):
        with pytest.raises(ValueError, match="Access token not found"):
            await LumosClient.build_from_toolset(
                {"authType": "OAUTH", "credentials": {}}, logger
            )

    @pytest.mark.asyncio
    async def test_api_key_from_top_level_snake_case(self, logger):
        wrapper = await LumosClient.build_from_toolset(
            {"authType": "API_KEY", "api_key": "k-snake"},
            logger,
        )
        assert isinstance(wrapper.get_client(), LumosRESTClientViaApiKey)

    @pytest.mark.asyncio
    async def test_api_key_from_top_level_camel_case(self, logger):
        wrapper = await LumosClient.build_from_toolset(
            {"authType": "API_KEY", "apiKey": "k-camel"},
            logger,
        )
        assert isinstance(wrapper.get_client(), LumosRESTClientViaApiKey)

    @pytest.mark.asyncio
    async def test_api_key_falls_back_to_credentials_block(self, logger):
        wrapper = await LumosClient.build_from_toolset(
            {"authType": "API_KEY", "credentials": {"api_key": "k-creds"}},
            logger,
        )
        assert isinstance(wrapper.get_client(), LumosRESTClientViaApiKey)

    @pytest.mark.asyncio
    async def test_api_key_missing_raises(self, logger):
        with pytest.raises(ValueError, match="API key required"):
            await LumosClient.build_from_toolset({"authType": "API_KEY"}, logger)

    @pytest.mark.asyncio
    async def test_api_token_from_snake_case(self, logger):
        wrapper = await LumosClient.build_from_toolset(
            {"authType": "API_TOKEN", "api_token": "t-snake"},
            logger,
        )
        assert isinstance(wrapper.get_client(), LumosRESTClientViaToken)
        assert wrapper.get_client().get_token() == "t-snake"

    @pytest.mark.asyncio
    async def test_api_token_from_camel_case(self, logger):
        wrapper = await LumosClient.build_from_toolset(
            {"authType": "API_TOKEN", "apiToken": "t-camel"},
            logger,
        )
        assert wrapper.get_client().get_token() == "t-camel"

    @pytest.mark.asyncio
    async def test_api_token_missing_raises(self, logger):
        with pytest.raises(ValueError, match="API token required"):
            await LumosClient.build_from_toolset({"authType": "API_TOKEN"}, logger)

    @pytest.mark.asyncio
    async def test_unsupported_auth_type_raises(self, logger):
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await LumosClient.build_from_toolset(
                {"authType": "PASSWORD"}, logger
            )

    @pytest.mark.asyncio
    async def test_missing_auth_type_raises(self, logger):
        """Missing authType becomes empty string after .upper() — falls through to unsupported."""
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await LumosClient.build_from_toolset({"some": "thing"}, logger)


# ---------------------------------------------------------------------------
# LumosClient._get_connector_config
# ---------------------------------------------------------------------------


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_config_dict_on_success(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"auth": {}, "credentials": {}})
        result = await LumosClient._get_connector_config(logger, config_service, "inst-1")
        assert result == {"auth": {}, "credentials": {}}
        config_service.get_config.assert_awaited_once_with(
            "/services/connectors/inst-1/config"
        )

    @pytest.mark.asyncio
    async def test_empty_config_raises(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Lumos connector configuration"):
            await LumosClient._get_connector_config(logger, config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_non_dict_config_raises(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not-a-dict")
        with pytest.raises(ValueError, match="Failed to get Lumos connector configuration"):
            await LumosClient._get_connector_config(logger, config_service, "inst-1")

    @pytest.mark.asyncio
    async def test_get_config_exception_wrapped_as_value_error(self, logger):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd unreachable"))
        with pytest.raises(ValueError, match="Failed to get Lumos connector configuration"):
            await LumosClient._get_connector_config(logger, config_service, "inst-1")
