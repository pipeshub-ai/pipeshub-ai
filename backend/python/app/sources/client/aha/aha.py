import logging
from dataclasses import asdict, dataclass
from typing import Any

from app.config.configuration_service import ConfigurationService

# from app.config.configuration_service import ConfigurationService
# from app.sources.client.http.exception.exception import HttpStatusCode
# from app.sources.client.http.http_client import HTTPClient
# from app.sources.client.http.http_request import HTTPRequest
# from app.sources.client.iclient import IClient
# from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.http.exception.exception import (
    HttpStatusCode,  # type: ignore[import-untyped]
)
from app.sources.client.http.http_client import (
    HTTPClient,  # type: ignore[import-untyped]
)
from app.sources.client.http.http_request import (
    HTTPRequest,  # type: ignore[import-untyped]
)
from app.sources.client.http.http_response import (
    HTTPResponse,  # type: ignore[import-untyped]
)
from app.sources.client.iclient import IClient  # type: ignore[import-untyped]


class AhaRESTClientViaToken(HTTPClient):
    """Aha! REST client via Bearer token authentication.

    Args:
        base_url: Base URL to the Aha! REST API (e.g., https://company.aha.io/api/v1)
        token: The token to use for authentication
        token_type: The type of token to use for authentication (default: "Bearer")

    """

    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        # self.base_url = base_url.rstrip("/")
        self.base_url = base_url

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def get_features(
        self,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HTTPResponse:
        """Get a list of features from Aha!

        Args:
            query_params: Optional query parameters for filtering/pagination
            headers: Optional additional headers to include in the request

        Returns:
            HTTPResponse containing the features data

        Raises:
            Exception: If the API request fails

        """
        url = f"{self.base_url}/features"
        request_headers = dict(headers or {})
        request_headers.setdefault("Content-Type", "application/json")

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=request_headers,
            query_params=dict(query_params or {}),
            body=None,


            
        )

        response = await self.execute(request)

        # Handle common error cases
        if response.status == HttpStatusCode.UNAUTHORIZED.value:
            msg = "Authentication failed. Please check your access token."
            raise ValueError(msg)
        if response.status == HttpStatusCode.FORBIDDEN.value:
            msg = "Access forbidden. Please check your permissions."
            raise PermissionError(msg)
        if response.status == HttpStatusCode.NOT_FOUND.value:
            msg = "Features endpoint not found."
            raise ValueError(msg)
        if response.status == HttpStatusCode.TOO_MANY_REQUESTS.value:
            msg = "Rate limit exceeded. Please try again later."
            raise RuntimeError(msg)
        if response.status >= 500:
            msg = f"Server error (status {response.status}). Please try again later."
            raise RuntimeError(msg)
        if response.status >= 400:
            response.raise_for_status()

        return response

    async def get_products(
        self,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HTTPResponse:
        """Get a list of products from Aha!

        Args:
            query_params: Optional query parameters for filtering/pagination
            headers: Optional additional headers to include in the request

        Returns:
            HTTPResponse containing the products data

        Raises:
            Exception: If the API request fails

        """
        url = f"{self.base_url}/products"
        request_headers = dict(headers or {})
        request_headers.setdefault("Content-Type", "application/json")

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=request_headers,
            query_params=dict(query_params or {}),
            body=None,
        )

        response = await self.execute(request)

        # Handle common error cases
        if response.status == HttpStatusCode.UNAUTHORIZED.value:
            msg = "Authentication failed. Please check your access token."
            raise ValueError(msg)
        if response.status == HttpStatusCode.FORBIDDEN.value:
            msg = "Access forbidden. Please check your permissions."
            raise PermissionError(msg)
        if response.status == HttpStatusCode.NOT_FOUND.value:
            msg = "Products endpoint not found."
            raise ValueError(msg)
        if response.status == HttpStatusCode.TOO_MANY_REQUESTS.value:
            msg = "Rate limit exceeded. Please try again later."
            raise RuntimeError(msg)
        if response.status >= 500:
            msg = f"Server error (status {response.status}). Please try again later."
            raise RuntimeError(msg)
        if response.status >= 400:
            response.raise_for_status()

        return response


class AhaRESTClientViaApiKey(HTTPClient):
    """Aha! REST client via API key authentication.

    Args:
        base_url: Base URL to the Aha! REST API (e.g., https://company.aha.io/api/v1)
        api_key: The API key to use for authentication
        token_type: The type of token header to use (default: "ApiKey")

    """

    def __init__(self, base_url: str, api_key: str, token_type: str = "ApiKey") -> None:
        # HTTPClient will attach the token as the Authorization header: "{token_type} {token}"
        super().__init__(api_key, token_type)
        self.base_url = base_url.rstrip("/")

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url

    async def get_features(
        self,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HTTPResponse:
        """Get a list of features from Aha!

        Args:
            query_params: Optional query parameters for filtering/pagination
            headers: Optional additional headers to include in the request

        Returns:
            HTTPResponse containing the features data

        Raises:
            Exception: If the API request fails

        """
        url = f"{self.base_url}/features"
        request_headers = dict(headers or {})
        request_headers.setdefault("Content-Type", "application/json")

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=request_headers,
            query_params=dict(query_params or {}),
            body=None,
        )

        response = await self.execute(request)

        # Handle common error cases
        if response.status == HttpStatusCode.UNAUTHORIZED.value:
            msg = "Authentication failed. Please check your access token."
            raise ValueError(msg)
        if response.status == HttpStatusCode.FORBIDDEN.value:
            msg = "Access forbidden. Please check your permissions."
            raise PermissionError(msg)
        if response.status == HttpStatusCode.NOT_FOUND.value:
            msg = "Features endpoint not found."
            raise ValueError(msg)
        if response.status == HttpStatusCode.TOO_MANY_REQUESTS.value:
            msg = "Rate limit exceeded. Please try again later."
            raise RuntimeError(msg)
        if response.status >= 500:
            msg = f"Server error (status {response.status}). Please try again later."
            raise RuntimeError(msg)
        if response.status >= 400:
            response.raise_for_status()

        return response

    async def get_products(
        self,
        query_params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HTTPResponse:
        """Get a list of products from Aha!

        Args:
            query_params: Optional query parameters for filtering/pagination
            headers: Optional additional headers to include in the request

        Returns:
            HTTPResponse containing the products data

        Raises:
            Exception: If the API request fails

        """
        url = f"{self.base_url}/products"
        request_headers = dict(headers or {})
        request_headers.setdefault("Content-Type", "application/json")

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=request_headers,
            query_params=dict(query_params or {}),
            body=None,
        )

        response = await self.execute(request)

        # Handle common error cases
        if response.status == HttpStatusCode.UNAUTHORIZED.value:
            msg = "Authentication failed. Please check your access token."
            raise ValueError(msg)
        if response.status == HttpStatusCode.FORBIDDEN.value:
            msg = "Access forbidden. Please check your permissions."
            raise PermissionError(msg)
        if response.status == HttpStatusCode.NOT_FOUND.value:
            msg = "Products endpoint not found."
            raise ValueError(msg)
        if response.status == HttpStatusCode.TOO_MANY_REQUESTS.value:
            msg = "Rate limit exceeded. Please try again later."
            raise RuntimeError(msg)
        if response.status >= 500:
            msg = f"Server error (status {response.status}). Please try again later."
            raise RuntimeError(msg)
        if response.status >= 400:
            response.raise_for_status()

        return response


@dataclass
class AhaTokenConfig:
    """Configuration for Aha! REST client via token
    Args:
        base_url: The base URL of the Aha! instance (e.g., https://company.aha.io/api/v1)
        access_token: The token to use for authentication
        ssl: Whether to use SSL.
        subdomain: Optional Aha! subdomain. If provided and base_url is not, base_url will be derived.
    """

    access_token: str
    base_url: str | None = None
    ssl: bool = False
    # Backwards compatibility with existing references
    subdomain: str | None = None

    def create_client(self) -> AhaRESTClientViaToken:
        """Create an AhaRESTClientViaToken instance from this configuration."""
        resolved_base_url = (self.base_url or "").rstrip("/") if self.base_url else ""
        if not resolved_base_url:
            if not self.subdomain:
                msg = "Either base_url or subdomain must be provided for AhaTokenConfig"
                raise ValueError(msg)
            resolved_base_url = f"https://{self.subdomain}.aha.io/api/v1"
        return AhaRESTClientViaToken(resolved_base_url, self.access_token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary."""
        return asdict(self)


@dataclass
class AhaApiKeyConfig:
    """Configuration for Aha! REST client via API key
    Args:
        base_url: The base URL of the Aha! instance (e.g., https://company.aha.io/api/v1)
        api_key: The API key to use for authentication
        ssl: Whether to use SSL.
    """

    base_url: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> AhaRESTClientViaApiKey:
        return AhaRESTClientViaApiKey(self.base_url, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary."""
        return asdict(self)


class AhaClient(IClient):
    """Builder class for Aha! clients with different construction methods."""

    def __init__(
        self,
        client: AhaRESTClientViaToken | AhaRESTClientViaApiKey,
    ) -> None:
        """Initialize with an Aha! client object.

        Args:
            client: Aha REST client instance

        """
        self.client = client

    def get_client(self) -> AhaRESTClientViaToken | AhaRESTClientViaApiKey:
        """Return the Aha! client object."""
        return self.client

    @classmethod
    def build_with_config(cls, config: AhaTokenConfig | AhaApiKeyConfig) -> "AhaClient":
        """Build AhaClient with configuration.

        Args:
            config: AhaTokenConfig | AhaApiKeyConfig instance

        Returns:
            AhaClient instance

        """
        return cls(config.create_client())

    # ===== Confluence-like builder pattern (services-based) =====
    @classmethod
    async def build_from_services(
        cls,
        logger: "logging.Logger",
        config_service: "ConfigurationService",
    ) -> "AhaClient":
        """Build AhaClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            AhaClient instance.
        """
        try:
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                msg = "Failed to get Aha connector configuration"
                raise ValueError(msg)

            auth_config = (config.get("auth") or {}) if isinstance(config, dict) else {}
            if not auth_config:
                msg = "Auth configuration not found in Aha connector configuration"
                raise ValueError(msg)

            credentials_config = (config.get("credentials") or {}) if isinstance(config, dict) else {}
            if not credentials_config:
                msg = "Credentials configuration not found in Aha connector configuration"
                raise ValueError(msg)

            # Determine auth type
            auth_type = auth_config.get("authType", "BEARER_TOKEN")  # BEARER_TOKEN, API_KEY, OAUTH

            # Resolve base_url: prefer explicit base_url, else derive from subdomain
            base_url = (credentials_config.get("base_url") or "").rstrip("/")
            subdomain = (credentials_config.get("subdomain") or "").strip()
            if not base_url and subdomain:
                base_url = f"https://{subdomain}.aha.io/api/v1"

            if auth_type == "API_KEY":
                api_key = auth_config.get("api_key") or credentials_config.get("api_key") or ""
                if not base_url:
                    msg = "base_url or subdomain required for API_KEY auth type"
                    raise ValueError(msg)
                if not api_key:
                    msg = "api_key required for API_KEY auth type"
                    raise ValueError(msg)
                client = AhaRESTClientViaApiKey(base_url, api_key)

            elif auth_type == "BEARER_TOKEN":
                token = auth_config.get("bearerToken") or credentials_config.get("access_token") or ""
                if not base_url:
                    msg = "base_url or subdomain required for BEARER_TOKEN auth type"
                    raise ValueError(msg)
                if not token:
                    msg = "Token required for BEARER_TOKEN auth type"
                    raise ValueError(msg)
                client = AhaRESTClientViaToken(base_url, token)

            elif auth_type == "OAUTH":
                # Placeholder parity with Confluence; uses access_token same as bearer
                access_token = credentials_config.get("access_token") or auth_config.get("access_token") or ""
                if not base_url:
                    msg = "base_url or subdomain required for OAUTH auth type"
                    raise ValueError(msg)
                if not access_token:
                    msg = "Access token required for OAUTH auth type"
                    raise ValueError(msg)
                client = AhaRESTClientViaToken(base_url, access_token)

            else:
                msg = f"Invalid auth type: {auth_type}"
                raise ValueError(msg)

            return cls(client)

        except Exception as e:
            logger.exception(f"Failed to build Aha client from services: {e!s}")
            raise

    @staticmethod
    async def _get_connector_config(logger: "logging.Logger", config_service: "ConfigurationService") -> dict[str, Any]:
        """Fetch connector config from etcd for Aha."""
        try:
            # Keep path consistent with Confluence pattern
            return await config_service.get_config("/services/connectors/aha/config") or {}
        except Exception as e:
            logger.exception(f"Failed to get Aha connector config: {e}")
            return {}

