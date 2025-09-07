
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

try:
    from dropbox import Dropbox  # type: ignore
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



class DropboxRESTClientViaToken(HTTPClient):
    """Dropbox client via short/long‑lived OAuth2 access token."""
    def __init__(self, access_token: str, timeout: Optional[float] = None, base_url: str = "https://api.dropboxapi.com") -> None:
        super().__init__(access_token, token_type="Bearer")
        self.access_token = access_token
        self.timeout = timeout
        self.base_url = base_url

    def create_client(self) -> Dropbox: # type: ignore[valid-type]
        # `timeout` is supported by SDK constructor
        return Dropbox(oauth2_access_token=self.access_token, timeout=self.timeout) # type: ignore[valid-type]

    def get_base_url(self) -> str:
        return self.base_url

class DropboxRESTClientViaOAuth2(HTTPClient):
    """
    Dropbox client via refresh token + app key/secret (recommended for servers).

    Args:
        app_key: Dropbox app key
        app_secret: Dropbox app secret
        refresh_token: Long-lived refresh token obtained from OAuth2 PKCE/code flow
        timeout: Optional request timeout (seconds)
        user_agent: Optional custom UA string
    """
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        refresh_token: str,
        timeout: Optional[float] = None,
        user_agent: Optional[str] = None,
        base_url: str = "https://api.dropboxapi.com"
    ) -> None:
        super().__init__(refresh_token, token_type="Bearer")
        self.app_key = app_key
        self.app_secret = app_secret
        self.refresh_token = refresh_token
        self.timeout = timeout
        self.user_agent = user_agent
        self.base_url = base_url

    def create_client(self) -> Dropbox:# type: ignore[valid-type]
        return Dropbox(# type: ignore[valid-type]
            oauth2_refresh_token=self.refresh_token,
            app_key=self.app_key,
            app_secret=self.app_secret,
            timeout=self.timeout,
            user_agent=self.user_agent,
        )

    # Use HTTPClient's execute method for HTTP requests

    def get_base_url(self) -> str:
        return self.base_url

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
        return DropboxRESTClientViaToken(self.access_token, timeout=self.timeout, base_url=self.base_url)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DropboxOAuth2Config:
    """
    Configuration for Dropbox client via refresh token + app key/secret.

    Args:
        app_key: Dropbox app key
        app_secret: Dropbox app secret
        refresh_token: OAuth2 refresh token
        timeout: Optional request timeout in seconds
        user_agent: Optional custom user agent
        base_url: Present for parity; ignored by Dropbox SDK
        ssl: Unused; kept for interface parity
    """
    app_key: str
    app_secret: str
    refresh_token: str
    timeout: Optional[float] = None
    user_agent: Optional[str] = None
    base_url: str = "https://api.dropboxapi.com"   # not used by SDK
    ssl: bool = True

    def create_client(self) -> DropboxRESTClientViaOAuth2:
        return DropboxRESTClientViaOAuth2(
            self.app_key,
            self.app_secret,
            self.refresh_token,
            timeout=self.timeout,
            user_agent=self.user_agent,
            base_url=self.base_url
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
        client: Union[DropboxRESTClientViaToken, DropboxRESTClientViaOAuth2]
    ) -> None:
        self.client = client

    def get_client(self) -> Union[DropboxRESTClientViaToken, DropboxRESTClientViaOAuth2]:
        """Return the underlying auth-holder client object (call `.create_client()` to get SDK)."""
        return self.client

    def get_base_url(self) -> str:
        if hasattr(self.client, "get_base_url"):
            return self.client.get_base_url()
        raise AttributeError("Underlying Dropbox client does not have get_base_url method")

    @classmethod
    def build_with_config(
        cls,
        config: Union[DropboxTokenConfig, DropboxOAuth2Config],
    ) -> "DropboxClient":
        """Build DropboxClient using one of the config dataclasses."""
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> "DropboxClient":
        """
        Build DropboxClient using your configuration service & org/user context.
        """

        logger.info("DropboxClient.build_from_services: placeholder using empty client")
        return cls(client=DropboxRESTClientViaToken(access_token=""))
