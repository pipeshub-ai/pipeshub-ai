import json
from dataclasses import asdict, dataclass
from typing import Any

import discord  # type: ignore

from app.sources.client.iclient import IClient


@dataclass
class DiscordResponse:
    """Standardized Discord API response wrapper"""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


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


@dataclass
class DiscordUsernamePasswordConfig:
    """Configuration for Discord REST client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL
    """

    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> DiscordRESTClientViaUsernamePassword:
        return DiscordRESTClientViaUsernamePassword(
            self.username, self.password, "Basic"
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

    def get_client(self) -> discord.Client:
        return self.create_client().get_client()


@dataclass
class DiscordTokenConfig:
    """Configuration for Discord REST client via token
    Args:
        token: The bot token to use for authentication
        ssl: Whether to use SSL
    """

    token: str
    ssl: bool = False

    def create_client(self) -> DiscordRESTClientViaToken:
        return DiscordRESTClientViaToken(self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class DiscordApiKeyConfig:
    """Configuration for Discord REST client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
        ssl: Whether to use SSL
    """

    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> DiscordRESTClientViaApiKey:
        return DiscordRESTClientViaApiKey(self.email, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class DiscordClient(IClient):
    """Builder class for Discord clients with different construction methods"""

    def __init__(
        self,
        client: DiscordRESTClientViaUsernamePassword
        | DiscordRESTClientViaApiKey
        | DiscordRESTClientViaToken,
    ) -> None:
        """Initialize with a Discord client object"""
        self.client = client

    def get_client(
        self,
    ) -> (
        DiscordRESTClientViaUsernamePassword
        | DiscordRESTClientViaApiKey
        | DiscordRESTClientViaToken
    ):
        """Return the Discord client object"""
        return self.client

    def get_discord_client(self) -> discord.Client:
        """Return the Discord SDK client object"""
        return self.client.get_client()

    @classmethod
    def build_with_config(
        cls,
        config: DiscordUsernamePasswordConfig
        | DiscordTokenConfig
        | DiscordApiKeyConfig,
    ) -> "DiscordClient":
        """Build DiscordClient with configuration
        Args:
            config: DiscordConfigBase instance
        Returns:
            DiscordClient instance
        """
        return cls(config.create_client())


