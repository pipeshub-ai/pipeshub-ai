from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# The @ConnectorBuilder name, which an app document stores as its type.
CONNECTOR_TYPE = "Nextcloud"


def _edge_source_key(edge: dict) -> str:
    """The user key an edge comes from: Neo4j returns ``from_id``, Arango ``_from`` ("users/<key>")."""
    return str(edge.get("from_id") or edge.get("_from") or "").rsplit("/", 1)[-1]


class NextcloudUserAppEdgesMigrationService:
    """Removes the user-to-app edges Nextcloud syncs wrote for anyone but the connector's creator.

    Every full sync used to link the PipesHub user whose email matched the Nextcloud profile
    email to the app, and a Nextcloud app is personal and app-level: that user could read the
    creator's whole drive. The connector now links only the creator; this takes away the
    edges earlier syncs left. Uses only generic provider calls, so it runs on Neo4j and Arango.

    Idempotent. An app whose creator can't be looked up keeps its edges and leaves the flag
    unset, so the next startup tries again.
    """

    MIGRATION_FLAG_KEY = "/migrations/nextcloud_user_app_edges_v1"

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

    async def _creator_key(self, app: dict) -> str | None:
        created_by = app.get("createdBy")
        if not created_by:
            return None
        user = await self.graph_provider.get_user_by_user_id(created_by, raise_on_error=True)
        return (user or {}).get("_key") or (user or {}).get("id")

    async def migrate(self) -> dict[str, Any]:
        if await self._is_migration_already_done():
            return {"success": True, "removed": 0, "skipped": True}
        removed = 0
        unresolved: list[str] = []
        try:
            apps = await self.graph_provider.get_nodes_by_filters(
                CollectionNames.APPS.value, {"type": CONNECTOR_TYPE}, raise_on_error=True
            )
            for app in apps:
                app_key = app.get("_key") or app.get("id")
                creator = await self._creator_key(app)
                if not app_key or not creator:
                    unresolved.append(str(app_key))
                    continue
                edges = await self.graph_provider.get_edges_to_node(
                    f"{CollectionNames.APPS.value}/{app_key}",
                    CollectionNames.USER_APP_RELATION.value,
                    raise_on_error=True,
                )
                for edge in edges:
                    user_key = _edge_source_key(edge)
                    if user_key and user_key != creator:
                        await self.graph_provider.delete_edge(
                            user_key, CollectionNames.USERS.value, app_key, CollectionNames.APPS.value,
                            CollectionNames.USER_APP_RELATION.value,
                        )
                        removed += 1
        except Exception as e:
            self.logger.error(f"❌ Nextcloud user-app edges migration failed: {e}", exc_info=True)
            return {"success": False, "removed": removed, "error": str(e)}

        if removed:
            self.logger.info(f"Nextcloud: removed {removed} user-to-app edge(s) of users who are not the creator")
        if unresolved:
            self.logger.warning(
                f"Nextcloud: could not look up the creator of app(s) {', '.join(unresolved)}; their "
                "user edges are kept and the migration runs again next startup"
            )
            return {"success": False, "removed": removed, "unresolved": unresolved}
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {"done": True, "removed": removed, "timestamp": get_epoch_timestamp_in_ms()},
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to set the Nextcloud user-app edges flag: {e}; it will re-run (harmlessly)")
        return {"success": True, "removed": removed}


async def run_nextcloud_user_app_edges_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await NextcloudUserAppEdgesMigrationService(graph_provider, config_service, logger).migrate()
