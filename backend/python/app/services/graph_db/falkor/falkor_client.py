"""
Falkor Async Client Wrapper

This module provides an async wrapper around the official Falkor Python driver,
handling connection pooling, transaction management, and query execution.
"""

import asyncio
from logging import Logger
from typing import TYPE_CHECKING, Any

from falkordb.asyncio import FalkorDB
import redis

if TYPE_CHECKING:
    from neo4j import AsyncSession


class FalkorClient:
    """Async client wrapper for Falkor driver"""

    def __init__(
        self,
        host: str,
        port: str,
        username: str,
        password: str,
        database: str,
        logger: Logger
    ) -> None:
        """
        Initialize Falkor client.

        Args:
            uri: Falkor connection URI (e.g., "bolt://localhost:7687" or "neo4j://localhost:7687")
            username: Database username
            password: Database password
            database: Database name (Falkor 4.0+)
            logger: Logger instance
        """
        # Assign logger first before using it
        self.logger = logger
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.driver: FalkorDB | None = None
        self._active_sessions: dict[str, Any] = {}  # Track active transaction sessions
        self._session_locks: dict[str, asyncio.Lock] = {}  # Lock per transaction to prevent concurrent access

        # Log connection details
        self.logger.info(f"🔌 Connecting to Falkor at {host}:{port}")
        self.logger.info(f"🔌 Username: {username}")
        self.logger.info(f"🔌 Database: {database}")


    async def connect(self) -> bool:
        """
        Create Falkor driver and test connection.
        If the specified database doesn't exist, it will be created automatically.

        Returns:
            bool: True if connection successful
        """
        try:
            self.driver = FalkorDB(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password
            )

            # Test connection
            self.driver.connection.ping()
            server_info = self.driver.connection.info()
            self.logger.info(f"✅ Connected to Falkor {server_info}")

            # Check if database exists and create if needed
            await self._ensure_database_exists()

            return True

        except redis.exceptions.RedisError as e:
            self.logger.error(f"❌ Failed to connect to Falkor: {str(e)}")
            return False

    async def _ensure_database_exists(self) -> None:
        """
        Check if the database exists, and create it if it doesn't.
        This method connects to the 'system' database to check and create databases.
        """
        try:
            graph = self.driver.select_graph(self.database)
            graph.query("RETURN 1")
        except Exception as e:
            raise RuntimeError(
                f"Cannot access graph '{self.database}'"
            ) from e

    async def disconnect(self) -> None:
        """Close Falkor driver and all sessions"""
        try:

            if self.driver:
                self.driver.connection.close()
                self.driver = None
                self.logger.info("✅ Disconnected from Falkor")
        except redis.exceptions.RedisError as e:
            self.logger.error(f"❌ Error disconnecting from Falkor: {str(e)}")

    async def begin_transaction(self, read: list[str], write: list[str]) -> str:
        """
        Begin a Falkor transaction session.

        Args:
            read: Collections to read from (for compatibility, not used in Falkor)
            write: Collections to write to (for compatibility, not used in Falkor)

        Returns:
            str: Transaction ID (session identifier)
        """
        pass

    async def commit_transaction(self, txn_id: str) -> None:
        """
        Commit a Falkor transaction.

        Args:
            txn_id: Transaction ID (session identifier)
        """
        pass

    async def abort_transaction(self, txn_id: str) -> None:
        """
        Abort (rollback) a Falkor transaction.

        Args:
            txn_id: Transaction ID (session identifier)
        """
        pass

    async def execute_query(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        txn_id: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute a Cypher query.

        Args:
            query: Cypher query string
            parameters: Query parameters
            txn_id: Optional transaction ID (if None, creates auto-commit transaction)

        Returns:
            List[Dict]: Query results as list of dictionaries
        """
        if not self.driver:
            await self.connect()
            if not self.driver:
                raise RuntimeError("Falkor driver not connected")

        parameters = parameters or {}

        graph = self.driver.select_graph(self.database)
        result = await graph.query(
            query,
            parameters
        )
        return result.result_set

    def get_session(self, txn_id: str) -> "AsyncSession":
        """
        Get the session for a transaction ID.

        Args:
            txn_id: Transaction ID

        Returns:
            Falkor session object
        """
        if txn_id not in self._active_sessions:
            raise ValueError(f"Transaction {txn_id} not found")
        return self._active_sessions[txn_id]

