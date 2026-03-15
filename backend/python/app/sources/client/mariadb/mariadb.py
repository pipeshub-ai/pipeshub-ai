"""MariaDB client implementation.

This module provides clients for interacting with MariaDB databases using
the official MariaDB Connector/Python.

MariaDB Documentation: https://mariadb.com/docs/
MariaDB Connector/Python: https://mariadb.com/docs/server/connect/programming-languages/python/
"""

import logging
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, ValidationError

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
    ) -> None:
        if mariadb is None:
            raise ImportError(
                "mariadb is required for MariaDB client. "
                "Install with: pip install mariadb"
            )

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
        self._connection = None

        db_part = f"/{database}" if database else ""
        logger.info(
            f"🔧 [MariaDBClient] Initialized successfully for "
            f"{user}@{host}:{port}{db_part}"
        )

    def connect(self) -> "MariaDBClient":
        """Establish connection to MariaDB.

        Returns:
            Self for method chaining

        Raises:
            ConnectionError: If connection fails
        """
        if self._connection is not None:
            logger.debug("🔧 [MariaDBClient] Already connected")
            return self

        try:
            db_part = f"/{self.database}" if self.database else ""
            logger.debug(
                f"🔧 [MariaDBClient] Connecting to "
                f"{self.host}:{self.port}{db_part}"
            )

            connect_kwargs: Dict[str, Any] = {
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

            self._connection = mariadb.connect(**connect_kwargs)

            logger.info("🔧 [MariaDBClient] Successfully connected to MariaDB")
            return self

        except mariadb.Error as e:
            logger.error(f"🔧 [MariaDBClient] Connection failed: {e}")
            raise ConnectionError(f"Failed to connect to MariaDB: {e}") from e

    def close(self) -> None:
        """Close the MariaDB connection."""
        if self._connection is not None:
            try:
                self._connection.close()
                logger.info("🔧 [MariaDBClient] Connection closed")
            except mariadb.Error as e:
                logger.warning(
                    f"🔧 [MariaDBClient] Failed to close connection gracefully: {e}"
                )
            finally:
                self._connection = None

    def is_connected(self) -> bool:
        """Check if connection is active."""
        if self._connection is None:
            return False
        try:
            self._connection.ping()
            return True
        except mariadb.Error:
            return False

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
            cursor = self._connection.cursor(dictionary=True)
            cursor.execute(query, params or ())

            if cursor.description:
                results = cursor.fetchall()
            else:
                results = [{"affected_rows": cursor.rowcount}]

            self._connection.commit()
            cursor.close()

            return results

        except mariadb.Error as e:
            self._connection.rollback()
            logger.error(f"🔧 [MariaDBClient] Query execution failed: {e}")
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
        logger.debug(
            f"🔧 [MariaDBClient.execute_query_raw] Executing query: {query[:200]}..."
        )

        if not self.is_connected():
            self.connect()

        try:
            cursor = self._connection.cursor()
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

            self._connection.commit()
            cursor.close()

            return (columns, rows)

        except mariadb.Error as e:
            self._connection.rollback()
            logger.error(f"🔧 [MariaDBClient] Query execution failed: {e}")
            raise RuntimeError(f"Query execution failed: {e}") from e

    def get_connection_info(self) -> Dict[str, Any]:
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

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: Dict[str, Any],
        logger: logging.Logger,
    ) -> "MariaDBClient":
        """Build MariaDBClient from toolset configuration.

        Args:
            toolset_config: Toolset configuration from etcd.
            logger: Logger instance.

        Returns:
            MariaDBClient instance.
        """

        def pick_value(config: Dict[str, Any], *keys: str) -> Optional[Any]:
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
                    "MariaDB toolset is not authenticated. Missing toolset configuration."
                )

            host = pick_value(toolset_config, "host", "hostname")
            port = pick_value(toolset_config, "port")
            database = pick_value(toolset_config, "database", "db", "databaseName")
            user = pick_value(toolset_config, "user", "username")
            password = pick_value(toolset_config, "password")
            timeout = pick_value(toolset_config, "timeout", "connectTimeout")
            ssl_ca = pick_value(toolset_config, "ssl_ca", "sslCa", "caCert")
            charset = pick_value(toolset_config, "charset", "characterSet")

            if host is None:
                raise ValueError(
                    "MariaDB authentication config is missing required field: host"
                )
            if user is None:
                raise ValueError(
                    "MariaDB authentication config is missing required field: user"
                )

            # Password can be intentionally empty for some setups.
            password_value = "" if password is None else str(password)

            config = MariaDBConfig(
                host=str(host),
                port=int(port) if port is not None else 3306,
                database=str(database) if database not in (None, "") else None,
                user=str(user),
                password=password_value,
                timeout=int(timeout) if timeout is not None else 30,
                ssl_ca=str(ssl_ca) if ssl_ca not in (None, "") else None,
                charset=str(charset) if charset not in (None, "") else "utf8mb4",
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
    user: str = Field(..., description="Username for authentication")
    password: str = Field(default="", description="Password for authentication")
    timeout: int = Field(default=30, description="Connection timeout in seconds", gt=0)
    ssl_ca: Optional[str] = Field(
        default=None, description="Path to CA certificate for SSL"
    )
    charset: str = Field(default="utf8mb4", description="Character set")

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
        )


class AuthConfig(BaseModel):
    """Authentication configuration for MariaDB connector."""

    host: str = Field(..., description="MariaDB server host")
    port: int = Field(default=3306, description="MariaDB server port")
    database: Optional[str] = Field(default=None, description="Database name (optional)")
    user: str = Field(..., description="Username")
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

    def get_connection_info(self) -> Dict[str, Any]:
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
    ) -> Dict[str, Any]:
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
    data: Optional[Union[Dict[str, Any], List[Any]]] = Field(
        default=None, description="Response data"
    )
    error: Optional[str] = Field(default=None, description="Error message if failed")
    message: Optional[str] = Field(default=None, description="Additional message")

    class Config:
        extra = "allow"

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(exclude_none=True)

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)