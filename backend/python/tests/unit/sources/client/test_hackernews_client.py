"""Unit tests for HackerNews client module."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.client.hackernews.hackernews import (
    DEFAULT_BASE_URL,
    HackerNewsClient,
    HackerNewsConfig,
    HackerNewsResponse,
    HackerNewsRESTClient,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_hackernews_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


CUSTOM_BASE_URL = "https://hn.example.com/v0"


# ---------------------------------------------------------------------------
# HackerNewsResponse
# ---------------------------------------------------------------------------


class TestHackerNewsResponse:
    def test_success(self):
        resp = HackerNewsResponse(success=True, data={"id": 1, "title": "Hello"})
        assert resp.success is True

    def test_error(self):
        resp = HackerNewsResponse(success=False, error="oops")
        assert resp.error == "oops"

    def test_to_dict(self):
        resp = HackerNewsResponse(success=True, data=[1, 2, 3])
        d = resp.to_dict()
        assert d["success"] is True
        assert d["data"] == [1, 2, 3]

    def test_to_json(self):
        resp = HackerNewsResponse(success=True)
        j = resp.to_json()
        assert "true" in j


# ---------------------------------------------------------------------------
# HackerNewsRESTClient
# ---------------------------------------------------------------------------


class TestHackerNewsRESTClient:
    def test_default_base_url(self):
        client = HackerNewsRESTClient()
        assert client.base_url == DEFAULT_BASE_URL

    def test_custom_base_url(self):
        client = HackerNewsRESTClient(CUSTOM_BASE_URL)
        assert client.base_url == CUSTOM_BASE_URL

    def test_trailing_slash_stripped(self):
        client = HackerNewsRESTClient(f"{CUSTOM_BASE_URL}/")
        assert client.base_url == CUSTOM_BASE_URL

    def test_get_base_url(self):
        client = HackerNewsRESTClient(CUSTOM_BASE_URL)
        assert client.get_base_url() == CUSTOM_BASE_URL

    def test_no_authorization_header(self):
        """HackerNews needs no auth — the base HTTPClient's Authorization
        header must be dropped, not sent empty."""
        client = HackerNewsRESTClient()
        assert "Authorization" not in client.headers


# ---------------------------------------------------------------------------
# HackerNewsConfig
# ---------------------------------------------------------------------------


class TestHackerNewsConfig:
    def test_default_base_url(self):
        cfg = HackerNewsConfig()
        assert cfg.base_url == DEFAULT_BASE_URL

    def test_create_client(self):
        cfg = HackerNewsConfig(base_url=CUSTOM_BASE_URL)
        client = cfg.create_client()
        assert isinstance(client, HackerNewsRESTClient)
        assert client.base_url == CUSTOM_BASE_URL

    def test_to_dict(self):
        cfg = HackerNewsConfig(base_url=CUSTOM_BASE_URL)
        d = cfg.to_dict()
        assert d["base_url"] == CUSTOM_BASE_URL


# ---------------------------------------------------------------------------
# HackerNewsClient
# ---------------------------------------------------------------------------


class TestHackerNewsClient:
    def test_init(self):
        rest = HackerNewsRESTClient()
        client = HackerNewsClient(rest)
        assert client.get_client() is rest

    def test_get_base_url(self):
        rest = HackerNewsRESTClient(CUSTOM_BASE_URL)
        client = HackerNewsClient(rest)
        assert client.get_base_url() == CUSTOM_BASE_URL

    def test_build_with_config_default(self):
        client = HackerNewsClient.build_with_config()
        assert isinstance(client, HackerNewsClient)
        assert client.get_base_url() == DEFAULT_BASE_URL

    def test_build_with_config_custom(self):
        cfg = HackerNewsConfig(base_url=CUSTOM_BASE_URL)
        client = HackerNewsClient.build_with_config(cfg)
        assert client.get_base_url() == CUSTOM_BASE_URL

    @pytest.mark.asyncio
    async def test_build_and_validate_success(self):
        mock_response = MagicMock()
        mock_response.json.return_value = 49415609

        original = HackerNewsClient.build_with_config

        def mock_build(config=None):
            result = original(config)
            inner = result.get_client()
            inner.execute = AsyncMock(return_value=mock_response)
            return result

        with pytest.MonkeyPatch.context() as m:
            m.setattr(HackerNewsClient, "build_with_config", staticmethod(mock_build))
            client = await HackerNewsClient.build_and_validate()
            assert isinstance(client, HackerNewsClient)

    @pytest.mark.asyncio
    async def test_build_and_validate_unexpected_shape(self):
        """A non-integer response means something is not actually the
        HackerNews API — retrying would never fix this, so this must
        raise rather than silently return a client."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "not found"}

        original = HackerNewsClient.build_with_config

        def mock_build(config=None):
            result = original(config)
            inner = result.get_client()
            inner.execute = AsyncMock(return_value=mock_response)
            return result

        with pytest.MonkeyPatch.context() as m:
            m.setattr(HackerNewsClient, "build_with_config", staticmethod(mock_build))
            with pytest.raises(ValueError, match="validation failed"):
                await HackerNewsClient.build_and_validate()

    @pytest.mark.asyncio
    async def test_build_and_validate_connection_error(self):
        original = HackerNewsClient.build_with_config

        def mock_build(config=None):
            result = original(config)
            inner = result.get_client()
            inner.execute = AsyncMock(side_effect=ConnectionError("no connection"))
            return result

        with pytest.MonkeyPatch.context() as m:
            m.setattr(HackerNewsClient, "build_with_config", staticmethod(mock_build))
            with pytest.raises(ValueError, match="Failed to connect"):
                await HackerNewsClient.build_and_validate()

    @pytest.mark.asyncio
    async def test_build_from_services_with_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"baseURL": CUSTOM_BASE_URL}
        )
        client = await HackerNewsClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, HackerNewsClient)
        assert client.get_base_url() == CUSTOM_BASE_URL

    @pytest.mark.asyncio
    async def test_build_from_services_no_config_falls_back_to_default(self, logger, mock_config_service):
        """Unlike sources that need credentials, HackerNews has no reason to
        raise when there is no stored config — it should just use the
        public default endpoint."""
        mock_config_service.get_config = AsyncMock(return_value=None)
        client = await HackerNewsClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, HackerNewsClient)
        assert client.get_base_url() == DEFAULT_BASE_URL

    @pytest.mark.asyncio
    async def test_get_connector_config_exception_returns_none(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        result = await HackerNewsClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result is None
