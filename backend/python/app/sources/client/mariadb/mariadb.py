"""MariaDB client implementation.

This module provides clients for interacting with MariaDB databases using
MariaDB Connector/Python 2.x async connections and pools.

MariaDB Documentation: https://mariadb.com/docs/
MariaDB Connector/Python: https://mariadb.com/docs/connectors/mariadb-connector-python/
"""

import asyncio
import contextlib
import logging
import os
import tempfile
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, ValidationError

from app.config.configuration_service import ConfigurationService
from app.core.org_context import spawn_in_request
from app.sources.client.iclient import IClient
from app.utils.url_fetcher import HostCheckError, vetted_addresses

logger = logging.getLogger(__name__)
# Seconds a read-only agent query may run before the server stops it.
READ_ONLY_TIMEOUT_S = 120

_DEFAULT_MAX_POOL_SIZE = 10
# The async driver has no read timeout: a stalled server is bounded on our side,
# this long after the server's own statement limit should have fired.
READ_TIMEOUT_MARGIN_S = 5.0
# Pooled reads (catalog, connector metadata, the agent toolset) have no
# statement limit of their own; this bounds a stalled server.
POOL_READ_TIMEOUT_S = 300
_ER_UNKNOWN_SYSTEM_VARIABLE = 1193


class NoResultSetError(ValueError):
    """A read-only query ran a statement that returns no rows."""


class QueryTimeoutError(TimeoutError):
    """No answer within the query's time limit; the connection was closed."""

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
        ssl_ca_pem: CA bundle as PEM text (optional); written to a private
            temp file for the driver, which takes only a path
        charset: Character set (default: utf8mb4)

    With a CA, TLS is on with certificate and hostname checks. Every connect
    vets the host first: without TLS the vetted address is dialled;
    with TLS the name is, so the certificate can be checked against it.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        database: str | None = None,
        port: int = 3306,
        timeout: int = 30,
        ssl_ca: str | None = None,
        charset: str = "utf8mb4",
        pool_acquire_timeout: float = 60.0,
        ssl_ca_pem: str | None = None,
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
        self.ssl_ca_pem = ssl_ca_pem
        self._ca_file: str | None = None
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

                connect_kwargs = await self._connect_kwargs()
                # The pool swallows connect errors and then retries forever; one
                # connection first, in a thread (the binary driver's connect blocks),
                # surfaces a wrong password or an unreachable host as an error.
                await asyncio.to_thread(_open_and_close, connect_kwargs)

                self._pool = await mariadb.create_async_pool(
                    min_size=1,
                    max_size=self._max_pool_size,
                    acquire_timeout=self.pool_acquire_timeout,
                    reset_connection=True,
                    # A stalled server fails the read instead of hanging the caller.
                    read_timeout=POOL_READ_TIMEOUT_S,
                    write_timeout=POOL_READ_TIMEOUT_S,
                    **connect_kwargs,
                )

                logger.info("🔧 [MariaDBClient] MariaDB async connection pool ready")
                return self

            except HostCheckError as e:
                self._remove_ca_file()
                # As other clients report it: a connection error, the address check as its cause.
                raise ConnectionError(str(e)) from e
            except Exception as e:
                self._remove_ca_file()
                logger.error(f"🔧 [MariaDBClient] Pool initialization failed: {e}")
                raise ConnectionError(f"Failed to connect to MariaDB: {e}") from e

    async def _connect_kwargs(self) -> dict[str, Any]:
        """The driver's connect arguments: the vetted address, and TLS when a CA is given.

        Only arguments the binary driver accepts: it raises TypeError on any
        other (``charset`` among them; utf8mb4 is its default).
        Raises HostCheckError before anything is dialled when the host is refused.
        """
        addresses = await vetted_addresses(self.host, lookup_timeout_s=float(self.timeout))
        ca_file = self.ssl_ca or self._write_ca_file()
        kwargs: dict[str, Any] = {
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "connect_timeout": self.timeout,
        }
        if self.host.startswith("/"):
            kwargs["unix_socket"] = self.host
        elif ca_file or not addresses:
            # With TLS the certificate names the host, so the name is dialled.
            kwargs["host"] = self.host
        else:
            ipv4 = [a for a in addresses if a.version == 4]
            kwargs["host"] = str((ipv4 or addresses)[0])
        if self.database:
            kwargs["database"] = self.database
        if ca_file:
            kwargs.update(ssl=True, ssl_verify_cert=True, ssl_ca=ca_file)
        return kwargs

    def _write_ca_file(self) -> str | None:
        if not self.ssl_ca_pem or not self.ssl_ca_pem.strip():
            return None
        if self._ca_file is None:
            fd, path = tempfile.mkstemp(prefix="mariadb-ca-", suffix=".pem")
            with os.fdopen(fd, "w") as f:  # mkstemp creates it 0600
                f.write(self.ssl_ca_pem.strip() + "\n")
            self._ca_file = path
        return self._ca_file

    def _remove_ca_file(self) -> None:
        if self._ca_file is not None:
            with contextlib.suppress(OSError):
                os.remove(self._ca_file)
            self._ca_file = None

    async def close(self) -> None:
        """Close all connections in the pool."""
        if self._pool is None:
            self._remove_ca_file()
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
        finally:
            self._remove_ca_file()

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
        params: dict[str, Any] | list[Any] | tuple | None = None,
        *,
        read_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts.

        ``read_only`` runs it in a READ ONLY transaction with a statement timeout, so even a
        stored function it calls cannot write or hold the server."""
        if not self.is_connected():
            await self.connect()
        if self._pool is None:
            raise RuntimeError("MariaDB pool not initialized")

        try:
            async with self._pool.connection() as conn:
                cursor = None
                try:
                    cursor = conn.cursor(dictionary=True)
                    if read_only:
                        await cursor.execute("START TRANSACTION READ ONLY")
                        # A server that doesn't know SET STATEMENT (older MariaDB, MySQL) rejects it: the
                        # error propagates and the query is not retried without the guard.
                        query = f"SET STATEMENT max_statement_time={READ_ONLY_TIMEOUT_S} FOR {query}"
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
            # Re-raised as-is: the driver's errno/sqlstate is what the streaming
            # path maps to a status, and a RuntimeError wrapper destroys it.
            logger.error(f"🔧 [MariaDBClient] Query execution failed: {e}")
            raise

    async def execute_query_raw(
        self,
        query: str,
        params: dict[str, Any] | list[Any] | tuple | None = None,
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
            raise

    async def execute_read_only_query(
        self,
        query: str,
        timeout_s: float,
        *,
        max_rows: int | None = None,
        params: tuple[Any, ...] | None = None,
        slots: asyncio.Semaphore | None = None,
    ) -> tuple[list[str], list[tuple], bool]:
        """Run one reading statement in a read-only transaction, bounded in time.

        On a connection of its own, in a worker thread: the binary driver's
        connect blocks, and a pooled connection abandoned mid-answer would be
        reset (and hang) or handed back out of step. The session and the
        transaction are read-only; the server stops the statement after
        ``timeout_s`` (``max_statement_time``; MySQL's ``MAX_EXECUTION_TIME``
        on a MySQL server); the driver's read and write timeouts end a stalled
        exchange; the transaction is rolled back whatever happened. At most
        ``max_rows`` rows are read; the third value says whether more existed.
        ``params`` are bound to ``?`` placeholders. A slot of ``slots`` is held
        until the worker thread ends, which is after a timeout here.

        Raises:
            NoResultSetError: the statement returns no rows (refused after it
                ran; the rollback undoes it).
            QueryTimeoutError: no answer in time.
        """
        kwargs = await self._connect_kwargs()
        socket_limit = int(timeout_s + READ_TIMEOUT_MARGIN_S)
        kwargs.update(read_timeout=socket_limit, write_timeout=socket_limit)
        if slots is not None:
            await slots.acquire()
        try:
            work = spawn_in_request(asyncio.to_thread(_read_only, kwargs, query, timeout_s, max_rows, params))
        except BaseException:
            if slots is not None:
                slots.release()
            raise

        def finished(future: asyncio.Future) -> None:
            if slots is not None:
                slots.release()
            if not future.cancelled():
                future.exception()  # read, so an abandoned failure is not reported as unretrieved

        work.add_done_callback(finished)
        try:
            return await asyncio.wait_for(
                asyncio.shield(work),
                # Connect, statement and the driver's own read timeout, with room.
                self.timeout + socket_limit + READ_TIMEOUT_MARGIN_S,
            )
        except asyncio.TimeoutError:
            # The thread ends on its own once the driver's read timeout fires.
            raise QueryTimeoutError(f"MariaDB did not answer within {timeout_s:g} seconds") from None

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

        from app.edition_config import get_toolset_by_id
        mariadb_instance = await get_toolset_by_id(instance_id, config_service)
        if not isinstance(mariadb_instance, dict):
            raise ValueError(
                f"MariaDB toolset instance '{instance_id}' not found or inaccessible. "
                f"Please verify the instance exists and is properly configured."
            )

        def pick_value(config: dict[str, Any], *keys: str) -> Any | None:
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


def _open_and_close(kwargs: dict[str, Any]) -> None:
    mariadb.connect(**kwargs).close()


def _read_only(
    kwargs: dict[str, Any],
    query: str,
    timeout_s: float,
    max_rows: int | None = None,
    params: tuple[Any, ...] | None = None,
) -> tuple[list[str], list[tuple], bool]:
    """The read-only exchange of ``execute_read_only_query``, on the blocking driver."""
    conn = mariadb.connect(**kwargs)
    try:
        cursor = conn.cursor()
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        try:
            cursor.execute(f"SET SESSION max_statement_time = {float(timeout_s):g}")
        except Exception as e:
            if getattr(e, "errno", None) != _ER_UNKNOWN_SYSTEM_VARIABLE:
                raise
            cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {int(timeout_s * 1000)}")
        cursor.execute("START TRANSACTION READ ONLY")
        try:
            cursor.execute(query, params) if params else cursor.execute(query)
            if not cursor.description:
                raise NoResultSetError("Only statements that return rows can run here.")
            columns = [desc[0] for desc in cursor.description]
            if max_rows is None:
                return columns, cursor.fetchall(), False
            rows = cursor.fetchmany(max_rows + 1)
            return columns, rows[:max_rows], len(rows) > max_rows
        finally:
            with contextlib.suppress(Exception):
                conn.rollback()
    finally:
        with contextlib.suppress(Exception):
            conn.close()


class MariaDBConfig(BaseModel):
    """Configuration for MariaDB client."""

    host: str = Field(..., description="MariaDB server host")
    port: int = Field(default=3306, description="MariaDB server port", ge=1, le=65535)
    database: str | None = Field(default=None, description="Database name to connect to (optional)")
    user: str = Field(
        ...,
        description="Username for authentication",
        validation_alias=AliasChoices("username", "user"),
    )
    password: str = Field(default="", description="Password for authentication")
    timeout: int = Field(default=30, description="Connection timeout in seconds", gt=0)
    ssl_ca: str | None = Field(
        default=None, description="Path to CA certificate for SSL"
    )
    ssl_ca_pem: str | None = Field(default=None, description="CA bundle as PEM text; turns on verified TLS")
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
            ssl_ca_pem=self.ssl_ca_pem,
        )


class AuthConfig(BaseModel):
    """Authentication configuration for MariaDB connector."""

    host: str = Field(..., description="MariaDB server host")
    port: int = Field(default=3306, description="MariaDB server port")
    database: str | None = Field(default=None, description="Database name (optional)")
    user: str = Field(
        ...,
        description="Username",
        validation_alias=AliasChoices("username", "user"),
    )
    password: str = Field(default="", description="Password")
    ssl_ca: str | None = Field(default=None, description="Path to CA certificate")
    ssl_ca_pem: str | None = Field(
        default=None, description="CA bundle as PEM text (the form's sslCa)",
        validation_alias=AliasChoices("sslCa", "ssl_ca_pem"),
    )


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
        connector_instance_id: str | None = None,
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
                ssl_ca_pem=config.auth.ssl_ca_pem or None,
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
        connector_instance_id: str | None = None,
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
    data: dict[str, Any] | list[Any] | None = Field(
        default=None, description="Response data"
    )
    error: str | None = Field(default=None, description="Error message if failed")
    message: str | None = Field(default=None, description="Additional message")

    class Config:
        extra = "allow"

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)

    def to_json(self) -> str:
        return self.model_dump_json(exclude_none=True)
