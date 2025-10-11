import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient
from app.sources.external.common.atlassian import AtlassianCloudResource


class JiraRESTClientViaUsernamePassword(HTTPClient):
    """JIRA REST client via username and password
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

class JiraRESTClientViaApiKey(HTTPClient):
    """JIRA REST client via API key
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

class JiraRESTClientViaToken(HTTPClient):
    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.base_url = base_url

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

@dataclass
class JiraUsernamePasswordConfig:
    """Configuration for JIRA REST client via username and password
    Args:
        base_url: The base URL of the JIRA instance
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL
    """

    base_url: str
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> JiraRESTClientViaUsernamePassword:
        return JiraRESTClientViaUsernamePassword(self.base_url, self.username, self.password, "Basic")

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class JiraTokenConfig:
    """Configuration for JIRA REST client via token
    Args:
        base_url: The base URL of the JIRA instance
        token: The token to use for authentication
        ssl: Whether to use SSL
    """

    base_url: str
    token: str
    ssl: bool = False

    def create_client(self) -> JiraRESTClientViaToken:
        return JiraRESTClientViaToken(self.base_url, self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class JiraApiKeyConfig:
    """Configuration for JIRA REST client via API key
    Args:
        base_url: The base URL of the JIRA instance
        email: The email to use for authentication
        api_key: The API key to use for authentication
        ssl: Whether to use SSL
    """

    base_url: str
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> JiraRESTClientViaApiKey:
        return JiraRESTClientViaApiKey(self.base_url, self.email, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

class JiraClient(IClient):
    """Builder class for JIRA clients with different construction methods"""

    def __init__(self, client: JiraRESTClientViaUsernamePassword | JiraRESTClientViaApiKey | JiraRESTClientViaToken) -> None:
        """Initialize with a JIRA client object"""
        self.client = client

    def get_client(self) -> JiraRESTClientViaUsernamePassword | JiraRESTClientViaApiKey | JiraRESTClientViaToken:
        """Return the JIRA client object"""
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
        resources = await JiraClient.get_accessible_resources(token)
        if not resources:
            raise Exception("No accessible resources found")
        return resources[0].id

    @staticmethod
    async def get_jira_base_url(token: str) -> str:
        """Get the Jira base URL using cloud ID
        Args:
            token: The authentication token
        Returns:
            Jira base URL string
        """
        cloud_id = await JiraClient.get_cloud_id(token)
        return f"https://api.atlassian.com/ex/jira/{cloud_id}"

    @classmethod
    def build_with_config(cls, config: JiraUsernamePasswordConfig | JiraTokenConfig | JiraApiKeyConfig) -> "JiraClient":
        """Build JiraClient with configuration (placeholder for future OAuth2/enterprise support)

        Args:
            config: JiraConfigBase instance
        Returns:
            JiraClient instance with placeholder implementation

        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "JiraClient":
        """Build JiraClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            JiraClient instance
        """
        try:
            # Get Jira configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                raise ValueError("Failed to get Jira connector configuration")
            auth_config = config.get("auth",{}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Jira connector configuration")

            credentials_config = config.get("credentials",{}) or {}
            if not credentials_config:
                raise ValueError("Credentials configuration not found in Jira connector configuration")

            # Extract configuration values
            auth_type = auth_config.get("authType", "BEARER_TOKEN")  # token, username_password, api_key


            # Create appropriate client based on auth type
            # to be implemented
            # if auth_type == "USERNAME_PASSWORD":
            #     username = auth_config.get("username", "")
            #     password = auth_config.get("password", "")
            #     if not username or not password:
            #         raise ValueError("Username and password required for username_password auth type")
            #     client = JiraRESTClientViaUsernamePassword(base_url, username, password)

            # # to be implemented
            # elif auth_type == "API_KEY":
            #     email = auth_config.get("email", "")
            #     api_key = auth_config.get("api_key", "")
            #     if not email or not api_key:
            #         raise ValueError("Email and API key required for api_key auth type")
            #     # Get base URL using the token
            #     base_url = await cls.get_jira_base_url(token)

            #     if not base_url:
            #         raise ValueError("Jira base_url not found in configuration")

            #     client = JiraRESTClientViaApiKey(base_url, email, api_key)

            if auth_type == "BEARER_TOKEN":  # Default to token auth
                token = auth_config.get("bearerToken", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                # Get base URL using the token
                base_url = await cls.get_jira_base_url(token)

                if not base_url:
                    raise ValueError("Jira base_url not found in configuration")

                client = JiraRESTClientViaToken(base_url, token)
            elif auth_type == "OAUTH":
                access_token = credentials_config.get("access_token", "")
                if not access_token:
                    raise ValueError("Access token required for OAuth auth type")
                # Get base URL using the token
                base_url = await cls.get_jira_base_url(access_token)

                if not base_url:
                    raise ValueError("Jira base_url not found in configuration")

                client = JiraRESTClientViaToken(base_url, access_token)
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Jira client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Jira."""
        try:
            config = await config_service.get_config("/services/connectors/jira/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Jira connector config: {e}")
            return {}
