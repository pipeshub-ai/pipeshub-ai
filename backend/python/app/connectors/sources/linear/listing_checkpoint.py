"""Where an incremental listing stands.

A listing is "everything changed since the last completed run". The checkpoint moves only
when a run has read its whole list, and then to the moment that run *started*, so a
change made while the list was being read is picked up by the next run. An interrupted
run leaves its cursor behind and the next run continues from it instead of starting over.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.connectors.core.base.sync_point.sync_point import SyncPoint

# Linear stamps updatedAt when a change commits; a listing that started a moment earlier
# can miss it. Each run re-reads this much before the previous run's start.
OVERLAP_MS = 60_000


@dataclass
class ListingRun:
    since: int | None  # read what changed after this instant; None reads everything
    started_at: int
    cursor: str | None = None
    resumed: bool = False

    @property
    def is_full(self) -> bool:
        return self.since is None


class ListingCheckpoint:
    """Stored as flat values (the graph stores keep sync points as plain properties):
    ``last_sync_time``; ``resume_cursor``, ``resume_since`` and ``resume_started_at`` of
    an unfinished run; ``held_runs``. Zero and the empty string mean "not set"."""

    def __init__(self, sync_point: SyncPoint, key: str, now: Callable[[], int]) -> None:
        self._sync_point = sync_point
        self._key = key
        self._now = now

    async def _read(self) -> dict[str, Any]:
        return await self._sync_point.read_sync_point(self._key) or {}

    async def _write(self, data: dict[str, Any], **changes: int | str) -> None:
        merged = {
            "last_sync_time": data.get("last_sync_time") or 0,
            "resume_cursor": data.get("resume_cursor") or "",
            "resume_since": data.get("resume_since") or 0,
            "resume_started_at": data.get("resume_started_at") or 0,
            "held_runs": data.get("held_runs") or 0,
        }
        merged.update(changes)
        await self._sync_point.update_sync_point(self._key, merged)

    async def last_completed(self) -> int | None:
        return (await self._read()).get("last_sync_time") or None

    async def begin(self) -> ListingRun:
        data = await self._read()
        if data.get("resume_cursor") and data.get("resume_started_at"):
            return ListingRun(data.get("resume_since") or None, data["resume_started_at"],
                              data["resume_cursor"], resumed=True)
        last = data.get("last_sync_time")
        since = max(1, last - OVERLAP_MS) if last else None
        return ListingRun(since, self._now())

    async def advance(self, run: ListingRun, cursor: str | None) -> None:
        """Remember how far an unfinished run has got."""
        if not cursor:
            return
        run.cursor = cursor
        await self._write(await self._read(), resume_cursor=cursor, resume_since=run.since or 0,
                          resume_started_at=run.started_at)

    async def abandon_cursor(self, run: ListingRun) -> None:
        """The saved cursor is no longer accepted: start this run's list from the top."""
        run.cursor, run.resumed = None, False
        await self._write(await self._read(), resume_cursor="", resume_since=0, resume_started_at=0)

    async def hold(self) -> int:
        """Keep the checkpoint where it is for another run. Returns how many runs in a
        row have now been held."""
        data = await self._read()
        held = int(data.get("held_runs") or 0) + 1
        await self._write(data, resume_cursor="", resume_since=0, resume_started_at=0, held_runs=held)
        return held

    async def finish(self, run: ListingRun) -> None:
        await self._write({}, last_sync_time=run.started_at)

    async def reset(self) -> None:
        await self._write({})
