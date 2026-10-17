"""Durable release of a deleted connector's blob storage.

Deleting a connector or KB ends with ``StorageCleanupHelper.release_connector_storage``:
shared content is handed over to a surviving holder, then the connector's
storage is deleted, and nothing is deleted if any handover failed. A connector's
credentials are deleted with it. The intent is recorded before the graph delete,
because afterwards nothing else knows which connector's storage is owed, and
cleared only once a release completed.
``StorageReleaseReconciler`` retries whatever is left, from the connectors
service, under a Redis leader lease like the trash purge.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.storage_cleanup import StorageCleanupHelper
from app.connectors.services.connector_intents import ConnectorIntentStore
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

PENDING_DIRECTORY = "/services/storageRelease/pending/"
LEADER_KEY = "storage_release:leader"
# An intent no attempt has touched yet is left this long to the delete that
# recorded it, which may still be running.
GRACE_MS = 15 * 60 * 1000
# Wait after a failed attempt, doubled per attempt up to the cap. Never given
# up: a release blocked by an older storage service completes once it upgrades.
RETRY_MS = 5 * 60 * 1000
RETRY_CAP_MS = 6 * 60 * 60 * 1000
# An intent whose app still exists is dropped (its delete was reverted) only
# after this long: a KB has no DELETING status while its delete runs.
STALE_MS = 24 * 60 * 60 * 1000
TICK_SECONDS = 5 * 60.0
STARTUP_GRACE_SECONDS = 120.0
LEASE_TTL_SECONDS = 30 * 60
_MAX_BACKOFF_MULTIPLIER = 4


class StorageReleaseIntentError(Exception):
    """The intent could not be recorded; the deletion must not go ahead."""


_STORE = ConnectorIntentStore(PENDING_DIRECTORY, StorageReleaseIntentError, "storage release")


async def record_pending_storage_release(
    config_service: ConfigurationService, *, org_id: str, connector_id: str, now_ms: int | None = None,
) -> None:
    """Raises ``StorageReleaseIntentError`` when the KV store refuses the write."""
    await _STORE.record(config_service, org_id=org_id, connector_id=connector_id, now_ms=now_ms)


async def clear_pending_storage_release(config_service: ConfigurationService, connector_id: str) -> bool:
    return await _STORE.clear(config_service, connector_id)


async def list_pending_storage_releases(config_service: ConfigurationService) -> list[dict[str, Any]]:
    return await _STORE.list(config_service)


def retry_delay_ms(attempts: int) -> int:
    return min(RETRY_MS * (2 ** attempts), RETRY_CAP_MS)


async def delete_connector_credentials(
    logger: Logger, config_service: ConfigurationService, connector_id: str,
) -> bool:
    """Whether the connector's credentials are gone. Never raises."""
    key = f"/services/connectors/{connector_id}/config"
    try:
        # delete_config answers False rather than raising, and Redis answers
        # False for a key an earlier attempt already deleted.
        if await config_service.delete_config(key) or await config_service.get_config(
            key, raise_on_error=True
        ) is None:
            return True
    except Exception:
        logger.warning("Connector credentials not confirmed deleted | connector=%s", connector_id, exc_info=True)
        return False
    logger.warning("Connector credentials not deleted | connector=%s", connector_id)
    return False


async def release_connector_storage(
    logger: Logger,
    helper: StorageCleanupHelper,
    config_service: ConfigurationService,
    *,
    org_id: str,
    connector_id: str,
    org_config_service: ConfigurationService | None = None,
    intent: dict[str, Any] | None = None,
    now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
) -> bool:
    """Run one release and settle its intent: cleared when it completed,
    rescheduled with backoff when it did not. ``org_config_service`` holds the
    connector's credentials; None when it has none (a KB). Never raises."""
    try:
        result = await helper.release_connector_storage(org_id, connector_id)
        completed = result.completed
    except Exception:
        logger.exception("Storage release raised | org=%s connector=%s", org_id, connector_id)
        completed = False
    # Attempted whatever the storage did, so a stuck handover never keeps them.
    if org_config_service is not None and not await delete_connector_credentials(
        logger, org_config_service, connector_id
    ):
        completed = False
    try:
        if completed:
            if await clear_pending_storage_release(config_service, connector_id):
                logger.info("Storage release intent cleared | org=%s connector=%s", org_id, connector_id)
            else:
                logger.warning("Storage release intent not cleared | org=%s connector=%s", org_id, connector_id)
            return True
        current = intent or {"orgId": org_id, "connectorId": connector_id, "requestedAt": now_ms()}
        attempts = int(current.get("attempts") or 0)
        wait = retry_delay_ms(attempts)
        await _STORE.reschedule(config_service, current, next_attempt_at=now_ms() + wait)
        logger.warning(
            "Storage release kept for retry | org=%s connector=%s attempts=%d retry_in_s=%d",
            org_id, connector_id, attempts + 1, wait // 1000,
        )
    except Exception:
        logger.warning(
            "Storage release intent not updated | org=%s connector=%s", org_id, connector_id, exc_info=True,
        )
    return completed


class StorageReleaseReconciler:
    """Each tick settles at most one due intent left by a delete that did not
    finish its storage release."""

    def __init__(
        self,
        *,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        lock: LeaderLock,
        now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
        config_service_for: Callable[[str], Awaitable[ConfigurationService]] | None = None,
    ) -> None:
        self.logger = logger
        self.graph = graph_provider
        self.config_service = config_service
        self.lock = lock
        self.now_ms = now_ms
        self.config_service_for = config_service_for

    async def tick(self) -> str:
        """``not_leader``, ``idle``, ``released``, ``retrying`` or ``dropped``."""
        if not await self.lock.try_acquire():
            return "not_leader"
        now = self.now_ms()
        for intent in await list_pending_storage_releases(self.config_service):
            requested = int(intent.get("requestedAt") or 0)
            if not intent.get("attempts") and requested > now - GRACE_MS:
                continue
            if int(intent.get("nextAttemptAt") or 0) > now:
                continue
            org_id, connector_id = str(intent["orgId"]), str(intent["connectorId"])
            # Raised, not None: a failed read must never pass for a gone app.
            app = await self.graph.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True)
            if app is not None:
                if app.get("status") == APP_STATUS_DELETING or requested > now - STALE_MS:
                    continue
                await clear_pending_storage_release(self.config_service, connector_id)
                self.logger.info(
                    "Storage release intent dropped; the connector still exists | org=%s connector=%s",
                    org_id, connector_id,
                )
                return "dropped"
            self.logger.info(
                "Storage release retried | org=%s connector=%s attempts=%d",
                org_id, connector_id, int(intent.get("attempts") or 0),
            )
            try:
                org_config = (
                    await self.config_service_for(org_id) if self.config_service_for else self.config_service
                )
            except Exception:
                self.logger.warning(
                    "Storage release kept for retry; the org's config is unavailable | org=%s connector=%s",
                    org_id, connector_id, exc_info=True,
                )
                await _STORE.reschedule(
                    self.config_service, intent,
                    next_attempt_at=now + retry_delay_ms(int(intent.get("attempts") or 0)),
                )
                return "retrying"
            helper = StorageCleanupHelper(self.logger, self.graph, self.config_service)
            try:
                done = await release_connector_storage(
                    self.logger, helper, self.config_service,
                    org_id=org_id, connector_id=connector_id, org_config_service=org_config,
                    intent=intent, now_ms=self.now_ms,
                )
            finally:
                await helper.close()
            return "released" if done else "retrying"
        return "idle"


async def run_storage_release_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
    *,
    config_service_for: Callable[[str], Awaitable[ConfigurationService]] | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    logger = app_container.logger()
    await sleep(STARTUP_GRACE_SECONDS)
    owner = f"storage-release:{uuid4().hex}"
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
                reconciler = StorageReleaseReconciler(
                    logger=logger, graph_provider=graph_provider,
                    config_service=app_container.config_service(), lock=lock,
                    config_service_for=config_service_for,
                )
                outcome = await reconciler.tick()
                backoff = 1
                if outcome in ("released", "dropped"):
                    # More may be due; look again without waiting a full tick.
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Storage release tick failed")
                if lock is not None:
                    await lock.close()
                    lock = None
                backoff = min(backoff * 2, _MAX_BACKOFF_MULTIPLIER)
            await sleep(TICK_SECONDS * backoff)
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()
