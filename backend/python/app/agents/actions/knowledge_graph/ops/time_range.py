"""ISO 8601 date parsing for time-aware retrieval search.

``parse_time_range`` is the single entry point knowledgegraph__search uses to
turn its four optional LLM-facing date parameters (``created_after`` /
``created_before`` / ``modified_after`` / ``modified_before``) into the
epoch-ms ``time_range`` dict the graph provider layer already understands
(see ``IGraphDBProvider.get_accessible_virtual_record_ids`` and
``ArangoHTTPProvider._append_source_created_time_filters``).

Kept deliberately separate from ``app.utils.time_conversion``: those helpers
treat a timezone-naive ISO string as local/UTC silently, which is exactly
the ambiguity we want to reject here — a naive value from an LLM is a
strong signal it guessed a timezone rather than took one from the user.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

__all__ = ["parse_time_range", "time_range_to_kh_filters"]

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 5-minute grace window for clock skew between the caller and this server
# when rejecting a `created_after` that appears to be in the future.
_FUTURE_TIMESTAMP_GRACE_MS = 5 * 60 * 1000

_FIELD_LABELS = {
    "created_after": "created_after",
    "created_before": "created_before",
    "modified_after": "modified_after",
    "modified_before": "modified_before",
}

# Maps each LLM-facing parameter to its key in the epoch-ms dict the graph
# provider layer expects.
_TIME_RANGE_KEYS = {
    "created_after": "source_created_after_ms",
    "created_before": "source_created_before_ms",
    "modified_after": "source_updated_after_ms",
    "modified_before": "source_updated_before_ms",
}


def _error(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


def _parse_iso_to_epoch_ms(
    value: str, *, is_upper_bound: bool, field_label: str
) -> tuple[int | None, str | None]:
    """Parse a single ISO 8601 date/datetime string to epoch milliseconds.

    ``YYYY-MM-DD`` resolves to start-of-day UTC for a lower bound and
    end-of-day UTC (23:59:59.999) for an upper bound, matching how a human
    reads "created before 2026-03-31" (inclusive of the whole day). A full
    ISO 8601 datetime is used exactly as given, but MUST carry a timezone
    offset — a naive datetime is rejected rather than silently assumed to
    be UTC, since that assumption is a common source of off-by-hours bugs
    when the caller actually meant the user's local timezone.

    Returns (epoch_ms, None) on success, (None, error_json) on failure.
    """
    if _DATE_ONLY_RE.match(value):
        try:
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None, _error(f"{field_label}: invalid calendar date {value!r}.")
        if is_upper_bound:
            dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
        return int(dt.timestamp() * 1000), None

    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None, _error(
            f"{field_label}: {value!r} is not a valid ISO 8601 date. "
            "Use 'YYYY-MM-DD' or a full datetime with a timezone offset "
            "(e.g. '2026-01-15T00:00:00Z')."
        )

    if dt.tzinfo is None:
        return None, _error(
            f"{field_label}: {value!r} has no timezone. Use 'YYYY-MM-DD' or "
            "include a timezone offset (e.g. '2026-01-15T08:00:00-07:00' or "
            "'2026-01-15T00:00:00Z')."
        )

    return int(dt.timestamp() * 1000), None


def _validate_range(
    after_ms: int | None, before_ms: int | None, *, after_label: str, before_label: str
) -> str | None:
    if after_ms is not None and before_ms is not None and after_ms > before_ms:
        return _error(
            f"{after_label} must be on or before {before_label} — got an inverted range."
        )
    return None


def _validate_not_future(epoch_ms: int, *, field_label: str) -> str | None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if epoch_ms > now_ms + _FUTURE_TIMESTAMP_GRACE_MS:
        return _error(f"{field_label} cannot be in the future.")
    return None


def parse_time_range(
    *,
    created_after: str | None = None,
    created_before: str | None = None,
    modified_after: str | None = None,
    modified_before: str | None = None,
) -> tuple[dict[str, int] | None, str | None]:
    """Parse the four LLM-facing date parameters into a graph-provider time_range dict.

    Returns:
        (time_range_dict, None) — one or more of the four parameters was
            provided and parsed successfully. Dict keys present only for
            the bounds that were supplied: ``source_created_after_ms``,
            ``source_created_before_ms``, ``source_updated_after_ms``,
            ``source_updated_before_ms`` (all epoch milliseconds).
        (None, None) — every parameter was omitted, blank, or whitespace —
            caller should search without any time filter.
        (None, error_json) — a parameter failed validation; ``error_json``
            is a ready-to-return JSON string describing the problem.
    """
    raw = {
        "created_after": created_after,
        "created_before": created_before,
        "modified_after": modified_after,
        "modified_before": modified_before,
    }
    cleaned = {k: (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
    cleaned = {k: (v or None) for k, v in cleaned.items()}

    if not any(cleaned.values()):
        return None, None

    epoch_values: dict[str, int] = {}
    for field, value in cleaned.items():
        if value is None:
            continue
        is_upper_bound = field.endswith("_before")
        epoch_ms, error = _parse_iso_to_epoch_ms(
            value, is_upper_bound=is_upper_bound, field_label=_FIELD_LABELS[field]
        )
        if error is not None:
            return None, error
        epoch_values[field] = epoch_ms

    range_error = _validate_range(
        epoch_values.get("created_after"),
        epoch_values.get("created_before"),
        after_label="created_after",
        before_label="created_before",
    )
    if range_error is not None:
        return None, range_error

    range_error = _validate_range(
        epoch_values.get("modified_after"),
        epoch_values.get("modified_before"),
        after_label="modified_after",
        before_label="modified_before",
    )
    if range_error is not None:
        return None, range_error

    for after_field in ("created_after", "modified_after"):
        if after_field in epoch_values:
            future_error = _validate_not_future(
                epoch_values[after_field], field_label=after_field
            )
            if future_error is not None:
                return None, future_error

    time_range = {
        _TIME_RANGE_KEYS[field]: epoch_ms for field, epoch_ms in epoch_values.items()
    }
    return time_range, None


def time_range_to_kh_filters(
    time_range: dict[str, int] | None,
) -> tuple[dict[str, int | None] | None, dict[str, int | None] | None]:
    """Bridge `parse_time_range()`'s epoch-ms dict (keyed by
    source_created_after_ms/source_created_before_ms/source_updated_after_ms/
    source_updated_before_ms — the shape knowledgegraph__search's retrieval
    path expects) to the `{"gte": ..., "lte": ...}` shape
    `KnowledgeHubService.get_nodes()` expects for its `created_at`/
    `updated_at` params. Reusing the same parser as search() (rather than a
    second, separate ISO-parsing implementation) means navigate() gets the
    exact same validation: rejecting timezone-naive datetimes, checking
    after <= before, and rejecting future created_after dates.
    """
    if not time_range:
        return None, None
    created_at: dict[str, int | None] | None = None
    if "source_created_after_ms" in time_range or "source_created_before_ms" in time_range:
        created_at = {
            "gte": time_range.get("source_created_after_ms"),
            "lte": time_range.get("source_created_before_ms"),
        }
    updated_at: dict[str, int | None] | None = None
    if "source_updated_after_ms" in time_range or "source_updated_before_ms" in time_range:
        updated_at = {
            "gte": time_range.get("source_updated_after_ms"),
            "lte": time_range.get("source_updated_before_ms"),
        }
    return created_at, updated_at
