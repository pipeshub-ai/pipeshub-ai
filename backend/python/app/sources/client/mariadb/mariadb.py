"""MariaDB client implementation.

This module provides clients for interacting with MariaDB databases using
the official MariaDB Connector/Python.

MariaDB Documentation: https://mariadb.com/docs/
MariaDB Connector/Python: https://mariadb.com/docs/server/connect/programming-languages/python/
"""

import logging
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from app.api.routes.toolsets import get_toolset_by_id
from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

try:
    import mariadb
except ImportError:
    mariadb = None


class MariaDBClient:
    """MariaDB client for database connections.

    Uses the official MariaDB Connector/Python for connecting to MariaDB databases.

    Args:
        host: MariaDB server host
        port: MariaDB server port
        database: Database name
        user: Username for authentication
        password: Password for authentication
        timeout: Connection timeout in seconds
        ssl_ca: Path to CA certificate file (optional)
        charset: Character set (default: utf8mb4)
    """

    _pool_seq: int = 0

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        database: Optional[str] = None,
        port: int = 3306,
        timeout: int = 30,
        ssl_ca: Optional[str] = None,
        charset: str = "utf8mb4",
        pool_size: int = 5,
    ) -> None:
        if mariadb is None:
            raise ImportError(
                "mariadb is required for MariaDB client. "
                "Install with: pip install mariadb"
            )

        if pool_size < 1 or pool_size > 64:
            # mariadb's ConnectionPool caps at 64.
            raise ValueError("pool_size must be between 1 and 64")

        logger.debug(
            f"🔧 [MariaDBClient] Initializing with host={host}, port={port}, "
            f"database={database}, user={user}, pool_size={pool_size}"
        )

        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.timeout = timeout
        self.ssl_ca = ssl_ca
        self.charset = charset
        self.pool_size = pool_size
        self._pool: Any = None  # mariadb.ConnectionPool when connected
        # Pool names must be unique within the process — tag with id+sequence.
        MariaDBClient._pool_seq += 1
        self._pool_name = f"pipeshub_mariadb_{id(self)}_{MariaDBClient._pool_seq}"

        db_part = f"/{database}" if database else ""
        logger.info(
            f"🔧 [MariaDBClient] Initialized successfully for "
            f"{user}@{host}:{port}{db_part}"
        )

    def connect(self) -> "MariaDBClient":
        """Initialize the MariaDB connection pool.

        Opens a thread-safe pool of connections that callers check out per query.
        Safe to call repeatedly — a no-op if the pool is already initialized.

        Returns:
            Self for method chaining

        Raises:
            ConnectionError: If pool initialization fails
        """
        if self._pool is not None:
            logger.debug("🔧 [MariaDBClient] Pool already initialized")
            return self

        try:
            db_part = f"/{self.database}" if self.database else ""
            logger.debug(
                f"🔧 [MariaDBClient] Initializing pool to "
                f"{self.host}:{self.port}{db_part} (pool_size={self.pool_size})"
            )

            connect_kwargs: dict[str, Any] = {
                "host": self.host,
                "port": self.port,
                "user": self.user,
                "password": self.password,
                "connect_timeout": self.timeout,
            }
            if self.database:
                connect_kwargs["database"] = self.database
            if self.ssl_ca:
                connect_kwargs["ssl_ca"] = self.ssl_ca
            # Do not pass charset/character_encoding: the driver does not accept
            # 'charset' and default is utf8mb4.

            self._pool = mariadb.ConnectionPool(
                pool_name=self._pool_name,
                pool_size=self.pool_size,
                pool_reset_connection=True,
                **connect_kwargs,
            )

            logger.info("🔧 [MariaDBClient] MariaDB connection pool ready")
            return self

        except mariadb.Error as e:
            logger.error(f"🔧 [MariaDBClient] Pool initialization failed: {e}")
            raise ConnectionError(f"Failed to connect to MariaDB: {e}") from e

    def close(self) -> None:
        """Close all connections in the pool."""
        if self._pool is not None:
            try:
                self._pool.close()
                logger.info("🔧 [MariaDBClient] Connection pool closed")
            except mariadb.Error as e:
                logger.warning(
                    f"🔧 [MariaDBClient] Failed to close pool gracefully: {e}"
                )
            finally:
                self._pool = None

    def is_connected(self) -> bool:
        """Check if the pool is initialized."""
        return self._pool is not None

    def _checkout_conn(self) -> Any:
        """Check out a connection from the pool, initializing it if needed."""
        if not self.is_connected():
            self.connect()
        return self._pool.get_connection()

    def _return_conn(self, conn: Any) -> None:
        """Return a connection to the pool. Calling close() returns it for reuse."""
        if conn is None:
            return
        try:
            conn.close()
        except Exception as e:
            logger.warning(
                f"🔧 [MariaDBClient] Failed to return connection to pool: {e}"
            )

    def execute_query(
        self,
        query: str,
        params: Optional[dict[str, Any] | list[Any] | tuple] = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts.

        Checks out a connection from the pool, runs the query, and returns the
        connection to the pool when done.

        Args:
            query: SQL query to execute
            params: Optional query parameters (for parameterized queries)

        Returns:
            List of dictionaries containing query results

        Raises:
            ConnectionError: If pool is not initialized
            RuntimeError: If query execution fails
        """
        conn = self._checkout_conn()
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query, params or ())

            if cursor.description:
                results = cursor.fetchall()
            else:
                results = [{"affected_rows": cursor.rowcount}]

            conn.commit()
            return results

        except mariadb.Error as e:
            try:
                conn.rollback()
            except mariadb.Error:
                pass
            logger.error(f"🔧 [MariaDBClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except mariadb.Error:
                    pass
            self._return_conn(conn)

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
            ConnectionError: If pool is not initialized
            RuntimeError: If query execution fails
        """
        logger.debug(
            f"🔧 [MariaDBClient.execute_query_raw] Executing query: {query[:200]}..."
        )

        conn = self._checkout_conn()
        cursor = None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params or ())

            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                logger.debug(
                    f"🔧 [MariaDBClient.execute_query_raw] Fetched {len(rows)} rows "
                    f"with columns {columns}"
                )
            else:
                columns = []
                rows = []
                logger.warning(
                    "🔧 [MariaDBClient.execute_query_raw] cursor.description is None "
                    "- no result set"
                )

            conn.commit()
            return (columns, rows)

        except mariadb.Error as e:
            try:
                conn.rollback()
            except mariadb.Error:
                pass
            logger.error(f"🔧 [MariaDBClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except mariadb.Error:
                    pass
            self._return_conn(conn)

    def get_connection_info(self) -> dict[str, Any]:
        """Get connection information."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "charset": self.charset,
        }

    def __enter__(self) -> "MariaDBClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type,
        exc_val,
        exc_tb,
    ) -> None:
        """Context manager exit."""
        self.close()

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "MariaDBClient":
        """Build MariaDBClient from toolset configuration.

        Args:
            toolset_config: Toolset configuration from etcd.
            logger: Logger instance.
            config_service: Configuration service instance.
        Returns:
            MariaDBClient instance.
        """

        instance_id = toolset_config.get("instanceId")
        if not instance_id:
            raise ValueError("MariaDB toolset configuration is missing required field: instanceId")

        mariadb_instance = await get_toolset_by_id(instance_id, config_service)

        def pick_value(config: dict[str, Any], *keys: str) -> Optional[Any]:
            auth_config = config.get("auth", {}) or {}

            for container in (config, auth_config):
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
                    "MariaDB toolset is not authenticated. Missing toolset configuration."
                )

            host = pick_value(mariadb_instance, "host", "hostname")
            port = pick_value(mariadb_instance, "port")
            database = pick_value(mariadb_instance, "database", "db", "databaseName")
            user = pick_value(toolset_config, "username", "username")
            password = pick_value(toolset_config, "password")

            if host is None:
                raise ValueError(
                    "MariaDB authentication config is missing required field: host"
                )
            if user is None:
                raise ValueError(
                    "MariaDB authentication config is missing required field: username"
                )

            # Password can be intentionally empty for some setups.
            password_value = "" if password is None else str(password)

            config = MariaDBConfig(
                host=str(host),
                port=int(port) if port is not None else 3306,
                database=str(database) if database not in (None, "") else None,
                user=str(user),
                password=password_value,
                timeout=30,
                ssl_ca=None,
                charset="utf8mb4",
            )

            logger.info("Built MariaDB client from toolset config")
            return config.create_client()

        except Exception as e:
            logger.error(f"Failed to build MariaDB client from toolset config: {str(e)}")
            raise


class MariaDBConfig(BaseModel):
    """Configuration for MariaDB client."""

    host: str = Field(..., description="MariaDB server host")
    port: int = Field(default=3306, description="MariaDB server port", ge=1, le=65535)
    database: Optional[str] = Field(default=None, description="Database name to connect to (optional)")
    user: str = Field(
        ...,
        description="Username for authentication",
        validation_alias=AliasChoices("username", "user"),
    )
    password: str = Field(default="", description="Password for authentication")
    timeout: int = Field(default=30, description="Connection timeout in seconds", gt=0)
    ssl_ca: Optional[str] = Field(
        default=None, description="Path to CA certificate for SSL"
    )
    charset: str = Field(default="utf8mb4", description="Character set")
    pool_size: int = Field(default=5, description="Connection pool size", ge=1, le=64)

    def create_client(self) -> MariaDBClient:
        """Create a MariaDB client."""
        return MariaDBClient(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
            timeout=self.timeout,
            ssl_ca=self.ssl_ca,
            charset=self.charset,
            pool_size=self.pool_size,
        )


class AuthConfig(BaseModel):
    """Authentication configuration for MariaDB connector."""

    host: str = Field(..., description="MariaDB server host")
    port: int = Field(default=3306, description="MariaDB server port")
    database: Optional[str] = Field(default=None, description="Database name (optional)")
    user: str = Field(
        ...,
        description="Username",
        validation_alias=AliasChoices("username", "user"),
    )
    password: str = Field(default="", description="Password")
    ssl_ca: Optional[str] = Field(default=None, description="Path to CA certificate")


class MariaDBConnectorConfig(BaseModel):
    """Configuration model for MariaDB connector from services."""

    auth: AuthConfig = Field(..., description="Authentication configuration")
    timeout: int = Field(default=30, description="Connection timeout in seconds", gt=0)


class MariaDBClientBuilder(IClient):
    """Builder class for MariaDB clients."""

    def __init__(self, client: MariaDBClient) -> None:
        self._client = client

    def get_client(self) -> MariaDBClient:
        """Return the MariaDB client object."""
        return self._client

    def get_connection_info(self) -> dict[str, Any]:
        """Return the connection information."""
        return self._client.get_connection_info()

    @classmethod
    def build_with_config(cls, config: MariaDBConfig) -> "MariaDBClientBuilder":
        """Build MariaDBClientBuilder with configuration."""
        return cls(client=config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> "MariaDBClientBuilder":
        """Build MariaDBClientBuilder using configuration service."""
        try:
            logger.debug(
                f"🔧 [MariaDBClientBuilder] build_from_services called with "
                f"connector_instance_id: {connector_instance_id}"
            )

            config_dict = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )

            config = MariaDBConnectorConfig.model_validate(config_dict)
            logger.debug(
                f"🔧 [MariaDBClientBuilder] Validated config - "
                f"host: '{config.auth.host}', port: {config.auth.port}, "
                f"database: '{config.auth.database or '(all)'}'"
            )

            client = MariaDBClient(
                host=config.auth.host,
                port=config.auth.port,
                database=config.auth.database,
                user=config.auth.user,
                password=config.auth.password,
                timeout=config.timeout,
                ssl_ca=config.auth.ssl_ca,
            )

            db_part = f"/{config.auth.database}" if config.auth.database else ""
            logger.info(
                f"🔧 [MariaDBClientBuilder] Successfully built client for "
                f"{config.auth.user}@{config.auth.host}:{config.auth.port}"
                f"{db_part}"
            )
            return cls(client=client)

        except ValidationError as e:
            logger.error(f"Invalid MariaDB connector configuration: {e}")
            raise ValueError("Invalid MariaDB connector configuration") from e
        except Exception as e:
            logger.error(f"Failed to build MariaDB client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for MariaDB."""
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                instance_msg = (
                    f" for instance {connector_instance_id}"
                    if connector_instance_id
                    else ""
                )
                raise ValueError(
                    f"Failed to get MariaDB connector configuration{instance_msg}"
                )
            if not isinstance(config, dict):
                instance_msg = (
                    f" for instance {connector_instance_id}"
                    if connector_instance_id
                    else ""
                )
                raise ValueError(
                    f"Invalid MariaDB connector configuration format{instance_msg}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get MariaDB connector config: {e}")
            instance_msg = (
                f" for instance {connector_instance_id}"
                if connector_instance_id
                else ""
            )
            raise ValueError(
                f"Failed to get MariaDB connector configuration{instance_msg}"
            ) from e


class MariaDBResponse(BaseModel):
    """Standard response wrapper for MariaDB operations."""

    success: bool = Field(..., description="Whether the request was successful")
    data: Optional[dict[str, Any] | list[Any]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(default=None, description="Error message if failed")
    message: Optional[str] = Field(default=None, description="Additional message")

    class Config:
        extra = "allow"

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)
