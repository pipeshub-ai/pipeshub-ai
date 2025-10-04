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


class DiscordRESTClientViaUsernamePassword:
    """Discord REST client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """

    def __init__(self, username: str, password: str, token_type: str = "Basic") -> None:
        # TODO: Implement
        self.client = None
        raise NotImplementedError

    def get_client(self) -> discord.Client:
        raise NotImplementedError(
            "Username/Password authentication is not yet implemented."
        )


class DiscordRESTClientViaApiKey:
    """Discord REST client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
    """

    def __init__(self, email: str, api_key: str) -> None:
        # TODO: Implement
        self.client = None
        raise NotImplementedError

    def get_client(self) -> discord.Client:
        raise NotImplementedError("API Key authentication is not yet implemented.")


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


class DiscordUsernamePasswordConfig(BaseModel):
    """Configuration for Discord REST client via username and password"""

    username: str = Field(..., description="The username to use for authentication")
    password: str = Field(..., description="The password to use for authentication")
    ssl: bool = Field(False, description="Whether to use SSL")

    def create_client(self) -> DiscordRESTClientViaUsernamePassword:
        """Create Discord client with username/password authentication.

        Returns:
            DiscordRESTClientViaUsernamePassword instance
        """
        return DiscordRESTClientViaUsernamePassword(
            self.username, self.password, "Basic"
        )

    def get_client(self) -> discord.Client:
        """Get the underlying discord.Client instance.

        Returns:
            discord.Client instance
        """
        return self.create_client().get_client()


class DiscordTokenConfig(BaseModel):
    """Configuration for Discord REST client via bot token"""

    token: str = Field(..., description="The bot token to use for authentication")
    ssl: bool = Field(False, description="Whether to use SSL")

    def create_client(self) -> DiscordRESTClientViaToken:
        """Create Discord client with token authentication.

        Returns:
            DiscordRESTClientViaToken instance
        """
        return DiscordRESTClientViaToken(self.token)


class DiscordApiKeyConfig(BaseModel):
    """Configuration for Discord REST client via API key"""

    email: str = Field(..., description="The email to use for authentication")
    api_key: str = Field(..., description="The API key to use for authentication")
    ssl: bool = Field(False, description="Whether to use SSL")

    def create_client(self) -> DiscordRESTClientViaApiKey:
        """Create Discord client with API key authentication.

        Returns:
            DiscordRESTClientViaApiKey instance
        """
        return DiscordRESTClientViaApiKey(self.email, self.api_key)


class DiscordClient(IClient):
    """Builder class for Discord clients with different construction methods"""

    def __init__(
        self,
        client: DiscordRESTClientViaUsernamePassword
        | DiscordRESTClientViaApiKey
        | DiscordRESTClientViaToken,
    ) -> None:
        """Initialize with a Discord client object.

        Args:
            client: Discord REST client instance
        """
        self.client = client

    def get_client(
        self,
    ) -> (
        DiscordRESTClientViaUsernamePassword
        | DiscordRESTClientViaApiKey
        | DiscordRESTClientViaToken
    ):
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
        config: DiscordUsernamePasswordConfig
        | DiscordTokenConfig
        | DiscordApiKeyConfig,
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
    "DiscordUsernamePasswordConfig",
    "DiscordApiKeyConfig",
    "DiscordRESTClientViaToken",
    "DiscordRESTClientViaUsernamePassword",
    "DiscordRESTClientViaApiKey",
]
