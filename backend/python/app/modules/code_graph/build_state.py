"""A repo's code-graph build state, kept on its ``codeEdgeBuild`` sync point.

``BuildStateStore`` is the only code that reads or writes that sync point: the
indexing side marks a build as owed, a build begins and settles, the reconciler
reads what is owed and what is due, and the API reports it. A build's settle is a
compare-and-set on the lease token it began under and the request ids it read, so
a build that lost its lease, or a request that lands while it settles, is never
overwritten (O-6).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph.version import EDGE_RULES_VERSION
from app.utils.env_config import env_int

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.modules.code_graph.edge_builder import EdgeBuildResult
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "BuildOutcome",
    "BuildState",
    "BuildStateStore",
    "GraphStatus",
    "claimed_build_already_ran",
    "PendingBuild",
    "RetryPolicy",
    "SYNC_POINT_SUFFIX",
    "SYNC_POINT_TYPE",
    "full_build_still_owed",
    "graph_status",
    "is_due",
    "needs_full_build",
    "new_request_id",
    "record_group_id_from_key",
    "request_is_stale",
    "still_owed",
    "sync_point_key_for",
]

SYNC_POINT_SUFFIX = "code-edge-build"
SYNC_POINT_TYPE = "codeEdgeBuild"
# A request younger than this still has its message in flight (the publish
# dedupe window in edge_build_trigger is the same length).
REQUEST_IN_FLIGHT_MS = 60_000
# A repo whose files are still indexing is asked about again by the request its
# last file makes; the reconciler only needs to look back now and then.
_DEFER_MS = 30 * 60_000
_SETTLE_ATTEMPTS = 3


class BuildOutcome(str, Enum):
    """What the last build attempt did; stored as ``edgeBuildStatus``."""

    BUILDING = "building"
    READY = "ready"
    WAITING_ON_INDEXING = "waiting_on_indexing"
    RETRYING = "retrying"
    FAILED = "failed"
    TOO_LARGE = "too_large"


class GraphStatus(str, Enum):
    """What a reader of the repo's graph should be told about it."""

    NEVER_BUILT = "never_built"
    BUILDING = "building"
    READY = "ready"
    # Built, and a newer change is waiting for the next build.
    STALE = "stale"
    WAITING_ON_INDEXING = "waiting_on_indexing"
    RETRYING = "retrying"
    FAILED = "failed"
    TOO_LARGE = "too_large"


class BuildState(NamedTuple):
    last_build: int | None
    pending: bool
    requested_at: int | None
    # Unique per request; a build compares it with the one it started under,
    # so no clock has to agree with another service's.
    request_id: str | None = None
    # Set when a change only a full rebuild sees -- a deleted code file leaves
    # no record for an incremental build to start from. Cleared, by the same
    # id comparison, by the full build that served it.
    full_request_id: str | None = None
    outcome: str | None = None
    attempts: int = 0
    next_attempt_at: int | None = None
    last_error: str | None = None
    lease_token: str | None = None
    last_full_build: int | None = None
    # EDGE_RULES_VERSION of the last full build; another means rebuild in full.
    build_version: str | None = None


class PendingBuild(NamedTuple):
    org_id: str
    connector_id: str
    record_group_id: str
    state: BuildState


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with jitter, ending in a terminal failure (O-5)."""

    base_ms: int = 60_000
    max_ms: int = 6 * 3_600_000
    max_failures: int = 6

    def next_attempt_at(self, failures: int, now_ms: int,
                        rand: Callable[[], float] = random.random) -> int | None:
        """When to retry after the ``failures``-th consecutive failure; None means give up."""
        if failures >= self.max_failures:
            return None
        delay = min(self.max_ms, self.base_ms * 2 ** max(0, failures - 1))
        return now_ms + int(delay * (1 + 0.2 * rand()))


DEFAULT_RETRY_POLICY = RetryPolicy(
    max_failures=max(1, env_int("CODE_EDGE_BUILD_MAX_FAILURES", 6)),
)


def sync_point_key_for(record_group_id: str) -> str:
    return f"{record_group_id}/{SYNC_POINT_SUFFIX}"


def record_group_id_from_key(sync_point_key: object) -> str | None:
    """Inverse of ``sync_point_key_for``; None for a key of another type."""
    if not isinstance(sync_point_key, str):
        return None
    record_group_id, sep, suffix = sync_point_key.rpartition("/")
    if not sep or suffix != SYNC_POINT_SUFFIX or not record_group_id:
        return None
    return record_group_id


def new_request_id() -> str:
    return uuid4().hex


def still_owed(state: BuildState, started_at_ms: int,
               started_request_id: str | None = None) -> bool:
    """Whether a build leaves one owed: someone asked again while it ran.

    That request was for records this build may not have seen, so clearing
    ``edgeBuildPending`` there would erase it. Decided by request id when there
    is one: comparing ``edgeBuildRequestedAt`` (the indexing service's clock)
    with ``started_at_ms`` (this service's) loses a request whenever the two
    clocks disagree by more than the gap between them. A mark written before
    request ids existed falls back to that comparison.
    """
    if state.request_id is not None or started_request_id is not None:
        return state.pending and state.request_id != started_request_id
    return state.requested_at is not None and state.requested_at > started_at_ms


def full_build_still_owed(state: BuildState, started_full_request_id: str | None) -> bool:
    """Whether a full rebuild is still owed after a build that started under *started_full_request_id*.

    Only a full build serves the request it saw; one asked for while it ran
    carries a new id and stays.
    """
    return state.full_request_id is not None and state.full_request_id != started_full_request_id


def request_is_stale(state: BuildState, now_ms: int) -> bool:
    """A pending build whose request is older than the dedupe window.

    Such a request is presumed lost, so the dedupe claim must not suppress the
    next one. A pending mark with no timestamp predates the field and is
    treated the same way.
    """
    if not state.pending:
        return False
    if state.requested_at is None:
        return True
    return now_ms - state.requested_at > REQUEST_IN_FLIGHT_MS


def claimed_build_already_ran(state: BuildState, now_ms: int) -> bool:
    """The build the current dedupe claim was taken for has started and settled.

    A request that loses the claim relies on that build reading it. When the
    build already started inside the window and none is running, nothing will:
    the request sat pending until the reconciler's sweep (up to 10 min, B3).
    """
    return (state.outcome != BuildOutcome.BUILDING.value and state.last_build is not None
            and now_ms - state.last_build < REQUEST_IN_FLIGHT_MS)


def is_due(state: BuildState, now_ms: int) -> bool:
    """Whether the reconciler should start this owed build now.

    A terminal failure waits for a new request, which the indexing side sends
    on the repo's next change; backoff holds a retry until its time.
    """
    if not request_is_stale(state, now_ms) or state.outcome == BuildOutcome.FAILED.value:
        return False
    return state.next_attempt_at is None or state.next_attempt_at <= now_ms


def needs_full_build(state: BuildState) -> bool:
    """A full rebuild is owed, never built, or the last one ran under another version."""
    return (state.full_request_id is not None or state.last_build is None
            or state.build_version != EDGE_RULES_VERSION)


def graph_status(state: BuildState | None) -> GraphStatus:
    """The one status a person or an agent is told for a repo's graph."""
    if state is None:
        return GraphStatus.NEVER_BUILT
    if state.outcome in (BuildOutcome.FAILED.value, BuildOutcome.RETRYING.value,
                         BuildOutcome.TOO_LARGE.value, BuildOutcome.BUILDING.value,
                         BuildOutcome.WAITING_ON_INDEXING.value):
        return GraphStatus(state.outcome)
    if state.last_build is None:
        return GraphStatus.NEVER_BUILT
    return GraphStatus.STALE if state.pending else GraphStatus.READY


_STATE_FIELDS = (
    "lastEdgeBuildAt", "edgeBuildPending", "edgeBuildRequestedAt", "edgeBuildRequestId",
    "edgeBuildFullRequestId", "edgeBuildStatus", "edgeBuildAttempts", "edgeBuildNextAttemptAt",
    "edgeBuildLastError", "edgeBuildLeaseToken", "lastFullEdgeBuildAt", "edgeBuildVersion",
)


def _ms_or_none(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _state_from_row(row: dict[str, Any]) -> BuildState:
    return BuildState(
        _ms_or_none(row.get("lastEdgeBuildAt")),
        bool(row.get("edgeBuildPending")),
        _ms_or_none(row.get("edgeBuildRequestedAt")),
        _str_or_none(row.get("edgeBuildRequestId")),
        _str_or_none(row.get("edgeBuildFullRequestId")),
        _str_or_none(row.get("edgeBuildStatus")),
        _ms_or_none(row.get("edgeBuildAttempts")) or 0,
        _ms_or_none(row.get("edgeBuildNextAttemptAt")),
        _str_or_none(row.get("edgeBuildLastError")),
        _str_or_none(row.get("edgeBuildLeaseToken")),
        _ms_or_none(row.get("lastFullEdgeBuildAt")),
        _str_or_none(row.get("edgeBuildVersion")),
    )


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value)


class BuildStateStore:
    """Reads and writes a repo's ``codeEdgeBuild`` sync point."""

    def __init__(self, graph_provider: IGraphDBProvider, log: Logger | None = None,
                 retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY) -> None:
        self._graph = graph_provider
        self._log = log
        self._retry = retry_policy

    # -- reads ------------------------------------------------------------

    async def read(self, org_id: str, record_group_id: str) -> BuildState | None:
        """The repo's state; None when it has never been asked for a build."""
        rows = await self._graph.get_nodes_by_filters(
            collection=CollectionNames.SYNC_POINTS.value,
            filters={"orgId": org_id, "syncPointKey": sync_point_key_for(record_group_id)},
            return_fields=list(_STATE_FIELDS),
            # Unreadable must not read as "never built, nothing owed": a finishing
            # build would clear a request it never saw.
            raise_on_error=True,
        )
        return _state_from_row(rows[0]) if rows else None

    async def read_or_empty(self, org_id: str, record_group_id: str) -> BuildState:
        return await self.read(org_id, record_group_id) or BuildState(None, False, None)

    async def all(self, *, owed_only: bool = False) -> list[PendingBuild]:
        """Every repo's state across all orgs; with ``owed_only``, those still marked pending.

        The mark is written before the request is published and cleared only
        when a build settles, so a build that died with its process, or whose
        request was dead-lettered, is still visible here afterwards.
        """
        filters: dict[str, Any] = {"syncDataPointType": SYNC_POINT_TYPE}
        if owed_only:
            filters["edgeBuildPending"] = True
        rows = await self._graph.get_nodes_by_filters(
            collection=CollectionNames.SYNC_POINTS.value,
            filters=filters,
            return_fields=["orgId", "connectorId", "syncPointKey", *_STATE_FIELDS],
            raise_on_error=True,
        )
        out: list[PendingBuild] = []
        for row in rows or []:
            record_group_id = record_group_id_from_key(row.get("syncPointKey"))
            org_id, connector_id = row.get("orgId"), row.get("connectorId")
            if not record_group_id or not _non_empty(org_id) or not _non_empty(connector_id):
                continue
            out.append(PendingBuild(str(org_id), str(connector_id), record_group_id,
                                    _state_from_row(row)))
        return out

    # -- requests ---------------------------------------------------------

    async def mark_requested(self, org_id: str, connector_id: str, record_group_id: str,
                             now_ms: int) -> None:
        """Mark a build as owed. Written on every request, before any publish."""
        await self._upsert(org_id, connector_id, record_group_id, {
            "edgeBuildPending": True,
            "edgeBuildRequestedAt": now_ms,
            "edgeBuildRequestId": new_request_id(),
        })

    async def request_full(self, org_id: str, connector_id: str, record_group_id: str) -> None:
        """Owe a full rebuild: a change an incremental build cannot see."""
        await self._upsert(org_id, connector_id, record_group_id,
                           {"edgeBuildFullRequestId": new_request_id()})

    # -- one build's lifecycle ---------------------------------------------

    async def begin(self, org_id: str, connector_id: str, record_group_id: str,
                    lease_token: str) -> BuildState:
        """Claim the state for this build's lease and return what it starts from."""
        await self._upsert(org_id, connector_id, record_group_id, {
            "edgeBuildStatus": BuildOutcome.BUILDING.value,
            "edgeBuildLeaseToken": lease_token,
        })
        return await self.read_or_empty(org_id, record_group_id)

    async def settle_built(self, org_id: str, record_group_id: str, *, lease_token: str,
                           start: BuildState, started_at_ms: int, advance_watermark: bool,
                           full: bool, result: EdgeBuildResult | None = None) -> bool:
        """A build ran to the end (or found nothing to do)."""
        def data(now: BuildState) -> dict[str, Any]:
            out: dict[str, Any] = {
                "edgeBuildStatus": BuildOutcome.READY.value,
                "edgeBuildPending": still_owed(now, started_at_ms, start.request_id),
                "edgeBuildAttempts": 0,
                "edgeBuildNextAttemptAt": None,
                "edgeBuildLastError": None,
            }
            if advance_watermark:
                out["lastEdgeBuildAt"] = started_at_ms
                if full:
                    out["lastFullEdgeBuildAt"] = started_at_ms
                    out["edgeBuildVersion"] = EDGE_RULES_VERSION
                    if not full_build_still_owed(now, start.full_request_id):
                        out["edgeBuildFullRequestId"] = None
            if result is not None:
                out["edgeBuildLastDurationMs"] = int(result.duration_s * 1000)
                out["edgeBuildLastEdgesWritten"] = result.edges_written
                out["edgeBuildLastEdgesRemoved"] = result.edges_removed
                out["edgeBuildLastFilesReresolved"] = result.files_reresolved
            return out
        return await self._settle(org_id, record_group_id, lease_token, data)

    async def settle_deferred(self, org_id: str, record_group_id: str, *, lease_token: str,
                              now_ms: int) -> bool:
        """Files are still indexing; the last one to finish asks again."""
        return await self._settle(org_id, record_group_id, lease_token, lambda _: {
            "edgeBuildStatus": BuildOutcome.WAITING_ON_INDEXING.value,
            "edgeBuildNextAttemptAt": now_ms + _DEFER_MS,
        })

    async def settle_failed(self, org_id: str, record_group_id: str, *, lease_token: str,
                            now_ms: int, error: BaseException) -> bool:
        """Count the failure and schedule the retry, or give up (O-5). The build stays owed."""
        def data(now: BuildState) -> dict[str, Any]:
            failures = now.attempts + 1
            retry_at = self._retry.next_attempt_at(failures, now_ms)
            return {
                "edgeBuildStatus": (BuildOutcome.RETRYING if retry_at is not None
                                    else BuildOutcome.FAILED).value,
                "edgeBuildPending": True,
                "edgeBuildAttempts": failures,
                "edgeBuildNextAttemptAt": retry_at,
                "edgeBuildLastError": f"{type(error).__name__}: {error}"[:500],
            }
        return await self._settle(org_id, record_group_id, lease_token, data)

    async def settle_too_large(self, org_id: str, record_group_id: str, *, lease_token: str,
                               start: BuildState, started_at_ms: int, reason: str) -> bool:
        """The repo is past the size a build may load; retrying would not change that."""
        return await self._settle(org_id, record_group_id, lease_token, lambda now: {
            "edgeBuildStatus": BuildOutcome.TOO_LARGE.value,
            "edgeBuildPending": still_owed(now, started_at_ms, start.request_id),
            "edgeBuildNextAttemptAt": None,
            "edgeBuildLastError": reason[:500],
        })

    # -- internals ---------------------------------------------------------

    async def _upsert(self, org_id: str, connector_id: str, record_group_id: str,
                      data: dict[str, Any]) -> None:
        await self._graph.upsert_sync_point(
            sync_point_key=sync_point_key_for(record_group_id),
            sync_point_data={"orgId": org_id, "connectorId": connector_id,
                             "syncDataPointType": SYNC_POINT_TYPE, **data},
            collection=CollectionNames.SYNC_POINTS.value,
        )

    async def _settle(self, org_id: str, record_group_id: str, lease_token: str,
                      data_for: Callable[[BuildState], dict[str, Any]]) -> bool:
        """Write ``data_for(current state)`` only if this lease still owns the state
        and no request landed since it was read; re-read and retry if one did.

        Returns False when another build's lease has taken the state over.
        """
        for _ in range(_SETTLE_ATTEMPTS):
            now = await self.read_or_empty(org_id, record_group_id)
            if now.lease_token != lease_token:
                if self._log:
                    self._log.warning(
                        "Code edge build for record_group=%s lost its state to another "
                        "build; not settling", record_group_id,
                    )
                return False
            applied = await self._graph.update_sync_point_if(
                sync_point_key=sync_point_key_for(record_group_id),
                expected={
                    "edgeBuildLeaseToken": lease_token,
                    "edgeBuildRequestId": now.request_id,
                    "edgeBuildFullRequestId": now.full_request_id,
                },
                sync_point_data={**data_for(now), "edgeBuildLeaseToken": None},
                collection=CollectionNames.SYNC_POINTS.value,
            )
            if applied:
                return True
        raise RuntimeError(
            f"Could not settle the code edge build for {record_group_id}: its state "
            f"kept changing for {_SETTLE_ATTEMPTS} attempts"
        )
