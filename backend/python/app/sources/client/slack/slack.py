import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from slack_sdk import WebClient  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


@dataclass
class SlackResponse:
    """Standardized Slack API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


class SlackRESTClientViaUsernamePassword:
    """Slack REST client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """
    def __init__(self, username: str, password: str, token_type: str = "Basic") -> None:
        # TODO: Implement
        self.client = None
        raise NotImplementedError

    def get_web_client(self) -> WebClient:
        raise NotImplementedError("Username/Password authentication is not yet implemented.")

class SlackRESTClientViaApiKey:
    """Slack REST client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
    """
    def __init__(self, email: str, api_key: str) -> None:
        # TODO: Implement
        self.client = None
        raise NotImplementedError

    def get_web_client(self) -> WebClient:
        raise NotImplementedError("Username/Password authentication is not yet implemented.")

class SlackRESTClientViaToken:
    """Slack REST client via token
    Args:
        token: The token to use for authentication
    """
    def __init__(self, token: str) -> None:
        # Validate token format
        if not token:
            raise ValueError("Slack token cannot be empty")

        # Check if it's a bot token (starts with xoxb-) or user token (starts with xoxp-)
        if not (token.startswith('xoxb-') or token.startswith('xoxp-')):
            raise ValueError(f"Invalid Slack token format. Token should start with 'xoxb-' (bot token) or 'xoxp-' (user token), got: {token[:10]}...")

        self.client = WebClient(token=token)

    def get_web_client(self) -> WebClient:
        return self.client

@dataclass
class SlackUsernamePasswordConfig:
    """Configuration for Slack REST client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL
    """
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> SlackRESTClientViaUsernamePassword:
        return SlackRESTClientViaUsernamePassword(self.username, self.password, "Basic")

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

    def get_web_client(self) -> WebClient:
        return self.create_client().get_web_client()

@dataclass
class SlackTokenConfig:
    """Configuration for Slack REST client via token
    Args:
        token: The token to use for authentication
        ssl: Whether to use SSL
    """
    token: str
    ssl: bool = False

    def create_client(self) -> SlackRESTClientViaToken:
        return SlackRESTClientViaToken(self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class SlackApiKeyConfig:
    """Configuration for Slack REST client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
        ssl: Whether to use SSL
    """
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> SlackRESTClientViaApiKey:
        return SlackRESTClientViaApiKey(self.email, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

class SlackClient(IClient):
    """Builder class for Slack clients with different construction methods"""

    def __init__(self, client: SlackRESTClientViaUsernamePassword | SlackRESTClientViaApiKey | SlackRESTClientViaToken) -> None:
        """Initialize with a Slack client object"""
        self.client = client

    def get_client(self) -> SlackRESTClientViaUsernamePassword | SlackRESTClientViaApiKey | SlackRESTClientViaToken:
        """Return the Slack client object"""
        return self.client

    def get_web_client(self) -> WebClient:
        """Return the Slack client object"""
        return self.client.get_web_client()

    @classmethod
    def build_with_config(cls, config: SlackUsernamePasswordConfig | SlackTokenConfig | SlackApiKeyConfig) -> 'SlackClient':
        """
        Build SlackClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: SlackConfigBase instance
        Returns:
            SlackClient instance with placeholder implementation
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> 'SlackClient':
        """
        Build SlackClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            SlackClient instance
        """
        try:
            # Get Slack configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Slack connector configuration")

            # Extract configuration values
            auth_type = config.get("authType", "botToken")  # token, username_password, api_key

            # Create appropriate client based on auth type
            # to be implemented
            if auth_type == "USERNAME_PASSWORD":
                username = config.get("username", "")
                password = config.get("password", "")
                if not username or not password:
                    raise ValueError("Username and password required for username_password auth type")
                client = SlackRESTClientViaUsernamePassword(username, password)

            # to be implemented
            elif auth_type == "API_KEY":
                email = config.get("email", "")
                api_key = config.get("apiKey", "")
                if not email or not api_key:
                    raise ValueError("Email and API key required for api_key auth type")
                client = SlackRESTClientViaApiKey(email, api_key)

            elif auth_type == "API_TOKEN" or auth_type == "botToken":  # Default to token auth
                token = config.get("botToken", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = SlackRESTClientViaToken(token)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Slack client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Slack."""
        try:
            config = await config_service.get_config("/services/connectors/slack/config")
            return config.get("auth",{}) or {}
        except Exception as e:
            logger.error(f"Failed to get Slack connector config: {e}")
            return {}
