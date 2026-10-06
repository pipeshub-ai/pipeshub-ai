"""
ArcadeDB Provider Implementation

ArcadeDB speaks openCypher over the same Bolt wire protocol as Neo4j (its
Neo4j-Bolt plugin), so this provider is Neo4jProvider unchanged except for:

1. connect() — points the driver at ArcadeDB's Bolt endpoint (ARCADEDB_*
   env vars) via ArcadeDBClient instead of Neo4jClient.
2. _generate_required_field_constraints() — skipped. ArcadeDB accepts the
   ``CREATE CONSTRAINT ... REQUIRE n.prop IS NOT NULL`` DDL (unlike Neo4j
   Community, which rejects it outright — the base class already tolerates
   that rejection), but then enforces it eagerly on node creation inside
   MERGE, before the write's own SET clause has populated the property.
   Since almost every write in this provider is
   ``MERGE (n:Label {id: ...}) SET n.prop = ...``, an existence constraint
   here would break every such write rather than just validate it. Skipping
   these constraints leaves ArcadeDB with the same validation coverage as
   Neo4j Community already has in production (existence constraints are a
   Neo4j Enterprise feature): the unique 'id' constraints and indexes still
   get created, since those do not affect the MERGE + SET pattern.
"""

from __future__ import annotations

import os
from logging import Logger
from typing import TYPE_CHECKING

from app.services.graph_db.arcadedb.arcadedb_client import ArcadeDBClient
from app.services.graph_db.neo4j.neo4j_client import DEFAULT_MAX_CONNECTION_POOL_SIZE
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.env_config import env_int
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.services.cache.interface import IAccessibleRecordsCache


class ArcadeDBProvider(Neo4jProvider):
    """ArcadeDB implementation of IGraphDBProvider (via Neo4jProvider reuse)."""

    def __init__(
        self,
        logger: Logger,
        config_service: "ConfigurationService",
        accessible_records_cache: "IAccessibleRecordsCache | None" = None,
    ) -> None:
        super().__init__(logger, config_service, accessible_records_cache)
        # Overrides the Neo4jClient type annotation from the base class;
        # ArcadeDBClient is a Neo4jClient subclass so every base method stays valid.
        self.client: ArcadeDBClient | None = None

    async def connect(self) -> bool:
        try:
            self.logger.info("🚀 Connecting to ArcadeDB...")

            uri = str(os.getenv("ARCADEDB_URI", "bolt://localhost:7687"))
            http_uri = str(os.getenv("ARCADEDB_HTTP_URI", "http://localhost:2480"))
            username = str(os.getenv("ARCADEDB_USERNAME", "root"))
            password = str(os.getenv("ARCADEDB_PASSWORD", ""))
            database = str(os.getenv("ARCADEDB_DATABASE", "pipeshub"))

            if not password:
                raise ValueError("ArcadeDB password is required (set ARCADEDB_PASSWORD environment variable or configure in etcd)")

            self.client = ArcadeDBClient(
                uri=uri,
                username=username,
                password=password,
                database=database,
                logger=self.logger,
                http_uri=http_uri,
                max_connection_pool_size=env_int(
                    "ARCADEDB_MAX_CONNECTION_POOL_SIZE", DEFAULT_MAX_CONNECTION_POOL_SIZE
                ),
                explicit_transactions=env_bool("ARCADEDB_EXPLICIT_TRANSACTIONS", False),
            )

            if not await self.client.connect():
                raise Exception("Failed to connect to ArcadeDB")

            self.logger.info("✅ ArcadeDB provider connected successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to connect to ArcadeDB: {str(e)}")
            self.client = None
            return False

    async def disconnect(self) -> bool:
        try:
            self.logger.info("🚀 Disconnecting from ArcadeDB...")
            if self.client:
                await self.client.disconnect()
            self.client = None
            self.logger.info("✅ Disconnected from ArcadeDB")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect: {str(e)}")
            return False

    def _generate_required_field_constraints(self) -> list[str]:
        """See module docstring: existence constraints break MERGE + SET on ArcadeDB."""
        return []
