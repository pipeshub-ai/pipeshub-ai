from typing import Any, Dict

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# The only module that names the pre-rename edge: a guard test exempts it by
# name, so these literals cannot reappear elsewhere unnoticed.
LEGACY_ARANGO_COLLECTION = "recordRelations"
LEGACY_NEO4J_RELATIONSHIP_TYPE = "RECORD_RELATION"


class NodeRelationMigrationError(Exception):
    """Base exception for the node relation migration."""
    pass


class NodeRelationMigrationService:
    """Renames the hierarchy edge in place: `recordRelations` to `nodeRelations`
    on ArangoDB, `RECORD_RELATION` to `NODE_RELATION` on Neo4j.

    Must run **before** `ensure_schema()`: schema init would otherwise create an
    empty `nodeRelations` collection on a deployment whose edges still live
    under the old name, and the application would read the empty one.

    Idempotent on both backends: a deployment already on the new name is a
    no-op. A failure leaves the completion flag unset, so the next startup
    retries.
    """

    MIGRATION_FLAG_KEY = "/migrations/node_relation_v1"

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger

    async def _is_migration_already_done(self) -> bool:
        try:
            flag = await self.config_service.get_config(self.MIGRATION_FLAG_KEY)
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(f"Unable to read migration flag (assuming not done): {e}")
            return False

    async def _mark_migration_done(self, result: Dict) -> None:
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "migrated": result.get("migrated", 0),
                    "already_current": result.get("already_current", False),
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
            self.logger.info("✅ Node relation migration completion flag set successfully")
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set migration completion flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def migrate(self) -> Dict[str, Any]:
        if await self._is_migration_already_done():
            self.logger.info("✅ Node relation migration already completed - skipping")
            return {
                "success": True,
                "migrated": 0,
                "skipped": True,
                "message": "Migration already completed",
            }

        try:
            self.logger.info("Starting node relation edge migration")
            result = await self.graph_provider.migrate_legacy_relation_edge(
                legacy_collection=LEGACY_ARANGO_COLLECTION,
                legacy_relationship_type=LEGACY_NEO4J_RELATIONSHIP_TYPE,
            )

            migrated = (result or {}).get("migrated", 0)
            already_current = (result or {}).get("already_current", False)
            if already_current:
                self.logger.info("✅ Hierarchy edge already carries the new name")
            else:
                self.logger.info(f"✅ Migrated {migrated} hierarchy edge(s) to the new name")

            outcome = {
                "success": True,
                "migrated": migrated,
                "already_current": already_current,
            }
            await self._mark_migration_done(outcome)
            return outcome

        except Exception as e:
            # Flag deliberately left unset so the next startup retries.
            self.logger.error(f"❌ Node relation migration failed: {e}", exc_info=True)
            return {"success": False, "migrated": 0, "error": str(e)}


async def run_node_relation_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> Dict[str, Any]:
    """Run the node relation edge migration; returns its result with ``success``."""
    service = NodeRelationMigrationService(graph_provider, config_service, logger)

    return await service.migrate()
