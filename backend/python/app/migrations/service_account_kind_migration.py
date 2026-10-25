from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# How many nodes are stamped per round trip.
_UPSERT_BATCH = 100


class ServiceAccountKindMigrationService:
    """Marks the user nodes that belong to service accounts rather than people.

    ``kind`` is new on the user node, and nodes written before it existed do
    not carry it. We cannot work out which is which: the rule lives in the user
    management service's Mongo collection, which this service cannot reach, and
    the reserved email domain is a consequence of that rule, not the rule.

    So the two halves split the work. The user management service stages the
    ``userId`` of every live service account under ``MIGRATION_FLAG_KEY``
    (``service_account_kind_backfill.migration.ts``); this stamps ``kind`` on
    those nodes and writes ``done`` back to the same key. Nothing records the
    backfill as finished until the write has actually happened here — queuing
    an event is not the same as applying it, and during a rolling upgrade the
    previous replica is still consuming, so an event carrying ``kind`` can be
    read by a binary that drops it.

    Only ``kind`` is written. A node is looked up first and left alone if it is
    not there, so nothing is created by a stale id, and no other field is
    touched — in particular not ``isActive``, so an account deleted after it was
    staged stays deleted.

    Idempotent, and retried on the next startup when it fails.
    """

    MIGRATION_FLAG_KEY = "/migrations/service_account_kind_v1"
    STATE_VERSION = 1

    def __init__(self, graph_provider: IGraphDBProvider, config_service: ConfigurationService, logger) -> None:
        self.graph_provider = graph_provider
        self.config_service = config_service
        self.logger = logger

    async def _read_state(self) -> dict | None:
        try:
            state = await self.config_service.get_config(self.MIGRATION_FLAG_KEY)
            return state if isinstance(state, dict) else None
        except Exception as e:
            self.logger.debug(f"Unable to read the service-account kind state: {e}")
            return None

    async def migrate(self) -> dict[str, Any]:
        state = await self._read_state()
        if state is None:
            # Nothing staged yet. The user management service stages on every
            # boot until we answer, so this is not a failure and must not be
            # recorded as done.
            return {"success": True, "stamped": 0, "skipped": True}
        if state.get("done") is True:
            return {"success": True, "stamped": 0, "skipped": True}

        user_ids = state.get("userIds") or []
        if not user_ids:
            return {"success": True, "stamped": 0, "skipped": True}

        stamped = 0
        missing = 0
        pending: list[dict] = []

        try:
            for user_id in user_ids:
                # raise_on_error: a failed read answers None too, and reading
                # that as "no such node" would record the backfill finished
                # while nodes still have no kind.
                node = await self.graph_provider.get_user_by_user_id(
                    str(user_id), raise_on_error=True
                )
                if not node:
                    missing += 1
                    continue
                if node.get("kind") == "service":
                    continue

                node_key = node.get("id") or node.get("_key")
                if not node_key:
                    missing += 1
                    continue

                pending.append(
                    {
                        "id": node_key,
                        "kind": "service",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                )
                if len(pending) >= _UPSERT_BATCH:
                    await self.graph_provider.batch_upsert_nodes(pending, CollectionNames.USERS.value)
                    stamped += len(pending)
                    pending = []

            if pending:
                await self.graph_provider.batch_upsert_nodes(pending, CollectionNames.USERS.value)
                stamped += len(pending)
        except Exception as e:
            self.logger.error(f"❌ Service-account kind migration failed: {e}", exc_info=True)
            return {"success": False, "stamped": stamped, "error": str(e)}

        if missing:
            self.logger.warning(
                f"⚠️ {missing} staged service account(s) have no user node; left unmarked"
            )

        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "version": self.STATE_VERSION,
                    "done": True,
                    "stamped": stamped,
                    "missing": missing,
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set the service-account kind flag: {e}; it will re-run (harmlessly)"
            )
        return {"success": True, "stamped": stamped, "missing": missing}


async def run_service_account_kind_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await ServiceAccountKindMigrationService(graph_provider, config_service, logger).migrate()
