import base64
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.http.exception.exception import BadRequestError
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


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
        instance_url: The ServiceNow instance URL (e.g., https://dev12345.service-now.com)
        username: The username to use for authentication
        password: The password to use for authentication
    """
    def __init__(self, instance_url: str, username: str, password: str) -> None:
        # Encode credentials for Basic auth
        credentials = f"{username}:{password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        # Initialize with empty token and override headers
        super().__init__("", "")
        self.instance_url = instance_url.rstrip('/')
        self.base_url = f"{self.instance_url}"
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
        super().__init__(token, "Bearer")
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
    """
    def __init__(self, instance_url: str, api_key: str, header_name: str = "x-sn-apikey") -> None:
        super().__init__("", "")
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
    """
    def __init__(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        access_token: Optional[str] = None
    ) -> None:
        super().__init__(access_token or "", "Bearer")

        self.instance_url = instance_url.rstrip('/')
        self.base_url = f"{self.instance_url}/api/now"
        self.oauth_token_url = f"{self.instance_url}/oauth_token.do"
        self.client_id = client_id
        self.client_secret = client_secret
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

        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            raise BadRequestError(f"Token request failed with status {response.status}: {response.text()}")

        token_data = response.json()

        self.access_token = token_data.get("access_token")

        if self.access_token:
            self.headers["Authorization"] = f"Bearer {self.access_token}"
            self._oauth_completed = True

        return self.access_token


class ServiceNowRESTClientViaOAuthAuthorizationCode(HTTPClient):
    """ServiceNow REST client via OAuth 2.0 Authorization Code Flow
    Args:
        instance_url: The ServiceNow instance URL
        client_id: The OAuth client ID
        client_secret: The OAuth client secret
        redirect_uri: The redirect URI for OAuth flow
        access_token: Optional existing access token
    """
    def __init__(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        access_token: Optional[str] = None
    ) -> None:
        super().__init__(access_token or "", "Bearer")

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

        if response.status >= HttpStatusCode.BAD_REQUEST.value:
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

        if response.status >= HttpStatusCode.BAD_REQUEST.value:
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
        super().__init__(access_token or "", "Bearer")

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

        if response.status >= HttpStatusCode.BAD_REQUEST.value:
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
    ) -> "ServiceNowClient":
        """Build ServiceNowClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            ServiceNowClient instance
        """
        try:
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                raise ValueError("Failed to get ServiceNow connector configuration")
            auth_type = config.get("authType", "USERNAME_PASSWORD")  # USERNAME_PASSWORD or TOKEN or API_KEY or OAUTH_CLIENT_CREDENTIALS or OAUTH_AUTHORIZATION_CODE or OAUTH_ROPC
            auth_config = config.get("auth", {})
            if auth_type == "USERNAME_PASSWORD":
                client = ServiceNowRESTClientViaUsernamePassword(
                    instance_url=auth_config.get("instanceUrl"),
                    username=auth_config.get("username"),
                    password=auth_config.get("password")
                )
            elif auth_type == "TOKEN":
                client = ServiceNowRESTClientViaToken(
                    instance_url=auth_config.get("instanceUrl"),
                    token=auth_config.get("token")
                )
            elif auth_type == "API_KEY":
                client = ServiceNowRESTClientViaAPIKey(
                    instance_url=auth_config.get("instanceUrl"),
                    api_key=auth_config.get("apiKey"),
                    header_name=auth_config.get("headerName", "x-sn-apikey")
                )
            elif auth_type == "OAUTH_CLIENT_CREDENTIALS":
                credentials_config = auth_config.get("credentials", {})
                client = ServiceNowRESTClientViaOAuthClientCredentials(
                    instance_url=auth_config.get("instanceUrl"),
                    client_id=auth_config.get("clientId"),
                    client_secret=auth_config.get("clientSecret"),
                    access_token=credentials_config.get("access_token")
                )
            elif auth_type == "OAUTH_AUTHORIZATION_CODE":
                credentials_config = auth_config.get("credentials", {})
                client = ServiceNowRESTClientViaOAuthAuthorizationCode(
                    instance_url=auth_config.get("instanceUrl"),
                    client_id=auth_config.get("clientId"),
                    client_secret=auth_config.get("clientSecret"),
                    redirect_uri=auth_config.get("redirectUri"),
                    access_token=credentials_config.get("access_token")
                )
            elif auth_type == "OAUTH_ROPC":
                credentials_config = auth_config.get("credentials", {})
                client = ServiceNowRESTClientViaOAuthROPC(
                    instance_url=auth_config.get("instanceUrl"),
                    client_id=auth_config.get("clientId"),
                    client_secret=auth_config.get("clientSecret"),
                    username=auth_config.get("username"),
                    password=auth_config.get("password"),
                    access_token=credentials_config.get("access_token")
                )
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")
            return cls(client=client)
        except Exception as e:
            logger.error(f"Failed to build ServiceNow client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger, config_service: ConfigurationService) -> Dict[str, Any]:
        """Fetch connector config from etcd for ServiceNow."""
        try:
            config = await config_service.get_config("/services/connectors/servicenow/config")
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get ServiceNow connector config: {e}")
            return {}
