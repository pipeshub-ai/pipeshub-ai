from typing import Any, Dict, Optional

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class LatticeResponse(BaseModel):
    """Standardized Lattice API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None
    status_code: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class LatticeRESTClientViaToken(HTTPClient):
    """Lattice REST client via API Key (Bearer token)

    The Lattice API uses API keys to authenticate requests.
    Authentication is performed via Bearer Authentication.
    All API requests must be made over HTTPS.

    Args:
        token: The Lattice API key
        base_url: The Lattice API base URL (defaults to US)
    """

    # US data residency
    BASE_URL_US = "https://api.latticehq.com"
    # EMEA data residency
    BASE_URL_EMEA = "https://api.emea.latticehq.com"

    def __init__(
        self,
        token: str,
        base_url: Optional[str] = None,
    ) -> None:
        super().__init__(token, "Bearer")
        self.base_url = (base_url or self.BASE_URL_US).rstrip("/")
        self.headers.update({
            "Content-Type": "application/json",
        })

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_token(self) -> str:
        """Get the current token"""
        auth_header = self.headers.get("Authorization", "")
        return auth_header.replace("Bearer ", "")

    def set_token(self, token: str) -> None:
        """Update the API token"""
        self.headers["Authorization"] = f"Bearer {token}"


class LatticeTokenConfig(BaseModel):
    """Configuration for Lattice REST client via API Key

    Args:
        token: The Lattice API key
        base_url: The Lattice API base URL (defaults to US)
    """
    token: str
    base_url: Optional[str] = None

    def create_client(self) -> LatticeRESTClientViaToken:
        return LatticeRESTClientViaToken(
            token=self.token,
            base_url=self.base_url,
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class LatticeClient(IClient):
    """Builder class for Lattice clients with different construction methods"""

    def __init__(self, client: LatticeRESTClientViaToken) -> None:
        """Initialize with a Lattice client object"""
        self.client = client

    def get_client(self) -> LatticeRESTClientViaToken:
        """Return the Lattice client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: LatticeTokenConfig,
    ) -> "LatticeClient":
        """Build LatticeClient with configuration
        Args:
            config: LatticeTokenConfig instance
        Returns:
            LatticeClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "LatticeClient":
        """Build LatticeClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID
        Returns:
            LatticeClient instance
        """
        try:
            config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not config:
                raise ValueError(
                    "Failed to get Lattice connector configuration"
                )
            auth_config = config.get("auth", {}) or {}
            auth_type = auth_config.get("authType", "API_TOKEN")

            if auth_type in ("API_TOKEN", "BEARER_TOKEN"):
                token = auth_config.get("apiToken") or auth_config.get(
                    "bearerToken", ""
                )
                base_url = auth_config.get("baseUrl")
                client = LatticeRESTClientViaToken(
                    token=token,
                    base_url=base_url,
                )
            elif auth_type == "OAUTH":
                credentials = auth_config.get("credentials", {})
                access_token = credentials.get("access_token", "")
                base_url = auth_config.get("baseUrl")
                client = LatticeRESTClientViaToken(
                    token=access_token,
                    base_url=base_url,
                )
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(
                f"Failed to build Lattice client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Lattice."""
        config_key = (
            f"/services/connectors/{connector_instance_id}/config"
        )
        try:
            config = await config_service.get_config(config_key)
        except Exception as e:
            logger.error(
                f"Failed to fetch Lattice connector config "
                f"from {config_key}: {e}"
            )
            raise ValueError(
                f"Failed to get Lattice connector configuration "
                f"for instance {connector_instance_id}"
            ) from e

        if not config:
            raise ValueError(
                f"Lattice connector configuration not found or is empty "
                f"for instance {connector_instance_id}"
            )
        return config
