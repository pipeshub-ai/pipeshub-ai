"""Shared utility helpers for the GitHub Teams connector."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterType,
    SyncFilterKey,
)
from app.utils.time_conversion import datetime_to_epoch_ms, get_epoch_timestamp_in_ms

# Operators the "updated since" filter offers. The connector UI turns the
# rolling ones into an absolute start when the filter is saved; a config written
# through the API can still carry one with no value, so they are resolved here
# too, against the time of the sync.
SYNC_WINDOW_OPERATORS: list[str] = [
    FilterOperator.LAST_7_DAYS,
    FilterOperator.LAST_14_DAYS,
    FilterOperator.LAST_30_DAYS,
    FilterOperator.LAST_90_DAYS,
    FilterOperator.LAST_180_DAYS,
    FilterOperator.LAST_365_DAYS,
    FilterOperator.IS_AFTER,
]

_RELATIVE_WINDOW_DAYS: dict[str, int] = {
    FilterOperator.LAST_7_DAYS: 7,
    FilterOperator.LAST_14_DAYS: 14,
    FilterOperator.LAST_30_DAYS: 30,
    FilterOperator.LAST_90_DAYS: 90,
    FilterOperator.LAST_180_DAYS: 180,
    FilterOperator.LAST_365_DAYS: 365,
}


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


def updated_since_filter_field() -> FilterField:
    """The "updated since" sync filter, shared by the team and personal connectors.

    Unset means the full history, as before the filter existed. Code files are
    not dated this way and are not affected.
    """
    return FilterField(
        name=SyncFilterKey.MODIFIED.value,
        display_name="Sync issues and pull requests updated since",
        description=(
            "Only issues and pull requests updated on or after this date are synced. "
            "Leave it empty to sync the full history. A changed date takes full "
            "effect on the next full sync."
        ),
        filter_type=FilterType.DATETIME,
        category=FilterCategory.SYNC,
        allowed_operators=SYNC_WINDOW_OPERATORS,
        no_implicit_operator_default=True,
    )


def sync_window_start(sync_filters: FilterCollection | None) -> datetime | None:
    """Start of the "updated since" window, or ``None`` for the full history.

    Read from the ``modified`` sync filter, the key the GitLab connector uses
    for the same setting. Only the start is honoured: an end date would stop
    an incremental sync from ever seeing new activity.
    """
    window = sync_filters.get(SyncFilterKey.MODIFIED) if sync_filters else None
    if window is None:
        return None
    start_ms = window.get_datetime_start()
    if start_ms is not None:
        return datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    days = _RELATIVE_WINDOW_DAYS.get(window.operator_value)
    if days:
        return datetime.now(timezone.utc) - timedelta(days=days)
    return None


def listing_since(checkpoint: datetime | None, window: datetime | None) -> datetime | None:
    """Where a listing starts: the later of the checkpoint and the window start.

    A checkpoint inside the window wins, so an incremental sync still reads
    only what changed. A window start later than the checkpoint wins, so a
    narrowed window stops reading older items (it does not remove any).
    """
    if checkpoint is None:
        return window
    if window is None:
        return checkpoint
    return max(checkpoint, window)
