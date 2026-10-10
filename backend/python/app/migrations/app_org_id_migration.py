from typing import Any

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class AppOrgIdMigrationService:
    """Stamps ``orgId`` on Apps created before connector instances carried one
    (July 2026), from the organization's ORG_APP_RELATION edge. Every org filter
    on Apps (the connector lists, the connector gate, the access check) would
    otherwise drop those connectors on an upgraded install.

    Idempotent, and retried on the next startup when it fails.
    """

    MIGRATION_FLAG_KEY = "/migrations/app_org_id_v1"

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
            return {"success": True, "backfilled": 0, "skipped": True}
        try:
            result = await self.graph_provider.backfill_app_org_ids()
        except NotImplementedError:
            return {"success": True, "backfilled": 0, "skipped": True, "unsupported": True}
        except Exception as e:
            self.logger.error(f"❌ App orgId migration failed: {e}", exc_info=True)
            return {"success": False, "backfilled": 0, "error": str(e)}

        backfilled = (result or {}).get("backfilled", 0)
        ambiguous = (result or {}).get("ambiguous", 0)
        if ambiguous:
            self.logger.warning(
                f"⚠️ {ambiguous} App(s) without orgId are linked to several organizations; left unset"
            )
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "backfilled": backfilled, "ambiguous": ambiguous,
                 "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to set the App orgId migration flag: {e}; it will re-run (harmlessly)")
        return {"success": True, "backfilled": backfilled, "ambiguous": ambiguous}


async def run_app_org_id_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await AppOrgIdMigrationService(graph_provider, config_service, logger).migrate()
