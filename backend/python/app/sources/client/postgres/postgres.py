"""PostgreSQL client implementation.

This module provides clients for interacting with PostgreSQL databases using:
1. Username/Password authentication
2. Connection string authentication

PostgreSQL Documentation: https://www.postgresql.org/docs/
psycopg2 Documentation: https://www.psycopg.org/docs/
"""

import logging
from typing import Any, Dict, List, Optional, Union
from urllib.parse import unquote, urlparse

from pydantic import AliasChoices, BaseModel, Field, ValidationError, model_validator

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import pool as psycopg2_pool
except ImportError:
    psycopg2 = None
    psycopg2_pool = None


class PostgreSQLClient:
    """PostgreSQL client for database connections.
    
    Uses psycopg2 for connecting to PostgreSQL databases.
    
    Args:
        host: PostgreSQL server host
        port: PostgreSQL server port
        database: Database name
        user: Username for authentication
        password: Password for authentication
        timeout: Connection timeout in seconds
    """
    
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
    ) -> None:
        """Initialize PostgreSQL client.

        Args:
            host: PostgreSQL server host (e.g., localhost, 192.168.1.1)
            database: Database name to connect to (REQUIRED)
            user: Username for authentication
            password: Password for authentication
            port: PostgreSQL server port (default: 5432)
            timeout: Connection timeout in seconds
            sslmode: SSL mode (disable, allow, prefer, require, verify-ca, verify-full)
            min_pool_size: Minimum number of connections kept open in the pool
            max_pool_size: Maximum number of connections the pool may open
        """
        if psycopg2 is None:
            raise ImportError(
                "psycopg2 is required for PostgreSQL client. "
                "Install with: pip install psycopg2-binary"
            )

        if min_pool_size < 1:
            raise ValueError("min_pool_size must be >= 1")
        if max_pool_size < min_pool_size:
            raise ValueError("max_pool_size must be >= min_pool_size")

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
        self._pool: Any = None  # psycopg2_pool.ThreadedConnectionPool when connected

        logger.info(f"🔧 [PostgreSQLClient] Initialized successfully for {user}@{host}:{port}/{database}")
    
    def connect(self) -> "PostgreSQLClient":
        """Initialize the PostgreSQL connection pool.

        Opens a thread-safe pool of connections that callers check out per query.
        Safe to call repeatedly — a no-op if the pool is already initialized.

        Returns:
            Self for method chaining

        Raises:
            ConnectionError: If pool initialization fails
        """
        if self._pool is not None and not self._pool.closed:
            logger.debug("🔧 [PostgreSQLClient] Pool already initialized")
            return self

        try:
            logger.debug(
                f"🔧 [PostgreSQLClient] Initializing pool to "
                f"{self.host}:{self.port}/{self.database} "
                f"(min={self.min_pool_size}, max={self.max_pool_size})"
            )

            self._pool = psycopg2_pool.ThreadedConnectionPool(
                self.min_pool_size,
                self.max_pool_size,
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                connect_timeout=self.timeout,
                sslmode=self.sslmode,
            )

            logger.info("🔧 [PostgreSQLClient] PostgreSQL connection pool ready")
            return self

        except Exception as e:
            logger.error(f"🔧 [PostgreSQLClient] Pool initialization failed: {e}")
            raise ConnectionError(f"Failed to connect to PostgreSQL: {e}") from e

    def close(self) -> None:
        """Close all connections in the pool."""
        if self._pool is not None:
            try:
                self._pool.closeall()
                logger.info("🔧 [PostgreSQLClient] Connection pool closed")
            except Exception as e:
                logger.warning(f"🔧 [PostgreSQLClient] Failed to close pool gracefully: {e}")
            finally:
                self._pool = None

    def is_connected(self) -> bool:
        """Check if the connection pool is active."""
        return self._pool is not None and not self._pool.closed
    
    def _checkout_conn(self) -> Any:
        """Check out a connection from the pool, initializing it if needed."""
        if not self.is_connected():
            self.connect()
        return self._pool.getconn()

    def _return_conn(self, conn: Any, broken: bool = False) -> None:
        """Return a connection to the pool. Drop it if broken or already closed."""
        if conn is None or self._pool is None:
            return
        try:
            discard = broken or bool(getattr(conn, "closed", 0))
            self._pool.putconn(conn, close=discard)
        except Exception as e:
            logger.warning(f"🔧 [PostgreSQLClient] Failed to return connection to pool: {e}")

    def execute_query(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts.

        Checks out a connection from the pool, runs the query, and returns the
        connection (or discards it if the connection is broken).

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
        broken = False
        try:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            # For SELECT queries, fetch results
            if cursor.description:
                results = [dict(row) for row in cursor.fetchall()]
            else:
                # For INSERT/UPDATE/DELETE, return affected rows count
                results = [{"affected_rows": cursor.rowcount}]

            conn.commit()
            return results

        except Exception as e:
            broken = True
            try:
                conn.rollback()
                # If rollback succeeded the connection is reusable.
                broken = bool(getattr(conn, "closed", 0))
            except Exception:
                broken = True
            logger.error(f"🔧 [PostgreSQLClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            self._return_conn(conn, broken=broken)

    def execute_query_raw(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
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
        logger.debug(f"🔧 [PostgreSQLClient.execute_query_raw] Executing query: {query[:200]}...")

        conn = self._checkout_conn()
        cursor = None
        broken = False
        try:
            cursor = conn.cursor()
            logger.debug("🔧 [PostgreSQLClient.execute_query_raw] Cursor created")

            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            logger.debug(f"🔧 [PostgreSQLClient.execute_query_raw] Query executed, cursor.description={cursor.description is not None}")

            if cursor.description:
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                logger.debug(f"🔧 [PostgreSQLClient.execute_query_raw] Fetched {len(rows)} rows with columns {columns}")
                if rows:
                    logger.debug(f"🔧 [PostgreSQLClient.execute_query_raw] First row: {rows[0]}")
            else:
                columns = []
                rows = []
                logger.warning("🔧 [PostgreSQLClient.execute_query_raw] cursor.description is None - no result set")

            conn.commit()
            logger.info(f"🔧 [PostgreSQLClient.execute_query_raw] Returning {len(columns)} columns, {len(rows)} rows")
            return (columns, rows)

        except Exception as e:
            broken = True
            try:
                conn.rollback()
                broken = bool(getattr(conn, "closed", 0))
            except Exception:
                broken = True
            logger.error(f"🔧 [PostgreSQLClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            self._return_conn(conn, broken=broken)
    
    def get_connection_info(self) -> Dict[str, Any]:
        """Get connection information."""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "sslmode": self.sslmode,
        }
    
    def __enter__(self) -> "PostgreSQLClient":
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()


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
        )


class AuthConfig(BaseModel):
    """Authentication configuration for PostgreSQL connector."""

    host: Optional[str] = Field(default=None, description="PostgreSQL server host")
    port: int = Field(default=5432, description="PostgreSQL server port")
    database: Optional[str] = Field(default=None, description="Database name")
    user: Optional[str] = Field(
        default=None,
        description="Username",
        validation_alias=AliasChoices("username", "user"),
    )
    password: str = Field(default="", description="Password")
    sslmode: str = Field(default="prefer", description="SSL mode")
    connection_string: Optional[str] = Field(
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
    
    def get_connection_info(self) -> Dict[str, Any]:
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
        connector_instance_id: Optional[str] = None,
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
        connector_instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
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
    data: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(default=None, description="Error message if failed")
    message: Optional[str] = Field(default=None, description="Additional message")
    
    class Config:
        """Pydantic configuration."""
        extra = "allow"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary."""
        return self.model_dump(exclude_none=True)
    
    def to_json(self) -> str:
        """Convert response to JSON string."""
        return self.model_dump_json(exclude_none=True)
