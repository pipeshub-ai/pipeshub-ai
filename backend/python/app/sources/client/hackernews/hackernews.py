import logging
from typing import Any

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

DEFAULT_BASE_URL = "https://hacker-news.firebaseio.com/v0"


class HackerNewsResponse(BaseModel):
    """Standardized HackerNews API response wrapper"""

    success: bool
    data: Any | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class HackerNewsRESTClient(HTTPClient):
    """HackerNews REST client.

    The official HackerNews API (Firebase-backed) is public and read-only —
    it requires no authentication of any kind.
        base_url: The base URL of the HackerNews API (default: official endpoint)
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        # No credentials to send; HTTPClient always builds an Authorization
        # header, so construct with an empty token and drop it immediately.
        super().__init__("", "")
        self.headers.pop("Authorization", None)
        self.base_url = base_url.rstrip("/")

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class HackerNewsConfig(BaseModel):
    """Configuration for the HackerNews REST client
    Args:
        base_url: The base URL of the HackerNews API (default: official endpoint)
    """

    base_url: str = DEFAULT_BASE_URL

    def create_client(self) -> HackerNewsRESTClient:
        """Create a HackerNews client"""
        return HackerNewsRESTClient(self.base_url)

    def to_dict(self) -> dict[str, Any]:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class HackerNewsClient(IClient):
    """Builder class for HackerNews clients with different construction methods"""

    def __init__(self, client: HackerNewsRESTClient) -> None:
        """Initialize with a HackerNews client object"""
        self.client = client

    def get_client(self) -> HackerNewsRESTClient:
        """Return the HackerNews client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(cls, config: HackerNewsConfig | None = None) -> "HackerNewsClient":
        """Build HackerNewsClient with configuration
        Args:
            config: HackerNewsConfig instance (defaults to the official API)

        Returns:
            HackerNewsClient instance

        """
        return cls((config or HackerNewsConfig()).create_client())

    @classmethod
    async def build_and_validate(cls, config: HackerNewsConfig | None = None) -> "HackerNewsClient":
        """Builds the HackerNewsClient and validates connectivity with a lightweight call.

        Raises:
            ValueError: If the API cannot be reached or returns an unexpected shape.

        """
        client_instance = cls.build_with_config(config)
        http_client = client_instance.get_client()
        base_url = http_client.get_base_url()

        validation_url = base_url + "/maxitem.json"
        headers = dict(http_client.headers)

        request = HTTPRequest(
            method="GET",
            url=validation_url,
            headers=headers,
            query_params={},
            body=None,
        )

        try:
            response = await http_client.execute(request)
            data = response.json()

            if not isinstance(data, int):
                raise ValueError(
                    f"HackerNews validation failed: expected an integer item id, got {data!r}",
                )

            return client_instance

        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to connect to HackerNews for validation: {e!s}") from e

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "HackerNewsClient":
        """Build HackerNewsClient using configuration service.

        HackerNews needs no credentials, so a missing or partial connector
        configuration is not an error — it just means the official base URL
        is used.

        Args:
            config_service: Configuration service instance
        Returns:
            HackerNewsClient instance

        """
        config = await cls._get_connector_config(logger, config_service, connector_instance_id)
        base_url = config.get("baseURL") or DEFAULT_BASE_URL if config else DEFAULT_BASE_URL
        return cls.build_with_config(HackerNewsConfig(base_url=base_url))

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get connector configuration from config service, if any exists."""
        try:
            config_path = f"/services/connectors/{connector_instance_id}/config"
            return await config_service.get_config(config_path)
        except Exception as e:
            logger.warning(f"No HackerNews connector configuration for instance {connector_instance_id}: {e!s}")
            return None
