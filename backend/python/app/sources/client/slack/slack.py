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
        connector_instance_id: Optional[str] = None,
    ) -> 'SlackClient':
        """
        Build SlackClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID to get specific instance config
        Returns:
            SlackClient instance
        """
        try:
            # Get Slack configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service, connector_instance_id)

            if not config:
                raise ValueError("Failed to get Slack connector configuration")

            # Extract configuration values
            auth_config = config.get("auth",{}) or {}
            auth_type = auth_config.get("authType", "userOAuthAccessToken")  # token, username_password, api_key

            # Create appropriate client based on auth type
            # to be implemented
            if auth_type == "USERNAME_PASSWORD":
                username = auth_config.get("username", "")
                password = auth_config.get("password", "")
                if not username or not password:
                    raise ValueError("Username and password required for username_password auth type")
                client = SlackRESTClientViaUsernamePassword(username, password)

            # to be implemented
            elif auth_type == "API_KEY":
                email = auth_config.get("email", "")
                api_key = auth_config.get("apiKey", "")
                if not email or not api_key:
                    raise ValueError("Email and API key required for api_key auth type")
                client = SlackRESTClientViaApiKey(email, api_key)

            elif auth_type == "API_TOKEN" or auth_type == "userOAuthAccessToken":  # Default to token auth
                token = auth_config.get("userOAuthAccessToken", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = SlackRESTClientViaToken(token)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Slack client from services: {str(e)}")
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: Dict[str, Any],
        logger: logging.Logger,
    ) -> 'SlackClient':
        """
        Build SlackClient using toolset configuration from etcd.

        NEW ARCHITECTURE: This method uses toolset configs directly instead of
        connector instance configs. Toolset configs are stored per-user at:
        /services/toolsets/{user_id}/{toolset_type}

        Args:
            toolset_config: Toolset configuration dictionary from etcd
            logger: Logger instance

        Returns:
            SlackClient instance
        """
        try:
            if not toolset_config:
                raise ValueError("Toolset config is required for Slack client")

            # Extract auth configuration from toolset config
            credentials_config = toolset_config.get("credentials", {}) or {}
            auth_type = toolset_config.get("authType", "").upper()

            # Create appropriate client based on auth type
            if auth_type == "OAUTH":
                # For OAuth/token-based auth, use the OAuth access token
                token = credentials_config.get("access_token", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = SlackRESTClientViaToken(token)

            elif auth_type == "API_TOKEN":
                # For API_TOKEN (bot token), use the api_token from auth config
                # API_TOKEN is typically a bot token like "xoxb-..." stored in auth_config
                token = toolset_config.get("api_token", "") or toolset_config.get("apiToken", "")
                if not token:
                    raise ValueError("API token required for API_TOKEN auth type")
                client = SlackRESTClientViaToken(token)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            logger.info(f"Built Slack client from toolset config with auth type: {auth_type}")
            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Slack client from toolset config: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Slack.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID to get specific instance config

        Returns:
            Configuration dictionary
        """
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get Slack connector configuration for instance {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to get Slack connector config: {e}")
            raise ValueError(f"Failed to get Slack connector configuration for instance {connector_instance_id}")
