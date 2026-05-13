from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.oauth_service import OAuthConfig


def get_oauth_config(auth_config: dict) -> OAuthConfig:
    # Derive authorize/token URLs from instanceUrl when not explicitly set.
    # This allows self-managed connectors (e.g. GitLab EE) to work without
    # requiring the caller to pre-compute OAuth endpoint URLs.
    instance_url = auth_config.get("instanceUrl", "").rstrip("/")
    authorize_url = auth_config.get("authorizeUrl") or (
        f"{instance_url}/oauth/authorize" if instance_url else ""
    )
    token_url = auth_config.get("tokenUrl") or (
        f"{instance_url}/oauth/token" if instance_url else ""
    )

    oauth_config = OAuthConfig(
            client_id=auth_config['clientId'],
            client_secret=auth_config['clientSecret'],
            redirect_uri=auth_config.get('redirectUri', ''),
            authorize_url=authorize_url,
            token_url=token_url,
            scope=' '.join(auth_config.get('scopes', [])) if auth_config.get('scopes') else ''
        )

    if auth_config.get('tokenAccessType'):
        oauth_config.token_access_type = auth_config.get('tokenAccessType')
    if auth_config.get('additionalParams'):
        oauth_config.additional_params = auth_config.get('additionalParams')
    else:
        oauth_config.additional_params = {}

    # Add scope_parameter_name support (defaults to "scope" if not provided)
    if auth_config.get('scopeParameterName'):
        oauth_config.scope_parameter_name = auth_config.get('scopeParameterName')

    # Add token_response_path support (optional, for providers with nested token responses)
    if auth_config.get('tokenResponsePath'):
        oauth_config.token_response_path = auth_config.get('tokenResponsePath')

    # Check if this is Notion OAuth (by checking the token URL)
    # Notion requires Basic Auth with JSON body
    # Use the effective token_url (already computed above, which falls back via instanceUrl)
    if token_url:
        try:
            parsed_url = urlparse(token_url)
            hostname = parsed_url.hostname or ''
            hostname_lower = hostname.lower()
            # Check if hostname is exactly notion.com or ends with .notion.com (for subdomains)
            # This prevents matching malicious domains like evilnotion.com or notion.com.evil.com
            if hostname_lower == 'notion.com' or hostname_lower.endswith('.notion.com'):
                oauth_config.additional_params["use_basic_auth"] = True
                oauth_config.additional_params["use_json_body"] = True
                oauth_config.additional_params["notion_version"] = "2025-09-03"
        except Exception:
            # If URL parsing fails, skip the Notion-specific configuration
            pass

    return oauth_config


async def fetch_toolset_oauth_config_by_id(
    oauth_config_id: str,
    toolset_type: str,
    config_service: ConfigurationService,
    logger=None,
) -> Optional[Dict[str, Any]]:
    """Fetch a toolset OAuth configuration by ID from the config service.

    Toolset OAuth configs live at /services/oauths/toolsets/{toolsetType} (distinct
    from connector OAuth configs at /services/oauth/{connectorType}). This mirrors
    ``fetch_oauth_config_by_id`` but reads from the toolset path.
    """
    if not oauth_config_id or not toolset_type:
        if logger:
            logger.warning("oauth_config_id and toolset_type are required to fetch toolset OAuth config")
        return None

    try:
        from app.agents.constants.toolset_constants import get_toolset_oauth_config_path

        oauth_config_path = get_toolset_oauth_config_path(toolset_type)
        oauth_configs = await config_service.get_config(oauth_config_path, default=[])

        if not isinstance(oauth_configs, list):
            if logger:
                logger.warning(f"Toolset OAuth configs at {oauth_config_path} is not a list")
            return None

        for oauth_cfg in oauth_configs:
            if oauth_cfg.get("_id") == oauth_config_id:
                return oauth_cfg

        if logger:
            logger.warning("Requested toolset OAuth config was not found")
        return None

    except Exception:
        if logger:
            logger.error("Error fetching toolset OAuth config")
        return None


async def fetch_oauth_config_by_id(
    oauth_config_id: str,
    connector_type: str,
    config_service: ConfigurationService,
    logger=None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch an OAuth configuration by ID from the config service.

    This utility function retrieves an OAuth config from the etcd storage
    using the oauth_config_id and connector_type to construct the path.

    Args:
        oauth_config_id: The ID of the OAuth config to fetch
        connector_type: The type of connector (e.g., "DROPBOX_PERSONAL", "GOOGLE_DRIVE")
        config_service: The configuration service instance to use for fetching
        logger: Optional logger instance for logging errors/warnings


    Returns:
        The OAuth config dictionary if found, None otherwise.


    Example:
        # Get full OAuth config
        oauth_config = await fetch_oauth_config_by_id(
            oauth_config_id="abc123",
            connector_type="DROPBOX_PERSONAL",
            config_service=config_service,
            logger=logger
        )
        # oauth_config contains: {"_id": "...", "config": {...}, "oauthInstanceName": "...", ...}

        # Get only the config field (clientId, clientSecret, etc.)
        config_data = await fetch_oauth_config_by_id(
            oauth_config_id="abc123",
            connector_type="DROPBOX_PERSONAL",
            config_service=config_service,
            logger=logger,
        )
        # config_data contains: {"clientId": "...", "clientSecret": "...", ...}
    """
    if not oauth_config_id or not connector_type:
        if logger:
            logger.warning("oauth_config_id and connector_type are required to fetch OAuth config")
        return None

    try:
        # Construct the OAuth config path
        normalized_type = connector_type.lower().replace(" ", "")
        oauth_config_path = f"/services/oauth/{normalized_type}"

        # Fetch all OAuth configs for this connector type
        oauth_configs = await config_service.get_config(oauth_config_path, default=[])

        if not isinstance(oauth_configs, list):
            if logger:
                logger.warning(f"OAuth configs at {oauth_config_path} is not a list")
            return None

        # Find the OAuth config with matching ID
        for oauth_cfg in oauth_configs:
            if oauth_cfg.get("_id") == oauth_config_id:
                return oauth_cfg

        # OAuth config not found
        if logger:
            logger.warning("Requested OAuth config was not found")
        return None

    except Exception:
        if logger:
            logger.error("Error fetching OAuth config")
        return None
