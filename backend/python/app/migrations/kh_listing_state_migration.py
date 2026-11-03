from typing import Any

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import (
    KH_LISTING_STATE_FLAG,
    IGraphDBProvider,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KhListingStateMigrationService:
    """Stamps the knowledge hub listing state (see
    ``IGraphDBProvider.stamp_kh_listing_state``) on nodes written before the
    write paths kept it. The listing reads that state only once this flag is set,
    and derives everything per request until then, so a failed or partial run
    changes nothing a user sees; the next startup runs it again.
    """

    MIGRATION_FLAG_KEY = KH_LISTING_STATE_FLAG

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
            return {"success": True, "stamped": 0, "skipped": True}
        try:
            result = await self.graph_provider.stamp_kh_listing_state()
        except NotImplementedError:
            return {"success": True, "stamped": 0, "skipped": True, "unsupported": True}
        except Exception as e:
            self.logger.error(f"❌ Knowledge hub listing state migration failed: {e}", exc_info=True)
            return {"success": False, "stamped": 0, "error": str(e)}

        stamped = (result or {}).get("stamped", 0)
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "stamped": stamped, "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set the knowledge hub listing state flag: {e}; it will re-run (harmlessly)"
            )
        return {"success": True, "stamped": stamped}


async def run_kh_listing_state_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await KhListingStateMigrationService(graph_provider, config_service, logger).migrate()
