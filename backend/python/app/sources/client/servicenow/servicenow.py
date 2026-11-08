import base64
import os
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.exception.exception import BadRequestError
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient
from app.sources.client.resilience import ResiliencePolicy
from app.utils.url_fetcher import (
    PRIVATE_ADDRESS_SWITCH_ENV,
    FetchError,
    check_http_url_without_resolving,
    private_addresses_blocked,
)

# Server-wide opt-in for an instance reached over plain http (a local test server).
ALLOW_INSECURE_HTTP_ENV = "SERVICENOW_ALLOW_INSECURE_HTTP"
_DEFAULT_PORTS = {"http": 80, "https": 443}


def check_servicenow_instance_url(url: str, *, same_host: Mapping[str, str | None] | None = None) -> str:
    """The instance URL without a trailing slash; raises ValueError, with a message for the user,
    when it may not be used.

    The access token goes in a header on every request, so the URL must be https and name the
    instance only: paths are appended to it as text. Custom domains are fine. ``same_host`` maps
    a label to another URL (the OAuth token and authorize URLs) that must be on the same scheme,
    host and port, so a token is never minted on, or sent to, another host.
    """
    url = (url or "").strip().rstrip("/")
    try:
        parts = urlsplit(url)
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme)
    except ValueError as e:
        raise ValueError(f"The ServiceNow instance URL {url!r} is not a valid URL") from e
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("The ServiceNow instance URL must start with https:// and name a host")
    if parts.username or parts.password:
        raise ValueError("The ServiceNow instance URL must not contain a user name or password")
    if parts.path or "?" in url or "#" in url:
        raise ValueError(
            "The ServiceNow instance URL must be the instance address only, such as "
            "https://acme.service-now.com, with no path, query or fragment"
        )
    if parts.scheme == "http" and os.getenv(ALLOW_INSECURE_HTTP_ENV, "").strip().lower() not in {"1", "true", "yes"}:
        raise ValueError(
            "The ServiceNow instance URL must use https: the access token is sent with every request"
        )
    allow_private = not private_addresses_blocked()
    try:
        check_http_url_without_resolving(url, block_non_global=not allow_private, allow_private=allow_private)
    except FetchError as e:
        rule = "it must not point to this server (loopback), a link-local or a cloud metadata address"
        if not allow_private:
            rule += f", or a private network address ({PRIVATE_ADDRESS_SWITCH_ENV} is on)"
        raise ValueError(f"The ServiceNow instance URL is not allowed: {rule}") from e
    for label, other in (same_host or {}).items():
        if not other:
            continue
        try:
            theirs = urlsplit(other.strip())
            their_port = theirs.port or _DEFAULT_PORTS.get(theirs.scheme)
            same = (theirs.scheme, theirs.hostname, their_port) == (parts.scheme, parts.hostname, port)
        except ValueError:
            same = False
        if not same:
            raise ValueError(f"The ServiceNow {label} must be on the instance's address, {parts.scheme}://{parts.netloc}")
    return url


# The clients send credentials (a password, an API key header, a client secret or refresh token in a
# token POST) that must never reach another host, so none of them follows a redirect.
_FIRST_REDIRECT_STATUS = 300


def rate_limit_reset_label(reset_at: float) -> str:
    """``HH:MM`` UTC of a reset, to the nearest minute: limits reset on the minute, and a reset
    worked out from Retry-After lands a second or so off it (truncating showed 20:59 for 21:00)."""
    return datetime.fromtimestamp(round(reset_at / 60) * 60, tz=timezone.utc).strftime("%H:%M")


class ServiceNowRateLimited(Exception):
    """ServiceNow asked for a longer wait than a sync should hold: a rate limit rule is spent until ``reset_at``."""

    def __init__(self, reset_at: float, rule: str | None = None) -> None:
        self.reset_at = reset_at
        self.rule = rule
        super().__init__(
            f"ServiceNow rate limit reached until {rate_limit_reset_label(reset_at)} UTC" + (f" (rule {rule})" if rule else "")
        )


class ServiceNowRateLimitPolicy(ResiliencePolicy):
    """Retries short waits, and stops once a 429 asks for more than ``max_delay``.

    ServiceNow rate limit rules count per hour: a 429 carries Retry-After (seconds to the next
    full hour), X-RateLimit-Reset (epoch seconds) and X-RateLimit-Rule, and no Remaining header,
    so there is nothing to throttle on ahead of it. Waiting out the hour would hold a sync, so
    every request after it raises ``ServiceNowRateLimited`` unsent until the reset.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.reset_at: float | None = None
        self.rule: str | None = None
        self._last_status: int | None = None
        self._last_headers: Mapping[str, str] = {}

    def note_response_status(self, status: int, headers: Mapping[str, str] | None = None) -> None:
        """The response the next ``note_retry`` is about (set by the transport)."""
        self._last_status, self._last_headers = status, headers or {}

    def note_retry(self, attempt: int, retry_after: float | None = None) -> float:
        # Cleared on read: a network error that follows a 429 is not another 429.
        status, self._last_status = self._last_status, None
        if status == HttpStatusCode.TOO_MANY_REQUESTS.value:
            if retry_after is None:
                try:
                    retry_after = float(self._last_headers.get("X-RateLimit-Reset") or "") - time.time()
                except ValueError:
                    retry_after = None
            if retry_after is not None and retry_after > self.max_delay:
                self.reset_at = time.time() + retry_after
                self.rule = self._last_headers.get("X-RateLimit-Rule")
                raise ServiceNowRateLimited(self.reset_at, self.rule)
        return super().note_retry(attempt, retry_after)

    async def acquire(self) -> None:
        if self.reset_at is not None and time.time() < self.reset_at:
            raise ServiceNowRateLimited(self.reset_at, self.rule)
        await super().acquire()


class ServiceNowResponse(BaseModel):
    """Standardized ServiceNow API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class ServiceNowRESTClientViaUsernamePassword(HTTPClient):
    """ServiceNow REST client via username and password (Basic Auth)
    Args:
        instance_url: The ServiceNow instance URL (e.g., https://acme.service-now.com)
        username: The username to use for authentication
        password: The password to use for authentication
        resilience: Rate limit and retry policy applied below every request
    """
    def __init__(
        self, instance_url: str, username: str, password: str, resilience: ResiliencePolicy | None = None,
    ) -> None:
        # Encode credentials for Basic auth
        credentials = f"{username}:{password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        # Initialize with empty token and override headers
        super().__init__("", "", follow_redirects=False, resilience=resilience)
        self.instance_url = instance_url.rstrip('/')
        self.base_url = f"{self.instance_url}/api/now"
        self.username = username

        self.headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the instance URL"""
        return self.instance_url


class ServiceNowRESTClientViaToken(HTTPClient):
    """ServiceNow REST client via Bearer Token (for pre-existing access tokens)
    Args:
        instance_url: The ServiceNow instance URL
        token: The bearer token to use for authentication
    """
    def __init__(self, instance_url: str, token: str) -> None:
        super().__init__(token, "Bearer", follow_redirects=False)
        self.instance_url = instance_url.rstrip('/')
        self.base_url = f"{self.instance_url}/api/now"

        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the instance URL"""
        return self.instance_url


class ServiceNowRESTClientViaAPIKey(HTTPClient):
    """ServiceNow REST client via API Key
    Args:
        instance_url: The ServiceNow instance URL
        api_key: The API key to use for authentication
        header_name: The header name for the API key (default: x-sn-apikey)
        resilience: Rate limit and retry policy applied below every request
    """
    def __init__(
        self, instance_url: str, api_key: str, header_name: str = "x-sn-apikey",
        resilience: ResiliencePolicy | None = None,
    ) -> None:
        super().__init__("", "", follow_redirects=False, resilience=resilience)
        self.instance_url = instance_url.rstrip('/')
        self.base_url = f"{self.instance_url}/api/now"
        self.api_key = api_key
        self.header_name = header_name

        self.headers = {
            header_name: api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the instance URL"""
        return self.instance_url


class ServiceNowRESTClientViaOAuthClientCredentials(HTTPClient):
    """ServiceNow REST client via OAuth 2.0 Client Credentials
    Args:
        instance_url: The ServiceNow instance URL
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        access_token: Optional existing access token
        resilience: Rate limit and retry policy applied below every request
    """
    def __init__(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        access_token: Optional[str] = None,
        resilience: ResiliencePolicy | None = None,
    ) -> None:
        super().__init__(access_token or "", "Bearer", follow_redirects=False, resilience=resilience)

        self.instance_url = instance_url.rstrip('/')
        self.base_url = f"{self.instance_url}/api/now"
        self.oauth_token_url = f"{self.instance_url}/oauth_token.do"
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        # time.time() when the current token expires; None until fetch_token reads expires_in
        self.expires_at: float | None = None
        if not access_token:
            # "Bearer " with no token is not a legal header value; fetch_token sets it
            self.headers.pop("Authorization", None)

        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

        self._oauth_completed = access_token is not None

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the instance URL"""
        return self.instance_url

    def is_oauth_completed(self) -> bool:
        """Check if OAuth flow has been completed"""
        return self._oauth_completed

    async def fetch_token(self) -> Optional[str]:
        """Fetch access token using client credentials grant
        Returns:
            Access token from OAuth exchange
        """
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        request = HTTPRequest(
            method="POST",
            url=self.oauth_token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=data
        )


        response = await self.execute(request)

        if response.status >= _FIRST_REDIRECT_STATUS:  # a redirect is not followed: the secrets stay here
            raise BadRequestError(f"Token request failed with status {response.status}: {response.text()[:300]}")

        token_data = response.json()

        self.access_token = token_data.get("access_token")

        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            self._oauth_completed = True
            try:
                self.expires_at = time.time() + float(token_data["expires_in"])
            except (KeyError, TypeError, ValueError):
                self.expires_at = None

        return self.access_token


class ServiceNowRESTClientViaOAuthAuthorizationCode(HTTPClient):
    """ServiceNow REST client via OAuth 2.0 Authorization Code Flow
    Args:
        instance_url: The ServiceNow instance URL
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
        resilience: Rate limit and retry policy applied below every request
    """
    def __init__(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        access_token: Optional[str] = None,
        resilience: ResiliencePolicy | None = None,
    ) -> None:
        super().__init__(access_token or "", "Bearer", follow_redirects=False, resilience=resilience)

        self.instance_url = instance_url.rstrip('/')
        self.base_url = f"{self.instance_url}/api/now"
        self.oauth_auth_url = f"{self.instance_url}/oauth_auth.do"
        self.oauth_token_url = f"{self.instance_url}/oauth_token.do"
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.access_token = access_token
        self.refresh_token: Optional[str] = None

        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

        self._oauth_completed = access_token is not None

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the instance URL"""
        return self.instance_url

    def is_oauth_completed(self) -> bool:
        """Check if OAuth flow has been completed"""
        return self._oauth_completed

    def set_access_token(self, access_token: str) -> None:
        """Use a new access token for the requests that follow.

        The transport reads self.headers, so writing self.access_token on its
        own leaves the stale bearer token on every request.
        """
        self.access_token = access_token
        self.headers["Authorization"] = f"Bearer {access_token}"

    def get_authorization_url(self, state: Optional[str] = None, scope: str = "useraccount") -> str:
        """Generate OAuth authorization URL
        Args:
            state: Optional state parameter for security
            scope: OAuth scopes (default: useraccount)
        Returns:
            Authorization URL
        """
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope
        }

        if state:
            params["state"] = state

        return f"{self.oauth_auth_url}?{urlencode(params)}"

    async def initiate_oauth_flow(self, authorization_code: str) -> Optional[str]:
        """Complete OAuth flow with authorization code
        Args:
            authorization_code: The code received from OAuth callback
        Returns:
            Access token from OAuth exchange
        """
        return await self._exchange_code_for_token(authorization_code)

    async def refresh_access_token(self, refresh_token: Optional[str] = None) -> Optional[str]:
        """Refresh OAuth access token
        Args:
            refresh_token: The refresh token (uses stored token if not provided)
        Returns:
            New access token
        """
        token_to_use = refresh_token or self.refresh_token

        if not token_to_use:
            raise ValueError("No refresh token available")

        data = {
            "grant_type": "refresh_token",
            "refresh_token": token_to_use,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        request = HTTPRequest(
            method="POST",
            url=self.oauth_token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=data
        )

        response = await self.execute(request)

        if response.status >= _FIRST_REDIRECT_STATUS:  # a redirect is not followed: the secrets stay here
            raise BadRequestError(f"Token refresh failed with status {response.status}: {response.text()}")

        token_data = response.json()

        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token", self.refresh_token)

        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"

        return self.access_token

    async def _exchange_code_for_token(self, code: str) -> Optional[str]:
        """Exchange authorization code for access token
        Args:
            code: Authorization code from callback
        Returns:
            Access token from OAuth exchange
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        request = HTTPRequest(
            method="POST",
            url=self.oauth_token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=data
        )

        response = await self.execute(request)

        if response.status >= _FIRST_REDIRECT_STATUS:  # a redirect is not followed: the secrets stay here
            raise BadRequestError(f"Token exchange failed with status {response.status}: {response.text()}")

        token_data = response.json()

        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token")

        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            self._oauth_completed = True

        return self.access_token


class ServiceNowRESTClientViaOAuthROPC(HTTPClient):
    """ServiceNow REST client via OAuth 2.0 Resource Owner Password Credentials
    Note: This flow is not recommended by ServiceNow and should be avoided in production.It's incompatible with MFA and violates OAuth security best practices.
    Args:
        instance_url: The ServiceNow instance URL
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        username: The resource owner username
        password: The resource owner password
        access_token: Optional existing access token
    """
    def __init__(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        access_token: Optional[str] = None
    ) -> None:
        super().__init__(access_token or "", "Bearer", follow_redirects=False)

        self.instance_url = instance_url.rstrip('/')
        self.base_url = f"{self.instance_url}/api/now"
        self.oauth_token_url = f"{self.instance_url}/oauth_token.do"
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password
        self.access_token = access_token

        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })

        self._oauth_completed = access_token is not None

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url

    def get_instance_url(self) -> str:
        """Get the instance URL"""
        return self.instance_url

    def is_oauth_completed(self) -> bool:
        """Check if OAuth flow has been completed"""
        return self._oauth_completed

    async def fetch_token(self) -> Optional[str]:
        """Fetch access token using password grant
        Returns:
            Access token from OAuth exchange
        """
        data = {
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        request = HTTPRequest(
            method="POST",
            url=self.oauth_token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=data
        )

        response = await self.execute(request)

        if response.status >= _FIRST_REDIRECT_STATUS:  # a redirect is not followed: the secrets stay here
            raise BadRequestError(f"Token request failed with status {response.status}: {response.text()}")

        token_data = response.json()

        self.access_token = token_data.get("access_token")

        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            self._oauth_completed = True

        return self.access_token


@dataclass
class ServiceNowUsernamePasswordConfig:
    """Configuration for ServiceNow REST client via username and password
    Args:
        instance_url: The ServiceNow instance URL
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL (always True for ServiceNow)
    """
    instance_url: str
    username: str
    password: str
    ssl: bool = True

    def create_client(self) -> ServiceNowRESTClientViaUsernamePassword:
        return ServiceNowRESTClientViaUsernamePassword(
            self.instance_url,
            self.username,
            self.password
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ServiceNowTokenConfig:
    """Configuration for ServiceNow REST client via bearer token
    Args:
        instance_url: The ServiceNow instance URL
        token: The bearer token to use for authentication
        ssl: Whether to use SSL (always True for ServiceNow)
    """
    instance_url: str
    token: str
    ssl: bool = True

    def create_client(self) -> ServiceNowRESTClientViaToken:
        return ServiceNowRESTClientViaToken(self.instance_url, self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ServiceNowAPIKeyConfig:
    """Configuration for ServiceNow REST client via API key
    Args:
        instance_url: The ServiceNow instance URL
        api_key: The API key to use for authentication
        header_name: The header name for the API key (default: x-sn-apikey)
        ssl: Whether to use SSL (always True for ServiceNow)
    """
    instance_url: str
    api_key: str
    header_name: str = "x-sn-apikey"
    ssl: bool = True

    def create_client(self) -> ServiceNowRESTClientViaAPIKey:
        return ServiceNowRESTClientViaAPIKey(
            self.instance_url,
            self.api_key,
            self.header_name
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ServiceNowOAuthClientCredentialsConfig:
    """Configuration for ServiceNow REST client via OAuth 2.0 Client Credentials
    Args:
        instance_url: The ServiceNow instance URL
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        access_token: Optional existing access token
        ssl: Whether to use SSL (always True for ServiceNow)
    """
    instance_url: str
    client_id: str
    client_secret: str
    access_token: Optional[str] = None
    ssl: bool = True

    def create_client(self) -> ServiceNowRESTClientViaOAuthClientCredentials:
        return ServiceNowRESTClientViaOAuthClientCredentials(
            self.instance_url,
            self.client_id,
            self.client_secret,
            self.access_token
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ServiceNowOAuthAuthorizationCodeConfig:
    """Configuration for ServiceNow REST client via OAuth 2.0 Authorization Code
    Args:
        instance_url: The ServiceNow instance URL
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
        ssl: Whether to use SSL (always True for ServiceNow)
    """
    instance_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    access_token: Optional[str] = None
    ssl: bool = True

    def create_client(self) -> ServiceNowRESTClientViaOAuthAuthorizationCode:
        return ServiceNowRESTClientViaOAuthAuthorizationCode(
            self.instance_url,
            self.client_id,
            self.client_secret,
            self.redirect_uri,
            self.access_token
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


@dataclass
class ServiceNowOAuthROPCConfig:
    """Configuration for ServiceNow REST client via OAuth 2.0 ROPC
    Note: Not recommended - incompatible with MFA and violates OAuth best practices
    Args:
        instance_url: The ServiceNow instance URL
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        username: The resource owner username
        password: The resource owner password
        access_token: Optional existing access token
        ssl: Whether to use SSL (always True for ServiceNow)
    """
    instance_url: str
    client_id: str
    client_secret: str
    username: str
    password: str
    access_token: Optional[str] = None
    ssl: bool = True

    def create_client(self) -> ServiceNowRESTClientViaOAuthROPC:
        return ServiceNowRESTClientViaOAuthROPC(
            self.instance_url,
            self.client_id,
            self.client_secret,
            self.username,
            self.password,
            self.access_token
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class ServiceNowClient(IClient):
    """Builder class for ServiceNow clients with different construction methods"""

    def __init__(
        self,
        client: Union[
            ServiceNowRESTClientViaUsernamePassword,
            ServiceNowRESTClientViaToken,
            ServiceNowRESTClientViaAPIKey,
            ServiceNowRESTClientViaOAuthClientCredentials,
            ServiceNowRESTClientViaOAuthAuthorizationCode,
            ServiceNowRESTClientViaOAuthROPC
        ]
    ) -> None:
        """Initialize with a ServiceNow client object"""
        self.client = client

    def get_client(self) -> Union[
        ServiceNowRESTClientViaUsernamePassword,
        ServiceNowRESTClientViaToken,
        ServiceNowRESTClientViaAPIKey,
        ServiceNowRESTClientViaOAuthClientCredentials,
        ServiceNowRESTClientViaOAuthAuthorizationCode,
        ServiceNowRESTClientViaOAuthROPC
    ]:
        """Return the ServiceNow client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    def get_instance_url(self) -> str:
        """Get the instance URL"""
        return self.client.get_instance_url()

    @classmethod
    def build_with_config(
        cls,
        config: Union[
            ServiceNowUsernamePasswordConfig,
            ServiceNowTokenConfig,
            ServiceNowAPIKeyConfig,
            ServiceNowOAuthClientCredentialsConfig,
            ServiceNowOAuthAuthorizationCodeConfig,
            ServiceNowOAuthROPCConfig
        ]
    ) -> "ServiceNowClient":
        """Build ServiceNowClient with configuration
        Args:
            config: ServiceNow configuration instance
        Returns:
            ServiceNowClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "ServiceNowClient":
        """Build ServiceNowClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID to get specific instance config
        Returns:
            ServiceNowClient instance
        """
        try:
            config = await cls._get_connector_config(logger, config_service, connector_instance_id)
            if not config:
                raise ValueError(f"Failed to get ServiceNow connector configuration for instance {connector_instance_id}")
            auth_config = config.get("auth", {}) or {}
            auth_type = auth_config.get("authType", "USERNAME_PASSWORD")  # USERNAME_PASSWORD or TOKEN or API_KEY or OAUTH_CLIENT_CREDENTIALS or OAUTH_AUTHORIZATION_CODE or OAUTH_ROPC
            if auth_type == "USERNAME_PASSWORD":
                client = ServiceNowRESTClientViaUsernamePassword(
                    instance_url=auth_config.get("instanceUrl", ""),
                    username=auth_config.get("username", ""),
                    password=auth_config.get("password", "")
                )
            elif auth_type == "TOKEN":
                client = ServiceNowRESTClientViaToken(
                    instance_url=auth_config.get("instanceUrl", ""),
                    token=auth_config.get("token", "")
                )
            elif auth_type == "API_KEY":
                client = ServiceNowRESTClientViaAPIKey(
                    instance_url=auth_config.get("instanceUrl", ""),
                    api_key=auth_config.get("apiKey", ""),
                    header_name=auth_config.get("headerName", "x-sn-apikey")
                )
            elif auth_type == "OAUTH_CLIENT_CREDENTIALS":
                credentials_config = auth_config.get("credentials", {})
                client = ServiceNowRESTClientViaOAuthClientCredentials(
                    instance_url=auth_config.get("instanceUrl", ""),
                    client_id=auth_config.get("clientId", ""),
                    client_secret=auth_config.get("clientSecret", ""),
                    access_token=credentials_config.get("access_token")
                )
            elif auth_type == "OAUTH_AUTHORIZATION_CODE":
                credentials_config = auth_config.get("credentials", {})
                client = ServiceNowRESTClientViaOAuthAuthorizationCode(
                    instance_url=auth_config.get("instanceUrl", ""),
                    client_id=auth_config.get("clientId", ""),
                    client_secret=auth_config.get("clientSecret", ""),
                    redirect_uri=auth_config.get("redirectUri", ""),
                    access_token=credentials_config.get("access_token")
                )
            elif auth_type == "OAUTH_ROPC":
                credentials_config = auth_config.get("credentials", {})
                client = ServiceNowRESTClientViaOAuthROPC(
                    instance_url=auth_config.get("instanceUrl", ""),
                    client_id=auth_config.get("clientId", ""),
                    client_secret=auth_config.get("clientSecret", ""),
                    username=auth_config.get("username", ""),
                    password=auth_config.get("password", ""),
                    access_token=credentials_config.get("access_token")
                )
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")
            return cls(client=client)
        except Exception as e:
            logger.error(f"Failed to build ServiceNow client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger, config_service: ConfigurationService, connector_instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetch connector config from etcd for ServiceNow."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get ServiceNow connector configuration for instance {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to get ServiceNow connector config: {e}")
            raise ValueError(f"Failed to get ServiceNow connector configuration for instance {connector_instance_id}")
