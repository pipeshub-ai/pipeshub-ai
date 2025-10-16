from typing import Any, Dict, Optional

from linkedin_api.clients.restli.client import RestliClient
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class LinkedInOAuth2Config(BaseModel):
    """Configuration for LinkedIn REST client via OAuth 2.0

    This configuration uses the official LinkedIn SDK (linkedin-api-client)
    which implements the Rest.li protocol correctly.

    Args:
        access_token: OAuth 2.0 access token for LinkedIn API
        version_string: Optional API version string in format YYYYMM or YYYYMM.RR
                       (e.g., "202406", "202412"). If provided, uses versioned
                       APIs base URL (https://api.linkedin.com/rest)
    """
    access_token: str
    version_string: Optional[str] = None

    class Config:
        """Pydantic configuration"""
        arbitrary_types_allowed = True

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class LinkedInClient(IClient):
    """Wrapper for official LinkedIn SDK RestliClient

    This client follows the project pattern (similar to Slack/GitHub datasources)
    by wrapping the official LinkedIn SDK rather than implementing custom HTTP logic.

    The underlying RestliClient handles:
    - Rest.li protocol compliance
    - Automatic query tunneling for large requests
    - Proper parameter encoding
    - Request header management
    - Versioned API support

    Example:
        >>> config = LinkedInOAuth2Config(
        ...     access_token="YOUR_TOKEN",
        ...     version_string="202406"
        ... )
        >>> client = LinkedInClient.build_with_config(config)
        >>> restli_client = client.get_client()
        >>> response = restli_client.get(
        ...     resource_path="/me",
        ...     access_token=client.access_token,
        ...     version_string=client.version_string
        ... )
    """

    def __init__(
        self,
        access_token: str,
        version_string: Optional[str] = None
    ) -> None:
        """Initialize LinkedIn client with OAuth2 credentials

        Args:
            access_token: OAuth 2.0 access token
            version_string: Optional API version (e.g., "202406")
        """
        self.access_token = access_token
        self.version_string = version_string
        self._restli_client = RestliClient()

    def get_client(self) -> RestliClient:
        """Return the underlying LinkedIn SDK RestliClient

        Returns:
            RestliClient instance from official LinkedIn SDK
        """
        return self._restli_client

    @classmethod
    def build_with_config(cls, config: LinkedInOAuth2Config) -> 'LinkedInClient':
        """Build LinkedInClient with configuration

        Args:
            config: LinkedInOAuth2Config instance

        Returns:
            LinkedInClient instance
        """
        return cls(
            access_token=config.access_token,
            version_string=config.version_string
        )

    @classmethod
    async def build_from_services(
        cls,
        logger: object,
        config_service: ConfigurationService,
    ) -> 'LinkedInClient':
        """Build LinkedInClient using configuration service

        Args:
            logger: Logger instance
            config_service: Configuration service instance

        Returns:
            LinkedInClient instance
        """
        config = await cls._get_connector_config(logger, config_service)
        if not config:
            raise ValueError("Failed to get LinkedIn connector configuration")
        auth_type = config.get("authType", "API_TOKEN")  # API_TOKEN or OAUTH
        auth_config = config.get("auth", {})
        if auth_type == "API_TOKEN":
            token = auth_config.get("token", "")
            if not token:
                raise ValueError("Token required for token auth type")
            client = LinkedInOAuth2Config(access_token=token).create_client()
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")
        return cls(client)

    @staticmethod
    async def _get_connector_config(logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for LinkedIn."""
        try:
            config = await config_service.get_config("/services/connectors/linkedin/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get LinkedIn connector config: {e}")
            return {}
