"""A classic chat turn a schedule fired (ADR-SCH-02 D3, D4), as Node's classic runner marks it.

The marks are never authority: the turn is authenticated by the actor credential Node minted for the firing, and
all they change is that a call needing approval waits for the person (who will read the conversation later) instead
of being refused, and that the turn is not offered ``schedule_create`` (a schedule never makes schedules).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from app.schedules.arguments import parse_instant

if TYPE_CHECKING:
    from collections.abc import Mapping

SCHEDULE_FIRING_HEADER: Final = "x-pipeshub-schedule-firing"
ASK_DEADLINE_HEADER: Final = "x-pipeshub-ask-deadline"
_FIRING_ID: Final = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def scheduled_turn(headers: Mapping[str, str]) -> dict[str, object] | None:
    """``{"firingId", "askDeadline"}`` (the deadline in epoch seconds) when the request carries both marks and both
    read; ``None`` otherwise, which is an ordinary chat turn."""
    firing = headers.get(SCHEDULE_FIRING_HEADER)
    deadline = parse_instant(headers.get(ASK_DEADLINE_HEADER))
    if not isinstance(firing, str) or not _FIRING_ID.match(firing) or deadline is None:
        return None
    return {"firingId": firing, "askDeadline": deadline.timestamp()}


__all__ = ["ASK_DEADLINE_HEADER", "SCHEDULE_FIRING_HEADER", "scheduled_turn"]
