"""Zoho CRM client implementation.

This module provides a client for interacting with the Zoho CRM API using the
official Zoho CRM SDK (zohocrmsdk8-0). The SDK uses a global Initializer pattern,
so this client wraps that initialization and exposes operation classes.

Authentication Reference: https://www.zoho.com/crm/developer/docs/api/v7/auth-request.html
SDK Reference: https://github.com/zoho/zohocrm-python-sdk-7.0

Supports:
1. OAuth with grant_token (initial authorization)
2. OAuth with refresh_token (token renewal)
"""

import logging
from enum import Enum
from typing import Any, cast

from pydantic import BaseModel, Field  # type: ignore
from typing_extensions import override
from zohocrmsdk.src.com.zoho.api.authenticator import (  # type: ignore[reportMissingImports,reportUnknownVariableType]
    OAuthToken,  # type: ignore[reportUnknownVariableType]
)

# ---------------------------------------------------------------------------
# Zoho CRM SDK imports (untyped third-party package)
# ---------------------------------------------------------------------------
from zohocrmsdk.src.com.zoho.crm.api import Initializer  # type: ignore[reportMissingImports,reportUnknownVariableType]
from zohocrmsdk.src.com.zoho.crm.api.dc import (  # type: ignore[reportMissingImports,reportUnknownVariableType]
    AUDataCenter,  # type: ignore[reportUnknownVariableType]
    CADataCenter,  # type: ignore[reportUnknownVariableType]
    CNDataCenter,  # type: ignore[reportUnknownVariableType]
    EUDataCenter,  # type: ignore[reportUnknownVariableType]
    INDataCenter,  # type: ignore[reportUnknownVariableType]
    JPDataCenter,  # type: ignore[reportUnknownVariableType]
    USDataCenter,  # type: ignore[reportUnknownVariableType]
)
from zohocrmsdk.src.com.zoho.crm.api.modules import (  # type: ignore[reportMissingImports,reportUnknownVariableType]
    ModulesOperations,  # type: ignore[reportUnknownVariableType]
)
from zohocrmsdk.src.com.zoho.crm.api.org import (  # type: ignore[reportMissingImports,reportUnknownVariableType]
    OrgOperations,  # type: ignore[reportUnknownVariableType]
)
from zohocrmsdk.src.com.zoho.crm.api.profiles import (  # type: ignore[reportMissingImports,reportUnknownVariableType]
    ProfilesOperations,  # type: ignore[reportUnknownVariableType]
)
from zohocrmsdk.src.com.zoho.crm.api.record import (  # type: ignore[reportMissingImports,reportUnknownVariableType]
    RecordOperations,  # type: ignore[reportUnknownVariableType]
)
from zohocrmsdk.src.com.zoho.crm.api.roles import (  # type: ignore[reportMissingImports,reportUnknownVariableType]
    RolesOperations,  # type: ignore[reportUnknownVariableType]
)
from zohocrmsdk.src.com.zoho.crm.api.users import (  # type: ignore[reportMissingImports,reportUnknownVariableType]
    UsersOperations,  # type: ignore[reportUnknownVariableType]
)

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ZohoAuthType(str, Enum):
    """Authentication types supported by the Zoho CRM connector."""

    GRANT_TOKEN = "GRANT_TOKEN"
    REFRESH_TOKEN = "REFRESH_TOKEN"
    OAUTH = "OAUTH"


class ZohoDomain(str, Enum):
    """Zoho data center domains."""

    US = "US"
    EU = "EU"
    IN = "IN"
    CN = "CN"
    AU = "AU"
    JP = "JP"
    CA = "CA"


# ---------------------------------------------------------------------------
# Domain resolver
# ---------------------------------------------------------------------------

_DATA_CENTER_MAP: dict[str, Any] = {
    "US": USDataCenter,
    "EU": EUDataCenter,
    "IN": INDataCenter,
    "CN": CNDataCenter,
    "AU": AUDataCenter,
    "JP": JPDataCenter,
    "CA": CADataCenter,
}


def _resolve_environment(domain: str) -> object:
    """Resolve a Zoho data center domain string to an SDK environment object.

    Args:
        domain: One of US, EU, IN, CN, AU, JP, CA

    Returns:
        The SDK PRODUCTION environment for the given data center
    """
    dc_class = _DATA_CENTER_MAP.get(domain.upper())
    if dc_class is None:
        raise ValueError(
            f"Unsupported Zoho domain: {domain}. "
            f"Supported: {', '.join(_DATA_CENTER_MAP.keys())}"
        )
    return dc_class.PRODUCTION()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ZohoResponse(BaseModel):
    """Standardized Zoho CRM API response wrapper."""

    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, object] | list[object] | None = Field(
        default=None, description="Response data"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(
        default=None, description="Additional message information"
    )

    class Config:
        """Pydantic configuration."""

        extra = "allow"

    def to_dict(self) -> dict[str, object]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# SDK client wrapper
# ---------------------------------------------------------------------------


class ZohoClientViaOAuth:
    """Zoho CRM client via OAuth.

    Wraps the Zoho CRM SDK global Initializer and provides access to
    SDK operation classes (RecordOperations, UsersOperations, etc.).

    The Zoho SDK uses a global Initializer pattern, so this class manages
    initialization state and prevents re-initialization.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        domain: Zoho data center domain (US, EU, IN, CN, AU, JP, CA)
        grant_token: OAuth grant token (for initial authorization)
        refresh_token: OAuth refresh token (for token renewal)
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        domain: str = "US",
        grant_token: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        if not client_id or not client_secret:
            raise ValueError("client_id and client_secret are required")
        if not grant_token and not refresh_token:
            raise ValueError(
                "Either grant_token or refresh_token must be provided"
            )

        self.client_id = client_id
        self.client_secret = client_secret
        self.domain = domain
        self.grant_token = grant_token
        self.refresh_token = refresh_token
        self._initialized = False

    def initialize(self) -> None:
        """Initialize the Zoho CRM SDK global state.

        This sets up the SDK Initializer with the OAuth token and
        data center environment. Must be called before using any
        operation classes.
        """
        if self._initialized:
            return

        environment = _resolve_environment(self.domain)

        token_kwargs: dict[str, str] = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.grant_token:
            token_kwargs["grant_token"] = self.grant_token
        if self.refresh_token:
            token_kwargs["refresh_token"] = self.refresh_token

        token = OAuthToken(**token_kwargs)  # type: ignore[no-untyped-call]

        Initializer.initialize(  # type: ignore[no-untyped-call]
            environment=environment,
            token=token,
        )
        self._initialized = True

    def ensure_initialized(self) -> None:
        """Ensure the SDK is initialized. Calls initialize() if needed."""
        if not self._initialized:
            self.initialize()

    def get_record_operations(self, module_api_name: str) -> RecordOperations:  # type: ignore[no-any-return]
        """Get RecordOperations instance for a given module.

        Args:
            module_api_name: The API name of the module (e.g., 'Leads', 'Contacts')

        Returns:
            RecordOperations instance
        """
        self.ensure_initialized()
        return RecordOperations(module_api_name)  # type: ignore[no-untyped-call]

    def get_users_operations(self) -> UsersOperations:  # type: ignore[no-any-return]
        """Get UsersOperations instance.

        Returns:
            UsersOperations instance
        """
        self.ensure_initialized()
        return UsersOperations()  # type: ignore[no-untyped-call]

    def get_modules_operations(self) -> ModulesOperations:  # type: ignore[no-any-return]
        """Get ModulesOperations instance.

        Returns:
            ModulesOperations instance
        """
        self.ensure_initialized()
        return ModulesOperations()  # type: ignore[no-untyped-call]

    def get_roles_operations(self) -> RolesOperations:  # type: ignore[no-any-return]
        """Get RolesOperations instance.

        Returns:
            RolesOperations instance
        """
        self.ensure_initialized()
        return RolesOperations()  # type: ignore[no-untyped-call]

    def get_profiles_operations(self) -> ProfilesOperations:  # type: ignore[no-any-return]
        """Get ProfilesOperations instance.

        Returns:
            ProfilesOperations instance
        """
        self.ensure_initialized()
        return ProfilesOperations()  # type: ignore[no-untyped-call]

    def get_org_operations(self) -> OrgOperations:  # type: ignore[no-any-return]
        """Get OrgOperations instance.

        Returns:
            OrgOperations instance
        """
        self.ensure_initialized()
        return OrgOperations()  # type: ignore[no-untyped-call]

    def get_domain(self) -> str:
        """Get the configured Zoho domain."""
        return self.domain


# ---------------------------------------------------------------------------
# Configuration models (Pydantic)
# ---------------------------------------------------------------------------


class ZohoGrantTokenConfig(BaseModel):
    """Configuration for Zoho CRM client via grant token.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        grant_token: OAuth grant token
        domain: Zoho data center domain
    """

    client_id: str = Field(..., description="OAuth client ID")
    client_secret: str = Field(..., description="OAuth client secret")
    grant_token: str = Field(..., description="OAuth grant token")
    domain: str = Field(default="US", description="Zoho data center domain")

    def create_client(self) -> ZohoClientViaOAuth:
        """Create a Zoho CRM client."""
        return ZohoClientViaOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            domain=self.domain,
            grant_token=self.grant_token,
        )


class ZohoRefreshTokenConfig(BaseModel):
    """Configuration for Zoho CRM client via refresh token.

    Args:
        client_id: OAuth client ID
        client_secret: OAuth client secret
        refresh_token: OAuth refresh token
        domain: Zoho data center domain
    """

    client_id: str = Field(..., description="OAuth client ID")
    client_secret: str = Field(..., description="OAuth client secret")
    refresh_token: str = Field(..., description="OAuth refresh token")
    domain: str = Field(default="US", description="Zoho data center domain")

    def create_client(self) -> ZohoClientViaOAuth:
        """Create a Zoho CRM client."""
        return ZohoClientViaOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            domain=self.domain,
            refresh_token=self.refresh_token,
        )


# ---------------------------------------------------------------------------
# Connector configuration models for build_from_services
# ---------------------------------------------------------------------------


class ZohoAuthConfig(BaseModel):
    """Auth section of the Zoho CRM connector configuration from etcd."""

    authType: ZohoAuthType = ZohoAuthType.OAUTH
    clientId: str | None = None
    clientSecret: str | None = None
    grantToken: str | None = None
    domain: str | None = Field(default="US")
    oauthConfigId: str | None = None

    class Config:
        extra = "allow"


class ZohoCredentialsConfig(BaseModel):
    """Credentials section of the Zoho CRM connector configuration."""

    access_token: str | None = None
    refresh_token: str | None = None

    class Config:
        extra = "allow"


class ZohoConnectorConfig(BaseModel):
    """Top-level Zoho CRM connector configuration from etcd."""

    auth: ZohoAuthConfig = Field(default_factory=ZohoAuthConfig)
    credentials: ZohoCredentialsConfig = Field(
        default_factory=ZohoCredentialsConfig
    )

    class Config:
        extra = "allow"


class ZohoSharedOAuthConfigEntry(BaseModel):
    """A single entry from the shared OAuth config list in etcd.

    Handles both camelCase and snake_case key variants from the config store.
    """

    entry_id: str | None = Field(default=None, alias="_id")
    clientId: str | None = None
    client_id: str | None = None
    clientSecret: str | None = None
    client_secret: str | None = None

    class Config:
        extra = "allow"
        populate_by_name = True

    def resolved_client_id(self, fallback: str = "") -> str:
        return self.clientId or self.client_id or fallback

    def resolved_client_secret(self, fallback: str = "") -> str:
        return self.clientSecret or self.client_secret or fallback


class ZohoSharedOAuthWrapper(BaseModel):
    """Wrapper for a shared OAuth config entry with nested config."""

    entry_id: str | None = Field(default=None, alias="_id")
    config: ZohoSharedOAuthConfigEntry = Field(
        default_factory=ZohoSharedOAuthConfigEntry
    )

    class Config:
        extra = "allow"
        populate_by_name = True


# ---------------------------------------------------------------------------
# Client builder
# ---------------------------------------------------------------------------


class ZohoClient(IClient):
    """Builder class for Zoho CRM clients.

    Wraps ZohoClientViaOAuth and manages SDK initialization.
    """

    def __init__(self, client: ZohoClientViaOAuth) -> None:
        """Initialize with a Zoho CRM client object."""
        super().__init__()
        self.client = client

    @override
    def get_client(self) -> ZohoClientViaOAuth:
        """Return the Zoho CRM client object."""
        return self.client

    def get_domain(self) -> str:
        """Return the configured Zoho domain."""
        return self.client.get_domain()

    @classmethod
    def build_with_config(
        cls,
        config: ZohoGrantTokenConfig | ZohoRefreshTokenConfig,
    ) -> "ZohoClient":
        """Build ZohoClient with configuration.

        Args:
            config: ZohoGrantTokenConfig or ZohoRefreshTokenConfig instance

        Returns:
            ZohoClient instance
        """
        client = config.create_client()
        client.initialize()
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "ZohoClient":
        """Build ZohoClient using configuration service.

        Supports OAuth authentication with grant_token or refresh_token.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            ZohoClient instance
        """
        try:
            raw_config = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            if not raw_config:
                raise ValueError(
                    "Failed to get Zoho CRM connector configuration"
                )

            connector_config = ZohoConnectorConfig.model_validate(raw_config)

            client_id = connector_config.auth.clientId or ""
            client_secret = connector_config.auth.clientSecret or ""
            domain = connector_config.auth.domain or "US"

            # Try shared OAuth config if credentials are missing
            oauth_config_id = connector_config.auth.oauthConfigId
            if oauth_config_id and not (client_id and client_secret):
                shared_cfg = await cls._find_shared_oauth_config(
                    config_service, oauth_config_id, logger
                )
                if shared_cfg:
                    client_id = shared_cfg.resolved_client_id(client_id)
                    client_secret = shared_cfg.resolved_client_secret(
                        client_secret
                    )

            if not (client_id and client_secret):
                raise ValueError(
                    "client_id and client_secret are required "
                    "for Zoho CRM authentication"
                )

            # Prefer refresh_token from credentials, fall back to grant_token
            refresh_token = connector_config.credentials.refresh_token or ""
            grant_token = connector_config.auth.grantToken or ""

            if refresh_token:
                zoho_client = ZohoClientViaOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    domain=domain,
                    refresh_token=refresh_token,
                )
            elif grant_token:
                zoho_client = ZohoClientViaOAuth(
                    client_id=client_id,
                    client_secret=client_secret,
                    domain=domain,
                    grant_token=grant_token,
                )
            else:
                raise ValueError(
                    "Either refresh_token or grant_token is required "
                    "for Zoho CRM authentication"
                )

            zoho_client.initialize()
            return cls(zoho_client)

        except Exception as e:
            logger.error(
                f"Failed to build Zoho CRM client from services: {str(e)}"
            )
            raise

    @staticmethod
    async def _find_shared_oauth_config(
        config_service: ConfigurationService,
        oauth_config_id: str,
        logger: logging.Logger,
    ) -> ZohoSharedOAuthConfigEntry | None:
        """Look up shared OAuth config by ID from the config store.

        Args:
            config_service: Configuration service instance
            oauth_config_id: The shared OAuth config ID to match
            logger: Logger instance

        Returns:
            Matched ZohoSharedOAuthConfigEntry or None
        """
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                "/services/oauth/zoho", default=[]
            )
            entries: list[object] = list(raw) if isinstance(raw, list) else []  # type: ignore[arg-type]
            for entry in entries:
                wrapper = ZohoSharedOAuthWrapper.model_validate(entry)
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
        """Fetch connector config from etcd for Zoho CRM."""
        try:
            raw = await config_service.get_config(  # type: ignore[reportUnknownMemberType]
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not raw:
                raise ValueError(
                    f"Failed to get Zoho CRM connector configuration "
                    f"for instance {connector_instance_id}"
                )
            return cast(dict[str, Any], raw)
        except Exception as e:
            logger.error(f"Failed to get Zoho CRM connector config: {e}")
            raise ValueError(
                f"Failed to get Zoho CRM connector configuration "
                f"for instance {connector_instance_id}"
            ) from e
