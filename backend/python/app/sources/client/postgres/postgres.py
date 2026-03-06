"""PostgreSQL client implementation.

This module provides clients for interacting with PostgreSQL databases using:
1. Username/Password authentication
2. Connection string authentication

PostgreSQL Documentation: https://www.postgresql.org/docs/
psycopg2 Documentation: https://www.psycopg.org/docs/
"""

import logging
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, ValidationError

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient

logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None


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
        """
        if psycopg2 is None:
            raise ImportError(
                "psycopg2 is required for PostgreSQL client. "
                "Install with: pip install psycopg2-binary"
            )
        
        logger.debug(f"🔧 [PostgreSQLClient] Initializing with host={host}, port={port}, database={database}, user={user}")
        
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.timeout = timeout
        self.sslmode = sslmode
        self._connection = None
        
        logger.info(f"🔧 [PostgreSQLClient] Initialized successfully for {user}@{host}:{port}/{database}")
    
    def connect(self) -> "PostgreSQLClient":
        """Establish connection to PostgreSQL.
        
        Returns:
            Self for method chaining
            
        Raises:
            ConnectionError: If connection fails
        """
        if self._connection is not None and not self._connection.closed:
            logger.debug("🔧 [PostgreSQLClient] Already connected")
            return self
        
        try:
            logger.debug(f"🔧 [PostgreSQLClient] Connecting to {self.host}:{self.port}/{self.database}")
            
            self._connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                connect_timeout=self.timeout,
                sslmode=self.sslmode,
            )
            
            logger.info(f"🔧 [PostgreSQLClient] Successfully connected to PostgreSQL")
            return self
            
        except Exception as e:
            logger.error(f"🔧 [PostgreSQLClient] Connection failed: {e}")
            raise ConnectionError(f"Failed to connect to PostgreSQL: {e}") from e
    
    def close(self) -> None:
        """Close the PostgreSQL connection."""
        if self._connection is not None:
            try:
                self._connection.close()
                logger.info("🔧 [PostgreSQLClient] Connection closed")
            except Exception as e:
                logger.warning(f"🔧 [PostgreSQLClient] Failed to close connection gracefully: {e}")
            finally:
                self._connection = None
    
    def is_connected(self) -> bool:
        """Check if connection is active."""
        return self._connection is not None and not self._connection.closed
    
    def execute_query(
        self,
        query: str,
        params: Optional[Union[Dict[str, Any], List[Any], tuple]] = None,
    ) -> List[Dict[str, Any]]:
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
            cursor = self._connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            
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
            
            self._connection.commit()
            cursor.close()
            
            return results
            
        except Exception as e:
            self._connection.rollback()
            logger.error(f"🔧 [PostgreSQLClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e
    
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
            ConnectionError: If not connected
            RuntimeError: If query execution fails
        """
        logger.debug(f"🔧 [PostgreSQLClient.execute_query_raw] Executing query: {query[:200]}...")
        
        if not self.is_connected():
            logger.debug("🔧 [PostgreSQLClient.execute_query_raw] Not connected, connecting...")
            self.connect()
        
        try:
            cursor = self._connection.cursor()
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
            
            self._connection.commit()
            cursor.close()
            
            logger.info(f"🔧 [PostgreSQLClient.execute_query_raw] Returning {len(columns)} columns, {len(rows)} rows")
            return (columns, rows)
            
        except Exception as e:
            self._connection.rollback()
            logger.error(f"🔧 [PostgreSQLClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e
    
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
    user: str = Field(..., description="Username for authentication")
    password: str = Field(default="", description="Password for authentication")
    timeout: int = Field(default=30, description="Connection timeout in seconds", gt=0)
    sslmode: str = Field(
        default="prefer",
        description="SSL mode (disable, allow, prefer, require, verify-ca, verify-full)"
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
        )


class AuthConfig(BaseModel):
    """Authentication configuration for PostgreSQL connector."""
    
    host: str = Field(..., description="PostgreSQL server host")
    port: int = Field(default=5432, description="PostgreSQL server port")
    database: str = Field(..., description="Database name")
    user: str = Field(..., description="Username")
    password: str = Field(default="", description="Password")
    sslmode: str = Field(default="prefer", description="SSL mode")


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
            logger.debug(f"🔧 [PostgreSQLClientBuilder] Raw config_dict: {config_dict}")
            
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
