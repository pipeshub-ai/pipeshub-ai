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

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger("keep_old_copy")

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


async def swap_allowed(config_service: Any) -> bool:  # noqa: ANN401
    """The flag is on and the fleet is confirmed to count a pending virtual record as held.

    Confirmed means: a fresh membership result checked every group, no live consumer (idle ones included) lacks the protocol
    tag. Anything unknown, an unreadable result, no config service, refuses: the run then uses the order it always had."""
    if not enabled():
        return False
    try:
        from app.services.messaging.membership import reader_for

        reader = reader_for(config_service)
        if reader is None:
            return False
        verdict = await reader.strict_verdict()
    except Exception as exc:
        logger.warning("Keep-old-copy swap refused: the fleet could not be confirmed (%s)", type(exc).__name__)
        return False
    if not verdict.ok:
        logger.warning(
            "Keep-old-copy swap refused: fleet not confirmed (%s%s); the old order is used",
            verdict.reason, f": {', '.join(verdict.untagged)}" if verdict.untagged else "",
        )
    return verdict.ok


def pending_of(doc: Mapping[str, Any]) -> str | None:
    value = doc.get(PENDING_VIRTUAL_RECORD_ID)
    return value if isinstance(value, str) and value else None


#: Statuses a record can have while a run of its own replaces its copy.
RUNNING = (ProgressStatus.IN_PROGRESS.value, ProgressStatus.QUEUED.value)
#: The statuses the commit accepts: running, or reset to NOT_STARTED by a save made mid-run (R3-1).
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
