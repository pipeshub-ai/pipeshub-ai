"""What one connector sync is doing while it runs, and what it did once it ends.

One ``SyncProgress`` per run, created by ``run_sync_task`` and published on a
ContextVar so the shared record processor, the backoff gate and the connector
itself can report into it without threading it through every signature. The
runner writes its snapshot to the connector's app doc every few seconds
(``syncProgress``), and at the end folds it into the run history (``syncRuns``).

Counts come from what the processor actually wrote, never from guesses: a
record is counted once per run however many times the connector hands it over
(a Drive file shared with fifty users reaches the processor fifty times), and a
total is only shown when the connector knows one. Indexing progress is not
counted here at all; it is read from record state (see ``sync_progress_view``),
so a redelivered message can never be counted twice.
"""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.utils.time_conversion import get_epoch_timestamp_in_ms


class SyncPhase(str, Enum):
    STARTING = "starting"
    PEOPLE = "people"
    CONTAINERS = "containers"
    CONTENT = "content"
    FINISHING = "finishing"


class SyncTrigger(str, Enum):
    """What asked for the run; shown in the run history."""

    MANUAL = "manual"
    SCHEDULE = "schedule"
    ENABLE = "enable"
    STARTUP = "startup"
    RESUME = "resume"
    FOLLOW_UP = "follow_up"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: object) -> "SyncTrigger":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN


class ChangeKind(str, Enum):
    NEW = "new"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


# Dedupe memory is bounded: past this many distinct records in one run, a record
# seen again is counted again and the snapshot says the counts are approximate.
# Ids are kept as hashes, about 50 bytes each, so the cap costs ~25 MB at most.
_DEDUPE_LIMIT = 500_000

# Pauses shorter than this are ordinary retry jitter, not worth telling anyone about.
MIN_REPORTED_THROTTLE_SECONDS = 10.0

SYNC_RUNS_KEPT = 10


@dataclass
class SyncProgress:
    connector_id: str
    run_id: str
    full: bool
    trigger: SyncTrigger = SyncTrigger.UNKNOWN
    started_at: int = field(default_factory=get_epoch_timestamp_in_ms)
    phase: SyncPhase = SyncPhase.STARTING
    last_activity_at: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    removed: int = 0
    containers: int = 0
    filtered_by_reason: dict[str, int] = field(default_factory=dict)
    unit_kind: str | None = None
    units_done: int = 0
    units_total: int | None = None
    expected_total: int | None = None
    throttled_until: int | None = None
    approximate: bool = False
    #: Bumped on every change, so the writer can skip a write that would say nothing new.
    version: int = 0
    closed: bool = False
    _seen_unchanged: set[int] = field(default_factory=set, repr=False)
    _seen_other: set[int] = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        if not self.last_activity_at:
            self.last_activity_at = self.started_at

    # ---- reporting -------------------------------------------------------

    def touch(self) -> None:
        """Note that the run made headway without anything countable (a checkpoint, a page read)."""
        if self.closed:
            return
        self.last_activity_at = get_epoch_timestamp_in_ms()
        self.version += 1

    def enter_phase(self, phase: SyncPhase) -> None:
        if self.closed:
            return
        if self.phase != phase:
            self.phase = phase
        self.touch()

    def note_record(self, record_id: str | None, kind: ChangeKind) -> None:
        """Count a record the processor wrote, once per run.

        A record first seen unchanged and later seen changed (a metadata pass
        followed by a content pass in the same run) moves to changed; one
        already counted new or changed stays where it is.
        """
        if self.closed or not record_id:
            return
        key = hash(record_id)
        if key in self._seen_other:
            self.touch()
            return
        if key in self._seen_unchanged:
            if kind is ChangeKind.UNCHANGED:
                self.touch()
                return
            self._seen_unchanged.discard(key)
            self.unchanged -= 1
            self._remember(self._seen_other, key)
        elif kind is ChangeKind.UNCHANGED:
            self._remember(self._seen_unchanged, key)
        else:
            self._remember(self._seen_other, key)
        if kind is ChangeKind.NEW:
            self.new += 1
        elif kind is ChangeKind.CHANGED:
            self.changed += 1
        else:
            self.unchanged += 1
        if self.phase in (SyncPhase.STARTING, SyncPhase.PEOPLE, SyncPhase.CONTAINERS):
            self.phase = SyncPhase.CONTENT
        self.touch()

    def _remember(self, bucket: set[int], key: int) -> None:
        if len(self._seen_unchanged) + len(self._seen_other) >= _DEDUPE_LIMIT:
            self.approximate = True
            return
        bucket.add(key)

    def note_removed(self, count: int = 1) -> None:
        if self.closed or count <= 0:
            return
        self.removed += count
        self.touch()

    def note_containers(self, count: int) -> None:
        if self.closed or count <= 0:
            return
        self.containers += count
        if self.phase in (SyncPhase.STARTING, SyncPhase.PEOPLE):
            self.phase = SyncPhase.CONTAINERS
        self.touch()

    def note_filtered(self, reason: str, count: int = 1) -> None:
        """Count items the connector's sync filters left out, by reason (``extension``, ``date``, ...)."""
        if self.closed or count <= 0:
            return
        self.filtered_by_reason[reason] = self.filtered_by_reason.get(reason, 0) + count
        self.touch()

    def set_units(self, kind: str, done: int, total: int | None = None) -> None:
        """Report progress through the connector's natural unit: 3 of 12 mailboxes, 40 of 90 tables.

        ``kind`` is a singular noun the UI knows how to name (``user``,
        ``mailbox``, ``drive``, ``site``, ``bucket``, ``share``, ``channel``,
        ``space``, ``project``, ``repository``, ``table``, ``feed``, ``page``);
        anything else is shown generically.
        """
        if self.closed:
            return
        self.unit_kind = kind
        self.units_done = max(0, done)
        self.units_total = None if total is None else max(0, total)
        self.touch()

    def advance_units(self, kind: str, by: int = 1, total: int | None = None) -> None:
        if self.closed:
            return
        if self.unit_kind != kind:
            self.unit_kind = kind
            self.units_done = 0
        self.units_done += by
        if total is not None:
            self.units_total = total
        self.touch()

    def expect_total(self, total: int | None) -> None:
        """How many items the source says it holds, when it says so cheaply."""
        if self.closed:
            return
        self.expected_total = None if total is None else max(0, total)
        self.touch()

    def note_throttled(self, seconds: float) -> None:
        """The source asked us to slow down; nothing will happen for ``seconds``."""
        if self.closed or seconds < MIN_REPORTED_THROTTLE_SECONDS:
            return
        until = get_epoch_timestamp_in_ms() + int(seconds * 1000)
        self.throttled_until = max(self.throttled_until or 0, until)
        self.version += 1

    def close(self) -> None:
        self.closed = True
        self._seen_unchanged.clear()
        self._seen_other.clear()

    # ---- reading ---------------------------------------------------------

    def counts(self) -> dict[str, int]:
        return {
            "found": self.new + self.changed + self.unchanged,
            "new": self.new,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "filtered": sum(self.filtered_by_reason.values()),
            "containers": self.containers,
        }

    def snapshot(self) -> dict[str, Any]:
        """The live view stored as ``syncProgress``; camelCase, JSON-safe."""
        now = get_epoch_timestamp_in_ms()
        return {
            "runId": self.run_id,
            "trigger": self.trigger.value,
            "full": self.full,
            "startedAt": self.started_at,
            "phase": self.phase.value,
            "lastActivityAt": self.last_activity_at,
            "counts": self.counts(),
            "filteredByReason": dict(self.filtered_by_reason),
            "unit": (
                {"kind": self.unit_kind, "done": self.units_done, "total": self.units_total}
                if self.unit_kind else None
            ),
            "expectedTotal": self.expected_total,
            "throttledUntil": (
                self.throttled_until if self.throttled_until and self.throttled_until > now else None
            ),
            "approximate": self.approximate,
        }

    def run_entry(
        self,
        *,
        outcome: str,
        finished_at: int,
        error: dict[str, Any] | None = None,
        skip_code: str | None = None,
        started_by: str | None = None,
    ) -> dict[str, Any]:
        """The run's line in ``syncRuns``."""
        entry: dict[str, Any] = {
            "runId": self.run_id,
            "trigger": self.trigger.value,
            "full": self.full,
            "startedAt": self.started_at,
            "finishedAt": finished_at,
            "outcome": outcome,
            "counts": self.counts(),
            "approximate": self.approximate,
        }
        if self.unit_kind:
            entry["unit"] = {"kind": self.unit_kind, "done": self.units_done, "total": self.units_total}
        if self.filtered_by_reason:
            entry["filteredByReason"] = dict(self.filtered_by_reason)
        if error:
            entry["error"] = error
        if skip_code:
            entry["skipCode"] = skip_code
        if started_by:
            entry["startedBy"] = started_by
        return entry


def merged_run_history(stored: object, entry: dict[str, Any]) -> str:
    """``entry`` ahead of the stored ``syncRuns``, newest first, capped; as stored text."""
    runs = [entry, *(old for old in parse_run_history(stored) if old.get("runId") != entry.get("runId"))]
    return json.dumps(runs[:SYNC_RUNS_KEPT], default=str)


def parse_run_history(stored: object) -> list[dict[str, Any]]:
    """``syncRuns`` as a list, whatever shape a store handed back; [] for anything unreadable."""
    value = stored
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    return [run for run in value if isinstance(run, dict)]


def parse_json_field(stored: object) -> dict[str, Any] | None:
    """A JSON-object app field (``syncProgress``, ``lastSyncError``) as a dict, or None."""
    value = stored
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


# ---- the current run ------------------------------------------------------

_current: ContextVar[SyncProgress | None] = ContextVar("sync_progress", default=None)


def set_current_sync_progress(progress: SyncProgress) -> Token:
    return _current.set(progress)


def reset_current_sync_progress(token: Token) -> None:
    _current.reset(token)


def current_sync_progress(connector_id: str | None = None) -> SyncProgress | None:
    """The open run on this context, if any, and only if it is ``connector_id``'s.

    The match matters for the org-wide processor collections share: an upload
    handled while some connector syncs must not count into that sync.
    """
    progress = _current.get()
    if progress is None or progress.closed:
        return None
    if connector_id is not None and progress.connector_id != connector_id:
        return None
    return progress


class NoSyncProgress:
    """Stand-in a connector gets outside a run (a reindex, a webhook): every report is dropped."""

    def touch(self) -> None:
        return None

    def enter_phase(self, phase: SyncPhase) -> None:
        return None

    def note_filtered(self, reason: str, count: int = 1) -> None:
        return None

    def note_containers(self, count: int) -> None:
        return None

    def set_units(self, kind: str, done: int, total: int | None = None) -> None:
        return None

    def advance_units(self, kind: str, by: int = 1, total: int | None = None) -> None:
        return None

    def expect_total(self, total: int | None) -> None:
        return None

    def note_throttled(self, seconds: float) -> None:
        return None


NO_PROGRESS = NoSyncProgress()


def report_throttled(seconds: float) -> None:
    """For a connector's own backoff code: the source asked us to wait ``seconds``."""
    progress = current_sync_progress()
    if progress is not None:
        progress.note_throttled(seconds)


def report_filtered(reason: str, count: int = 1) -> None:
    """For shared filter helpers, which do not know their connector: count into the current run."""
    progress = current_sync_progress()
    if progress is not None:
        progress.note_filtered(reason, count)
