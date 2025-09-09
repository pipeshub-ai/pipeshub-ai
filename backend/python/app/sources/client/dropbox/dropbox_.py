import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

try:
    from dropbox import Dropbox, DropboxTeam  # type: ignore
except ImportError:
    raise ImportError("dropbox is not installed. Please install it with `pip install dropbox`")

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
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
    def __init__(self, access_token: str, timeout: Optional[float] = None) -> None:
        self.access_token = access_token
        self.timeout = timeout

    def create_client(self) -> Dropbox: # type: ignore[valid-type]
        # `timeout` is supported by SDK constructor
        return Dropbox(oauth2_access_token=self.access_token, timeout=self.timeout) # type: ignore[valid-type]

    def create_team_client(self) -> DropboxTeam: # type: ignore[valid-type]
        """Create team client for business operations."""
        return DropboxTeam(oauth2_access_token=self.access_token, timeout=self.timeout) # type: ignore[valid-type]


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
        timeout: Optional[float] = None,
    ) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout

    def create_client(self) -> Dropbox:# type: ignore[valid-type]
        return Dropbox(# type: ignore[valid-type]
            app_key=self.app_key,
            app_secret=self.app_secret,
            timeout=self.timeout,
        )

    def create_team_client(self) -> DropboxTeam: # type: ignore[valid-type]
        """Create team client for business operations."""
        return DropboxTeam(
            app_key=self.app_key,
            app_secret=self.app_secret,
            timeout=self.timeout,
        )

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
    access_token: str
    timeout: Optional[float] = None
    base_url: str = "https://api.dropboxapi.com"   # not used by SDK, for parity only
    ssl: bool = True

    def create_client(self) -> DropboxRESTClientViaToken:
        return DropboxRESTClientViaToken(self.access_token, timeout=self.timeout)

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

    def create_client(self) -> DropboxRESTClientWithAppKeySecret:
        return DropboxRESTClientWithAppKeySecret(
            self.app_key,
            self.app_secret,
            timeout=self.timeout,
        )

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
    def build_with_config(
        cls,
        config: Union[DropboxTokenConfig, DropboxAppKeySecretConfig],
        is_team: bool = False,
    ) -> "DropboxClient":
        """Build DropboxClient using one of the config dataclasses."""
        if is_team:
            return cls(config.create_team_client())
        else:
            return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger : logging.Logger,
        config_service: ConfigurationService,
        arango_service: IGraphService,
    ) -> "DropboxClient":
        """
        Build DropboxClient using your configuration service & org/user context.
        """
        return cls(client=DropboxRESTClientViaToken(access_token=""))
