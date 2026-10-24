import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.service import DefaultEndpoints
from app.utils.oauth_redirect import (
    get_configured_frontend_base_url,
    resolve_oauth_base_url,
    resolve_validated_base_url,
)

CONFIGURED = "https://app.example.com"


def _config_service(return_value=None, side_effect=None) -> MagicMock:
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value=return_value, side_effect=side_effect)
    return svc


class TestResolveValidatedBaseUrl:
    def test_none_uses_configured(self) -> None:
        assert resolve_validated_base_url(None, CONFIGURED) == CONFIGURED

    def test_empty_uses_configured(self) -> None:
        assert resolve_validated_base_url("", CONFIGURED) == CONFIGURED

    def test_matching_scheme_and_host_uses_configured(self) -> None:
        assert resolve_validated_base_url("https://app.example.com/foo/", CONFIGURED) == CONFIGURED

    def test_matching_origin_keeps_configured_sub_path(self) -> None:
        # Browsers send only their origin; the redirect URI must keep the configured sub-path.
        configured = "https://app.example.com/pipeshub"
        assert resolve_validated_base_url("https://app.example.com", configured) == configured

    def test_mismatched_host_rejected(self) -> None:
        assert resolve_validated_base_url("https://evil.com", CONFIGURED) == CONFIGURED

    def test_mismatched_scheme_rejected(self) -> None:
        assert resolve_validated_base_url("http://app.example.com", CONFIGURED) == CONFIGURED

    def test_mismatched_port_rejected(self) -> None:
        assert resolve_validated_base_url("https://app.example.com:8443", CONFIGURED) == CONFIGURED

    def test_desktop_app_origin_rejected(self) -> None:
        assert resolve_validated_base_url("app://.", CONFIGURED) == CONFIGURED

    def test_malformed_url_falls_back(self) -> None:
        assert resolve_validated_base_url("not a url \x00", CONFIGURED) == CONFIGURED

    def test_rejection_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="app.utils.oauth_redirect"):
            resolve_validated_base_url("app://.", CONFIGURED)
        assert "Rejected client-supplied OAuth baseUrl 'app://.'" in caplog.text


class TestGetConfiguredFrontendBaseUrl:
    @pytest.mark.asyncio
    async def test_reads_public_endpoint(self) -> None:
        svc = _config_service({"frontend": {"publicEndpoint": "https://app.example.com/"}})
        assert await get_configured_frontend_base_url(svc) == CONFIGURED

    @pytest.mark.asyncio
    async def test_missing_endpoint_uses_default(self) -> None:
        svc = _config_service({"frontend": None})
        assert await get_configured_frontend_base_url(svc) == DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")

    @pytest.mark.asyncio
    async def test_config_error_uses_default(self) -> None:
        svc = _config_service(side_effect=RuntimeError("boom"))
        assert await get_configured_frontend_base_url(svc) == DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")


class TestResolveOauthBaseUrl:
    @pytest.mark.asyncio
    async def test_desktop_origin_resolves_to_configured(self) -> None:
        svc = _config_service({"frontend": {"publicEndpoint": CONFIGURED}})
        assert await resolve_oauth_base_url(svc, "app://.") == CONFIGURED

    @pytest.mark.asyncio
    async def test_matching_origin_is_kept(self) -> None:
        svc = _config_service({"frontend": {"publicEndpoint": CONFIGURED}})
        assert await resolve_oauth_base_url(svc, "https://app.example.com/") == CONFIGURED

    @pytest.mark.asyncio
    async def test_unconfigured_uses_the_browser_origin(self) -> None:
        # No public endpoint set: the browser's origin beats the localhost default.
        svc = _config_service({"frontend": None})
        assert await resolve_oauth_base_url(svc, "https://pipeshub.example/") == "https://pipeshub.example"

    @pytest.mark.asyncio
    async def test_unconfigured_desktop_origin_uses_default(self) -> None:
        svc = _config_service({"frontend": None})
        assert await resolve_oauth_base_url(svc, "app://.") == DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")

    @pytest.mark.asyncio
    async def test_unconfigured_without_client_url_uses_default(self) -> None:
        svc = _config_service(side_effect=RuntimeError("boom"))
        assert await resolve_oauth_base_url(svc, None) == DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")

