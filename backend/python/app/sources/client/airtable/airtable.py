import base64
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


class AirtableResponse(BaseModel):
    """Standardized Airtable API response wrapper"""
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


class AirtableRESTClientViaToken(HTTPClient):
    """Airtable REST client via Personal Access Token
    Args:
        token: The personal access token to use for authentication
        base_url: The base URL of the Airtable API
    """

    def __init__(self, token: str, base_url: str = "https://api.airtable.com/v0") -> None:
        super().__init__(token, "Bearer")
        self.base_url = base_url
        # Add Airtable-specific headers
        self.headers.update({
            "Content-Type": "application/json"
        })

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class AirtableRESTClientViaOAuth(HTTPClient):
    """Airtable REST client via OAuth 2.0 - handles OAuth flow internally
    Args:
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
        base_url: The base URL of the Airtable API
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        access_token: Optional[str] = None,
        base_url: str = "https://api.airtable.com/v0"
    ) -> None:
        # Initialize with empty token first, will be set after OAuth flow
        super().__init__(access_token or "", "Bearer")

        self.base_url = base_url
        self.oauth_base_url = "https://airtable.com/oauth2/v1"
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.access_token = access_token

        # Add Airtable-specific headers
        self.headers.update({
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

    def get_authorization_url(self, state: Optional[str] = None, scope: str = "data.records:read data.records:write") -> str:
        """Generate OAuth authorization URL
        Args:
            state: Optional state parameter for security
            scope: OAuth scopes (default: read/write records)
        Returns:
            Authorization URL
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": scope
        }

        if state:
            params["state"] = state

        return f"{self.oauth_base_url}/authorize?{urlencode(params)}"

    async def initiate_oauth_flow(self, authorization_code: str) -> Optional[str]:
        """Complete OAuth flow with authorization code
        Args:
            authorization_code: The code received from OAuth callback
        Returns:
            Access token from OAuth exchange
        """
        return await self._exchange_code_for_token(authorization_code)

    async def refresh_token(self, refresh_token: str) -> Optional[str]:
        """Refresh OAuth access token
        Args:
            refresh_token: The refresh token from previous OAuth flow
        Returns:
            New access token
        """
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }

        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/token",
            headers=headers,
            body=data
        )

        async with HTTPClient(token="") as client:
            response = await client.execute(request)
            token_data = response.json()

        self.access_token = token_data.get("access_token")

        # Update headers with new token
        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"

        return token_data.get("access_token") if token_data.get("access_token") else None

    async def _exchange_code_for_token(self, code: str) -> Optional[str]:
        """Exchange authorization code for access token
        Args:
            code: Authorization code from callback
        Returns:
            Access token from OAuth exchange
        """
        # Encode client credentials for Basic auth
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
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

        response = await self.execute(request)

        # Check response status before parsing JSON
        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise Exception(f"Token request failed with status {response.status}: {response.text}")

        token_data = response.json()

        self.access_token = token_data.get("access_token")

        # Update headers with new token
        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            self._oauth_completed = True

        return token_data.get("access_token") if token_data.get("access_token") else None


class AirtableTokenConfig(BaseModel):
    """Configuration for Airtable REST client via Personal Access Token
    Args:
        token: The personal access token
        base_url: The base URL of the Airtable API
        ssl: Whether to use SSL (always True for Airtable)
    """
    token: str
    base_url: str = "https://api.airtable.com/v0"
    ssl: bool = True

    def create_client(self) -> AirtableRESTClientViaToken:
        return AirtableRESTClientViaToken(self.token, self.base_url)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class AirtableOAuthConfig(BaseModel):
    """Configuration for Airtable REST client via OAuth 2.0
    Args:
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
        base_url: The base URL of the Airtable API
        ssl: Whether to use SSL (always True for Airtable)
    """
    client_id: str
    client_secret: str
    redirect_uri: str
    access_token: Optional[str] = None
    base_url: str = "https://api.airtable.com/v0"
    ssl: bool = True

    def create_client(self) -> AirtableRESTClientViaOAuth:
        return AirtableRESTClientViaOAuth(
            self.client_id,
            self.client_secret,
            self.redirect_uri,
            self.access_token,
            self.base_url
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class AirtableClient(IClient):
    """Builder class for Airtable clients with different construction methods"""

    def __init__(self, client: AirtableRESTClientViaToken | AirtableRESTClientViaOAuth) -> None:
        """Initialize with an Airtable client object"""
        self.client = client

    def get_client(self) -> AirtableRESTClientViaToken | AirtableRESTClientViaOAuth:
        """Return the Airtable client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(cls, config: AirtableTokenConfig | AirtableOAuthConfig) -> "AirtableClient":
        """Build AirtableClient with configuration
        Args:
            config: AirtableTokenConfig or AirtableOAuthConfig instance
        Returns:
            AirtableClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "AirtableClient":
        """Build AirtableClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            AirtableClient instance
        """
        try:
            # Get Airtable configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Airtable connector configuration")

            # Extract configuration values
            base_url = config.get("base_url", "https://api.airtable.com/v0")

            auth_type = config.get("authType", "token")  # token or oauth
            auth_config = config.get("auth", {})

            # Create appropriate client based on auth type
            if auth_type == "OAUTH":
                client_id = auth_config.get("clientId", "")
                client_secret = auth_config.get("clientSecret", "")
                redirect_uri = auth_config.get("redirectUri", "")
                credentials = config.get("credentials", {})
                access_token = credentials.get("access_token", "")

                if not client_id or not client_secret or not redirect_uri:
                    raise ValueError("Client ID, client secret, and redirect URI required for OAuth auth type")

                client = AirtableRESTClientViaOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    access_token=access_token,
                    base_url=base_url
                )

            elif auth_type == "API_TOKEN":  # Default to token auth
                token = config.get("apiToken", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = AirtableRESTClientViaToken(token, base_url)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Airtable client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Airtable."""
        try:
            config = await config_service.get_config("/services/connectors/airtable/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Airtable connector config: {e}")
            return {}
