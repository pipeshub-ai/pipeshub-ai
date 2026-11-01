"""The structured fields a demo record's source system would hold.

The fixture writes them on the record's header line, as each system shows them
(``**Status:** Solved · **Resolved:** 2026-02-13 · **Priority:** Normal``); a
real connector reads the same values from its API. Builders set them on the
record through the real connectors' value mapper, so a status or priority is
stored exactly as a production sync would store it.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from app.connectors.utils.value_mapper import map_priority, map_status

# A value ends at the next field, a bracket, a sentence or a dash: "Closed —
# not needed" is Closed, "In progress. Renewal due 1 June" In progress.
_HEADER_FIELD = re.compile(
    r"\*\*(Status|Priority|Product|Stage|Resolved|Close date):\*\*\s*([^·\n(.—]+)",
)
# Deals write "**Amount (ARR):** $54,000"; the hand-written E0 sample writes it unbolded.
_AMOUNT = re.compile(r"Amount(?: \(ARR\))?:(?:\*\*)?\s*\$([\d,]+)")


def header_fields(body: str) -> dict[str, str]:
    """``{"status": "Solved", ...}`` from a body's header line, as written; first value wins."""
    found: dict[str, str] = {}
    for key, value in _HEADER_FIELD.findall(body):
        found.setdefault(key.lower(), " ".join(value.split()))
    if amount := _AMOUNT.search(body):
        found.setdefault("amount", amount.group(1).replace(",", ""))
    return found


def _epoch_ms(day: str) -> int:
    return int(dt.datetime.combine(dt.date.fromisoformat(day), dt.time(), tzinfo=dt.UTC).timestamp() * 1000)


def ticket_fields(rec: dict[str, Any]) -> dict[str, Any]:
    """Status (mapped and as shown), priority and resolved date for a ticket or case."""
    header = header_fields(rec.get("body") or "")
    fields: dict[str, Any] = {}
    if status := header.get("status"):
        fields["status"] = map_status(status)
        fields["source_status"] = status
    if priority := header.get("priority"):
        fields["priority"] = map_priority(priority)
    if resolved := header.get("resolved"):
        fields["resolved_at"] = _epoch_ms(resolved)
    return fields


def deal_fields(rec: dict[str, Any]) -> dict[str, Any]:
    """Amount, close date and outcome for a deal; "Closed Won" and "Closed Lost" close it."""
    header = header_fields(rec.get("body") or "")
    fields: dict[str, Any] = {}
    if amount := header.get("amount"):
        fields["amount"] = float(amount)
    if close := header.get("close date"):
        fields["close_date"] = close
    if stage := header.get("stage"):
        closed = stage.lower().startswith("closed")
        fields["is_closed"] = closed
        fields["is_won"] = closed and "won" in stage.lower()
    return fields
