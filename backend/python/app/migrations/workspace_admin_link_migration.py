from typing import Any

from app.config.configuration_service import ConfigurationService
from app.core.org_context import system
from app.services.cache.invalidation_hooks import notify_connector_sync_completed
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# The @ConnectorBuilder names, which an app document stores as its type: the connectors whose
# credentials act as an admin account (domain-wide delegation, a team token), and so can name an account other than the creator's.
CONNECTOR_TYPES = ("Gmail Workspace", "Drive Workspace", "Dropbox")


class WorkspaceAdminLinkMigrationService:
    """Removes the link from a connector's creator to the account its credentials act as, where it is wrong.

    A creator the connector lists as one of the source's own users has their own account there, so they are never linked to
    the account the credentials act as (the admin who set up the delegation or the team token): the link would hand them that
    account's mailbox or files. A creator the connector does not list (an outsider who holds the credentials) stays linked,
    and so do the connectors of every other type. Every sync applies the same rule; this applies it at startup to the Gmail
    Workspace, Drive Workspace and Dropbox team connectors, so nobody waits for the next sync and a sync that cannot run still
    leaves no wrong link. A link from a user to themselves goes too.

    Runs on every startup, not once: it is idempotent and cheap, and a replica still on the old version can write the
    link again during a rolling upgrade, which the next start of any new replica then removes.
    """

    def __init__(self, graph_provider: IGraphDBProvider, config_service: ConfigurationService, logger) -> None:
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger

    async def _drop_cached_maps(self, connector_id: str, org_id: str | None) -> None:
        try:
            await notify_connector_sync_completed(connector_id, org_id)
        except Exception as e:
            self.logger.warning("workspace admin link: cache drop failed error=%s", type(e).__name__)

    async def migrate(self) -> dict[str, Any]:
        """One call: ArangoDB and Neo4j make one pass over the links, whatever the number of orgs (a read of every org's apps
        per org took 13 minutes at 2,000 orgs); PostgreSQL does one statement per org, each bound to its org. The caches of
        every link removed are dropped before a failure is reported: the link is gone, so nothing later would drop them."""
        try:
            with system("workspace_admin_link"):
                removed, failed_orgs = await self.graph_provider.remove_listed_authenticator_links(list(CONNECTOR_TYPES))
        except Exception as e:
            self.logger.error("Workspace admin link migration failed error=%s", type(e).__name__)
            return {"success": False, "removed": 0, "error": type(e).__name__}
        for link in removed:
            await self._drop_cached_maps(str(link["connectorId"]), link.get("org"))
        if removed:
            self.logger.info(f"Workspace admin link: removed {len(removed)} link(s) from a creator to the admin account")
        if failed_orgs:
            self.logger.error(f"Workspace admin link removal failed for {len(failed_orgs)} org(s); it runs again next startup")
            return {"success": False, "removed": len(removed), "failed_orgs": failed_orgs}
        return {"success": True, "removed": len(removed)}


async def run_workspace_admin_link_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await WorkspaceAdminLinkMigrationService(graph_provider, config_service, logger).migrate()
