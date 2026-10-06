from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.modules.agents import handles
from app.modules.agents.handle_allocator import HandlesExhaustedError, claim
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

BATCH_SIZE = 500


class AgentHandlesMigrationService:
    """
    Gives every agent an ``orgId`` and a unique per-org ``handle``.

    ``orgId`` is the agent's own, else its creator's. The handle is the slug of the
    name, suffixed ``-2``, ``-3``... on collision, oldest agent first. Each agent is
    written in one update, so a crash leaves it either untouched or complete, and a
    re-run only sees agents still missing a handle. Agents with no derivable org
    are never listed and stay as they are. The flag is set only after a pass that
    ended on an empty batch with no failures.
    """

    MIGRATION_FLAG_KEY = "/migrations/agent_handles_v1"

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

    async def _mark_migration_done(self, result: dict[str, Any]) -> None:
        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "done": True,
                    "agents_updated": result.get("agents_updated", 0),
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to set agent handles migration flag: {e}. May run again on next startup."
            )

    async def _assign(self, agent: dict[str, Any]) -> bool:
        org_id = agent["orgId"]

        async def write(handle: str) -> bool:
            return await self.graph_provider.update_node(
                agent["id"], CollectionNames.AGENT_INSTANCES.value, {"orgId": org_id, "handle": handle},
            )

        try:
            await claim(self.graph_provider, org_id, handles.slugify(agent.get("name") or ""), write)
        except HandlesExhaustedError:
            return False
        return True

    async def migrate(self) -> dict[str, Any]:
        if await self._is_migration_already_done():
            return {"success": True, "skipped": True, "agents_updated": 0, "agents_failed": 0}

        updated = 0
        failed_ids: set[str] = set()
        try:
            while True:
                batch = await self.graph_provider.list_agents_missing_handle(BATCH_SIZE)
                if not batch:
                    break
                batch_updated = 0
                for agent in batch:
                    try:
                        ok = await self._assign(agent)
                    except Exception as e:
                        self.logger.warning(f"Agent handle backfill failed for {agent.get('id')}: {e}")
                        ok = False
                    batch_updated += ok
                    if ok:
                        failed_ids.discard(agent["id"])
                    else:
                        failed_ids.add(agent["id"])
                updated += batch_updated
                if batch_updated == 0:
                    # Only agents that keep failing are left; looping again would spin.
                    break
        except Exception as e:
            self.logger.error(f"Agent handles migration failed: {e}")
            return {"success": False, "agents_updated": updated, "agents_failed": len(failed_ids), "error": str(e)}

        if failed_ids:
            error = f"{len(failed_ids)} agent(s) could not be given a handle; will retry on next startup"
            self.logger.warning(f"Agent handles migration: {error}")
            return {"success": False, "agents_updated": updated, "agents_failed": len(failed_ids), "error": error}

        result = {"success": True, "agents_updated": updated, "agents_failed": 0}
        self.logger.info(f"Agent handles migration: {updated} agent(s) updated")
        await self._mark_migration_done(result)
        return result


async def run_agent_handles_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await AgentHandlesMigrationService(graph_provider, config_service, logger).migrate()
