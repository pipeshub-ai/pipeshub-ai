"""
ArcadeDB Async Client Wrapper

ArcadeDB ships a Bolt protocol plugin that speaks openCypher over the same
wire protocol as Neo4j, so this reuses Neo4jClient's driver/session/
transaction handling untouched and overrides only what ArcadeDB does
differently: database provisioning.

ArcadeDB's Cypher/Bolt channel does not implement ``CREATE DATABASE`` (it
returns "Only CREATE CONSTRAINT, CREATE INDEX and CREATE USER are currently
supported"), so the base class's system-database bootstrap can only ever
detect a missing database, never create one. This subclass creates it over
ArcadeDB's HTTP API instead, then falls back to the base behavior.
"""

from logging import Logger

import aiohttp
from neo4j.exceptions import ClientError

from app.services.graph_db.neo4j.neo4j_client import (
    DEFAULT_CONNECTION_ACQUISITION_TIMEOUT,
    DEFAULT_LIVENESS_CHECK_TIMEOUT,
    DEFAULT_MAX_CONNECTION_LIFETIME,
    DEFAULT_MAX_CONNECTION_POOL_SIZE,
    DEFAULT_REBUILD_COOLDOWN,
    DEFAULT_STALE_SESSION_MAX_AGE,
    DEFAULT_TRANSACTION_TIMEOUT,
    Neo4jClient,
)


class ArcadeDBClient(Neo4jClient):
    """Neo4jClient pointed at ArcadeDB's Bolt endpoint.

    Args mirror Neo4jClient exactly, plus ``http_uri`` for the one operation
    (database creation) that ArcadeDB does not expose over Bolt.
    """

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str,
        logger: Logger,
        *,
        http_uri: str,
        max_connection_pool_size: int = DEFAULT_MAX_CONNECTION_POOL_SIZE,
        connection_acquisition_timeout: float = DEFAULT_CONNECTION_ACQUISITION_TIMEOUT,
        max_connection_lifetime: float = DEFAULT_MAX_CONNECTION_LIFETIME,
        liveness_check_timeout: float = DEFAULT_LIVENESS_CHECK_TIMEOUT,
        stale_session_max_age: float = DEFAULT_STALE_SESSION_MAX_AGE,
        rebuild_cooldown: float = DEFAULT_REBUILD_COOLDOWN,
        explicit_transactions: bool = False,
        transaction_timeout: float = DEFAULT_TRANSACTION_TIMEOUT,
    ) -> None:
        super().__init__(
            uri=uri,
            username=username,
            password=password,
            database=database,
            logger=logger,
            max_connection_pool_size=max_connection_pool_size,
            connection_acquisition_timeout=connection_acquisition_timeout,
            max_connection_lifetime=max_connection_lifetime,
            liveness_check_timeout=liveness_check_timeout,
            stale_session_max_age=stale_session_max_age,
            rebuild_cooldown=rebuild_cooldown,
            explicit_transactions=explicit_transactions,
            transaction_timeout=transaction_timeout,
        )
        self.http_uri = http_uri.rstrip("/")

    async def _ensure_database_exists(self) -> None:
        """Check via Bolt (ArcadeDB supports ``SHOW DATABASES``); create over
        HTTP if missing, since ArcadeDB's Cypher engine rejects ``CREATE
        DATABASE``."""
        try:
            async with self.driver.session(database="system") as session:
                # The WHERE clause is accepted but not actually applied by
                # ArcadeDB's SHOW DATABASES (it always returns every
                # database), so the name is matched client-side instead.
                result = await session.run("SHOW DATABASES", {})
                databases = await result.data()

            if any(db.get("name") == self.database for db in databases):
                self.logger.info(f"✅ Database '{self.database}' already exists")
                return

            self.logger.info(f"📦 Database '{self.database}' not found. Creating it over HTTP...")
            await self._create_database_over_http()
            self.logger.info(f"✅ Database '{self.database}' created successfully")

        except (ClientError, aiohttp.ClientError, RuntimeError) as e:
            self.logger.warning(f"⚠️ Could not verify/create database '{self.database}': {str(e)}")

    async def _create_database_over_http(self) -> None:
        auth = aiohttp.BasicAuth(self.username, self.password)
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.post(
                f"{self.http_uri}/api/v1/server",
                json={"command": f"create database {self.database}"},
            ) as response:
                if response.status >= 400:
                    body = await response.text()
                    raise RuntimeError(f"ArcadeDB database creation failed ({response.status}): {body}")
