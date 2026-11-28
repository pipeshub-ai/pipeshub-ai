"""The state of a Zammad knowledge-base answer, by Zammad's own rule.

Ported from Zammad (tag 6.4.1, unchanged in 7.2):

* An answer's state is the first of archived, published and internal whose
  timestamp is set and already passed, else draft
  (``lib/can_be_published/state_machine.rb#calculated_state``). A transition keeps
  the earlier timestamps, so an archived answer still carries ``published_at``,
  and a timestamp in the future (a scheduled change) does not count yet.

Who may read an answer, by its category's role access, is read in
``app/ee/permission_sync/zammad.py``. This module reads nothing itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

ARCHIVED = "archived"
PUBLISHED = "published"
INTERNAL = "internal"
DRAFT = "draft"
# The order Zammad tests the states in.
_STATE_ORDER = (ARCHIVED, PUBLISHED, INTERNAL)


def _epoch_ms(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def answer_state(answer: Mapping[str, object], now_ms: int) -> str:
    """The answer's state at ``now_ms``: archived, published, internal or draft."""
    for state in _STATE_ORDER:
        at = _epoch_ms(answer.get(f"{state}_at"))
        if at is not None and at < now_ms:
            return state
    return DRAFT


def answer_changed_at(answer: Mapping[str, object], now_ms: int) -> int:
    """When the answer last changed as a reader sees it: its last edit, or a state change that has come due.

    A scheduled state change (a timestamp set ahead) takes effect without touching
    ``updated_at``, so a sync that compares ``updated_at`` alone would never see it.
    """
    times = [_epoch_ms(answer.get("updated_at")) or 0]
    for state in _STATE_ORDER:
        at = _epoch_ms(answer.get(f"{state}_at"))
        if at is not None and at < now_ms:
            times.append(at)
    return max(times)
