from dataclasses import asdict, dataclass
from typing import Any, List, Optional

from app.config.configuration_service import ConfigurationService

try:
    from azure.identity.aio import ClientSecretCredential  #type: ignore
    from msgraph import GraphServiceClient  #type: ignore
except ImportError:
    raise ImportError("azure-identity is not installed. Please install it with `pip install azure-identity`")

from app.sources.client.iclient import IClient


@dataclass
class MSGraphResponse:
    """Standardized response wrapper for Microsoft Graph operations."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate response state."""
        if self.success and self.error:
            raise ValueError("Response cannot be successful and have an error")


class MSGraphClientViaUsernamePassword():
    """Microsoft Graph client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """
    def __init__(self, username: str, password: str, token_type: str = "Basic") -> None:
        #TODO: Implement
        pass

class MSGraphClientViaApiKey():
    """Microsoft Graph client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
    """
    def __init__(self, email: str, api_key: str) -> None:
        #TODO: Implement
        pass

class MSGraphClientViaToken():
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        scopes: List[str] = ["https://graph.microsoft.com/.default"]
    ) -> None:
        credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
        self.client = GraphServiceClient(credential, scopes=scopes)

@dataclass
class MSGraphUsernamePasswordConfig():
    """Configuration for Microsoft Graph client via username and password
    Args:
        base_url: The base URL of the Microsoft Graph instance
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> MSGraphClientViaUsernamePassword:
        return MSGraphClientViaUsernamePassword(self.username, self.password, "Basic")

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class MSGraphTokenConfig():
    """Configuration for Microsoft Graph client via token
    Args:
        base_url: The base URL of the Microsoft Graph instance
        token: The token to use for authentication
        ssl: Whether to use SSL
    """
    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str

    def create_client(self) -> MSGraphClientViaToken:
        return MSGraphClientViaToken(self.azure_client_id, self.azure_client_secret, self.azure_tenant_id)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class MSGraphApiKeyConfig():
    """Configuration for Microsoft Graph client via API key
    Args:
        base_url: The base URL of the Microsoft Graph instance
        email: The email to use for authentication
        api_key: The API key to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> MSGraphClientViaApiKey:
        return MSGraphClientViaApiKey(self.email, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

class MSGraphClient(IClient):
    """Builder class for Microsoft Graph clients with different construction methods"""

    def __init__(self, client: MSGraphClientViaUsernamePassword | MSGraphClientViaApiKey | MSGraphClientViaToken) -> None:
        """Initialize with a Microsoft Graph client object"""
        self.client = client

    def get_client(self) -> MSGraphClientViaUsernamePassword | MSGraphClientViaApiKey | MSGraphClientViaToken:
        """Return the Microsoft Graph client object"""
        return self.client

    @classmethod
    def build_with_config(cls, config: MSGraphUsernamePasswordConfig | MSGraphTokenConfig | MSGraphApiKeyConfig) -> 'MSGraphClient':
        """
        Build MSGraphClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: MSGraphConfigBase instance
        Returns:
            MSGraphClient instance with placeholder implementation
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
    ) -> 'MSGraphClient':
        """
        Build MSGraphClient using configuration service and arango service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
        Returns:
            MSGraphClient instance
        """
        #TODO: Implement
        return cls(client=None) #type:ignore
