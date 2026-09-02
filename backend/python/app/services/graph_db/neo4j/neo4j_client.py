"""
Neo4j Async Client Wrapper

This module provides an async wrapper around the official Neo4j Python driver,
handling connection pooling, transaction management, and query execution.
"""

import asyncio
import threading
from logging import Logger
from typing import TYPE_CHECKING, Any

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ClientError, ServiceUnavailable, SessionExpired

if TYPE_CHECKING:
    from neo4j import AsyncSession


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
        # A Neo4j driver's connection pool binds to the loop that created it,
        # so one driver cannot be shared across loops — the indexing service
        # builds this client on the main loop and runs its pipeline on a worker
        # loop. Keyed by loop, the same way QdrantService keeps its clients.
        self._drivers: dict[Any, Any] = {}
        self._driver_override: Any | None = None
        self._connect_locks: dict[Any, asyncio.Lock] = {}
        # Loops live in different threads, so the maps above are guarded by a
        # threading lock, not an asyncio one.
        self._drivers_lock = threading.Lock()
        self._active_sessions: dict[str, Any] = {}  # Track active transaction sessions
        self._session_locks: dict[str, asyncio.Lock] = {}  # Lock per transaction to prevent concurrent access
        self._session_loops: dict[str, Any] = {}  # Loop each session was opened on

        # Log connection details
        self.logger.info(f"🔌 Connecting to Neo4j at {uri}")
        self.logger.info(f"🔌 Username: {username}")
        self.logger.info(f"🔌 Database: {database}")


    @staticmethod
    def _current_loop() -> Any:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    @property
    def driver(self) -> Any | None:
        """The driver bound to the running loop, or None if not connected here."""
        if self._driver_override is not None:
            return self._driver_override
        return self._drivers.get(self._current_loop())

    @driver.setter
    def driver(self, value: Any | None) -> None:
        """Assigning a driver serves it to every loop (tests, legacy callers);
        assigning None clears that and drops this loop's own driver."""
        if value is None:
            self._driver_override = None
            with self._drivers_lock:
                self._drivers.pop(self._current_loop(), None)
        else:
            self._driver_override = value

    def _connect_lock_for_current_loop(self) -> asyncio.Lock:
        """One lock per loop: an asyncio.Lock binds to the loop that first
        contends for it, so a shared one would raise on the second loop."""
        loop = self._current_loop()
        with self._drivers_lock:
            lock = self._connect_locks.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self._connect_locks[loop] = lock
            return lock

    async def connect(self) -> bool:
        """
        Create Neo4j driver and test connection.
        If the specified database doesn't exist, it will be created automatically.

        Returns:
            bool: True if connection successful
        """
        async with self._connect_lock_for_current_loop():
            # Double-checked: another coroutine may have connected while we waited
            if self.driver is not None:
                return True
            return await self._connect_inner()

    async def _connect_inner(self) -> bool:
        """Create driver and verify connectivity.

        Must be called with this loop's connect lock already held to avoid
        deadlocks.
        """
        # Bound before the try: if the constructor itself raises, the handlers
        # below still have something to hand _close_driver_safely, which would
        # otherwise raise UnboundLocalError instead of returning False.
        driver = None
        try:
            driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=(self.username, self.password),
                keep_alive=True,
                max_connection_lifetime=30 * 60,  # 30 min — recycle before going stale
                max_connection_pool_size=100,
                connection_acquisition_timeout=60,  # wait up to 60s for pool slot under pressure
                liveness_check_timeout=30,  # verify connection health before reuse from pool
            )
            with self._drivers_lock:
                self._drivers[self._current_loop()] = driver

            # Test connection
            await driver.verify_connectivity()
            server_info = await driver.get_server_info()
            self.logger.info(f"✅ Connected to Neo4j {server_info}")

            # Check if database exists and create if needed
            await self._ensure_database_exists()

            return True

        except ServiceUnavailable as e:
            self.logger.error(f"❌ Failed to connect to Neo4j: {str(e)}")
            await self._close_driver_safely(driver)
            return False
        except ClientError as e:
            self.logger.error(f"❌ Failed to connect to Neo4j: {str(e)}")
            await self._close_driver_safely(driver)
            return False
        except Exception as e:
            self.logger.error(f"❌ Unexpected error connecting to Neo4j: {str(e)}")
            await self._close_driver_safely(driver)
            return False

    async def _close_driver_safely(self, failed_driver: Any = None) -> None:
        """Close the driver if it exists and reset to None.

        If failed_driver is provided, only close self.driver when it is the
        exact same instance — prevents a concurrent coroutine from closing a
        freshly created driver after reconnection.
        """
        loop = self._current_loop()
        target = failed_driver if failed_driver is not None else self._drivers.get(loop)
        if target is not None and self._drivers.get(loop) is target:
            try:
                await target.close()
            except Exception:
                pass
            with self._drivers_lock:
                self._drivers.pop(loop, None)

    async def _ensure_database_exists(self) -> None:
        """
        Check if the database exists, and create it if it doesn't.
        This method connects to the 'system' database to check and create databases.
        """
        try:
            # Connect to system database to check if our target database exists
            async with self.driver.session(database="system") as session:
                # Query to check if database exists
                result = await session.run(
                    "SHOW DATABASES WHERE name = $dbName",
                    {"dbName": self.database}
                )
                databases = await result.data()

                if not databases:
                    # Database doesn't exist, create it
                    self.logger.info(f"📦 Database '{self.database}' not found. Creating it...")
                    await session.run(f"CREATE DATABASE `{self.database}` IF NOT EXISTS")
                    self.logger.info(f"✅ Database '{self.database}' created successfully")
                else:
                    self.logger.info(f"✅ Database '{self.database}' already exists")

        except ClientError as e:
            self.logger.warning(f"⚠️ Could not verify/create database '{self.database}': {str(e)}")
            self.logger.warning("This may be expected if using Neo4j Community Edition (single database only)")

    async def _close_on_owning_loop(self, resource: Any, owner: Any, what: str) -> bool:
        """Close a loop-bound resource on the loop that created it.

        Closing a driver or session from a foreign loop raises "attached to a
        different loop" and abandons the pool rather than releasing it. The
        owning loop is known here — it is the key this resource was stored
        under — so hand the close back to it while it is still running.

        Once that loop has stopped there is no thread left to run the close on
        and the pool dies with it, so the reference is dropped either way:
        keeping it would leave a dead driver in the map for `connect()` to hand
        back to the next caller.
        """
        # Resolved before the try: deciding *where* to close must not be able
        # to fail in a way that skips the close itself.
        owner_loop = owner if isinstance(owner, asyncio.AbstractEventLoop) else None
        delegate = (
            owner_loop is not None
            and owner_loop is not self._current_loop()
            and owner_loop.is_running()
        )
        try:
            if delegate:
                await asyncio.wrap_future(
                    asyncio.run_coroutine_threadsafe(resource.close(), owner_loop)
                )
            else:
                await resource.close()
            return True
        except Exception as e:
            self.logger.warning("Error closing %s: %s", what, e)
            return False

    def _close_could_be_retried(self, owner: Any) -> bool:
        """Whether a failed close on this resource is worth keeping around.

        Only if something could still run the close later. A loop that has
        stopped will never run another coroutine, so its driver's pool is gone
        with it and the entry is pure leak — worse, it would sit in `_drivers`
        for `connect()` to hand back on a loop that can no longer serve it.

        An owner that is not a loop at all (the explicit-assignment override,
        or a test double) closes on the caller's loop, which is running by
        definition, so a retry stays possible.
        """
        if not isinstance(owner, asyncio.AbstractEventLoop):
            return True
        return not owner.is_closed() and owner.is_running()

    async def disconnect(self) -> None:
        """Close Neo4j driver and all sessions"""
        try:
            # Forget a resource only once it is actually released. Clearing
            # first would drop the only reference to a pool that is still open,
            # leaving nothing to retry with and no way to see it again.
            # Sessions are loop-bound too, so each closes on the loop that
            # opened it (recorded at begin_transaction).
            for txn_id, session in list(self._active_sessions.items()):
                owner = self._session_loops.get(txn_id)
                closed = await self._close_on_owning_loop(
                    session, owner, f"session {txn_id}"
                )
                if not closed and self._close_could_be_retried(owner):
                    continue
                self._active_sessions.pop(txn_id, None)
                self._session_locks.pop(txn_id, None)
                self._session_loops.pop(txn_id, None)

            with self._drivers_lock:
                owned = list(self._drivers.items())
            override = self._driver_override
            if override is not None:
                owned.append((None, override))

            for owner, driver in owned:
                closed = await self._close_on_owning_loop(driver, owner, "a Neo4j driver")
                if not closed:
                    if self._close_could_be_retried(owner):
                        # Still closable later: keeping it is the only way that
                        # retry can ever happen, and the lock goes with it so a
                        # later connect() on that loop still serialises.
                        continue
                    self.logger.warning(
                        "Discarding a Neo4j driver whose event loop has stopped; "
                        "its pool cannot be closed from anywhere now"
                    )
                if driver is override:
                    self._driver_override = None
                    continue
                with self._drivers_lock:
                    self._drivers.pop(owner, None)
                    self._connect_locks.pop(owner, None)
            if owned:
                self.logger.info("✅ Disconnected from Neo4j")
        except (ClientError, ServiceUnavailable) as e:
            self.logger.error(f"❌ Error disconnecting from Neo4j: {str(e)}")

    async def begin_transaction(self, read: list[str], write: list[str]) -> str:
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
            await self.connect()
            if not self.driver:
                raise RuntimeError("Neo4j driver not connected")

        # Create a new session for this transaction
        session = self.driver.session(database=self.database)
        txn_id = str(uuid.uuid4())
        self._active_sessions[txn_id] = session
        self._session_locks[txn_id] = asyncio.Lock()  # Create lock for this transaction
        self._session_loops[txn_id] = self._current_loop()

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
            if txn_id in self._session_locks:
                del self._session_locks[txn_id]
            self._session_loops.pop(txn_id, None)

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
            if txn_id in self._session_locks:
                del self._session_locks[txn_id]
            self._session_loops.pop(txn_id, None)

    async def execute_query(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        txn_id: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute a Cypher query with automatic reconnection on transient failures.

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
                raise RuntimeError("Neo4j driver not connected")

        parameters = parameters or {}

        if txn_id:
            # Use existing transaction session with lock to prevent concurrent access
            if txn_id not in self._active_sessions:
                raise ValueError(f"Transaction {txn_id} not found")

            session = self._active_sessions[txn_id]
            lock = self._session_locks.get(txn_id)

            if lock:
                # Serialize access to the session to prevent concurrent operations
                async with lock:
                    result = await session.run(query, parameters)
                    return await result.data()
            else:
                # Fallback if lock doesn't exist (shouldn't happen)
                result = await session.run(query, parameters)
                return await result.data()
        else:
            # Auto-commit transaction with reconnection on transient failure.
            # The driver's liveness_check_timeout catches most stale connections,
            # but a race (connection dies between check and use) can still occur.
            try:
                async with self.driver.session(database=self.database) as session:
                    result = await session.run(query, parameters)
                    return await result.data()
            except (ServiceUnavailable, SessionExpired) as e:
                stale_driver = self.driver  # capture identity before acquiring lock
                self.logger.warning(
                    "Neo4j connection lost during query — reconnecting: %s", e
                )
                # Serialize reconnection so concurrent failures don't spawn duplicate drivers.
                # Using _connect_inner() directly to avoid deadlocking on the lock.
                async with self._connect_lock_for_current_loop():
                    if self.driver is not stale_driver:
                        # Another coroutine already replaced the driver; skip reconnect.
                        pass
                    else:
                        await self._close_driver_safely(stale_driver)
                        connected = await self._connect_inner()
                        if not connected:
                            raise RuntimeError("Neo4j reconnection failed") from e
                # Retry the query once after reconnecting
                async with self.driver.session(database=self.database) as session:
                    result = await session.run(query, parameters)
                    return await result.data()

    def get_session(self, txn_id: str) -> "AsyncSession":
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

