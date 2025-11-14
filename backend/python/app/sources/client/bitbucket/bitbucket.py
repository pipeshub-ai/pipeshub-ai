import base64
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


@dataclass
class BitbucketResponse:
    """Standardized Bitbucket API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        import json
        return json.dumps(self.to_dict())


class BitbucketRESTClientViaToken(HTTPClient):
    """Bitbucket REST client via API Token (Personal Access Token)

    Bitbucket API tokens are personal access tokens that can be created in user settings.
    They use Bearer authentication.

    Args:
        token: The personal access token
        base_url: The base URL of the Bitbucket API
    """

    def __init__(self, token: str, base_url: str = "https://api.bitbucket.org/2.0") -> None:
        super().__init__(token, "Bearer")
        self.base_url = base_url
        # Add Bitbucket-specific headers
        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

        self.headers

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class BitbucketRESTClientViaOAuth(HTTPClient):
    """Bitbucket REST client via OAuth 2.0

    Handles OAuth 2.0 flow for Bitbucket. Supports authorization code flow
    and token refresh.

    OAuth Documentation: https://developer.atlassian.com/cloud/bitbucket/oauth-2/

    Args:
        client_id: The OAuth client ID (consumer key)
        client_secret: The OAuth client secret (consumer secret)
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
        base_url: The base URL of the Bitbucket API
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        access_token: Optional[str] = None,
        base_url: str = "https://api.bitbucket.org/2.0"
    ) -> None:
        # Initialize with empty token first, will be set after OAuth flow
        super().__init__(access_token or "", "Bearer")

        self.base_url = base_url
        self.oauth_base_url = "https://bitbucket.org/site/oauth2"
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.access_token = access_token

        # Add Bitbucket-specific headers
        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

        # If no access token provided, we'll need to go through OAuth flow
        self._oauth_completed = access_token is not None

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def is_oauth_completed(self) -> bool:
        """Check if OAuth flow has been completed"""
        return self._oauth_completed

    def get_authorization_url(
        self,
        state: Optional[str] = None,
        scope: str = "repository account"
    ) -> str:
        """Generate OAuth authorization URL

        Args:
            state: Optional state parameter for security (recommended)
            scope: OAuth scopes (space-separated). Common scopes:
                   - repository: Read/write repository data
                   - repository:write: Write access to repositories
                   - account: Read account information
                   - pullrequest: Read/write pull requests
                   - issue: Read/write issues
                   - wiki: Read/write wikis
        Returns:
            Authorization URL to redirect user to
        """
        params = {
            "client_id": self.client_id,
            "response_type": "code"
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
        # Bitbucket uses Basic auth with client credentials
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
            url=f"{self.oauth_base_url}/access_token",
            headers=headers,
            body=data
        )

        async with HTTPClient(token="") as client:
            response = await client.execute(request)

            if response.status >= HttpStatusCode.BAD_REQUEST.value:
                raise Exception(f"Token refresh failed with status {response.status}: {response.text}")

            token_data = response.json()

        self.access_token = token_data.get("access_token")

        # Update headers with new token
        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"

        return self.access_token

    async def _exchange_code_for_token(self, code: str) -> Optional[str]:
        """Exchange authorization code for access token

        Bitbucket OAuth token endpoint uses Basic authentication with
        client_id:client_secret and requires the authorization code.

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
            "code": code
        }

        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/access_token",
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

        return self.access_token


@dataclass
class BitbucketTokenConfig:
    """Configuration for Bitbucket REST client via Personal Access Token

    Args:
        token: The personal access token
        base_url: The base URL of the Bitbucket API
        ssl: Whether to use SSL (always True for Bitbucket Cloud)
    """
    token: str
    base_url: str = "https://api.bitbucket.org/2.0"
    ssl: bool = True

    def create_client(self) -> BitbucketRESTClientViaToken:
        return BitbucketRESTClientViaToken(self.token, self.base_url)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class BitbucketOAuthConfig:
    """Configuration for Bitbucket REST client via OAuth 2.0

    Args:
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
        base_url: The base URL of the Bitbucket API
        ssl: Whether to use SSL (always True for Bitbucket Cloud)
    """
    client_id: str
    client_secret: str
    redirect_uri: str
    access_token: Optional[str] = None
    base_url: str = "https://api.bitbucket.org/2.0"
    ssl: bool = True

    def create_client(self) -> BitbucketRESTClientViaOAuth:
        return BitbucketRESTClientViaOAuth(
            self.client_id,
            self.client_secret,
            self.redirect_uri,
            self.access_token,
            self.base_url
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class BitbucketClient(IClient):
    """Builder class for Bitbucket clients with different construction methods

    Supports two authentication methods:
    1. Personal Access Token (API Token) - for individual accounts
    2. OAuth 2.0 - for applications requiring user authorization
    """

    def __init__(
        self,
        client: BitbucketRESTClientViaToken | BitbucketRESTClientViaOAuth
    ) -> None:
        """Initialize with a Bitbucket client object"""
        self.client = client

    def get_client(self) -> BitbucketRESTClientViaToken | BitbucketRESTClientViaOAuth:
        """Return the Bitbucket client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: BitbucketTokenConfig | BitbucketOAuthConfig
    ) -> "BitbucketClient":
        """Build BitbucketClient with configuration

        Args:
            config: BitbucketTokenConfig or BitbucketOAuthConfig instance
        Returns:
            BitbucketClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "BitbucketClient":
        """Build BitbucketClient using configuration service

        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            BitbucketClient instance
        """
        try:
            # Get Bitbucket configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Bitbucket connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Bitbucket connector configuration")

            credentials_config = config.get("credentials", {}) or {}

            # Extract configuration values
            auth_type = auth_config.get("authType", "API_TOKEN")
            base_url = config.get("baseUrl", "https://api.bitbucket.org/2.0")

            # Create appropriate client based on auth type
            if auth_type == "API_TOKEN":
                token = auth_config.get("apiToken", "")
                if not token:
                    raise ValueError("API token required for API_TOKEN auth type")

                client = BitbucketRESTClientViaToken(token, base_url)

            elif auth_type == "OAUTH":
                # Check for existing access token first
                access_token = credentials_config.get("access_token", "")

                # Get OAuth credentials
                client_id = auth_config.get("clientId", "")
                client_secret = auth_config.get("clientSecret", "")
                redirect_uri = auth_config.get("redirectUri", "")

                if not client_id or not client_secret or not redirect_uri:
                    raise ValueError("Client ID, client secret, and redirect URI required for OAuth auth type")

                client = BitbucketRESTClientViaOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    redirect_uri=redirect_uri,
                    access_token=access_token,
                    base_url=base_url
                )

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Bitbucket client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Bitbucket."""
        try:
            config = await config_service.get_config("/services/connectors/bitbucket/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Bitbucket connector config: {e}")
            return {}
