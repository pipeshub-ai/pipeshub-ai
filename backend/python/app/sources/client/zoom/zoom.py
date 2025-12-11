import base64
import logging
from dataclasses import asdict, dataclass
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.exception.exception import (
    HttpStatusCode as HttpStatusCodeEnum,
)
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


class ZoomRESTClientViaToken(HTTPClient):
    """Zoom REST client via access token
    Args:
        token: The access token to use for authentication
        token_type: The type of token to use for authentication (default: "Bearer")
    """

    def __init__(self, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.token = token
        self.base_url = "https://api.zoom.us/v2"

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_token(self) -> str:
        """Get the token"""
        return self.token

    def set_token(self, token: str) -> None:
        """Set the token"""
        self.token = token
        self.headers["Authorization"] = f"Bearer {token}"


class ZoomRESTClientViaOAuth(HTTPClient):
    """Zoom REST client via OAuth (Server-to-Server OAuth)

    Args:
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        account_id: The Zoom account ID
        redirect_uri: The OAuth redirect URI

    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        account_id: str,
        redirect_uri: str | None = None,
    ) -> None:
        # Initialize with empty token, will be set after OAuth flow
        super().__init__("", "Bearer")
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_id = account_id
        self.redirect_uri = redirect_uri or "http://localhost:3001/connectors/oauth/callback/Zoom"
        self.base_url = "https://api.zoom.us/v2"
        self.oauth_base_url = "https://zoom.us/oauth"
        self.access_token: str | None = None
        self._oauth_completed = False

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def is_oauth_completed(self) -> bool:
        """Check if OAuth flow is completed"""
        return self._oauth_completed

    async def get_access_token_via_server_to_server(self) -> str | None:
        """Get access token using Server-to-Server OAuth (account credentials grant)

        Returns:
            Access token from OAuth exchange

        """
        return await self._exchange_account_credentials_for_token()

    async def _exchange_account_credentials_for_token(self) -> str | None:
        """Exchange account credentials for access token using Server-to-Server OAuth
        Returns:
            Access token from OAuth exchange
        """
        # Create Basic Auth header
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        url = f"{self.oauth_base_url}/token?grant_type=account_credentials&account_id={self.account_id}"

        request = HTTPRequest(
            method="POST",
            url=url,
            headers={
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )

        try:
            response = await self.execute(request)

            # Check response status before parsing JSON
            if response.status >= HttpStatusCodeEnum.BAD_REQUEST.value:
                raise Exception(
                    f"Token exchange failed with status {response.status}: {response.text}",
                )

            token_data = response.json()
            self.access_token = token_data.get("access_token")

            # Update headers with new token
            if self.access_token:
                self.headers["Authorization"] = f"Bearer {self.access_token}"
                self._oauth_completed = True

            return self.access_token
        except Exception as e:
            raise ValueError(f"Failed to exchange account credentials for token: {e!s}") from e


@dataclass
class ZoomTokenConfig:
    """Configuration for Zoom REST client via token
    Args:
        token: The access token to use for authentication
    """

    token: str

    def create_client(self) -> ZoomRESTClientViaToken:
        return ZoomRESTClientViaToken(self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ZoomOAuthConfig:
    """Configuration for Zoom REST client via OAuth
    Args:
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        account_id: The Zoom account ID
        redirect_uri: The OAuth redirect URI (optional)
    """

    client_id: str
    client_secret: str
    account_id: str
    redirect_uri: str | None = None

    def create_client(self) -> ZoomRESTClientViaOAuth:
        return ZoomRESTClientViaOAuth(
            self.client_id, self.client_secret, self.account_id, self.redirect_uri,
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class ZoomClient(IClient):
    """Wrapper that can expose either an official SDK client or HTTP client.

    Zoom does not currently publish an official Python SDK. This wrapper
    therefore defaults to the HTTP client while leaving a hook for a future
    SDK-backed implementation.
    """

    def __init__(
        self,
        http_client: ZoomRESTClientViaToken | ZoomRESTClientViaOAuth | None,
        sdk_client: Any | None = None,
    ) -> None:
        """Initialize with a Zoom HTTP client and optional SDK client."""
        self.http_client = http_client
        self.sdk_client = sdk_client

    def get_client(
        self,
    ) -> ZoomRESTClientViaToken | ZoomRESTClientViaOAuth | Any:
        """Return the preferred client.

        If an SDK client is provided it is returned; otherwise the HTTP client
        is used. Downstream callers that require HTTP features should call
        `get_http_client` explicitly.
        """
        return self.sdk_client or self.http_client

    def get_http_client(self) -> ZoomRESTClientViaToken | ZoomRESTClientViaOAuth:
        """Return the HTTP client, raising if it is missing."""
        if self.http_client is None:
            raise ValueError("HTTP client is not configured for Zoom")
        return self.http_client

    def get_sdk_client(self) -> Any | None:
        """Return the SDK client if one is configured."""
        return self.sdk_client

    @classmethod
    def build_with_config(
        cls, config: ZoomTokenConfig | ZoomOAuthConfig,
    ) -> "ZoomClient":
        """Build ZoomClient with configuration."""
        return cls(config.create_client())

    @classmethod
    def build_with_sdk(cls, sdk_client: Any) -> "ZoomClient":
        """Build ZoomClient with an SDK client.

        This is a placeholder for when Zoom offers an official Python SDK.
        """
        if sdk_client is None:
            raise ValueError("Zoom SDK client is required to build with SDK")
        return cls(http_client=None, sdk_client=sdk_client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "ZoomClient":
        """Build ZoomClient using configuration service.
        """
        try:
            # Get Zoom configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                raise ValueError("Failed to get Zoom connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Zoom connector configuration")

            # Extract configuration values
            auth_type = auth_config.get("authType", "OAUTH")  # OAUTH or TOKEN

            # Create appropriate client based on auth type
            if auth_type == "TOKEN":
                token = auth_config.get("accessToken", "")
                if not token:
                    raise ValueError("Access token required for token auth type")
                http_client = ZoomRESTClientViaToken(token)
            elif auth_type == "OAUTH":
                client_id = auth_config.get("clientId", "")
                client_secret = auth_config.get("clientSecret", "")
                account_id = auth_config.get("accountId", "")
                redirect_uri = auth_config.get("redirectUri", "")

                if not client_id or not client_secret or not account_id:
                    raise ValueError(
                        "Client ID, Client Secret, and Account ID required for OAuth auth type",
                    )

                http_client = ZoomRESTClientViaOAuth(
                    client_id, client_secret, account_id, redirect_uri,
                )

                # For Server-to-Server OAuth, get access token immediately
                await http_client.get_access_token_via_server_to_server()
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(http_client=http_client)

        except Exception as e:
            logger.error(f"Failed to build Zoom client from services: {e!s}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger, config_service: ConfigurationService,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Zoom."""
        try:
            config = await config_service.get_config("/services/connectors/zoom/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Zoom connector config: {e}")
            return {}
