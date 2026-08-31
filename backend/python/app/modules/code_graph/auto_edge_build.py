"""Trigger cross-file code edge resolution when a repository finishes indexing."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.modules.code_graph.connectors import (
    _NORMALIZED_CODE_TYPES,
    _normalized,
)
from app.modules.code_graph.edge_builder import build_code_graph_edges
from app.services.vector_db.rebuild_state import redis_from_config_service

if TYPE_CHECKING:
    from logging import Logger

    from redis.asyncio import Redis

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

EDGE_BUILD_LOCK_PREFIX = "pipeshub:code-edge-build:"
EDGE_BUILD_LOCK_TTL_SECONDS = 600

_BLOCKING_STATUSES = (
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.QUEUED.value,
    ProgressStatus.IN_PROGRESS.value,
)
_SYNC_POINT_KEY_SUFFIX = "code-edge-build"
_RELEASE_IF_OWNER_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
_edge_build_tasks: set[asyncio.Task[None]] = set()


async def _count_unfinished(
    graph_provider: IGraphDBProvider,
    org_id: str,
    record_group_id: str,
) -> int:
    return await graph_provider.count_nodes_by_filters(
        collection=CollectionNames.RECORDS.value,
        filters={"orgId": org_id, "recordGroupId": record_group_id},
        in_filters={"indexingStatus": list(_BLOCKING_STATUSES)},
    )


async def _load_last_build(
    graph_provider: IGraphDBProvider,
    org_id: str,
    record_group_id: str,
) -> int | None:
    docs = await graph_provider.get_nodes_by_filters(
        collection=CollectionNames.SYNC_POINTS.value,
        filters={
            "orgId": org_id,
            "syncPointKey": f"{record_group_id}/{_SYNC_POINT_KEY_SUFFIX}",
        },
        return_fields=["lastEdgeBuildAt"],
    )
    if not docs:
        return None
    value = docs[0].get("lastEdgeBuildAt")
    return int(value) if isinstance(value, (int, float)) else None


async def _touched_records(
    graph_provider: IGraphDBProvider,
    org_id: str,
    record_group_id: str,
    since_ms: int,
) -> set[str]:
    rows = await graph_provider.get_nodes_updated_since(
        collection=CollectionNames.RECORDS.value,
        timestamp_field="updatedAtTimestamp",
        since=since_ms,
        filters={"orgId": org_id, "recordGroupId": record_group_id},
        return_fields=["_key"],
    )
    return {
        row["_key"]
        for row in rows
        if isinstance(row.get("_key"), str)
    }


async def _stamp_last_build(
    graph_provider: IGraphDBProvider,
    org_id: str,
    connector_id: str,
    record_group_id: str,
    started_at_ms: int,
) -> None:
    await graph_provider.upsert_sync_point(
        sync_point_key=f"{record_group_id}/{_SYNC_POINT_KEY_SUFFIX}",
        sync_point_data={
            "orgId": org_id,
            "connectorId": connector_id,
            "syncDataPointType": "codeEdgeBuild",
            "lastEdgeBuildAt": started_at_ms,
        },
        collection=CollectionNames.SYNC_POINTS.value,
    )


async def _release_lock(redis: Redis, lock_key: str, token: str) -> None:
    await redis.eval(_RELEASE_IF_OWNER_LUA, 1, lock_key, token)


async def _run_edge_build(
    *,
    graph_provider: IGraphDBProvider,
    redis: Redis,
    lock_key: str,
    lock_token: str,
    org_id: str,
    connector_id: str,
    record_group_id: str,
    logger: Logger,
) -> None:
    try:
        if await _count_unfinished(graph_provider, org_id, record_group_id):
            return

        last_build = await _load_last_build(
            graph_provider,
            org_id,
            record_group_id,
        )
        touched_record_ids = None
        if last_build is not None:
            touched_record_ids = await _touched_records(
                graph_provider,
                org_id,
                record_group_id,
                last_build,
            )
            if not touched_record_ids:
                return

        started_at_ms = int(time.time() * 1000)
        result = await build_code_graph_edges(
            graph_provider=graph_provider,
            org_id=org_id,
            record_group_id=record_group_id,
            touched_record_ids=touched_record_ids,
            log=logger,
        )
        await _stamp_last_build(
            graph_provider,
            org_id,
            connector_id,
            record_group_id,
            started_at_ms,
        )
        logger.info("Automatic code edge build complete: %s", result.as_log_fields())
    except Exception:
        logger.exception(
            "Automatic code edge build failed for org=%s record_group=%s",
            org_id,
            record_group_id,
        )
    finally:
        try:
            await _release_lock(redis, lock_key, lock_token)
        except Exception:
            logger.exception(
                "Failed to release code edge build lock for org=%s record_group=%s",
                org_id,
                record_group_id,
            )
        await redis.aclose()


async def maybe_trigger_edge_build(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService,
    record: dict[str, object],
    logger: Logger,
) -> None:
    connector_name = record.get("connectorName")
    if _normalized(connector_name) not in _NORMALIZED_CODE_TYPES:
        return

    org_id = record.get("orgId")
    connector_id = record.get("connectorId")
    record_group_id = record.get("recordGroupId")
    if (
        not isinstance(org_id, str)
        or not org_id
        or not isinstance(connector_id, str)
        or not connector_id
        or not isinstance(record_group_id, str)
        or not record_group_id
    ):
        logger.warning(
            "Skipping automatic code edge build; record %s lacks scope identifiers",
            record.get("_key") or record.get("id"),
        )
        return

    redis = None
    try:
        if await _count_unfinished(graph_provider, org_id, record_group_id):
            return

        redis = await redis_from_config_service(config_service)
        lock_key = f"{EDGE_BUILD_LOCK_PREFIX}{org_id}:{record_group_id}"
        lock_token = str(uuid4())
        acquired = await redis.set(
            lock_key,
            lock_token,
            nx=True,
            ex=EDGE_BUILD_LOCK_TTL_SECONDS,
        )
        if not acquired:
            await redis.aclose()
            return

        task = asyncio.create_task(
            _run_edge_build(
                graph_provider=graph_provider,
                redis=redis,
                lock_key=lock_key,
                lock_token=lock_token,
                org_id=org_id,
                connector_id=connector_id,
                record_group_id=record_group_id,
                logger=logger,
            ),
            name=f"code_edge_build_{org_id}_{record_group_id}",
        )
        _edge_build_tasks.add(task)
        task.add_done_callback(_edge_build_tasks.discard)
    except Exception:
        if redis is not None:
            await redis.aclose()
        logger.exception(
            "Failed to trigger automatic code edge build for record %s",
            record.get("_key") or record.get("id"),
        )
