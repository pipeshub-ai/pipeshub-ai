"""Recent connector indexing counts, for callers that accept counts a little old.

Counting a connector's records walks every record it holds in the graph, which
takes seconds on a large connector, and an agent listing its sources asks for
every connector at once. A caller that only needs a picture of coverage can
pass ``maxAgeSeconds`` on ``GET /api/v1/stats`` and get counts up to that old.
Every response says when its counts were taken (``countedAt``) and how old
they are (``ageSeconds``). A caller that passes nothing gets fresh counts, as
before; those fresh counts are also kept here for the next caller who accepts
older ones.

Counts depend only on the org and the connector, not on who asks, so an entry
is keyed by both. The endpoint checks the caller's access before it reads
here, so a kept entry is never shown to someone who may not open the connector.

Entries live in this process only. Failures are never kept.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "MAX_AGE_SECONDS_LIMIT",
    "ConnectorStatsCache",
    "connector_stats_cache",
]

# The oldest counts a caller may ask for. Past this they stop describing a
# connector that is syncing.
MAX_AGE_SECONDS_LIMIT = 600

# One entry per connector; a few KB each.
_MAX_ENTRIES = 1024


class ConnectorStatsCache:
    """Last counts per (org, connector), with the time they were taken.

    ``clock`` (monotonic seconds) measures age; ``wall_clock`` (epoch
    seconds) stamps ``countedAt``. Both are injectable for tests.
    """

    def __init__(
        self,
        max_entries: int = _MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._wall_clock = wall_clock
        # key -> (monotonic time counted, epoch ms counted, stats data)
        self._entries: OrderedDict[tuple[str, str], tuple[float, int, dict[str, Any]]] = OrderedDict()

    def get(self, org_id: str, connector_id: str, max_age_seconds: float) -> dict[str, Any] | None:
        """Kept counts no older than ``max_age_seconds``, stamped; else None."""
        entry = self._entries.get((org_id, connector_id))
        if entry is None:
            return None
        counted, counted_at_ms, data = entry
        age = self._clock() - counted
        if age > max_age_seconds:
            return None
        return _stamped(data, counted_at_ms, age)

    def put(self, org_id: str, connector_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Keep counts just taken, and return them stamped as fresh."""
        key = (org_id, connector_id)
        counted_at_ms = int(self._wall_clock() * 1000)
        self._entries[key] = (self._clock(), counted_at_ms, data)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return _stamped(data, counted_at_ms, 0.0)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


def _stamped(data: dict[str, Any], counted_at_ms: int, age_seconds: float) -> dict[str, Any]:
    return {**data, "countedAt": counted_at_ms, "ageSeconds": int(age_seconds)}


connector_stats_cache = ConnectorStatsCache()
