from typing import Any, Dict

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class TeamOrgIdMigrationService:
    """
    Backfills ``orgId`` on teams created before the field was stamped.

    Team lookups are org-scoped and fail closed, so a team without ``orgId``
    is unreachable. ``all_<orgId>`` teams take the id suffix; other teams take
    the org of their ``createdBy`` user. Teams whose org cannot be derived are
    left untouched and reported by id.
    """

    MIGRATION_FLAG_KEY = "/migrations/team_org_id_v1"

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

    async def _mark_migration_done(self, result: Dict[str, Any]) -> None:
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "teams_updated": result.get("teams_updated", 0),
                    "teams_unresolved": result.get("teams_unresolved", 0),
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to set team orgId migration flag: {e}. May run again on next startup."
            )

    async def migrate(self) -> Dict[str, Any]:
        if await self._is_migration_already_done():
            return {"success": True, "skipped": True, "teams_updated": 0, "teams_unresolved": 0}

        try:
            outcome = await self.graph_provider.backfill_team_org_ids()
        except Exception as e:
            self.logger.error(f"Team orgId migration failed: {e}")
            return {"success": False, "teams_updated": 0, "teams_unresolved": 0, "error": str(e)}

        unresolved = list(outcome.get("unresolved_team_ids") or [])
        result: Dict[str, Any] = {
            "success": True,
            "teams_updated": int(outcome.get("updated", 0)),
            "teams_unresolved": len(unresolved),
        }
        self.logger.info(f"Team orgId migration: {result['teams_updated']} team(s) updated")
        if unresolved:
            self.logger.warning(
                f"Team orgId migration: {len(unresolved)} team(s) have no derivable org and stay "
                f"unresolvable: {unresolved[:50]}"
            )
        await self._mark_migration_done(result)
        return result


async def run_team_org_id_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> Dict[str, Any]:
    return await TeamOrgIdMigrationService(graph_provider, config_service, logger).migrate()
