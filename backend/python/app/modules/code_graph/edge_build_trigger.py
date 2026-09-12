"""Decide when a repository's cross-file edge build should be asked for.

Blocks are written to the graph as each file is indexed, carrying their
unresolved cross-file references; the corpus-wide pass that resolves them can
only run once every file of the repo is in. This module answers "is the repo
in?", and makes sure one drained repo produces one request rather than one per
file that finished alongside the last.

Scoped to the record group, not the connector. A repo connector also syncs
issues and merge requests into groups of their own (``-work-items``,
``-merge-requests``); those contribute no blocks, so waiting on them would hold
the build behind work it does not read, and their steady trickle would mean a
busy repo never looks drained at all.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.modules.code_graph.connectors import (
    _NORMALIZED_CODE_TYPES,
    _normalized,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from logging import Logger

    from redis.asyncio import Redis

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "BLOCKING_STATUSES",
    "BUILD_LOCK_PREFIX",
    "BUILD_LOCK_RENEW_INTERVAL_SECONDS",
    "BUILD_LOCK_TTL_SECONDS",
    "REFRESH_LOCK_IF_OWNER_LUA",
    "RELEASE_LOCK_IF_OWNER_LUA",
    "SYNC_POINT_SUFFIX",
    "claim_publish",
    "group_has_unfinished_records",
    "is_code_record",
    "publishable_scope",
    "read_build_state",
    "renew_build_lock_until_cancelled",
    "sync_point_key_for",
]

BUILD_LOCK_PREFIX = "pipeshub:code-edge-build:"
# Short lease plus renewal, as the vector-store rebuild lock does: a build that
# outlives an un-renewed lease lets a second one delete the edges the first just
# wrote, while a long lease would block every later build after a crash.
BUILD_LOCK_TTL_SECONDS = 300
BUILD_LOCK_RENEW_INTERVAL_SECONDS = 60
SYNC_POINT_SUFFIX = "code-edge-build"

# A file still being indexed means an incomplete symbol table, and an
# incomplete symbol table produces wrong edges rather than missing ones.
BLOCKING_STATUSES = (
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.QUEUED.value,
    ProgressStatus.IN_PROGRESS.value,
)

RELEASE_LOCK_IF_OWNER_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

# Compare-and-expire in one step: a bare EXPIRE would extend the lease of a lock
# a new owner has since taken.
REFRESH_LOCK_IF_OWNER_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""

# Collapses the tail: the last several files of a repo can each write a
# terminal status and then each observe a drained group, so without this one
# drain produces a small burst of identical requests. Long enough to cover that
# burst, short enough that a genuine second drain minutes later is not swallowed.
_PUBLISH_DEDUPE_PREFIX = "pipeshub:code-edge-publish:"
_PUBLISH_DEDUPE_TTL_SECONDS = 60


def sync_point_key_for(record_group_id: str) -> str:
    return f"{record_group_id}/{SYNC_POINT_SUFFIX}"


def is_code_record(record: "Mapping[str, Any] | None") -> bool:
    """Whether this record came from a connector that puts code in the graph.

    Split out from ``publishable_scope`` because it is the one part answerable
    from a copy of the record that may predate its final status write, which is
    what lets a caller skip re-reading the record at all.
    """
    if not record:
        return False
    return _normalized(record.get("connectorName")) in _NORMALIZED_CODE_TYPES


def publishable_scope(
    record: "Mapping[str, Any] | None",
) -> tuple[str, str, str] | None:
    """The ``(org, connector, record group)`` a build for this record would run
    for, or ``None`` if this record cannot be the one that completes a repo.

    Terminal is defined as "no longer blocking" rather than as COMPLETED: a
    record that ended EMPTY or FAILED is just as done, and the repo's last file
    is disproportionately likely to be exactly that. Gating on COMPLETED leaves
    such a repo drained with nobody left to notice.
    """
    if not is_code_record(record):
        return None
    status = record.get("indexingStatus")
    if status is None or status in BLOCKING_STATUSES:
        return None
    scope = (
        record.get("orgId"),
        record.get("connectorId"),
        record.get("recordGroupId"),
    )
    if not all(isinstance(value, str) and value for value in scope):
        return None
    return str(scope[0]), str(scope[1]), str(scope[2])


async def group_has_unfinished_records(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    record_group_id: str,
) -> bool:
    """Whether any record in the group is still on its way through indexing."""
    return await graph_provider.has_nodes_by_filters(
        collection=CollectionNames.RECORDS.value,
        filters={"orgId": org_id, "recordGroupId": record_group_id},
        in_filters={"indexingStatus": list(BLOCKING_STATUSES)},
    )


async def read_build_state(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    record_group_id: str,
) -> tuple[int | None, bool]:
    """``(lastEdgeBuildAt, edgeBuildPending)`` from the group's sync point.

    ``edgeBuildPending`` outlives the message that was going to do the build,
    so a request that died in the broker leaves a mark instead of nothing.
    """
    rows = await graph_provider.get_nodes_by_filters(
        collection=CollectionNames.SYNC_POINTS.value,
        filters={
            "orgId": org_id,
            "syncPointKey": sync_point_key_for(record_group_id),
        },
        return_fields=["lastEdgeBuildAt", "edgeBuildPending"],
    )
    if not rows:
        return None, False
    last_build = rows[0].get("lastEdgeBuildAt")
    return (
        int(last_build) if isinstance(last_build, (int, float)) else None,
        bool(rows[0].get("edgeBuildPending")),
    )


async def claim_publish(
    redis: "Redis",
    org_id: str,
    record_group_id: str,
) -> bool:
    """Win the right to ask for this group's build, once per dedupe window."""
    return bool(
        await redis.set(
            f"{_PUBLISH_DEDUPE_PREFIX}{org_id}:{record_group_id}",
            "1",
            nx=True,
            ex=_PUBLISH_DEDUPE_TTL_SECONDS,
        )
    )


async def renew_build_lock_until_cancelled(
    redis: "Redis",
    lock_key: str,
    lock_token: str,
    log: "Logger",
) -> None:
    """Hold the build's lease open until the caller cancels this task.

    Losing ownership means another replica is already rebuilding the same edges,
    so stop renewing rather than take it back and have both believe they hold it.
    """
    while True:
        await asyncio.sleep(BUILD_LOCK_RENEW_INTERVAL_SECONDS)
        try:
            if not await redis.eval(
                REFRESH_LOCK_IF_OWNER_LUA,
                1,
                lock_key,
                lock_token,
                BUILD_LOCK_TTL_SECONDS,
            ):
                log.error(
                    "Code edge build lost its lock %s; another replica may be "
                    "rebuilding the same edges. Stopping renewal.",
                    lock_key,
                )
                return
        except Exception:
            log.exception("Failed to renew code edge build lock %s", lock_key)
