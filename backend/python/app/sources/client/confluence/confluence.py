from dataclasses import asdict, dataclass

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


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
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> "ConfluenceClient":
        """Build ConfluenceClient using configuration service and arango service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
        Returns:
            ConfluenceClient instance
        """
        #TODO: Implement
        return cls(client=None)#type:ignore
