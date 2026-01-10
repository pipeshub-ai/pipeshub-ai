import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class WorkdayResponse(BaseModel):
    """Standardized Workday API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()


class WorkdayRESTClient(HTTPClient):
    """Workday REST client for Token or OAuth Authentication.

    Args:
        base_url: The base URL of the Workday instance
        token: The access token to use for authentication (Bearer token or OAuth token)
    """
    def __init__(self, base_url: str, token: str) -> None:
        if not base_url:
            raise ValueError("Workday base_url cannot be empty")
        if not token:
            raise ValueError("Workday token cannot be empty")

        self.base_url = base_url.rstrip('/')
        self.token = token

        super().__init__(token, "Bearer")

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


@dataclass
class WorkdayConfig:
    """Configuration for Workday REST client.

    Supports both API tokens and OAuth access tokens.

    Args:
        base_url: The base URL of the Workday instance
        token: The access token (API token or OAuth access token)
    """
    base_url: str
    token: str

    def create_client(self) -> WorkdayRESTClient:
        return WorkdayRESTClient(self.base_url, self.token)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)




class WorkdayClient(IClient):
    """Builder class for Workday clients"""

    def __init__(
        self,
        client: WorkdayRESTClient
    ) -> None:
        self.client = client

    def get_client(self) -> WorkdayRESTClient:
        return self.client

    def get_base_url(self) -> str:
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: WorkdayConfig,
    ) -> "WorkdayClient":
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "WorkdayClient":
        """Build WorkdayClient using configuration service"""
        try:
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Workday connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Workday connector configuration")

            base_url = config.get("base_url") or config.get("baseUrl")
            if not base_url:
                raise ValueError("Base URL not found in Workday connector configuration")

            auth_type = auth_config.get("authType", "TOKEN")

            # Extract token - support both 'token' and 'accessToken' field names
            token = auth_config.get("token") or auth_config.get("accessToken") or auth_config.get("access_token")

            if not token:
                raise ValueError(f"Token/access token required for {auth_type} auth type")

            client = WorkdayRESTClient(base_url, token)

            logger.info(f"Successfully created Workday client with {auth_type} authentication")
            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Workday client from services: {e}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> Dict[str, Any]:
        """Fetch connector config from configuration service for Workday"""
        try:
            config = await config_service.get_config("/services/connectors/workday/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Workday connector config: {e}")
            raise
