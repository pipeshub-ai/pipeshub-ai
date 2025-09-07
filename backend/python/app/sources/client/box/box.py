from dataclasses import asdict, dataclass

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

class BoxRESTClientViaAccessToken(HTTPClient):
    """Box REST client via Access Token
    Args:
        access_token: The OAuth2 access token for Box API
    """
    def __init__(self, base_url: str, access_token: str, token_type: str = "Bearer") -> None:
        super().__init__(access_token, token_type)
        self.base_url = base_url
        self.access_token = access_token

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


@dataclass
class BoxAccessTokenConfig:
    """Configuration for Box REST client via access token
    Args:
        base_url: The base URL of the Box API
        access_token: The OAuth2 access token
    """
    base_url: str
    access_token: str

    def create_client(self) -> BoxRESTClientViaAccessToken:
        return BoxRESTClientViaAccessToken(self.base_url, self.access_token)

    def to_dict(self) -> dict:
        return asdict(self)


class BoxClient(IClient):
    """Builder class for Box clients with different construction methods"""
    def __init__(self, client: BoxRESTClientViaAccessToken) -> None:
        self.client = client

    def get_client(self) -> BoxRESTClientViaAccessToken:
        return self.client

    @classmethod
    def build_with_config(cls, config: BoxAccessTokenConfig) -> "BoxClient":
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> "BoxClient":
        # TODO: Implement
        return cls(client=None)  # type: ignore
