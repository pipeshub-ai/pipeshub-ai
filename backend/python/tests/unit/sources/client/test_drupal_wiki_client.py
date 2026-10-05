"""Unit tests for the Drupal Wiki client."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.sources.client.drupal_wiki.drupal_wiki import (
    DrupalWikiClient,
    DrupalWikiRESTClientViaToken,
    DrupalWikiTokenConfig,
    normalize_base_url,
    normalize_personal_access_token,
)

BASE_URL = "https://wiki.example.com"


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_drupal_wiki_client")


def _config_service(config: dict | None) -> AsyncMock:
    service = AsyncMock()
    service.get_config = AsyncMock(return_value=config)
    return service


class TestNormalizePersonalAccessToken:
    @pytest.mark.parametrize("raw", ["", "   ", None, "Bearer "])
    def test_empty_token_rejected(self, raw) -> None:
        with pytest.raises(ValueError, match="token is required"):
            normalize_personal_access_token(raw)


class TestNormalizeBaseUrl:
    @pytest.mark.parametrize(
        "raw",
        [
            BASE_URL,
            f"{BASE_URL}/",
            f"{BASE_URL}/api",
            f"{BASE_URL}/api/rest",
            f"{BASE_URL}/api/rest/",
            f"{BASE_URL}/api/spec",
        ],
    )
    def test_reduces_to_wiki_origin(self, raw) -> None:
        assert normalize_base_url(raw) == BASE_URL

    def test_keeps_context_path(self) -> None:
        assert normalize_base_url("https://intranet.example.com/wiki/api/rest") == "https://intranet.example.com/wiki"

    @pytest.mark.parametrize("raw", ["", "wiki.example.com", "ftp://wiki.example.com"])
    def test_invalid_url_rejected(self, raw) -> None:
        with pytest.raises(ValueError):
            normalize_base_url(raw)


class TestDrupalWikiRESTClientViaToken:
    def test_headers(self) -> None:
        client = DrupalWikiRESTClientViaToken(f"{BASE_URL}/api/rest", "secret")
        assert client.headers["Authorization"] == "Bearer secret"
        assert client.headers["X-API-Version"] == "1"
        assert client.headers["Accept"] == "application/json"
        # Content-Type is per request: JSON for bodies, multipart for uploads.
        assert "Content-Type" not in client.headers
        assert client.get_base_url() == BASE_URL
        assert client.get_token() == "secret"

class TestDrupalWikiClient:
    def test_build_with_config(self) -> None:
        client = DrupalWikiClient.build_with_config(DrupalWikiTokenConfig(base_url=BASE_URL, token="pat:t"))
        assert isinstance(client.get_client(), DrupalWikiRESTClientViaToken)
        assert client.get_base_url() == BASE_URL

    def test_config_to_dict(self) -> None:
        assert DrupalWikiTokenConfig(base_url=BASE_URL, token="t").to_dict() == {
            "base_url": BASE_URL,
            "token": "t",
            "timeout": 30.0,
        }

    @pytest.mark.asyncio
    async def test_build_from_services_api_token(self, logger) -> None:
        service = _config_service({"auth": {"authType": "API_TOKEN", "baseUrl": f"{BASE_URL}/", "apiToken": "tok"}})
        client = await DrupalWikiClient.build_from_services(logger, service, "conn-1")
        service.get_config.assert_awaited_once_with("/services/connectors/conn-1/config")
        assert client.get_base_url() == BASE_URL
        assert client.get_client().headers["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_auth_type_defaults_to_api_token(self, logger) -> None:
        service = _config_service({"auth": {"baseUrl": BASE_URL, "apiToken": "tok"}})
        client = await DrupalWikiClient.build_from_services(logger, service, "conn-1")
        assert client.get_client().get_token() == "tok"

    @pytest.mark.asyncio
    async def test_resilience_is_passed_to_http_client(self, logger) -> None:
        policy = object()
        service = _config_service({"auth": {"baseUrl": BASE_URL, "apiToken": "tok"}})
        client = await DrupalWikiClient.build_from_services(logger, service, "conn-1", resilience=policy)
        assert client.get_client().resilience is policy

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("config", "message"),
        [
            ({"auth": {"authType": "OAUTH", "baseUrl": BASE_URL, "apiToken": "t"}}, "Unsupported Drupal Wiki auth type"),
            ({"auth": {"authType": "BASIC_AUTH", "baseUrl": BASE_URL}}, "Unsupported Drupal Wiki auth type"),
            ({"auth": {"apiToken": "t"}}, "Base URL is required"),
            ({"auth": {"baseUrl": BASE_URL}}, "Personal access token is required"),
            ({"auth": {}}, "Auth configuration not found"),
            ({"auth": None}, "Auth configuration not found"),
        ],
    )
    async def test_build_from_services_rejects_bad_config(self, logger, config, message) -> None:
        with pytest.raises(ValueError, match=message):
            await DrupalWikiClient.build_from_services(logger, _config_service(config), "conn-1")

    @pytest.mark.asyncio
    async def test_missing_config_raises(self, logger) -> None:
        with pytest.raises(ValueError, match="Failed to get Drupal Wiki connector configuration"):
            await DrupalWikiClient.build_from_services(logger, _config_service(None), "conn-1")

    @pytest.mark.asyncio
    async def test_config_service_error_raises_value_error(self, logger) -> None:
        service = AsyncMock()
        service.get_config = AsyncMock(side_effect=RuntimeError("kv down"))
        with pytest.raises(ValueError, match="Failed to get Drupal Wiki connector configuration"):
            await DrupalWikiClient.build_from_services(logger, service, "conn-1")


def _toolset_config(**overrides: object) -> dict:
    config = {
        "isAuthenticated": True,
        "authType": "API_TOKEN",
        "instanceId": "inst-1",
        "orgId": "org-1",
        "auth": {"apiToken": "user-token"},
    }
    config.update(overrides)
    return config


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_uses_instance_base_url_and_user_token(self, logger: logging.Logger) -> None:
        instance = {"_id": "inst-1", "auth": {"baseUrl": f"{BASE_URL}/api/rest"}}
        lookup = AsyncMock(return_value=instance)
        service = AsyncMock()
        with patch("app.edition_config.get_toolset_by_id", lookup):
            client = await DrupalWikiClient.build_from_toolset(_toolset_config(), logger, service)

        lookup.assert_awaited_once_with("inst-1", service, org_id="org-1")
        assert client.get_base_url() == BASE_URL
        assert client.get_client().headers["Authorization"] == "Bearer user-token"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("config", "instance", "message"),
        [
            (_toolset_config(authType="OAUTH"), {"auth": {"baseUrl": BASE_URL}}, "Unsupported Drupal Wiki auth type"),
            (_toolset_config(instanceId=None), {"auth": {"baseUrl": BASE_URL}}, "instanceId is required"),
            (_toolset_config(), None, "instance 'inst-1' not found"),
            (_toolset_config(), {"auth": {}}, "Admin must configure the Drupal Wiki URL"),
            (_toolset_config(auth={}), {"auth": {"baseUrl": BASE_URL}}, "Personal access token is required"),
        ],
    )
    async def test_rejects_bad_config(
        self, logger: logging.Logger, config: dict, instance: dict | None, message: str
    ) -> None:
        with patch("app.edition_config.get_toolset_by_id", AsyncMock(return_value=instance)):
            with pytest.raises(ValueError, match=message):
                await DrupalWikiClient.build_from_toolset(config, logger, AsyncMock())

    @pytest.mark.asyncio
    async def test_requires_authenticated_toolset(self, logger: logging.Logger) -> None:
        with pytest.raises(ValueError, match="not authenticated"):
            await DrupalWikiClient.build_from_toolset(_toolset_config(isAuthenticated=False), logger, AsyncMock())

    @pytest.mark.asyncio
    async def test_requires_config_service(self, logger: logging.Logger) -> None:
        with pytest.raises(ValueError, match="config_service is required"):
            await DrupalWikiClient.build_from_toolset(_toolset_config(), logger, None)
