"""Snowflake client implementation.

This module provides clients for interacting with Snowflake's SQL REST API using either:
1. OAuth token authentication
2. Personal Access Token (PAT) authentication

Snowflake SQL API Reference: https://docs.snowflake.com/en/developer-guide/sql-api/index
Authentication: https://docs.snowflake.com/en/developer-guide/sql-api/authenticating
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, Field, ValidationError  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class AuthType(str, Enum):
    """Authentication type for Snowflake connector."""

    OAUTH = "OAUTH"
    PAT = "PAT"


class SnowflakeRESTClientViaOAuth(HTTPClient):
    """Snowflake REST client via OAuth token.

    Uses Snowflake's OAuth authentication where an access token is obtained
    through the OAuth flow (e.g., Snowflake OAuth, External OAuth).

    OAuth Documentation: https://docs.snowflake.com/en/user-guide/oauth-intro

    Args:
        account_identifier: Snowflake account identifier
        oauth_token: OAuth access token
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        account_identifier: str,
        oauth_token: str,
        timeout: float = 30.0,
    ) -> None:
        """Initialize Snowflake REST client with OAuth token.

        Args:
            account_identifier: Snowflake account identifier
            oauth_token: OAuth access token obtained through Snowflake OAuth flow
            timeout: Request timeout in seconds
        """
        super().__init__(token=oauth_token, token_type="Bearer", timeout=timeout)
        self.account_identifier = account_identifier
        self.oauth_token = oauth_token
        self._base_url = self._build_base_url(account_identifier)

    @staticmethod
    def _build_base_url(account_identifier: str) -> str:
        """Build the Snowflake SQL API base URL from account identifier.

        Args:
            account_identifier: Snowflake account identifier (can be a full URL,
                partial URL, or just the account name)

        Returns:
            Base URL for Snowflake SQL API
        """
        # Parse the account identifier as a URL
        parsed = urlparse(account_identifier if "://" in account_identifier else f"https://{account_identifier}")
        # Extract the account name from netloc
        netloc = parsed.netloc or parsed.path.split("/")[0] if parsed.path else account_identifier
        # Remove the .snowflakecomputing.com suffix if present
        if netloc.endswith(".snowflakecomputing.com"):
            account = netloc[:-len(".snowflakecomputing.com")]
        else:
            # If no suffix, assume netloc is the account name
            account = netloc

        # Build the base URL using urlunparse for proper URL construction
        base_url = urlunparse((
            "https",
            f"{account}.snowflakecomputing.com",
            "/api/v2",
            "",
            "",
            ""
        ))
        return base_url

    def get_base_url(self) -> str:
        """Get the base URL for Snowflake API."""
        return self._base_url

    def get_account_identifier(self) -> str:
        """Get the Snowflake account identifier."""
        return self.account_identifier


class SnowflakeRESTClientViaPAT(HTTPClient):
    """Snowflake REST client via Personal Access Token (PAT).

    Uses Snowflake's PAT authentication where a programmatic access token
    is generated for API access.

    PAT Documentation: https://docs.snowflake.com/en/user-guide/programmatic-access-tokens

    Args:
        account_identifier: Snowflake account identifier
        pat_token: Personal Access Token
        timeout: Request timeout in seconds
    """

    def __init__(
        self,
        account_identifier: str,
        pat_token: str,
        timeout: float = 30.0,
    ) -> None:
        """Initialize Snowflake REST client with Personal Access Token.

        Args:
            account_identifier: Snowflake account identifier
            pat_token: Personal Access Token for authentication
            timeout: Request timeout in seconds
        """
        # Snowflake PAT uses "Snowflake Token" as the auth scheme
        super().__init__(token=pat_token, token_type="Snowflake Token", timeout=timeout)
        self.account_identifier = account_identifier
        self.pat_token = pat_token
        self._base_url = self._build_base_url(account_identifier)

    @staticmethod
    def _build_base_url(account_identifier: str) -> str:
        """Build the Snowflake SQL API base URL from account identifier.

        Args:
            account_identifier: Snowflake account identifier (can be a full URL,
                partial URL, or just the account name)

        Returns:
            Base URL for Snowflake SQL API
        """
        # Parse the account identifier as a URL
        parsed = urlparse(account_identifier if "://" in account_identifier else f"https://{account_identifier}")

        # Extract the account name from netloc
        netloc = parsed.netloc or parsed.path.split("/")[0] if parsed.path else account_identifier

        # Remove the .snowflakecomputing.com suffix if present
        if netloc.endswith(".snowflakecomputing.com"):
            account = netloc[:-len(".snowflakecomputing.com")]
        else:
            # If no suffix, assume netloc is the account name
            account = netloc

        # Build the base URL using urlunparse for proper URL construction
        base_url = urlunparse((
            "https",
            f"{account}.snowflakecomputing.com",
            "/api/v2",
            "",
            "",
            ""
        ))
        return base_url

    def get_base_url(self) -> str:
        """Get the base URL for Snowflake API."""
        return self._base_url

    def get_account_identifier(self) -> str:
        """Get the Snowflake account identifier."""
        return self.account_identifier


class SnowflakeOAuthConfig(BaseModel):
    """Configuration for Snowflake client via OAuth token.

    OAuth flow endpoints depend on the OAuth provider (Snowflake OAuth or External OAuth).

    Args:
        account_identifier: Snowflake account identifier
        oauth_token: OAuth access token
        timeout: Request timeout in seconds
    """

    account_identifier: str = Field(
        ...,
        description="Snowflake account identifier"
    )
    oauth_token: str = Field(..., description="OAuth access token")
    timeout: float = Field(default=30.0, description="Request timeout in seconds", gt=0)

    def create_client(self) -> SnowflakeRESTClientViaOAuth:
        """Create a Snowflake REST client with OAuth authentication."""
        return SnowflakeRESTClientViaOAuth(
            account_identifier=self.account_identifier,
            oauth_token=self.oauth_token,
            timeout=self.timeout,
        )


class SnowflakePATConfig(BaseModel):
    """Configuration for Snowflake client via Personal Access Token (PAT).

    Args:
        account_identifier: Snowflake account identifier
        pat_token: Personal Access Token
        timeout: Request timeout in seconds
    """

    account_identifier: str = Field(
        ...,
        description="Snowflake account identifier"
    )
    pat_token: str = Field(..., description="Personal Access Token")
    timeout: float = Field(default=30.0, description="Request timeout in seconds", gt=0)

    def create_client(self) -> SnowflakeRESTClientViaPAT:
        """Create a Snowflake REST client with PAT authentication."""
        return SnowflakeRESTClientViaPAT(
            account_identifier=self.account_identifier,
            pat_token=self.pat_token,
            timeout=self.timeout,
        )


class AuthConfig(BaseModel):
    """Authentication configuration for Snowflake connector."""

    authType: AuthType = Field(default=AuthType.PAT, description="Authentication type (OAUTH or PAT)")
    patToken: Optional[str] = Field(default=None, description="Personal Access Token for PAT auth")


class CredentialsConfig(BaseModel):
    """Credentials configuration for Snowflake connector."""

    access_token: Optional[str] = Field(default=None, description="OAuth access token")


class SnowflakeConnectorConfig(BaseModel):
    """Configuration model for Snowflake connector from services."""

    accountIdentifier: str = Field(..., description="Snowflake account identifier")
    auth: AuthConfig = Field(default_factory=AuthConfig, description="Authentication configuration")
    credentials: Optional[CredentialsConfig] = Field(
        default=None, description="Credentials configuration"
    )
    timeout: float = Field(default=30.0, description="Request timeout in seconds", gt=0)


class SnowflakeClient(IClient):
    """Builder class for Snowflake clients with different construction methods.

    This class provides a unified interface for creating Snowflake clients
    using either OAuth or Personal Access Token (PAT) authentication.

    Example usage with OAuth:
        config = SnowflakeOAuthConfig(
            account_identifier="your_account_identifier",
            oauth_token="your_oauth_token"
        )
        client = SnowflakeClient.build_with_config(config)
        rest_client = client.get_client()

    Example usage with PAT:
        config = SnowflakePATConfig(
            account_identifier="your_account_identifier",
            pat_token="your_pat_token"
        )
        client = SnowflakeClient.build_with_config(config)
        rest_client = client.get_client()
    """

    def __init__(
        self,
        client: Union[SnowflakeRESTClientViaOAuth, SnowflakeRESTClientViaPAT],
    ) -> None:
        """Initialize with a Snowflake REST client.

        Args:
            client: Snowflake REST client (OAuth or PAT)
        """
        self._client = client

    def get_client(self) -> Union[SnowflakeRESTClientViaOAuth, SnowflakeRESTClientViaPAT]:
        """Return the Snowflake REST client object."""
        return self._client

    def get_base_url(self) -> str:
        """Return the Snowflake API base URL."""
        return self._client.get_base_url()

    def get_account_identifier(self) -> str:
        """Return the Snowflake account identifier."""
        return self._client.get_account_identifier()

    @classmethod
    def build_with_config(
        cls,
        config: Union[SnowflakeOAuthConfig, SnowflakePATConfig],
    ) -> "SnowflakeClient":
        """Build SnowflakeClient with configuration.

        Args:
            config: Snowflake configuration instance (OAuth or PAT)

        Returns:
            SnowflakeClient instance
        """
        return cls(client=config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "SnowflakeClient":
        """Build SnowflakeClient using configuration service.

        This method retrieves Snowflake connector configuration from
        the configuration service (etcd) and creates the appropriate client.

        Args:
            logger: Logger instance for error reporting
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            SnowflakeClient instance

        Raises:
            ValueError: If configuration is missing or invalid
        """
        try:
            config_dict = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )

            config = SnowflakeConnectorConfig.model_validate(config_dict)

            auth_type = config.auth.authType
            account_identifier = config.accountIdentifier
            timeout = config.timeout

            if auth_type == AuthType.OAUTH:
                if not config.credentials or not config.credentials.access_token:
                    raise ValueError("OAuth access token required for OAuth auth type")
                client = SnowflakeRESTClientViaOAuth(
                    account_identifier=account_identifier,
                    oauth_token=config.credentials.access_token,
                    timeout=timeout,
                )

            elif auth_type == AuthType.PAT:
                if not config.auth.patToken:
                    raise ValueError("PAT token required for PAT auth type")
                client = SnowflakeRESTClientViaPAT(
                    account_identifier=account_identifier,
                    pat_token=config.auth.patToken,
                    timeout=timeout,
                )

            else:
                raise ValueError(f"Unsupported auth type: {auth_type}")

            return cls(client=client)

        except ValidationError as e:
            logger.error(f"Invalid Snowflake connector configuration: {e}")
            raise ValueError("Invalid Snowflake connector configuration") from e
        except Exception as e:
            logger.error(f"Failed to build Snowflake client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for Snowflake.

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            connector_instance_id: Connector instance ID

        Returns:
            Configuration dictionary

        Raises:
            ValueError: If configuration cannot be retrieved
        """
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
                raise ValueError(
                    f"Failed to get Snowflake connector configuration{instance_msg}"
                )
            if not isinstance(config, dict):
                instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
                raise ValueError(
                    f"Invalid Snowflake connector configuration format{instance_msg}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get Snowflake connector config: {e}")
            instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
            raise ValueError(
                f"Failed to get Snowflake connector configuration{instance_msg}"
            ) from e


class SnowflakeResponse(BaseModel):
    """Standard response wrapper for Snowflake API calls."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(default=None, description="Error code if failed")
    message: Optional[str] = Field(default=None, description="Error message if failed")
    statement_handle: Optional[str] = Field(
        default=None, description="Statement handle for async queries"
    )

    class Config:
        """Pydantic configuration."""
        extra = "allow"

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)

    def to_json(self) -> str:
        """Convert response to JSON string."""
        return self.model_dump_json(exclude_none=True)
