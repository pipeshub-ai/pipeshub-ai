"""Periodic orphan sweep of named entities (see ``named_entities.sweep``).

It runs whether or not the Labs flag is on: turning the flag off leaves the
nodes other records still mention, and those become orphans as the records are
reindexed, so a flag-gated sweep would keep them (and any PII in them) forever.

One replica sweeps at a time, under a Redis leader lease; without Redis no
replica sweeps. The grace period has a floor: a node is safe from deletion only
while it is younger than the grace period, so the grace must stay far above the
time a writer takes from claiming a node to committing its link.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.modules.indexing.entity_index_rebuild import resolve_entity_store
from app.modules.indexing.vector_membership_backfill import (
    VectorMembershipBackfillLeaderLock,
)
from app.modules.named_entities.sweep import DEFAULT_GRACE_MS, NamedEntitySweeper
from app.services.messaging.utils import MessagingUtils

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

STARTUP_GRACE_SECONDS = 300.0
DEFAULT_INTERVAL_SECONDS = 6 * 60 * 60.0
MIN_GRACE_SECONDS = 60 * 60.0
LEADER_KEY = "named_entity_sweep:leader"
# The leader renews on every pass; a dead leader's lease lapses within one pass.
_LEASE_MARGIN_SECONDS = 15 * 60


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError:
        return default
    return max(1.0, value) if math.isfinite(value) else default


async def run_named_entity_sweep_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: IGraphDBProvider,
) -> None:
    logger = app_container.logger()
    interval = _env_float("NAMED_ENTITY_SWEEP_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)
    grace_seconds = _env_float("NAMED_ENTITY_ORPHAN_GRACE_SECONDS", DEFAULT_GRACE_MS / 1000)
    if grace_seconds < MIN_GRACE_SECONDS:
        logger.warning(
            "named_entity_sweep: grace %.0fs is below the %.0fs floor; using the floor", grace_seconds, MIN_GRACE_SECONDS,
        )
        grace_seconds = MIN_GRACE_SECONDS
    grace_ms = int(grace_seconds * 1000)
    await asyncio.sleep(STARTUP_GRACE_SECONDS)
    logger.info("named_entity_sweep: starting, interval=%.0fs grace=%dms", interval, grace_ms)
    lock: VectorMembershipBackfillLeaderLock | None = None
    owner = f"named-entity-sweep:{uuid4().hex}"
    try:
        while True:
            try:
                if lock is None:
                    redis_config = await MessagingUtils._get_redis_config(app_container)
                    lock = VectorMembershipBackfillLeaderLock(
                        logger, redis_config, owner, ttl_seconds=int(interval) + _LEASE_MARGIN_SECONDS, key=LEADER_KEY,
                    )
                if await lock.try_acquire():
                    store = await resolve_entity_store(app_container)
                    await NamedEntitySweeper(graph_provider, store, grace_ms=grace_ms, logger_=logger).sweep_all()
                else:
                    logger.debug("named_entity_sweep: another replica holds the lease")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("named_entity_sweep: pass failed")
            await asyncio.sleep(interval)
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()
