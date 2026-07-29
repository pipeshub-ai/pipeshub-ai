"""RecordLocation — hierarchy breadcrumb for retrieved records.

Provides a batched, cache-aware wrapper around
``IGraphDBProvider.get_record_locations`` so the retrieval hot path incurs
at most one extra round trip regardless of how many records were retrieved.

Usage in retrieval_service.py::
    # Gather alongside existing fetch_files / fetch_mails calls
    locations = await resolve_locations(
        record_ids,
        graph_provider=graph_provider,
        org_id=org_id,
        cache=per_request_cache,
    )
    # Stash onto each virtual_record entry
    for rid, loc in locations.items():
        if rid in virtual_to_record_map:
            virtual_to_record_map[rid]["location"] = loc.render()

Usage in Record.to_llm_context()::
    if self.location:
        parts.append(f"Location        : {self.location}")
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_MAX_DEPTH = 6
_MAX_RENDER_LEN = 80


@dataclass(frozen=True)
class RecordLocation:
    """Immutable hierarchy path for one record.

    Attributes:
        path: Ancestor names from root down to the immediate parent (names only).
        parent_id: The nearest ancestor's node id — pass to ``navigate()`` to
            browse siblings or children.
        truncated: True when the actual depth exceeded the query's max_depth cap.
    """

    path: tuple[str, ...]
    parent_id: str | None
    truncated: bool

    def render(self) -> str:
        """One-line breadcrumb for ``to_llm_context()``.

        Example: ``"Engineering KB > Runbooks (parent id: 8f2c…)"``
        Returns an empty string when there is no path information.
        """
        if not self.path and not self.parent_id:
            return ""
        crumb = " > ".join(self.path) if self.path else ""
        if self.truncated:
            crumb = "… > " + crumb if crumb else "…"
        parts = [crumb] if crumb else []
        if self.parent_id:
            short_id = self.parent_id[:8] if len(self.parent_id) > 8 else self.parent_id
            parts.append(f"(parent id: {short_id})")
        return " ".join(parts)


def _location_from_row(row: dict[str, Any]) -> RecordLocation:
    raw_path: list[str] = row.get("path") or []
    parent_id: str | None = row.get("parentId") or None
    truncated: bool = bool(row.get("truncated", False))
    path = tuple(str(n) for n in raw_path if n)
    return RecordLocation(path=path, parent_id=parent_id, truncated=truncated)


async def resolve_locations(
    record_ids: Sequence[str],
    *,
    graph_provider: "IGraphDBProvider",
    org_id: str,
    cache: "dict[str, RecordLocation]",
) -> "dict[str, RecordLocation]":
    """Cache-aware wrapper — returns locations for all requested ids.

    ``cache`` is a mutable dict (per-request scratch space) that lets callers
    avoid re-fetching locations for records that appear in multiple tool results
    within the same turn.

    Records already in ``cache`` are returned directly. Unknown ids are
    fetched in one batched provider call and inserted into ``cache``.
    """
    result: dict[str, RecordLocation] = {}
    missing: list[str] = []
    for rid in record_ids:
        if rid in cache:
            result[rid] = cache[rid]
        else:
            missing.append(rid)

    if not missing:
        return result

    try:
        raw = await graph_provider.get_record_locations(
            missing, org_id, max_depth=_MAX_DEPTH
        )
    except Exception:
        raw = {}

    for rid in missing:
        row = raw.get(rid)
        loc = _location_from_row(row) if row else RecordLocation(path=(), parent_id=None, truncated=False)
        cache[rid] = loc
        result[rid] = loc

    return result
