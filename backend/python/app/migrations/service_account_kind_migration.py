from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# How many ids one lookup or one write asks about.
_BATCH = 100


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

    Only ``kind`` is written. The nodes are resolved first, in batched queries
    rather than one lookup per account, so nothing is created by a stale id and
    the startup health gate is not held open by a round trip per account. No
    other field is touched — in particular not ``isActive``, so an account
    deleted after it was staged stays deleted.

    Idempotent, and retried on the next startup when it fails. A staged id with
    no node yet is a failure rather than a skip: it usually means that
    account's own ``userAdded`` has not been consumed, and recording the
    backfill finished would leave it unmarked for good.
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

        wanted = [str(user_id) for user_id in user_ids]
        stamped = 0

        try:
            # One query per batch rather than one per account, and it raises
            # for an id it cannot resolve, so an account whose own userAdded is
            # still in flight is retried instead of being recorded as done.
            keys = await self.graph_provider.get_graph_user_keys_by_mongo_user_ids(
                wanted, chunk_size=_BATCH
            )

            node_keys = [keys[user_id] for user_id in wanted if user_id in keys]
            for start in range(0, len(node_keys), _BATCH):
                batch = [
                    {
                        "id": node_key,
                        "kind": "service",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    for node_key in node_keys[start : start + _BATCH]
                ]
                # Both backends answer falsy for a write that did not land, and
                # counting those as stamped would retire them from the retry.
                if not await self.graph_provider.batch_upsert_nodes(
                    batch, CollectionNames.USERS.value
                ):
                    raise RuntimeError(
                        f"The graph rejected {len(batch)} service-account kind write(s)"
                    )
                stamped += len(batch)
        except Exception as e:
            self.logger.error(f"❌ Service-account kind migration failed: {e}", exc_info=True)
            return {"success": False, "stamped": stamped, "error": str(e)}

        try:
            await self.config_service.set_config(
                self.MIGRATION_FLAG_KEY,
                {
                    "version": self.STATE_VERSION,
                    "done": True,
                    "stamped": stamped,
                    "timestamp": get_epoch_timestamp_in_ms(),
                },
            )
        except Exception as e:
            self.logger.warning(
                f"⚠️ Failed to set the service-account kind flag: {e}; it will re-run (harmlessly)"
            )
        return {"success": True, "stamped": stamped}


async def run_service_account_kind_migration(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    logger,
) -> dict[str, Any]:
    return await ServiceAccountKindMigrationService(graph_provider, config_service, logger).migrate()
