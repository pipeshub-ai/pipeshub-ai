from dataclasses import asdict, dataclass
from typing import Union

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class ServiceNowClientViaUsernamePassword(HTTPClient):
    """ServiceNow REST client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """
    def __init__(self, base_url: str, username: str, password: str, token_type: str = "Basic") -> None:
        #TODO: Implement
        pass

class ServiceNowClientViaApiKey(HTTPClient):
    """ServiceNow REST client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
    """
    def __init__(self, base_url: str, email: str, api_key: str) -> None:
        #TODO: Implement
        pass

class ServiceNowClientViaToken(HTTPClient):
    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(base_url, token, token_type)

@dataclass
class ServiceNowUsernamePasswordConfig:
    """Configuration for ServiceNow REST client via username and password
    Args:
        base_url: The base URL of the ServiceNow instance
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> ServiceNowClientViaUsernamePassword:
        return ServiceNowClientViaUsernamePassword(self.base_url, self.username, self.password, "Basic")

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class ServiceNowTokenConfig():
    """Configuration for ServiceNow REST client via token
    Args:
        base_url: The base URL of the ServiceNow instance
        token: The token to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    token: str
    ssl: bool = False

    def create_client(self) -> ServiceNowClientViaToken:
        return ServiceNowClientViaToken(self.base_url, self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class ServiceNowApiKeyConfig():
    """Configuration for ServiceNow REST client via API key
    Args:
        base_url: The base URL of the ServiceNow instance
        email: The email to use for authentication
        api_key: The API key to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> ServiceNowClientViaApiKey:
        return ServiceNowClientViaApiKey(self.base_url, self.email, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

class ServiceNowClient(IClient):
    """Builder class for ServiceNow clients with different construction methods"""

    def __init__(self, client: Union[ServiceNowClientViaUsernamePassword, ServiceNowClientViaApiKey, ServiceNowClientViaToken]) -> None:
        """Initialize with a ServiceNow client object"""
        self.client = client

    def get_client(self) -> Union[ServiceNowClientViaUsernamePassword, ServiceNowClientViaApiKey, ServiceNowClientViaToken]:
        """Return the ServiceNow client object"""
        return self.client

    @classmethod
    def build_with_config(cls, config: Union[ServiceNowUsernamePasswordConfig, ServiceNowTokenConfig, ServiceNowApiKeyConfig]) -> 'ServiceNowClient':
        """
        Build ServiceNowClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: ServiceNowConfigBase instance
        Returns:
            ServiceNowClient instance with placeholder implementation
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> 'ServiceNowClient':
        """
        Build ServiceNowClient using configuration service and arango service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
        Returns:
            ServiceNowClient instance with placeholder implementation # noqa: S101
        """
        #TODO: Implement
        return cls(client=None) #type:ignore # noqa: S101
