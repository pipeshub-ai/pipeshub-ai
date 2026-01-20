from typing import Any, Dict, Optional

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.oauth_service import OAuthConfig


def get_oauth_config(auth_config: dict) -> OAuthConfig:

    oauth_config = OAuthConfig(
            client_id=auth_config['clientId'],
            client_secret=auth_config['clientSecret'],
            redirect_uri=auth_config.get('redirectUri', ''),
            authorize_url=auth_config.get('authorizeUrl', ''),
            token_url=auth_config.get('tokenUrl', ''),
            scope=' '.join(auth_config.get('scopes', [])) if auth_config.get('scopes') else ''
        )

    if auth_config.get('tokenAccessType'):
        oauth_config.token_access_type = auth_config.get('tokenAccessType')
    if auth_config.get('additionalParams'):
        oauth_config.additional_params = auth_config.get('additionalParams')
    else:
        oauth_config.additional_params = {}

    # Check if this is Notion OAuth (by checking token_url)
    # Notion requires Basic Auth with JSON body
    token_url = auth_config.get('tokenUrl', '').lower()
    if 'notion.com' in token_url or 'notion' in token_url:
        oauth_config.additional_params["use_basic_auth"] = True
        oauth_config.additional_params["use_json_body"] = True
        oauth_config.additional_params["notion_version"] = "2025-09-03"

    return oauth_config


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
            logger.warning(f"OAuth config {oauth_config_id} not found for connector type {connector_type}")
        return None

    except Exception as e:
        if logger:
            logger.error(f"Error fetching OAuth config {oauth_config_id} for connector type {connector_type}: {e}", exc_info=True)
        return None
