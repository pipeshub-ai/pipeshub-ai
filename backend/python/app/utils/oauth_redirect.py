"""Resolve the frontend base URL that OAuth redirect URIs are built from."""

import logging
from urllib.parse import urlparse

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import DefaultEndpoints

logger = logging.getLogger(__name__)

ENDPOINTS_CONFIG_PATH = "/services/endpoints"


async def get_configured_frontend_base_url(config_service: ConfigurationService) -> str:
    try:
        endpoints = await config_service.get_config(ENDPOINTS_CONFIG_PATH, use_cache=False)
        if isinstance(endpoints, dict):
            frontend_url = (endpoints.get("frontend") or {}).get("publicEndpoint")
            if frontend_url:
                return frontend_url.rstrip("/")
    except Exception as e:
        logger.debug(f"Could not resolve configured frontend endpoint: {e}")
    return DefaultEndpoints.FRONTEND_ENDPOINT.value.rstrip("/")


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
    configured = await get_configured_frontend_base_url(config_service)
    return resolve_validated_base_url(requested_base_url, configured)
