"""HubSpot REST client implementation using official SDK"""
import logging
from dataclasses import asdict, dataclass

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient
from hubspot import HubSpot as HubSpotSDK  # type: ignore

logger = logging.getLogger(__name__)


class HubSpotRESTClientViaToken:
    """HubSpot REST client via access token

    Args:
        token: The access token to use for authentication (can be private app token or OAuth token)

    """

    def __init__(self, token: str) -> None:
        # Validate token format
        if not token:
            raise ValueError("HubSpot token cannot be empty")

        self.client = HubSpotSDK(access_token=token)

    def get_client(self) -> HubSpotSDK:
        """Get the underlying HubSpot SDK client"""
        return self.client


@dataclass
class HubSpotTokenConfig:
    """Configuration for HubSpot REST client via token

    Args:
        token: The access token to use for authentication

    """

    token: str

    def create_client(self) -> HubSpotRESTClientViaToken:
        """Create a HubSpot REST client from this configuration"""
        return HubSpotRESTClientViaToken(self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

    def get_client(self) -> HubSpotSDK:
        """Get the HubSpot SDK client"""
        return self.create_client().get_client()


class HubSpotClient(IClient):
    """HubSpot client wrapper implementing IClient interface

    This class provides a unified interface to interact with HubSpot APIs
    using different authentication methods.
    """

    def __init__(
        self,
        client: HubSpotRESTClientViaToken,
    ) -> None:
        """Initialize HubSpot client

        Args:
            client: An instance of HubSpotRESTClientViaToken

        """
        self._rest_client = client

    @staticmethod
    def build_with_config(
        config: HubSpotTokenConfig,
    ) -> "HubSpotClient":
        """Build a HubSpot client from a configuration object

        Args:
            config: Configuration object (HubSpotTokenConfig)

        Returns:
            HubSpotClient instance

        """
        if isinstance(config, HubSpotTokenConfig):
            client = config.create_client()
            return HubSpotClient(client)
        raise ValueError(f"Unsupported config type: {type(config)}")

    @staticmethod
    def build(
        config_service: ConfigurationService,
    ) -> "HubSpotClient":
        """Build a HubSpot client from configuration service

        Args:
            config_service: Configuration service instance

        Returns:
            HubSpotClient instance

        """
        config = config_service.get_config("hubspot")
        if not config:
            raise ValueError("HubSpot configuration not found")

        auth_method = config.get("auth_method", "token")

        if auth_method == "token":
            token = config.get("token")
            if not token:
                raise ValueError("HubSpot token not found in configuration")
            return HubSpotClient.build_with_config(HubSpotTokenConfig(token=token))

        raise ValueError(f"Unsupported authentication method: {auth_method}")

    def get_client(self) -> HubSpotSDK:
        """Get the underlying HubSpot SDK client

        Returns:
            HubSpot SDK client instance

        """
        return self._rest_client.get_client()

    def get_rest_client(self) -> HubSpotRESTClientViaToken:
        """Get the REST client instance

        Returns:
            REST client instance

        """
        return self._rest_client
