# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""DocuSign client implementation.

Uses the official docusign-esign SDK for eSignature API, and HTTP client
for Admin, Rooms, Click, Monitor, and WebForms REST APIs.

Authentication modes:
1. OAuth 2.0 authorization code flow (access_token + account_id)
2. Pre-generated Bearer token (token + account_id)

SDK Reference: https://pypi.org/project/docusign-esign/
API Reference: https://developers.docusign.com/docs/esign-rest-api/reference/
"""

import logging
from enum import Enum
from typing import Any, cast

import docusign_esign  # type: ignore[reportMissingImports]
from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DocuSignAuthType(str, Enum):
    """Authentication types supported by the DocuSign connector."""

    OAUTH = "OAUTH"
    TOKEN = "TOKEN"


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class DocuSignResponse(BaseModel):
    """Standardized DocuSign API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | bytes | None = Field(
        default=None, description="Response data from the SDK or HTTP"
    )
    error: str | None = Field(
        default=None, description="Error message if failed"
    )
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# SDK + HTTP client classes
# ---------------------------------------------------------------------------


class DocuSignClientViaOAuth:
    """DocuSign client via OAuth 2.0 authorization code flow.

    Creates a ``docusign_esign.ApiClient`` for eSignature operations and
    lazy ``HTTPClient`` instances for Admin, Rooms, Click, Monitor, and
    WebForms REST APIs.

    Args:
        access_token: The OAuth access token
        account_id: DocuSign account ID (used in API calls)
        base_path: eSign API base path (default: demo environment)
    """

    def __init__(
        self,
        access_token: str,
        account_id: str,
        base_path: str = "https://demo.docusign.net/restapi",
    ) -> None:
        self.access_token = access_token
        self.account_id = account_id
        self.base_path = base_path

        self._sdk: docusign_esign.ApiClient | None = None
        self._http_clients: dict[str, HTTPClient] = {}

    def create_client(self) -> docusign_esign.ApiClient:
        """Create and configure the SDK ApiClient."""
        self._sdk = docusign_esign.ApiClient(base_path=self.base_path)
        self._sdk.set_default_header(  # type: ignore[reportUnknownMemberType]
            "Authorization", f"Bearer {self.access_token}"
        )
        return self._sdk

    def get_sdk(self) -> docusign_esign.ApiClient:
        """Return the SDK ApiClient, lazily creating it if needed."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_http_client(self, base_url: str) -> HTTPClient:
        """Return an HTTPClient configured for the given base URL.

        Clients are cached per base_url so repeated calls return the
        same instance.

        Args:
            base_url: The base URL for the target REST API.

        Returns:
            HTTPClient ready to execute requests.
        """
        if base_url not in self._http_clients:
            client = HTTPClient(self.access_token, token_type="Bearer")
            client.base_url = base_url  # type: ignore[attr-defined]
            client.headers["Content-Type"] = "application/json"
            self._http_clients[base_url] = client
        return self._http_clients[base_url]

    def get_access_token(self) -> str:
        """Return the access token."""
        return self.access_token

    def get_account_id(self) -> str:
        """Return the account ID."""
        return self.account_id


class DocuSignClientViaToken:
    """DocuSign client via pre-generated Bearer token.

    Functionally identical to the OAuth variant but semantically distinct
    for configuration clarity.

    Args:
        token: The pre-generated Bearer token
        account_id: DocuSign account ID (used in API calls)
        base_path: eSign API base path (default: demo environment)
    """

    def __init__(
        self,
        token: str,
        account_id: str,
        base_path: str = "https://demo.docusign.net/restapi",
    ) -> None:
        self.token = token
        self.account_id = account_id
        self.base_path = base_path

        self._sdk: docusign_esign.ApiClient | None = None
        self._http_clients: dict[str, HTTPClient] = {}

    def create_client(self) -> docusign_esign.ApiClient:
        """Create and configure the SDK ApiClient."""
        self._sdk = docusign_esign.ApiClient(base_path=self.base_path)
        self._sdk.set_default_header(  # type: ignore[reportUnknownMemberType]
            "Authorization", f"Bearer {self.token}"
        )
        return self._sdk

    def get_sdk(self) -> docusign_esign.ApiClient:
        """Return the SDK ApiClient, lazily creating it if needed."""
        if self._sdk is None:
            return self.create_client()
        return self._sdk

    def get_http_client(self, base_url: str) -> HTTPClient:
        """Return an HTTPClient configured for the given base URL.

        Clients are cached per base_url so repeated calls return the
        same instance.

        Args:
            base_url: The base URL for the target REST API.

        Returns:
            HTTPClient ready to execute requests.
        """
        if base_url not in self._http_clients:
            client = HTTPClient(self.token, token_type="Bearer")
            client.base_url = base_url  # type: ignore[attr-defined]
            client.headers["Content-Type"] = "application/json"
            self._http_clients[base_url] = client
        return self._http_clients[base_url]

    def get_access_token(self) -> str:
        """Return the token."""
        return self.token

    def get_account_id(self) -> str:
        """Return the account ID."""
        return self.account_id


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class DocuSignOAuthConfig(BaseModel):
    """Configuration for DocuSign client via OAuth 2.0.

    Args:
        access_token: The OAuth access token
        account_id: DocuSign account ID
        base_path: API base path (default: demo environment)
    """

    access_token: str
    account_id: str
    base_path: str = "https://demo.docusign.net/restapi"

    def create_client(self) -> DocuSignClientViaOAuth:
        return DocuSignClientViaOAuth(
            self.access_token,
            self.account_id,
            self.base_path,
        )


class DocuSignTokenConfig(BaseModel):
    """Configuration for DocuSign client via pre-generated Bearer token.

    Args:
        token: The pre-generated Bearer token
        account_id: DocuSign account ID
        base_path: API base path (default: demo environment)
    """

    token: str
    account_id: str
    base_path: str = "https://demo.docusign.net/restapi"

    def create_client(self) -> DocuSignClientViaToken:
        return DocuSignClientViaToken(
            self.token, self.account_id, self.base_path
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class DocuSignAuthConfig(BaseModel):
    """Auth section of the DocuSign connector configuration from etcd."""

    authType: DocuSignAuthType = DocuSignAuthType.OAUTH
    clientId: str | None = None
    clientSecret: str | None = None
    redirectUri: str | None = None
    token: str | None = None
    accountId: str | None = None
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class DocuSignCredentialsConfig(BaseModel):
    """Credentials section of the DocuSign connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class DocuSignConnectorConfig(BaseModel):
    """Top-level DocuSign connector configuration from etcd."""

    auth: DocuSignAuthConfig = Field(default_factory=DocuSignAuthConfig)
    credentials: DocuSignCredentialsConfig = Field(
        default_factory=DocuSignCredentialsConfig
    )
    accountId: str | None = None
    baseUrl: str | None = None

    class Config:
        extra = "allow"


class DocuSignSharedOAuthConfigEntry(BaseModel):
    """A single entry from the shared OAuth config list in etcd.

    Handles both camelCase and snake_case key variants from the config store.
    """

    entry_id: str | None = Field(default=None, alias="_id")
    clientId: str | None = None
    client_id: str | None = None
    clientSecret: str | None = None
    client_secret: str | None = None
    redirectUri: str | None = None
    redirect_uri: str | None = None

    class Config:
        extra = "allow"
        populate_by_name = True

    def resolved_client_id(self, fallback: str = "") -> str:
        return self.clientId or self.client_id or fallback

    def resolved_client_secret(self, fallback: str = "") -> str:
        return self.clientSecret or self.client_secret or fallback

    def resolved_redirect_uri(self, fallback: str = "") -> str:
        return self.redirectUri or self.redirect_uri or fallback


class DocuSignSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: DocuSignSharedOAuthConfigEntry = Field(
        default_factory=DocuSignSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class DocuSignClient(IClient):
    """Builder class for DocuSign clients with different authentication methods.

    Supports:
    - OAuth 2.0 authorization code flow
    - Pre-generated Bearer token
    """

    def __init__(
        self,
        client: DocuSignClientViaOAuth | DocuSignClientViaToken,
    ) -> None:
        """Initialize with a DocuSign client wrapper."""
        super().__init__()
        self.client = client

    @override
    def get_client(
        self,
    ) -> DocuSignClientViaOAuth | DocuSignClientViaToken:
        """Return the DocuSign client wrapper."""
        return self.client

    def get_sdk(self) -> docusign_esign.ApiClient:
        """Return the underlying SDK ApiClient."""
        return self.client.get_sdk()

    def get_account_id(self) -> str:
        """Return the account ID."""
        return self.client.get_account_id()

    @classmethod
    def build_with_config(
        cls,
        config: DocuSignOAuthConfig | DocuSignTokenConfig,
    ) -> "DocuSignClient":
        """Build DocuSignClient with configuration.

        Args:
            config: DocuSignOAuthConfig or DocuSignTokenConfig instance

        Returns:
            DocuSignClient instance
        """
        client = config.create_client()
        client.get_sdk()  # eagerly initialise the SDK
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "DocuSignClient":
        """Build DocuSignClient using configuration service.

        Supports two authentication strategies:
        1. OAUTH: OAuth 2.0 authorization code flow with access token
        2. TOKEN: Pre-generated Bearer token

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            DocuSignClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get DocuSign connector configuration"
                )

            connector_config = DocuSignConnectorConfig.model_validate(
                raw_config
            )

            # Resolve account ID from auth config or top-level
            account_id = (
                connector_config.auth.accountId
                or connector_config.accountId
                or ""
            )
            base_path = (
                connector_config.baseUrl
                or "https://demo.docusign.net/restapi"
            )

            if not account_id:
                raise ValueError(
                    "account_id is required for DocuSign connector"
                )

            if connector_config.auth.authType == DocuSignAuthType.OAUTH:
                access_token = connector_config.credentials.access_token or ""

                # Try shared OAuth config if credentials are missing
                oauth_config_id = connector_config.auth.oauthConfigId
                if oauth_config_id:
                    shared_cfg = await cls._find_shared_oauth_config(
                        config_service, oauth_config_id, logger
                    )
                    if shared_cfg:
                        logger.debug(
                            "Resolved shared OAuth config for DocuSign"
                        )

                if not access_token:
                    raise ValueError(
                        "Access token required for OAuth auth type"
                    )

                oauth_cfg = DocuSignOAuthConfig(
                    access_token=access_token,
                    account_id=account_id,
                    base_path=base_path,
                )
                return cls.build_with_config(oauth_cfg)

            elif connector_config.auth.authType == DocuSignAuthType.TOKEN:
                token = connector_config.auth.token or ""
                if not token:
                    raise ValueError(
                        "Token required for TOKEN auth type"
                    )

                token_config = DocuSignTokenConfig(
                    token=token,
                    account_id=account_id,
                    base_path=base_path,
                )
                return cls.build_with_config(token_config)

            else:
                raise ValueError(
                    f"Invalid auth type: {connector_config.auth.authType}"
                )

        except Exception as e:
            logger.error(
                f"Failed to build DocuSign client from services: {str(e)}"
            )
            raise

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService | None = None,
    ) -> "DocuSignClient":
        """Build client from per-user toolset configuration.

        Args:
            toolset_config: Per-user toolset configuration dict
            logger: Logger instance
            config_service: Optional configuration service for shared OAuth config

        Returns:
            DocuSignClient instance
        """
        try:
            credentials: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("credentials", {}) or {}
            )
            auth_config: dict[str, Any] = cast(
                dict[str, Any], toolset_config.get("auth", {}) or {}
            )

            access_token: str = str(credentials.get("access_token", ""))
            if not access_token:
                raise ValueError("Access token not found in toolset config")

            account_id: str = str(
                toolset_config.get("accountId", "")
                or auth_config.get("accountId", "")
            )
            if not account_id:
                raise ValueError("Account ID not found in toolset config")

            base_path: str = str(
                toolset_config.get(
                    "baseUrl", "https://demo.docusign.net/restapi"
                )
            )

            # Try shared OAuth config
            oauth_config_id: str | None = cast(
                str | None, auth_config.get("oauthConfigId")
            )
            if oauth_config_id and config_service:
                shared_cfg = await cls._find_shared_oauth_config(
                    config_service, oauth_config_id, logger
                )
                if shared_cfg:
                    logger.debug(
                        "Resolved shared OAuth config for DocuSign toolset"
                    )

            oauth_cfg = DocuSignOAuthConfig(
                access_token=access_token,
                account_id=account_id,
                base_path=base_path,
            )
            return cls.build_with_config(oauth_cfg)

        except Exception as e:
            logger.error(
                f"Failed to build DocuSign client from toolset: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> DocuSignSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched DocuSignSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/docusign", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[reportUnknownArgumentType]
            for entry in entries:
                wrapper = DocuSignSharedOAuthWrapper.model_validate(entry)
                if wrapper.entry_id == oauth_config_id:
                    return wrapper.config
        except Exception as e:
            logger.warning(f"Failed to fetch shared OAuth config: {e}")
        return None

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for DocuSign."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get DocuSign connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get DocuSign connector config: {e}")
            raise ValueError(
                f"Failed to get DocuSign connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
