from dataclasses import asdict, dataclass

from atlassian import Confluence  # type: ignore

from app.agents.client.iclient import IClient
from app.config.configuration_service import ConfigurationService


class ConfluenceConfigBase:
    def create_client(self) -> Confluence: # type: ignore
        raise NotImplementedError # pragma: no cover

@dataclass
class ConfluenceUsernamePasswordConfig(ConfluenceConfigBase):
    base_url: str
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> Confluence:
        return Confluence(url=self.base_url, username=self.username, password=self.password, ssl=self.ssl)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class ConfluenceTokenConfig(ConfluenceConfigBase):
    base_url: str
    token: str
    ssl: bool = False

    def create_client(self) -> Confluence:
        return Confluence(url=self.base_url, token=self.token, ssl=self.ssl)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class ConfluenceApiKeyConfig(ConfluenceConfigBase):
    base_url: str
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> Confluence:
        return Confluence(url=self.base_url, email=self.email, api_key=self.api_key, ssl=self.ssl)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class ConfluenceClient(IClient):
    """Builder class for Confluence clients with different construction methods"""

    def __init__(self, client: object) -> None:
        """Initialize with a Confluence client object"""
        self.client = client

    def get_client(self) -> object:
        """Return the Confluence client object"""
        return self.client

    @classmethod
    def build_with_client(cls, client: object) -> 'ConfluenceClient':
        """
        Build ConfluenceClient with an already authenticated client
        Args:
            client: Authenticated Confluence client object
        Returns:
            ConfluenceClient instance
        """
        return cls(client)

    @classmethod
    def build_with_config(cls, config: ConfluenceConfigBase) -> 'ConfluenceClient':
        """
        Build ConfluenceClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: ConfluenceConfigBase instance
        Returns:
            ConfluenceClient instance with placeholder implementation
        """
        # TODO: Implement OAuth2 flow and enterprise account authentication
        # For now, return a placeholder client
        placeholder_client = None  # This will be implemented later
        return cls(placeholder_client)

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> 'ConfluenceClient':
        """
        Build ConfluenceClient using configuration service and arango service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
        Returns:
            ConfluenceClient instance
        """
        return cls(client=None)

