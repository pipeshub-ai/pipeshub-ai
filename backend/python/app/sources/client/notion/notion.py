
import base64
import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode, urlparse

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient
from app.sources.client.resilience import ResiliencePolicy

DEFAULT_NOTION_API_BASE_URL = "https://api.notion.com/v1"


_logger = logging.getLogger(__name__)
_reported_overrides: set[str] = set()


def notion_api_base_url() -> str:
    """The Notion API base URL: ``NOTION_API_BASE_URL`` when set (test stacks point it at a local
    stand-in for the API), else Notion's own. The token goes wherever this points, so a value
    that is not an http(s) URL with a host is refused, and the host in use is logged once."""
    raw = (os.environ.get("NOTION_API_BASE_URL") or "").strip().rstrip("/")
    if not raw:
        return DEFAULT_NOTION_API_BASE_URL
    parsed = urlparse(raw)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    valid = parsed.scheme in ("http", "https") and bool(parsed.hostname) and port != -1
    if raw not in _reported_overrides:
        _reported_overrides.add(raw)
        if valid:
            _logger.warning(
                "NOTION_API_BASE_URL is set: Notion API calls go to %s://%s%s",
                parsed.scheme, parsed.hostname, f":{port}" if port else "",
            )
        else:
            _logger.error("NOTION_API_BASE_URL is not an http(s) URL with a host; using %s", DEFAULT_NOTION_API_BASE_URL)
    return raw if valid else DEFAULT_NOTION_API_BASE_URL


@dataclass
class NotionResponse:
    """Standardized Notion API response wrapper"""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None
    status_code: Optional[int] = None
    # Notion's error ``code`` from the JSON body (``validation_error``, ``object_not_found``, ...), when it has one.
    code: Optional[str] = None

    @classmethod
    def from_http(cls, response: HTTPResponse) -> "NotionResponse":
        """Build a response from an HTTPResponse; non-2xx is success=False."""
        status = response.status
        if 200 <= status < 300:
            return cls(success=True, data=response, status_code=status)
        text = ""
        try:
            text = response.text() or ""
        except Exception:
            text = ""
        code = None
        try:
            parsed = json.loads(text) if text else None
            if isinstance(parsed, dict) and isinstance(parsed.get("code"), str):
                code = parsed["code"]
        except ValueError:
            code = None
        error = f"HTTP {status}"
        if text:
            error = f"{error}: {text[:200]}"
        return cls(success=False, data=response, error=error, status_code=status, code=code)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())

class NotionRESTClientViaOAuth(HTTPClient):
    """Notion REST client via OAuth 2.0 - handles OAuth flow internally.
    If no access token provided, we'll need to go through OAuth flow
    Args:
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
        version: Notion API version (default: "2025-09-03")
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        access_token: Optional[str] = None,
        version: str = "2025-09-03",
        resilience: Optional[ResiliencePolicy] = None
    ) -> None:
        # Initialize with empty token first, will be set after OAuth flow
        super().__init__(access_token or "", "Bearer", resilience=resilience)

        self.base_url = notion_api_base_url()
        self.oauth_base_url = f"{self.base_url}/oauth"
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.access_token = access_token
        self.version = version

        # Add Notion-specific headers
        self.headers.update({
            "Notion-Version": version,
            "Content-Type": "application/json"
        })

        # If no access token provided, we'll need to go through OAuth flow
        self._oauth_completed = access_token is not None

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def is_oauth_completed(self) -> bool:
        """Check if OAuth flow has been completed"""
        return self._oauth_completed

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Generate OAuth authorization URL (internal method)
        Args:
            state: Optional state parameter for security
        Returns:
            Authorization URL
        """
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "owner": "user",
            "redirect_uri": self.redirect_uri
        }

        if state:
            params["state"] = state

        return f"{self.oauth_base_url}/authorize?{urlencode(params)}"

    # TODO: Before using this method, implement a callback endpoint that can be used to handle the OAuth callback
    # the authorization code is received from the callback endpoint and then this method is called to complete the OAuth flow
    # and the token is returned
    async def initiate_oauth_flow(self, authorization_code: str) -> Optional[str]:
        """Complete OAuth flow with authorization code
        Args:
            authorization_code: The code received from OAuth callback
        Returns:
            Token data from OAuth exchange
        """
        return await self._exchange_code_for_token(authorization_code)

    async def _exchange_code_for_token(self, code: str) -> Optional[str]:
        """Exchange authorization code for access token (internal method)
        Args:
            code: Authorization code from callback
        Returns:
            Token response containing access_token, token_type, etc.
        """
        # Encode client credentials for Basic auth
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json",
            "Notion-Version": self.version
        }

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri
        }

        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/token",
            headers=headers,
            body=data
        )
        token_data: Dict[str, Any]
        response = await self.execute(request)

        # Check response status before parsing JSON
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise Exception(f"Token request failed with status {response.status}: {response.text()}")

        token_data = response.json()
        self.access_token = token_data.get("access_token")

        # Update headers with new token
        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            self._oauth_completed = True

        return token_data.get("access_token") if token_data.get("access_token") else None

    async def introspect_access_token(self, access_token: str) -> Dict[str, Any]:
        """Return Notion's introspection payload for this OAuth access token.

        Uses client-id/secret Basic auth, not the user Bearer token.
        """
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/introspect",
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/json",
                "Notion-Version": self.version,
            },
            body={"token": access_token},
        )
        async with HTTPClient(token="", resilience=self.resilience) as client:
            response = await client.execute(request)
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise Exception(
                f"Token introspect failed with status {response.status}: {response.text()}"
            )
        payload = response.json()
        return payload if isinstance(payload, dict) else {}


class NotionRESTClientViaToken(HTTPClient):
    """Notion REST client via Internal Integration token
    Args:
        token: The internal integration token to use for authentication
        version: Notion API version (default: "2025-09-03")
    """

    def __init__(
        self,
        token: str,
        version: str = "2025-09-03",
        resilience: Optional[ResiliencePolicy] = None
    ) -> None:
        super().__init__(token, "Bearer", resilience=resilience)
        self.base_url = notion_api_base_url()
        self.version = version
        self.headers.update({
            "Notion-Version": version,
            "Content-Type": "application/json"
        })

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

@dataclass
class NotionTokenConfig:
    """Configuration for Notion REST client via Internal Integration token
    Args:
        token: The internal integration token
        version: Notion API version
        ssl: Whether to use SSL (always True for Notion)
        resilience: Optional shared rate limit / retry policy
    """
    token: str
    version: str = "2025-09-03"
    ssl: bool = True
    resilience: Optional[ResiliencePolicy] = None

    def create_client(self) -> NotionRESTClientViaToken:
        return NotionRESTClientViaToken(self.token, self.version, resilience=self.resilience)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary.

        Serializes the settings only: ``resilience`` is a live object holding a
        lock, which ``asdict``'s deep copy cannot handle.
        """
        return {"token": self.token, "version": self.version, "ssl": self.ssl}


class NotionClient(IClient):
    """Builder class for Notion clients with different authentication methods"""

    def __init__(self, client: Union[NotionRESTClientViaOAuth, NotionRESTClientViaToken]) -> None:
        """Initialize with a Notion client object (OAuth or Token-based)"""
        self.client = client

    def get_client(self) -> Union[NotionRESTClientViaOAuth, NotionRESTClientViaToken]:
        """Return the Notion client object"""
        return self.client

    @classmethod
    def build_with_config(cls, config: NotionTokenConfig) -> "NotionClient":
        """Build NotionClient with configuration
        Args:
            config: NotionTokenConfig instance
        Returns:
            NotionClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
        resilience: Optional[ResiliencePolicy] = None,
        connector_type: str = "notion",
    ) -> "NotionClient":
        """Build NotionClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Connector instance whose config to read
            resilience: Shared rate limit / retry policy owned by the connector
            connector_type: Normalized connector type owning the shared OAuth app,
                as written by ``_get_oauth_config_path`` (e.g. "notionpersonal").
                Shared OAuth configs are stored per connector type, so a variant
                that registers its own app must look under its own key.
        Returns:
            NotionClient instance
        """
        try:
            # Get Notion configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service, connector_instance_id)

            if not config:
                raise ValueError("Failed to get Notion connector configuration")

            # Extract configuration values from auth section
            auth_config = config.get("auth", {}) or {}
            auth_type = auth_config.get("authType", "API_TOKEN")  # API_TOKEN or OAUTH
            version = config.get("version", "2025-09-03")

            # Create appropriate client based on auth type
            if auth_type == "OAUTH":
                # Try to get OAuth credentials from connector config first
                client_id = auth_config.get("clientId", "")
                client_secret = auth_config.get("clientSecret", "")
                redirect_uri = auth_config.get("redirectUri", "")

                # Shared OAuth apps live on the owning org; inheritedFromOrgId
                # re-scopes the read so a child org can use an admin-configured app.
                oauth_config_id = auth_config.get("oauthConfigId")
                needs_shared_config = oauth_config_id and not (client_id and client_secret)

                if needs_shared_config:
                    try:
                        from app.edition_config import fetch_oauth_config_by_id

                        matching_config = await fetch_oauth_config_by_id(
                            oauth_config_id=oauth_config_id,
                            connector_type=connector_type,
                            config_service=config_service,
                            logger=logger,
                            org_id=auth_config.get("inheritedFromOrgId"),
                        )
                        if matching_config:
                            shared_config = matching_config.get("config", {}) or {}
                            client_id = shared_config.get("clientId") or shared_config.get("client_id") or client_id
                            client_secret = shared_config.get("clientSecret") or shared_config.get("client_secret") or client_secret
                            if not redirect_uri:
                                redirect_uri = shared_config.get("redirectUri") or shared_config.get("redirect_uri") or ""
                    except Exception as e:
                        logger.warning(f"Failed to fetch shared OAuth config: {e}, using connector auth config")

                # Get access token from credentials section (where OAuth provider stores it)
                credentials = config.get("credentials", {}) or {}
                access_token = credentials.get("access_token", "")

                if not client_id or not client_secret or not redirect_uri:
                    raise ValueError("Client ID, client secret, and redirect URI required for OAuth auth type")

                client = NotionRESTClientViaOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    access_token=access_token,
                    version=version,
                    resilience=resilience
                )

            elif auth_type == "API_TOKEN":  # Default to token auth
                token = auth_config.get("apiToken", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = NotionRESTClientViaToken(token, version, resilience=resilience)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Notion client from services: {str(e)}")
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "NotionClient":
        """Build NotionClient from a per-user toolset configuration.

        Args:
            toolset_config: Toolset configuration from etcd containing:
                - authType: OAUTH or API_TOKEN
                - credentials: { access_token, ... } for OAuth
                - auth: { apiToken } for API_TOKEN
                - isAuthenticated: bool
            logger: Logger instance
            config_service: Unused; accepted for the common factory signature

        Returns:
            NotionClient instance

        Raises:
            ValueError: If the configuration is missing or not authenticated
        """
        if not toolset_config:
            raise ValueError("Toolset configuration is required")
        if not toolset_config.get("isAuthenticated", False):
            raise ValueError("Toolset is not authenticated. Please complete authentication first.")

        auth_type = (toolset_config.get("authType") or "").upper()
        version = toolset_config.get("version") or "2025-09-03"
        if auth_type == "OAUTH":
            token = (toolset_config.get("credentials") or {}).get("access_token", "")
            if not token:
                raise ValueError("Access token not found in OAuth credentials. Please re-authenticate.")
        elif auth_type == "API_TOKEN":
            token = ((toolset_config.get("auth") or {}).get("apiToken") or "").strip()
            if not token:
                raise ValueError("API token is required for API_TOKEN auth")
        else:
            raise ValueError(f"Unsupported auth type: {auth_type}. Supported: OAUTH, API_TOKEN")

        logger.info(f"Created Notion client from toolset config (auth type: {auth_type})")
        # Both auth types call the API with a bearer token; OAuth refresh is handled by the toolset store.
        return cls(NotionRESTClientViaToken(token, version))

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetch connector config from etcd for Notion."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get Notion connector configuration for instance {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to get Notion connector config: {e}")
            raise ValueError(f"Failed to get Notion connector configuration for instance {connector_instance_id}")
