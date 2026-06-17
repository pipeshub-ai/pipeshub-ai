"""MariaDB client implementation.

This module provides clients for interacting with MariaDB databases using
MariaDB Connector/Python 2.x async connections and pools.

MariaDB Documentation: https://mariadb.com/docs/
MariaDB Connector/Python: https://mariadb.com/docs/connectors/mariadb-connector-python/
"""

import asyncio
import logging
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from app.api.routes.toolsets import get_toolset_by_id
from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

_DEFAULT_MAX_POOL_SIZE = 10

try:
    import mariadb
except ImportError:
    mariadb = None


class MariaDBClient:
    """MariaDB client for async database connections.

    Uses MariaDB Connector/Python for connecting to MariaDB databases.

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
        pool_acquire_timeout: float = 60.0,
    ) -> None:
        if mariadb is None:
            raise ImportError(
                "mariadb is required for MariaDB client. "
                "Install with: uv add 'mariadb[binary,pool]>=2.0.0rc2,<3'"
            )

        if pool_acquire_timeout <= 0:
            raise ValueError("pool_acquire_timeout must be > 0")

        logger.debug(
            f"🔧 [MariaDBClient] Initializing with host={host}, port={port}, "
            f"database={database}, user={user}"
        )

        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.timeout = timeout
        self.ssl_ca = ssl_ca
        self.charset = charset
        self._max_pool_size = _DEFAULT_MAX_POOL_SIZE
        self.pool_acquire_timeout = pool_acquire_timeout
        self._pool: Any = None
        self._connect_lock = asyncio.Lock()

        db_part = f"/{database}" if database else ""
        logger.info(
            f"🔧 [MariaDBClient] Initialized successfully for "
            f"{user}@{host}:{port}{db_part}"
        )

    async def connect(self) -> "MariaDBClient":
        """Initialize the MariaDB async connection pool."""
        if self.is_connected():
            logger.debug("🔧 [MariaDBClient] Pool already initialized")
            return self

        async with self._connect_lock:
            if self.is_connected():
                logger.debug("🔧 [MariaDBClient] Pool already initialized")
                return self

            try:
                db_part = f"/{self.database}" if self.database else ""
                logger.debug(
                    f"🔧 [MariaDBClient] Initializing async pool to "
                    f"{self.host}:{self.port}{db_part} (max_size={self._max_pool_size})"
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

                self._pool = await mariadb.create_async_pool(
                    min_size=1,
                    max_size=self._max_pool_size,
                    acquire_timeout=self.pool_acquire_timeout,
                    reset_connection=True,
                    **connect_kwargs,
                )

                logger.info("🔧 [MariaDBClient] MariaDB async connection pool ready")
                return self

            except Exception as e:
                logger.error(f"🔧 [MariaDBClient] Pool initialization failed: {e}")
                raise ConnectionError(f"Failed to connect to MariaDB: {e}") from e

    async def close(self) -> None:
        """Close all connections in the pool."""
        if self._pool is None:
            return

        pool = self._pool
        self._pool = None
        try:
            await pool.close()
            logger.info("🔧 [MariaDBClient] Connection pool closed")
        except Exception as e:
            logger.warning(
                f"🔧 [MariaDBClient] Failed to close pool gracefully: {e}"
            )

    def is_connected(self) -> bool:
        """Check if the pool is initialized."""
        return self._pool is not None

    def resize_pool(self, max_size: int) -> "MariaDBClient":
        """Resize the connection pool. Must be called before ``connect()``."""
        if self._pool is not None:
            raise RuntimeError("Cannot resize after pool is initialized")
        if max_size < 1 or max_size > 64:
            raise ValueError("max_size must be between 1 and 64")
        self._max_pool_size = max_size
        return self

    async def execute_query(
        self,
        query: str,
        params: Optional[dict[str, Any] | list[Any] | tuple] = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts."""
        if not self.is_connected():
            await self.connect()
        if self._pool is None:
            raise RuntimeError("MariaDB pool not initialized")

        try:
            async with self._pool.connection() as conn:
                cursor = None
                try:
                    cursor = conn.cursor(dictionary=True)
                    await cursor.execute(query, params or ())
                    if cursor.description:
                        results = await cursor.fetchall()
                    else:
                        results = [{"affected_rows": cursor.rowcount}]
                    await conn.commit()
                    return results
                except Exception:
                    try:
                        await conn.rollback()
                    except Exception:
                        pass
                    raise
                finally:
                    if cursor is not None:
                        try:
                            await cursor.close()
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"🔧 [MariaDBClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e

    async def execute_query_raw(
        self,
        query: str,
        params: Optional[dict[str, Any] | list[Any] | tuple] = None,
    ) -> tuple:
        """Execute a SQL query and return raw columns and rows."""
        logger.debug(
            f"🔧 [MariaDBClient.execute_query_raw] Executing query: {query[:200]}..."
        )

        if not self.is_connected():
            await self.connect()
        if self._pool is None:
            raise RuntimeError("MariaDB pool not initialized")

        try:
            async with self._pool.connection() as conn:
                cursor = None
                try:
                    cursor = conn.cursor()
                    await cursor.execute(query, params or ())
                    if cursor.description:
                        columns = [desc[0] for desc in cursor.description]
                        rows = await cursor.fetchall()
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

                    await conn.commit()
                    return (columns, rows)
                except Exception:
                    try:
                        await conn.rollback()
                    except Exception:
                        pass
                    raise
                finally:
                    if cursor is not None:
                        try:
                            await cursor.close()
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"🔧 [MariaDBClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e

    def get_connection_info(self) -> dict[str, Any]:
        """Get connection information."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "charset": self.charset,
        }

    async def __aenter__(self) -> "MariaDBClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_val,
        exc_tb,
    ) -> None:
        """Async context manager exit."""
        await self.close()

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
    pool_acquire_timeout: float = Field(
        default=30.0,
        description="Seconds a caller will wait for a free pool connection",
        gt=0,
    )

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
            pool_acquire_timeout=self.pool_acquire_timeout,
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
