"""One permission walk for every dated search of a timeline.

A search with date bounds cannot use the cached map of what a user may reach
(the cache holds unbounded maps), so the graph walks the user's permissions
for it live, and applies the bounds only at the end of the walk. A timeline
runs a search over its whole span and one per period, all with dates, so it
walked the same permissions once per search: seven times for six periods.

Inside ``shared_dated_scope(span)`` a provider walks once, for the span,
keeping each reachable record's dates, and every dated read whose bounds lie
within the span takes its records from that walk, filtered here by the same
rules the providers' queries apply (``_build_time_range_conditions`` in the
Neo4j provider, ``_append_source_created_time_filters`` in the ArangoDB one).
A read the span does not cover goes to the graph as before. Only records the
span's walk admitted are ever returned, so sharing can leave a record out if
the rules here drift from the queries', but never add one.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.models.source_dates import DATED_ONLY_TIME_RANGE_KEY, upload_times_undated

if TYPE_CHECKING:
    import logging
    from collections.abc import Awaitable, Callable, Hashable, Iterator, Mapping

# The time-range keys the providers read, by the record field they bound.
_AFTER_KEYS = {"source_created_after_ms": "created", "source_updated_after_ms": "updated"}
_BEFORE_KEYS = {"source_created_before_ms": "created", "source_updated_before_ms": "updated"}


@dataclass(frozen=True)
class DatedRow:
    """One record a permission walk reached, with what its date bounds read."""

    virtual_id: str
    record_id: str
    created: Any = None
    updated: Any = None
    origin: Any = None


def _number(value: Any) -> float | None:  # noqa: ANN401
    # The graph compares numbers; a stored timestamp is always one, and a
    # missing one compares as no match on either side of a bound.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def row_in_time_range(row: DatedRow, time_range: Mapping[str, Any]) -> bool:
    """Whether the providers' time-range conditions admit ``row``.

    After-bounds need a timestamp at or after the bound. Before-bounds need
    one at or before it, or, while uploads are undated and the range is not
    dated-only, an upload's own timestamp (written at or before the upload).
    A dated-only range leaves uploads out.
    """
    dated_only = bool(time_range.get(DATED_ONLY_TIME_RANGE_KEY)) and upload_times_undated()
    is_upload = row.origin == "upload"
    undated_passes_before = upload_times_undated() and not dated_only and is_upload
    values = {"created": _number(row.created), "updated": _number(row.updated)}
    for key, field_name in _AFTER_KEYS.items():
        if key in time_range:
            value = values[field_name]
            if value is None or value < time_range[key]:
                return False
    for key, field_name in _BEFORE_KEYS.items():
        if key in time_range:
            value = values[field_name]
            if (value is None or value > time_range[key]) and not undated_passes_before:
                return False
    return not (dated_only and is_upload)


def virtual_ids_in_range(rows: list[DatedRow], time_range: Mapping[str, Any]) -> dict[str, str]:
    """``virtualRecordId -> recordId`` for the rows in ``time_range``, the first
    admitted row winning for a virtual id, as each provider's live read does."""
    mapping: dict[str, str] = {}
    for row in rows:
        if row.virtual_id and row.record_id and row.virtual_id not in mapping and row_in_time_range(row, time_range):
            mapping[row.virtual_id] = row.record_id
    return mapping


@dataclass
class DatedScope:
    """The span the shared walks are read for, and the walks read so far."""

    span: dict[str, Any]
    _reads: dict[Hashable, asyncio.Task] = field(default_factory=dict)

    def covers(self, time_range: Mapping[str, Any] | None) -> bool:
        """Whether every record ``time_range`` admits is one the span admits:
        each of the span's bounds is in ``time_range``, as tight or tighter,
        and a dated-only span is read only for dated-only ranges."""
        if not time_range:
            return False
        if self.span.get(DATED_ONLY_TIME_RANGE_KEY) and not time_range.get(DATED_ONLY_TIME_RANGE_KEY):
            return False
        for key in _AFTER_KEYS:
            if key in self.span and (key not in time_range or time_range[key] < self.span[key]):
                return False
        for key in _BEFORE_KEYS:
            if key in self.span and (key not in time_range or time_range[key] > self.span[key]):
                return False
        return True

    async def rows(self, key: Hashable, load: Callable[[], Awaitable[list[DatedRow]]]) -> list[DatedRow]:
        """The span's walk for ``key``, read once however many searches ask.

        A failed walk is not kept, so a later search asks the graph again.
        """
        task = self._reads.get(key)
        if task is None:
            task = asyncio.ensure_future(load())
            self._reads[key] = task
        try:
            # Shielded: one search giving up must not cancel the walk the
            # others are waiting on.
            return await asyncio.shield(task)
        except Exception:
            if self._reads.get(key) is task:
                del self._reads[key]
            raise

    def close(self) -> None:
        """Cancel the walks still running once no search can ask for them.

        A walk outlives a search that gave up on it (it is shielded), so one
        left running when the scope closes would keep querying the graph for
        nobody. A finished walk's error is read here, so asyncio doesn't report
        it as never retrieved.
        """
        for task in self._reads.values():
            if not task.done():
                task.cancel()
            elif not task.cancelled():
                task.exception()
        self._reads.clear()


_scope: ContextVar[DatedScope | None] = ContextVar("dated_permission_scope", default=None)


@contextmanager
def shared_dated_scope(span: Mapping[str, Any]) -> Iterator[DatedScope]:
    """Share one permission walk among the dated searches started inside.

    ``span`` is a time range (the providers' keys) that holds every search
    the block runs; an empty span holds any dated range. Tasks started inside
    the block, as ``asyncio.gather`` starts them, share the scope.
    """
    scope = DatedScope(dict(span))
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)
        scope.close()


def scope_for(time_range: Mapping[str, Any] | None) -> DatedScope | None:
    """The shared scope ``time_range`` can be read from, if one is open."""
    scope = _scope.get()
    return scope if scope is not None and scope.covers(time_range) else None


def frozen_filters(filters: Mapping[str, list[str]] | None) -> tuple:
    """A hashable form of a provider's metadata filters, for a read's key."""
    return tuple(sorted((k, tuple(v)) for k, v in (filters or {}).items() if v))


async def read_within(
    scope: DatedScope,
    key: Hashable,
    load: Callable[[], Awaitable[list[DatedRow]]],
    time_range: Mapping[str, Any],
    *,
    raise_on_error: bool,
    logger: logging.Logger,
) -> dict[str, str]:
    """``time_range``'s records from the span's shared walk for ``key``.

    On a failed walk this does what the live read does: raise when asked to,
    and otherwise log it and return no records.
    """
    try:
        rows = await scope.rows(key, load)
    except Exception as exc:
        if raise_on_error:
            raise
        logger.error("Shared dated permission read %s failed: %s", key, exc)
        return {}
    return virtual_ids_in_range(rows, time_range)
