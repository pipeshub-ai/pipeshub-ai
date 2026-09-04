"""
Neo4j Async Client Wrapper

This module provides an async wrapper around the official Neo4j Python driver,
handling connection pooling, transaction management, and query execution.
"""

import asyncio
import threading
import time
from logging import Logger
from typing import TYPE_CHECKING, Any

from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ClientError, ServiceUnavailable, SessionExpired

from app.services.resource_governor.feedback import get_default_downstream_feedback

if TYPE_CHECKING:
    from neo4j import AsyncSession


DEFAULT_MAX_CONNECTION_POOL_SIZE = 100
# Pool wait, not query time. Was 60: a stalled pool then held every query
# for a minute before failing, which is how one leaked session per record
# turned into 1800s record timeouts instead of retryable errors.
DEFAULT_CONNECTION_ACQUISITION_TIMEOUT = 30.0
DEFAULT_MAX_CONNECTION_LIFETIME = 30 * 60.0
DEFAULT_LIVENESS_CHECK_TIMEOUT = 30.0
# Longer than any record is allowed to process (RECORD_PROCESSING_TIMEOUT,
# 1800s by default) plus a margin: a session older than this belongs to a
# caller that will never commit or abort it.
DEFAULT_STALE_SESSION_MAX_AGE = 1800.0 + 300.0
DEFAULT_REBUILD_COOLDOWN = 30.0
# Server-side bound on one explicit transaction. Shorter than the record
# budget on purpose: a transaction is one graph write at the end of a record,
# not the whole record.
DEFAULT_TRANSACTION_TIMEOUT = 120.0
# The driver's message when connection_acquisition_timeout elapses with every
# pooled connection checked out. A ClientError, not ServiceUnavailable: the
# server is fine, this process has too many queries in flight for its pool.
_POOL_EXHAUSTED_MARKER = "failed to obtain a connection from the pool"


def _report_neo4j_failure(error: BaseException) -> None:
    feedback = get_default_downstream_feedback()
    if isinstance(error, ClientError) and _POOL_EXHAUSTED_MARKER in str(error):
        feedback.report_pool_exhausted("neo4j")
    elif isinstance(error, (ServiceUnavailable, SessionExpired)):
        feedback.report_unavailable("neo4j")


class Neo4jClient:
    """Async client wrapper for Neo4j driver"""

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str,
        logger: Logger,
        *,
        max_connection_pool_size: int = DEFAULT_MAX_CONNECTION_POOL_SIZE,
        connection_acquisition_timeout: float = DEFAULT_CONNECTION_ACQUISITION_TIMEOUT,
        max_connection_lifetime: float = DEFAULT_MAX_CONNECTION_LIFETIME,
        liveness_check_timeout: float = DEFAULT_LIVENESS_CHECK_TIMEOUT,
        stale_session_max_age: float = DEFAULT_STALE_SESSION_MAX_AGE,
        rebuild_cooldown: float = DEFAULT_REBUILD_COOLDOWN,
        explicit_transactions: bool = False,
        transaction_timeout: float = DEFAULT_TRANSACTION_TIMEOUT,
    ) -> None:
        """
        Initialize Neo4j client.

        Args:
            uri: Neo4j connection URI (e.g., "bolt://localhost:7687" or "neo4j://localhost:7687")
            username: Database username
            password: Database password
            database: Database name (Neo4j 4.0+)
            logger: Logger instance
            max_connection_pool_size: driver pool size per event loop
            connection_acquisition_timeout: seconds a query waits for a pooled
                connection. This is pool wait, not query time: failing fast
                turns a stalled pool into one retryable error instead of a
                record that sits on its timeout budget.
            max_connection_lifetime: seconds before a pooled connection is recycled
            liveness_check_timeout: idle seconds after which a pooled connection
                is verified before reuse
            stale_session_max_age: seconds after which a transaction session
                nobody committed or aborted is closed by ``_reap_stale_sessions``
            rebuild_cooldown: minimum seconds between two rebuilds of one
                loop's driver
            explicit_transactions: open a real Neo4j transaction per
                ``begin_transaction`` so ``abort_transaction`` rolls back.
                Off by default: each query in a "transaction" is then its own
                auto-commit, as it has always been, and an abort only closes
                the session. Explicit transactions hold node locks until
                commit, which changes contention on shared nodes.
            transaction_timeout: server-side timeout for an explicit transaction
        """
        # Assign logger first before using it
        self.logger = logger
        self.uri = uri
        self.username = username
        self.password = password
        self.database = database
        self._max_connection_pool_size = max(1, int(max_connection_pool_size))
        self._connection_acquisition_timeout = float(connection_acquisition_timeout)
        self._max_connection_lifetime = float(max_connection_lifetime)
        self._liveness_check_timeout = float(liveness_check_timeout)
        self._stale_session_max_age = float(stale_session_max_age)
        self._rebuild_cooldown = float(rebuild_cooldown)
        self._explicit_transactions = bool(explicit_transactions)
        self._transaction_timeout = float(transaction_timeout)
        # Populated on the first successful connect. Rebuilding a driver must
        # not re-run SHOW/CREATE DATABASE against the system database: the
        # database has not gone anywhere, and a rebuild happens exactly when
        # the server is least able to answer.
        self._database_ensured = False
        self._rebuilt_at: dict[Any, float] = {}
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
        self._session_started: dict[str, float] = {}  # monotonic start, for the stale reaper
        self._active_txs: dict[str, Any] = {}  # explicit transactions, when enabled

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
                max_connection_lifetime=self._max_connection_lifetime,
                max_connection_pool_size=self._max_connection_pool_size,
                connection_acquisition_timeout=self._connection_acquisition_timeout,
                liveness_check_timeout=self._liveness_check_timeout,
            )
            with self._drivers_lock:
                self._drivers[self._current_loop()] = driver

            # Test connection
            await driver.verify_connectivity()
            server_info = await driver.get_server_info()
            self.logger.info(f"✅ Connected to Neo4j {server_info}")

            if not self._database_ensured:
                await self._ensure_database_exists()
                self._database_ensured = True

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
                self._forget_session(txn_id)

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

        # Cheap enough to run inline: it only walks the map once it is
        # holding more than half the pool, which is already a sign that
        # sessions are outliving their callers.
        if len(self._active_sessions) > self._max_connection_pool_size // 2:
            await self._reap_stale_sessions()

        # Create a new session for this transaction
        session = self.driver.session(database=self.database)
        txn_id = str(uuid.uuid4())
        if self._explicit_transactions:
            try:
                tx = await session.begin_transaction(timeout=self._transaction_timeout)
            except BaseException:
                await session.close()
                raise
            self._active_txs[txn_id] = tx
        self._active_sessions[txn_id] = session
        self._session_locks[txn_id] = asyncio.Lock()  # Create lock for this transaction
        self._session_loops[txn_id] = self._current_loop()
        self._session_started[txn_id] = time.monotonic()

        self.logger.debug(f"🔵 Started Neo4j transaction: {txn_id}")
        return txn_id

    @property
    def explicit_transactions(self) -> bool:
        return self._explicit_transactions

    def _forget_session(self, txn_id: str) -> None:
        self._active_sessions.pop(txn_id, None)
        self._session_locks.pop(txn_id, None)
        self._session_loops.pop(txn_id, None)
        self._session_started.pop(txn_id, None)
        self._active_txs.pop(txn_id, None)

    @property
    def active_session_count(self) -> int:
        """Transaction sessions opened and not yet committed or aborted."""
        return len(self._active_sessions)

    async def _reap_stale_sessions(self) -> int:
        """Close transaction sessions older than ``stale_session_max_age``.

        Defence in depth behind the callers' own commit/abort: every such
        session holds one of the pool's connections, and a caller that was
        cancelled before it could abort (or that never used the context
        manager) would otherwise hold it until process shutdown. Under load
        that is how the pool ran dry a few hundred records in.
        """
        now = time.monotonic()
        stale = [
            txn_id
            for txn_id, started in list(self._session_started.items())
            if now - started > self._stale_session_max_age
        ]
        reaped = 0
        for txn_id in stale:
            session = self._active_sessions.get(txn_id)
            if session is None:
                self._forget_session(txn_id)
                continue
            owner = self._session_loops.get(txn_id)
            tx = self._active_txs.get(txn_id)
            if tx is not None:
                # Closing the session discards the transaction anyway; the
                # explicit rollback keeps the server from waiting out its own
                # transaction timeout first.
                await self._close_on_owning_loop(tx, owner, f"stale transaction {txn_id}")
            closed = await self._close_on_owning_loop(session, owner, f"stale session {txn_id}")
            if not closed and self._close_could_be_retried(owner):
                continue
            self._forget_session(txn_id)
            reaped += 1
        if reaped:
            self.logger.warning(
                "Neo4j: closed %d transaction session(s) older than %.0fs that were never "
                "committed or aborted (%d still open)",
                reaped, self._stale_session_max_age, len(self._active_sessions),
            )
        return reaped

    async def commit_transaction(self, txn_id: str) -> None:
        """
        Commit a Neo4j transaction.

        Args:
            txn_id: Transaction ID (session identifier)
        """
        if txn_id not in self._active_sessions:
            raise ValueError(f"Transaction {txn_id} not found")

        session = self._active_sessions[txn_id]
        tx = self._active_txs.get(txn_id)
        try:
            if tx is not None:
                await tx.commit()
            self.logger.debug(f"✅ Committed Neo4j transaction: {txn_id}")
        finally:
            try:
                await session.close()
            finally:
                self._forget_session(txn_id)

    async def abort_transaction(self, txn_id: str) -> None:
        """
        Abort (rollback) a Neo4j transaction.

        Args:
            txn_id: Transaction ID (session identifier)
        """
        if txn_id not in self._active_sessions:
            raise ValueError(f"Transaction {txn_id} not found")

        session = self._active_sessions[txn_id]
        tx = self._active_txs.get(txn_id)
        try:
            if tx is not None:
                try:
                    await tx.rollback()
                except Exception as e:
                    # A transaction the server already tore down (timeout,
                    # lost connection) cannot be rolled back and needs no
                    # rollback; the session close below still releases the
                    # pooled connection either way.
                    self.logger.warning("Neo4j rollback of %s raised: %s", txn_id, e)
            await session.close()
            self.logger.debug(f"🔄 Aborted Neo4j transaction: {txn_id}")
        finally:
            self._forget_session(txn_id)

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
            # With explicit transactions on, queries run inside the open
            # transaction; otherwise on the session, where each is its own
            # auto-commit.
            runner = self._active_txs.get(txn_id, session)

            try:
                if lock:
                    # Serialize access to the session to prevent concurrent operations
                    async with lock:
                        result = await runner.run(query, parameters)
                        return await result.data()
                # Fallback if lock doesn't exist (shouldn't happen)
                result = await runner.run(query, parameters)
                return await result.data()
            except (ClientError, ServiceUnavailable, SessionExpired) as e:
                _report_neo4j_failure(e)
                raise
        else:
            # Auto-commit transaction. The driver's liveness_check_timeout
            # catches most stale connections, but a race (connection dies
            # between check and use) can still occur.
            try:
                return await self._run_autocommit(query, parameters)
            except (ServiceUnavailable, SessionExpired) as first:
                # One dead connection is not a dead driver: the pool opens a
                # fresh connection for the retry, so try that before doing
                # anything to the driver other coroutines are using.
                self.logger.warning(
                    "Neo4j connection lost during query — retrying on the pool: %s", first
                )
                try:
                    return await self._run_autocommit(query, parameters)
                except (ServiceUnavailable, SessionExpired) as second:
                    await self._rebuild_driver_if_unreachable(second)
                    return await self._run_autocommit(query, parameters)

    async def _run_autocommit(
        self, query: str, parameters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        try:
            async with self.driver.session(database=self.database) as session:
                result = await session.run(query, parameters)
                return await result.data()
        except (ClientError, ServiceUnavailable, SessionExpired) as e:
            _report_neo4j_failure(e)
            raise

    async def _rebuild_driver_if_unreachable(self, cause: Exception) -> None:
        """Replace this loop's driver only once it is proven unreachable.

        Closing a driver closes every connection in its pool, including the
        ones other coroutines are mid-query on, and invalidates every open
        transaction session -- so it is the last resort, not the first
        response to a single ``ServiceUnavailable``. ``verify_connectivity``
        decides; a driver that can still reach the server keeps its pool and
        the caller simply retries on it. Rebuilds are rate-limited per loop so
        a server that is flapping cannot make every failing query tear the
        pool down again.
        """
        stale_driver = self.driver
        if stale_driver is None:
            if not await self.connect():
                raise RuntimeError("Neo4j reconnection failed") from cause
            return
        try:
            await stale_driver.verify_connectivity()
        except Exception:
            pass
        else:
            return

        loop = self._current_loop()
        async with self._connect_lock_for_current_loop():
            if self.driver is not stale_driver:
                # Another coroutine already replaced the driver.
                return
            now = time.monotonic()
            with self._drivers_lock:
                last_rebuild = self._rebuilt_at.get(loop, 0.0)
            since = now - last_rebuild
            if since < self._rebuild_cooldown:
                raise RuntimeError(
                    f"Neo4j driver was rebuilt {since:.0f}s ago and is unreachable again; "
                    f"not rebuilding within {self._rebuild_cooldown:.0f}s"
                ) from cause
            self.logger.warning(
                "Neo4j driver unreachable — rebuilding its connection pool: %s", cause
            )
            with self._drivers_lock:
                self._rebuilt_at[loop] = now
            await self._close_driver_safely(stale_driver)
            if not await self._connect_inner():
                raise RuntimeError("Neo4j reconnection failed") from cause

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

