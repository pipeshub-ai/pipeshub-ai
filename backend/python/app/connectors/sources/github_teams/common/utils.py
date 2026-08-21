"""Shared utility helpers for the GitHub Teams connector."""

from __future__ import annotations

from typing import Any

from app.utils.time_conversion import datetime_to_epoch_ms, get_epoch_timestamp_in_ms


def epoch_ms_or_now(dt: Any) -> int:
    """Epoch-ms for ``dt``, falling back to now when it is missing or unparseable.

    Record timestamps are non-nullable, so an absent ``updated_at`` has to
    resolve to something; ``datetime_to_epoch_ms`` alone returns ``None``.
    """
    return datetime_to_epoch_ms(dt) or get_epoch_timestamp_in_ms()


def listing_payload(obj: Any) -> dict[str, Any]:
    """The raw REST JSON behind a ``GhObject`` — for fields not worth
    modelling as attributes (e.g. ``type``, ``parent_issue_url``).

    Fields absent from a listing payload stay absent; callers that need the
    complete payload (the reindex path) fetch the object explicitly and pass
    that in, where this returns the full dict just the same.
    """
    return getattr(obj, "_rawData", None) or {}
