import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

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
    """Confluence REST client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
    """

    def __init__(self, base_url: str, email: str, api_key: str) -> None:
        self.base_url = base_url
        #TODO: Implement
        pass

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class ConfluenceRESTClientViaToken(HTTPClient):
    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.base_url = base_url

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

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
        return ConfluenceRESTClientViaToken(self.base_url, self.token)

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
        return ConfluenceRESTClientViaApiKey(self.base_url, self.email, self.api_key)

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
    async def get_accessible_resources(token: str) -> List[AtlassianCloudResource]:
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
                raise Exception(f"API request failed with status {response.status}: {response.text}")

            # Try to parse JSON response
            try:
                response_data = response.json()
            except Exception as json_error:
                raise Exception(f"Failed to parse JSON response: {json_error}. Response: {response.text}")

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
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                raise ValueError("Failed to get Confluence connector configuration")
            auth_config = config.get("auth",{}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Confluence connector configuration")

            credentials_config = config.get("credentials",{}) or {}
            if not credentials_config:
                raise ValueError("Credentials configuration not found in Confluence connector configuration")

            # Extract configuration values
            auth_type = auth_config.get("authType", "BEARER_TOKEN")  # token, username_password, api_key

            # Create appropriate client based on auth type
            # to be implemented
            # if auth_type == "USERNAME_PASSWORD":
            #     username = auth_config.get("username", "")
            #     password = auth_config.get("password", "")
            #     if not username or not password:
            #         raise ValueError("Username and password required for username_password auth type")
            #     client = ConfluenceRESTClientViaUsernamePassword(base_url, username, password)

            # to be implemented
            # elif auth_type == "API_KEY":
            #     email = auth_config.get("email", "")
            #     api_key = auth_config.get("api_key", "")
            #     if not email or not api_key:
            #         raise ValueError("Email and API key required for api_key auth type")
            #     client = ConfluenceRESTClientViaApiKey(base_url, email, api_key)

            if auth_type == "BEARER_TOKEN":  # Default to token auth
                token = auth_config.get("bearerToken", "")
                if not token:
                    raise ValueError("Token required for token auth type")

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
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Confluence client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Confluence."""
        try:
            config = await config_service.get_config("/services/connectors/confluence/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Confluence connector config: {e}")
            return {}
