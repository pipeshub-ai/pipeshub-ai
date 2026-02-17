"""ClickHouse client implementation.

This module provides clients for interacting with ClickHouse using the
clickhouse-connect SDK with either:
1. Username/Password authentication
2. Access Token authentication

SDK Documentation: https://clickhouse.com/docs/en/integrations/python
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

try:
    import clickhouse_connect
except ImportError:
    clickhouse_connect = None


class AuthType(str, Enum):
    """Authentication type for ClickHouse connector."""

    CREDENTIALS = "CREDENTIALS"
    TOKEN = "TOKEN"


class AuthConfig(BaseModel):
    """Authentication configuration for ClickHouse connector."""

    authType: AuthType = Field(default=AuthType.CREDENTIALS, description="Authentication type (CREDENTIALS or TOKEN)")
    username: Optional[str] = Field(default=None, description="ClickHouse username for credentials auth")
    password: Optional[str] = Field(default=None, description="ClickHouse password for credentials auth")
    token: Optional[str] = Field(default=None, description="Bearer token for token auth")


class ClickHouseConnectorConfig(BaseModel):
    """Configuration model for ClickHouse connector from services."""

    host: str = Field(..., description="ClickHouse server hostname")
    port: int = Field(default=8123, description="HTTP interface port")
    database: str = Field(default="default", description="Default database")
    secure: bool = Field(default=False, description="Use HTTPS")
    auth: AuthConfig = Field(default_factory=AuthConfig, description="Authentication configuration")
    timeout: float = Field(default=30.0, description="Request timeout in seconds", gt=0)


class ClickHouseClientViaCredentials:
    """ClickHouse SDK client via username/password credentials.

    Args:
        host: ClickHouse server hostname
        port: HTTP interface port (default 8123)
        username: ClickHouse username (default "default")
        password: ClickHouse password (default "")
        database: Default database (default "default")
        secure: Use HTTPS (default False)
    """

    def __init__(
        self,
        host: str,
        port: int = 8123,
        username: str = "default",
        password: str = "",
        database: str = "default",
        secure: bool = False,
    ) -> None:
        if clickhouse_connect is None:
            raise ImportError(
                "clickhouse-connect is required for ClickHouse SDK client. "
                "Install with: pip install clickhouse-connect"
            )
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.secure = secure
        self._sdk = None

    def create_client(self) -> object:
        """Create the clickhouse-connect SDK client with credentials auth."""
        self._sdk = clickhouse_connect.get_client(
            host=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            database=self.database,
            secure=self.secure,
        )
        return self._sdk

    def get_sdk(self) -> object:
        """Return the raw clickhouse-connect client.

        Raises:
            RuntimeError: If client not initialized via create_client().
        """
        if self._sdk is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._sdk

    def get_host(self) -> str:
        """Return the ClickHouse server hostname."""
        return self.host


class ClickHouseClientViaToken:
    """ClickHouse SDK client via access token.

    Args:
        host: ClickHouse server hostname
        port: HTTP interface port (default 8443)
        token: Access token
        database: Default database (default "default")
        secure: Use HTTPS (default True)
    """

    def __init__(
        self,
        host: str,
        port: int = 8443,
        token: str = "",
        database: str = "default",
        secure: bool = True,
    ) -> None:
        if clickhouse_connect is None:
            raise ImportError(
                "clickhouse-connect is required for ClickHouse SDK client. "
                "Install with: pip install clickhouse-connect"
            )
        self.host = host
        self.port = port
        self.token = token
        self.database = database
        self.secure = secure
        self._sdk = None

    def create_client(self) -> object:
        """Create the clickhouse-connect SDK client with token auth."""
        self._sdk = clickhouse_connect.get_client(
            host=self.host,
            port=self.port,
            access_token=self.token,
            database=self.database,
            secure=self.secure,
        )
        return self._sdk

    def get_sdk(self) -> object:
        """Return the raw clickhouse-connect client.

        Raises:
            RuntimeError: If client not initialized via create_client().
        """
        if self._sdk is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._sdk

    def get_host(self) -> str:
        """Return the ClickHouse server hostname."""
        return self.host


class ClickHouseClient(IClient):
    """Builder class for ClickHouse SDK clients.

    Provides a unified interface for creating ClickHouse clients
    using either username/password or access token authentication.

    Example usage:
        client = await ClickHouseClient.build_from_services(
            logger, config_service, connector_instance_id
        )
        sdk = client.get_sdk()
    """

    def __init__(self, client) -> None:
        """Initialize with a ClickHouse auth holder client.

        Args:
            client: ClickHouseClientViaCredentials or ClickHouseClientViaToken
        """
        self.client = client

    def get_client(self) -> object:
        """Return the auth holder client (satisfies IClient)."""
        return self.client

    def get_sdk(self) -> object:
        """Return the raw clickhouse-connect SDK client."""
        return self.client.get_sdk()

    def get_host(self) -> str:
        """Return the ClickHouse server hostname."""
        return self.client.get_host()

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "ClickHouseClient":
        """Build ClickHouseClient using configuration service.

        Retrieves ClickHouse connector configuration from the configuration
        service (etcd) and creates the appropriate client.

        Args:
            logger: Logger instance for error reporting
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            ClickHouseClient instance

        Raises:
            ValueError: If configuration is missing or invalid
        """
        try:
            config_dict = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )

            config = ClickHouseConnectorConfig.model_validate(config_dict)

            auth_type = config.auth.authType
            host = config.host
            port = config.port
            database = config.database
            secure = config.secure

            if auth_type == AuthType.CREDENTIALS:
                username = config.auth.username or "default"
                password = config.auth.password or ""
                client = ClickHouseClientViaCredentials(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    database=database,
                    secure=secure,
                )
                client.create_client()

            elif auth_type == AuthType.TOKEN:
                if not config.auth.token:
                    raise ValueError("Bearer token required for TOKEN auth type")
                client = ClickHouseClientViaToken(
                    host=host,
                    port=port,
                    token=config.auth.token,
                    database=database,
                    secure=secure,
                )
                client.create_client()

            else:
                raise ValueError(f"Unsupported auth type: {auth_type}")

            return cls(client=client)

        except ValidationError as e:
            logger.error(f"Invalid ClickHouse connector configuration: {e}")
            raise ValueError("Invalid ClickHouse connector configuration") from e
        except Exception as e:
            logger.error(f"Failed to build ClickHouse client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch connector config from etcd for ClickHouse.

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
                    f"Failed to get ClickHouse connector configuration{instance_msg}"
                )
            if not isinstance(config, dict):
                instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
                raise ValueError(
                    f"Invalid ClickHouse connector configuration format{instance_msg}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get ClickHouse connector config: {e}")
            instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
            raise ValueError(
                f"Failed to get ClickHouse connector configuration{instance_msg}"
            ) from e


class ClickHouseResponse(BaseModel):
    """Standard response wrapper for ClickHouse API calls."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[Dict[str, Any] | List[Any]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(default=None, description="Error code if failed")
    message: Optional[str] = Field(default=None, description="Error message if failed")

    class Config:
        """Pydantic configuration."""
        extra = "allow"

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)

    def to_json(self) -> str:
        """Convert response to JSON string."""
        return self.model_dump_json(exclude_none=True)
