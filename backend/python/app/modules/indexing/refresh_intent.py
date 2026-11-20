"""The refresh intent: a Force reindex's request, kept on the record until a refresh run has happened.

A user's Force reindex re-parses and re-indexes: every stored paid result is bypassed and paid for again. Its flags
(``forceReindex``, ``refreshPaidOutputs``) travel on the event only, so an event rebuilt from the record (the stale
recovery pass, the stranded-record republisher, a redelivery after a lost hand-back) used to drop them. The intent
is the same request on the record: ``refreshIntentId`` names it, ``refreshIntentAtMs`` says when it was made.

* It is written when the reindex is requested (the walk's own status write), and by the consumer for an event that
  carries no id (an older producer, an internal sender).
* Every full record event for a record that holds a live intent runs as a refresh under that id, whoever built it.
* A run clears only the id it saw when it started, so a request made while it ran survives and is served next.
* It lives ``INTENT_LIFE_MS`` (the paid-result refresh marker's life), counted from the request or from the hand-over.

``REFRESH_INTENT_WRITE`` (default off) gates every write of the two fields, so a rolling deploy never meets a record
schema that does not know them. Reads, clears and the twin filters are always on.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from collections.abc import Mapping

ID_FIELD = "refreshIntentId"
AT_FIELD = "refreshIntentAtMs"
#: The id of the newest request whose refresh ran on the record, written with the clear of the request it served.
DONE_ID_FIELD = "refreshDoneId"
#: Set on an event that was built from, or upgraded by, a stored intent (not the user's own message). Such an event
#: whose id is no longer the stored one was satisfied meanwhile and runs plain.
FROM_INTENT_KEY = "refreshFromIntent"
#: The payload key that names the refresh: the walk stamps it, the republishers copy it from the record.
PAYLOAD_ID_KEY = "refreshIntentId"

#: The paid-result refresh marker's life (``paid_cache.locks.DELETED_TTL_MS``, pinned equal by a test): inside it a
#: re-sent refresh finds its own marker and reuses what its first attempt stored.
INTENT_LIFE_MS = 24 * 3600 * 1000

#: What a sweep writes on a row whose intent expired before it was served.
REFRESH_EXPIRED_REASON = (
    "A Force reindex was requested for this record but could not be started within 24 hours. Request it again."
)


REFRESH_INTENT_EVENTS = METRICS_BACKEND.counter(
    "pipeshub_refresh_intent_total",
    "Force reindex requests kept on a record, by what happened to them",
    ["event"],
)


def write_enabled() -> bool:
    """``REFRESH_INTENT_WRITE``: whether this process writes the two fields (off by default; see the module doc)."""
    return os.getenv("REFRESH_INTENT_WRITE", "false").strip().lower() in {"1", "true", "yes", "on"}


def stored_intent(record: Mapping[str, Any] | None) -> tuple[str, int | None] | None:
    """``(id, at_ms)`` of the intent on a stored record, or None. A record from before the fields has none."""
    if not record:
        return None
    intent_id = record.get(ID_FIELD)
    if not isinstance(intent_id, str) or not intent_id:
        return None
    at = record.get(AT_FIELD)
    return intent_id, int(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None


def live_intent(record: Mapping[str, Any] | None, now_ms: int) -> str | None:
    """The id of the record's intent while it is younger than its life; None for none, or an expired one.

    An intent with no time (a hand-written row) is treated as expired: nothing could ever bound its life.
    """
    held = stored_intent(record)
    if held is None or held[1] is None:
        return None
    # A time ahead of this process's clock stays live: the request was stamped by another host (the walk's), and the sweep's wait
    # already holds the send until the time has passed. Only a request older than its life is expired.
    return held[0] if now_ms - held[1] < INTENT_LIFE_MS else None


def waiting_intent(record: Mapping[str, Any] | None, now_ms: int, run_started_ms: int) -> str | None:
    """The request a run that has just ended should hand over: a live one, or one made while the run was in progress even if
    the run outlasted its life (a record held by a run does not age out under it)."""
    live = live_intent(record, now_ms)
    if live is not None:
        return live
    held = stored_intent(record)
    return held[0] if held is not None and held[1] is not None and held[1] >= run_started_ms else None


def served(record: Mapping[str, Any] | None) -> bool:
    """Whether the request the record holds has already been served: the refresh that ran last on it was of this very request.
    Identity, not time: two requests stamped by different hosts can be ordered wrongly by their clocks."""
    held = stored_intent(record)
    return held is not None and (record or {}).get(DONE_ID_FIELD) == held[0]


def expired_intent(record: Mapping[str, Any] | None, now_ms: int) -> bool:
    held = stored_intent(record)
    return held is not None and live_intent(record, now_ms) is None


def intent_flags(intent_id: str, *, from_intent: bool) -> dict[str, Any]:
    """The payload keys that make an event a Force-reindex refresh under ``intent_id``."""
    flags: dict[str, Any] = {"forceReindex": True, "refreshPaidOutputs": True, PAYLOAD_ID_KEY: intent_id}
    if from_intent:
        flags[FROM_INTENT_KEY] = True
    return flags


def with_intent(payload: dict[str, Any], record: Mapping[str, Any], now_ms: int) -> dict[str, Any]:
    """``payload`` of an event rebuilt from ``record``, made a refresh when the record holds a live intent."""
    intent_id = live_intent(record, now_ms)
    if intent_id is not None:
        payload.update(intent_flags(intent_id, from_intent=True))
    return payload


def intent_fields(intent_id: str, at_ms: int) -> dict[str, Any]:
    """The fields every intent write sets. ``republishCount`` is reset in the same write so the intent sweep schedules
    from the intent's own age and not from the counter of an earlier wait for the same record."""
    return {ID_FIELD: intent_id, AT_FIELD: at_ms, "republishCount": None}


#: The two fields a clear removes. Removed, never left null (ArangoDB would keep the nulls): a cleared record carries neither.
CLEARED_FIELDS: tuple[str, str] = (ID_FIELD, AT_FIELD)


async def clear_intent(
    graph_provider: Any,  # noqa: ANN401 - an IGraphDBProvider
    record_id: str,
    intent_id: str | None,
    extra: Mapping[str, Any] | None = None,
    *,
    expected_at: int | float | None = None,
) -> bool:
    """Remove the request ``intent_id`` from the record, and only that one (a compare-and-set on its id); ``extra`` fields
    are written in the same statement. ``expected_at`` also requires the request's time to be what the caller read, so a
    hand-over (which renews the time) between the read and the clear is not undone. True when it was cleared."""
    if not intent_id:
        return False
    return bool(await graph_provider.update_node_fields_if_match(
        record_id, "records", dict(extra or {}),
        {ID_FIELD: intent_id, **({AT_FIELD: expected_at} if expected_at is not None else {})}, unset=CLEARED_FIELDS,
    ))
