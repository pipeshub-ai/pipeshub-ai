"""Redshift client implementation.

This module provides clients for interacting with Amazon Redshift databases using:
1. Username/Password authentication
2. Connection string authentication

Redshift is based on PostgreSQL 8.0.2 but has several key differences:
- Uses port 5439 by default (not 5432)
- Does NOT support: pg_database_size, pg_size_pretty, format_type(), pg_get_expr()
- Uses redshift_connector (AWS-recommended) instead of psycopg2
- SSL is enabled by default and strongly recommended
- Supports IAM authentication (extendable)

Amazon Redshift Documentation: https://docs.aws.amazon.com/redshift/
redshift_connector Documentation: https://github.com/aws/amazon-redshift-python-driver
"""

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from app.api.routes.toolsets import get_toolset_by_id
from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

try:
    import redshift_connector
except ImportError:
    redshift_connector = None


class RedshiftClient:
    """Redshift client for database connections.

    Uses redshift_connector (AWS-recommended) for connecting to Amazon Redshift.

    Note on Redshift vs PostgreSQL differences:
    - Default port is 5439
    - pg_database_size() and pg_size_pretty() are NOT available
    - format_type() and pg_get_expr() are NOT available
    - information_schema is available but some system catalog functions differ
    - SSL is enabled by default

    Args:
        host: Redshift cluster endpoint or workgroup endpoint (Serverless)
        port: Redshift port (default: 5439)
        database: Database name
        user: Username for authentication
        password: Password for authentication
        timeout: Connection timeout in seconds
        ssl: Whether to use SSL (default: True, strongly recommended)
    """

    def __init__(
        self,
        host: str,
        database: str,
        user: str,
        password: str,
        port: int = 5439,
        timeout: int = 180,
        ssl: bool = True,
    ) -> None:
        """Initialize Redshift client.

        Args:
            host: Redshift cluster endpoint (e.g., cluster.xxxx.us-east-1.redshift.amazonaws.com)
                  or Serverless workgroup endpoint
            database: Database name to connect to (REQUIRED)
            user: Username for authentication
            password: Password for authentication
            port: Redshift server port (default: 5439)
            timeout: Connection timeout in seconds
            ssl: Whether to use SSL (default: True)
        """
        if redshift_connector is None:
            raise ImportError(
                "redshift_connector is required for Redshift client. "
                "Install with: pip install redshift_connector"
            )

        logger.debug(
            f"🔧 [RedshiftClient] Initializing with host={host}, port={port}, "
            f"database={database}, user={user}"
        )

        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.timeout = timeout
        self.ssl = ssl
        self._connection = None

        logger.info(
            f"🔧 [RedshiftClient] Initialized successfully for {user}@{host}:{port}/{database}"
        )

    def connect(self) -> "RedshiftClient":
        """Establish connection to Redshift.

        Returns:
            Self for method chaining

        Raises:
            ConnectionError: If connection fails
        """
        if self._connection is not None and self.is_connected():
            logger.debug("🔧 [RedshiftClient] Already connected")
            return self

        try:
            logger.debug(
                f"🔧 [RedshiftClient] Connecting to {self.host}:{self.port}/{self.database}"
            )

            self._connection = redshift_connector.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                timeout=self.timeout,
                ssl=self.ssl,
            )

            logger.info("🔧 [RedshiftClient] Successfully connected to Redshift")
            return self

        except Exception as e:
            logger.error(f"🔧 [RedshiftClient] Connection failed: {e}")
            raise ConnectionError(f"Failed to connect to Redshift: {e}") from e

    def close(self) -> None:
        """Close the Redshift connection."""
        if self._connection is not None:
            try:
                self._connection.close()
                logger.info("🔧 [RedshiftClient] Connection closed")
            except Exception as e:
                logger.warning(
                    f"🔧 [RedshiftClient] Failed to close connection gracefully: {e}"
                )
            finally:
                self._connection = None

    def is_connected(self) -> bool:
        """Check if connection is active."""
        if self._connection is None:
            return False

        try:
            # redshift_connector 2.0.918 has no is_closed(); probe with cursor().
            cursor = self._connection.cursor()
            cursor.close()
            return True
        except Exception:
            self._connection = None
            return False

    def execute_query(
        self,
        query: str,
        params: Optional[dict[str, Any] | list[Any] | tuple] = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts.

        Args:
            query: SQL query to execute
            params: Optional query parameters (for parameterized queries)

        Returns:
            List of dictionaries containing query results

        Raises:
            ConnectionError: If not connected
            RuntimeError: If query execution fails
        """
        if not self.is_connected():
            self.connect()

        try:
            cursor = self._connection.cursor()

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            # For SELECT queries, fetch results
            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                results = [dict(zip(columns, row)) for row in rows]
            else:
                # For INSERT/UPDATE/DELETE, return affected rows count
                results = [{"affected_rows": cursor.rowcount}]

            self._connection.commit()
            cursor.close()

            return results

        except Exception as e:
            self._connection.rollback()
            logger.error(f"🔧 [RedshiftClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e

    def execute_query_raw(
        self,
        query: str,
        params: Optional[dict[str, Any] | list[Any] | tuple] = None,
    ) -> tuple:
        """Execute a SQL query and return raw cursor results.

        Args:
            query: SQL query to execute
            params: Optional query parameters

        Returns:
            Tuple of (columns, rows) where columns is list of column names
            and rows is list of tuples

        Raises:
            ConnectionError: If not connected
            RuntimeError: If query execution fails
        """
        logger.debug(
            f"🔧 [RedshiftClient.execute_query_raw] Executing query: {query[:200]}..."
        )

        if not self.is_connected():
            logger.debug("🔧 [RedshiftClient.execute_query_raw] Not connected, connecting...")
            self.connect()

        try:
            cursor = self._connection.cursor()
            logger.debug("🔧 [RedshiftClient.execute_query_raw] Cursor created")

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            logger.debug(
                f"🔧 [RedshiftClient.execute_query_raw] Query executed, "
                f"cursor.description={cursor.description is not None}"
            )

            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                logger.debug(
                    f"🔧 [RedshiftClient.execute_query_raw] Fetched {len(rows)} rows "
                    f"with columns {columns}"
                )
                if rows:
                    logger.debug(
                        f"🔧 [RedshiftClient.execute_query_raw] First row: {rows[0]}"
                    )
            else:
                columns = []
                rows = []
                logger.warning(
                    "🔧 [RedshiftClient.execute_query_raw] cursor.description is None - "
                    "no result set"
                )

            self._connection.commit()
            cursor.close()

            logger.info(
                f"🔧 [RedshiftClient.execute_query_raw] Returning {len(columns)} columns, "
                f"{len(rows)} rows"
            )
            return (columns, rows)

        except Exception as e:
            self._connection.rollback()
            logger.error(f"🔧 [RedshiftClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e

    def get_connection_info(self) -> dict[str, Any]:
        """Get connection information."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "ssl": self.ssl,
        }

    def __enter__(self) -> "RedshiftClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "RedshiftClient":
        """Build RedshiftClient from toolset configuration.

        Args:
            toolset_config: Toolset configuration from etcd.
            logger: Logger instance.
            config_service: Configuration service instance.
        Returns:
            RedshiftClient instance.
        """
        instance_id = toolset_config.get("instanceId")
        if not instance_id:
            raise ValueError("Instance ID is required for Redshift client")

        redshift_instance = await get_toolset_by_id(instance_id, config_service)
        def pick_value(config: dict[str, Any], *keys: str) -> Optional[Any]:
            auth_config = config.get("auth", {}) or {}
            credentials_config = config.get("credentials", {}) or {}

            for container in (config, auth_config, credentials_config):
                if not isinstance(container, dict):
                    continue
                for key in keys:
                    value = container.get(key)
                    if value not in (None, ""):
                        return value
            return None

        try:
            if not toolset_config:
                raise ValueError(
                    "Redshift toolset is not authenticated. Missing toolset configuration."
                )

            host = pick_value(redshift_instance, "host", "hostname", "endpoint")
            port = pick_value(redshift_instance, "port")
            database = pick_value(redshift_instance, "database", "db", "databaseName")
            user = pick_value(toolset_config, "username", "username")
            password = pick_value(toolset_config, "password")

            if host is None:
                raise ValueError(
                    "Redshift authentication config is missing required field: host"
                )
            if user is None:
                raise ValueError(
                    "Redshift authentication config is missing required field: user"
                )
            if database is None:
                raise ValueError(
                    "Redshift authentication config is missing required field: database"
                )

            # Password can be intentionally empty for some setups.
            password_value = "" if password is None else str(password)

            config = RedshiftConfig(
                host=str(host),
                port=int(port) if port is not None else 5439,
                database=str(database),
                user=str(user),
                password=password_value,
                timeout= 180,
                ssl=True,
            )

            logger.info("Built Redshift client from toolset config")
            return config.create_client()

        except Exception as e:
            logger.error(f"Failed to build Redshift client from toolset config: {str(e)}")
            raise


class RedshiftConfig(BaseModel):
    """Configuration for Redshift client.

    Args:
        host: Redshift cluster endpoint
        port: Redshift server port (default: 5439)
        database: Database name (REQUIRED)
        user: Username for authentication
        password: Password for authentication
        timeout: Connection timeout in seconds
        ssl: Whether to use SSL (default: True)
    """

    host: str = Field(..., description="Redshift cluster endpoint")
    port: int = Field(default=5439, description="Redshift server port", ge=1, le=65535)
    database: str = Field(..., description="Database name to connect to")
    user: str = Field(..., description="Username for authentication")
    password: str = Field(default="", description="Password for authentication")
    timeout: int = Field(default=180, description="Connection timeout in seconds", gt=0)
    ssl: bool = Field(default=True, description="Whether to use SSL (strongly recommended)")

    def create_client(self) -> RedshiftClient:
        """Create a Redshift client."""
        return RedshiftClient(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
            timeout=self.timeout,
            ssl=self.ssl,
        )


class AuthConfig(BaseModel):
    """Authentication configuration for Redshift connector."""

    host: str = Field(..., description="Redshift cluster endpoint")
    port: int = Field(default=5439, description="Redshift server port")
    database: str = Field(..., description="Database name")
    user: str = Field(..., description="Username")
    password: str = Field(default="", description="Password")
    ssl: bool = Field(default=True, description="Whether to use SSL")


class RedshiftConnectorConfig(BaseModel):
    """Configuration model for Redshift connector from services."""

    auth: AuthConfig = Field(..., description="Authentication configuration")
    timeout: int = Field(default=180, description="Connection timeout in seconds", gt=0)


class RedshiftClientBuilder(IClient):
    """Builder class for Redshift clients.

    This class provides a unified interface for creating Redshift clients.

    Example usage:
        config = RedshiftConfig(
            host="cluster.xxxx.us-east-1.redshift.amazonaws.com",
            port=5439,
            database="mydb",
            user="myuser",
            password="mypassword"
        )
        client_builder = RedshiftClientBuilder.build_with_config(config)
        client = client_builder.get_client()
    """

    def __init__(self, client: RedshiftClient) -> None:
        """Initialize with a Redshift client.

        Args:
            client: Redshift client instance
        """
        self._client = client

    def get_client(self) -> RedshiftClient:
        """Return the Redshift client object."""
        return self._client

    def get_connection_info(self) -> dict[str, Any]:
        """Return the connection information."""
        return self._client.get_connection_info()

    @classmethod
    def build_with_config(
        cls,
        config: RedshiftConfig,
    ) -> "RedshiftClientBuilder":
        """Build RedshiftClientBuilder with configuration.

        Args:
            config: Redshift configuration instance

        Returns:
            RedshiftClientBuilder instance
        """
        return cls(client=config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "RedshiftClientBuilder":
        """Build RedshiftClientBuilder using configuration service.

        This method retrieves Redshift connector configuration from
        the configuration service (etcd) and creates the client.

        Args:
            logger: Logger instance for error reporting
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID

        Returns:
            RedshiftClientBuilder instance

        Raises:
            ValueError: If configuration is missing or invalid
        """
        try:
            logger.debug(
                f"🔧 [RedshiftClientBuilder] build_from_services called with "
                f"connector_instance_id: {connector_instance_id}"
            )

            config_dict = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )

            config = RedshiftConnectorConfig.model_validate(config_dict)
            logger.debug(
                f"🔧 [RedshiftClientBuilder] Validated config - "
                f"host: '{config.auth.host}', port: {config.auth.port}, "
                f"database: '{config.auth.database}'"
            )

            client = RedshiftClient(
                host=config.auth.host,
                port=config.auth.port,
                database=config.auth.database,
                user=config.auth.user,
                password=config.auth.password,
                timeout=config.timeout,
                ssl=config.auth.ssl,
            )

            logger.info(
                f"🔧 [RedshiftClientBuilder] Successfully built client for "
                f"{config.auth.user}@{config.auth.host}:{config.auth.port}/{config.auth.database}"
            )
            return cls(client=client)

        except ValidationError as e:
            logger.error(f"Invalid Redshift connector configuration: {e}")
            raise ValueError("Invalid Redshift connector configuration") from e
        except Exception as e:
            logger.error(f"Failed to build Redshift client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for Redshift.

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
                instance_msg = (
                    f" for instance {connector_instance_id}" if connector_instance_id else ""
                )
                raise ValueError(
                    f"Failed to get Redshift connector configuration{instance_msg}"
                )
            if not isinstance(config, dict):
                instance_msg = (
                    f" for instance {connector_instance_id}" if connector_instance_id else ""
                )
                raise ValueError(
                    f"Invalid Redshift connector configuration format{instance_msg}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get Redshift connector config: {e}")
            instance_msg = (
                f" for instance {connector_instance_id}" if connector_instance_id else ""
            )
            raise ValueError(
                f"Failed to get Redshift connector configuration{instance_msg}"
            ) from e


class RedshiftResponse(BaseModel):
    """Standard response wrapper for Redshift operations."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[dict[str, Any] | list[Any]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(default=None, description="Error message if failed")
    message: Optional[str] = Field(default=None, description="Additional message")

    class Config:
        """Pydantic configuration."""

        extra = "allow"

    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)

    def to_json(self) -> str:
        """Convert response to JSON string."""
        return self.model_dump_json(exclude_none=True)
