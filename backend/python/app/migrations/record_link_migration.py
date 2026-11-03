from typing import Any

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RecordLinkMigrationService:
    """Moves link edges (BLOCKS, FOREIGN_KEY, SIBLING, ...) off the hierarchy
    edge type onto their own, so the knowledge hub and the access check cannot
    walk a link as a parent-child edge.

    Idempotent, and retried on the next startup when it fails. A backend that
    does not implement the split is skipped without setting the flag, so it
    runs there once the backend supports it.
    """

    MIGRATION_FLAG_KEY = "/migrations/record_link_v1"

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
            return {"success": True, "migrated": 0, "skipped": True}
        try:
            result = await self.graph_provider.split_link_edges()
        except NotImplementedError:
            return {"success": True, "migrated": 0, "skipped": True, "unsupported": True}
        except Exception as e:
            self.logger.error(f"❌ Record link migration failed: {e}", exc_info=True)
            return {"success": False, "migrated": 0, "error": str(e)}

        migrated = (result or {}).get("migrated", 0)
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "migrated": migrated, "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to set the record link migration flag: {e}; it will re-run (harmlessly)")
        return {"success": True, "migrated": migrated}


async def run_record_link_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await RecordLinkMigrationService(graph_provider, config_service, logger).migrate()
