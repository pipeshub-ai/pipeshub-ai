from typing import Any

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RedundantGroupEdgesMigrationService:
    """Deletes the group edges a nested record kept from an older build (see
    ``IGraphDBProvider.drop_redundant_group_edges``): the hierarchy backfill gave
    it an inheritance edge to its parent record and left the one to its group,
    so it kept the group's audience past its parent and was listed twice.

    Removes only edges a sync of that record removes today. Idempotent; a failure
    or a partial run leaves the flag unset and the next startup runs it again.
    """

    MIGRATION_FLAG_KEY = "/migrations/redundant_group_edges_v1"

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
            return {"success": True, "removed": 0, "skipped": True}
        try:
            result = await self.graph_provider.drop_redundant_group_edges()
        except NotImplementedError:
            return {"success": True, "removed": 0, "skipped": True, "unsupported": True}
        except Exception as e:
            self.logger.error(f"❌ Redundant group edges migration failed: {e}", exc_info=True)
            return {"success": False, "removed": 0, "error": str(e)}

        removed = (result or {}).get("removed", 0)
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "removed": removed, "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to set the redundant group edges flag: {e}; it will re-run (harmlessly)")
        return {"success": True, "removed": removed}


async def run_redundant_group_edges_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await RedundantGroupEdgesMigrationService(graph_provider, config_service, logger).migrate()
