"""Periodic backfill of connectorIds/recordGroupIds onto existing vector points.

Runs in-process on indexing so it never takes Pool.INDEX or floods record-events.
One Redis leader, one connector, one page per tick.
"""

from __future__ import annotations

import asyncio
import inspect
from logging import Logger
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.constants import ConnectorStateKeys
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.messaging.config import RedisConfig, messaging_env
from app.services.messaging.utils import MessagingUtils

if TYPE_CHECKING:
    from app.services.redis.connection_provider import RedisClient as Redis

LEADER_KEY = "vector_membership_backfill:leader"

_RELEASE_IF_OWNER_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
# Comfortably above the per-VRID ceiling in membership.py: one hung
# sync_vector_membership must not be able to burn the whole lease before the
# next refresh, or a second replica starts the same connector mid-page.
_LEADER_TTL_SECONDS = 600

# After this many full passes that still hit failures, stop retrying and mark the
# connector done rather than scanning it forever.
MAX_BACKFILL_ATTEMPTS = 3

# Lease is measured in minutes; renewing every VRID is pure Redis chatter.
_LEASE_RENEW_EVERY_N_VRIDS = 10

# Backfill is strictly background work, so it yields to trouble rather than
# competing with it: each failed tick doubles the wait, each clean tick resets.
_BACKOFF_FACTOR = 2
_MAX_BACKOFF_MULTIPLIER = 16


class LeaderLock(Protocol):
    async def try_acquire(self) -> bool: ...
    async def refresh(self) -> bool: ...
    async def release(self) -> None: ...
    async def close(self) -> None: ...


class VectorMembershipBackfillLeaderLock:
    """Redis SET NX leader lock. If Redis is down, every acquire fails."""

    def __init__(
        self,
        logger: Logger,
        redis_config: RedisConfig,
        owner: str,
        ttl_seconds: int = _LEADER_TTL_SECONDS,
        key: str = LEADER_KEY,
    ) -> None:
        self.logger = logger
        self.redis_config = redis_config
        self.owner = owner
        self.ttl_seconds = max(1, ttl_seconds)
        self.key = key
        self._redis: Redis | None = None

    async def _client(self) -> Redis | None:
        if self._redis is not None:
            return self._redis
        from app.services.redis.config import ClientOptions, RedisConnectionConfig
        from app.services.redis.connection_provider_factory import get_redis_provider

        provider = get_redis_provider(
            RedisConnectionConfig.from_redis_config(self.redis_config)
        )
        client = provider.create_client(
            ClientOptions(
                decode_responses=True,
                socket_timeout_seconds=5.0,
                socket_connect_timeout_seconds=5.0,
            )
        )
        try:
            await client.ping()
        except Exception as e:
            self.logger.warning(
                "Vector membership backfill skipped this tick; Redis unavailable: %s",
                e,
            )
            await client.aclose()
            return None
        self._redis = client
        return client

    async def try_acquire(self) -> bool:
        try:
            client = await self._client()
            if client is None:
                return False
            # SET XX renews only if the key still exists AND is still ours; a
            # GET-then-EXPIRE would report success when the key expired in
            # between, putting two replicas on the same connector.
            if await client.set(
                self.key, self.owner, xx=True, ex=self.ttl_seconds, get=True
            ) == self.owner:
                return True
            return bool(
                await client.set(self.key, self.owner, nx=True, ex=self.ttl_seconds)
            )
        except Exception as e:
            self.logger.warning(
                "Vector membership backfill skipped this tick; Redis lock failed: %s",
                e,
            )
            await self.close()
            return False

    async def refresh(self) -> bool:
        try:
            client = self._redis
            if client is None:
                return False
            return (
                await client.set(
                    self.key, self.owner, xx=True, ex=self.ttl_seconds, get=True
                )
                == self.owner
            )
        except Exception as e:
            self.logger.warning(
                "Vector membership backfill leader refresh failed: %s", e
            )
            return False

    async def release(self) -> None:
        client = self._redis
        if client is None:
            return
        try:
            # Compare-and-delete in one step: a GET-then-DELETE can remove the
            # lock a different replica legitimately acquired after ours expired.
            await client.eval(_RELEASE_IF_OWNER_LUA, 1, self.key, self.owner)
        except Exception as e:
            self.logger.warning(
                "Vector membership backfill leader release failed: %s", e
            )

    async def close(self) -> None:
        client = self._redis
        self._redis = None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


def distinct_non_empty_vrids(page: list[dict]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for row in page:
        vrid = row.get("virtualRecordId")
        if not isinstance(vrid, str) or not vrid or vrid in seen:
            continue
        seen.add(vrid)
        ordered.append(vrid)
    return ordered


def _prior_vrids(app: dict) -> int:
    """VRIDs this connector's pass has covered so far, across earlier pages."""
    try:
        return max(0, int(app.get(ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS) or 0))
    except (TypeError, ValueError):
        return 0


def _app_key(app: dict) -> str | None:
    key = app.get("_key") or app.get("id")
    if isinstance(key, str) and key:
        return key
    return None


async def _resolve_indexing_pipeline(app_container: Any) -> Any | None:
    getter = getattr(app_container, "indexing_pipeline", None)
    if getter is None:
        return None
    try:
        pipeline = getter() if callable(getter) else getter
        if inspect.isawaitable(pipeline):
            pipeline = await pipeline
    except Exception:
        return None
    if pipeline is None or not hasattr(pipeline, "sync_vector_membership"):
        return None
    return pipeline


async def run_vector_membership_backfill_tick(
    *,
    logger: Logger,
    graph_provider: IGraphDBProvider,
    pipeline: Any,
    lock: LeaderLock,
    page_size: int,
    vrid_pause_ms: int,
) -> None:
    """Process one page of one connector. No-op if this replica is not leader."""
    if not await lock.try_acquire():
        return

    app = await graph_provider.get_app_needing_vector_membership_backfill()
    if not app:
        return

    connector_id = _app_key(app)
    if not connector_id:
        logger.warning("App needing vector membership backfill is missing _key")
        return

    after_key = app.get(ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY)
    if not isinstance(after_key, str) or not after_key:
        after_key = None

    page = await graph_provider.page_records_for_vector_membership_backfill(
        connector_id,
        after_key,
        max(1, page_size),
    )

    failed = 0
    vrids = distinct_non_empty_vrids(page)
    for processed, vrid in enumerate(vrids, start=1):
        try:
            await pipeline.sync_vector_membership(vrid)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed += 1
            logger.exception(
                "Vector membership backfill failed for virtualRecordId=%s "
                "connector=%s; continuing page",
                vrid,
                connector_id,
            )
        if vrid_pause_ms > 0:
            await asyncio.sleep(vrid_pause_ms / 1000.0)

        # Renew on a time basis, not once per VRID: a 50-item page would
        # otherwise spend 50 Redis round trips renewing a lease measured in
        # minutes. Losing leadership mid-page means another replica is now on
        # this connector, so stop rather than write alongside it.
        if processed % _LEASE_RENEW_EVERY_N_VRIDS and processed != len(vrids):
            continue
        if not await lock.refresh():
            # Drop the page's work rather than half-recording it: the new leader
            # re-reads from the same cursor, so counting these failures here
            # would double-count them against the attempt budget.
            logger.warning(
                "Vector membership backfill lost leadership mid-page for "
                "connector %s after %d/%d VRIDs (%d failed); the new leader "
                "resumes from the same cursor",
                connector_id,
                processed,
                len(vrids),
                failed,
            )
            return

    if len(page) >= max(1, page_size):
        # Walk back for the last usable key rather than trusting page[-1]: a row
        # without one would otherwise leave the cursor unmoved and this exact
        # page would be re-fetched on every tick for ever.
        last_key = next(
            (
                key
                for row in reversed(page)
                if isinstance(key := (row.get("_key") or row.get("id")), str) and key
            ),
            None,
        )
        if last_key is None:
            logger.error(
                "Vector membership backfill page for connector %s had no usable "
                "key in any row; marking it done rather than re-reading the same "
                "page indefinitely",
                connector_id,
            )
            await graph_provider.update_node(
                connector_id,
                CollectionNames.APPS.value,
                {
                    ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILLED: True,
                    ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY: None,
                },
            )
            return
        # The cursor advances even past failures so one poison VRID cannot pin a
        # connector forever; the flag below is what records whether the pass was
        # actually clean.
        update: dict[str, Any] = {
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY: last_key,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS: _prior_vrids(app)
            + len(vrids),
        }
        if failed:
            update[ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES] = int(
                app.get(ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES) or 0
            ) + failed
        await graph_provider.update_node(
            connector_id,
            CollectionNames.APPS.value,
            update,
        )
        return

    total_failures = int(
        app.get(ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES) or 0
    ) + failed
    if total_failures:
        # Do not claim completion: the flag is what stops this connector being
        # revisited, so setting it after failures would leave those VRIDs
        # permanently unbackfilled while reporting success. Rewind to a fresh
        # pass, up to a bounded number of attempts.
        attempts = int(
            app.get(ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS) or 0
        ) + 1
        if attempts < MAX_BACKFILL_ATTEMPTS:
            logger.warning(
                "Vector membership backfill for connector %s finished with %d "
                "failure(s); retrying from the start (attempt %d/%d)",
                connector_id,
                total_failures,
                attempts,
                MAX_BACKFILL_ATTEMPTS,
            )
            await graph_provider.update_node(
                connector_id,
                CollectionNames.APPS.value,
                {
                    ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY: None,
                    ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES: 0,
                    ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS: attempts,
                    # Zeroed with the cursor: the next pass re-walks from the
                    # start, so carrying this total forward would count the same
                    # VRIDs twice and overstate what the final pass covered.
                    ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS: 0,
                },
            )
            return
        logger.error(
            "Vector membership backfill for connector %s giving up after %d "
            "attempts with %d failure(s); those vector points keep stale "
            "membership until reindexed",
            connector_id,
            attempts,
            total_failures,
        )
        # Stopping the scan is deliberate, but the flag that stops it means
        # "done", so on its own it makes a connector that never succeeded
        # indistinguishable from a healthy one — and zeroing the counters below
        # would erase the evidence too. Record the giving-up explicitly and keep
        # the failure count, so an operator can find these and decide.
        await graph_provider.update_node(
            connector_id,
            CollectionNames.APPS.value,
            {
                ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILLED: True,
                ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_EXHAUSTED: True,
                ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY: None,
                ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES: total_failures,
                ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS: attempts,
                ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS: _prior_vrids(app)
                + len(vrids),
            },
        )
        return

    total_vrids = _prior_vrids(app) + len(vrids)
    if total_vrids == 0:
        # Completing without touching a single VRID is indistinguishable from a
        # real pass in the flag alone, and the flag is what stops this connector
        # ever being revisited. It is legitimate for a connector with no records,
        # but it is also exactly what a connectorId that does not match any
        # record looks like — so say so rather than recording silent success.
        logger.warning(
            "Vector membership backfill marked connector %s done without "
            "processing any virtualRecordId; it has no records, or none whose "
            "connectorId matches its app key",
            connector_id,
        )
    else:
        logger.info(
            "Vector membership backfill completed connector %s across %d "
            "virtualRecordId(s)",
            connector_id,
            total_vrids,
        )

    # Clear the counters alongside the flag. Leaving them set means a later
    # re-run (flag reset by an operator, or a future re-backfill) starts from the
    # old attempt count and gives up after one pass instead of MAX_BACKFILL_ATTEMPTS.
    # The VRID total is deliberately kept: it is the only durable record of how
    # much a completed pass actually did.
    await graph_provider.update_node(
        connector_id,
        CollectionNames.APPS.value,
        {
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILLED: True,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_AFTER_KEY: None,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_FAILURES: 0,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_ATTEMPTS: 0,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_VRIDS: total_vrids,
            ConnectorStateKeys.VECTOR_MEMBERSHIP_BACKFILL_EXHAUSTED: False,
        },
    )


async def run_vector_membership_backfill_loop(
    app_container: Any,
    graph_provider: IGraphDBProvider,
) -> None:
    logger = app_container.logger()
    startup_grace = max(0.0, messaging_env.vector_membership_backfill_startup_grace_seconds)
    if startup_grace:
        logger.info(
            "Delaying vector membership backfill for %.0fs during startup",
            startup_grace,
        )
        await asyncio.sleep(startup_grace)

    owner = f"backfill:{uuid4().hex}"
    lock: VectorMembershipBackfillLeaderLock | None = None
    pipeline = None
    backoff = 1
    try:
        while True:
            try:
                if lock is None:
                    redis_config = await MessagingUtils._get_redis_config(app_container)
                    lock = VectorMembershipBackfillLeaderLock(
                        logger,
                        redis_config,
                        owner,
                    )
                if pipeline is None:
                    pipeline = await _resolve_indexing_pipeline(app_container)
                if pipeline is None:
                    logger.warning(
                        "Vector membership backfill skipped this tick; "
                        "indexing pipeline is unavailable"
                    )
                else:
                    await run_vector_membership_backfill_tick(
                        logger=logger,
                        graph_provider=graph_provider,
                        pipeline=pipeline,
                        lock=lock,
                        page_size=messaging_env.vector_membership_backfill_page_size,
                        vrid_pause_ms=messaging_env.vector_membership_backfill_vrid_pause_ms,
                    )
                    backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Vector membership backfill tick failed")
                if lock is not None:
                    await lock.close()
                    lock = None
                backoff = min(backoff * _BACKOFF_FACTOR, _MAX_BACKOFF_MULTIPLIER)
                logger.warning(
                    "Backing off vector membership backfill to %dx the interval",
                    backoff,
                )
            await asyncio.sleep(
                max(1.0, messaging_env.vector_membership_backfill_interval_seconds)
                * backoff
            )
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()
