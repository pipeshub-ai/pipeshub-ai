"""
Gmail `users.threads.list` search query (`q`) from RECEIVED_DATE sync filter.

Gmail returns a thread if any message in the thread matches the query
(see https://developers.google.com/workspace/gmail/api/guides/threads).
That aligns with indexing the full thread after `threads.get` without
per-message date skipping.

Assumes normalized datetime filters: operators `is_after`, `is_before`,
`is_between` with epoch millisecond bounds (see Filter model). Rolling
`last_*` operators are expanded before persistence; not handled here.

Gmail `after:` / `before:` with Unix seconds may not match `internalDate`
millisecond-for-millisecond; thread-level selection is the source of truth.
"""

from __future__ import annotations

from app.connectors.core.registry.filters import DatetimeOperator, Filter, FilterType


def _ms_to_after_seconds(start_ms: int) -> int:
    return start_ms // 1000


def _ms_to_before_seconds_exclusive(end_ms: int) -> int:
    """Upper bound in seconds for Gmail `before:` (exclusive semantics in practice)."""
    return (end_ms + 999) // 1000


def build_gmail_received_date_threads_query(received_filter: Filter | None) -> str | None:
    """
    Build Gmail search string for `users.threads.list(q=...)`.

    Returns None if the filter is missing, empty, wrong type, or uses an
    unsupported operator (caller omits `q` and lists all threads).
    """
    if received_filter is None:
        return None
    if received_filter.type != FilterType.DATETIME or received_filter.is_empty():
        return None

    op = received_filter.operator
    start_ms = received_filter.get_datetime_start()
    end_ms = received_filter.get_datetime_end()

    parts: list[str] = []

    if op == DatetimeOperator.IS_AFTER:
        if start_ms is None:
            return None
        parts.append(f"after:{_ms_to_after_seconds(start_ms)}")
    elif op == DatetimeOperator.IS_BEFORE:
        if end_ms is None:
            return None
        parts.append(f"before:{_ms_to_before_seconds_exclusive(end_ms)}")
    elif op == DatetimeOperator.IS_BETWEEN:
        if start_ms is not None:
            parts.append(f"after:{_ms_to_after_seconds(start_ms)}")
        if end_ms is not None:
            parts.append(f"before:{_ms_to_before_seconds_exclusive(end_ms)}")
        if not parts:
            return None
    else:
        return None

    return " ".join(parts) if parts else None
