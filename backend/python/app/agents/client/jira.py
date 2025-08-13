from dataclasses import asdict, dataclass

from atlassian import JIRA  # type: ignore

from app.agents.client.iclient import IClient
from app.config.configuration_service import ConfigurationService


class JiraConfigBase:
    def create_client(self) -> JIRA: # type: ignore
        raise NotImplementedError

@dataclass
class JiraUsernamePasswordConfig(JiraConfigBase):
    base_url: str
    username: str
    password: str

    def create_client(self) -> JIRA:
        return JIRA(server=self.base_url, basic_auth=(self.username, self.password))

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class JiraTokenConfig(JiraConfigBase):
    base_url: str
    token: str

    def create_client(self) -> JIRA:
        return JIRA(server=self.base_url, token_auth=self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

class JiraClient(IClient):
    """Builder class for Jira clients with different construction methods"""

    def __init__(self, client: object) -> None:
        """Initialize with a Jira client object"""
        self.client = client

    def get_client(self) -> object:
        """Return the Jira client object"""
        return self.client

    @classmethod
    def build_with_client(cls, client: object) -> 'JiraClient':
        """
        Build JiraClient with an already authenticated client
        Args:
            client: Authenticated Jira client object
        Returns:
            JiraClient instance
        """
        return cls(client)

    @classmethod
    def build_with_config(cls, config: JiraConfigBase) -> 'JiraClient':
        """
        Build JiraClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: JiraConfigBase instance
        Returns:
            JiraClient instance with placeholder implementation
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
    ) -> 'JiraClient':
        """
        Build JiraClient using configuration service and arango service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
        Returns:
            JiraClient instance
        """
        return cls(client=None)

