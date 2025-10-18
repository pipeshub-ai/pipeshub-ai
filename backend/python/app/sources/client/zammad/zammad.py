import base64
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class ZammadResponse(BaseModel):
    """Standardized Zammad API response wrapper"""
    success: bool
    data: Optional[Union[Dict[str, Any], List[Any]]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class ZammadRESTClientViaUsernamePassword(HTTPClient):
    """Zammad REST client via HTTP Basic Authentication (username/password)
    Note: This authentication method can be disabled and may not be available in your system.
    We strongly suggest against using basic authentication. Use access tokens when ever possible!
    Args:
        base_url: The base URL of the Zammad instance (FQDN)
        username: The username to use for authentication
        password: The password to use for authentication
    """
    def __init__(self, base_url: str, username: str, password: str) -> None:
        if not base_url:
            raise ValueError("Zammad base_url cannot be empty")
        if not username:
            raise ValueError("Zammad username cannot be empty")
        if not password:
            raise ValueError("Zammad password cannot be empty")

        # Remove trailing slash from base_url if present
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password

        # Create Basic auth credentials
        credentials = f"{username}:{password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        # Initialize parent with empty token since we're using Basic auth
        super().__init__("", "Basic")

        # Override headers with Basic auth
        self.headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json"
        }

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class ZammadRESTClientViaToken(HTTPClient):
    """Zammad REST client via HTTP Token Authentication (access token)
    The access token must be provided as HTTP header in the HTTP call.
    Each user can create several access tokens in their user preferences.
    This authentication method can be disabled and may not be available in your system.
    Args:
        base_url: The base URL of the Zammad instance (FQDN)
        token: The access token to use for authentication
    """
    def __init__(self, base_url: str, token: str) -> None:
        if not base_url:
            raise ValueError("Zammad base_url cannot be empty")
        if not token:
            raise ValueError("Zammad token cannot be empty")

        # Remove trailing slash from base_url if present
        self.base_url = base_url.rstrip('/')
        self.token = token

        # Initialize parent with custom token type for Zammad
        super().__init__(token, "Token token=")

        # Override headers to use Zammad's token format
        self.headers = {
            "Authorization": f"Token token={token}",
            "Content-Type": "application/json"
        }

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class ZammadRESTClientViaOAuth2(HTTPClient):
    """Zammad REST client via OAuth2 (token access)
    The token must be provided as HTTP header in your calls.
    This allows 3rd party applications to authenticate against Zammad.
    Args:
        base_url: The base URL of the Zammad instance (FQDN)
        bearer_token: The OAuth2 bearer token to use for authentication
    """
    def __init__(self, base_url: str, bearer_token: str) -> None:
        if not base_url:
            raise ValueError("Zammad base_url cannot be empty")
        if not bearer_token:
            raise ValueError("Zammad bearer_token cannot be empty")

        # Remove trailing slash from base_url if present
        self.base_url = base_url.rstrip('/')
        self.bearer_token = bearer_token

        # Initialize parent with Bearer token
        super().__init__(bearer_token, "Bearer")

        # Headers are already set correctly by parent class
        self.headers.update({
            "Content-Type": "application/json"
        })

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


@dataclass
class ZammadUsernamePasswordConfig:
    """Configuration for Zammad REST client via username and password
    Args:
        base_url: The base URL of the Zammad instance
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL (deprecated, base_url should include https://)
    """
    base_url: str
    username: str
    password: str
    ssl: bool = Field(default=True, description="Whether to use SSL")

    def create_client(self) -> ZammadRESTClientViaUsernamePassword:
        return ZammadRESTClientViaUsernamePassword(self.base_url, self.username, self.password)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ZammadTokenConfig:
    """Configuration for Zammad REST client via access token
    Args:
        base_url: The base URL of the Zammad instance
        token: The access token to use for authentication
        ssl: Whether to use SSL (deprecated, base_url should include https://)
    """
    base_url: str
    token: str
    ssl: bool = Field(default=True, description="Whether to use SSL")

    def create_client(self) -> ZammadRESTClientViaToken:
        return ZammadRESTClientViaToken(self.base_url, self.token)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ZammadOAuth2Config:
    """Configuration for Zammad REST client via OAuth2
    Args:
        base_url: The base URL of the Zammad instance
        bearer_token: The OAuth2 bearer token to use for authentication
        ssl: Whether to use SSL (deprecated, base_url should include https://)
    """
    base_url: str
    bearer_token: str
    ssl: bool = Field(default=True, description="Whether to use SSL")

    def create_client(self) -> ZammadRESTClientViaOAuth2:
        return ZammadRESTClientViaOAuth2(self.base_url, self.bearer_token)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class ZammadClient(IClient):
    """Builder class for Zammad clients with different authentication methods
    Supports three authentication methods:
    1. HTTP Basic Authentication (username/password) - NOT RECOMMENDED
    2. HTTP Token Authentication (access token) - RECOMMENDED
    3. OAuth2 (bearer token)
    """

    def __init__(
        self,
        client: ZammadRESTClientViaUsernamePassword | ZammadRESTClientViaToken | ZammadRESTClientViaOAuth2
    ) -> None:
        """Initialize with a Zammad client object
        Args:
            client: Zammad REST client instance
        """
        self.client = client

    def get_client(self) -> ZammadRESTClientViaUsernamePassword | ZammadRESTClientViaToken | ZammadRESTClientViaOAuth2:
        """Return the underlying Zammad client object
        Returns:
            Zammad REST client instance
        """
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL of the Zammad instance
        Returns:
            Base URL string
        """
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: ZammadUsernamePasswordConfig | ZammadTokenConfig | ZammadOAuth2Config,
    ) -> "ZammadClient":
        """Build ZammadClient with configuration
        Args:
            config: Zammad configuration instance (dataclass)
        Returns:
            ZammadClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "ZammadClient":
        """Build ZammadClient using configuration service
        This method fetches Zammad configuration from the configuration service
        and creates the appropriate client based on the authentication type.
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            ZammadClient instance
        Raises:
            ValueError: If configuration is invalid or missing required fields
        """
        try:
            # Get Zammad configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Zammad connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Zammad connector configuration")

            base_url = config.get("base_url") or config.get("baseUrl")
            if not base_url:
                raise ValueError("Base URL not found in Zammad connector configuration")

            # Get authentication type
            auth_type = auth_config.get("authType", "TOKEN")

            # Create appropriate client based on auth type
            if auth_type == "USERNAME_PASSWORD" or auth_type == "BASIC":
                username = auth_config.get("username", "")
                password = auth_config.get("password", "")

                if not username or not password:
                    raise ValueError("Username and password required for basic auth type")

                client = ZammadRESTClientViaUsernamePassword(base_url, username, password)
                logger.warning("Using Basic Authentication for Zammad. Consider using Token authentication instead.")

            elif auth_type == "TOKEN" or auth_type == "API_TOKEN":
                token = auth_config.get("token", "")

                if not token:
                    raise ValueError("Token required for token auth type")

                client = ZammadRESTClientViaToken(base_url, token)

            elif auth_type == "OAUTH2" or auth_type == "BEARER":
                bearer_token = auth_config.get("bearerToken") or auth_config.get("bearer_token", "")

                if not bearer_token:
                    raise ValueError("Bearer token required for OAuth2 auth type")

                client = ZammadRESTClientViaOAuth2(base_url, bearer_token)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}. Must be one of: USERNAME_PASSWORD, TOKEN, OAUTH2")

            logger.info(f"Successfully created Zammad client with {auth_type} authentication")
            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Zammad client from services: {e}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> Dict[str, Any]:
        """Fetch connector config from configuration service for Zammad
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            Configuration dictionary
        """
        try:
            config = await config_service.get_config("/services/connectors/zammad/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Zammad connector config: {e}")
            return {}
