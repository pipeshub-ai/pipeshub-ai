import base64
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

from app.sources.client.http.http_request import HTTPRequest

try:
    from dropbox import Dropbox, DropboxTeam  # type: ignore
except ImportError:
    raise ImportError("dropbox is not installed. Please install it with `pip install dropbox`")

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


@dataclass
class DropboxResponse:
    """Standardized Dropbox API response wrapper."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class DropboxRESTClientViaToken:
    """Dropbox client via short/long‑lived OAuth2 access token."""
    def __init__(
        self,
        access_token: str,
        refresh_token: Optional[str] = None,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        timeout: Optional[float] = None,
        is_team: bool = False
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout
        self.is_team = is_team
        self.dropbox_client = None

    def create_client(self) -> Dropbox: # type: ignore[valid-type]
        # `timeout` is supported by SDK constructor
        if self.is_team:
            self.dropbox_client = DropboxTeam(
                oauth2_access_token=self.access_token,
                timeout=self.timeout,
                oauth2_refresh_token=self.refresh_token,
                app_key=self.app_key,
                app_secret=self.app_secret
            ) # type: ignore[valid-type]
        else:
            self.dropbox_client = Dropbox(
                oauth2_access_token=self.access_token,
                timeout=self.timeout,
                oauth2_refresh_token=self.refresh_token,
                app_key=self.app_key,
                app_secret=self.app_secret
            ) # type: ignore[valid-type]
        return self.dropbox_client

    def get_dropbox_client(self) -> Dropbox: # type: ignore[valid-type]
        if self.dropbox_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.dropbox_client

class DropboxRESTClientWithAppKeySecret:
    """
    Dropbox client via refresh token + app key/secret (recommended for servers).

    Args:
        app_key: Dropbox app key
        app_secret: Dropbox app secret
        timeout: Optional request timeout (seconds)
    """
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        token: str,
        timeout: Optional[float] = None,
        is_team: bool = False,
    ) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout
        self.is_team = is_team
        self.token = token
        self.dropbox_client = None

    def create_client(self) -> Dropbox:# type: ignore[valid-type]
        if self.is_team:
                self.dropbox_client = DropboxTeam(
                oauth2_access_token=self.token,
                app_key=self.app_key,
                app_secret=self.app_secret,
                timeout=self.timeout,
            )
        else:
                self.dropbox_client = Dropbox(oauth2_access_token=self.token,
                                        app_key=self.app_key,
                                        app_secret=self.app_secret,
                                        timeout=self.timeout)
        return self.dropbox_client

    def get_dropbox_client(self) -> Dropbox: # type: ignore[valid-type]
        if self.dropbox_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.dropbox_client

@dataclass
class DropboxTokenConfig:
    """
    Configuration for Dropbox client via access token.

    Args:
        access_token: OAuth2 access token (user or app-scoped)
        timeout: Optional request timeout in seconds
        base_url: Present for API parity with Slack config; ignored by Dropbox SDK
        ssl: Unused; kept for interface parity
    """
    token: str
    refresh_token: Optional[str] = None
    app_key: Optional[str] = None
    app_secret: Optional[str] = None
    timeout: Optional[float] = None
    base_url: str = "https://api.dropboxapi.com"   # not used by SDK, for parity only
    ssl: bool = True

    async def create_client(self, is_team: bool = False) -> DropboxRESTClientViaToken:
        """Create a Dropbox client."""
        return DropboxRESTClientViaToken(
            self.token,
            self.refresh_token,
            app_key=self.app_key,
            app_secret=self.app_secret,
            timeout=self.timeout,
            is_team=is_team
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DropboxAppKeySecretConfig:
    """
    Configuration for Dropbox client via refresh token + app key/secret.

    Args:
        app_key: Dropbox app key
        app_secret: Dropbox app secret
        timeout: Optional request timeout in seconds
        base_url: Present for parity; ignored by Dropbox SDK
        ssl: Unused; kept for interface parity
    """
    app_key: str
    app_secret: str
    timeout: Optional[float] = None
    base_url: str = "https://api.dropboxapi.com"   # not used by SDK
    ssl: bool = True

    async def create_client(self, is_team: bool = False) -> DropboxRESTClientWithAppKeySecret:
        """Create a Dropbox client."""
        token = await self._fetch_token()
        return DropboxRESTClientWithAppKeySecret(
            app_key=self.app_key,
            app_secret=self.app_secret,
            token=token,
            timeout=self.timeout,
            is_team=is_team,
        )

    async def _fetch_token(self) -> str:
        """Fetch a token."""
        credentials = base64.b64encode(f"{self.app_key}:{self.app_secret}".encode()).decode()
        request = HTTPRequest(
            method="POST",
            url="https://api.dropboxapi.com/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}"
            },
            body={"grant_type": "client_credentials"},
        )
        http_client = HTTPClient(token="")
        response = await http_client.execute(request)
        return response.json()["access_token"]

    def to_dict(self) -> dict:
        return asdict(self)

class DropboxClient(IClient):
    """
    Builder class for Dropbox clients with multiple construction methods.

    Mirrors your SlackClient shape so it can be swapped in existing wiring.
    """

    def __init__(
        self,
        client: Union[DropboxRESTClientViaToken, DropboxRESTClientWithAppKeySecret],
        ) -> None:
        self.client = client

    def get_client(self) -> Union[DropboxRESTClientViaToken, DropboxRESTClientWithAppKeySecret]:
        """Return the underlying auth-holder client object (call `.create_client()` to get SDK)."""
        return self.client

    @classmethod
    async def build_with_config(
        cls,
        config: Union[DropboxTokenConfig, DropboxAppKeySecretConfig],
        is_team: bool = False,
    ) -> "DropboxClient":
        """Build DropboxClient using one of the config dataclasses."""
        client = await config.create_client(is_team=is_team)
        return cls(client=client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "DropboxClient":
        """
        Build DropboxClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            DropboxClient instance
        """
        try:
            # Get Dropbox configuration from the configuration service
            config = await cls._get_connector_config(logger, config_service)

            if not config:
                raise ValueError("Failed to get Dropbox connector configuration")

            # Extract configuration values
            auth_type = config.get("authType", "APP_KEY_SECRET")  # APP_KEY_SECRET or OAUTH
            auth_config = config.get("auth", {})

            # Create appropriate client based on auth type
            if auth_type == "APP_KEY_SECRET":
                app_key = auth_config.get("appKey", "")
                app_secret = auth_config.get("appSecret", "")
                timeout = auth_config.get("timeout", None)
                is_team = auth_config.get("isTeam", False)
                if not app_key or not app_secret:
                    raise ValueError("App key and app secret required for app_key_secret auth type")

                app_config = DropboxAppKeySecretConfig(
                    app_key=app_key,
                    app_secret=app_secret,
                    timeout=timeout
                )
                client = await app_config.create_client(is_team=is_team)

            elif auth_type == "OAUTH":
                credentials_config = auth_config.get("credentials", {})
                is_team = credentials_config.get("isTeam", False)
                access_token = credentials_config.get("accessToken", "")
                if not access_token:
                    raise ValueError("Access token required for oauth auth type")
                client = await DropboxTokenConfig(token=access_token).create_client(is_team=is_team)

            else:
                raise ValueError(f"Unsupported auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Dropbox client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for Dropbox."""
        try:
            config = await config_service.get_config("/services/connectors/dropbox/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get Dropbox connector config: {e}")
            return {}
