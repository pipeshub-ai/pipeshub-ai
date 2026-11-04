"""How long a Drive feed may stay on its checkpoint because some of its items could not be stored.

One job: count, in the feed's own sync point, the runs in a row its checkpoint was held for failed items, and
say when to stop holding. Holding re-reads the failed changes next run; holding for ever would stop every later
change of that feed, so after MAX_HELD_RUNS the checkpoint moves on and the failed items wait for their next
change or a full sync.
"""

from __future__ import annotations

from typing import Any

HELD_RUNS_FIELD = "heldFailedItemRuns"
MAX_HELD_RUNS = 5


def held_runs(sync_point: dict[str, Any] | None) -> int:
    value = (sync_point or {}).get(HELD_RUNS_FIELD)
    return int(value) if isinstance(value, int) and value > 0 else 0


def hold_again(sync_point: dict[str, Any] | None) -> dict[str, Any] | None:
    """The fields to write to keep holding, or None when the feed has held long enough and must move on."""
    runs = held_runs(sync_point) + 1
    return {HELD_RUNS_FIELD: runs} if runs < MAX_HELD_RUNS else None


def released(sync_point: dict[str, Any] | None) -> dict[str, Any]:
    """Fields to write with a checkpoint that moves on: the count starts over."""
    return {HELD_RUNS_FIELD: 0} if held_runs(sync_point) else {}
