from typing import Any

from app.config.configuration_service import ConfigurationService
from app.migrations.kb_apps_migration import KBAppsMigrationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KBTeamEdgeRoleMigrationService:
    """One-shot stamp of ``role`` on legacy team->KB edges.

    An edge role is a grant to the team as a whole, including members who join later,
    so only edges whose active members are all READER are stamped (READER). Every other
    legacy edge stays role-less and resolves per member at read time; those do not block
    the completion flag. The legacy fallback is removed once ``remaining_role_less`` is 0.
    """

    MIGRATION_FLAG_KEY = "/migrations/kb_team_edge_role_v1"
    PREREQUISITE_FLAG_KEY = KBAppsMigrationService.MIGRATION_FLAG_KEY

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        logger,
    ) -> None:
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger

    async def _is_flag_done(self, key: str) -> bool:
        try:
            flag = await self.config_service.get_config(key)
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(f"Unable to read migration flag {key} (assuming not done): {e}")
            return False

    async def _mark_migration_done(self, result: dict[str, Any]) -> None:
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "stamped": result.get("stamped", 0),
                    "remaining_role_less": result.get("remaining_role_less", 0),
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
            self.logger.info("✅ KB team-edge role migration completion flag set successfully")
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set KB team-edge role migration flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def migrate(self) -> dict[str, Any]:
        if await self._is_flag_done(self.MIGRATION_FLAG_KEY):
            self.logger.info("✅ KB team-edge role migration already completed - skipping")
            return {"success": True, "skipped": True, "stamped": 0, "remaining_role_less": 0}

        # Team->KB edges must already point at apps/ nodes; otherwise they would be skipped and the flag set.
        if not await self._is_flag_done(self.PREREQUISITE_FLAG_KEY):
            self.logger.warning("⚠️ KB team-edge role migration deferred: KB apps migration not complete")
            return {
                "success": False,
                "skipped": True,
                "reason": "kb_apps pending",
                "stamped": 0,
                "remaining_role_less": 0,
            }

        try:
            counts = await self.graph_provider.backfill_kb_team_edge_roles()
        except Exception as e:
            self.logger.error(f"KB team-edge role migration failed: {e}")
            return {"success": False, "error": str(e), "stamped": 0, "remaining_role_less": 0}

        result: dict[str, Any] = {
            "success": True,
            "skipped": False,
            "stamped": counts["stamped"],
            "remaining_role_less": counts["remaining_role_less"],
        }
        self.logger.info(
            "KB team-edge role migration: stamped=%s remaining_role_less=%s",
            result["stamped"],
            result["remaining_role_less"],
        )
        await self._mark_migration_done(result)
        return result


async def run_kb_team_edge_role_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await KBTeamEdgeRoleMigrationService(graph_provider, config_service, logger).migrate()
