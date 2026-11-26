"""What the loop is told of the trigger that started a scheduled run (TR-03).

An engine finds the same context at ``in/trigger.json``; the loop has no ``in/``, so it is told in words on its
first turn. The context holds ids and times only (Node never puts outsider-written content in it), so quoting it
in the goal adds nothing an outsider wrote.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

_KINDS = {"cron": "schedule", "manual": "Run now", "webhook": "webhook", "connector": "connector event"}


def trigger_note(context: Mapping[str, JsonValue] | None) -> str:
    """One paragraph naming the trigger, its slot and its details; empty for a run no schedule fired."""
    if not context:
        return ""
    kind = str(context.get("kind") or "")
    slot = str(context.get("slotAt") or "")
    details = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (
        f"This run was started by its schedule's {_KINDS.get(kind, kind)} trigger for the slot {slot}. "
        f"Trigger details (ids and times only; read any record it names with your tools): {details}"
    )


__all__ = ["trigger_note"]
