"""Resolve the frontend base URL that OAuth redirect URIs are built from."""

import logging
from urllib.parse import urlparse

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import DefaultEndpoints

logger = logging.getLogger(__name__)

ENDPOINTS_CONFIG_PATH = "/services/endpoints"


DEFAULT_FRONTEND_BASE_URL = DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")


async def _read_configured_frontend_base_url(config_service: ConfigurationService) -> str | None:
    """The admin-configured public frontend endpoint, or None when none is set."""
    try:
        endpoints = await config_service.get_config(ENDPOINTS_CONFIG_PATH, use_cache=False)
        if isinstance(endpoints, dict):
            frontend_url = (endpoints.get("frontend") or {}).get("publicEndpoint")
            if frontend_url:
                return frontend_url.rstrip("/")
    except Exception as e:
        logger.debug(f"Could not resolve configured frontend endpoint: {e}")
    return None


async def get_configured_frontend_base_url(config_service: ConfigurationService) -> str:
    return await _read_configured_frontend_base_url(config_service) or DEFAULT_FRONTEND_BASE_URL


def _web_base_url_or_none(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    return url.rstrip("/") if parsed.scheme in ("http", "https") and parsed.netloc else None


def resolve_validated_base_url(requested_base_url: str | None, configured_base_url: str) -> str:
    """The configured public frontend endpoint, whatever the client sent.

    A client `baseUrl` on another scheme+host would make the OAuth flow an open redirect, and a
    matching one adds nothing: browsers send only their origin, which drops any sub-path the
    configured endpoint has (and that the redirect URI shown to admins includes). Mismatches
    are logged so a misconfigured endpoint is visible.
    """
    if not requested_base_url:
        return configured_base_url
    try:
        requested = urlparse(requested_base_url)
        configured = urlparse(configured_base_url)
    except Exception:
        return configured_base_url
    if requested.scheme == configured.scheme and requested.netloc == configured.netloc:
        return configured_base_url
    logger.warning(
        f"Rejected client-supplied OAuth baseUrl '{requested_base_url}'; "
        f"falling back to configured '{configured_base_url}'"
    )
    return configured_base_url


async def resolve_oauth_base_url(
    config_service: ConfigurationService, requested_base_url: str | None
) -> str:
    """Base URL for OAuth redirect URIs.

    The configured public frontend endpoint wins when one is set. Without one, a deployment
    has nothing better than the browser's own http(s) origin, so that is used, as before;
    a non-web origin such as the desktop app's `app://` falls back to the default.
    """
    configured = await _read_configured_frontend_base_url(config_service)
    if configured:
        return resolve_validated_base_url(requested_base_url, configured)
    return _web_base_url_or_none(requested_base_url) or DEFAULT_FRONTEND_BASE_URL
