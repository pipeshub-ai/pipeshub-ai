"""ClickUp client implementation.

This module provides clients for interacting with the ClickUp API using either:
1. Personal API Token authentication (pk_* tokens)
2. OAuth 2.0 access token authentication

ClickUp API supports both v2 and v3 versions. The version is configurable
via the client, and the base URL is constructed accordingly.

Authentication Reference: https://developer.clickup.com/docs/authentication
API v2 Reference: https://clickup.com/api/developer-portal/clickup20api/
API v3 Reference: https://clickup.com/api/developer-portal/clickupapi/
"""

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, cast

from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


@dataclass
class ClickUpResponse:
    """Standardized ClickUp API response wrapper."""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class ClickUpRESTClientViaPersonalToken(HTTPClient):
    """ClickUp REST client via Personal API Token.

    Personal tokens begin with pk_ and are passed directly in the
    Authorization header without a Bearer prefix.

    Args:
        token: The personal API token (pk_*)
        version: API version to use ("v2" or "v3", default: "v2")
    """

    def __init__(self, token: str, version: str = "v2") -> None:
        # Initialize with empty token_type; we override the header below
        super().__init__(token, token_type="Bearer")
        self.base_url = f"https://api.clickup.com/api/{version}"
        self.version = version
        # ClickUp personal tokens: Authorization: {personal_token}
        self.headers["Authorization"] = token
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL including API version."""
        return self.base_url

    def get_version(self) -> str:
        """Get the API version."""
        return self.version


class ClickUpRESTClientViaOAuth(HTTPClient):
    """ClickUp REST client via OAuth 2.0 access token.

    OAuth tokens are passed as Bearer tokens in the Authorization header.

    Args:
        access_token: The OAuth access token
        version: API version to use ("v2" or "v3", default: "v2")
        client_id: OAuth client ID (for reference / token refresh)
        client_secret: OAuth client secret (for reference / token refresh)
    """

    def __init__(
        self,
        access_token: str,
        version: str = "v2",
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        super().__init__(access_token, "Bearer")
        self.base_url = f"https://api.clickup.com/api/{version}"
        self.version = version
        self.access_token = access_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL including API version."""
        return self.base_url

    def get_version(self) -> str:
        """Get the API version."""
        return self.version


@dataclass
class ClickUpPersonalTokenConfig:
    """Configuration for ClickUp client via Personal API Token.

    Args:
        token: The personal API token (pk_*)
        version: API version ("v2" or "v3", default: "v2")
    """

    token: str
    version: str = "v2"

    def create_client(self) -> ClickUpRESTClientViaPersonalToken:
        return ClickUpRESTClientViaPersonalToken(self.token, self.version)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClickUpOAuthConfig:
    """Configuration for ClickUp client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        version: API version ("v2" or "v3", default: "v2")
        client_id: OAuth client ID
        client_secret: OAuth client secret
    """

    access_token: str
    version: str = "v2"
    client_id: str | None = None
    client_secret: str | None = None

    def create_client(self) -> ClickUpRESTClientViaOAuth:
        return ClickUpRESTClientViaOAuth(
            self.access_token,
            self.version,
            self.client_id,
            self.client_secret,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClickUpClient(IClient):
    """Builder class for ClickUp clients with different authentication methods.

    Supports:
    - Personal API Token (pk_*) authentication
    - OAuth 2.0 access token authentication
    - API version selection (v2 or v3)
    """

    def __init__(
        self,
        client: ClickUpRESTClientViaPersonalToken | ClickUpRESTClientViaOAuth,
    ) -> None:
        """Initialize with a ClickUp client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> ClickUpRESTClientViaPersonalToken | ClickUpRESTClientViaOAuth:
        """Return the ClickUp client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @property
    def version(self) -> str:
        """Return the API version."""
        return self.client.get_version()

    @classmethod
    def build_with_config(
        cls,
        config: ClickUpPersonalTokenConfig | ClickUpOAuthConfig,
    ) -> "ClickUpClient":
        """Build ClickUpClient with configuration.

        Args:
            config: ClickUpPersonalTokenConfig or ClickUpOAuthConfig instance

        Returns:
            ClickUpClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "ClickUpClient":
        """Build ClickUpClient using configuration service.

        Supports two authentication strategies:
        1. PERSONAL_TOKEN: For personal API tokens (pk_*)
        2. OAUTH: For OAuth 2.0 access tokens

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            ClickUpClient instance
        """
        try:
            config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not config:
                raise ValueError("Failed to get ClickUp connector configuration")

            auth_config: dict[str, Any] = cast(
                dict[str, Any], config.get("auth", {}) or {}
            )
            credentials_config: dict[str, Any] = cast(
                dict[str, Any], config.get("credentials", {}) or {}
            )
            auth_type: str = str(auth_config.get("authType", "PERSONAL_TOKEN"))
            version: str = str(config.get("version", "v2"))

            if auth_type == "OAUTH":
                access_token: str = str(credentials_config.get("access_token", ""))
                client_id: str = str(auth_config.get("clientId", ""))
                client_secret: str = str(auth_config.get("clientSecret", ""))

                # Try shared OAuth config if credentials are missing
                oauth_config_id: str | None = cast(
                    str | None, auth_config.get("oauthConfigId")
                )
                if oauth_config_id and not (client_id and client_secret):
                    try:
                        oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                            "/services/oauth/clickup", default=[]
                        )
                        oauth_configs: list[Any] = (
                            cast(list[Any], oauth_configs_raw)
                            if isinstance(oauth_configs_raw, list)
                            else []
                        )
                        for cfg in oauth_configs:
                            c: dict[str, Any] = cast(dict[str, Any], cfg)
                            if c.get("_id") == oauth_config_id:
                                shared: dict[str, Any] = cast(
                                    dict[str, Any], c.get("config", {})
                                )
                                client_id = str(
                                    shared.get("clientId")
                                    or shared.get("client_id")
                                    or client_id
                                )
                                client_secret = str(
                                    shared.get("clientSecret")
                                    or shared.get("client_secret")
                                    or client_secret
                                )
                                break
                    except Exception as e:
                        logger.warning(
                            f"Failed to fetch shared OAuth config: {e}"
                        )

                if not access_token:
                    raise ValueError(
                        "Access token required for OAuth auth type"
                    )

                oauth_config = ClickUpOAuthConfig(
                    access_token=access_token,
                    version=version,
                    client_id=client_id,
                    client_secret=client_secret,
                )
                return cls(oauth_config.create_client())

            elif auth_type == "PERSONAL_TOKEN":
                token: str = str(
                    auth_config.get("apiToken", "")
                    or auth_config.get("token", "")
                )
                if not token:
                    raise ValueError(
                        "Personal token required for PERSONAL_TOKEN auth type"
                    )

                token_config = ClickUpPersonalTokenConfig(
                    token=token, version=version
                )
                return cls(token_config.create_client())

            else:
                raise ValueError(f"Invalid auth type: {auth_type}")

        except Exception as e:
            logger.error(
                f"Failed to build ClickUp client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "ClickUpClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            ClickUpClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )
            version: str = str(toolset_config.get("version", "v2"))

            access_token: str = str(credentials.get("access_token", ""))
            if not access_token:
                raise ValueError("Access token not found in toolset config")

            client_id: str = str(auth_config.get("clientId", ""))
            client_secret: str = str(auth_config.get("clientSecret", ""))

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service and not (
                client_id and client_secret
            ):
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/clickup", default=[]
                    )
                    oauth_configs: list[Any] = (
                        cast(list[Any], oauth_configs_raw)
                        if isinstance(oauth_configs_raw, list)
                        else []
                    )
                    for cfg in oauth_configs:
                        c: dict[str, Any] = cast(dict[str, Any], cfg)
                        if c.get("_id") == oauth_config_id:
                            shared: dict[str, Any] = cast(
                                dict[str, Any], c.get("config", {})
                            )
                            client_id = str(
                                shared.get("clientId")
                                or shared.get("client_id")
                                or client_id
                            )
                            client_secret = str(
                                shared.get("clientSecret")
                                or shared.get("client_secret")
                                or client_secret
                            )
                            break
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch shared OAuth config: {e}"
                    )

            oauth_config = ClickUpOAuthConfig(
                access_token=access_token,
                version=version,
                client_id=client_id,
                client_secret=client_secret,
            )
            return cls(oauth_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build ClickUp client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for ClickUp."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get ClickUp connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get ClickUp connector config: {e}")
            raise ValueError(
                f"Failed to get ClickUp connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
