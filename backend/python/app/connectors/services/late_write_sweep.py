"""Durable sweep of what indexing writes for a connector after its delete.

Indexing already in flight when a connector or KB is deleted keeps writing its
blocks and stage states for a while: a run that started before the delete may
commit long after it. The delete sweeps them itself, but a count of zero does
not prove those writers are done, and once the app is gone nothing else knows
the connector existed. So the deleting service records an intent before the
graph delete, and ``LateWriteSweeper`` sweeps the connector again, from the
connectors service under a Redis leader lease, until nothing has been written
for it for ``QUIET_MS``.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.connectors.services.connector_intents import ConnectorIntentStore
from app.core.org_context import for_org
from app.modules.indexing.vector_membership_backfill import (
    VectorMembershipBackfillLeaderLock,
)
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING
from app.services.messaging.utils import MessagingUtils
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.modules.indexing.vector_membership_backfill import LeaderLock
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

PENDING_DIRECTORY = "/services/lateWriteSweep/pending/"
LEADER_KEY = "late_write_sweep:leader"
# The delete sweeps for itself first, and may still be running.
GRACE_MS = 5 * 60 * 1000
# A record may legitimately stay IN_PROGRESS for about 2 h (the PDF processing
# timeout, the parse admission wait and the lease; docs/indexing-service.md), and a
# stage job picked up meanwhile writes after it. Nothing written for this long
# means no writer from before the delete is left.
QUIET_MS = 6 * 60 * 60 * 1000
# Something still writing a week on is not a writer from before the delete.
GIVE_UP_MS = 7 * 24 * 60 * 60 * 1000
# An intent whose app still exists (and is not being deleted) is dropped after
# this long: its delete failed or was undone, and a live connector is never swept.
STALE_MS = 24 * 60 * 60 * 1000
# Between passes, doubled per pass up to the cap.
RETRY_MS = 5 * 60 * 1000
RETRY_CAP_MS = 60 * 60 * 1000
TICK_SECONDS = 5 * 60.0
STARTUP_GRACE_SECONDS = 120.0
LEASE_TTL_SECONDS = 30 * 60
_MAX_BACKOFF_MULTIPLIER = 4


class LateWriteSweepIntentError(Exception):
    """The intent could not be recorded; the deletion must not go ahead."""


_STORE = ConnectorIntentStore(PENDING_DIRECTORY, LateWriteSweepIntentError, "late-write sweep")


async def record_pending_late_write_sweep(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    *,
    org_id: str,
    connector_id: str,
    now_ms: int | None = None,
) -> None:
    """Raises ``LateWriteSweepIntentError`` when the KV store refuses the write, or when the
    connector's record groups or an earlier intent cannot be read.

    The intent keeps the connector's record groups, read here before the delete removes
    them: the links indexing writes late carry a group and no connector (see
    ``IGraphDBProvider.sweep_deleted_connector_writes``)."""
    try:
        rows = await graph_provider.get_nodes_by_filters(
            collection=CollectionNames.RECORD_GROUPS.value,
            filters={"connectorId": connector_id},
            return_fields=["_key"],
            raise_on_error=True,
        )
        # A retried delete may run after the first attempt removed some groups; keep those too.
        earlier = await config_service.get_config(_STORE.key(connector_id), use_cache=False, raise_on_error=True)
    except Exception as e:
        raise LateWriteSweepIntentError(
            f"could not read the record groups to sweep for connector {connector_id}"
        ) from e
    kept = earlier.get("recordGroupIds") if isinstance(earlier, dict) else None
    read = [row.get("_key") for row in rows or []]
    groups = list(dict.fromkeys(str(g) for g in [*(kept if isinstance(kept, list) else []), *read] if g))
    await _STORE.record(
        config_service, org_id=org_id, connector_id=connector_id, now_ms=now_ms, recordGroupIds=groups,
    )


async def list_pending_late_write_sweeps(config_service: ConfigurationService) -> list[dict[str, Any]]:
    return await _STORE.list(config_service)


def retry_delay_ms(passes: int) -> int:
    return min(RETRY_MS * (2 ** passes), RETRY_CAP_MS)


class LateWriteSweeper:
    """Each tick sweeps every due intent once."""

    def __init__(
        self,
        *,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        lock: LeaderLock,
        now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
    ) -> None:
        self.logger = logger
        self.graph = graph_provider
        self.config_service = config_service
        self.lock = lock
        self.now_ms = now_ms

    async def tick(self) -> dict[str, int]:
        """How many intents ended each way: ``swept``, ``retrying``, ``cleared``, ``dropped``,
        ``given_up``; ``{"not_leader": 1}`` when another replica holds the lease."""
        if not await self.lock.try_acquire():
            return {"not_leader": 1}
        outcomes: Counter[str] = Counter()
        for intent in await list_pending_late_write_sweeps(self.config_service):
            outcome = await self._settle(intent, self.now_ms())
            if outcome:
                outcomes[outcome] += 1
        return dict(outcomes)

    async def _settle(self, intent: dict[str, Any], now: int) -> str | None:
        requested = int(intent.get("requestedAt") or 0)
        if requested > now - GRACE_MS or int(intent.get("nextAttemptAt") or 0) > now:
            return None
        org_id, connector_id = str(intent["orgId"]), str(intent["connectorId"])
        try:
            # The loop runs with no ambient org, and a store that scopes every read by org
            # (PostgreSQL) refuses one made outside it.
            with for_org(org_id, job="late_write_sweep"):
                # Raised, not None: a failed read must never pass for a gone app.
                app = await self.graph.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True)
                if app is not None:
                    if app.get("status") == APP_STATUS_DELETING or requested > now - STALE_MS:
                        return None
                    await self._clear(org_id, connector_id)
                    self.logger.info(
                        "Late-write sweep dropped; the connector still exists | org=%s connector=%s",
                        org_id, connector_id,
                    )
                    return "dropped"
                removed, left = await self.graph.sweep_deleted_connector_writes(
                    connector_id, [str(g) for g in intent.get("recordGroupIds") or []],
                )
        except Exception:
            if now - requested >= GIVE_UP_MS:
                await self._clear(org_id, connector_id)
                self.logger.error(
                    "Late-write sweep given up: its passes for connector %s in org %s have failed for a week "
                    "after its delete", connector_id, org_id, exc_info=True,
                )
                return "given_up"
            self.logger.warning(
                "Late-write sweep failed; kept for retry | org=%s connector=%s", org_id, connector_id, exc_info=True,
            )
            # On Arango a writer still running surfaces as a write conflict that raises here, so a
            # failed pass restarts the quiet clock instead of counting toward it.
            await self._reschedule({**intent, "lastFoundAt": now}, now)
            return "retrying"
        if removed:
            self.logger.warning(
                "Late-write sweep removed %d block(s) and stage state(s) written after the delete "
                "| org=%s connector=%s", removed, org_id, connector_id,
            )
        if removed or left:
            intent = {**intent, "lastFoundAt": now}
        quiet_since = max(requested, int(intent.get("lastFoundAt") or 0))
        if not left and now - quiet_since >= QUIET_MS:
            await self._clear(org_id, connector_id)
            self.logger.info("Late-write sweep finished | org=%s connector=%s", org_id, connector_id)
            return "cleared"
        if now - requested >= GIVE_UP_MS:
            await self._clear(org_id, connector_id)
            self.logger.error(
                "Late-write sweep given up: something still writes for connector %s in org %s a week after "
                "its delete (%d left after the last pass)", connector_id, org_id, left,
            )
            return "given_up"
        await self._reschedule(intent, now)
        return "swept"

    async def _reschedule(self, intent: dict[str, Any], now: int) -> None:
        delay = retry_delay_ms(int(intent.get("attempts") or 0))
        try:
            await _STORE.reschedule(self.config_service, intent, next_attempt_at=now + delay)
        except Exception:
            self.logger.warning(
                "Late-write sweep intent not updated | connector=%s", intent.get("connectorId"), exc_info=True,
            )

    async def _clear(self, org_id: str, connector_id: str) -> None:
        try:
            if await _STORE.clear(self.config_service, connector_id):
                return
        except Exception:
            self.logger.warning("Late-write sweep intent not cleared | connector=%s", connector_id, exc_info=True)
            return
        self.logger.warning("Late-write sweep intent not cleared | org=%s connector=%s", org_id, connector_id)


async def run_late_write_sweep_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    logger = app_container.logger()
    await sleep(STARTUP_GRACE_SECONDS)
    owner = f"late-write-sweep:{uuid4().hex}"
    lock: VectorMembershipBackfillLeaderLock | None = None
    backoff = 1
    try:
        while True:
            try:
                if lock is None:
                    lock = VectorMembershipBackfillLeaderLock(
                        logger,
                        await MessagingUtils._get_redis_config(app_container),
                        owner,
                        ttl_seconds=LEASE_TTL_SECONDS,
                        key=LEADER_KEY,
                    )
                await LateWriteSweeper(
                    logger=logger, graph_provider=graph_provider,
                    config_service=app_container.config_service(), lock=lock,
                ).tick()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Late-write sweep tick failed")
                if lock is not None:
                    await lock.close()
                    lock = None
                backoff = min(backoff * 2, _MAX_BACKOFF_MULTIPLIER)
            await sleep(TICK_SECONDS * backoff)
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()
