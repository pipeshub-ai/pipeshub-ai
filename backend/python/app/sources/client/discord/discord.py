from typing import Optional, Union

from pydantic import BaseModel, Field

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class DiscordResponse(BaseModel):
    """Standardized Discord API response wrapper using Pydantic"""

    success: bool = Field(..., description="Whether the API call was successful")
    data: Optional[Union[dict[str, object], list[object]]] = Field(
        None, description="Response data from Discord API (dict or list)"
    )
    error: Optional[str] = Field(None, description="Error message if the call failed")
    message: Optional[str] = Field(None, description="Additional message information")

    class Config:
        """Pydantic configuration"""

        json_schema_extra = {
            "example": {
                "success": True,
                "data": {"id": "123456789", "name": "Example Guild"},
                "error": None,
                "message": None,
            },
        }

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class DiscordRESTClientViaToken(HTTPClient):
    """Discord REST client via bot token
    Args:
        token: The bot token to use for authentication
        base_url: The base URL of the Discord API
    """

    def __init__(
        self, token: str, base_url: str = "https://discord.com/api/v10",
    ) -> None:
        super().__init__(token, "Bot")
        self.base_url = base_url
        # Add Discord-specific headers
        self.headers.update({"Content-Type": "application/json"})

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class DiscordTokenConfig(BaseModel):
    """Configuration for Discord REST client via bot token"""

    token: str = Field(..., description="The bot token to use for authentication")
    base_url: Optional[str] = Field(
        default="https://discord.com/api/v10",
        description="The base URL of the Discord API",
    )

    def create_client(self) -> DiscordRESTClientViaToken:
        """Create Discord client with token authentication.

        Returns:
            DiscordRESTClientViaToken instance

        """
        return DiscordRESTClientViaToken(
            self.token, self.base_url or "https://discord.com/api/v10",
        )


class DiscordClient(IClient):
    """Builder class for Discord clients with different construction methods"""

    def __init__(
        self,
        client: DiscordRESTClientViaToken,
    ) -> None:
        """Initialize with a Discord client object.

        Args:
            client: Discord REST client instance

        """
        self.client = client

    def get_client(
        self,
    ) -> DiscordRESTClientViaToken:
        """Return the Discord client object.

        Returns:
            Discord REST client instance

        """
        return self.client

    @classmethod
    def build_with_config(
        cls,
        config: DiscordTokenConfig,
    ) -> "DiscordClient":
        """Build DiscordClient with configuration.

        Args:
            config: Discord configuration instance (Pydantic model)

        Returns:
            DiscordClient instance

        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        config_service: ConfigurationService,
        graph_db_service: IGraphService,
    ) -> "DiscordClient":
        """Build DiscordClient using configuration service and graph database service.

        Args:
            config_service: Configuration service instance.
            graph_db_service: Graph database service instance.

        Returns:
            DiscordClient instance.
        """
        # TODO: Implement - fetch config from services
        # This would typically:
        # 1. Query graph_db_service for stored DiscordClient credentials
        # 2. Use config_service to get environment-specific settings
        # 3. Return appropriate client based on available credentials

        raise NotImplementedError("build_from_services is not yet implemented")
