import base64
import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.api.routes.toolsets import get_toolset_by_id
from app.config.configuration_service import ConfigurationService
from app.sources.client.http.exception.exception import HttpStatusCode
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient
from app.sources.external.common.atlassian import AtlassianCloudResource


class ConfluenceRESTClientViaUsernamePassword(HTTPClient):
    """Confluence REST client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """

    def __init__(self, base_url: str, username: str, password: str, token_type: str = "Basic") -> None:
        self.base_url = base_url
        #TODO: Implement
        pass

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

class ConfluenceRESTClientViaApiKey(HTTPClient):
    """Confluence REST client via API key (Basic auth with email:apiToken)

    Atlassian Cloud uses Basic authentication with email:apiToken format.
    The credentials are base64 encoded and sent as 'Basic <encoded>' header.

    Args:
        base_url: The base URL of the Confluence instance
        email: The email to use for authentication
        api_key: The API key/token to use for authentication
    """

    def __init__(self, base_url: str, email: str, api_key: str) -> None:
        credentials = base64.b64encode(f"{email}:{api_key}".encode()).decode()
        super().__init__(credentials, "Basic")
        self.base_url = base_url
        self.email = email

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class ConfluenceRESTClientViaToken(HTTPClient):
    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.base_url = base_url
        self.token = token

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

@dataclass
class ConfluenceUsernamePasswordConfig:
    """Configuration for Confluence REST client via username and password
    Args:
        base_url: The base URL of the Confluence instance
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL
    """

    base_url: str
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> ConfluenceRESTClientViaUsernamePassword:
        return ConfluenceRESTClientViaUsernamePassword(self.base_url, self.username, self.password, "Basic")

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ConfluenceTokenConfig:
    """Configuration for Confluence REST client via token
    Args:
        base_url: The base URL of the Confluence instance
        token: The token to use for authentication
        ssl: Whether to use SSL
    """

    base_url: str
    token: str
    ssl: bool = False

    def create_client(self) -> ConfluenceRESTClientViaToken:
        # Normalize base URL to include /wiki/api/v2 if not present
        base_url = self.base_url.rstrip('/')
        if not base_url.endswith('/wiki/api/v2'):
            base_url = f"{base_url}/wiki/api/v2"
        return ConfluenceRESTClientViaToken(base_url, self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ConfluenceApiKeyConfig:
    """Configuration for Confluence REST client via API key
    Args:
        base_url: The base URL of the Confluence instance
        email: The email to use for authentication
        api_key: The API key to use for authentication
        ssl: Whether to use SSL
    """

    base_url: str
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> ConfluenceRESTClientViaApiKey:
        # Normalize base URL to include /wiki/api/v2 if not present
        base_url = self.base_url.rstrip('/')
        if not base_url.endswith('/wiki/api/v2'):
            base_url = f"{base_url}/wiki/api/v2"
        return ConfluenceRESTClientViaApiKey(base_url, self.email, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class ConfluenceClient(IClient):
    """Builder class for Confluence clients with different construction methods"""

    def __init__(
        self,
        client: ConfluenceRESTClientViaUsernamePassword | ConfluenceRESTClientViaApiKey | ConfluenceRESTClientViaToken,
    ) -> None:
        """Initialize with a Confluence client object"""
        self.client = client

    def get_client(self) -> ConfluenceRESTClientViaUsernamePassword | ConfluenceRESTClientViaApiKey | ConfluenceRESTClientViaToken:
        """Return the Confluence client object"""
        return self.client

    @staticmethod
    async def get_accessible_resources(token: str) -> list[AtlassianCloudResource]:
        """Get list of Atlassian sites (Confluence/Jira instances) accessible to the user
        Args:
            token: The authentication token
        Returns:
            List of accessible Atlassian Cloud resources
        """
        RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"

        if not token:
            raise ValueError("No token provided for resource fetching")

        http_client = HTTPClient(token, "Bearer")
        request = HTTPRequest(
            url=RESOURCE_URL,
            method="GET",
            headers={"Content-Type": "application/json"}
        )

        try:
            response = await http_client.execute(request)

            # Check if the response is successful
            if response.status != HttpStatusCode.SUCCESS.value:
                raise Exception(f"API request failed with status {response.status}: {response.text()}")

            # Try to parse JSON response
            try:
                response_data = response.json()
            except Exception as json_error:
                raise Exception(f"Failed to parse JSON response: {json_error}. Response: {response.text()}")

            # Check if response_data is a list
            if not isinstance(response_data, list):
                raise Exception(f"Expected list of resources, got {type(response_data)}: {response_data}")

            return [
                AtlassianCloudResource(
                    id=resource["id"],
                    name=resource.get("name", ""),
                    url=resource["url"],
                    scopes=resource.get("scopes", []),
                    avatar_url=resource.get("avatarUrl"),
                )
                for resource in response_data
            ]
        except Exception as e:
            raise Exception(f"Failed to fetch accessible resources: {str(e)}") from e

    @staticmethod
    async def get_cloud_id(token: str) -> str:
        """Get the first available cloud ID from accessible resources
        Args:
            token: The authentication token
        Returns:
            Cloud ID string
        """
        resources = await ConfluenceClient.get_accessible_resources(token)
        if not resources:
            raise Exception("No accessible resources found")
        return resources[0].id

    @staticmethod
    async def get_confluence_base_url(token: str) -> str:
        """Get the Confluence base URL using cloud ID
        Args:
            token: The authentication token
        Returns:
            Confluence base URL string
        """
        cloud_id = await ConfluenceClient.get_cloud_id(token)
        # Confluence Cloud v2 endpoints live under /wiki/api/v2
        return f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2"

    @classmethod
    def build_with_config(
        cls,
        config: ConfluenceUsernamePasswordConfig | ConfluenceTokenConfig | ConfluenceApiKeyConfig,
    ) -> "ConfluenceClient":
        """Build ConfluenceClient with configuration (placeholder for future OAuth2/enterprise support)

        Args:
            config: ConfluenceConfigBase instance
        Returns:
            ConfluenceClient instance with placeholder implementation

        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "ConfluenceClient":
        """Build ConfluenceClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            ConfluenceClient instance
        """
        try:
            # Get Confluence configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service, connector_instance_id)
            if not config:
                raise ValueError("Failed to get Confluence connector configuration")
            auth_config = config.get("auth",{}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Confluence connector configuration")

            # Extract configuration values
            auth_type = auth_config.get("authType", "BEARER_TOKEN")

            # Create appropriate client based on auth type
            if auth_type == "API_TOKEN":
                # API Token authentication - uses Basic auth with email:apiToken
                base_url = auth_config.get("baseUrl", "").strip()
                email = auth_config.get("email", "").strip()
                api_token = auth_config.get("apiToken", "").strip()

                if not base_url:
                    raise ValueError("Base URL is required for API_TOKEN auth")
                if not email or not api_token:
                    raise ValueError("Email and API token are required for API_TOKEN auth")

                # Normalize base URL and append Confluence API v2 path if needed
                base_url = base_url.rstrip('/')
                if not base_url.endswith('/wiki/api/v2'):
                    base_url = f"{base_url}/wiki/api/v2"

                client = ConfluenceRESTClientViaApiKey(base_url, email, api_token)

            elif auth_type == "BEARER_TOKEN":  # Default to token auth
                token = auth_config.get("bearerToken", "")
                if not token:
                    raise ValueError("Token required for token auth type")

                # Get base URL using the token
                base_url = await cls.get_confluence_base_url(token)

                if not base_url:
                    raise ValueError("Confluence base_url not found in configuration")

                client = ConfluenceRESTClientViaToken(base_url, token)
            elif auth_type == "OAUTH":
                credentials_config = config.get("credentials", {}) or {}
                if not credentials_config:
                    raise ValueError("Credentials configuration not found for OAuth auth type")
                access_token = credentials_config.get("access_token", "")
                if not access_token:
                    raise ValueError("Access token required for OAuth auth type")

                # Get base URL using the token
                base_url = await cls.get_confluence_base_url(access_token)

                if not base_url:
                    raise ValueError("Confluence base_url not found in configuration")

                client = ConfluenceRESTClientViaToken(base_url, access_token)
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Confluence client from services: {str(e)}")
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: Optional[ConfigurationService] = None,
    ) -> "ConfluenceClient":
        """
        Build ConfluenceClient using toolset configuration from etcd.

        NEW ARCHITECTURE: This method uses toolset configs directly instead of
        connector instance configs. Toolset configs are stored per-user at:
        /services/toolsets/{user_id}/{toolset_type}

        Args:
            toolset_config: Toolset configuration dictionary from etcd
            logger: Logger instance
            config_service: Configuration service for fetching instance config

        Returns:
            ConfluenceClient instance
        """
        try:
            if not toolset_config:
                raise ValueError("Toolset config is required for Confluence client")

            # Extract auth configuration from toolset config
            credentials_config = toolset_config.get("credentials", {}) or {}
            auth_type = toolset_config.get("authType", "BEARER_TOKEN")

            # Create appropriate client based on auth type
            if auth_type == "BEARER_TOKEN":
                token = toolset_config.get("bearerToken", "")
                if not token:
                    raise ValueError("Token required for bearer token auth type")

                # Get base URL using the token
                base_url = await cls.get_confluence_base_url(token)
                if not base_url:
                    raise ValueError("Confluence base_url not found in configuration")

                client = ConfluenceRESTClientViaToken(base_url, token)

            elif auth_type == "OAUTH":
                access_token = credentials_config.get("access_token", "")
                if not access_token:
                    raise ValueError("Access token required for OAuth auth type")

                # Get base URL using the token
                base_url = await cls.get_confluence_base_url(access_token)
                if not base_url:
                    raise ValueError("Confluence base_url not found in configuration")

                client = ConfluenceRESTClientViaToken(base_url, access_token)

            elif auth_type == "API_TOKEN":
                # API Token authentication - fetch instance config for CONFIGURE fields,
                # use toolset_config for AUTHENTICATE fields (like MariaDB pattern)
                instance_id = toolset_config.get("instanceId")
                if not instance_id:
                    raise ValueError("instanceId is required for API_TOKEN auth")

                if not config_service:
                    raise ValueError("config_service is required for API_TOKEN auth")

                # Fetch instance config to get CONFIGURE-level fields (baseUrl)
                confluence_instance = await get_toolset_by_id(instance_id, config_service)
                if not confluence_instance:
                    raise ValueError(f"Confluence instance '{instance_id}' not found")

                # Get baseUrl from instance config (CONFIGURE field)
                instance_auth = confluence_instance.get("auth", {})
                base_url = instance_auth.get("baseUrl", "").strip()

                # Get email and apiToken from user config (AUTHENTICATE fields)
                user_auth = toolset_config.get("auth", {})
                email = user_auth.get("email", "").strip()
                api_token = user_auth.get("apiToken", "").strip()

                if not base_url:
                    raise ValueError("Base URL is required. Admin must configure the Atlassian instance URL.")
                if not email or not api_token:
                    raise ValueError("Email and API token are required for API_TOKEN auth")

                # Normalize base URL and append Confluence API v2 path
                base_url = base_url.rstrip('/')
                if not base_url.endswith('/wiki/api/v2'):
                    base_url = f"{base_url}/wiki/api/v2"

                client = ConfluenceRESTClientViaApiKey(base_url, email, api_token)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}. Supported: OAUTH, API_TOKEN, BEARER_TOKEN")

            logger.info(f"Built Confluence client from toolset config with auth type: {auth_type}")
            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Confluence client from toolset config: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> dict[str, Any]:
        """Fetch connector config from etcd for Confluence."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get Confluence connector configuration for instance {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to get Confluence connector config: {e}")
            raise ValueError(f"Failed to get Confluence connector configuration for instance {connector_instance_id}")
