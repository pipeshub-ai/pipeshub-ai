from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class DuplicateUserGroupsMigrationService:
    """Merges the copies of each ServiceNow company, location, department and cost
    center group onto one. Every sync that read one of them again wrote a new copy
    with a fresh id; memberships and grants were written onto whichever copy the
    lookup by external id happened to return. The lookup now returns the oldest copy,
    so that is where the copies' edges go.

    Only ServiceNow: it is the connector known to have written copies of the same
    source group, and merging groups another connector keeps apart on purpose would
    merge their members. Idempotent; a failure leaves the flag unset and the next
    start runs it again.
    """

    MIGRATION_FLAG_KEY = "/migrations/duplicate_user_groups_v1"
    CONNECTORS = (Connectors.SERVICENOW,)

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
            result = await self.graph_provider.merge_duplicate_user_groups([c.value for c in self.CONNECTORS])
        except NotImplementedError:
            return {"success": True, "removed": 0, "skipped": True, "unsupported": True}
        except Exception as e:
            self.logger.error(f"❌ Duplicate user groups migration failed: {e}", exc_info=True)
            return {"success": False, "removed": 0, "error": str(e)}

        result = {key: (result or {}).get(key, 0) for key in ("groups", "removed", "moved")}
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, **result, "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to set the duplicate user groups flag: {e}; it will re-run (harmlessly)")
        return {"success": True, **result}


async def run_duplicate_user_groups_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await DuplicateUserGroupsMigrationService(graph_provider, config_service, logger).migrate()
