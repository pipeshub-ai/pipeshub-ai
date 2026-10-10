from typing import Any

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class ChatGrantEdgesCleanupMigrationService:
    """One-shot removal of legacy user READER edges onto chat attachments and artifacts.

    Chat content access is decided by the PDP, so these edges are dead weight and, left in
    place, would keep granting recipients files the owner never consented to share.
    Supersedes ``chat_grants_reconcile_v1``.

    Must ship in the same release as PR-7.1-7.3 (PDP read path live): running it without
    the PDP would briefly cut recipients off from shared chat files.
    """

    MIGRATION_FLAG_KEY = "/migrations/chat_grant_edges_cleanup_v1"

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        logger,
        batch_size: int = 1000,
    ) -> None:
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger
        self.batch_size = batch_size

    async def _is_migration_already_done(self) -> bool:
        try:
            flag = await self.config_service.get_config(self.MIGRATION_FLAG_KEY)
            return bool(flag and flag.get("done") is True)
        except Exception as e:
            self.logger.debug(f"Unable to read migration flag (assuming not done): {e}")
            return False

    async def _mark_migration_done(self, deleted: int) -> None:
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "deleted": deleted, "timestamp": get_epoch_timestamp_in_ms()},
            )
            self.logger.info("Chat grant edges cleanup completion flag set successfully")
        except Exception as e:
            self.logger.warning(
                f"Failed to set chat grant edges cleanup flag: {e}. "
                "Migration completed but may run again on next startup."
            )

    async def migrate(self) -> dict[str, Any]:
        if await self._is_migration_already_done():
            self.logger.info("Chat grant edges cleanup already completed - skipping")
            return {"success": True, "skipped": True, "deleted": 0}

        try:
            deleted = await self.graph_provider.delete_chat_content_reader_edges(self.batch_size)
        except Exception as e:
            self.logger.error(f"Chat grant edges cleanup failed: {e}")
            return {"success": False, "error": str(e), "deleted": 0}

        self.logger.info("Chat grant edges cleanup: deleted %s READER edges", deleted)
        await self._mark_migration_done(deleted)
        return {"success": True, "skipped": False, "deleted": deleted}


async def run_chat_grant_edges_cleanup_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await ChatGrantEdgesCleanupMigrationService(graph_provider, config_service, logger).migrate()
