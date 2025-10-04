from typing import Any, Optional

import discord  # type: ignore
from pydantic import BaseModel, Field

from app.sources.client.iclient import IClient


class DiscordResponse(BaseModel):
    """Standardized Discord API response wrapper using Pydantic"""

    success: bool = Field(..., description="Whether the API call was successful")
    data: Optional[dict[str, Any]] = Field(
        None, description="Response data from Discord API"
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
            }
        }


class DiscordRESTClientViaToken:
    """Discord REST client via bot token
    Args:
        token: The bot token to use for authentication
    """

    def __init__(self, token: str) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        self.client = discord.Client(intents=intents)
        self.token = token

    def get_client(self) -> discord.Client:
        return self.client


class DiscordTokenConfig(BaseModel):
    """Configuration for Discord REST client via bot token"""

    token: str = Field(..., description="The bot token to use for authentication")

    def create_client(self) -> DiscordRESTClientViaToken:
        """Create Discord client with token authentication.

        Returns:
            DiscordRESTClientViaToken instance
        """
        return DiscordRESTClientViaToken(self.token)


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

    def get_discord_client(self) -> discord.Client:
        """Return the Discord SDK client object.

        Returns:
            discord.Client instance
        """
        return self.client.get_client()

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


__all__ = [
    "DiscordResponse",
    "DiscordClient",
    "DiscordTokenConfig",
    "DiscordRESTClientViaToken",
]
