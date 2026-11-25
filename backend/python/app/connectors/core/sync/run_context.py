"""Per-run identifier and tally for a sync.

A sync runs in the connectors process; the indexing process consumes the
`record-events` it publishes. A ContextVar set around a single `run_sync()` call
lets the record-event producers stamp the run's id onto every message without
threading it through each connector's signature, so downstream processing can
be correlated back to the run that produced it. ContextVars propagate into tasks
created after they are set, so one `set_run_sync_id` around the run covers the
whole downstream call tree. The run's `SyncRunStats` travels the same way, by
reference, so every task of the run adds to the one object its `sync_run.end`
line reads.
"""

from collections import Counter
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum

_run_sync_id: ContextVar[str | None] = ContextVar("run_sync_id", default=None)


def set_run_sync_id(run_sync_id: str) -> Token:
    return _run_sync_id.set(run_sync_id)


def get_run_sync_id() -> str | None:
    return _run_sync_id.get()


def reset_run_sync_id(token: Token) -> None:
    _run_sync_id.reset(token)


class RecordOutcome(StrEnum):
    NEW = "new"                # not stored before, or stored only as a placeholder
    UPDATED = "updated"        # stored before; its source revision changed, or it came back from the trash
    UNCHANGED = "unchanged"    # stored before with the same source revision, and saved again
    DELETED = "deleted"        # removed or sent to the trash, descendants included
    FAILED = "failed"          # a batch that failed for good, a record the schema refused, a delete the store refused


class SkipReason(StrEnum):
    """Why the processor did not store a record. Closed, so the field names stay bounded."""

    OWNED_ELSEWHERE = "owned_elsewhere"   # its id belongs to another org or connector
    IN_TRASH = "in_trash"                 # a user deleted it; the delete holds until the purge
    NOT_CONTENT = "not_content"           # a system file (lock file, Thumbs.db, ...)


@dataclass
class SyncRunStats:
    """What one sync run did, for its sync_run.end line."""

    records: Counter[RecordOutcome] = field(default_factory=Counter)
    skipped: Counter[SkipReason] = field(default_factory=Counter)
    backoff_wait_s: float = 0.0

    def add(self, other: "SyncRunStats") -> None:
        """Fold a committed transaction's record counts into this tally."""
        self.records.update(other.records)
        self.skipped.update(other.skipped)

    def end_fields(self) -> dict[str, int | None]:
        """The sync_run.end fields, None (left out of the line) for a zero."""
        return {
            **{f"records_{outcome}": self.records[outcome] or None for outcome in RecordOutcome},
            "records_skipped": self.skipped.total() or None,
            **{f"skipped_{reason}": self.skipped[reason] or None for reason in SkipReason},
            "backoff_wait_ms": round(self.backoff_wait_s * 1000) or None,
        }


_run_stats: ContextVar[SyncRunStats | None] = ContextVar("run_sync_stats", default=None)


def bind_run_stats(stats: SyncRunStats) -> Token:
    return _run_stats.set(stats)


def current_run_stats() -> SyncRunStats | None:
    return _run_stats.get()


def reset_run_stats(token: Token) -> None:
    _run_stats.reset(token)


def note_backoff_wait(seconds: float) -> None:
    """Add time this run's own calls kept its backoff gate shut. Outside a run it does nothing."""
    stats = _run_stats.get()
    if stats is not None and seconds > 0:
        stats.backoff_wait_s += seconds
