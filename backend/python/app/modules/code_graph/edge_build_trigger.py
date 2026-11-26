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
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames, ProgressStatus, RecordTypes
from app.modules.code_graph.build_state import REQUEST_IN_FLIGHT_MS
from app.modules.code_graph.connectors import (
    NORMALIZED_CODE_TYPES,
    normalize_connector_type,
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
    "LeaseLostError",
    "REFRESH_LOCK_IF_OWNER_LUA",
    "RELEASE_LOCK_IF_OWNER_LUA",
    "acquire_build_lock",
    "assert_lease_held",
    "claim_publish",
    "group_has_unfinished_records",
    "is_code_record",
    "record_scope",
    "publishable_scope",
    "records_updated_since",
    "release_build_lock",
    "renew_build_lock_until_cancelled",
]

BUILD_LOCK_PREFIX = "pipeshub:code-edge-build:"
# Short lease plus renewal, as the vector-store rebuild lock does: a build that
# outlives an un-renewed lease lets a second one delete the edges the first just
# wrote, while a long lease would block every later build after a crash.
BUILD_LOCK_TTL_SECONDS = 300
BUILD_LOCK_RENEW_INTERVAL_SECONDS = 60

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
_PUBLISH_DEDUPE_TTL_SECONDS = REQUEST_IN_FLIGHT_MS // 1000

def is_code_record(record: "Mapping[str, Any] | None") -> bool:
    """Whether this record is a file from a connector that puts code in the graph.

    Split out from ``publishable_scope`` because it is the one part answerable
    from a copy of the record that may predate its final status write, which is
    what lets a caller skip re-reading the record at all.
    """
    if not record:
        return False
    # Issues and merge requests come from the same connectors, into groups of
    # their own with no blocks (O-8). A payload without the field predates it.
    record_type = record.get("recordType")
    if record_type is not None and record_type != RecordTypes.CODE_FILE.value:
        return False
    return normalize_connector_type(record.get("connectorName")) in NORMALIZED_CODE_TYPES


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
    return record_scope(record)


def record_scope(record: "Mapping[str, Any]") -> tuple[str, str, str] | None:
    """The record's own ``(org, connector, record group)``, or ``None`` if one is missing."""
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


# How far behind the last build's start the watermark looks. lastIndexTimestamp
# comes from the indexing service's clock and the watermark from this one's, and
# a connector stamps updatedAtTimestamp before it commits the record (R-10).
# shortcut: a fixed margin, not a shared clock; re-resolving a few extra files is
# cheap, so widen it rather than add one if skew is ever larger.
WATERMARK_MARGIN_MS = 5 * 60_000

# The connector's stamp sees a rename (a new path, same blocks); the indexer's
# sees a re-projection the connector never made (a reindex, a retry) (R-3).
_CHANGE_STAMPS = ("updatedAtTimestamp", "lastIndexTimestamp")


async def records_updated_since(
    graph_provider: "IGraphDBProvider",
    org_id: str,
    record_group_id: str,
    since_ms: int,
) -> set[str]:
    """Keys of the group's records changed by a connector or re-indexed after the watermark."""
    keys: set[str] = set()
    for field in _CHANGE_STAMPS:
        rows = await graph_provider.get_nodes_updated_since(
            collection=CollectionNames.RECORDS.value,
            timestamp_field=field,
            since=since_ms - WATERMARK_MARGIN_MS,
            filters={"orgId": org_id, "recordGroupId": record_group_id},
            return_fields=["_key"],
            # An unreadable graph must not read as "nothing changed": the build
            # would settle and clear the request it was asked to serve.
            raise_on_error=True,
        )
        keys.update(row["_key"] for row in rows if isinstance(row.get("_key"), str))
    return keys


class LeaseLostError(RuntimeError):
    """This build no longer holds the repo's lock; another may be writing its edges."""


def _pg_state():  # noqa: ANN202
    """The database lock in ``REDIS_MODE=postgres`` (``None`` otherwise): the same lease, in ``platform.kv_entries``."""
    from app.services.redis.connection_provider_factory import uses_pg_state

    if not uses_pg_state():
        return None
    from app.services.pg_state import PgState

    return PgState()


async def assert_lease_held(redis: "Redis", lock_key: str, lock_token: str) -> None:
    """Fence a write: raise unless this build still owns the lock (O-6)."""
    pg = _pg_state()
    if pg is not None:
        if await pg.lock_owner(lock_key) != lock_token:
            raise LeaseLostError(f"code edge build lost its lock {lock_key}")
        return
    held = await redis.get(lock_key)
    if isinstance(held, bytes):
        held = held.decode()
    if held != lock_token:
        raise LeaseLostError(f"code edge build lost its lock {lock_key}")


async def acquire_build_lock(
    redis: "Redis",
    org_id: str,
    record_group_id: str,
) -> tuple[str, str] | None:
    """``(lock_key, lock_token)`` if this caller now owns the group's build, else None.

    Pair with ``renew_build_lock_until_cancelled`` for the build's duration and
    ``release_build_lock`` afterwards.
    """
    lock_key = f"{BUILD_LOCK_PREFIX}{org_id}:{record_group_id}"
    lock_token = str(uuid4())
    pg = _pg_state()
    if pg is not None:
        acquired = await pg.lock_acquire(lock_key, lock_token, BUILD_LOCK_TTL_SECONDS)
        return (lock_key, lock_token) if acquired else None
    acquired = await redis.set(
        lock_key, lock_token, nx=True, ex=BUILD_LOCK_TTL_SECONDS
    )
    return (lock_key, lock_token) if acquired else None


async def release_build_lock(
    redis: "Redis",
    lock_key: str,
    lock_token: str,
) -> bool:
    """Drop the lease only if it is still ours; a lease that expired and was
    re-taken belongs to the build that took it."""
    pg = _pg_state()
    if pg is not None:
        return await pg.lock_release(lock_key, lock_token)
    return bool(
        await redis.eval(RELEASE_LOCK_IF_OWNER_LUA, 1, lock_key, lock_token)
    )


async def claim_publish(
    redis: "Redis",
    org_id: str,
    record_group_id: str,
) -> bool:
    """Win the right to ask for this group's build, once per dedupe window."""
    pg = _pg_state()
    if pg is not None:   # a fresh owner each time, so only an absent or expired row can be taken
        return await pg.lock_acquire(
            f"{_PUBLISH_DEDUPE_PREFIX}{org_id}:{record_group_id}", str(uuid4()), _PUBLISH_DEDUPE_TTL_SECONDS
        )
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
            pg = _pg_state()
            if not (
                await pg.lock_renew(lock_key, lock_token, BUILD_LOCK_TTL_SECONDS)
                if pg is not None
                else await redis.eval(REFRESH_LOCK_IF_OWNER_LUA, 1, lock_key, lock_token, BUILD_LOCK_TTL_SECONDS)
            ):
                log.error(
                    "Code edge build lost its lock %s; another replica may be "
                    "rebuilding the same edges. Stopping renewal.",
                    lock_key,
                )
                return
        except Exception:
            log.exception("Failed to renew code edge build lock %s", lock_key)
