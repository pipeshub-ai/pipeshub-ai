import base64
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Union

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class NextcloudRESTClientViaUsernamePassword(HTTPClient):
    """Nextcloud REST client via Username and App Password (Basic Auth).
    Args:
        base_url: The URL of the Nextcloud instance
        username: The username
        password: The App Password (generated in Security settings)
    """

    def __init__(self, base_url: str, username: str, password: str) -> None:
        # HTTP Basic Auth requires "username:password" to be Base64 encoded
        auth_string = f"{username}:{password}"
        encoded_auth = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
        super().__init__(encoded_auth, token_type="Basic")
        self.base_url = base_url

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

class NextcloudRESTClientViaToken(HTTPClient):
    """Nextcloud REST client via Bearer Token (OIDC/OAuth2).
    Args:
        base_url: The URL of the Nextcloud instance
        token: The Bearer token
    """
    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.base_url = base_url
        self.token = token

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_token(self) -> str:
        """Get the token"""
        return self.token

    def set_token(self, token: str) -> None:
        """Set the token"""
        self.token = token
        self.headers["Authorization"] = f"Bearer {token}"

@dataclass
class NextcloudUsernamePasswordConfig:
    """Configuration for Nextcloud REST client via username and password"""
    base_url: str
    username: str
    password: str
    ssl: bool = True

    def create_client(self) -> NextcloudRESTClientViaUsernamePassword:
        return NextcloudRESTClientViaUsernamePassword(self.base_url, self.username, self.password)

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class NextcloudTokenConfig:
    """Configuration for Nextcloud REST client via token"""
    base_url: str
    token: str
    ssl: bool = True

    def create_client(self) -> NextcloudRESTClientViaToken:
        return NextcloudRESTClientViaToken(self.base_url, self.token)

    def to_dict(self) -> dict:
        return asdict(self)

class NextcloudClient(IClient):
    """Builder class for Nextcloud clients with different construction methods"""

    def __init__(
        self,
        client: Union[NextcloudRESTClientViaUsernamePassword, NextcloudRESTClientViaToken],
    ) -> None:
        """Initialize with a Nextcloud client object"""
        self.client = client

    def get_client(self) -> Union[NextcloudRESTClientViaUsernamePassword, NextcloudRESTClientViaToken]:
        """Return the Nextcloud client object"""
        return self.client

    @classmethod
    def build_with_config(
        cls,
        config: Union[NextcloudUsernamePasswordConfig, NextcloudTokenConfig],
    ) -> "NextcloudClient":
        """Build NextcloudClient with configuration object"""
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "NextcloudClient":
        """Build NextcloudClient using configuration service (ETCD/Env)
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            NextcloudClient instance
        """
        try:
            # 1. Fetch Configuration
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                raise ValueError("Failed to get Nextcloud connector configuration")

            auth_config = config.get("auth", {}) or {}
            if not auth_config:
                raise ValueError("Auth configuration not found in Nextcloud connector configuration")

            credentials_config = config.get("credentials", {}) or {}

            # 2. Extract Base URL (Mandatory for Nextcloud)
            # Nextcloud is self-hosted, so URL is not dynamic/discovered like Confluence Cloud
            base_url = credentials_config.get("baseUrl") or config.get("baseUrl")
            if not base_url:
                raise ValueError("Nextcloud 'baseUrl' is required in configuration")

            # 3. Determine Auth Type
            auth_type = auth_config.get("authType", "BASIC_AUTH")
            client = None

            if auth_type == "BASIC_AUTH":
                # Standard App Password / User flow
                username = auth_config.get("username")
                password = auth_config.get("password")

                if not username or not password:
                    raise ValueError("Username and Password required for BASIC_AUTH type")

                client = NextcloudRESTClientViaUsernamePassword(base_url, username, password)

            elif auth_type == "BEARER_TOKEN":
                # SSO / OIDC Flow
                token = auth_config.get("bearerToken")
                if not token:
                    raise ValueError("Token required for BEARER_TOKEN auth type")

                client = NextcloudRESTClientViaToken(base_url, token)

            else:
                raise ValueError(f"Invalid auth type for Nextcloud: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Nextcloud client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Nextcloud."""
        try:
            config = await config_service.get_config("/services/connectors/nextcloud/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Nextcloud connector config: {e}")
            return {}
