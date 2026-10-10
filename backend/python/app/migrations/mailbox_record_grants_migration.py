from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, RecordTypes
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class MailboxRecordGrantsMigrationService:
    """Removes the grants older versions wrote on every mail and attachment of the
    mailbox connectors. Outlook granted each mail to its from/to/cc addresses as
    well as to the mailbox owner, so a recipient read the copy in someone else's
    mailbox; Outlook Personal and Gmail granted the owner alone. These connectors
    now write no grant on a record: the mailbox or folder group holds the owner's
    grant and every mail and attachment inherits it. A sync only clears the grants
    of a mail it rewrites, and delta sync never revisits an unchanged one.

    A record loses its grants only where its group carries the grant of one of
    its grantees, the owner, who keeps it through that group. Idempotent; a
    failure leaves the flag unset and the next start runs it again.
    """

    MIGRATION_FLAG_KEY = "/migrations/mailbox_record_grants_v1"
    CONNECTORS = (
        Connectors.OUTLOOK,
        Connectors.OUTLOOK_INDIVIDUAL,
        Connectors.GOOGLE_MAIL,
        Connectors.GOOGLE_MAIL_WORKSPACE,
    )
    RECORD_TYPES = (RecordTypes.MAIL, RecordTypes.GROUP_MAIL, RecordTypes.FILE)

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
            result = await self.graph_provider.remove_inherited_record_grants(
                [c.value for c in self.CONNECTORS], [t.value for t in self.RECORD_TYPES],
            )
        except NotImplementedError:
            return {"success": True, "removed": 0, "skipped": True, "unsupported": True}
        except Exception as e:
            self.logger.error(f"❌ Mailbox record grants migration failed: {e}", exc_info=True)
            return {"success": False, "removed": 0, "error": str(e)}

        removed = (result or {}).get("removed", 0)
        records = (result or {}).get("records", 0)
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "removed": removed, "records": records, "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to set the mailbox record grants flag: {e}; it will re-run (harmlessly)")
        return {"success": True, "removed": removed, "records": records}


async def run_mailbox_record_grants_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await MailboxRecordGrantsMigrationService(graph_provider, config_service, logger).migrate()
