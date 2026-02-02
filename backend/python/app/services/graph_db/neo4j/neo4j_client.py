"""
Neo4j Async Client Wrapper

This module provides an async wrapper around the official Neo4j Python driver,
handling connection pooling, transaction management, and query execution.
"""

from logging import Logger
from typing import Any, Dict, List, Optional

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable


class Neo4jClient:
    """Async client wrapper for Neo4j driver"""

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str,
        logger: Logger
    ) -> None:
        """
        Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI (e.g., "bolt://localhost:7687" or "neo4j://localhost:7687")
            username: Database username
            password: Database password
            database: Database name (Neo4j 4.0+)
            logger: Logger instance
        """
        # Assign logger first before using it
        self.logger = logger
        self.uri = uri
        self.username = username
        self.password = password
        self.database = database
        self.driver: Optional[Any] = None
        self._active_sessions: Dict[str, Any] = {}  # Track active transaction sessions

        # Log connection details
        self.logger.info(f"🔌 Connecting to Neo4j at {uri}")
        self.logger.info(f"🔌 Username: {username}")
        self.logger.info(f"🔌 Database: {database}")
        # self.logger.info(f"🔌 Password: {password}")


    async def connect(self) -> bool:
        """
        Create Neo4j driver and test connection.

        Returns:
            bool: True if connection successful
        """
        try:
            self.driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password)
            )

            # Test connection
            await self.driver.verify_connectivity()
            server_info = await self.driver.get_server_info()
            self.logger.info(f"✅ Connected to Neo4j {server_info}")
            return True

        except ServiceUnavailable as e:
            self.logger.error(f"❌ Failed to connect to Neo4j: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"❌ Failed to connect to Neo4j: {str(e)}")
            return False

    async def disconnect(self) -> None:
        """Close Neo4j driver and all sessions"""
        try:
            # Close all active sessions
            for txn_id, session in self._active_sessions.items():
                try:
                    await session.close()
                except Exception as e:
                    self.logger.warning(f"Error closing session {txn_id}: {str(e)}")
            self._active_sessions.clear()

            if self.driver:
                await self.driver.close()
                self.driver = None
                self.logger.info("✅ Disconnected from Neo4j")
        except Exception as e:
            self.logger.error(f"❌ Error disconnecting from Neo4j: {str(e)}")

    async def begin_transaction(self, read: List[str], write: List[str]) -> str:
        """
        Begin a Neo4j transaction session.

        Args:
            read: Collections to read from (for compatibility, not used in Neo4j)
            write: Collections to write to (for compatibility, not used in Neo4j)

        Returns:
            str: Transaction ID (session identifier)
        """
        import uuid

        if not self.driver:
            raise RuntimeError("Neo4j driver not connected")

        # Create a new session for this transaction
        session = self.driver.session(database=self.database)
        txn_id = str(uuid.uuid4())
        self._active_sessions[txn_id] = session

        self.logger.debug(f"🔵 Started Neo4j transaction: {txn_id}")
        return txn_id

    async def commit_transaction(self, txn_id: str) -> None:
        """
        Commit a Neo4j transaction.

        Args:
            txn_id: Transaction ID (session identifier)
        """
        if txn_id not in self._active_sessions:
            raise ValueError(f"Transaction {txn_id} not found")

        session = self._active_sessions[txn_id]
        try:
            await session.close()
            self.logger.debug(f"✅ Committed Neo4j transaction: {txn_id}")
        finally:
            del self._active_sessions[txn_id]

    async def abort_transaction(self, txn_id: str) -> None:
        """
        Abort (rollback) a Neo4j transaction.

        Args:
            txn_id: Transaction ID (session identifier)
        """
        if txn_id not in self._active_sessions:
            raise ValueError(f"Transaction {txn_id} not found")

        session = self._active_sessions[txn_id]
        try:
            await session.close()
            self.logger.debug(f"🔄 Aborted Neo4j transaction: {txn_id}")
        finally:
            del self._active_sessions[txn_id]

    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        txn_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
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
            raise RuntimeError("Neo4j driver not connected")

        parameters = parameters or {}

        if txn_id:
            # Use existing transaction session
            if txn_id not in self._active_sessions:
                raise ValueError(f"Transaction {txn_id} not found")

            session = self._active_sessions[txn_id]
            result = await session.run(query, parameters)
            records = await result.data()
            return records
        else:
            # Auto-commit transaction
            async with self.driver.session(database=self.database) as session:
                result = await session.run(query, parameters)
                records = await result.data()
                return records

    def get_session(self, txn_id: str) -> Any:
        """
        Get the session for a transaction ID.

        Args:
            txn_id: Transaction ID

        Returns:
            Neo4j session object
        """
        if txn_id not in self._active_sessions:
            raise ValueError(f"Transaction {txn_id} not found")
        return self._active_sessions[txn_id]

