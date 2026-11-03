import base64
import http.cookiejar
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Union
from urllib.parse import urlsplit

import httpx

from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.url_guard import GuardedTransport, _env_proxy_for
from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_resilient_transport import ResilientHTTPTransport
from app.sources.client.iclient import IClient
from app.sources.client.resilience import ResiliencePolicy
from app.utils.url_fetcher import (
    PRIVATE_ADDRESS_SWITCH_ENV,
    FetchError,
    check_http_url_without_resolving,
    private_addresses_blocked,
)

# Server-wide opt-in for a Nextcloud reached over plain http on a network the operator trusts.
ALLOW_INSECURE_HTTP_ENV = "PIPESHUB_NEXTCLOUD_ALLOW_INSECURE_HTTP"


class NextcloudAddressNotAllowedError(Exception):
    """A request would have left the configured Nextcloud server, or reached an address it may not."""


def insecure_http_allowed() -> bool:
    return os.getenv(ALLOW_INSECURE_HTTP_ENV, "").strip().lower() in {"1", "true", "yes"}


def check_nextcloud_base_url(base_url: str) -> str:
    """The base URL without a trailing slash; raises ValueError when it may not be used.

    Paths are appended to it as text, so a '?' or '#' would turn them into a query or
    fragment. The app password goes in a Basic auth header on every request, so plain
    http needs the operator's opt-in.
    """
    url = (base_url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError as e:
        raise ValueError(f"The Nextcloud server URL {url!r} is not a valid URL") from e
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The Nextcloud server URL must start with https:// and name a host")
    if "?" in url or "#" in url:
        raise ValueError("The Nextcloud server URL must not contain '?' or '#'")
    if parts.username or parts.password:
        raise ValueError("The Nextcloud server URL must not contain a user name or password")
    if parts.scheme == "http" and not insecure_http_allowed():
        raise ValueError(
            "The Nextcloud server URL must use https: the app password is sent with every request. "
            f"http is allowed only when the server sets {ALLOW_INSECURE_HTTP_ENV}=true."
        )
    allow_private = not private_addresses_blocked()
    try:
        check_http_url_without_resolving(url, block_non_global=not allow_private, allow_private=allow_private)
    except FetchError as e:
        raise ValueError(f"The Nextcloud server URL is not allowed: {nextcloud_address_rule()}") from e
    return url.rstrip("/")


def nextcloud_address_rule() -> str:
    rule = "it must not point to this server (loopback), a link-local or a cloud metadata address"
    if private_addresses_blocked():
        rule += f", or a private network address ({PRIVATE_ADDRESS_SWITCH_ENV} is on)"
    return rule


class NextcloudRESTClientViaUsernamePassword(HTTPClient):
    """Nextcloud REST client via Username and App Password (Basic Auth).

    Every request, redirects included, stays on the base URL's origin and is sent to
    the address that was checked for it (see ``_ensure_client``).

    Args:
        base_url: The URL of the Nextcloud instance
        username: The username
        password: The App Password (generated in Security settings)
        resilience: The connector's rate limit and retry policy, if it declares one
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        resilience: ResiliencePolicy | None = None,
    ) -> None:
        # HTTP Basic Auth requires "username:password" to be Base64 encoded
        auth_string = f"{username}:{password}"
        encoded_auth = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
        super().__init__(encoded_auth, token_type="Basic", resilience=resilience)
        self.base_url = check_nextcloud_base_url(base_url)
        if self.base_url.startswith("http:"):
            logging.getLogger(__name__).warning(
                "Sending the Nextcloud app password over plain http because %s is set", ALLOW_INSECURE_HTTP_ENV
            )

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self.client is None:
            proxy = _env_proxy_for(httpx.URL(self.base_url))
            inner = ResilientHTTPTransport(self.resilience, proxy=proxy) if self.resilience else None
            self.client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=self.follow_redirects,
                headers=self.headers,
                transport=NextcloudGuardedTransport(
                    self.base_url, allow_private=not private_addresses_blocked(), inner=inner
                ),
                # The guard applies the environment proxy itself; httpx's own would route around it.
                trust_env=False,
                # Nextcloud keeps a session's mounts: one whose storage went offline stays 404 for
                # that session (seen live), where a request without the cookie gets its id -1 entry.
                cookies=http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[])),
            )
        return self.client


class NextcloudGuardedTransport(GuardedTransport):
    """A redirect to another origin is refused, and each connection goes to an address that
    passed the check: loopback, link-local and cloud metadata never, private ones only while
    the deployment allows them."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await super().handle_async_request(request)
        except MCPUrlBlockedError as e:
            # The guard's message names MCP servers; this client isn't one.
            raise NextcloudAddressNotAllowedError(
                "Nextcloud request refused: requests must stay on the configured server, and "
                f"{nextcloud_address_rule()}"
            ) from e

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
