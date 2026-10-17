from typing import Any

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class HierarchyBackfillMigrationService:
    """Writes the hierarchy edges a graph synced by an older version lacks
    (see ``IGraphDBProvider.backfill_hierarchy``). Without them the
    knowledge hub and the access check cannot reach a collection's root items or
    a connector's groups and records until a full resync.

    Only adds hierarchy and inheritance edges, never grants, so it cannot widen
    access. Idempotent; a failure or a partial run leaves the flag unset and the
    next startup runs it again.
    """

    MIGRATION_FLAG_KEY = "/migrations/hierarchy_backfill_v1"

    def __init__(self, graph_provider: IGraphDBProvider, config_service: ConfigurationService, logger) -> None:
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

    async def migrate(self) -> dict[str, Any]:
        if await self._is_migration_already_done():
            return {"success": True, "added": {}, "skipped": True}
        try:
            result = await self.graph_provider.backfill_hierarchy()
        except NotImplementedError:
            return {"success": True, "added": {}, "skipped": True, "unsupported": True}
        except Exception as e:
            self.logger.error(f"❌ Hierarchy backfill migration failed: {e}", exc_info=True)
            return {"success": False, "added": {}, "error": str(e)}

        added = (result or {}).get("added", {})
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "added": added, "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to set the hierarchy backfill flag: {e}; it will re-run (harmlessly)")
        return {"success": True, "added": added}


async def run_hierarchy_backfill_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await HierarchyBackfillMigrationService(graph_provider, config_service, logger).migrate()
