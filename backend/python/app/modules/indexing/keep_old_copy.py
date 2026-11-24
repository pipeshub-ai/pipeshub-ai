"""A re-index keeps serving the record's old copy until the new one is committed.

A run that replaces a record's indexed copy writes the new set under a fresh virtual record (Y) while the record keeps
naming the old one (X). Y is recorded on the record as ``pendingVirtualRecordId``. One conditional write at the end moves
``virtualRecordId`` to Y, marks the record COMPLETED and removes the pending field; X is released afterwards. A run that
fails at any earlier point leaves X untouched and served.

Who holds what: a record holds the virtual record it names, and the pending one only while it is IN_PROGRESS or QUEUED
(``record_visibility.holds_vrid``), so every sweep and delete that asks "does anything still hold this?" leaves a running
copy alone, and any other end state of the record releases it.

The pending write is switched on by ``INDEXING_KEEP_OLD_COPY`` (off by default) and refused unless the indexing fleet is
confirmed to follow this protocol (``swap_allowed``).
"""
from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import ProgressStatus
from app.services.graph_db.common.record_visibility import PENDING_VIRTUAL_RECORD_ID
from app.plugins.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger("keep_old_copy")
swap_refusals = METRICS_BACKEND.counter(
    "pipeshub_keep_old_copy_refused_total",
    "Runs that used the order they always had because the fleet could not be confirmed for keeping the old copy", ["reason"],
)

ENV_ENABLED = "INDEXING_KEEP_OLD_COPY"
#: Added to the accessible-records cache TTL for the time an old copy is kept after its commit.
GRACE_MARGIN_SECONDS = 30.0
RETIRED_AT_FIELD = "retiredAtMs"


def enabled() -> bool:
    return os.getenv(ENV_ENABLED, "off").strip().lower() in {"1", "true", "yes", "on"}


def grace_seconds() -> float:
    """How long a retired copy is kept: the accessible-records cache's effective TTL plus a margin (derived, not a setting)."""
    from app.services.cache.accessible_records_cache import (
        AccessibleRecordsCache,
        _ttl_from_env,
    )
    from app.services.redis.connection_provider_factory import uses_pg_state

    ttl = _ttl_from_env(AccessibleRecordsCache.DEFAULT_TTL_SECONDS)
    if uses_pg_state():
        ttl = min(ttl, AccessibleRecordsCache.PG_STATE_TTL_SECONDS)
    return float(ttl) + GRACE_MARGIN_SECONDS


ENV_PG_BROKER_CONFIRMED = "INDEXING_KEEP_OLD_COPY_FLEET_CONFIRMED"
_refusals_logged: set[str] = set()


def _refused(reason: str, detail: str = "") -> bool:
    """Counted every time, logged once per process and reason (never once per record)."""
    swap_refusals.inc(reason)
    if reason not in _refusals_logged:
        _refusals_logged.add(reason)
        logger.warning("Keep-old-copy swap refused (%s)%s; runs use the order they always had", reason, f": {detail}" if detail else "")
    return False


async def _postgres_broker_allowed() -> bool:
    """The PostgreSQL broker keeps no member list: a pod that holds no claim leaves no trace in its queue tables. So the answer has two
    parts: no live claim by a consumer without the protocol tag (an older pod that is working), and the operator's statement that no
    older pod runs at all (``INDEXING_KEEP_OLD_COPY_FLEET_CONFIRMED``), without which the swap stays refused."""
    if os.getenv(ENV_PG_BROKER_CONFIRMED, "off").strip().lower() not in {"1", "true", "yes", "on"}:
        return _refused("pg_broker_unconfirmed", f"set {ENV_PG_BROKER_CONFIRMED} once no older pod runs")
    try:
        from app.services.messaging.membership import groups_to_check
        from app.services.messaging.postgres.fleet_claims import untagged_claimants
        from app.services.messaging.storage_protocol import STORAGE_PROTOCOL_TAG

        groups = sorted(groups_to_check())
        if not groups:
            return _refused("indexing_groups_unknown", "this process has not declared its consumer groups")
        # only the indexing groups: a connector service's consumers never carry the tag and must not refuse the swap
        untagged = await untagged_claimants(STORAGE_PROTOCOL_TAG, groups)
    except Exception as exc:
        return _refused("pg_broker_unreadable", type(exc).__name__)
    if untagged:
        return _refused("older_consumer", ", ".join(untagged))
    return True


async def swap_allowed(config_service: Any) -> bool:  # noqa: ANN401
    """The flag is on and the fleet is confirmed to count a pending virtual record as held.

    Confirmed means: a fresh membership result checked every group, no live consumer (idle ones included) lacks the protocol
    tag. Anything unknown, an unreadable result, no config service, refuses: the run then uses the order it always had."""
    if not enabled():
        return False
    from app.services.messaging.config import MessageBrokerType, get_message_broker_type

    if get_message_broker_type() == MessageBrokerType.POSTGRES:
        return await _postgres_broker_allowed()
    try:
        from app.services.messaging.membership import reader_for

        reader = reader_for(config_service)
        if reader is None:
            return _refused("no_membership")
        verdict = await reader.strict_verdict()
    except Exception as exc:
        return _refused("membership_unreadable", type(exc).__name__)
    if not verdict.ok:
        return _refused(verdict.reason or "unconfirmed", ", ".join(verdict.untagged))
    return True


def pending_of(doc: Mapping[str, Any]) -> str | None:
    value = doc.get(PENDING_VIRTUAL_RECORD_ID)
    return value if isinstance(value, str) and value else None


#: Statuses a record can have while a run of its own replaces its copy.
RUNNING = (ProgressStatus.IN_PROGRESS.value, ProgressStatus.QUEUED.value)
#: The statuses the commit accepts: running, or reset to NOT_STARTED by a save made mid-run.
COMMITTABLE = (*RUNNING, ProgressStatus.NOT_STARTED.value)


# ---------------------------------------------------------------------------------------------------------------------
# The run in flight, for the commit (set where the pending vrid is written, read where the record is committed)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SwapRun:
    """A run that writes ``new_vrid`` while its record keeps naming ``old_vrid``."""

    record_id: str
    org_id: str
    old_vrid: str
    new_vrid: str
    forced: bool = False


_swap: ContextVar[SwapRun | None] = ContextVar("keep_old_copy_swap", default=None)


def current_swap(record_id: str | None = None, vrid: str | None = None) -> SwapRun | None:
    """The run's swap, when it is for this record (and writes this vrid, when given)."""
    run = _swap.get()
    if run is None or (record_id is not None and run.record_id != record_id) or (vrid is not None and run.new_vrid != vrid):
        return None
    return run


def begin_swap(run: SwapRun) -> None:
    _swap.set(run)


def end_swap() -> None:
    # Set back rather than reset by token: an async generator closed from another context cannot reset.
    _swap.set(None)
