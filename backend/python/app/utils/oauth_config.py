import json
import logging
import re
from typing import Any, Dict, Optional  # noqa: UP035 - keep aliases for legacy callers
from urllib.parse import urlparse

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.oauth_service import OAuthConfig


# ============================================================================
# OAuth Error Formatting
# ============================================================================

# Standard OAuth 2.0 error codes → user-friendly messages.
# Covers RFC 6749 §5.2 plus provider-specific codes from Slack, Atlassian,
# Notion, Dropbox, GitHub/GitLab, Linear, HubSpot, ServiceNow, etc.
_OAUTH_ERROR_CODE_MAP: dict[str, str] = {
    "invalid_client": "Invalid OAuth client credentials. The client ID or secret may be incorrect or expired.",
    "invalid_grant": "The authorization code has expired or was already used. Please try authenticating again.",
    "unauthorized_client": "This application is not authorized for the requested scopes or grant type.",
    "invalid_scope": "One or more requested scopes are invalid or not approved for this application.",
    "unsupported_grant_type": "The OAuth grant type is not supported by the provider.",
    "access_denied": "Access was denied by the provider. The user or admin may have rejected the request.",
    "invalid_request": "The OAuth request was malformed. This usually indicates a configuration issue.",
    "server_error": "The OAuth provider encountered an internal error. Please try again later.",
    "temporarily_unavailable": "The OAuth provider is temporarily unavailable. Please try again later.",
    # Slack-specific
    "invalid_client_id": "The Slack client ID is invalid. Please check the OAuth app configuration.",
    "bad_client_secret": "The Slack client secret is invalid. Please update it in the admin settings.",
    "invalid_code": "The Slack authorization code is invalid or has expired. Please try authenticating again.",
    "bad_redirect_uri": "The redirect URI does not match the one configured in the Slack app.",
    "oauth_authorization_url_mismatch": "The Slack OAuth authorization URL does not match. Check the app configuration.",
    "preview_feature_not_available": "This Slack feature is in preview and not available for this workspace.",
    "accesslimited": "Access to this Slack workspace is limited. Contact the workspace admin.",
    "team_added_to_org": "This Slack workspace has been added to an Enterprise Grid organization. Re-authenticate with the org-level app.",
    # Notion-specific
    "unauthorized": "Notion rejected the request. The integration token or client credentials may be invalid.",
    # Atlassian (Jira/Confluence) specific
    "invalid_token": "The token is invalid or has been revoked. Please re-authenticate.",
    # GitHub/GitLab
    "incorrect_client_credentials": "The GitHub/GitLab client credentials are incorrect.",
    "redirect_uri_mismatch": "The redirect URI does not match the one registered with the OAuth application.",
    "application_suspended": "The OAuth application has been suspended by the provider.",
    # HubSpot
    "invalid_redirect_uri": "The redirect URI is invalid. Please check the OAuth app configuration.",
    # Generic HTTP status patterns
    "forbidden": "The provider rejected the request (403 Forbidden). The app may lack required permissions.",
}


def extract_oauth_error_message(exc: Exception) -> str:
    """Extract a user-friendly error message from an OAuth exception.

    Supports error response formats from all major OAuth providers:
    - Standard OAuth 2.0 (Google, Microsoft, Dropbox, Linear, HubSpot)
    - Slack (``{"ok": false, "error": "bad_client_secret"}``)
    - Notion (``{"error": "unauthorized", "message": "..."}``)
    - Atlassian / Jira / Confluence (``{"error": "...", "error_description": "..."}``)
    - GitHub / GitLab (``{"error": "...", "error_description": "..."}``)
    - ServiceNow (``{"error": "...", "error_description": "..."}``)

    The function tries, in order:
      1. Parse the embedded JSON response from the exception message
      2. Extract ``error_description`` or ``message`` for a provider-supplied explanation
      3. Map the ``error`` code to a known user-friendly string
      4. Use the HTTP status code for a generic message
      5. Fall back to a safe generic message
    """
    msg = str(exc)

    # --- 1. Try to extract the JSON body from the exception string --------
    # _make_token_request embeds: "... Response: {json_body}"
    error_body: dict[str, Any] = {}
    response_match = re.search(r"Response:\s*(.+)", msg, re.DOTALL)
    if response_match:
        try:
            error_body = json.loads(response_match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # --- 2. Extract the provider's human-readable description -------------
    description = ""
    if isinstance(error_body, dict):
        # Standard OAuth / Atlassian / Google / Microsoft / GitHub / GitLab / Linear
        description = (error_body.get("error_description") or "").strip()
        # Notion uses "message" instead of "error_description"
        if not description:
            description = (error_body.get("message") or "").strip()
        # Slack uses "error" as a code string, no separate description

    # If we got a meaningful description from the provider, use it directly
    if description and len(description) > 5:
        clean_desc = description.rstrip(".")
        return f"OAuth provider error: {clean_desc}. Please verify your OAuth credentials in the admin settings."

    # --- 3. Map the error code to a known message -------------------------
    error_code = ""
    if isinstance(error_body, dict):
        error_code = (error_body.get("error") or "").strip().lower()
        # Slack returns ok:false with error as the code
        if not error_code and error_body.get("ok") is False:
            error_code = "invalid_request"

    if error_code and error_code in _OAUTH_ERROR_CODE_MAP:
        return f"{_OAUTH_ERROR_CODE_MAP[error_code]} Please update it in the admin settings."

    # --- 4. Fallback: regex scan for known codes in the raw message -------
    # Handles cases where JSON parsing failed but the error code is in the text
    msg_lower = msg.lower()
    for code, friendly in _OAUTH_ERROR_CODE_MAP.items():
        if code in msg_lower:
            return f"{friendly} Please update it in the admin settings."

    # --- 5. HTTP status code fallback -------------------------------------
    status_match = re.search(r"status (\d{3})", msg)
    if status_match:
        status = int(status_match.group(1))
        if status == 400:
            return "The OAuth request was rejected (400 Bad Request). The client credentials or redirect URI may be misconfigured."
        if status == 401:
            return "Authentication was rejected by the provider (401 Unauthorized). Please verify your OAuth credentials in the admin settings."
        if status == 403:
            return "The provider denied access (403 Forbidden). The OAuth app may lack required permissions or scopes."
        if status == 404:
            return "The OAuth endpoint was not found (404). The token URL may be misconfigured."
        if status == 429:
            return "The OAuth provider rate-limited the request. Please wait a moment and try again."
        if status >= 500:
            return "The OAuth provider is experiencing issues. Please try again later."

    # --- 6. Check for common ValueError messages from handle_callback -----
    if "Invalid or expired state" in msg:
        return "The authentication session has expired. Please close this window and try again."
    if "already been used" in msg:
        return "This authorization code has already been used. Please close this window and start a new authentication."

    return "An unexpected error occurred during authentication. Please try again or contact your administrator."


_STANDARD_OAUTH_PATHS = ("/oauth/authorize", "/oauth/token")


def _override_oauth_host_with_instance(url: str, instance_url: str) -> str:
    """Swap the host of a standard OAuth endpoint URL to the user-supplied instance host.

    Used for self-managed connectors (e.g. GitLab EE) where the registry/SaaS
    default ``authorizeUrl``/``tokenUrl`` (``https://gitlab.com/oauth/...``) is
    stored in config, but the user's actual OAuth endpoints live on their own
    instance. Only swaps when the URL's path matches a standard OAuth path so
    connectors that use non-standard paths (e.g. ServiceNow's ``/oauth_auth.do``)
    are left untouched.
    """
    if not url or not instance_url:
        return url
    try:
        url_parsed = urlparse(url)
        instance_parsed = urlparse(instance_url)
    except Exception:
        return url
    if not (instance_parsed.scheme and instance_parsed.netloc):
        return url
    if url_parsed.path.rstrip("/") not in [p.rstrip("/") for p in _STANDARD_OAUTH_PATHS]:
        return url
    if url_parsed.netloc == instance_parsed.netloc:
        return url
    return f"{instance_parsed.scheme}://{instance_parsed.netloc}{url_parsed.path}"


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

    # When the saved URL still points at the SaaS host (registry default) but
    # the user supplied a self-managed instance, redirect to the instance host.
    # No-op for URLs with non-standard OAuth paths (e.g. ServiceNow).
    if instance_url:
        authorize_url = _override_oauth_host_with_instance(authorize_url, instance_url)
        token_url = _override_oauth_host_with_instance(token_url, instance_url)

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


async def resolve_instance_url(
    auth_config: Optional[dict[str, Any]],
    config_service: ConfigurationService,
    default: str = "",
    *,
    logger: Optional[logging.Logger] = None,
) -> str:
    """Resolve the runtime ``instanceUrl`` for a connector instance.

    Self-managed connectors (GitLab EE, ServiceNow, Jira DC, etc.) store the
    host in the per-instance ``auth.instanceUrl``. Legacy installs created
    before the OAuth-field strip was relaxed have an empty value there because
    ``instanceUrl`` was filtered out alongside ``clientSecret`` — fall back to
    the shared OAuth-app config's ``config.instanceUrl`` so those installs keep
    working without a re-save.

    Args:
        auth_config: Connector instance ``auth`` dict (may be ``None``).
        config_service: Configuration service used to read the shared OAuth
            config when fallback is needed.
        default: Returned when neither layer has a value.
        logger: Optional logger; debug-level only.

    Returns:
        Resolved ``instanceUrl`` (rstripped of trailing slash), or ``default``.
    """
    auth_config = auth_config or {}
    instance_url = (auth_config.get("instanceUrl") or "").strip()
    if instance_url:
        return instance_url.rstrip("/")

    oauth_config_id = auth_config.get("oauthConfigId")
    connector_type = auth_config.get("connectorType")
    if not oauth_config_id or not connector_type:
        return default.rstrip("/") if default else default

    try:
        shared = await fetch_oauth_config_by_id(
            oauth_config_id=oauth_config_id,
            connector_type=connector_type,
            config_service=config_service,
            logger=logger,
        )
    except Exception:
        return default.rstrip("/") if default else default

    if not shared:
        return default.rstrip("/") if default else default

    shared_config_data = (shared.get("config") or {})
    instance_url = (shared_config_data.get("instanceUrl") or "").strip()
    if instance_url:
        return instance_url.rstrip("/")

    return default.rstrip("/") if default else default


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
