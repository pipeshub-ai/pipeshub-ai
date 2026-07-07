"""Startup / background helpers for the org-wide indexing progress bar.

Owned by Python because it holds the graph abstraction (Arango + Neo4j). Node
only reads Redis. All three seed triggers (boot, reconcile, on-demand) call
``seed_orgs`` and rely on the counter's overwrite semantics, so they interleave
safely with the live funnel.
"""

import asyncio
import logging
import os

from app.services.progress.progress_counter import (
    ProgressCounter,
    init_progress_counter,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30
# Backstop that heals folder/collection deletions and drift; the funnel +
# connector_removed handle the common cases instantly. Overridable via env.
DEFAULT_RECONCILE_INTERVAL_SECONDS = int(os.getenv("PROGRESS_RECONCILE_SECONDS", "60"))
# Fast cadence that reconciles only orgs newly flagged dirty (a fresh connector),
# so the bar fills in all connectors within a few seconds of a sync starting.
DEFAULT_FAST_RECONCILE_INTERVAL_SECONDS = int(os.getenv("PROGRESS_RECONCILE_FAST_SECONDS", "5"))


async def create_progress_counter(config_service, logger: logging.Logger) -> ProgressCounter | None:
    """Resolve the Redis URL and install the per-process counter singleton.

    The counter creates its own per-event-loop clients from this URL (see
    ProgressCounter), which is what keeps it safe across the indexing service's
    main + worker-thread loops. Best-effort: a failure returns None and leaves the
    counter uninstalled, so every hook becomes a no-op rather than breaking the
    host service.
    """
    try:
        from app.config.constants.service import config_node_constants
        from app.utils.redis_util import build_redis_url

        redis_config = await config_service.get_config(config_node_constants.REDIS.value)
        if not redis_config or not isinstance(redis_config, dict):
            raise ValueError("Redis configuration not found")
        redis_url = build_redis_url(redis_config)
        return init_progress_counter(redis_url, logger)
    except Exception as e:  # noqa: BLE001 - isolation
        logger.warning(f"⚠️ Progress counter unavailable (Redis init failed): {e}")
        return None


async def seed_orgs(
    counter: ProgressCounter, graph_provider, org_ids, logger: logging.Logger
) -> None:
    """Overwrite Redis counters for each org from DB truth (index-only query)."""
    for org_id in {o for o in (org_ids or []) if o}:
        try:
            rows = await graph_provider.get_indexing_status_counts(org_id)
            await counter.seed_org(org_id, rows)
        except Exception as e:  # noqa: BLE001 - isolation
            logger.debug(f"progress seed skipped for org {org_id}: {e}")


async def progress_maintenance_loop(
    counter: ProgressCounter,
    logger: logging.Logger,
    *,
    heartbeat_interval: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Refresh the indexer heartbeat every ~30s (Redis-only, per-loop client).

    Deliberately does NOT run the periodic graph reconcile: on Neo4j the graph
    driver is bound to the indexing worker-thread loop while this loop runs on the
    main loop, so a graph read here would be cross-loop. Drift healing is instead
    covered by the boot seed (startup), the live funnel, and the query service's
    on-demand seed when an admin opens the widget.
    """
    logger.info(f"🫀 Progress heartbeat loop started ({heartbeat_interval}s)")
    while True:
        try:
            await counter.write_heartbeat(get_epoch_timestamp_in_ms())
        except asyncio.CancelledError:
            logger.info("🛑 Progress heartbeat loop cancelled")
            raise
        except Exception as e:  # noqa: BLE001 - isolation
            logger.warning(f"⚠️ Progress heartbeat tick failed: {e}")
        await asyncio.sleep(heartbeat_interval)


async def progress_reconcile_loop(
    counter: ProgressCounter,
    graph_provider,
    logger: logging.Logger,
    *,
    fast_interval: int = DEFAULT_FAST_RECONCILE_INTERVAL_SECONDS,
    full_interval: int = DEFAULT_RECONCILE_INTERVAL_SECONDS,
) -> None:
    """Overwrite active orgs' counters to DB truth. Runs on the QUERY service
    (single main event loop — no worker-thread/graph cross-loop hazard).

    Two cadences:
      * fast (~5s): reconcile only orgs newly flagged dirty by a fresh connector,
        so the bar shows ALL connectors within a few seconds of a sync starting.
      * full (~60s): reconcile every active org — the authoritative healer for
        funnel drift, deleted connectors/collections, and records purged while a
        service was down.
    Zero graph reads when nothing is active/dirty.
    """
    every = max(1, full_interval // max(1, fast_interval))
    ticks = 0
    logger.info(
        f"♻️ Progress reconcile loop started (dirty every {fast_interval}s, "
        f"full every {full_interval}s)"
    )
    while True:
        try:
            dirty = await counter.pop_dirty_orgs()
            if dirty:
                await seed_orgs(counter, graph_provider, dirty, logger)
            if ticks % every == 0:
                orgs = await counter.get_active_orgs()
                if orgs:
                    await seed_orgs(counter, graph_provider, orgs, logger)
        except asyncio.CancelledError:
            logger.info("🛑 Progress reconcile loop cancelled")
            raise
        except Exception as e:  # noqa: BLE001 - isolation
            logger.warning(f"⚠️ Progress reconcile tick failed: {e}")
        ticks += 1
        await asyncio.sleep(fast_interval)
