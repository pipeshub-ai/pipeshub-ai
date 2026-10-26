"""Which lane a connector is given: the edition hook and its OSS default.

The lane map (``assignment.py``) records one lane per connector. *Which* lane
is a policy decision, so it is made here, by a pure function the edition can
replace through ``app.edition_services``:

- ``choose_connector_lane(request, snapshot)`` returns a lane number, or
  ``KEEP_CURRENT`` to leave a connector that already has a valid lane where it
  is. It sees the connector (id, org, class, type) and a snapshot of every
  lane's load, read from Redis in the same round trip that found the
  connector unassigned. The store commits the answer only if no other
  assignment changed the snapshot in the meantime, and otherwise asks again
  with a fresh one, so the hook never has to think about concurrency.
- ``admin_lane_move_allowed(org_id, connector_id)`` says whether an
  administrator may move a connector to another lane. Never, here: the open
  source edition moves a connector only during the one-time upgrade fix-up and
  when the lane count is lowered.

An edition can pin a connector to a lane of its own, weight lanes, or allow
admin-triggered moves by replacing these two functions; ``LaneSnapshot.fields``
carries the whole meta hash, so per-lane fields an edition keeps there reach
its rule without any change here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "BUSY_READING_MAX_AGE_MS",
    "KEEP_CURRENT",
    "ConnectorClass",
    "KeepCurrent",
    "LaneChoice",
    "LaneLoad",
    "LaneRequest",
    "LaneRequestReason",
    "LaneSnapshot",
    "admin_lane_move_allowed",
    "choose_connector_lane",
    "is_large_class",
]

# A busy reading older than this is ignored, so a stopped indexer does not
# freeze placement on whatever it last saw.
BUSY_READING_MAX_AGE_MS: Final = 5 * 60 * 1000


class ConnectorClass(StrEnum):
    """How much traffic a connector is expected to bring.

    ``team`` connectors sync whole workspaces and are where six-figure
    backlogs come from; the rest are small.
    """

    TEAM = "team"
    PERSONAL = "personal"
    KB = "kb"
    SYSTEM = "system"


def is_large_class(connector_class: str) -> bool:
    """Counted as a large occupant of its lane. Must agree with the Lua scripts."""
    return connector_class == ConnectorClass.TEAM.value


class LaneRequestReason(StrEnum):
    """Why a lane is being chosen."""

    # No entry yet: a new connector, or an existing one seen for the first time.
    FIRST_ASSIGNMENT = "first_assignment"
    # Its entry names a lane the current lane count no longer has.
    LANE_OUT_OF_RANGE = "lane_out_of_range"
    # The one-time upgrade fix-up separating connectors that share a hash lane.
    UPGRADE = "upgrade"
    # An administrator asked for it (only if admin_lane_move_allowed says so).
    ADMIN = "admin"


class KeepCurrent:
    """Leave the connector on the lane it already has."""

    _instance: KeepCurrent | None = None

    def __new__(cls) -> KeepCurrent:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "KEEP_CURRENT"


KEEP_CURRENT: Final = KeepCurrent()

LaneChoice = int | KeepCurrent


@dataclass(frozen=True)
class LaneRequest:
    """The connector a lane is being chosen for."""

    connector_id: str
    connector_class: str
    # Where hashing puts it: today's lane for a connector that predates the map.
    hash_lane: int
    reason: LaneRequestReason = LaneRequestReason.FIRST_ASSIGNMENT
    org_id: str | None = None
    # The connector type (e.g. "SLACK"), when the caller knows it.
    connector_type: str | None = None
    # Its lane now, when it has one inside the current lane count. The
    # snapshot's counts already leave the connector itself out.
    current_lane: int | None = None


@dataclass(frozen=True)
class LaneLoad:
    """What is on one lane, as last recorded."""

    lane: int
    large: int = 0
    small: int = 0
    # The indexing service last saw this lane's oldest unfinished event more
    # than five minutes old, and that reading is itself recent.
    busy: bool = False


@dataclass(frozen=True)
class LaneSnapshot:
    """Every lane's load, read atomically with the connector's own entry."""

    lane_count: int
    lanes: tuple[LaneLoad, ...]
    # Bumped by every write that can change a placement; the store's commit
    # is refused if it moved since this snapshot was read.
    version: int = 0
    # Redis's clock when the snapshot was read.
    now_ms: int = 0
    # The raw meta hash, for edition rules that keep their own fields in it.
    fields: Mapping[str, str] = field(default_factory=dict)


def _score(load: LaneLoad) -> tuple[int, int, int]:
    return (load.large, int(load.busy), load.small)


def choose_connector_lane(request: LaneRequest, snapshot: LaneSnapshot) -> LaneChoice:
    """The OSS rule: the least-loaded lane, keeping the hash lane when it ties.

    Lanes are compared on the number of large connectors already on them,
    then whether they are busy, then the number of small connectors, then
    the lane number so the answer is deterministic. A connector being moved
    keeps its current lane when that ties for best; otherwise, if its hash
    lane ties for best on the first three, it takes that: an install whose
    connectors never collided sees no lane change at all.
    """
    if not snapshot.lanes:
        return request.hash_lane
    best = min(snapshot.lanes, key=lambda load: (*_score(load), load.lane))
    by_lane = {load.lane: load for load in snapshot.lanes}
    # A connector being moved stays put unless some lane is strictly better:
    # a move splits its order for a while, and a tie gains nothing.
    current = by_lane.get(request.current_lane) if request.current_lane is not None else None
    if current is not None and _score(current) <= _score(best):
        return current.lane
    hashed = by_lane.get(request.hash_lane)
    if hashed is not None and _score(hashed) == _score(best):
        return hashed.lane
    return best.lane


def admin_lane_move_allowed(org_id: str | None, connector_id: str) -> bool:
    """Whether an administrator may move this connector. Never, in this edition."""
    return False
