"""PostgreSQL client implementation.

This module provides PostgreSQL connection and pool management using asyncpg.

PostgreSQL Documentation: https://www.postgresql.org/docs/
asyncpg Documentation: https://magicstack.github.io/asyncpg/current/
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from pydantic import AliasChoices, BaseModel, Field, ValidationError, model_validator

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

# TYPE_CHECKING is False at runtime but True for Pylance/mypy, so asyncpg types
# (e.g. asyncpg.Pool) resolve correctly in annotations instead of widening to Any.
if TYPE_CHECKING:
    import asyncpg
else:
    try:
        import asyncpg
    except ImportError:
        asyncpg = None

class PostgreSQLClient:
    """PostgreSQL client for asyncpg pool management."""

    def __init__(
        self,
        host: str,
        database: str,
        user: str,
        password: str,
        port: int = 5432,
        timeout: int = 30,
        sslmode: str = "prefer",
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        pool_acquire_timeout: float = 60.0,
    ) -> None:
        """Initialize PostgreSQL client."""
        if asyncpg is None:
            raise ImportError(
                "asyncpg is required for PostgreSQL client. "
                "Install with: pip install asyncpg"
            )

        if min_pool_size < 1:
            raise ValueError("min_pool_size must be >= 1")
        if max_pool_size < min_pool_size:
            raise ValueError("max_pool_size must be >= min_pool_size")
        if pool_acquire_timeout <= 0:
            raise ValueError("pool_acquire_timeout must be > 0")

        logger.debug(f"🔧 [PostgreSQLClient] Initializing with host={host}, port={port}, database={database}, user={user}")

        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.timeout = timeout
        self.sslmode = sslmode
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.pool_acquire_timeout = pool_acquire_timeout
        self._pool: asyncpg.Pool | None = None
        self._connect_lock = asyncio.Lock() # Serialize pool creation so concurrent connect() calls create only one pool.

        logger.info(f"🔧 [PostgreSQLClient] Initialized successfully for {user}@{host}:{port}/{database}")

    async def connect(self) -> "PostgreSQLClient":
        """Initialize the asyncpg connection pool."""
        if self._pool is not None and not self._pool.is_closing():
            logger.debug("🔧 [PostgreSQLClient] Pool already initialized")
            return self

        async with self._connect_lock:
            if self._pool is not None and not self._pool.is_closing():
                logger.debug("🔧 [PostgreSQLClient] Pool already initialized")
                return self

            try:
                logger.debug(
                    f"🔧 [PostgreSQLClient] Initializing pool to "
                    f"{self.host}:{self.port}/{self.database} "
                    f"(min={self.min_pool_size}, max={self.max_pool_size})"
                )

                self._pool = await asyncpg.create_pool(
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password,
                    min_size=self.min_pool_size,
                    max_size=self.max_pool_size,
                    timeout=self.timeout,
                    command_timeout=self.timeout,
                    ssl=(self.sslmode or "prefer").strip().lower(),
                )

                logger.info("🔧 [PostgreSQLClient] PostgreSQL connection pool ready")
                return self

            except Exception as e:
                logger.error(f"🔧 [PostgreSQLClient] Pool initialization failed: {e}")
                raise ConnectionError(f"Failed to connect to PostgreSQL: {e}") from e

    async def close(self) -> None:
        """Close all connections in the pool."""
        if self._pool is None:
            return

        pool = self._pool
        self._pool = None
        try:
            await pool.close()
            logger.info("🔧 [PostgreSQLClient] Connection pool closed")
        except Exception as e:
            logger.warning(f"🔧 [PostgreSQLClient] Failed to close pool gracefully: {e}")
            pool.terminate()

    def is_connected(self) -> bool:
        """Check if the connection pool is active."""
        return self._pool is not None and not self._pool.is_closing()

    def resize_pool(self, min_pool_size: int, max_pool_size: int) -> "PostgreSQLClient":
        """Resize the connection pool. Must be called before ``connect()``."""
        if self._pool is not None and not self._pool.is_closing():
            raise RuntimeError("Cannot resize after pool is initialized")
        if min_pool_size < 1:
            raise ValueError("min_pool_size must be >= 1")
        if max_pool_size < min_pool_size:
            raise ValueError("max_pool_size must be >= min_pool_size")
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        return self

    def get_pool(self) -> asyncpg.Pool | None:
        """Return the underlying asyncpg pool for datasource-level querying."""
        return self._pool

    async def execute_query(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts."""
        if not self.is_connected():
            await self.connect()

        if self._pool is None:
            raise RuntimeError("PostgreSQL pool not initialized")

        try:
            async with self._pool.acquire(timeout=self.pool_acquire_timeout) as conn:
                prepared_params = self._normalize_params(params)
                statement = await conn.prepare(query)
                if statement.get_attributes():
                    rows = await statement.fetch(*prepared_params)
                    return [dict(row) for row in rows]

                status = await conn.execute(query, *prepared_params)
                return [{"affected_rows": self._parse_affected_rows(status)}]
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e

    async def execute_query_raw(
        self,
        query: str,
        params: Sequence[Any] | None = None,
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        """
        Execute a SQL query and return raw columns/rows.
        conn.execute doesn't support multi-statement queries.
        """
        if not self.is_connected():
            await self.connect()

        if self._pool is None:
            raise RuntimeError("PostgreSQL pool not initialized")

        try:
            async with self._pool.acquire(timeout=self.pool_acquire_timeout) as conn:
                prepared_params = self._normalize_params(params)
                statement = await conn.prepare(query)
                attributes = statement.get_attributes()
                if not attributes:
                    await conn.execute(query, *prepared_params)
                    return ([], [])

                rows = await statement.fetch(*prepared_params)
                columns = [attr.name for attr in attributes]
                raw_rows = [tuple(row[column] for column in columns) for row in rows]
                return (columns, raw_rows)
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLClient.execute_query_raw] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e

    @staticmethod
    def _normalize_params(
        params: Any,
    ) -> tuple[Any, ...]:
        """Normalize params to asyncpg positional args."""
        if params is None:
            return ()
        if isinstance(params, dict):
            raise TypeError(
                "asyncpg only accepts positional parameters ($1, $2, ...); "
                "dict params with %(name)s placeholders are not supported."
            )
        if isinstance(params, Sequence) and not isinstance(params, (str, bytes, bytearray)):
            return tuple(params)
        return (params,)

    @staticmethod
    def _parse_affected_rows(status: str) -> int:
        """Extract affected row count from an asyncpg status string."""
        try:
            return int(status.split()[-1])
        except (IndexError, ValueError, AttributeError):
            return 0

    def get_connection_info(self) -> dict[str, Any]:
        """Get connection information."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "sslmode": self.sslmode,
        }

    async def __aenter__(self) -> "PostgreSQLClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()


class PostgreSQLConfig(BaseModel):
    """Configuration for PostgreSQL client.
    
    Args:
        host: PostgreSQL server host
        port: PostgreSQL server port
        database: Database name (REQUIRED)
        user: Username for authentication
        password: Password for authentication
        timeout: Connection timeout in seconds
        sslmode: SSL mode
    """
    
    host: str = Field(..., description="PostgreSQL server host")
    port: int = Field(default=5432, description="PostgreSQL server port", ge=1, le=65535)
    database: str = Field(..., description="Database name to connect to")
    user: str = Field(
        ...,
        description="Username for authentication",
        validation_alias=AliasChoices("username", "user"),
    )
    password: str = Field(default="", description="Password for authentication")
    timeout: int = Field(default=30, description="Connection timeout in seconds", gt=0)
    sslmode: str = Field(
        default="prefer",
        description="SSL mode (disable, allow, prefer, require, verify-ca, verify-full)"
    )
    min_pool_size: int = Field(default=1, description="Min pool size", ge=1)
    max_pool_size: int = Field(default=10, description="Max pool size", ge=1)
    pool_acquire_timeout: float = Field(
        default=30.0,
        description="Seconds a caller will wait for a free pool connection",
        gt=0,
    )

    def create_client(self) -> PostgreSQLClient:
        """Create a PostgreSQL client."""
        return PostgreSQLClient(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
            timeout=self.timeout,
            sslmode=self.sslmode,
            min_pool_size=self.min_pool_size,
            max_pool_size=self.max_pool_size,
            pool_acquire_timeout=self.pool_acquire_timeout,
        )


class AuthConfig(BaseModel):
    """Authentication configuration for PostgreSQL connector."""

    host: str | None = Field(default=None, description="PostgreSQL server host")
    port: int = Field(default=5432, description="PostgreSQL server port")
    database: str | None = Field(default=None, description="Database name")
    user: str | None = Field(
        default=None,
        description="Username",
        validation_alias=AliasChoices("username", "user"),
    )
    password: str = Field(default="", description="Password")
    sslmode: str = Field(default="prefer", description="SSL mode")
    connection_string: str | None = Field(
        default=None,
        description="Full DSN/URI; used when authType is CONNECTION_STRING",
        validation_alias=AliasChoices("connectionString", "connection_string"),
    )

    @model_validator(mode="after")
    def _populate_from_connection_string(self) -> "AuthConfig":
        if self.connection_string:
            parsed = urlparse(self.connection_string)
            set_fields = self.model_fields_set
            if parsed.hostname and "host" not in set_fields:
                self.host = parsed.hostname
            if parsed.port and "port" not in set_fields:
                self.port = parsed.port
            if parsed.path and "database" not in set_fields:
                self.database = parsed.path.lstrip("/") or None
            if parsed.username and "user" not in set_fields:
                self.user = unquote(parsed.username)
            if parsed.password and "password" not in set_fields:
                self.password = unquote(parsed.password)

        missing = [f for f in ("host", "database", "user") if not getattr(self, f)]
        if missing:
            raise ValueError(
                f"Missing required PostgreSQL auth fields: {missing}. "
                "Provide them directly or via connection_string."
            )
        return self


class PostgreSQLConnectorConfig(BaseModel):
    """Configuration model for PostgreSQL connector from services."""
    
    auth: AuthConfig = Field(..., description="Authentication configuration")
    timeout: int = Field(default=30, description="Connection timeout in seconds", gt=0)


class PostgreSQLClientBuilder(IClient):
    """Builder class for PostgreSQL clients.
    
    This class provides a unified interface for creating PostgreSQL clients.
    
    Example usage:
        config = PostgreSQLConfig(
            host="localhost",
            port=5432,
            database="mydb",
            user="myuser",
            password="mypassword"
        )
        client_builder = PostgreSQLClientBuilder.build_with_config(config)
        client = client_builder.get_client()
    """
    
    def __init__(self, client: PostgreSQLClient) -> None:
        """Initialize with a PostgreSQL client.
        
        Args:
            client: PostgreSQL client instance
        """
        self._client = client
    
    def get_client(self) -> PostgreSQLClient:
        """Return the PostgreSQL client object."""
        return self._client
    
    def get_connection_info(self) -> dict[str, Any]:
        """Return the connection information."""
        return self._client.get_connection_info()
    
    @classmethod
    def build_with_config(
        cls,
        config: PostgreSQLConfig,
    ) -> "PostgreSQLClientBuilder":
        """Build PostgreSQLClientBuilder with configuration.
        
        Args:
            config: PostgreSQL configuration instance
            
        Returns:
            PostgreSQLClientBuilder instance
        """
        return cls(client=config.create_client())
    
    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "PostgreSQLClientBuilder":
        """Build PostgreSQLClientBuilder using configuration service.
        
        This method retrieves PostgreSQL connector configuration from
        the configuration service (etcd) and creates the client.
        
        Args:
            logger: Logger instance for error reporting
            config_service: Configuration service instance
            connector_instance_id: Optional connector instance ID
            
        Returns:
            PostgreSQLClientBuilder instance
            
        Raises:
            ValueError: If configuration is missing or invalid
        """
        try:
            logger.debug(
                f"🔧 [PostgreSQLClientBuilder] build_from_services called with "
                f"connector_instance_id: {connector_instance_id}"
            )
            
            config_dict = await cls._get_connector_config(
                logger, config_service, connector_instance_id
            )
            
            
            config = PostgreSQLConnectorConfig.model_validate(config_dict)
            logger.debug(
                f"🔧 [PostgreSQLClientBuilder] Validated config - "
                f"host: '{config.auth.host}', port: {config.auth.port}, "
                f"database: '{config.auth.database}'"
            )
            
            client = PostgreSQLClient(
                host=config.auth.host,
                port=config.auth.port,
                database=config.auth.database,
                user=config.auth.user,
                password=config.auth.password,
                timeout=config.timeout,
                sslmode=config.auth.sslmode,
            )
            
            logger.info(
                f"🔧 [PostgreSQLClientBuilder] Successfully built client for "
                f"{config.auth.user}@{config.auth.host}:{config.auth.port}/{config.auth.database}"
            )
            return cls(client=client)
            
        except ValidationError as e:
            logger.error(f"Invalid PostgreSQL connector configuration: {e}")
            raise ValueError("Invalid PostgreSQL connector configuration") from e
        except Exception as e:
            logger.error(f"Failed to build PostgreSQL client from services: {str(e)}")
            raise
    
    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for PostgreSQL.
        
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
                    f"Failed to get PostgreSQL connector configuration{instance_msg}"
                )
            if not isinstance(config, dict):
                instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
                raise ValueError(
                    f"Invalid PostgreSQL connector configuration format{instance_msg}"
                )
            return config
        except Exception as e:
            logger.error(f"Failed to get PostgreSQL connector config: {e}")
            instance_msg = f" for instance {connector_instance_id}" if connector_instance_id else ""
            raise ValueError(
                f"Failed to get PostgreSQL connector configuration{instance_msg}"
            ) from e


class PostgreSQLResponse(BaseModel):
    """Standard response wrapper for PostgreSQL operations."""
    
    success: bool = Field(..., description="Whether the request was successful")
    data: dict[str, Any] | list[Any] | None = Field(
        default=None, description="Response data"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(default=None, description="Additional message")
    
    class Config:
        """Pydantic configuration."""
        extra = "allow"
    
    def to_dict(self) -> dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)
    
    def to_json(self) -> str:
        """Convert response to JSON string."""
        return self.model_dump_json(exclude_none=True)
