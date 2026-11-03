from typing import Any

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class FolderMimeTypeMigrationService:
    """Writes ``text/directory`` on every folder record that carries another
    mimeType: collection folders (``application/vnd.folder``), Google Drive
    folders (Google's own type), and object-store folders that took the
    storage's content type. Every writer uses ``text/directory``.

    Safe to skip: every reader still accepts all three folder values
    (``FOLDER_MIME_TYPES``), so a folder not rewritten yet reads as before.
    Idempotent, and retried on the next startup when it fails or stops early.
    """

    MIGRATION_FLAG_KEY = "/migrations/folder_mime_type_v1"

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
            return {"success": True, "normalized": 0, "skipped": True}
        try:
            result = await self.graph_provider.normalize_folder_mime_types()
        except NotImplementedError:
            return {"success": True, "normalized": 0, "skipped": True, "unsupported": True}
        except Exception as e:
            self.logger.error(f"❌ Folder mimeType migration failed: {e}", exc_info=True)
            return {"success": False, "normalized": 0, "error": str(e)}

        normalized = (result or {}).get("normalized", 0)
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "normalized": normalized, "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to set the folder mimeType migration flag: {e}; it will re-run (harmlessly)")
        return {"success": True, "normalized": normalized}


async def run_folder_mime_type_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await FolderMimeTypeMigrationService(graph_provider, config_service, logger).migrate()
