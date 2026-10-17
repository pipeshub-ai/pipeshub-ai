"""Which retrieval layer found each record, for the opt-in ``retrieval_context`` trace.

Tools note records here as they surface them; the retrieval ledger reports
each (record, layer) pair once. Nothing reaches the model from this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, MutableMapping

RetrievalLayer = Literal["vector", "pattern_match", "entity", "fetch", "prefetch"]

RETRIEVED_BY_KEY = "retrieved_by"


@dataclass
class RecordProvenance:
    layers: list[RetrievalLayer] = field(default_factory=list)
    record_id: str | None = None
    record_name: str | None = None


def note_retrieved_by(
    state: MutableMapping[str, Any] | None,
    layer: RetrievalLayer,
    records: Iterable[tuple[str | None, str | None, str | None]],
) -> None:
    """Record that ``layer`` surfaced each ``(virtual_record_id, record_id, record_name)``.

    Kept only when the request opted into ``retrieval_context``, the one reader.
    """
    if not state or not state.get("include_retrieval_context"):
        return
    store = state.setdefault(RETRIEVED_BY_KEY, {})
    for virtual_record_id, record_id, record_name in records:
        if not virtual_record_id:
            continue
        entry = store.setdefault(str(virtual_record_id), RecordProvenance())
        entry.record_id = entry.record_id or record_id
        entry.record_name = entry.record_name or record_name
        if layer not in entry.layers:
            entry.layers.append(layer)


def graph_record_refs(
    rows: Iterable[Mapping[str, Any]],
) -> list[tuple[str | None, str | None, str | None]]:
    """``(virtual_record_id, record_id, record_name)`` of graph record rows."""
    return [
        (row.get("virtualRecordId"), row.get("_key") or row.get("id"), row.get("recordName"))
        for row in rows
    ]
