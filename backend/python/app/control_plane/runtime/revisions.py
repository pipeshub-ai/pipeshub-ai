"""The revision rule every holder of control-plane state applies to a change event (02-lld §8.1).

An event at or below the held revision is a duplicate (delivery is at least once, so handlers are idempotent by
revision); the event right after it is the next one; anything else means events were missed, and the holder drops
what it has for the org instead of replaying. The Node twin is ``domain/revisions.ts`` ``changeStep``.
"""

from __future__ import annotations

from typing import Literal

ChangeStep = Literal["duplicate", "next", "gap"]


def change_step(as_of: int, *, revision: int, previous: int) -> ChangeStep:
    if revision <= as_of:
        return "duplicate"
    return "next" if previous == as_of else "gap"


__all__ = ["ChangeStep", "change_step"]
