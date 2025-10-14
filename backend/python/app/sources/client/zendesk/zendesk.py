import base64
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


class ZendeskResponse(BaseModel):
    """Standardized Zendesk API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class ZendeskRESTClientViaToken(HTTPClient):
    """Zendesk REST client via Personal Access Token or API Token
    Args:
        subdomain: The Zendesk subdomain
        token: The personal access token or API token
        email: The email address associated with the token
    """
    def __init__(self, subdomain: str, token: str, email: str) -> None:
        # For Zendesk API token authentication, we need to use Basic auth with email/token
        credentials = f"{email}/token:{token}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        # Initialize with empty token and override the headers manually
        super().__init__("", "")
        self.subdomain = subdomain
        self.base_url = f"https://{subdomain}.zendesk.com/api/v2"
        # Set the correct Basic authentication header
        self.headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json"
        }

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_subdomain(self) -> str:
        """Get the Zendesk subdomain"""
        return self.subdomain


class ZendeskRESTClientViaOAuth(HTTPClient):
    """Zendesk REST client via OAuth 2.0 - handles OAuth flow internally
    Args:
        subdomain: The Zendesk subdomain
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
    """
    def __init__(
        self,
        subdomain: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        access_token: Optional[str] = None
    ) -> None:
        # Initialize with empty token first, will be set after OAuth flow
        super().__init__(access_token or "", "Bearer")

        self.subdomain = subdomain
        self.base_url = f"https://{subdomain}.zendesk.com/api/v2"
        self.oauth_base_url = f"https://{subdomain}.zendesk.com/oauth"
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.access_token = access_token

        # Add Zendesk-specific headers
        self.headers.update({
            "Content-Type": "application/json"
        })

        # If no access token provided, we'll need to go through OAuth flow
        self._oauth_completed = access_token is not None

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_subdomain(self) -> str:
        """Get the Zendesk subdomain"""
        return self.subdomain

    def is_oauth_completed(self) -> bool:
        """Check if OAuth flow has been completed"""
        return self._oauth_completed

    def get_authorization_url(
        self,
        scope: str = "read",
        state: Optional[str] = None,
        code_challenge: Optional[str] = None,
        code_challenge_method: str = "S256"
    ) -> str:
        """Generate OAuth authorization URL
        Args:
            scope: OAuth scopes (default: "read")
            state: Optional state parameter for security
            code_challenge: Optional code challenge for PKCE
            code_challenge_method: Method used for code challenge
        Returns:
            Authorization URL
        """
        params = {
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "scope": scope
        }

        if state:
            params["state"] = state

        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = code_challenge_method

        return f"{self.oauth_base_url}/authorizations/new?{urlencode(params)}"

    async def initiate_oauth_flow(
        self,
        authorization_code: str,
        scope: str = "read",
        code_verifier: Optional[str] = None,
        expires_in: Optional[int] = None,
        refresh_token_expires_in: Optional[int] = None
    ) -> Optional[str]:
        """Complete OAuth flow with authorization code
        Args:
            authorization_code: The code received from OAuth callback
            scope: OAuth scopes
            code_verifier: Code verifier for PKCE
            expires_in: Optional access token expiration in seconds
            refresh_token_expires_in: Optional refresh token expiration in seconds
        Returns:
            Access token from OAuth exchange
        """
        return await self._exchange_code_for_token(
            authorization_code, scope, code_verifier, expires_in, refresh_token_expires_in
        )

    async def get_token_via_client_credentials(
        self,
        scope: str = "read",
        expires_in: Optional[int] = None
    ) -> Optional[str]:
        """Get token using client credentials grant type (for confidential clients)
        Args:
            scope: OAuth scopes
            expires_in: Optional token expiration in seconds
        Returns:
            Access token from client credentials flow
        """
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": scope
        }

        if expires_in:
            data["expires_in"] = expires_in

        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/tokens",
            headers={"Content-Type": "application/json"},
            body=data
        )

        response = await self.execute(request)

        # Check response status before parsing JSON
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            error_text = await response.text() if hasattr(response, 'text') else "Unknown error"
            raise Exception(f"Token request failed with status {response.status}: {error_text}")

        token_data = response.json()
        self.access_token = token_data.get("access_token")

        # Update headers with new token
        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            self._oauth_completed = True

        return self.access_token

    async def refresh_token(self, refresh_token: str) -> Optional[str]:
        """Refresh OAuth access token
        Args:
            refresh_token: The refresh token from previous OAuth flow
        Returns:
            New access token
        """
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/tokens",
            headers={"Content-Type": "application/json"},
            body=data
        )

        response = await self.execute(request)

        # Check response status before parsing JSON
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise Exception(f"Token refresh failed with status {response.status}: {response.text}")

        token_data = response.json()

        self.access_token = token_data.get("access_token")

        # Update headers with new token
        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"

        return self.access_token

    async def _exchange_code_for_token(
        self,
        code: str,
        scope: str = "read",
        code_verifier: Optional[str] = None,
        expires_in: Optional[int] = None,
        refresh_token_expires_in: Optional[int] = None
    ) -> Optional[str]:
        """Exchange authorization code for access token
        Args:
            code: Authorization code from callback
            scope: OAuth scopes
            code_verifier: Code verifier for PKCE
            expires_in: Optional access token expiration in seconds
            refresh_token_expires_in: Optional refresh token expiration in seconds
        Returns:
            Access token from OAuth exchange
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "scope": scope
        }

        if code_verifier:
            data["code_verifier"] = code_verifier

        if expires_in:
            data["expires_in"] = expires_in

        if refresh_token_expires_in:
            data["refresh_token_expires_in"] = refresh_token_expires_in

        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/tokens",
            headers={"Content-Type": "application/json"},
            body=data
        )

        response = await self.execute(request)

        # Check response status before parsing JSON
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise Exception(f"Token exchange failed with status {response.status}: {response.text}")

        token_data = response.json()
        self.access_token = token_data.get("access_token")

        # Update headers with new token
        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            self._oauth_completed = True

        return self.access_token


class ZendeskTokenConfig(BaseModel):
    """Configuration for Zendesk REST client via Personal Access Token or API Token
    Args:
        subdomain: The Zendesk subdomain
        token: The personal access token or API token
        email: The email address associated with the token
        ssl: Whether to use SSL (always True for Zendesk)
    """
    subdomain: str
    token: str
    email: str
    ssl: bool = True

    def create_client(self) -> ZendeskRESTClientViaToken:
        return ZendeskRESTClientViaToken(self.subdomain, self.token, self.email)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class ZendeskOAuthConfig(BaseModel):
    """Configuration for Zendesk REST client via OAuth 2.0
    Args:
        subdomain: The Zendesk subdomain
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
        ssl: Whether to use SSL (always True for Zendesk)
    """
    subdomain: str
    client_id: str
    client_secret: str
    redirect_uri: str
    access_token: Optional[str] = None
    ssl: bool = True

    def create_client(self) -> ZendeskRESTClientViaOAuth:
        return ZendeskRESTClientViaOAuth(
            self.subdomain,
            self.client_id,
            self.client_secret,
            self.redirect_uri,
            self.access_token
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class ZendeskClient(IClient):
    """Builder class for Zendesk clients with different construction methods"""

    def __init__(
        self,
        client: Union[ZendeskRESTClientViaToken, ZendeskRESTClientViaOAuth]
    ) -> None:
        """Initialize with a Zendesk client object"""
        self.client = client

    def get_client(self) -> Union[ZendeskRESTClientViaToken, ZendeskRESTClientViaOAuth]:
        """Return the Zendesk client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    def get_subdomain(self) -> str:
        """Get the Zendesk subdomain"""
        return self.client.get_subdomain()

    @classmethod
    def build_with_config(
        cls,
        config: Union[ZendeskTokenConfig, ZendeskOAuthConfig]
    ) -> "ZendeskClient":
        """Build ZendeskClient with configuration
        Args:
            config: ZendeskTokenConfig or ZendeskOAuthConfig instance
        Returns:
            ZendeskClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
    ) -> "ZendeskClient":
        """Build ZendeskClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            ZendeskClient instance
        """
        try:
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                raise ValueError("Failed to get Zendesk connector configuration")
            auth_type = config.get("authType", "API_TOKEN")  # API_TOKEN or OAUTH
            auth_config = config.get("auth", {})

            if auth_type == "API_TOKEN":
                client = ZendeskRESTClientViaToken(
                    subdomain=auth_config.get("subdomain"),
                    token=auth_config.get("apiToken"),
                    email=auth_config.get("email")
                )


            elif auth_type == "OAUTH":
                credentials_config = auth_config.get("credentials", {})
                client = ZendeskRESTClientViaOAuth(
                    subdomain=auth_config.get("subdomain"),
                    client_id=auth_config.get("clientId"),
                    client_secret=auth_config.get("clientSecret"),
                    redirect_uri=auth_config.get("redirectUri"),
                    access_token=credentials_config.get("access_token")
                )

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Zendesk client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Zendesk."""
        try:
            config = await config_service.get_config("/services/connectors/zendesk/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Zendesk connector config: {e}")
            return {}
