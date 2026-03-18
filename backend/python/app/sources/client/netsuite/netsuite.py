"""NetSuite client implementation.

This module provides a client for interacting with the NetSuite SuiteTalk
REST API using a pre-generated Bearer token (Token-Based Authentication).

NetSuite SuiteTalk REST supports OAuth 1.0 TBA and OAuth 2.0. For simplicity,
this client accepts a pre-generated Bearer token.

Authentication Reference: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_157373248498.html
API Reference: https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/chapter_1540391670.html
"""

import base64
import json
import logging
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class NetSuiteResponse(BaseModel):
    """Standardized NetSuite API response wrapper.

    The data field supports JSON responses (dict/list) and binary file
    downloads (bytes). When serializing to dict/JSON, binary data is
    automatically base64-encoded.
    """

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | bytes | None = Field(
        default=None, description="Response data (JSON) or file content (bytes)"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    class Config:
        """Pydantic configuration."""

        extra = "allow"

    def to_dict(self) -> dict[str, object]:
        """Convert response to dictionary.

        Binary data is base64-encoded for safe serialization.
        """
        result = self.model_dump(exclude_none=True)
        if isinstance(result.get("data"), bytes):
            result["data"] = base64.b64encode(result["data"]).decode("utf-8")
        return result

    def to_json(self) -> str:
        """Convert response to JSON string.

        Binary data is base64-encoded for safe serialization.
        """
        if isinstance(self.data, bytes):
            result = self.model_dump(exclude_none=True)
            result["data"] = base64.b64encode(self.data).decode("utf-8")
            return json.dumps(result)
        return self.model_dump_json(exclude_none=True)


# ---------------------------------------------------------------------------
# REST client class
# ---------------------------------------------------------------------------


class NetSuiteRESTClientViaToken(HTTPClient):
    """NetSuite REST client via pre-generated Bearer token.

    Uses a pre-generated Bearer token for authentication against the
    NetSuite SuiteTalk REST API.

    Args:
        token: The pre-generated Bearer token
        account_id: NetSuite account ID (e.g. "1234567" or "1234567_SB1")
    """

    def __init__(
        self,
        token: str,
        account_id: str,
    ) -> None:
        super().__init__(token, token_type="Bearer")
        self.account_id = account_id
        self.base_url = (
            f"https://{account_id}.suitetalk.api.netsuite.com/services/rest"
        )
        self.headers["Content-Type"] = "application/json"

    def get_base_url(self) -> str:
        """Get the base URL."""
        return self.base_url


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class NetSuiteTokenConfig(BaseModel):
    """Configuration for NetSuite client via pre-generated Bearer token.

    Args:
        token: The pre-generated Bearer token
        account_id: NetSuite account ID (e.g. "1234567" or "1234567_SB1")
    """

    token: str
    account_id: str

    def create_client(self) -> NetSuiteRESTClientViaToken:
        return NetSuiteRESTClientViaToken(self.token, self.account_id)


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class NetSuiteAuthConfig(BaseModel):
    """Auth section of the NetSuite connector configuration from etcd."""

    accountId: str | None = None
    token: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class NetSuiteCredentialsConfig(BaseModel):
    """Credentials section of the NetSuite connector configuration."""

    access_token: str | None = None

    class Config:
        extra = "allow"


class NetSuiteConnectorConfig(BaseModel):
    """Top-level NetSuite connector configuration from etcd."""

    auth: NetSuiteAuthConfig = Field(default_factory=NetSuiteAuthConfig)
    credentials: NetSuiteCredentialsConfig = Field(
        default_factory=NetSuiteCredentialsConfig
    )

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class NetSuiteClient(IClient):
    """Builder class for NetSuite clients.

    Supports:
    - Pre-generated Bearer token authentication (Token-Based Auth)
    """

    def __init__(
        self,
        client: NetSuiteRESTClientViaToken,
    ) -> None:
        """Initialize with a NetSuite client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> NetSuiteRESTClientViaToken:
        """Return the NetSuite client object."""
        return self.client

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(
        cls,
        config: NetSuiteTokenConfig,
    ) -> "NetSuiteClient":
        """Build NetSuiteClient with configuration.

        Args:
            config: NetSuiteTokenConfig instance

        Returns:
            NetSuiteClient instance
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "NetSuiteClient":
        """Build NetSuiteClient using configuration service.

        Uses a pre-generated Bearer token and account_id from the
        connector configuration.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            NetSuiteClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get NetSuite connector configuration"
                )

            connector_config = NetSuiteConnectorConfig.model_validate(
                raw_config
            )

            account_id = connector_config.auth.accountId or ""
            token = (
                connector_config.credentials.access_token
                or connector_config.auth.token
                or ""
            )

            # Try shared OAuth config if credentials are missing
            oauth_config_id = connector_config.auth.oauthConfigId
            if oauth_config_id and not token:
                try:
                    oauth_configs_raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                        "/services/oauth/netsuite", default=[]
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
                            account_id = str(
                                shared.get("accountId")
                                or shared.get("account_id")
                                or account_id
                            )
                            token = str(
                                shared.get("token")
                                or shared.get("access_token")
                                or token
                            )
                            break
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch shared OAuth config: {e}"
                    )

            if not (account_id and token):
                raise ValueError(
                    "account_id and token are required for NetSuite auth"
                )

            token_config = NetSuiteTokenConfig(
                token=token,
                account_id=account_id,
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build NetSuite client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "NetSuiteClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared config

        Returns:
            NetSuiteClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            account_id: str = str(auth_config.get("accountId", ""))
            token: str = str(
                credentials.get("access_token", "")
                or auth_config.get("token", "")
            )

            if not (account_id and token):
                raise ValueError(
                    "account_id and token are required in toolset config "
                    "for NetSuite"
                )

            token_config = NetSuiteTokenConfig(
                token=token,
                account_id=account_id,
            )
            return cls(token_config.create_client())

        except Exception as e:
            logger.error(
                f"Failed to build NetSuite client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for NetSuite."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get NetSuite connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get NetSuite connector config: {e}")
            raise ValueError(
                f"Failed to get NetSuite connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
