from dataclasses import asdict, dataclass
from typing import Optional

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class BoxRESTClientViaAccessToken(HTTPClient):
    """Box REST client via Access Token.

    Args:
        base_url: Base URL for Box API
        access_token: The OAuth2 access token
        token_type: Defaults to 'Bearer'
    """

    def __init__(self, base_url: str, access_token: str, token_type: str = "Bearer") -> None:
        super().__init__(access_token, token_type)
        self.base_url = base_url or "https://api.box.com/2.0"
        self.access_token = access_token

    def get_base_url(self) -> str:
        """Return the base URL for API calls."""
        return self.base_url


@dataclass
class BoxAccessTokenConfig:
    """Configuration for Box REST client via access token.

    Args:
        base_url: Base URL of the Box API
        access_token: OAuth2 access token
    """

    base_url: str = "https://api.box.com/2.0"
    access_token: str = ""

    def create_client(self) -> BoxRESTClientViaAccessToken:
        return BoxRESTClientViaAccessToken(self.base_url, self.access_token)

    def to_dict(self) -> dict:
        return asdict(self)


class BoxClient(IClient):
    """Builder class for Box clients with different construction methods."""

    def __init__(self, client: Optional[BoxRESTClientViaAccessToken]) -> None:
        self.client = client

    def get_client(self) -> BoxRESTClientViaAccessToken:
        if not self.client:
            raise RuntimeError("BoxClient has not been initialized with a client.")
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
        """
        Build BoxClient using ConfigurationService and org/user context.
        Expects config_service to return the Box access token for the given org/user.
        """
        logger.info("Building BoxClient from services")

        # Example: fetch token from config_service
        access_token = await config_service.get_secret("box_access_token", org_id, user_id)
        if not access_token:
            raise RuntimeError("Box access token not found in configuration service")

        config = BoxAccessTokenConfig(access_token=access_token)
        return cls(config.create_client())
