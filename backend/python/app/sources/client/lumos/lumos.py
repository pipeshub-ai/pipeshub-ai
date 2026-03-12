import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

LUMOS_BASE_URL = "https://api.lumos.com"


class LumosRESTClientViaToken(HTTPClient):
    """Lumos REST client via Bearer token (OAuth or API token).

    Args:
        token: The Bearer token for authentication.
        base_url: The Lumos API base URL.
        token_type: Token type (default Bearer).
    """

    def __init__(
        self,
        token: str,
        base_url: str = LUMOS_BASE_URL,
        token_type: str = "Bearer",
    ) -> None:
        if not token:
            raise ValueError("Lumos token cannot be empty")
        super().__init__(token, token_type)
        self.base_url = base_url

    def get_base_url(self) -> str:
        return self.base_url

    def get_token(self) -> str:
        return self.headers.get("Authorization", "").replace("Bearer ", "")

    def set_token(self, token: str) -> None:
        self.headers["Authorization"] = f"Bearer {token}"


class LumosRESTClientViaApiKey(HTTPClient):
    """Lumos REST client via API key.

    Lumos API keys are passed as Bearer tokens in the Authorization header.

    Args:
        api_key: The API key for authentication.
        base_url: The Lumos API base URL.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = LUMOS_BASE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("Lumos API key cannot be empty")
        super().__init__(api_key, "Bearer")
        self.base_url = base_url

    def get_base_url(self) -> str:
        return self.base_url


@dataclass
class LumosTokenConfig:
    """Configuration for Lumos REST client via Bearer token.

    Args:
        token: The Bearer token.
        base_url: The Lumos API base URL.
    """

    token: str
    base_url: str = LUMOS_BASE_URL

    def create_client(self) -> LumosRESTClientViaToken:
        return LumosRESTClientViaToken(self.token, self.base_url)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LumosApiKeyConfig:
    """Configuration for Lumos REST client via API key.

    Args:
        api_key: The API key.
        base_url: The Lumos API base URL.
    """

    api_key: str
    base_url: str = LUMOS_BASE_URL

    def create_client(self) -> LumosRESTClientViaApiKey:
        return LumosRESTClientViaApiKey(self.api_key, self.base_url)

    def to_dict(self) -> dict:
        return asdict(self)


class LumosClient(IClient):
    """Builder class for Lumos clients with different authentication methods."""

    def __init__(
        self, client: LumosRESTClientViaToken | LumosRESTClientViaApiKey
    ) -> None:
        self.client = client

    def get_client(self) -> LumosRESTClientViaToken | LumosRESTClientViaApiKey:
        return self.client

    @classmethod
    def build_with_config(
        cls, config: LumosTokenConfig | LumosApiKeyConfig
    ) -> "LumosClient":
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "LumosClient":
        try:
            config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not config:
                raise ValueError("Failed to get Lumos connector configuration")

            auth_config = config.get("auth", {}) or {}
            credentials_config = config.get("credentials", {}) or {}
            auth_type = auth_config.get("authType", "API_KEY")

            if auth_type == "OAUTH" or auth_type == "BEARER_TOKEN":
                token = credentials_config.get(
                    "access_token", ""
                ) or auth_config.get("bearerToken", "")
                if not token:
                    raise ValueError("Token required for token auth type")
                client = LumosRESTClientViaToken(token)

            elif auth_type == "API_KEY":
                api_key = auth_config.get("apiKey", "") or credentials_config.get(
                    "api_key", ""
                )
                if not api_key:
                    raise ValueError("API key required for API_KEY auth type")
                client = LumosRESTClientViaApiKey(api_key)

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build Lumos client from services: {str(e)}")
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: Dict[str, Any],
        logger: logging.Logger,
    ) -> "LumosClient":
        """Build LumosClient using toolset configuration from etcd.

        Args:
            toolset_config: Toolset configuration dictionary from etcd.
            logger: Logger instance.

        Returns:
            LumosClient instance.
        """
        try:
            if not toolset_config:
                raise ValueError("Toolset config is required for Lumos client")

            credentials_config = toolset_config.get("credentials", {}) or {}
            auth_type = toolset_config.get("authType", "").upper()

            if auth_type == "OAUTH":
                token = credentials_config.get("access_token", "")
                if not token:
                    raise ValueError(
                        "Access token not found in OAuth credentials. Please re-authenticate."
                    )
                client = LumosRESTClientViaToken(token)

            elif auth_type == "API_KEY":
                api_key = (
                    toolset_config.get("api_key", "")
                    or toolset_config.get("apiKey", "")
                    or credentials_config.get("api_key", "")
                )
                if not api_key:
                    raise ValueError("API key required for API_KEY auth type")
                client = LumosRESTClientViaApiKey(api_key)

            elif auth_type == "API_TOKEN":
                token = (
                    toolset_config.get("api_token", "")
                    or toolset_config.get("apiToken", "")
                )
                if not token:
                    raise ValueError("API token required for API_TOKEN auth type")
                client = LumosRESTClientViaToken(token)

            else:
                raise ValueError(
                    f"Unsupported auth type: {auth_type}. Supported: OAUTH, API_KEY, API_TOKEN"
                )

            logger.info(
                f"Built Lumos client from toolset config with auth type: {auth_type}"
            )
            return cls(client)

        except Exception as e:
            logger.error(
                f"Failed to build Lumos client from toolset config: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config or not isinstance(config, dict):
                raise ValueError(
                    f"Failed to get Lumos connector configuration for instance {connector_instance_id}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get Lumos connector config: {e}")
            raise ValueError(
                f"Failed to get Lumos connector configuration for instance {connector_instance_id}"
            ) from e
