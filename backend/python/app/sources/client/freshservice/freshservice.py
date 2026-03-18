import base64
import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)


class FreshserviceConfigurationError(Exception):
    """Custom exception for Freshservice configuration errors."""

    def __init__(
        self, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.details = details or {}


class FreshserviceResponse(BaseModel):
    """Standardized Freshservice API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, Any] | list[Any] | None = Field(
        default=None, description="Response data"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string."""
        return self.model_dump_json()


class FreshserviceRESTClientViaApiKey(HTTPClient):
    """Freshservice REST client via API key.

    Freshservice uses Basic Authentication with API Key as username
    and 'X' as password (same pattern as FreshDesk).

    Args:
        domain: The Freshservice domain (e.g., 'company.freshservice.com')
        api_key: The API key to use for authentication
    """

    def __init__(self, domain: str, api_key: str) -> None:
        # Freshservice uses Basic auth with API key as username, 'X' as password
        credentials = f"{api_key}:X"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        # Initialize HTTPClient with Basic token
        super().__init__(encoded_credentials, "Basic")
        self.domain = domain
        self.base_url = f"https://{domain}/api/v2"
        self.api_key = api_key

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    def get_domain(self) -> str:
        """Get the Freshservice domain."""
        return self.domain


class FreshserviceApiKeyConfig(BaseModel):
    """Configuration for Freshservice REST client via API Key.

    Args:
        domain: The Freshservice domain (e.g., 'company.freshservice.com')
        api_key: The API key for authentication
        ssl: Whether to use SSL (default: True)
    """

    domain: str
    api_key: str
    ssl: bool = True

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, v: str) -> str:
        """Validate domain field."""
        if not v or not v.strip():
            raise ValueError("domain cannot be empty or None")

        if v.startswith(("http://", "https://")):
            raise ValueError(
                "domain should not include protocol (http:// or https://)"
            )

        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate api_key field."""
        if not v or not v.strip():
            raise ValueError("api_key cannot be empty or None")

        return v

    def create_client(self) -> FreshserviceRESTClientViaApiKey:
        """Create Freshservice REST client."""
        return FreshserviceRESTClientViaApiKey(self.domain, self.api_key)

    def to_dict(self) -> dict[str, Any]:
        """Convert the configuration to a dictionary."""
        return {
            "domain": self.domain,
            "ssl": self.ssl,
            "has_api_key": bool(self.api_key),
        }


class FreshserviceClient(IClient):
    """Builder class for Freshservice clients."""

    def __init__(self, client: FreshserviceRESTClientViaApiKey) -> None:
        """Initialize with a Freshservice client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> FreshserviceRESTClientViaApiKey:
        """Return the Freshservice REST client object."""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.client.get_base_url()

    def get_domain(self) -> str:
        """Get the Freshservice domain."""
        return self.client.get_domain()

    @classmethod
    def build_with_config(
        cls,
        config: FreshserviceApiKeyConfig,
    ) -> "FreshserviceClient":
        """Build FreshserviceClient with configuration.

        Args:
            config: FreshserviceApiKeyConfig instance
        Returns:
            FreshserviceClient instance
        """
        return cls(config.create_client())

    @classmethod
    def build_with_api_key_config(
        cls, config: FreshserviceApiKeyConfig
    ) -> "FreshserviceClient":
        """Build FreshserviceClient with API key configuration.

        Args:
            config: FreshserviceApiKeyConfig instance

        Returns:
            FreshserviceClient: Configured client instance
        """
        return cls.build_with_config(config)

    @classmethod
    def build_with_api_key(
        cls,
        domain: str,
        api_key: str,
        *,
        ssl: bool = True,
    ) -> "FreshserviceClient":
        """Build FreshserviceClient with API key directly.

        Args:
            domain: The Freshservice domain (e.g., 'company.freshservice.com')
            api_key: The API key for authentication
            ssl: Whether to use SSL (default: True)

        Returns:
            FreshserviceClient: Configured client instance
        """
        config = FreshserviceApiKeyConfig(
            domain=domain,
            api_key=api_key,
            ssl=ssl,
        )
        return cls.build_with_config(config)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "FreshserviceClient":
        """Build FreshserviceClient using configuration service.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID
        Returns:
            FreshserviceClient: Configured client instance

        Raises:
            ValueError: If configuration is invalid or missing
        """
        config = await cls._get_connector_config(
            logger, config_service, connector_instance_id
        )
        if not config:
            raise ValueError(
                "Failed to get Freshservice connector configuration"
            )
        auth_config = config.get("auth", {})
        auth_type = auth_config.get("authType", "API_KEY")
        if auth_type == "API_KEY":
            api_key = auth_config.get("apiKey", "")
            domain = auth_config.get("domain", "")
            if not api_key:
                raise ValueError("API key required for API key auth type")
            client = FreshserviceApiKeyConfig(
                domain=domain, api_key=api_key
            ).create_client()
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")
        return cls(client)

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Freshservice."""
        try:
            config = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get Freshservice connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return dict(config)  # type: ignore[arg-type]
        except Exception as e:
            logger.error(f"Failed to get Freshservice connector config: {e}")
            raise ValueError(
                f"Failed to get Freshservice connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
