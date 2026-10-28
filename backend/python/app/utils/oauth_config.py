import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Dict, Optional  # noqa: UP035 - keep aliases for legacy callers
from urllib.parse import quote, urlparse

from app.config.configuration_service import ConfigurationService
from app.connectors.core.base.token_service.oauth_service import OAuthConfig
from app.connectors.core.constants import AuthFieldKeys

logger = logging.getLogger(__name__)


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

    The function never echoes raw exception text or provider-supplied free
    text back to the caller — the ``except Exception`` blocks that call this
    helper span more than just the OAuth token exchange (DB updates, cache
    refresh, scheduling), so ``exc`` may carry internal details unrelated to
    the OAuth provider. Instead it only classifies the exception against a
    fixed allow-list of known messages. Callers log ``exc`` itself, which
    carries the provider's full response.

    The function tries, in order:
      1. Parse the embedded JSON response from the exception message
      2. Map the ``error`` code to a known user-friendly string
      3. Use the HTTP status code for a generic message
      4. Fall back to a safe generic message
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

    # --- 2. Map the error code to a known message -------------------------
    error_code = ""
    if isinstance(error_body, dict):
        error_code = (error_body.get("error") or "").strip().lower()
        # Slack returns ok:false with error as the code
        if not error_code and error_body.get("ok") is False:
            error_code = "invalid_request"

    if error_code and error_code in _OAUTH_ERROR_CODE_MAP:
        return f"{_OAUTH_ERROR_CODE_MAP[error_code]} Please update it in the admin settings."

    # --- 3. Fallback: regex scan for known codes in the raw message -------
    # Handles cases where JSON parsing failed but the error code is in the text
    msg_lower = msg.lower()
    for code, friendly in _OAUTH_ERROR_CODE_MAP.items():
        if code in msg_lower:
            return f"{friendly} Please update it in the admin settings."

    # --- 4. HTTP status code fallback -------------------------------------
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

    # --- 5. Check for common ValueError messages from handle_callback -----
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


SALESFORCE_PRODUCTION_LOGIN_HOST = "login.salesforce.com"
_SALESFORCE_DOMAIN = "salesforce.com"
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HTTPS_DEFAULT_PORT = 443
SALESFORCE_LOGIN_URL_ERROR = (
    "The Salesforce Login URL must be an https address on salesforce.com with nothing after "
    "the host, such as https://test.salesforce.com for a sandbox or "
    "https://yourcompany.my.salesforce.com for My Domain. Leave it blank for production."
)


def normalize_salesforce_login_url(value: object) -> str:
    """Return ``https://<host>`` for a Salesforce login URL, or "" when it is blank.

    The token request carries the client secret and runs on the server, so only hosts
    Salesforce owns are accepted. Raises ValueError for anything else.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(SALESFORCE_LOGIN_URL_ERROR)
    raw = value.strip()
    if not raw:
        return ""
    if any(ch.isspace() or ch == "\\" or ord(ch) < 0x20 for ch in raw):
        raise ValueError(SALESFORCE_LOGIN_URL_ERROR)
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        raise ValueError(SALESFORCE_LOGIN_URL_ERROR) from None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, _HTTPS_DEFAULT_PORT)
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.netloc.lower() not in (host, f"{host}:{_HTTPS_DEFAULT_PORT}")
        or not host.endswith(f".{_SALESFORCE_DOMAIN}")
        or not all(_DNS_LABEL.match(label) for label in host.split("."))
    ):
        raise ValueError(SALESFORCE_LOGIN_URL_ERROR)
    return f"https://{host}"


def _is_salesforce_production_login(url: str) -> bool:
    # A malformed saved URL must not break sign-in for connectors that never had this setting.
    try:
        return bool(url) and (urlparse(url).hostname or "").lower() == SALESFORCE_PRODUCTION_LOGIN_HOST
    except ValueError:
        return False


def _apply_salesforce_login_host(url: str, login_url: str) -> str:
    """Move a Salesforce production OAuth endpoint onto the chosen login host, keeping its path."""
    if not _is_salesforce_production_login(url):
        return url
    # The token request carries the client secret, so a saved http:// endpoint must not survive the move.
    return urlparse(url)._replace(scheme="https", netloc=urlparse(login_url).netloc).geturl()


_MICROSOFT_TENANT_SEGMENT = re.compile(r"^(https://login\.microsoftonline\.com/)[^/]+(/)")


def apply_tenant_to_microsoft_oauth_url(url: str, tenant_id: str | None) -> str:
    """Put *tenant_id* in place of the tenant segment of a Microsoft login URL.

    A single-tenant Azure AD app cannot use the /common endpoints, so sign-in and every
    refresh of its tokens must use its own tenant's. A blank or "common" tenant, or a URL
    that is not Microsoft's, is returned unchanged.
    """
    # Anchored: only a URL on Microsoft's login host is changed, never one that merely mentions it.
    if not url or not _MICROSOFT_TENANT_SEGMENT.match(url):
        return url
    tenant = str(tenant_id or "").strip()
    if not tenant or tenant.lower() == "common":
        return url
    # The tenant is user input: encoded so it stays one path segment ("/", "?" and "#" cannot
    # reshape the URL), and inserted by a function so a backslash is not read as a regex template.
    # A real tenant (a GUID or a domain) has nothing to encode.
    segment = quote(tenant, safe="")
    return _MICROSOFT_TENANT_SEGMENT.sub(lambda m: f"{m.group(1)}{segment}{m.group(2)}", url, count=1)


def check_salesforce_login_url_setting(connector_type: str, settings: dict[str, Any] | None) -> None:
    """Raise ValueError when a Salesforce OAuth app's Login URL is not a Salesforce host.

    Other connector and toolset types are left alone. Called on save, so an admin
    hears about a bad value at once instead of at sign-in.
    """
    if (connector_type or "").replace(" ", "").lower() != "salesforce":
        return
    normalize_salesforce_login_url((settings or {}).get(AuthFieldKeys.LOGIN_URL))


@dataclass(frozen=True)
class OAuthUrlRule:
    """A connector's own rule for where its OAuth requests may go.

    The code exchange and refresh send the client secret from the server, so a
    connector whose endpoints are fixed by its settings registers one of these
    instead of trusting the saved URL fields. ``check`` runs on save, ``pin``
    rewrites the flow's URLs before every request; both raise ValueError with a
    message fit to show the admin. ``settings`` are the keys ``pin`` reads.
    """

    check: Callable[[dict[str, Any]], None]
    pin: Callable[[dict[str, Any]], None]
    settings: tuple[str, ...] = ()


_OAUTH_URL_RULES: dict[str, OAuthUrlRule] = {}


def _rule_for(connector_type: str) -> OAuthUrlRule | None:
    return _OAUTH_URL_RULES.get((connector_type or "").replace(" ", "").lower())


def register_oauth_url_rule(connector_type: str, rule: OAuthUrlRule) -> None:
    _OAUTH_URL_RULES[connector_type.replace(" ", "").lower()] = rule


def check_oauth_url_settings(connector_type: str, settings: dict[str, Any] | None) -> None:
    rule = _rule_for(connector_type)
    if rule:
        rule.check(settings or {})


def pin_oauth_urls(connector_type: str, flow_config: dict[str, Any]) -> None:
    rule = _rule_for(connector_type)
    if rule:
        rule.pin(flow_config)


def carry_oauth_url_settings(connector_type: str, source: dict[str, Any], flow_config: dict[str, Any]) -> None:
    rule = _rule_for(connector_type)
    for key in rule.settings if rule else ():
        if not flow_config.get(key) and source.get(key):
            flow_config[key] = source[key]


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

    # Sandboxes and My Domain orgs sign in on their own host, and a refresh must go back
    # to the host that issued the token, so authorize, token and refresh all move together.
    if _is_salesforce_production_login(authorize_url) or _is_salesforce_production_login(token_url):
        login_url = normalize_salesforce_login_url(auth_config.get(AuthFieldKeys.LOGIN_URL))
        if login_url:
            authorize_url = _apply_salesforce_login_host(authorize_url, login_url)
            token_url = _apply_salesforce_login_host(token_url, login_url)

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
            if _is_host_or_subdomain(hostname, 'notion.com') or _is_notion_override(token_url):
                oauth_config.additional_params["use_basic_auth"] = True
                oauth_config.additional_params["use_json_body"] = True
                oauth_config.additional_params["notion_version"] = "2025-09-03"
            if any(_is_host_or_subdomain(hostname, host) for host in _BASIC_AUTH_ONLY_TOKEN_HOSTS):
                oauth_config.token_endpoint_auth_method = "client_secret_basic"
        except Exception:
            # If URL parsing fails, skip the provider-specific configuration
            pass

    return oauth_config


# Token endpoints whose providers accept client credentials only in a Basic
# header: their OAuth metadata lists client_secret_basic and no client_secret_post.
_BASIC_AUTH_ONLY_TOKEN_HOSTS = ("airtable.com", "zoom.us")


def _is_notion_override(token_url: str) -> bool:
    """A token URL under NOTION_API_BASE_URL (a test stack's stand-in for Notion) is Notion's too."""
    from app.sources.client.notion.notion import (
        DEFAULT_NOTION_API_BASE_URL,
        notion_api_base_url,
    )

    base = notion_api_base_url()
    return base != DEFAULT_NOTION_API_BASE_URL and token_url.startswith(f"{base}/")


def _is_host_or_subdomain(hostname: str, domain: str) -> bool:
    # Exact or dot-anchored, so evilnotion.com and notion.com.evil.com don't match.
    hostname = hostname.lower()
    return hostname == domain or hostname.endswith(f".{domain}")


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
    org_id: Optional[str] = None,
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
        org_id: Organization ID


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
    del org_id
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
