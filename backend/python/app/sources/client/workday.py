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


class WorkdayRESTClientViaToken(HTTPClient):
    """Workday REST client via Token Authentication (Bearer token)
    Args:
        base_url: The base URL of the Workday instance
        token: The access token to use for authentication
    """
    def __init__(self, base_url: str, token: str) -> None:
        if not base_url:
            raise ValueError("Workday base_url cannot be empty")
        if not token:
            raise ValueError("Workday token cannot be empty")

        self.base_url = base_url.rstrip('/')
        self.token = token

        super().__init__(token, "Bearer")

        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class WorkdayRESTClientViaOAuth(HTTPClient):
    """Workday REST client via OAuth 2.0
    Args:
        base_url: The base URL of the Workday instance
        access_token: The OAuth access token
    """
    def __init__(self, base_url: str, access_token: str) -> None:
        if not base_url:
            raise ValueError("Workday base_url cannot be empty")
        if not access_token:
            raise ValueError("Workday access_token cannot be empty")

        self.base_url = base_url.rstrip('/')
        self.access_token = access_token

        super().__init__(access_token, "Bearer")

        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


@dataclass
class WorkdayTokenConfig:
    """Configuration for Workday REST client via Token
    Args:
        base_url: The base URL of the Workday instance
        token: The access token
    """
    base_url: str
    token: str

    def create_client(self) -> WorkdayRESTClientViaToken:
        return WorkdayRESTClientViaToken(self.base_url, self.token)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WorkdayOAuthConfig:
    """Configuration for Workday REST client via OAuth
    Args:
        base_url: The base URL of the Workday instance
        access_token: The OAuth access token
    """
    base_url: str
    access_token: str

    def create_client(self) -> WorkdayRESTClientViaOAuth:
        return WorkdayRESTClientViaOAuth(self.base_url, self.access_token)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WorkdayClient(IClient):
    """Builder class for Workday clients"""

    def __init__(
        self,
        client: WorkdayRESTClientViaToken | WorkdayRESTClientViaOAuth
    ) -> None:
        self.client = client

    def get_client(self) -> WorkdayRESTClientViaToken | WorkdayRESTClientViaOAuth:
        return self.client

    def get_base_url(self) -> str:
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: WorkdayTokenConfig | WorkdayOAuthConfig,
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

            if auth_type == "TOKEN" or auth_type == "API_TOKEN":
                token = auth_config.get("token", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = WorkdayRESTClientViaToken(base_url, token)

            elif auth_type == "OAUTH" or auth_type == "OAUTH2":
                access_token = auth_config.get("accessToken") or auth_config.get("access_token", "")
                if not access_token:
                    raise ValueError("Access token required for OAuth auth type")
                client = WorkdayRESTClientViaOAuth(base_url, access_token)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

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
            return {}
