"""Unit tests for BookStack client module."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.client.bookstack.bookstack import (
    BookStackClient,
    BookStackRESTClientViaToken,
    BookStackResponse,
    BookStackTokenConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_bookstack_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


BASE_URL = "https://bookstack.example.com"


# ---------------------------------------------------------------------------
# BookStackResponse
# ---------------------------------------------------------------------------


class TestBookStackResponse:
    def test_success(self):
        resp = BookStackResponse(success=True, data={"key": "val"})
        assert resp.success is True

    def test_error(self):
        resp = BookStackResponse(success=False, error="oops")
        assert resp.error == "oops"

    def test_to_dict(self):
        resp = BookStackResponse(success=True, data={"k": "v"})
        d = resp.to_dict()
        assert d["success"] is True

    def test_to_json(self):
        resp = BookStackResponse(success=True)
        j = resp.to_json()
        assert "true" in j


# ---------------------------------------------------------------------------
# BookStackRESTClientViaToken
# ---------------------------------------------------------------------------


class TestBookStackRESTClientViaToken:
    def test_init(self):
        client = BookStackRESTClientViaToken(BASE_URL, "token-id", "token-secret")
        assert client.base_url == BASE_URL
        assert client.token_id == "token-id"
        assert client.token_secret == "token-secret"
        assert client.headers["Authorization"] == "Token token-id:token-secret"

    def test_trailing_slash_stripped(self):
        client = BookStackRESTClientViaToken(f"{BASE_URL}/", "tid", "tsec")
        assert client.base_url == BASE_URL

    def test_get_base_url(self):
        client = BookStackRESTClientViaToken(BASE_URL, "tid", "tsec")
        assert client.get_base_url() == BASE_URL

    def test_content_type_header(self):
        client = BookStackRESTClientViaToken(BASE_URL, "tid", "tsec")
        assert client.headers["Content-Type"] == "application/json"
        assert client.headers["Accept"] == "application/json"


# ---------------------------------------------------------------------------
# BookStackTokenConfig
# ---------------------------------------------------------------------------


class TestBookStackTokenConfig:
    def test_create_client(self):
        cfg = BookStackTokenConfig(base_url=BASE_URL, token_id="tid", token_secret="tsec")
        client = cfg.create_client()
        assert isinstance(client, BookStackRESTClientViaToken)

    def test_to_dict(self):
        cfg = BookStackTokenConfig(base_url=BASE_URL, token_id="tid", token_secret="tsec")
        d = cfg.to_dict()
        assert d["base_url"] == BASE_URL
        assert d["token_id"] == "tid"
        assert d["ssl"] is True

    def test_defaults(self):
        cfg = BookStackTokenConfig(base_url=BASE_URL, token_id="tid", token_secret="tsec")
        assert cfg.ssl is True


# ---------------------------------------------------------------------------
# BookStackClient
# ---------------------------------------------------------------------------


class TestBookStackClient:
    def test_init(self):
        rest = BookStackRESTClientViaToken(BASE_URL, "tid", "tsec")
        client = BookStackClient(rest)
        assert client.get_client() is rest

    def test_get_base_url(self):
        rest = BookStackRESTClientViaToken(BASE_URL, "tid", "tsec")
        client = BookStackClient(rest)
        assert client.get_base_url() == BASE_URL

    def test_build_with_config(self):
        cfg = BookStackTokenConfig(base_url=BASE_URL, token_id="tid", token_secret="tsec")
        client = BookStackClient.build_with_config(cfg)
        assert isinstance(client, BookStackClient)

    @pytest.mark.asyncio
    async def test_build_from_services_api_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "API_TOKEN",
                    "tokenId": "tid",
                    "tokenSecret": "tsec",
                    "baseURL": BASE_URL,
                }
            }
        )
        client = await BookStackClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, BookStackClient)

    @pytest.mark.asyncio
    async def test_build_from_services_unsupported_auth(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={"auth": {"authType": "UNSUPPORTED"}}
        )
        with pytest.raises(ValueError, match="Unsupported auth type"):
            await BookStackClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await BookStackClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_empty_config(self, logger, mock_config_service):
        """Empty dict is falsy in Python, so _get_connector_config raises."""
        mock_config_service.get_config = AsyncMock(return_value={})
        with pytest.raises(ValueError, match="Failed to get"):
            await BookStackClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_and_validate_success(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": []}

        mock_http_client = MagicMock()
        mock_http_client.execute = AsyncMock(return_value=mock_response)
        mock_http_client.headers = {"Authorization": "Token tid:tsec"}
        mock_http_client.get_base_url.return_value = BASE_URL

        cfg = BookStackTokenConfig(base_url=BASE_URL, token_id="tid", token_secret="tsec")

        with MagicMock() as mock_client:
            mock_client.get_client.return_value = mock_http_client
            # Patch the build_with_config to return our mock
            original = BookStackClient.build_with_config

            def mock_build(c):
                result = original(c)
                # Override the inner client's execute
                inner = result.get_client()
                inner.execute = AsyncMock(return_value=mock_response)
                return result

            with pytest.MonkeyPatch.context() as m:
                m.setattr(BookStackClient, "build_with_config", staticmethod(mock_build))
                client = await BookStackClient.build_and_validate(cfg)
                assert isinstance(client, BookStackClient)

    @pytest.mark.asyncio
    async def test_build_and_validate_error_response(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": {"message": "Unauthorized"}}

        cfg = BookStackTokenConfig(base_url=BASE_URL, token_id="tid", token_secret="tsec")

        original = BookStackClient.build_with_config

        def mock_build(c):
            result = original(c)
            inner = result.get_client()
            inner.execute = AsyncMock(return_value=mock_response)
            return result

        with pytest.MonkeyPatch.context() as m:
            m.setattr(BookStackClient, "build_with_config", staticmethod(mock_build))
            with pytest.raises(ValueError, match="token validation failed"):
                await BookStackClient.build_and_validate(cfg)

    @pytest.mark.asyncio
    async def test_build_and_validate_connection_error(self):
        cfg = BookStackTokenConfig(base_url=BASE_URL, token_id="tid", token_secret="tsec")

        original = BookStackClient.build_with_config

        def mock_build(c):
            result = original(c)
            inner = result.get_client()
            inner.execute = AsyncMock(side_effect=ConnectionError("no connection"))
            return result

        with pytest.MonkeyPatch.context() as m:
            m.setattr(BookStackClient, "build_with_config", staticmethod(mock_build))
            with pytest.raises(ValueError, match="Failed to connect"):
                await BookStackClient.build_and_validate(cfg)

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await BookStackClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await BookStackClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError):
            await BookStackClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )
